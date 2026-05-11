# `/spec-review` — Skill Build Plan

> **Scope of this plan**: design and build the `/spec-review` skill only.
> Cross-cutting changes to `/port:revise`, `/port:ship`, and `/dev:apply` are
> documented at the end as **dependencies / future work** but are out of scope
> for the spec-review build itself.

---

## 1. Goal

Independent skill that processes a ticket sitting in `need-spec-review` state.
Walks the human through every `[AUTO-ACCEPTED]` assumption left by `/port:ff`,
captures decisions (Confirm / Revise / Defer), posts a single structured Linear
comment with a stable marker, and flips the label to `ready-to-dev`.

The skill **does not modify any OpenSpec artifact**. Decisions are persisted as
a Linear comment that downstream consumers (eventually `/dev:apply`) treat as
authoritative override on top of artifacts.

## 2. Non-Goals

- No filesystem writes (no artifact edits, no `.spec-review/` state).
- No git commits, no pushes, no PRs.
- No `openspec validate`, no `/spec-lint` invocation.
- No dependency on `/port:*` worktree layout (`.port/` not required).
- No state persistence — interrupt = restart from the top.
- No re-evaluation of auto-accept severity. The skill surfaces, the human decides.

---

## 3. Architecture

```
┌──────────────────┐                ┌─────────────────────┐
│   /port:ff       │  writes        │  Linear ticket      │
│   (synth-agent)  │ ─────────────► │  comment with       │
│                  │  [AUTO-        │  [AUTO-ACCEPTED]    │
│                  │  ACCEPTED]     │  markers (v1)       │
└──────────────────┘                └──────────┬──────────┘
                                                │
                                                │ reads
                                                ▼
                                    ┌─────────────────────┐
                                    │   /spec-review      │  <-- THIS PLAN
                                    │   <ticket-id>       │
                                    │                     │
                                    │   HITL per item     │
                                    └──────────┬──────────┘
                                                │ writes
                                                ▼
                                    ┌─────────────────────┐
                                    │  Linear comment     │
                                    │  <!-- spec-review:  │
                                    │       v1 -->        │
                                    │  [CONFIRMED]        │
                                    │  [REVISED]          │
                                    │  [DEFERRED]         │
                                    │  + source_hash      │
                                    └──────────┬──────────┘
                                                │
                                                │ + label flip
                                                │   need-spec-review
                                                │   → ready-to-dev
                                                │
                                                ▼ reads (future)
                                    ┌─────────────────────┐
                                    │   /dev:apply        │
                                    │   honors [REVISED]  │
                                    │   verifies hash     │
                                    └─────────────────────┘
```

The load-bearing wall is the **Linear comment marker contract** described in
§5. spec-review owns the write side; downstream owns the read side.

---

## 4. Contract

| Field | Spec |
|-------|------|
| Entry | `/spec-review <ticket-id>` |
| Source of truth | Linear ticket — `description` + `comments` |
| Precondition (soft) | Ticket label includes `need-spec-review` (warn if absent, do not abort) |
| Side effect 1 | Post one Linear comment with `<!-- spec-review:v1 -->` marker |
| Side effect 2 | Remove label `need-spec-review`, add `ready-to-dev` |
| Success | Comment posted **AND** label flipped |
| Partial failure | Comment posted, label flip failed → print comment ID, exit non-zero |
| No state file | Restart-on-interrupt only |

---

## 5. Marker contract

### 5.1 Input — what spec-review reads (from `/port:*`)

**Target format (v1, after future port-side update):**
```
- <!-- ac:v1 sev=medium -->**[AUTO-ACCEPTED]** medium — <body text>
```

