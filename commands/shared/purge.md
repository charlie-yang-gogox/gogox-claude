---
name: purge
description: >
  Purge generated files and build caches. Interactive two-step flow: first
  choose platform (All/Flutter/iOS/Android), then choose scope (system-wide
  caches, current working directory, or a custom path). Previews all targets
  with sizes and lets the user multi-select which categories to delete.
---

# Purge — Remove Generated & Cached Files

Reclaim disk space by scanning for generated files that are safe to delete.
Designed for the "disk is full" moment — one command to find and remove
everything regenerable.

**Interactive by default**: choose platform → choose scope → preview sizes →
pick categories → delete. Nothing is removed without explicit user approval.

**Arguments** (all optional — if omitted, the interactive prompts appear):
- `--yes` — skip all prompts, select all platforms + all scopes + all
  categories. For scripted / urgent use.

---

## Step 1: Choose platform

Use **AskUserQuestion** (`multiSelect: false`) to ask which platform to scan:

```yaml
question: "Which platform do you want to purge?"
header: "Platform"
options:
  - label: "All (Recommended)"
    description: "Scan Flutter, iOS, Android, and Node.js targets"
  - label: "Flutter"
    description: "build/, .dart_tool/, Pods/, ephemeral/ + Flutter-attributed DerivedData + ~/.pub-cache"
  - label: "iOS"
    description: "build/, Pods/, native DerivedData, Archives, CocoaPods cache+repos, SPM cache, DeviceSupport, Simulator caches"
  - label: "Android"
    description: "build/, .gradle/, ~/.gradle/caches + wrapper dists"
```

If the user selects **Other**, present a follow-up single-select with the
remaining platform:

```yaml
question: "Select platform:"
header: "Platform"
options:
  - label: "Node.js"
    description: "node_modules/, .next/, dist/, .nuxt/, .cache/, ~/.npm"
```

If `--yes` is set, skip this step and use **All**.

Record the selected platform(s) for Step 3.

---

## Step 2: Choose scope

Use **AskUserQuestion** (`multiSelect: true`) to ask where to scan:

```yaml
question: "Where should I scan?"
header: "Scope"
options:
  - label: "Full scan (Recommended)"
    description: "System caches + all projects under $HOME"
  - label: "Working directory only"
    description: "Current directory and its subdirectories only"
  - label: "Custom path"
    description: "You specify a directory to scan"
```

**Full scan** combines system caches (`~/Library/Developer/Xcode/DerivedData`,
`~/Library/Developer/Xcode/Archives`, `~/Library/Caches/CocoaPods`) with a
recursive project scan under `$HOME` (up to 6 levels deep, excludes
`~/Library`, `~/.Trash`, and top-level hidden dirs).

- If the user selects **Custom path**, follow up by asking for the path using
  AskUserQuestion with a free-text prompt: `"Enter the directory path to scan:"`.
- If `--yes` is set, skip this step and use **Full scan**.

Record the scan roots for Step 3.

---

## Step 3: Scan and build target list

### Scan roots

Based on Step 2 selections, determine where to search:

| Scope | Scan root |
|---|---|
| Full scan | System cache paths (listed below) + `$HOME` (up to 6 levels deep, exclude `$HOME/Library`, `$HOME/.Trash`, top-level hidden dirs) |
| Working directory only | `$(pwd)` and subdirectories |
| Custom path | The user-provided path |

### Per-platform targets

For each scan root, find projects and check for these targets. Only include
paths that **actually exist on disk**.

#### Flutter

Find directories containing `pubspec.yaml`, then check relative to each:

| Target | Description |
|---|---|
| `build/` | Flutter build output |
| `.dart_tool/` | Dart tooling cache |
| `ios/Pods/` | CocoaPods dependencies |
| `macos/Pods/` | CocoaPods dependencies (macOS) |
| `ios/Flutter/ephemeral/` | Flutter engine generated files |
| `macos/Flutter/ephemeral/` | Flutter engine generated files |
| `linux/flutter/ephemeral/` | Flutter engine generated files |
| `windows/flutter/ephemeral/` | Flutter engine generated files |
| `ios/.symlinks/` | Flutter plugin symlinks |

#### iOS

Find directories containing `*.xcodeproj` or `*.xcworkspace`, then check:

| Target | Description |
|---|---|
| `build/` | Xcode build output |
| `Pods/` | CocoaPods dependencies |

#### Android

Find directories containing `build.gradle` or `build.gradle.kts`, then check:

| Target | Description |
|---|---|
| `build/` | Gradle build output (verify sibling `build.gradle` / `build.gradle.kts` exists) |
| `.gradle/` | Local Gradle cache |

#### Node.js

Find directories containing `package.json` (exclude any inside existing
`node_modules/` to avoid nested matches), then check:

| Target | Description |
|---|---|
| `node_modules/` | npm/yarn/pnpm dependencies |
| `.next/` | Next.js build output |
| `.nuxt/` | Nuxt.js build output |
| `dist/` | Common build output (only if sibling `package.json` exists) |
| `.cache/` | Bundler caches (webpack, parcel, etc.) |

#### System & global caches — attributed per platform

