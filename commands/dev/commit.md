Analyze all current git changes and split them into logical, atomic commits.

**Arguments:**
- `--skip-openspec` — skip the archive check in Step 7.
- `--tidy [--from <ref>]` — **reorganize the whole branch's existing commits**
  into a clean atomic set (the safe substitute for `git rebase -i`, which this
  environment does not support). Resets the branch back to its base, keeps every
  net change, and re-runs the normal grouping below. See **Step 0**. Optional
  `--from <ref>` overrides the reset target (default: merge-base with trunk).

## Step 0: Tidy mode (`--tidy` only)

Run this BEFORE Step 1. Skip the entire section in a normal commit.

Use `--tidy` when the branch has messy / churned commits (added-then-removed
files, a feature split awkwardly across commits, WIP commits) and you want one
clean atomic series with the **exact same final tree**.

1. **Resolve the base ref `BASE`**: `--from <ref>` if given, else
   `git merge-base HEAD <trunk>` (resolve trunk via the repo profile, else
   `origin/HEAD` → `trunk`/`main`/`master`).
2. **Safety preconditions** — stop with an error if any fail:
   - `BASE` is an ancestor of `HEAD` (`git merge-base --is-ancestor BASE HEAD`).
   - `HEAD != BASE` (there is something to tidy).
   - The current branch is **not** trunk itself.
   - The branch is **not pushed**, OR the user explicitly confirms a
     history rewrite of a pushed branch (force-push will be required). In
     `--auto`/unattended mode, refuse to tidy a pushed branch.
3. **Create a backup TAG** at the current HEAD — a **tag, never a branch**:
   `BACKUP_TAG=tidy-backup-$(git rev-parse --short HEAD); git tag "$BACKUP_TAG"`.
   A branch would pollute the `{feat|fix|release|ci}/{ticket-id}` namespace
   (ticket-id must never carry a suffix); a tag lives in `refs/tags`, is
   invisible to `git branch`, and is trivially deletable. Record `BACKUP_TAG`
   and tell the user how to restore (`git reset --hard "$BACKUP_TAG"`).
4. **Reset, keeping all changes**: `git reset --mixed "$BASE"`. HEAD moves to
   `BASE`, every net change becomes unstaged/untracked in the working tree, and
   **nothing is staged** (this is required — otherwise Step 6's per-group
   `git add` would be defeated by an already-full index and `git commit` would
   swallow every file into one commit).
5. Proceed to Step 1. The "full scope of changes" in Step 2 is now the entire
   `BASE..old-HEAD` net diff, present in the working tree.

**Tidy-mode adjustments to the steps below:**
- **Step 1 (`/format`)** is **skipped** — tidy only re-commits already-committed,
  already-formatted content; the Step 7.5 tree-identity gate is the safety net.
- **Step 7.5 (tidy only)** — after all commits, verify the rewrite changed
  nothing: `git diff --quiet "$BACKUP_TAG" HEAD`. If it reports a difference,
  **STOP**: the re-commit dropped or altered content. Tell the user to restore
  with `git reset --hard "$BACKUP_TAG"` and report what differs. Only on a clean
  (empty) diff is the tidy successful.

## Steps

1. **Run `/format` before anything else** — execute the full format skill as defined in the project's `format` command. **In `--tidy` mode this step is skipped** (re-committing already-formatted content; the Step 7.5 tree-identity gate guards correctness instead).
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
7.5. **Tidy-mode tree-identity gate (`--tidy` only)**: run `git diff --quiet "$BACKUP_TAG" HEAD`. Empty diff → the rewrite preserved the tree exactly; success. Any difference → **STOP**, report what differs, and instruct restore via `git reset --hard "$BACKUP_TAG"`.
8. Run `git log --oneline -10` at the end to show the result. In `--tidy` mode, also remind the user the backup tag `$BACKUP_TAG` can be deleted once they're satisfied (`git tag -d "$BACKUP_TAG"`).

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
