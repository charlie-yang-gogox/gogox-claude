---
name: route
description: >
  Atomic decision command. Reads a ticket's classification (Linear label
  `bug` / `port` / `feature` / `design bug`, or Jira `issuetype.name`) plus
  minimal worktree state and recommends the ONE pipeline entry-point command
  to run next (`/port:ff`, `/dev:ff`, `/bug:ff`, `/ui-tweak:ff`, or
  `/spec-review`). Advisory
  only by default — prints the recommendation for the user to copy-paste.
  Does NOT execute the command, does NOT mutate ticket state, does NOT
  detect resume points inside a pipeline (the FF wrappers do that
  themselves via `infer_*_stage`). Supports both Linear and Jira via the
  abstraction documented in `_ticket-lib.md`. The `/ggx-work` orchestrator
  (and `/ggx-dispatcher` via spawned `/ggx-work`) call
  `/route --non-interactive` to drive multi-stage flows; in that mode
  prompts are converted to structured `Status:` errors with non-zero exit.
Prerequisite: >
  - Linear MCP authenticated for CAF/DAF tickets; Atlassian Rovo MCP
    authenticated for CET/DET tickets.
  - For port-classified tickets (Linear only — Jira has no port lane): a
    worktree at `../<ticket-id>` is helpful but not required (absent
    worktree → recommend /port:ff).
---

# `/route [ticket-id]`

> `/route` is an **atomic decision command**. It answers ONE question:
> **which pipeline entry-point should I run for this ticket?**
>
> - It does NOT detect resume points within a pipeline — `/port:ff`,
>   `/dev:ff`, and `/ui-tweak:ff` derive their own resume via
>   `infer_port_stage` / `infer_dev_stage` / `infer_ui_stage`.
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

### Step 2: Resolve ticket_system + fetch ticket

Run the resolution block from `_ticket-lib.md` to set `<ticket-system>` ∈
`{linear, jira}` and (if Jira) `<jira-cloud-id>`. If `<ticket-system> ==
unknown` → STOP with structured error:

```
Status: UNKNOWN_TICKET_SYSTEM
Ticket: <ticket-id>
Reason: profile does not resolve to linear or jira (check .gogox-claude.yaml + org.yaml prefixes)
```

Exit non-zero.

Then fetch the issue per the matching branch:

- **Linear**: `mcp__claude_ai_Linear__get_issue` for `<ticket-id>`. Hold
  `<labels>` = `.labels[].name`. `<issue-type>` is `null` for Linear.
- **Jira**: `mcp__claude_ai_Atlassian_Rovo__getJiraIssue` with
  `cloudId: <jira-cloud-id>`, `issueIdOrKey: <ticket-id>`,
  `responseContentFormat: markdown`. Hold `<issue-type>` =
  `.fields.issuetype.name`. `<labels>` is irrelevant for Jira lane derivation.

Failure (network, not found, permission) → STOP with the verbatim MCP error
and the hint `Verify the ticket id and that the matching MCP server (Linear
or Atlassian Rovo) is authenticated.`

### Step 3: Determine lane

