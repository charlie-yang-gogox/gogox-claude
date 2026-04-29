---
name: remove-worktree
description: >
  Remove a git worktree and its local branch. Auto-detects from current
  directory or accepts a ticket ID argument.
---

# Remove Worktree — Clean Up Worktree and Local Branch

Clean up a worktree after development is done. Counterpart of `/add-worktree`.

**Usage**: `/remove-worktree [<ticket-id>]`

- `<ticket-id>` — Linear ticket ID (e.g. `CAF-123`). Auto-detected from the current worktree when omitted.

Parse `$ARGUMENTS` to extract the optional ticket ID.

---

## Step 1: Identify the worktree to remove

- If a ticket ID is provided, derive:
  - `WORKTREE_PATH` = `../<ticket-id>`, resolved to absolute path.
  - Look up `WORKTREE_PATH` in `git worktree list --porcelain`. If not found:
    - Run `git worktree prune` and retry.
    - If still not found, report error and stop: the worktree does not exist.
  - Extract `BRANCH` from the matching `branch refs/heads/<BRANCH>` line in the porcelain output.
  - If no `branch` line exists (detached HEAD), set `BRANCH` to empty — branch deletion will be skipped in Step 4.
- If no ticket ID is provided:
  1. Run `pwd` to get the current directory.
  2. Run `git worktree list --porcelain` and match the `worktree <path>` entry whose resolved absolute path equals `pwd`.
  3. If the current directory is the main worktree (not a secondary one), report an error and stop:
     ```
     You are in the main worktree. Provide a ticket ID to specify which worktree to remove.
     ```
  4. Extract `WORKTREE_PATH` and `BRANCH` from the matched entry.
  5. If no `branch` line exists (detached HEAD), set `BRANCH` to empty.
- Store `MAIN_REPO_PATH` — the path of the first (main) worktree from `git worktree list`.

## Step 2: Pre-flight checks

1. Run `git -C "$WORKTREE_PATH" status --porcelain` to check for uncommitted changes.
   - If changes exist, warn the user and list the changed files.
   - Ask: proceed with force removal, or abort?
   - **Do not proceed without explicit confirmation.**

## Step 3: Move session back

1. If the current session is inside the worktree (entered via `EnterWorktree`), use `ExitWorktree` with `action: "keep"` to return to the main repo directory.
2. Otherwise, `cd "$MAIN_REPO_PATH"` to ensure the session is in the main repo.

## Step 4: Remove worktree and branch

1. `git worktree remove "$WORKTREE_PATH"` — remove the worktree.
   - If Step 2 confirmed force removal, use `git worktree remove --force "$WORKTREE_PATH"`.
   - If removal fails, show the error and stop. Do not proceed to branch deletion.
2. Delete the local branch (skip if `BRANCH` is empty — detached HEAD):
   - First try `git branch -d "$BRANCH"` (safe delete).
   - If it fails because the branch is not fully merged, warn the user and ask whether to force delete with `git branch -D "$BRANCH"`.
   - **Do not force-delete without explicit confirmation.**
3. `git worktree prune` — clean up stale worktree references.
4. Print confirmation:
   ```
   Worktree removed
   Path:   <WORKTREE_PATH> (deleted)
   Branch: <BRANCH> (deleted locally) — or "N/A (detached HEAD)" if no branch

   Tip: run `/list-worktrees` to verify. Remote branch was kept — delete via `git push origin --delete <branch>` after PR is merged.
   ```

## Rules

- Never force-remove without explicit user confirmation.
- Never delete remote branches — only clean up local worktree and local branch.
- Always move the session out of the worktree before removing it.
- If worktree removal fails, do not attempt to delete the branch.
- If branch deletion fails after worktree removal, report partial completion status.