**Legacy format (today's `/port:*` output, must keep working):**
```
- **[AUTO-ACCEPTED] medium** — <body text>
```

Parser strategy (precedence order):
1. Search for `<!-- ac:v1 sev=(low|medium|high) -->` HTML comments. If any found,
   use v1 path: extract severity from the HTML comment, body from the text after
   the marker line.
2. Else fall back to regex on visible text:
   `\*\*\[AUTO-ACCEPTED\] (low|medium|high)\*\*\s*—\s*(.+?)(?=\n- \*\*|\n###|\n##|\Z)`
3. Print which format was used in the skill's stdout. If legacy, append a
   one-line warning to the final Linear comment: `> Note: parsed legacy marker
   format (v0). Recommend re-running /port:ff once port:* skills are upgraded.`

### 5.2 Output — what spec-review writes (for `/dev:apply` to read)

```markdown
<!-- spec-review:v1 ticket=<TICKET-ID> -->
## Spec Review — Final Decisions

### [CONFIRMED] <auto-accept short label>
- Severity: <low|medium|high>
- Verdict: Accept as-is
- Source hash: `sha256:<first 16 hex>`

### [REVISED] <auto-accept short label>
- Severity: <low|medium|high>
- Verdict: Reject — needs change
- Original: <one-line summary of original auto-accept body>
- Directive: <user-written replacement directive>
- Source hash: `sha256:<first 16 hex>`

### [DEFERRED] <auto-accept short label>
- Severity: <low|medium|high>
- Verdict: Defer — note only
- Note: <user-written note>
- Source hash: `sha256:<first 16 hex>`

---

**Reviewer**: <Linear user> at <ISO timestamp>
**Authoritative for `/dev:apply`. `[REVISED]` directives override conflicting
guidance in OpenSpec artifacts. If `source_hash` mismatches the current
auto-accept body, re-review is required.**
```

### 5.3 source_hash

For each block, compute `sha256(<original auto-accept body>)`. Store the first
16 hex chars in the block. Purpose:

- `/dev:apply` re-hashes the live `[AUTO-ACCEPTED]` body when reading.
- Mismatch ⇒ ticket mutated since review ⇒ abort, ask for re-review.
- Defeats: schema drift, concurrent reviewer race, post-review ticket edits.
- Cost: one hash per block, no infrastructure.

---

## 6. Pipeline

### Stage 1 — Resolve
- Call `mcp__linear-server__get_issue <ticket-id>`.
- Capture: `description`, `labels`, `gitBranchName` (informational only),
  `assignee`.
- If `need-spec-review` not in labels → print warning, ask `Continue` /
  `Abort`. Continuing is allowed (re-review use case).

### Stage 2 — Extract auto-accepts
- `mcp__linear-server__list_comments` ordered by `createdAt` desc.
- Iterate newest → oldest. For each comment:
  - Try v1 HTML marker first (§5.1).
  - Else try legacy regex.
  - Stop iterating once a comment yields ≥1 matches.
- Heuristic for "is this a port summary":
  - Contains substring `## Port summary` **or** `## Synth summary`
    **or** `Authored by port:` **or** ≥1 occurrence of `[AUTO-ACCEPTED]`.
- **Cross-check (defends silent zero-acceptance)**:
  - If a port-summary comment exists AND zero markers parsed:
    - Print first 300 chars of the suspect comment.
    - AskUserQuestion: `Treat as parse error and abort` /
      `Override — proceed as lite mode` / `Other`.
    - Default option must be **abort** (defaulting toward "ship" is the
      banned failure mode).
  - If no port-summary comment exists at all → genuine lite mode (§7.5).

### Stage 3 — Pre-HITL summary (anti-fatigue, mandatory when N ≥ 5)
Before any decision prompts, print a single overview message listing all
parsed items with severity badges, e.g.:
```
Parsed 7 auto-accepted assumptions:
  [HIGH]   1. Risk R-1 — backend populates bookmarked_order
  [MEDIUM] 2. Risk R-2 — provider hydration race
  [LOW]    3. Check 6 omission — citation style
  ...
```
This forces the reviewer to see the **shape** of the review before clicking.

### Stage 4 — HITL decision loop
Sort items by severity: `high → medium → low`. Within severity, preserve
source order.

For each item, AskUserQuestion with four options:
- `Accept as-is` (Recommended for low only)
- `Reject — needs change`
- `Defer — note only`
- `Other`

**High-severity gate**: when severity is `high`, `Accept as-is` MUST require a
follow-up `AskUserQuestion` asking the reviewer to type the literal word
`REVIEWED` (treat any other answer as the user wanting to reconsider — loop
back to the original four options). This prevents rubber-stamping high-stakes
items.

**Reject path**: ask a free-text follow-up: "What should this be changed to?"
- If answer is empty / "I don't know" / "TBD" → fall back to `Defer` with a note
  capturing the conversation. Never proceed with an empty directive.

**Defer path**: ask for an optional note (free-text).

**Other path**: treat any free-text response as a `Reject + directive`, then
loop back to confirm: "Recorded as Reject with directive '<text>' — confirm? Y/N".
Never silently bucket Other.

### Stage 5 — Post Linear comment
- Build comment body per §5.2.
- Compute `source_hash` per block (sha256 of original auto-accept body).
- Pre-post check: re-fetch comments. If a `<!-- spec-review:v1 -->` comment
  exists with `createdAt > skill-start-time` ⇒ **another reviewer landed first**.
  Abort with: "Concurrent review detected at <ts>. Re-run /spec-review to see
  the latest state."
- `mcp__linear-server__save_comment` with `issueId` and built body.
- On API failure → retry once. On second failure → abort, no label flip.

### Stage 6 — Flip label
- `mcp__linear-server__save_issue` with labels = current labels − `need-spec-review`
  + `ready-to-dev`.
- On failure → print comment ID + suggest manual recovery. Exit non-zero.
- Do NOT roll back the comment — the review work is the value, label is bookkeeping.

---

## 7. UX details

### 7.1 Output verbosity
After Stage 1, print a one-line resolve summary:
`Reviewing CAF-370 (assignee: Charlie Yang) — labels: [need-spec-review, ...]`.

After Stage 2, print: `Found N auto-accepts (parser: v1 | legacy).`

After Stage 6, print: `Posted comment <id>. Label flipped. URL: <ticket URL>`.

### 7.2 Lite mode (no port-summary comment found)
- AskUserQuestion: `Confirm — review with no auto-accepts to process` / `Abort`.
- On confirm → post short comment (still with `<!-- spec-review:v1 -->`):
  ```
  <!-- spec-review:v1 ticket=<id> -->
  ## Spec Review — No revisions
  No auto-accepted assumptions were posted by upstream synthesis.
  Reviewer confirmed artifacts as-is.
  ```
- Flip label.

### 7.3 Re-review of an already-reviewed ticket
- Stage 5's pre-post check finds an older `spec-review:v1` comment.
- This is **allowed** if the older comment's `createdAt` is older than
  skill-start-time (the user is intentionally re-reviewing).
- Post the new comment (latest wins).
- Do NOT delete the older comment.
- Stage 6 still flips label (idempotent if already `ready-to-dev`).

### 7.4 Abort safety
- Stage 1–4 abort: nothing posted, nothing flipped. Restart from the top.
- Stage 5 succeeds + Stage 6 fails: print recovery hint, exit non-zero.
- No partial state to clean up.

---

## 8. Edge cases

| Scenario | Behavior |
|----------|----------|
| Ticket missing `need-spec-review` label | Warn + AskUserQuestion to continue |
| Ticket has zero comments | Lite mode confirmation flow |
| Port comment found but 0 markers | **Parse error** flow (§Stage 2 cross-check) |
| Reviewer cannot articulate Reject directive | Auto-fallback to Defer with conversation note |
| Reviewer chooses "Other" | Reroute to Reject + confirm loop, never silent |
| Concurrent reviewer race | Pre-post check aborts second reviewer |
| Linear API 5xx during comment post | Retry once, then abort cleanly |
| Linear API 5xx during label flip | Print comment ID + manual recovery hint |
| Older `spec-review:v1` comment exists | Allowed (intentional re-review), latest wins |
| `gitBranchName` missing on ticket | Irrelevant — skill does not use fs |
| Auto-accept body contains backticks / fences | Hash the raw text, do not re-render |

---

## 9. File layout

```
commands/dev/spec-review.md      # the skill (single file, ~370 lines)
plans/spec-review.md             # this document
plans/spec-review-adapters.md    # downstream port + dev adapter TODOs
```

Installed via `./install.sh` — the skill file symlinks to
`~/.claude/commands/spec-review.md` so the CLI surfaces it as `/spec-review`.

The skill is **not** decomposed into atomic sub-commands (no `/spec-review:start`,
no `/spec-review:apply`). The pipeline is short enough to live in one file.
Revisit if Stage 4 starts requiring resume support.

---

## 10. Adapter TODOs — out of scope for this build

The `/port:*` and `/dev:apply` adaptations needed for the v1 marker contract
are tracked separately in **`plans/spec-review-adapters.md`**. Spec-review
must ship working with legacy-format port comments via §5.1 fallback so it
can land **before** those adapters.

---

## 11. Open decisions deferred to implementation

- Exact wording of HITL prompts (will draft during skill authoring).
- Whether the pre-HITL summary (§Stage 3) should also list which artifact
  files each auto-accept likely affects (would require reading the worktree —
  punt; spec-review stays Linear-only).
- Telemetry / logging — none for v1, add only if usage warrants.
