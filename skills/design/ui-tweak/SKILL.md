---
name: ui-tweak
description: "UI-Designer-safe codebase edit. Given a UI change described as free text, a Linear/Jira ticket (ID or URL), and/or a Figma link, it edits the codebase for UI-only changes (visual values, layout, structure) and builds to confirm it compiles — never touching logic or build config (a PreToolUse hard-block hook enforces this physically at the file level; an independent 2-judge panel blocks logic changes inside UI files). Use when a designer says 'make the order-page button 5dp bigger', 'change this color', 'increase the padding', 'reorder these sections', passes a ticket like CAF-1234 that describes a UI tweak, or provides a Figma frame to match."
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
- The pipeline, stages, markers, guard, judges, and every wayfinding card are specified in
  `plans/ui-tweak-v2-build-spec.md` and implemented in `commands/design/ui-tweak/{ff,apply,verify,start}.md`.

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
   orchestrator runs `apply` only (iteration is build-free), then:
   - iteration terminal → presents **card C1 (show-me)** — two choices: *I'm done — show me on a phone*
     (→ Phase 1 `preview`: build onto a device) or *more changes*;
   - after preview → **card C1 (looks-good)** — *Ship it* (→ Phase 2 `audit` → commit → draft PR) or
     *more changes*;
   - the designer picks an option, or just describes another change in words (a correction via the
     `AskUserQuestion` **Other** field) — the orchestrator re-navigates automatically.

**Never run `apply` / `preview` / `audit` / `commit` yourself, and never hand the designer raw
git/judge/build output.** The orchestrator owns the stages and translates everything into
plain-language cards.

## The guarantee (read this first)

The skill accepts **any UI-form change** and is built so a designer **cannot break logic**, on two
levels:

- **HARD — file-level containment (the `PreToolUse` hook `skills/_lib/ui_guard.py`).** While armed it
  *physically* blocks any write to a non-UI/logic file (ViewModels, Repositories, `data/`/`domain/`/
  `network/`/DI, build config, manifests, tests, generated code), any edit outside the repo or
  through a symlink, **creating any new source file**, and **all Bash**. The unbreakable floor: a
  designer can only ever touch existing UI-eligible files.
- **BEST-EFFORT — no-logic-inside-UI-files (build + a 2-judge panel, across two phases R18).** Inside
  a UI file (Compose/SwiftUI/Flutter share UI+logic syntax) no static rule perfectly tells UI from
  logic, so two checks run at **different times**: (1) the **build** runs in **Phase 1
  (`/ui-tweak:preview`)** when the designer asks to see it — it builds the change INTO a device and
  reverts anything that won't compile; (2) the **decorrelated 2-judge panel** (`ui-verify-agent`,
  sonnet UI-lens + `dev-reviewer`, opus behavior-lens, with a deterministic structural pre-pass) runs
  in **Phase 2 (`/ui-tweak:audit`)** when the designer ships — ONCE, on the final cumulative diff, and
  **reverts on any logic/behavior change (unanimous CLEAR required) before anything is committed or a
  PR is opened**. Iteration stays free (apply only, no build); a build or audit failure is treated as
  the agent's implementation problem and **auto-repaired in `apply` (max 3 attempts)** before the
  designer is ever shown an "ask an engineer" card. The promise inside a UI file is "you only ever
  make UI changes; the panel is the safety net at the gate".
- **DEFAULT policy is `strict`** (token-level value-only hard gate) for the designer alias — this is
  what keeps the *un-audited working-tree diff safe while iterating*: strict proves value-only at edit
  time without an LLM. `open` (accept any UI-form change, logic caught only by the panel) is per-repo
  / power-user opt-in; under `open` the diff is genuinely un-audited until ship, and the panel still
  gates the PR.

**Two phases, two designer decisions (R18).** Iteration is build-free — adjust as often as you like
for free. **Phase 1**: pick "I'm done — show me" and the change is **built + launched onto a real
device** (`flutter run` covers Android emulators + iOS simulators; cascade boots one, else uses a
connected device incl. a physical phone, else honestly falls back to build-only) so **you** can look
at it. The skill only gets the app running, then **hands you the device — it never screenshots, taps,
navigates, or grants permissions; you look and drive it yourself**. **Phase 2**: once it looks right, pick "Ship it" — the full logic audit runs, then it
**commits and opens a draft PR** with a designer-verifiable summary + a link on the work item. It
**never** auto-merges, flips draft→ready, or mutates ticket status (the only ticket write is the
read-only PR-link comment).
