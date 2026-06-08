# Implementation Plan: Integrate `/ui-tweak` into `/ggx-work` + `/ggx-dispatcher` (the `design bug` lane)

> Provenance: produced by a planning workflow (4 parallel subsystem readers → design synthesis → ai-expert adversarial review → revision). All `file:line` citations verified against the working tree on branch `dev-ui-tweak-into-workflow`.
>
> **Status: IMPLEMENTED** (same branch). Deltas from this plan, found by the post-implementation
> ai-expert diff review:
> 1. **`commands/design/ui-tweak/preview.md` was missing from §5's edit list** — its `--auto`
>    section claimed preview is unreachable under `--auto`, contradicting the revised D7. Fixed:
>    `--auto` reaches preview in direct-ship build-only mode (the load-bearing compile gate).
> 2. **`--auto` build-fail contract resolved**: build-fail keeps the SAME max-3 agent repair loop
>    as interactive (the loop needs no card, so it is `--auto`-safe); at `repair-count >= 3` the
>    ff dispatch loop loud-fails (stderr + non-zero). Audit BLOCKED stays loud-fail-immediately
>    with NO repair loop (R4 default) — a logic finding is not a mechanical fix.
> 3. **ARCHITECTURE.md edit dropped** — it is a stakeholder one-pager with no lane enumeration;
>    adding pipeline-lane detail there would be out of place.
> 4. Follow-up (not v1): emit a machine-readable `reason=` token on the four `--auto` loud-fail
>    lines (`repair-exhausted` / `not-deliverable` / `audit-blocked` / `build-fail`) and widen the
>    dispatcher §6.1 parse so the §6.5 digest's `reason=` field is populated for inline ui-tweak
>    failures.

---

## 1. Goal & non-goals

**Goal.** Add a fifth routing lane, `ui-tweak`, so that:
1. `/ggx-work <ID>` (single-ticket orchestrator) routes a `design bug` ticket to `/ui-tweak:ff` instead of `/bug:ff`.
2. The same routing works when `/ggx-dispatcher` fans the ticket out as a `general-purpose` subagent running `/ggx-work <ID> --auto`.
3. The discriminator is the Linear classification label **`design bug`**: its presence ⇒ `ui-tweak` lane, evaluated **before and overriding** the canonical `{bug,port,feature}` set-count, **regardless of which other canonical labels co-occur** (see §2.1 for the full precedence rule).
4. An unattended `/ui-tweak:ff <ID> --auto` run reaches a **draft PR** (audited + committed), terminating exactly like `/dev:ff`/`/bug:ff` do, so the dispatcher batch can carry a design bug end-to-end. The draft PR is the human gate.

**Non-goals.**
- No Jira `ui-tweak` lane. Jira has no `design bug` classification and `/_ticket-init lane=port` is already Jira-rejected; ui-tweak stays **Linear-only**, mirroring `port`. (route.md Step 3 Jira table; `_ticket-init.md:76`.)
- Do NOT remove or weaken the audit dual-judge requirement (audit.md:52-57; ui-verify-agent.md / dev-reviewer.md frontmatter). The whole skill's only logic enforcement is that panel. **Tier-decorrelation is preserved as the default** — see §4.
- Do NOT make `--auto` reach a *device* preview. Autonomous runs use the existing build-only compile gate (direct-ship, R20). No emulator/simulator in the dispatcher path.
- Do NOT add a new dispatcher concurrency budget, lock namespace, or new `ready-*` workflow label (default: reuse `ready-to-dev` / `dispatcher-dev-in-flight`).
- No draft→ready, no merge, no ticket status mutation beyond what the dev lane already does. Terminal is a draft PR.

---

## 2. Routing design

### 2.1 `/route` decision-table changes (label precedence: `design bug` overrides the canonical-set count)

**Step 3 — Linear lane derivation (route.md:112-126).** Today the canonical set is `{bug, port, feature}`, whole-string case-insensitive; "two or three of the three" and "zero of the three" both resolve to `unknown` (route.md:118-120). Insert a **precedence rule evaluated BEFORE the canonical-set count**:

> **`design bug` precedence (evaluated first).** If `<labels>` (each name lowercased, whole-string) contains **`design bug`** → `<lane> = ui-tweak`, **full stop** — this is evaluated before and overrides the canonical-set count, **regardless of which other canonical labels co-occur** (`bug`, `feature`, `port`, none, or several). Only if `design bug` is absent do we fall through to the existing canonical `{bug,port,feature}` match.

This single rule resolves every combination: `design bug`+`bug` → ui-tweak; `design bug`+`feature` → ui-tweak; `design bug`+`port` → ui-tweak; `design bug` alone (no canonical label, today→`unknown`) → ui-tweak. State all four outcomes explicitly in route.md so there is no silent fall-through.

