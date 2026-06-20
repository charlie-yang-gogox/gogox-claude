---
name: preview
description: "Phase-1 stage of the /ui-tweak pipeline — build + install + launch the change onto a device, then (GGC-14) navigate to the target screen and capture it (screenshot + short recording) FOR the designer (Step 2.5), so they review the result without driving. This is the SOLE capture point — there is no separate post-commit demo stage. Navigation is bounded to nav-only (deep-link + navigation taps); the agent never edits code, never taps state-mutating controls, and never logs in EXCEPT the sanctioned GGC-65 Step 2.4 staging-QA login gate (opt-in via the repo's demo_auth selector, so login-gated screens can be captured). If it can't reach the screen (no route / unpassable login wall) it FAIL-SILENTs — no capture, the designer is never asked to drive (the C1 card just shows no image). Reached when the designer picks 'I'm done — show me' on card C1. Freezes the audited file set, runs a device cascade (use an already-running/connected device incl. physical FIRST → else boot an emulator/simulator → else honest no-device build-only fallback), then `ui_preview_cmd` (flutter run = build + install + launch; covers Android emulators AND iOS simulators) — all flutter calls use the fvm-aware resolved binary from .dev/ui-tweak/flutter-bin. Quarantines build side-effects, writes .dev/ui-tweak/build-pass (PASS|FAIL) + .dev/ui-tweak/preview-shown. Also runs in DIRECT-SHIP mode (R20, .dev/ui-tweak/direct-ship present): the designer already saw the change on their own device, so it is a build-only compile gate — EXCEPT when auto-navigate is set (GGC-14), where it launches onto an already-running device and Step 2.5 navigates + captures for the PR. No preview-shown, no card in direct-ship; the walker then advances to audit. Build fail → write repair-context + bump repair-count → the orchestrator routes back to /ui-tweak:apply for an agent fix (max 3, then the engineer card). The expensive LLM logic audit is Phase 2 (/ui-tweak:audit), AFTER the designer confirms the look. Internal stage — designers run /ui-tweak. Also exposes a --capture-only sub-mode (GGC-59, Step 0c) used exclusively by /ggx-demo for post-hoc demo capture against an already-shipped PR: runs the Step 2.4 login gate + Step 2.5 navigate+capture slice on an already-running device (path (a) only, no cold-boot; Step 2.4 logs in with a staging QA account when the repo's demo_auth selector is set, GGC-65), writes only demo-files (NONE of the walker markers), and inverts the disposition to fail-LOUD (the demo is the whole deliverable). The 3 device-capture fixes (package-targeted ggv:// deep-link, screenrecord --size ladder, scaled Tier-2 taps) live in the shared Step 2.5 body, so the --auto path benefits too."
---

<!-- RULE: command content is English. Designer-facing CARD text may be Traditional Chinese. -->

# `/ui-tweak:preview`

> **Single responsibility (Phase 1)**: build + install + launch the change onto a device, then (GGC-14
> reorientation) **navigate to the target screen and capture it — screenshot + short recording — FOR
> the designer** (Step 2.5), so they review the *result* instead of driving the device. This is the
> **sole capture point** in the pipeline (there is no separate post-commit demo stage). Driving is
> bounded to **navigation only** (deep-link + nav-only taps) — the agent never edits code and never taps
> state-mutating controls. It does **not** log in either, except the one sanctioned GGC-65 exception: the
> Step 2.4 login gate, which logs in with a staging QA automation account when the repo opts in via a
> `demo_auth` selector (see the Drive policy in Step 2). If it cannot reach the
> screen (no route / login wall it cannot pass) it **FAIL-SILENTs** — captures nothing and the designer is never asked
> to drive (the C1 card simply shows no image). Reached when `.dev/ui-tweak/preview-requested` exists
> (designer picked "I'm done — show me"). It does NOT run the LLM logic audit — that is Phase 2
> (`/ui-tweak:audit`), gated behind the designer confirming the look. Build is folded in here —
> `flutter run` builds + installs + launches in one step.

## Inputs

The working-tree diff relative to `base_ref`; the profile's `ui_preview_cmd` (preferred) and
`ui_build_cmd` (no-device fallback). `{device}` in `ui_preview_cmd` is substituted after the cascade.

## Step 0a — misdirect guard (R5/D11)

If `UI_TWEAK_FF` is not set, print **C-MISDIRECT** (see `/ui-tweak:apply` Step 0a) and STOP.

## Step 0 — precondition + freeze the audited surface (F3)

```bash
WT=$(git rev-parse --show-toplevel)
# GGC-59: detect --capture-only FIRST (before the base_ref gate). A post-hoc demo run via /ggx-demo
# operates on an already-shipped PR's worktree (often a fresh `git worktree add` checkout) which has
# NO `.dev/ui-tweak/base_ref` and needs no audit-files freeze — there is no Phase-2 audit to protect.
# Gating these on capture-only is what lets Step 0c run at all; without it the precondition below would
# hard-fail every /ggx-demo invocation.
CAPTURE_ONLY=0; printf '%s' "$ARGUMENTS" | grep -q -- '--capture-only' && CAPTURE_ONLY=1
if [ "$CAPTURE_ONLY" != "1" ]; then
  [ -f "$WT/.dev/ui-tweak/base_ref" ] || { echo "FAIL: no base_ref — run /ui-tweak:apply first." >&2; exit 1; }
  BASE=$(cat "$WT/.dev/ui-tweak/base_ref")
  # FREEZE the audited file set BEFORE building (F3): the build/run will mutate the tree (regenerated
  # registrants, codegen); those side-effects must never widen what Phase-2 audit later judges.
  git diff "$BASE" --name-only > "$WT/.dev/ui-tweak/audit-files"
fi
```

The `$FLUTTER_BIN` resolution and flavor fallback below STILL run under `--capture-only` (capture needs
`$FLUTTER_BIN` for `flutter devices` / `flutter run`) — only the `base_ref` precondition and the
audit-files freeze are gated off.

