---
name: spec-review
description: >
  Independent skill that processes a ticket sitting in `need-spec-review`
  state. Walks the human through every reviewable item upstream synthesis left
  behind — `[AUTO-ACCEPTED]` markers, `### Assumptions` (A-N), and `### Risks`
  (R-N) — captures decisions (Confirm / Revise / Defer), posts a single
  structured Linear comment with a stable marker, and flips the label to
  `ready-to-dev`. Does NOT modify OpenSpec artifacts, does NOT commit, does
  NOT touch the filesystem. Pure Linear-side review gate.
Prerequisite: >
  - Linear MCP authenticated.
  - Ticket has typically been processed by /port:ff (label `need-spec-review`).
    Other origins are allowed; the skill warns but does not abort.
---

# `/spec-review <ticket-id>`

Process a ticket that's been auto-synthesized and now needs human review of
the assumptions upstream made on the user's behalf. Read the ticket comments
to find `[AUTO-ACCEPTED]` items, walk through each one with the user, and
persist the decisions as a Linear comment that downstream `/dev:apply`
treats as authoritative override.

**Usage**: `/spec-review <ticket-id>`

- `<ticket-id>` — Linear ticket ID (e.g. `CAF-370`). Required.
- No `--auto` mode. This skill is fundamentally HITL — auto-accepting auto-accepts
  defeats its purpose.

---

## Steps

### Step 0: Capture skill-start timestamp

Capture an ISO-8601 timestamp `<skill-start-time>` immediately. Used in step 5
to detect concurrent reviewers.

### Step 1: Parse input + resolve ticket

1. Extract `<ticket-id>` from `$ARGUMENTS`. Missing → use `AskUserQuestion`
   to ask for it. Stop if still missing.
2. Call `mcp__linear-server__get_issue` with the id. Capture:
   - `<labels>` — array of label names
   - `<assignee>`
   - `<title>`, `<url>`
3. **Soft precondition check**: if `need-spec-review` not in `<labels>`:
   - Use `AskUserQuestion` with options `Continue` / `Abort`.
   - On `Continue`, proceed (re-review of an already-flipped ticket is allowed).
   - On `Abort`, STOP.
4. Print one-line status:
   `Reviewing <ticket-id> "<title>" (assignee: <assignee>) — labels: [<labels>]`

### Step 2: Extract review items

A ticket's port-summary comment may surface review items in **three** shapes.
Spec-review accepts all three and merges them into a single review list.

| Shape | Marker | Example | Default severity |
|-------|--------|---------|------------------|
| Auto-accept (v1) | `<!-- ac:v1 sev=X -->**[AUTO-ACCEPTED]** ...` | `<!-- ac:v1 sev=medium -->**[AUTO-ACCEPTED]** medium — Risk R-1 ...` | explicit from marker |
| Auto-accept (legacy) | `**[AUTO-ACCEPTED] X**` | `- **[AUTO-ACCEPTED] medium** — Risk R-1 ...` | explicit from marker |
| Assumption | `**A-N**` / `**AR-N**` under `### Assumptions` | `- **A-1**: bookmarkedTransportOrdersProvider is already populated ...` | `low` |
| Risk | `**R-N**` under `### Risks` | `- **R-1**: CAF-368 not yet built — Edit screen depends on ...` | `medium` |

#### Steps

1. Call `mcp__linear-server__list_comments` with `orderBy: createdAt` (newest
   first). Page through if needed.
2. Walk comments newest → oldest. The first comment that contains a port
   summary (heuristic in §2.6) is the **target comment**. Parse it as below;
   stop iterating once parsed.
3. **Parse auto-accepts** (precedence: v1 > legacy):
   - **v1 path** — find all `<!-- ac:v1 sev=(low|medium|high) -->`. For each:
     - `severity` = HTML attribute value
     - `body` = the line containing the marker plus continuation lines (until
       next `- ` list item / blank line / section heading)
     - `source` = `"auto-accept"`, `parser_mode` = `"v1"`
   - **Legacy fallback** — only if v1 found nothing in this comment. Regex:
     ```
     \*\*\[AUTO-ACCEPTED\]\s+(low|medium|high)\*\*\s+—\s+(.+?)(?=\n- \*\*|\n###|\n##|\Z)
     ```
     `source` = `"auto-accept"`, `parser_mode` = `"legacy"`.