- Amend the existing note at **route.md:125-126** ("a label like `Design bug` does NOT match … not a substring") — it must now say `design bug` is a recognized classification label routing to the `ui-tweak` lane, matched whole-string case-insensitive (`Design bug`/`design bug`/`DESIGN BUG` all match), evaluated with precedence over the canonical set.
- Add a row to the match-shape table: `contains "design bug"` → `ui-tweak` (**precedence — wins over any canonical co-label**), placed visually above the existing three rows.

**Step 3 — Jira table (route.md:128+).** No change to the mapping, but add an explicit exclusion sentence: *"Jira has no `ui-tweak` lane (no `design bug` issuetype); design-bug routing is Linear-only, like `port`."*

**Step 3 — UNKNOWN_LANE `AskUserQuestion` options.** Add `ui-tweak` to the Linear interactive option set (bug/port/feature/ui-tweak). Jira options unchanged (bug/feature).

**NEW Step 4.ui-tweak** (parallel to Step 4.bug):
- `recommended_command = "/ui-tweak:ff <ticket-id>"`, `phase = ui-tweak`.
- `next_after_recommended = "(none — /ui-tweak:ff terminates at a draft PR)"`.
- Mirror bug's done-detection: if `gh pr view <id>` state == `OPEN` OR Linear status == `In Review` → `recommended_command = "(none — /ui-tweak:ff terminates at its draft PR)"`, `phase = done`.
- No port/spec-review gate.

**Step 4.port Jira guard.** Add an analogous defensive guard so a `jira` + `ui-tweak` derivation (cannot happen) emits `Status: UNKNOWN_LANE` rather than recommending a Linear-only command.

**Front-matter description + opening blurb.** Add `/ui-tweak:ff` to the enumerated entry-point commands.

### 2.2 `/ggx-work` loop changes

**Step 2.5 lane derivation (ggx-work.md:125-141) — MUST learn the `design bug` discriminator independently.** This is a BLOCKER fix. Today Step 2.5 item 1 (ggx-work.md:126-131) re-derives the lane *itself* from `{bug,port,feature}` and **skips the entire Step 2.5 on "zero or multiple"**. Without change, a `design bug`-only ticket (zero canonical labels) → Step 2.5 skipped → `/_ticket-init` never called → ticket never moved to In Progress, `ready-to-dev` never dropped → the dispatcher state machine breaks. Fix:

- In Step 2.5 item 1 (Linear branch), **add the `design bug` precedence rule first**, mirroring route.md: if `<labels>` contains `design bug` → `<lane> = ui-tweak` (regardless of co-labels), and do NOT skip Step 2.5. Only fall through to the `{bug,port,feature}` exactly-one check if `design bug` is absent.
- In Step 2.5 item 2 (lane→`/_ticket-init` arg map, ggx-work.md:139-141), add **`ui-tweak → dev`** alongside `port→port`, `bug`/`feature`→`dev`. Rationale: `/_ticket-init` hard-rejects any lane other than `port`/`dev` (`_ticket-init.md:56-59`); ui-tweak flows through a worktree + ship like dev. Add a comment: "ui-tweak maps to `dev` for init — `/_ticket-init` only accepts port/dev."

> **Tension to document (Risk R3):** ui-tweak's own `start.md` deliberately does NOT call `/_ticket-init` (`/ui-tweak:start` is read-only on the ticket). `/ggx-work` Step 2.5 calling `/_ticket-init lane=dev` **before** dispatching `/ui-tweak:ff` means the ticket DOES move to In Progress + assignee→self + `ready-to-dev` dropped + starting comment, in the dispatcher/ggx-work path. This is intended: the lifecycle write belongs to `/ggx-work` (as for dev/bug today), `start.md` stays read-only. No contradiction — the write happens one level up.

**Step 3.3 classification table.** Add row: `^/ui-tweak:ff ` → Pipeline (Step 4.4). Without it, `/ui-tweak:ff …` falls to `anything else` → Step 4.3 `unrecognized-recommendation` abort.

**Step 4.4 spawn-cmd builder (ggx-work.md:347-352).** Already generic (`<spawn-cmd> = <recommended_command>` + ` --auto` + optional ` --no-ticket-init`). No structural change — but note the spawn builder **appends `--no-ticket-init`** when set, and `/ui-tweak:ff` does not parse it today. See §5 items 5+6 for the accept-and-ignore wiring in BOTH ff.md and start.md.

**Step 4.4a port→spec-review short-circuit.** ui-tweak is dev-like (terminates at PR-open, no mid-pipeline human gate) so it must NOT trigger the Step 4.4a short-circuit. Step 4.4a is already gated on "spawned pipeline was `/port:ff`", so ui-tweak is excluded — **extend the "Why this does NOT fire for /dev:ff or /bug:ff" note** to also name `/ui-tweak:ff`. A successful `/ui-tweak:ff --auto` then **continues the loop** → next `/route` sees PR OPEN → Step 4.ui-tweak done-detection → `(none…)` → Terminal `outcome=done`.

