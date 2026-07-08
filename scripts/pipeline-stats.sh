#!/usr/bin/env bash
#
# pipeline-stats.sh — GGC-54 fleet telemetry aggregator (Layer 2, P1).
#
# Report-only. Reads the per-run COLLECTOR ledger
# (~/.claude/metrics/session_metrics.csv, written by the session-metrics skill)
# and rolls it up into a FLEET-LEVEL monthly trend: are we getting cheaper /
# faster / more first-pass over time. Emits a human markdown trend table plus a
# machine-readable metrics.jsonl (one line per month) that GGC-51 (gate
# calibration) and GGC-52 (self-improvement trend detection) consume.
#
# Design (per GGC-54): lightweight, report-only, NEVER gates. The CSV parsing +
# math run in an embedded python3 (stdlib only — the collector already depends
# on python3; no new tooling), keeping quoted-field handling correct where awk
# would be fragile.
#
# CSV convention it MUST respect (session_metrics.py write_csv):
#   * A "run" = all CSV rows sharing a run key = run_stem (dispatcher batch) or
#     session_id (standalone). Cost/tokens are PER-MODEL rows → SUMMED per run.
#     Per-run SCALARS (pr_merged, cycle_time_sec, active_proxy_sec, first_pass_
#     accepted, time_saved_multiplier, …) are written on the FIRST model row of
#     a run only → read-first per run, NEVER naive-summed across model rows.
#
# Scope (P1, this slice): the CSV-driven monthly trend. Per-SHA success and
# per-lane breakdown need a `pipeline_sha` stamp + timings.jsonl/run-report
# joins that do not exist yet — a documented follow-up slice of GGC-54, not
# here. Coverage of the outcome columns is reported honestly (they are recent).
#
# Usage:
#   scripts/pipeline-stats.sh [--csv <path>] [--out-dir <dir>] [--stdout]
#     --csv      CSV to read (default: ~/.claude/metrics/session_metrics.csv)
#     --out-dir  output dir  (default: claude-reports/pipeline-stats)
#     --stdout   also print the markdown table to stdout
#     --help     this help

set -euo pipefail

CSV="${HOME}/.claude/metrics/session_metrics.csv"
OUT_DIR="claude-reports/pipeline-stats"
TO_STDOUT=0

while [ $# -gt 0 ]; do
  case "$1" in
    --csv)     CSV="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --stdout)  TO_STDOUT=1; shift ;;
    --help|-h)
      sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "pipeline-stats: unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [ ! -f "$CSV" ]; then
  echo "pipeline-stats: no CSV at $CSV — nothing to aggregate (not an error)." >&2
  exit 0
fi

mkdir -p "$OUT_DIR"
MD_PATH="${OUT_DIR}/summary.md"
JSONL_PATH="${OUT_DIR}/metrics.jsonl"

CSV="$CSV" MD_PATH="$MD_PATH" JSONL_PATH="$JSONL_PATH" TO_STDOUT="$TO_STDOUT" \
python3 <<'PY'
import csv, json, os, statistics
from collections import defaultdict, OrderedDict
from datetime import datetime, timezone

csv_path = os.environ["CSV"]
md_path = os.environ["MD_PATH"]
jsonl_path = os.environ["JSONL_PATH"]
to_stdout = os.environ.get("TO_STDOUT") == "1"


def num(v):
    """Parse a numeric cell → float, else None (blank / non-numeric)."""
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ---- 1. Fold per-model CSV rows into RUNS. -----------------------------------
# Run key = run_stem (dispatcher batch) else session_id (standalone). Cost/tokens
# summed per run; per-run scalars read from whichever row carries them (they are
# written on the first model row only, so "first non-blank wins" is exact).
runs = OrderedDict()  # key -> aggregate dict
excluded_unknown = {"runs": set(), "cost": 0.0}  # interactive / non-pipeline rows

SCALAR_NUM = [
    "wall_clock_sec", "active_sec", "active_proxy_sec", "total_turns",
    "story_points", "time_saved_multiplier", "cycle_time_sec",
    "review_rounds", "reviewer_comments", "ci_fail_count",
]

