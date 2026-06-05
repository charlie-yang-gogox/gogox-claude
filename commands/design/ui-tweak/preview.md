---
name: preview
description: "Phase-1 stage of the /ui-tweak pipeline (R18) — build + install + launch the change onto a device, then STOP and hand the device to the designer to look at and drive THEMSELVES. The agent never screenshots, taps, navigates, or grants permissions — its job ends the moment the app is up. Reached when the designer picks 'I'm done — show me' on card C1. Freezes the audited file set, runs a device cascade (use an already-running/connected device incl. physical FIRST → else boot an emulator/simulator → else honest no-device build-only fallback), then `ui_preview_cmd` (flutter run = build + install + launch; covers Android emulators AND iOS simulators) — all flutter calls use the fvm-aware resolved binary from .dev/ui-tweak/flutter-bin. Quarantines build side-effects, writes .dev/ui-tweak/build-pass (PASS|FAIL) + .dev/ui-tweak/preview-shown. Also runs in DIRECT-SHIP mode (R20, .dev/ui-tweak/direct-ship present): the designer already saw the change on their own device, so it becomes a pure build-only compile gate — no device cascade, no preview-shown, no card; the walker then advances straight to audit. Build fail → write repair-context + bump repair-count → the orchestrator routes back to /ui-tweak:apply for an agent fix (max 3, then the engineer card). The expensive LLM logic audit is Phase 2 (/ui-tweak:audit), AFTER the designer confirms the look. Internal stage — designers run /ui-tweak."
---

<!-- RULE: command content is English. Designer-facing CARD text may be Traditional Chinese. -->

# `/ui-tweak:preview`

> **Single responsibility (Phase 1)**: build + install + **launch** the change onto a device, then
> **hand the device to the designer** — they look at it and drive it themselves. **The agent does NOT
> screenshot, record, tap, navigate, log in, or grant permissions** (see the HARD BOUNDARY in Step 2);
> building onto a real screen exists so the *designer* can interact, not the agent. This REPLACES the
> old "build-only, can't show a screen" terminal (R18). Reached only when
> `.dev/ui-tweak/preview-requested` exists (designer picked "I'm done — show me" on card C1). It does
> NOT run the LLM logic audit — that is Phase 2 (`/ui-tweak:audit`), gated behind the designer
> confirming the look. Build is folded in here — `flutter run` builds + installs + launches in one
> step.

## Inputs

The working-tree diff relative to `base_ref`; the profile's `ui_preview_cmd` (preferred) and
`ui_build_cmd` (no-device fallback). `{device}` in `ui_preview_cmd` is substituted after the cascade.

## Step 0a — misdirect guard (R5/D11)

If `UI_TWEAK_FF` is not set, print **C-MISDIRECT** (see `/ui-tweak:apply` Step 0a) and STOP.

## Step 0 — precondition + freeze the audited surface (F3)

```bash
WT=$(git rev-parse --show-toplevel)
[ -f "$WT/.dev/ui-tweak/base_ref" ] || { echo "FAIL: no base_ref — run /ui-tweak:apply first." >&2; exit 1; }
BASE=$(cat "$WT/.dev/ui-tweak/base_ref")
# FREEZE the audited file set BEFORE building (F3): the build/run will mutate the tree (regenerated
# registrants, codegen); those side-effects must never widen what Phase-2 audit later judges.
git diff "$BASE" --name-only > "$WT/.dev/ui-tweak/audit-files"
```

Resolve `ui_preview_cmd` / `ui_build_cmd`: prefer a repo override in `<repo>/.gogox-claude.yaml`,
else the platform default.

**`{platform} = flutter` ONLY — resolve the flutter binary (probe-based, fvm-aware).** On
`android` / `ios` SKIP this whole block: their profile `ui_build_cmd` (gradlew / xcodebuild) runs
as-is — no flutter resolution, no flutter tooling, nothing here may fail a native-platform run:

```bash
# /ui-tweak:start writes this marker; inline fallback covers a stale worktree from before the marker.
# Probe-based, mirroring start.md: candidates by priority (pinned → fvm first; fvm resolved by
# absolute path since it is often off the agent shell's PATH, e.g. ~/.pub-cache/bin/fvm), each
# verified with one `--version` run; the first that WORKS is persisted. Never guess from config
# alone — some machines have only fvm (no bare flutter), others only bare flutter (fvm off PATH).
if [ -f "$WT/.dev/ui-tweak/flutter-bin" ]; then FLUTTER_BIN=$(cat "$WT/.dev/ui-tweak/flutter-bin"); else
  probe() { eval "$1 --version" >/dev/null 2>&1; }
  FVM_BIN=$(command -v fvm 2>/dev/null || true)
  [ -z "$FVM_BIN" ] && [ -x "$HOME/.pub-cache/bin/fvm" ] && FVM_BIN="$HOME/.pub-cache/bin/fvm"
  PINNED=0; { [ -f "$WT/.fvmrc" ] || [ -f "$WT/.fvm/fvm_config.json" ]; } && PINNED=1
  FLUTTER_BIN=""
  if [ "$PINNED" = 1 ] && [ -n "$FVM_BIN" ] && probe "$FVM_BIN flutter"; then FLUTTER_BIN="$FVM_BIN flutter"
  elif probe flutter; then FLUTTER_BIN="flutter"
  elif [ -n "$FVM_BIN" ] && probe "$FVM_BIN flutter"; then FLUTTER_BIN="$FVM_BIN flutter"
  fi
  [ -z "$FLUTTER_BIN" ] && { echo "FAIL: no working flutter found (tried fvm + bare flutter)." >&2; exit 1; }
  printf '%s\n' "$FLUTTER_BIN" > "$WT/.dev/ui-tweak/flutter-bin"
fi
```

