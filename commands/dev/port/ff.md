---
name: port:ff
description: >
  Fast-forward wrapper that chains the six atomic port stages
  (start → explore → plan → synth → revise → ship) into one
  invocation. Honors each stage's HITL gates by default; `--auto`
  applies the G1-G9 decision rules for unattended dispatcher use.
  Naming follows the OpenSpec `/opsx:ff` convention — same intent,
  port pipeline scope.
---

# /port:ff — Full Port Pipeline

Run the entire port flow end-to-end from a single invocation. Mirrors what `/opsx:ff` does for OpenSpec changes: take a ticket, produce a ready-for-implementation outcome (here: pushed branch + Linear summary). Each underlying stage retains its own HITL gates in default mode; `--auto` collapses them via the auto-decision table.

**Usage**:

- `/port:ff --ticket:<ID>` — HITL run (Locate gate, pre-review clarification, review gate still pause for input).
- `/port:ff --ticket:<ID> --auto` — Unattended run for the dispatcher; aborts on locate-low or agent double-failure.
- `/port:ff --ticket:<ID> --simple` — Shortcut that invokes only `/port:explore --simple --ticket:<ID>`. No worktree, no spec; produces a Linear analysis comment for ticket enrichment.
- `/port:ff --ticket:<ID> --no-ticket-init` — Skip the Linear ticket-init step (status / labels / assignee / estimate / starting comment). Use when running the pipeline locally for inspection / debugging without flipping the ticket on Linear. Default: enabled. Passed through verbatim to `/port:start`.

**Required**: `--ticket:<ID>`. The wrapper has no cwd context to infer from.

---

## Steps

### Step 1: Parse arguments

Parse `$ARGUMENTS`:

- Extract `--ticket:<ID>` (required). Missing → STOP with:
  > `/port:ff` requires `--ticket:<ID>` (e.g. `/port:ff --ticket:CAF-212`).
- Detect flags: `--auto`, `--simple`, `--no-ticket-init`. `--auto` and `--simple` are mutually exclusive — `--simple` ignores `--auto` (no autonomy needed for a one-stage run). `--no-ticket-init` is passed through verbatim to `/port:start`.
- All other flags are passed through to the underlying stages.

### Step 2: Simple-mode shortcut

If `--simple` is present:

```
/port:explore --simple --ticket:<ID>
```

Print "`/port:ff --simple` complete — see Linear ticket for the analysis comment." and STOP. Do NOT continue to start/plan/synth/revise/ship.

### Step 3: Decide chain mode

If neither `--simple` nor `--auto` is present → **HITL mode**.
If `--auto` is present → **auto mode**, governed by the G1-G9 decision rules below. Set `<auto-flag>` accordingly so subsequent stage invocations include the right flag.

### Step 3a: Derive entry point via `infer_port_stage`

`/port:ff` resumes from the filesystem instead of running every stage unconditionally. If a previous run on this ticket already produced `.port/dev-notes.md`, the walker advances to `plan` and skips `start` + `explore`.

```bash
infer_port_stage() {
  local n wt id
  wt=$(git rev-parse --show-toplevel 2>/dev/null)
  id="<ticket-id from --ticket: argument>"
  n=$(ls "$wt/openspec/changes" 2>/dev/null | grep -v '^archive$' | head -1)

  # ship complete?
  # Resolve PR by HEAD BRANCH, not ticket id (branch is <prefix>/<TICKET-ID>).
  if [ -n "$id" ] && gh pr list --head "$(git -C "$wt" branch --show-current 2>/dev/null)" --state all --json state -q '.[0].state' 2>/dev/null | grep -q OPEN; then
    echo done; return; fi

  if [ -n "$n" ]; then
    # revise approved? `Review approved` line is appended to synth-report.md by /port:revise step 10
    if grep -q '^Review approved' "$wt/openspec/changes/$n/.port/synth-report.md" 2>/dev/null; then
      echo ship; return; fi

    # synth complete?
    if [ -f "$wt/openspec/changes/$n/.port/synth-report.md" ]; then
      echo revise; return; fi

    # plan complete?
    if [ -f "$wt/openspec/changes/$n/.port/pm-notes.md" ] \
       && [ -f "$wt/openspec/changes/$n/.port/design-notes.md" ]; then
      echo synth; return; fi

    # explore complete?
    if [ -f "$wt/openspec/changes/$n/.port/dev-notes.md" ]; then
      echo plan; return; fi

    # start complete? (proposal skeleton + .port dir)
    if [ -f "$wt/openspec/changes/$n/proposal.md" ] \
       && [ -d "$wt/openspec/changes/$n/.port" ]; then
      echo explore; return; fi
  fi

  echo start
}

CURRENT=$(infer_port_stage)
```

