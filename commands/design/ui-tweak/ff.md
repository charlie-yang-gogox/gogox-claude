---
name: ff
description: "Orchestrator for the /ui-tweak pipeline — the engine behind the designer-facing /ui-tweak alias. Splits a ticket-named worktree up-front (R19, Step 0 → /ui-tweak:start → /add-worktree), mirroring /dev:ff and /port:ff, before the first edit. Derives the current stage from filesystem markers (infer_ui_stage) and dispatches the two-phase flow (R18): iteration is apply-only (no build); Phase 1 (preview) builds the change onto a device when the designer picks 'show me'; Phase 2 (audit → commit → [demo] → pr → review) runs when they pick 'Ship it' — demo is the opt-in Tier-1 passive capture ('Ship it — and record a short demo' on C1 looks-good): zero-input screenshot+recording of the approved screen, after commit, fail-silent. Direct-ship (R20): on C1 (show-me) a designer who already saw the change on their own device can ship without the device preview — a build-only compile gate still runs before the audit. Owns the navigation cards (C0, C-WT, C1's show-me/looks-good variants, C5, the engineer card Ce; C3/C4 removed); atomic stages render C-MISDIRECT / C6. A build/audit failure routes back to apply for an agent UI-only fix (max 3, then Ce). Sets UI_TWEAK_FF=1 so atomic stages know they were reached through the orchestrator. No --pr flag: in interactive mode a draft PR happens only when the designer picks 'Ship it'. --auto (the /ggx-work / /ggx-dispatcher lane for `design bug` tickets) shows no cards and never reaches a device preview; instead it auto-takes the R20 direct-ship path after the single apply — build-only compile gate → dual-judge audit → commit → draft PR (terminal; never draft→ready, never merge). Accepts-and-ignores --no-ticket-init (ui-tweak never calls /_ticket-init; the flag exists so /ggx-work's lane-agnostic spawn builder can append it uniformly)."
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

- **Decision cards (C1's two variants, C3, the engineer card Ce, and the atomic-stage card C6) are
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
    pr_state=$(gh pr view "$id" --json state -q .state 2>/dev/null)
    [ "$pr_state" = "OPEN" ] && [ -f "claude-reports/$id/code-review.md" ] && { echo done; return; }
    [ "$pr_state" = "OPEN" ] && { echo review; return; }
    if [ -f "$wt/.dev/ui-verify-pass.md" ] && grep -q '^Status: CLEAR' "$wt/.dev/ui-verify-pass.md"; then
      trunk=$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@')
      trunk=${trunk:-$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null | sed 's@^origin/@@')}
      ahead=$(git rev-list --count "origin/${trunk:-main}..HEAD" 2>/dev/null || echo 0)
      if [ "${ahead:-0}" -gt 0 ]; then                 # already committed
        # opt-in demo capture (Tier 1): runs AFTER commit (diff frozen) and BEFORE pr, so the PR
        # embeds it. demo consumes demo-requested on completion OR failure (fail-silent).
        [ -f "$wt/.dev/ui-tweak/demo-requested" ] && { echo demo; return; }
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

  # nothing yet — figma is NOT a walker stage (folded into apply). → apply.
  echo apply
}
```

Output whitelist: `start | apply | preview | audit | commit | demo | pr | review | done`. Guard the output
against this set (mirror `infer_bug_stage_safe`). There is no standalone `verify` stage — the build is
folded into `preview` (`flutter run` = build + deploy); `format` is folded into `audit`.

## Dispatch loop

`export UI_TWEAK_FF=1`, **run Step 0 (split the worktree up-front, R19)**, then loop. **Each iteration
first checks the repair-exhausted card-terminus**; only if it does not fire does it call the walker and
dispatch:

```
Step 0   → split+enter ../<ticket-id> (skip if worktree-ready exists; no id → card C-WT, else STOP)
loop:
  # --- card-terminus (checked BEFORE the walker) ---
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
preview-shown → the post-preview C1 variant ("looks good — ship it / more changes")**; **otherwise →
the iteration C1 ("I'm done — show me / more changes")**.

**`--auto` auto-decision (the ONE structural difference from interactive — D7, revised).** Under
`--auto` no card may render, so the two `done` resolutions that would show a card are auto-taken:

- `done` would render **C1 (show-me)** (`base_ref` present, `preview-requested`/`deliver` absent) →
  do NOT stop. First check `.dev/ui-tweak/.not-deliverable`: if present, a partial change must not
  ship — print `FAIL: /ui-tweak:ff --auto — change is partial (.not-deliverable); needs a human.`
  to stderr and exit non-zero (R13). Otherwise **auto-take the R20 direct-ship branch**: write
  `.dev/ui-tweak/deliver` AND `.dev/ui-tweak/direct-ship`, then continue the loop. This is exactly
  the existing C1 "It already looks right — ship it" choice, auto-supplied — the build-only compile
  gate, the dual-judge audit, commit, draft PR, and code-review all still run; nothing is skipped
  except the human look. The draft PR (engineer review) is the human gate that replaces the card.
- `done` would render **C5** (deliver + PR open + code-review) → terminal: print
  `Ticket <id>: ui-tweak shipped — draft PR open.` and exit 0. (`/ggx-work`'s next `/route` call
  sees PR OPEN → phase done.)
- `done` would render **C1 (looks-good)** → structurally unreachable under `--auto` (nothing writes
  `preview-requested`); if ever hit, treat as the show-me case.
- `demo` is never requested under `--auto` (`demo-requested` is only written by a card choice).

Note the iteration phase collapses to a single `apply` under `--auto` — corrections require a human
reply, so there are none (R18's "designer iterates first" intent is deliberately relaxed here; the
audit panel + draft-PR review are the safety net, same as `/dev:ff --auto` producing a first-pass
diff a human reviews on the PR).

