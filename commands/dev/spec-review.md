---
name: spec-review
description: >
  Independent skill that processes tickets sitting in `need-spec-review`
  state. Two invocation shapes: pass a ticket id to review just that one,
  or call with no args to auto-fetch every `need-spec-review` ticket assigned
  to you on the active team and walk them sequentially. Walks the human
  through every reviewable item upstream synthesis left behind — structured
  `ri:v1` records (joined by ID, routed by `verify`: `unconfirmed`/`n/a`
  items need a human decision, `confirmed`/`refuted` items are shown as
  verified-FYI and not prompted), plus legacy `[AUTO-ACCEPTED]` / `A-N` /
  `R-N` shapes for older comments — captures decisions (Confirm / Revise /
  Defer), posts a single structured Linear comment with a stable marker, and
  flips the label to `ready-to-dev`. Does NOT modify OpenSpec artifacts, does
  NOT commit, does NOT touch the filesystem. Pure Linear-side review gate.
Prerequisite: >
  - Linear MCP authenticated.
  - Ticket has typically been processed by /port:ff (label `need-spec-review`).
    Other origins are allowed; the skill warns but does not abort.
  - Batch mode additionally requires the active repo to have a resolvable
    gogox project profile (.gogox-claude.yaml or registry entry) so the team
    key for `list_issues` can be derived.
---

# `/spec-review [ticket-id]`

Process tickets that have been auto-synthesized and now need human review of
the assumptions upstream made on the user's behalf. Read each ticket's
comments to find `[AUTO-ACCEPTED]` items, walk through each one with the user,
and persist the decisions as a Linear comment that downstream `/dev:apply`
treats as authoritative override.

**Usage**:

- `/spec-review <ticket-id>` — **single mode**. Review just this one ticket.
- `/spec-review` — **batch mode**. Auto-fetch every `need-spec-review` ticket
  assigned to me on the active project's team and walk through them
  sequentially. Per-ticket failures do NOT abort the batch.

Notes:

- `<ticket-id>` — Linear ticket ID (e.g. `CAF-370`). Optional; absent → batch.
- No `--auto` mode. This skill is fundamentally HITL — auto-accepting auto-accepts
  defeats its purpose. Batch mode still asks every HITL question per ticket.

---

## Steps

### Step 0: Capture batch-start timestamp

Capture an ISO-8601 timestamp `<batch-start-time>` immediately. Used in Step 5
to detect concurrent reviewers. In batch mode this timestamp is shared across
all tickets in the run (so a v1 comment on any ticket newer than batch-start
counts as a concurrent reviewer for that ticket).

### Step 1: Parse input — single vs batch mode

1. Extract `<ticket-id>` from `$ARGUMENTS`. Two paths:
   - **Present** → `<batch-mode> = False`. `<queue> = [<ticket-id>]`. Skip to Step 2.
   - **Absent** → `<batch-mode> = True`. Proceed to **Step 1.5** to populate
     `<queue>`. Do NOT prompt the user for a ticket id — batch is intentional.

The remaining sub-steps below apply per-ticket inside the main loop (Step 2
onward iterates `<queue>`):

2. **Resolve the Linear MCP server once** (then reuse it for every Linear call
   in this skill, including the Step 1.5 `list_issues` above — `list_issues`,
   `get_issue`, `list_comments`, `save_comment`, `save_issue`):
   use whichever is connected this session — prefer `mcp__claude_ai_Linear__*`,
   otherwise fall back to `mcp__linear-server__*` (the project `.mcp.json`
   server). Only one is live per environment (the claude.ai connector is
   auto-hidden when a project server shares its URL), so hardcoding either
   prefix breaks in the other environment — mirror `ggx-dispatcher.md` §"All MCP
   tool calls". The calls below are written with the `mcp__claude_ai_Linear__*`
   prefix; substitute the resolved prefix uniformly.
   Call `get_issue` with the current ticket's id. Capture:
   - `<labels>` — array of label names
   - `<assignee>`
   - `<title>`, `<url>`
