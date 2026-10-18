"""Fix 6 (18 Oct): scheduled lesson release (LessonAssignment.publish_at).

In-memory SQLite (create_all on the needed tables only) with get_db monkeypatched in
every module touched, so nothing reaches a file database. Time is frozen by replacing
`datetime` in lesson_assignment_service with a subclass whose utcnow() is controllable."""
import os
import tempfile

if "iqbalai_local.db" in os.environ.get("DATABASE_URL", "iqbalai_local.db"):
    os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "oct18_fix6.db").replace("\\", "/")

import pathlib
from datetime import datetime, timedelta

import pytest
from flask import Flask
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.database_models import Base, Lesson, User
from app.models.lms_models import ClassEnrollment, LessonAssignment, SchoolClass
import app.models.models as models_module
from app.services.lms import class_service
from app.services.lms import lesson_assignment_service as las

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 18, 7, 0, 0)


@pytest.fixture(autouse=True)
def _never_the_dev_db():
    from app.config import Config

    if "iqbalai_local.db" in str(getattr(Config, "SQLALCHEMY_DATABASE_URI", "")):
        pytest.skip("app configured against the dev DB; refusing to run")


class _Clock:
    now = NOW


class FrozenDateTime(datetime):
    @classmethod
    def utcnow(cls):
        return _Clock.now


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(
        engine,
        tables=[User.__table__, Lesson.__table__, SchoolClass.__table__, ClassEnrollment.__table__, LessonAssignment.__table__],
    )
    s = sessionmaker(bind=engine)()
    for mod in (models_module, class_service, las):
        monkeypatch.setattr(mod, "get_db", lambda: s)
    import app.utils.db as db_mod

    monkeypatch.setattr(db_mod, "get_db", lambda: s)
    monkeypatch.setattr(las, "datetime", FrozenDateTime)
    _Clock.now = NOW
    yield s
    s.close()


def _user(s, name, role, grade="8"):
    u = User(username=name, useremail=f"{name}@x.com", password="h", role=role, class_standard=grade, medium="English", groq_api_key="")
    s.add(u)
    s.commit()
    return u


@pytest.fixture
def world(db):
    s = db
    teacher = _user(s, "t", "teacher")
    student = _user(s, "stu", "student")
    outsider = _user(s, "out", "student")
    cls = SchoolClass(teacher_id=teacher.id, name="8A", join_code="JOIN8AAA", grade_level="8", is_active=True)
    s.add(cls)
    s.commit()
    s.add(ClassEnrollment(class_id=cls.id, student_id=student.id, status="active"))
    lesson = Lesson(teacher_id=teacher.id, title="Quadratics", content="c", grade_level="8", lesson_id="quad",
                    has_child_version=False, is_public=False, created_at=NOW)
    s.add(lesson)
    s.commit()
    return dict(s=s, teacher=teacher, student=student, outsider=outsider, cls=cls, lesson=lesson)


def _publish(w, publish_at=None, due=None):
    return las.publish_lesson_to_class(
        teacher_id=w["teacher"].id, lesson_id=w["lesson"].id, class_id=w["cls"].id, due_date=due, publish_at=publish_at
    )


def _visible(w, who="student"):
    uid = w[who].id
    return w["lesson"].id in las.list_published_lesson_ids_for_student(uid), las.student_has_lesson_assignment(uid, w["lesson"].id)


# ------------------------------------------------------------- parsing helpers

@pytest.mark.parametrize("raw", ["2026-10-18T07:07:00.000Z", "2026-10-18T12:07:00+05:00", "2026-10-18T07:07:00"])
def test_parse_iso_datetime_utc_normalizes_to_naive_utc(raw):
    out = las.parse_iso_datetime_utc(raw)
    assert out == datetime(2026, 10, 18, 7, 7) and out.tzinfo is None


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_parse_iso_datetime_utc_empty_is_none(raw):
    assert las.parse_iso_datetime_utc(raw) is None


@pytest.mark.parametrize("raw", ["tomorrow", "2026-13-45T00:00", 12345, ["x"]])
def test_parse_iso_datetime_utc_invalid_raises_value_error(raw):
    with pytest.raises(ValueError):
        las.parse_iso_datetime_utc(raw)


def test_to_naive_utc():
    from datetime import timezone

    assert las.to_naive_utc(None) is None
    assert las.to_naive_utc(datetime(2026, 1, 1, 5)) == datetime(2026, 1, 1, 5)
    aware = datetime(2026, 1, 1, 10, tzinfo=timezone(timedelta(hours=5)))
    assert las.to_naive_utc(aware) == datetime(2026, 1, 1, 5)


