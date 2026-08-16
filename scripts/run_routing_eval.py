#!/usr/bin/env python
"""
Phase 4: routing regression eval runner.

Runs the fixture cases in tests/eval_fixtures/routing_regressions.jsonl against the REAL,
currently-configured LLM provider (whatever LLM_PROVIDER / model env vars the environment
already has set - this deliberately does not use a separate eval-only provider, since the point
is to catch drift in what's actually deployed). Each case makes real LLM calls through the
actual compiled chat graph (app.utils.rag_service.chatbot), so this script:

  * is NOT pytest-collected (no test_ prefix, lives outside tests/) and must never run per-PR /
    per-commit in CI - it costs real LLM spend and is comparatively slow;
  * is meant to be run periodically (e.g. a nightly/weekly scheduled job) or manually against a
    real environment with DATABASE_URL, an LLM provider API key, and (for document_qa cases) a
    working ingestion path (Chroma local or Milvus) already configured;
  * requires a full Flask app context - run it with the same environment you'd use to run the
    app itself (`python scripts/run_routing_eval.py`), not inside a bare test runner.

Usage:
    python scripts/run_routing_eval.py
    python scripts/run_routing_eval.py --case bug-001-discriminant-brevity
    python scripts/run_routing_eval.py --fixtures tests/eval_fixtures/routing_regressions.jsonl \
        --out-dir eval_reports --known-failing tests/eval_fixtures/known_failing.txt

Output:
    eval_reports/routing_eval_<UTC timestamp>.json  - full per-case detail
    eval_reports/routing_eval_<UTC timestamp>.md     - human-readable summary
    eval_reports/history.csv                         - one row appended per run (trend over time)

Exit code: 0 if every case not listed in --known-failing passed, 1 otherwise. This is the signal
a scheduled CI job should alert on.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DEFAULT = REPO_ROOT / "tests" / "eval_fixtures" / "routing_regressions.jsonl"
PRIOR_TURNS_DIR = REPO_ROOT / "tests" / "eval_fixtures" / "prior_turns"
DOCS_DIR = REPO_ROOT / "tests" / "eval_fixtures" / "docs"
OUT_DIR_DEFAULT = REPO_ROOT / "eval_reports"

EVAL_USER_EMAIL = "phase4-routing-eval@iqbalai.internal"
EVAL_USER_USERNAME = "phase4_routing_eval_bot"


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    cases = []
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {e}") from e
    return cases


def _resolve_prior_turns(setup: Dict[str, Any]) -> Dict[str, Any]:
    """
    Resolves setup["prior_turns"] into a dict with `prior_user_turns` (list[str]) and an
    optional `document_fixture` inherited from the referenced fixture file, if any.
    Accepts either an inline list of prior user message strings, or "@fixture:<name>" pointing
    at tests/eval_fixtures/prior_turns/<name>.json.
    """
    prior_turns = setup.get("prior_turns")
    if not prior_turns:
        return {"prior_user_turns": [], "document_fixture": None}
    if isinstance(prior_turns, str) and prior_turns.startswith("@fixture:"):
        name = prior_turns[len("@fixture:"):]
        fixture_path = PRIOR_TURNS_DIR / f"{name}.json"
        with open(fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "prior_user_turns": data.get("prior_user_turns", []),
            "document_fixture": data.get("document_fixture"),
        }
    if isinstance(prior_turns, list):
        return {"prior_user_turns": prior_turns, "document_fixture": None}
    raise ValueError(f"Unrecognized prior_turns value: {prior_turns!r}")


def _text_fixture_to_pdf_bytes(text: str) -> bytes:
    """Render a plain-text fixture doc to a minimal PDF using reportlab (already a project dep),
    so eval fixtures stay human-readable/diffable in git while still exercising the real PDF
    ingestion pipeline (app.utils.rag_service.ingest_pdf expects PDF bytes, not plain text)."""
    from io import BytesIO

    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER
    x, y = 60, height - 60
    line_height = 14
    for raw_line in text.splitlines():
        # Wrap very long lines so nothing runs off the page edge.
        line = raw_line
        while len(line) > 100:
            c.drawString(x, y, line[:100])
            line = line[100:]
            y -= line_height
            if y < 60:
                c.showPage()
                y = height - 60
        c.drawString(x, y, line)
        y -= line_height
        if y < 60:
            c.showPage()
            y = height - 60
    c.save()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Behavior assertions
# ---------------------------------------------------------------------------


def _assert_max_sentences(reply_text: str, value: int) -> Optional[str]:
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", (reply_text or "").strip()) if s.strip()]
    if len(sentences) > value:
        return f"expected <= {value} sentences, got {len(sentences)}"
    return None


def _assert_not_contains_any(reply_text: str, values: List[str]) -> Optional[str]:
    low = (reply_text or "").lower()
    hits = [v for v in values if v.lower() in low]
    if hits:
        return f"reply unexpectedly contains: {hits}"
    return None


def _assert_contains_any(reply_text: str, values: List[str]) -> Optional[str]:
    low = (reply_text or "").lower()
    if not any(v.lower() in low for v in values):
        return f"reply contains none of the expected substrings: {values}"
    return None


def _assert_contains_exact_prior_user_text(
    reply_text: str, prior_user_turns: List[str]
) -> Optional[str]:
    if not prior_user_turns:
        return "no prior_user_turns available to check exact-text retrieval against"
    target = prior_user_turns[-1].strip().lower()
    if target not in (reply_text or "").lower():
        return f"reply does not contain the exact prior question text: {prior_user_turns[-1]!r}"
    return None


_ASSERTION_HANDLERS = {
    "max_sentences": lambda reply, v, prior: _assert_max_sentences(reply, v),
    "not_contains_any": lambda reply, v, prior: _assert_not_contains_any(reply, v),
    "contains_any": lambda reply, v, prior: _assert_contains_any(reply, v),
    "contains_exact_prior_user_text": lambda reply, v, prior: (
        _assert_contains_exact_prior_user_text(reply, prior) if v else None
    ),
}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    router_intent: Optional[str] = None
    reply_text: str = ""
    failures: List[str] = field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _run_one_case(case: Dict[str, Any], run_tag: str) -> CaseResult:
    from langchain_core.messages import HumanMessage

    from app.utils.db import get_db
    from app.models.database_models import User, RAGThread
    from app.utils.rag_service import chatbot, ingest_pdf

    case_id = case["id"]
    setup = case.get("setup", {})
    resolved = _resolve_prior_turns(setup)
    prior_user_turns: List[str] = resolved["prior_user_turns"]
    document_fixture = setup.get("document_fixture") or resolved.get("document_fixture")

    db = get_db()
    user = db.query(User).filter_by(useremail=EVAL_USER_EMAIL).first()
    if not user:
        return CaseResult(case_id, passed=False, error=(
            f"Eval user {EVAL_USER_EMAIL!r} not found - create it once (role=student, any "
            "class_standard/medium/groq_api_key placeholder) before running this script."
        ))

    thread_id = f"eval-{run_tag}-{case_id}"
    thread = RAGThread(
        user_id=user.id,
        thread_id=thread_id,
        name=f"Phase4 eval: {case_id}",
        filename=document_fixture,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(thread)
    db.commit()

    try:
        if document_fixture:
            doc_path = DOCS_DIR / document_fixture
            text = doc_path.read_text(encoding="utf-8")
            pdf_bytes = _text_fixture_to_pdf_bytes(text)
            ingest_pdf(pdf_bytes, thread_id, filename=document_fixture, user_id=user.id)

        config = {"configurable": {"thread_id": thread_id}}

        # Replay prior turns for real (each is a genuine LLM call through the actual graph) so
        # the meta-conversation fixtures have real conversation history to retrieve from.
        for prior_text in prior_user_turns:
            chatbot.invoke({"messages": [HumanMessage(content=prior_text)]}, config=config)

        state = chatbot.invoke(
            {"messages": [HumanMessage(content=case["input"])]}, config=config
        )
        messages = state.get("messages", [])
        reply_text = ""
        if messages:
            content = getattr(messages[-1], "content", "") or ""
            reply_text = content if isinstance(content, str) else str(content)

        router_intent = state.get("router_intent")
        failures: List[str] = []

        expected = case.get("expected", {})
        if "intent" in expected and expected["intent"] != router_intent:
            failures.append(f"intent: expected {expected['intent']!r}, got {router_intent!r}")
        if "requested_brevity" in expected:
            got = state.get("router_requested_brevity")
            if bool(expected["requested_brevity"]) != bool(got):
                failures.append(
                    f"requested_brevity: expected {expected['requested_brevity']!r}, got {got!r}"
                )
        if "meta_conversation_scope" in expected:
            got = state.get("router_meta_scope")
            if expected["meta_conversation_scope"] != got:
                failures.append(
                    f"meta_conversation_scope: expected {expected['meta_conversation_scope']!r}, got {got!r}"
                )
        if "meta_conversation_n" in expected:
            got = state.get("router_meta_n")
            if expected["meta_conversation_n"] != got:
                failures.append(
                    f"meta_conversation_n: expected {expected['meta_conversation_n']!r}, got {got!r}"
                )

        for assertion in case.get("behavior_assertions", []):
            handler = _ASSERTION_HANDLERS.get(assertion["type"])
            if handler is None:
                failures.append(f"unknown behavior_assertion type: {assertion['type']!r}")
                continue
            msg = handler(reply_text, assertion.get("value"), prior_user_turns)
            if msg:
                failures.append(f"{assertion['type']}: {msg}")

        return CaseResult(
            case_id=case_id,
            passed=not failures,
            router_intent=router_intent,
            reply_text=reply_text,
            failures=failures,
        )
    except Exception as e:
        return CaseResult(case_id=case_id, passed=False, error=f"{type(e).__name__}: {e}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fixtures", type=Path, default=FIXTURES_DEFAULT)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR_DEFAULT)
    parser.add_argument("--known-failing", type=Path, default=None, help=(
        "Optional text file, one case id per line, allowed to fail without affecting exit code "
        "(xfail-style, for cases filed but not yet fixed)."
    ))
    parser.add_argument("--case", type=str, default=None, help="Run only this one case id.")
    args = parser.parse_args()

    known_failing = set()
    if args.known_failing and args.known_failing.exists():
        known_failing = {
            line.strip() for line in args.known_failing.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }

    cases = _load_jsonl(args.fixtures)
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
        if not cases:
            print(f"No case with id={args.case!r} found in {args.fixtures}", file=sys.stderr)
            return 2

    from app import create_app

    app = create_app()
    run_tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    results: List[CaseResult] = []
    with app.app_context():
        for case in cases:
            print(f"Running {case['id']}...", flush=True)
            t0 = time.perf_counter()
            result = _run_one_case(case, run_tag)
            elapsed = time.perf_counter() - t0
            status = "PASS" if result.passed else "FAIL"
            print(f"  {status} ({elapsed:.1f}s)" + (f" - {result.error}" if result.error else ""))
            for f in result.failures:
                print(f"    - {f}")
            results.append(result)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "run_tag": run_tag,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "fixtures_file": str(args.fixtures),
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "cases": [
            {
                "id": r.case_id,
                "passed": r.passed,
                "router_intent": r.router_intent,
                "reply_text": r.reply_text,
                "failures": r.failures,
                "error": r.error,
                "known_failing": r.case_id in known_failing,
            }
            for r in results
        ],
    }
    json_path = args.out_dir / f"routing_eval_{run_tag}.json"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    md_lines = [
        f"# Routing eval report - {run_tag}",
        "",
        f"{report['passed']}/{report['total']} passed.",
        "",
        "| case | status | notes |",
        "|---|---|---|",
    ]
    for r in results:
        status = "PASS" if r.passed else ("FAIL (known)" if r.case_id in known_failing else "FAIL")
        notes = r.error or "; ".join(r.failures) or ""
        md_lines.append(f"| {r.case_id} | {status} | {notes} |")
    md_path = args.out_dir / f"routing_eval_{run_tag}.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    history_path = args.out_dir / "history.csv"
    is_new = not history_path.exists()
    with open(history_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["timestamp_utc", "total", "passed", "failed", "pass_rate"])
        pass_rate = (report["passed"] / report["total"]) if report["total"] else 0.0
        writer.writerow([report["timestamp_utc"], report["total"], report["passed"], report["failed"], f"{pass_rate:.3f}"])

    print(f"\nReport written to {json_path} and {md_path}")

    # Exit non-zero only for failures NOT on the known-failing allowlist, so a scheduled CI job
    # can alert on genuinely new regressions without needing zero known issues first.
    unexpected_failures = [r for r in results if not r.passed and r.case_id not in known_failing]
    return 1 if unexpected_failures else 0


if __name__ == "__main__":
    sys.exit(main())
