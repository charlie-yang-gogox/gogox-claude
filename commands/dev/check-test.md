---
name: check-test
description: >
  Run the project's test suite with smart incremental detection. By default,
  only tests affected by the current branch's changes are executed, making
  the feedback loop fast during development. Branches by {platform}:
  flutter (file-level), android (module-level), ios (suite-level fallback).
---

# Check Test — Run Project Test Suite

Run tests with platform-appropriate incremental detection.

**Arguments**:

- `--all` — run the full test suite (ignores incremental detection).
- `--fix` — automatically diagnose and fix failures, then re-run until green.
- `--no-escalate` — incremental mode only: when a changed file is "widely-used" (imported by many test files), do **NOT** fall back to the full suite — cap at the directly-affected tests (the file's mirror test + same-feature-dir tests) and note that the full suite is deferred to CI. For callers that re-test only because *base* moved (a rebase), not because the branch diff changed — e.g. `/ggx-pr-resolver`'s rebase stage — where the full-suite escalation buys little over what CI already runs. Ignored under `--all`.
- Arguments can be combined: `--all --fix`, `--fix --no-escalate`.

---

## Step 0: Resolve project profile

1. Determine the active repo:
   - If `<repo-root>/.gogox-claude.yaml` exists, read its `platform` and `product`.
   - Else read `~/.claude/commands/profiles/registry/$(basename "$(git rev-parse --show-toplevel)").yaml` for `platform` and `product`.
2. Branch on `{platform}` for the platform-specific steps below.

## Step 1: Detect CPU cores

Cross-platform — used by all platform branches:

```bash
TOTAL_CORES=$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)
```

- **Incremental mode**: `CORES = max(2, TOTAL_CORES / 2)` — lightweight, leaves headroom for IDE/emulator.
- **Full mode (`--all`)**: `CORES = max(2, TOTAL_CORES * 3 / 4)` — heavier but still leaves headroom.

(Whether `CORES` is honored depends on the platform's runner — see Step 3 / 4.)

## Step 2: Determine mode

- If `$ARGUMENTS` contains `--all` → **Full mode**, jump to Step 4.
- Else → **Incremental mode**, proceed to Step 3.
- If the current branch is the repo's default branch (no diff against base) → automatically fall back to **Full mode** and inform the user.

## Step 3: Incremental detection (cross-platform skeleton)

Find the base and the list of changed files — same on every platform:

```bash
source "$HOME/.claude/lib/dev-mode.sh"
BASE=$(git merge-base HEAD "$(default_branch)")   # default branch: trunk (flutter) or main (gogox-claude)

# Committed changes on this branch
git diff --name-only "$BASE"..HEAD

# Uncommitted (staged + unstaged) changes
git diff --name-only HEAD
```

Combine and deduplicate into `CHANGED_FILES`. Then run the platform-appropriate filter and runner below.

### Step 3.flutter (when {platform} = flutter)

a. Filter `CHANGED_FILES` to entries under `lib/` or `test/`.

b. Map source files to test files:

   - **A `test/*_test.dart` changed** → include it directly.
   - **A `lib/` file changed** → look for its mirror test:
     - General pattern: replace `lib/` with `test/` and `.dart` with `_test.dart`.
     - Examples: `lib/features/foo/bar.dart` → `test/features/foo/bar_test.dart`; `lib/router/foo.dart` → `test/router/foo_test.dart`.
   - **A `lib/` file changed but no direct mirror test exists** → use `grep -rl` to find test files that import the changed file (search within `test/`).
   - **A widely-used file changed** (e.g. `lib/core/`, `lib/theme/`, `lib/common/`, providers, models imported by 10+ test files) → fall back to **Full mode** and inform the user why. **Exception — `--no-escalate`:** do NOT escalate; cap `TEST_FILES` at the file's direct mirror test plus the `_test.dart` files in the same feature directory, and print `note: --no-escalate — skipping full-suite escalation for widely-used file <f>; the full suite is deferred to CI`. (2026-06-08: a `lib/router/app_router.dart` touch, imported by 29 test files, escalated a rebase-stage check to the full 4379-test suite (~350s) and dominated wall-clock — pure waste when only `base` moved, since CI re-runs the full suite on push anyway.)

c. Collect into `TEST_FILES`. If empty (only non-code files changed), report "No affected tests found" and succeed.

d. Run only the affected tests:

```bash
flutter test -j $CORES <file1> <file2> ...
```

### Step 3.android (when {platform} = android)

a. Filter `CHANGED_FILES` to entries under `**/src/main/{java,kotlin}/**` or `**/src/test/{java,kotlin}/**` or `**/src/androidTest/{java,kotlin}/**`.

b. **Resolve gradle modules from changed files**: for each changed file, walk up its directory tree until you find a `build.gradle` or `build.gradle.kts`. The module path is the directory containing that file (relative to repo root, e.g. `app`, `feature/booking`, `core/network`). Convert to gradle path notation (`:app`, `:feature:booking`, `:core:network`).

c. Deduplicate the module list into `AFFECTED_MODULES`. If empty (only non-code files changed), report "No affected modules found" and succeed.

d. **Optionally narrow further with class-name filters**: for each changed source file, derive the class name (filename without extension). Build a `--tests` filter for the matching test class:

   - `Foo.kt` → `--tests "*FooTest*"`

e. Run tests per affected module. Two strategies:

   - **Module-only (recommended default)**:
     ```bash
     ./gradlew :<module>:testDebugUnitTest --parallel --max-workers=$CORES
     ```
     For each module in `AFFECTED_MODULES`, in one combined invocation:
     ```bash
     ./gradlew :app:testDebugUnitTest :feature:booking:testDebugUnitTest --parallel --max-workers=$CORES
     ```
   - **Module + class filter (faster but more brittle)**:
     ```bash
     ./gradlew :<module>:testDebugUnitTest --tests "*FooTest*" --tests "*BarTest*" --parallel --max-workers=$CORES
     ```

   Default to module-only unless `CHANGED_FILES` only touches a single module's source files (then class filter is reliable).

f. **Widely-used files** (under `core/`, `common/`, base classes imported by many modules) → fall back to **Full mode** and inform the user why.

### Step 3.ios (when {platform} = ios)

True file-level incremental testing on iOS requires reading scheme/target metadata from `.xcodeproj` / `.xcworkspace`, which is project-specific and brittle. **Fall back to Full mode** and inform the user why:

> "Incremental test selection is not supported on `{platform}=ios` in this skill. Running the full suite. If you want a faster loop, scope manually with `xcodebuild test -only-testing:Target/Class`."

## Step 4: Full mode (cross-platform with platform-specific runner)

### Step 4.flutter

```bash
flutter test -j $CORES
```

### Step 4.android

```bash
./gradlew testDebugUnitTest --parallel --max-workers=$CORES
```

### Step 4.ios

Use the platform's `test_cmd` from the profile yaml (already includes workspace/scheme/destination):

```bash
{test_cmd}
```

(For ios, `~/.claude/commands/profiles/platform/ios.yaml` holds the full xcodebuild invocation.)

## Step 5: Report results

Always start the report with the **mode** used:

```
🔍 Incremental mode ({platform}): running N test files (out of TOTAL)
```

or:

```
🔍 Full mode ({platform}): running all tests
```

If all tests **pass**: report `✅ All tests passed` with the test count.

If any tests **fail**:

- Report the failure summary:
  ```
  ❌ Tests failed: X passed, Y failed
  ```
- Parse the platform's runner output (flutter test stdout, gradle test reports under `build/reports/tests/`, xcodebuild result bundle) and present a **failure table** with every failing test:

  | # | Test Name | File:Line | Error Message |
  |---|-----------|-----------|---------------|
  | 1 | `group › test description` | `test/foo_test.dart:42` | `Expected: 3  Actual: 5` |
  | 2 | `another group › other test` | `test/bar_test.dart:118` | `type 'Null' is not a subtype of type 'String'` |

  - **Test Name**: the full test path including group/suite hierarchy.
  - **File:Line**: file path and line number where the failure was reported. If the runner does not provide a line number, use the line from the top of the stack trace.
  - **Error Message**: the assertion or exception message. Truncate to 120 characters if longer, appending `…`.

**Without `--fix`**: stop and fail.

**With `--fix`**:

1. Read the failure output carefully.
2. Diagnose the root cause (missing imports, type mismatches, renamed symbols, logic errors, etc.).
3. Fix the issue in the source or test file.
4. Re-run the same test command (not the full suite, unless `--all` was used).
5. Repeat until all tests pass or max 5 fix attempts reached.
6. If a failure cannot be fixed confidently after 5 attempts, stop and **ask the user**.

## Rules

- Without `--fix`: this is a read-only check — never modify code.
- With `--fix`: only modify files directly related to the failing test. Do not refactor or improve unrelated code.
- Always show the total test count (passed + failed) when the runner provides one.
- If the test run itself errors (compilation failure, gradle sync failure, xcodebuild config error), surface the error clearly — do not retry blindly.
- With `--fix`, report each fix attempt: what was changed and why.
- In incremental mode, if you are unsure whether the detected tests are sufficient, suggest the user run `--all` to be safe.
- When on the repo's default branch (no diff against base), automatically fall back to `--all`.
- iOS incremental mode is intentionally not implemented — full suite only. Revisit if iOS team onboards a workable filter.
