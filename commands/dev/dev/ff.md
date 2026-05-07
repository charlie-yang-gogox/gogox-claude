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

- If `$ARGUMENTS` contains a ticket-id AND `state.json` does not exist: invoke `/dev:start <ticket-id> [--auto] [--no-figma]`, then continue from the new `current_stage`.
- If `$ARGUMENTS` contains a ticket-id AND `state.json` exists: refuse (use `/dev:start`'s re-entry rules — do not silently overwrite).
- If `$ARGUMENTS` is empty AND `state.json` exists: resume.
- If `$ARGUMENTS` is empty AND `state.json` does not exist: STOP with usage.
- `--from <stage>` flag: reset before dispatching (see Step 0a).
- `--auto` flag on a state with `mode == default`: upgrade mode (see Step 0b). This is the canonical path from `done_default` to a full auto chain.

### Step 0a: --from handling

Reset `current_stage` to `<stage>` and **drop the most recent `<stage>` entry plus everything after it** from `stage_history`. The next stage run will re-append a fresh entry on completion (no duplicate "done" entries).

```bash
if [ "$FROM" != "" ]; then
  TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  jq --arg s "$FROM" --arg ts "$TS" '
    # rindex finds the LAST occurrence — there may be earlier <stage> entries
    # from prior --from cycles; we want to truncate at the most recent one.
    ([.stage_history[].stage] | rindex($s)) as $i
    | .current_stage = $s
    | .stage_history = (if $i == null then [] else .stage_history[:$i] end)
    | .stage_history += [{ stage: "ff", status: "done", ts: $ts, result: ("reset to " + $s) }]
  ' .dev/state.json > .dev/state.json.tmp && mv .dev/state.json.tmp .dev/state.json
fi
```

The `rindex` (last-index) variant matters: a user who has already done `--from align` once and then does `--from align` again must truncate at the SECOND `align` entry, not the first. The reset marker (`{stage: "ff", result: "reset to <s>"}`) is appended AFTER the slice so the audit trail shows the rewind happened.

### Step 0b: Mode upgrade handling (default → auto)

If `--auto` was passed AND `state.mode == default`, upgrade mode before dispatch:

```bash
if [ "$AUTO_FLAG" = "1" ] && [ "$(jq -r '.mode' .dev/state.json)" = "default" ]; then
  TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  CUR=$(jq -r '.current_stage' .dev/state.json)

  # If the pipeline already terminated at done_default, advance to verify.
  # Otherwise leave current_stage where it is — the upgrade just unlocks the
  # auto-only stages for whatever comes next.
  NEW_STAGE=$([ "$CUR" = "done_default" ] && echo "verify" || echo "$CUR")

  jq --arg ts "$TS" --arg ns "$NEW_STAGE" '
    .mode = "auto"
    | .current_stage = $ns
    | .stage_history += [{
        stage: "ff",
        status: "done",
        ts: $ts,
        result: ("default→auto upgrade; resuming at " + $ns)
      }]
  ' .dev/state.json > .dev/state.json.tmp && mv .dev/state.json.tmp .dev/state.json
fi
```

`auto → default` is NOT supported here — it would orphan auto-only state (e.g. `verify.status`). If a user really wants to drop back, they remove `.dev/state.json` and re-run `/dev:start`.

## Step 1: Dispatch loop

Both `done` (auto-mode terminal) and `done_default` (default-mode terminal) end the loop.

```
while current_stage NOT IN { "done", "done_default" }:
  read state.json
  case current_stage of:
    "start"  → /dev:start <ticket-id> [--auto] [--no-figma]
    "figma"  → /dev:figma
    "detect" → /dev:detect
    "align"  → /dev:align
    "apply"  → /dev:apply
    "verify" → /dev:verify
    "review" → /dev:review
    "ship"   → /dev:ship

  After dispatch, re-read state.json to detect what happened:
    - If stage_history's last entry has status == "failed":
        STOP — emit resume hint:
          "Pipeline halted at /dev:<previous current_stage>. Reason: <last entry's reason>.
           Inspect .dev/state.json. Resume with /dev:ff (or /dev:<stage> --force to retry that stage)."
    - If current_stage is unchanged (stage paused at HITL gate or was a no-op):
        STOP — print the gate prompt or the stage's stop message, let the user respond.
    - Otherwise: loop.
```

**Failure detection — read the history, not the exit code.** Slash-command sub-invocations don't surface meaningful exit codes to a parent skill; the only reliable signal that a stage failed is a `{status: "failed"}` entry in `stage_history`. Always check the history first.

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
