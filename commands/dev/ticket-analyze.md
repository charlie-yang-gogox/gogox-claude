---
name: ticket-analyze
description: >
  Upstream ticket analyzer — the automated replacement for the manual
  "human marks ready" step that feeds `/ggx-dispatcher`. Two invocation
  shapes: pass a ticket id to analyze just that one, or call with no args
  to sweep every **To-Do** ticket **assigned to me** on the active team.
  Per ticket it judges content completeness against the lane's pipeline
  needs (port / feature / bug), builds a dependency graph from explicit
  relations (Linear `.relations[]`, Jira `issuelinks`) plus LLM content
  inference, computes a topological implementation order and the best
  starting ticket, then writes the outcome back: complete + unblocked →
  `ready-to-port` / `ready-to-dev`; incomplete → `need-revision` +
  reasoned comment; complete but blocked → `need-dependency` + blocker
  comment. Writes state ONLY — never invokes downstream pipelines;
  `/ggx-dispatcher` / `/ggx-work` pick up from the labels. Jira runs in
  degraded mode (comment + `fields.labels` string labels, no workflow
  labels). Supports both trackers via `_ticket-lib.md`.
Prerequisite: >
  - Linear MCP (or Atlassian MCP for Jira tickets) authenticated.
  - Batch mode requires the active repo to have a resolvable gogox project
    profile (.gogox-claude.yaml or registry entry) so the team key for
    `list_issues` can be derived.
---

# `/ticket-analyze [ticket-id]`

Analyze To-Do tickets assigned to me for **pipeline readiness**: is the
content sufficient for the port / feature / bug workflow, and is the ticket
blocked by another ticket? Persist the verdict as workflow labels + a
structured comment so the existing dispatcher flow (`/ggx-dispatcher` →
`/ggx-work` → `/route`) picks up ready tickets with zero extra steps.

**Usage**:

- `/ticket-analyze` — **batch mode**. Sweep every To-Do ticket assigned to
  me on the active project's team, analyze cross-ticket dependencies, and
  write per-ticket verdicts. Per-ticket failures do NOT abort the batch.
- `/ticket-analyze <ticket-id>` — **single mode**. Analyze just this one
  ticket. Dependency edges to other tickets are still detected and
  resolved against their live status, but no batch-wide ordering is
  computed.
- `--dry-run` — full analysis + report with a `would-write` column; no
  comments, no label writes.
- `--non-interactive` — never prompt. Confirm gates are skipped; inferred
  dependencies are recorded but never treated as blocking (see Step 4).
  For unattended / scripted callers.
- `--team:<KEY>` — batch mode only; required when the repo's
  `branch_prefix` is `auto` (validated against `org.yaml`, same contract
  as `/ggx-dispatcher` Step 0).

**Verdict → label decision matrix** (the contract everything below serves):

| completeness | blocked? | lane | → label written |
|---|---|---|---|
| incomplete (incl. missing/ambiguous classification) | any | any | `need-revision` |
| complete | blocked | any | `need-dependency` |
| complete | unblocked | port | `ready-to-port` |
| complete | unblocked | feature / bug | `ready-to-dev` |

The four analyzer-owned labels (`ready-to-port`, `ready-to-dev`,
`need-revision`, `need-dependency`) are **mutually exclusive** — every
label write is a full-set rewrite that removes the other three (mirrors
the dispatcher §4.1 swap pattern). `bug`-lane tickets get `ready-to-dev`;
`/route` derives the bug pipeline from the classification label downstream.

**Re-run semantics** (how tickets flow through repeated runs):

| Ticket's current analyzer label | Next run |
|---|---|
| none (fresh) | analyzed |
| `need-revision` | re-analyzed — completeness re-judged from current content; may flip to `ready-to-*` / `need-dependency` |
| `need-dependency` | re-analyzed — every blocker's live status re-fetched; all blockers Done → flips to `ready-to-*` |
| `ready-to-port` / `ready-to-dev` | skipped (already actionable; re-analyzing races the dispatcher) |
| `need-spec-review` / `dispatcher-*-in-flight` | skipped (already inside a pipeline) |

---

## Steps

### Step 0: Capture batch-start timestamp

