import io
from datetime import date, datetime
from pathlib import Path

import pytest
from flask import Flask
from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.database_models import Base, TutorDailyUsage, User
from app.models.lms_models import (
    Assessment,
    AssessmentAttempt,
    AssessmentQuestion,
    AttemptAnswer,
    Question,
    StudentStudyGroupAssignment,
    StudentStudyGroupAudit,
    Topic,
)
from app.routes.student_reporting_routes import bp
from app.services.student_reporting_service import (
    ANSWER_HEADERS,
    RESULT_HEADERS,
    TOPIC_HEADERS,
    assign_students,
    build_report,
    classify_assessment,
    export_report,
    parse_initial_effective_date,
    preview_group_import,
    workbook_for_file,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def user(user_id, email, role="student", name=None, school="School A"):
    return User(
        id=user_id, username=email.split("@")[0], full_name=name or f"Student {user_id}",
        school_name=school, useremail=email, password="not-used", role=role,
        class_standard="9", medium="English", groq_api_key="not-used",
    )


def seed_attempt(db, student_id=2, *, submitted=datetime(2026, 10, 29, 7, 30), correct=True, title="Algebra Test", assessment_created=None):
    topic = Topic(name="Algebra", slug=f"algebra-{student_id}-{int(submitted.timestamp())}", subject="Mathematics", grade_level="9")
    db.add(topic); db.flush()
    question = Question(
        topic_id=topic.id, question_text="2 + 2 = ?",
        options_json='[{"label":"A","text":"3"},{"label":"B","text":"4"},{"label":"C","text":"5"},{"label":"D","text":"6"}]',
        correct_option_index=1, difficulty="easy", source_type="manual",
    )
    assessment = Assessment(
        title=title, assessment_type="quiz", creation_mode="manual", status="published",
        created_at=assessment_created or submitted,
    )
    db.add_all([question, assessment]); db.flush()
    db.add(AssessmentQuestion(assessment_id=assessment.id, question_id=question.id, sort_order=1))
    attempt = AssessmentAttempt(
        student_id=student_id, assessment_id=assessment.id, status="submitted",
        started_at=datetime(2026, 10, 29, 7, 0), submitted_at=submitted,
        score=1 if correct else 0, max_score=1,
    )
    db.add(attempt); db.flush()
    db.add(AttemptAnswer(
        attempt_id=attempt.id, question_id=question.id,
        selected_option_index=1 if correct else 0, is_correct=correct,
    ))
    db.flush()
    return assessment, attempt, question


def test_bulk_assignment_preserves_effective_history_and_audit(db):
    db.add_all([user(1, "admin@example.com", "admin"), user(2, "one@example.com"), user(3, "two@example.com")]); db.flush()
    first = assign_students(db, [2, 3], "Controlled", "pilot-9", 1)
    db.flush()
    second = assign_students(db, [2], "Non-Controlled", "pilot-9", 1)
    db.flush()

    assert first["changed"] == 2
    assert second["group"] == "Uncontrolled"
    active = db.query(StudentStudyGroupAssignment).filter_by(student_id=2, cohort_key="pilot-9", effective_to=None).one()
    assert active.group_name == "Uncontrolled"
    old = db.query(StudentStudyGroupAssignment).filter_by(student_id=2, group_name="Controlled").one()
    assert old.effective_to is not None
    assert [(row.old_group_name, row.new_group_name) for row in db.query(StudentStudyGroupAudit).filter_by(student_id=2).order_by(StudentStudyGroupAudit.id)] == [
        (None, "Controlled"), ("Controlled", "Uncontrolled")
    ]


def test_initial_assignment_accepts_effective_date_but_rejects_backdated_reassignment(db):
    db.add_all([user(1, "admin@example.com", "admin"), user(2, "student@example.com")]); db.flush()
    db.autoflush = False
    effective = parse_initial_effective_date("2026-10-07")

    first = assign_students(
        db, [2], "Uncontrolled", "default", 1, source="import",
        effective_from=effective, note="canonical roster",
    )
    repeated = assign_students(
        db, [2], "Uncontrolled", "default", 1, source="import",
        effective_from=effective, note="canonical roster",
    )

    assert first["changed"] == 1
    assert repeated == {"changed": 0, "unchanged": 1, "group": "Uncontrolled", "cohort": "default", "effective_from": effective.isoformat()}
    assert db.query(StudentStudyGroupAssignment).filter_by(student_id=2).one().effective_from == effective
    with pytest.raises(ValueError, match="only allowed for students with no group history"):
        assign_students(db, [2], "Controlled", "default", 1, source="import", effective_from=effective)


def test_import_preview_rejects_duplicates_and_unmatched_without_creating_users(db):
    db.add(user(2, "known@example.com")); db.flush()

    class Upload(io.BytesIO):
        filename = "groups.csv"

    upload = Upload(b"Email,Group\nknown@example.com,Controlled\nknown@example.com,Uncontrolled\nmissing@example.com,Controlled\n")
    preview = preview_group_import(db, upload)

    assert preview["ready"] == 1
    assert preview["errors"] == 2
    assert "Duplicate student" in preview["rows"][1]["errors"][0]
    assert "Email not found" in preview["rows"][2]["errors"]
    assert db.query(User).count() == 1


def test_classification_is_cohort_scoped_and_prevents_duplicate_baselines(db):
    db.add(user(1, "admin@example.com", "admin"))
    first = Assessment(title="Baseline A", assessment_type="quiz", creation_mode="manual", status="published")
    second = Assessment(title="Baseline B", assessment_type="quiz", creation_mode="manual", status="published")
    db.add_all([first, second]); db.flush()

    classify_assessment(db, first.id, "pilot", "Baseline", None, 1)
    db.flush()
    with pytest.raises(ValueError, match="already has a Baseline"):
        classify_assessment(db, second.id, "pilot", "Baseline", None, 1)
    # Another cohort is isolated, and explicit unclassification frees the original cohort.
    classify_assessment(db, second.id, "another-pilot", "Baseline", None, 1)
    classify_assessment(db, first.id, "pilot", "", None, 1)
    db.flush()
    assert classify_assessment(db, second.id, "pilot", "Baseline", None, 1)["test_type"] == "Baseline"


def test_report_uses_latest_attempt_actual_answers_topics_and_usage(db):
    db.add_all([user(1, "admin@example.com", "admin"), user(2, "student@example.com", name="Ayesha")]); db.flush()
    assessment, older, question = seed_attempt(db, correct=False)
    newer = AssessmentAttempt(
        student_id=2, assessment_id=assessment.id, status="submitted",
        started_at=datetime(2026, 10, 29, 8, 0), submitted_at=datetime(2026, 10, 29, 8, 15),
        score=1, max_score=1,
    )
    db.add(newer); db.flush()
    db.add(AttemptAnswer(attempt_id=newer.id, question_id=question.id, selected_option_index=1, is_correct=True))
    db.add(StudentStudyGroupAssignment(
        student_id=2, cohort_key="pilot", group_name="Controlled",
        effective_from=datetime(2026, 10, 1), assigned_by=1,
    ))
    classify_assessment(db, assessment.id, "pilot", "Baseline", None, 1)
    db.add(TutorDailyUsage(user_id=2, usage_date=date(2026, 10, 29), active_ms=600000, credited_until=datetime(2026, 10, 29, 8, 10)))
    db.commit()

    report = build_report(db, {"cohort": "pilot", "group": "controlled", "start": "2026-10-29", "end": "2026-10-29", "test_type": "baseline", "assessment_id": assessment.id})

    assert report["attempts"] == 1
    result = report["files"][0]["results"][0]
    assert result[7:11] == [1, 1, 0, 1.0]
    assert result[11:16] == [15.0, 10.0, 1, date(2026, 10, 29), 2]
    assert report["files"][0]["topics"][0][3:] == [1, 1, 1.0]
    assert report["files"][0]["answers"][0][5:] == ["B", "B", "Yes"]
    assert report["attempt_rule"].startswith("Selected assessment:")


def test_cross_group_isolation_and_missing_usage_is_blank(db):
    db.add_all([user(1, "admin@example.com", "admin"), user(2, "controlled@example.com"), user(3, "other@example.com")]); db.flush()
    assessment, _, _ = seed_attempt(db, student_id=2)
    # Same assessment for the second student.
    question_id = db.query(AssessmentQuestion.question_id).filter_by(assessment_id=assessment.id).scalar()
    attempt = AssessmentAttempt(student_id=3, assessment_id=assessment.id, status="submitted", started_at=datetime(2026,10,29,7), submitted_at=datetime(2026,10,29,7,10))
    db.add(attempt); db.flush(); db.add(AttemptAnswer(attempt_id=attempt.id, question_id=question_id, selected_option_index=1, is_correct=True))
    db.add_all([
        StudentStudyGroupAssignment(student_id=2, cohort_key="pilot", group_name="Controlled", effective_from=datetime(2026,10,1)),
        StudentStudyGroupAssignment(student_id=3, cohort_key="pilot", group_name="Uncontrolled", effective_from=datetime(2026,10,1)),
    ])
    classify_assessment(db, assessment.id, "pilot", "FollowUp", 1, 1); db.commit()

    report = build_report(db, {"cohort":"pilot", "group":"controlled", "start":"2026-10-29", "end":"2026-10-29", "test_type":"all"})

    assert report["matching_students"] == 1
    assert report["files"][0]["results"][0][1] == "controlled@example.com"
    assert report["files"][0]["results"][0][12] is None
    assert any("blank, not zero" in warning for warning in report["warnings"])


def test_all_tests_date_excludes_old_assessment_retake_but_explicit_selection_allows_it(db):
    db.add_all([
        user(1, "admin@example.com", "admin"),
        user(2, "retake@example.com"),
        user(3, "current@example.com"),
    ]); db.flush()
    old_quiz, _, _ = seed_attempt(
        db, 2, submitted=datetime(2026, 11, 2, 9, 13),
        assessment_created=datetime(2026, 10, 26, 8, 43),
        title="Practice Quiz 1 - Mathematics IX",
    )
    current_quiz, _, _ = seed_attempt(
        db, 3, submitted=datetime(2026, 11, 2, 8, 5),
        assessment_created=datetime(2026, 11, 2, 6, 48),
        title="Practice Quiz 2 - Mathematics IX",
    )
    db.add_all([
        StudentStudyGroupAssignment(student_id=2, cohort_key="default", group_name="Uncontrolled", effective_from=datetime(2026, 10, 1)),
        StudentStudyGroupAssignment(student_id=3, cohort_key="default", group_name="Uncontrolled", effective_from=datetime(2026, 10, 1)),
    ])
    classify_assessment(db, old_quiz.id, "default", "FollowUp", 1, 1)
    classify_assessment(db, current_quiz.id, "default", "FollowUp", 2, 1)
    db.commit()

    filters = {
        "cohort": "default", "group": "uncontrolled", "start": "2026-11-02", "end": "2026-11-02",
        "test_type": "all", "assessment_id": "all",
    }
    all_tests = build_report(db, filters)
    assert all_tests["attempts"] == 1
    assert [row[6] for item in all_tests["files"] for row in item["results"]] == ["Practice Quiz 2 - Mathematics IX"]
    assert all_tests["attempt_rule"].startswith("All tests: assessment creation")

    explicit_retake = build_report(db, {**filters, "assessment_id": old_quiz.id})
    assert explicit_retake["attempts"] == 1
    assert explicit_retake["files"][0]["results"][0][6] == "Practice Quiz 1 - Mathematics IX"

    date_range = build_report(db, {**filters, "start": "2026-10-26"})
    assert date_range["attempts"] == 2


def test_same_named_school_assessments_can_be_selected_as_one_quiz(db):
    db.add_all([user(1, "admin@example.com", "admin"), user(2, "one@example.com"), user(3, "two@example.com")]); db.flush()
    first, _, _ = seed_attempt(db, student_id=2, title="Practice Quiz 2 - Mathematics IX")
    second, _, _ = seed_attempt(db, student_id=3, title="Practice Quiz 2 - Mathematics IX")
    db.add_all([
        StudentStudyGroupAssignment(student_id=2, cohort_key="pilot", group_name="Uncontrolled", effective_from=datetime(2026,10,1)),
        StudentStudyGroupAssignment(student_id=3, cohort_key="pilot", group_name="Uncontrolled", effective_from=datetime(2026,10,1)),
    ])
    classify_assessment(db, first.id, "pilot", "FollowUp", 2, 1)
    classify_assessment(db, second.id, "pilot", "FollowUp", 2, 1)
    db.commit()

    report = build_report(db, {
        "cohort":"pilot", "group":"uncontrolled", "start":"2026-10-29", "end":"2026-10-29",
        "test_type":"all", "assessment_id":"title:Practice Quiz 2 - Mathematics IX",
    })

    assert report["attempts"] == 2
    assert report["matching_students"] == 2
    assert len(report["files"]) == 2


def test_preview_diagnostics_explain_unclassified_and_historically_unassigned_attempts(db):
    db.add_all([user(1, "admin@example.com", "admin"), user(2, "student@example.com")]); db.flush()
    assessment, _, _ = seed_attempt(db)
    db.commit()

    unclassified = build_report(db, {"cohort":"default", "group":"uncontrolled", "start":"2026-10-29", "end":"2026-10-29", "test_type":"all"})
    assert unclassified["diagnostics"] == {
        "submitted_candidates": 1, "unclassified": 1,
        "without_effective_group": 0, "different_group": 0,
    }
    assert any("not configured" in warning for warning in unclassified["warnings"])

    classify_assessment(db, assessment.id, "default", "FollowUp", 1, 1); db.commit()
    unassigned = build_report(db, {"cohort":"default", "group":"uncontrolled", "start":"2026-10-29", "end":"2026-10-29", "test_type":"all"})
    assert unassigned["diagnostics"]["without_effective_group"] == 1
    assert any("no group assignment effective" in warning for warning in unassigned["warnings"])


def test_report_ui_uses_simple_primary_filters_and_cache_busts_loader():
    template = Path("templates/admin/sections/student_reports.html").read_text(encoding="utf-8")
    dashboard = Path("templates/admin/dashboard.html").read_text(encoding="utf-8")

    primary = template.split("<details", 1)[0]
    assert "Student group" in primary and "Test date" in primary and "Assessment" in primary and "School" in primary
    assert "Cohort" not in primary and "Test type" not in primary and "FollowUp order" not in template
    assert 'id="sr-cohort" value="default"' in template
    assert "admin-dashboard.js') }}?v=20261103reports4" in dashboard


def test_workbook_matches_reference_contract_and_export_merges_files(db):
    file_data = {
        "group":"Controlled", "test_type":"Baseline", "test_date":date(2026,10,29),
        "filename":"Controlled_Baseline_2026-10-29.xlsx", "assessment_id":1,
        "results":[["=unsafe", "student@example.com", "School", "Controlled", "Baseline", date(2026,10,29), "Test", 1, 1, 0, 1.0, 5.0, None, 1, date(2026,10,29), 1]],
        "topics":[["Student", "student@example.com", "Algebra", 1, 1, 1.0]],
        "answers":[["Student", "student@example.com", 1, "Algebra", "2+2?", "B", "B", "Yes"]],
    }
    stream = workbook_for_file(file_data)
    workbook = load_workbook(stream, data_only=False)
    reference = load_workbook(Path("app/report_templates/controlled.xlsx"))

    assert workbook.sheetnames == reference.sheetnames == ["Results", "Topic Scores", "Answers", "Instructions"]
    assert [cell.value for cell in workbook["Results"][1]] == RESULT_HEADERS
    assert [cell.value for cell in workbook["Topic Scores"][1]] == TOPIC_HEADERS
    assert [cell.value for cell in workbook["Answers"][1]] == ANSWER_HEADERS
    assert workbook["Instructions"]["A1"].value == reference["Instructions"]["A1"].value
    assert workbook["Results"]["K2"].number_format == "0.00%"
    assert workbook["Topic Scores"]["F2"].number_format == "0.00%"
    assert workbook["Results"]["A2"].data_type == "s"  # formula injection is neutralized
    second = {
        **file_data,
        "group": "Uncontrolled",
        "filename": "Uncontrolled_Baseline_2026-10-29.xlsx",
        "results": [["Second", "second@example.com", "School B", "Uncontrolled", "Baseline", date(2026,10,29), "Test B", 1, 0, 1, 0.0, 4.0, None, 1, date(2026,10,29), 1]],
        "topics": [["Second", "second@example.com", "Numbers", 0, 1, 0.0]],
        "answers": [["Second", "second@example.com", 1, "Numbers", "3+3?", "A", "B", "No"]],
    }
    report = {"files":[file_data, second], "filters":{"start":"2026-10-29", "end":"2026-10-29"}}
    merged, name, mime = export_report(report)
    assert name == "IqbalAI_Performance_Both_Groups_2026-10-29.xlsx"
    assert mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    exported = load_workbook(merged, data_only=False)
    assert exported.sheetnames == ["Results", "Topic Scores", "Answers", "Instructions"]
    assert exported["Results"].max_row == 3
    assert exported["Topic Scores"].max_row == 3
    assert exported["Answers"].max_row == 3
    assert [exported["Results"][row][1].value for row in (2, 3)] == ["student@example.com", "second@example.com"]
    assert exported["Instructions"]["A2"].value == "One workbook contains all selected tests and students."


def test_report_api_requires_login(monkeypatch):
    monkeypatch.setattr("app.utils.auth.sync_session_user", lambda: None)
    app = Flask(__name__); app.secret_key = "test"; app.register_blueprint(bp)
    response = app.test_client().get("/admin/student-groups", headers={"Accept":"application/json"})
    assert response.status_code == 401
    assert response.get_json()["error"] == "Login required"
