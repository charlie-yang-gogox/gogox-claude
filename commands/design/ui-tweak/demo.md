---
name: demo
description: "Tier-1 capture stage of the /ui-tweak pipeline — runs when .dev/ui-tweak/demo-requested is present (written either by C1 looks-good's 'Ship it — and record a short demo' OR, under --auto / the interactive navigate opt-in, by the orchestrator's auto-decision). Scheduled by the walker AFTER commit (diff frozen) and BEFORE pr, in the deliver path the designer has already left — so it adds ZERO designer wait. Two modes: (1) PASSIVE (default) — captures what is CURRENTLY on the previewed device's screen, the screen the designer just navigated to and approved; (2) NAVIGATE+capture (GGC-14, when .dev/ui-tweak/auto-navigate is present) — fires exactly ONE deep-link URI to a whitelisted ggv:// host derived from the ticket, then captures the resulting screen. Capture = one screenshot + a short (~6s) recording via pure-output tools (xcrun simctl io / adb screencap|screenrecord). HARD RULE: zero input events EXCEPT the single sanctioned deep-link fire — never taps, swipes, types, logs in, or grants permissions; navigation is ONLY the one URI fire (no tap-through). Relies on an already-logged-in running session; if the target isn't whitelisted, or the deep-link lands on login (unauthenticated), it captures whatever is shown + notes it. Best-effort + fail-silent: ANY failure (device gone, app closed, no deep-link route, capture error) consumes demo-requested, prints one line, and exits 0 — the PR opens on schedule with the normal Demo fallback chain. Internal stage — designers run /ui-tweak."
---

<!-- RULE: command content is English. -->

# `/ui-tweak:demo`

> **Single responsibility**: capture the target screen for the PR's `## Demo` section, after the
> designer has left. Two modes — PASSIVE (capture whatever the designer already navigated to) and
> NAVIGATE (GGC-14: fire ONE deep-link to reach the target, then capture). **The only drive action
> ever permitted is that single deep-link fire** — no tap-through, no login, fail-silent, never delays
> the PR.

## Inputs

`.dev/ui-tweak/demo-requested` (authorization — written by C1 looks-good's
`Ship it — and record a short demo`, by the interactive navigate opt-in, or by the `--auto`
auto-decision); `.dev/ui-tweak/auto-navigate` (when present → NAVIGATE mode); the device the preview
launched onto (re-detected below); `.dev/ui-tweak/ticket.json` (cached by `start` — the navigation
target is derived from it).

## Step 0a — misdirect guard (R5/D11)

If `UI_TWEAK_FF` is not set, print **C-MISDIRECT** (see `/ui-tweak:apply` Step 0a) and STOP.

## Step 0 — precondition

```bash
WT=$(git rev-parse --show-toplevel)
[ -f "$WT/.dev/ui-tweak/demo-requested" ] || { echo "demo: not requested — nothing to do."; exit 0; }
AUTO_NAV=0; [ -f "$WT/.dev/ui-tweak/auto-navigate" ] && AUTO_NAV=1   # GGC-14: NAVIGATE mode
```

## Step 1 — find the previewed device (READ-ONLY discovery — never boot or launch the app here)

Re-detect the device preview used: `$FLUTTER_BIN devices --machine` (resolved binary from
`.dev/ui-tweak/flutter-bin` — flutter platform only; the marker may legitimately be absent on
native android/ios platforms), or directly `xcrun simctl list devices booted` (iOS) /
`adb devices` (Android). Take the booted/connected device — in NAVIGATE mode this is the
already-logged-in device `preview` launched the app onto. Record its `id` and platform (iOS udid vs
Android serial). **If no device is found, or the app process is no longer running → FAIL-SILENT
(Step 3).** Do NOT re-boot a device or re-launch the app to "fix" this — the moment is gone; the
fallback chain covers the PR. (Device *discovery* is read-only; the one sanctioned drive action is the
Step 1.5 deep-link fire, and only in NAVIGATE mode.)

## Step 1.5 — navigate via a single deep-link (NAVIGATE mode only — GGC-14)

_Skip entirely when `AUTO_NAV=0` (PASSIVE mode → go straight to Step 2 and capture the current screen)._

Goal: get the app to the screen the change affects, using exactly ONE deep-link URI — never tap-through.
This rides the app's existing deep-link surface (CAF: `app_links` + `go_router`), which only recognizes
a **whitelist** of `ggv://` hosts. Arbitrary routes are NOT URI-addressable.