3. **Soft precondition check**: if `need-spec-review` not in `<labels>`:
   - **Single mode**: use `AskUserQuestion` with options `Continue` / `Abort`.
     On `Continue` proceed (re-review of an already-flipped ticket is allowed).
     On `Abort` STOP.
   - **Batch mode**: the queue was built by Step 1.5 filtering on this exact
     label, so absence here means it was flipped by a concurrent reviewer
     between fetch and loop iteration. Print `[<k>/<N>] <ticket-id> skipped — label flipped by concurrent reviewer.` and continue to the next ticket.
4. Print one-line status:
   `[<k>/<N>] Reviewing <ticket-id> "<title>" (assignee: <assignee>) — labels: [<labels>]`
   (In single mode the `[<k>/<N>]` prefix is `[1/1]`.)

### Step 1.5: Batch fetch (batch mode only)

Only runs when `<batch-mode> == True`. Builds `<queue>` from Linear instead of
the single ticket id passed by the user.

1. **Resolve project profile** to obtain `<team_key>`:
   - If `<repo-root>/.gogox-claude.yaml` exists → read its `team_key` field.
   - Else read `~/.claude/commands/profiles/registry/$(basename "$(git rev-parse --show-toplevel)").yaml` for `team_key`.
   - Neither resolves → STOP with:
     ```
     Cannot resolve gogox project profile for the active repo. Batch mode
     needs a team key to query Linear. Either add a registry entry or place
     .gogox-claude.yaml in the repo root, then re-run /spec-review.
     ```

2. **List tickets**: call `mcp__claude_ai_Linear__list_issues` with:
   - `team` = `<team_key>`
   - `label` = `need-spec-review`
   - `assignee` = `me`
   - `state` omitted (a ticket's status may stay `In Progress` after `/port:ship`
     stamped `need-spec-review` — filtering by state would drop those).

3. **Post-fetch status filter** (mirrors dispatcher §2.0): drop survivors whose
   `statusType` is `completed` / `canceled`, or whose `status` name is
   `In Review` / `Ready for QA`. These are past review scope.

4. **Sort**: priority `urgent > high > medium > low > none`, then `createdAt`
   ascending (oldest first).

5. **Empty case**: if the list is empty, print:
   `No need-spec-review tickets assigned to you on team <team_key>. Nothing to review.`
   STOP cleanly (exit zero).

6. **Print queue overview** — **emit ONE row per surviving ticket**. The two-row
   block below is a *format template*, not an output cap. If `<N>` is 7 you
   print 7 data rows; if `<N>` is 1 you print 1. Do NOT truncate the table to
   match the template's row count — that's the most common implementation slip
   on this step, and the AskUserQuestion in step 7 hides the missing rows behind
   its divider so the user only notices when they pick "Review all".

   ```
   Found <N> need-spec-review tickets on team <team_key>:

   | # | ticket          | title                       | priority |
   |---|-----------------|-----------------------------|----------|
   | 1 | [CAF-370](url1) | <truncated title, ≤60 chars>| high     |
   | 2 | [CAF-401](url2) | <truncated title, ≤60 chars>| medium   |
   ... (one row per ticket through row <N>)
   ```

   Columns:
   - `#` — 1-based row index matching position in `<queue>`.
   - `ticket` — markdown link `[<ticket-id>](<linear-url>)`. Claude Code
     renders markdown, so the user sees `CAF-370` as a clickable link
     without a separate url column blowing out the row width. Use the
     `url` field returned by `list_issues` for the link target.
   - `title` — Linear title, hard-truncated to 60 chars with `…` appended if
     trimmed. Single line only; collapse internal newlines to spaces.
   - `priority` — `urgent` / `high` / `medium` / `low` / `none`.

   The url is embedded in the ticket cell as a markdown link rather than
   broken out as a separate column — full Linear URLs as standalone column
   text blow out the row width and push the table off-screen in narrow
   terminals, hiding later rows behind the AskUserQuestion divider.