Then rewrite the **leading `flutter` token** of the resolved `ui_preview_cmd` / `ui_build_cmd`
(including repo overrides) with `$FLUTTER_BIN`, and use `$FLUTTER_BIN` for every `flutter devices` /
`flutter emulators` call below. Do NOT re-discover fvm by trial-and-error — the marker is
authoritative.

## Step 0b — direct-ship mode (R20)

```bash
DIRECT_SHIP=0; [ -f "$WT/.dev/ui-tweak/direct-ship" ] && DIRECT_SHIP=1
```

When `DIRECT_SHIP=1` the designer picked **"It already looks right — ship it"** on card C1 (show-me):
they have already looked at it on their own device, so this stage runs as a **build-only compile gate**
— NOT a device preview. Concretely:

- **Skip Step 1 entirely** (no device cascade — do not boot/launch anything). Go straight to the
  **no-device build-only path**: run `ui_build_cmd`.
- **Do NOT write `preview-shown`** in Step 3 (there is no "looks good?" stop — the designer already
  decided to ship). On build PASS the orchestrator's walker (`deliver=1` + `direct-ship` + build PASS)
  proceeds directly to `audit` with no card.
- The build-fail path (Step 4) is **unchanged** — a compile failure still routes to the agent repair
  loop (max 3, then Ce). This gate exists precisely because the designer's hand-build may predate the
  latest tweak.

The rest of this file (Steps 1–4) is the normal **device-preview** path used when `DIRECT_SHIP=0`.

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

## Step 2 — build INTO the device, then STOP (this is also the build gate)

- **Device path**: run `ui_preview_cmd` (e.g. `fvm flutter run -d <id> --debug [--flavor …]` — the
  leading token is the resolved `$FLUTTER_BIN` from Step 0). This builds,
  installs, and launches the app on the device. Run it so the app stays up (background the
  long-running `flutter run` session; do not block the pipeline on its attached console). The moment
  the app is installed + launched (process is up on the device) the build gate has **passed** — go to
  Step 3. A **build/compile failure here is the build-fail path** (Step 4).
- **No-device path (c)**: run `ui_build_cmd` (build-only). Compile failure → Step 4.

> ### ⛔ HARD BOUNDARY — the agent does NOT drive the app (R18, your-job-ends-at-launch)
> The agent's job is **build + install + launch, then hand the device to the designer.** Once the app
> process is up, **STOP touching the device.** You must **NOT**, under any circumstance:
> - take a screenshot or screen recording (no `take_screenshot*`, no screen-record);
> - tap / swipe / type / `adb shell input` / `am start` to a specific screen;
> - grant or dismiss permission dialogs;
> - navigate to "the screen the change affects", log in, fill forms, or re-launch to a deep link.
>
> The **designer** looks at the device and drives it themselves — that is the entire point of building
> onto a real screen. Determining build pass/fail needs only the launch result + the command's
> **exit code**, NOT a screenshot. Key pass/fail on **exit code / a successful install+launch**, never
> on log text — some flutter flavored builds print a false `Gradle build failed to produce an .apk
> file` tail yet exit 0 (confirm via the installed/launched app, not by reading the app's UI).
>
> If the app crashes on launch or won't start, treat it like a build failure → Step 4 (do NOT poke at
> it to "fix" the runtime state).
>
> The ONLY sanctioned capture path in the whole pipeline is the opt-in `/ui-tweak:demo` stage
> (Phase 2, after commit, designer-authorized via `demo-requested`) — and even that stage is
> capture-only (zero input events) on the screen the designer already approved. Inside preview this
> boundary is absolute.

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

STOP. **Normal path**: the orchestrator renders the **"looks good — ship it / more changes"** card (the
post-preview variant of C1); on the no-device path it appends the honest "no device" note.
**Direct-ship path (`DIRECT_SHIP=1`)**: no card — the walker advances to `audit`.

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

Under `--auto`, preview IS reached — in **direct-ship build-only mode** (D7, revised): the
orchestrator's auto-decision wrote `deliver` + `direct-ship`, so this stage runs as the pure compile
gate (no device cascade, no `preview-shown`, no card) — it is the load-bearing build proof before
the audit. The *device* mode is never reached under `--auto` (no card ever writes
`preview-requested`).

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
`App launched on <device> — handed to the designer to look at. Audit deferred to ship.` — or, in
direct-ship mode: `Build gate PASS (direct-ship — no device preview). Next: audit.` The
orchestrator then renders the post-preview C1, whose wording tells the designer **the app is running
on their device and to go look / navigate to the screen themselves** ("It's running on <device> now —
take a look. Does it look right?"). The agent does NOT describe what the screen shows (it never looked).
