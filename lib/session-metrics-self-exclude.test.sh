#!/usr/bin/env bash
# Regression test for session_metrics.py `_invoked_scan_subagents` (GGC-78).
#
# Guards the GGC-78 fix: the batch metric scanner (`session_metrics.py
# --scan-subagents`) is itself a sibling subagent under the same parent
# session, so `scan_subagents_aggregate` must NOT sum the scanner's own
# transcript into a ticket's total. The exclusion is content-based (detect an
# actual Bash invocation of --scan-subagents) so it holds regardless of the
# scanner's process cwd — replacing the fragile incidental cwd-based exclusion.
#
# Critically, a worker that merely EDITED session_metrics.py also carries the
# literal "--scan-subagents" in its transcript; it must NOT be excluded. This
# test pins both directions.
#
# No external test runner exists yet (a full suite is GGC-27).
# scripts/prompt-lint.sh invokes lib/*.test.sh as a whole-repo invariant, so
# this runs as part of the `prompt` platform's verify-stage test_cmd.
#
# Run directly:  bash lib/session-metrics-self-exclude.test.sh  (0 = pass, 1 = fail)

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


def write(tmp, name, records):
    p = Path(tmp) / name
    with open(p, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return p


def check(label, got, want):
    global fails
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'} {label}: got={got} want={want}")
    if not ok:
        fails += 1


with tempfile.TemporaryDirectory() as tmp:
    # The scanner: an assistant Bash tool_use that invokes --scan-subagents.
    scanner = write(tmp, "agent-scanner.jsonl", [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command":
                "python3 ~/.claude/skills/session-metrics/session_metrics.py "
                "--scan-subagents --ticket-id GGC-78 --parent-session abc "
                "--run-ts 20260623T000000Z"}}]}},
    ])
    # A worker that EDITED session_metrics.py — its transcript contains the
    # literal "--scan-subagents" inside an Edit tool_use, but it is NOT a
    # Bash invocation, so it must stay included.
    editor = write(tmp, "agent-editor.jsonl", [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {
                "file_path": "session_metrics.py",
                "new_string": "help='... --scan-subagents flag ...'"}}]}},
    ])
    # A normal worker doing unrelated Bash work.
    worker = write(tmp, "agent-worker.jsonl", [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "bash lib/dev-mode.test.sh"}}]}},
    ])

    check("scanner transcript detected (excluded)",
          sm._invoked_scan_subagents(scanner), True)
    check("editor of session_metrics.py NOT excluded (Edit, not a Bash invoke)",
          sm._invoked_scan_subagents(editor), False)
    check("normal worker NOT excluded",
          sm._invoked_scan_subagents(worker), False)

print(f"session-metrics-self-exclude.test: {3 - fails} passed, {fails} failed")
sys.exit(1 if fails else 0)
PY
