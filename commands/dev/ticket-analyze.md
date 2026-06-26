---
name: ticket-analyze
description: >
  Upstream ticket analyzer — the automated replacement for the manual
  "human marks ready" step that feeds `/ggx-dispatcher`. Two invocation
  shapes: pass a ticket id to analyze just that one, or call with no args
  to sweep the active team's **actionable pool** — every ticket in
  Triage / Backlog / To-Do, **any assignee** (GGC-95: PM/QA tickets land
  unassigned in the pool, so an assigned-to-me-only sweep never saw them).
  Output is always **label-only — the analyzer assigns NOTHING** (pull
  model: a human/teammate assigns to pull; the dispatcher still filters
  `assignee = me`).
  Per ticket it judges content completeness against the lane's pipeline
  needs (port / feature / bug / ui-tweak), builds a dependency graph from explicit
  relations (Linear `.relations[]`, Jira `issuelinks`) plus LLM content
  inference, computes a topological implementation order and the best
  starting ticket, then writes the outcome back: complete + unblocked →
  `ready-to-port` / `ready-to-dev`; incomplete → `need-revision` +
  reasoned comment; complete but blocked → `need-dependency` + blocker
  comment. Writes state ONLY — never invokes downstream pipelines;
  `/ggx-dispatcher` / `/ggx-work` pick up from the labels. A human-owned
  `analyze-hold` label parks a ticket — the analyzer skips it entirely
  (never re-analyzes, re-labels, or re-comments) until a human removes the
  label, so a manually-parked `need-revision` is no longer silently
  re-qualified every sweep (GGC-60). Jira runs in
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

Analyze the team's actionable-pool tickets for **pipeline readiness**: is the
content sufficient for the port / feature / bug / ui-tweak workflow, and is
the ticket blocked by another ticket? Persist the verdict as workflow labels + a
structured comment so the existing dispatcher flow (`/ggx-dispatcher` →
`/ggx-work` → `/route`) picks up ready tickets with zero extra steps.

**Usage**:

- `/ticket-analyze` — **batch mode**. Sweep the active project's team
  **actionable pool** — every Triage / Backlog / To-Do ticket regardless of
  assignee (GGC-95) — analyze cross-ticket dependencies, and write per-ticket
  verdicts **label-only (never assign)**. Per-ticket failures do NOT abort the
  batch.
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
| complete | unblocked | ui-tweak (`design bug`) **whose text high-confidence predicts the fix needs logic** | `need-revision` (+ reclassify → `Bug` recommended — see the Design-bug ready-to-dev gate below) |

The four analyzer-owned labels (`ready-to-port`, `ready-to-dev`,
`need-revision`, `need-dependency`) are **mutually exclusive** — every
label write is a full-set rewrite that removes the other three (mirrors
the dispatcher §4.1 swap pattern). `bug`- and `ui-tweak`-lane tickets get
`ready-to-dev` (no new workflow label for ui-tweak — the dispatcher sweep
stays two-label); `/route` derives the bug / ui-tweak pipeline from the
classification label downstream.

**Design-bug ready-to-dev gate** (GGC-37 + GGC-58 — ONE shared design-bug skip
hook with two independent inputs): before writing `ready-to-dev` for a
`design bug` (ui-tweak lane) ticket, evaluate two suppression signals. EITHER
one alone suppresses `ready-to-dev` — re-dispatching a behaviour-needing fix to
ui-tweak would just BLOCK, since only a logic-capable lane (dev/bug) can
implement it:

- **(a) reactive — ui-blocked marker present (GGC-37)**: the comments contain
  `<!-- dispatch-triage-ui-blocked -->` (posted by the dispatcher's
  `triageTerminalUiBlock` after a *deterministic* ui-tweak BLOCK) **and the
  ticket is still classified `design bug`** → **HOLD**: do NOT write
  `ready-to-dev`; leave the existing `need-revision` label as-is and report
  `skipped — ui-tweak-blocked`. Silent on Linear (no re-comment — the
  dispatcher's marker comment already carries the reason / suggested action /
  attempt count). Enforced operationally in Step 8.2b.