These caches live outside any project. Each entry is **attributed to a
platform** so that choosing a single platform in Step 1 only cleans that
platform's share — selecting Flutter must never delete a native iOS app's
DerivedData, or a cache shared with native projects.

**Attribution rules:**

| Cache | Attributed to | Rationale |
|---|---|---|
| `~/Library/Developer/Xcode/DerivedData/<folder>/` | Flutter **or** iOS, per folder | Resolved individually via `info.plist` → `WorkspacePath` (see algorithm below). |
| `~/.pub-cache/` | Flutter | Dart/Flutter global package cache. 100% Flutter-owned. |
| `~/Library/Developer/Xcode/Archives/*` | iOS | Xcode release archives (native concept). |
| `~/Library/Caches/CocoaPods/*` | iOS only (shared) | Pod download cache keyed by pod name+version; shared by native iOS **and** Flutter iOS → **not separable**, so NOT attributed to Flutter. |
| `~/.cocoapods/repos/*` | iOS only (shared) | CocoaPods spec repos (the podspec index, **not** the download cache above). Re-buildable via `pod repo update`. Shared with Flutter iOS → not attributed to Flutter. |
| `~/Library/Caches/org.swift.swiftpm/*` | iOS only (shared) | Swift Package Manager download/clone cache. Re-fetched on next resolve. |
| `~/Library/Developer/Xcode/iOS DeviceSupport/*` | iOS | Per-OS-version symbol files cached when debugging on a **real device**. Regenerated automatically on next attach. Accumulates forever, one heavy folder per iOS version. |
| `~/Library/Developer/Xcode/watchOS DeviceSupport/*`, `tvOS DeviceSupport/*` | iOS | Same as above for watchOS / tvOS devices. |
| `~/Library/Developer/CoreSimulator/Caches/*` | iOS only (shared) | Simulator runtime cache; not project-specific. |
| `~/.gradle/caches/*` | Android only (shared) | Gradle download cache; shared by native Android **and** Flutter Android → **not separable**, so NOT attributed to Flutter. |
| `~/.gradle/wrapper/dists/*` | Android only (shared) | Downloaded Gradle distributions (one per wrapper version). Old versions are dead weight; re-downloaded on demand. |
| `~/.npm/*` | Node.js | npm global download cache. Re-fetched on next install. (Yarn/pnpm stores — `~/Library/Caches/Yarn`, `~/Library/pnpm` / `~/.pnpm-store` — when present, same treatment.) |

**Resolving DerivedData attribution** (run when scanning system caches):

```bash
DD=~/Library/Developer/Xcode/DerivedData
for d in "$DD"/*/; do
  name=$(basename "$d")
  case "$name" in *.noindex) continue;; esac          # skip shared index caches
  wp=$(/usr/libexec/PlistBuddy -c "Print :WorkspacePath" "$d/info.plist" 2>/dev/null)
  if [ -z "$wp" ] || [ ! -e "$wp" ]; then
    echo "STALE   $d"                                  # no info.plist, or workspace deleted (e.g. removed worktree)
  else
    projdir=$(dirname "$wp")                            # Flutter workspace lives at <project>/ios/Runner.xcworkspace
    if [ -f "$projdir/../pubspec.yaml" ] || [ -f "$projdir/pubspec.yaml" ]; then
      echo "FLUTTER $d   $wp"
    else
      echo "IOS     $d   $wp"
    fi
  fi
done
```

**Which caches each platform pulls in:**

- **Flutter** → DerivedData entries tagged `FLUTTER` + `~/.pub-cache`.
  All native-shared caches below (CocoaPods cache, cocoapods/repos, SPM,
  DeviceSupport, CoreSimulator, Gradle caches/dists) and Archives are
  **excluded** (shared with native projects, or native-only).
- **iOS** → DerivedData tagged `IOS` + Archives + CocoaPods cache +
  `~/.cocoapods/repos` + SPM cache + iOS/watchOS/tvOS DeviceSupport +
  CoreSimulator caches.
- **Android** → `~/.gradle/caches` + `~/.gradle/wrapper/dists`.
- **Node.js** → `~/.npm` (+ Yarn/pnpm stores when present).
- **All** → everything above (each shared cache counted once).
- Entries tagged `STALE` (no `info.plist`, or `WorkspacePath` points to a
  deleted directory — typically a removed worktree) go into their own
  **Stale DerivedData** category regardless of platform, so they can be
  cleared without risking any attributable build.

#### Simulator apps (shown as separate category in Step 4)

Clean up apps installed inside simulators/emulators. The simulator devices
themselves are preserved — only installed apps and their data are removed.

| Platform | Action | Effect |
|---|---|---|
| iOS Simulator | `xcrun simctl erase all` | Removes all installed apps and data, resets every simulator to factory state. Devices are kept. |
| Android Emulator | `adb -s <emulator> shell pm list packages -3` then `adb uninstall <pkg>` for each | Removes all third-party (user-installed) apps. System apps are untouched. |

**Important**: Do NOT delete simulator/emulator device files directly. Use
the CLI tools above so the devices remain functional.