# ------------------------------------------------------------- visibility

def test_immediate_publish_visible(world):
    data = _publish(world)
    assert data["scheduled"] is False and data["publish_at"] is None
    assert data["status"] == "published"
    assert _visible(world) == (True, True)


def test_scheduled_future_hidden_until_time_then_visible(world):
    at = NOW + timedelta(hours=2)
    data = _publish(world, publish_at=at)
    assert data["scheduled"] is True
    assert data["publish_at"] == "2026-10-18T09:00:00Z"
    assert data["status"] == "published" and data["is_public"] is True
    assert _visible(world) == (False, False)

    _Clock.now = at - timedelta(seconds=1)
    assert _visible(world) == (False, False)
    _Clock.now = at  # boundary: exactly == now is visible
    assert _visible(world) == (True, True)
    _Clock.now = at + timedelta(days=1)
    assert _visible(world) == (True, True)


def test_scheduled_aware_publish_at_stored_as_utc(world):
    from datetime import timezone

    local = datetime(2026, 10, 18, 14, 0, tzinfo=timezone(timedelta(hours=5)))  # = 09:00 UTC
    data = _publish(world, publish_at=local)
    row = world["s"].query(LessonAssignment).one()
    assert row.publish_at == datetime(2026, 10, 18, 9, 0)
    assert data["publish_at"] == "2026-10-18T09:00:00Z"


def test_past_publish_at_means_publish_now(world):
    data = _publish(world, publish_at=NOW - timedelta(minutes=5))
    assert data["scheduled"] is False and data["publish_at"] is None
    assert world["s"].query(LessonAssignment).one().publish_at is None
    assert _visible(world) == (True, True)


def test_publish_at_equal_now_means_publish_now(world):
    data = _publish(world, publish_at=NOW)
    assert data["scheduled"] is False


def test_due_date_preserved_alongside_schedule(world):
    due = NOW + timedelta(days=7)
    _publish(world, publish_at=NOW + timedelta(days=1), due=due)
    row = world["s"].query(LessonAssignment).one()
    assert row.due_date == due and row.publish_at == NOW + timedelta(days=1)
    assert _visible(world) == (False, False)
    _Clock.now = NOW + timedelta(days=1)
    assert _visible(world) == (True, True)


def test_lesson_hidden_from_students_after_due_date(world):
    # The reported case: published 17:45 PKT, due 17:46 PKT, checked 17:47 PKT.
    _publish(world, publish_at=None, due=NOW + timedelta(minutes=1))
    assert _visible(world) == (True, True)
    _Clock.now = NOW + timedelta(seconds=59)
    assert _visible(world) == (True, True)
    _Clock.now = NOW + timedelta(minutes=1)          # exactly at the due date -> hidden
    assert _visible(world) == (False, False)
    _Clock.now = NOW + timedelta(minutes=2)
    assert _visible(world) == (False, False)
    # Still assigned, so the legacy grade-link fallback cannot re-expose it.
    assert las.lesson_has_any_assignments(world["lesson"].id) is True
    # The general-question lesson lookup hides it too.
    assert world["lesson"].id in las.unreleased_scheduled_lesson_ids([world["lesson"].id])


def test_republish_with_new_due_date_makes_lesson_visible_again(world):
    _publish(world, due=NOW + timedelta(minutes=1))
    _Clock.now = NOW + timedelta(minutes=5)
    assert _visible(world) == (False, False)
    _publish(world, due=NOW + timedelta(days=2))
    assert _visible(world) == (True, True)


def test_past_due_date_is_rejected(world):
    from app.services.lms.exceptions import LMSValidationError

    with pytest.raises(LMSValidationError, match="Due date must be in the future"):
        _publish(world, due=NOW - timedelta(minutes=1))
    with pytest.raises(LMSValidationError):
        _publish(world, due=NOW)
    assert world["s"].query(LessonAssignment).count() == 0


def test_due_date_timezone_aware_value_stored_as_utc(world):
    from datetime import timezone as _tz

    pkt = _tz(timedelta(hours=5))
    _publish(world, due=datetime(2026, 10, 18, 12, 49, tzinfo=pkt))   # 12:49 PKT == 07:49 UTC
    row = world["s"].query(LessonAssignment).one()
    assert row.due_date == datetime(2026, 10, 18, 7, 49) and row.due_date.tzinfo is None
    _Clock.now = datetime(2026, 10, 18, 7, 48, 59)
    assert _visible(world) == (True, True)
    _Clock.now = datetime(2026, 10, 18, 7, 49)
    assert _visible(world) == (False, False)