1. **Derive the target host.** Read `.dev/ui-tweak/ticket.json` (title, description, labels) plus the
   change summary, and map the affected screen to ONE whitelisted host. Known CAF hosts (gogox-client-flutter
   `DeeplinkParser`): `news`, `promotions`, `payment`, `profile`, `service-delivery`, `rate-us`, `login`,
   `voucher`, `order-detail`, `rate-driver`. A repo MAY override/extend this list via
   `deeplink_hosts:` in `<repo>/.gogox-claude.yaml`; if that key exists, use it as the authoritative
   whitelist instead of the built-in CAF list. Pick the single best match.
   - **No confident match** (target screen is not a whitelisted host) → do NOT guess, do NOT tap-through.
     Set `NAV_NOTE="no deep-link route for the target screen — captured the app's current screen instead"`
     and skip to Step 2 (capture whatever is shown).

2. **Fire ONE deep-link** on the running app (platform-conditional; `$DEV` is the id from Step 1):
   ```bash
   URI="ggv://<host>"            # e.g. ggv://order-detail
   case "$PLATFORM_KIND" in
     ios)     xcrun simctl openurl "$DEV" "$URI" ;;
     android) adb -s "$DEV" shell am start -a android.intent.action.VIEW -d "$URI" ;;
   esac
   ```
   Then give the app a brief, counter-bounded settle (NEVER `timeout` — absent on macOS; see preview.md):
   ```bash
   i=0; while [ "$i" -lt 4 ]; do sleep 1; i=$((i+1)); done   # ~4s to let the route render
   ```

3. **Unauthenticated handling.** A `requiresAuth` deep-link fired without a logged-in session does NOT
   error — the app stashes it and shows `/logon/personal` (login). We cannot reliably detect that here,
   and we do NOT attempt to log in (no credentials, no tap-through). Just proceed to Step 2 and capture;
   set `NAV_NOTE="navigated via deep-link; if the capture shows a login screen the device was not logged in"`.
   The precondition is an **already-logged-in running device** (the designer logs in once beforehand);
   under `--auto` an unauthenticated device simply yields a login-screen capture, which the PR note flags.

This single URI fire is the ONLY drive action this stage may perform. Everything in Step 2's HARD RULE
still holds.

## Step 2 — capture (pure output; ZERO input events beyond the Step 1.5 deep-link fire)

```bash
mkdir -p "$WT/.dev/ui-tweak/demo"
```

- **iOS simulator**: `xcrun simctl io <udid> screenshot "$WT/.dev/ui-tweak/demo/after.png"`, then a
  short recording: `xcrun simctl io <udid> recordVideo --codec h264 "$WT/.dev/ui-tweak/demo/after.mp4"`
  backgrounded for ~6s, then stop it (SIGINT).
- **Android (emulator or USB device)**: `adb -s <id> exec-out screencap -p > .../after.png`, then
  `adb -s <id> shell screenrecord --time-limit 6 /sdcard/ui-tweak-demo.mp4` + `adb pull`.

> ### ⛔ Capture-only — the HARD BOUNDARY still holds here (one narrow exception)
> This stage may **read** the screen. The ONLY drive action it may ever perform is the **single
> deep-link URI fire** of Step 1.5, and ONLY in NAVIGATE mode. Beyond that: **No** `adb shell input`,
> no `simctl launch`, no taps/swipes/typing, no permission dialogs, no login, no tap-through, no
> second deep-link, no "navigate by poking the UI". In PASSIVE mode the screen captured is, by
> construction, the one the designer navigated to and approved at C1 (looks-good); in NAVIGATE mode it
> is whatever the one deep-link produced. If the app crashed, the phone slept, or the device is gone,
> that is a FAIL-SILENT (Step 3), not a reason to drive the app.

On success, register the outputs and consume the request. In NAVIGATE mode also record `NAV_NOTE`
(when set) so the `pr` stage can caption the embedded image honestly:

```bash
{ echo "$WT/.dev/ui-tweak/demo/after.png"; [ -f "$WT/.dev/ui-tweak/demo/after.mp4" ] && echo "$WT/.dev/ui-tweak/demo/after.mp4"; } >> "$WT/.dev/ui-tweak/demo-files"
[ -n "$NAV_NOTE" ] && printf '%s\n' "$NAV_NOTE" > "$WT/.dev/ui-tweak/demo-note"
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

**Reachable under `--auto` (GGC-14).** The `--auto` auto-decision writes `demo-requested` +
`auto-navigate` alongside `deliver` + `direct-ship` (see `/ui-tweak:ff` dispatch loop), and `preview`
(direct-ship + auto-navigate) launches the app onto an already-running logged-in device so this stage
has a live app to navigate. So `--auto` runs this stage in **NAVIGATE mode**: derive the host, fire one
deep-link, capture, embed in the PR `## Demo`. Everything stays **fail-silent** — if there was no
running device (preview fell back to build-only), the app isn't up, or no host matched, consume
`demo-requested`, print one line, exit 0; the PR opens with the normal Demo fallback chain. A capture
failure NEVER fails the `--auto` run (the build gate + audit are the load-bearing gates; the demo is
reviewer evidence only).

## Stop

Print: `Demo captured (<files>) — will be embedded in the PR.` — or the fail-silent line. The walker
proceeds to `pr` either way.