| stage | action |
|---|---|
| `apply` | `/ui-tweak:apply <source> [figma] [--auto]` — iteration edit (no build). In **repair mode** (`repair-context` present) it reads the error and fixes the edit UI-only (see Correction/Repair loop) |
| `preview` | `/ui-tweak:preview [--auto]` — **Phase 1**: build INTO a device (cascade: already-running/connected device incl. physical FIRST → boot emulator/sim → honest no-device build-only) so the designer SEES it. Writes `build-pass` + `preview-shown`. In **direct-ship mode** (`direct-ship` present, R20): build-only compile gate — no device, no `preview-shown`, no card; walker then advances to `audit`. Build-fail → repair-context (→ apply, max 3) |
| `audit` | `/ui-tweak:audit [--auto]` — **Phase 2** first check (deliver only): `/format` then the dual-judge on the final cumulative diff. CLEAR → `commit`; BLOCKED → repair-context (→ apply, max 3) |
| `start` | `/ui-tweak:start <ticket> [--auto]` — split+enter the `../<ticket>` worktree via `/add-worktree`. Run up-front by **Step 0 (R19)** before the first `apply`. (Under B3 every run splits up-front, so the walker's deliver-path `start` branch is defensive only.) |
| `commit` | `/commit` (NO extra confirm — "Ship it" on C1 already authorized the handoff, R18) — commit ONLY the files in the Step-5 coverage table (R12); formatter-touched extras go in the PR body `### Formatter-only changes` |
| `demo` | `/ui-tweak:demo` — opt-in **Tier-1 passive capture** (`demo-requested` present): screenshot + short recording of what is CURRENTLY on the previewed device's screen — **zero input events** (never taps/launches/navigates; the screen is the one the designer just approved). Appends output paths to `demo-files`, consumes `demo-requested`. Best-effort + fail-silent: ANY failure also consumes `demo-requested` and the walker proceeds to `pr` with the normal Demo fallback chain |
| `pr` | `/pull-request --draft` with the **pre-built PR body** (see "Deliver PR body" below — its `## Demo` embeds ticket visuals and any designer-supplied capture from `.dev/ui-tweak/demo-files`); title prefixed `[ui-tweak]`; structured read-only ticket comment (`🎨 UI tweak ready for engineer review` + audit verdict + coverage summary) |
| `review` | `/code-review <pr>` → `claude-reports/<ticket>/code-review.md` |
| `done` | terminal: iteration → **C1 (show-me)**; post-preview → **C1 (looks-good)**; deliver (PR open + code-review) → **C5** |

## Wayfinding cards (this orchestrator owns the navigation cards C-WT, C0–C5)

> The stage-stop cards **C-MISDIRECT / C6** are rendered by the atomic stages themselves
> (`apply.md` / `preview.md` / `start.md`), since those are the stages that physically stop — see
> `/ui-tweak:apply` Step 0a / Step 4. C-WT + C0–C5 + the repair-exhausted **engineer card Ce** (the
> flow-navigation cards) live here.

**C-WT — work-item number (split the workspace)** (Step 0, free-text run with no work-item id; R19) —
*`AskUserQuestion`, `header: "Work-item no."`*. Asked **once, up-front**, before any edit — it's how
the workspace gets named. A run that already carried an id never sees this card.
- **question**:
  ```
  Before I start, what's the work-item number for this (like CAF-1234)?
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
- **options**: `I'm done — show me on a phone` *(recommended)* — "I'll build it onto a phone/emulator
  so you can see it." / `It already looks right — ship it` — "You've already seen it on your own
  device — skip the phone preview; I'll confirm it still works, run the full check, and open a
  draft PR."
  *(BOTH the "show me" AND "ship it" options are OMITTED when `.not-deliverable` exists — you can
  neither preview nor ship a partial; tell them to adjust.)* / `I want more changes` — "Tell me what to
  adjust (e.g. 'move it down one')."
- **routing**:
  - `I'm done — show me` → write `.dev/ui-tweak/preview-requested` → walker → `preview` (Phase 1).
  - `It already looks right — ship it` (R20) → resolve the ticket id from `.dev/ui-tweak/ticket.json`
    (always present under B3) → write **`.dev/ui-tweak/deliver`** AND **`.dev/ui-tweak/direct-ship`** →
    walker. The deliver branch first runs a **build-only gate** (preview in build-only mode — no device,
    no look, no C1 looks-good stop) so the cumulative diff is proven to compile, then proceeds to
    `audit` → `commit` → `pr` → `review` → C5. A build fail here routes to the normal agent repair loop
    (max 3, then Ce) exactly like a device-preview build fail — the designer's earlier hand-build may
    predate the latest tweak, so this gate is never skipped.
  - `I want more changes` / **Other** → Correction loop.

**C1 (looks-good) — preview is live on a device** (`preview-requested` + `preview-shown` present):
- **question**:
  ```
  It's running on <device> now — take a look.  (What changed: <plain summary>.)
  Does it look right? If so I'll do the full check and open a draft PR for engineer review.
  (Took a screenshot or recording you'd like to include? Pick Other and paste/drag the file here —
  I'll attach it to the PR.)
  ```
  **No-device fallback (R18)**: if preview ran build-only (no device found), replace line 1 with
  *"I couldn't find a phone/emulator to show it on, but I confirmed it builds."* and keep the rest —
  and OMIT the `Ship it — and record a short demo` option (there is no screen to record).
- **options**: `Ship it` *(recommended)* — "Looks right — run the full check + open a draft PR with a
  link on the work item." / `Ship it — and record a short demo` — "Same as Ship it, plus I'll record
  what's on the screen right now and include it in the PR. You don't wait — it happens after you're
  done here." / `I want more changes` — "Tell me what to adjust; I'll redo and re-show it."
- **routing**: `Ship it` → resolve ticket id from `.dev/ui-tweak/ticket.json` (always present under B3
  — Step 0 split the worktree with a ticket, so the id is never missing here): write
  `.dev/ui-tweak/deliver` → walker (→ audit/commit/pr/review/C5). **DO NOT re-ask for a number.**
  `Ship it — and record a short demo` → same as `Ship it`, plus write
  `.dev/ui-tweak/demo-requested` — the walker runs the `demo` stage (passive capture of the screen
  the designer just approved) after `commit`, before `pr`. Recording is best-effort: if it fails the
  PR still opens with the normal Demo fallback chain, silently.
  **Other text that is ONLY existing local image/video file path(s)** (e.g. dragged into the prompt;
  verify each file exists and is an image/video) → that is a **demo attachment, NOT a correction**:
  append each absolute path to `.dev/ui-tweak/demo-files` (one per line), reply in plain words
  ("Got it — I'll include it when I wrap this up."), and re-render C1 (looks-good). The `pr` stage
  uploads + embeds them (see "Deliver PR body").
  `I want more changes` / any other **Other** text → Correction loop (which clears the preview
  markers so it re-iterates).
- **Doing nothing is fine**: walking away leaves the reviewed diff in the tree; no extra "leave it"
  button. Shipping (→ draft PR) is the only explicit terminal action.

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
When the `demo` stage captured successfully, append to the `📦` line: *"Includes a short demo of the
screen you approved."* On a silent demo failure say **nothing** (fail-silent — the Demo fallback
chain already covered the PR).

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
        "$wt/.dev/ui-tweak/demo-requested" \
        "$wt/.dev/ui-verify-pass.md" "$wt/.dev/dev-reviewer-pass.md" \
        "$wt/.dev/ui-tweak/repair-context" "$wt/.dev/ui-tweak/repair-count"
  ```
  A designer correction is a fresh intent → it also **resets the repair budget** (`repair-count`) and
  the **direct-ship** flag (a new edit must be re-decided, not silently re-shipped). The designer then
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
      "$wt/.dev/ui-tweak/demo-requested" \
      "$wt/.dev/ui-verify-pass.md" "$wt/.dev/dev-reviewer-pass.md"
# keep preview-requested (still wants the preview) and repair-count (accumulates toward the cap of 3).
# direct-ship IS cleared: after a fix the designer re-decides at C1 (show-me) — "show me" gives a real
# device preview (not build-only), "ship it" re-arms direct-ship. Avoids a stale build-only preview.
```
After 3 attempts (`repair-count >= 3`) the dispatch loop renders the **engineer card Ce** instead of
re-entering apply. A clean `preview` build resets `repair-count`.