7. **Confirm gate**: `AskUserQuestion` with options:
   - `Review all <N> sequentially` (Recommended)
   - `Abort`

   On `Abort` → STOP with no side effects. No subset-selection here on purpose;
   to review a single ticket out of the batch, abort and re-run with the
   explicit single-mode form `/spec-review <ticket-id>`.

8. Set `<queue>` = ordered list of `ticket-id`s from steps 4–6.

The main loop (Step 2 onward) now iterates `<queue>` with `<k>` running from
1 to `<N>`. Per-ticket processing is identical to single mode; per-ticket
failure handling is documented inline in Steps 5 and 6.

### Step 2: Extract review items

A ticket's port-summary comment may surface review items in several shapes.
The current writers (`/port:revise` + `/port:ship`) emit **structured `ri:v1`
records**; older comments use the legacy shapes. Spec-review accepts all of
them and merges them into a single review list, **joining on the `id=`
attribute (never fuzzy-matching prose)**.

| Shape | Marker | Example | Severity / queue |
|-------|--------|---------|------------------|
| **Review item (`ri:v1`)** — primary | `<!-- ri:v1 id=X kind=K sev=S verify=V -->` | `<!-- ri:v1 id=AD-1 kind=empirical sev=medium verify=unconfirmed -->` | `sev` from marker; queue from `verify` (see §2.3a) |
| Auto-accept (v1) — legacy | `<!-- ac:v1 sev=X -->**[AUTO-ACCEPTED]** ...` | `<!-- ac:v1 sev=medium -->**[AUTO-ACCEPTED]** medium — Risk R-1 ...` | explicit from marker; review |
| Auto-accept (legacy) | `**[AUTO-ACCEPTED] X**` | `- **[AUTO-ACCEPTED] medium** — Risk R-1 ...` | explicit from marker; review |
| Assumption | `**A-N**` / `**AR-N**` / `**AD/AP/AG/AU-N**` under an Assumptions / `### Needs review` heading | `- **AD-1** — provider is already populated ...` | `low` (unless marker); review |
| Risk | `**R-N**` under `### Risks` | `- **R-1**: CAF-368 not yet built — Edit screen depends on ...` | `medium`; review |

#### Steps

1. Call `mcp__claude_ai_Linear__list_comments` with `orderBy: createdAt` (newest
   first). Page through if needed.
2. Walk comments newest → oldest. The first comment that contains a port
   summary (heuristic in §2.6) is the **target comment**. Parse it as below;
   stop iterating once parsed.
3. **Parse `ri:v1` records (PRIMARY path — current writers).**
   - Find all `<!-- ri:v1 id=(\S+) kind=(empirical|judgment|lint) sev=(low|medium|high) verify=(confirmed|refuted|unconfirmed|n/a) -->`.
   - For each marker, the `body` = the marker line's following block: the
     `- **<id>** — <Summary>` line plus its indented continuation lines
     (`Why` / `Impact` / `Evidence` / `Reality`), until the next `<!-- ri:v1`
     marker, the next section heading, or end of comment. Capture the **whole
     block verbatim** — this rich body is what makes the item understandable.
   - Capture per item: `id`, `kind`, `severity` (= `sev`), `verify`,
     `source` = `"ri"`, `parser_mode` = `"ri-v1"`.
   - `id` is the join key; never re-derive provenance by matching prose.

3a. **Queue routing for `ri:v1` items** (this is the value of the verify field):
   - `verify` ∈ {`confirmed`, `refuted`} → `queue = "fyi"`. These were settled
     against the code in `/port:explore`; they are an audit trail, NOT
     human-decision items. They are shown (Step 3 overview + recorded in the
     posted comment) but the Step 4 decision loop does NOT prompt on them.
   - `verify` ∈ {`unconfirmed`, `n/a`} → `queue = "review"`. Genuine
     human-decision items (judgment calls + empirical claims the pipeline
     could not settle). These flow into the Step 4 loop.
   - A `refuted` item's body carries `Reality:` — surface it prominently in
     the FYI section so the reviewer sees what the original guess got wrong.

