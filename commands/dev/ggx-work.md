---
name: ggx-work
description: >
  Single-ticket orchestrator. Drives one Linear ticket through every
  pipeline it needs (port → spec-review → dev, or just dev, or bug)
  by repeatedly calling `/route --non-interactive` (in --auto mode)
  for decisions and executing the recommended command. Stops cleanly
  at HITL gates (spec-review via Step 4.4a short-circuit; missing
  classification label via Step 4.3) so a human can take over, then
  can be re-invoked to resume. Used by `/ggx-dispatcher` as the
  uniform spawn target so the cross-pipeline routing logic lives in
  ONE place (`/route`) instead of being re-implemented in every caller.
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

Build the invocation:

```
<route-cmd> = "/route <ticket-id>"
if <auto-mode>:
    <route-cmd> += " --non-interactive"
```

`--non-interactive` is sticky to `--auto`: every spawned subagent path
runs in a context where `AskUserQuestion` cannot be answered (no human
attached), so `/route` must surface gates as structured errors instead
of prompts. Interactive mode does not pass the flag — `/route`'s own
AskUserQuestion path is the user-facing recovery there.

Invoke `<route-cmd>` inline. Parse its output for:

- `recommended_command` — the one-line command from the `Recommendation:` block
- `phase` — from the `Phase:` line
- `lane` — from the `Lane:` line (for logging)

`/route` failure dispatch:

- Exit non-zero with `Status: UNKNOWN_LANE` on stdout → STOP per the
  `<auto-mode>` rules in Step 4.3 with
  `reason = unknown-lane: missing classification label`. Auto mode posts
  the standard ggx-work-error Linear comment so a human can attach the
  right classification label and re-invoke.
- Exit non-zero with `Status: MISSING_TICKET_ID` → should not happen
  (`/ggx-work` always passes an explicit ticket id), but if it does →
  STOP via Step 4.3 with `reason = route-internal: missing-ticket-id`.
- Any other failure (exit non-zero with no recognized `Status:` line, or
  malformed output) → STOP via Step 4.3 with `reason = route-call-failed`.

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
designed pause point), differing only in messaging.

This branch is normally **unreachable** when the loop just finished
running `/port:ff`, because Step 4.4's post-pipeline short-circuit
catches the `need-spec-review` label and terminates before `/route`
is called a second time. Step 4.2 still exists for the residual case
where `/ggx-work` is invoked directly against a ticket that already
sits at `need-spec-review` (e.g. someone re-runs `/ggx-work` after
human spec-review was forgotten); in that case there is no upstream
`/port:ship` comment to lean on, so the messaging below still fires.

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

Silent exit. Do NOT post a Linear comment. The Step 4.4 short-circuit
already covers the common case (port pipeline just shipped and dropped
the `need-spec-review` label, with `/port:ship` having posted its own
human-facing comment). Auto-mode re-entries that bypass Step 4.4
(direct `/ggx-work <id> --auto` against a ticket already at
`need-spec-review`) should be invisible — the Linear state already says
what the human needs to know; double-posting from `/ggx-work` is noise.

Print one line to stdout for the dispatcher's audit trail:

```
Ticket <ticket-id>: already at need-spec-review (HITL). No comment posted.
```

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
  handoff that `/port:ship` writes):
    1. If the spawned pipeline was `/port:ff`, run the **port → spec-review
       short-circuit** (Step 4.4a) before looping. This catches the
       canonical port-handoff state — `/port:ship` has added the
       `need-spec-review` label and posted its own user-facing comment —
       and exits the loop cleanly without a second `/route` call.
    2. Otherwise, continue loop (go to Step 3.1).

- **Failure** (FF wrapper exited non-zero, raised an error, hit its own
  abort path, or a stage marker file shows `Status: FAILED` / `ABORTED`) →
  jump to Step 4.3 with `reason = pipeline-failed: <spawn-cmd>` and include
  the last 20 lines of pipeline stderr / the failure marker file path in
  the abort output.

  **Do NOT re-spawn the failed pipeline.** Do NOT post-fix. The user
  investigates, fixes the root cause, and re-invokes `/ggx-work`.

##### Step 4.4a: port → spec-review short-circuit

Triggered only when Step 4.4 just finished a successful `/port:ff`
invocation. Purpose: terminate the loop without re-invoking `/route` or
posting a duplicate HITL comment.

```
re-fetch ticket labels via mcp__claude_ai_Linear__get_issue <ticket-id>
if "need-spec-review" ∈ labels:
    print:
      Ticket <ticket-id>: port complete, paused for human spec review.
      /port:ship has notified Linear. Re-invoke /ggx-work <ticket-id>
      after the human runs /spec-review and flips the label to
      ready-to-dev.
    exit 0   (terminal — do NOT continue the loop)
else:
    # Unexpected: /port:ff terminated cleanly but did not leave
    # need-spec-review on the ticket. Don't paper over — fall through
    # to the normal continue-loop path and let /route re-derive.
    continue loop (go to Step 3.1)
```

**Why a fresh `get_issue` call rather than trusting cached labels**:
`/ggx-work` last saw the ticket at Step 2 pre-flight, before `/port:ship`
ran. The `need-spec-review` label is added inside `/port:ship`'s ship
step, so the cached copy is stale by design. One MCP call here is cheap;
the alternative — letting the loop continue, re-calling `/route`, and
posting a Step 4.2 comment — is the noise this short-circuit exists to
eliminate.

**Why this lives in Step 4.4, not as a Step 3.3 routing case**:
`/route` is read-only and lane-agnostic; making it post-process
`/port:ff`'s side effects would couple it to a specific pipeline. The
short-circuit belongs to `/ggx-work`'s pipeline-result interpretation,
which Step 4.4 already owns.

**Why this does NOT fire for `/dev:ff` or `/bug:ff`**: dev and bug
pipelines terminate at PR-open. Their "done" is unambiguous — the next
`/route` call returns `(none)` and the loop terminates via Step 4.1.
Only port has a mid-pipeline handoff that requires a human gate.

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

`/ggx-dispatcher` now spawns `/ggx-work <id> --auto` for every locked
ticket regardless of lane (see `ggx-dispatcher.md` §5.1). Inside each
spawned subagent, `/ggx-work` calls `/route --non-interactive` to pick
`/port:ff` / `/dev:ff` / `/bug:ff` from the classification label plus
worktree filesystem state.

Label ownership is split (see `ggx-dispatcher.md`'s "Label ownership
boundary" section for the canonical statement):

- **Workflow labels** (`ready-to-port`, `ready-to-dev`,
  `dispatcher-*-in-flight`, `need-spec-review`) stay owned by the
  dispatcher + `/port:ship` + `/dev:ship`. `/ggx-work` reads
  `need-spec-review` exactly once (Step 4.4a short-circuit after a
  `/port:ff` success) and never writes any of them.
- **Classification labels** (`bug`, `port`, `feature`) are owned by
  humans and read only by `/route`.

What `/ggx-work` contributes on top of plain ff spawn:

- Handles the port → spec-review HITL handoff cleanly via Step 4.4a
  (no duplicate Linear comment).
- Translates `/route`'s `Status: UNKNOWN_LANE` structured failure into
  a Linear comment via Step 4.3 (auto mode).
- Same spawn shape for every lane, so dispatcher §5 has no
  lane-specific branching.