The dispatch loop (Steps 4–9 below) **starts at `$CURRENT`**, not at `start`. Each step's "if `$CURRENT` >= this stage in dependency order, run it" check makes resume free.

### Step 4: Stage 1 — `/port:start` (skip if `$CURRENT` != `start`)

Invoke:

```
/port:start --ticket:<ID> [<auto-flag>] [--no-ticket-init]
```

Pass through `--prd:` / `--prd-file:` / `--recreate` / `--no-ticket-init` if the user provided them.

If `/port:start` exits non-zero or aborts (e.g. assignee mismatch, worktree user-aborted): bubble up the error and STOP. Do not proceed.

After success, cwd is now inside the worktree at `../<ticket-id>`. All subsequent stage invocations run from this cwd.

### Step 5: Stage 2 — `/port:explore` (skip if `$CURRENT` is past explore: `plan`/`synth`/`revise`/`ship`/`done`)

```
/port:explore [<auto-flag>]
```

Auto-detect ticket-id from worktree (already verified in step 4). The Locate gate is internal to `/port:explore`:

- `--auto` Locate `low` → the stage itself aborts and posts to Linear (per its own logic). The wrapper bubbles up that exit and STOPs without running plan/synth/revise/ship. Append a `claude-reports/<session>/ff-aborted.md` with reason `locate-low`.
- `--auto` Locate `medium` → proceeds with primary candidate, flag is recorded by the stage.
- HITL Locate `medium` / `low` → user is prompted by `/port:explore`; their choice determines whether the wrapper continues.

### Step 6: Stage 3 — `/port:plan` (skip if `$CURRENT` is past plan: `synth`/`revise`/`ship`/`done`)

```
/port:plan [<auto-flag>]
```

Two parallel agents (pm + designer) run inside `/port:plan`. The wrapper waits for completion.

Stage failure handling: if either agent fails twice (per G6), `/port:plan` aborts; the wrapper bubbles up and STOPs.

### Step 7: Stage 4 — `/port:synth` (skip if `$CURRENT` is past synth: `revise`/`ship`/`done`)

```
/port:synth [<auto-flag>]
```

Runs `synth-agent` (opus pinned) + `openspec validate` + `/spec-lint`. Always exits success even with findings — those are the input to revise. Validate errors are surfaced but do not abort the wrapper.

### Step 8: Stage 5 — `/port:revise` (skip if `$CURRENT` is past revise: `ship`/`done`)

```
/port:revise [<auto-flag>]
```

In `--auto`:
- All findings auto-accepted per G7.
- Final approval auto-granted per G8.
- The stage writes the `Review approved` sentinel and exits success.

In HITL: the user drives the clarification batches and the review gate. If the user picks `abort`, the wrapper bubbles up the exit and STOPs (worktree preserved per existing convention).

### Step 9: Stage 6 — `/port:ship` (skip if `$CURRENT == done`)

```
/port:ship [<auto-flag>]
```

Final stage — commit, push, Linear write-back. Failure handling per its own logic:

- Push failure: the wrapper bubbles up. Worktree + lock retained for manual recovery.
- Linear MCP failure after retries: `/port:ship` STOPs without writing any payload file; wrapper bubbles up. `/port:ship` is idempotent — user simply re-runs it once the Linear flake clears.

### Step 10: Final report

On full success:

```
Port pipeline complete: <ticket-id> — <change-name>

Branch     : feat/<ticket-id> (pushed)
Spec tree  : <tree-url>
Linear     : description updated + summary comment posted
Worktree   : <worktree-path> (kept for /dev:ff)

Mode       : <HITL | auto>
Duration   : <total ms from .port/timings.jsonl summed>
Auto-fixes : <count, in --auto mode> 

Next: cd <worktree-path> && /dev:ff
```

In `--auto` mode also print the path to `claude-reports/<session>/` for the dispatcher to inspect.

