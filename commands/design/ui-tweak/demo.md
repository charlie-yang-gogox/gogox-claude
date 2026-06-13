---
name: demo
description: "Tier-1 capture stage of the /ui-tweak pipeline — runs when .dev/ui-tweak/demo-requested is present (written either by C1 looks-good's 'Ship it — and record a short demo' OR, under --auto / the interactive navigate opt-in, by the orchestrator's auto-decision). Scheduled by the walker AFTER commit (diff frozen) and BEFORE pr, in the deliver path the designer has already left — so it adds ZERO designer wait. Two modes: (1) PASSIVE (default) — captures what is CURRENTLY on the previewed device's screen, the screen the designer just navigated to and approved; (2) NAVIGATE+capture (GGC-14, when .dev/ui-tweak/auto-navigate is present) — two-tier navigation then capture: Tier 1 fires ONE deep-link URI to a whitelisted ggv:// host derived from the ticket; Tier 2 (no deep-link route, e.g. a drawer) is an LLM-planned tap-through — read the codebase to plan the path, then a capped observe→tap loop (MAX_TAPS) of NAVIGATION-only taps (adb shell input tap / idb ui tap). Capture = one screenshot + a short (~6s) recording via pure-output tools (xcrun simctl io / adb screencap|screenrecord). HARD RULE: navigation is for a screenshot only — NEVER taps confirm/submit/pay/destructive controls, never grants permissions, never types/logs in, never edits code, never gates the run. iOS tap-through needs idb (else honest fallback). Relies on an already-logged-in running session; if the target can't be reached, or the device is unauthenticated, it captures whatever is shown + sets a NAV_NOTE. Best-effort + fail-silent: ANY failure (device gone, app closed, no deep-link route, capture error) consumes demo-requested, prints one line, and exits 0 — the PR opens on schedule with the normal Demo fallback chain. Internal stage — designers run /ui-tweak."
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

# P1 short-circuit (GGC-14): the interactive C1 (looks-good) "record a short demo" path captures the
# approved screen AT THE MOMENT OF APPROVAL (no drift) and pre-populates demo-files. If that already
# happened, the best artifact exists — do NOT re-capture or navigate; just consume + exit (pr uploads).
if [ -s "$WT/.dev/ui-tweak/demo-files" ]; then
  rm -f "$WT/.dev/ui-tweak/demo-requested"
  echo "demo: instant capture already present (P1) — nothing to re-capture."
  exit 0
fi
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

## Step 1.4 — login-gated pre-detection (NAVIGATE mode only — GGC-50)

_Skip entirely when `AUTO_NAV=0` (PASSIVE mode captures the already-approved screen — no navigation, no login wall to hit)._

Before attempting ANY navigation (Tier 1 deep-link or Tier 2 tap-through), pre-detect the case the whole
ticket is about: the target screen is **login-gated** and the running app session is **not logged in**.
Reaching it is then impossible (nav-only never logs in — see the Login wall note below), so a nav attempt
is a guaranteed no-op: a deep-link to a `requiresAuth` host bounces to `/logon/personal`, and a tap-through
burns `MAX_TAPS` only to stop at the login wall. Detecting this up-front lets the stage **skip cleanly with
a surfaced reason instead of a silent no-op** — addressing the GGC-50 finding that a whole on-duty session
captured 0 demos with only "session-blocked" to show for it.

1. **Derive the target host** exactly as Tier 1 Step 1 does (from `.dev/ui-tweak/ticket.json` + the change
   summary). Classify it as auth-gated when it is the `login` host itself, or a host known to sit behind the
   login wall on this repo (e.g. `payment`, `profile`, `voucher`, `order-detail`, `service-delivery`,
   `rate-driver` — anything the app guards with `requiresAuth`). A repo MAY declare its auth-gated hosts via
   `auth_gated_hosts:` in `<repo>/.gogox-claude.yaml` (authoritative when present); absent that, treat the
   `login` host plus the booking/order/payment/profile family above as auth-gated.
2. **Probe the running session's logged-in state** (read-only — never log in). Best-effort, cheap signals:
   - Android: `adb -s "$DEV" exec-out screencap -p > /tmp/uitw-pre.png` and check whether the current screen
     is already the login wall (a `/logon` route / login form). If the launch landed on login, the session
     is unauthenticated.
   - iOS: `xcrun simctl io "$DEV" screenshot /tmp/uitw-pre.png` and the same check.
   - If the probe itself fails (device gone, app not running), fall through to Step 3 (fail-silent) — there
     is nothing to capture either way.
3. **Decision**:
   - Target host is auth-gated AND the session is not logged in → **skip up-front**: do NOT navigate. Set the
     reason and consume the request, then exit (this is the explicit, surfaced skip the ticket asks for):
     ```bash
     REASON="demo skipped: target screen is login-gated and the demo device is not logged in. See the Demo device prerequisite below (a logged-in debug \`dev\` build is required)."
     printf '%s\n' "$REASON" > "$WT/.dev/ui-tweak/demo-note"   # pr's relevance gate skips embedding when a note is present
     rm -f "$WT/.dev/ui-tweak/demo-requested"                  # consume — never loop
     echo "demo: $REASON"
     exit 0
     ```
   - Target host is NOT auth-gated, OR the session is already logged in → proceed to Step 1.5 navigation
     normally.

