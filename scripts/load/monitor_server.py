#!/usr/bin/env python3
"""SSH-monitor staging during the LMS load test. Writes CSV + log under scripts/load/results/.

Uses OpenSSH (paramiko drops this host with EOFError). Samples every INTERVAL seconds
and watches for crashes, 5xx health failures, OOM, and Groq rate-limit signatures.
"""
from __future__ import annotations

import csv
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ssh_cli import APP, HOST, ssh_run

OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(parents=True, exist_ok=True)
INTERVAL = int(sys.argv[1]) if len(sys.argv) > 1 else 60
DURATION = int(sys.argv[2]) if len(sys.argv) > 2 else 1800

GROQ_RE = re.compile(
    r"rate.?limit|429|too many requests|tpm|tokens per minute|rpm|"
    r"groq.*error|RateLimit|rate_limit_exceeded",
    re.I,
)


def run(cmd: str, timeout: int = 60) -> str:
    try:
        code, out, err = ssh_run(cmd, timeout=timeout)
        return (out or "") + (("\n" + err) if err else "")
    except Exception as exc:
        return f"SSH_FAIL: {exc}"


def parse_stats(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.upper().startswith("CONTAINER"):
            continue
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 4:
            continue
        rows.append(
            {
                "name": parts[1] if len(parts) > 1 else parts[0],
                "cpu": parts[2] if len(parts) > 2 else "",
                "mem": parts[3] if len(parts) > 3 else "",
                "mem_pct": parts[4] if len(parts) > 4 else "",
            }
        )
    return rows


def main() -> int:
    csv_path = OUT / "docker_stats.csv"
    log_path = OUT / "monitor.log"
    groq_path = OUT / "groq_monitor.log"
    stop_path = OUT / "STOP_UNHEALTHY"
    for p in (csv_path, log_path, groq_path, stop_path):
        if p.exists():
            p.unlink()

    fieldnames = [
        "ts_utc",
        "container",
        "cpu",
        "mem",
        "mem_pct",
        "loadavg",
        "health",
        "lms_health",
        "compose_down",
        "groq_hits",
    ]
    start = time.time()
    sample = 0

    with csv_path.open("w", encoding="utf-8", newline="") as cf, log_path.open(
        "w", encoding="utf-8"
    ) as lf, groq_path.open("w", encoding="utf-8") as gf:
        writer = csv.DictWriter(cf, fieldnames=fieldnames)
        writer.writeheader()
        print(f"Monitoring {HOST} every {INTERVAL}s for {DURATION}s", flush=True)
        while time.time() - start <= DURATION:
            sample += 1
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            stats = run("docker stats --no-stream --format 'table {{.Name}}\\t{{.CPUPerc}}\\t{{.MemUsage}}\\t{{.MemPerc}}'")
            loadavg = run("cat /proc/loadavg").strip()
            health = run("curl -k -sS --max-time 10 https://127.0.0.1/health || echo FAIL").strip()
            lms = run(
                "curl -k -sS --max-time 10 https://127.0.0.1/api/lms/health || echo FAIL"
            ).strip()
            compose = run(f"cd {APP} && docker compose ps --format '{{.Name}} {{.Status}}'")
            redis = run(f"cd {APP} && docker compose exec -T redis redis-cli INFO memory | head -20")
            pg = run(
                f'cd {APP} && docker compose exec -T postgres sh -c \'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t -c "SELECT count(*) FROM pg_stat_activity;"\''
            )
            flask_logs = run(
                f"cd {APP} && docker compose logs --since {INTERVAL + 15}s flask_app1",
                timeout=90,
            )
            celery_logs = run(
                f"cd {APP} && docker compose logs --since {INTERVAL + 15}s celery_worker",
                timeout=90,
            )
            celery_q = run(
                f"cd {APP} && docker compose exec -T redis redis-cli LLEN celery; docker compose exec -T redis redis-cli LLEN default"
            )
            groq_lines = [
                ln
                for ln in (flask_logs + "\n" + celery_logs).splitlines()
                if GROQ_RE.search(ln)
            ]
            groq_hits = len(groq_lines)
            if groq_lines:
                gf.write(f"\n===== {ts} groq_hits={groq_hits} =====\n")
                gf.write("\n".join(groq_lines[-80:]) + "\n")
                gf.flush()

            down = [
                ln
                for ln in compose.splitlines()
                if ln.strip()
                and not ln.lower().startswith("name")
                and "up" not in ln.lower()
                and "healthy" not in ln.lower()
            ]
            compose_down = ";".join(down)[:200]

            lf.write(f"\n===== {ts} sample={sample} loadavg={loadavg} =====\n")
            lf.write(f"health={health}\nlms={lms}\ncompose_down={compose_down}\ngroq_hits={groq_hits}\n")
            lf.write(f"redis_mem:\n{redis}\n")
            lf.write(f"pg_activity_count:{pg}\n")
            lf.write(f"celery_queue_lens:\n{celery_q}\n")
            lf.write("--- compose ---\n")
            lf.write(compose)
            lf.write("\n--- flask ---\n")
            lf.write(flask_logs[-5000:])
            lf.write("\n--- celery ---\n")
            lf.write(celery_logs[-3000:])
            lf.write("\n--- docker stats ---\n")
            lf.write(stats)
            lf.flush()

            health_fail = (
                "FAIL" in health
                or "FAIL" in lms
                or "SSH_FAIL" in health
                or '"healthy"' not in health
            )
            oom = "oom" in (flask_logs + celery_logs).lower() or "killed process" in (
                flask_logs + celery_logs
            ).lower()
            crashed = bool(down) or "exited" in compose.lower() or "restarting" in compose.lower()
            if health_fail or oom or crashed:
                stop_path.write_text(
                    f"{ts} unhealthy={health_fail} oom={oom} crashed={crashed}\n"
                    f"health={health}\nlms={lms}\ncompose_down={compose_down}\n",
                    encoding="utf-8",
                )
                print(
                    f"STOP CONDITION at {ts}: unhealthy={health_fail} oom={oom} crashed={crashed}",
                    flush=True,
                )

            rows = parse_stats(stats)
            if not rows:
                writer.writerow(
                    {
                        "ts_utc": ts,
                        "container": "(none)",
                        "cpu": "",
                        "mem": "",
                        "mem_pct": "",
                        "loadavg": loadavg,
                        "health": health[:120],
                        "lms_health": lms[:120],
                        "compose_down": compose_down,
                        "groq_hits": groq_hits,
                    }
                )
            else:
                for row in rows:
                    writer.writerow(
                        {
                            "ts_utc": ts,
                            "container": row["name"],
                            "cpu": row["cpu"],
                            "mem": row["mem"],
                            "mem_pct": row["mem_pct"],
                            "loadavg": loadavg,
                            "health": health[:120],
                            "lms_health": lms[:120],
                            "compose_down": compose_down,
                            "groq_hits": groq_hits,
                        }
                    )
            cf.flush()
            print(
                f"{ts} sample={sample} load={loadavg} groq_hits={groq_hits} "
                f"health_ok={not health_fail} crashed={crashed}",
                flush=True,
            )
            time.sleep(INTERVAL)

    print("Monitor finished.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
