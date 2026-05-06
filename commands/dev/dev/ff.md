---
name: ff
description: "Fast-forward orchestrator — chains every /dev:* atomic stage from the current state to `done` (or to the next HITL gate / failure). Pass `<ticket-id> [--auto]` to start fresh; pass nothing to resume an in-progress pipeline. Mirrors the /port:ff pattern."
Prerequisite: >
  Same as /dev:start when starting fresh. When resuming, only requires that
  `.dev/state.json` exists with a non-`done` `current_stage`.
---

# `/dev:ff`

Single-command run-the-whole-pipeline. Reads `.dev/state.json` to know where to start and dispatches each atomic stage in order. Stops on the first failure, HITL gate (default mode), or completion.

## Usage

- `/dev:ff <ticket-id> --auto` — start a fresh auto-mode pipeline. Equivalent to: `/dev:start <ticket-id> --auto` followed by chaining all stages until `done`.
- `/dev:ff <ticket-id>` — start a fresh default-mode pipeline. Stops at the first HITL gate (`/dev:apply`'s review gate by default).
- `/dev:ff` — resume. Reads `.dev/state.json` and dispatches `/dev:<current_stage>`. Loops until `done`, failure, or HITL gate.
- `/dev:ff --from <stage>` — reset `current_stage` to `<stage>` and resume from there. Truncates `stage_history` after that point.

## Step 0: Decide entry point

```bash
if [ -f .dev/state.json ]; then
  CURRENT=$(jq -r '.current_stage' .dev/state.json)
  TICKET=$(jq -r '.ticket_id' .dev/state.json)
  MODE=$(jq -r '.mode' .dev/state.json)
else
  CURRENT="start"
fi
```

- If `$ARGUMENTS` contains a ticket-id AND `state.json` does not exist: invoke `/dev:start <ticket-id> [--auto]`, then continue from the new `current_stage`.
- If `$ARGUMENTS` contains a ticket-id AND `state.json` exists: refuse (use `/dev:start`'s re-entry rules — do not silently overwrite).
- If `$ARGUMENTS` is empty AND `state.json` exists: resume.
- If `$ARGUMENTS` is empty AND `state.json` does not exist: STOP with usage.
- `--from <stage>` flag: reset before dispatching (see Step 0a).

### Step 0a: --from handling

```bash
if [ "$FROM" != "" ]; then
  TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  jq --arg s "$FROM" --arg ts "$TS" '
    .current_stage = $s
    | .stage_history = (.stage_history | map(select(
        ([.stage] | inside(["start","figma","detect","align","apply","verify","review","ship"])) and
        (([.stage] | index($s)) == null)
      )))
    | .stage_history += [{ stage: "ff", status: "done", ts: $ts, result: ("reset to " + $s) }]
  ' .dev/state.json > .dev/state.json.tmp && mv .dev/state.json.tmp .dev/state.json
fi
```

(Implementation note: the truncation logic above is approximate — in practice, drop history entries that come after the `<stage>` entry. Use a precise jq filter when implementing.)

## Step 1: Dispatch loop

```
while current_stage != "done":
  read state.json
  case current_stage of:
    "start"  → /dev:start <ticket-id> [--auto]
    "figma"  → /dev:figma
    "detect" → /dev:detect
    "align"  → /dev:align
    "apply"  → /dev:apply
    "verify" → /dev:verify
    "review" → /dev:review
    "ship"   → /dev:ship

  if the dispatched stage failed (exit non-zero or wrote stage_history failed entry):
    STOP — emit a resume hint:
      "Pipeline halted at /dev:<current_stage>. Inspect .dev/state.json. Resume with /dev:ff."

  if the dispatched stage paused for HITL (default mode review gate):
    STOP — print the gate options and let the user respond.

  refresh state.json and loop.
```

Implementation requirement: each stage update must be atomic (Step transitions in stage files use `mv .dev/state.json.tmp .dev/state.json`). The orchestrator re-reads state after each stage to pick up the new `current_stage`.

## Mode-specific behavior

- **`mode == auto`**: no HITL gates fire. Stages stop only on failure. Loop runs end-to-end without prompts.
- **`mode == default`**: HITL gates in `/dev:figma` (failure case), `/dev:align`, and `/dev:apply` will pause. The user resumes with `/dev:ff` after answering the gate.

## Failure recovery

- A failed stage leaves `current_stage` unchanged AND appends a `failed` history entry.
- Re-running `/dev:ff` re-dispatches the same stage. The stage's idempotency rules apply — most stages refuse to re-run without `--force` to prevent silent overwrites.
- For a clean reset of one stage: `/dev:<stage> --force`.
- For a clean reset back to an earlier point: `/dev:ff --from <stage>`.

## Output

Final status (whether success or pause): print `current_stage`, last 3 entries of `stage_history`, and a one-line "next action" hint.

## Guardrails

- Does NOT bypass any individual stage's preconditions — `/dev:_state-check` runs at the start of every stage regardless.
- Does NOT skip `verify`. The auditor/implementer split is non-negotiable.
- Does NOT modify Git settings, force-push, or operate destructively without explicit user request.
