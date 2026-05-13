"""Phase 4 REST API: practice queue, intelligence, recovery, groups, chat, VA."""
from __future__ import annotations

import json
from datetime import datetime

from flask import Blueprint, jsonify, request, session

from app.models import UserModel
from app.models.phase4_models import GapAnalysisConsent, TeacherGroupSuggestion
from app.models.phase3_models import LectureTeacherReview
from app.services.phase3 import question_bank_service
from app.services.phase4 import (
    intelligence_service,
    micro_revision_service,
    parent_risk_service,
    pedagogy_service,
    phase4_chat_service,
    practice_attempt_service,
    question_queue_service,
    recovery_service,
    scheduled_notification_service,
    va_service,
)
from app.services.phase4 import group_study_v2_service
from app.utils.auth import login_required
from app.utils.db import get_db
from app.utils.decorators import student_required, teacher_required

phase4_api_bp = Blueprint("phase4_api", __name__, url_prefix="/api/phase4")


def _uid() -> int:
    return int(session["user_id"])


def _json_error(msg: str, code: int = 400):
    return jsonify({"error": msg}), code


def _role() -> str:
    return UserModel(session["user_id"]).get_role()


# --- Queue ---
@phase4_api_bp.route("/queue/enqueue", methods=["POST"])
@login_required
def queue_enqueue():
    if _role() != "student":
        return _json_error("Students only", 403)
    db = get_db()
    data = request.get_json(silent=True) or {}
    qid = data.get("question_bank_item_id")
    source = data.get("source") or "daily_practice"
    if not qid:
        return _json_error("question_bank_item_id required")
    row = question_queue_service.enqueue(
        db,
        student_user_id=_uid(),
        question_bank_item_id=int(qid),
        source=str(source),
        due_at=None,
        payload=data.get("payload"),
    )
    return jsonify({"id": row.id, "source": row.source, "status": row.status}), 201


@phase4_api_bp.route("/queue/next", methods=["GET"])
@login_required
def queue_next():
    if _role() != "student":
        return _json_error("Students only", 403)
    db = get_db()
    row = question_queue_service.dequeue_next_pending(db, student_user_id=_uid())
    if not row:
        return jsonify({"item": None})
    from app.models.phase3_models import QuestionBankItem

    qb = db.query(QuestionBankItem).filter_by(id=row.question_bank_item_id).first()
    return jsonify(
        {
            "item": {
                "queue": {"id": row.id, "source": row.source},
                "question": question_bank_service.item_to_dict(qb) if qb else None,
            }
        }
    )


# --- Practice ---
@phase4_api_bp.route("/practice/start", methods=["POST"])
@login_required
def practice_start():
    if _role() != "student":
        return _json_error("Students only", 403)
    db = get_db()
    data = request.get_json(silent=True) or {}
    qbid = data.get("question_bank_item_id")
    if not qbid:
        return _json_error("question_bank_item_id required")
    row = practice_attempt_service.start_attempt(
        db,
        student_user_id=_uid(),
        question_bank_item_id=int(qbid),
        queue_item_id=data.get("queue_item_id"),
    )
    return jsonify(practice_attempt_service.attempt_to_dict(row)), 201


@phase4_api_bp.route("/practice/<int:attempt_id>/submit", methods=["POST"])
@login_required
def practice_submit(attempt_id: int):
    if _role() != "student":
        return _json_error("Students only", 403)
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        pat = practice_attempt_service.recent_correctness_pattern(db, student_user_id=_uid())
        row = practice_attempt_service.submit_attempt(
            db,
            attempt_id=attempt_id,
            student_user_id=_uid(),
            confidence_before_result=int(data.get("confidence_before_result")),
            response_payload=data.get("response") or {},
            is_correct=bool(data.get("is_correct")),
            correct_answer_hint=str(data.get("correct") or ""),
            recent_pattern=pat,
        )
    except ValueError as e:
        return _json_error(str(e), 400)
    similar = None
    if not row.is_correct and row.question_bank_item_id:
        from app.models.phase3_models import QuestionBankItem

        item = db.query(QuestionBankItem).filter_by(id=row.question_bank_item_id).first()
        if item:
            sims = question_bank_service.list_similar_concept(
                db,
                syllabus_topic_id=item.syllabus_topic_id,
                exclude_ids=[row.question_bank_item_id],
                bloom_level=item.bloom_level,
                tag_overlap=json.loads(item.tags_json or "[]") if item.tags_json else [],
                limit=1,
            )
            if sims:
                similar = sims[0]
                practice_attempt_service.attach_similar_followup(
                    db, attempt_id=attempt_id, student_user_id=_uid(), similar_item_id=int(similar["id"])
                )
    return jsonify({"attempt": practice_attempt_service.attempt_to_dict(row), "similar_question": similar})