- **(b) predictive — text predicts the fix needs logic (GGC-58)**: the Step 3
  logic-prediction sub-judgment found, with HIGH confidence and from the ticket
  TEXT alone (no diff exists yet), that the described fix inherently needs
  behaviour — gesture/tap recognizers, `initState`/`dispose` (lifecycle),
  async, state mutation, navigation, controllers, or view-model wiring → do NOT
  write `ready-to-dev`; write `need-revision` and post a reasoned comment
  recommending the human reclassify `Design bug` → `Bug` (the dev/bug lane CAN
  handle logic). Unlike (a) this is NOT silent — there is no upstream
  dispatcher comment to lean on, so it posts its own reasoned `ticket-analysis`
  comment through the normal Step 8 write path. **Conservative + fail-safe**:
  only a CLEAR behaviour signal triggers this; ambiguous or clearly-visual
  design bugs fall through to `ready-to-dev` exactly as today and let the
  ui-tweak dual-judge panel be the authority. A false "needs logic" must never
  strand a pure-visual ticket — a mis-held visual ticket is strictly worse than
  the wasted build it would otherwise cost.

If BOTH fire, **(a) wins** (silent hold — the marker comment already explains
the situation; do not also post a (b) reclassify comment). Both branches are
**label-only / HITL — never auto-flip the classification** (pull-model /
no-bulk-assign convention; classification stays human-owned, §C). Shared escape
hatches: a human reclassifies `design bug` → `bug` (neither signal applies →
analyzes normally for the dev/bug lane, which CAN handle logic), or adds
`ready-to-dev` directly to force re-dispatch (dispatcher Q3). This is ONE gate
with two inputs — do NOT build a second skip path. The reactive (a) half is the
safety net for whatever the predictive (b) half misses.

**Re-run semantics** (how tickets flow through repeated runs):

| Ticket's current analyzer label | Next run |
|---|---|
| `analyze-hold` (human-owned park sentinel, GGC-60) | **skipped entirely — never re-analyzed, re-labeled, or re-commented**, regardless of every other label (this row has TOP precedence; it overrides the `need-revision` re-evaluate rule below, which is exactly the manually-parked case). The analyzer NEVER adds or removes `analyze-hold` — a human adds it to park, removes it to resume. It survives any label write because it is non-analyzer-owned. |
| none (fresh) | analyzed |
| `need-revision` | re-analyzed — completeness re-judged from current content; may flip to `ready-to-*` / `need-dependency`. EXCEPTION: a `design bug` is held back from `ready-to-dev` by the Design-bug ready-to-dev gate when EITHER it carries the `<!-- dispatch-triage-ui-blocked -->` marker (GGC-37, input a) OR its text still high-confidence predicts a logic-requiring fix (GGC-58, input b) — it stays `need-revision` until reclassified to `bug` (or, for b, until the text no longer reads as logic). |
| `need-dependency` | re-analyzed — every blocker's live status re-fetched; all blockers Done → flips to `ready-to-*` |
| `ready-to-port` / `ready-to-dev` | skipped (already actionable; re-analyzing races the dispatcher) |
| `need-spec-review` / `dispatcher-*-in-flight` | skipped (already inside a pipeline) |

**Classification stickiness (GGC-96, orthogonal to the analyzer-label axis
above).** Step 2.7 may auto-write a *classification* label (`bug` / `design
bug`) on a 2-vote consensus, leaving a `<!-- ta-class:v1 source=analyzer -->`
marker. On any later run, if the ticket's current classification differs from
that marker, a human overrode it → the classification is **human-owned and never
re-flipped** (same anti-re-flip invariant as `analyze-hold`, applied to
classification). port / feature are never auto-written (deferred to GGC-98).

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

