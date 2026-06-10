---
name: purge:sim
description: >
  Delete simulator/emulator DEVICES and iOS runtimes system-wide (not just
  their installed apps). Scans iOS Simulators, Android AVDs, and downloadable
  iOS runtimes, previews each with size and state, and lets the user
  multi-select exactly which to delete. Companion to /purge — where /purge's
  "Simulator apps" category only erases installed apps and keeps the devices,
  /purge:sim removes the devices and runtimes themselves.
---

# Purge:sim — Delete Simulators, Emulators & Runtimes

Reclaim the disk that simulator devices and iOS runtime system-images eat.
Simulator data dirs routinely grow to 10+ GB each, and a single downloaded iOS
runtime is 7–10 GB — duplicates and stale OS versions are the biggest hidden
hogs on a developer Mac.

**This deletes DEVICES and RUNTIMES, not just apps.** It is destructive and
interactive by default: scan → preview → multi-select → confirm → delete.
Nothing is removed without explicit selection.

**Relationship to `/purge`**: `/purge`'s "Simulator apps" category runs
`simctl erase` / `adb uninstall` — it wipes installed apps but keeps the
devices factory-fresh. `/purge:sim` goes further and removes the devices and
runtimes outright. Use `/purge` for routine cleanup; use `/purge:sim` when you
want to cull simulators you no longer use or reclaim runtime disk images.

**Arguments** (all optional):
- `--yes` — skip all prompts and select **everything** in every category
  (all devices + all deletable runtimes). Destructive clean-slate; booted
  devices are shut down first. For scripted / urgent use only.

---

## Step 0: Resolve toolchains

- **iOS** (macOS only): `xcrun simctl` — skip the iOS sections if `xcrun` is
  absent or `xcrun simctl help` fails.
- **Android**: resolve the SDK root as
  `${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}}`. Tools:
  - list AVDs: `$SDK/emulator/emulator -list-avds`
  - delete AVD: `$SDK/cmdline-tools/latest/bin/avdmanager delete avd -n <name>`
    (fall back to `$SDK/tools/bin/avdmanager`)
  - running check / kill: `$SDK/platform-tools/adb`
  - AVD data dir: read it from `~/.android/avd/<name>.ini` → the `path=` key
    (do **not** assume `~/.android/avd/<name>.avd` — the folder name often
    differs from the AVD name, e.g. AVD `Medium_Phone_API_35` →
    `~/.android/avd/Medium_Phone.avd`).
  Skip the Android sections if no `emulator` binary is found.

If neither toolchain is present, report "No simulator toolchains found." and stop.

---

## Step 1: Scan

### iOS simulator devices

```bash
xcrun simctl list devices --json
```
For every device, record: `name`, runtime (derive the iOS/visionOS version from
the `com.apple.CoreSimulator.SimRuntime.*` key), `state` (Booted / Shutdown),
`udid`, and `dataPathSize` (bytes — already in the JSON, no `du` needed).

Also note devices reported with `isAvailable: false` / an `availabilityError`
(runtime no longer installed) — group these as **Unavailable** (always safe to
delete).

### iOS runtimes (downloaded system images)

```bash
xcrun simctl runtime list -j        # identifier, build, state, deletable, sizeBytes
xcrun simctl list runtimes          # human names, e.g. "iOS 26.0 (26.0 - 23A339)"
```
Join the two by `build` to get `name + version + build + sizeBytes`. Only
runtimes with `deletable: true` can be removed (Xcode-bundled runtimes are not).
Flag two sub-groups for safe presets:
- **Duplicate** — more than one build for the same version string (e.g. three
  iOS 26.0 builds); all but the newest build are duplicates.
- **Old** — not the latest version of its platform (e.g. iOS 17.5 / 18.3 when
  26.x exists).

### Android emulators (AVDs)

```bash
$SDK/emulator/emulator -list-avds                      # AVD names
p=$(grep '^path=' ~/.android/avd/<name>.ini | cut -d= -f2-)  # real data dir
du -sh "$p"                                            # size per AVD
$SDK/platform-tools/adb devices                        # find running emulator-XXXX
$SDK/platform-tools/adb -s <serial> emu avd name       # map serial → AVD name
```
Record per AVD: `name`, size, and running-or-not.

---

## Step 2: Preview

Print current disk availability, then a numbered table grouped by category.
Show state and warnings inline so the user can judge safety:

```
Disk available: 24 GB

iOS Simulators — 19 GB
  [1]  11.7 GB  iPhone 17 Pro          iOS 26.2   ● Booted
  [2]   5.4 GB  iPhone 17 Pro Max      iOS 26.2     Shutdown
  [3]   1.9 GB  iPhone 16 Pro          iOS 18.6   ● Booted
  (no unavailable devices)

iOS Runtimes — 73 GB   (deletable system images)
  [4]   9.7 GB  iOS 26.0 (23A5326a)    ⚠ duplicate of iOS 26.0
  [5]   9.3 GB  iOS 26.0 (23A339)      ⚠ duplicate of iOS 26.0
  [6]   9.3 GB  iOS 26.0 (23A343)      ← newest iOS 26.0 build (kept by preset)
  [7]   6.8 GB  iOS 17.5 (21F79)       ⚠ old
  [8]   8.1 GB  iOS 18.3 (22D8075)     ⚠ old
  ...

Android Emulators — 13 GB
  [9]   13 GB   Medium_Phone_API_35      Shutdown

──────────────────────────
Total reclaimable: ~105 GB
```

