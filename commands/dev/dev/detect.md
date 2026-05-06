---
name: detect
description: "Stage 3 — detect the OpenSpec change state (A: no artifacts, B: apply-ready, C: partial). Decides whether to run /dev:align next (B/C with Figma) or skip directly to /dev:apply (state A or no Figma)."
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

- **State A** → `current_stage = "apply"`. Append `align` to stage_history as `skipped` with reason `"state A — no existing artifacts to align against"`.
- **State B/C with `state.figma.receipt` present** → `current_stage = "align"`.
- **State B/C with `state.figma == null` or `state.figma.receipt == null`** → `current_stage = "apply"`. Append `align` as `skipped` with reason `"no figma receipt — alignment check has nothing to compare"`.

## Step 4: Commit transition

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
