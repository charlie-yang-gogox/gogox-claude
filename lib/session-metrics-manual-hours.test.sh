#!/usr/bin/env bash
# Regression test for session_metrics.py write_csv `manual_hours` reconciliation (GGC-127).
#
# Guards: the CSV `manual_hours` column must AGREE with `estimated_manual_sec`
# (and the report / speed multiplier, which both derive from story_point_seconds).
# Before the fix, `manual_hours` stored the raw pre-snap --manual-hours estimate
# while `estimated_manual_sec` stored the post-snap story-point hours, so a
# consumer reading one got a different number than one reading the other whenever
# the blind estimate did not land exactly on a STORY_POINT_HOURS bucket.
#
# scripts/prompt-lint.sh invokes lib/*.test.sh as a whole-repo invariant.
# Run directly:  bash lib/session-metrics-manual-hours.test.sh  (0 = pass, 1 = fail)

set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)

python3 - "$ROOT" <<'PY'
import csv as _csv
import os
import sys
import tempfile
from pathlib import Path

root = sys.argv[1]
sys.path.insert(0, os.path.join(root, "skills/shared/session-metrics"))
import session_metrics as sm  # noqa: E402

fails = 0
checks = 0


def check(label, got, want):
    global fails, checks
    checks += 1
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'} {label}: got={got!r} want={want!r}")
    if not ok:
        fails += 1


def minimal_metrics():
    return {
        "model_usage": {"claude-opus-4": {
            "input_tokens": 100, "output_tokens": 50,
            "cache_write_tokens": 0, "cache_read_tokens": 0}},
        "agents": [], "user_msgs": 1, "assistant_msgs": 1,
        "tool_calls": 0, "total_turns": 1, "claude_code_version": "1.0",
    }


DURATIONS = {"wall_clock_sec": 100, "active_sec": 0, "idle_sec": 100, "active_proxy_sec": 60}


def write_and_read(story_points, manual_hours, run_stem="RS1"):
    with tempfile.TemporaryDirectory() as d:
        csvp = Path(d) / "m.csv"
        orig_csv, orig_dir = sm.CSV_PATH, sm.METRICS_DIR
        sm.CSV_PATH, sm.METRICS_DIR = csvp, Path(d)
        try:
            sm.write_csv(minimal_metrics(), DURATIONS, "GGC-127", run_stem,
                         "fix/GGC-127", story_points, manual_hours, run_stem=run_stem)
            with open(csvp, newline="") as f:
                return next(_csv.DictReader(f))
        finally:
            sm.CSV_PATH, sm.METRICS_DIR = orig_csv, orig_dir


# Case A: the CAF-1047 shape — manual_hours 3.0 snaps to SP 2 (4.0h / 14400s).
# manual_hours column must be reconciled to 4.0, not left at the raw 3.0.
row = write_and_read(story_points=2, manual_hours=3.0)
check("A manual_hours reconciled to snapped bucket", row["manual_hours"], "4.0")
check("A estimated_manual_sec is the snapped seconds", row["estimated_manual_sec"], "14400")
check("A manual_hours*3600 == estimated_manual_sec",
      float(row["manual_hours"]) * 3600, float(row["estimated_manual_sec"]))

# Case B: no story point → nothing to snap to; keep the raw manual_hours, est blank.
rowb = write_and_read(story_points=None, manual_hours=2.5)
check("B manual_hours kept raw when no SP", rowb["manual_hours"], "2.5")
check("B estimated_manual_sec blank when no SP", rowb["estimated_manual_sec"], "")

# Case C: explicit SP already on a bucket → agrees, no drift introduced.
rowc = write_and_read(story_points=3, manual_hours=8.0)
check("C on-bucket SP stays consistent", rowc["manual_hours"], "8.0")
check("C estimated_manual_sec matches", rowc["estimated_manual_sec"], "28800")

# Case D: SP set but manual_hours is None — the column used to write "" while
# estimated_manual_sec was already populated (the core disagreement). It must now
# write the bucket hours so the two agree.
rowd = write_and_read(story_points=5, manual_hours=None)
check("D SP-set + no manual_hours → bucket, not blank", rowd["manual_hours"], "16.0")
check("D estimated_manual_sec populated", rowd["estimated_manual_sec"], "57600")

print(f"session-metrics-manual-hours.test: {checks - fails} passed, {fails} failed")
sys.exit(1 if fails else 0)
PY
