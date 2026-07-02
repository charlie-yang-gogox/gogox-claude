# `/ui-tweak` Guide (for UI Designers)

> This guide explains how to use `/ui-tweak`, where its safety guarantees are, and how to get
> unstuck when something goes wrong. Written primarily for **UI Designers (no coding needed)**,
> secondarily for engineers who maintain the skill.

---

## 1. What it is / what it can and can't do

**One-line definition**: you describe a UI change in plain words (or with a Figma link / a ticket),
and Claude edits the "look-only" parts in the codebase for you. When you want to see it, it **builds
the app onto a device (an emulator or your connected phone), navigates to the affected screen, and
shows you a screenshot of your change**. Once you're happy, it wraps the change up as a **draft PR for
an engineer to review**.

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

> ⚠️ About "seeing it": it **builds and launches** the app on the device, then **navigates to the
> affected screen and captures it for you** (a screenshot + short recording), so you review the
> *result* instead of driving the device. Navigation is **navigation-only** — it taps tabs / menus /
> list rows to reach the screen, but **never** taps confirm/submit/pay/delete, grants permissions,
> types, or logs in. If a screen needs login (or it simply can't reach it), it captures **nothing**
> rather than a wrong screen — and you are never asked to drive. The screenshot it captured is what
> the "does it look right?" card shows you, and it's embedded in the PR automatically.

---

## 2. Prerequisites (check before your first run)

1. **Run `install.sh` first**: this links the skill / commands / agents into Claude Code. **It no
   longer installs any hook and leaves no "guard" in your settings file** (`~/.claude/settings.json`)
   — safety is enforced instead by a build (Phase 1) plus a 2-judge panel (Phase 2) that run before
   you ship, not at edit time (see "The guarantee" below).
2. **Use it inside the actual App project folder**, not inside this tooling repo — i.e. inside the
   Android / Flutter / iOS code project. That project's root needs a `.gogox-claude.yaml` (tells the
   skill which platform it is and which commands compile it / build it onto a device).