# --- Intelligence ---
@phase4_api_bp.route("/intelligence/snapshot", methods=["POST"])
@login_required
def intelligence_recompute():
    if _role() != "student":
        return _json_error("Students only", 403)
    db = get_db()
    data = request.get_json(silent=True) or {}
    snap = intelligence_service.compute_intelligence_snapshot(
        db, student_user_id=_uid(), exam_target_id=data.get("exam_target_id")
    )
    intelligence_service.persist_cognitive_snapshot(db, student_user_id=_uid())
    parent_risk_service.maybe_alert_parents(db, student_user_id=_uid())
    va_service.refresh_student_cards(db, student_user_id=_uid())
    out = intelligence_service.latest_snapshot_dict(db, student_user_id=_uid())
    return jsonify(out or {})


@phase4_api_bp.route("/intelligence/latest", methods=["GET"])
@login_required
def intelligence_latest():
    if _role() != "student":
        return _json_error("Students only", 403)
    db = get_db()
    out = intelligence_service.latest_snapshot_dict(db, student_user_id=_uid())
    if not out:
        return jsonify({"message": "no_snapshot", "prediction_disclaimer": intelligence_service.disclaimer()})
    return jsonify(out)


@phase4_api_bp.route("/intelligence/cognitive", methods=["GET"])
@login_required
def intelligence_cognitive():
    db = get_db()
    role = _role()
    uid = _uid()
    target_id = request.args.get("student_id", type=int)
    sid = uid
    if role in ("teacher", "principal", "coordinator", "school_admin", "district_admin", "platform_admin", "admin"):
        if target_id is None:
            return _json_error("student_id required for staff", 400)
        sid = target_id
    elif role != "student":
        return _json_error("Forbidden", 403)

    dna = intelligence_service.compute_cognitive_dna(db, student_user_id=sid)
    r_stu, r_raw = intelligence_service.radar_payloads(dna)
    if role == "student":
        return jsonify({"dna": dna, "radar": r_stu, "prediction_disclaimer": intelligence_service.disclaimer()})
    return jsonify({"dna": dna, "radar": r_raw, "prediction_disclaimer": intelligence_service.disclaimer()})


@phase4_api_bp.route("/intelligence/retention-map", methods=["GET"])
@login_required
def intelligence_retention():
    if _role() != "student":
        return _json_error("Students only", 403)
    db = get_db()
    return jsonify({"items": intelligence_service.retention_map(db, student_user_id=_uid())})


# --- Gap analysis consent ---
@phase4_api_bp.route("/gap-analysis/consent", methods=["POST"])
@login_required
def gap_consent():
    if _role() != "student":
        return _json_error("Students only", 403)
    db = get_db()
    data = request.get_json(silent=True) or {}
    approved = bool(data.get("approved"))
    row = GapAnalysisConsent(student_user_id=_uid(), status="approved" if approved else "rejected")
    if approved:
        row.consented_at = datetime.utcnow()
        # root cause: walk weak topics from latest snapshot
        snap = intelligence_service.latest_snapshot_dict(db, student_user_id=_uid())
        topics = (snap or {}).get("causal_topics") or []
        row.result_json = json.dumps({"root_topics": topics[:5]}, default=str)
        row.status = "completed"
    db.add(row)
    db.commit()
    return jsonify({"id": row.id, "status": row.status}), 201


# --- Recovery ---
@phase4_api_bp.route("/recovery/start", methods=["POST"])
@login_required
def recovery_start():
    if _role() != "student":
        return _json_error("Students only", 403)
    db = get_db()
    data = request.get_json(silent=True) or {}
    row = recovery_service.start_session(db, student_user_id=_uid(), syllabus_topic_id=data.get("syllabus_topic_id"))
    return jsonify(recovery_service.session_to_dict(row)), 201


@phase4_api_bp.route("/recovery/<int:sid>/advance", methods=["POST"])
@login_required
def recovery_advance(sid: int):
    if _role() != "student":
        return _json_error("Students only", 403)
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        row = recovery_service.advance(
            db, session_id=sid, student_user_id=_uid(), force=bool(data.get("force"))
        )
    except ValueError:
        return _json_error("not_found", 404)
    return jsonify(recovery_service.session_to_dict(row))


