---
name: ticket-analyze
description: >
  Upstream ticket analyzer — the automated replacement for the manual
  "human marks ready" step that feeds `/ggx-dispatcher`. Two invocation
  shapes: pass a ticket id to analyze just that one, or call with no args
  to sweep every **To-Do** ticket **assigned to me** on the active team.
  Per ticket it judges content completeness against the lane's pipeline
  needs (port / feature / bug / ui-tweak), builds a dependency graph from explicit
  relations (Linear `.relations[]`, Jira `issuelinks`) plus LLM content
  inference, computes a topological implementation order and the best
  starting ticket, then writes the outcome back: complete + unblocked →
  `ready-to-port` / `ready-to-dev`; incomplete → `need-revision` +
  reasoned comment; complete but blocked → `need-dependency` + blocker
  comment. Writes state ONLY — never invokes downstream pipelines;
  `/ggx-dispatcher` / `/ggx-work` pick up from the labels. Jira runs in
  degraded mode (comment + `fields.labels` string labels, no workflow
  labels). Supports both trackers via `_ticket-lib.md`. The optional
  `--triage` flag adds a human-confirmed Phase 0 intake pass that classifies
  untriaged pool tickets (and can pull chosen ones into the sweep) — the only
  path on which the analyzer writes a classification label / assigns, always
  behind a per-ticket confirm.
Prerequisite: >
  - Linear MCP (or Atlassian MCP for Jira tickets) authenticated.
  - Batch mode requires the active repo to have a resolvable gogox project
    profile (.gogox-claude.yaml or registry entry) so the team key for
    `list_issues` can be derived.
---

# `/ticket-analyze [ticket-id]`

Analyze To-Do tickets assigned to me for **pipeline readiness**: is the
content sufficient for the port / feature / bug / ui-tweak workflow, and is
the ticket blocked by another ticket? Persist the verdict as workflow labels + a
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
- `--triage` — batch mode only. Run a **Phase 0 intake pass** (Step 1.6)
  BEFORE the normal sweep: classify untriaged pool tickets (no
  classification label → invisible to `/route` and every sweep) with
  per-ticket **human confirmation**, optionally pulling chosen ones into
  this session's analyze queue. Always interactive — incompatible with
  `--non-interactive` and with single mode. This is the ONLY path on which
  the analyzer writes a classification label / changes assignee+status, and
  only after an explicit per-ticket confirm (§C carve-out).

**Verdict → label decision matrix** (the contract everything below serves):

| completeness | blocked? | lane | → label written |
|---|---|---|---|
| incomplete (incl. missing/ambiguous classification) | any | any | `need-revision` |
| complete | blocked | any | `need-dependency` |
| complete | unblocked | port | `ready-to-port` |
| complete | unblocked | feature / bug / ui-tweak | `ready-to-dev` |

The four analyzer-owned labels (`ready-to-port`, `ready-to-dev`,
`need-revision`, `need-dependency`) are **mutually exclusive** — every
label write is a full-set rewrite that removes the other three (mirrors
the dispatcher §4.1 swap pattern). `bug`- and `ui-tweak`-lane tickets get
`ready-to-dev` (no new workflow label for ui-tweak — the dispatcher sweep
stays two-label); `/route` derives the bug / ui-tweak pipeline from the
classification label downstream.

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
   `--team:<KEY>`, `--triage`. Unknown flags → STOP with the usage block.
   - `--triage` is **batch-only and interactive-only**: if a `<ticket-id>`
     remains after stripping, OR `--non-interactive` is also set → STOP with
     the usage block (`--triage` is a human-confirmed pool sweep, not a
     single-ticket or unattended path). These two STOPs are also what keep
     the cloud routine safe — it invokes with no args, so never `--triage`.
2. Extract `<ticket-id>` from what remains. Two paths:
   - **Present** → `<batch-mode> = False`. `<queue> = [<ticket-id>]`.
     Resolve `ticket_system` for this id per the `_ticket-lib.md`
     resolution block (replicate it — do not assume an upstream caller
     resolved). `unknown` → STOP. Skip to Step 2.
   - **Absent** → `<batch-mode> = True`. Proceed to Step 1.5 to populate
     `<queue>`. Do NOT prompt for a ticket id — batch is intentional.

### Step 1.5: Batch fetch (batch mode only)