def test_student_not_in_class_never_sees_it(world):
    _publish(world, publish_at=NOW + timedelta(hours=1))
    _Clock.now = NOW + timedelta(days=30)
    assert _visible(world, "outsider") == (False, False)
    assert _visible(world, "student") == (True, True)


def test_class_id_filter(world):
    _publish(world)
    sid, cid = world["student"].id, world["cls"].id
    assert las.list_published_lesson_ids_for_student(sid, class_id=cid) == {world["lesson"].id}
    assert las.list_published_lesson_ids_for_student(sid, class_id=cid + 999) == set()


def test_scheduled_lesson_counts_as_assigned_so_grade_fallback_cannot_expose_it(world):
    _publish(world, publish_at=NOW + timedelta(hours=1))
    lid = world["lesson"].id
    assert las.lesson_has_any_assignments(lid) is True
    # The student IS grade-linked to this teacher, so the legacy rule alone would allow it:
    assert class_service.student_can_access_teacher_grade(world["student"].id, world["teacher"].id, "8") is True
    assert las.student_has_lesson_assignment(world["student"].id, lid) is False


def test_republish_reschedules_and_unschedules(world):
    _publish(world)
    assert _visible(world) == (True, True)
    _publish(world, publish_at=NOW + timedelta(hours=3))
    assert world["s"].query(LessonAssignment).count() == 1
    assert _visible(world) == (False, False)
    _publish(world, publish_at=None)
    assert _visible(world) == (True, True)


def test_draft_assignment_with_past_publish_at_not_visible(world):
    a = las.create_assignment(world["teacher"].id, world["cls"].id, world["lesson"].id, "L")
    assert a.status == "draft"
    assert _visible(world) == (False, False)


def test_list_assignments_for_lesson_exposes_publish_at(world):
    _publish(world, publish_at=NOW + timedelta(hours=2))
    rows = las.list_assignments_for_lesson(world["lesson"].id, world["teacher"].id)
    assert rows[0]["publish_at"] == "2026-10-18T09:00:00Z"


# ------------------------------------------------------------- _can_access_lesson (lesson_routes)

def test_lesson_routes_can_access_lesson_respects_schedule(world):
    from app.routes import lesson_routes

    _publish(world, publish_at=NOW + timedelta(hours=1))
    lesson = {"id": world["lesson"].id, "teacher_id": world["teacher"].id, "is_public": True,
              "grade_level": "8", "has_child_version": False}
    sid = world["student"].id
    assert lesson_routes._can_access_lesson(lesson, sid, "student") is False
    _Clock.now = NOW + timedelta(hours=1)
    assert lesson_routes._can_access_lesson(lesson, sid, "student") is True


# ------------------------------------------------------------- routes

@pytest.fixture
def lms_app(world, monkeypatch):
    from app.routes import lms_routes
    from app.services.lms import lesson_topic_service

    monkeypatch.setattr("app.utils.auth.sync_session_user", lambda: True)
    monkeypatch.setattr(lesson_topic_service, "get_lesson_topics", lambda lid: [])
    app = Flask(__name__)
    app.secret_key = "t"
    app.register_blueprint(lms_routes.bp, url_prefix="/api/lms")

    def client(uid, role):
        c = app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = uid
            s["role"] = role
        return c

    return world, client


def _p2c(c, w, **extra):
    body = {"lesson_id": w["lesson"].id, "class_id": w["cls"].id, "title": "Quadratics"}
    body.update(extra)
    return c.post("/api/lms/lesson-assignments/publish-to-class", json=body)


@pytest.mark.parametrize("bad", ["not-a-date", "2026-99-01T00:00:00Z", 12345])
def test_route_invalid_publish_at_400_and_nothing_created(lms_app, bad):
    w, client = lms_app
    r = _p2c(client(w["teacher"].id, "teacher"), w, publish_at=bad)
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "validation_error"
    assert w["s"].query(LessonAssignment).count() == 0


def test_route_due_before_publish_400(lms_app):
    w, client = lms_app
    r = _p2c(client(w["teacher"].id, "teacher"), w,
             publish_at="2026-10-20T10:00:00.000Z", due_date="2026-10-19T10:00:00.000Z")
    assert r.status_code == 400
    assert "earlier than the scheduled publish time" in r.get_json()["error"]["message"]
    assert w["s"].query(LessonAssignment).count() == 0