# --- Micro revision ---
@phase4_api_bp.route("/micro-revision/start", methods=["POST"])
@login_required
def micro_start():
    if _role() != "student":
        return _json_error("Students only", 403)
    db = get_db()
    data = request.get_json(silent=True) or {}
    row = micro_revision_service.start_micro_revision(
        db, student_user_id=_uid(), syllabus_topic_id=data.get("syllabus_topic_id"), recall_seconds=30
    )
    return jsonify(micro_revision_service.session_to_dict(row)), 201


@phase4_api_bp.route("/micro-revision/<int:sid>/recall", methods=["POST"])
@login_required
def micro_recall(sid: int):
    if _role() != "student":
        return _json_error("Students only", 403)
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        row = micro_revision_service.submit_recall(
            db, session_id=sid, student_user_id=_uid(), recall_text=str(data.get("text") or "")
        )
    except ValueError:
        return _json_error("not_found", 404)
    return jsonify(micro_revision_service.session_to_dict(row))


# --- Scheduled notification ---
@phase4_api_bp.route("/notifications/schedule", methods=["POST"])
@login_required
def notif_schedule():
    db = get_db()
    data = request.get_json(silent=True) or {}
    when = data.get("fire_at")
    if not when:
        return _json_error("fire_at required")
    try:
        fire = datetime.fromisoformat(str(when).replace("Z", "+00:00"))
    except Exception:
        return _json_error("invalid fire_at", 400)
    row = scheduled_notification_service.schedule_notification(
        db,
        recipient_user_id=_uid(),
        fire_at_utc=fire,
        title=str(data.get("title") or "Reminder"),
        message=str(data.get("message") or ""),
        notif_type=str(data.get("type") or "phase4_reminder"),
        action_link=data.get("action_link"),
    )
    return jsonify({"id": row.id, "fire_at_utc": row.fire_at_utc.isoformat()}), 201


# --- Groups ---
@phase4_api_bp.route("/groups", methods=["POST"])
@login_required
def groups_create():
    if _role() != "student":
        return _json_error("Students only", 403)
    db = get_db()
    data = request.get_json(silent=True) or {}
    g = group_study_v2_service.create_group(
        db, creator_user_id=_uid(), name=str(data.get("name") or "Study group"), purpose=data.get("purpose")
    )
    return jsonify({"id": g.id, "name": g.name}), 201


@phase4_api_bp.route("/groups/mine", methods=["GET"])
@login_required
def groups_mine():
    db = get_db()
    return jsonify({"groups": group_study_v2_service.list_my_groups(db, user_id=_uid())})


@phase4_api_bp.route("/groups/<int:gid>/invite", methods=["POST"])
@login_required
def groups_invite(gid: int):
    db = get_db()
    try:
        out = group_study_v2_service.create_invite(db, group_id=gid, actor_user_id=_uid())
    except PermissionError:
        return _json_error("Forbidden", 403)
    return jsonify(out), 201


@phase4_api_bp.route("/groups/join", methods=["POST"])
@login_required
def groups_join():
    if _role() != "student":
        return _json_error("Students only", 403)
    db = get_db()
    data = request.get_json(silent=True) or {}
    token = data.get("token")
    if not token:
        return _json_error("token required")
    try:
        group_study_v2_service.join_with_token(db, user_id=_uid(), token=str(token))
    except ValueError as e:
        return _json_error(str(e), 400)
    return jsonify({"ok": True})


@phase4_api_bp.route("/groups/<int:gid>/ai/shared/messages", methods=["GET"])
@login_required
def group_ai_shared_get(gid: int):
    db = get_db()
    if not group_study_v2_service._is_member(db, group_id=gid, user_id=_uid()):
        return _json_error("Forbidden", 403)
    th = group_study_v2_service.get_or_create_shared_thread(db, group_id=gid)
    from app.models.phase4_models import StudyGroupAIMessage

    rows = db.query(StudyGroupAIMessage).filter_by(thread_id=th.id).order_by(StudyGroupAIMessage.id).limit(100).all()
    return jsonify(
        {
            "thread_id": th.id,
            "messages": [
                {"role": m.role, "content": m.content, "sources": json.loads(m.sources_json or "{}")} for m in rows
            ],
        }
    )