> This pre-detection is fail-soft: when in doubt (host classification ambiguous, probe inconclusive), it
> **falls through to Step 1.5** rather than over-skipping a capturable screen. It only short-circuits on the
> high-confidence "auth-gated host + provably-on-login-wall" case.

## Step 1.5 — navigate to the target screen (NAVIGATE mode only — GGC-14)

_Skip entirely when `AUTO_NAV=0` (PASSIVE mode → go straight to Step 2 and capture the current screen)._

Goal: get the running app to the screen the change affects, then capture it. **Two tiers, tried in
order; both best-effort** — if neither reaches the target, capture whatever is shown + set `NAV_NOTE`.
Neither tier may EVER edit code or change app/account state — this is navigation for a screenshot only,
strictly after the build gate, never gating.

### Tier 1 — deep-link (preferred: deterministic, one action)

1. **Derive the target host** from `.dev/ui-tweak/ticket.json` (title/description/labels) + the change
   summary. Known CAF `ggv://` hosts (gogox-client-flutter `DeeplinkParser`): `news`, `promotions`,
   `payment`, `profile`, `service-delivery`, `rate-us`, `login`, `voucher`, `order-detail`,
   `rate-driver`. A repo MAY override/extend this via `deeplink_hosts:` in `<repo>/.gogox-claude.yaml`
   (authoritative when present). Pick the single best match.
2. **If a host matches**, fire ONE deep-link and settle (counter-bounded, NEVER `timeout` — absent on
   macOS; see preview.md); `$DEV` + `$PLATFORM_KIND` are from Step 1:
   ```bash
   URI="ggv://<host>"            # e.g. ggv://order-detail
   case "$PLATFORM_KIND" in
     ios)     xcrun simctl openurl "$DEV" "$URI" ;;
     android) adb -s "$DEV" shell am start -a android.intent.action.VIEW -d "$URI" ;;
   esac
   i=0; while [ "$i" -lt 4 ]; do sleep 1; i=$((i+1)); done   # ~4s to let the route render
   ```
   → go to Step 2 (capture). Done.
3. **No whitelisted host matches** → Tier 2.

### Tier 2 — LLM-planned tap-through (no deep-link route — GGC-14)

For screens that are not URI-addressable (e.g. a hamburger / side-menu drawer), navigate by driving the
UI, **planned from the codebase**. Best-effort, capped, fail-silent; never edits code, never gates.

1. **Plan the path from the codebase.** Read the app source to determine the route from the current
   screen (usually `/home` after launch) to the target: which affordances to tap and in what order
   (e.g. "tap the top-left menu icon → tap the 'Wallet' row"). Use widget keys / semantics labels /
   route names from the code to identify targets and minimize guessing.
