---
name: route
description: >
  Atomic decision command. Reads a Linear ticket's classification label
  (`bug` / `port` / `feature`) plus minimal worktree state and recommends
  the ONE pipeline entry-point command to run next (`/port:ff`, `/dev:ff`,
  `/bug:ff`, or `/spec-review`). Advisory only by default — prints the
  recommendation for the user to copy-paste. Does NOT execute the command,
  does NOT mutate Linear labels, does NOT detect resume points inside a
  pipeline (the FF wrappers do that themselves via `infer_*_stage`). The
  `/ggx-work` orchestrator (and `/ggx-dispatcher` via spawned `/ggx-work`)
  call `/route --non-interactive` to drive multi-stage flows; in that mode
  prompts are converted to structured `Status:` errors with non-zero exit.
Prerequisite: >
  - Linear MCP authenticated.
  - For port-classified tickets: a worktree at `../<ticket-id>` is helpful
    but not required (absent worktree → recommend /port:ff).
---

# `/route [ticket-id]`

> `/route` is an **atomic decision command**. It answers ONE question:
> **which pipeline entry-point should I run for this ticket?**
>
> - It does NOT detect resume points within a pipeline — `/port:ff` and
>   `/dev:ff` derive their own resume via `infer_port_stage` /
>   `infer_dev_stage`.
> - It does NOT execute the recommended command — it prints it for the user
>   to copy.
> - It does NOT mutate Linear labels — only reads.
> - It does NOT read `dispatcher-*-in-flight` labels — this skill is for the
>   manual workflow only. If you are running `/ggx-dispatcher`, route through
>   that instead.
>
> **Composable design.** `/route` is intended to be the decision core for a
> future `/ggx-work` orchestrator that calls `/route`, runs the
> recommendation, waits for completion (or a human gate), then calls `/route`
> again until the ticket reaches a terminal state. Today only the atomic
> decision step is implemented; users invoke the recommended command
> manually.

**Usage**: `/route [ticket-id] [--non-interactive]`

- `<ticket-id>` — Linear ticket ID (e.g. `CAF-370`). Optional.
- If absent, infer from the current worktree directory name (`basename "$PWD"`
  uppercased). If inference fails → **AskUserQuestion** for the ticket id
  (or exit non-zero in `--non-interactive`, see Step 1).
- `--non-interactive` — disable every `AskUserQuestion` path; any branch
  that would prompt the user instead emits a structured `Status:` line and
  exits non-zero. Designed for `/ggx-work --auto` and other unattended
  callers that cannot satisfy a prompt.

---

## Steps

### Step 1: Resolve ticket id

1. Parse `$ARGUMENTS` for the `--non-interactive` flag → `<non-interactive>`
   (True/False). Strip it before reading the remaining arguments.
2. Read remaining `$ARGUMENTS`. If non-empty → `<ticket-id> = $ARGUMENTS`
   (trim, uppercase).
3. Else infer from cwd:
   ```bash
   inferred=$(basename "$PWD" | tr '[:lower:]' '[:upper:]')
   if [[ "$inferred" =~ ^[A-Z]+-[0-9]+$ ]]; then
     ticket_id="$inferred"
   fi
   ```
4. If still empty:
   - `<non-interactive> == True` → STOP with structured error:
     ```
     Status: MISSING_TICKET_ID
     Reason: no ticket id in arguments and cwd is not a ticket worktree
     ```
     Exit non-zero.
   - `<non-interactive> == False` → `AskUserQuestion`:
     > "No ticket id provided and the current directory is not a ticket worktree.
     > What Linear ticket should I route?" — free-text answer; abort if empty.

### Step 2: Fetch ticket

Call `mcp__claude_ai_Linear__get_issue` for `<ticket-id>`.

- Failure (network, not found, permission) → STOP with the verbatim MCP error
  and the hint `Verify the ticket id and that Linear MCP is authenticated.`

Hold `<labels>` = the `labels[]` array from the response.

### Step 3: Determine lane from classification label

Match `<labels>` against `{bug, port, feature}`:

| Match shape                    | `<lane>`  |
|--------------------------------|-----------|
| exactly one of `{bug,port,feature}` | that one |
| zero of the three              | `unknown` |
| two or three of the three      | `unknown` |

If `<lane> == unknown`:

- `<non-interactive> == True` → STOP with structured error:
  ```
  Status: UNKNOWN_LANE
  Ticket: <ticket-id>
  Reason: ticket has no single classification label
  Labels found in {bug,port,feature}: <comma-joined or 'none'>
  ```
  Exit non-zero. `/ggx-work` (the canonical non-interactive caller) is
  expected to translate this into a Linear comment + abort.
