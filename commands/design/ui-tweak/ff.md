---
name: ff
description: "Orchestrator for the /ui-tweak pipeline — the engine behind the designer-facing /ui-tweak alias. Splits a ticket-named worktree up-front (R19, Step 0 → /ui-tweak:start → /add-worktree), mirroring /dev:ff and /port:ff, before the first edit. Derives the current stage from filesystem markers (infer_ui_stage) and dispatches the two-phase flow (R18): iteration is apply-only (no build); Phase 1 (preview) builds the change onto a device when the designer picks 'show me', then (Step 2.5) navigates to the target screen and captures it (screenshot + short recording) — preview is the SOLE capture point (no separate demo stage): Tier-1 fires ONE whitelisted ggv:// deep-link, Tier-2 falls back to an LLM-planned, codebase-guided, navigation-only tap-through for non-deep-linkable screens; if it can't reach the screen it FAIL-SILENTs (no capture, the designer never drives). Phase 2 (audit → commit → pr → review) runs when they pick 'Ship it'. Direct-ship (R20): on C1 (show-me) a designer who already saw the change on their own device can ship without the device preview — a build-only compile gate runs before the audit (or, with the navigate opt-in, a launch onto an already-running device so preview's Step 2.5 can navigate+capture). Owns the navigation cards (C0, C-WT, C1's show-me/looks-good variants, C5, the engineer card Ce, and the pre-edit reclassify card C-RECLASSIFY; C3/C4 removed); atomic stages render C-MISDIRECT / C6. A read-only `/ui-tweak:detect` visual-vs-logic triage runs as the FIRST dispatched stage (after Step 0) — a needs-logic verdict stops before any edit (interactive → C-RECLASSIFY; --auto → UI-TWEAK BLOCKED). A build/audit failure routes back to apply for an agent UI-only fix (max 3, then Ce). Sets UI_TWEAK_FF=1 so atomic stages know they were reached through the orchestrator. No --pr flag: in interactive mode a draft PR happens only when the designer picks 'Ship it'. --auto (the /ggx-work / /ggx-dispatcher lane for `design bug` tickets) shows no cards and never reaches an interactive device-preview card; instead it auto-takes the R20 direct-ship path after the single apply, with navigate+capture ON by default — build/launch gate (launch onto an already-running device if present, else build-only) → preview Step-2.5 navigate+capture (one ggv:// deep-link + screenshot/recording, best-effort/fail-silent) → dual-judge audit → commit → draft PR (terminal; never draft→ready, never merge). Accepts-and-ignores --no-ticket-init (ui-tweak never calls /_ticket-init; the flag exists so /ggx-work's lane-agnostic spawn builder can append it uniformly)."
---

<!-- RULE: ALL content, including designer-facing CARD text, is English. No Chinese / non-ASCII. -->

# `/ui-tweak:ff`

The engine behind `/ui-tweak`. A designer never needs to know `apply` / `verify` / `commit` / git
exist — they type `/ui-tweak "<one sentence>"`, and at every stop this orchestrator prints a **wayfinding
card** they advance by picking a number (or just talking back). Mirrors `/dev:ff`: no `state.json`,
derive `current_stage` from filesystem markers, dispatch, re-derive, until `done` / failure / a card.

**The orchestrator sets `UI_TWEAK_FF=1` before dispatching any atomic stage** so each stage's
misdirect guard (Step 0a) knows it was reached legitimately.

## Routing rule (alias entry)

- **Flag pre-pass**: strip `--auto` (→ `<auto-mode>`) and `--no-ticket-init` from `<source>` before
  any other parsing. `--no-ticket-init` is **accepted and ignored** — ui-tweak never calls
  `/_ticket-init` (`/ui-tweak:start` is read-only on the ticket; in the orchestrated path the
  lifecycle write happens one level up, in `/ggx-work` Step 2.5). The flag exists only so
  `/ggx-work`'s lane-agnostic spawn builder can append it uniformly; it is stripped here and NOT
  forwarded to `/ui-tweak:start` (which would also ignore it — belt and suspenders).
- `<source>` **empty / whitespace / a help token** (`help`, `?`, `how`) → print **card C0**, do
  not dispatch.
- `<source>` carries a **requirement string** → **first run Step 0 (split the worktree, R19)**, then
  (re)run `apply` from inside that worktree:
  - fresh tree → split+enter the worktree (Step 0), then start a new run;
  - `worktree-ready` already present (in-flight, already inside the worktree) → skip Step 0; if
    `base_ref` also exists treat the new requirement as a **correction** (see Correction loop).
- **no argument** → pure resume: skip Step 0 (already inside the worktree), run `infer_ui_stage` and
  dispatch that stage.

Mirrors `/dev:ff` (arg = fresh/act, no arg = resume); the difference is `/ui-tweak` EXPECTS repeated
corrections on the same tree, so "`base_ref` exists + new requirement" is a correction, not an error.

**Two phases (R18).** Iteration is cheap — `apply` only, **no build** — so a designer can adjust as
many times as they like for free. Building happens once, in **Phase 1 (`preview`)**, when they pick
"I'm done — show me": the change is built INTO a device (`flutter run` covers Android emulators + iOS
simulators) so they can **see it on a real screen**, then confirm the look. **Phase 2** (the real
verify + ship) runs only after that confirmation: `audit` (dual-judge logic check) → `commit` → draft
PR → review. **Direct-ship shortcut (R20)**: a designer who already saw the change on their own device
can pick "It already looks right — ship it" on C1 (show-me) — the device preview and its "looks good?"
stop are skipped, but a **build-only compile gate still runs** before the audit (their hand-build may
predate the latest tweak; the "cannot ship broken code" guarantee never relaxes). A build or audit failure is the agent's implementation problem, not the designer's — the
orchestrator routes back to `apply` for an **agent fix (max 3 attempts)**, then surfaces the engineer
card. `--auto` (the unattended `/ggx-work` / `/ggx-dispatcher` lane for `design bug` tickets):
suppress ALL cards, walk silently; the C1 (show-me) decision is **auto-taken as the R20 direct-ship
path** (write `deliver` + `direct-ship` after the single apply), so the run still passes the
build-only compile gate AND the dual-judge audit, then commits and opens a **draft PR** — the
terminal (D7, revised). `--auto` never reaches a *device preview* (no card can request one), never
goes draft→ready, never merges — the human reviewing the draft PR is the gate that replaces the
suppressed cards. Failures under `--auto` still print the deterministic stderr line (R13).