3. **Resolve the actionable-pool states** (GGC-95 — do NOT hardcode names):
   - Linear: `mcp__claude_ai_Linear__list_issue_statuses` for `<team_key>`;
     the pool = every status whose `type ∈ {triage, backlog, unstarted}`.
     This captures Triage, Backlog, AND the To-Do-named unstarted state(s) —
     the wider scope is the whole point: PM/QA tickets sit unassigned in
     Triage/Backlog, never To-Do. Record the matching state names/ids.
   - Jira: the pool = `statusCategory = "To Do"` (covers the Backlog / To Do /
     Triage-equivalent column) via the JQL below.

4. **List tickets — team-wide, NO assignee filter** (GGC-95):
   - Linear: run one `mcp__claude_ai_Linear__list_issues` per resolved pool
     state (`team = <team_key>`, `state = <pool state>`, and **OMIT
     `assignee`** so every assignee — and the unassigned pool — is covered);
     union the results and dedup by id. Per-state queries because `state` is
     singular in the MCP schema — the same multi-query shape as
     `/ggx-dispatcher`'s discovery (§ Discovery). The analyzer still **writes
     no assignee on any ticket** — label-only output regardless of who (if
     anyone) owns the ticket.
   - Jira: `project = <team_key> AND statusCategory = "To Do"` (no `assignee`
     clause) via `mcp__claude_ai_Atlassian_Rovo__searchJiraIssuesUsingJql`,
     post-filtering on `.fields.status.name` as before.

5. **Re-analysis post-filter** — apply to the fetched set, printing one
   skip line per excluded ticket:
   - **Skip (highest precedence)**: tickets carrying `analyze-hold`
     (`skipped — human-parked (analyze-hold); remove the label to resume`)
     — checked FIRST, so a `need-revision` ticket a human parked with
     `analyze-hold` is never re-qualified (GGC-60).
   - **Keep**: tickets with none of the analyzer labels (fresh), and
     tickets carrying `need-revision` or `need-dependency` (re-evaluate —
     content may have been enriched / blockers may have closed) **and NOT
     also carrying `analyze-hold`**.
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
   `No analyzable actionable-pool tickets on team <team_key>.` and
   STOP cleanly (exit zero) — **UNLESS `--triage` is set**, in which case
   continue to Step 1.6 with an empty base `<queue>` (the triage pass may
   pull pool tickets into it; a triage run with no pool backlog is normal).

