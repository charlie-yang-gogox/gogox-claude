---
name: review
description: "Stage 7 — run /code-review on the diff. Address critical findings, looping back to /dev:verify if any code changed. Saves the report to claude-reports/<ticket-id>/code-review.md. Auto mode only."
---

# `/dev:review`

Self-review of the committed diff. Distinct from verify-agent: verify-agent audits contract surfaces (renames, missed call sites); `/code-review` is broader code-review-style critique (logic, design, edge cases).

## Inputs

- The committed diff from `/dev:verify`.
- `ticket_id` derived from branch name.

## Outputs

- `claude-reports/<ticket_id>/code-review.md` — **the stage's done marker** (when no `^critical:` line is present).
- If critical issues found and fixed: a new commit; the walker loops back through `/dev:verify` because the new commit invalidates the previous `verify-pass.md`.

## Step 0: Inline precondition

```bash
WT=$(git rev-parse --show-toplevel)
TICKET_ID=$(git rev-parse --abbrev-ref HEAD | grep -oE '[A-Z]+-[0-9]+' | head -1)
MODE=$(echo "$ARGUMENTS" | grep -q -- '--auto' && echo auto || echo default)
BASE_REF="origin/trunk"   # default

# Pipeline mode: bug vs feature. Resolved by pipe_mode (lib/dev-mode.sh).
source "$HOME/.claude/lib/dev-mode.sh"
PIPE_MODE=$(pipe_mode "$WT")

if [ "$MODE" != "auto" ] && [ "$PIPE_MODE" != "bug" ]; then
  echo "FAIL: /dev:review requires --auto (or bug mode via /bug:ff)." >&2
  exit 1
fi
[ -f "$WT/.dev/verify-pass.md" ] && grep -q '^Status: CLEAR' "$WT/.dev/verify-pass.md" \
  || { echo "FAIL: .dev/verify-pass.md missing or not CLEAR. Run /dev:verify first." >&2; exit 1; }
[ -n "$TICKET_ID" ] || { echo "FAIL: cannot derive ticket_id from branch name" >&2; exit 1; }
```

## Step 1: Run /code-review

Run `/code-review --auto` against the current branch's diff vs `$BASE_REF`. The `--auto` flag is mandatory here — `/dev:review` is an auto-only stage, and when the parent `/dev:ff` runs inside a `/ggx-dispatcher`-spawned `general-purpose` subagent the inner `/code-review` cannot spawn `git-branch-code-reviewer` via the `Agent` tool (nested-Agent spawn is officially unsupported — see `ARCHITECTURE.md` "Nested-spawn constraint"). `--auto` triggers `/code-review`'s inline execution path; see `commands/dev/code-review.md` step 2's mode table.

## Step 2: Address critical issues

If `/code-review` flags **critical** issues:

1. Fix them in source.
2. The new edits invalidate the existing `.dev/verify-pass.md` — delete it (`rm -f .dev/verify-pass.md`) so the walker routes back to verify.
3. STOP this stage. Walker re-enters `/dev:verify` on next `/dev:ff` iteration to re-test, re-audit, re-commit.

The point of looping back through verify (not just re-running review) is that the fixes are new code that needs the same contract-surface audit + test gate as the original implementation.

If only non-critical issues: note them in the report but do not block.

## Step 3: Save report

```bash
mkdir -p "claude-reports/$TICKET_ID"
# /code-review's output → claude-reports/$TICKET_ID/code-review.md
# Make sure no `^critical:` lines remain — those would re-trigger the loop above.
```

## Step 4: Stop

No state mutation. The done marker is `claude-reports/$TICKET_ID/code-review.md` without any `^critical:` line — the walker advances to `ship`.

Print: `Review complete. Next: /dev:ship.`