**Front-matter + usage + guardrails.** Add `/ui-tweak:ff` to every enumeration.

### 2.3 `/ticket-analyze` + `/ggx-dispatcher` sweep — which `ready-*` label?

**Decision: reuse `ready-to-dev` + `dispatcher-dev-in-flight`. Do NOT introduce a new `ready-to-ui-tweak` workflow label or a new lock namespace.**

Rationale: the dispatcher is **lane-agnostic at spawn time** — it sweeps the workflow labels `ready-to-dev`/`ready-to-port`, race-locks, and spawns the uniform `/ggx-work <ID> --auto`; the *classification* lane is read INSIDE the worker by `/route`. So a `design bug` ticket carrying `ready-to-dev` already flows through the sweep — **except** the dispatcher must now understand the ui-tweak lane for outcome derivation (§5 item 8). One lock budget, one digest lane.

**`/ticket-analyze` changes — MUST resolve a `design bug`-only ticket to a lane.** This is a MAJOR fix. Today ticket-analyze derives lane from `{bug,port,feature}` exactly-one (ticket-analyze.md:191-194); a `design bug`-only ticket (no `bug` label) resolves to no lane → `need-revision` "no single classification label," and never gets `ready-to-dev` → the dispatcher sweep never sees it. Fix:

- **Add the `design bug` precedence rule to the Step-2 lane derivation (ticket-analyze.md:191-194), mirroring route.md and ggx-work.md**: `design bug` present (with or without any canonical co-label) → `<lane> = ui-tweak`, evaluated first. Only fall through to `{bug,port,feature}` exactly-one if absent.
- **Add a `ui-tweak` completeness checklist** (Step 3, after the bug checklist): a design-bug ticket needs (a) what visual/layout change is wanted, (b) where (screen/component), (c) ideally a Figma link or before/after — NOT reproduction-steps like a logic bug.
- Decision matrix: `ui-tweak` lane + complete + unblocked → **`ready-to-dev`** (same as bug/feature — no new label).
- §1.5.5 re-analysis skip filter, §8.1 label-ensure list, §8.4 mutually-exclusive rewrite set, §A comment-schema lane enum: add `ui-tweak` as a recognized lane value (it writes the *workflow* label `ready-to-dev`, never the classification label `design bug` — classification is human-owned).

**`/ggx-dispatcher` changes:** because we reuse `ready-to-dev`, the **sweep (Step 2), lock (§4.1), spawn (§5.1) need NO change** for dev/bug tickets — but design-bug tickets get a lane-specific branch (see §4.3 and §5 item 8).

---

## 3. ui-tweak autonomous mode design (`/ui-tweak:ff <ID> --auto`)

Today `--auto` is a structural dead-end: it splits the worktree, runs `apply`, the walker would resolve to C1 (show-me), but `--auto` suppresses the card, so it STOPS with an un-built, un-audited, un-committed diff (ff.md:43-45, 82-84). To be dispatcher-usable, `--auto` must auto-traverse the cards that today only humans drive. The downstream machinery already exists — only the auto-decision is missing.

### 3.1 The one auto-decision: auto-write `deliver` + `direct-ship` after `apply`

The only structural gap is the suppressed **C1 (show-me)** decision. Under `--auto`, after `apply` writes `base_ref` and the walker would resolve to C1, the orchestrator must instead **auto-pick the R20 direct-ship path**: write BOTH `.dev/ui-tweak/deliver` AND `.dev/ui-tweak/direct-ship`, then re-enter the walker.

This is exactly the existing C1 "It already looks right — ship it" branch (ff.md ~320), which already writes both markers. We auto-select that human choice: add an `--auto` branch in ff.md's dispatch loop at the iteration-terminal point — instead of `AskUserQuestion`, write `deliver`+`direct-ship` and continue.

Downstream is then fully cardless and already reachable:
- Walker sees `deliver` + `direct-ship` + `build-pass != PASS` → **`preview` in direct-ship mode** = build-only compile gate (no device cascade, no `preview-shown`, no card). Satisfies "cannot ship broken code" with a pure compile gate. **Reuses direct-ship machinery — no new gate invented.**
- Build PASS → walker → **`audit`** (dual-judge panel — see §4 for the spawn-model handling). BLOCKED under `--auto`: see §4.4.
- CLEAR → **`commit`** → (demo skipped: `demo-requested` never written under `--auto`) → **`pr`** (`/pull-request --draft`, pre-built body, read-only ticket comment `🎨 UI tweak ready for engineer review`) → **`review`** (`/code-review`) → terminal **C5** (suppressed under `--auto`; walker resolves `done`, ggx-work reads PR OPEN).

