#!/usr/bin/env python3
"""
LMS load test — 200 student journey (login → diagnostic → learning chat → quiz).
Run after setup_loadtest_prereqs.py. Writes metrics to _qa_audit_tmp/load_test/.
"""
from __future__ import annotations

import json
import random
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "_qa_audit_tmp" / "load_test"
CFG_PATH = OUT / "loadtest_config.json"

BASE_URL = "https://209.23.10.34"
STUDENT_PASSWORD = "LoadTest2026!"
NUM_STUDENTS = 200

# Load profile (seconds)
WARMUP_END = 120
RAMP_END = 240
STEADY_END = 1440  # 24 min total from start
TOTAL_END = 1680  # 28 min

EXPLAIN_FRACTION = 0.20
STOP_ERROR_RATE = 0.05
STOP_WINDOW_SEC = 300


@dataclass
class Metrics:
    lock: threading.Lock = field(default_factory=threading.Lock)
    records: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stop: bool = False

    def add(self, scenario: str, ok: bool, latency: float, detail: str = ""):
        with self.lock:
            self.records.append(
                {"scenario": scenario, "ok": ok, "latency": latency, "detail": detail[:200], "ts": time.time()}
            )
            if not ok and detail:
                self.errors.append(f"{scenario}: {detail[:120]}")

    def summary(self) -> dict:
        by_scenario: dict[str, list[float]] = {}
        ok_count: dict[str, int] = {}
        fail_count: dict[str, int] = {}
        for r in self.records:
            s = r["scenario"]
            by_scenario.setdefault(s, []).append(r["latency"])
            if r["ok"]:
                ok_count[s] = ok_count.get(s, 0) + 1
            else:
                fail_count[s] = fail_count.get(s, 0) + 1

        def pct(vals: list[float], p: float) -> Optional[float]:
            if not vals:
                return None
            vs = sorted(vals)
            i = min(len(vs) - 1, int(len(vs) * p))
            return round(vs[i], 3)

        rows = {}
        for s in set(ok_count) | set(fail_count):
            total = ok_count.get(s, 0) + fail_count.get(s, 0)
            lat = by_scenario.get(s, [])
            rows[s] = {
                "success_pct": round(100.0 * ok_count.get(s, 0) / total, 2) if total else 0,
                "count": total,
                "p50": pct(lat, 0.5),
                "p95": pct(lat, 0.95),
                "p99": pct(lat, 0.99),
            }
        total = len(self.records)
        ok = sum(1 for r in self.records if r["ok"])
        return {
            "total_requests": total,
            "success_pct": round(100.0 * ok / total, 2) if total else 0,
            "by_scenario": rows,
            "top_errors": self.errors[:15],
        }


def load_config() -> dict:
    if CFG_PATH.is_file():
        return json.loads(CFG_PATH.read_text(encoding="utf-8"))
    return {}


def unwrap(body: Any) -> Any:
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def err_msg(body: Any) -> str:
    if isinstance(body, dict):
        e = body.get("error")
        if isinstance(e, dict):
            return e.get("message", str(e))
        return str(e or body)
    return str(body)[:200]