4. **Parse Assumption items** (independent of auto-accept presence):
   - Locate every section heading matching `^###\s+Assumptions?\b` (e.g.
     `### Assumptions`, `### Assumptions (post-clarification)`).
   - Within the section body (until the next `^##` or `^###` heading), match
     list items shaped `- \*\*(A|AR)-\d+\*\*:?\s*(.+?)(?=\n- \*\*|\n###|\n##|\Z)`.
     The body may span multiple lines (continuation if subsequent lines start
     with whitespace).
   - For each hit: `severity` = `low`, `source` = `"assumption"`.
   - Sub-sections such as `**From dev-notes**:`, `**From pm-notes**:` inside
     `### Assumptions` are walked transparently — items are flattened.
5. **Parse Risk items**:
   - Locate every section heading matching `^###\s+Risks?\b`.
   - Same list-item shape as assumptions but ID prefix `R`:
     `- \*\*R-\d+\*\*:?\s*(.+?)(?=...)`.
   - For each hit: `severity` = `medium`, `source` = `"risk"`.
   - **Skip** items whose body matches `(?i)out of scope|tracked in\s+\.port`
     — these are informational pointers, not review items. Print one line:
     `Skipped R-N as informational (out-of-scope / tracked elsewhere).`
6. **Heuristic — is this a port summary?**
   Set `<port_summary_seen>` = `True` if any inspected comment body contains
   any of (case-sensitive): `## Port summary`, `## Synth summary`,
   `Authored by port:`, `[AUTO-ACCEPTED]`, or **both** of (`### Assumptions`
   AND `### Risks`).
