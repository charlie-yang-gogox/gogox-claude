---
name: ship
description: "Stage 8 — archive OpenSpec changes (feature mode only), commit the archive, push the branch, open a draft PR, transition the ticket to In Review (Linear or Jira), write the final session report, and post a summary comment to the tracker. Auto mode (or a direct mode: bug / feature-direct). Supports both Linear and Jira via the abstraction documented in `_ticket-lib.md`."
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
MODE=$(echo "$ARGUMENTS" | grep -q -- '--auto' && echo auto || echo default)

# Pipeline mode: bug / feature-direct / port-handoff / feature. Resolved by
# pipe_mode (lib/dev-mode.sh). is_direct_mode gates the --auto requirement and
# the apply path; the SEPARATE "is there a change dir to archive?" question is
# answered by the filesystem (Step 1 / has_change_dir), NOT by the mode name —
# a bug-lane spec delta means a direct mode can now carry a change dir too.
# See /dev:start --bug and /dev:verify Step 0.
source "$HOME/.claude/lib/dev-mode.sh"
PIPE_MODE=$(pipe_mode "$WT")

if [ "$MODE" != "auto" ] && ! is_direct_mode "$PIPE_MODE"; then
  echo "FAIL: /dev:ship requires --auto (or a direct mode: bug via /bug:ff, feature-direct on no-OpenSpec repos)." >&2
  exit 1
fi
[ -n "$TICKET_ID" ] || { echo "FAIL: cannot derive ticket_id from branch name" >&2; exit 1; }
[ -f "$WT/.dev/verify-pass.md" ] && grep -q '^Status: CLEAR' "$WT/.dev/verify-pass.md" \
  || { echo "FAIL: verify not CLEAR. Run /dev:verify first." >&2; exit 1; }
[ -f "claude-reports/$TICKET_ID/code-review.md" ] && ! grep -qiE '^critical:' "claude-reports/$TICKET_ID/code-review.md" \
  || { echo "FAIL: code-review.md missing or has critical findings. Run /dev:review first." >&2; exit 1; }

# Resolve the change dir from the FILESYSTEM (has_change_dir / ls), NOT from the
# mode. is_direct_mode is an apply-path predicate — it no longer implies "no
# change dir": a bug-lane fix that was spec-impacting authors an OpenSpec delta
# (see /dev:apply Step 0-bug.4.6) while staying a direct-apply bug. So archive
# is gated on "does a change dir exist", covering feature, port-handoff, AND a
# bug-authored delta uniformly.
N=$(ls "$WT/openspec/changes" 2>/dev/null | grep -v '^archive$' | head -1)
if ! is_direct_mode "$PIPE_MODE"; then
  # feature / port-handoff always ride the OpenSpec flow — a missing change dir
  # is a hard error there.
  [ -n "$N" ] || { echo "FAIL: no openspec change directory" >&2; exit 1; }
