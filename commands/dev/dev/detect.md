---
name: detect
description: "Stage 3 — detect the OpenSpec change state (A: no artifacts, B: apply-ready, C: partial). In default mode on state A, asks the user whether to inline-author via /opsx:ff or abort to run /port:ff first. Otherwise routes to /dev:align (B/C with Figma) or /dev:apply."
---

# `/dev:detect`

Determines what state the OpenSpec change is in and routes the pipeline accordingly.

## Inputs

- `.dev/state.json` (read for `change_name`, `platform`, `mode`, `figma`).
- `openspec list --json` and `openspec status --change <name> --json`.

## Outputs

- `state.openspec = { state: "A"|"B"|"C", change_dir: <path> }`.
- `state.current_stage = "align"` (B/C with figma.receipt) or `"apply"` (otherwise).
- A `skipped` entry for `align` in `stage_history` if we route directly to `apply`.
- **Abort path (state A, default mode, user picks `/port:ff`)**: no state mutation. Pipeline STOPs; re-running `/dev:ff` re-dispatches `/dev:detect` and re-asks the gate.

## Step 0: Validate state

Run `/dev:_state-check detect`. STOP on non-zero. Parse JSON for `change_name`, `mode`, `figma`.

## Step 1: List and match

Run `openspec list --json`. Match against `change_name`. Accept exact matches; if only one loose candidate obviously relates to the ticket, accept it.

- `mode == auto`: if multiple plausible candidates, pick the first. If the matched change has all `applyRequires` done → state B. If partial → state C. If empty/broken → `rm -rf` it and treat as state A.
- `mode == default`: if multiple candidates, **AskUserQuestion** to let the user pick.

## Step 2: Determine state

For the chosen change name, run `openspec status --change "<name>" --json`.

| State | Condition |
|---|---|
| **A** | no matching change directory exists for this ticket |
| **B** | all artifact IDs in `applyRequires` are `status: "done"` |
| **C** | change exists but some `applyRequires` artifacts are still pending |

`change_dir` = `openspec/changes/<change-name>/`.

Announce: `Detected state: <A|B|C>. Change: <change-name>.`

## Step 3: Routing

- **State A AND `mode == default`** → run the State-A gate (Step 3a). Two outcomes:
  - User picks `inline-author` → `current_stage = "apply"`. Append `align` to stage_history as `skipped` with reason `"state A — no existing artifacts to align against"`. Continue to Step 4.
  - User picks `abort-to-port` → STOP. Do not mutate `.dev/state.json`. Skip Steps 4–5 entirely. Print the abort hint (Step 3a). Re-running `/dev:ff` will re-dispatch `/dev:detect` and re-ask the gate (idempotent).
- **State A AND `mode == auto`** → `current_stage = "apply"`. Append `align` to stage_history as `skipped` with reason `"state A — no existing artifacts to align against"`. (Auto mode must not block on prompts; the dispatcher accepts inline-author quality for unattended runs.)
- **State B/C with `state.figma.receipt` present** → `current_stage = "align"`.
- **State B/C with `state.figma == null` or `state.figma.receipt == null`** → `current_stage = "apply"`. Append `align` as `skipped` with reason `"no figma receipt — alignment check has nothing to compare"`.

## Step 3a: State-A gate (default mode only)

Runs only when state == A AND mode == default. Skip otherwise.

Use **AskUserQuestion** with this single question:

> No OpenSpec artifacts exist for this ticket. `/dev:apply` can inline-author a lightweight spec via `/opsx:ff` (no pm/designer/synth grounding). For larger features that need stronger spec, `/port:ff` produces a properly-grounded one. How do you want to proceed?

Options (mutually exclusive, single-select):

1. **Inline author + apply** (recommended for bug fix / chore / small features)
   - Continue with `/dev:apply`'s lightweight authoring via `/opsx:ff`. Best when the spec quality bar is low and you want to ship fast. The current `/dev:apply` Step 1A.5 review gate still surfaces the authored artifacts before `/opsx:apply` runs.
2. **Abort to use `/port:ff`** (recommended for real features needing pm/designer/synth grounding)
   - Stop `/dev:ff`. Run `/port:ff <ticket-id>` separately to author a properly-grounded spec, then re-run `/dev:ff` (state will be B on the next `/dev:detect` run, so this gate does not re-fire).

On `abort-to-port`, print:

```
/dev:ff aborted at /dev:detect.
Run /port:ff <ticket-id> to author the spec, then re-run /dev:ff to resume implementation.
```

Then STOP. Do not run Steps 4 or 5. State remains untouched — re-running `/dev:ff` re-enters `/dev:detect` and re-asks the gate (idempotent by design; an audit entry is unnecessary because no side effect occurred).

## Step 4: Commit transition

_Skip this step entirely if Step 3a's `abort-to-port` branch was taken._

```bash
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
OS_STATE='<"A"|"B"|"C">'
CHANGE_DIR='<path>'
NEXT='<"align"|"apply">'
# If next == "apply", also append a skipped align entry
if [ "$NEXT" = "apply" ]; then
  SKIP_REASON='<reason string>'
  jq --arg ts "$TS" --arg s "$OS_STATE" --arg cd "$CHANGE_DIR" --arg reason "$SKIP_REASON" '
    .openspec = { state: $s, change_dir: $cd }
    | .current_stage = "apply"
    | .stage_history += [
        { stage: "detect", status: "done", ts: $ts, result: $s },
        { stage: "align",  status: "skipped", ts: $ts, reason: $reason }
      ]
  ' .dev/state.json > .dev/state.json.tmp && mv .dev/state.json.tmp .dev/state.json
else
  jq --arg ts "$TS" --arg s "$OS_STATE" --arg cd "$CHANGE_DIR" '
    .openspec = { state: $s, change_dir: $cd }
    | .current_stage = "align"
    | .stage_history += [{ stage: "detect", status: "done", ts: $ts, result: $s }]
  ' .dev/state.json > .dev/state.json.tmp && mv .dev/state.json.tmp .dev/state.json
fi
```

## Step 5: Stop

Print: `Detect complete. State: <A|B|C>. Next: /dev:<align|apply>.`
