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

# Pipeline mode: bug / feature-direct / port-handoff / feature. Resolved by pipe_mode
# (lib/dev-mode.sh); is_direct_mode answers whether it rides the OpenSpec flow.
source "$HOME/.claude/lib/dev-mode.sh"
PIPE_MODE=$(pipe_mode "$WT")
BASE_REF=$(trunk_ref)   # origin/<default branch> — trunk (flutter) or main (gogox-claude)

if [ "$MODE" != "auto" ] && ! is_direct_mode "$PIPE_MODE"; then
  echo "FAIL: /dev:review requires --auto (or a direct mode: bug via /bug:ff, feature-direct on no-OpenSpec repos)." >&2
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

## Step 2.5: Lazy-None catch (bug lane spec-impact)

Defensive cross-check for the bug lane's Spec-Impact rubric. If `.dev/apply-result.md` carries `Spec-Impact: None` **but** the committed diff touches a criterion-(b) contract surface, the rubric was likely a lazy `None` and a spec delta is missing:

```bash
if [ -f "$WT/.dev/apply-result.md" ] && grep -q '^Spec-Impact: None' "$WT/.dev/apply-result.md" \
   && git -C "$WT" diff "$BASE_REF"...HEAD --name-only | grep -qE '^lib/apis/|^lib/services/analytics/|analytics_events\.dart'; then
  echo "REVIEW FLAG: Spec-Impact: None but the diff touches a contract surface (criterion b)."
fi
```

Treat a raised flag as a review finding to resolve: either the verdict is wrong (re-enter `/dev:apply` to author the delta — delete `.dev/verify-pass.md` so the walker loops back through verify, same as a critical fix) or there is a defensible reason it is genuinely `None` (record it in the report). This is a no-op on non-bug lanes and on repos without those paths (e.g. gogox-claude).

## Step 3: Save report

```bash
mkdir -p "claude-reports/$TICKET_ID"
# /code-review's output → claude-reports/$TICKET_ID/code-review.md
# Make sure no `^critical:` lines remain — those would re-trigger the loop above.
```

## Step 4: Stop

No state mutation. The done marker is `claude-reports/$TICKET_ID/code-review.md` without any `^critical:` line — the walker advances to `ship`.

Print: `Review complete. Next: /dev:ship.`