with open(csv_path, newline="") as f:
    for row in csv.DictReader(f):
        run_stem = (row.get("run_stem") or "").strip()
        ticket = (row.get("ticket_id") or "").strip()
        session_id = (row.get("session_id") or "").strip()
        # A dispatcher batch shares ONE run_stem across N tickets (each ticket is
        # its own upsert row — write_csv keys on (ticket_id, run_stem)). So the
        # run key MUST include ticket_id on the batch path; keying on run_stem
        # alone collapses the whole batch to one ticket and silently drops the
        # rest (confirmed: 15 run_stems in the live CSV span >1 ticket). Standalone
        # rows have no run_stem and a unique session_id per (session, ticket).
        if run_stem:
            key = run_stem + "\t" + ticket
        else:
            key = session_id
        if not key:
            continue
        # UNKNOWN-ticket rows are interactive / dev sessions where no ticket was
        # inferred — not pipeline deliveries. Exclude them from the pipeline fleet
        # trend, but report the excluded count + cost so the omission is visible
        # (never silently folded into "runs").
        if ticket == "UNKNOWN" or ticket == "":
            excluded_unknown["runs"].add(key)
            excluded_unknown["cost"] += num(row.get("estimated_cost")) or 0.0
            continue
        r = runs.get(key)
        if r is None:
            r = {
                "key": key,
                "ticket_id": row.get("ticket_id", ""),
                "timestamp": row.get("timestamp", ""),
                "cost": 0.0,
                "total_tokens": 0.0,
                "cache_read_tokens": 0.0,
                "scalars": {},
                "pr_merged": None,
                "pr_state": "",
                "first_pass": None,
                "multiplier_basis": "",
            }
            runs[key] = r
        # earliest timestamp for the run
        ts = row.get("timestamp", "")
        if ts and (not r["timestamp"] or ts < r["timestamp"]):
            r["timestamp"] = ts
        # per-model SUMs
        c = num(row.get("estimated_cost")); r["cost"] += c or 0.0
        t = num(row.get("total_tokens")); r["total_tokens"] += t or 0.0
        cr = num(row.get("cache_read_tokens")); r["cache_read_tokens"] += cr or 0.0
        # per-run scalars: first non-blank wins
        for col in SCALAR_NUM:
            if col not in r["scalars"]:
                v = num(row.get(col))
                if v is not None:
                    r["scalars"][col] = v
        pm = (row.get("pr_merged") or "").strip()
        if r["pr_merged"] is None and pm != "":
            r["pr_merged"] = (pm == "1")
        ps = (row.get("pr_state") or "").strip()
        if not r["pr_state"] and ps:
            r["pr_state"] = ps
        fp = (row.get("first_pass_accepted") or "").strip()
        if r["first_pass"] is None and fp != "":
            r["first_pass"] = (fp == "1")
        mb = (row.get("multiplier_basis") or "").strip()
        if not r["multiplier_basis"] and mb:
            r["multiplier_basis"] = mb


# ---- 2. Group runs by month. -------------------------------------------------
def month_of(ts):
    return ts[:7] if len(ts) >= 7 else "unknown"


by_month = defaultdict(list)
for r in runs.values():
    by_month[month_of(r["timestamp"])].append(r)


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def pct(n, d):
    return round(100 * n / d) if d else None


def fmt_dur(sec):
    if sec is None:
        return "—"
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    return f"{sec // 3600}h {(sec % 3600) // 60}m"


# ---- 3. Per-month aggregate. -------------------------------------------------
rows_out = []
for month in sorted(by_month):
    rs = by_month[month]
    n = len(rs)
    total_cost = sum(r["cost"] for r in rs)
    total_tokens = sum(r["total_tokens"] for r in rs)
    total_cache_read = sum(r["cache_read_tokens"] for r in rs)
    # Outcome coverage: only runs that actually gathered a PR outcome count in
    # the merged/first-pass denominators (the outcome columns are recent).
    with_outcome = [r for r in rs if r["pr_state"]]
    merged = [r for r in with_outcome if r["pr_merged"]]
    n_outcome = len(with_outcome)
    n_merged = len(merged)
    first_pass = [r for r in merged if r["first_pass"]]
    cycles = [r["scalars"].get("cycle_time_sec") for r in merged]
    proxies = [r["scalars"].get("active_proxy_sec") for r in rs]
    # avg cost per merged PR = mean cost of the runs that merged (honest ROI unit
    # once outcome coverage is high; labeled n so small-N is visible).
    avg_cost_per_merged = round(sum(r["cost"] for r in merged) / n_merged, 2) if n_merged else None

    rec = {
        "month": month,
        "runs": n,
        "runs_with_outcome": n_outcome,
        "total_cost": round(total_cost, 2),
        "total_tokens": int(total_tokens),
        "cache_read_pct": pct(total_cache_read, total_tokens),
        "merged": n_merged,
        "merged_rate_pct": pct(n_merged, n_outcome),
        "first_pass_rate_pct": pct(len(first_pass), n_merged),
        "median_cycle_sec": (int(median(cycles)) if median(cycles) is not None else None),
        "avg_cost_per_merged_pr": avg_cost_per_merged,
        "median_active_proxy_sec": (int(median(proxies)) if median(proxies) is not None else None),
    }
    rows_out.append(rec)

generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

# ---- 4. Markdown trend table. ------------------------------------------------
md = []
md.append("# Pipeline stats — fleet trend")
md.append("")
md.append(f"_Generated {generated_at} from `{csv_path}` · {len(runs)} pipeline runs across {len(by_month)} month(s)._")
n_excl = len(excluded_unknown["runs"])
if n_excl:
    md.append("")
    md.append(f"_Excluded {n_excl} UNKNOWN-ticket (interactive / non-pipeline) run(s), "
              f"${excluded_unknown['cost']:.2f} — not part of the pipeline fleet trend._")
md.append("")
md.append("> Report-only, advisory. A **run** is deduped by `run_stem`/`session_id`; "
          "per-run scalars are read-first (never summed across model rows). "
          "`merged` / `first-pass` denominators count only runs that gathered a PR "
          "outcome (`runs w/ outcome`) — the outcome columns are recent, so early "
          "months show low coverage, not low quality.")
md.append("")
md.append("| Month | Runs | w/ outcome | Cost | Tokens | Cache-read | Merged | Merged% | First-pass% | Median cycle | $/merged PR | Median AI-active(approx) |")
md.append("|-------|-----:|-----------:|-----:|-------:|-----------:|-------:|--------:|------------:|-------------:|------------:|-------------------------:|")
for r in rows_out:
    md.append(
        "| {month} | {runs} | {wo} | ${cost:.2f} | {tok:,} | {crp} | {merged} | {mr} | {fp} | {cyc} | {cpm} | {ap} |".format(
            month=r["month"], runs=r["runs"], wo=r["runs_with_outcome"],
            cost=r["total_cost"], tok=r["total_tokens"],
            crp=(f"{r['cache_read_pct']}%" if r["cache_read_pct"] is not None else "—"),
            merged=r["merged"],
            mr=(f"{r['merged_rate_pct']}%" if r["merged_rate_pct"] is not None else "—"),
            fp=(f"{r['first_pass_rate_pct']}%" if r["first_pass_rate_pct"] is not None else "—"),
            cyc=fmt_dur(r["median_cycle_sec"]),
            cpm=(f"${r['avg_cost_per_merged_pr']:.2f}" if r["avg_cost_per_merged_pr"] is not None else "—"),
            ap=fmt_dur(r["median_active_proxy_sec"]),
        )
    )
md.append("")
md.append("## Follow-up slices (GGC-54, not in this P1)")
md.append("- **Per-SHA success table** — needs a `pipeline_sha` stamp in run-report headers (not yet emitted); correlates a failure spike to the prompt commit that caused it.")
md.append("- **Per-lane breakdown** — the CSV has no lane column; join via run reports / `timings.jsonl`.")
md.append("- **Reverted-PR rate & human-edit-ratio** — cross-PR / post-merge signals gathered via `gh` over each period.")
md_text = "\n".join(md) + "\n"

with open(md_path, "w") as f:
    f.write(md_text)

with open(jsonl_path, "w") as f:
    for r in rows_out:
        f.write(json.dumps({**r, "generated_at": generated_at}) + "\n")

print(f"pipeline-stats: wrote {md_path} and {jsonl_path} "
      f"({len(runs)} pipeline runs, {len(by_month)} months; "
      f"excluded {len(excluded_unknown['runs'])} UNKNOWN-ticket runs).")
if to_stdout:
    print()
    print(md_text)
PY
