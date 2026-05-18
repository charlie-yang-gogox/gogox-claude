---
name: ggx-work
description: >
  Single-ticket orchestrator. Drives one Linear ticket through every
  pipeline it needs (port → spec-review → dev, or just dev, or bug)
  by repeatedly calling `/route` for decisions and executing the
  recommended command. Stops cleanly at HITL gates (spec-review,
  missing classification label) so a human can take over, then can
  be re-invoked to resume. Designed to replace direct `/port:ff` /
  `/dev:ff` invocation in `/ggx-dispatcher` so the cross-pipeline
  routing logic lives in ONE place (`/route`) instead of being
  re-implemented in every caller.
Prerequisite: >
  - Linear MCP authenticated.
  - `<ticket-id>` references a real Linear ticket with a single
    classification label (`bug` / `port` / `feature`); missing /
    ambiguous classification halts at the first `/route` call.
  - For ticket worktree-required stages: a worktree at `../<ticket-id>`
    is created on first `/port:ff` / `/dev:ff` invocation by those
    pipelines' own `:start` stages.
---

# `/ggx-work <ticket-id> [--auto]`

> `/ggx-work` is a **single-ticket orchestrator**. It loops:
>
> 1. Call `/route` to decide the next pipeline command.
> 2. Execute that command (or exit cleanly if it's a HITL gate or terminal).
> 3. Repeat.
>
> **It does NOT contain routing logic** — that lives in `/route`. `/ggx-work`
> is a thin loop driver. If routing seems wrong, fix `/route`, not here.
>
> **It does NOT retry failed pipelines.** A non-zero exit from `/port:ff` /
> `/dev:ff` / `/bug:ff` terminates the loop immediately. The user fixes the
> root cause and re-invokes `/ggx-work` (re-entry is idempotent — `/route`
> re-derives the next step from current state).
>
> **It does NOT write state files.** Cross-invocation resume is entirely
> derived by `/route` reading Linear + worktree filesystem.

**Usage**:

- `/ggx-work <ticket-id>` — **interactive mode**. HITL gates stop with a
  human-readable hint. `AskUserQuestion` may fire via `/route` if the
  classification label is missing.
- `/ggx-work <ticket-id> --auto` — **unattended mode**. HITL gates post a
  Linear comment and exit cleanly. No prompts. Suitable for `/ggx-dispatcher`
  spawn paths.

Notes:

- `<ticket-id>` — Linear ticket ID (e.g. `CAF-370`). **Required** — unlike
  `/route`, `/ggx-work` does not infer from cwd because the orchestration
  span outlives any single worktree.
- `--auto` propagates: each spawned `/port:ff` / `/dev:ff` is invoked with
  its own `--auto` flag.

---

## Steps

### Step 1: Parse arguments

1. Extract `<ticket-id>` from `$ARGUMENTS`. Trim, uppercase.
2. Detect `--auto` flag → `<auto-mode> = True/False`.
3. Missing `<ticket-id>`:
   - `<auto-mode> == True` → STOP with `/ggx-work requires <ticket-id> in --auto mode.`
   - `<auto-mode> == False` → `AskUserQuestion`:
     > "What Linear ticket should `/ggx-work` orchestrate?" — abort if empty.

### Step 2: Pre-flight

1. Verify Linear MCP reachability via a lightweight `mcp__claude_ai_Linear__get_issue`
   call for `<ticket-id>`. Failure (network, not found, permission) → STOP with
   the verbatim error.
2. Hold the ticket's `url` so we can include it in any Linear comment posted later.

### Step 3: Decision loop

Initialize `<iter> = 0`. Maximum `<iter-cap> = 5` (sanity check — see Step 5).

Loop:

#### Step 3.1: Increment + cap check

```
<iter> += 1
if <iter> > <iter-cap>:
    → Step 5 (loop cap fired)
```

#### Step 3.2: Call `/route`

Invoke `/route <ticket-id>` inline. Parse its output for:

- `recommended_command` — the one-line command from the `Recommendation:` block
- `phase` — from the `Phase:` line
- `lane` — from the `Lane:` line (for logging)

If `/route` itself failed (exit non-zero, malformed output) → STOP per the
`<auto-mode>` rules in Step 4.3 with reason `route-call-failed`.

#### Step 3.3: Classify `recommended_command` → branch

| `recommended_command` pattern         | Branch                  |
|---------------------------------------|-------------------------|
| starts with `(none`                   | **Terminal** (Step 4.1) |
| matches `^/spec-review `              | **HITL** (Step 4.2)     |
| matches `^/port:ff `                  | **Pipeline** (Step 4.4) |
| matches `^/dev:ff `                   | **Pipeline** (Step 4.4) |
| matches `^/bug:ff `                   | **Pipeline** (Step 4.4) |
| anything else                         | **Unknown** — Step 4.3 with reason `unrecognized-recommendation: <cmd>` |

---

### Step 4: Per-branch behavior

#### Step 4.1: Terminal

Ticket is done. Print:

```
Ticket <ticket-id>: done.
Iterations: <iter>
Final phase: <phase>
```

Exit 0.

#### Step 4.2: HITL (recommended_command is `/spec-review …`)

A human action is required. Both modes exit 0 (no error — this is a
designed pause point), differing only in messaging:

**Interactive mode (`<auto-mode> == False`)**:

Print:
```
Ticket <ticket-id>: paused at HITL gate.
Phase     : <phase>
Next step : <recommended_command>

Run that command yourself. When it completes, re-invoke /ggx-work <ticket-id>
to continue.
```

Exit 0.

**Auto mode (`<auto-mode> == True`)**:

Post a single Linear comment via `mcp__claude_ai_Linear__save_comment`:

```
<!-- ggx-work-hitl -->
`/ggx-work --auto` paused at HITL gate.

Phase   : <phase>
Next    : `<recommended_command>`

This ticket needs a human to run `/spec-review <ticket-id>` (or equivalent)
before the dev pipeline can proceed.
```

Idempotency: before posting, list existing comments and skip if any body
starts with `<!-- ggx-work-hitl -->` for this ticket and the recommended
command matches — prevents duplicate comments on re-runs.

Exit 0.

#### Step 4.3: Generic error exit

For: `/route` call failed, unrecognized recommendation, or any internal
guard tripping.

**Interactive mode**:

Print:
```
/ggx-work: aborting.
Reason: <reason>
Iter  : <iter>
Last  : <recommended_command (if any)>
```

Exit non-zero.

**Auto mode**:

Post Linear comment:
```
<!-- ggx-work-error -->
`/ggx-work --auto` aborted.

Reason : <reason>
Iter   : <iter>
Last   : `<recommended_command (if any)>`

Manual investigation needed.
```

Exit non-zero.

#### Step 4.4: Pipeline (recommended_command is `/port:ff`, `/dev:ff`, `/bug:ff`)

Build the command to execute:

```
<spawn-cmd> = <recommended_command>
if <auto-mode>:
    <spawn-cmd> += " --auto"
```

Execute `<spawn-cmd>` inline (LLM continues the current session, walking
the slash command's pseudocode just like `/dev:ff` and `/port:ff` do for
their own stages).

Print before spawn:
```
[iter <iter>] running: <spawn-cmd>
  lane=<lane> phase=<phase>
```

When the spawned pipeline terminates:

- **Success** (the FF wrapper reported `done` and exited cleanly, OR
  reported a designed pause like `Status: BLOCKED` for `need-spec-review`
  handoff that `/port:ship` writes) → continue loop (go to Step 3.1).

- **Failure** (FF wrapper exited non-zero, raised an error, hit its own
  abort path, or a stage marker file shows `Status: FAILED` / `ABORTED`) →
  jump to Step 4.3 with `reason = pipeline-failed: <spawn-cmd>` and include
  the last 20 lines of pipeline stderr / the failure marker file path in
  the abort output.

  **Do NOT re-spawn the failed pipeline.** Do NOT post-fix. The user
  investigates, fixes the root cause, and re-invokes `/ggx-work`.

---

### Step 5: Loop cap fired

Reached only if `<iter>` exceeds 5 (Step 3.1).

The normal worst case is 2 iterations (route → pipeline → route → terminal).
3+ iterations imply route is recommending the same pipeline repeatedly
without progress, or the loop is otherwise pathological.

Jump to Step 4.3 with `reason = loop-cap-exceeded (<iter-cap>): last recommendation <recommended_command>`.

---

## Worked examples

### Feature ticket, interactive

```
/ggx-work CAF-512
  iter 1: /route → /dev:ff CAF-512
          running: /dev:ff CAF-512
          ...dev pipeline runs to PR...
  iter 2: /route → (none — /dev:ff terminates at /dev:ship)
          Ticket CAF-512: done. Iterations: 2.
```

### Port ticket, --auto, first dispatcher round

```
/ggx-work CAF-370 --auto
  iter 1: /route → /port:ff CAF-370
          running: /port:ff CAF-370 --auto
          ...port pipeline runs, ships, adds need-spec-review...
  iter 2: /route → /spec-review CAF-370
          (HITL gate, --auto)
          posted Linear comment <!-- ggx-work-hitl -->
          exit 0
```

### Port ticket, --auto, post-spec-review round

```
/ggx-work CAF-370 --auto
  (human already ran /spec-review since last invocation; ready-to-dev set)
  iter 1: /route → /dev:ff CAF-370
          running: /dev:ff CAF-370 --auto
          ...dev pipeline runs to PR, In Review...
  iter 2: /route → (none — /dev:ff terminates at /dev:ship)
          Ticket CAF-370: done. Iterations: 2.
```

### Failed dev pipeline, interactive

```
/ggx-work CAF-512
  iter 1: /route → /dev:ff CAF-512
          running: /dev:ff CAF-512
          ...dev:apply fails: test failures in src/foo_test.go...
          /dev:ff exits non-zero
  /ggx-work: aborting.
  Reason: pipeline-failed: /dev:ff CAF-512
  Iter  : 1
  Last  : /dev:ff CAF-512
  (last 20 lines of stderr printed)

  exit non-zero
```

User fixes the failing test → re-invokes `/ggx-work CAF-512` → loop
counter resets to 0, `/route` re-derives stage, `/dev:ff` resumes from
where it left off via `infer_dev_stage`.

---

## Guardrails

- **One source of truth for routing.** All "which pipeline next?" logic
  lives in `/route`. `/ggx-work` MUST NOT parse Linear labels, inspect
  worktree state, or otherwise re-derive routing — call `/route` and
  trust it.
- **No retry on failure.** Any sub-pipeline failure terminates the loop.
  The user is the retry mechanism (by re-invoking `/ggx-work`).
- **No state file.** `/ggx-work` does not write `.ggx-work/state.json` or
  equivalent. Re-entry is idempotent via `/route`'s fresh derivation.
- **Loop cap is a sanity check, not a control flow.** Normal runs hit
  `<iter> <= 2`. Cap firing means a bug in `/route` or a pathological
  pipeline — surface, don't paper over.
- **`--auto` is sticky downward.** When `--auto` is set, every spawned
  `/port:ff` / `/dev:ff` / `/bug:ff` gets its own `--auto` flag. Mixed
  modes (parent auto, child interactive) are not supported.
- **Linear writes only in `--auto`.** Interactive mode never posts Linear
  comments — keeps the ticket history clean for manual usage.
- **HITL exits with code 0.** Pausing at `/spec-review` is a designed
  pause point, not an error. Only `/route` failures and pipeline failures
  exit non-zero.

---

## Relationship to `/ggx-dispatcher`

Today `/ggx-dispatcher` spawns `/port:ff --auto` or `/dev:ff --auto`
per ticket, having pre-computed the lane via its `fresh-port` /
`fresh-dev` selection (see `ggx-dispatcher.md` §2.1, §5.1, §5.2).

Once `/ggx-work --auto` is in place, dispatcher can migrate to:

```diff
- spawn /port:ff --ticket:<id> --auto
- spawn /dev:ff <id> --auto [--no-figma]
+ spawn /ggx-work <id> --auto
```

Benefits:
- Lane selection collapses into `/route` (single source of truth)
- Dispatcher loses lane-specific spawn branches
- Recovery (Q2/Q4 `dispatcher-*-in-flight` lanes) no longer needs to
  encode "which pipeline was in flight" — `/route` re-derives from
  worktree state on re-entry

The migration is out of scope for this command's initial landing; the
contract above (`/ggx-work --auto` ≡ dispatcher's per-pipeline spawn)
exists to make the dispatcher migration a mechanical change later.