- `<non-interactive> == False` → **AskUserQuestion**:
  > "Ticket `<ticket-id>` has no single classification label (found:
  > `<comma-joined labels∩{bug,port,feature} or 'none'>`). Which pipeline
  > should it use?"

  Options: `bug` / `port` / `feature`. The user's answer becomes `<lane>`.
  `confidence = user-input`. Continue to Step 4 with the chosen lane.

Otherwise `confidence = rule-based`.

### Step 4: Route by lane

#### Step 4.bug — lane is `bug`

Bug-fix pipeline. Same phase-detection shape as Step 4.port but simpler:
no `port:ship marker` probe needed — bug tickets never go through the port
pipeline. The only phase information `/route` surfaces is whether the bug
ticket is already done (PR open + Linear `In Review`); everything else
delegates to `/bug:ff`'s own `infer_bug_stage` walker.

```
locate worktree at ../<ticket-id> (case-insensitive, via `git worktree list`
or fallback to ../<ticket-id> directly)
check: is the ticket already shipped? (gh pr view <id> state == OPEN, OR
       Linear status == In Review). If yes:
  recommended_command  = "(none — /bug:ff terminates at /dev:ship)"
  phase                = "done"
  reasoning            = "Classification is `bug`. PR is open and Linear
                         status is In Review — the bug pipeline has shipped."

Otherwise:
  recommended_command  = "/bug:ff <ticket-id>"
  phase                = "bug" (the walker inside /bug:ff resolves the
                                 sub-phase: start / fix-pending / verify /
                                 review / ship)
  reasoning            = "Classification is `bug`. /bug:ff will run the
                         appropriate stage based on .dev/mode.md and
                         current worktree state. If the human has not yet
                         written the fix, /bug:ff will pause at
                         fix-pending — write your fix in the worktree,
                         commit, then re-run /bug:ff."
next_after_recommended = "(none — /bug:ff terminates at /dev:ship)"
```

#### Step 4.feature — lane is `feature`

```
recommended_command  = "/dev:ff <ticket-id>"
phase                = "feature"
reasoning            = "Classification is `feature`. No port phase, no
                       spec-review gate — go straight to dev."
next_after_recommended = "(none — /dev:ff terminates at /dev:ship)"
```

Skip to Step 5.

#### Step 4.port — lane is `port`

Run the two binary probes below. The full decision matrix is:

| `port:ship marker` | `need-spec-review` label | → `recommended_command`         | `phase`                |
|--------------------|--------------------------|---------------------------------|------------------------|
| absent             | (any)                    | `/port:ff <ticket-id>`          | `porting`              |
| present            | present                  | `/spec-review <ticket-id>`      | `spec-review-pending`  |
| present            | absent                   | `/dev:ff <ticket-id>`           | `ready-for-dev`        |

**Probe 1: `port:ship marker`**

`/port:ship` commits the entire `openspec/changes/<change-name>/.port/`
directory (including `synth-report.md`) at the end of the port pipeline.
The presence of a committed `.port/synth-report.md` inside the ticket
worktree is therefore the canonical "port:ship has run" signal.

```bash
# Locate the ticket's worktree. /add-worktree convention: ../<ticket-id> lowercased.
ticket_lc=$(echo "<ticket-id>" | tr '[:upper:]' '[:lower:]')
wt=$(git worktree list --porcelain \
       | awk -v t="$ticket_lc" '/^worktree / && tolower($2) ~ t"$" {print $2; exit}')

# Fallback: try ../<ticket-id> directly if the worktree-list lookup missed.
if [ -z "$wt" ] && [ -d "../$ticket_lc" ]; then
  wt="$(cd "../$ticket_lc" && pwd)"
fi

port_ship_marker=absent
if [ -n "$wt" ] && [ -d "$wt" ]; then
  # synth-report.md must exist AND be tracked in git (port:ship commits it).
  if (cd "$wt" && git ls-files --error-unmatch \
        "openspec/changes/*/.port/synth-report.md" >/dev/null 2>&1); then
    port_ship_marker=present
  fi
fi
```

> **TODO**: when `/bug:ff` lands or `/ggx-work` / `/ggx-dispatcher` start
> consuming `/route`, extract this probe (and the `infer_port_stage` walkers
> in `commands/dev/port/ff.md` / `commands/dev/dev/ff.md`) into a shared
> shell library so the "is port done" definition lives in one place.

