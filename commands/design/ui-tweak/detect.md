---
name: detect
description: "Stage 2 of the /ui-tweak pipeline (GGC-107) — the FIRST dispatched stage INSIDE /ui-tweak:ff, immediately after Step 0 (the worktree split). A cheap, read-only visual-vs-logic triage: it locates the target widget (from ticket text + Figma refs) and READS it, then classifies the requested change. pure-visual → write .dev/ui-tweak/triage-pass (carrying the widget path + a one-line rationale; /ui-tweak:apply reuses that widget) and proceed to :apply. needs-logic → stop BEFORE any edit and recommend reclassifying Design bug → Bug (human-owned, never auto-flip the label): under --auto exit non-zero with a `UI-TWEAK BLOCKED (detect: needs-logic)` error so the dispatcher's terminal-ui-block cleanup runs verbatim; interactive writes .dev/ui-tweak/needs-logic (a card-terminus the orchestrator renders as C-RECLASSIFY). Reads code but NEVER edits it and NEVER writes any marker other than triage-pass / needs-logic. Mirrors /dev:detect (a markerless/marker-driven router that classifies and can abort/redirect). Internal stage: designers run /ui-tweak, not this directly — a misdirect guard routes them back."
---

<!-- RULE: ALL content, including designer-facing CARD text, is English. No Chinese / non-ASCII. -->

# `/ui-tweak:detect`

> **Single responsibility (GGC-107)**: a read-only visual-vs-logic triage that runs BEFORE any edit.
> It is the **middle tier** of a 3-tier cascade, cheap → expensive:
> 1. `ticket-analyze` (GGC-58 design-bug gate) — whole-pool, **text-only**, upstream.
> 2. `/ui-tweak:detect` (this stage) — per-ticket, **reads one widget**, before the worktree work pays off.
> 3. `/ui-tweak:audit` dual-judge — post-apply backstop (unchanged; remains the safety net).
>
> It reads the actual target widget so it catches a misrouted `design bug` (one that really needs
> logic/behaviour changes) **before** `:apply` cuts an edit and `:preview` builds — the waste the late
> `/ui-tweak:audit` BLOCK only catches after a whole apply + build + 3× repair cycle. It NEVER edits
> code and NEVER auto-reclassifies the ticket (the `Design bug → Bug` type-label flip stays human-owned,
> consistent with the existing late-audit reclassify policy).

## Inputs

`<source> [figma-url] [--auto]` — `<source>` is free text or a ticket (ID/URL); optional trailing
`[figma-url]`. `--auto` is the dispatcher / `/ggx-work` path (no cards). Normally reuses
`.dev/ui-tweak/ticket.json` + `.dev/ui-tweak/comments.json` cached by `/ui-tweak:start` (Step 0).

## Outputs

Exactly **one** marker (never both), under `$REPO_ROOT/.dev/ui-tweak/`:

- **`triage-pass`** — pure-visual verdict. Carries the located widget path + a one-line rationale so
  `/ui-tweak:apply`'s Step-4 locate can **reuse the already-resolved widget** instead of re-locating it
  (collapses two "find the widget" passes into one and tightens apply/audit agreement on *what* is
  edited). Format:
  ```
  Verdict: pure-visual
  Widget: <relative/path/to/widget.dart>[:<line>]
  Rationale: <one line — the change is token/colour/typography/spacing/layout/structure only>
  ```
- **`needs-logic`** — needs-logic verdict, **interactive path only** (under `--auto` detect exits
  non-zero instead, see Step 5). A card-terminus the orchestrator renders as **C-RECLASSIFY** before
  the walker runs again. Format:
  ```
  Verdict: needs-logic
  Widget: <relative/path/to/widget.dart>
  Rationale: <one line — why this touches gesture/state/control-flow/data/interaction wiring>
  Suggested: reclassify Design bug -> Bug
  ```

No code is changed. No other marker is written.

## Step 0a — misdirect guard (R5/D11)

If the environment variable `UI_TWEAK_FF` is **not set**, a designer typed `/ui-tweak:detect`
directly (this stage is internal). Do **not** execute. Print card **C-MISDIRECT** and STOP:

