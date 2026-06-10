---
name: clean-branches
description: >
  Pure-git local branch hygiene for the current repo: delete local branches
  already merged into trunk (including branchless ones with no worktree), with
  optional squash-merge detection and git gc. Judged purely by git — never
  PR/ticket state. Local-only, dry-run preview + confirmation, never
  force-deletes without opt-in. Branches checked out in a worktree are left to
  /remove-worktree (single responsibility: this command touches branches, not
  worktrees).
---

# Clean Branches — Prune Local Branches Merged into Trunk

Local feature branches pile up long after their work is merged. This deletes
the ones already merged into **trunk**, judged purely by git (is the branch an
ancestor of trunk?) — no PR or ticket lookup.

**Scope boundary**: this command deletes **branches**, not worktrees. A merged
branch that is currently checked out in a worktree cannot be deleted with
`git branch -d` anyway — those are listed separately and delegated to
`/remove-worktree` (or `/remove-worktree --auto`). Keeps each command single-
purpose: `/clean-branches` = branches, `/remove-worktree` = worktrees.

**Interactive by default**: resolve trunk → scan → dry-run preview → confirm →
delete. Nothing is removed without explicit confirmation.

**Arguments** (all optional):
- `--yes` — skip prompts; delete all SAFE (merged) branches. Never force-deletes;
  squash-merged-only branches are skipped unless `--force-squash` is also passed.
- `--force-squash` — also delete branches detected as squash-merged via `gh`
  (their commits are not trunk ancestors, so `git branch -d` would refuse).
  Requires `gh`. Off by default.
- `--gc` — also run `git gc` at the end (otherwise offered in the Step 4 gate).

---

## Step 0: Preconditions

- Must be inside a git repo (`git rev-parse --git-dir`), else stop.
- **Resolve `TRUNK`**: the repo profile's trunk if available; else
  `git symbolic-ref --quiet refs/remotes/origin/HEAD` (strip `origin/`); else
  the first of `trunk` / `main` / `master` that exists.
- **Protected branches** (never delete): `TRUNK`, the current branch, and any
  of `main` / `master` / `develop` / `trunk` present.

## Step 1: Refresh

```bash
git fetch --prune origin        # accurate merge detection; prunes refs for deleted remote branches (local-only)
```

## Step 2: Classify branches

```bash
git branch --merged "origin/$TRUNK" --format '%(refname:short)'   # commits are trunk ancestors
```
Drop protected + current from that list → **SAFE** (deletable with
`git branch -d`). For each remaining (non-merged) local branch:
- **`gone`** — `git branch -vv` shows `: gone]` (upstream pruned; likely
  squash/rebase-merged on the remote).
- **`squash-merged`** — only if `gh` is available AND `--force-squash`:
  `gh pr list --head "<branch>" --state merged --json number --limit 1` returns
  a PR. Removable only with `git branch -D`.
- else **`unmerged`** → KEEP, never offered.

## Step 3: Flag worktree-bound branches

```bash
git worktree list --porcelain     # branch checked out in each worktree
```
Any SAFE/squash branch that is checked out in a worktree **cannot** be deleted
here (git refuses, and removing it is a worktree operation). Move it to a
**DELEGATE** bucket with the hint `checked out in <path> → /remove-worktree`.
The deletable set = SAFE/squash branches **not** bound to a worktree.

## Step 4: Dry-run preview

```
Repo: gogox-client-flutter   trunk: trunk

Merged branches — safe delete (2)
  bug/CAF-301
  chore/cleanup-logs

Squash-merged — needs --force-squash (1)
  feat/CAF-318   (remote gone)

Bound to a worktree → use /remove-worktree (1)
  feat/CAF-272   checked out in ../CAF-272

KEEP (1)
  feat/CAF-999   not merged

Repo objects: .git = 1.8 GB   → git gc available
```
If the deletable set is empty (and no gc requested), print `Nothing to clean.`
and stop.

## Step 5: Confirm

If `--yes`, select all SAFE branches (+ squash-merged only if `--force-squash`)
and proceed. Otherwise **AskUserQuestion** (`multiSelect`), only including rows
that exist:
- `Delete N merged branches`
- `Force-delete K squash-merged branches (git branch -D)` — only with
  `--force-squash`; never preselected.
- `Run git gc`

Selecting nothing → stop, delete nothing. Worktree-bound branches are never
selectable here.

## Step 6: Execute

Logging each action; on per-item failure warn and continue:
1. `git branch -d "<branch>"` for each selected SAFE branch (safe delete). For
   confirmed squash-merged selections only, `git branch -D "<branch>"`.
   - If `-d` refuses ("not fully merged" — local trunk is behind the merge
     commit), skip with: `Skipped <branch> — not merged into local trunk; run 'git checkout TRUNK && git pull' then re-run`.
2. If `git gc` was selected (or `--gc`): `git gc`.

## Step 7: Report

```
Clean-branches complete.

  Deleted: bug/CAF-301, chore/cleanup-logs
  Skipped: feat/CAF-318 (needs --force-squash), feat/CAF-272 (worktree — use /remove-worktree)
  Repo objects: 1.8 GB → 1.1 GB (git gc)

  Remote branches were NOT touched. Delete with:
    git push origin --delete <branch>   (only after the PR is fully closed)
```

---

## Rules

- **Local only.** Never delete or modify remote branches. `git fetch --prune`
  only prunes local remote-tracking refs, not the remote.
- **Never delete protected branches** (`TRUNK`, current, main/master/develop).
- **Never force-delete (`-D`) without explicit opt-in** (`--force-squash` plus an
  explicit selection). Default is always the safe `git branch -d`.
- **Never touch worktrees.** Branches checked out in a worktree are delegated to
  `/remove-worktree` — this command's scope is branches only.
- Merged-detection is against **`origin/TRUNK`**, so an un-pulled local trunk
  does not hide freshly-merged branches; the safe `git branch -d` still
  cross-checks local-merge and skips with guidance if local trunk is behind.
- Always show the dry-run preview (including KEEP + DELEGATE) before deleting,
  unless `--yes` is passed.
- If nothing is deletable, report `Nothing to clean.` and stop.
- Pairs with `/remove-worktree` (worktree lifecycle) and `/list-worktrees`.