Capture an ISO-8601 timestamp `<batch-start-time>` immediately. Used in
Step 8 to detect concurrent analyzers / dispatcher locks. In batch mode the
timestamp is shared across all tickets in the run.

### Step 1: Parse input — single vs batch mode

1. Strip flags from `$ARGUMENTS` first: `--dry-run`, `--non-interactive`,
   `--team:<KEY>`. Unknown flags → STOP with the usage block.
2. Extract `<ticket-id>` from what remains. Two paths:
   - **Present** → `<batch-mode> = False`. `<queue> = [<ticket-id>]`.
     Resolve `ticket_system` for this id per the `_ticket-lib.md`
     resolution block (replicate it — do not assume an upstream caller
     resolved). `unknown` → STOP. Skip to Step 2.
   - **Absent** → `<batch-mode> = True`. Proceed to Step 1.5 to populate
     `<queue>`. Do NOT prompt for a ticket id — batch is intentional.

### Step 1.5: Batch fetch (batch mode only)

1. **Resolve project profile** per `_ticket-lib.md` to obtain
   `ticket_system` and `<team_key>`:
   - `<repo-root>/.gogox-claude.yaml`, else the registry entry
     `~/.claude/commands/profiles/registry/$(basename "$(git rev-parse --show-toplevel)").yaml`.
   - Neither resolves → STOP with:
     ```
     Cannot resolve gogox project profile for the active repo. Batch mode
     needs a team key to query the tracker. Either add a registry entry or
     place .gogox-claude.yaml in the repo root, then re-run /ticket-analyze.
     ```
   - `branch_prefix` concrete → `team_key = branch_prefix` (a passed
     `--team:<KEY>` must match, case-insensitive, else STOP).
   - `branch_prefix == auto` → `--team:<KEY>` required; validate against
     the known prefixes in `org.yaml`; unknown → STOP. (Same contract as
     `/ggx-dispatcher` Step 0.3.)

2. **Jira degraded-mode gate**: if `ticket_system == jira`, print once:
   ```
   Jira degraded mode: analysis runs, but verdicts are recorded via comment
   + Jira string labels (fields.labels) only. No workflow labels, no
   dispatcher integration (Linear-only per _ticket-lib parity table).
   ```
   and continue. If `ticket_system == unknown` → STOP (never default to
   Linear silently).

3. **Resolve the To-Do state name** — do NOT hardcode it:
   - Linear: `mcp__claude_ai_Linear__list_issue_statuses` for
     `<team_key>`; pick the status whose `type == unstarted` and whose
     name matches `To-Do` / `Todo` / `To Do` (case-insensitive). Multiple
     `unstarted` states (e.g. `Backlog` + `Todo`) → pick the To-Do-named
     one; Backlog is intentionally out of scope.
   - Jira: target status name `To Do` (post-filter on
     `.fields.status.name` after a JQL search
     `assignee = currentUser() AND project = <team_key> AND statusCategory = "To Do"`
     via `mcp__claude_ai_Atlassian_Rovo__searchJiraIssuesUsingJql`).

4. **List tickets**:
   - Linear: `mcp__claude_ai_Linear__list_issues` with `team =
     <team_key>`, `assignee = me`, `state = <resolved To-Do name>`.
   - Jira: the JQL search above.

5. **Re-analysis post-filter** — apply to the fetched set, printing one
   skip line per excluded ticket:
   - **Keep**: tickets with none of the analyzer labels (fresh), and
     tickets carrying `need-revision` or `need-dependency` (re-evaluate —
     content may have been enriched / blockers may have closed).
   - **Skip**: tickets carrying `ready-to-port` / `ready-to-dev`
     (`skipped — already actionable`), `need-spec-review` or
     `dispatcher-*-in-flight` (`skipped — in pipeline`).
   - Jira: the analyzer's own string labels
     (`ticket-analysis-ready`) take the place of `ready-to-*` in this
     filter; pipeline labels don't exist on Jira so only the ready filter
     applies.

6. **Sort**: priority `urgent > high > medium > low > none`, then
   `createdAt` ascending.

7. **Empty case**: print
   `No analyzable To-Do tickets assigned to you on team <team_key>.` and
   STOP cleanly (exit zero).