```
📍 Looks like you called an internal step directly.
📦 No worries — nothing has changed.
👉 Just type:  /ui-tweak "describe what you want to change in one sentence"  and I'll run the whole flow for you.
```
(C-MISDIRECT is a plain-text notice — it offers no choice, so no `AskUserQuestion` and no footer.)

Only proceed past 0a when `UI_TWEAK_FF=1` (the orchestrator — or the dispatcher's `runUiTweak` prep
agent acting as the orchestrator — sets it). A designer must never see `detect` as a stage name.

## Step 0b — idempotent re-entry

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
```

If `$REPO_ROOT/.dev/ui-tweak/triage-pass` already exists, this run already triaged pure-visual on a
prior iteration — print `Detect: already pure-visual (triage-pass present). Next: /ui-tweak:apply.`
and STOP (no re-read, no re-write). The walker (`infer_ui_stage`) normally consumes `triage-pass`
before re-dispatching `detect`, so this is a belt-and-braces guard for a direct re-invocation.

## Step 1 — usage log + resolve profile

```bash
echo "{\"skill\":\"ui-tweak:detect\",\"ts\":\"$(date -u +%FT%TZ)\"}" >> ~/.gogox-claude-usage.jsonl 2>/dev/null || true
```

Resolve the platform profile: `<repo>/.gogox-claude.yaml` → `platform`; fallback
`~/.claude/commands/profiles/registry/<basename>.yaml`. Note the friendly repo / screen name for the
designer-facing C-RECLASSIFY card.

## Step 2 — resolve the requirement (read-only)

Mirror `/ui-tweak:apply` Step 3 exactly (read-only — never change status/assignee, never comment):

- Ticket (ID/URL): **reuse `.dev/ui-tweak/ticket.json`** if `/ui-tweak:start` cached it. Otherwise
  fetch via `mcp__claude_ai_Linear__get_issue` / Jira (`_ticket-lib.md`), read-only, and cache it.
- **Read the comment thread too** (GGC-84): reuse `.dev/ui-tweak/comments.json`; if absent/empty,
  re-fetch read-only. Fail-soft — proceed with the description alone if comments are unavailable.
- Derive the requirement from the UNION of title + description + the full comment thread; the
  **most-recent comment is authoritative** when it refines or contradicts the description.
- Free text: use it verbatim.
- **Ticket reference image (GGC-62)**: if the ticket carries a screenshot (an `.attachments[]` entry
  in `ticket.json`, or an inline `![](https://uploads.linear.app/...)`), `curl -fsSL` it and **Read it
  as an image** — the picture often pins scope a sentence loses, and it is part of the visual-vs-logic
  signal (a before/after that only repaints pixels is a strong pure-visual signal; one that adds a
  control or changes what a tap does is a strong needs-logic signal).

## Step 3 — locate the target widget (read-only)

Locate the single primary target widget the change is about (the same locate `/ui-tweak:apply` Step 4
would do, but read-only and stopping at the FIRST widget rather than enumerating every `Ti`):

- From the requirement + any Figma node refs, grep the target screen/component. Record the resolved
  `<file>[:<line>]`.
- If the target genuinely **cannot be located** (no plausible code site), this is NOT a detect verdict
  — let `/ui-tweak:apply`'s own locate gate handle the not-found case (it renders card **C6**). Write
  **no marker**, print `Detect: target not locatable read-only — deferring to /ui-tweak:apply locate
  gate.` and proceed (the walker, finding no `triage-pass`, would re-dispatch detect; to avoid a loop,
  write `triage-pass` with `Widget: <unresolved>` and a rationale noting the locate is deferred, so
  apply runs and renders C6 if still not found). Prefer to resolve a widget — a deferred locate is the
  rare fallback.

Read the located widget (read-only). You may read the immediately-relevant collaborators (the
widget's build method, the state/controller it reads, the gesture/callback wiring) — enough to judge
whether satisfying the request requires touching behaviour. Do not read the whole tree; this is a
cheap triage, not a full investigation.

## Step 4 — classify pure-visual vs needs-logic

Decide whether the **requested change** (not the widget in general) can be satisfied by visual edits
alone:

- **pure-visual** — token / colour / typography / spacing / sizing / layout / structure. Changing a
  value, a style, a constraint, a widget arrangement; nothing about *what the screen does* changes.
- **needs-logic** — the request can only be satisfied by changing **gesture / state / control-flow /
  data / interaction wiring**: a different tap target or navigation, a new/altered callback, a state
  transition, conditional rendering driven by new logic, a changed data binding, etc. (The motivating
  case is CAF-884: "reusing a DP order while on the TP page redirects to the TP flow" — a routing/
  control-flow bug dressed as a design bug.)

When genuinely ambiguous, **lean pure-visual and pass through** (mirror the GGC-58 gate's bias): the
`/ui-tweak:audit` dual-judge is the post-apply backstop, so a false pure-visual is caught later, while
a false needs-logic wrongly blocks a real design bug from the cheap path. Only emit needs-logic when
the logic dependency is clear from the code you read.

## Step 5 — emit the verdict

### pure-visual

Write the `triage-pass` marker (see Outputs) carrying the resolved widget path + a one-line rationale,
then STOP:

```bash
mkdir -p "$REPO_ROOT/.dev/ui-tweak"
cat > "$REPO_ROOT/.dev/ui-tweak/triage-pass" <<EOF
Verdict: pure-visual
Widget: <relative/path>[:<line>]
Rationale: <one line>
EOF
```

Print: `Detect: pure-visual (<widget>). Next: /ui-tweak:apply (reuses the resolved widget).`

### needs-logic — `--auto` (dispatcher / `/ggx-work`) path

Do **not** write `needs-logic` (no card under `--auto`). **Exit non-zero** with a single stderr line
carrying the `UI-TWEAK BLOCKED` prefix so the caller routes it as a terminal block:

```
UI-TWEAK BLOCKED (detect: needs-logic): <one-line rationale>. Recommend reclassifying Design bug -> Bug; no edit was made.
```

The prefix is what the dispatcher keys on: the `runUiTweak` prep agent surfaces this as
`{ outcome: "failed", uiTweakFailed: true, stage: "ui:detect-block", error: "UI-TWEAK BLOCKED …" }`,
and `classifyFailure` routes any `UI-TWEAK BLOCKED` error to `terminal-ui-block` (GGC-37) — which
removes `dispatcher-dev-in-flight`, adds `need-revision`, resets status `To-do`, and posts the
`<!-- dispatch-triage-ui-blocked -->` reclassify-recommendation comment. **No new dispatcher code, no
new marker grammar.** A standalone `/ggx-work --auto` (not via the dispatcher) classifies the non-zero
exit as `pipeline-failed` and posts its own error comment for a human — also acceptable.

### needs-logic — interactive (designer) path

Write the `needs-logic` marker (see Outputs) and STOP. Do **not** render a card here — the marker is a
**card-terminus** the orchestrator checks BEFORE the walker (alongside the repair-exhausted terminus),
and renders as **C-RECLASSIFY** (a *pre-edit* reclassify card — NOT `Ce`, which fires after edits and
says "everything's back to how it was", misrepresenting a pre-edit stop). The orchestrator-rendered
card respects the misdirect guard so the designer never sees `detect` as a stage name.

```bash
mkdir -p "$REPO_ROOT/.dev/ui-tweak"
cat > "$REPO_ROOT/.dev/ui-tweak/needs-logic" <<EOF
Verdict: needs-logic
Widget: <relative/path>
Rationale: <one line>
Suggested: reclassify Design bug -> Bug
EOF
```

Print: `Detect: needs-logic (<widget>). Stopping before any edit; orchestrator renders C-RECLASSIFY.`

## Stop

Detect writes no other state and changes no code. The actual stage advance happens in `/ui-tweak:ff`'s
walker / dispatch loop — this stage's job is to classify and let routing fall out of the marker it
wrote (`triage-pass` → `apply`; `needs-logic` → C-RECLASSIFY terminus; `--auto` needs-logic → non-zero
exit).