def test_route_scheduled_publish_returns_schedule_and_hides_lesson(lms_app):
    w, client = lms_app
    r = _p2c(client(w["teacher"].id, "teacher"), w,
             publish_at="2026-10-18T14:00:00+05:00", due_date="2026-10-25T10:00:00.000Z")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["scheduled"] is True and data["publish_at"] == "2026-10-18T09:00:00Z"
    assert _visible(w) == (False, False)
    # topics endpoint is gated too
    rs = client(w["student"].id, "student").get(f"/api/lms/lessons/{w['lesson'].id}/topics")
    assert rs.status_code == 403
    _Clock.now = datetime(2026, 10, 18, 9, 0)
    rs = client(w["student"].id, "student").get(f"/api/lms/lessons/{w['lesson'].id}/topics")
    assert rs.status_code == 200


def test_route_without_publish_at_is_immediate(lms_app):
    w, client = lms_app
    r = _p2c(client(w["teacher"].id, "teacher"), w)
    assert r.status_code == 200
    assert r.get_json()["data"]["scheduled"] is False
    assert _visible(w) == (True, True)


def test_route_topics_outsider_forbidden(lms_app):
    w, client = lms_app
    _p2c(client(w["teacher"].id, "teacher"), w)
    rs = client(w["outsider"].id, "student").get(f"/api/lms/lessons/{w['lesson'].id}/topics")
    assert rs.status_code == 403


def test_route_search_lessons_hides_scheduled_lesson(world, monkeypatch):
    from app.routes import lesson_routes

    app = Flask(__name__)
    app.secret_key = "t"
    app.register_blueprint(lesson_routes.bp, url_prefix="/api/lessons")
    las.publish_lesson_to_class(teacher_id=world["teacher"].id, lesson_id=world["lesson"].id,
                                class_id=world["cls"].id, publish_at=NOW + timedelta(hours=1))
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = world["student"].id
        s["role"] = "student"
    r = c.get("/api/lessons/search_lessons?q=Quadratics")
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["lessons"] == []
    _Clock.now = NOW + timedelta(hours=1)
    r = c.get("/api/lessons/search_lessons?q=Quadratics")
    assert [l["id"] for l in r.get_json()["lessons"]] == [world["lesson"].id]


# ------------------------------------------------------------- migrations

def test_db_py_migration_adds_column_to_old_table():
    """Run the exact ALTER used by app/utils/db.py / databasemigration.py on a pre-fix table."""
    src = (ROOT / "app" / "utils" / "db.py").read_text(encoding="utf-8")
    assert "ALTER TABLE lesson_assignments ADD COLUMN publish_at TIMESTAMP" in src
    import databasemigration as dm

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE lesson_assignments (id INTEGER PRIMARY KEY, due_date TIMESTAMP)"))
        dm.ensure_lesson_assignments_publish_at_column(conn, inspect(conn))
    cols = {c["name"] for c in inspect(engine).get_columns("lesson_assignments")}
    assert "publish_at" in cols
    with engine.begin() as conn:  # idempotent
        dm.ensure_lesson_assignments_publish_at_column(conn, inspect(conn))


def test_databasemigration_skips_missing_table():
    import databasemigration as dm

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        dm.ensure_lesson_assignments_publish_at_column(conn, inspect(conn))


def test_databasemigration_main_calls_new_step():
    src = (ROOT / "databasemigration.py").read_text(encoding="utf-8")
    main = src[src.index("def main():"):]
    assert "ensure_lesson_assignments_publish_at_column(conn, inspector)" in main


def test_assign_template_has_publish_at_field_separate_from_due():
    html = (ROOT / "templates" / "teacher" / "lessons" / "assign.html").read_text(encoding="utf-8")
    assert 'id="lmsLessonAssignPublishAt" type="datetime-local"' in html
    assert 'id="lmsLessonAssignDue" type="datetime-local"' in html
    js = (ROOT / "static" / "teacher" / "js" / "teacher-lms-hubs.js").read_text(encoding="utf-8")
    assert "publish_at: publishAt ? publishAt.toISOString() : null" in js


# ------------------------------------------------------------- follow-up: unreleased_scheduled_lesson_ids

def _extra_lesson(w, title, public=True):
    s = w["s"]
    l = Lesson(teacher_id=w["teacher"].id, title=title, content="c", grade_level="8",
               lesson_id=title.lower().replace(" ", "-"), has_child_version=False, is_public=public, created_at=NOW)
    s.add(l)
    s.commit()
    return l


