---
name: review
description: "Stage 7 — run /code-review on the diff. Address critical findings, looping back to /dev:verify if any code changed. Saves the report to claude-reports/<ticket-id>/code-review.md. Auto mode only."
---

# `/dev:review`

Self-review of the committed diff. Distinct from verify-agent: verify-agent audits contract surfaces (renames, missed call sites); `/code-review` is broader code-review-style critique (logic, design, edge cases).

## Inputs

- `.dev/state.json` (read for `ticket_id`, `mode`, `verify`).
- The committed diff from `/dev:verify`.

## Outputs

- `claude-reports/<ticket_id>/code-review.md`.
- If critical issues found and fixed: a new commit; loops back through `/dev:verify`.
- `state.current_stage = "ship"`.

## Step 0: Validate state

Run `/dev:_state-check review`. STOP on non-zero. Parse for `ticket_id`, `mode`. Refuse if `mode != auto`.

## Step 1: Run /code-review

Run `/code-review` against the current branch's diff vs `state.base_ref`.

## Step 2: Address critical issues

If `/code-review` flags **critical** issues:

1. Fix them in source.
2. Reset `state.current_stage = "verify"`, append `{stage: "review", status: "failed", ts, reason: "critical findings — looping to verify"}`. Atomic write.
3. STOP this stage and let `/dev:ff` re-enter `/dev:verify` to re-test, re-audit, re-commit.

The point of looping back through verify (not just re-running review) is that the fixes are new code that needs the same contract-surface audit + test gate as the original implementation.

If only non-critical issues: note them in the report but do not block.

## Step 3: Save report

```bash
mkdir -p "claude-reports/<ticket_id>"
# /code-review's output → claude-reports/<ticket_id>/code-review.md
```

## Step 4: Commit transition

```bash
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
jq --arg ts "$TS" '
  .current_stage = "ship"
  | .stage_history += [{ stage: "review", status: "done", ts: $ts }]
' .dev/state.json > .dev/state.json.tmp && mv .dev/state.json.tmp .dev/state.json
```

## Step 5: Stop

Print: `Review complete. Next: /dev:ship.`
