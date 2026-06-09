---
name: ui-tweak
description: "UI-Designer-safe codebase edit. Given a UI change described as free text, a Linear/Jira ticket (ID or URL), and/or a Figma link, it edits the codebase for UI-only changes (visual values, layout, structure) and builds to confirm it compiles — never touching logic or build config. There is no edit-time hook: a build (Phase 1) plus an independent decorrelated 2-judge panel (Phase 2, both judges always run) catch any logic/behavior change and revert the whole run before anything is committed. Use when a designer says 'make the order-page button 5dp bigger', 'change this color', 'increase the padding', 'reorder these sections', passes a ticket like CAF-1234 that describes a UI tweak, or provides a Figma frame to match."
---

<!--
  RULE: All skill content — including every designer-facing CARD — must be written in English.
  No Chinese / non-ASCII anywhere. No exceptions.
-->

# `/ui-tweak`

> **One-line summary**: a UI Designer describes a UI change in plain language; this skill edits only
> the UI (visual / layout / structure) in the codebase, confirms it still compiles, and is blocked
> from touching logic. It then **guides the designer step by step** with plain-language cards — they
> never need to know git / commit / PR / build exist.

**This skill is a thin alias.** It hands `$ARGUMENTS` to the orchestrator `/ui-tweak:ff`, which runs
the atomic pipeline (`apply → verify`) and prints a **wayfinding card** at every stop. The designer
only ever types `/ui-tweak`; everything else is internal.

```
/ui-tweak <source> [figma-url]   ≡   /ui-tweak:ff <source> [figma-url]
```

- `<source>` is **polymorphic**: free text (`make the order-page button 5dp taller`), a Linear/Jira ticket
  (`CAF-1234` / a ticket URL — the richest source; often already names the screen, target value, and
  Figma frame), or implicitly carries a trailing `[figma-url]` to ground the exact target.
- The pipeline, stages, markers, judges, and every wayfinding card are implemented in
  `commands/design/ui-tweak/{ff,start,apply,preview,audit,demo}.md`.

## What to do when invoked

1. **Empty / `help` / `?` / garbled `<source>`** → do NOT dispatch. Print the first-contact card
   **C0** so a designer learns how to ask:
   ```
   📍 Hi! I can change how the App looks — sizes, colors, spacing, layout.
   📦 Just describe it in plain words. It helps to say "which screen + what to change".
   👉 e.g.  /ui-tweak "make the order-page primary button a bit taller"
            /ui-tweak "increase the home-card corner radius"   (a Figma link can go at the end)
   ```
   (C0 is a plain-text info card — no choice — so no footer / no `AskUserQuestion`.)