## Designer-facing language rules (apply to EVERY card)

**Plain-language vocabulary — use the right column, never the left:**

| BANNED (dev jargon) | Plain wording |
|---|---|
| branch / commit / merge | (don't surface; handled internally) |
| build / compile | confirm it still works |
| emulator / /run | see it on a phone screen |
| marker / base_ref / SHA / hunk / diff | (don't surface) |
| ticket | work-item number |
| revert | take the change back / put it back |
| judge / logic / behavior | check / the part about how the program runs |

**"PR" / "draft PR" is ALLOWED vocabulary** — designers know what a PR is; say it directly, never
euphemize it as "a proposal". ("review" as an engineer activity is fine too: "an engineer will
review the PR".)

- **Translate, never forward**: a judge's `Status`/findings are translated into ONE plain sentence;
  never paste `ui-verify-agent` / `dev-reviewer` raw text to the designer.
- **📦 content rule**: the `📦` narrative carries only `component + visual property + old→new` (dp /
  hex color OK). NEVER file paths, branch names, SHAs, marker names, raw build errors — those go to
  the engineer-facing PR body / commit.

### How cards are rendered (R16)

- **Decision cards (C1's two variants, the engineer card Ce, the pre-edit reclassify card C-RECLASSIFY,
  and the atomic-stage card C6) are
  rendered with the `AskUserQuestion` tool**, not printed as numbered text. The `📍`/`📦` narrative goes in the
  question text; each choice is one option (`label` short + plain `description`). The tool's built-in
  **"Other"** free-text field IS the correction escape — a designer types "a bit bigger" / "make it
  blue" there and it routes to the Correction loop exactly like the old `[4]`. So decision cards no
  longer carry a "Reply with a number" footer, and there is no explicit "adjust" option (Other owns
  it). Always remind them in the question text: *"…or pick **Other** and tell me what to change."*
- **Info cards (C0 first-contact, C5 done) and the stop-only notice C-MISDIRECT stay
  as plain text** — they present no choice, so a tool prompt would be noise.
- **`--auto` NEVER calls `AskUserQuestion`** (it is interactive). Under `--auto` all cards are
  suppressed (D7, revised): instead of rendering C1 (show-me), the orchestrator **auto-takes the
  direct-ship branch** (writes `deliver` + `direct-ship` — see the dispatch loop's `--auto`
  auto-decision), so the run flows build-gate → audit → commit → **draft PR** with no prompt.
  C1 (looks-good) is structurally unreachable under `--auto` (nothing ever writes
  `preview-requested`), so a device preview can never happen. Only verify's deterministic stderr
  line survives (R13).
- **Routing keys on the returned selection** (the chosen option's `label`, or the Other free-text),
  not on a parsed `[N]`. The branch logic below names the option by its label.

## Step 0 — split the worktree up-front (R19)

Before the dispatch loop runs `apply` for the first time, the orchestrator moves the session into a
dedicated, ticket-named worktree — the same `../<ticket-id>` (off latest trunk) that `/dev:ff` and
`/port:ff` create via `/add-worktree`. This keeps every designer edit off the engineer's current
checkout, lets parallel work coexist, and gives `base_ref` a clean trunk baseline. The split is
**silent** (per the language table, `branch` / `worktree` are never surfaced); the only
designer-visible consequence is the up-front work-item ask (**card C-WT**) when `<source>` carries no
number.

Run this **once per run**, gated on the `worktree-ready` marker so resumes and corrections (already
inside the worktree) never re-split:

```bash
wt=$(git rev-parse --show-toplevel)
[ -f "$wt/.dev/ui-tweak/worktree-ready" ] && SPLIT_DONE=1 || SPLIT_DONE=0
```

- `SPLIT_DONE=1` (resume / in-flight correction — already inside the worktree) → **skip Step 0**, go
  straight to the dispatch loop.
- `SPLIT_DONE=0` → parse a work-item id from `<source>`:
  - id pattern: `[A-Z]+-[0-9]+`, or a Linear issue URL (`linear.app/<org>/issue/<ID>/...`). Take the
    first match.
  - **id found** → dispatch `/ui-tweak:start <id> [--auto]`. It creates+enters `../<id>` (off trunk),
    writes `.dev/ui-tweak/worktree-ready`, and read-only-caches `.dev/ui-tweak/ticket.json`. After it
    returns the session is inside the worktree; re-derive `wt` and continue to the loop.
  - **no id (pure free text)** — the workspace can't be ticket-named, and there is **no in-place
    fallback** (B3): a UI change is always tracked under a work item, exactly like `/dev:ff` and
    `/port:ff` (which hard-require a ticket). We never fabricate a Linear ticket and we never edit the
    engineer's checkout directly.
    - interactive → render **card C-WT**. A number → `/ui-tweak:start <id>` (split as above) and
      continue. **No number → STOP, no edit** (the card explains a number is needed first); nothing is
      changed, no marker is written, so re-running `/ui-tweak` later with a number starts cleanly.
    - `--auto` → no card is allowed; print
      `FAIL: /ui-tweak:ff --auto needs a work-item id in <source> to name the worktree.` to stderr and
      STOP (R13). (`--auto` is non-interactive; the `/ggx-work` / `/ggx-dispatcher` callers always
      pass a ticket id, so this is a guard against malformed direct invocations.)

> **Why no in-place fallback (B3).** An earlier draft let "I don't have one" edit the current tree
> with no worktree. That re-introduced two defects: (1) a later "Ship it" routed through `start` →
> `/add-worktree` off trunk, **orphaning the uncommitted in-place edits** in the old tree (empty PR);
> (2) because no `worktree-ready` marker is written, every correction re-entered Step 0 and **re-asked
> C-WT**. Requiring a work-item number up-front removes both: a run either splits a worktree (marker
> written → asked once) or never starts (nothing to lose).

Because the split runs through `/ui-tweak:start` → `/add-worktree`, it inherits `/add-worktree`'s
own pre-flight (dirty-tree warning, existing-branch / existing-worktree prompts). Under `--auto` those
pass through non-interactively; in the interactive designer flow they surface as `/add-worktree`'s
prompts (the one place git wording can leak — acceptable, same trade-off `/dev:ff` and `/port:ff`
accept).

## Walker — `infer_ui_stage`

All markers live in the worktree under `.dev/`; no `state.json`. Consume-on-existence order.

```bash
infer_ui_stage() {
  local wt id pr_state deliver preview_req preview_shown trunk ahead
  wt=$(git rev-parse --show-toplevel)
  id=$(git rev-parse --abbrev-ref HEAD | grep -oE '[A-Z]+-[0-9]+' | head -1)

  # PREVIEW-REQUESTED (Phase 1): written when the designer picks "I'm done — show me" on card C1.
  # Never written under --auto (no card → no device preview, ever).
  # DELIVER (Phase 2): written when the designer picks "Ship it" on a C1 variant — or, under
  # --auto, auto-written together with direct-ship by the dispatch loop's auto-decision (D7,
  # revised: --auto terminates at a draft PR via the direct-ship build-gate + audit path).
  preview_req=0;   [ -f "$wt/.dev/ui-tweak/preview-requested" ] && preview_req=1
  preview_shown=0; [ -f "$wt/.dev/ui-tweak/preview-shown" ]     && preview_shown=1
  deliver=0;       [ -f "$wt/.dev/ui-tweak/deliver" ]           && deliver=1
  # DIRECT-SHIP (R20): designer picked "It already looks right — ship it" on C1 (show-me), skipping the
  # device preview. Still gated by a build-only compile check before audit (see the deliver branch).
  direct_ship=0;   [ -f "$wt/.dev/ui-tweak/direct-ship" ]       && direct_ship=1

  # UNIFIED REPAIR (R18): any failed stage (preview build-fail / audit BLOCKED) writes
  # repair-context + bumps repair-count, then the AGENT fixes it in apply — not the designer.
  # repair-count >= 3 is a card-terminus (engineer card) handled by the DISPATCH LOOP before this
  # walker is called; here, a present repair-context (count < 3) always routes to apply (repair mode).
  [ -f "$wt/.dev/ui-tweak/repair-context" ] && { echo apply; return; }

  # ---- Phase 2 — ship (deliver set) ----
  if [ "$deliver" = "1" ]; then
    [ -z "$id" ] && { echo start; return; }            # defensive only: under R19/B3 deliver always
                                                       # implies a worktree (id from branch); not expected
    # DIRECT-SHIP build gate (R20): if the designer skipped the device preview, still prove the
    # cumulative diff compiles before the audit. Normal deliver runs already have build-pass=PASS
    # (preview built it), so this only fires for direct-ship. preview reads `direct-ship` → build-only.
    if [ "$direct_ship" = "1" ] && ! grep -q '^Status: PASS' "$wt/.dev/ui-tweak/build-pass" 2>/dev/null; then
      echo preview; return
    fi
    # Resolve the PR by HEAD BRANCH, not by ticket id: the worktree branch is
    # <prefix>/<TICKET-ID> (e.g. fix/<PREFIX>-<n>), so `gh pr view "$id"` cannot find
    # it and returns empty — the walker would then mis-walk back to `review`.
    pr_state=$(gh pr list --head "$(git -C "$wt" branch --show-current 2>/dev/null)" --state all --json state -q '.[0].state' 2>/dev/null)
    [ "$pr_state" = "OPEN" ] && [ -f "claude-reports/$id/code-review.md" ] && { echo done; return; }
    [ "$pr_state" = "OPEN" ] && { echo review; return; }
    if [ -f "$wt/.dev/ui-verify-pass.md" ] && grep -q '^Status: CLEAR' "$wt/.dev/ui-verify-pass.md"; then
      trunk=$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@')
      trunk=${trunk:-$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null | sed 's@^origin/@@')}
      ahead=$(git rev-list --count "origin/${trunk:-main}..HEAD" 2>/dev/null || echo 0)
      if [ "${ahead:-0}" -gt 0 ]; then                 # already committed
        # Capture already happened in `preview` (the sole capture point) → straight to PR.
        echo pr; return                                # → open PR
      fi
      echo commit; return                              # audit CLEAR, not committed → commit
    fi
    echo audit; return                                 # deliver set, not yet audited → Phase-2 audit
  fi

  # ---- Phase 1 — preview (requested, not yet delivering) ----
  if [ "$preview_req" = "1" ]; then
    # build PASS is recorded by preview; preview-shown means it launched (or did the no-device
    # build-only fallback). Both present → terminal → post-preview card (C1 "looks good?" variant).
    if grep -q '^Status: PASS' "$wt/.dev/ui-tweak/build-pass" 2>/dev/null && [ "$preview_shown" = "1" ]; then
      echo done; return
    fi
    echo preview; return                               # build + launch onto a device
  fi

  # ---- iteration (cheap; apply only, NO build) ----
  [ -f "$wt/.dev/ui-tweak/base_ref" ] && { echo done; return; }   # diff exists → card C1 (no build)

  # ---- DETECT: read-only visual-vs-logic triage, FIRST stage of a fresh run ----
  # Consume-on-existence: once detect writes `triage-pass` (pure-visual verdict) this gate is
  # skipped and the run falls through to `apply`. A `needs-logic` verdict is NOT handled here — it is
  # a CARD-TERMINUS the dispatch loop checks BEFORE this walker (it never writes `triage-pass`, so
  # without that terminus the walker would re-emit `detect` forever — exactly why it is a terminus).
  # Idempotent on resume: a re-entered run that already passed detect (triage-pass present) skips it.
  # Reached only on a truly fresh run (no repair-context, no deliver, no preview-requested, no
  # base_ref) — so detect never retroactively fires on an in-flight worktree that already has a diff.
  [ ! -f "$wt/.dev/ui-tweak/triage-pass" ] && { echo detect; return; }

  # nothing yet — figma is NOT a walker stage (folded into apply). → apply.
  echo apply
}
```

Output whitelist: `start | detect | apply | preview | audit | commit | pr | review | done`. Guard the output
against this set (mirror `infer_bug_stage_safe`). There is no standalone `verify` stage — the build is
folded into `preview` (`flutter run` = build + deploy); `format` is folded into `audit`.

## Dispatch loop

`export UI_TWEAK_FF=1`, **run Step 0 (split the worktree up-front, R19)**, then loop. **Each iteration
first checks the repair-exhausted card-terminus**; only if it does not fire does it call the walker and
dispatch:

```
Step 0   → split+enter ../<ticket-id> (skip if worktree-ready exists; no id → card C-WT, else STOP)
loop:
  # --- card-termini (checked BEFORE the walker) ---
  needs-logic marker present
                     → `/ui-tweak:detect` triaged the change as needs-logic (it touches behaviour,
                       not just look) and stopped before any edit. interactive: render card
                       C-RECLASSIFY (a PRE-edit reclassify card — NOT Ce) and STOP; make no edit,
                       write no triage-pass, never expose `detect` as a stage name. --auto: detect
                       already exited non-zero (UI-TWEAK BLOCKED) before reaching here, so this branch
                       is defensive — print
                       `FAIL: /ui-tweak:ff --auto — UI-TWEAK BLOCKED (detect: needs-logic); needs an engineer / reclassify Design bug -> Bug.`
                       to stderr and exit non-zero (R13). (This terminus is checked BEFORE the walker
                       because detect's needs-logic path writes no `triage-pass`, so the walker would
                       otherwise re-emit `detect` every iteration.)
  repair-count >= 3  → interactive: render the engineer card (couldn't do it as a pure look change)
                       and STOP. --auto: no card — print
                       `FAIL: /ui-tweak:ff --auto — repair budget exhausted (3); needs an engineer.`
                       to stderr and exit non-zero (R13); the caller (/ggx-work / dispatcher)
                       classifies the ticket failed.
  # --- otherwise advance ---
  s = infer_ui_stage
  dispatch s
  re-derive   # stage success is NOT the loop terminus
  stop at `done` / a failure / an interactive card
```

`done` resolves to a card by markers: **deliver + PR open + code-review → C5**; **preview-requested +
preview-shown → C1 (looks-good)** showing the captured screenshot (or, when preview's Step-2.5 capture
was skipped/failed, no image + an honest "couldn't auto-reach the screen" note — fail-silent, the
designer never drives); **otherwise → the iteration C1 ("I'm done — show me / more changes")**.

**`--auto` auto-decision (the ONE structural difference from interactive — D7, revised).** Under
`--auto` no card may render, so the two `done` resolutions that would show a card are auto-taken:

- `done` would render **C1 (show-me)** (`base_ref` present, `preview-requested`/`deliver` absent) →
  do NOT stop. First check `.dev/ui-tweak/.not-deliverable`: if present, a partial change must not
  ship — print `FAIL: /ui-tweak:ff --auto — change is partial (.not-deliverable); needs a human.`
  to stderr and exit non-zero (R13). Otherwise **auto-take the R20 direct-ship branch**: write
  `.dev/ui-tweak/deliver` AND `.dev/ui-tweak/direct-ship` AND `.dev/ui-tweak/auto-navigate`,
  then continue the loop. This is the existing C1 "It already looks right — ship it" choice,
  auto-supplied, PLUS the navigate+capture so the draft PR embeds a real screenshot — the build
  gate, preview's Step-2.5 navigate+capture, the dual-judge audit, commit, draft PR, and code-review
  all still run; nothing is skipped except the human look. With `auto-navigate` set, `preview` launches
  the app onto an already-running device (else build-only) and Step 2.5 fires one deep-link to
  the target screen and captures it (best-effort / fail-silent — a missing capture never fails the run).
  The draft PR (engineer review) is the human gate that replaces the card.
- `done` would render **C5** (deliver + PR open + code-review) → terminal: print
  `Ticket <id>: ui-tweak shipped — draft PR open.` and exit 0. (`/ggx-work`'s next `/route` call
  sees PR OPEN → phase done.)
- `done` would render **C1 (looks-good)** → structurally unreachable under `--auto` (nothing writes
  `preview-requested`); if ever hit, treat as the show-me case.

Note the iteration phase collapses to a single `apply` under `--auto` — corrections require a human
reply, so there are none (R18's "designer iterates first" intent is deliberately relaxed here; the
audit panel + draft-PR review are the safety net, same as `/dev:ff --auto` producing a first-pass
diff a human reviews on the PR).

| stage | action |
|---|---|
| `detect` | `/ui-tweak:detect <source> [figma] [--auto]` — the FIRST dispatched stage after Step 0. Read-only visual-vs-logic triage of the target widget. **pure-visual** → writes `.dev/ui-tweak/triage-pass` (resolved widget path + one-line rationale; `:apply` Step 4 reuses that widget) → walker → `apply`. **needs-logic** → stops before any edit: interactive writes `.dev/ui-tweak/needs-logic` (the card-terminus above → **C-RECLASSIFY**); `--auto` exits non-zero with a `UI-TWEAK BLOCKED (detect: needs-logic)` error (→ `/ggx-work` pipeline-failed, or the dispatcher's `terminal-ui-block` cleanup). Never edits code, never auto-reclassifies the ticket |
| `apply` | `/ui-tweak:apply <source> [figma] [--auto]` — iteration edit (no build). In **repair mode** (`repair-context` present) it reads the error and fixes the edit UI-only (see Correction/Repair loop) |
| `preview` | `/ui-tweak:preview [--auto]` — **Phase 1**: build INTO a device (cascade: already-running/connected device incl. physical FIRST → boot emulator/sim → honest no-device build-only), then **(Step 2.5) navigate to the target screen + capture it (screenshot + short recording) → `demo-files`** — this is the SOLE capture point. Writes `build-pass` + `preview-shown`. In **direct-ship mode** (`direct-ship` present, R20): build-only compile gate — no `preview-shown`, no card; EXCEPT with `auto-navigate` set it launches onto an already-running device and Step 2.5 captures. Nav can't reach the screen → fail-silent (no capture, designer never drives). Build-fail → repair-context (→ apply, max 3) |
| `audit` | `/ui-tweak:audit [--auto]` — **Phase 2** first check (deliver only): `/format` then the dual-judge on the final cumulative diff. CLEAR → `commit`; BLOCKED → repair-context (→ apply, max 3) |
| `start` | `/ui-tweak:start <ticket> [--auto]` — split+enter the `../<ticket>` worktree via `/add-worktree`. Run up-front by **Step 0 (R19)** before the first `apply`. (Under B3 every run splits up-front, so the walker's deliver-path `start` branch is defensive only.) |
| `commit` | `/commit` (NO extra confirm — "Ship it" on C1 already authorized the handoff, R18) — commit ONLY the files in the Step-5 coverage table (R12); formatter-touched extras go in the PR body `### Formatter-only changes` |
| `pr` | `/pull-request --draft` with the **pre-built PR body** (see "Deliver PR body" below — its `## Demo` embeds ticket visuals and any designer-supplied capture from `.dev/ui-tweak/demo-files`); title prefixed `[ui-tweak]`; structured PR-link ticket comment (`🎨 UI tweak ready for engineer review` + audit verdict + coverage summary). Then **transition the ticket** (see "Ticket transition on PR open" below): status → `In Review` and remove the `ready-to-dev` label (keep all others, e.g. `design bug`) — Linear-only, idempotent, both interactive and `--auto` |
| `review` | `/code-review <pr>` → `claude-reports/<ticket>/code-review.md` |
| `done` | terminal: iteration → **C1 (show-me)**; post-preview → **C1 (looks-good)**; deliver (PR open + code-review) → **C5** |

## Wayfinding cards (this orchestrator owns the navigation cards C-WT, C0–C5)

> The stage-stop cards **C-MISDIRECT / C6** are rendered by the atomic stages themselves
> (`apply.md` / `detect.md` / `preview.md` / `start.md`), since those are the stages that physically
> stop — see `/ui-tweak:apply` Step 0a / Step 4, `/ui-tweak:detect` Step 0a. C-WT + C0–C5 + the
> repair-exhausted **engineer card Ce** + the pre-edit reclassify card **C-RECLASSIFY**
> (rendered from the `needs-logic` card-terminus) — the flow-navigation cards — live here.

**C-WT — work-item number (split the workspace)** (Step 0, free-text run with no work-item id; R19) —
*`AskUserQuestion`, `header: "Work-item no."`*. Asked **once, up-front**, before any edit — it's how
the workspace gets named. A run that already carried an id never sees this card.
- **question**:
  ```
  Before I start, what's the work-item number for this (like <PREFIX>-<n>)?
  I use it to keep your change in its own space and to hand it over later.
  Pick Other and paste the number to begin.
  ```
- **options**: `I'll paste the number` — "I have a number — I'll type it next." / `I don't have one yet`
  — "I don't have a number yet."
- **routing**: a number via **Other** (or the follow-up after `I'll paste the number`) →
  `/ui-tweak:start <id>` → worktree split, then the loop. `I don't have one yet` → **STOP without
  changing anything** and reply in plain words: *"No problem — every change needs a work-item number so
  it can be tracked and handed to an engineer. Create one (or ask your PM/engineer for it), then run
  `/ui-tweak` again with that number and I'll start."* (No edit, no marker — B3.)

**Ce — couldn't do it (engineer)** (loop card-terminus when `repair-count >= 3`, R18) —
*`AskUserQuestion`, `header: "Needs an engineer"`*. After 3 agent fix attempts the change still won't
build, or can't be done without touching how the program runs.
- **question**: "I tried a few times but couldn't make this work as a pure look-and-feel change —
  this part may need an engineer (it likely touches how the program runs, not just the look).
  Everything's back to how it was. (Or pick Other to describe it a different way and I'll start fresh.)"
- **options**: `Hand to an engineer` — "I'll write up what I tried for an engineer." / `Describe it
  differently` — "Start fresh with a new wording (resets my attempts)."
- **routing**: `Hand to an engineer` → prepare the summary; `Describe it differently` / **Other** →
  reset `repair-count` + Correction loop.

**C-RECLASSIFY — looks like an engineer change (pre-edit reclassify)** (loop card-terminus
when `/ui-tweak:detect` wrote `.dev/ui-tweak/needs-logic`) — *`AskUserQuestion`, `header: "Needs an
engineer"`*. Detect read the target widget BEFORE any edit and judged the change touches how the
screen behaves, not just its look. **Nothing was changed and no worktree edit was made** — the key
difference from `Ce`, which fires only after edits + 3 failed repairs and says "everything's back to
how it was", wording that would misrepresent a pre-edit stop (so `Ce` is deliberately NOT reused here).
- **question**: "This change looks like it needs an engineer — it touches how the screen behaves
  (like what a tap does or which screen it opens), not just its look. **Nothing was changed.** I'd
  suggest re-filing it as a Bug so an engineer can pick it up. (Or pick Other to describe it a
  different way and I'll take another look.)"
- **options**: `Hand to an engineer` *(recommended)* — "Re-file this as a Bug for an engineer; I'll
  note what I found." / `Describe it differently` — "Tell me another way to say it and I'll re-check —
  maybe it's a pure look change after all."
- **routing**: `Hand to an engineer` → recommend reclassifying `Design bug → Bug` (human-owned — the
  orchestrator NEVER flips the issue-type label itself, consistent with the late-audit reclassify
  policy) and STOP; the `needs-logic` marker stays, so a re-run renders this same card until the
  ticket is reclassified or the wording changes. `Describe it differently` / **Other** → remove
  `.dev/ui-tweak/needs-logic` and re-run `detect` with the new wording (a fresh triage may now read
  pure-visual).

**C0 — first contact** (empty/help `<source>`) — *plain text (info card, no choice)*
```
📍 Hi! I can change how the App looks — sizes, colors, spacing, layout.
📦 Just describe it in plain words. It helps to say "which screen + what to change".
👉 e.g.  /ui-tweak "make the order-page primary button a bit taller"
         /ui-tweak "increase the home-card corner radius"   (a Figma link can go at the end)
```

**C1 — two variants, two essential choices (R17/R18)** — *`AskUserQuestion`, `header: "Next step"`*.
After any change the card asks only the meaningful question; never a wall of options. Which variant
the orchestrator renders is decided by markers (see the dispatch loop's `done` resolution).

**C1 (show-me) — change made, not yet seen on a screen** (iteration terminal: `base_ref` present,
`preview-requested` ABSENT). The build has NOT run yet (iteration is build-free), so do NOT claim it
compiles or that logic was checked:
- **question**:
  ```
  I made the change (look-and-feel values only).
  What changed: order-page primary button — height 44→48dp, corner 4→8dp.
  [source: Figma-confirmed / from the work-item description / ⚠ estimated]
  Want to see it on a phone, ship it as-is (if you've already seen it), or make more changes first?
  (Show me = I build it onto a device so you can look. Ship it = I skip the phone preview, confirm it
  still works, run the full check, and open a draft PR. Or pick Other and tell me what to change.)
  ```
  When `.not-deliverable` is present, append the "⚠ N spot(s) weren't changed …" note. After a
  correction, first line becomes *"I adjusted it once more. In total I've changed: <cumulative>."*
- **options**: `I'm done — show me` *(recommended)* — "I'll build it, **go to that screen myself, and
  show you a screenshot + short recording** — you don't have to navigate. If I can't reach it
  automatically I just skip the image (I won't ask you to drive)." / `It already looks right — ship it`
  — "You've already seen it on your own device — skip the preview; I'll confirm it still works, run the
  full check, **try to grab a screenshot/recording of the screen for the PR** (best-effort), and open a
  draft PR."
  *(BOTH the "show me" AND "ship it" options are OMITTED when `.not-deliverable` exists — you can
  neither preview nor ship a partial; tell them to adjust.)* / `I want more changes` — "Tell me what to
  adjust (e.g. 'move it down one')."
- **routing**:
  - `I'm done — show me` → write `.dev/ui-tweak/preview-requested` → walker → `preview` (Phase 1).
  - `It already looks right — ship it` (R20) → resolve the ticket id from `.dev/ui-tweak/ticket.json`
    (always present under B3) → write **`.dev/ui-tweak/deliver`** AND **`.dev/ui-tweak/direct-ship`**
    AND **`.dev/ui-tweak/auto-navigate`** → walker. With `auto-navigate` set the deliver
    branch's gate is NOT pure build-only: `preview` (Step 0b) launches onto an **already-running
    device** if one exists (else build-only; Step 2.4 auto-resolves a staging account from Notion and
    logs in, no `demo_auth` config required), then **Step 2.5 fires ONE deep-link to the target screen and captures it (screenshot +
    recording) for the PR** — best-effort / fail-silent (no device, no whitelisted route, an unpassable
    login wall, or capture error → no screenshot, run never fails). Then
    `audit` → `commit` → `pr` → `review` → C5. A build fail routes to the normal agent repair loop
    (max 3, then Ce) exactly like a device-preview build fail — the designer's earlier hand-build may
    predate the latest tweak, so this gate is never skipped.
  - `I want more changes` / **Other** → Correction loop.

**C1 (looks-good) — preview has navigated to the target + captured it** (`preview-requested` +
`preview-shown` present). Reorientation: `preview` (Step 2.5) navigated to the affected screen
FOR the designer and captured it (screenshot + recording) — the designer reviews the **result**, they
**never drive the device**. ONE card, two presentations by whether `demo-files` is populated:

- **Capture succeeded (`demo-files` populated)** — embed/attach the captured screenshot (it IS the
  review surface):
  ```
  Here's <screen> with your change, on <device>:  [screenshot]
  (What changed: <plain summary>.)  Does it look right?
  ```
- **Capture skipped/failed (`demo-files` empty — couldn't auto-reach the screen, no device, or a login
  wall; fail-silent)** — be honest, show no image, do NOT ask the designer to drive:
  ```
  I built your change and confirmed it compiles, but I couldn't get to <screen> automatically to
  grab a shot — <one-line reason>.  (What changed: <plain summary>.)  Ship it anyway, or adjust?
  ```

- **options**: `Ship it` *(recommended)* — "Looks right — run the full check + open a draft PR with a
  link on the work item." / `I want more changes` — "Tell me what to adjust; I'll redo and re-show it."
- **routing**: `Ship it` → resolve ticket id from `.dev/ui-tweak/ticket.json` → write
  `.dev/ui-tweak/deliver` → walker (→ audit/commit/pr/review/C5). Any screenshot already in `demo-files`
  is embedded by `pr`; an empty `demo-files` ships via the Demo fallback chain. **DO NOT re-ask for a
  number.** `I want more changes` / any other **Other** text → Correction loop (clears preview markers).

**Other text that is ONLY existing local image/video file path(s)** (dragged in; verify each exists +
is an image/video) → a **demo attachment, NOT a correction**: append each absolute path to
`.dev/ui-tweak/demo-files`, reply "Got it — I'll include it.", re-render the card (now with an image).
The `pr` stage uploads + embeds them.

**Doing nothing is fine**: walking away leaves the reviewed diff in the tree. Shipping (→ draft PR) is
the only explicit terminal action.

> **C2 removed (R18).** There is no standalone "change blocked, take it back" designer card anymore.
> Build failures and audit (logic) blocks are the agent's implementation problem — they route to the
> **agent repair loop** (`apply` repair mode) silently, and only after 3 failed attempts surface as the
> **engineer card Ce**. Designers never see a raw "blocked" card mid-flow.

> **C3 removed (B3).** There is no longer a ship-time "no work-item number" card. Because the run
> **always** acquires a number up-front at card C-WT (no in-place fallback), by the time the designer
> picks "Ship it" the id is already cached in `ticket.json` — the question can never reappear. The old
> C3 only existed to backfill a number for the in-place path, which no longer exists.

> **C4 removed (R18).** No separate commit-confirm card. Picking **"Ship it"** on C1 (looks-good) is
> the single authorization for the whole pre-PR series (audit → commit → draft PR) — a second confirm
> would be one click too many. The audit (Phase 2) is the gate that can still stop the handoff (→
> agent repair / engineer card Ce); commit/pr just execute once audit is CLEAR.

**C5 — done** — *plain text (info card, no choice)*
```
📍 Done! I've opened a draft PR for engineer review and left a link on the work item.
📦 Link: <url>  (draft — an engineer reviews it; it won't go live automatically.)
👉 Your part is finished. Want more changes? Just tell me.
```
When `preview` (Step 2.5) captured successfully (`demo-files` populated), append to the `📦` line:
*"Includes a short demo of the screen you approved."* On a silent capture skip/failure say **nothing**
(fail-silent — the Demo fallback chain already covered the PR).

## Correction loop (designer) + Repair loop (agent) — R8 / R18

Two distinct re-entries into `apply`, both keeping the original `base_ref` so the diff is always
**cumulative** (base_ref → working tree):

### A. Designer correction — "I want more changes" / any **Other** free-text (at any C1 variant)

The designer just types the change they want (no dedicated "adjust" option — Other owns it).

- **Post-deliver guard (A2)**: before treating a reply as an in-flight correction, check whether a
  commit already exists beyond `base_ref` (`git merge-base --is-ancestor $base_ref HEAD` true AND
  `HEAD != base_ref`). If so, the previous change was already handed off — do NOT correct on top of
  the committed/PR'd branch. Print "The previous change was already wrapped up and handed over; to
  adjust I'll start a **fresh round** for you", and on confirm re-baseline (new `base_ref` = current
  HEAD) before `apply`.
- **In-flight correction**: keep `base_ref`; **clear ALL downstream phase markers** so the walker
  drops back to iteration (apply, no build), then re-run `apply` with the new requirement:
  ```bash
  rm -f "$wt/.dev/ui-tweak/build-pass"  "$wt/.dev/ui-tweak/preview-shown" \
        "$wt/.dev/ui-tweak/preview-requested" "$wt/.dev/ui-tweak/deliver" \
        "$wt/.dev/ui-tweak/direct-ship" "$wt/.dev/ui-tweak/demo-files" \
        "$wt/.dev/ui-tweak/auto-navigate" \
        "$wt/.dev/ui-verify-pass.md" "$wt/.dev/dev-reviewer-pass.md" \
        "$wt/.dev/ui-tweak/repair-context" "$wt/.dev/ui-tweak/repair-count"
  ```
  A designer correction is a fresh intent → it also **resets the repair budget** (`repair-count`), the
  **direct-ship** flag, and the **auto-navigate** flag (a new edit must be re-decided, not silently
  re-shipped or re-navigated). The designer then
  iterates from C1 (show-me) again; building only re-happens when they next pick "show me" or
  "ship it", and the dual-judge only when they next ship.

### B. Agent repair — a build-fail or audit-block wrote `repair-context` (R18, max 3)

This is NOT designer-driven. `preview` (build fail) or `audit` (logic block) wrote
`.dev/ui-tweak/repair-context` + bumped `repair-count`. The walker routes to `apply` in **repair
mode**; `apply` reads `repair-context`, fixes the edit **UI-only** (e.g. correct the value that broke
the build, or redo the change without the logic touch), then clears the downstream so the change
re-validates from Phase 1:
```bash
rm -f "$wt/.dev/ui-tweak/repair-context" "$wt/.dev/ui-tweak/build-pass" \
      "$wt/.dev/ui-tweak/preview-shown" "$wt/.dev/ui-tweak/deliver" \
      "$wt/.dev/ui-tweak/direct-ship" "$wt/.dev/ui-tweak/demo-files" \
      "$wt/.dev/ui-tweak/auto-navigate" \
      "$wt/.dev/ui-verify-pass.md" "$wt/.dev/dev-reviewer-pass.md"
# keep preview-requested (still wants the preview) and repair-count (accumulates toward the cap of 3).
# direct-ship + auto-navigate ARE cleared: after a fix the designer re-decides at C1 (show-me) —
# "show me" gives a real device preview (not build-only), "ship it" re-arms direct-ship (+ auto-navigate).
# Under --auto the show-me auto-decision re-writes all four markers next loop. Avoids a stale
# build-only preview / stale navigate request.
```
After 3 attempts (`repair-count >= 3`) the dispatch loop renders the **engineer card Ce** instead of
re-entering apply. A clean `preview` build resets `repair-count`.

- **Power-user escape**: `/ui-tweak:ff --from apply` deletes apply-and-downstream markers, then resumes.

## Deliver PR body (R2)

The `pr` stage MUST pass a pre-built `## UI Tweak — designer-verifiable summary` body (Source /
Grounding-provenance / Audit verdict / Coverage table with `shared?`) — this body is the reviewer's
primary way to judge visual correctness, so the stage also populates the body's `## Demo` section
with whatever visuals **already exist** (designer-supplied, ticket attachments) plus any capture
`preview` (Step 2.5) produced. The ONLY capture path is `preview` (the sole capture point) —
and it is **fail-silent**: when it cannot confidently reach the target screen it captures nothing
(rather than a misleading wrong screen), so `demo-files`, when present, only ever holds a real,
target-reached capture or a designer-supplied file. The `pr` stage surfaces and uploads:

1. **Captured demo** (preferred — shows the *actual* result): if `.dev/ui-tweak/demo-files` exists
   (written by `preview`'s Step-2.5 navigate+capture, and/or designer-supplied via C1 Other),
   upload each listed file to the ticket via the **Idempotent attach** contract below and reference each
   returned `assetUrl` in the marker-wrapped `## Demo` block. (This upload is the one extra ticket write
   the deliver path is allowed — see Constraints.) No relevance gate is needed: `preview` fail-silents
   instead of capturing a wrong screen, so an empty `demo-files` simply falls through to bullets 2–4
   (ticket visuals / Figma link / "No screenshot" line).
2. **Ticket visuals** (shows the *target* design): read `.dev/ui-tweak/ticket.json` (cached by
   `start`) for image attachments and embed their URLs as markdown images. Add the grounded Figma
   node URL (from apply's figma grounding, when present) as a plain
   `Target design (Figma): <url>` link — a Figma URL is a page, not an image asset; never wrap it in
   `![]()`.
3. **Verify before embedding**: GitHub only renders an image URL it can fetch without auth. For each
   candidate, check it is publicly fetchable (e.g. `curl -fsI <url>` → 200); an auth-gated URL goes
   in as a plain link instead of an embedded image (an honest link beats a broken image box).
   **A Linear `assetUrl` is a DETERMINISTIC 401 to GitHub on this private repo** (GitHub's image proxy
   cannot authenticate to Linear's CDN) — so in the **PR body it is ALWAYS a plain link**, never an
   `![](…)` embed; the inline render of that asset lives only on the Linear ticket (where the attachment
   was uploaded). This is not a "maybe it 401s" `curl` gamble — for Linear-hosted demo assets the plain
   link is the fixed outcome. Ticket-attachment image URLs and Figma node URLs follow the same `curl`
   rule; a Figma URL is a page (never `![]()`, always a `Target design (Figma): <url>` link).
4. **Fallback**: only when 1–3 yield nothing, keep the line "No screenshot — eyeball before→after
   against the Figma node or ticket".

Never reuse `/pull-request`'s empty placeholder.

### Idempotent attach (shared contract — reused by `/ggx-demo`)

Both the `pr` stage here and the post-hoc `/ggx-demo` operator skill upload + embed demo artifacts. The
upload is **not** filename-idempotent and a blind body append stacks `## Demo` sections, so BOTH callers
MUST follow this contract verbatim (single source of truth — do not re-derive in `/ggx-demo`):

- **Linear attachment dedupe — deterministic title, SKIP or REPLACE (E14).** Title each
  uploaded attachment `ui-tweak-demo-<sha>` where `<sha>` is the short HEAD of the demoed commit
  (`git rev-parse --short HEAD`). Before `create_attachment_from_upload`, list the ticket's existing
  attachments (`get_issue` → `.attachments[]`) and check for that exact title —
  `create_attachment_from_upload` is NOT idempotent on filename, so two runs would otherwise attach two
  inline videos. On a title match:
  - **Default — SKIP** the upload and reuse the existing attachment's `assetUrl` (idempotent re-run).
  - **Replace mode** (caller passed `--force`, e.g. `/ggx-demo <id> --force` after a bad first
    recording): `delete_attachment` the old attachment → upload the new capture under the same title →
    use the NEW `assetUrl`. **The `assetUrl` changes on re-upload**, so the PR-body region write below
    is mandatory in this mode (the old link dies with the deleted attachment). Never delete-then-upload
    without `--force` — a plain re-run must stay a no-op.
- **Upload mechanics (F19).** The `prepare_attachment_upload` → `curl` PUT handoff is strict:
  send EVERY header the prepare call returns, verbatim (`content-type`, `cache-control`,
  `x-goog-content-length-range`, `Content-Disposition`) — a missing header is a signed-URL signature
  mismatch (403). The signed URL expires in ~60s: prepare and PUT in the same breath, never prepare
  early and upload after the capture.
- **PR body — marker-delimited region, replace-between (never append, never to-EOF).** Wrap the demo
  region in HTML-comment delimiters:
  ```
  <!-- ui-tweak-demo -->
  ## Demo
  <links / images>
  <!-- /ui-tweak-demo -->
  ```
  To write it: read the current PR body (`gh pr view <pr> --json body -q .body`); if the marker pair is
  present, **replace only the text between the markers** (marker-to-marker — the closing
  `<!-- /ui-tweak-demo -->` is the hard boundary); else if an UNMARKED `## Demo` section exists,
  replace that section with a marked block, **bounded at the next `## ` heading or end-of-body —
  NEVER a replace-to-EOF (E15)**: a mid-body `## Demo` with sections after it would otherwise
  have its siblings silently eaten (the bug only stays invisible while Demo happens to be the last
  section); else append a marked block. Then `gh pr edit <pr> --body <new>`. This is a surgical
  read-modify-write of one region — it preserves any reviewer edits elsewhere in the body, unlike a
  blind `gh pr edit --body` overwrite, and it guarantees re-runs never stack a second `## Demo`. The
  `pr` stage emits the markers at PR-open so post-hoc `/ggx-demo` finds them; if the PR shipped before
  this landed (no markers), `/ggx-demo`'s replace-or-append still produces exactly one marked block.
  **Run `gh pr edit` from inside the repo directory with an absolute `--body-file` path
  (F17)** — the Bash tool's cwd resets between calls (often to a non-repo scratchpad), and `gh` needs a
  repo cwd to resolve the PR.
- **PR link form.** Inside the marked block, a Linear `assetUrl` is ALWAYS a plain link (bullet 3's
  deterministic-401 rule). The inline render is on the Linear ticket only.

## Ticket transition on PR open (`pr` stage tail)

After the draft PR is open and the PR-link comment is posted, move the work item forward so it
leaves the dev queue and lands in the engineer-review column — the same lifecycle step `/dev:ship`
performs after opening its PR. This is **Linear-only** (Jira has no ui-tweak lane) and **idempotent**
(read current state first; skip any write already at target):

1. **Status → `In Review`.** Resolve the ticket id from `.dev/ui-tweak/ticket.json` and set its state
   to the team's `In Review` status via `save_issue` (`state: "In Review"`). If the ticket is already
   in `In Review` / a later state (`Ready for QA`, `Done`), do not move it backward — skip.
2. **Remove the `ready-to-dev` label, keep the rest.** `save_issue`'s `labels` field replaces the
   whole set, so read the current labels (from `ticket.json` / a fresh fetch), drop `ready-to-dev`,
   and write back the remainder verbatim (e.g. keep `design bug`). If `ready-to-dev` is absent,
   skip.

Both writes are best-effort: a failure here must NOT fail the run (the PR is already open — that is
the deliverable). Log a single WARN and continue to `review`. `assignee` is still never changed.
Applies in BOTH interactive and `--auto` modes (parity with `/dev:ship`).

## Constraints (carry-over)

Terminal is a **draft PR** — never `draft→ready`, never merge. The deliver path's allowed ticket
writes are: (a) the PR-link comment, (b) when the designer supplied a capture, attaching that file so
the PR can embed its `assetUrl`, and (c) the **PR-open transition** above (status → `In Review` +
remove `ready-to-dev`, Linear-only, idempotent — mirrors `/dev:ship`). `assignee` is never touched.
No `--pr` flag; in interactive mode deliver happens only via a human picking "Ship it" on a C1 card.
Under `--auto` the deliver decision is auto-supplied via the direct-ship auto-decision (D7, revised)
— the draft PR remains the terminal and the human gate.

**Wired into `/route` / `/ggx-work` / `/ggx-dispatcher`**: `/route` recommends `/ui-tweak:ff` for
tickets whose Linear labels include `design bug` (precedence over the canonical
`{bug,port,feature}` set — see `_ticket-lib.md` § Lane derivation); `/ggx-work` executes it like
the other FF pipelines (terminates at PR-open, no mid-pipeline HITL gate); `/ggx-dispatcher` runs
`design bug` tickets as a **SCRIPT-spawned level-1 leg** in its `Workflow` script (`runUiTweak`)
so the audit panel's opus judge can spawn — see `ggx-dispatcher.md` §5.2. Linear-only; Jira has no ui-tweak lane.