2. **Observe → tap loop** (capped at `MAX_TAPS=6`):
   - **Screenshot** (read-only): Android `adb -s "$DEV" exec-out screencap -p > /tmp/uitw-step.png`;
     iOS `xcrun simctl io "$DEV" screenshot /tmp/uitw-step.png`.
   - **Decide ONE navigation tap** from the screenshot + the codebase plan, then execute it:
     - Android: `adb -s "$DEV" shell input tap <x> <y>`
     - iOS: `idb ui tap --udid "$DEV" <x> <y>` — **only if `command -v idb` succeeds**. `xcrun simctl`
       cannot tap, so if `idb` is absent iOS tap-through is unavailable → **could-not-reach** (reason:
       "iOS tap-through needs idb (not installed)"; see "On failure to reach the target" below).
   - Re-screenshot; judge whether the target screen is reached. Reached → break (→ Step 2 capture).
     Stuck / looping / `MAX_TAPS` hit → **could-not-reach** (reason: "couldn't reach <screen> after
     <n> nav taps").

   > ### ⛔ Tap-through guardrail — NAVIGATION taps ONLY (logged-in-app safety)
   > The app is on a **logged-in** (stag) session, so a wrong tap can fire a **real action**. You may tap
   > ONLY navigation affordances: tab bars, menu / drawer icons, list rows, back / close. You must
   > **NEVER** tap confirm / submit / pay / place-order / delete or any destructive or state-mutating
   > control, never grant a permission dialog, never type into a field. If the only way forward is through
   > such a control, STOP tap-through, set `NAV_NOTE`, and capture where you are. This drives the UI for a
   > screenshot — nothing here may change app or account state, and nothing here may edit code.

### Login wall (Q2 — login is NOT a precondition)

If navigation hits a **login wall** (a `requiresAuth` target lands on `/logon/personal`, or a tapped
feature demands login), that is a **could-not-reach** with reason "login needed to reach <screen>".
**Never log in or tap past the login wall yourself** (no credentials; nav-only). Being logged-in is not
assumed — a login wall is simply one reason navigation couldn't finish.

Step 1.4 (GGC-50) pre-detects the high-confidence variant of this (auth-gated host + provably-on-login-wall)
and skips up-front, so this reactive path now only fires for the residual cases the pre-check let through
(ambiguous host classification, or a login wall encountered mid tap-through). The fix to STOP demos
silently failing on login-gated screens is the **Demo device prerequisite** below — a logged-in debug
`dev` build on the target device.

### On failure to reach the target (mode-aware)

Whenever Tier 1 + Tier 2 cannot confidently reach the target (no route, tap-through stuck, idb absent,
or a login wall), branch by mode — do NOT silently capture the wrong screen:

- **Interactive** (not `--auto` — this is the path `preview` Step 2.5 drives): set the nav-help markers
  and STOP (the orchestrator renders C1 looks-good **Variant B** to inform the designer + keep the app
  live so they finish navigating / log in):
  ```bash
  printf '%s\n' "<reason>" > "$WT/.dev/ui-tweak/nav-help-reason"
  : > "$WT/.dev/ui-tweak/nav-help-needed"
  ```
  Do NOT capture here.
- **`--auto`** (no human to inform): honest fallback — capture the current screen anyway and set
  `NAV_NOTE="<reason> — captured the current screen"`. The `pr` relevance gate (a `demo-note` present)
  then skips embedding the misleading capture. The run never fails.

## Step 2 — capture (pure output; ZERO input events beyond the Step 1.5 deep-link fire)

```bash
mkdir -p "$WT/.dev/ui-tweak/demo"
```

- **iOS simulator**: `xcrun simctl io <udid> screenshot "$WT/.dev/ui-tweak/demo/after.png"`, then a
  short recording: `xcrun simctl io <udid> recordVideo --codec h264 "$WT/.dev/ui-tweak/demo/after.mp4"`
  backgrounded for ~6s, then stop it (SIGINT).
- **Android (emulator or USB device)**: `adb -s <id> exec-out screencap -p > .../after.png`, then
  `adb -s <id> shell screenrecord --time-limit 6 /sdcard/ui-tweak-demo.mp4` + `adb pull`.

> ### ⛔ Capture + bounded navigation — the boundary, restated for NAVIGATE mode
> In **PASSIVE mode** this stage may ONLY read the screen — no input events at all; the screen captured
> is the one the designer approved at C1 (looks-good). In **NAVIGATE mode** (GGC-14) the ONLY drive
> actions permitted are Step 1.5's navigation: one deep-link fire (Tier 1) and/or a capped sequence of
> **navigation-only** taps (Tier 2, `MAX_TAPS`). Under BOTH modes it is still **absolutely forbidden**
> to: edit code; tap confirm / submit / pay / delete or any state-mutating or destructive control;
> grant permission dialogs; type into fields; or log in. Navigation is for reaching a screen to
> screenshot — it never changes app, account, or repo state, and it never affects the build/audit gate
> (which already passed). If the app crashed, the phone slept, or the device is gone, that is a
> FAIL-SILENT (Step 3), not a reason to keep poking.

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

## Demo device prerequisite (login-gated screens — GGC-50)

NAVIGATE-mode capture of a **login-gated** screen (delivery booking flow, order tracking, payment,
profile, …) only succeeds when the target device already holds a **logged-in session in a build the
worktree can run**. Two conditions must both hold; today they conflict unless the operator sets this up
once:

1. **A logged-in debug `dev` build is installed on the demo device.** The demo/preview build is a fresh
   worktree build; it can only co-exist with an already-logged-in app if they share the **same package +
   signing**. That means a debug `com.gogox.clientapp.dev` build, logged in via a **one-time manual OTP
   login by a human** (OTP is not headless-drivable, so the pipeline can never do this itself).
2. **Pin the preview/demo flavor to debug `dev`** so the worktree build does NOT collide with the only
   other logged-in build on the device — the CI-signed STAGING app (`--flavor stag`), whose signature a
   debug worktree build cannot match. Pinning is a one-line repo override (GGC-7):
   ```yaml
   # <repo>/.gogox-claude.yaml
   flavor: dev          # preview/demo build = debug com.gogox.clientapp.dev (matches the logged-in demo build)
   ```
   The platform default is `--flavor stag` (see `commands/dev/profiles/platform/flutter.yaml`); the
   `flavor:` override wins and `/ui-tweak:start` probes + caches it (`.dev/ui-tweak/flavor`).

When prerequisite (1) is not met for a login-gated target, **Step 1.4 skips up-front with a surfaced
reason** rather than spawning a no-op navigation — no silent "session-blocked". A missing demo is never a
ship blocker: the PR opens on schedule via the normal Demo fallback chain (ticket visuals → "No
screenshot" line). See `commands/dev/ggx-on-duty.md` "Demo capture prerequisite".

## Stop

Print: `Demo captured (<files>) — will be embedded in the PR.` — or the fail-silent line. The walker
proceeds to `pr` either way.