**Linear path** — first apply the `design bug` precedence rule, then match
`<labels>` against `{bug, port, feature}` (**case-insensitive**: lowercase
each label name before comparison, so the workspace's actual capitalized
labels `Bug` / `Port` / `Feature` / `Design bug` map to the canonical lanes —
mirrors the Jira path's case-insensitive treatment below):

> **`design bug` precedence (evaluated first).** If `<labels>` (each name
> lowercased, whole-string) contains **`design bug`** → `<lane> = ui-tweak`,
> full stop — evaluated **before and overriding** the canonical-set count,
> regardless of which other canonical labels co-occur (`bug`, `feature`,
> `port`, none, or several). All four shapes resolve identically:
> `design bug`+`bug` → ui-tweak; `design bug`+`feature` → ui-tweak;
> `design bug`+`port` → ui-tweak; `design bug` alone (zero canonical labels,
> which would otherwise be `unknown`) → ui-tweak. Only if `design bug` is
> absent do we fall through to the canonical match below. Canonical
> statement lives in `_ticket-lib.md` § Lane derivation.

| Match shape                    | `<lane>`  |
|--------------------------------|-----------|
| contains `design bug`          | `ui-tweak` (**precedence — wins over any canonical co-label**) |
| exactly one of `{bug,port,feature}` | that one |
| zero of the three              | `unknown` |
| two or three of the three      | `unknown` |

Note: `design bug` is a recognized classification label routing to the
`ui-tweak` lane — matched **whole-string** case-insensitive (`Design bug` /
`design bug` / `DESIGN BUG` all match), never as a substring. The other
three lanes likewise match the whole lowercased label name equalling
`bug`/`port`/`feature`, not a substring.

**Jira path** — derive from `<issue-type>` (case-insensitive):

| `<issue-type>`                                              | `<lane>`   |
|-------------------------------------------------------------|------------|
| `Bug`                                                       | `bug`      |
| `Story`, `Task`, `Sub-task`, `Subtask`, `Improvement`, `New Feature` | `feature` |
| anything else                                               | `unknown`  |

Jira repos have no `port` lane — the port pipeline is Linear-specific
(copy-from-source CAF/DAF tickets). Jira also has no `ui-tweak` lane (no
`design bug` issuetype); design-bug routing is Linear-only, like `port`. A
Jira ticket can only resolve to `bug` or `feature`.

If `<lane> == unknown`:

- `<non-interactive> == True` → STOP with structured error:
  ```
  Status: UNKNOWN_LANE
  Ticket: <ticket-id>
  System: <linear|jira>
  Reason: <linear: ticket has no single classification label | jira: issuetype.name not recognized>
  Signal: <linear: comma-joined labels∩{bug,port,feature} or 'none' | jira: issuetype.name value>
  ```
  Exit non-zero. `/ggx-work` (the canonical non-interactive caller) is
  expected to translate this into a ticket comment + abort.
- `<non-interactive> == False` → **AskUserQuestion**:
  > "Ticket `<ticket-id>` cannot derive a lane (system=<linear|jira>, signal=
  > `<labels-or-issuetype>`). Which pipeline should it use?"

  Options for Linear: `bug` / `port` / `feature` / `ui-tweak`. Options for
  Jira: `bug` / `feature` (no port or ui-tweak lane on Jira — silently
  omit). The user's answer becomes `<lane>`. `confidence = user-input`.
  Continue to Step 4 with the chosen lane.

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

#### Step 4.ui-tweak — lane is `ui-tweak` (Linear only)

If `<ticket-system> == jira` and `<lane> == ui-tweak` somehow → STOP with
`Status: UNKNOWN_LANE` (per Step 3, the Jira path can never derive
`ui-tweak`; this is a defense-in-depth guard, mirroring Step 4.port's).

Same done-detection shape as Step 4.bug: the only phase information
`/route` surfaces is whether the design-bug ticket is already shipped;
everything else delegates to `/ui-tweak:ff`'s own `infer_ui_stage` walker.

```
check: is the ticket already shipped? (gh pr view <id> state == OPEN, OR
       Linear status == In Review). If yes:
  recommended_command  = "(none — /ui-tweak:ff terminates at its draft PR)"
  phase                = "done"
  reasoning            = "Classification includes `design bug`. PR is open
                         (or Linear status is In Review) — the ui-tweak
                         pipeline has shipped its draft PR."

Otherwise:
  recommended_command  = "/ui-tweak:ff <ticket-id>"
  phase                = "ui-tweak" (the walker inside /ui-tweak:ff resolves
                                     the sub-phase: start / apply / preview /
                                     audit / commit / pr / review)
  reasoning            = "Classification includes `design bug` (precedence
                         over any canonical co-label). /ui-tweak:ff drives
                         the UI-only pipeline: apply → build gate → dual-judge
                         audit → commit → draft PR."
next_after_recommended = "(none — /ui-tweak:ff terminates at its draft PR)"
```

Skip to Step 5.

#### Step 4.port — lane is `port` (Linear only)

If `<ticket-system> == jira` and `<lane> == port` somehow → STOP with
`Status: UNKNOWN_LANE` (per Step 3 Jira mapping, port should already have
been rejected; this is a defense-in-depth guard).


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

- **Read-only.** No writes to either tracker, no file writes, no
  git mutations. Linear MCP calls are limited to `get_issue` and
  `list_comments` (the latter only when phase resolves to `ready-for-dev`,
  purely to surface the spec-review WARN line in Step 5 — read-only).
  Jira MCP calls are limited to `getJiraIssue` (returns embedded comments).
- **Ticket-system aware.** Always run the `_ticket-lib.md` resolution
  block. Never default to Linear silently; emit `UNKNOWN_TICKET_SYSTEM`
  and exit non-zero if the profile does not resolve.
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
