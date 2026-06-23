---
name: ggx-bundle
argument-hint: "<lead-ticket> <siblings...> [--auto] [--dry-run]"
description: >
  Single-feature bundle orchestrator for a dependency-ordered set of `port`
  tickets that are finely-sliced scenarios of ONE feature with code
  dependencies. Instead of porting each ticket into its own OpenSpec change,
  it CONSOLIDATES every ticket's requirements into ONE PRD, drives the standard
  port → spec-review → dev flow on the lead ticket (one `feat/<lead>` branch,
  one OpenSpec change, one archive, one draft PR), and finalizes all N tickets
  in Linear with auditable per-ticket coverage. The single-ticket `/port:ff` /
  `/dev:ff` flow is bracketed by a consolidate-before step and a fan-out-after
  step; per-ticket traceability (lost when N changes collapse to one) is bought
  back by `Source: <ticket-id>` tags + a coverage gate + Linear relations +
  claim/ship comments. Linear-only — the port lane is Linear-exclusive (see
  `_ticket-lib.md`). For INDEPENDENT tickets (no shared feature), dispatch them
  individually via `/ggx-dispatcher` instead.
Prerequisite: >
  - Linear MCP authenticated; gh CLI authenticated.
  - cwd is the main worktree of a registered Linear repo (resolve via
    `_ticket-lib.md`). Clean tree on the default branch.
  - On a FRESH run, every ticket id passed is a real Linear ticket that is
    (1) assigned to you AND (2) labeled `ready-to-port` AND (3) carries the
    `port` classification label. (On a RESUME, the lead has already advanced
    past `ready-to-port`; Step 1.5 detects this and skips the entry gate.)
  - The set is genuinely ONE feature sliced finely with implementation
    dependencies. For independent tickets, dispatch them via `/ggx-dispatcher`
    (each gets its own branch + PR).
---

# `/ggx-bundle <lead-ticket> <sibling...> [--auto] [--dry-run]`