### Step 11: Cleanup port-internal runtime files

Run ONLY on full success (after step 10's report has been printed). Skip entirely on any abort path — the file is the audit trail for debug.

`.port/timings.jsonl` accumulates a per-stage telemetry line across the chain (start → explore → plan → synth → revise → ship). Step 10 reads + summarizes it. After that summary is in the user's hand, the file has served its purpose — keeping it around leaks port-internal observability into the project repo (and dirties the working tree for the next pipeline).

Delete it:

```bash
rm -f "<worktree>/.port/timings.jsonl"
```

Rationale: the gitignore in `/port:ship` step 5 prevents the file from being committed, but doesn't prevent it from existing on disk. Physical deletion is what the chain orchestrator owes the user — `.port/` should look the same after a successful pipeline as it did before, minus the consultative artifacts that the ship commit captured.

If a user invokes individual stages manually (no `/port:ff`), they own their own cleanup — the gitignore still protects them from accidental commits.

---

## Auto-mode decision rules (G1–G9)

The wrapper does not override stage-internal decisions; it only ensures every stage runs with `--auto` so they apply the same table consistently.

| Gate | Where it lives | Auto rule |
|---|---|---|
| **G1** Thin ticket / PRD missing | `/port:start` | Use Linear `<!-- port:simple:start -->` block if present, else proceed with empty PRD; agents infer. |
| **G2** Worktree already exists | `/port:start` | Always `recreate`. |
| **G3** OpenSpec change already exists | `/port:start` | Always `recreate`. |
| **G4** Existing `.port/*.md` (reused worktree) | `/port:explore` and `/port:plan` | Always `regenerate`. |
| **G5** Locate confidence | `/port:explore` | high → proceed. medium → proceed flagged. low → **abort**, post Linear, STOP wrapper. |
| **G6** Agent failure | `/port:explore` and `/port:plan` | retry once; second failure → **abort** wrapper. |
| **G7** Pre-review clarification | `/port:revise` | Auto-accept all assumptions; record in `claude-reports/<session>/auto-accepted.md`. |
| **G8** Final approval | `/port:revise` | Auto-approve; write `Review approved` sentinel. |
| **G9** Revision loop | `/port:revise` | Skip entirely (no interactive revision in auto). |

### Wrapper-level abort handling

When the wrapper aborts:

1. Append a JSONL line to `<worktree>/.port/timings.jsonl` (D22) with stage `ff` and `outcome: aborted-<reason>`.
2. Atomic-write `claude-reports/<session>/ff-aborted.md` with: ticket id, stage at which abort happened, reason, and pointer to the stage's own report (e.g. `/port:revise`'s `auto-accepted.md`).
3. Linear comment: stages that abort already post their own Linear comment (locate-low, push-fail). The wrapper does not duplicate.
4. **Do NOT run step 11's cleanup.** `timings.jsonl` is the audit trail; on abort it is the user's only visibility into how far the chain got. Gitignore protects against accidental commit; preservation is intentional for debug.
5. STOP — do not proceed to later stages.

---

## Guardrails

- `--ticket:<ID>` is required. The wrapper never infers from cwd because it is the entry point.
- `--simple` and `--auto` are mutually exclusive; `--simple` wins (one-stage run, no autonomy needed).
- Each stage runs in sequence; the wrapper does NOT run any stage in parallel. Parallel agent dispatch happens *inside* `/port:plan` only.
- Stage internal HITL gates remain in HITL mode — the wrapper never strips `AskUserQuestion`; it only chooses whether to forward `--auto` so the stage itself collapses them.
- The wrapper preserves the underlying stages' "worktree always preserved on failure" invariant. Lock release / cleanup happens inside `/port:ship` only on full success.
- Auto-decision table G1–G9 lives inside the individual stages. The wrapper only ensures the same flag (`--auto`) is propagated, so the table is applied consistently.
- No new file formats. The wrapper writes only timings JSONL and the optional `ff-aborted.md` report — both already mandated by the plan.
- Bypass-permissions is set per-stage by the underlying commands (when they invoke sub-agents), not by the wrapper. The wrapper itself uses standard slash invocation.
- After a successful run, the cwd is the new worktree, NOT the main repo. The user must `cd` back manually if they want to start another port — same behavior as the underlying `/port:start` already establishes.
