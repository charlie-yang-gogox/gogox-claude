---
name: detect
description: "Stage 3 — detect the OpenSpec change state (A: no artifacts, B: apply-ready, C: partial). In default mode on state A, asks the user whether to inline-author via /opsx:ff or abort to run /port:ff first. Otherwise routes to /dev:align (B/C with Figma) or /dev:apply."
---

# `/dev:detect`

Determines what state the OpenSpec change is in and routes the pipeline accordingly. Per `plans/ff-state-rationalization.md` v8 this stage has **no persistent marker** — classification is re-derived each run via `openspec status --json` (cheap, ~1s, no network).

## Inputs

- `openspec list --json` and `openspec status --change <name> --json`.
- `.dev/figma-context.md` first line (existence + `Fetched: <ISO|FAILED|SKIPPED>` inspected for routing).

## Outputs

- No on-disk marker. `infer_dev_stage` re-derives based on `align-result.md` and `figma-context.md`'s first line after this stage finishes.
- In State A + `mode == default` + user picks `abort-to-port`: STOP with no side effect (idempotent re-run).

## Step 0: Inline precondition

```bash
WT=$(git rev-parse --show-toplevel)
N=$(ls "$WT/openspec/changes" 2>/dev/null | grep -v '^archive$' | head -1)
[ -n "$N" ] || { echo "FAIL: no openspec change directory found in $WT/openspec/changes/" >&2; exit 1; }
MODE=$(echo "$ARGUMENTS" | grep -q -- '--auto' && echo auto || echo default)
```

## Step 1: List and match

Run `openspec list --json`. Match against `$N` (the directory name). Accept exact matches; if only one loose candidate obviously relates to the ticket, accept it.

- `mode == auto`: if multiple plausible candidates, pick the first. If the matched change has all `applyRequires` done → state B. If partial → state C. If empty/broken → `rm -rf` it and treat as state A.
- `mode == default`: if multiple candidates, **AskUserQuestion** to let the user pick.

## Step 2: Determine state

For the chosen change name, run `openspec status --change "$N" --json`.

| State | Condition |
|---|---|
| **A** | no matching change directory exists for this ticket |
| **B** | `isComplete == true` (all `applyRequires` artifacts ready) |
| **C** | change exists but some artifacts are still pending (`isComplete == false`, but at least one artifact is `ready` or `complete`) |

`change_dir` = `openspec/changes/$N/`.

Announce: `Detected state: <A|B|C>. Change: $N.`

## Step 3: Routing

Determine the next stage by inspecting filesystem markers; do NOT mutate any state file.

- **State A AND `mode == default`** → run the State-A gate (Step 3a). Two outcomes:
  - User picks `inline-author` → next stage is `apply` (the walker reads `.dev/figma-context.md` — `Fetched: SKIPPED` first line routes past align directly to apply).
  - User picks `abort-to-port` → STOP. No side effect. Re-running `/dev:ff` re-dispatches `/dev:detect` and re-asks the gate (idempotent).
- **State A AND `mode == auto`** → next stage is `apply` (auto must not block; dispatcher accepts inline-author quality for unattended runs).
- **State B/C with Figma receipt present** (`.dev/figma-context.md` exists) → next stage is `align`. Walker reads `.dev/align-result.md` after align runs; CLEAR advances to apply.
- **State B/C with `.dev/figma-context.md` first line `Fetched: SKIPPED`** → next stage is `apply`. The SKIPPED first line signals "no figma source"; align has nothing to compare against. Walker treats SKIPPED as a completed figma stage and routes directly to apply.

The actual advance happens in `/dev:ff`'s walker — this stage's job is to classify and let routing fall out of the markers naturally.

## Step 3a: State-A gate (default mode only)

Runs only when state == A AND mode == default. Skip otherwise.

Use **AskUserQuestion** with this single question:

> No OpenSpec artifacts exist for this ticket. `/dev:apply` can inline-author a lightweight spec via `/opsx:ff` (no pm/designer/synth grounding). For larger features that need stronger spec, `/port:ff` produces a properly-grounded one. How do you want to proceed?

Options (mutually exclusive, single-select):

1. **Inline author + apply** (recommended for bug fix / chore / small features)
   - Continue with `/dev:apply`'s lightweight authoring via `/opsx:ff`. Best when the spec quality bar is low and you want to ship fast. The current `/dev:apply` review gate still surfaces the authored artifacts before `/opsx:apply` runs.
2. **Abort to use `/port:ff`** (recommended for real features needing pm/designer/synth grounding)
   - Stop `/dev:ff`. Run `/port:ff <ticket-id>` separately to author a properly-grounded spec, then re-run `/dev:ff` to resume. State will be B on the next `/dev:detect` run, so this gate does not re-fire.

On `abort-to-port`, print:

```
/dev:ff aborted at /dev:detect.
Run /port:ff <ticket-id> to author the spec, then re-run /dev:ff to resume implementation.
```

Then STOP. No filesystem side effect.

## Step 4: Stop

Print: `Detect complete. State: <A|B|C>. Next: /dev:<align|apply>.`

(The walker decides the next stage automatically on the next `/dev:ff` iteration; this stage just announces.)