def test_unreleased_scheduled_lesson_ids(world):
    w = world
    _publish(w, publish_at=NOW + timedelta(hours=2))            # scheduled
    released = _extra_lesson(w, "Released")
    las.publish_lesson_to_class(teacher_id=w["teacher"].id, lesson_id=released.id, class_id=w["cls"].id)
    unassigned = _extra_lesson(w, "Unassigned")
    ids = [w["lesson"].id, released.id, unassigned.id]
    assert las.unreleased_scheduled_lesson_ids(ids) == {w["lesson"].id}
    _Clock.now = NOW + timedelta(hours=2)
    assert las.unreleased_scheduled_lesson_ids(ids) == set()
    assert las.unreleased_scheduled_lesson_ids([]) == set()
    assert las.unreleased_scheduled_lesson_ids([None, 0]) == set()


def test_unreleased_includes_lessons_with_only_draft_assignments(world):
    w = world
    las.create_assignment(w["teacher"].id, w["cls"].id, w["lesson"].id, "L")  # draft only
    assert las.unreleased_scheduled_lesson_ids([w["lesson"].id]) == {w["lesson"].id}


@pytest.fixture
def available_ids(world, monkeypatch):
    from flask import Flask as _F
    from app.services.lesson_service import LessonService

    w = world
    released = _extra_lesson(w, "Released")
    las.publish_lesson_to_class(teacher_id=w["teacher"].id, lesson_id=released.id, class_id=w["cls"].id)
    unassigned = _extra_lesson(w, "Unassigned")
    _publish(w, publish_at=NOW + timedelta(hours=2))  # w["lesson"] scheduled (now is_public=True)
    users = {w["teacher"].id: {"role": "teacher"}, w["student"].id: {"role": "student"}}
    other_teacher = _user(w["s"], "t2", "teacher")
    users[other_teacher.id] = {"role": "teacher"}
    monkeypatch.setattr(models_module.UserModel, "get_user_by_id", staticmethod(lambda uid: users.get(uid)))
    monkeypatch.setattr(models_module.LessonModel, "get_lessons_by_teacher",
                        staticmethod(lambda uid: [{"id": l.id} for l in w["s"].query(Lesson).filter(Lesson.teacher_id == uid)]))
    monkeypatch.setattr(models_module.LessonModel, "get_public_lessons",
                        staticmethod(lambda: [{"id": l.id} for l in w["s"].query(Lesson).filter(Lesson.is_public.is_(True))]))
    app = _F(__name__)
    app.secret_key = "t"
    svc = LessonService.__new__(LessonService)

    def run(uid):
        with app.test_request_context("/"):
            from flask import session
            session["user_id"] = uid
            return set(svc._get_available_lesson_ids())

    return w, run, released, unassigned, other_teacher


def test_available_ids_hide_future_scheduled_from_student_and_other_teacher(available_ids):
    w, run, released, unassigned, other = available_ids
    assert run(w["student"].id) == {released.id, unassigned.id}
    assert run(other.id) == {released.id, unassigned.id}


def test_available_ids_owner_teacher_still_sees_scheduled(available_ids):
    w, run, released, unassigned, other = available_ids
    assert w["lesson"].id in run(w["teacher"].id)


def test_available_ids_scheduled_visible_after_release(available_ids):
    w, run, released, unassigned, other = available_ids
    _Clock.now = NOW + timedelta(hours=2)
    assert run(w["student"].id) == {w["lesson"].id, released.id, unassigned.id}


# ------------------------------------------------------------- follow-up: browse_lessons pagination

def test_browse_lessons_keeps_query_total_pages(world, monkeypatch):
    import app.models as models_pkg
    from app.routes import lesson_routes

    class FakeUser:
        def __init__(self, uid):
            pass

        def is_student(self):
            return True

    monkeypatch.setattr(models_pkg, "UserModel", FakeUser)
    from app.services.lms import lesson_topic_service

    monkeypatch.setattr(lesson_topic_service, "enrich_lessons_with_topics", lambda ls: ls)
    w = world
    for i in range(11):
        _extra_lesson(w, f"Extra {i}")  # legacy grade-linked, visible
    _publish(w, publish_at=NOW + timedelta(hours=1))  # 12th lesson: scheduled, hidden
    app = Flask(__name__)
    app.secret_key = "t"
    app.register_blueprint(lesson_routes.bp, url_prefix="/api/lessons")
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = w["student"].id
        s["role"] = "student"
    seen = []
    pages = None
    for page in range(1, 5):
        r = c.get(f"/api/lessons/browse_lessons?page={page}&per_page=5")
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json()
        pages = body["total_pages"]
        seen += [l["id"] for l in body["lessons"]]
        if page >= pages:
            break
    assert pages == 3, "total_pages must not be forced to 1"
    assert w["lesson"].id not in seen
    assert len(seen) == 11 and len(set(seen)) == 11
