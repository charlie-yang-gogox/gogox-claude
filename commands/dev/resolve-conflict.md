---
name: resolve-conflict
description: >
  Merge/Rebase current branch onto trunk, resolve any merge conflicts,
  run tests until green, format, and commit. Single mode does NOT push.
  Platform-aware: uses the right test and format commands per project. With
  --batch, sweeps open GitHub PRs (optionally filtered by --user), resolves
  each that is behind its base branch, and pushes only the ones that came
  through cleanly (no conflicts, tests green).
---

# Resolve Conflict — Rebase onto Trunk & Fix Conflicts

Pull latest trunk via merge/rebase, resolve conflicts, verify tests pass, format, and commit.

---

## Modes

| Mode | Trigger | Scope |
|------|---------|-------|
| **Single** (default) | no `--batch` | Resolves the **current branch** against `origin/trunk`. Runs Steps 0–8 below. |
| **Batch** | `--batch` | Sweeps open GitHub PRs (optionally filtered by `--user`), and for each PR that is behind its base branch, runs the per-branch core (Steps 2–6) against **that PR's own base branch**. Batch-level pre-flight and reporting replace Steps 0–1 and 7–8. See the **Batch Mode** section near the end. |

**Usage**:

```
/resolve-conflict [--rebase | --merge]
/resolve-conflict --batch [--user=<login> | --user=@me] [--rebase | --merge] [--dry-run]
```

- `--rebase` (default) / `--merge` — strategy, passed through to every PR in batch mode.
- `--user=<login>` — batch only; restrict to PRs **created by** that GitHub login. `@me` resolves to the authenticated user. Omit to sweep all PRs (bots excluded).
- `--dry-run` — batch only; **read-only preview**. Lists every matching PR and reports GitHub's own merge status (`mergeable` / `mergeStateStatus`) for each. Performs **no** checkout, fetch-merge, rebase, conflict resolution, test run, commit, or push — nothing is mutated, locally or remotely.

**Base branch.** Throughout Steps 2–8, `{base_branch}` is the remote ref the branch is rebased/merged onto:

- Single mode → `{base_branch}` is `origin/trunk`.
- Batch mode → `{base_branch}` is `origin/<the PR's baseRefName>` (i.e. the actual base the PR targets — `main`, `master`, `trunk`, or any release branch), set by the batch loop per PR.

---

## Step 0: Resolve project profile

Before any other step, determine the active project profile so later steps know which test and format commands to run.

**Resolution order:**

1. **Repo self-describes** — read `<repo-root>/.gogox-claude.yaml` if present. Use its `platform` field.
2. **Central mapping** — else, read `~/.claude/commands/profiles/registry/$(basename "$(git rev-parse --show-toplevel)").yaml` for `platform` and `product`.
3. **Error** — if neither resolves, stop and tell the user:
   > Cannot resolve gogox project profile. Either add `~/.claude/commands/profiles/registry/<basename>.yaml`, or create `<repo>/.gogox-claude.yaml` with `platform:` and `product:`.

After resolution, read:

- `~/.claude/commands/profiles/platform/<platform>.yaml` — exposes `test_cmd`, `format_cmd`.

Hold these values in memory for use in Steps 4 and 5 where you see `{test_cmd}` and `{format_cmd}`.

## Steps

### 1. Pre-check

If `--merge` is provided, use `merge` instead of `rebase` in all git commands in this skill.

If `--rebase` is provided, use `rebase` in all git commands in this skill.

Invoke `/check-clean`. If it fails, stop and ask the user to commit or stash before proceeding.

### 2. Fetch & Merge/Rebase onto trunk

`{base_branch}` is `origin/trunk` in single mode, or `origin/<the PR's baseRefName>` in batch mode (see **Modes** above). Substitute the bare branch name (e.g. `trunk`, `main`) for the `git fetch` refspec.

#### merge commands
```bash
git fetch origin <base-branch-name>
git merge {base_branch}
```

#### rebase commands
```bash
git fetch origin <base-branch-name>
git rebase {base_branch}
```

If the merge/rebase succeeds with no conflicts, skip to Step 4.

### 3. Resolve Conflicts

