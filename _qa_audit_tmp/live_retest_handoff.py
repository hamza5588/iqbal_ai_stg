#!/usr/bin/env python
"""Live retest of specialist-handoff after deploy. Isolated thread; does not mutate conv 116."""
from __future__ import annotations

import json
import time
from datetime import datetime

SOURCE_THREAD = "user_2_conv_116_1786901019_6166c3a2"

TURNS = [
    "PLEASE ADD TGHE EXAMPLE IN THE ELCTURE",
    "WHAT DID I ASK??",
    "SAVE THE LESSON",
]


def _preview(text: str, n: int = 500) -> str:
    text = (text or "").replace("\r\n", "\n").strip()
    if len(text) <= n:
        return text
    return text[:n] + "…"


def main() -> int:
    from langchain_core.messages import AIMessage, HumanMessage

    from app import create_app
    from app.models.database_models import RAGThread, RouterDecisionEvent
    from app.utils.db import get_db
    from app.utils.rag_service import _select_intent_tool_names, chatbot

    print("helpers:", {
        "lesson_save": _select_intent_tool_names("lesson_save"),
        "lesson_modification": _select_intent_tool_names("lesson_modification"),
        "meta_conversation": _select_intent_tool_names("meta_conversation"),
    })

    app = create_app()
    results = []
    with app.app_context():
        db = get_db()
        src = db.query(RAGThread).filter_by(thread_id=SOURCE_THREAD).first()
        if not src:
            print("FAIL: source thread not found:", SOURCE_THREAD)
            return 2

        from sqlalchemy import text as sql_text

        hist = db.execute(
            sql_text(
                "SELECT role, message FROM chat_history "
                "WHERE conversation_id = 116 ORDER BY id"
            )
        ).fetchall()
        prior = []
        for role, message in hist:
            msg = (message or "").strip()
            if not msg:
                continue
            if str(role).lower() in ("user", "human"):
                prior.append(HumanMessage(content=msg))
            else:
                prior.append(AIMessage(content=msg))
            if len(prior) >= 4:
                break

        if len(prior) < 4:
            print("FAIL: expected 4 prior messages from conv 116, got", len(prior))
            return 2

        thread_id = f"user_2_retest_handoff_{int(time.time())}"
        clone = RAGThread(
            user_id=src.user_id,
            thread_id=thread_id,
            name="retest specialist handoff (do not use)",
            filename=src.filename,
            has_document=True,
            doc_count=src.doc_count,
            num_pages=src.num_pages,
            last_ingested_at=src.last_ingested_at,
            embedding_model=src.embedding_model,
            embedding_dim=src.embedding_dim,
            lesson_finalized=False,
            last_lesson_text=src.last_lesson_text,
            lesson_title=src.lesson_title,
            headings_ready=src.headings_ready,
            headings_count=src.headings_count,
            gk_consent_state="none",
            ingest_status="success",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(clone)
        db.commit()
        print("cloned thread:", thread_id)
        print("draft_chars:", len(src.last_lesson_text or ""))
        print("draft_title:", src.lesson_title)

        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": 38,
        }

        from app.utils.llm_gateway import update_llm_telemetry_context

        seed_messages = list(prior)
        for i, user_text in enumerate(TURNS):
            started = time.time()
            invoke_messages = seed_messages + [HumanMessage(content=user_text)] if i == 0 else [
                HumanMessage(content=user_text)
            ]
            # The real /api/rag/chat route sets this before invoking the graph; the
            # RouterDecisionEvent row's thread_id/conversation_id come from this
            # ContextVar, not from the LangGraph `config`. Without it, tracing rows
            # are still written (intent/prefetch_branch correct) but with a blank
            # thread_id, making them unfindable by thread_id.
            update_llm_telemetry_context(
                workflow="rag_chat",
                thread_id=thread_id,
                conversation_id=None,
                user_id=src.user_id,
            )
            state = chatbot.invoke({"messages": invoke_messages}, config=config)
            elapsed = int((time.time() - started) * 1000)
            messages = state.get("messages") or []
            reply = ""
            if messages:
                content = getattr(messages[-1], "content", "") or ""
                reply = content if isinstance(content, str) else str(content)

            db.expire_all()
            ev = (
                db.query(RouterDecisionEvent)
                .filter(RouterDecisionEvent.thread_id == thread_id)
                .order_by(RouterDecisionEvent.id.desc())
                .first()
            )
            row = db.query(RAGThread).filter_by(thread_id=thread_id).first()
            rec = {
                "turn": i + 1,
                "user": user_text,
                "elapsed_ms": elapsed,
                "router_intent": getattr(ev, "intent", None) if ev else state.get("router_intent"),
                "prefetch_branch": getattr(ev, "prefetch_branch", None) if ev else None,
                "meta_conversation_active": bool(getattr(ev, "meta_conversation_active", False)) if ev else None,
                "tool_rounds_used": getattr(ev, "tool_rounds_used", None) if ev else None,
                "lesson_finalized": bool(getattr(row, "lesson_finalized", False)),
                "last_lesson_chars": len((getattr(row, "last_lesson_text", None) or "")),
                "reply_preview": _preview(reply, 700),
            }
            results.append(rec)
            print(json.dumps(rec, ensure_ascii=False, indent=2))
            seed_messages = []

        print("\n=== VERDICT ===")
        fail = []
        t1, t2, t3 = results
        if t1.get("prefetch_branch") != "specialist_handoff":
            fail.append(f"turn1 prefetch={t1.get('prefetch_branch')} expected specialist_handoff")
        if t1.get("router_intent") not in ("lesson_modification",):
            fail.append(f"turn1 intent={t1.get('router_intent')} expected lesson_modification")
        reply1 = (t1.get("reply_preview") or "").lower()
        if "not present in the document" in reply1:
            fail.append("turn1 used not-in-document canned phrase")
        if t1.get("last_lesson_chars", 0) <= 0:
            fail.append("turn1 draft empty")

        if t2.get("router_intent") != "meta_conversation":
            fail.append(f"turn2 intent={t2.get('router_intent')} expected meta_conversation")
        reply2 = (t2.get("reply_preview") or "").lower()
        if "not present in the document" in reply2 or "knowledge base" in reply2:
            fail.append("turn2 used document/GK canned phrase")
        if "please add" not in reply2 and "elcture" not in reply2 and "lecture" not in reply2:
            fail.append("turn2 did not quote the previous user request")

        if t3.get("prefetch_branch") != "specialist_handoff":
            fail.append(f"turn3 prefetch={t3.get('prefetch_branch')} expected specialist_handoff")
        if t3.get("router_intent") != "lesson_save":
            fail.append(f"turn3 intent={t3.get('router_intent')} expected lesson_save")
        if not t3.get("lesson_finalized"):
            fail.append("turn3 did not finalize the lesson")
        reply3 = (t3.get("reply_preview") or "").lower()
        if "not present in the document" in reply3:
            fail.append("turn3 used not-in-document canned phrase")
        if "misunderstand" in reply3 and not t3.get("lesson_finalized"):
            fail.append("turn3 misunderstood instead of saving")

        if fail:
            print("FAIL")
            for f in fail:
                print("-", f)
            return 1
        print("PASS")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
