#!/usr/bin/env bash
# Unit test for scripts/pipeline-stats.sh (GGC-54 fleet aggregator, P1).
#
# Guards the CSV-convention-sensitive aggregation math against session_metrics.py's
# write_csv layout:
#   * a RUN is deduped by run_stem (batch) / session_id (standalone);
#   * per-model COST rows are SUMMED per run;
#   * per-run SCALARS (pr_merged, cycle_time_sec, active_proxy_sec, first_pass)
#     live on the FIRST model row only → read-first, NEVER summed (else a
#     3-model run would count pr_merged=3);
#   * merged / first-pass denominators count ONLY runs that gathered a PR
#     outcome (pr_state non-empty).
#
# scripts/prompt-lint.sh invokes lib/*.test.sh, so this runs in the verify stage.
# Run directly:  bash lib/pipeline-stats.test.sh   (0 = pass, 1 = fail)

set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
CSV="$TMP/m.csv"

# Header: only the columns the aggregator reads must be present + consistent.
cat > "$CSV" <<'CSV'
timestamp,ticket_id,session_id,model,estimated_cost,total_tokens,cache_read_tokens,run_stem,pr_merged,pr_state,first_pass_accepted,cycle_time_sec,active_proxy_sec
2026-07-01T00:00:00Z,CAF-1,sA,claude-haiku-4-5,1.0,100,90,RS1,1,MERGED,1,3600,150
2026-07-01T00:00:00Z,CAF-1,sA,claude-opus-4-8,0.5,50,40,RS1,,,,,
2026-07-01T00:00:00Z,CAF-1,sA,claude-sonnet-5,0.25,25,20,RS1,,,,,
2026-07-02T00:00:00Z,CAF-2,sB,claude-opus-4-8,2.0,200,150,,0,OPEN,0,,
2026-07-03T00:00:00Z,CAF-3,sC,claude-opus-4-8,0.5,20,10,,,,,,
CSV

bash "$ROOT/scripts/pipeline-stats.sh" --csv "$CSV" --out-dir "$TMP/out" >/dev/null 2>&1

python3 - "$TMP/out/metrics.jsonl" <<'PY'
import json, sys
recs = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
fails = 0
def check(label, cond):
    global fails
    print(f"{'PASS' if cond else 'FAIL'} {label}")
    if not cond: fails += 1

jul = next((r for r in recs if r["month"] == "2026-07"), None)
check("2026-07 record exists", jul is not None)
if jul:
    check(f"3 runs deduped (got {jul['runs']})", jul["runs"] == 3)
    check(f"2 runs w/ outcome — C excluded (got {jul['runs_with_outcome']})", jul["runs_with_outcome"] == 2)
    check(f"cost summed across model rows = 4.25 (got {jul['total_cost']})", abs(jul["total_cost"] - 4.25) < 1e-6)
    check(f"merged=1, NOT 3 (read-first not summed) (got {jul['merged']})", jul["merged"] == 1)
    check(f"merged_rate=50% (1 of 2 w/ outcome) (got {jul['merged_rate_pct']})", jul["merged_rate_pct"] == 50)
    check(f"first_pass_rate=100% (1 of 1 merged) (got {jul['first_pass_rate_pct']})", jul["first_pass_rate_pct"] == 100)
    check(f"median cycle=3600 (got {jul['median_cycle_sec']})", jul["median_cycle_sec"] == 3600)
    check(f"$/merged=1.75 (run A cost, not incl. B/C) (got {jul['avg_cost_per_merged_pr']})", abs(jul["avg_cost_per_merged_pr"] - 1.75) < 1e-6)
    check(f"median active_proxy=150 (got {jul['median_active_proxy_sec']})", jul["median_active_proxy_sec"] == 150)
    check(f"cache_read_pct computed (got {jul['cache_read_pct']})", jul["cache_read_pct"] is not None)

total = 11
print(f"pipeline-stats.test: {total - fails} passed, {fails} failed")
sys.exit(1 if fails else 0)
PY