### 3.2 Invariant handling (R18/R19/R20)

- **R19 (up-front worktree split):** preserved. `/ui-tweak:start` still splits `../<id>` up-front. `--auto` already requires a work-item id (ff.md:122-124) — guaranteed by the dispatcher.
- **R20 (direct-ship build-only gate):** preserved and is the *load-bearing reuse*. Autonomous mode IS a direct-ship run with the human decision auto-supplied.
- **R18 (two-phase: iteration build-free → ship):** the iteration phase collapses to a single `apply` under `--auto` (no designer corrections), then immediately enters deliver. This RELAXES R18's "designer iterates first" intent. **Call-out:** under `--auto` there is exactly one `apply` then deliver; corrections (which require a human reply) cannot occur. Acceptable because the audit panel + draft-PR human review are the safety net, identical to how `/dev:ff --auto` produces a first-pass diff a human reviews on the PR.
- **D7 ("`--auto` can never reach a PR"):** this is the **one invariant we deliberately relax**, and it must be rewritten, not silently broken. The new contract: `--auto` reaches a **draft PR only** (never draft→ready, never merge, never ticket-status-beyond-In-Review), and only because the dispatcher/ggx-work workflow explicitly opts in. **All SIX D7 sites must be rewritten:** ff.md:3 (frontmatter `description`), ff.md:44, ff.md:82-84, ff.md:124, ff.md:151 (walker comment), ff.md:471 (Constraints — note this line ALSO says "Not wired into /route / /ggx-work / /ggx-dispatcher", which becomes **false** and must be replaced with the wired-in statement), plus SKILL.md:99. The human review of the draft PR replaces the in-pipeline card as the gate (see §6).

### 3.3 End state of an autonomous run