class StudentSession:
    def __init__(self, email: str, password: str, cfg: dict):
        self.email = email
        self.s = requests.Session()
        self.s.verify = False
        self.cfg = cfg

    def _timed(self, metrics: Metrics, scenario: str, fn):
        t0 = time.time()
        try:
            ok, detail = fn()
            metrics.add(scenario, ok, time.time() - t0, detail if not ok else "")
            return ok
        except Exception as exc:
            metrics.add(scenario, False, time.time() - t0, str(exc))
            return False

    def login(self, metrics: Metrics) -> bool:
        def do():
            r = self.s.post(
                f"{BASE_URL}/auth/login",
                data={"useremail": self.email, "password": STUDENT_PASSWORD},
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
                timeout=60,
            )
            if r.status_code != 200:
                return False, f"HTTP {r.status_code}"
            try:
                if r.json().get("success"):
                    return True, ""
            except Exception:
                pass
            return True, ""

        return self._timed(metrics, "login", do)

    def join_class(self, metrics: Metrics) -> bool:
        code = self.cfg.get("join_code")
        if not code:
            return True

        def do():
            r = self.s.post(f"{BASE_URL}/api/lms/classes/join", json={"join_code": code}, timeout=60)
            if r.status_code in (200, 201):
                return True, ""
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            msg = err_msg(body)
            if "already" in msg.lower():
                return True, ""
            return False, msg

        return self._timed(metrics, "join_class", do)

    def run_journey(self, metrics: Metrics, user_index: int) -> None:
        if metrics.stop:
            return
        if not self.login(metrics):
            return
        time.sleep(random.uniform(2, 5))
        if not self.join_class(metrics):
            return
        time.sleep(random.uniform(2, 5))

        # Dashboard
        def dash():
            r = self.s.get(f"{BASE_URL}/api/lms/students/me/dashboard", timeout=60)
            return r.status_code == 200, err_msg(r.json() if r.ok else r.text)

        self._timed(metrics, "dashboard", dash)
        time.sleep(random.uniform(2, 4))

        # Diagnostic
        diag_id = self.cfg.get("diagnostic_id")
        if not diag_id:
            r = self.s.get(f"{BASE_URL}/api/lms/diagnostics/default", timeout=60)
            if r.status_code == 200:
                diag_id = unwrap(r.json()).get("id")

        attempt_id = None

        def diag_start():
            nonlocal attempt_id
            r = self.s.post(f"{BASE_URL}/api/lms/quizzes/{diag_id}/start", json={}, timeout=60)
            if r.status_code not in (200, 201):
                return False, err_msg(r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)
            attempt_id = unwrap(r.json()).get("attempt_id")
            return bool(attempt_id), "no attempt_id"

        if not self._timed(metrics, "diagnostic_start", diag_start):
            return

        def get_q():
            r = self.s.get(f"{BASE_URL}/api/lms/attempts/{attempt_id}/questions", timeout=90)
            if r.status_code != 200:
                return False, err_msg(r.json())
            qs = unwrap(r.json()).get("questions") or []
            return bool(qs), f"questions={len(qs)}"

        if not self._timed(metrics, "diagnostic_questions", get_q):
            return

        r = self.s.get(f"{BASE_URL}/api/lms/attempts/{attempt_id}/questions", timeout=90)
        questions = unwrap(r.json()).get("questions") or []

        for q in questions:
            if metrics.stop:
                return
            qid = q.get("question_id")
            pick = random.randint(0, 3)

            def ans(qq=qid, pp=pick):
                r2 = self.s.post(
                    f"{BASE_URL}/api/lms/attempts/{attempt_id}/answer",
                    json={"question_id": qq, "selected_option_index": pp},
                    timeout=60,
                )
                return r2.status_code == 200, err_msg(r2.json() if r2.headers.get("content-type", "").startswith("application/json") else "")

            self._timed(metrics, "diagnostic_answer", ans)
            time.sleep(random.uniform(3, 8))

        def diag_submit():
            r3 = self.s.post(f"{BASE_URL}/api/lms/attempts/{attempt_id}/submit", timeout=120)
            if r3.status_code != 200:
                return False, err_msg(r3.json())
            return True, ""

        if not self._timed(metrics, "diagnostic_submit", diag_submit):
            return
        time.sleep(random.uniform(2, 5))

        # Learning chat
        session_id = None

        def chat_start():
            nonlocal session_id
            r = self.s.post(
                f"{BASE_URL}/api/lms/deficiency/sessions",
                json={"force_new": True},
                timeout=120,
            )
            if r.status_code not in (200, 201):
                return False, err_msg(r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)
            session_id = unwrap(r.json()).get("session_id")
            return bool(session_id), err_msg(r.json())

        if not self._timed(metrics, "learning_chat_start", chat_start):
            return

        for _ in range(3):
            if metrics.stop:
                return

            def chat_ans():
                r = self.s.post(
                    f"{BASE_URL}/api/lms/deficiency/sessions/{session_id}/answer",
                    json={"selected_option_index": random.randint(0, 3)},
                    timeout=90,
                )
                return r.status_code == 200, err_msg(r.json() if r.headers.get("content-type", "").startswith("application/json") else "")

            self._timed(metrics, "learning_chat_answer", chat_ans)
            time.sleep(random.uniform(2, 5))

        if user_index % int(1 / EXPLAIN_FRACTION) == 0:

            def explain():
                r = self.s.post(
                    f"{BASE_URL}/api/lms/deficiency/sessions/{session_id}/explain",
                    json={"message": "Explain this step by step"},
                    timeout=120,
                )
                return r.status_code == 200, err_msg(r.json() if r.headers.get("content-type", "").startswith("application/json") else "")

            self._timed(metrics, "learning_chat_explain", explain)
            time.sleep(random.uniform(2, 4))

        # Quiz
        quiz_id = self.cfg.get("quiz_id")
        assignment_id = self.cfg.get("assignment_id")
        if not quiz_id:
            r = self.s.get(f"{BASE_URL}/api/lms/students/me/assignments", timeout=60)
            if r.status_code == 200:
                items = unwrap(r.json()) or []
                for a in items:
                    if a.get("status") != "submitted":
                        quiz_id = a.get("quiz_id")
                        assignment_id = a.get("assignment_id")
                        break

        if not quiz_id:
            metrics.add("quiz_start", False, 0, "no quiz assignment")
            return

        quiz_attempt = None

        def quiz_start():
            nonlocal quiz_attempt
            body = {"assignment_id": assignment_id} if assignment_id else {}
            r = self.s.post(f"{BASE_URL}/api/lms/quizzes/{quiz_id}/start", json=body, timeout=60)
            if r.status_code not in (200, 201):
                return False, err_msg(r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)
            quiz_attempt = unwrap(r.json()).get("attempt_id")
            return bool(quiz_attempt), "no attempt"

        if not self._timed(metrics, "quiz_start", quiz_start):
            return

        rq = self.s.get(f"{BASE_URL}/api/lms/attempts/{quiz_attempt}/questions", timeout=90)
        qlist = unwrap(rq.json()).get("questions") or [] if rq.status_code == 200 else []

        for q in qlist:
            if metrics.stop:
                return
            qid = q.get("question_id")
            pick = random.randint(0, 3)

            def qans(qq=qid, pp=pick):
                r2 = self.s.post(
                    f"{BASE_URL}/api/lms/attempts/{quiz_attempt}/answer",
                    json={"question_id": qq, "selected_option_index": pp},
                    timeout=60,
                )
                return r2.status_code == 200, ""

            self._timed(metrics, "quiz_answer", qans)
            time.sleep(random.uniform(2, 6))

        def quiz_submit():
            r3 = self.s.post(f"{BASE_URL}/api/lms/attempts/{quiz_attempt}/submit", timeout=90)
            return r3.status_code == 200, err_msg(r3.json() if r.headers.get("content-type", "").startswith("application/json") else "")

        self._timed(metrics, "quiz_submit", quiz_submit)

        if random.random() < 0.1:
            self.s.get(f"{BASE_URL}/auth/logout", timeout=30)