Only show categories that have items. If nothing is found, report
"Nothing to delete." and stop.

---

## Step 3: Select what to delete

If `--yes` is set, select all devices + all deletable runtimes and skip to
Step 5.

There is **no separate "which category" gate** — go straight to picking the
actual items, which is friendlier. Present each non-empty category as its own
**AskUserQuestion** (`multiSelect: true`). Bundle up to 4 questions in a single
call so the user answers them together.

**Per-category option layout:**

- **≤ 3 selectable items** (typical for devices / AVDs) → list each item as its
  own option, preceded by a **"Select all (N, X GB)"** toggle. That is ≤ 4
  options total and lets the user either one-click All or cherry-pick.

  ```yaml
  question: "iOS Simulators — which to delete?"
  header: "iOS Sims"
  multiSelect: true
  options:
    - label: "Select all (3 devices, 19 GB)"
      description: "Delete every simulator device listed below"
    - label: "iPhone 17 Pro — iOS 26.2 (11.7 GB)"
      description: "Shutting down"
    - label: "iPhone 17 Pro Max — iOS 26.2 (5.4 GB)"
      description: "Shutdown"
    - label: "iPhone 16 Pro — iOS 18.6 (1.8 GB) ● Booted"
      description: "Will be shut down first (Step 5)"
  ```

- **> 3 items** (typical for runtimes) → one option per item overflows the
  4-option cap, so present **non-overlapping bucket toggles** that together
  cover every item, recommending the safe buckets first. The numbered preview
  from Step 2 stays the reference; the user can always reply with explicit
  numbers instead.

  ```yaml
  question: "iOS Runtimes — which to delete?"
  header: "Runtimes"
  multiSelect: true
  options:
    - label: "Duplicate iOS 26.0 builds (2, 19.8 GB) — recommended"
    - label: "Old versions: 17.5 / 18.3.1 / 18.6 (3, 24.8 GB) — recommended"
    - label: "visionOS 2.1 (1, 8.4 GB)"
    - label: "Newest (kept by default): iOS 26.2 + 26.0 latest (2, 20.4 GB)"
  ```

**Safe-preset guidance** — when ordering options or hinting a recommendation,
favor the low-risk items and never lead with the risky ones:
- iOS devices: a single **"Select all"** convenience plus per-device toggles;
  never pre-select a **Booted** device silently (it is shut down in Step 5).
- iOS runtimes: recommend **duplicate** + **old** buckets; the newest build of
  the latest version is a separate, non-recommended bucket.
- Android AVDs: never recommend deleting a **running** AVD.

If the user selects nothing across all categories → stop, delete nothing.

---

## Step 5: Safety pre-flight

Before deleting, resolve conflicts on the selected items:

- **Booted iOS device** selected → `xcrun simctl shutdown <udid>` first.
- **Running Android AVD** selected → `$SDK/platform-tools/adb -s <serial> emu kill`.
- **Runtime still in use** — if a selected runtime still has devices that are
  **not** also selected for deletion, warn the user (those devices would become
  Unavailable) and skip that runtime unless they confirm. Deleting the devices
  on a runtime first is the clean path.

---

## Step 6: Delete

Run per selected item; on any failure, log a warning and continue:

| Item | Command |
|---|---|
| iOS device | `xcrun simctl delete <udid>` |
| iOS unavailable (bulk) | `xcrun simctl delete unavailable` |
| iOS runtime | `xcrun simctl runtime delete <build-or-identifier>` |
| Android AVD | `$SDK/cmdline-tools/latest/bin/avdmanager delete avd -n <name>` |

---

## Step 7: Report

Show before/after disk availability and a summary of what was removed:

```
Purge:sim complete.

  Disk: 24 GB → 129 GB available (freed ~105 GB)

  Removed:
    ✓ iOS runtime   iOS 26.0 (23A5326a)        9.7 GB
    ✓ iOS runtime   iOS 17.5 (21F79)           6.8 GB
    ✓ iOS device    iPhone 17 Pro Max          5.4 GB
    ✓ Android AVD   Medium_Phone_API_35         13 GB
    ...

  To recreate when needed:
    iOS devices:   Xcode ▸ Window ▸ Devices and Simulators (or `xcrun simctl create`)
    iOS runtimes:  Xcode ▸ Settings ▸ Components (re-download from Apple)
    Android AVDs:  Android Studio ▸ Device Manager (or `avdmanager create avd`)
```

---

## Rules

- **Destructive — explicit selection required.** Never delete a device,
  emulator, or runtime that the user did not select (unless `--yes`).
- Never delete a **Booted** iOS device or **running** Android emulator without
  shutting it down / killing it first.
- Only offer iOS runtimes reported `deletable: true`; never attempt to remove
  Xcode-bundled runtimes.
- Always show current disk availability and per-item sizes so the user can
  judge the trade-off (runtimes are large but slow to re-download).
- Preserve the user's latest/newest runtime by default — only ever pre-tick
  duplicates and old versions.
- If a delete fails, warn and continue with the remaining items; report the
  failures at the end.
- If no deletable items are found, report "Nothing to delete." and stop.
- The recreate hint at the end should only list the categories that were
  actually touched.