Audited (dual-judge CLEAR) + formatted + committed + **draft PR open** + read-only ticket comment + status settled to In Review (via the dispatcher §6.2 dev-`done` fallback — see Risk R5; ui-tweak's `pr` stage does NOT transition status today, and `/ggx-work` Step 2.5 already moved it to In Progress). Outcome line emitted by ggx-work: `[ggx-work-result] outcome=done ticket=<id>`.

---

## 4. Nested-spawn analysis & spawn-model decision

### 4.1 Is it a real problem? — YES.

The dispatcher spawns `/ggx-work <ID> --auto` as a `general-purpose` subagent. Inside such a subagent, **nested opus spawns are unreliable/unavailable; nested sonnet spawns are known to work in practice** — verbatim precedent at **`commands/dev/dev/apply.md:17`** ("nested sonnet spawns are known to work") and the dispatcher's own statement at **`commands/dev/ggx-dispatcher.md:474`** ("nested sonnet spawns from a subagent are known to work in practice"); `/dev:verify` unconditionally spawns the sonnet verify-agent on the dispatcher path.

The ui-tweak audit panel (audit.md:52-57) spawns BOTH judges in parallel via the Agent tool:
- **`ui-verify-agent` — `model: sonnet`** (confirmed frontmatter). → known-working nested class. Same tier as verify-agent.
- **`dev-reviewer` — `model: opus`** (confirmed frontmatter). → the avoided class. This is the spawn that breaks from inside a dispatcher worker.

So the problem is real but **narrow**: only the *opus* judge (`dev-reviewer`) is at risk nested.

A decisive caveat: today the audit stage is **unreachable under `--auto`** (audit.md:99: "`--auto` cannot reach this stage normally"). Once §3 makes `--auto` reach audit, the constraint becomes live. This section therefore gates on the §3 change.

### 4.2 The decorrelation tension (why "just inline a judge" is wrong)

The naive fix — inline `dev-reviewer`'s judgement into the worker session (the pattern used for authoring agents like dev-agent/synth-agent) — is **forbidden here**. Those are *authoring* agents: the orchestrator IS the author, so inlining loses no independence property. `dev-reviewer` is an *auditor*. Inlining it would run the behavior-lens audit **in the same session that produced the diff** — exactly the CAF-467 self-audit failure the whole split exists to prevent (dev-agent.md:86; verify.md:8 "Same-session self-audit cannot catch the misses it produced"). dev-reviewer.md frontmatter requires it be "structurally incapable of mutating what it audits and … independent of the party that produced the change." **Inlining a judge into the authoring session is off the table.**

### 4.3 Chosen solution: run the design-bug lane **inline in the dispatcher main session**

**Decision: when the swept `ready-to-dev` ticket carries the `design bug` classification label, the dispatcher does NOT spawn a nested `/ggx-work … --auto` subagent for it. Instead the dispatcher runs the design-bug lane INLINE in its own main session**, where opus nesting works freely, so the audit panel keeps **ui-verify-agent (sonnet) + dev-reviewer (opus)** exactly as in interactive mode — **full tier-decorrelation retained, no override mechanism, no exported walker.**

This is the cleanest path because:
- A dispatcher *main session* nests opus freely (same property the default human "Ship it" path relies on) — the entire nested-opus problem evaporates for ui-tweak.
- The audit guarantee is preserved verbatim: both judges, both tiers, both spawns, both-must-be-CLEAR. No new audit mode table, no `model` override, no weakened decorrelation.
- It deletes a large fraction of the edit surface: no `infer_ui_stage_safe` export, no walker-marker probe inside a nested worker, no audit.md mode table.

**Cost / surface this introduces (stated honestly):**
- The dispatcher gains ONE lane-specific branch in its spawn fan-out: "if the swept `ready-to-dev` ticket classifies as `design bug` (read the classification label during the sweep, which the dispatcher already fetches), run the ui-tweak lane inline rather than spawning a `/ggx-work` subagent." This is a deviation from the "uniform spawn shape for all lanes" property — acknowledged as the price of keeping the audit guarantee intact (Risk R1).
- Design bugs run **serially within the dispatcher main session**, not in parallel with the spawned batch. For v1 batch sizes this is acceptable (design bugs are expected to be a minority); revisit if design-bug volume grows (Risk R7).
- The inline path still goes through `/route` for the decision and runs the **ui-tweak walker in-session** (`infer_ui_stage` as-is, no safe-wrapper export needed because it is not crossing the worker/dispatcher boundary).

### 4.4 `--auto` audit BLOCKED behavior

Today under `--auto`, audit BLOCKED prints the loud stderr line and exits non-zero with **no repair loop** (audit.md:97-105). Default recommendation: **keep loud-fail-no-repair for v1** (simplest, matches current contract). The inline lane classifies it `failed`, leaves `dispatcher-dev-in-flight` set as the resume signal, and a human picks it up. Enabling the bounded max-3 repair loop is a fast-follow once v1 is proven (Risk R4).

### 4.5 Rejected alternatives

- **(R-A) Inline `dev-reviewer` into the *authoring* worker session.** Rejected — destroys auditor independence (the CAF-467 self-audit failure; §4.2). Note this is distinct from §4.3's chosen R-C: R-C runs the lane in the *dispatcher main session* and still spawns BOTH judges as separate subagents off that main session — the auditor never shares the authoring context, so independence is fully preserved.
- **(R-B) Dispatcher-level audit callback** (nested worker signals "ready for audit", dispatcher spawns the opus judge, resumes the worker). Rejected — requires a brand-new worker↔dispatcher handshake protocol, a pause/resume marker, and breaks the lane-agnostic-spawn architecture. Far more surface area than R-C.
- **(R-D) Sonnet-override for `dev-reviewer` under `--auto`** (spawn the worker normally, but pass `dev-reviewer` with a `model: "sonnet"` override on the `--auto` path). **Demoted to fallback**: it weakens tier-decorrelation — `dev-reviewer.md:3` treats the tier-pin as a named decorrelation axis ("so the two judges' misses are not positively correlated"); two sonnet judges have correlated blind spots on exactly the subtle logic-vs-UI calls the panel exists to catch. Also drags in a model-override mechanism + an exported walker. Retained as the fallback if R-C's serial design bugs unacceptably bottleneck the batch. See Risk R1.
- **(R-E) Drop `dev-reviewer` entirely under `--auto`, run ui-verify-agent only.** Rejected — violates "neither judge may be skipped" (audit.md:52-53; SKILL.md guarantee). Halves the panel.

---

## 5. File-by-file edit list (dependency order)

> Spawn-model note: this list reflects **R-C (inline-in-dispatcher-main-session) as primary** (§4.3). The sonnet-override-specific edits are listed as **conditional / fallback-only** and are NOT part of the v1 edit set unless R-C is abandoned for R-D.

1. **`commands/dev/_ticket-lib.md`** — Add `ui-tweak` to the documented Linear classification-label → lane mapping (`design bug` whole-string, case-insensitive → `ui-tweak`, **precedence over the canonical set**), Linear-only; note Jira has no equivalent, and note the `design bug` label is human-owned and must exist in the workspace (Risk R6). *(Shared lib read by route + ggx-work + ticket-analyze — the single source of the precedence rule the other three files reference.)*

2. **`commands/dev/route.md`** — All §2.1 edits: the full `design bug` precedence rule (overrides canonical-set count, covers all co-label combinations + `design bug`-alone), amend the route.md:125-126 note, new Step 4.ui-tweak branch, Jira exclusion + defensive guard, UNKNOWN_LANE option, front-matter/blurb enumeration. *(Depends on _ticket-lib.)*

3. **`commands/dev/ggx-work.md`** — All §2.2 edits: Step 2.5 item 1 **add `design bug` precedence so a `design bug`-only ticket is NOT skipped** (BLOCKER), Step 2.5 item 2 lane-arg map `ui-tweak→dev` + comment, Step 3.3 row `^/ui-tweak:ff ` → Pipeline, Step 4.4a exclusion-note extension to name `/ui-tweak:ff`, front-matter/usage/guardrail enumeration. *(Depends on route + _ticket-lib.)*

4. **`commands/dev/ticket-analyze.md`** — §2.3 edits: **add `design bug` precedence to the Step-2 lane derivation so a `design bug`-only ticket resolves to `ui-tweak`, not `need-revision`** (MAJOR), ui-tweak completeness checklist (Step 3), decision-matrix row ui-tweak+complete+unblocked → `ready-to-dev`, §1.5.5 / §8.1 / §8.4 / §A lane-enum additions. Never writes the `design bug` classification label (human-owned). *(Depends on _ticket-lib lane vocabulary.)*

5. **`commands/design/ui-tweak/ff.md`** — (a) Add the `--auto` auto-decision in the dispatch loop: at the iteration-terminal (C1 show-me) point, under `--auto` write `.dev/ui-tweak/deliver`+`.dev/ui-tweak/direct-ship` and continue (§3.1) instead of rendering the card. (b) Rewrite D7 / the `--auto` contract at **all six sites**: frontmatter `description` (line 3), line 44, lines 82-84, line 124, the walker comment (line 151), and the Constraints block (line 471 — replace the now-false "Not wired into /route / /ggx-work / /ggx-dispatcher" with the wired-in draft-PR-terminal statement). (c) **Accept-and-ignore `--no-ticket-init`** as a no-op flag: ui-tweak never inits, but the ggx-work spawn builder may append it. Ensure ff.md parses-and-discards it AND does not forward an unrecognized flag to `/ui-tweak:start` — either strip it before the Step-0 `start` dispatch or pass it through to a start.md that also ignores it (see item 6).

6. **`commands/design/ui-tweak/start.md`** — **Accept-and-ignore `--no-ticket-init`**. ff.md Step 0 dispatches `/ui-tweak:start <id> [--auto]`; if `--no-ticket-init` is forwarded, `start.md` must swallow it as a no-op (start.md already does no `/_ticket-init`, so it is semantically a no-op — just ensure it does not error on the unrecognized flag). Simplest implementation: ff.md strips the flag before the start dispatch (item 5c); this item is the belt-and-suspenders guarantee in case it is forwarded.

7. **`skills/design/ui-tweak/SKILL.md`** — Update the `--auto` guarantee wording (SKILL.md:99) to the new draft-PR-terminal contract; reaffirm "both judges always run, neither skipped" holds in `--auto` (under R-C: still sonnet + **opus**, full panel).

8. **`commands/dev/ggx-dispatcher.md`** — **Primary (R-C) edits:**
   - **Sweep/classify (§6.2 and the spawn fan-out):** during the existing per-ticket Linear re-fetch (ggx-dispatcher.md:595-601, which already reads `labels[]`), detect the `design bug` classification label. For a `design bug` ticket, run the ui-tweak lane **inline in the dispatcher main session** rather than spawning a `/ggx-work … --auto` subagent (§4.3). This is the one lane-specific branch in the otherwise-uniform fan-out (Risk R1).
   - **Walker selection (§6.2, ggx-dispatcher.md:600-608):** the inline ui-tweak lane uses the **ui-tweak walker in-session** (`infer_ui_stage`, sourced from ff.md as-is — no cross-boundary export). The dev-lane walker-selection (which today picks `infer_dev_stage`/`infer_bug_stage_safe` purely by lane tag) is **untouched** for spawned dev/bug workers, because ui-tweak no longer rides a spawned dev worker.
   - **Outcome derivation (§6.2, ggx-dispatcher.md:617-620) — BLOCKER:** the dev-lane `outcome=done` rule is hard-coded to `infer_dev_stage`'s `done` predicate, which requires `openspec/changes/archive/<n>` (`commands/dev/dev/ff.md:107-110`) — a ui-tweak worktree NEVER creates that, so a shipped design bug would always fall through to the ambiguous-combination branch (ggx-dispatcher.md:638-646) and be misclassified `failed`. For the inline ui-tweak lane, use a **ui-tweak-specific outcome rule**: `outcome=done ⟺ ui-tweak-walker == done AND pr_state==OPEN AND claude-reports/<id>/code-review.md present` (the ui-tweak walker's own `done` predicate, ff.md:176); `outcome=failed ⟺ not-done AND dispatcher-dev-in-flight ∈ labels`. This must be a distinct branch, not a reuse of the dev `done` rule.
   - **Status fallback (§6.2 step 5):** the inline ui-tweak `done` path writes the same dev-`done` fallback (ensure status In Review, `dispatcher-dev-in-flight ∉ labels`) — settles ticket status since ui-tweak's `pr` stage does not (Risk R5).
   - **Digest (§6.4/§6.5):** render ui-tweak under the existing dev-lane row/legend (no new outcome token → no `_slack-notify.md` change). **One-line check:** confirm §6.4/§6.5 do not separately read the classification label to print a per-lane name (which would mislabel a design-bug ticket "bug"/"feature" in the digest); if they do, map `design bug` → the dev-lane display row consistently.
   - **NO** Step 2 query / §4.1 lock / §5.1 spawn-target change (reusing `ready-to-dev`).

