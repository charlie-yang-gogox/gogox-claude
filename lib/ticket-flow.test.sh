#!/usr/bin/env bash
# Regression tests for skills/shared/ticket-flow/analyze.py (the dev-flow
# bottleneck analyzer). Pure-Python, deterministic — fed a fixed `--now` and a
# fixture that includes the REAL GGC-100 stateHistory so the duration math is
# anchored to ground truth, plus a synthetic open ticket (aging-WIP) and a
# re-entry ticket (re-entries must be summed).
#
# scripts/prompt-lint.sh runs every lib/*.test.sh as the prompt platform's
# verify-stage test_cmd. Run directly: bash lib/ticket-flow.test.sh

set -u
HERE=$(cd "$(dirname "$0")" && pwd)
PY="$HERE/../skills/shared/ticket-flow/analyze.py"
[ -f "$PY" ] || { echo "FAIL: cannot find $PY" >&2; exit 1; }

FIX=$(mktemp)
OUTF=$(mktemp)
trap 'rm -f "$FIX" "$OUTF"' EXIT

cat > "$FIX" <<'JSON'
[
  { "identifier": "GGC-100", "title": "real", "assignee": "Charlie", "labels": ["Feature"],
    "createdAt": "2026-06-29T06:22:57.246Z",
    "stateHistory": [
      {"state":{"name":"Backlog","type":"backlog"},   "startedAt":"2026-06-29T06:22:57.246Z","endedAt":"2026-06-29T06:32:37.578Z"},
      {"state":{"name":"In Progress","type":"started"},"startedAt":"2026-06-29T06:32:37.593Z","endedAt":"2026-06-29T06:48:40.121Z"},
      {"state":{"name":"In Review","type":"started"},  "startedAt":"2026-06-29T06:48:40.121Z","endedAt":"2026-06-29T07:51:51.568Z"},
      {"state":{"name":"Done","type":"completed"},     "startedAt":"2026-06-29T07:51:51.568Z","endedAt":null}
    ] },
  { "identifier": "T-OPEN", "title": "open wip", "assignee": null, "labels": [],
    "createdAt": "2026-06-29T06:00:00Z",
    "stateHistory": [
      {"state":{"name":"Backlog","type":"backlog"},  "startedAt":"2026-06-29T06:00:00Z","endedAt":"2026-06-29T08:00:00Z"},
      {"state":{"name":"In Review","type":"started"},"startedAt":"2026-06-29T08:00:00Z","endedAt":null}
    ] },
  { "identifier": "T-RE", "title": "re-entry", "assignee": "Q", "labels": [],
    "createdAt": "2026-06-01T00:00:00Z",
    "stateHistory": [
      {"state":{"name":"In Progress","type":"started"},"startedAt":"2026-06-01T00:00:00Z","endedAt":"2026-06-01T01:00:00Z"},
      {"state":{"name":"In Review","type":"started"},  "startedAt":"2026-06-01T01:00:00Z","endedAt":"2026-06-01T03:00:00Z"},
      {"state":{"name":"In Progress","type":"started"},"startedAt":"2026-06-01T03:00:00Z","endedAt":"2026-06-01T03:30:00Z"},
      {"state":{"name":"In Review","type":"started"},  "startedAt":"2026-06-01T03:30:00Z","endedAt":"2026-06-01T06:30:00Z"},
      {"state":{"name":"Done","type":"completed"},     "startedAt":"2026-06-01T06:30:00Z","endedAt":null}
    ] },
  { "identifier": "T-CANCEL", "title": "canceled", "assignee": null, "labels": [],
    "createdAt": "2026-06-01T00:00:00Z",
    "stateHistory": [
      {"state":{"name":"Backlog","type":"backlog"},   "startedAt":"2026-06-01T00:00:00Z","endedAt":"2026-06-02T00:00:00Z"},
      {"state":{"name":"Canceled","type":"canceled"}, "startedAt":"2026-06-02T00:00:00Z","endedAt":null}
    ] }
]
JSON

python3 "$PY" "$FIX" --now "2026-06-29T10:00:00Z" > "$OUTF" || {
  echo "FAIL: analyze.py exited non-zero" >&2; exit 1; }

# NOTE: pass the report path as argv — `python3 - <<HEREDOC` already uses stdin
# for the program, so the report cannot also be piped in on stdin.
python3 - "$OUTF" <<'PYCHECK'
import json, sys
with open(sys.argv[1]) as fh:
    r = json.load(fh)
p = f = 0
def check(name, got, want, tol=None):
    global p, f
    ok = (abs(got - want) <= tol) if tol is not None else (got == want)
    if ok: print(f"PASS {name}: {got}"); p += 1
    else:  print(f"FAIL {name}: got={got!r} want={want!r}"); f += 1

bystate = {t["identifier"]: t["byState"] for t in r["perTicket"]}

# 1. Real GGC-100 durations (hours), anchored to ground truth.
check("GGC-100 Backlog h",    bystate["GGC-100"]["Backlog"],    0.1612, tol=0.01)
check("GGC-100 In Progress h",bystate["GGC-100"]["In Progress"],0.2674, tol=0.01)
check("GGC-100 In Review h",  bystate["GGC-100"]["In Review"],  1.0532, tol=0.01)
# 2. Terminal Done excluded from per-ticket byState.
check("GGC-100 Done excluded", "Done" in bystate["GGC-100"], False)
# 3. Re-entries summed (In Review = 2h + 3h; In Progress = 1h + 0.5h).
check("T-RE In Review summed",   bystate["T-RE"]["In Review"],   5.0, tol=0.001)
check("T-RE In Progress summed", bystate["T-RE"]["In Progress"], 1.5, tol=0.001)
# 4. Canceled ticket excluded by default.
check("ticketCount excl canceled", r["ticketCount"], 3)
check("skippedCanceled", r["skippedCanceled"], 1)
# 5. Bottleneck = In Review (8.05h total > Backlog 2.16 > In Progress 1.77).
check("bottleneck state", r["bottleneck"]["state"], "In Review")
# 6. Aging-WIP: the open ticket, measured to --now (08:00→10:00 = 2h).
wip = {w["identifier"]: w for w in r["agingWip"]}
check("T-OPEN in agingWip", "T-OPEN" in wip, True)
check("T-OPEN aging state", wip.get("T-OPEN", {}).get("state"), "In Review")
check("T-OPEN aging hours", wip.get("T-OPEN", {}).get("ageHours", -1), 2.0, tol=0.001)
check("only one open WIP", len(r["agingWip"]), 1)

print(f"ticket-flow.test: {p} passed, {f} failed")
sys.exit(0 if f == 0 else 1)
PYCHECK