> `/ggx-bundle` is a **single-feature bundle orchestrator**. It exists for one
> situation: a single feature was sliced into several `port` tickets (typically
> one QA acceptance scenario each) that depend on each other's code, so porting
> + implementing them one-at-a-time through `/ggx-work` breaks — each `/ggx-work`
> builds its dev branch off trunk and ticket B can't see ticket A's unmerged code.
>
> **It collapses N tickets into ONE feature.** Rather than keeping N independent
> OpenSpec changes (which then fight at multi-archive time because finely-sliced
> tickets touch the same capability), it CONSOLIDATES every ticket's requirements
> into one PRD and runs the **standard single-ticket flow on the lead**:
> `/port:start --prd-file` → `/port:ff` → `/spec-review` → `/dev:ff` → one
> `feat/<lead>` branch, one OpenSpec change, **one archive (zero overlap
> possible)**, one draft PR.
>
> **The single-ticket flow is bracketed, not modified.** A *consolidate-before*
> step (read N tickets → one PRD, claim siblings) and a *fan-out-after* step
> (finalize all N tickets in Linear) wrap the unchanged `/port:ff` + `/dev:ff`
> middle. `/ggx-bundle` reuses those stages verbatim; it never re-implements them.
>
> **Per-ticket traceability is bought back.** Collapsing N changes to one loses
> the per-ticket spec→ticket link that separate changes give for free. This skill
> reconstructs it with three layers: `Source: <ticket-id>` tags on every
> consolidated requirement + a hard coverage gate (every sibling id MUST appear in
> the shipped spec) + Linear `relatedTo` relations + claim/ship comments.
>
> **It is re-entrant.** Like `/ggx-work` it writes no state file. Re-invoking with
> the same id list re-derives the current phase from Linear labels + the lead
> worktree, and resumes (the `/spec-review` human gate is just "run /spec-review,
> then re-invoke").

**Usage**:

- `/ggx-bundle DAF-501 DAF-502 DAF-503` — interactive. First id = **lead** (owns the
  `feat/<lead>` branch + the PR); the rest = **siblings** in **dependency order**
  (left = implemented earliest). Verifies preconditions, consolidates the PRD,
  shows it, and asks you to confirm before any mutation.
- `/ggx-bundle DAF-501 DAF-502 DAF-503 --auto` — skip the Step 2 confirmation gate
  and run the port/dev stages unattended. The `/spec-review` gate (Step 4d) still
  PAUSES — `--auto` never skips human review of an LLM-synthesized spec.
- `/ggx-bundle DAF-501 DAF-502 DAF-503 --dry-run` — consolidate + print the PRD and
  plan, then STOP. **No Linear / git mutation.**

Notes:

- Order is **explicit and load-bearing**: `/ggx-bundle` never re-orders the list.
- This skill does the porting ITSELF (consolidated, once). Do NOT pre-port the
  tickets through `/ggx-dispatcher` — that produces N separate changes (the thing
  this skill avoids). Entry state is `ready-to-port`, not `ready-to-dev`.

---

## Steps

### Step 1: Parse + pre-flight

1. **Parse arguments.** Read `$ARGUMENTS`. Separate flags (`--auto`, `--dry-run`)
   from ticket ids.
   - `<lead>` = first id (trim, uppercase). `<siblings>` = the rest in order.
     `<all>` = `[<lead>] + <siblings>`.
   - Fewer than 2 ids → STOP: `/ggx-bundle needs ≥2 tickets (a lead + at least one sibling). For one ticket use /ggx-work.`
   - Duplicate ids in `<all>` → STOP with the offending id.
2. **Pre-flight.**
   1. Resolve profile + `<ticket-system>` via `_ticket-lib.md` (reads
      `.gogox-claude.yaml` + `org.yaml`). Hold `<team-key>` (= `branch_prefix`).
      - not `linear` → STOP: `/ggx-bundle supports ticket_system: linear only — the port lane is Linear-exclusive. This repo is: <value>.`
   2. **Worktree guard** (must run from the main repo, not a linked worktree):
      ```bash
      if [ "$(git rev-parse --git-common-dir)" != "$(git rev-parse --git-dir)" ]; then
        MAIN=$(git rev-parse --git-common-dir | sed 's|/\.git$||')
        abort "/ggx-bundle must run from the main repo, not a worktree. cd \"$MAIN\" then re-invoke."
      fi
      ```
   3. `gh auth status >/dev/null 2>&1` else STOP: `gh CLI not authenticated. Run: gh auth login`.
   4. `git worktree prune` (silent).

### Step 1.5: Derive phase (re-entrancy)

This skill writes no state file. Derive the current phase fresh from the **lead's**
Linear labels + the lead worktree, then route to the right step.

```bash
lead_lc=$(echo "<lead>" | tr '[:upper:]' '[:lower:]')
lead_wt=$(git worktree list --porcelain \
            | awk -v t="$lead_lc" '/^worktree / && tolower($2) ~ t"$" {print $2; exit}')
[ -z "$lead_wt" ] && [ -d "../$lead_lc" ] && lead_wt="$(cd "../$lead_lc" && pwd)"
```

Fetch the lead via `mcp__claude_ai_Linear__get_issue <lead>` → hold `lead_labels`,
`lead_status`. Resolve the lead's branch PR state by branch:
`gh pr list --head "feat/<lead>" --state all --json state,url -q '.[0]'`.

| Condition (checked top-down)                                            | Phase                  | Resume at |
|-------------------------------------------------------------------------|------------------------|-----------|
| lead PR state `OPEN`/`MERGED`                                           | **FAN-OUT** (or done)  | Step 5    |
| lead label `ready-to-dev`                                              | **DEV**                | Step 4e   |
| lead label `need-spec-review`                                         | **REVIEW-PAUSE**       | Step 4d   |
| lead worktree exists with a committed `openspec/changes/*/` (non-archive) | **PORT-RESUME**     | Step 4b   |
| none of the above (fresh)                                              | **FRESH**              | Step 2    |

**Entry gate (FRESH phase ONLY).** When phase = FRESH, every ticket must be
assigned to you AND labeled `ready-to-port`:
- One query `mcp__claude_ai_Linear__list_issues --assignee me --label ready-to-port --team <team-key> --limit 250` → build a present-set of ids.
- For each id in `<all>` not in the set, `get_issue <id>` and report which condition
  failed (`not assigned to you` / `missing ready-to-port label`).
- Any violation → STOP with the lines plus:
  > `/ggx-bundle (fresh run) requires every ticket assigned to you AND labeled ready-to-port. Fix the assignment/label, then re-invoke. (Do NOT pre-port via /ggx-dispatcher — /ggx-bundle ports the consolidated feature itself.)`
- Also require the `port` classification label on each; any miss → STOP:
  `/ggx-bundle: <id> is not a port ticket (labels: <csv>). This skill bundles port tickets only.`

On any RESUME phase, skip the entry gate (the lead has advanced past
`ready-to-port` and siblings were de-labeled at claim time) and jump to the
resume step. Print a one-line phase banner (`phase: <PHASE> · lead <lead> · +N siblings`).

---

### Step 2: Consolidate requirements → PRD  (FRESH phase; READ-ONLY)

1. `mcp__claude_ai_Linear__get_issue <id>` for every id in `<all>`; read each
   `description` + acceptance criteria in full. Cache `url` + `title` per id.
2. **Synthesize one consolidated PRD** (`<prd-text>`, markdown). Organize the union
   of all N tickets' requirements by logical area (not by ticket), de-duplicating
   overlapping scenarios. **Every requirement / scenario block MUST be annotated
   with its origin ticket(s)** on its own line, exactly:
   ```
   Source: <ticket-id>[, <ticket-id>...]
   ```
   This is the provenance seed that Step 4b propagates into the spec and Step 4c
   asserts on. Record `<coverage-set>` = the set of all `<all>` ids (every one must
   appear in the shipped spec).
3. **`--dry-run` → STOP here.** Print `<prd-text>` + the plan table (lead, siblings
   in dependency order, the `feat/<lead>` branch, the would-be steps). No mutation.
4. Otherwise present `<prd-text>` + the plan and, unless `--auto`, **confirm via
   `AskUserQuestion`**: *"Consolidate these N tickets into one feature on
   `feat/<lead>` and run port → spec-review → dev?"* — Decline → STOP cleanly
   (exit 0, no mutation). With `--auto`, log `auto: Step 2 confirmation gate skipped`.

### Step 3: Claim siblings  (FRESH phase; first mutations — idempotent, read-before-write)

For each id in `<all>`, read current state before writing; skip writes already at target.

1. **All tickets** (lead + siblings): single `mcp__claude_ai_Linear__save_issue` to
   set `assignee = me`, **remove `ready-to-port`** (so `/ggx-dispatcher` will not
   pick them up for independent porting), and set status `In Progress`.
2. **Each sibling**: add a Linear relation `relatedTo` → `<lead>` (native UI link
   from the sibling side), and post a claim comment if its marker is absent:
   ```
   <!-- ggx-bundle:claim:v1 lead=<lead> -->
   This ticket's requirements are being implemented as part of feature bundle
   **<lead>** (consolidated). Track implementation there; this ticket will move to
   In Review with a link to the shared PR when the feature ships.
   ```
3. **Lead**: post a roster comment if absent:
   ```
   <!-- ggx-bundle:lead:v1 -->
   Feature bundle lead. Bundled tickets: <lead>, <sibling...>. Implemented on one
   branch (feat/<lead>) as a single consolidated OpenSpec change; ships as one PR.
   ```

### Step 4: Port → spec-review → dev on the lead (the `/ggx-work` flow, single-ticket)

#### 4a. Scaffold + seed the consolidated PRD  (FRESH phase)
- Write `<prd-text>` to a temp file (`mktemp`). Run
  `/port:start --ticket:<lead> --prd-file:<tmp>` (+ `--auto` when `--auto`). This
  creates the `feat/<lead>` worktree, scaffolds ONE `openspec/changes/<name>/`, and
  writes `<name>/.port/prd.md` (`port/start.md` Step 10). Hold `<name>`.
- Do NOT pass `--recreate` on resume — Step 1.5 already routed resumes past 4a.

#### 4b. Port the consolidated feature  (FRESH continuation / PORT-RESUME)
- Run `/port:ff <lead>` (+ `--auto`). It resumes from the scaffold:
  explore (reads `.port/prd.md`, cross-references native source for the union) →
  plan → synth → revise → ship. Result: ONE change covering all N; lead → `need-spec-review`.
- **Source-tag propagation.** The consolidated PRD already carries `Source: <id>`
  lines. Require synthesis to preserve a `Source: <ticket-id>` annotation on each
  requirement/scenario it writes into `openspec/changes/<name>/specs/**/spec.md`
  (the PRD lines + the next step's gate enforce this — if synth drops them, 4c STOPs).

#### 4c. COVERAGE GATE  (hard — the traceability buy-back)
After port:ff reaches `need-spec-review`, assert every ticket's requirement survived
consolidation. In the lead worktree:
```bash
spec_glob="openspec/changes/<name>/specs"
missing=""
for id in <all>; do
  grep -rqiE "Source:[^\n]*\b${id}\b" "$spec_glob" 2>/dev/null || missing="$missing $id"
done
[ -z "$missing" ] || abort "Coverage gate FAILED — consolidation dropped requirement(s) for:$missing. Re-run /port:ff <lead> --force after enriching .port/prd.md, or split the missing ticket(s) out of the bundle."
```
Pure-grep, `/spec-lint`-style. A miss means the consolidated spec does not represent
that sibling's requirement → STOP rather than ship an incomplete feature.

#### 4d. PAUSE for human spec-review  (REVIEW-PAUSE phase)
When the lead is `need-spec-review`, PAUSE (exit 0 — a designed pause, mirrors
`/ggx-work` Step 4.4a). Print:
```
Bundle <lead> +N: feature ported as one consolidated change, awaiting human spec review.

Review the consolidated spec (Source: tags show which ticket each requirement came from):

    /spec-review <lead>

When it flips to ready-to-dev, re-invoke to enter the dev phase:

    /ggx-bundle <lead> <sibling...>

[ggx-bundle-result] outcome=ported-paused lead=<lead> tickets=<count>
```
Do NOT post extra Linear comments here — `/port:ship` already posted the lead's
handoff comment and `/spec-review` is the next human action.

#### 4e. Implement + ship  (DEV phase)
When the lead is `ready-to-dev`, run `/dev:ff <lead>` (+ `--auto`). Because there is
ONE change, this is the ordinary single-ticket dev flow: apply → verify → review →
`/dev:ship` (archives the one change — **one archive, no cross-change conflict** —
opens ONE draft PR on `feat/<lead>`, lead → In Review). Hold `<pr-url>`.
- `/dev:ff` derives the ticket id from the branch (`grep -oE '[A-Z]+-[0-9]+'`), so the
  PR + reports + status all key on the lead correctly.

### Step 5: Multi-ticket fan-out  (FAN-OUT phase; post-ship — idempotent)

The lead PR exists and the lead is In Review. Finalize the siblings.

1. **Augment the PR body.** Add (if absent) a section listing the whole set so Linear
   auto-links every ticket and humans see the coverage:
   ```
   ## Bundled tickets
   Implements as one consolidated feature: <lead>, <sibling...>
   ```
   `gh pr edit "feat/<lead>" --body "<augmented body>"` (read current body first; skip if the section is already present).
2. **Each sibling** (read-before-write): if status is already `In Review` AND a
   `<!-- ggx-bundle:ship:v1 -->` comment exists, SKIP. Else `save_issue` to set status
   `In Review` and drop `dispatcher-dev-in-flight` if present, then post:
   ```
   <!-- ggx-bundle:ship:v1 -->
   Implemented & shipped as part of feature bundle **<lead>** — PR <pr-url>.
   This ticket's requirement(s) in the consolidated spec (by Source tag):
   <quote the Source-tagged requirement headers for this id from openspec/changes/<name>/specs/**/spec.md>
   ```

### Step 6: Report + stop

Write `claude-reports/<lead>/feature-bundle.md`: the ticket list, the consolidated
`<name>`, a **coverage table (sibling → spec requirement header(s))**, the shared
branch, the PR URL, and per-ticket status. Then print:
```
Bundle <lead> +N complete.
PR     : <pr-url>
Tickets: <lead> In Review, <sibling> In Review, ...
Report : claude-reports/<lead>/feature-bundle.md
[ggx-bundle-result] outcome=shipped lead=<lead> tickets=<count>
```
Exit 0.

---

## Worked example

```
# Fresh: DAF-501/502/503 are assigned to me, labeled port + ready-to-port.
/ggx-bundle DAF-501 DAF-502 DAF-503
  Step 1/1.5 : phase FRESH (no feat/DAF-501 branch, lead is ready-to-port)
  Step 2     : read all 3 descriptions → one consolidated PRD, each requirement
               tagged `Source: DAF-50x`; show PRD → you confirm
  Step 3     : assign me + drop ready-to-port on all 3; 502/503 relatedTo 501 +
               claim comments; 501 roster comment
  Step 4a/b  : /port:start --prd-file → /port:ff DAF-501 → ONE change → need-spec-review
  Step 4c    : coverage gate — DAF-501/502/503 all found in specs/**/spec.md ✓
  Step 4d    : PAUSE → run /spec-review DAF-501. exit 0.

  (human) /spec-review DAF-501  → flips to ready-to-dev

/ggx-bundle DAF-501 DAF-502 DAF-503
  Step 1.5   : phase DEV (lead ready-to-dev)
  Step 4e    : /dev:ff DAF-501 → apply → verify → review → /dev:ship
               (one archive) → ONE draft PR on feat/DAF-501 → DAF-501 In Review
  Step 5     : PR body lists DAF-501/502/503; 502/503 → In Review + ship comments
  Step 6     : coverage table written. Bundle DAF-501 +2 complete. exit 0.
```

---

## Guardrails

- **Entry is `ready-to-port`; this skill ports the consolidated feature itself.**
  Do NOT pre-port via `/ggx-dispatcher` (that yields N separate changes — the exact
  thing this skill collapses). The FRESH-phase entry gate enforces `ready-to-port` +
  assigned-to-me + `port` classification.
- **One feature, one change, one archive, one PR.** Consolidation is what removes the
  multi-archive overlap risk — never reintroduce N separate changes on the branch.
- **Coverage gate is mandatory (Step 4c).** Every bundled ticket id MUST appear via a
  `Source:` tag in the shipped spec. A miss STOPs — shipping a feature that silently
  dropped a ticket's requirement is the worst outcome and this gate prevents it.
- **spec-review is a mandatory human gate.** Step 4d PAUSES even under `--auto` — an
  LLM-synthesized consolidated spec is never implemented without human review.
- **Consolidation is an LLM synthesis surface.** Mitigated by the coverage gate + the
  human spec-review; never weaken either to "save a step".
- **Per-ticket traceability lives in three places**, not in commits: `Source:` tags in
  the spec, Linear `relatedTo` relations + claim/ship comments, and the PR body roster.
  Per `commit.md`, commits carry no ticket ids.
- **Re-entrant, no state file.** Step 1.5 re-derives the phase from the lead's labels +
  worktree + PR. Re-invoking after the spec-review pause (or any STOP) resumes.
- **Never call `/dev:ff` before the lead is `ready-to-dev`**, and never re-run
  `/port:start --recreate` on resume — Step 1.5 routes resumes to the correct step.
- **Idempotent fan-out.** Step 5 reads before writing; a re-run finishes only the
  not-yet-In-Review / not-yet-commented siblings and skips PR-body augmentation if present.
- **Never write `dispatcher-*-in-flight`.** That label is exclusively the dispatcher's
  resume signal (Plan X); `/ggx-bundle` only ever *removes* it in the Step 5 fan-out.
- **All user-facing output is English.** Per repo convention.