3b. **Parse legacy auto-accepts** — ONLY if step 3 found zero `ri:v1` records
   in this comment (older comments predating the `ri:v1` rollout). Precedence
   v1 > legacy:
   - **ac:v1 path** — find all `<!-- ac:v1 sev=(low|medium|high) -->`. For each:
     - `severity` = HTML attribute value
     - `body` = the line containing the marker plus continuation lines (until
       next `- ` list item / blank line / section heading)
     - `source` = `"auto-accept"`, `parser_mode` = `"v1"`, `queue = "review"`
   - **Legacy fallback** — only if ac:v1 found nothing in this comment. Regex:
     ```
     \*\*\[AUTO-ACCEPTED\]\s+(low|medium|high)\*\*\s+—\s+(.+?)(?=\n- \*\*|\n###|\n##|\Z)
     ```
     `source` = `"auto-accept"`, `parser_mode` = `"legacy"`, `queue = "review"`.
4. **Parse Assumption items** — ONLY for items NOT already captured as `ri:v1`
   in step 3 (e.g. an old comment with bare `**A-N**` bullets, or a record that
   lost its marker). Join by ID against step-3 results to avoid double-counting.
   - Locate every section heading matching
     `^###\s+(Assumptions?|Needs review)\b` (e.g. `### Assumptions`,
     `### Assumptions (post-clarification)`, `### Needs review`).
   - Within the section body (until the next `^##` or `^###` heading), match
     list items shaped
     `- \*\*(A|AR|AD|AP|AG|AU)-\d+\*\*\s*(?:—|:)?\s*(.+?)(?=\n- \*\*|\n<!--|\n###|\n##|\Z)`.
     **The alternation EXTENDS the legacy `(A|AR)` — it must keep `A` so bare
     legacy `A-7` IDs still match.** The body may span multiple lines.
   - For each hit: `severity` = `low`, `source` = `"assumption"`,
     `queue = "review"`.
   - Sub-sections such as `**From dev-notes**:`, `**From pm-notes**:` inside
     the section are walked transparently — items are flattened.
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
   any of (case-sensitive): `<!-- ri:v1`, `### Needs review`,
   `### Verified (FYI)`, `## Port summary`, `## Synth summary`,
   `Authored by port:`, `[AUTO-ACCEPTED]`, or **both** of (`### Assumptions`
   AND `### Risks`).
7. Build `<items>: [{id, kind, severity, verify, queue, body, source, comment_id, parser_mode}]`
   (`id`/`kind`/`verify` are empty for legacy shapes that lack them; legacy
   items default `queue = "review"`).
   Deduplicate **by `id`** (the join key): if the same ID appears via more than
   one parser path (e.g. a `ri:v1` record AND a bare `**A-N**` fallback), keep
   the `ri:v1` capture (richest body + verdict). For legacy-only comments,
   fall back to the old rule: if the same `A-N`/`R-N` ID appears both inside an
   `[AUTO-ACCEPTED]` body AND as a standalone assumption/risk item, keep the
   auto-accept.
8. Compute `<source_hash[i]> = sha256(<items[i].body>)`. Keep first 16 hex chars.
9. **Cross-check (defends silent zero-acceptance)**:
   - Let `<review-items>` = items with `queue == "review"`,
     `<fyi-items>` = items with `queue == "fyi"`.
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
   - If `len(<review-items>) == 0` AND `len(<fyi-items>) > 0`:
     - Legitimate "all settled upstream" case — NOT a parse error. Print the
       FYI items (so the reviewer sees what was auto-verified), then proceed to
       the lite-mode flow (**Step 7**): confirm-as-is and record the FYI items.
   - If `len(items) == 0` AND `<port_summary_seen>` is `False`:
     - True lite mode. Skip to **Step 7** (lite-mode flow).