fi
# Direct modes: $N is empty for a plain bug / feature-direct (nothing to
# archive), or names the delta for a spec-impacting bug fix (archive it).
```

## Step 1: Archive OpenSpec changes (whenever a change dir exists)

Gate on `$N`, not on the mode. If `$N` is empty (a plain `bug` / `feature-direct` run with no spec impact), skip this step entirely and proceed to Step 2.

When `$N` is non-empty — **feature, port-handoff, or a bug-lane spec delta** — archive it. Two sub-paths by apply mode:

**Non-direct (`! is_direct_mode` — feature / port-handoff):**

1. Run `/opsx:archive` to archive all OpenSpec changes for `$N` (updates the main specs and moves the change to `archive/`).
2. Commit the archived changes.
3. Run `/check-archive` to verify archive integrity.

**Direct + spec delta (`is_direct_mode` AND `$N` non-empty — the bug lane):**

1. Sync the delta into the main specs **and** archive in one non-interactive step — `openspec archive` does both (its help: "Archive a completed change and update main specs"). There is NO `openspec sync` subcommand; do not invent one, and do NOT pass `--skip-specs` (the delta MUST land in the main specs — that is the whole point of authoring it):

   ```bash
   openspec archive "$N" -y || { echo "FAIL: openspec archive $N failed" >&2; exit 1; }
   ```

   `-y` skips the confirmation prompts (incomplete-artifact warnings etc.) so this never blocks a `--auto` run.
2. `git add -f "openspec/"` then `/commit` the archive as a `docs(openspec): <ticket-id> archive spec delta` commit (`-f` because some repos gitignore the change tree — see the `/port:ship` force-add precedent).
3. Post-archive guardrails (see `/dev:apply` Step 0-bug for the authoring half): post an English delta-summary comment on the ticket, and add the **non-gating** `spec-review-pending` label (async human sampling — it does NOT block ship; the delta is already in the main specs).

**Both sub-paths — unconditional assert** (fires whenever `$N` is non-empty; mirrors the bug-ship done marker): the stage must NOT report done if archive silently no-oped.

```bash
[ -z "$N" ] || [ -d "$WT/openspec/changes/archive/$N" ] || { echo "FAIL: archive did not produce openspec/changes/archive/$N — refusing to push an un-archived change." >&2; exit 1; }
```

## Step 2: Push and create PR

Run `/pull-request --draft` to push the branch and create the PR as a draft.

Capture the PR URL from the output.

## Step 3: Ticket status update (system-aware)

Resolve `TICKET_SYSTEM` (and `JIRA_CLOUD_ID` if Jira) via the
`_ticket-lib.md` resolution flow. Then:

**Linear path** — single `mcp__claude_ai_Linear__save_issue` call that performs both transitions in one payload:

1. Set ticket status to `In Review`.
2. Remove `dispatcher-dev-in-flight` label if currently present (no-op if absent). This is the canonical "dispatcher work is complete" signal — see `commands/dev/ggx-dispatcher.md` §2 Plan X. Without this, the next `/ggx-dispatcher` run would treat the just-shipped ticket as a recovery candidate via Q4.

If the ticket was NOT dispatched (user ran `/dev:ff --auto` from main with no in-flight label present), step 2 is a no-op — Linear `save_issue` accepts a label-set that excludes labels the ticket doesn't have.

**Jira path** — two calls:

1. `mcp__claude_ai_Atlassian_Rovo__getTransitionsForJiraIssue --cloudId "$JIRA_CLOUD_ID" --issueIdOrKey "$TICKET_ID"` → find the transition whose `to.name` matches `In Review` (case-insensitive). If no match exists in this project's workflow, fall back to `Code Review` or `Review`; if still no match, log one WARN line `dev:ship: no In Review transition available on Jira; status left unchanged` and continue (PR is the primary "done" signal).
2. `mcp__claude_ai_Atlassian_Rovo__transitionJiraIssue --cloudId "$JIRA_CLOUD_ID" --issueIdOrKey "$TICKET_ID" --transition '{"id":"<matched-id>"}'`.

Jira repos have no `dispatcher-dev-in-flight` label (dispatcher is Linear-only — see `_ticket-lib.md` "Workflow-label parity table"); skip the label-drop step entirely.

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

(Stage history is no longer persisted — git log + result files cover audit.)

## Step 5: Post tracker summary

System-aware post (uses the `TICKET_SYSTEM` resolved in Step 3):

- **Linear**: `mcp__claude_ai_Linear__save_comment --issueId "$TICKET_ID" --body <markdown>`
- **Jira**: `mcp__claude_ai_Atlassian_Rovo__addCommentToJiraIssue --cloudId "$JIRA_CLOUD_ID" --issueIdOrKey "$TICKET_ID" --commentBody <markdown>`

Body (identical for both trackers):

```markdown
## Auto-dev complete: <ticket_id>

**PR**: <PR URL>
**Branch**: <type>/<ticket_id>
**Tests**: <pass | failed (note in report)>
**Verify**: CLEAR
**Review**: <N critical issues addressed>
```

## Step 6: Stop

No state mutation. The done markers are:

- Feature / port-handoff (always have a change dir): (1) `openspec/changes/archive/$N/` exists, (2) the PR for the worktree branch is `OPEN` — resolve by head branch (`gh pr list --head "$(git branch --show-current)" --state all --json state -q '.[0].state'`), NOT `gh pr view $TICKET_ID` which fails when the branch is `<prefix>/<TICKET-ID>`, (3) tracker status is `In Review` (Linear) or matched transition applied (Jira), (4) `dispatcher-dev-in-flight` label absent on the ticket (Linear only — per Step 3 + `commands/dev/ggx-dispatcher.md` Plan X; Jira tickets do not have this label).
- Direct modes (bug / feature-direct): (2)–(4) above, plus (1) **conditionally** — a plain direct run has no change dir so (1) does not apply, but a bug-lane fix that authored a spec delta DOES produce `openspec/changes/archive/$N/` and the Step 1 assert requires it. So (1) is gated on `$N`: absent → skip, present → must be archived. The walker otherwise derives `done` from PR-open + tracker `In Review`.

Print: `Pipeline complete. PR: <PR URL>.`

Removing `.dev/` is safe at any point after this stage — all markers survive in git or Linear.
