---
name: resolve-conflict
description: >
  Merge/Rebase current branch onto trunk, resolve any merge conflicts,
  run tests until green, format, and commit. Single mode does NOT push.
  Platform-aware: uses the right test and format commands per project. With
  --batch, sweeps open GitHub PRs (optionally filtered by --user) and fans out
  one parallel subagent per PR — each works in the PR's worktree, merges onto
  its base, tests, and pushes only if clean (conflicted PRs are flagged for a
  human).
---

# Resolve Conflict — Rebase onto Trunk & Fix Conflicts

Pull latest trunk via merge/rebase, resolve conflicts, verify tests pass, format, and commit.

---

## Modes

| Mode | Trigger | Scope |
|------|---------|-------|
| **Single** (default) | no `--batch` | Resolves the **current branch** against `origin/trunk`. Runs Steps 0–8 below. |
| **Batch** | `--batch` | Sweeps open GitHub PRs (optionally filtered by `--user`) and fans out **one parallel subagent per PR**. Each subagent works in that PR's own git worktree (reused if it exists, created otherwise), merges/rebases onto the PR's base branch, runs tests, and **pushes only if the merge was clean**. Conflicted PRs are aborted and flagged for human review. See the **Batch Mode** section near the end. |

**Usage**:

```
/resolve-conflict [--rebase | --merge]
/resolve-conflict --batch [--user=<login> | --user=@me] [--limit=<N>] [--rebase | --merge] [--dry-run]
```