1. **Resolve project profile** per `_ticket-lib.md` to obtain
   `ticket_system`, `<team_key>`, and `<platform>` (the latter feeds the
   Step 3 platform overlay):
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
   STOP cleanly (exit zero) — **UNLESS `--triage` is set**, in which case
   continue to Step 1.6 with an empty base `<queue>` (the triage pass may
   pull pool tickets into it; a triage run with no To-Do backlog is normal).

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

### Step 1.6: Triage intake pass (`--triage` only)

Runs ONLY when `--triage` is set; otherwise skipped entirely (no behavior
change for any existing invocation — AC1). This is the loop's **intake
stage**: untriaged pool tickets carry no classification label, so `/route`
returns `UNKNOWN_LANE` and every sweep ignores them — intake starvation.
This pass gives them a human-confirmed path IN. It runs after Step 1.5 so it
reuses that profile / team / To-Do resolution; conceptually it is "Phase 0".

Preconditions were enforced at Step 1.1 (batch-only, interactive-only).
One more gate here: `ticket_system == jira` → print
`Triage intake is Linear-only in v1; skipping Phase 0.` and continue to
Step 2 unchanged (do NOT STOP — triage is additive).

1. **Fetch the untriaged pool.** `mcp__claude_ai_Linear__list_issues` for
   `<team_key>` across the pool states (Linear `Triage` + `Backlog` + the
   Step-1.5.3 resolved To-Do name), then KEEP only tickets carrying **none**
   of:
   - any classification label (`bug` / `port` / `feature` / `design bug`),
   - any analyzer label (`ready-to-port` / `ready-to-dev` / `need-revision`
     / `need-dependency`),
   - any pipeline label (`need-spec-review` / `dispatcher-*-in-flight`).
   These are the genuinely untriaged tickets. Sort priority
   `urgent > high > medium > low > none`, then `createdAt` asc.
   - Empty → print `No untriaged pool tickets on team <team_key>.` and
     continue to Step 2 with the base `<queue>` unchanged.

2. **Propose a classification per ticket** using the Step 3 lane-signal
   logic (LLM judgment over title + description). Record
   `{class ∈ bug|feature|port|design bug, reasoning, signal: strong|ambiguous}`:
   - **strong** — clear repro + expected-vs-actual → `bug`; explicit port
     language ("port from", "same as v1", "align with the native app") →
     `port`; an explicit visual/layout defect with no logic → `design bug`.
     The proposal is offered as the **Recommended** default (one keypress
     accepts).
   - **ambiguous** — anything else, **especially port-vs-feature** (the
     analyzer never reads code, so whether the feature exists upstream is
     unknowable from the ticket — a coin-flip default is worse than none).
     NO Recommended default: the operator must choose deliberately (the
     fast-confirm-fatigue guard).