9. **`commands/design/ui-tweak/audit.md`** — Update the `--auto` section (audit.md:97-105) to reflect that audit is now **reachable under `--auto`** (via the inline lane) and decide BLOCKED behavior (default: keep loud-fail-no-repair, Risk R4). **Under R-C, the panel is unchanged** — ui-verify-agent (sonnet) + dev-reviewer (opus), both spawned off the dispatcher main session, full tier-decorrelation. **No mode table / no model override is added** (that is the R-D fallback only).

10. **`agents/design/ui-verify-agent.md`** — Fix the internally-contradictory frontmatter: line 3 says "on the final cumulative diff" while body line 9 says "before the build runs." Once `--auto` reaches audit post-preview (build already ran), "before the build runs" is doubly wrong. Correct the body wording to "after preview's build, on the final cumulative diff." Low risk; fix alongside.

11. **`ARCHITECTURE.md`** — Add one line to any lane enumeration / rollout note: the `design bug` lane now flows through the dispatcher (inline in the main session) and terminates at a draft PR. Light touch.

**Conditional / fallback-only edits (NOT in v1 unless R-C is abandoned for R-D):**

- **(F1) `agents/design/dev-reviewer.md`** — add a note that the model tier is overridable to sonnet ONLY on the `--auto`/dispatcher path. *Only if R-D.*
- **(F2) `commands/design/ui-tweak/audit.md` mode table** — `--auto` spawns `dev-reviewer` with `model: "sonnet"` override. *Only if R-D.*
- **(F3) `commands/design/ui-tweak/ff.md` `infer_ui_stage_safe` wrapper** + **(F4) `agents/AGENTS.md` walker registration** — needed only if a *nested worker* must export the walker to the dispatcher (the R-D spawn model). Under R-C the walker stays in-session. *(Also confirm the no-`state.json` CI grep still passes — ui-tweak uses filesystem markers, no state.json — regardless of path.)*