@phase4_api_bp.route("/groups/<int:gid>/ai/shared/messages", methods=["POST"])
@login_required
def group_ai_shared_post(gid: int):
    db = get_db()
    if not group_study_v2_service._is_member(db, group_id=gid, user_id=_uid()):
        return _json_error("Forbidden", 403)
    data = request.get_json(silent=True) or {}
    text = str(data.get("message") or "").strip()
    if not text:
        return _json_error("message required", 400)
    th = group_study_v2_service.get_or_create_shared_thread(db, group_id=gid)
    group_study_v2_service.append_ai_message(
        db, thread_id=th.id, sender_user_id=_uid(), role="user", content=text
    )
    reply, sources = phase4_chat_service.generate_assistant_reply(text)
    group_study_v2_service.append_ai_message(
        db, thread_id=th.id, sender_user_id=None, role="assistant", content=reply, sources=sources
    )
    return jsonify({"reply": reply, "sources": sources})


@phase4_api_bp.route("/groups/<int:gid>/ai/private/messages", methods=["POST"])
@login_required
def group_ai_private_post(gid: int):
    db = get_db()
    if not group_study_v2_service._is_member(db, group_id=gid, user_id=_uid()):
        return _json_error("Forbidden", 403)
    data = request.get_json(silent=True) or {}
    text = str(data.get("message") or "").strip()
    th = group_study_v2_service.get_private_thread(db, group_id=gid, user_id=_uid())
    group_study_v2_service.append_ai_message(
        db, thread_id=th.id, sender_user_id=_uid(), role="user", content=text
    )
    reply, sources = phase4_chat_service.generate_assistant_reply(text)
    group_study_v2_service.append_ai_message(
        db, thread_id=th.id, sender_user_id=None, role="assistant", content=reply, sources=sources
    )
    return jsonify({"reply": reply, "sources": sources})


@phase4_api_bp.route("/teacher/dashboard-extensions", methods=["GET"])
@login_required
def teacher_dashboard_extensions():
    if _role() != "teacher":
        return _json_error("Teachers only", 403)
    db = get_db()
    sug = (
        db.query(TeacherGroupSuggestion)
        .filter_by(teacher_user_id=_uid(), status="pending")
        .count()
    )
    pending_reviews = (
        db.query(LectureTeacherReview)
        .filter(LectureTeacherReview.teacher_user_id == _uid())
        .count()
    )
    return jsonify(
        {
            "pending_group_suggestions": int(sug),
            "pending_teacher_reviews": int(pending_reviews),
            "lecture_quality_trend": {"data_available": False, "note": "Wire from student_lecture_ratings aggregation"},
            "teaching_innovation_summary": {"data_available": False, "note": "Wire from ai_teaching_adaptations"},
        }
    )
@login_required
def teacher_suggestions_list():
    if _role() != "teacher":
        return _json_error("Teachers only", 403)
    db = get_db()
    rows = (
        db.query(TeacherGroupSuggestion)
        .filter_by(teacher_user_id=_uid(), status="pending")
        .order_by(TeacherGroupSuggestion.created_at.desc())
        .limit(30)
        .all()
    )
    out = []
    for r in rows:
        out.append({"id": r.id, "payload": json.loads(r.payload_json or "{}")})
    return jsonify({"items": out})


@phase4_api_bp.route("/teacher/group-suggestions/<int:sid>/accept", methods=["POST"])
@login_required
def teacher_suggestions_accept(sid: int):
    if _role() != "teacher":
        return _json_error("Teachers only", 403)
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        g = group_study_v2_service.accept_suggestion(
            db, suggestion_id=sid, teacher_user_id=_uid(), group_name=str(data.get("name") or "Study group")
        )
    except ValueError:
        return _json_error("not_found", 404)
    return jsonify({"group_id": g.id})


# --- Chatbot ---
@phase4_api_bp.route("/chat/conversations", methods=["POST"])
@login_required
def chat_conv_create():
    db = get_db()
    data = request.get_json(silent=True) or {}
    c = phase4_chat_service.get_or_create_conversation(
        db, user_id=_uid(), conversation_id=data.get("conversation_id"), subject_hint=data.get("subject")
    )
    return jsonify({"id": c.id}), 201


@phase4_api_bp.route("/chat/conversations/<int:cid>/messages", methods=["GET"])
@login_required
def chat_messages(cid: int):
    db = get_db()
    rows = phase4_chat_service.list_messages(db, conversation_id=cid, user_id=_uid())
    return jsonify(
        {
            "messages": [
                {"role": m.role, "content": m.content, "sources": json.loads(m.sources_json or "{}")} for m in rows
            ]
        }
    )


