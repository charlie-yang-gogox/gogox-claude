#!/usr/bin/env bash
# Regression test for session_metrics.py --scan-subagents residuals (GGC-125).
#
# Guards three code-path fixes surfaced by --metric dogfood runs:
#   R4 — output_tokens undercount: parse_session dedup-by-requestId used to read
#        the FIRST record per request. Streaming emits the cumulative output only
#        in the FINAL record, so output must be the MAX across a request's records
#        (input/cache stay first-record — constant per request). ~8% cost undercount.
#   R5 — cycle_time_sec must be blank unless the PR actually merged (open→merge).
#        The upstream gather computed now−createdAt for unmerged PRs (a naive
#        local-vs-UTC subtraction → bogus ~8h HKT-offset value). Gated in
#        outcome_to_csv + format_outcome_section regardless of what was emitted.
#   R3 — get_ticket_cumulative(run_stem=…) aggregates ONLY prior DISPATCH rows
#        (non-empty run_stem ≠ current), so stray per-session hook rows no longer
#        leak into the batch header and make it disagree with provenance.
#
# scripts/prompt-lint.sh invokes lib/*.test.sh as a whole-repo invariant, so this
# runs as part of the `prompt` platform's verify-stage test_cmd.
#
# 10 assertions across R3/R4/R5 (the pass count is derived from a live counter).
# Run directly:  bash lib/session-metrics-residuals.test.sh  (0 = pass, 1 = fail)

set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)

python3 - "$ROOT" <<'PY'
import csv as _csv
import json
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


# ---- R4: output = MAX per requestId; input/cache = first record ----
with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp) / "agent-r4.jsonl"
    M = "claude-opus-4"

    def rec(rid, ts, out):
        return {"type": "assistant", "requestId": rid, "timestamp": ts,
                "message": {"model": M, "usage": {
                    "input_tokens": 1000, "output_tokens": out,
                    "cache_creation_input_tokens": 50,
                    "cache_read_input_tokens": 900}}}

    with open(p, "w") as f:
        # Request A streams: 5 -> 200 -> 29403 (cumulative in the last record).
        for ts, out in (("2026-07-09T00:00:00Z", 5),
                        ("2026-07-09T00:00:01Z", 200),
                        ("2026-07-09T00:00:02Z", 29403)):
            f.write(json.dumps(rec("A", ts, out)) + "\n")
        # Request B: single record.
        b = rec("B", "2026-07-09T00:00:03Z", 11)
        b["message"]["usage"]["input_tokens"] = 7
        b["message"]["usage"]["cache_creation_input_tokens"] = 0
        b["message"]["usage"]["cache_read_input_tokens"] = 0
        f.write(json.dumps(b) + "\n")

    u = sm.parse_session(p)["model_usage"][M]
    check("R4 output = max(A)=29403 + B=11", u["output_tokens"], 29414)
    check("R4 input = first per request 1000+7", u["input_tokens"], 1007)
    check("R4 cache_write first-record only", u["cache_write_tokens"], 50)
    check("R4 cache_read first-record only", u["cache_read_tokens"], 900)

# ---- R5: cycle_time gated on merged ----
check("R5 unmerged CSV cycle blank",
      sm.outcome_to_csv({"merged": False, "cycle_time_sec": 28928})["cycle_time_sec"], "")
check("R5 merged CSV cycle kept",
      sm.outcome_to_csv({"merged": True, "cycle_time_sec": 3600})["cycle_time_sec"], 3600)
check("R5 unmerged report has no Cycle row",
      any("Cycle time" in l for l in
          sm.format_outcome_section({"merged": False, "pr_state": "OPEN",
                                     "cycle_time_sec": 28928}, 1.0)), False)
check("R5 merged report has Cycle row",
      any("Cycle time" in l for l in
          sm.format_outcome_section({"merged": True, "pr_state": "MERGED",
                                     "cycle_time_sec": 3600}, 1.0)), True)

# ---- R3: get_ticket_cumulative run_stem scoping ----
with tempfile.TemporaryDirectory() as tmp:
    csvp = Path(tmp) / "m.csv"
    rows = [dict.fromkeys(sm.CSV_FIELDS, "") for _ in range(3)]
    rows[0].update(ticket_id="CAF-996", session_id="R1", run_stem="R1",
                   estimated_cost="10.0", wall_clock_sec="100", active_sec="0")
    rows[1].update(ticket_id="CAF-996", session_id="554f2d0b", run_stem="",
                   estimated_cost="2.30", wall_clock_sec="30", active_sec="0")
    rows[2].update(ticket_id="CAF-996", session_id="R2", run_stem="R2",
                   estimated_cost="47.04", wall_clock_sec="200", active_sec="0")
    with open(csvp, "w", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=sm.CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    orig = sm.CSV_PATH
    sm.CSV_PATH = csvp
    try:
        cum = sm.get_ticket_cumulative("CAF-996", "", "/tmp", run_stem="R2")
    finally:
        sm.CSV_PATH = orig
    check("R3 prior_cost = only prior dispatch row R1 ($10)",
          None if cum is None else round(cum["prior_cost"], 2), 10.0)
    check("R3 prior_session_count = 1 (stray + current excluded)",
          None if cum is None else cum["prior_session_count"], 1)

print(f"session-metrics-residuals.test: {checks - fails} passed, {fails} failed")
sys.exit(1 if fails else 0)
PY