def target_users(elapsed: float) -> int:
    if elapsed < WARMUP_END:
        return max(1, int(20 * elapsed / WARMUP_END))
    if elapsed < RAMP_END:
        frac = (elapsed - WARMUP_END) / (RAMP_END - WARMUP_END)
        return int(20 + 180 * frac)
    if elapsed < STEADY_END:
        return 200
    if elapsed < TOTAL_END:
        frac = 1.0 - (elapsed - STEADY_END) / (TOTAL_END - STEADY_END)
        return max(0, int(200 * frac))
    return 0


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    if not cfg.get("diagnostic_id"):
        print("Run setup_loadtest_prereqs.py first")
        return 1

    metrics = Metrics()
    start = time.time()
    spawned = 0
    futures = []
    print(f"Load test starting — target peak {NUM_STUDENTS} users, duration {TOTAL_END}s")

    with ThreadPoolExecutor(max_workers=NUM_STUDENTS) as pool:
        while time.time() - start < TOTAL_END and not metrics.stop:
            elapsed = time.time() - start
            want = min(NUM_STUDENTS, target_users(elapsed))
            while spawned < want and spawned < NUM_STUDENTS:
                spawned += 1
                email = f"loadtest_student_{spawned:03d}@test.iqbalai.local"
                fut = pool.submit(StudentSession(email, STUDENT_PASSWORD, cfg).run_journey, metrics, spawned)
                futures.append(fut)
                time.sleep(0.05)
            # stop check
            recent = [r for r in metrics.records if r["ts"] > time.time() - STOP_WINDOW_SEC]
            if len(recent) > 50:
                fails = sum(1 for r in recent if not r["ok"])
                if fails / len(recent) > STOP_ERROR_RATE:
                    print("STOP: error rate > 5% in 5 min window")
                    metrics.stop = True
                    break
            time.sleep(1)

        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as exc:
                metrics.errors.append(str(exc))

    summary = metrics.summary()
    summary["peak_spawned"] = spawned
    summary["duration_sec"] = round(time.time() - start, 1)
    summary["base_url"] = BASE_URL
    summary["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    # Verdict
    login_ok = summary["by_scenario"].get("login", {}).get("success_pct", 0)
    diag_ok = summary["by_scenario"].get("diagnostic_submit", {}).get("success_pct", 0)
    chat_ok = summary["by_scenario"].get("learning_chat_start", {}).get("success_pct", 0)
    quiz_ok = summary["by_scenario"].get("quiz_submit", {}).get("success_pct", 0)
    summary["verdict"] = "PASS" if login_ok >= 99 and diag_ok >= 95 and quiz_ok >= 95 else "FAIL"

    raw_path = OUT / "loadtest_raw_summary.json"
    raw_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    report = build_report(summary)
    report_path = OUT / "LOAD_TEST_REPORT.md"
    report_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nWrote {report_path}")
    return 0 if summary["verdict"] == "PASS" else 1


def build_report(s: dict) -> str:
    lines = [
        f"# Load Test Report — {s.get('timestamp')} — {s.get('base_url')}",
        "",
        "## Summary",
        f"- Peak users spawned: {s.get('peak_spawned')}",
        f"- Total steps: {s.get('total_requests')} | Success: {s.get('success_pct')}%",
        f"- Duration: {s.get('duration_sec')}s",
        f"- Verdict: **{s.get('verdict')}**",
        "",
        "## Results by scenario",
        "| Scenario | Success % | Count | p50 | p95 | p99 |",
        "|----------|-----------|-------|-----|-----|-----|",
    ]
    for name, row in sorted(s.get("by_scenario", {}).items()):
        lines.append(
            f"| {name} | {row.get('success_pct')} | {row.get('count')} | {row.get('p50')} | {row.get('p95')} | {row.get('p99')} |"
        )
    if s.get("top_errors"):
        lines.extend(["", "## Top errors", ""])
        for e in s["top_errors"]:
            lines.append(f"- {e}")
    lines.extend(["", "## Files", f"- `{OUT / 'loadtest_raw_summary.json'}`", f"- `{OUT / 'loadtest_config.json'}`"])
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
