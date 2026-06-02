# `/ui-tweak` Guide (for UI Designers)

> This guide explains how to use `/ui-tweak`, where its safety guarantees are, and how to get
> unstuck when something goes wrong. Written primarily for **UI Designers (no coding needed)**,
> secondarily for engineers who maintain the skill.

---

## 1. What it is / what it can and can't do

**One-line definition**: you describe a UI change in plain words (or with a Figma link / a ticket),
and Claude edits the "look-only" parts in the codebase for you. When you want to see it, it **builds
the app onto a device (an emulator or your connected phone) so you can look at it yourself**. Once
you're happy, it wraps the change up as a **proposal for an engineer (a draft PR)**.

**Can do (visual / layout / structural UI changes):**
- Values: spacing, padding, margin, corner radius, font size/weight, color, opacity, shadow, element
  size, etc.
- Layout / structure: reorder, swap sections, add/remove on-screen UI elements, change containers,
  change alignment.
- Match Figma: change values to match what the Figma frame pins.

**Can't do (will be blocked):**
- Logic: price calculation, conditionals, what a button *does* when pressed, how data is read/stored,
  where a screen navigates.
- Build / project config: `build.gradle`, `pubspec.yaml`, `AndroidManifest.xml`, `Info.plist`, etc.

**Does for you, so you don't have to:** compile, install the app onto a device, commit, open a draft
PR — all at the right moment. **It never auto-merges, never flips a draft PR to ready, never changes
ticket status.**

> ⚠️ About "seeing it": it only **builds and launches** the app on the device, then **hands the
> device to you**. It will **not** screenshot for you, tap around for you, grant permissions, or
> navigate to a specific screen. You look and drive to the page you want to check, yourself.

---

## 2. Prerequisites (check before your first run)

1. **Run `install.sh` first**: this registers a "guard" program (a hook) into Claude Code's settings
   file `~/.claude/settings.json`. Without it, the skill **refuses to work** (the hard guarantee is
   missing).
2. **After registering the hook, restart your Claude Code session** so it takes effect — the hook is
   loaded at session start, so a fresh install without a restart isn't active yet.
3. **Use it inside the actual App project folder**, not inside this tooling repo — i.e. inside the
   Android / Flutter / iOS code project. That project's root needs a `.gogox-claude.yaml` (tells the
   skill which platform it is and which commands compile it / build it onto a device).
4. **To see it on a device**: either have an emulator/simulator you can boot, or connect a physical
   phone first. If neither exists, that's fine — it will honestly tell you "I couldn't find a device
   to show it on, but I confirmed it compiles", and you decide whether to ship anyway.

> Reminder: hook, guard, and "guard program" all mean the same thing — a small program that checks
> before Claude writes any file and blocks the ones it shouldn't touch.

---

## 3. How to use it

Command format:

```
/ui-tweak <source> [figma-url]
```

`<source>` can be one of three forms; the skill auto-detects which:

**A. Free text** (just say what you want)
```
/ui-tweak make the order-page button 5dp taller
```

**B. Linear / Jira ticket (ID or URL)** — usually the richest source, because a ticket often already
names the screen, the component, the target value, and may attach a Figma link.
```
/ui-tweak CAF-1234
```
> The ticket is **read-only**: the skill only *reads* it — it never changes status, assigns, or
> comments. The one exception is that, after you choose to ship, it posts a **draft-PR link** on the
> ticket (a read-only notice; it does not change status).

**C. Figma link** (match the design)
```
/ui-tweak align the order-page button to the design https://figma.com/design/....?node-id=...
```

The trailing `[figma-url]` is optional: add it when you want the skill to read exact values from
Figma; omit it for plain text, or when the ticket already contains the right Figma link.

---

## 4. What happens when it runs (plain-language flow — two phases)

You only ever see **plain-language cards**; just pick a number or reply in one sentence. Behind the
scenes there are two phases:

**Iterate (free, fast)**
1. **Parse the source + edit code**: works out "what to change" from your text / ticket / Figma, shows
   you a table ("which files, current value → target value") to confirm direction, then edits only the
   look-related parts. **This phase does NOT compile**, so you can adjust as many times as you like,
   fast.
2. **Asks you**: "I made the change → **show me / more changes**". To adjust, just say it (e.g.
   "a bit bigger", "move it down one") and it stacks the change on top — no compile wait.

**Phase 1: show you (runs only when you pick "I'm done — show me")**
3. It **compiles + installs + launches** the change onto a device (emulator/simulator, or your
   connected physical phone; if none, it falls back to "just confirm it compiles" and tells you
   honestly).
4. The moment the app is up, **it stops and hands the device to you** — you look and tap to the page
   you want yourself. It does not screenshot or drive the app.
5. Then it asks: "**Does it look right? → Ship it / more changes**".

**Phase 2: ship (runs only when you pick "Ship it")**
6. It runs the **full logic check** (an independent AI audit confirming only the look changed, nothing
   about how the program runs), and on pass **commits and opens a draft PR**, leaving a link on the
   ticket.
7. Done — your part is finished. The draft PR won't go live automatically; an engineer does the final
   review.

**What if compile or the logic check fails?**
You will **not** see a wall of compile errors. The skill treats it as "the implementation has a
problem" and **fixes it itself (up to 3 times)**. If it still can't after 3 tries (usually meaning the
change actually needs to touch how the program runs, not just the look), it tells you "**this part
should go to an engineer**".

---

## 5. Safety guarantees (plain language — two layers, two moments)

The design goal: **make it impossible for a designer to break logic by accident.** Protection comes at
two strengths:

**Layer 1: hard guarantee (ironclad, no AI judgment)**
- It **physically cannot touch non-UI files**: ViewModels, Repositories, data/network layers, DI,
  build config, manifests, tests, generated files — these are **unwritable** during the edit window.
