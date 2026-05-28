---
name: ggx-work
description: >
  Single-ticket orchestrator. Drives one ticket (Linear or Jira) through
  every pipeline it needs (port → spec-review → dev, or just dev, or bug)
  by repeatedly calling `/route --non-interactive` (in --auto mode)
  for decisions and executing the recommended command. Stops cleanly
  at HITL gates (spec-review via Step 4.4a short-circuit — Linear only;
  missing classification via Step 4.3) so a human can take over, then
  can be re-invoked to resume. Used by `/ggx-dispatcher` as the
  uniform spawn target so the cross-pipeline routing logic lives in
  ONE place (`/route`) instead of being re-implemented in every caller.
  Ticket-system support: Linear (CAF/DAF) and Jira (CET/DET) via the
  abstraction documented in `_ticket-lib.md`. Jira tickets have no
  port lane, so the port→spec-review handoff path is Linear-exclusive.
Prerequisite: >
  - Linear MCP authenticated for CAF/DAF tickets; Atlassian Rovo MCP
    authenticated for CET/DET tickets.
  - `<ticket-id>` references a real ticket with a derivable lane:
      - Linear: exactly one classification label ∈ {`bug`,`port`,`feature`}.
      - Jira: `fields.issuetype.name` ∈ {`Bug`,`Story`,`Task`,`Sub-task`,
        `Improvement`,`New Feature`}.
    Anything else halts at the first `/route` call.
  - For ticket worktree-required stages: a worktree at `../<ticket-id>`
    is created on first `/port:ff` / `/dev:ff` invocation by those
    pipelines' own `:start` stages.
---

# `/ggx-work <ticket-id> [--auto] [--no-ticket-init]`

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
- `/ggx-work <ticket-id> --no-ticket-init` — skip Step 2.5's Linear lifecycle
  init AND pass `--no-ticket-init` through to every spawned `/port:ff` /
  `/dev:ff` / `/bug:ff` so the downstream `:start` stage also short-circuits.
  Use when running the orchestrator locally for inspection / debugging
  without flipping the ticket on Linear. Combinable with `--auto`. Does NOT
  affect `/ggx-dispatcher` (the dispatcher always inits regardless).

Notes:

- `<ticket-id>` — Linear ticket ID (e.g. `CAF-370`). **Required** — unlike
  `/route`, `/ggx-work` does not infer from cwd because the orchestration
  span outlives any single worktree.
- `--auto` propagates: each spawned `/port:ff` / `/dev:ff` is invoked with
  its own `--auto` flag.
- `--no-ticket-init` propagates the same way — passed verbatim into every
  spawned FF wrapper so the chain (`/ggx-work` → `/<port|dev>:ff` →
  `/<port|dev>:start`) honors the opt-out uniformly.

---

## Steps

### Step 1: Parse arguments

1. Extract `<ticket-id>` from `$ARGUMENTS`. Trim, uppercase.
2. Detect `--auto` flag → `<auto-mode> = True/False`.
3. Detect `--no-ticket-init` flag → `<no-ticket-init> = True/False`. When True,
   Step 2.5 short-circuits and `--no-ticket-init` is propagated verbatim into
   every spawned `/port:ff` / `/dev:ff` / `/bug:ff` invocation in Step 3.
4. Missing `<ticket-id>`:
   - `<auto-mode> == True` → STOP with `/ggx-work requires <ticket-id> in --auto mode.`
   - `<auto-mode> == False` → `AskUserQuestion`:
     > "What Linear ticket should `/ggx-work` orchestrate?" — abort if empty.

### Step 2: Pre-flight

1. **Resolve `<ticket-system>`** via the `_ticket-lib.md` resolution flow
   (reads `.gogox-claude.yaml` and `org.yaml`). If `unknown` → STOP with:
   `/ggx-work cannot resolve ticket_system for <ticket-id>. Check .gogox-claude.yaml + org.yaml prefixes.`
