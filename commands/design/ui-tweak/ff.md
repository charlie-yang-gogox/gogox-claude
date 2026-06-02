---
name: ff
description: "Orchestrator for the /ui-tweak pipeline — the engine behind the designer-facing /ui-tweak alias. Derives the current stage from filesystem markers (infer_ui_stage) and dispatches the two-phase flow (R18): iteration is apply-only (no build); Phase 1 (preview) builds the change onto a device when the designer picks 'show me'; Phase 2 (audit → commit → pr → review) runs when they pick 'Ship it'. Owns the navigation cards (C0, C1's show-me/looks-good variants, C3, C4, C5, the engineer card Ce); atomic stages render C-MISDIRECT / C6 / C7. A build/audit failure routes back to apply for an agent UI-only fix (max 3, then Ce). Sets UI_TWEAK_FF=1 so atomic stages know they were reached through the orchestrator. No --pr flag: a draft PR happens only when the designer picks 'Ship it'. --auto shows no cards → reaches neither a device preview nor a PR. Spec: §4.4/§4.5 / §5 / §6."
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

- `<source>` **empty / whitespace / a help token** (`help`, `?`, `how`) → print **card C0**, do
  not dispatch.
- `<source>` carries a **requirement string** → (re)run `apply`:
  - fresh tree → start a new run;
  - `base_ref` already exists (in-flight) → treat as a **correction** (see Correction loop).
- **no argument** → pure resume: run `infer_ui_stage` and dispatch that stage.

Mirrors `/dev:ff` (arg = fresh/act, no arg = resume); the difference is `/ui-tweak` EXPECTS repeated
corrections on the same tree, so "`base_ref` exists + new requirement" is a correction, not an error.

**Two phases (R18).** Iteration is cheap — `apply` only, **no build** — so a designer can adjust as
many times as they like for free. Building happens once, in **Phase 1 (`preview`)**, when they pick
"I'm done — show me": the change is built INTO a device (`flutter run` covers Android emulators + iOS
simulators) so they can **see it on a real screen**, then confirm the look. **Phase 2** (the real
verify + ship) runs only after that confirmation: `audit` (dual-judge logic check) → `commit` → draft
PR → review. A build or audit failure is the agent's implementation problem, not the designer's — the
orchestrator routes back to `apply` for an **agent fix (max 3 attempts)**, then surfaces the engineer
card. `--auto`: suppress ALL cards, walk silently; with no card the designer can never pick "show me"
or "ship it", so `--auto` reaches neither a device-preview nor a PR (structural guarantee, D7).
Failures under `--auto` still print the deterministic stderr line (R13).

## Designer-facing language rules (apply to EVERY card)

**Plain-language vocabulary — use the right column, never the left:**