3. **Per-ticket confirmation — always human, three actions.** For each
   surviving pool ticket fire ONE `AskUserQuestion` (NEVER skipped — there
   is no `--auto`/`--non-interactive` here, AC4). The question states the
   proposed class + one-line reasoning. For the chosen class `<c>`:
   - **`Label <c> only`** — write the single classification label `<c>`;
     **zero** assignee/status writes; the ticket stays in the shared pool
     for anyone to pull (AC2). Marked `(Recommended)` for strong-signal rows
     only.
   - **`Label <c> + Pull`** — write `<c>`, then assign me + move to To-Do
     (pulled into THIS session's analyze loop). Counts against the
     **per-batch pull cap (default 3)**; once the cap is reached, this
     option is dropped from every remaining row's menu with the note
     `pull cap (3) reached — Label-only / Skip only`. (AC3 — pull is an
     explicit per-ticket choice, capped; no bulk self-assign exists.)
   - **`Skip`** — no writes.
   Ambiguous rows carry NO `(Recommended)` option (forces a deliberate
   keypress); use the question's `Other` free-text to set a different class
   when the best guess is wrong. One prompt per ticket — never batch the
   classification decision away from the human (§C ownership).

4. **Writes** (read-before-write; reuse the Step 8 patterns):
   - **Ensure classification labels exist** (`list_issue_labels` for the
     team). They are normally human-created; a missing one → try
     `create_issue_label`, and on failure print
     `Classification label <c> missing and create failed — apply manually on <ticket>`,
     mark the row `errored`, continue.
   - **Label write** (`save_issue --labels`, read-before-write full-set):
     add the confirmed `<c>`. **Stale `need-revision` boundary** — remove a
     pre-existing `need-revision` in the SAME write ONLY when the ticket is
     being **Pulled** (it re-enters analysis this session, so the analyze
     pass re-judges it fresh); for **Label-only** leave any analyzer label
     untouched — the next analyze sweep owns it. Triage never writes
     `ready-to-*` / `need-dependency`.
   - **Pull write** (`Label + Pull` only): `save_issue` assign = me, state =
     the resolved To-Do name. `Label-only` and `Skip` make ZERO
     assignee/status writes.
   - Post no analysis comment here — that is the analyze pass's job for the
     pulled tickets; Label-only tickets get their analysis on a later sweep
     once someone pulls them.

5. **Merge pulled tickets into `<queue>`.** Union every `Label + Pull`
   ticket-id into `<queue>` (dedup against the Step 1.5 set; pulled tickets
   are now To-Do + assigned-to-me and satisfy the same criteria). Print
   `Triage: <L> labeled, <P> pulled, <S> skipped · +<P> into the analyze queue (now <total> total).`
   `Label-only` and `Skip` tickets do NOT enter `<queue>` — they stay in the
   pool. Proceed to Step 2 with the merged queue.

### Step 2: Fetch full ticket data + explicit relations

For every ticket in `<queue>` (single mode: the one ticket), fetch via the
`_ticket-lib.md` `get_ticket` branch and capture using the logical field
names from its field-mapping table:

- `<title>`, `<description>`, `<url>`, `<status_name>`, `<labels>`,
  `<priority>`, `<createdAt>`
- `<lane>` — derive per the `_ticket-lib.md` lane table: Linear — first
  the **`design bug` precedence rule** (whole-string, case-insensitive;
  present → `ui-tweak`, regardless of which canonical labels co-occur —
  a `design bug`-only ticket must resolve to `ui-tweak`, NOT fall through
  to `unknown`/`need-revision`); only if absent, classification label ∈
  `{bug, port, feature}` (exactly one → that lane; zero or multiple →
  `unknown`). Jira `fields.issuetype.name` (`Bug` → bug, story/task
  family → feature; no port or ui-tweak lane).
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
- **ui-tweak** — sufficient for `/ui-tweak:ff` (a design bug is a visual
  defect, NOT a logic bug — do not demand repro steps):
  - [ ] What visual/layout change is wanted (the target look, or the
        delta from current: size, color, spacing, ordering, …).
  - [ ] Where — the screen / component the change applies to.
  - [ ] Figma URL or before/after reference — preferred but not required
        when the textual description is unambiguous (e.g. "make the
        order-page CTA button 5dp taller").
- **all lanes**:
  - [ ] Resolvable lane. Linear: `design bug` present → `ui-tweak`
        (precedence — always resolvable, even with canonical co-labels);
        otherwise exactly one classification label — missing or multiple
        is itself a revision reason
        (`no single classification label — add exactly one of bug/port/feature, or design bug for UI-only defects`),
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

**Platform overlay — `platform: prompt` repos (e.g. gogox-claude itself).**
When the resolved `<platform>` (Step 1.5.1) is `prompt`, the artifacts are
prompts / skill bodies / workflow scripts, NOT an app — so the lane checklists
above are reinterpreted, and several items simply do not apply:

- **Drop**: device / build / version / OS-environment (there is no build), and
  Figma / before-after references (there is no UI). Do NOT flag their absence.
- **Require instead, for every lane** (this replaces the lane-specific items):
  - [ ] **Where** — which command / skill / workflow file or area changes
        (e.g. `commands/design/ui-tweak/preview.md`).
  - [ ] **What** — the concrete change (the fix, the new behavior, the delta).
  - [ ] **≥ 1 testable acceptance / done-when** statement.
- Lane derivation is unchanged (`bug` / `feature` still routes via `/route`);
  only the *completeness* lens changes. A gogox-claude `Bug` like a macOS
  `timeout` regression is "complete" when it names the file, the defect, and how
  to confirm the fix — not when it lists repro-env or a Figma link.

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

#### Step 10.1: Slack digest (best-effort, opt-in)

<!-- SYNC: the status mapping, message grammar, config gates, and send
     block live in commands/dev/_slack-notify.md. Do not re-inline. -->

After the `Summary:` line, post ONE run-level Slack digest via
`/_slack-notify digest ticket-analyzer`. Gates (skip with a one-line
audit, in order):

1. `--dry-run` → skip (`slack-notify: skipped (dry-run)`). Dry-run is
   100% read-only end-to-end; it also short-circuits at Step 7, so this
   gate is belt-and-suspenders.
2. `X + Z == 0` (empty sweep or everything skipped) → skip
   (`slack-notify: skipped (no-op sweep)`). Zero-candidate fires stay
   silent by design.

Otherwise build the inputs from data already in memory (Step 9 report —
no extra MCP calls):

- Header stats: `team=<KEY>`, `analyzed=<X>`, `ready=<C>`,
  `need_revision=<I>`, `blocked=<B>`, `errored=<Z>`, and
  `best_start=<id>` when Step 9 has a recommended start.
- One raw-signal line per analyzed/errored ticket, format per
  `_slack-notify.md` Inputs (`title` = the ticket title, already in
  memory from the analysis loop — the helper truncates to 60 chars):
  `<ticket-id> <url> <lane> ready title="<title>"` /
  `... need-revision reasons=<comma-list> title="<title>"` /
  `... need-dependency blockers=<comma-list> title="<title>"` /
  `... cycle ids=<id1↔id2> title="<title>"` /
  `... errored detail=<what failed> title="<title>"`.

The helper owns the emoji/token mapping, `#needs-human` tagging, and the
fail-soft send — invoke it and continue regardless of its outcome (it
always exits 0). Re-announcing tickets that were already `need-revision`
/ `need-dependency` last sweep is **deliberate** (standing reminder —
see `_slack-notify.md` Guardrails); do not add change-detection here.

---

## §A — Output comment schema

```markdown
<!-- ticket-analysis:v1 ticket=<TICKET-ID> verdict=<complete-unblocked|complete-blocked|incomplete> lane=<port|feature|bug|ui-tweak|unknown> -->
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
| Zero tickets after filters | 1.5.7 | STOP cleanly, exit zero — unless `--triage` (continue to Step 1.6 with empty base queue) |
| `--dry-run` | 7 | Full report, zero writes of any kind |
| `--triage` + ticket-id, or `--triage` + `--non-interactive` | 1.1 | STOP — triage is a human-confirmed batch pool sweep |
| `--triage` on a Jira repo | 1.6 | Skip Phase 0 (Linear-only v1), continue to normal sweep |
| `--triage`, untriaged pool empty | 1.6.1 | Note + continue to Step 2 with the base queue |
| `--triage` ambiguous classification (esp. port vs feature) | 1.6.2 | No Recommended default — operator must choose deliberately |
| `--triage` pull cap reached | 1.6.3 | `Label + Pull` dropped from remaining rows; Label-only / Skip only |
| `--triage` Label-only choice | 1.6.4 | One classification label written; zero assignee/status writes; stays in pool |
| `--triage` Pulled ticket with stale `need-revision` | 1.6.4 | `need-revision` cleared in the same label write (re-judged this session) |

## §C — Non-goals (do not extend)

- Do NOT invoke `/ggx-work`, `/route`, or any pipeline — labels are the
  only handoff.
- Do NOT write classification labels (`bug` / `port` / `feature` /
  `design bug`) — those are human-owned (`/ggx-dispatcher` ownership
  table). **Carve-out (`--triage` Step 1.6 only):** the triage pass writes
  the ONE confirmed classification label, and only after an explicit
  per-ticket human confirmation (`AskUserQuestion`) — the human's confirm IS
  the ownership step, there is no `--auto`/`--non-interactive` for Phase 0.
  Every non-`--triage` invocation still never writes a classification label.
- Do NOT write `dispatcher-*-in-flight` or `need-spec-review` — other
  writers own those. (The `--triage` carve-out does NOT extend here — triage
  writes only the classification label, and on a Pulled ticket may clear a
  stale `need-revision`; it never writes `ready-to-*` / `need-dependency` /
  pipeline labels.)
- Do NOT create Linear issue relations from inferred dependencies — record
  them in the comment; humans promote them to real relations.
- Do NOT transition ticket status or change assignee. **Carve-out
  (`--triage` "Label + Pull" choice only):** the explicit per-ticket Pull
  choice assigns the operator + moves the ticket to To-Do, subject to the
  per-batch pull cap. `Label-only` (the default) and `Skip` make zero
  assignee/status writes. No bulk self-assign exists anywhere (pull model —
  PM never assigns; people pull their own tickets).
- Do NOT touch the filesystem / git — pure tracker-side analysis.
- Do NOT support resume / state files — restart-on-interrupt re-derives
  everything from live tracker state.