2. Verify ticket-tracker MCP reachability via a lightweight fetch:
   - **Linear**: `mcp__claude_ai_Linear__get_issue --id <ticket-id>`
   - **Jira**: `mcp__claude_ai_Atlassian_Rovo__getJiraIssue --cloudId <jira-cloud-id> --issueIdOrKey <ticket-id> --responseContentFormat markdown`

   Failure (network, not found, permission) → STOP with the verbatim error
   plus the hint `Verify the ticket id and that the matching MCP server
   (Linear or Atlassian Rovo) is authenticated.`
3. Hold from the response (mapped to logical names — see `_ticket-lib.md`
   "Field mapping"): `url`, `labels` (Linear) or `issue_type` (Jira),
   `status_name`, `assignee_id`. The `url` is included in any ticket
   comment posted later; the rest drive Step 2.5's idempotency guard.

### Step 2.5: Linear lifecycle init (idempotent, both modes)

<!-- SYNC: ticket-init lives in commands/dev/_ticket-init.md. The 4 callers
     (port:start Step 5a, dev:start Step 3c, ggx-dispatcher Step 4.1,
     ggx-work Step 2.5) all invoke it; do not re-inline the block here. -->

Owns the ticket-lifecycle moves that `/ggx-dispatcher` §4.1 performs for the
auto path. Runs in HITL too — the manual orchestrator path otherwise leaves the
ticket frozen at its pre-pipeline state (status stays `To-do`, `ready-to-*`
lingers), which silently breaks downstream batch tooling (`/spec-review` no-args
won't find the ticket; reporting that filters on `In Progress` will miss it).

**Opt-out**: if `<no-ticket-init> == True` (from Step 1), log a single line
`ticket-init: skipped (--no-ticket-init)` and skip the rest of Step 2.5 entirely.
The flag is still propagated to spawned FF wrappers in Step 3 so the downstream
`:start` stage's own ticket-init invocation also short-circuits — keeping the
whole chain consistent for the manual / debugging workflow.

1. **Derive `<lane>` from the classification** held by Step 2 — system-aware:
   - **Linear**: exactly one of `{bug, port, feature}` ∈ `<labels>` → that one.
     Zero or multiple → **skip the entire Step 2.5**. Step 3's `/route`
     call will surface the missing-classification error via its own
     UNKNOWN_LANE path; duplicating the check here would just produce a
     second error message. Case-insensitive match (Linear sometimes
     returns `Port` with capital P).
   - **Jira**: derive from `<issue-type>` per `_ticket-lib.md` lane table —
     `Bug` → `bug`; `Story` / `Task` / `Sub-task` / `Subtask` /
     `Improvement` / `New Feature` → `feature`; anything else → skip
     Step 2.5 (let `/route` surface UNKNOWN_LANE).
     Case-insensitive match.

2. **Map `<lane>` to the `/_ticket-init` lane argument**:
   - `port` → `port`
   - `bug` / `feature` → `dev` (both flow through the `/dev:ff` pipeline)

3. Invoke `/_ticket-init <ticket-id> <lane-arg>` (idempotent; safe to re-call). The skill drives status → `In Progress`, drops `ready-to-<lane-arg>`, sets assignee to self, sets estimate=1 if null, and posts a `<!-- ticket-init:v1 lane=<lane-arg> -->` starting comment if absent. Every write is short-circuited by a per-field skip condition, so dispatcher-spawned runs (where §4.1 already invoked the same skill) and re-entries (after a crash) collapse to no-ops naturally — no separate idempotency guard needed here.

   `/_ticket-init` NEVER writes `dispatcher-*-in-flight` (exclusively the
   dispatcher's resume signal per the Plan X guardrail
   `ggx-dispatcher.md` Guardrails: "Adding the label outside the dispatcher
   would silently make manual runs dispatcher-recoverable, breaking the
   user's expectation of 'if I stopped, it stays stopped.'"). Step 2.5 does
   not pass any flag that would change that.

4. **Audit line** to stdout (in addition to `/_ticket-init`'s own audit line):

   ```
   Lifecycle init: <ticket-id> via /_ticket-init lane=<lane-arg> (classification=<lane>).
   ```

5. **MCP failure handling.** Delegated to `/_ticket-init` — per-write failures
   are logged as WARNs by the skill and the pipeline continues. The only
   hard-stop inside `/_ticket-init` is a `get_issue` failure (cannot evaluate
   skip conditions), which bubbles up to here as a non-zero exit; in that case
   `/ggx-work` continues (the pipeline below still works without lifecycle
   init; subsequent invocations retry).

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

> **Cosmetic exit-line contract** (read by `/ggx-dispatcher` §6.1).
> Every terminal point below ends its stdout with a single line of the form
> `[ggx-work-result] outcome=<done|port-paused|failed> ticket=<ticket-id>`
> immediately before `exit`. This line is **cosmetic** — the dispatcher uses
> it only for the live `[joined/N]` progress line in §6.1. Authoritative
> classification in §6.2 and §6.4 reads filesystem markers + Linear labels
> + PR state, not this line. Treat the line as best-effort: if a future
> refactor drops it the dispatcher must not break.

#### Step 4.1: Terminal

Ticket is done. Print:

```
Ticket <ticket-id>: done.
Iterations: <iter>
Final phase: <phase>
[ggx-work-result] outcome=done ticket=<ticket-id>
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
[ggx-work-result] outcome=port-paused ticket=<ticket-id>
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
[ggx-work-result] outcome=port-paused ticket=<ticket-id>
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
[ggx-work-result] outcome=failed ticket=<ticket-id>
```

Exit non-zero.

**Auto mode**:

Post a ticket comment (system-aware — Step 2's `<ticket-system>` value):

- **Linear**: `mcp__claude_ai_Linear__save_comment --issueId <ticket-id> --body <markdown>`
- **Jira**: `mcp__claude_ai_Atlassian_Rovo__addCommentToJiraIssue --cloudId <jira-cloud-id> --issueIdOrKey <ticket-id> --commentBody <markdown>`

Body:
```
<!-- ggx-work-error -->
`/ggx-work --auto` aborted.

Reason : <reason>
Iter   : <iter>
Last   : `<recommended_command (if any)>`

Manual investigation needed.
```

Print to stdout (in addition to the ticket comment above), so the
dispatcher's §6.1 cosmetic parse picks up the outcome line from the
agent's return message:

```
[ggx-work-result] outcome=failed ticket=<ticket-id>
```

Exit non-zero.

#### Step 4.4: Pipeline (recommended_command is `/port:ff`, `/dev:ff`, `/bug:ff`)

Build the command to execute:

```
<spawn-cmd> = <recommended_command>
if <auto-mode>:
    <spawn-cmd> += " --auto"
if <no-ticket-init>:
    <spawn-cmd> += " --no-ticket-init"
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

##### Step 4.4a: port → spec-review short-circuit (Linear only)

Triggered only when Step 4.4 just finished a successful `/port:ff`
invocation. **Skip entirely if `<ticket-system> == jira`** — Jira repos
have no port pipeline (rejected at `/route` Step 4.port) so this branch
is structurally unreachable for Jira. Purpose: terminate the loop
without re-invoking `/route` or posting a duplicate HITL comment.

```
re-fetch ticket labels via mcp__claude_ai_Linear__get_issue <ticket-id>
if "need-spec-review" ∈ labels:
    # Canonical auto path: /port:ship --auto step 13 added the label and
    # already posted its own user-facing comment.
    print:
      Ticket <ticket-id>: port complete, paused for human spec review.
      /port:ship has notified Linear. Re-invoke /ggx-work <ticket-id>
      after the human runs /spec-review and flips the label to
      ready-to-dev.
      [ggx-work-result] outcome=port-paused ticket=<ticket-id>
    exit 0   (terminal — do NOT continue the loop)
else:
    # Canonical HITL path: /port:ship HITL intentionally skips the
    # need-spec-review add per its own "auto-only" guardrail
    # (commands/dev/port/ship.md step 13: "HITL: skip the
    # need-spec-review add (reviewer applies it manually)"). When
    # /ggx-work is run interactively as the lifecycle owner, that
    # leaves the handoff in a broken state — /spec-review's batch path
    # filters on this label and so won't find the ticket. Falling
    # through to /route would also be wrong: with the port-ship marker
    # committed and need-spec-review absent, /route would recommend
    # /dev:ff and silently skip the human review gate.
    #
    # Fix: add the label here ourselves, then exit the loop with the
    # same pause message the if-branch uses. The dispatcher path
    # never lands here (it goes through the if-branch above), so this
    # write is exclusive to the HITL orchestrator path.
    save_issue(<ticket-id>, labels = labels ∪ {"need-spec-review"})
        — retry up to 3× with backoff 1s, 4s, 16s on failure
        — final failure → soft warning to stdout, do NOT exit non-zero
          (the spec is shipped; user can flip the label manually)
    print:
      Ticket <ticket-id>: port complete, paused for human spec review.
      Added `need-spec-review` label so /spec-review (batch or single
      mode) will pick this ticket up. Re-invoke /ggx-work <ticket-id>
      after running /spec-review.
      [ggx-work-result] outcome=port-paused ticket=<ticket-id>
    exit 0   (terminal — do NOT continue the loop)
```

**Why a fresh `get_issue` call rather than trusting cached labels**:
`/ggx-work` last saw the ticket at Step 2 pre-flight, before `/port:ship`
ran. The `need-spec-review` label is added inside `/port:ship`'s ship
step (auto only — see Edit-A2 rationale in the `else:` block for the
HITL fallback this short-circuit pairs with), so the cached copy is
stale by design. One MCP call here is cheap; the alternative — letting
the loop continue, re-calling `/route`, and posting a Step 4.2 comment
— is the noise this short-circuit exists to eliminate.

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
- **Linear writes are scoped by purpose, not by mode.** Both interactive
  and `--auto` perform Step 2.5 lifecycle init (status / assignee /
  estimate / labels / starting comment) and the Step 4.4a HITL fallback
  label add — these are necessary for the orchestrator's job and are
  idempotent. Step 4.3 generic-error Linear comments still fire in
  `--auto` only (interactive prints to stdout for the human user).
  Never write `dispatcher-*-in-flight`; that label is exclusively
  dispatcher's resume signal.
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
boundary" section for the canonical statement). `/ggx-work` has limited
write authority — it is NOT label-agnostic:

- **Workflow labels** (`ready-to-port`, `ready-to-dev`,
  `dispatcher-*-in-flight`, `need-spec-review`) are owned by the
  dispatcher + `/port:ship` + `/dev:ship` + `/ggx-work`. `/ggx-work`'s
  writes are scoped:
  - **Step 2.5 lifecycle init** — removes `ready-to-port` /
    `ready-to-dev` after derivable lane is confirmed. Idempotent with
    `/ggx-dispatcher` §4.1 and `/port:start` / `/dev:start` auto-mode
    item 4 (whichever ran first wins; later runs short-circuit on the
    guard). Never writes `dispatcher-*-in-flight` — that remains
    dispatcher-exclusive per Plan X.
  - **Step 4.4a HITL fallback** — adds `need-spec-review` after a
    successful `/port:ff` if absent. Compensates for `/port:ship`'s
    HITL skip so the spec-review handoff is discoverable by
    `/spec-review`'s batch path. Never fires when `/port:ship --auto`
    already added the label (the if-branch wins).
- **Classification labels** (`bug`, `port`, `feature`) are owned by
  humans and read only by `/route` (and now read by `/ggx-work` Step
  2.5 to derive lane — read-only). `/ggx-work` never writes them.

What `/ggx-work` contributes on top of plain ff spawn:

- Owns the Linear lifecycle in HITL mode via Step 2.5 — status,
  assignee, estimate, starting comment, actionable-label cleanup.
  Auto-path users see this as idempotent no-ops because
  `/ggx-dispatcher` §4.1 has already done the equivalent writes.
- Handles the port → spec-review handoff cleanly via Step 4.4a in both
  modes (no duplicate Linear comment, label correctly present on the
  ticket so downstream batch tooling finds it).
- Translates `/route`'s `Status: UNKNOWN_LANE` structured failure into
  a Linear comment via Step 4.3 (auto mode).
- Same spawn shape for every lane, so dispatcher §5 has no
  lane-specific branching.