| BANNED (dev jargon) | Plain wording |
|---|---|
| review / PR / draft PR | hand to an engineer / a proposal |
| branch / commit / merge | (don't surface; handled internally) |
| build / compile | confirm it still works |
| emulator / /run | see it on a phone screen |
| marker / base_ref / SHA / hunk / diff | (don't surface) |
| ticket | work-item number |
| revert | take the change back / put it back |
| judge / logic / behavior | check / the part about how the program runs |

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
- **Info cards (C0 first-contact, C5 done) and stop-only notices (C7 guard-missing, C-MISDIRECT) stay
  as plain text** — they present no choice, so a tool prompt would be noise.
- **`--auto` NEVER calls `AskUserQuestion`** (it is interactive). Under `--auto` all cards are
  suppressed (D7); with no prompt there is no way to pick "wrap up as a proposal", which is the
  structural guarantee that `--auto` can never reach a PR. Only verify's deterministic stderr line
  survives (R13).
- **Routing keys on the returned selection** (the chosen option's `label`, or the Other free-text),
  not on a parsed `[N]`. The branch logic below names the option by its label.

## Walker — `infer_ui_stage`

All markers live in the worktree under `.dev/`; no `state.json`. Consume-on-existence order.

```bash
infer_ui_stage() {
  local wt id pr_state deliver preview_req preview_shown trunk ahead
  wt=$(git rev-parse --show-toplevel)
  id=$(git rev-parse --abbrev-ref HEAD | grep -oE '[A-Z]+-[0-9]+' | head -1)

  # PREVIEW-REQUESTED (Phase 1): written when the designer picks "I'm done — show me" on card C1.
  # DELIVER (Phase 2): written when the designer picks "Ship it" on the post-preview card. No
  # unattended path writes either (--auto shows no cards → can reach neither preview nor PR, D7).
  preview_req=0;   [ -f "$wt/.dev/ui-tweak/preview-requested" ] && preview_req=1
  preview_shown=0; [ -f "$wt/.dev/ui-tweak/preview-shown" ]     && preview_shown=1
  deliver=0;       [ -f "$wt/.dev/ui-tweak/deliver" ]           && deliver=1

  # UNIFIED REPAIR (R18): any failed stage (preview build-fail / audit BLOCKED) writes
  # repair-context + bumps repair-count, then the AGENT fixes it in apply — not the designer.
  # repair-count >= 3 is a card-terminus (engineer card) handled by the DISPATCH LOOP before this
  # walker is called; here, a present repair-context (count < 3) always routes to apply (repair mode).
  [ -f "$wt/.dev/ui-tweak/repair-context" ] && { echo apply; return; }

  # ---- Phase 2 — ship (deliver set) ----
  if [ "$deliver" = "1" ]; then
    [ -z "$id" ] && { echo start; return; }            # deliver but no worktree yet
    pr_state=$(gh pr view "$id" --json state -q .state 2>/dev/null)
    [ "$pr_state" = "OPEN" ] && [ -f "claude-reports/$id/code-review.md" ] && { echo done; return; }
    [ "$pr_state" = "OPEN" ] && { echo review; return; }
    if [ -f "$wt/.dev/ui-verify-pass.md" ] && grep -q '^Status: CLEAR' "$wt/.dev/ui-verify-pass.md"; then
      trunk=$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@')
      trunk=${trunk:-$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null | sed 's@^origin/@@')}
      ahead=$(git rev-list --count "origin/${trunk:-main}..HEAD" 2>/dev/null || echo 0)
      [ "${ahead:-0}" -gt 0 ] && { echo pr; return; }  # already committed → open PR
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

Output whitelist: `start | apply | preview | audit | commit | pr | review | done`. Guard the output
against this set (mirror `infer_bug_stage_safe`). There is no standalone `verify` stage — the build is
folded into `preview` (`flutter run` = build + deploy); `format` is folded into `audit`.

## Dispatch loop

`export UI_TWEAK_FF=1`, then loop. **Each iteration first checks the repair-exhausted card-terminus**;
only if it does not fire does it call the walker and dispatch:

```
loop:
  # --- card-terminus (checked BEFORE the walker) ---
  repair-count >= 3  → render the engineer card (couldn't do it as a pure look change) and STOP
  # --- otherwise advance ---
  s = infer_ui_stage
  dispatch s
  re-derive   # stage success is NOT the loop terminus
  stop at `done` / a failure / an interactive card
```

`done` resolves to a card by markers: **deliver + PR open + code-review → C5**; **preview-requested +
preview-shown → the post-preview C1 variant ("looks good — ship it / more changes")**; **otherwise →
the iteration C1 ("I'm done — show me / more changes")**.

| stage | action |
|---|---|
| `apply` | `/ui-tweak:apply <source> [figma] [--auto]` — iteration edit (no build). In **repair mode** (`repair-context` present) it reads the error and fixes the edit UI-only (see Correction/Repair loop) |
| `preview` | `/ui-tweak:preview [--auto]` — **Phase 1**: build INTO a device (cascade: boot emulator/sim → connected device incl. physical → honest no-device build-only) so the designer SEES it. Writes `build-pass` + `preview-shown`. Build-fail → repair-context (→ apply, max 3) |
| `audit` | `/ui-tweak:audit [--auto]` — **Phase 2** first check (deliver only): `/format` then the dual-judge on the final cumulative diff. CLEAR → `commit`; BLOCKED → repair-context (→ apply, max 3) |
| `start` | `/ui-tweak:start <ticket>` (deliver only; `/add-worktree`) |
| `commit` | `/commit` (NO extra confirm — "Ship it" on C1 already authorized the handoff, R18) — commit ONLY the files in the Step-5 coverage table (R12); formatter-touched extras go in the PR body `### Formatter-only changes` |
| `pr` | `/pull-request --draft` with the **§6.1 PR body**; title prefixed `[ui-tweak]`; structured read-only ticket comment (`🎨 UI tweak ready for engineer review` + audit verdict + coverage summary) |
| `review` | `/code-review <pr>` → `claude-reports/<ticket>/code-review.md` |
| `done` | terminal: iteration → **C1 (show-me)**; post-preview → **C1 (looks-good)**; deliver (PR open + code-review) → **C5** |

## Wayfinding cards (this orchestrator owns the navigation cards C0–C5)

> The stage-stop cards **C-MISDIRECT / C6 / C7** are rendered by the atomic stages themselves
> (`apply.md` / `preview.md` / `start.md`), since those are the stages that physically stop — see
> `/ui-tweak:apply` Step 0a / Step 5. C0–C5 + the repair-exhausted **engineer card Ce** (the
> flow-navigation cards) live here.

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
  Want to see it on a phone, or make more changes first?
  (When you're ready, I build it onto a device so you can look, then a full check before anyone sees it.)
  ```
  When `.not-deliverable` is present, append the "⚠ N spot(s) weren't changed …" note. After a
  correction, first line becomes *"I adjusted it once more. In total I've changed: <cumulative>."*
- **options**: `I'm done — show me on a phone` *(recommended)* — "I'll build it onto a phone/emulator
  so you can see it." *(OMIT when `.not-deliverable` exists — can't preview a partial; tell them to
  adjust.)* / `I want more changes` — "Tell me what to adjust (e.g. 'move it down one')."
- **routing**: `I'm done — show me` → write `.dev/ui-tweak/preview-requested` → walker → `preview`
  (Phase 1). `I want more changes` / **Other** → Correction loop.

**C1 (looks-good) — preview is live on a device** (`preview-requested` + `preview-shown` present):
- **question**:
  ```
  It's running on <device> now — take a look.  (What changed: <plain summary>.)
  Does it look right? If so I'll do the full check and wrap it up as a proposal for an engineer.
  ```
  **No-device fallback (R18)**: if preview ran build-only (no device found), replace line 1 with
  *"I couldn't find a phone/emulator to show it on, but I confirmed it builds."* and keep the rest.
- **options**: `Ship it` *(recommended)* — "Looks right — run the full check + open a proposal with a
  link on the work item." / `I want more changes` — "Tell me what to adjust; I'll redo and re-show it."
- **routing**: `Ship it` → resolve ticket id from `.dev/ui-tweak/ticket.json`. **Known id → DO NOT ask
  again**: write `.dev/ui-tweak/deliver` → walker (→ start?/audit/commit/pr/review/C5). **No cached
  ticket** → C3 to request one. `I want more changes` / **Other** → Correction loop (which clears the
  preview markers so it re-iterates).
- **Doing nothing is fine**: walking away leaves the reviewed diff in the tree; no extra "leave it"
  button. Shipping (→ draft PR) is the only explicit terminal action.

> **C2 removed (R18).** There is no standalone "change blocked, take it back" designer card anymore.
> Build failures and audit (logic) blocks are the agent's implementation problem — they route to the
> **agent repair loop** (`apply` repair mode) silently, and only after 3 failed attempts surface as the
> **engineer card Ce**. Designers never see a raw "blocked" card mid-flow.

**C3 — chose "Ship it", free-text-started run with NO ticket** (ticket-started runs SKIP this — the id
was already cached in `ticket.json`) — *`AskUserQuestion`, `header: "Work-item no."`*
- **question**:
  ```
  To wrap this up as a proposal for an engineer, I need a work-item number (like CAF-1234).
  You started with a plain-text description, so there's no number yet.
  If you have one, pick Other and paste it.
  ```
- **options**: `I'll paste the number` — "I have a number — I'll type it next." / `Never mind, skip it`
  — "Go back; keep it as a clean change without a proposal."
- **routing**: a number via **Other** (or the follow-up after `I'll paste the number`) → write
  `.dev/ui-tweak/deliver` with that id, continue the walker; `Never mind, skip it` → back to C1.

> **C4 removed (R18).** No separate commit-confirm card. Picking **"Ship it"** on C1 (looks-good) is
> the single authorization for the whole pre-PR series (audit → commit → draft PR) — a second confirm
> would be one click too many. The audit (Phase 2) is the gate that can still stop the handoff (→
> agent repair / engineer card Ce); commit/pr just execute once audit is CLEAR.

**C5 — done** — *plain text (info card, no choice)*
```
📍 Done! I've wrapped it up as a proposal for an engineer and left a link on the work item.
📦 Link: <url>  (draft state — an engineer will look it over; it won't go live automatically.)
👉 Your part is finished. Want more changes? Just tell me.
```

## Correction loop (designer) + Repair loop (agent) — R8 / R18 / §4.5.1

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
        "$wt/.dev/ui-verify-pass.md" "$wt/.dev/dev-reviewer-pass.md" \
        "$wt/.dev/ui-tweak/repair-context" "$wt/.dev/ui-tweak/repair-count"
  ```
  A designer correction is a fresh intent → it also **resets the repair budget** (`repair-count`). The
  designer then iterates from C1 (show-me) again; building only re-happens when they next pick "show
  me", and the dual-judge only when they next "Ship it".

### B. Agent repair — a build-fail or audit-block wrote `repair-context` (R18, max 3)

This is NOT designer-driven. `preview` (build fail) or `audit` (logic block) wrote
`.dev/ui-tweak/repair-context` + bumped `repair-count`. The walker routes to `apply` in **repair
mode**; `apply` reads `repair-context`, fixes the edit **UI-only** (e.g. correct the value that broke
the build, or redo the change without the logic touch), then clears the downstream so the change
re-validates from Phase 1:
```bash
rm -f "$wt/.dev/ui-tweak/repair-context" "$wt/.dev/ui-tweak/build-pass" \
      "$wt/.dev/ui-tweak/preview-shown" "$wt/.dev/ui-tweak/deliver" \
      "$wt/.dev/ui-verify-pass.md" "$wt/.dev/dev-reviewer-pass.md"
# keep preview-requested (still wants the preview) and repair-count (accumulates toward the cap of 3)
```
After 3 attempts (`repair-count >= 3`) the dispatch loop renders the **engineer card Ce** instead of
re-entering apply. A clean `preview` build resets `repair-count`.

- **Power-user escape**: `/ui-tweak:ff --from apply` deletes apply-and-downstream markers, then resumes.

## Deliver PR body — see §6.1 (R2)

The `pr` stage MUST pass a pre-built `## UI Tweak — designer-verifiable summary` body (Source /
Grounding-provenance / Audit verdict / Coverage table with `shared?` / "No screenshot — eyeball
before→after against the Figma node or ticket") — build-only means this body is the reviewer's only
way to judge visual correctness. Never reuse `/pull-request`'s empty placeholder.

## Constraints (carry-over)

Terminal is a **draft PR** — never `draft→ready`, never merge, never mutate ticket status (the only
ticket write is the read-only PR-link comment). No `--pr` flag; deliver only via a human picking C1
`[3]`. Not wired into `/route` / `/ggx-work` / `/ggx-dispatcher`.