3. **To see it on a device**: either have an emulator/simulator you can boot, or connect a physical
   phone first. If neither exists, that's fine — it will honestly tell you "I couldn't find a device
   to show it on, but I confirmed it compiles", and you decide whether to ship anyway.

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
/ui-tweak <ticket-id>
```
> The ticket is **read-only**: the skill only *reads* it — it never changes status, assigns, or
> comments. The exceptions: after you choose to ship, it posts a **draft-PR link** on the ticket, and
> it **attaches the demo it captured** (or a screenshot/recording you handed over) to the ticket (so
> the PR can show it). Neither touches status or assignee.

**C. Figma link** (match the design)
```
/ui-tweak align the order-page button to the design https://figma.com/design/....?node-id=...
```

The trailing `[figma-url]` is optional: add it when you want the skill to read exact values from
Figma; omit it for plain text, or when the ticket already contains the right Figma link.

> **A work-item number is always required.** Forms A and C still work, but if your input doesn't carry
> a number (like `<ticket-id>`) the skill asks for one up-front before it starts — every change is tracked
> under a work item and handed to an engineer that way. The quickest path is form B (start from the
> ticket), or include the number in your text.

---

## 4. What happens when it runs (plain-language flow — two phases)

You only ever see **plain-language cards**; just pick a number or reply in one sentence. Behind the
scenes there are two phases:

**Set up (automatic, silent)**
0. Before touching anything, it puts your change in **its own private space**, separate from everyone
   else's work, so nothing you do here disturbs the team's current code. If your request already names
   a work-item number (like `<ticket-id>`) it uses that automatically; if you started from plain text with
   no number, it asks once up-front: *"what's the work-item number for this?"* — **a number is
   required** (every change is tracked under a work item, so it can later be handed to an engineer). If
   you don't have one, create it first or ask your PM/engineer, then run `/ui-tweak` again with it. You
   never deal with any of the setup yourself.

**Iterate (free, fast)**
1. **Parse the source + edit code**: works out "what to change" from your text / ticket / Figma, shows
   you a table ("which files, current value → target value") to confirm direction, then edits only the
   look-related parts. **This phase does NOT compile**, so you can adjust as many times as you like,
   fast.
2. **Asks you**: "I made the change → **show me / ship it as-is / more changes**". To adjust, just say
   it (e.g. "a bit bigger", "move it down one") and it stacks the change on top — no compile wait.
   **Already looked at it on your own device?** Pick "**It already looks right — ship it**": it skips
   the phone preview, quickly confirms the latest change still works, and goes straight to the final
   check + handoff (your own build might be from before the latest tweak, so that quick confirm always
   runs).

**Phase 1: show you (runs only when you pick "I'm done — show me")**
3. It **compiles + installs + launches** the change onto a device (your connected physical phone or
   an already-running emulator/simulator is used first; otherwise it boots one; if none exists, it
   falls back to "just confirm it compiles" and tells you honestly). A simulator is quietly warmed up
   in the background from the moment your run starts, so this step usually skips the slow cold boot.
4. The moment the app is up, **it navigates to the affected screen and captures it for you**
   (screenshot + short recording). Navigation is **navigation-only** — it taps tabs / menus / rows to
   reach the screen, but never taps confirm/pay/delete, grants permissions, types, or logs in. If it
   can't reach the screen (e.g. a login wall), it captures **nothing** rather than a wrong screen — you
   are never asked to drive.
5. Then it shows you the captured screenshot and asks: "**Does it look right? → Ship it / more
   changes**". And if you took a screenshot or recording yourself, you can hand the file over right
   here (paste/drag it) — it gets attached to the PR alongside the captured demo so the engineer sees
   the actual result.

**Phase 2: ship (runs only when you pick "Ship it")**
6. It runs the **full logic check** (an independent AI audit confirming only the look changed, nothing
   about how the program runs), and on pass **commits and opens a draft PR**, leaving a link on the
   ticket. The PR carries whatever visuals exist — the demo it captured during preview, the ticket's
   design images / Figma link, and your own screenshot if you handed one over — so the engineer sees
   what to compare.
7. Done — your part is finished. The draft PR won't go live automatically; an engineer does the final
   review.

**What if compile or the logic check fails?**
You will **not** see a wall of compile errors. The skill treats it as "the implementation has a
problem" and **fixes it itself (up to 3 times)**. If it still can't after 3 tries (usually meaning the
change actually needs to touch how the program runs, not just the look), it tells you "**this part
should go to an engineer**".

---

## 5. Safety guarantees (plain language)

The design goal: **make it hard for a designer to break logic by accident.** There is **no edit-time
guard** — protection is deferred to two checks that run before anything ships:

- **Compile (Phase 1)** runs when you pick "show me"; if it doesn't compile, it's reverted.
- **Two independent AI auditors, with different lenses (Phase 2)** run when you pick "Ship it",
  **once**, just before the PR opens; they read the whole diff and decide whether logic was touched.
  **Both must pass — if either flags logic (a non-UI file that shouldn't have changed, OR logic
  edited inside a UI file), the whole change is sent back for the agent to redo, never silently
  shipped.** Because nothing upstream proves the diff is values-only, **both auditors always run** —
  neither is skipped.

> Why put the logic check at the very end, once? Because while you iterate you don't need to burn an
> AI audit every time — running it once, right before shipping, is the most economical, and it audits
> exactly the version that will ship. The working tree is **un-audited while you iterate** — the
> auditors are the safety net at the gate, not at edit time.

**In one sentence:** "no logic changes" is **best-effort** — backed by the pre-ship dual AI audit +
compile, not a physical impossibility — so **you should only make UI changes**.

---

## 6. What gets blocked (examples)

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

## 7. Limits & known weak spots

- **Flutter / SwiftUI / Compose mixed files are hardest to tell apart**: UI and logic live in the same
  file with the same syntax, and no static rule perfectly separates "UI vs logic".
- Because there is **no edit-time guard**, the guarantee is **"pre-ship dual review + compile" level**,
  not physical impossibility. It defends against **accidental breakage**, not against someone
  deliberately trying to bypass it.
- **Seeing it needs a device**: with no emulator / physical phone, it can only confirm "it compiles",
  not show you the screen — connect a device and re-run, or ship and let an engineer look.
- Android is the cleanest case (resource files are clearly separated); for Flutter / SwiftUI, changes
  are best confined to theme / token files, or to simple value/color swaps inside a widget file.

---

## 8. Troubleshooting

**"Show me" can't open a device / no device found**
- It tries, in order: use an already-connected/running device (incl. physical) → boot an
  emulator/simulator → fall back to "just confirm it compiles".
- To view on a physical device, connect the phone first (USB / wifi, visible in `flutter devices`),
  then run "show me".
- The fastest path is keeping your simulator/emulator open between runs — an already-running device
  is picked up immediately, with no boot wait (a fresh run also pre-warms one in the background).

**The skill's description looks wrong / the skill isn't recognized**
- Usually the `SKILL.md` frontmatter (the `---`-delimited block at the top) isn't on the **first line**.
- Fix: make sure line 1 of `SKILL.md` is `---`; frontmatter must be at the very start of the file.

---

## 9. Where it ends (important)

`/ui-tweak`'s terminal is a **draft PR**, and it only gets there **when you personally pick "Ship it"**:

- Iterating, or picking "show me" → nothing is shipped; the change just stays in your working tree.
- Picking "Ship it" → logic check → commit → open a **draft PR** (its Demo section carries the demo
  captured during preview, the ticket's design images, and your screenshot if you gave one) + leave a
  link on the ticket, then move the ticket to **In Review** and drop its `ready-to-dev` label (same
  handoff as the dev flow).
- **It never auto-merges and never flips draft → ready.** The status move stops at In Review; the
  human review step is kept on purpose — with no edit-time guard, "no logic changes" is only
  best-effort (pre-ship dual audit + compile), so a human must do the final check on the PR.