**Platform filtering** for system & global caches is governed by the
attribution rules in the "System & global caches" section above — selecting a
platform pulls in only that platform's attributed caches.

---

## Step 4: Deduplicate, measure, and preview

1. Deduplicate — if a child path is already covered by a parent target, drop
   the child.

2. Measure each target with `du -sh`.

3. Aggregate into categories. System & global caches fold into their attributed
   platform (per the attribution rules) rather than a monolithic bucket:

   | Category | Contents |
   |---|---|
   | Flutter | Flutter project targets + `FLUTTER`-tagged DerivedData + `~/.pub-cache` |
   | iOS | iOS project targets + `IOS`-tagged DerivedData + Archives + CocoaPods cache + `~/.cocoapods/repos` + SPM cache + iOS/watchOS/tvOS DeviceSupport + CoreSimulator caches |
   | Android | Android project targets + `~/.gradle/caches` + `~/.gradle/wrapper/dists` |
   | Node.js | Node.js project targets + `~/.npm` (+ Yarn/pnpm stores when present) |
   | Stale DerivedData | Orphan DerivedData (no `info.plist`, or workspace deleted — e.g. removed worktrees) |
   | Simulator apps | Installed apps in iOS Simulator and Android Emulator |

4. Print a preview table grouped by category, with subtotals and grand total.
   For platform categories, list project targets and attributed system caches
   together so the user sees the full footprint:

```
Disk available: 2.0 GB

Flutter — 8.1 GB
  4.3 GB    ~/projects/app/build/
  737 MB    ~/projects/app/.dart_tool/
  408 MB    ~/projects/app/ios/Pods/
  1.2 GB    ~/projects/feature-branch/build/
  680 MB    ~/Library/Developer/Xcode/DerivedData/Runner-cgpth…/   (→ app/ios/Runner.xcworkspace)
  720 MB    ~/.pub-cache/
  ...

Stale DerivedData — 2.1 GB
  1.4 GB    ~/Library/Developer/Xcode/DerivedData/Runner-fkxfkx…/  (workspace deleted)
  700 MB    ~/Library/Developer/Xcode/DerivedData/Pods-andje…/     (no info.plist)

──────────────────────────
Total: ~10.2 GB
```

Only show categories that have targets. If nothing is found, report
"Nothing to purge." and stop.

---

## Step 5: Choose what to delete

If `--yes` is set, select all categories and proceed to Step 6.

Otherwise, use **AskUserQuestion** (`multiSelect: true`) to let the user pick
which categories to purge. Only include categories that have targets (2–4
options). Each option shows the category name and total size:

```yaml
question: "Which categories do you want to purge?"
header: "Purge"
multiSelect: true
options:
  - label: "Flutter (8.1 GB) (Recommended)"
    description: "build/, .dart_tool/, Pods/ across N projects + Flutter DerivedData + ~/.pub-cache"
  - label: "Stale DerivedData (2.1 GB)"
    description: "Orphan Xcode caches — deleted workspaces / removed worktrees"
  - label: "iOS (1.1 GB)"
    description: "build/, Pods/ + native DerivedData, Archives, CocoaPods cache+repos, SPM, DeviceSupport, Simulator caches"
  - label: "Android (200 MB)"
    description: "build/, .gradle/ + ~/.gradle/caches + wrapper dists"
```

- If the user selects nothing or cancels → stop, delete nothing.
- Otherwise → proceed to Step 6 with only the selected categories.

---

## Step 6: Delete

Remove each target in the selected categories:

```bash
rm -rf <target>
```

If a target fails due to permissions, run `chmod -R u+w <target>` and retry
once. If it still fails, log a warning and continue with the remaining targets.

---

## Step 7: Report

Show before/after disk availability and a summary:

```
Purge complete.

  Disk: 2.0 GB → 41 GB available (freed ~39 GB)

  Removed:
    ✓ ~/Library/Developer/Xcode/DerivedData/      29 GB
    ✓ ~/projects/app/build/                       4.3 GB
    ✓ ~/projects/app/.dart_tool/                  737 MB
    ...

  To restore project dependencies when needed:
    Flutter:  flutter pub get && cd ios && pod install
    iOS:      pod install   (cocoapods/repos: pod repo update if needed)
    Android:  ./gradlew dependencies (or just rebuild)
    Node.js:  npm install (or yarn / pnpm install)

  Global caches re-warm automatically: Xcode iOS DeviceSupport regenerates the
  next time you attach that device; SPM / Gradle / npm caches re-download on the
  next build or install. No manual restore needed.
```

---

## Rules

- **Interactive by default.** Guide the user through platform → scope →
  category selection. Never delete without explicit user choices (unless
  `--yes` is passed).
- Never delete source code, git history, or configuration files.
- Never delete `lib/`, `src/`, `test/`, `.git/`, or any user-authored content.
- Only delete directories that are known to be fully regenerable by build tools.
- Always check existence before attempting deletion — skip silently if not found.
- Always show current disk availability in the preview so the user can judge
  urgency.
- If no cleanable targets are found, report "Nothing to purge." and stop.
- The restore hint at the end should only list platforms that were actually
  cleaned.