While the merge/rebase is paused due to conflicts:

1. Run `git diff --name-only --diff-filter=U` to list all conflicted files.
2. For **each** conflicted file:
   a. **Read** the file to understand the conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`).
   b. **Read** the surrounding code context and the intent of both sides (ours = current branch, theirs = trunk).
   c. Resolve the conflict by choosing the correct merge of both sides. Prefer preserving both sides' intent; if in doubt, ask the user via AskUserQuestion.
   d. After editing, stage the resolved file: `git add <file>`.
3. Continue the merge/rebase:
   ```bash
   git merge --continue
   ```
   ```bash
   git rebase --continue
   ```
4. If further conflicts arise, repeat from step 3.1.

**Rules for conflict resolution:**
- Never blindly accept "ours" or "theirs" — always read and understand both sides.
- If a conflict involves non-trivial logic changes from both sides, **ask the user** before resolving.
- Ensure no conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`) remain in any file after resolution.

### 4. Verify — Run Tests until Green

Run `{test_cmd}` to verify the test suite passes after the merge/rebase. Fix any rebase-related test failures before continuing.

Note: on platforms where `{test_cmd}` is itself a slash command (e.g. Flutter's `/check-test --all --fix`), invoke it as a slash command. On platforms where it's a raw shell command (e.g. Android's `./gradlew testDebugUnitTest`), run it via Bash.

### 5. Format

Run `{format_cmd}` to apply formatter and lint fixes — without committing.

Same note as Step 4: invoke as slash command when it starts with `/`, otherwise run via Bash.

### 6. Commit

Invoke `/commit` to create atomic, well-scoped commits for all changes (including formatting fixes).

If `/commit` is not available in this project, fall back to creating a single conventional-commit message that summarizes the merge/rebase and any fix-ups.

### 7. Conflict Summary Report

After all conflicts are resolved and commits are done, produce a summary table of every conflict that was resolved during the merge/rebase. Track this information as you work through Step 3.

Format:

```
## Conflict Resolution Summary

| # | File | Conflict Area | Ours (branch) | Theirs (trunk) | Resolution |
|---|------|--------------|----------------|----------------|------------|
| 1 | lib/features/auth/login.dart | import block | added `package:foo` | added `package:bar` | kept both imports |
| 2 | lib/core/api/client.dart | `fetchData()` method | changed return type to `Future<Result>` | added retry logic | merged both: kept new return type + retry logic |
| ... | ... | ... | ... | ... | ... |
```

- **File**: the conflicted file path
- **Conflict Area**: which section/function/block had the conflict
- **Ours (branch)**: what the current branch changed
- **Theirs (trunk)**: what trunk changed
- **Resolution**: how you resolved it (kept both, chose ours, chose theirs, merged manually, etc.)

If no conflicts occurred, report: "No conflicts — merge/rebase was clean."

### 8. Done — Do NOT Push

Report the result:
- Show the Conflict Summary from Step 7.
- Show `git log --oneline -10` so the user can review.
- Remind the user that changes have **not** been pushed.
- If the user wants to push, they should do so manually.

---

## Rules

- **Single mode: never force-push** or run `git push` automatically.
- **Never run `git rebase --abort`, `git merge --abort` nor `git cherry-pick --abort`** without asking the user first.
- If the merge/rebase hits more than 5 conflict rounds, pause and ask the user if they want to continue or abort.
- Do not modify files unrelated to the conflict resolution or test fixes.
- All conflict markers must be verified removed before continuing the merge/rebase.
- **Batch mode push policy:** push a PR branch **only** when it came through **clean** — the rebase/merge applied with **zero conflicts** and tests are green. PRs whose conflicts had to be resolved (or whose tests failed) are **never** auto-pushed; they stay local for human review. Always return to the original branch/worktree at the end, even on failure.

---

## Batch Mode (`--batch`)

When `--batch` is present, **do not** run the single-branch flow against the current branch. Instead orchestrate a sweep over open GitHub PRs. Each selected PR is resolved by running the per-branch core (Steps 0–8) against that PR's own `{base_branch}`.

### Step B0: Pre-flight

1. Resolve the project profile exactly as in **Step 0** (needed for `{test_cmd}` / `{format_cmd}` per PR). **Dry-run:** skip — no tests are run.
2. Resolve the repo: `REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)`. If this fails (not a GitHub-backed repo), stop and tell the user.
3. Invoke `/check-clean`. If the working tree is dirty, stop and ask the user to commit or stash — batch mode switches branches and must start clean. **Dry-run:** skip — dry-run never checks out a branch, so a dirty tree is harmless.
4. Record the current branch so it can be restored at the end:
   ```bash
   ORIG_REF=$(git symbolic-ref --quiet --short HEAD || git rev-parse HEAD)
   ```
   **Dry-run:** skip — the current branch is never left.

### Step B1: List & filter PRs

```bash
gh pr list --repo "$REPO" --state open \
  --json number,headRefName,baseRefName,author,isDraft,title,mergeable,mergeStateStatus \
  --limit 100
```

`mergeable` is `MERGEABLE` / `CONFLICTING` / `UNKNOWN`; `mergeStateStatus` is `CLEAN` / `BEHIND` / `DIRTY` / `BLOCKED` / `UNSTABLE` / `UNKNOWN`. These are GitHub's authoritative merge-status fields — used directly by `--dry-run` (Step B3) and as the conflict hint in the summary. If a value is `UNKNOWN`, GitHub is still computing it; re-running the command shortly resolves it.

Filter the list:

- **`--user=<login>` (created-by).** If provided, keep only PRs whose `author.login` matches `<login>` case-insensitively. `--user=@me` → resolve the login first via `gh api user --jq .login` and match that. If `--user` is omitted, keep all PRs.
- **Drop bots.** Always drop PRs whose `author.login` is `dependabot`, `renovate`, `github-actions`, or ends in `[bot]` (consistent with `/code-review`).

If the filtered list is empty, print `No matching open PRs in $REPO.` and stop.

### Step B2: Determine which PRs are behind their base

For each surviving PR, decide whether it needs resolution (it is **not** up to date with its base branch):

```bash
git fetch origin <baseRefName> <headRefName>
BEHIND=$(git rev-list --count "origin/<headRefName>..origin/<baseRefName>")
```

- `BEHIND == 0` → the PR is already up to date with its base → **skip**, mark `up-to-date` in the batch summary.
- `BEHIND > 0` → the PR is behind → queue it for resolution. Also note `mergeable` (`CONFLICTING` vs `MERGEABLE`) for the summary, but resolve regardless — a clean fast-forwardable rebase still brings the branch current.

> Note on fork PRs: `headRefName` may not exist on `origin`. If the fetch of `<headRefName>` fails, fall back to `gh pr checkout` in Step B3 (it handles forks) and compute `BEHIND` after checkout as `git rev-list --count "HEAD..origin/<baseRefName>"`.

> **Dry-run:** skip the local `git fetch` / `git rev-list` entirely — use GitHub's `mergeStateStatus` (`BEHIND` ⇒ behind base, `CLEAN` ⇒ up to date) as the behind/up-to-date signal. Dry-run touches no local refs.

### Step B3: Resolve each queued PR (sequential)

> **Dry-run short-circuit.** If `--dry-run` was passed, do **not** check out, resolve, test, commit, or push anything. Instead print the read-only preview table below — using the `mergeable` / `mergeStateStatus` values already fetched in Step B1 — and stop. Nothing is mutated.
>
> ```
> ## Batch Dry-Run — open PRs in <owner/repo>
>
> Strategy if run: <rebase|merge>   Filter: <--user value or "all (bots excluded)">
>
> | # | PR | Branch ← Base | Merge status (GitHub) | Up to date? | Would do |
> |---|----|--------------|------------------------|-------------|----------|
> | 1 | #123 Add foo | feat/foo ← main | CONFLICTING / BEHIND | no | resolve conflicts, leave local for review |
> | 2 | #124 Fix bar | fix/bar ← trunk | MERGEABLE / BEHIND | no | rebase clean, push (--force-with-lease) |
> | 3 | #125 Tidy    | tidy ← main | MERGEABLE / CLEAN | yes | skip (already current) |
> | 4 | #126 WIP     | wip ← main | UNKNOWN / UNKNOWN | ? | re-run shortly — GitHub still computing |
> ```
>
> The **Would do** column is derived purely from GitHub's reported status — `CONFLICTING` ⇒ would resolve then leave local; `MERGEABLE` + `BEHIND` ⇒ would resolve cleanly and push; `CLEAN` ⇒ would skip. No local git operations are performed to produce this.

Process queued PRs **one at a time** (conflict resolution needs focused, sequential attention). For each PR:

1. Announce: `[batch i/N] PR #<number> "<title>" — <headRefName> ← <baseRefName> (behind <BEHIND>)`.
2. Check out the PR branch robustly (handles forks, creates/updates the local tracking branch):
   ```bash
   gh pr checkout <number> --repo "$REPO"
   ```
   If the local branch has **unpushed commits that differ from the PR's remote head**, do not clobber them — skip the PR, mark `local-diverged`, and tell the user to reconcile manually.
3. Set `{base_branch}` = `origin/<baseRefName>` for this PR.
4. Run the per-branch core — **Steps 2 → 6** (Fetch & rebase/merge → Resolve conflicts → Verify tests → Format → Commit) — using this PR's `{base_branch}` and the strategy from `--merge`/`--rebase` (default rebase). The Step 3 conflict rules and the 5-round gate apply per PR.
5. Capture the per-PR outcome for the summary:
   - `resolved` — conflicts were resolved, tests green. **Not pushed** (had conflicts → human review).
   - `clean` — rebase/merge applied with **no conflicts** and tests green.
   - `tests-failed` — rebase applied but `{test_cmd}` is red after fix attempts (leave the branch as-is; flag it; **not pushed**).
   - `skipped` — `up-to-date`, `local-diverged`, or user chose to skip at a gate.
   - `aborted` — user aborted this PR's rebase/merge (only after confirming, per Rules).
6. **Push clean PRs only.** If — and only if — the outcome is `clean`, push the branch now (we are already checked out on it):
   - **rebase** strategy → history was rewritten: `git push --force-with-lease origin <headRefName>`. `--force-with-lease` (never `--force`) so a concurrent push by someone else aborts the push instead of clobbering it.
   - **merge** strategy → fast-forward, no rewrite: `git push origin <headRefName>`.
   - Record `pushed` (or `push-failed` with the error) in the summary Notes. A failed push does **not** stop the batch.
   - Any outcome other than `clean` is **never** pushed.
7. **Isolation between PRs:** before moving to the next PR, ensure no rebase/merge is mid-flight (`git status`); if the user aborted, the working tree must be clean. Never carry an in-progress rebase across PRs.

**Failure isolation:** a failure on one PR (tests red, abort, diverged) must not stop the batch — record it and continue to the next PR.

### Step B4: Restore & report

1. Return to the starting point: `git checkout "$ORIG_REF"`.
2. Print the **Batch Summary** table:

```
## Batch Conflict Resolution Summary

Repo: <owner/repo>   Strategy: <rebase|merge>   Filter: <--user value or "all (bots excluded)">

| # | PR | Branch ← Base | Behind | Outcome | Conflicts resolved | Pushed | Notes |
|---|----|--------------|--------|---------|--------------------|--------|-------|
| 1 | #123 Add foo | feat/foo ← main | 7 | resolved | 3 files | no | had conflicts — left local for review |
| 2 | #124 Fix bar | fix/bar ← trunk | 5 | clean | — | yes | force-with-lease pushed |
| 3 | #125 Forky    | pr-125 ← master | 4 | tests-failed | 1 file | no | 2 tests red — left as-is |
| 4 | #126 Tidy     | tidy ← main | 0 | up-to-date (skipped) | — | — | — |
```

3. For every PR with outcome `resolved` or `clean`, include the per-PR **Conflict Resolution Summary** (Step 7 format) beneath the batch table.
4. **Push report:**
   - List the branches that **were pushed** (outcome `clean`) — these are now current with their base on the remote.
   - List the branches **left local** (`resolved` / `tests-failed`) that the user must review and push manually — rebased ones need `git push --force-with-lease`, merged ones can fast-forward push.
