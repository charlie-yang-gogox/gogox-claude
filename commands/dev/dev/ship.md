---
name: ship
description: "Stage 8 — archive OpenSpec changes, commit the archive, push the branch, open a draft PR, transition Linear to In Review, write the final session report, and post a summary comment to Linear. Auto mode only."
---

# `/dev:ship`

Final stage. Closes out the dev loop with archive + PR + Linear update + report.

## Inputs

- `.dev/state.json` (read for `ticket_id`, `change_name`, `worktree_path`, `verify`, `stage_history`, `mode`).
- The current branch (committed by `/dev:verify` and possibly amended by `/dev:review`).

## Outputs

- OpenSpec change archived (additional commit).
- Branch pushed; draft PR created via `/pull-request --draft`.
- Linear ticket: status `In Review`.
- `claude-reports/<ticket_id>/report.md` — final session report.
- Linear comment summarizing the run.
- `state.current_stage = "done"`.

## Step 0: Validate state

Run `/dev:_state-check ship`. STOP on non-zero. Parse for `ticket_id`, `change_name`, `worktree_path`, `mode`, `verify`, `stage_history`. Refuse if `mode != auto`.

## Step 1: Archive OpenSpec changes

1. Run `/opsx:archive` to archive all OpenSpec changes for `<change-name>`.
2. Commit the archived changes.
3. Run `/check-archive` to verify archive integrity.

## Step 2: Push and create PR

Run `/pull-request --draft` to push the branch and create the PR as a draft.

Capture the PR URL from the output.

## Step 3: Linear status update

Set ticket status to `In Review` via `mcp__claude_ai_Linear__save_issue`.

Reviewer assignment is intentionally not automated here — rely on `CODEOWNERS` (or PR template) to invite reviewers.

## Step 4: Write final report

Write `claude-reports/<ticket_id>/report.md`:

```markdown
# Auto-dev report: <ticket_id>

**Ticket**: <id> — <title>
**Branch**: <type>/<ticket_id>
**Pull Request**: <PR URL>
**Mode**: --auto (unattended)
**Date**: <YYYY-MM-DD>

## Stages

<For each entry in state.stage_history, render:>
- [<status icon>] **<stage>** — <result if any> — <ts>

## Verify
- Status: <state.verify.status>
- Retries: <state.verify.retry_count>
- Report: <state.verify.report>

## Errors / warnings
<any failures from stage_history with status: "failed">
```

Status icons: `✅ done`, `⏭ skipped`, `❌ failed`. (Use plain ASCII if emojis are off.)

## Step 5: Post Linear summary

Use `mcp__claude_ai_Linear__save_comment`:

```markdown
## Auto-dev complete: <ticket_id>

**PR**: <PR URL>
**Branch**: <type>/<ticket_id>
**Tests**: <pass | failed (note in report)>
**Verify**: CLEAR (retries: <n>)
**Review**: <N critical issues addressed>
```

## Step 6: Commit transition

```bash
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
PR_URL='<captured>'
jq --arg ts "$TS" --arg pr "$PR_URL" '
  .pr_url = $pr
  | .current_stage = "done"
  | .stage_history += [{ stage: "ship", status: "done", ts: $ts, result: $pr }]
' .dev/state.json > .dev/state.json.tmp && mv .dev/state.json.tmp .dev/state.json
```

## Step 7: Stop

Print: `Pipeline complete. PR: <PR URL>.`

The `.dev/state.json` survives on the local working tree as the run record. Removing `.dev/` is safe at any point after this stage.
