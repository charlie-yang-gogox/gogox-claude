#!/usr/bin/env bash
# Regression test for session_metrics.py Workflow-worker attribution (GGC-86).
#
# Guards the GGC-86 fix: under the `Workflow` fan-out (the only /ggx-dispatcher
# fan-out path since GGC-55), every spawned worker records cwd=<main repo> /
# gitBranch=<default> on every record — the per-ticket `/add-worktree` `cd`
# moves only the shell, not the agent process the harness records. So the
# legacy first-record cwd/gitBranch match could attribute NO transcript to its
# ticket, and `--scan-subagents` exited 4 ("ZERO matching") for every done
# ticket, making `--metric` a silent no-op on Linear.
#
# The fix reads the authoritative agentId -> result.ticketId map from the run
# journal (`subagents/workflows/<runId>/journal.jsonl`) and attributes by it,
# keeping cwd/gitBranch as the fallback for non-workflow (top-level) subagents.
#
# This pins, with two workers sharing an identical cwd/gitBranch:
#   1. _workflow_agent_ticket_map reads the journal correctly (and is empty for
#      a non-workflow session).
#   2. each ticket attributes ONLY its own journal-mapped worker (no
#      cross-attribution despite identical cwd/branch).
#   3. a ticket no worker maps to returns None (no false positive).
#   4. the non-workflow fallback (cwd carries the ticket, no journal) still
#      matches.
#
# scripts/prompt-lint.sh invokes lib/*.test.sh as a whole-repo invariant, so
# this runs as part of the `prompt` platform's verify-stage test_cmd.
#
# Run directly:  bash lib/session-metrics-workflow-attribution.test.sh  (0 = pass, 1 = fail)

set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)

python3 - "$ROOT" <<'PY'
import json
import os
import sys
import tempfile
from pathlib import Path

root = sys.argv[1]
sys.path.insert(0, os.path.join(root, "skills/shared/session-metrics"))
import session_metrics as sm  # noqa: E402

fails = 0
MAIN_REPO = "/Users/dev/Projects/work_project/gogox-client-flutter"
RUN_TS = "20260624T050000Z"  # before every transcript ts below


def check(label, got, want):
    global fails
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'} {label}: got={got} want={want}")
    if not ok:
        fails += 1


def jline(**kw):
    return json.dumps(kw) + "\n"


def worker_records(cwd, branch):
    # A minimal parse_session-safe transcript: first record carries the head
    # fields (cwd / gitBranch / timestamp) the match gate reads, plus a usage
    # block so the aggregate has something to sum.
    return [
        jline(type="assistant", timestamp="2026-06-24T05:30:00.000Z",
              cwd=cwd, gitBranch=branch, requestId="r1",
              message={"model": "claude-x", "usage": {"input_tokens": 100,
                                                       "output_tokens": 50}}),
    ]


def write(path: Path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.writelines(lines)
    return path


with tempfile.TemporaryDirectory() as tmp:
    sm.PROJECTS_DIR = Path(tmp) / "projects"

    SESSION = "11111111-1111-1111-1111-111111111111"
    NONWF = "22222222-2222-2222-2222-222222222222"

    # ---- Workflow session: journal maps two workers to two tickets; both
    #      workers share an identical (main-repo / trunk) cwd+branch. ----
    wf = sm.PROJECTS_DIR / "proj1" / SESSION / "subagents" / "workflows" / "wf_x"
    write(wf / "journal.jsonl", [
        jline(type="started", agentId="A"),
        jline(type="started", agentId="B"),
        jline(type="result", agentId="A",
              result={"ticketId": "CAF-100", "outcome": "done", "prUrl": "x"}),
        jline(type="result", agentId="B",
              result={"ticketId": "CAF-200", "outcome": "done", "prUrl": "y"}),
        # a non-ticket result (evidence-style) must not pollute the map
        jline(type="result", agentId="A", result={"verdict": "confirmed"}),
    ])
    write(wf / "agent-A.jsonl", worker_records(MAIN_REPO, "trunk"))
    write(wf / "agent-B.jsonl", worker_records(MAIN_REPO, "trunk"))

    # ---- Non-workflow session: a top-level subagent whose cwd carries the
    #      ticket and which has NO journal (legacy fallback path). ----
    write(sm.PROJECTS_DIR / "proj1" / NONWF / "subagents" / "agent-C.jsonl",
          worker_records(MAIN_REPO.replace("gogox-client-flutter", "CAF-300"),
                         "fix/CAF-300"))

    # 1. journal map
    check("journal map (workflow session)",
          sm._workflow_agent_ticket_map(SESSION),
          {"A": "CAF-100", "B": "CAF-200"})
    check("journal map empty (non-workflow session)",
          sm._workflow_agent_ticket_map(NONWF), {})

    def stems(agg):
        return None if agg is None else sorted(p["stem"] for p in agg["provenance"])

    # 2. each ticket attributes ONLY its own worker — no cross-attribution
    #    even though agent-A and agent-B share cwd=main-repo / branch=trunk.
    check("CAF-100 → agent-A only",
          stems(sm.scan_subagents_aggregate(SESSION, "CAF-100", RUN_TS)),
          ["agent-A"])
    check("CAF-200 → agent-B only",
          stems(sm.scan_subagents_aggregate(SESSION, "CAF-200", RUN_TS)),
          ["agent-B"])

    # 3. a ticket no worker maps to → None (cwd/branch are main-repo/trunk,
    #    so the fallback cannot false-positive either).
    check("CAF-999 → None (no false positive)",
          sm.scan_subagents_aggregate(SESSION, "CAF-999", RUN_TS), None)

    # 4. non-workflow fallback still matches via cwd.
    check("non-workflow CAF-300 → agent-C (cwd fallback)",
          stems(sm.scan_subagents_aggregate(NONWF, "CAF-300", RUN_TS)),
          ["agent-C"])

total = 6
print(f"session-metrics-workflow-attribution.test: {total - fails} passed, {fails} failed")
sys.exit(1 if fails else 0)
PY