---

## 6. HITL gates kept

The repo owner deliberately keeps HITL gates. None are silently removed.

1. **The draft PR IS the gate (autonomous lane).** A `/ui-tweak:ff --auto` run terminates at a **draft PR + a read-only ticket comment `🎨 UI tweak ready for engineer review`** — never draft→ready, never merge, never beyond In Review. The human engineer reviewing/merging that draft PR is the gate replacing the suppressed designer cards. Stated explicitly in ff.md (all six D7 sites) and SKILL.md — do not euphemize ("draft PR", per the established PR vocabulary).

2. **The dual-judge audit panel is retained as a hard gate** even autonomously — both judges spawn, both must return `Status: CLEAR`, any BLOCKED reverts (audit.md:69-72). It is the SOLE logic enforcement. Under the chosen R-C spawn model it runs with **full tier-decorrelation** (sonnet UI-lens + opus behavior-lens), identical to interactive mode — NOT relaxed.

3. **Interactive `/ui-tweak` (no `--auto`) keeps every designer card unchanged** — C-WT, C1 (show-me + looks-good), C5, the engineer card Ce, plus the stage-owned C-MISDIRECT/C6. A human still drives show-me → ship-it and gets the opus-tier dev-reviewer. The autonomous path is purely additive.

4. **`/spec-review` HITL gate** is untouched — ui-tweak has no port/spec-review stage; the dev lane's spec-review path is unaffected.

5. **The engineer card Ce (`repair-count >= 3`)** survives in interactive mode. Under `--auto` it cannot render (no cards); instead `repair-count >= 3` (or audit BLOCKED, Risk R4) classifies the ticket `failed` and a human picks it up via the persisted `dispatcher-dev-in-flight` label + failure comment — a deferred human gate, not a removed one.

---

## 7. Risks & open questions

1. **R1 — R-C (inline-in-dispatcher-main-session) trades the uniform-spawn property + parallelism for a preserved audit guarantee.** R-C adds one lane-specific branch to the dispatcher fan-out and runs design bugs serially in the main session. *Recommended default:* accept this for v1 — preserving full tier-decorrelation is worth one branch and serial design-bug handling, given design bugs are expected to be a minority. **Fallback (R-D):** if serial design bugs bottleneck the batch, spawn the worker and downgrade `dev-reviewer` to sonnet via a `model` override on the `--auto` path (weaker lens-only + structural-pre-pass decorrelation), enabling the conditional edits F1–F4.

