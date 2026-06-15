---
name: monkey-test
description: >
  Stress-test a Flutter Android app with `adb shell monkey`, triage any
  crash/ANR it finds, fix the root cause in the Flutter/Dart code, rebuild, and
  re-run until the app survives a configurable number of consecutive clean
  monkey runs. Use when you want to fuzz the UI for robustness or reproduce/fix
  monkey-found crashes. Project-agnostic — resolves the active repo profile,
  Android package id, and Dart import prefix at runtime.
---

# Monkey Test — Stress-test & Auto-fix Android Crashes

Loop `adb shell monkey` against a debug build of the app, catch every crash and
ANR, fix the underlying Flutter/Dart (or native) cause, rebuild, and repeat
until the app runs clean.

**Usage**: `/monkey-test [--events N] [--rounds R] [--seed S] [--throttle MS] [--flavor F] [--no-fix] [--package PKG]`

- `--events N` — monkey events per round (default `1000`).
- `--throttle MS` — milliseconds the monkey pauses between events (default
  `300`). Lower = faster, more aggressive fuzzing (harder on slower
  devices/emulators); higher = gentler, gives async work time to settle.
- `--rounds R` — number of **consecutive clean** rounds required to declare
  success (default `3`). More rounds = higher confidence, slower.
- `--seed S` — fix the monkey PRNG seed for reproducible event streams. Omit for
  a fresh random seed each round (better coverage).
- `--flavor F` — build/install flavor for the triage build (default `dev`).
  Pick a flavor whose `applicationId` is the one installed on the device.
- `--no-fix` — report crashes only; do **not** edit code or rebuild. Useful for a
  quick robustness check or in CI gate mode.
- `--package PKG` — target package. Default resolved in Step 0 from the repo's
  Android `applicationId` + flavor suffix; pass this to override.

---

## Step 0: Resolve project profile & targets

1. Determine the active repo:
   - If `<repo-root>/.gogox-claude.yaml` exists, read its `platform` and `product`.
   - Else read `~/.claude/commands/profiles/registry/$(basename "$(git rev-parse --show-toplevel)").yaml` for `platform` and `product`.
2. **Platform guard**: this command fuzzes the **Android** build of a **Flutter**
   app. If `{platform}` is not `flutter`, stop and tell the user monkey-test is
   Flutter-Android only.
3. **Resolve the Dart import prefix** (used to attribute crash frames to app code,
   not framework/plugin code):
   ```bash
   PKG_PREFIX="$(awk '/^name:/ {print $2; exit}' pubspec.yaml)"   # e.g. client_app
   ```
   App frames look like `package:<PKG_PREFIX>/...`. Fall back to scanning all
   non-framework frames if `pubspec.yaml` has no `name:`.
4. **Resolve the Android package id** (`PKG`):
   - If `--package` was passed, use it verbatim.
   - Else derive from the Gradle build: read `applicationId` from
     `android/app/build.gradle.kts` (or `build.gradle`) and append the chosen
     flavor's `applicationIdSuffix` (commonly `.<flavor>`, e.g. `.dev`).
     ```bash
     grep -E 'applicationId|applicationIdSuffix' android/app/build.gradle.kts
     ```
   - Confirm the resolved `PKG` actually matches an installed package in Step 1.3.

## Pre-flight