**Probe 2: `need-spec-review` label**

```
need_spec_review=$(contains <labels> "need-spec-review" ? present : absent)
```

(Already in memory from Step 2; no extra MCP call.)

**Probe 3 (advisory only): `spec-review:v1` comment present**

This probe does NOT change the recommendation — it exists purely for
discoverability. When `phase == ready-for-dev`, a `<!-- spec-review:v1 -->`
comment is the upstream contract from `/spec-review`; we want the user to
know it will be picked up by `/dev:start` Step 4c and surfaced to
`/dev:apply` so `[REVISED]` directives are honored.

```bash
# Only worth probing in the ready-for-dev row; cheap to call but skip when
# the recommendation is anything other than /dev:ff.
spec_review_comment=absent
if [ "$phase" = "ready-for-dev" ]; then
  spec_review_comment=$(mcp__claude_ai_Linear__list_comments \
      --issueId "<ticket-id>" --orderBy createdAt 2>/dev/null \
    | jq -r --arg t "<ticket-id>" '
        [.comments[]? | select(.body
            | test("^<!-- spec-review:v1 ticket=" + $t + " -->"))]
        | length > 0' 2>/dev/null || echo false)
fi
```

**Decide** per the matrix above. Populate:

- `phase` from the matrix
- `recommended_command` from the matrix
- `reasoning` — one paragraph citing both probe results, e.g.:
  > "Classification is `port`. Worktree `../caf-370` exists; committed
  > `openspec/changes/.../synth-report.md` is present → port pipeline has
  > shipped. Linear label `need-spec-review` is also present → the ported
  > spec has not been human-reviewed yet. Run /spec-review before /dev:ff
  > so an LLM-written spec is not implemented by another LLM without a
  > human gate."
- `next_after_recommended`:
  - phase=porting → `/spec-review <ticket-id>` (then `/dev:ff`)
  - phase=spec-review-pending → `/dev:ff <ticket-id>`
  - phase=ready-for-dev → `(none — /dev:ff terminates at /dev:ship)`

### Step 5: Print the recommendation

Emit a single block to stdout (NOT a Linear comment, NOT a file). Plain
markdown — humans read it, copy the command line:

```
Ticket  : <ticket-id> — "<ticket title>"
Lane    : <lane>            (confidence: <rule-based|user-input>)
Phase   : <phase>

Recommendation:
  <recommended_command>

Reasoning:
  <reasoning>

Next after this finishes:
  <next_after_recommended>
  (Re-run /route <ticket-id> after this command completes to confirm.)
```

If Step 4.port's Probe 3 reported `spec_review_comment == true`, append a
one-line WARN immediately after the `Recommendation:` block (before
`Reasoning:`):

```
WARN: A `<!-- spec-review:v1 -->` comment exists on this ticket.
      /dev:start Step 4c will capture it to .dev/spec-review-directives.md;
      /dev:apply will surface any [REVISED] directives to the authoring
      agent as authoritative overrides. No action required here — this is
      a discoverability note, not a behavior change.
```

This WARN is a discoverability win only — `/route` itself still recommends
`/dev:ff` for `ready-for-dev`. Do NOT redirect to `/spec-review`; the
spec-review human gate has already run by the time this row matches.

STOP. Do not execute the recommended command.

---

## Guardrails

- **Read-only.** No `save_issue`, no `save_comment`, no file writes, no
  git mutations. Linear MCP calls are limited to `get_issue` and
  `list_comments` (the latter only when phase resolves to `ready-for-dev`,
  purely to surface the spec-review WARN line in Step 5 — read-only).
- **No dispatcher coupling.** `/route` does not read `ready-to-port`,
  `ready-to-dev`, or `dispatcher-*-in-flight` labels. The classification
  label + `need-spec-review` + worktree filesystem are the only signals.
- **No pipeline-internal resume detection.** If `/port:ff` was interrupted
  mid-stage, `/route` still recommends `/port:ff` — the FF wrapper's own
  `infer_port_stage` figures out where to resume.
- **AskUserQuestion is the ONLY HITL path.** Triggered when (a) ticket id
  cannot be inferred, or (b) classification labels are missing/ambiguous.
  No other prompts. With `--non-interactive`, both paths convert to
  structured `Status:` errors + non-zero exit; the caller (typically
  `/ggx-work --auto`) is responsible for surfacing the gate.
- **Idempotent.** Re-running `/route` on the same ticket with no state
  changes returns the same recommendation.
