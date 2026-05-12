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
    description: "build/, .dart_tool/, Pods/, ephemeral/"
  - label: "iOS"
    description: "build/, Pods/, Xcode DerivedData"
  - label: "Android"
    description: "build/, .gradle/"
```

If the user selects **Other**, present a follow-up single-select with the
remaining platform:

```yaml
question: "Select platform:"
header: "Platform"
options:
  - label: "Node.js"
    description: "node_modules/, .next/, dist/, .nuxt/, .cache/"
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

#### System caches (when "System caches" scope is selected)

| Target | Description |
|---|---|
| `~/Library/Developer/Xcode/DerivedData/*` | Xcode build cache (all projects) |
| `~/Library/Developer/Xcode/Archives/*` | Xcode archived builds |
| `~/Library/Caches/CocoaPods/*` | CocoaPods download cache |
| `~/Library/Developer/CoreSimulator/Caches/*` | iOS Simulator caches |

#### Simulator apps (shown as separate category in Step 4)

Clean up apps installed inside simulators/emulators. The simulator devices
themselves are preserved — only installed apps and their data are removed.

| Platform | Action | Effect |
|---|---|---|
| iOS Simulator | `xcrun simctl erase all` | Removes all installed apps and data, resets every simulator to factory state. Devices are kept. |
| Android Emulator | `adb -s <emulator> shell pm list packages -3` then `adb uninstall <pkg>` for each | Removes all third-party (user-installed) apps. System apps are untouched. |

**Important**: Do NOT delete simulator/emulator device files directly. Use
the CLI tools above so the devices remain functional.

**Platform filtering**: If the user chose a specific platform in Step 1,
only include system caches relevant to that platform:
- **Flutter** / **iOS**: include all three system cache targets.
- **Android**: skip Xcode and CocoaPods system caches.
- **All**: include all.

---

## Step 4: Deduplicate, measure, and preview

1. Deduplicate — if a child path is already covered by a parent target, drop
   the child.

2. Measure each target with `du -sh`.

3. Aggregate into categories based on source:

   | Category | Contents |
   |---|---|
   | System caches | Xcode DerivedData, Archives, CocoaPods cache |
   | Simulator apps | Installed apps in iOS Simulator and Android Emulator |
   | Flutter | All Flutter project targets found |
   | iOS | All iOS project targets found |
   | Android | All Android project targets found |
   | Node.js | All Node.js project targets found |

4. Print a preview table grouped by category, with subtotals and grand total:

```
Disk available: 2.0 GB

System caches — 30.2 GB
   29 GB    ~/Library/Developer/Xcode/DerivedData/
  622 MB    ~/Library/Developer/Xcode/Archives/
  589 MB    ~/Library/Caches/CocoaPods/

Flutter — 7.4 GB
  4.3 GB    ~/projects/app/build/
  737 MB    ~/projects/app/.dart_tool/
  408 MB    ~/projects/app/ios/Pods/
  1.2 GB    ~/projects/feature-branch/build/
  ...

──────────────────────────
Total: ~37.6 GB
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
  - label: "System caches (30.2 GB) (Recommended)"
    description: "Xcode DerivedData, Archives, CocoaPods cache"
  - label: "Flutter (7.4 GB)"
    description: "build/, .dart_tool/, Pods/ across N projects"
  - label: "iOS (1.1 GB)"
    description: "build/, Pods/ across N projects"
  - label: "Android (200 MB)"
    description: "build/, .gradle/ across N projects"
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
    iOS:      pod install
    Android:  ./gradlew dependencies (or just rebuild)
    Node.js:  npm install (or yarn / pnpm install)
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