Resolve `ui_preview_cmd` / `ui_build_cmd`: prefer a repo override in `<repo>/.gogox-claude.yaml`,
else the platform default.

**`{platform} = flutter` ONLY — resolve the flutter binary (probe-based, fvm-aware).** On
`android` / `ios` SKIP this whole block: their profile `ui_build_cmd` (gradlew / xcodebuild) runs
as-is — no flutter resolution, no flutter tooling, nothing here may fail a native-platform run:

```bash
# /ui-tweak:start writes the worktree-local marker; this inline fallback covers a stale worktree from
# before the marker. It MIRRORS start.md (a): prefer the per-machine shared cache (a relative token),
# else probe by priority (direct SDK binary → fvm wrapper → bare). Cache file: line1=CACHE_FMT,
# line2=token (sdk-rel|<rel> / fvm-abs|<abs fvm> / bare). Tokens are re-expanded against THIS $WT —
# never a stale $WT-absolute path. Never guess from config alone (some machines have only fvm).
if [ -f "$WT/.dev/ui-tweak/flutter-bin" ]; then FLUTTER_BIN=$(cat "$WT/.dev/ui-tweak/flutter-bin"); else
  TRUNK=$(dirname "$(git rev-parse --git-common-dir)"); CACHE_DIR="$HOME/.cache/ui-tweak/$(basename "$TRUNK")"; CACHE_FMT=v1
  probe() { eval "$1 --version" >/dev/null 2>&1; }
  expand_token() { case "$1" in
      "sdk-rel|"*) printf '%s' "$WT/${1#sdk-rel|}";;
      "fvm-abs|"*) printf '%s flutter' "${1#fvm-abs|}";;
      bare)        printf 'flutter';;
    esac; }
  bin_exists() { h=${1% flutter}; case "$h" in /*) [ -x "$h" ];; *) command -v "$h" >/dev/null 2>&1;; esac; }
  FLUTTER_BIN=""
  if [ -f "$CACHE_DIR/flutter-kind" ] && [ "$(sed -n 1p "$CACHE_DIR/flutter-kind")" = "$CACHE_FMT" ]; then
    cand=$(expand_token "$(sed -n 2p "$CACHE_DIR/flutter-kind")"); [ -n "$cand" ] && bin_exists "$cand" && FLUTTER_BIN="$cand"
  fi
  if [ -z "$FLUTTER_BIN" ]; then
    FVM_BIN=$(command -v fvm 2>/dev/null || true); [ -z "$FVM_BIN" ] && [ -x "$HOME/.pub-cache/bin/fvm" ] && FVM_BIN="$HOME/.pub-cache/bin/fvm"
    PINNED=0; { [ -f "$WT/.fvmrc" ] || [ -f "$WT/.fvm/fvm_config.json" ]; } && PINNED=1
    SDK_REL=".fvm/flutter_sdk/bin/flutter"; KIND=""
    if   [ "$PINNED" = 1 ] && [ -x "$WT/$SDK_REL" ] && probe "$WT/$SDK_REL"; then FLUTTER_BIN="$WT/$SDK_REL"; KIND="sdk-rel|$SDK_REL"
    elif [ "$PINNED" = 1 ] && [ -n "$FVM_BIN" ] && probe "$FVM_BIN flutter";  then FLUTTER_BIN="$FVM_BIN flutter"; KIND="fvm-abs|$FVM_BIN"
    elif probe flutter; then FLUTTER_BIN="flutter"; KIND="bare"
    elif [ -n "$FVM_BIN" ] && probe "$FVM_BIN flutter"; then FLUTTER_BIN="$FVM_BIN flutter"; KIND="fvm-abs|$FVM_BIN"
    fi
    [ -z "$FLUTTER_BIN" ] && { echo "FAIL: no working flutter found (tried fvm + bare flutter)." >&2; exit 1; }
    mkdir -p "$CACHE_DIR"; [ -n "$KIND" ] && printf '%s\n%s\n' "$CACHE_FMT" "$KIND" > "$CACHE_DIR/flutter-kind"
  fi
  mkdir -p "$WT/.dev/ui-tweak"; printf '%s\n' "$FLUTTER_BIN" > "$WT/.dev/ui-tweak/flutter-bin"
fi
```

Then rewrite the **leading `flutter` token** of the resolved `ui_preview_cmd` / `ui_build_cmd`
(including repo overrides) with `$FLUTTER_BIN`, and use `$FLUTTER_BIN` for every `flutter devices` /
`flutter emulators` call below. Do NOT re-discover fvm by trial-and-error — the marker is
authoritative.

**`{platform} = flutter` ONLY — graceful flavor fallback (GGC-7).** `/ui-tweak:start` (c) probed
whether the effective flavor actually exists in this repo (Android `productFlavors` / iOS scheme) and
wrote `.dev/ui-tweak/flavor` (line1 = flavor name, line2 = `detected|missing`). If the flavor was
**not** detected — or the repo declares no flavor at all — strip the trailing `--flavor <name>` from
the resolved `ui_preview_cmd` / `ui_build_cmd` and run a **no-flavor build** with a legible WARN,
rather than letting gradle/Xcode self-destruct on a flavor it does not have. When the marker is
`detected`, leave the command untouched. The flavor token lives at the END of the command on purpose
(see `flutter.yaml`), so the strip is a mechanical tail-edit. Skip this block on `android` / `ios`
(no flutter `--flavor` semantics there):

