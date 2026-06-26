#!/usr/bin/env bash
# Regression test for session_metrics.py `find_jsonl_by_session_id` (GGC worktree-locate fix).
#
# Guards the root-cause fix for "Could not find session JSONL" when /session-metrics
# (e.g. the /ggx-work --metric inline finalize) runs from a git worktree:
#
#   Claude Code files a session's transcript under the hash of the session's STARTUP
#   cwd. An EnterWorktree (e.g. /ggx-work bug lane → /add-worktree) moves the live
#   cwd to ../<TICKET>, whose hash dir holds NO top-level <sid>.jsonl (only a
#   subagent SUBDIR). The legacy cwd-hash locator (find_current_session) then globs
#   the worktree-hash dir and comes up empty → exit 1.
#
#   find_jsonl_by_session_id locates the transcript by its (globally-unique) session
#   id across ALL project dirs, so it resolves regardless of the live cwd. This test
#   pins: (1) by-id finds the startup-dir transcript while the cwd-hash path is empty,
#   (2) subagent transcripts are excluded, (3) misses return (None, None).
#
# No external test runner exists yet (a full suite is GGC-27).
# scripts/prompt-lint.sh invokes lib/*.test.sh as a whole-repo invariant, so this
# runs as part of the `prompt` platform's verify-stage test_cmd.
#
# Run directly:  bash lib/session-metrics-worktree-locate.test.sh  (0 = pass, 1 = fail)

set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)

python3 - "$ROOT" <<'PY'
import os
import sys
import tempfile
from pathlib import Path

root = sys.argv[1]
sys.path.insert(0, os.path.join(root, "skills/shared/session-metrics"))
import session_metrics as sm  # noqa: E402

fails = 0


def check(label, got, want):
    global fails
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'} {label}: got={got} want={want}")
    if not ok:
        fails += 1


SID = "f309c128-1b56-403e-8b2e-37017ab0c5ac"

with tempfile.TemporaryDirectory() as tmp:
    projects = Path(tmp)
    # Point the module's PROJECTS_DIR at our fixture tree.
    orig = sm.PROJECTS_DIR
    sm.PROJECTS_DIR = projects
    try:
        # STARTUP dir: the real transcript lives here (top-level <sid>.jsonl).
        startup = projects / "-Users-x-Projects-work-project-gogox-client-flutter"
        startup.mkdir(parents=True)
        real = startup / f"{SID}.jsonl"
        real.write_text('{"type":"assistant"}\n')

        # WORKTREE dir: only a subagent SUBDIR named after the sid — NO top-level
        # <sid>.jsonl. This is exactly what an EnterWorktree leaves behind.
        worktree = projects / "-Users-x-Projects-work-project-CAF-835"
        (worktree / SID).mkdir(parents=True)

        # by-id resolves to the startup-dir transcript regardless of cwd.
        _, p = sm.find_jsonl_by_session_id(SID)
        check("by-id finds the startup-dir transcript", p, real)

        # The legacy cwd-hash locator, run against the WORKTREE hash, comes up empty
        # — this is the bug the fix routes around.
        wt_cwd = "/Users/x/Projects/work-project/CAF-835"
        check("cwd-hash locator is empty in the worktree (the bug)",
              sm.find_current_session(wt_cwd), (None, None))

        # Subagent guard: a top-level dir literally named "subagents" must be skipped.
        subdir = projects / "subagents"
        subdir.mkdir()
        (subdir / f"{SID}.jsonl").write_text('{"type":"assistant"}\n')
        _, p2 = sm.find_jsonl_by_session_id(SID)
        check("subagents/ transcript is excluded (still the real one)", p2, real)

        # Misses are quiet (None, None), never a crash.
        check("unknown session id -> (None, None)",
              sm.find_jsonl_by_session_id("00000000-0000-0000-0000-000000000000"),
              (None, None))
        check("empty session id -> (None, None)",
              sm.find_jsonl_by_session_id(""), (None, None))
    finally:
        sm.PROJECTS_DIR = orig

print(f"session-metrics-worktree-locate.test: {5 - fails} passed, {fails} failed")
sys.exit(1 if fails else 0)
PY