8. **Print queue overview** — ONE row per surviving ticket (the template
   below is a format, not an output cap; never truncate the table):
   ```
   Found <N> To-Do tickets on team <team_key> (<F> fresh, <R> re-analysis):

   | # | ticket          | title                        | priority | current label |
   |---|-----------------|------------------------------|----------|---------------|
   | 1 | [CAF-370](url1) | <truncated title, ≤60 chars> | high     | —             |
   | 2 | [CAF-401](url2) | <truncated title, ≤60 chars> | medium   | need-revision |
   ... (one row per ticket through row <N>)
   ```
   Ticket cell is a markdown link (no separate url column — full URLs blow
   out row width in narrow terminals).

9. **Confirm gate** (skipped when `--non-interactive` — proceed without
   prompting): `AskUserQuestion` with options
   `Analyze all <N>` (Recommended) / `Abort`. On `Abort` → STOP with no
   side effects.

10. Set `<queue>` = ordered ticket-id list.

### Step 2: Fetch full ticket data + explicit relations

For every ticket in `<queue>` (single mode: the one ticket), fetch via the
`_ticket-lib.md` `get_ticket` branch and capture using the logical field
names from its field-mapping table:

- `<title>`, `<description>`, `<url>`, `<status_name>`, `<labels>`,
  `<priority>`, `<createdAt>`
- `<lane>` — derive per the `_ticket-lib.md` lane table: Linear
  classification label ∈ `{bug, port, feature}` (exactly one → that lane;
  zero or multiple → `unknown`); Jira `fields.issuetype.name` (`Bug` →
  bug, story/task family → feature; no port lane).
- **Explicit relations** — first command in the repo to read these:
  - Linear: `.relations[]` from `get_issue` — capture
    `type ∈ {blocks, blocked_by, related, duplicate}` + related issue id.
  - Jira: `.fields.issuelinks[]` — capture `type.name` +
    `inwardIssue`/`outwardIssue` key, normalizing Jira's inward/outward
    phrasing ("is blocked by" / "blocks") onto the same kinds.
  - Normalize every relation into an edge record:
    `{from, to, kind: blocks|blocked-by|related, source: explicit}`.
    Only `blocks`/`blocked-by` kinds can ever block; `related`/`duplicate`
    are recorded in the comment but never affect the verdict.

Print one line per ticket:
`[<k>/<N>] Fetched <ticket-id> "<title>" — lane: <lane>, relations: <n>`

### Step 3: Per-ticket completeness analysis

For each ticket, judge the content (title + description) against the
lane's checklist. This is an LLM judgment call — be strict about the
*presence* of each element, lenient about its format. Emit
`{verdict: complete|incomplete, reasons: [missing items with a one-line
explanation each]}`.

**Lane checklists**:

- **port** — sufficient for `/port:ff`:
  - [ ] Origin feature reference — names the source feature/screens to
        port (what `/port:explore` will locate in the origin codebase).
  - [ ] Scope statement — what's in and out of this port.
  - [ ] Figma URL — required only when the description implies UI work.