8. **Print queue overview** — ONE row per surviving ticket (the template
   below is a format, not an output cap; never truncate the table):
   ```
   Found <N> actionable-pool tickets on team <team_key> (<F> fresh, <R> re-analysis):

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

> **Superseded by Step 2.7 (GGC-96).** The default sweep now auto-classifies the
> pool through the Step 2.7 confidence gate (2-vote; strong `bug` / `design bug`
> auto-labeled, the rest to the human tail) — so the intake-starvation this pass
> was built to solve is handled automatically, no flag required. `--triage` is
> kept as the **manual-override / deliberate-pull** path: a human who wants to
> classify ambiguous port/feature tickets by hand (Step 2.7 leaves those in the
> tail) or to *pull* a chosen ticket into their own queue. It is no longer the
> primary intake mechanism. Unchanged below for that manual use.

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

**Universal `analyze-hold` guard (GGC-60)** — the single enforcement point that covers BOTH modes: immediately after `<labels>` is captured, if it contains `analyze-hold`, the ticket is HUMAN-PARKED. Drop it from `<queue>` now — do NOT proceed to Step 3+, write no label, post no comment, record no verdict. Print `[<k>/<N>] skipped <ticket-id> — human-parked (analyze-hold); remove the label to resume`. In **single mode** this is a clean STOP (exit zero); in **batch mode** it just removes the one ticket and the sweep continues. (Batch discovery already filters these out at Step 1.5.5; this guard is the safety net for single mode and for any ticket pulled in by a path that skipped that filter.) The analyzer never writes `analyze-hold` itself — it is human-owned.

Print one line per ticket (non-held):
`[<k>/<N>] Fetched <ticket-id> "<title>" — lane: <lane>, relations: <n>`

### Step 2.7: Auto-classification (GGC-96 — default flow, replaces the `--triage` per-ticket HITL)

Runs for every queued ticket whose Step 2 lane is `unknown` (no single
classification label among `{bug, port, feature}` and no `design bug`). A
classified ticket skips this step entirely — its human-set lane is authoritative.
This folds the old Step 1.6 (`--triage`) classification into the default sweep,
but **automated behind a confidence gate** instead of a per-ticket human confirm.
The analyzer still **assigns nothing** here — it writes at most one classification
label (label-only). Relaxes §C's "never write classification labels" behind the
two gates below (R1).

**Gate 0 — sticky human override (F3, the GGC-60 lesson).** Read the ticket's
comments for the newest `<!-- ta-class:v1 source=analyzer label=<c> -->` marker
(the idempotency key this step writes, §A2):

- **No marker** → fresh ticket; proceed to Gate 1.
- **Marker present AND current classification == the marker's `<c>`** → this is
  our own prior auto-label, unchanged by a human; re-affirm the lane and skip the
  re-classification (no duplicate write).
- **Marker present AND current classification differs** (a human removed it,
  changed it, or set a different one) → **the classification is now HUMAN-OWNED.
  Never re-flip it.** Do NOT auto-classify, do NOT re-add the old label. If the
  current classification is a single valid label, use it as `<lane>`; if it is now
  empty/ambiguous, treat the ticket as the human tail (Gate 2 "ungroundable" path)
  — the human deliberately un-classified it. This is the exact re-flip loop
  GGC-60 fixed for `need-revision`; do not reintroduce it for classification.

**Gate 1 — decorrelated 2-vote (F1).** Propose a class from the ticket TEXT, then
confirm with a SECOND, different-tier model — mirroring `audit.md`'s
both-must-agree contract (one model's "looks clear" is not trusted at a write
boundary). Only **strong-signal `bug` / `design bug`** are in scope here;
**port / feature stay in the human tail** (undecidable from text alone — the
analyzer reads no code; codebase grounding is P3 / GGC-98):

1. **Proposer (haiku)** — judge from title + description, emit
   `{class ∈ bug|design bug|port|feature|none, signal: strong|ambiguous, evidence}`.
   `strong` only when: repro steps + expected-vs-actual → `bug`; an explicit
   visual/layout defect with no behaviour language → `design bug`. Anything else
   (port/feature, mixed, weak) → `ambiguous`.
2. **Confirmer (sonnet)** — independently judge the same ticket; emit the same
   shape. Do NOT show it the proposer's answer (decorrelation).
3. **Consensus** — auto-write ONLY when **both** return the SAME `strong`
   class **and** it is `bug` or `design bug`. Otherwise → Gate 2 (human tail).

**Gate 2 — outcome:**

- **Consensus strong `bug` / `design bug`** → write the single classification
  label (read-before-write full-set, reusing the Step 8.4 pattern; ensure the
  label exists via `list_issue_labels`, create on miss). Post the
  `<!-- ta-class:v1 source=analyzer label=<c> -->` marker (F3). Set `<lane>`
  accordingly and continue to Step 3 with the right lane checklist. **No
  assignee write.**
- **No consensus / port / feature / `none`** → the ticket is the **human tail**:
  leave it lane `unknown`, do NOT write a classification label, and let Step 3 /
  Step 6 record it as `need-revision`. The Step 8 comment MUST state plainly this
  is the *"couldn't auto-classify the lane — please set one"* case (with the
  best-guess lane + evidence as a suggestion), distinct from the *"content
  incomplete"* case (Q3 — one label, two clearly-worded comment variants).

`--non-interactive` / unattended (cloud) runs behave identically — there is no
human confirm in this step (that was the old `--triage` bottleneck). `--dry-run`
proposes + reports the would-write class but writes no label and posts no marker.

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

  **Logic-prediction sub-judgment (ui-tweak lane only — GGC-58; feeds the
  Design-bug ready-to-dev gate, input b).** A `design bug` is meant to be a
  *visual* defect the ui-tweak pipeline can fix without touching behaviour.
  After the visual checklist above, make ONE additional judgment from the
  ticket TEXT (description + any technical notes — there is no diff at analyze
  time, so this is necessarily a heuristic): **does the described fix
  inherently require logic / behaviour changes?** Flag `needs-logic` ONLY with
  HIGH confidence — i.e. the text clearly calls for behaviour such as:
  - gesture / tap recognizers (e.g. `TapGestureRecognizer`, making part of a
    label tappable), or wiring an `onTap` / handler / callback that does not
    yet exist;
  - widget lifecycle — `initState` / `dispose`, controller creation+teardown;
  - async / await, futures, stream subscriptions, timers;
  - state mutation — `setState`, view-model / bloc / provider / notifier state;
  - navigation — pushing / popping routes, deep links;
  - controllers / view-models / other business-logic objects.

  Pure-visual work is NOT logic and MUST fall through to the normal verdict:
  color / spacing / padding / sizing / typography, swapping to a design-system
  icon or asset, re-ordering or restructuring existing widgets, alignment /
  constraints (`LayoutBuilder` / `ConstrainedBox`). Worked calibration: CAF-540
  (LayoutBuilder/ConstrainedBox empty-state) and CAF-611 (design-system icon
  swap) are visual → NOT flagged (they now pass ui-tweak post-GGC-57); CAF-555
  (tappable T&C link needing `TapGestureRecognizer` + `initState`/`dispose`)
  IS flagged.

  **Fail-safe rule** (the bias that makes this safe): when the signal is
  ambiguous, mixed, or clearly visual, judge `visual` (the default). Only an
  unambiguous behaviour signal sets `needs-logic`. A false `needs-logic`
  strands a ticket ui-tweak could have shipped — strictly worse than the
  wasted build — so bias hard toward `visual`. Record
  `{logic_prediction: needs-logic|visual, evidence: "<quoted phrase>"}` for the
  gate to consume at Step 6 / Step 8. This is a *prediction*, not the final
  authority — every ticket that proceeds is still judged by the ui-tweak
  dual-judge panel downstream.
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

The **Design-bug ready-to-dev gate** (contract, top of file) has two inputs,
both evaluated for a `design bug` (ui-tweak lane) ticket whose `target_label`
would be `ready-to-dev`:

- **(a) marker present (GGC-37)** — enforced operationally in Step 8.2b (it
  reuses the comments already re-fetched there): carrying the
  `<!-- dispatch-triage-ui-blocked -->` marker downgrades to a `held` outcome —
  no `ready-to-dev` write, no comment.
- **(b) text predicts logic (GGC-58)** — from the Step 3 logic-prediction
  sub-judgment (`logic_prediction == needs-logic`): downgrade `target_label`
  from `ready-to-dev` to `need-revision`, with verdict `incomplete` and a
  reclassify reason (`fix appears to require logic / behaviour (<evidence>) —
  reclassify Design bug → Bug so the dev/bug lane can implement it`). Unlike
  (a) this is NOT a silent hold: it flows through the normal Step 8.3 / 8.4
  comment + label write (there is no upstream dispatcher comment to lean on).

If BOTH (a) and (b) fire, **(a) wins** — the silent hold (the marker comment
already explains the situation; suppress the (b) reclassify comment).

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
   labels; call the fresh label set `<labels-fresh>` (it is the authoritative
   base for the Step 8.4 rewrite — see below). Decide as follows:

   **(i) hard conflict → skip/STOP.** If a `ticket-analysis:v1` comment by
   another author is newer than `<batch-start-time>`, OR the ticket now carries
   `dispatcher-*-in-flight` (dispatcher locked it mid-analysis), OR
   `<labels-fresh>` now carries `analyze-hold` (a human parked it mid-run), OR
   the **classification label changed** since the Step-2 read (the lane this
   verdict was computed for is no longer current):
   - **Single mode** → STOP:
     `Concurrent actor detected on <ticket-id> (<which signal>). Re-run /ticket-analyze <ticket-id> to see the latest state.`
   - **Batch mode** → print
     `[<k>/<N>] <ticket-id> skipped — concurrent actor (<which signal>).`,
     mark `skipped`, continue.

   **(ii) benign label drift → keep, but rebase the write (GGC-95 / F5).** If
   `<labels-fresh>` differs from the Step-2 `<labels>` only in labels that do
   NOT affect the verdict (e.g. a human added a priority/area tag between our
   read and write), do NOT skip — but the Step 8.4 full-set rewrite MUST be
   computed against `<labels-fresh>`, never the stale Step-2 `<labels>`.
   Otherwise the rewrite (which sends a complete label list) silently drops the
   concurrently-added label — a lost update. Always rebasing on the just-fetched
   set makes a benign concurrent label edit safe; the hard-conflict cases above
   are the only ones that skip.

   **2b. Design-bug ready-to-dev gate** (GGC-37 — reuse the comments just
   re-fetched in 8.2, no extra MCP call): if `target_label == ready-to-dev`
   AND `<lane> == ui-tweak` (still classified `design bug`) AND the comments
   contain the literal marker `<!-- dispatch-triage-ui-blocked -->`, then
   **HOLD** — skip the comment + label writes for this ticket (do NOT post a
   `ticket-analysis:v1` comment, do NOT write `ready-to-dev`; the existing
   `need-revision` is left untouched). Mark the outcome `held (ui-tweak-blocked)`
   and print
   `[<k>/<N>] <ticket-id> skipped — ui-tweak-blocked (reclassify Design bug → Bug to proceed).`,
   continue. (Once reclassified to `bug`, `<lane>` is no longer ui-tweak so this
   gate does not fire and the ticket analyzes normally. This is the shared gate
   GGC-58 extended with the (b) predictive branch above — do not add a second
   skip path.)

   This 8.2b enforcement is the **reactive (a)** branch ONLY. The **predictive
   (b)** branch (GGC-58 — text high-confidence predicts a logic-requiring fix)
   is NOT handled here: it was already folded into `target_label` at Step 6
   (`need-revision` + reclassify-to-`Bug` reason) and posts its reasoned
   comment + writes `need-revision` through the normal Step 8.3 / 8.4 path
   below. Only the marker branch needs this pre-write comment re-fetch. If both
   fire, (a) wins here — having held the ticket, skip the Step 8.3 / 8.4 writes
   so no (b) reclassify comment is posted.

3. **Post comment** via the `_ticket-lib.md` `save_comment` branch using
   the §A schema. Append-only — a fresh comment each run; the newest
   `ticket-analysis:v1` comment is authoritative on read.

4. **Label write**:
   - **Linear**: compute the new set = **`<labels-fresh>`** (the set re-fetched
     in Step 8.2 — NOT the stale Step-2 read, F5) − the other three
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

### §A2 — Auto-classification provenance marker (GGC-96)

When Step 2.7 auto-writes a classification label, it posts a **standalone**
one-line marker comment (separate from the `ticket-analysis:v1` body above):

```markdown
<!-- ta-class:v1 source=analyzer label=<bug|design bug> ts=<ISO timestamp> -->
Auto-classified `<label>` (2-vote consensus: <one-line evidence>). A human can
change this label any time — the analyzer will then treat the classification as
human-owned and never re-flip it.
```

Rules:
- `label=` is the class the 2-vote agreed on; `source=analyzer` is fixed (the
  marker is only ever written by this step).
- This marker is the **sticky-override key** read by Step 2.7 Gate 0: on the next
  sweep, if the ticket's current classification label ≠ this marker's `label`, a
  human overrode it → never re-classify (the GGC-60 anti-re-flip invariant,
  generalized to classification).
- Posted ONLY on an actual auto-write — never for the human tail, never in
  `--dry-run`, never when re-affirming an unchanged prior auto-label.

## §B — Edge case reference

| Scenario | Step | Behavior |
|----------|------|----------|
| `ticket_system == unknown` | 1 / 1.5 | STOP — never default to Linear |
| Jira repo / ticket | 1.5 / 8.4 | Degraded mode: comment + `fields.labels` strings, no workflow labels |
| No To-Do status resolvable on team | 1.5.3 | STOP with the team's actual status list printed |
| Ticket already `ready-to-*` | 1.5.5 | Skipped — re-analysis would race the dispatcher |
| Ticket in pipeline (`need-spec-review`, `dispatcher-*-in-flight`) | 1.5.5 | Skipped |
| `need-revision` / `need-dependency` ticket | 1.5.5 | Re-analyzed (the revise → ready loop) |
| Unclassified ticket, 2-vote agrees strong `bug`/`design bug` | 2.7 | Auto-write that classification label (label-only) + `ta-class:v1` marker (GGC-96) |
| Unclassified ticket, votes disagree / port / feature / weak | 2.7 | Human tail — no auto-label; `need-revision` comment says "couldn't classify the lane, pick one" + suggested lane (Q3) |
| `ta-class` marker exists but current classification differs (human override) | 2.7 Gate 0 | Classification is human-owned — never re-flip (GGC-96 / GGC-60 invariant) |
| Missing/multiple classification labels (after Step 2.7 tail) | 3 | Revision reason, not a prompt |
| Missing classification, strong lane signal in text | 3 | Reason carries a suggested lane + evidence (comment text only, never a label write) |
| Missing classification, weak/ambiguous signal (esp. port vs feature) | 3 | No suggestion — plain base sentence only |
| `design bug` text HIGH-confidence predicts a logic-needing fix | 3 / 6 | Design-bug gate (b): `need-revision` + reclassify→`Bug` comment; never `ready-to-dev` |
| `design bug` ambiguous / clearly-visual fix | 3 | No logic flag — `ready-to-dev` as today; ui-tweak dual-judge panel is the authority |
| `design bug` with BOTH ui-blocked marker and logic-text signal | 8.2b | Reactive marker branch (a) wins — silent hold, no reclassify comment |
| Bare ticket-id mention, no ordering language | 4.1 | `related` edge — recorded, never blocking |
| Inferred edge, `--non-interactive` | 4.2 | Report-only, never blocking |
| Blocking edge to a Done/canceled ticket | 4.3 | Satisfied — not blocking |
| Cycle among queue tickets | 5.2 | All members blocked w/ cycle reason; loud warning; no crash |
| Dispatcher locks a ticket mid-run | 8.2 | Pre-write re-check skips it |
| Newer foreign `ticket-analysis:v1` comment | 8.2 | Skip (batch) / STOP (single) |
| Classification label / `analyze-hold` changed since read | 8.2(i) | Hard conflict — skip (batch) / STOP (single); verdict was computed for a stale lane |
| Benign label added by a human between read and write | 8.2(ii) / 8.4 | Keep; rebase the full-set rewrite on the freshly-fetched labels so the new label is preserved (F5 — no lost update) |
| Unassigned / other-assignee pool ticket | 1.5.4 | In scope (GGC-95) — analyzed + labeled, never assigned |
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
- Classification labels (`bug` / `port` / `feature` / `design bug`):
  **auto-write is allowed ONLY through the Step 2.7 confidence gate (R1 /
  GGC-96)** — a decorrelated 2-vote (haiku + sonnet must agree) on a
  **strong-signal `bug` / `design bug`**, written label-only with a
  `<!-- ta-class:v1 source=analyzer -->` provenance marker. Outside that gate
  the analyzer never writes a classification label: **port / feature are never
  auto-written here** (undecidable from text — deferred to P3/GGC-98 codebase
  grounding), and a human's classification is **sticky** — once a human sets or
  changes the label away from our marker, Step 2.7 Gate 0 never re-flips it.
  (Legacy carve-out: the `--triage` Step 1.6 per-ticket-confirm path still
  exists but is superseded by Step 2.7's automated gate — see Step 1.6's note.)
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