- `--rebase` (default) / `--merge` — strategy, passed through to every PR in batch mode.
- `--user=<login>` — batch only; restrict to PRs **created by** that GitHub login. `@me` resolves to the authenticated user. Omit to sweep all PRs (bots excluded).
- `--limit=<N>` — batch only; **concurrency cap** — at most **N** subagents run at once. **All** eligible PRs are still processed; they are worked through in waves of N. Omit for no explicit cap (the harness's own concurrency cap still applies).
- `--dry-run` — batch only; **read-only preview**. Lists every matching PR and probes mergeability **locally with git** (`git merge-tree`, an in-memory merge that reports conflicts without touching the working tree, index, or any branch). Performs **no** checkout, real merge/rebase, conflict resolution, test run, commit, or push — nothing is mutated, locally or remotely (a read-only `git fetch` of the refs is the only network call).

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
- **Single mode: never run `git rebase --abort`, `git merge --abort` nor `git cherry-pick --abort`** without asking the user first.
- If the merge/rebase hits more than 5 conflict rounds (single mode), pause and ask the user if they want to continue or abort.
- Do not modify files unrelated to the conflict resolution or test fixes.
- All conflict markers must be verified removed before continuing the merge/rebase.
- **Batch mode push policy:** push a PR branch **only** when it came through **clean** — the rebase/merge applied with **zero conflicts** and `{test_cmd}` is green. PRs that hit conflicts, or whose tests failed, are **never** pushed; they are flagged for human review.
- **Batch mode conflict handling:** the parallel subagents are non-interactive, so they do **not** attempt HITL conflict resolution. On the first conflict a subagent **aborts** its own merge/rebase (`--abort`) to leave the PR's worktree clean, then reports `conflicts`. This per-worktree auto-abort is the one sanctioned exception to the "never abort without asking" rule — it touches only that PR's isolated worktree, never the user's main worktree. Conflicted PRs are routed back to single-mode `/resolve-conflict` for a human.

---

## Batch Mode (`--batch`)

When `--batch` is present, **do not** run the single-branch flow against the current branch. Instead the **orchestrator** (this session) sweeps open GitHub PRs and fans out **one subagent per PR, in parallel**. Each subagent does its work inside that PR's own git worktree, so the orchestrator never switches the current branch and the subagents never collide (distinct branches → distinct worktrees).

### Step B0: Pre-flight (orchestrator)

1. Resolve the project profile exactly as in **Step 0** — capture `{test_cmd}` to hand to each subagent. **Dry-run:** skip — no tests are run.
2. Resolve the repo: `REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)`. If this fails (not a GitHub-backed repo), stop and tell the user.
3. Capture the repo root for worktree paths: `ROOT=$(git rev-parse --show-toplevel)`.

> No `/check-clean` and no branch-restore are needed: the orchestrator stays put on the current branch and never checks anything out — all mutation happens inside per-PR worktrees.

### Step B1: List & filter PRs

```bash
gh pr list --repo "$REPO" --state open \
  --json number,headRefName,baseRefName,author,isDraft,title,mergeable,mergeStateStatus,updatedAt \
  --limit 100
```

`mergeable` is `MERGEABLE` / `CONFLICTING` / `UNKNOWN`; `mergeStateStatus` is `CLEAN` / `BEHIND` / `DIRTY` / `BLOCKED` / `UNSTABLE` / `UNKNOWN`. Carry these along as a coarse conflict hint in the summary, but the authoritative conflict check is the local `git merge-tree` probe (used by `--dry-run` in Step B3, and the real rebase/merge in the live run).

Filter the list:

- **`--user=<login>` (created-by).** If provided, keep only PRs whose `author.login` matches `<login>` case-insensitively. `--user=@me` → resolve the login first via `gh api user --jq .login` and match that. If `--user` is omitted, keep all PRs.
- **Drop bots.** Always drop PRs whose `author.login` is `dependabot`, `renovate`, `github-actions`, or ends in `[bot]` (consistent with `/code-review`).

If the filtered list is empty, print `No matching open PRs in $REPO.` and stop.

### Step B2: Pre-filter behind PRs (orchestrator)

To avoid spawning a subagent for a PR that is already current, the orchestrator does a cheap read-only behind-check per surviving PR (updates only remote-tracking refs — no working-tree/branch mutation):

```bash
git fetch origin <baseRefName> <headRefName>
BEHIND=$(git rev-list --count "origin/<headRefName>..origin/<baseRefName>")
```

- `BEHIND == 0` → already up to date → **skip**, mark `up-to-date`. No subagent spawned.
- `BEHIND > 0` → **queue** the PR for the fan-out in Step B3.

> Note on fork PRs: `headRefName` may not exist on `origin`. If the fetch of `<headRefName>` fails, do not pre-filter — queue the PR and let its subagent resolve the head via `gh pr checkout` (it handles forks).

> **Dry-run:** still run the read-only `git fetch` + `git rev-list` to get `BEHIND`. The mergeability check is done in Step B3 with `git merge-tree`; **no subagents are spawned in dry-run**.

### Step B3: Fan out one subagent per PR (parallel)

> **Dry-run short-circuit.** If `--dry-run` was passed, do **not** spawn subagents, check out, run a real merge/rebase, resolve, test, commit, or push anything. Instead probe each PR's mergeability locally and print the read-only preview table below, then stop. Nothing is mutated.
>
> For each PR, run an **in-memory merge** of base and head — no checkout, no working-tree/index/branch change:
>
> ```bash
> # git 2.38+: --write-tree writes only loose objects to the object store
> git merge-tree --write-tree --name-only "origin/<baseRefName>" "origin/<headRefName>"
> # exit 0 → clean (no conflicts); exit 1 → conflicts (stdout lists the conflicted paths)
> ```
>
> If `git merge-tree --write-tree` is unavailable (git < 2.38), fall back to the legacy form and grep its output for conflict markers (`<<<<<<<`):
>
> ```bash
> git merge-tree "$(git merge-base origin/<baseRefName> origin/<headRefName>)" \
>   origin/<baseRefName> origin/<headRefName> | grep -q '^<<<<<<<' && echo CONFLICTS || echo CLEAN
> ```
>
> ```
> ## Batch Dry-Run — open PRs in <owner/repo>
>
> Strategy if run: <rebase|merge>   Filter: <--user value or "all (bots excluded)">
>
> | # | PR | Branch ← Base | Behind | git merge-tree | Conflicted files | Would do |
> |---|----|--------------|--------|----------------|-------------------|----------|
> | 1 | #123 Add foo | feat/foo ← main | 7 | conflicts | lib/a.dart, lib/b.dart | flag for human — no auto-resolve |
> | 2 | #124 Fix bar | fix/bar ← trunk | 5 | clean | — | merge + test, push if green (--force-with-lease) |
> | 3 | #125 Tidy    | tidy ← main | 0 | — | — | skip (already current) |
> ```
>
> The **Would do** column follows directly from the probe: `behind 0` ⇒ skip; clean merge-tree ⇒ would merge, test, and push if green; conflicts ⇒ would abort and flag for a human (the live run does not auto-resolve in batch). List the conflicted paths so the user sees the blast radius before committing to a live run.
>
> Caveat to note in the output: `git merge-tree` models a **merge**; with `--rebase` the exact conflict set can differ, but it is a reliable mergeability signal.

**Spawn one subagent per queued PR, in parallel**, passing each the PR number, `headRefName`, `baseRefName`, the strategy (`rebase`/`merge`, default rebase), `{test_cmd}`, `REPO`, and `ROOT`. Each subagent is self-contained and **must return a structured result** (outcome + details) for the summary.

**Concurrency.** Process **all** queued PRs, but run at most `--limit=<N>` subagents at a time (default: no explicit cap — issue all `Agent` calls in one message and let the harness's own cap queue the overflow). When `--limit` is set, dispatch in **waves**: issue N `Agent` calls in a single message, wait for that wave to finish, then issue the next N, until every queued PR has been processed. Never drop a PR for being over the limit — the cap throttles *how many run at once*, not *how many run*.

Give each subagent these instructions:

1. **Find or create the PR's worktree.**
   - List worktrees: `git -C "$ROOT" worktree list --porcelain`. If an entry's `branch` is `refs/heads/<headRefName>`, use that worktree's path. Work there.
   - Otherwise create one. Derive a path from the ticket id in the branch (`[A-Z]+-[0-9]+`, e.g. `CAF-668`) → `<ROOT>/../<TICKET-ID>`; fall back to a sanitized branch name if there is no ticket id. Then:
     ```bash
     git -C "$ROOT" fetch origin <headRefName> <baseRefName>
     git -C "$ROOT" worktree add <path> <headRefName>   # creates a local branch tracking origin/<headRefName>
     ```
     For a **fork** PR (head not on `origin`), instead `git worktree add <path> --detach` then `gh pr checkout <number> --repo "$REPO"` inside `<path>`.
   - If the chosen worktree has **uncommitted changes** (`git -C <path> status --porcelain` non-empty), do not touch it — return `worktree-dirty` and stop.
2. **Perform the merge/rebase** inside the worktree, against `origin/<baseRefName>`, using the passed strategy:
   ```bash
   git -C <path> fetch origin <baseRefName>
   git -C <path> <rebase|merge> origin/<baseRefName>
   ```
3. **On conflict:** do **not** attempt to resolve (non-interactive). Abort to leave the worktree clean and return `conflicts` with the conflicted file list (`git -C <path> diff --name-only --diff-filter=U` captured before aborting):
   ```bash
   git -C <path> <rebase|merge> --abort
   ```
4. **On clean merge:** run `{test_cmd}` in the worktree.
   - Tests **red** → return `tests-failed` (leave the worktree as-is for inspection; do not push).
   - Tests **green** → push, since clean + green is the push criterion:
     - **rebase** → `git -C <path> push --force-with-lease origin <headRefName>` (history rewritten; `--force-with-lease`, never `--force`).
     - **merge** → `git -C <path> push origin <headRefName>` (fast-forward).
     - Return `pushed` on success, or `push-failed` with the error.
5. Return a structured result: `{ pr, branch, base, outcome, conflicted_files?, test_summary?, push_error?, worktree_path, worktree_created: bool }`.

**Failure isolation:** a subagent that errors or returns a failure outcome must not affect the others — the orchestrator records its result and moves on.

### Step B4: Collect & report (orchestrator)

Wait for all subagents to finish, then print the **Batch Summary** table:

```
## Batch Summary

Repo: <owner/repo>   Strategy: <rebase|merge>   Filter: <--user value or "all (bots excluded)">

| # | PR | Branch ← Base | Behind | Outcome | Pushed | Worktree | Notes |
|---|----|--------------|--------|---------|--------|----------|-------|
| 1 | #123 Add foo | feat/foo ← main | 7 | conflicts | no | ../CAF-123 (reused) | 3 files conflict — run /resolve-conflict there |
| 2 | #124 Fix bar | fix/bar ← trunk | 5 | pushed | yes | ../CAF-124 (created) | clean + tests green, force-with-lease |
| 3 | #125 Forky    | pr-125 ← master | 4 | tests-failed | no | ../pr-125 (created) | 2 tests red — left as-is |
| 4 | #126 Tidy     | tidy ← main | 0 | up-to-date | — | — | skipped (orchestrator pre-filter) |
```

Outcome values: `pushed`, `tests-failed`, `conflicts`, `worktree-dirty`, `up-to-date`, `push-failed`, `error`.

Then print:
- **Pushed:** branches now current with their base on the remote (outcome `pushed`).
- **Needs a human:** `conflicts` / `tests-failed` / `worktree-dirty` PRs, with the worktree path and the suggested next step — for conflicts, `cd <path> && /resolve-conflict` (single mode) to resolve interactively.
- **Worktrees created:** list the worktrees this run created (vs reused) so the user can `/remove-worktree` them when done.