- **Power-user escape**: `/ui-tweak:ff --from apply` deletes apply-and-downstream markers, then resumes.

## Deliver PR body (R2)

The `pr` stage MUST pass a pre-built `## UI Tweak — designer-verifiable summary` body (Source /
Grounding-provenance / Audit verdict / Coverage table with `shared?`) — this body is the reviewer's
primary way to judge visual correctness, so the stage also populates the body's `## Demo` section
with whatever visuals **already exist**. The agent still NEVER captures anything itself — the
preview HARD BOUNDARY is untouched; it only surfaces and uploads visuals a human or the ticket
already produced:

1. **Captured demo** (preferred — shows the *actual* result): if `.dev/ui-tweak/demo-files` exists
   (designer-supplied via C1 Other, and/or written by the `demo` stage's passive capture), upload
   each listed file to the ticket via the Linear 3-call flow — `prepare_attachment_upload` → PUT the
   raw bytes to the signed URL with its headers verbatim → `create_attachment_from_upload` — and
   embed each returned `assetUrl` in `## Demo` as `![demo](<assetUrl>)`. (This upload is the one
   extra ticket write the deliver path is allowed — see Constraints.)
2. **Ticket visuals** (shows the *target* design): read `.dev/ui-tweak/ticket.json` (cached by
   `start`) for image attachments and embed their URLs as markdown images. Add the grounded Figma
   node URL (from apply's figma grounding, when present) as a plain
   `Target design (Figma): <url>` link — a Figma URL is a page, not an image asset; never wrap it in
   `![]()`.
3. **Verify before embedding**: GitHub only renders an image URL it can fetch without auth. For each
   candidate, check it is publicly fetchable (e.g. `curl -fsI <url>` → 200); an auth-gated URL goes
   in as a plain link instead of an embedded image (an honest link beats a broken image box).
4. **Fallback**: only when 1–3 yield nothing, keep the line "No screenshot — eyeball before→after
   against the Figma node or ticket".

Never reuse `/pull-request`'s empty placeholder.

## Constraints (carry-over)

Terminal is a **draft PR** — never `draft→ready`, never merge, never mutate ticket status (the only
ticket writes are the read-only PR-link comment and — when the designer supplied a capture —
attaching that capture file to the ticket so the PR can embed its `assetUrl`; status/assignee are
never touched). No `--pr` flag; in interactive mode deliver happens only via a human picking
"Ship it" on a C1 card. Under `--auto` the deliver decision is auto-supplied via the direct-ship
auto-decision (D7, revised) — the draft PR remains the terminal and the human gate.

**Wired into `/route` / `/ggx-work` / `/ggx-dispatcher`**: `/route` recommends `/ui-tweak:ff` for
tickets whose Linear labels include `design bug` (precedence over the canonical
`{bug,port,feature}` set — see `_ticket-lib.md` § Lane derivation); `/ggx-work` executes it like
the other FF pipelines (terminates at PR-open, no mid-pipeline HITL gate); `/ggx-dispatcher` runs
`design bug` tickets **inline in its main session** (not as a spawned worker) so the audit panel's
opus judge can spawn — see `ggx-dispatcher.md` §5.0. Linear-only; Jira has no ui-tweak lane.