```bash
# Default to keeping --flavor if no marker (e.g. a stale worktree from before GGC-7): the pre-GGC-7
# behavior was "always carry --flavor stag", so an absent marker must NOT silently strip it.
FLAVOR_DETECTED=detected; FLAVOR_NAME=""
if [ -f "$WT/.dev/ui-tweak/flavor" ]; then
  FLAVOR_NAME=$(sed -n 1p "$WT/.dev/ui-tweak/flavor")
  FLAVOR_DETECTED=$(sed -n 2p "$WT/.dev/ui-tweak/flavor")
fi
if [ "$FLAVOR_DETECTED" = "missing" ]; then
  # Strip a trailing `--flavor <token>` from each resolved command (tail position; mechanical).
  ui_preview_cmd=$(printf '%s' "$ui_preview_cmd" | sed -E 's/[[:space:]]*--flavor[[:space:]]+[A-Za-z0-9_]+[[:space:]]*$//')
  ui_build_cmd=$(printf '%s'   "$ui_build_cmd"   | sed -E 's/[[:space:]]*--flavor[[:space:]]+[A-Za-z0-9_]+[[:space:]]*$//')
  echo "WARN: flavor '${FLAVOR_NAME:-<none>}' not present in this repo — building WITHOUT --flavor (graceful fallback, GGC-7). If the app needs a flavor, declare a present one via 'flavor:' in <repo>/.gogox-claude.yaml." >&2
fi
```

## Step 0b — direct-ship mode (R20) + navigate mode (GGC-14) + capture-only (GGC-59)

```bash
DIRECT_SHIP=0; [ -f "$WT/.dev/ui-tweak/direct-ship" ] && DIRECT_SHIP=1
AUTO_NAV=0;    [ -f "$WT/.dev/ui-tweak/auto-navigate" ] && AUTO_NAV=1   # GGC-14: demo will navigate+capture
# CAPTURE_ONLY was already parsed at the top of Step 0 (it gates the base_ref precondition there).
```

When `CAPTURE_ONLY=1` the entire normal flow below is **replaced by Step 0c** — this is the post-hoc
demo slice invoked by `/ggx-demo` (the operator skill), never the walker. It captures and nothing else.

When `DIRECT_SHIP=1` the designer picked **"It already looks right — ship it"** on card C1 (show-me),
or `--auto` auto-took the direct-ship branch. They have already looked at it (or there is no human to
look), so by default this stage is a **build-only compile gate** — NOT a device preview:

- **Skip Step 1 entirely** (no device cascade — do not boot/launch anything). Go straight to the
  **no-device build-only path**: run `ui_build_cmd`.
- **Do NOT write `preview-shown`** in Step 3 (there is no "looks good?" stop — the designer already
  decided to ship). On build PASS the orchestrator's walker (`deliver=1` + `direct-ship` + build PASS)
  proceeds directly to `audit` with no card.
- The build-fail path (Step 4) is **unchanged** — a compile failure still routes to the agent repair
  loop (max 3, then Ce). This gate exists precisely because the designer's hand-build may predate the
  latest tweak.

