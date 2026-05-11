---
name: ship
description: "Stage 8 — archive OpenSpec changes, commit the archive, push the branch, open a draft PR, transition Linear to In Review, write the final session report, and post a summary comment to Linear. Auto mode only."
---

# `/dev:ship`

Final stage. Closes out the dev loop with archive + PR + Linear update + report.

## Inputs

- The current branch (committed by `/dev:verify` and possibly amended by `/dev:review`).
- `ticket_id`, `change_name` derived from environment.

## Outputs

- OpenSpec change archived (additional commit).
- Branch pushed; draft PR created via `/pull-request --draft`. **PR open + archive dir IS the done marker.**
- Linear ticket: status `In Review`.
- `claude-reports/<ticket_id>/report.md` — final session report.
- Linear comment summarizing the run.

## Step 0: Inline precondition

```bash
WT=$(git rev-parse --show-toplevel)
TICKET_ID=$(git rev-parse --abbrev-ref HEAD | grep -oE '[A-Z]+-[0-9]+' | head -1)
N=$(ls "$WT/openspec/changes" 2>/dev/null | grep -v '^archive$' | head -1)
MODE=$(echo "$ARGUMENTS" | grep -q -- '--auto' && echo auto || echo default)

[ "$MODE" = "auto" ] || { echo "FAIL: /dev:ship is auto-only." >&2; exit 1; }
[ -n "$N" ] || { echo "FAIL: no openspec change directory" >&2; exit 1; }
[ -n "$TICKET_ID" ] || { echo "FAIL: cannot derive ticket_id from branch name" >&2; exit 1; }
[ -f "$WT/.dev/verify-pass.md" ] && grep -q '^Status: CLEAR' "$WT/.dev/verify-pass.md" \
  || { echo "FAIL: verify not CLEAR. Run /dev:verify first." >&2; exit 1; }
[ -f "claude-reports/$TICKET_ID/code-review.md" ] && ! grep -qiE '^critical:' "claude-reports/$TICKET_ID/code-review.md" \
  || { echo "FAIL: code-review.md missing or has critical findings. Run /dev:review first." >&2; exit 1; }
```

## Step 1: Archive OpenSpec changes

1. Run `/opsx:archive` to archive all OpenSpec changes for `$N`.
2. Commit the archived changes.
3. Run `/check-archive` to verify archive integrity.

## Step 2: Push and create PR

Run `/pull-request --draft` to push the branch and create the PR as a draft.

Capture the PR URL from the output.

## Step 3: Linear status update

Set ticket status to `In Review` via `mcp__claude_ai_Linear__save_issue`.

Reviewer assignment is intentionally not automated here — rely on `CODEOWNERS` (or PR template) to invite reviewers.

## Step 4: Write final report

Write `claude-reports/$TICKET_ID/report.md`:

```markdown
# Auto-dev report: <ticket_id>

**Ticket**: <id> — <title>
**Branch**: <type>/<ticket_id>
**Pull Request**: <PR URL>
**Mode**: --auto (unattended)
**Date**: <YYYY-MM-DD>

## Verify
- Status: CLEAR (from .dev/verify-pass.md)

## Review
- Findings: <see claude-reports/<ticket_id>/code-review.md>

## Errors / warnings
<any test failures noted during /dev:verify>
```

(Stage history is no longer persisted — git log + result files cover audit per `plans/ff-state-rationalization.md` §7.)

## Step 5: Post Linear summary

Use `mcp__claude_ai_Linear__save_comment`:

```markdown
## Auto-dev complete: <ticket_id>

**PR**: <PR URL>
**Branch**: <type>/<ticket_id>
**Tests**: <pass | failed (note in report)>
**Verify**: CLEAR
**Review**: <N critical issues addressed>
```

## Step 6: Stop

No state mutation. The done markers are: (1) `openspec/changes/archive/$N/` exists, (2) `gh pr view $TICKET_ID --json state -q .state` returns `OPEN`, (3) Linear status is `In Review`.

Print: `Pipeline complete. PR: <PR URL>.`

Removing `.dev/` is safe at any point after this stage — all markers survive in git or Linear.
