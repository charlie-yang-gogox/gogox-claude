---
name: demo
description: "Opt-in Tier-1 passive-capture stage of the /ui-tweak pipeline — runs ONLY when the designer picked 'Ship it — and record a short demo' on C1 (looks-good), which wrote .dev/ui-tweak/demo-requested. Scheduled by the walker AFTER commit (diff frozen) and BEFORE pr, in the deliver path the designer has already left — so recording adds ZERO designer wait. Captures what is CURRENTLY on the previewed device's screen (the screen the designer just navigated to and approved): one screenshot + a short (~6s) recording, via pure-output tools (xcrun simctl io / adb screencap|screenrecord). HARD RULE: zero input events — this stage never taps, swipes, types, launches, navigates, or grants permissions; it may only READ the screen. That keeps preview's HARD BOUNDARY meaningful: the sole sanctioned capture path is this stage, on a screen a human already approved. Best-effort + fail-silent: ANY failure (device gone, app closed, capture error) consumes demo-requested, leaves demo-files untouched, prints one line, and exits 0 — the PR opens on schedule with the normal Demo fallback chain. Internal stage — designers run /ui-tweak."
---

<!-- RULE: command content is English. -->

# `/ui-tweak:demo`

> **Single responsibility**: passively capture the already-approved screen for the PR's `## Demo`
> section, after the designer has left. **Never drive the app** — capture-only, fail-silent,
> never delays the PR.

## Inputs

`.dev/ui-tweak/demo-requested` (authorization — written by C1 looks-good's
`Ship it — and record a short demo`); the device the preview launched onto (re-detected below).

## Step 0a — misdirect guard (R5/D11)

If `UI_TWEAK_FF` is not set, print **C-MISDIRECT** (see `/ui-tweak:apply` Step 0a) and STOP.

## Step 0 — precondition

```bash
WT=$(git rev-parse --show-toplevel)
[ -f "$WT/.dev/ui-tweak/demo-requested" ] || { echo "demo: not requested — nothing to do."; exit 0; }
```

## Step 1 — find the previewed device (READ-ONLY — never boot or launch anything here)

Re-detect the device preview used: `$FLUTTER_BIN devices --machine` (resolved binary from
`.dev/ui-tweak/flutter-bin` — flutter platform only; the marker may legitimately be absent on
native android/ios platforms), or directly `xcrun simctl list devices booted` (iOS) /
`adb devices` (Android). Take the booted/connected device — it is the one the designer just looked
at. **If no device is found, or the app process is no longer running → FAIL-SILENT (Step 3).**
Do NOT re-boot a device or re-launch the app to "fix" this — the moment is gone; the fallback chain
covers the PR.

## Step 2 — capture (pure output; ZERO input events)

```bash
mkdir -p "$WT/.dev/ui-tweak/demo"
```

- **iOS simulator**: `xcrun simctl io <udid> screenshot "$WT/.dev/ui-tweak/demo/after.png"`, then a
  short recording: `xcrun simctl io <udid> recordVideo --codec h264 "$WT/.dev/ui-tweak/demo/after.mp4"`
  backgrounded for ~6s, then stop it (SIGINT).
- **Android (emulator or USB device)**: `adb -s <id> exec-out screencap -p > .../after.png`, then
  `adb -s <id> shell screenrecord --time-limit 6 /sdcard/ui-tweak-demo.mp4` + `adb pull`.

> ### ⛔ Capture-only — the HARD BOUNDARY still holds here
> This stage may **read** the screen and nothing else. **No** `adb shell input`, no `simctl launch`,
> no taps/swipes/typing, no permission dialogs, no deep links, no "navigate to the right screen
> first". The screen being captured is, by construction, the one the designer navigated to and
> approved at C1 (looks-good). If it is not (app crashed, phone slept), that is a FAIL-SILENT, not a
> reason to drive the app.

On success, register the outputs and consume the request:

```bash
{ echo "$WT/.dev/ui-tweak/demo/after.png"; [ -f "$WT/.dev/ui-tweak/demo/after.mp4" ] && echo "$WT/.dev/ui-tweak/demo/after.mp4"; } >> "$WT/.dev/ui-tweak/demo-files"
rm -f "$WT/.dev/ui-tweak/demo-requested"
```

The `pr` stage uploads + embeds everything in `demo-files` (see `/ui-tweak:ff` "Deliver PR body");
the capture files live under `.dev/` and are therefore never committed (R12 coverage-scoped commit).

## Step 3 — fail-silent (ANY failure)

```bash
rm -f "$WT/.dev/ui-tweak/demo-requested"     # consume so the walker proceeds to pr — never loop
echo "demo: capture skipped (<one-line reason>) — PR continues without it."
exit 0
```

Never write `repair-context`, never bump `repair-count`, never render a card, never delay or block
the PR. A missing demo is a cosmetic gap the Demo fallback chain already covers (ticket visuals →
"No screenshot" line); it is NOT an implementation failure.

## `--auto`

Structurally unreachable (`--auto` shows no cards → C1 can never arm `demo-requested`). If somehow
reached, the same fail-silent contract applies — one stdout line, exit 0.

## Stop

Print: `Demo captured (<files>) — will be embedded in the PR.` — or the fail-silent line. The walker
proceeds to `pr` either way.