7. Build `<items>: [{severity, body, source, comment_id, parser_mode}]`.
   Deduplicate: if the same `A-N`/`R-N` ID appears both inside an
   `[AUTO-ACCEPTED]` body AND as a standalone assumption/risk item, keep
   only the auto-accept (it's the explicit reviewer call).
8. Compute `<source_hash[i]> = sha256(<items[i].body>)`. Keep first 16 hex chars.
9. **Cross-check (defends silent zero-acceptance)**:
   - If `len(items) == 0` AND `<port_summary_seen>` is `True`:
     - Parse-error territory, NOT lite mode. Print first 300 chars of the
       suspect comment.
     - `AskUserQuestion`: options
       (a) `Treat as parse error and abort` (Recommended)
       (b) `Override — proceed as lite mode anyway`
       (c) `Other`
     - Default to (a) on ambiguous input.
     - On (a) → STOP with: `Review-item parser drift detected. Inspect the
       suspect comment and update either /port:* writers or this skill's parser.`
   - If `len(items) == 0` AND `<port_summary_seen>` is `False`:
     - True lite mode. Skip to **Step 7** (lite-mode flow).
10. Print: `Found <N> review items — auto-accept:<a>, assumption:<b>, risk:<c> (parser: <v1|legacy|sections>).`
    - `parser_mode` on output = `v1` if any v1 hit, else `legacy` if any
      legacy hit, else `sections` (assumptions/risks only).
11. If any item has `parser_mode == "legacy"`, set `<legacy_warning>` = `True`
    (used in Step 5's posted comment).

### Step 3: Pre-HITL overview (mandatory when N ≥ 5; recommended otherwise)

Print a single overview message before any decision prompts. Sort by:
1. severity `high → medium → low`
2. source priority `auto-accept → risk → assumption` (auto-accepts get
   reviewed first — they're the items synth explicitly flagged for human input)
3. source order within group

Format:

```
Found <N> review items:
  auto-accept:<a>  assumption:<b>  risk:<c>

  [HIGH]   [AC]  1. <first 80 chars of body>
  [MED]    [AC]  2. <...>
  [MED]    [R]   3. <...>
  [LOW]    [A]   4. <...>
  ...

Legend: [AC]=auto-accept, [A]=assumption, [R]=risk
```

This forces the reviewer to see the shape of the review before the
per-item flow starts. No prompt at the end of this step — just print and move
on to step 4.

### Step 4: HITL decision loop

Iterate `<items>` in the sorted order from step 3.

For each item:

1. Print the **full** body of the item (not truncated). Format:
   ```
   ─────────────────────────────────────────
   [<SEVERITY>] [<SOURCE>] Item <i>/<N>
   <full body text>
   ─────────────────────────────────────────
   ```
   `<SOURCE>` is one of `AUTO-ACCEPT`, `ASSUMPTION`, `RISK`.

2. `AskUserQuestion` with four options:
   - `Accept as-is` (mark Recommended only when severity is `low`)
   - `Reject — needs change`
   - `Defer — note only`
   - `Other`

3. **High-severity gate**: if `severity == "high"` AND user picks `Accept as-is`,
   issue a follow-up `AskUserQuestion`:
   - Question: "This is a HIGH severity item. Type the literal word `REVIEWED`
     to confirm acceptance, or pick another option to reconsider."
   - Options: `Type REVIEWED to confirm` / `Reconsider — go back to options`
   - On `Type REVIEWED to confirm` → user must enter `REVIEWED` via the
     question's free-text input. Anything other than the exact string `REVIEWED`
     loops back to the original four options for this item.
   - On `Reconsider` → loop back to the original four options.

4. **Reject path**: ask a free-text follow-up via `AskUserQuestion`:
   - Question: "What should this be changed to? (Be specific — this becomes
     the directive `/dev:apply` follows.)"
   - Options: `I'll write it now` / `I don't know yet — defer instead` / `Other`
   - On `I don't know yet — defer instead` → fall back to Defer with the
     conversation captured in the note.
   - On `I'll write it now` → user enters the directive in the free-text input.
     Empty / whitespace-only / "TBD" / "later" → loop back as Defer with note.
   - Never proceed with an empty directive.

5. **Defer path**: ask a free-text follow-up:
   - Question: "Optional note to capture context for this deferral."
   - Options: `Add a note` / `No note needed`

6. **Other path**: treat any free-text response as Reject + directive. Then
   loop back via `AskUserQuestion`:
   - Question: "Recorded as Reject with directive '<text>'. Confirm?"
   - Options: `Confirm` / `Edit directive` / `Switch to Defer instead`
   - Never silently bucket Other.

7. Append decision to `<decisions>: [{item_index, verdict, directive?, note?, source_hash}]`.

### Step 5: Pre-post concurrency check + post Linear comment

1. Re-fetch comments via `mcp__linear-server__list_comments`.
2. Scan for any comment whose body contains `<!-- spec-review:v1 ticket=<id> -->`
   AND whose `createdAt` is **strictly newer** than `<skill-start-time>` (from
   step 0). If found:
   - STOP with: `Concurrent review detected at <ts> by <author>. Re-run
     /spec-review <ticket-id> to see the latest state and decide whether to
     re-review.`
   - Do NOT post our comment.
3. Build the comment body using the schema in **§A**. Include:
   - Header HTML marker `<!-- spec-review:v1 ticket=<ticket-id> -->`
   - One block per decision (CONFIRMED / REVISED / DEFERRED) with severity,
     verdict, and source_hash
   - Footer: authoritative-for-/dev:apply note
   - If `<legacy_warning>` is True: prepend a one-line note inside the comment:
     `> Note: parsed legacy auto-accept marker format. Recommend re-running
     /port:ff after the v1 marker rollout.`
4. Call `mcp__linear-server__save_comment` with `issueId: <ticket-id>` and
   `body: <built body>`.
5. On 5xx / network failure → retry once after a short pause. Second failure
   → STOP with: `Failed to post review comment after retry. Decisions are not
   persisted. Re-run /spec-review.`
6. On success, capture `<comment_id>` from the response.

### Step 6: Flip label

1. Compute new label set:
   - Remove `need-spec-review` if present.
   - Add `ready-to-dev`.
   - Preserve all other labels unchanged.
2. Call `mcp__linear-server__save_issue` with `id: <ticket-id>` and
   `labels: <new label set>`.
3. On failure → print:
   ```
   Comment posted (id: <comment_id>) but label flip failed.
   Manual recovery: remove `need-spec-review`, add `ready-to-dev` on Linear.
   ```
   Exit non-zero. Do NOT roll back the comment.
4. On success, print:
   ```
   ✅ Spec review complete.
   Comment: <ticket-url>#comment-<comment_id>
   Label: need-spec-review → ready-to-dev
   ```

### Step 7: Lite mode (only reached from §Step 2.5 when no port summary exists)

This branch handles tickets that genuinely have no upstream auto-accepts
(e.g. manually created OpenSpec changes routed through `need-spec-review`).

1. `AskUserQuestion`:
   - Question: "No auto-accepts found in ticket comments. Confirm review with
     no revisions, or abort?"
   - Options: `Confirm — review approved as-is` / `Abort`
2. On Abort → STOP, no side effects.
3. On Confirm:
   - Post short comment with body:
     ```
     <!-- spec-review:v1 ticket=<ticket-id> -->
     ## Spec Review — No revisions

     No auto-accepted assumptions were posted by upstream synthesis.
     Reviewer confirmed artifacts as-is at <ISO timestamp>.
     ```
   - Then proceed to Step 6 (flip label). Same retry / failure semantics.

---

## §A — Output comment schema

The posted comment body MUST follow this exact structure. `/dev:apply` parses
it with regex anchored on the HTML marker and the bracketed verdict tags.

```markdown
<!-- spec-review:v1 ticket=<TICKET-ID> -->
## Spec Review — Final Decisions

### [CONFIRMED] <one-line label derived from item body>
- Severity: <low|medium|high>
- Source: <auto-accept|assumption|risk>
- Verdict: Accept as-is
- Source hash: `sha256:<16 hex>`

### [REVISED] <one-line label>
- Severity: <low|medium|high>
- Source: <auto-accept|assumption|risk>
- Verdict: Reject — needs change
- Original: <one-line summary of original item body, ≤120 chars>
- Directive: <user-written replacement directive, full text>
- Source hash: `sha256:<16 hex>`

### [DEFERRED] <one-line label>
- Severity: <low|medium|high>
- Source: <auto-accept|assumption|risk>
- Verdict: Defer — note only
- Note: <user-written note, may be empty>
- Source hash: `sha256:<16 hex>`

---

**Reviewer**: <Linear-display-name-of-current-user> at <ISO timestamp>
**Authoritative for `/dev:apply`. `[REVISED]` directives override conflicting
guidance in OpenSpec artifacts. If `source_hash` mismatches the current
item body, re-review is required.**

**Precedence hint for `/dev:apply`**:
- `Source: auto-accept` directives — firm, override artifacts unconditionally.
- `Source: risk` directives — firm, treat as mitigation requirements.
- `Source: assumption` directives — strong guidance; artifacts may still
  contradict if the implementation reveals the assumption is false. Document
  the divergence in commit message if so.
```

### Schema rules

- Severity / Source / Verdict / Source hash are required on every block.
- "One-line label" is the first 60 chars of the item body, trimmed at a word
  boundary, no trailing punctuation. For assumption / risk items, prefix
  the label with the original ID: `A-1: <label>` / `R-1: <label>`.
- Original / Directive / Note text are passed through verbatim except for
  trailing whitespace strip; do NOT re-render markdown inside.
- Source hash format: literal string `sha256:` followed by exactly 16
  lowercase hex chars (first 16 of `sha256(body)`).
- Footer + precedence hint are fixed text — copy them exactly so downstream
  parsers can grep.

---

## §B — Edge case reference

| Scenario | Step | Behavior |
|----------|------|----------|
| Missing `need-spec-review` label | 1.3 | Warn + AskUserQuestion to continue |
| 0 comments on ticket | 2 | Lite mode (Step 7) |
| Port summary has `[AUTO-ACCEPTED]` items | 2.3 | Reviewed first, explicit severity |
| Port summary has only `### Assumptions` / `### Risks` (no auto-accepts) | 2.4-2.5 | Reviewed with default severities (A=low, R=medium) |
| Port summary exists, 0 items of any shape parsed | 2.9 | Parse error abort (default) |
| Risk item marked `out of scope` / `tracked in .port` | 2.5 | Skipped as informational |
| Same `A-N`/`R-N` ID appears in both auto-accept and section | 2.7 | Dedup, keep auto-accept |
| Reviewer can't articulate Reject directive | 4.4 | Fall back to Defer with note |
| Reviewer chooses "Other" verdict | 4.6 | Reroute to Reject + confirm loop |
| Concurrent reviewer race | 5.2 | Abort, ask user to re-run |
| Linear 5xx on comment post | 5.5 | One retry, then abort cleanly |
| Linear 5xx on label flip | 6.3 | Print comment id, exit non-zero |
| Older `spec-review:v1` comment exists | 5 | Allowed (intentional re-review), latest wins |
| Item body has backticks / fences | 2.8 | Hash raw text, do not re-render |
| User aborts mid-loop | any | No side effects; restart from top |

---

## §C — Non-goals (do not extend)

- Do NOT read or modify any file under the ticket's worktree.
- Do NOT run `openspec validate` or `/spec-lint`.
- Do NOT git-commit anything.
- Do NOT push.
- Do NOT auto-decide on auto-accepts (the human is the value).
- Do NOT support resume / state files (restart-on-interrupt is intentional).
- Do NOT suggest moving auto-accepts into artifact files yourself; that is
  `/dev:apply`'s job per the override contract.
