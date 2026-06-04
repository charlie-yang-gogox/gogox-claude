---
name: format
description: >
  Run the project's formatter and static analyzer, optionally commit any
  formatting changes. Branches by {platform}: dart format/fix + flutter
  analyze for flutter; gradle detekt for android; swiftformat + swiftlint
  for ios. Use before creating a PR or whenever you want a clean tree.
---

# Format — Project Formatter & Static Analyzer

Run the full formatting pipeline appropriate for the active project, optionally commit any changes.

**Arguments**: `--skip-commit` to run format + analyze without committing.

---

## Step 0: Resolve project profile

1. Determine the active repo:
   - If `<repo-root>/.gogox-claude.yaml` exists, read its `platform` and `product`.
   - Else read `~/.claude/commands/profiles/registry/$(basename "$(git rev-parse --show-toplevel)").yaml` for `platform` and `product`.
2. Branch on `{platform}` for the rest of the steps.
3. **{platform} = flutter — resolve the SDK binary (probe-based) BEFORE running anything**: if a
   `.dev/ui-tweak/flutter-bin` marker exists it is authoritative — use its command as the
   `flutter` prefix (and the matching `<fvm> dart` form for dart commands). Otherwise probe, never
   guess: on a pinned repo (`.fvmrc` / `.fvm/fvm_config.json`) try the fvm binary first — resolved
   by absolute path (`command -v fvm`, else `~/.pub-cache/bin/fvm`; fvm is often NOT on the agent
   shell's PATH) and verified with one `--version` run; fall back to bare `flutter`/`dart` (with a
   one-line SDK-drift warning when pinned); some machines have ONLY fvm (no bare flutter), others
   ONLY bare flutter — the probe handles both. Never discover any of this by letting the first
   format command fail.

---

## Step 1: Run the platform-appropriate formatter

### {platform} = flutter

1. **`dart format`** (exclude generated API SDK)

   ```bash
   dart format --set-exit-if-changed $(find lib test -name '*.dart' -not -path 'lib/apis/*' | head -5000) || dart format .
   ```

   If the above is impractical, run `dart format .` but **do not stage any changes under `lib/apis/`** in Step 4.

2. **`dart fix --apply`** (exclude generated API SDK)

   ```bash
   dart fix --apply
   ```

   After running, **revert any changes under `lib/apis/`**:

   ```bash
   git checkout -- lib/apis/ 2>/dev/null || true
   ```

3. **Re-run `dart format` to stabilize** (`dart fix` may introduce new formatting needs)

   ```bash
   dart format .
   ```

   Again, **revert any changes under `lib/apis/`**:

   ```bash
   git checkout -- lib/apis/ 2>/dev/null || true
   ```

4. **`flutter analyze`**

   ```bash
   flutter analyze --fatal-warnings --fatal-infos
   ```

   **`flutter analyze` must use `--fatal-warnings --fatal-infos`** — this matches CI (Code Magic: `fvm flutter analyze --fatal-warnings --fatal-infos`). Never omit `--fatal-infos`; info-level lints will fail the build.

   If analyze reports any errors or warnings, **stop and surface them to the user**. Do not commit.

### {platform} = android

1. **Run detekt with auto-correct**

   ```bash
   ./gradlew detekt
   ```

   - **Config**: `lint/detekt/config.yml`
   - **Reports**: `app/build/reports/detekt/detekt.xml` (and per-module variants)
   - Detekt is configured with `autoCorrect = true` so formatting violations are fixed inline.

2. **Check the result**

   - **If BUILD SUCCESSFUL** → Step 2 (Check for changes).
   - **If BUILD FAILED** → parse the XML report, list findings grouped by file, and fix:

     ```
     ❌ Detekt failures:

     app/src/.../Foo.kt
       line 12 — RuleName: <message>
       line 34 — RuleName: <message>
     ```

     For each reported issue: read the file at the reported line, apply the fix, re-run `./gradlew detekt` until clean.

     If the issue is a **false positive**, add `@Suppress("RuleName")` at the affected declaration — only when the code is genuinely correct.

### {platform} = ios

1. **Run swiftformat**

   ```bash
   swiftformat .
   ```

2. **Run swiftlint with auto-correct**

   ```bash
   swiftlint --fix --quiet
   swiftlint --strict
   ```

   If `swiftlint --strict` reports remaining warnings or errors, **stop and surface them to the user**. Do not commit.

---

## Step 2: Check for changes

Run `git status --short`.

- If **no changes**: report "✅ No formatting changes needed" and stop.
- If **changes exist**: continue.

## Step 3: Stop if `--skip-commit` is set

If `$ARGUMENTS` contains `--skip-commit`, report the modified files and stop without committing.

## Step 4: Commit formatting changes

Stage only the modified files (do **not** use `git add -A` — avoid accidentally staging unrelated files):

```bash
git add <modified files>
git commit -m "style(format): apply project formatter and fixes"
```

Use a single commit for all formatting changes.

## Step 5: Show result

Run `git log --oneline -5` and report what happened.

---

## Rules

- **Never touch generated code.** For flutter, this means `lib/apis/`. Other platforms: discover any generated-code directory by reading `.gitignore` or build configs and exclude it.
- Never commit if the static analyzer reports errors.
- Only stage files that were actually changed by the format/fix commands.
- Do not reformat files the user has not touched — formatters are project-wide, but only stage what changed.
- If the user has unstaged changes unrelated to formatting, warn before staging to avoid mixing commits.
- `--skip-commit` is honored by other commands (e.g. `/commit`, `/pull-request`) when they invoke this skill internally.