@phase4_api_bp.route("/chat/conversations/<int:cid>/messages", methods=["POST"])
@login_required
def chat_send(cid: int):
    db = get_db()
    data = request.get_json(silent=True) or {}
    text = str(data.get("message") or "").strip()
    if not text:
        return _json_error("message required", 400)
    from app.models.database_models import User as DBUser

    u = db.query(DBUser).filter_by(id=_uid()).first()
    lang = (u.preferred_language if u else None) or "en"
    phase4_chat_service.append_message(db, conversation_id=cid, role="user", content=text)
    reply, sources = phase4_chat_service.generate_assistant_reply(text, preferred_language=lang)
    phase4_chat_service.append_message(db, conversation_id=cid, role="assistant", content=reply, sources=sources)
    return jsonify({"reply": reply, "sources": sources})


# --- VA cards ---
@phase4_api_bp.route("/va/cards", methods=["GET"])
@login_required
def va_list():
    db = get_db()
    rows = va_service.list_active(db, user_id=_uid())
    out = []
    for r in rows:
        out.append(
            {
                "id": r.id,
                "card_type": r.card_type,
                "title": r.title,
                "body": json.loads(r.body_json or "{}"),
                "action_cta": r.action_cta,
                "status": r.status,
            }
        )
    return jsonify({"items": out})


@phase4_api_bp.route("/va/cards/<int:cid>/dismiss", methods=["POST"])
@login_required
def va_dismiss(cid: int):
    db = get_db()
    va_service.dismiss(db, card_id=cid, user_id=_uid())
    return jsonify({"ok": True})


@phase4_api_bp.route("/va/cards/<int:cid>/snooze", methods=["POST"])
@login_required
def va_snooze(cid: int):
    db = get_db()
    data = request.get_json(silent=True) or {}
    va_service.snooze(db, card_id=cid, user_id=_uid(), hours=int(data.get("hours") or 24))
    return jsonify({"ok": True})


# --- Admin pedagogy ---
@phase4_api_bp.route("/admin/pedagogy/proposals", methods=["GET"])
@login_required
def admin_pedagogy_list():
    if _role() not in ("admin", "platform_admin", "district_admin"):
        return _json_error("Admin only", 403)
    db = get_db()
    rows = pedagogy_service.list_pending_proposals(db)
    return jsonify(
        {
            "items": [
                {
                    "id": r.id,
                    "syllabus_topic_id": r.syllabus_topic_id,
                    "proposed_body": r.proposed_body[:2000],
                    "critique": json.loads(r.critique_json or "{}"),
                }
                for r in rows
            ]
        }
    )


@phase4_api_bp.route("/admin/pedagogy/proposals/<int:pid>/approve", methods=["POST"])
@login_required
def admin_pedagogy_approve(pid: int):
    if _role() not in ("admin", "platform_admin", "district_admin"):
        return _json_error("Admin only", 403)
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        pedagogy_service.approve_proposal(db, proposal_id=pid, reviewer_user_id=_uid(), edited_body=data.get("body"))
    except ValueError:
        return _json_error("invalid", 400)
    return jsonify({"ok": True})


@phase4_api_bp.route("/admin/pedagogy/proposals/<int:pid>/reject", methods=["POST"])
@login_required
def admin_pedagogy_reject(pid: int):
    if _role() not in ("admin", "platform_admin", "district_admin"):
        return _json_error("Admin only", 403)
    db = get_db()
    data = request.get_json(silent=True) or {}
    pedagogy_service.reject_proposal(db, proposal_id=pid, reviewer_user_id=_uid(), reason=str(data.get("reason") or ""))
    return jsonify({"ok": True})


# --- Learning record (student self or teacher/parent via other routes) ---
@phase4_api_bp.route("/learning-record", methods=["GET"])
@login_required
def learning_record():
    if _role() != "student":
        return _json_error("Students only", 403)
    db = get_db()
    snap = intelligence_service.latest_snapshot_dict(db, student_user_id=_uid())
    dna = intelligence_service.compute_cognitive_dna(db, student_user_id=_uid())
    ret = intelligence_service.retention_map(db, student_user_id=_uid())
    return jsonify(
        {
            "intelligence": snap,
            "cognitive_dna": dna,
            "retention": ret,
            "prediction_disclaimer": intelligence_service.disclaimer(),
        }
    )