**Exception — `DIRECT_SHIP=1` AND `AUTO_NAV=1` (GGC-14): launch onto an already-running device so
preview itself can navigate + capture (Step 2.5).** A pure build-only gate leaves no running app to
deep-link into, so when navigation is requested we must actually install + launch — but only onto a
device that is **already running** (typically the designer's pre-warmed, already-logged-in device — though Step 2.4 can log in if it is not). Concretely:

- Run a **restricted cascade — path (a) ONLY** (Step 1 (a): an already-running emulator/simulator or
  physical handset). **Do NOT cold-boot (skip path (b))**: booting an emulator unattended is heavy and
  adds latency (and Step 2.4 handles login when needed, so a freshly-booted device buys nothing). If (a) yields a device → go to Step 2's
  **device path** (`ui_preview_cmd`) to build+install+launch and leave the app up; the build gate still
  keys on exit code exactly as the normal path, then **Step 2.5 navigates + captures** before the
  walker advances.
- If (a) yields **no running device** → fall back to the **build-only path** (`ui_build_cmd`) exactly as
  above. Step 2.5 is skipped (no live app) and FAIL-SILENT (the PR uses the Demo fallback chain).
- **Still do NOT write `preview-shown`** (direct-ship has no "looks good?" card) and the walker still
  advances to `audit` after the capture. The launch here exists solely to give Step 2.4/2.5 a live
  app to navigate (Step 2.4 logs it in if needed).

The rest of this file (Steps 1–4) is the normal **device-preview** path used when `DIRECT_SHIP=0`.

## Step 0c — `--capture-only` sub-mode (GGC-59) — the post-hoc demo slice

_Runs ONLY when `CAPTURE_ONLY=1` (invoked as `/ui-tweak:preview --capture-only`, exclusively by
`/ggx-demo`). It RUNS the capture body (Step 2.5) and bypasses ALL walker bookkeeping. Steps 1–4 below
are NOT run in this mode — this section is the whole flow._

This sub-mode exists so the **single source of truth for "how to reach a screen and record it"** stays
in Step 2.5, while the surrounding gate/marker logic is switched off — a post-hoc demo runs against an
already-shipped, already-reviewed PR, so there is no build gate to protect and no walker to feed.

- **Device acquisition — Step 1 path (a) ONLY.** Use an **already-running** device (`$FLUTTER_BIN
  devices --machine` lists a booted emulator/simulator or a connected handset). The device need NOT be
  pre-logged-in — the Step 2.4 login gate (GGC-65) logs in with a staging QA account when a `demo_auth`
  selector is configured. **Never cold-boot (skip path (b))** — booting an emulator unattended is heavy
  and adds latency; reuse a running one. **No device → FAIL-LOUD** (see disposition below): do not fall
  through to a build-only path (there is nothing to compile-gate here).
- **Build + launch onto the device.** Run `ui_preview_cmd` (`flutter run` = build + install + launch;
  the leading token is the resolved `$FLUTTER_BIN` from Step 0) so the PR's exact diff is live on the
  device. (A fresh `/ggx-demo` checkout has no prior build, so this compiles from source; if the app is
  already foreground from a prior install on the same device, reuse it.) This is the same launch as
  Step 2's device path, minus the build-gate semantics — a launch/compile failure here is a capture
  failure (fail-LOUD), not a `repair-context`.
- **Login gate then capture — run Step 2.4 + Step 2.5 verbatim.** First the Step 2.4 login gate (GGC-65:
  no-op unless `demo_auth` is configured; logs in with a staging QA account when the app is logged out),
  then Step 2.5 (Tier-1 deep-link → Tier-2 nav-only tap-through → screenshot + short recording → append
  to `.dev/ui-tweak/demo-files`). The 3 device fixes baked into Step 2.5 (package-targeted deep-link,
  `screenrecord --size` ladder, scaled taps) apply here too.
- **WRITE NOTHING the walker reads.** Do **NOT** write `build-pass`, `preview-shown`, the `audit-files`
  freeze, `repair-context`, or `repair-count`. Do **NOT** run Step 0's `audit-files` freeze or Step 3's
  quarantine (this slice is read-only on the source tree apart from the launch). The ONLY output is
  `.dev/ui-tweak/demo-files`. This is the fix for ai-expert red-flags #1/#2: a post-hoc run must leave
  the reused worktree exactly as it found it, so a later bare `/ui-tweak` resume in `../<ID>` is never
  mis-routed to `audit`/`commit` by a leftover marker.

> ### Disposition — FAIL-LOUD, the inverse of the forward pipeline (GGC-59 / R13)
> In the forward pipeline Step 2.5 is **fail-silent** (the capture is an incidental side-effect of a
> preview). Under `--capture-only` the capture **IS the deliverable**, so every failure is **LOUD**:
> non-zero exit + ONE deterministic stderr line, e.g.
> `GGX-DEMO CAPTURE-FAIL: <no device | could not reach <screen> | login wall | screenrecord ladder exhausted> (ticket <id>).`
> No device, no whitelisted route AND tap-through stuck, an unpassable login wall (auto-login failed or not configured), or a `screenrecord`
> size-ladder that never produced a playable file → exit non-zero. Reusing Step 2.5's *body* is correct;
> reusing its fail-silent *disposition* would silently reproduce the very pain `/ggx-demo` exists to fix.
> `/ggx-demo` surfaces this exit; `/ggx-demo --batch` catches it fail-soft and counts it.

STOP after the capture (success or loud failure). The walker is never involved in this sub-mode.

## Step 1 — device cascade (a → b → c, R18) — skipped when `DIRECT_SHIP=1`

> **Platform gate**: the device cascade below is the **flutter** path (`flutter run` covers Android
> emulators + iOS simulators). When the profile defines **no `ui_preview_cmd`** (the `android` /
> `ios` build-only profiles), skip the cascade entirely and go straight to **(c) build-only** —
> exactly the pre-existing behavior for native platforms; never invoke flutter tooling there.

Acquire a target device in this order; stop at the first that yields one:

- **(a) use an already-running device — including a physical phone.** If `$FLUTTER_BIN devices
  --machine` already lists a usable device (a booted emulator/simulator OR a physical handset over
  USB/wifi), use it **immediately — no boot, no poll** (this is the fastest path and the common case
  once `/ui-tweak:start`'s pre-warm has done its job; it is also required because the designer may be
  looking at a real device, not an emulator). On macOS, if `xcrun simctl list devices booted` shows a
  `(Booted)` simulator that `flutter devices` doesn't list yet (the pre-warm is still finishing), give
  it a SHORT grace poll (~10s) before falling through to (b).
- **(b) boot an emulator / simulator (cold-boot fallback).** `$FLUTTER_BIN emulators` to list;
  `$FLUTTER_BIN emulators --launch <id>` to boot one (Android AVD or iOS simulator). Poll
  `$FLUTTER_BIN devices --machine` until it appears (bounded wait, e.g. ~60s).
- **(c) no device available → honest fallback.** Do NOT fail. Run the **build-only** `ui_build_cmd`
  (so we still confirm it compiles), set a `no_device` flag for the orchestrator, and let card C1 be
  honest ("I couldn't find a phone/emulator to show it on; I did confirm it builds — connect a device
  to see it, or ship anyway").

Pick ONE device id; substitute it into `ui_preview_cmd`'s `{device}`.

> ### ⛔ macOS has no `timeout` — do NOT use it for the (a)/(b) waits (GGC-2 / F1)
> macOS ships **no** `timeout` command (it is GNU coreutils, Linux-only). A device wait written as
> `timeout <N> $FLUTTER_BIN devices --machine` therefore errors out on a designer's Mac, the early
> polls come back empty, and the cascade **wrongly concludes "no device"** — falling through to the
> build-only path (c) even when a simulator is already booted. The (a) grace poll and the (b)
> bounded wait below MUST be expressed as a **counter-bounded poll loop** (a `while` with a counter and
> `sleep`), which depends on nothing beyond POSIX builtins. Do not reach for `timeout`. If you want a
> hard wall-clock ceiling you may use `gtimeout` **only when `command -v gtimeout` confirms coreutils
> is installed** — never assume it. The bound is the number of poll iterations, not an external timer.

**Concrete wait mechanism for (a) and (b) — copy this loop; never `timeout`.** `poll_for_device` polls
`$FLUTTER_BIN devices --machine` once per second up to a bounded iteration count and prints the first
device id it finds (empty if the bound elapses with none). The grace poll (a) and the cold-boot wait
(b) differ only in `MAX` (`10` vs `60`):

```bash
# Counter-bounded device poll — no `timeout` (absent on macOS). MAX = max seconds to wait.
# Echoes the first usable device id, or nothing if the bound elapses.
poll_for_device() {
  MAX=$1; i=0
  while [ "$i" -lt "$MAX" ]; do
    DEV=$($FLUTTER_BIN devices --machine 2>/dev/null \
      | jq -r '[.[] | select(.isSupported != false) | .id][0] // empty' 2>/dev/null)
    [ -n "$DEV" ] && { printf '%s\n' "$DEV"; return 0; }
    i=$((i + 1)); sleep 1
  done
  return 1
}

# (a) grace poll (~10s) — only after `xcrun simctl list devices booted` shows a (Booted) sim
#     that `flutter devices` has not surfaced yet:
DEVICE=$(poll_for_device 10)

# (b) cold-boot bounded wait (~60s) — after `$FLUTTER_BIN emulators --launch <id>`:
[ -z "$DEVICE" ] && DEVICE=$(poll_for_device 60)
```

If `$DEVICE` is still empty after (b), fall through to **(c)**. The bound is the loop counter (`MAX`
iterations of `sleep 1`), so it is portable to stock macOS with no external dependency.

## Step 2 — build INTO the device, then STOP (this is also the build gate)

- **Device path**: run `ui_preview_cmd` (e.g. `fvm flutter run -d <id> --debug [--flavor …]` — the
  leading token is the resolved `$FLUTTER_BIN` from Step 0). This builds,
  installs, and launches the app on the device. Run it so the app stays up (background the
  long-running `flutter run` session; do not block the pipeline on its attached console). The moment
  the app is installed + launched (process is up on the device) the build gate has **passed** — go to
  Step 3. A **build/compile failure here is the build-fail path** (Step 4).
- **No-device path (c)**: run `ui_build_cmd` (build-only). Compile failure → Step 4.

> ### Build gate = exit code (never a screenshot)
> The moment the app is installed + launched, the **build gate has passed**. Key pass/fail on the
> **exit code / a successful install+launch**, NEVER on log text — some flutter flavored builds print a
> false `Gradle build failed to produce an .apk file` tail yet exit 0 (confirm via the installed app,
> not by reading its UI). If the app crashes on launch or won't start, treat it like a build failure →
> Step 4. Navigation + capture (Step 2.5) happens strictly AFTER this gate and can **never** flip it.

> ### ⛔ Drive policy — navigation-only, never state-mutating (GGC-14)
> Step 2.5 lets the agent navigate the running app to the target screen so it can screenshot it FOR the
> designer (the reorientation: don't make the designer drive). What it may do is bounded:
> - **Allowed**: ONE deep-link fire (`am start` / `simctl openurl`), and a capped sequence of
>   **navigation-only** taps (tabs, menu/drawer icons, list rows, back/close) via `adb shell input tap`
>   / `idb ui tap`; one screenshot + short recording.
> - **FORBIDDEN, always**: editing code; tapping confirm / submit / pay / place-order / delete or any
>   state-mutating / destructive control; granting permission dialogs; typing into fields **except the
>   sanctioned login gate below**.
> - **Logging in — forbidden by default, with ONE sanctioned exception (GGC-65): the Step 2.4 login
>   gate.** When the repo profile declares a `demo_auth` selector AND the app is not already logged in,
>   Step 2.4 may type a **dedicated staging QA _automation_ account**'s credentials into the login
>   screen and submit — nothing else. This establishes a throwaway QA session so login-gated target
>   screens can be captured; it never uses a real user or a production account, and is the only
>   sanctioned text entry / submit. With **no** `demo_auth` selector, login stays forbidden and a login
>   wall is a fail-silent no-capture (the pre-GGC-65 behavior).
> Apart from that one gate, navigation is for a screenshot only — it never changes app, account, or repo
> state, and never gates.

## Step 2.4 — login gate (GGC-65) — log in iff needed, so login-gated screens can be captured

_Runs after the build gate (Step 2 device path) and BEFORE Step 2.5, in every context that reaches a
live app (interactive device path, direct-ship navigate, `--capture-only`). It is a **no-op unless the
repo profile declares a `demo_auth` selector** — repos that don't opt in keep the pre-GGC-65 behavior (a
login wall → fail-silent / fail-LOUD, no login attempt)._

Most `design bug` target screens (booking flow, order tracking, profile, wallet) sit behind a login
wall, so the pre-GGC-65 pipeline — which never logged in — captured nothing for them. This gate logs in
with a dedicated staging QA **automation** account, **only when the app is not already logged in**, so
Step 2.5's navigate+capture can actually reach the screen. Credentials are NEVER stored in the repo —
they are fetched from Notion at runtime; the selector only names WHICH account.

### `demo_auth` selector (repo `<repo>/.gogox-claude.yaml`)

```yaml
demo_auth:
  notion_page: "443eb970733e452690cfa0a299eab6f2"  # the "Testing accounts ( Staging and Production )" page id (default if omitted)
  app: ca                   # ca = customer app | da = driver app — which login surface
  region: hk                # hk | sg | vn | tw | kr | in (matches the Notion account groupings)
  account_label: automation # prefer the account tagged "for automation usage" (never clobbers a manual tester)
  login_probe_host: profile # a logged-in-only ggv:// host used to detect login state (e.g. profile / order-detail)
```

Absent `demo_auth` → skip this entire step (return to Step 2.5; a login wall there stays fail-silent /
fail-LOUD exactly as before). The demo build is the **staging flavor** (`ui_preview_cmd` carries
`--flavor stag`), so the **Staging** accounts on the Notion page are the right ones.

### 2.4.1 — probe login state (cheap, deterministic)

Fire the `login_probe_host` deep-link (the same one-shot `ggv://` mechanism as Step 2.5 Tier-1) and
settle ~3s, then screenshot (read-only). Decide:
- Lands on the logged-in-only screen (e.g. profile / order-detail content) → **already logged in → SKIP
  login, go straight to Step 2.5** (an interactive designer's own session is never touched).
- Bounces to a login / sign-in screen → **not logged in → 2.4.2**.
- Inconclusive → prefer attempting login (2.4.2): a redundant login on an already-logged-in app is
  harmless, but a skipped login on a logged-out app yields an empty demo.

### 2.4.2 — fetch the account + drive the login (bounded; the sanctioned creds entry)

1. **Fetch the account from Notion** via the Notion MCP `fetch` on `demo_auth.notion_page`. Select by
   `app` + `region`, preferring the entry tagged *for automation usage* (`account_label`). E.g.
   `app: ca` → CA B2C `qa5@gogotech.hk / 12345678`; `app: da, region: hk` → Van `99999995 / Aa123456`
   or DriverCourier `56666665 / Aa123456`. **Never hardcode — always read it live.** If the Notion
   fetch fails → login failure (2.4.3).
2. **Drive the login screen, planned from the codebase** (same discipline as Step 2.5 Tier-2: locate the
   username/password fields + submit by widget key / semantics label; taps use the GGC-59 device-display
   coordinate scaling). Enter the fetched username + password, submit, and wait (counter-bounded poll,
   **never `timeout`**) for the home screen or a logged-in-only deep-link to succeed. This typing +
   submit is the SOLE sanctioned text entry (Drive-policy exception). **Do NOT handle OTP/2FA** — if the
   flow demands an SMS/OTP code, treat it as a login failure (2.4.3).
3. Confirmed logged in → proceed to Step 2.5 (navigate + capture).

### 2.4.3 — login failure disposition

If login cannot complete (Notion fetch failed / creds rejected or expired / login UI not found / an
OTP/2FA wall), do NOT capture a misleading screen:
- **Forward / interactive / `--auto`**: fail-silent, exactly like an unreached target (Step 2.5's
  fail-silent box) — no capture, no drive prompt, never gates the build.
- **`--capture-only` (Step 0c)**: fail-LOUD — non-zero exit + the deterministic line with the cause:
  `GGX-DEMO CAPTURE-FAIL: login wall — auto-login failed (<notion-fetch-failed|creds-rejected|login-ui-not-found|otp-required>) for <app>/<region> (ticket <id>). Check the staging account on the Notion page.`
  The `login wall` token lets `/ggx-demo --batch` short-circuit the rest of a batch (one shared device =
  one shared login state).

## Step 2.5 — navigate to the target + capture (the SOLE capture point — GGC-14)

_Runs whenever Step 2 took the **device path** (an app is live on a device): the interactive device
path (`DIRECT_SHIP=0`), AND the direct-ship navigate path (`DIRECT_SHIP=1` AND `AUTO_NAV=1`, Step 0b
launched onto an already-running device). **Skip only on the no-device build-only path (c)** and on a
pure direct-ship build-only gate (`AUTO_NAV=0`) — there is no live screen to navigate. There is no
separate post-commit capture stage; preview is the single place capture ever happens._

The agent navigates the running app to the target screen and captures it, so the designer reviews the
**result** without driving (and so `--auto` PRs carry a real artifact). **Two tiers, tried in order,
both best-effort.** Neither tier may EVER edit code or change app/account state — this is navigation
for a screenshot only, strictly after the build gate (Step 2), and it can never flip the gate.

### Tier 1 — deep-link (preferred: deterministic, one action)

1. **Derive the target host** from `.dev/ui-tweak/ticket.json` (title/description/labels) + the change
   summary. Known CAF `ggv://` hosts (gogox-client-flutter `DeeplinkParser`): `news`, `promotions`,
   `payment`, `profile`, `service-delivery`, `rate-us`, `login`, `voucher`, `order-detail`,
   `rate-driver`. A repo MAY override/extend this via `deeplink_hosts:` in `<repo>/.gogox-claude.yaml`
   (authoritative when present). Pick the single best match.
2. **If a host matches**, fire ONE deep-link and settle (counter-bounded, NEVER `timeout`). `$DEVICE`
   is from Step 1; derive its platform (an iOS simulator lists a UUID udid + `platform":"ios` in
   `$FLUTTER_BIN devices --machine`; otherwise treat as Android):
   ```bash
   PLATFORM_KIND=$($FLUTTER_BIN devices --machine 2>/dev/null \
     | jq -r --arg d "$DEVICE" '.[] | select(.id==$d) | (.targetPlatform // "")' 2>/dev/null \
     | grep -qi ios && echo ios || echo android)
   URI="ggv://<host>"            # e.g. ggv://order-detail
   case "$PLATFORM_KIND" in
     ios)     xcrun simctl openurl "$DEVICE" "$URI" ;;
     android)
       # GGC-59: a bare `am start -d ggv://…` with NO package triggers the Android app-chooser when
       # more than one gogovan app (e.g. staging + prod) is installed — the deep-link then stalls on a
       # disambiguation dialog and the route never renders. Target the launched app's package explicitly
       # via `-p <PKG>` (Intent.setPackage). Derive PKG from the app we just launched (the resumed
       # activity's package — deterministic + flavor-correct), falling back to the lone installed
       # gogovan/gogox package, then to the resolved applicationId (android/app/build.gradle[.kts]
       # `applicationId` + the active flavor's `applicationIdSuffix`, e.g. hk.gogovan.GoGoVanClient2.staging):
       PKG=$(adb -s "$DEVICE" shell dumpsys activity activities 2>/dev/null \
         | grep -m1 -oE '[a-zA-Z][a-zA-Z0-9_.]+/[a-zA-Z0-9_.]+' | cut -d/ -f1)
       [ -z "$PKG" ] && PKG=$(adb -s "$DEVICE" shell pm list packages 2>/dev/null \
         | sed 's/^package://' | grep -iE 'gogovan|gogox' | head -1)
       adb -s "$DEVICE" shell am start -a android.intent.action.VIEW -d "$URI" ${PKG:+-p "$PKG"} ;;
   esac
   i=0; while [ "$i" -lt 4 ]; do sleep 1; i=$((i+1)); done   # ~4s to let the route render
   ```
   → go to capture below.
3. **No whitelisted host matches** → Tier 2.

### Tier 2 — codebase-planned, navigation-only tap-through (no deep-link route)

For screens that are not URI-addressable (e.g. a side-menu drawer), navigate by driving the UI,
**planned from the codebase**. Capped, per the Drive policy above (navigation affordances only — never
confirm/submit/pay/delete, never grant permissions, never type, never log in).

1. **Plan the path from the codebase** (widget keys / semantics labels / route names) from the current
   screen (usually `/home`) to the target.
2. **Observe → tap loop** (capped at `MAX_TAPS=6`): screenshot (read-only) → decide ONE navigation tap
   → execute it. **GGC-59: tap coordinates must be in DEVICE-DISPLAY space, not screenshot-pixel space.**
   `screencap` returns the full native framebuffer (e.g. 1280×2856) while `input tap` / `idb ui tap`
   expect display coordinates (`wm size`, e.g. 720×1600) — passing raw screenshot pixels lands the tap
   in the wrong place. Read `wm size` once (`adb -s "$DEVICE" shell wm size` → `wm_w`×`wm_h`) and the
   PNG header dims (`shot_w`×`shot_h`), then scale before issuing the tap:
   `x = sx * wm_w / shot_w`, `y = sy * wm_h / shot_h`. Execute the scaled tap
   (`adb -s "$DEVICE" shell input tap <x> <y>` / `idb ui tap --udid "$DEVICE" <x> <y>` — iOS tap-through
   needs `idb`; `xcrun simctl` cannot tap) → re-screenshot; reached → capture; stuck /
   looping / `MAX_TAPS` / `idb` absent → **could-not-reach** (→ fail-silent below, or fail-LOUD under
   `--capture-only`, Step 0c).

### Capture (pure output)

On reaching the target, capture a screenshot + a short (~6s) recording into `.dev/ui-tweak/demo`:
- **iOS**: `xcrun simctl io "$DEVICE" screenshot .../after.png`; recording via `xcrun simctl io
  "$DEVICE" recordVideo --codec h264 .../after.mp4` backgrounded ~6s then SIGINT.
- **Android**: `adb -s "$DEVICE" exec-out screencap -p > .../after.png`; then record with an explicit
  `--size`. **GGC-59: `screenrecord` with NO size flag throws codec error -22 on large native
  resolutions** (e.g. 1280×2856 on a tall device) — the recording silently produces a 0-byte/unplayable
  file. Try a size ladder, stopping at the first rung that yields a non-empty, playable file:
  `--size 720x1280` → `--size 540x1140` → no `--size` (device-native, last resort):
  `adb -s "$DEVICE" shell screenrecord --size 720x1280 --time-limit 6 /sdcard/uitw.mp4` then
  `adb -s "$DEVICE" pull /sdcard/uitw.mp4 .../after.mp4`. If EVERY rung fails to produce a playable
  file, that is a capture failure — **fail-silent** in the forward pipeline, **fail-LOUD** under
  `--capture-only` (Step 0c: `screenrecord ladder exhausted`).

Append the output paths to `.dev/ui-tweak/demo-files`. The screenshot is the C1 review surface; both
screenshot + recording are embedded by `pr`.

### Pixel-verify subtle colour/shade changes (stale-build guard — GGC-62)

A subtle colour/shade edit (a few-% delta, e.g. `#FFFFFF` → `#F7F8F8`) is invisible to the eye, so a
**stale build** — the first `flutter run` occasionally renders the OLD UI even though the edit is on
disk + committed — sails through the device preview, the "looks good?" gate, AND the dual-judge audit
(CAF-609). Do NOT trust such a build by eye; verify the actual pixel.

**Trigger (cheap):** only when the target checklist names a colour —
`grep -qiE '#[0-9A-Fa-f]{6}|target=.*(colou?r|bg|background|shade|fill)' "$WT/.dev/ui-tweak/figma-context.md"`.
Otherwise SKIP (most tweaks are not subtle-colour and need no pixel check).

**Check:**
1. Sample the rendered pixel from the just-captured `after.png` at a point INSIDE the changed region —
   the agent picks the coordinate from the screenshot + the target screen (a flat area of the edited
   background, away from text/icons). First available sampler:
   - `python3 -c "from PIL import Image;print('#%02X%02X%02X'%Image.open('after.png').convert('RGB').getpixel((X,Y)))"`
   - else ImageMagick: `magick after.png -format '%[hex:p{X,Y}]' info:` (or `convert …`).
   - Neither installed → skip with a one-line note (best-effort, like the `screenrecord` ladder).
2. Compare to the target hex with a small tolerance (per-channel |Δ| ≤ 4, ~1.5%). Within tolerance →
   PASS, continue.
3. **Stale (the sample equals the OLD value, or is outside tolerance of the target):** the build is
   stale — kill the running app and re-launch a FRESH `ui_preview_cmd` (`flutter run` = reinstall +
   relaunch), settle (counter-bounded poll, **never `timeout`**), re-capture, and re-sample ONCE.
   - Now within tolerance → PASS (use the fresh capture).
   - Still stale after the re-launch → do NOT record a misleading preview: **fail-silent** in the
     forward pipeline (no capture; C1 shows the honest no-image note) / **fail-LOUD** under
     `--capture-only` (Step 0c: `GGX-DEMO CAPTURE-FAIL: pixel mismatch — built UI shows <sampled> not target <hex> (ticket <id>)`).

This NEVER relaxes the build gate (Step 2 — exit code only); it guards only what gets shown/shipped as
the preview, exactly when the eye cannot.

### On failure to reach the target → FAIL-SILENT (no designer driving)

> **`--capture-only` carve-out (GGC-59).** Everything in this subsection describes the FORWARD
> pipeline's fail-silent disposition. Under `--capture-only` (Step 0c) the disposition is INVERTED to
> **fail-LOUD**: an unreached target / login wall / `screenrecord` ladder exhausted is a non-zero exit
> with a deterministic stderr line, NOT a silent no-capture. The body below (what counts as "couldn't
> reach") is shared; only the disposition differs.

If Tier 1 + Tier 2 cannot confidently reach the target (no route, tap-through stuck, `idb` absent, or an
**unpassable login wall** — login is performed only by the sanctioned Step 2.4 gate when `demo_auth` is
configured, else not at all), do **NOT** capture a misleading wrong
screen and do **NOT** ask the designer to drive: just capture nothing, leave `demo-files` empty, and
continue. The orchestrator's C1 (looks-good) card then shows no image (honest "couldn't auto-reach the
screen" wording) and the PR uses the Demo fallback chain. Any navigation/capture error is likewise
swallowed here — it NEVER fails the build gate or the run.

**iOS-only actionable hint when the cause is missing `idb` (GGC-6).** When (and only when) the device is
an iOS simulator AND Tier-2 could not run because `idb` is absent, additionally emit ONE non-blocking,
informational stderr line so the designer learns how to enable iOS navigation next time — capture still
fail-silents exactly as above (no stall, no drive prompt, no gate, no retry):

```
note: couldn't auto-navigate to the target screen on iOS — Tier-2 tap-through needs `idb` (not found),
so nothing was captured. To enable iOS auto-navigation next time: `brew tap facebook/fb && brew install
idb-companion` then `pipx install fb-idb`. (Tier-1 ggv:// deep-link works without idb; Android is unaffected.)
```

This hint is purely informational. Do NOT print it on Android (taps via `adb`), nor when the fail-silent
cause is a login wall / no-route / tap-through-stuck rather than a missing `idb`.

## Step 3 — quarantine build side-effects (F3) + record success

```bash
# restore anything the build/run touched outside the frozen audit set
git diff "$BASE" --name-only | grep -vxF -f "$WT/.dev/ui-tweak/audit-files" | xargs -r git checkout -- 2>/dev/null || true
printf 'Status: PASS\n' > "$WT/.dev/ui-tweak/build-pass"
# preview-shown signals the orchestrator to render card C1's "looks good?" variant. SKIP it in
# direct-ship mode — the designer already decided to ship, so the walker continues straight to audit.
[ "$DIRECT_SHIP" = "1" ] || : > "$WT/.dev/ui-tweak/preview-shown"
rm -f "$WT/.dev/ui-tweak/repair-count"          # reset the repair budget on a clean build
```

STOP. **Interactive path (`DIRECT_SHIP=0`)**: the orchestrator renders C1 (looks-good) — with the
Step-2.5 screenshot when the target was reached, or with no image + an honest "couldn't auto-reach the
screen" note when capture was skipped/failed or there was no device (fail-silent — never a nav-help
hand-off; the designer never drives). **Direct-ship path (`DIRECT_SHIP=1`)**: no card — the walker
advances to `audit` (the Step-2.5 capture, if any, has already run).

## Step 4 — build-fail path → agent repair (R18 / max 3)

A build failure means the apply implementation has a problem — the **agent** fixes it, NOT the
designer (who can't act on a compile error). Do not revert the designer's intent; instead:

```bash
git checkout -- $(git diff "$BASE" --name-only)                    # drop the broken edit + build noise
n=$(cat "$WT/.dev/ui-tweak/repair-count" 2>/dev/null || echo 0); echo $((n+1)) > "$WT/.dev/ui-tweak/repair-count"
{ echo "kind: build"; echo "error:"; <one-line compile error>; } > "$WT/.dev/ui-tweak/repair-context"
rm -f "$WT/.dev/ui-tweak/build-pass" "$WT/.dev/ui-tweak/preview-shown"
```

The orchestrator's loop sees `repair-context` and routes back to `/ui-tweak:apply` (repair mode) when
`repair-count < 3`; at `>= 3` it renders the **engineer card** instead (see `/ui-tweak:ff`). STOP.

## `--auto` — failures must be LOUD (R13)

Under `--auto`, preview IS reached — in **direct-ship mode** (D7, revised): the orchestrator's
auto-decision wrote `deliver` + `direct-ship` (+ `auto-navigate`, GGC-14). It is the load-bearing
build proof before the audit, and:

- **`auto-navigate` absent** → pure build-only compile gate (no device cascade, no `preview-shown`, no
  card, no Step 2.5), exactly as before.
- **`auto-navigate` present** (the GGC-14 default for `--auto`) → the Step 0b restricted cascade: launch
  onto an **already-running** device if one exists, then **Step 2.5 navigates + captures** (best-effort
  / fail-silent), else build-only. Either way: no `preview-shown`, no card, exit-code-keyed gate.
  **`--auto` never cold-boots an emulator here and never reaches the interactive `preview-requested`
  device-preview card path.**

A build-fail under `--auto` goes through the SAME agent repair loop as interactive (write
`repair-context` + bump `repair-count` → apply fixes UI-only → re-gate, max 3 — the loop needs no
card, so it is `--auto`-safe). Each failed attempt prints one deterministic stderr line:

```
UI-TWEAK BUILD-FAIL (preview): <one-line reason> — repair attempt <n>/3.
```

At `repair-count >= 3` the orchestrator's `--auto` card-terminus fires instead of the engineer card
(`FAIL: ... repair budget exhausted` to stderr, exit non-zero — see `/ui-tweak:ff` dispatch loop);
the caller classifies the ticket `failed` and `dispatcher-dev-in-flight` stays as the resume signal.
(Contrast audit BLOCKED, which loud-fails immediately with NO repair loop under `--auto` — a logic
finding is not a mechanical fix; see `/ui-tweak:audit`.)

## HITL / Stop

preview is mechanical — no card here (the orchestrator owns the wayfinding cards). On success print:
`App launched on <device> — navigated to <screen> and captured it for review. Audit deferred to ship.`
(or, when capture was skipped/failed: `App launched on <device> — couldn't auto-reach the target screen
(fail-silent, no image).`) — or, in direct-ship mode: `Build gate PASS (direct-ship). Next: audit.`
The orchestrator then renders the post-preview C1 showing the Step-2.5 screenshot (or, when none was
captured, an honest "couldn't auto-reach the screen" note). The designer reviews the result — they do
NOT drive the device.