- During the edit window, **no terminal (Bash) command can run at all** — build tools are engines that
  run arbitrary code, and there's no such thing as a "safe command allowlist", so it's all blocked.

**Layer 2: best-effort (AI + compile), run at two different moments:**
- The **compile** runs when you pick "show me" (Phase 1); if it doesn't compile, it's reverted.
- The **independent AI auditors (two, with different lenses)** run when you pick "Ship it", **once**,
  just before the PR opens; they read the whole diff to decide whether logic was touched. **If either
  one flags logic, the whole change is sent back for the agent to redo (never silently shipped).**

> Why put the logic check at the very end, once? Because while you iterate you don't need to burn an
> AI audit every time — running it once, right before shipping, is the most economical, and it audits
> exactly the version that will ship. In the **default `strict` mode**, the working tree is locked by
> the guard to "values only" while you iterate, so skipping the audit mid-iteration is safe.

**In one sentence:**
Ironclad = "can't touch non-UI files" + "no terminal commands during the edit". "No logic inside a UI
file" is **best-effort** — backed by the pre-ship AI audit + compile, not a physical impossibility —
so **you should still only make UI changes**.

---

## 6. `strict` vs `open` modes

| Mode | Allows | How it's gated | When |
|---|---|---|---|
| **strict (designer default)** | values only, or pure reorder (same lines, new order) | the guard hard-checks at the character level and only lets value changes through (locked at edit time, no AI needed) | everyday designer use. Safest — would rather over-block than let something wrong through |
| **open (power-user / per-repo opt-in)** | any "UI-form" change: values, layout, reorder, add/remove UI elements | the guard only blocks non-UI files; inside a UI file it relies entirely on the pre-ship AI audit + compile | larger structural changes, for people who accept "the working tree is un-audited until ship" |

Quick rule: **designers default to `strict` (safest, values / pure reorder only).** `strict` blocks
some otherwise-reasonable structural UI changes — that's the price of certainty; switching to `open`
when you hit that is a per-repo / power-user choice, not the default.

---

## 7. What gets blocked (examples)

These are blocked; the skill stops and tells you why:

- **Editing a ViewModel / Repository**: e.g. the value lives only in a constant in `OrderViewModel.kt`
  — not a pure UI change; blocked, routed to engineering.
- **Editing price-calculation logic**: e.g. `if (user.isPremium) price = base * 0.8` — that's
  behavior; blocked.
- **Adding an onClick that calls a function**: changing a button's click to call a new function, or
  adding a click event that *does something* — that's behavior (logic), not styling; blocked.
- **Editing build / project config**: touching `build.gradle`, `pubspec.yaml`, `Info.plist`,
  `AndroidManifest.xml`, etc.; blocked.
- **Renaming an id / function the code references**: changing a referenced `@+id/...` or a function
  name could make the program fail to find things; blocked.

---

## 8. Limits & known weak spots

- **Flutter / SwiftUI / Compose mixed files are hardest to tell apart**: UI and logic live in the same
  file with the same syntax, and no static rule perfectly separates "UI vs logic".
- So in **`open` mode the guarantee inside a UI file is "pre-ship review + compile" level**, not
  physical impossibility. It defends against **accidental breakage**, not against someone deliberately
  trying to bypass it. (`strict` doesn't have this weakness — it's locked to values-only at edit time.)
- **Seeing it needs a device**: with no emulator / physical phone, it can only confirm "it compiles",
  not show you the screen — connect a device and re-run, or ship and let an engineer look.
- Android is the cleanest case (resource files are clearly separated); for Flutter / SwiftUI, changes
  are best confined to theme / token files, or to simple value/color swaps inside a widget file.

---

## 9. Troubleshooting

**Most important: a prior run was interrupted, so now every command is blocked**
- Cause: during the edit window the skill drops an "armed" marker file (the sentinel). If the previous
  run was interrupted (crash, Ctrl-C, context reset), that marker is left in the "armed" state, and the
  guard then blocks even the simplest command on the next run.
- Normal fix: re-run `/ui-tweak` — its first step detects and clears the stale marker itself.
- Manual fix (when stuck): delete the marker file.
  ```
  rm <project-root>/.dev/ui-designer-mode.json
  ```

**Hook not taking effect? (changes aren't blocked, or the skill says guard MISSING)**
- Fix: **re-run `install.sh`**, then **restart the Claude Code session**.
- Note: the hook must be registered in `~/.claude/settings.json`, not `settings.local.json` (the
  latter overrides the former, creating a "looks installed but the guard isn't active" illusion). When
  in doubt, re-run `install.sh` and read its notices.

**"Show me" can't open a device / no device found**
- It tries, in order: boot an emulator/simulator → use an already-connected device (incl. physical) →
  fall back to "just confirm it compiles".
- To view on a physical device, connect the phone first (USB / wifi, visible in `flutter devices`),
  then run "show me".

**The skill's description looks wrong / the skill isn't recognized**
- Usually the `SKILL.md` frontmatter (the `---`-delimited block at the top) isn't on the **first line**.
- Fix: make sure line 1 of `SKILL.md` is `---`; frontmatter must be at the very start of the file.

---

## 10. Where it ends (important)

`/ui-tweak`'s terminal is a **draft PR**, and it only gets there **when you personally pick "Ship it"**:

- Iterating, or picking "show me" → nothing is shipped; the change just stays in your working tree.
- Picking "Ship it" → logic check → commit → open a **draft PR** + leave a link on the ticket.
- **It never auto-merges, never flips draft → ready, never changes ticket status.** The human review
  step is kept on purpose — in `open` mode "no logic inside a UI file" is only best-effort, so a human
  must do the final check on the PR.