- **feature** — sufficient for `/dev:ff`:
  - [ ] Goal / problem statement — why this exists.
  - [ ] ≥ 1 acceptance criterion (explicit AC list, or testable "done
        when" statements).
  - [ ] Figma URL — required only when the description implies UI work.
- **bug** — sufficient for `/bug:ff`:
  - [ ] Reproduction steps.
  - [ ] Expected vs actual behavior.
  - [ ] Environment / build / version (or an explicit "all versions").
- **all lanes**:
  - [ ] Resolvable lane. Linear: exactly one classification label —
        missing or multiple is itself a revision reason
        (`no single classification label — add exactly one of bug/port/feature`),
        NOT a HITL prompt (keeps the batch flowing; contrast `/route`,
        which prompts because it must pick a pipeline *now*).
        Jira: unrecognized `issuetype.name` → same treatment.

  **Lane suggestion (strong signal only)**: when the classification is
  missing, append a suggested lane to the revision reason — but ONLY when
  the ticket text carries an unambiguous signal:
  - repro steps + expected-vs-actual present → suggest `bug`, citing the
    signal: `Content suggests \`bug\` (has repro steps + expected vs actual)`
  - explicit port language in the ticket itself ("port from", "same as
    v1", "align with the native app", etc.) → suggest `port`, quoting the
    phrase.
  - **anything else → NO suggestion** — in particular, port-vs-feature is
    undecidable from ticket text alone (whether the feature exists in the
    origin codebase is not in the ticket, and this analyzer never reads
    code). A coin-flip suggestion is worse than none: the ticket author
    will trust it. Emit the plain base sentence only.

  The suggestion is comment text only. It is NEVER written as a label —
  classification stays human-owned (§C), and the human applying the label
  is the confirmation step.

Figma is conditionally required so non-UI tickets are not falsely flagged.
When in doubt whether UI is implied, do not flag — a missing Figma link
surfaces again at `/dev:figma` with a better error.

### Step 4: Dependency inference + confirmation

1. **Inferred edges**: scan each ticket's title + description for
   references to other tickets that read as ordering constraints —
   `depends on CAF-368`, `blocked by DET-12`, `after CAF-212 ships`,
   `needs the Edit screen (CAF-368) first`, etc. A bare ticket-id mention
   without ordering language is `related`, not blocking. For each hit emit
   `{from, to, kind, source: inferred, evidence: "<quoted phrase>"}`.

2. **Inferred-edge confirmation gate**:
   - **Default mode**: ONE batched `AskUserQuestion` listing every
     inferred blocking edge (`<from> → <to> · "<evidence>"`), with
     `multiSelect` so the user picks which to confirm as blocking.
     Confirmed → `source: inferred-confirmed`, treated as blocking.
     Unconfirmed → kept in the comment as `INFERRED/UNCONFIRMED`, never
     blocking. No inferred edges → no prompt.
   - **`--non-interactive`**: never prompt; ALL inferred edges are
     report-only (recorded, never blocking). Conservative on purpose — a
     false-positive inference must not silently park a ticket in
     `need-dependency`; the comment still surfaces it for a human.

3. **External-target resolution**: for every blocking edge (explicit or
   inferred-confirmed) whose target is outside `<queue>`, fetch the
   target's live status via the `_ticket-lib.md` `get_ticket` branch:
   - target `statusType ∈ {completed, canceled}` (Jira: status category
     Done) → edge **satisfied**, not blocking.
   - target open → edge **blocking**.
   Cache per target id — fetch once per run.

### Step 5: Graph, order, best starting ticket

Build a directed graph over `<queue>` using **blocking edges only**
(explicit + inferred-confirmed, with open targets).

1. **Blocked** = a ticket with ≥ 1 inbound blocking edge from an open
   ticket (in-queue or external).
2. **Cycle detection**: a cycle among queue tickets → every member is
   flagged blocked with reason `circular dependency: <id ↔ id>`, the
   cycle is excluded from the order, and a prominent `⚠ CYCLE` warning is
   printed. Do not crash, do not pick an arbitrary winner — humans break
   cycles.
3. **Implementation order**: topological sort of the unblocked-reachable
   subgraph; ties broken by priority then `createdAt` asc.
4. **Best starting ticket** = first `complete` ticket in the order with
   zero inbound blocking edges. Marked in the report and in its own
   comment (`Recommended starting point for this batch`). Single mode:
   skip ordering; blocked-or-not is still computed.

### Step 6: Verdict

Combine Step 3 + Step 5 per ticket through the decision matrix (top of
file) → `{verdict, target_label, reasons, blockers, order_position}`.

### Step 7: Dry-run gate

If `--dry-run`: print the full Step 9 report with a `would-write` column
in place of `label written`, then STOP. No comments, no label writes, no
label creation.

### Step 8: Write state per ticket

Iterate `<queue>` in order. All writes are read-before-write.

1. **Ensure labels exist** (Linear, once per run, before the first
   write): `mcp__claude_ai_Linear__list_issue_labels` for the team; if
   `need-revision` / `need-dependency` are missing, create them via
   `mcp__claude_ai_Linear__create_issue_label`
   (`need-revision` color `#F2994A`, `need-dependency` color `#EB5757` —
   any visible color is fine). Creation fails (permissions) → degrade:
   comments still post, print
   `Label create failed — apply <label> manually on: <ticket list>`, mark
   affected tickets `errored (label create failed)`, continue.

2. **Pre-write concurrency check**: re-fetch the ticket's comments +
   labels. If a `ticket-analysis:v1` comment by another author is newer
   than `<batch-start-time>`, OR the ticket now carries
   `dispatcher-*-in-flight` (dispatcher locked it mid-analysis):
   - **Single mode** → STOP:
     `Concurrent actor detected on <ticket-id> (<which signal>). Re-run /ticket-analyze <ticket-id> to see the latest state.`
   - **Batch mode** → print
     `[<k>/<N>] <ticket-id> skipped — concurrent actor (<which signal>).`,
     mark `skipped`, continue.

3. **Post comment** via the `_ticket-lib.md` `save_comment` branch using
   the §A schema. Append-only — a fresh comment each run; the newest
   `ticket-analysis:v1` comment is authoritative on read.

4. **Label write**:
   - **Linear**: compute the new set = current labels − the other three
     analyzer-owned labels + `<target_label>`, preserving everything else;
     `mcp__claude_ai_Linear__save_issue --labels <new set>`. Already at
     target → skip the write, log `labels: already at target`.
   - **Jira**: skip workflow labels entirely (parity table). Write string
     labels instead: `fields.labels` = current −
     `{ticket-analysis-ready, ticket-analysis-need-revision, ticket-analysis-need-dependency}`
     + the one matching the verdict, via
     `mcp__claude_ai_Atlassian_Rovo__editJiraIssue`. Log one line:
     `workflow labels: skipped (jira) — wrote fields.labels instead`.

5. **Failure handling**: on 5xx / network failure retry once after a
   short pause (comment and label independently). Second failure:
   - **Single mode** → STOP with the verbatim error and what was / wasn't
     persisted.
   - **Batch mode** → mark `errored (<comment post|label write> failed)`,
     print the per-ticket line with a manual-recovery hint, continue. Do
     NOT roll back a posted comment when the label write fails.

6. Per-ticket success line:
   `[<k>/<N>] <ticket-id> ✅ <verdict> → <label>` (+ ` · recommended start`
   where applicable).

### Step 9: Report

Always printed (the human-facing deliverable), after the write loop (or at
Step 7 for dry-run). Two parts:

```
Batch analysis — team <KEY>, <N> tickets (<C> ready, <I> need revision, <B> blocked)

Implementation order:
  1. [CAF-212](url)  ← recommended start
  2. [CAF-198](url)  (after CAF-212)
  ...
Blocked (excluded from order):
  [CAF-370](url)  blocked by CAF-368 (open)   [explicit]
  [CAF-401](url)  blocked by CAF-212 (open)   [inferred-confirmed]
⚠ CYCLE: CAF-5 ↔ CAF-6   (omit line when none)

| # | ticket          | lane    | verdict             | label written   | blockers | reasons                |
|---|-----------------|---------|---------------------|-----------------|----------|------------------------|
| 1 | [CAF-212](url)  | feature | complete/unblocked  | ready-to-dev    | —        | —                      |
| 2 | [CAF-370](url)  | port    | complete/blocked    | need-dependency | CAF-368  | —                      |
| 3 | [CAF-401](url)  | bug     | incomplete          | need-revision   | —        | missing repro steps    |
```

One row per ticket, never truncated. Inferred-unconfirmed edges appear in
a trailing note (`Unconfirmed (not blocking): CAF-401 → CAF-212 "…"`), not
in the blockers column.

### Step 10: Batch summary (batch mode only)

Bucket each ticket as exactly one of `analyzed` (comment + label landed) /
`skipped` (concurrent actor, or excluded at Step 1.5) / `errored` (write
failure). Print the fixed-text trailing line, easy to grep:

```
Summary: <X> analyzed, <Y> skipped, <Z> errored.
```

Exit zero even when `<Z> > 0` — errored tickets carry their own recovery
hints; the batch itself did not fail.

---

## §A — Output comment schema

```markdown
<!-- ticket-analysis:v1 ticket=<TICKET-ID> verdict=<complete-unblocked|complete-blocked|incomplete> lane=<port|feature|bug|unknown> -->
## Ticket Analysis

**Verdict**: <Ready (ready-to-dev) | Ready (ready-to-port) | Blocked (need-dependency) | Needs revision (need-revision)>
**Lane**: <lane> · **Order position**: <n of N | blocked | —> <· Recommended starting point>

### Completeness
- ✓ <checklist item present>
- ✗ <checklist item missing> — <one-line reason / what to add>

### Dependencies
<!-- ta-dep:v1 to=<ID> kind=<blocks|blocked-by|related> source=<explicit|inferred> confirmed=<true|false> status=<open|done> -->
- BLOCKING — blocked by CAF-368 (open) · explicit relation
- INFERRED/UNCONFIRMED — "depends on CAF-212" (description) · not treated as blocking
(omit section when no edges)

---
**Analyzed** by /ticket-analyze at <ISO timestamp>. Downstream:
/ggx-dispatcher and /ggx-work read the workflow label; this analyzer does
not invoke them.
```

Schema rules:

- The header marker is the idempotency / concurrency key — copy exactly.
- One `ta-dep:v1` marker line immediately above each dependency bullet;
  `to=` is the join key for any future machine reader.
- The Completeness ✗ bullets are the user-facing revision checklist — be
  specific enough that the ticket author can fix the ticket without
  reading this skill.
- Jira: same body; it is the primary record there (string labels are only
  a filterable index).

## §B — Edge case reference

| Scenario | Step | Behavior |
|----------|------|----------|
| `ticket_system == unknown` | 1 / 1.5 | STOP — never default to Linear |
| Jira repo / ticket | 1.5 / 8.4 | Degraded mode: comment + `fields.labels` strings, no workflow labels |
| No To-Do status resolvable on team | 1.5.3 | STOP with the team's actual status list printed |
| Ticket already `ready-to-*` | 1.5.5 | Skipped — re-analysis would race the dispatcher |
| Ticket in pipeline (`need-spec-review`, `dispatcher-*-in-flight`) | 1.5.5 | Skipped |
| `need-revision` / `need-dependency` ticket | 1.5.5 | Re-analyzed (the revise → ready loop) |
| Missing/multiple classification labels | 3 | Revision reason, not a prompt |
| Missing classification, strong lane signal in text | 3 | Reason carries a suggested lane + evidence (comment text only, never a label write) |
| Missing classification, weak/ambiguous signal (esp. port vs feature) | 3 | No suggestion — plain base sentence only |
| Bare ticket-id mention, no ordering language | 4.1 | `related` edge — recorded, never blocking |
| Inferred edge, `--non-interactive` | 4.2 | Report-only, never blocking |
| Blocking edge to a Done/canceled ticket | 4.3 | Satisfied — not blocking |
| Cycle among queue tickets | 5.2 | All members blocked w/ cycle reason; loud warning; no crash |
| Dispatcher locks a ticket mid-run | 8.2 | Pre-write re-check skips it |
| Newer foreign `ticket-analysis:v1` comment | 8.2 | Skip (batch) / STOP (single) |
| `need-revision` label missing on team | 8.1 | Auto-create; on failure post comment + manual hint |
| Label already at target | 8.4 | No-op write, logged |
| 5xx on comment or label | 8.5 | Retry once; then errored + continue (batch) / STOP (single) |
| Zero tickets after filters | 1.5.7 | STOP cleanly, exit zero |
| `--dry-run` | 7 | Full report, zero writes of any kind |

## §C — Non-goals (do not extend)

- Do NOT invoke `/ggx-work`, `/route`, or any pipeline — labels are the
  only handoff.
- Do NOT write classification labels (`bug` / `port` / `feature`) — those
  are human-owned (`/ggx-dispatcher` ownership table).
- Do NOT write `dispatcher-*-in-flight` or `need-spec-review` — other
  writers own those.
- Do NOT create Linear issue relations from inferred dependencies — record
  them in the comment; humans promote them to real relations.
- Do NOT transition ticket status or change assignee.
- Do NOT touch the filesystem / git — pure tracker-side analysis.
- Do NOT support resume / state files — restart-on-interrupt re-derives
  everything from live tracker state.