10. Print: `Found <N> review items (<R> need review, <F> verified-FYI) — ri:<r>, auto-accept:<a>, assumption:<b>, risk:<c> (parser: <ri-v1|v1|legacy|sections>).`
    - `parser_mode` on output = `ri-v1` if any `ri:v1` hit, else `v1` if any
      ac:v1 hit, else `legacy` if any legacy hit, else `sections`.
11. If any item has `parser_mode == "legacy"`, set `<legacy_warning>` = `True`
    (used in Step 5's posted comment). When `parser_mode == "ri-v1"`,
    `<legacy_warning>` stays `False` — the rich path needs no re-run hint.

### Step 3: Pre-HITL overview (mandatory when N ≥ 5; recommended otherwise)

Print a single overview message before any decision prompts. The overview has
two parts: the **review queue** (`queue == "review"` — what Step 4 walks) and a
collapsed **Verified (FYI)** list (`queue == "fyi"` — shown for transparency,
never prompted).

Sort the review queue by:
1. severity `high → medium → low`
2. `verify` priority `unconfirmed → n/a` (empirical-but-unsettled first — those
   are the items where code and assumption may still diverge), then
   source priority `auto-accept → risk → assumption`
3. source order within group

Format:

```
Found <N> review items — <R> need review, <F> verified upstream (FYI):

Review queue (<R>):
  [HIGH]  [unconfirmed] [empirical]  1. <first 80 chars of Summary>
  [MED]   [n/a]         [judgment]   2. <...>
  [MED]   [—]           [R]          3. <...>
  [LOW]   [—]           [A]          4. <...>

Verified upstream — no action needed (<F>):
  [✓ confirmed] AD-2 — <Summary>   (evidence: lib/…:42)
  [✗ refuted]   AD-5 — <Summary>   → Reality: <…>

Legend: queue verify=[unconfirmed|n/a]; source [AC]=auto-accept [A]=assumption [R]=risk; FYI verify=[confirmed|refuted]
```

For a `refuted` FYI item, always print its `Reality:` line — that is the
single most useful thing in the whole review (the pipeline caught a false
premise and corrected it). This forces the reviewer to see the shape of the
review before the per-item flow starts. No prompt at the end of this step —
just print and move on to step 4.

### Step 4: HITL decision loop

Iterate the **review queue only** (`<review-items>`, `queue == "review"`) in
the sorted order from step 3. FYI items (`queue == "fyi"`) are NEVER prompted —
they were settled against the code upstream and are recorded as-is in Step 5.

For each item:

1. Print the **full** body of the item (not truncated). Format:
   ```
   ─────────────────────────────────────────
   [<SEVERITY>] [<SOURCE>] [<KIND>/<VERIFY>] Item <i>/<R>
   <full body text — Summary + Why + Impact + Evidence + Reality>
   ─────────────────────────────────────────
   ```
   `<SOURCE>` is one of `AUTO-ACCEPT`, `ASSUMPTION`, `RISK`, `LINT`.
   `<KIND>/<VERIFY>` is e.g. `empirical/unconfirmed` or `judgment/n-a` (omit
   the bracket for legacy items that carry no kind/verify).
   - When `verify == "unconfirmed"`, append a one-line hint:
     `↳ empirical claim the pipeline could not settle against the code — confirm against external source before accepting.`

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

1. Re-fetch comments via `mcp__claude_ai_Linear__list_comments`.
2. Scan for any comment whose body contains `<!-- spec-review:v1 ticket=<id> -->`
   AND whose `createdAt` is **strictly newer** than `<batch-start-time>` (from
   Step 0). If found:
   - **Single mode** → STOP with: `Concurrent review detected at <ts> by <author>. Re-run /spec-review <ticket-id> to see the latest state and decide whether to re-review.`
   - **Batch mode** → mark this ticket `skipped (concurrent reviewer)` in the
     batch summary, print `[<k>/<N>] <ticket-id> skipped — concurrent reviewer at <ts>.`, and **continue to the next ticket**. The user's decisions in Step 4 are discarded for this ticket.
   - In either mode: do NOT post our comment.
3. Build the comment body using the schema in **§A**. Include:
   - Header HTML marker `<!-- spec-review:v1 ticket=<ticket-id> -->`
   - One block per decision (CONFIRMED / REVISED / DEFERRED) with severity,
     source, `Verify:` (the upstream verdict, if the item carried one), and
     source_hash
   - A `### [FYI] Verified upstream` block listing the `<fyi-items>` verbatim
     (id + Summary + verify + evidence/reality) — recorded so `/dev:apply` and
     any later reviewer can see what was settled against the code and not
     re-litigate it. Omit the block when there are no FYI items.
   - Footer: authoritative-for-/dev:apply note
   - If `<legacy_warning>` is True: prepend a one-line note inside the comment:
     `> Note: parsed legacy auto-accept marker format. Recommend re-running
     /port:ff after the v1 marker rollout.`
4. Call `mcp__claude_ai_Linear__save_comment` with `issueId: <ticket-id>` and
   `body: <built body>`.
5. On 5xx / network failure → retry once after a short pause. Second failure:
   - **Single mode** → STOP with: `Failed to post review comment after retry. Decisions are not persisted. Re-run /spec-review.`
   - **Batch mode** → mark this ticket `errored (comment post failed)` in the
     batch summary, print `[<k>/<N>] <ticket-id> errored — comment post failed after retry.`, and **continue to the next ticket**. The user's decisions for this ticket are lost; re-running /spec-review will re-present the same items.
6. On success, capture `<comment_id>` from the response.

### Step 6: Flip label

1. Compute new label set:
   - Remove `need-spec-review` if present.
   - Add `ready-to-dev`.
   - Preserve all other labels unchanged.
2. Call `mcp__claude_ai_Linear__save_issue` with `id: <ticket-id>` and
   `labels: <new label set>`.
3. On failure:
   - **Single mode** → print:
     ```
     Comment posted (id: <comment_id>) but label flip failed.
     Manual recovery: remove `need-spec-review`, add `ready-to-dev` on Linear.
     ```
     Exit non-zero. Do NOT roll back the comment.
   - **Batch mode** → print the same manual-recovery hint, mark this ticket
     `errored (label flip failed, comment posted)` in the batch summary, and
     **continue to the next ticket**. Do NOT roll back the comment, do NOT
     exit the loop.
4. On success, print:
   - **Single mode**:
     ```
     ✅ Spec review complete.
     Comment: <ticket-url>#comment-<comment_id>
     Label: need-spec-review → ready-to-dev
     ```
   - **Batch mode**: a one-line per-ticket success line for the live feed:
     `[<k>/<N>] <ticket-id> ✅ <a> confirmed, <b> revised, <c> deferred → ready-to-dev.`
     Continue to the next ticket. The fuller summary is emitted by Step 8 once
     the loop ends.

### Step 7: Lite mode

Reached in two cases: (a) no port summary exists at all (manually created
OpenSpec changes routed through `need-spec-review`); (b) a port summary exists
but every item was settled upstream — all `<fyi-items>`, zero `<review-items>`
(Step 2.9 second branch).

1. `AskUserQuestion`:
   - Question (case a): "No review items found in ticket comments. Confirm
     review with no revisions, or abort?"
   - Question (case b): "All <F> items were verified against the code upstream
     (shown above); nothing needs a human decision. Confirm as-is, or abort?"
   - Options: `Confirm — review approved as-is` / `Abort`
2. On Abort:
   - **Single mode** → STOP, no side effects.
   - **Batch mode** → mark this ticket `skipped (lite-mode aborted)` in the
     batch summary, print `[<k>/<N>] <ticket-id> skipped — lite-mode aborted.`,
     and **continue to the next ticket**.
3. On Confirm:
   - Post short comment. Case (a) body:
     ```
     <!-- spec-review:v1 ticket=<ticket-id> -->
     ## Spec Review — No revisions

     No review items were posted by upstream synthesis.
     Reviewer confirmed artifacts as-is at <ISO timestamp>.
     ```
   - Case (b) body: same header + `## Spec Review — No revisions`, plus the
     `### [FYI] Verified upstream` block (§A) listing the `<fyi-items>` verbatim
     so the audit trail persists.
   - Then proceed to Step 6 (flip label). Same retry / failure semantics.

### Step 8: Batch summary (batch mode only)

Runs once, after the main loop has iterated every ticket in `<queue>`. Skipped
entirely in single mode (Step 6's single-mode success block is the terminal
output there).

Aggregate per-ticket outcomes recorded across Steps 1, 5, 6, and 7 into one
final block. Bucket each ticket as exactly one of:

- `confirmed` — comment posted + label flipped (Step 6 single-mode success
  path, or the equivalent batch-mode `[k/N] ✅` line)
- `skipped` — concurrent reviewer race (Step 5.2 batch path), label flipped
  by someone else between fetch and loop iteration (Step 1.3 batch path), or
  lite-mode aborted (Step 7.2 batch path)
- `errored` — comment post failed after retry (Step 5.5 batch path) or label
  flip failed after comment posted (Step 6.3 batch path)

Print:

```
Batch complete (<N>/<N> processed):

  ✅ [CAF-370](url) — 5 confirmed, 1 revised, 0 deferred → ready-to-dev
  ⏭  [CAF-401](url) — skipped (concurrent reviewer at 2026-05-12T14:23:11Z)
  ⚠  [CAF-398](url) — errored (label flip failed; manual recovery needed)
  ✅ [CAF-412](url) — 0 confirmed, 0 revised, 0 deferred → ready-to-dev (lite mode)

Summary: <X> confirmed, <Y> skipped, <Z> errored.
```

Format the ticket id as a markdown link `[<ticket-id>](<linear-url>)` so the
user can click through directly from the summary to the Linear issue. Use the
`url` captured in Step 1.5 (batch mode) or Step 1.2 (single mode, if Step 8
were ever reached — currently it is not).

Trailing line is fixed-text, easy to grep from CI / shell wrappers. Exit zero
even when `<Z> > 0` — errored tickets are individually surfaced with manual
recovery hints already; the batch itself did not fail.

---

## §A — Output comment schema

The posted comment body MUST follow this exact structure. `/dev:apply` parses
it with regex anchored on the HTML marker and the bracketed verdict tags.

```markdown
<!-- spec-review:v1 ticket=<TICKET-ID> -->
## Spec Review — Final Decisions

### [CONFIRMED] <one-line label derived from item body>
- ID: <AD-1|AP-1|...|A-1|—>
- Severity: <low|medium|high>
- Source: <auto-accept|assumption|risk|lint>
- Verify: <confirmed|refuted|unconfirmed|n/a|—>
- Verdict: Accept as-is
- Source hash: `sha256:<16 hex>`

### [REVISED] <one-line label>
- ID: <id|—>
- Severity: <low|medium|high>
- Source: <auto-accept|assumption|risk|lint>
- Verify: <confirmed|refuted|unconfirmed|n/a|—>
- Verdict: Reject — needs change
- Original: <one-line summary of original item body, ≤120 chars>
- Directive: <user-written replacement directive, full text>
- Source hash: `sha256:<16 hex>`

### [DEFERRED] <one-line label>
- ID: <id|—>
- Severity: <low|medium|high>
- Source: <auto-accept|assumption|risk|lint>
- Verify: <confirmed|refuted|unconfirmed|n/a|—>
- Verdict: Defer — note only
- Note: <user-written note, may be empty>
- Source hash: `sha256:<16 hex>`

### [FYI] Verified upstream (no decision required)
<one bullet per fyi-item, verbatim — omit this whole heading if none>
- `<id>` (<verify>) — <Summary>  ·  Evidence: <path:line | none>  ·  Reality: <… | n/a>

---

**Reviewer**: <Linear-display-name-of-current-user> at <ISO timestamp>
**Authoritative for `/dev:apply`. `[REVISED]` directives override conflicting
guidance in OpenSpec artifacts. If `source_hash` mismatches the current
item body, re-review is required.**

**Precedence hint for `/dev:apply`**:
- `Verify: refuted` directives — HARD override. The pipeline verified against
  the code that the original premise is false; the `Reality:` / directive is
  ground truth, not a suggestion.
- `Source: auto-accept` directives — firm, override artifacts unconditionally.
- `Source: risk` directives — firm, treat as mitigation requirements.
- `Source: assumption` directives — strong guidance; artifacts may still
  contradict if the implementation reveals the assumption is false. Document
  the divergence in commit message if so.
- `[FYI] Verified upstream` items — already settled against the code; treat as
  established facts. Do NOT re-open them.
```

### Schema rules

- Severity / Source / Verdict / Source hash are required on every decision block.
  `ID` and `Verify` are required when the source item carried them (`ri:v1`
  items always do); use `—` for legacy items that lacked them.
- "One-line label" is the item's `Summary` (the `- **<id>** — <Summary>` text)
  when present; otherwise the first 60 chars of the item body, trimmed at a
  word boundary, no trailing punctuation. For assumption / risk items without a
  Summary, prefix the label with the original ID: `A-1: <label>` / `R-1: <label>`.
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
| Comment has `ri:v1` records (current writers) | 2.3 | Primary path; join by `id`, route by `verify` |
| `ri:v1` item `verify=confirmed`/`refuted` | 2.3a | `queue=fyi` — shown, recorded, NOT prompted |
| `ri:v1` item `verify=unconfirmed`/`n/a` | 2.3a | `queue=review` — walked in Step 4 |
| All items are FYI, zero review items | 2.9 | Not a parse error; print FYI + lite-confirm (Step 7) |
| Old comment, no `ri:v1` (legacy `[AUTO-ACCEPTED]` / bare `A-N`) | 2.3b-2.4 | Legacy fallback paths; regex keeps `A` so still parses |
| Port summary has only `### Assumptions` / `### Risks` (no markers) | 2.4-2.5 | Reviewed with default severities (A=low, R=medium) |
| Port summary exists, 0 items of any shape parsed | 2.9 | Parse error abort (default) |
| Risk item marked `out of scope` / `tracked in .port` | 2.5 | Skipped as informational |
| Same ID appears via `ri:v1` AND a legacy fallback path | 2.7 | Dedup by `id`, keep the `ri:v1` capture |
| Same `A-N`/`R-N` ID in both auto-accept and section (legacy) | 2.7 | Dedup, keep auto-accept |
| Reviewer can't articulate Reject directive | 4.4 | Fall back to Defer with note |
| Reviewer chooses "Other" verdict | 4.6 | Reroute to Reject + confirm loop |
| Concurrent reviewer race (single) | 5.2 | Abort, ask user to re-run |
| Concurrent reviewer race (batch) | 5.2 | Skip ticket, continue batch, mark in summary |
| Linear 5xx on comment post (single) | 5.5 | One retry, then abort cleanly |
| Linear 5xx on comment post (batch) | 5.5 | One retry, then skip ticket + continue + mark errored |
| Linear 5xx on label flip (single) | 6.3 | Print comment id, exit non-zero |
| Linear 5xx on label flip (batch) | 6.3 | Print recovery hint, continue batch, mark errored |
| Older `spec-review:v1` comment exists | 5 | Allowed (intentional re-review), latest wins |
| Item body has backticks / fences | 2.8 | Hash raw text, do not re-render |
| User aborts mid-loop | any | No side effects; restart from top. Batch mode auto-skips already-flipped tickets on the next run. |
| Batch mode profile unresolvable | 1.5 | STOP with profile-config hint before any Linear call |
| Batch mode finds zero tickets | 1.5 | STOP cleanly, no side effects |
| Ticket flipped by concurrent reviewer between Step 1.5 fetch and loop iteration | 1.3 | Skip ticket, continue batch, mark in summary |

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
