#!/usr/bin/env python3
import csv
import re
from pathlib import Path

p = Path(__file__).resolve().parent / "results" / "docker_stats.csv"
rows = list(csv.DictReader(p.open(encoding="utf-8")))
print("unique_samples", len({r["ts_utc"] for r in rows}), "rows", len(rows))


def pct(s):
    m = re.search(r"([0-9.]+)", s or "")
    return float(m.group(1)) if m else 0.0


by = {}
for r in rows:
    n = r["container"]
    by.setdefault(n, {"cpu": 0.0, "mem": 0.0})
    c = pct(r["cpu"])
    m = pct(r["mem_pct"])
    if c >= by[n]["cpu"]:
        by[n]["cpu"] = c
        by[n]["cpu_ts"] = r["ts_utc"]
        by[n]["cpu_raw"] = r["cpu"]
        by[n]["mem_at_cpu"] = r["mem"]
    if m >= by[n]["mem"]:
        by[n]["mem"] = m
        by[n]["mem_ts"] = r["ts_utc"]
        by[n]["mem_raw"] = r["mem"]

for n, v in sorted(by.items()):
    print(
        n,
        "cpu_peak",
        v.get("cpu_raw"),
        "@",
        v.get("cpu_ts"),
        "mem_peak",
        v.get("mem_raw"),
        v["mem"],
        "%",
    )

seen = set()
print("--- samples ---")
for r in rows:
    if r["ts_utc"] in seen:
        continue
    seen.add(r["ts_utc"])
    print(r["ts_utc"], "groq", r.get("groq_hits"), "load", (r.get("loadavg") or "")[:24])