1. Verify exactly one device/emulator is attached and ready:
   ```bash
   adb devices
   ```
   - If none: tell the user to boot an emulator (or use the android-emulator
     skill's `emulator_boot.py`) and stop.
   - If several: ask which `-s <serial>` to target, then thread `-s` through all
     adb commands below.
2. Verify the target package is installed (exact match — the prod id is often a
   substring of the dev id, so use `-x`):
   ```bash
   adb shell pm list packages | grep -x "package:<PKG>"
   ```
   - If missing, build & install the chosen flavor (see Step 4) before continuing.
3. Confirm the working tree is clean enough to attribute new diffs to this run
   (`git status --porcelain`). Warn — do not block — if dirty.

---

## Definitions

- **Round** — one invocation of `adb shell monkey ... -v <events>` plus the
  logcat captured during it.
- **Clean round** — monkey completes all events with no crash, no ANR, and no
  fatal exception in logcat.
- **Crash signature** — the top non-framework stack frame (Dart exception class
  + first `package:<PKG_PREFIX>/...` frame, or the native fault address). Used to
  dedupe and to decide whether a fix actually worked.

---

## Steps

### 1. Run one monkey round

For each round:

Run the whole round as **one compound Bash command**. Shell/env state (the
backgrounded logcat PID) does **not** persist between separate Bash tool calls,
so clearing logcat, starting the capture, running monkey, and stopping the
capture must live in the same invocation:

```bash
adb logcat -c                                   # clear so capture covers only this round
adb logcat -v time > /tmp/monkey-logcat-<round>.txt 2>&1 &   # capture (catches what monkey stdout truncates)
LOGCAT_PID=$!
adb shell monkey -p <PKG> \
  --throttle <THROTTLE> \
  --pct-syskeys 0 \
  --kill-process-after-error \
  --monitor-native-crashes \
  [--ignore-timeouts] \
  [-s <SEED>] \
  -v -v <EVENTS> | tee /tmp/monkey-out-<round>.txt
sleep 2
kill $LOGCAT_PID 2>/dev/null                     # same invocation, so $LOGCAT_PID is in scope
```

- **ANRs:** by default do **not** pass `--ignore-timeouts`, so an ANR fails the
  round (matching the "no ANR" success criterion). Pass `--ignore-timeouts` only
  when you deliberately want the fuzzer to keep going past ANRs (e.g. to surface
  more distinct crashes in one `--no-fix` sweep) — and then exclude ANR from the
  failure scan below to stay consistent.
- Record the **seed** monkey prints (`:Monkey: seed=...`) so any crash is
  reproducible.

Then decide the round outcome by scanning monkey stdout (`/tmp/monkey-out-*`)
**and** the logcat file for:
- `// CRASH` / `** Monkey aborted` (monkey-reported crash)
- `FATAL EXCEPTION` / `E/AndroidRuntime` (Java/native fatal)
- `E/flutter` with an unhandled Dart exception / `Unhandled Exception:`
- `ANR in <PKG>` (failure unless `--ignore-timeouts` was deliberately set)

### 2. On a clean round

- Increment the consecutive-clean counter.
- If counter `>= ROUNDS` → **success**: report total events fuzzed, seeds used,
  and any crashes fixed along the way. Stop.
- Otherwise go to Step 1 for the next round (fresh seed unless `--seed` given).

### 3. On a crash/ANR

1. **Capture evidence** — save the monkey seed, the failing event index, the full
   stack trace from logcat, and a screenshot
   (`mcp__android-adb__take_screenshot_and_save`). Compute the crash signature.
2. **If `--no-fix`** — record the signature and (if `--rounds` not yet reached)
   continue to the next round so the report lists *all* distinct crashes, then
   finish with a non-zero/"crashes found" verdict. Skip the rest of Step 3.
3. **Triage** — map the top `package:<PKG_PREFIX>/...` frame to a source file.
   Reproduce deterministically by re-running monkey with the captured `-s <SEED>`
   and the same `--events` to confirm the signature before changing code.
4. **Fix the root cause, not the symptom.** Common monkey-found classes:
   - Null/late init access on rapid navigation → guard with null checks,
     `mounted`/`context.mounted` checks, or fix lifecycle ordering.
   - Navigation onto a disposed/popped route → check `mounted` before
     `Navigator`/`go_router` calls and before `setState`.
   - Tapping while async in flight (double-submit) → debounce/disable the control.
   - Missing route / deep-link arg → handle the null/unknown case gracefully.
   - Keyboard/IME or rotation races → null-safe reads of controllers.
   Prefer defensive, localised fixes. Do **not** disable the offending screen.
5. **Add/adjust a test** when the fix is unit/widget-testable (e.g. a widget test
   that pumps the screen and fires the offending gesture while async is pending).
   Follow the repo's conventions for any code you touch (see Rules).
6. **Rebuild & reinstall** the chosen flavor (Step 4).
7. **Re-verify**: re-run the exact failing seed/events. The crash signature must
   be gone. If it persists, deepen the fix — do not move on.
8. Reset the consecutive-clean counter to 0 and return to Step 1 (a fix can
   surface or mask other paths, so the clean streak restarts).

### 4. Build & reinstall the chosen flavor

```bash
flutter build apk --flavor <FLAVOR> --debug -t lib/main.dart
adb install -r build/app/outputs/flutter-apk/app-<FLAVOR>-debug.apk
```
- `flutter install --flavor <FLAVOR>` is an acceptable shorthand when a device is
  attached. Use a `--debug` (or `--profile`) build so Dart stack traces are
  symbolicated; release builds obfuscate frames and make triage hard.
- On fvm-pinned repos (`.fvmrc` / `.fvm/fvm_config.json` present), prefix the
  `flutter` token with `fvm` (`fvm flutter build apk ...`).
- A full APK rebuild per fix iteration is slow (minutes). When iterating on
  several fixes, prefer `flutter run --flavor <FLAVOR>` and hot-restart between
  attempts, then do one clean `flutter build apk --debug` + reinstall before the
  final verification round so the fuzzed build matches committed code.

### 5. Report

Summarise:
- Verdict: ✅ survived `R` consecutive clean rounds of `N` events, or ❌ crashes
  remain (with `--no-fix`).
- Crashes found & fixed: signature, file:line, one-line root cause, the fix.
- Seeds used (so any run is reproducible).
- Test coverage added.
- Total events fuzzed.

When the verdict is ❌ (crashes remain — `--no-fix` mode, or a crash that could
not be fixed), append a local breadcrumb (GGC-23) per remaining distinct crash
(respecting the helper's per-run cap of 3): run `/_file-followup monkey-crash
summary="<PKG>: <one-line crash root cause>" signature="<crash signature>"`. It
is fail-soft (never blocks the report) and writes only the local gitignored
`.ggx-followups/followups.md` — NO Linear ticket / GitHub. A ✅ survived verdict
writes nothing.

## Rules

- **Always build a debug/profile build for triage** — never run monkey against an
  obfuscated release build when you intend to fix crashes.
- **Never** weaken or delete a feature to silence a crash; fix the actual cause.
- **Never** edit generated code (e.g. a generated API SDK dir) — consult the
  repo's `CLAUDE.md` for which paths are generated/off-limits.
- Reproduce a crash with its captured seed before and after the fix; a fix counts
  only when the signature no longer reproduces.
- Restart the clean-round counter after every code change.
- Keep monkey pinned to the target package (`-p <PKG>`) so it can't wander into
  system UI; keep `--pct-syskeys 0` to avoid spurious system-key noise.
- Respect repo conventions for any code you touch — import ordering, date/time
  formatting, accessibility keys on new interactive widgets, etc. These live in
  the repo's `CLAUDE.md` and any `.claude/rules/*.md`.