2. **Otherwise** → dispatch `/ui-tweak:ff $ARGUMENTS` (NO `--pr` — that flag does not exist). The
   orchestrator **first silently splits a ticket-named worktree** (R19 / Step 0 → `/ui-tweak:start` →
   `/add-worktree`, exactly like `/dev:ff` and `/port:ff`, so designer edits never touch the
   engineer's checkout). A work-item number is **required** — like `/dev:ff` and `/port:ff`, a UI
   change is always tracked under a work item; if the input carries none, the orchestrator asks for it
   up-front (card C-WT) and does not start until it has one (no in-place editing, B3). Then it runs
   `apply` only (iteration is build-free), then:
   - iteration terminal → presents **card C1 (show-me)** — three choices: *I'm done — show me on a
     phone* (→ Phase 1 `preview`: build onto a device), *It already looks right — ship it* (R20 — the
     designer already saw it on their own device; skip the device preview, run a build-only compile
     gate, then go straight to Phase 2), or *more changes*;
   - after preview → **card C1 (looks-good)** — *Ship it* (→ Phase 2 `audit` → commit → draft PR),
     *Ship it — and record a short demo* (same, plus a zero-input capture of the screen they just
     approved is embedded in the PR — recorded after they leave, no waiting), or *more changes*;
   - the designer picks an option, or just describes another change in words (a correction via the
     `AskUserQuestion` **Other** field) — the orchestrator re-navigates automatically.

**Never run `apply` / `preview` / `audit` / `commit` yourself, and never hand the designer raw
git/judge/build output.** The orchestrator owns the stages and translates everything into
plain-language cards.

## The guarantee (read this first)

The skill accepts **any UI-form change** and is built so a designer **cannot ship broken logic**.
There is **no edit-time hook** — enforcement is deferred to two checks that run at different times:

- **Build (Phase 1, `/ui-tweak:preview`).** When the designer asks to see it, the change is built
  INTO a device; anything that won't compile is reverted.
- **Decorrelated 2-judge panel (Phase 2, `/ui-tweak:audit`).** When the designer ships, BOTH judges
  run ONCE on the final cumulative diff: `ui-verify-agent` (sonnet, UI-lens) + `dev-reviewer` (opus,
  behavior-lens), plus a deterministic structural pre-pass. **Unanimous CLEAR is required**; any
  logic/behavior change — a non-UI file that should never have been touched, OR logic edited inside
  an otherwise-UI file — reverts the whole run **before anything is committed or a PR is opened**.
  Because nothing upstream proves the diff is value-only, the panel **always runs both judges**;
  neither is skipped.

Iteration stays free (apply only, no build). A build or audit failure is treated as the agent's
implementation problem and **auto-repaired in `apply` (max 3 attempts)** before the designer is ever
shown an "ask an engineer" card. The working-tree diff is **un-audited while iterating** — the panel
is the safety net at the gate, not at edit time. The promise: "you only ever make UI changes; the
build and the panel catch anything that isn't, before it ships."

**Two phases, two designer decisions (R18).** Iteration is build-free — adjust as often as you like
for free. **Phase 1**: pick "I'm done — show me" and the change is **built + launched onto a real
device** (`flutter run` covers Android emulators + iOS simulators; cascade uses an already-running /
connected device first — incl. a physical phone — else boots one, else honestly falls back to
build-only; a simulator is pre-warmed in the background when the run starts, so this is usually fast)
so **you** can look
at it. The skill only gets the app running, then **hands you the device — it never screenshots, taps,
navigates, or grants permissions; you look and drive it yourself**. The one exception is the opt-in
*"Ship it — and record a short demo"*: AFTER you approve the look, it passively captures what is
already on the screen (still zero taps / zero navigation) so the PR can show it — best-effort, and
never delays the PR. **Phase 2**: once it looks right, pick "Ship it" — the full logic audit runs, then it
**commits and opens a draft PR** with a designer-verifiable summary + a link on the work item; the
PR's Demo section embeds the ticket's design visuals and — if you handed one over at the
"looks good?" card — your own screenshot/recording (the skill itself still never captures any). It
**never** auto-merges or flips draft→ready. When the draft PR opens it moves the work item to
**In Review** and removes the `ready-to-dev` label (keeping the rest, e.g. `design bug`) — the same
handoff step the dev flow does — so the ticket leaves the queue and lands in the engineer-review
column; `assignee` is never touched (Linear-only). The ticket writes are: the PR-link comment, that
status/label transition, and attaching your supplied capture, if any.

**Unattended lane (`--auto` — dispatcher/orchestrator use, not for designers).** Linear tickets
labeled `design bug` are routed here by `/route` / `/ggx-work` / `/ggx-dispatcher` as
`/ui-tweak:ff <ID> --auto`. In that mode no cards render and **no device preview ever happens**;
the run auto-takes the direct-ship path after a single apply — build-only compile gate → the SAME
decorrelated 2-judge panel (**both judges always run, sonnet + opus, neither skipped**) → commit →
**draft PR** (terminal; never draft→ready, never merge). The engineer reviewing that draft PR is
the human gate that replaces the cards. Interactive `/ui-tweak` is unchanged — this lane is purely
additive.
