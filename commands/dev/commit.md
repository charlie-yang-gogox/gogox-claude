Analyze all current git changes and split them into logical, atomic commits.

**Arguments:** `--skip-openspec` to skip the archive check in Step 7.

## Steps

1. **Run `/format` before anything else** — execute the full format skill as defined in the project's `format` command.
   - If format passes → proceed to Step 2.
   - If format fails → stop. The format skill will have already reported the issues.
2. Run `git --no-pager diff HEAD` and `git status --short` to understand the full scope of changes
3. Group the changes into logical units based on:
   - Feature / functionality boundaries
   - Layer separation (UI, business logic, data, config)
   - File relationships and dependencies
   - Whether changes are independent or tightly coupled
4. For each group, determine the appropriate conventional commit type:
   - `feat`: new feature or capability
   - `fix`: bug fix
   - `refactor`: code restructuring without behavior change
   - `style`: formatting, lint fixes, no logic change
   - `test`: adding or updating tests
   - `chore`: build config, dependencies, tooling
   - `docs`: documentation only
5. Present the proposed commit plan before executing:
   ```
   Proposed commits:
   1. feat(auth): add biometric login support
      Files: lib/features/auth/...
   2. refactor(api): extract base repository class
      Files: lib/core/repositories/...
   3. chore: update dependencies
      Files: pubspec.yaml, pubspec.lock
   ```
   - **Interactive mode**: wait for user confirmation before proceeding.
   - **Unattended / auto mode** (no human to confirm): log the plan and proceed immediately — but the analysis in Steps 2–4 **must still be performed with the same rigor**. Never shortcut into a single commit just because there is no human reviewer. The split quality should be identical to interactive mode.
6. Execute each commit in order:
   - `git add <specific files>`
   - `git commit -m "<type>(<scope>): <description>"`
7. **Archive verification** (run before showing final result):
   Invoke `/check-archive` (or `/check-archive --skip-openspec` if `--skip-openspec` was passed to this skill).
8. Run `git log --oneline -10` at the end to show the result

## OpenSpec Rules (highest priority)

These rules override general grouping logic when openspec files are detected:

- **Proposal commit**: All files under `openspec/changes/*/proposal.md` (and any files sitting directly in `openspec/changes/*/`) must be grouped into a single commit:
  `docs(openspec): add proposal for <feature-name>`
- **Archive commit**: All files under `openspec/archive/` that are part of the current changes must be grouped into a single commit:
  `docs(openspec): archive <feature-name> spec`
- If both proposals and archives are changed, they must be **two separate commits** (proposal first, archive second)
- OpenSpec commits are always `docs` type with `openspec` scope

## Rules

- **Commit message format is strictly `{type}({scope}): {description}`** — never include branch names, ticket numbers (e.g., CAF-123), or issue IDs in the commit message. Ticket tracking belongs in the PR, not in commits.
- **Generated API SDK changes must be a separate commit** — e.g. `lib/apis/` for Flutter or the equivalent generated module for other platforms. Never mix with other changes. Use `chore(api): regenerate API SDK (<modules>)` as the commit message.
- Each commit must be atomic and independently buildable
- Scope should reflect the module or feature name (e.g., `auth`, `booking`, `map`, `core`)
- Description: lowercase, imperative mood, no period, max 72 chars
- Never mix unrelated changes in a single commit
- If a file contains changes belonging to multiple logical units, note it and ask how to handle
- Step 1 (`/format`) is the static-analysis gate. Do not commit if it fails.
- Prefer more granular commits over large ones

## Scope Reference (GoGoX)

Use these scopes when applicable:
- `auth`, `booking`, `tracking`, `payment`, `profile`
- `core`, `shared`, `api`, `config`
- `ci`, `deps` for tooling/infrastructure changes
