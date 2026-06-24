#!/usr/bin/env bash
# Regression test for session_metrics.py report formatting (GGC-89).
#
# Guards the GGC-89 fix for the degraded batch (--metric) report:
#   1. The summed scan path has active_sec=0 (subagent transcripts carry no
#      `turn_duration` system records — those are main-loop only). The report
#      must NOT render "AI active: 0s" / "AI Active Time | 0s" as if real;
#      instead it omits the active value and computes Speed on a WALL-CLOCK
#      basis so the headline AI-vs-manual multiplier survives honestly.
#   2. A token + cache-read breakdown line appears on BOTH paths (the report
#      skeleton previously carried no tokens at all — they reached the reader
#      only via the LLM Summary, which the batch path drops).
#   3. The standalone path (active_sec>0) is unchanged: "(AI active: …)" in the
#      header and Speed computed against active time.
#
# scripts/prompt-lint.sh invokes lib/*.test.sh as a whole-repo invariant, so
# this runs as part of the `prompt` platform's verify-stage test_cmd.
#
# Run directly:  bash lib/session-metrics-report-format.test.sh  (0 = pass, 1 = fail)

set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)

python3 - "$ROOT" <<'PY'
import os
import sys
from collections import defaultdict

root = sys.argv[1]
sys.path.insert(0, os.path.join(root, "skills/shared/session-metrics"))
import session_metrics as sm  # noqa: E402

fails = 0


def check(label, cond):
    global fails
    print(f"{'PASS' if cond else 'FAIL'} {label}")
    if not cond:
        fails += 1


def mk_metrics(model_usage, turns):
    mu = defaultdict(lambda: {"input_tokens": 0, "output_tokens": 0,
                              "cache_write_tokens": 0, "cache_read_tokens": 0})
    for m, u in model_usage.items():
        mu[m].update(u)
    return {"model_usage": mu, "agents": [], "in_progress_agents": [],
            "total_turns": turns, "user_msgs": 0, "assistant_msgs": 0,
            "tool_calls": 0, "timestamps": [], "turn_durations_ms": []}


# --- BATCH path: active=0, cache-heavy tokens, provenance set, SP given ---
batch_m = mk_metrics(
    {"claude-opus-4-8": {"input_tokens": 22428, "output_tokens": 4687,
                         "cache_write_tokens": 348228,
                         "cache_read_tokens": 46701621}}, 9)
batch_d = {"wall_clock_sec": 1773, "active_sec": 0, "idle_sec": 0}
batch = sm.format_report(
    batch_m, batch_d, "CAF-785", 3, "trunk", session_id="",
    summary_file=None, story_points=2, cumulative=None, current_cost=77.27,
    run_stem="20260624T083916Z-90244",
    provenance=[{"stem": "agent-x", "cost": 77.27, "tokens": 47076964}])

check("batch: no misleading 'active: 0s' in header", "AI active: 0s" not in batch)
check("batch: active row marked n/a (not 0s)",
      "AI Active Time | n/a" in batch and "AI Active Time | 0s" not in batch)
check("batch: Speed on wall-clock basis present",
      "Speed" in batch and "wall-clock basis" in batch)
check("batch: token + cache-read line present",
      "**Tokens:**" in batch and "cache-read" in batch)

# --- STANDALONE path: active>0 — unchanged behavior ---
std_m = mk_metrics(
    {"claude-sonnet-4-6": {"input_tokens": 33, "output_tokens": 14604,
                           "cache_write_tokens": 67948,
                           "cache_read_tokens": 2046614}}, 12)
std_d = {"wall_clock_sec": 600, "active_sec": 420, "idle_sec": 180}
std = sm.format_report(
    std_m, std_d, "CAF-999", 1, "feat/x", session_id="abc",
    summary_file=None, story_points=2, cumulative=None, current_cost=1.09,
    run_stem=None, provenance=None)

check("standalone: header shows '(AI active: …)'", "(AI active:" in std)
check("standalone: real active time row present",
      "AI Active Time | 7m 0s" in std)
check("standalone: Speed has no wall-clock-basis qualifier",
      "Speed" in std and "wall-clock basis" not in std)
check("standalone: token + cache-read line present too",
      "**Tokens:**" in std and "cache-read" in std)

print(f"session-metrics-report-format.test: "
      f"{8 - fails} passed, {fails} failed")
sys.exit(1 if fails else 0)
PY