2. **R2 — Does the inline ui-tweak lane actually reach `audit` and a draft PR?** §3 makes `--auto` reach audit; this is new and unproven. *Recommended default:* gate rollout on a manual single-ticket test — run `/ggx-work <design-bug-id> --auto` directly first, confirm it reaches a draft PR; then test the dispatcher inline path on one design-bug ticket (`/ggx-dispatcher --max-parallel:1`, dry-run then live) before batch use.

3. **R3 — Double ticket-init semantics.** `/ggx-work` Step 2.5 calls `/_ticket-init lane=dev` (status→In Progress, drop `ready-to-dev`, assignee, starting comment) BEFORE `/ui-tweak:ff`, even though ui-tweak is read-only on the ticket. *Recommended default:* correct and intended — the lifecycle write belongs to `/ggx-work` (as for dev/bug), `/ui-tweak:start` stays read-only. Document the split in ggx-work.md Step 2.5 and ui-tweak/start.md.

4. **R4 — Audit BLOCKED under `--auto`: repair loop or loud-fail?** *Recommended default:* keep loud-fail-no-repair (audit.md:97-105) for v1; classify `failed`, leave `dispatcher-dev-in-flight` for human resume. Enable the bounded max-3 repair loop as a fast-follow once R2 is proven.

5. **R5 — Ticket status transition to In Review.** ui-tweak's `pr` stage posts a read-only comment but does NOT transition status; `/dev:ship` normally flips dev tickets to In Review. *Recommended default:* rely on the dispatcher §6.2 dev-`done` fallback write (ensure status In Review AND `dispatcher-dev-in-flight ∉ labels`) — applied to the inline ui-tweak `done` path (§5 item 8). Verify it fires for a ui-tweak ticket whose walker reaches `done`. For the single-ticket `/ggx-work` path (no dispatcher), `/ggx-work` does not write status either, so the ticket stays In Progress with an open draft PR; default: acceptable — the open PR is the signal; a human moves it.

6. **R6 — `design bug` label must exist in the Linear workspace.** The classification label `design bug` is human-owned and must be created before routing works. *Recommended default:* confirm the label exists (whole-string, case-insensitive `design bug`) as a setup precondition; document it in route.md / _ticket-lib.md alongside `bug`/`port`/`feature`.

7. **R7 — Design-bug parallelism under R-C.** Because the inline lane occupies the dispatcher main session, design bugs run serially against the parallel spawned batch. *Recommended default:* acceptable for v1 (design bugs a minority); revisit (switch to the R-D spawn model, or a dedicated inline queue) only if design-bug volume grows enough to bottleneck batches.

8. **R8 — Marker-collision / mis-walk guard.** A ui-tweak worktree never runs `/dev:start`, so it has **no `.dev/mode.md`**; `infer_dev_stage` treats absent `.dev/mode.md` ⇒ "feature" (`commands/dev/dev/ff.md:99`). Any path routing a ui-tweak worktree into `infer_dev_stage` would default to the feature branch and hunt for openspec dirs. **R-C structurally avoids this** — ui-tweak never rides a spawned dev worker. *Recommended default (still required):* confirm no resume path (e.g., a re-swept `ready-to-dev` ui-tweak ticket whose first run failed) can route a ui-tweak worktree into `infer_dev_stage`'s feature branch — the dispatcher must re-detect `design bug` on re-sweep and re-enter the inline lane, not the spawned dev lane. The marker *namespace* itself (`.dev/ui-tweak/*` vs `.dev/mode.md`, `.dev/spec-review-directives.md`) does not collide — that part is clean.

9. **R9 — Precedence-rule drift across three consumers.** The `design bug` precedence rule is duplicated across route.md / ggx-work.md / ticket-analyze.md (sourced from _ticket-lib.md). *Recommended default:* keep _ticket-lib.md as the single canonical statement; the three consumers cite it to avoid drift. Risk if they drift: a `design bug`-only ticket mis-handled at one layer. Mitigation: the manual single-ticket test (R2) exercises a `design bug`-only ticket end-to-end through all three.

10. **R10 — Slack digest classification rendering.** Reusing the dev-lane digest row means a design-bug ticket renders as a dev-lane entry; acceptable, but if §6.4/§6.5 read the classification label to print a lane name, a design bug could show as "bug"/"feature" misleadingly. *Recommended default:* the §5 item 8 one-line check resolves this; if such per-lane label rendering exists, map `design bug` → the dev-lane display consistently. No `_slack-notify.md` change either way.
