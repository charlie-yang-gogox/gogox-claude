---
name: remove-worktree
description: >
  Remove a git worktree and its local branch. Targeted mode accepts a ticket ID
  or auto-detects from the current directory. --auto mode sweeps every
  secondary worktree, joins PR state with the Linear ticket status, and
  recommends removal for worktrees whose PR is merged and ticket is
  "Ready for QA" — always behind a human confirmation gate.
---

# Remove Worktree — Clean Up Worktree and Local Branch

Clean up a worktree after development is done. Counterpart of `/add-worktree`.

**Usage**:

- `/remove-worktree [<ticket-id>]` — targeted mode. Removes one worktree.
- `/remove-worktree --auto` — sweep mode. Lists PR + ticket status across all worktrees, recommends safe-to-delete candidates, and gates on human confirmation before any deletion.

Parse `$ARGUMENTS`:

- If it contains the token `--auto`, run **Auto mode (sweep)** below.
- Otherwise, treat the argument as an optional ticket ID and run **Targeted mode**.

---

## Auto mode (sweep)

### Step A1: Enumerate worktrees

1. Run `git worktree list --porcelain`.
2. Drop the first entry — that is the main worktree, never removed by this skill.
3. For each remaining (secondary) entry, parse:
   - `path` — from the `worktree <path>` line.
   - `branch` — from the `branch refs/heads/<branch>` line. If absent (detached HEAD), the worktree is excluded from auto-removal because it cannot be mapped back to a ticket — list it under KEEP with reason `detached HEAD`.
   - `ticket-id` — first match of `[A-Z]+-\d+` in the branch name. If none, list under KEEP with reason `no ticket ID in branch`.
4. Store `MAIN_REPO_PATH` (path of the dropped main-worktree entry) for later session-return.
5. If there are zero secondary worktrees, print `No active worktrees.` and stop.

### Step A2: Fetch PR + Linear + dirty status (in parallel)

For every secondary worktree that has a ticket ID, gather the following — **in parallel across worktrees** (a single tool-call batch per worktree, but all worktrees fan out together):

1. **PR**:
   - `gh pr list --head "<branch>" --json number,url,state,mergedAt --limit 1`
   - Capture `state` ∈ {`OPEN`, `MERGED`, `CLOSED`} plus `number` and `url`.
   - If the array is empty, record `No PR`.
   - If `gh` exits non-zero, record `PR: unknown` and capture the stderr snippet.
2. **Linear ticket**:
   - Call `mcp__claude_ai_Linear__get_issue` with `id: "<ticket-id>"`.
   - Capture the ticket's current status name (Linear's workflow state name, e.g. `Ready for QA`, `In Review`, `Done`, `In Progress`).
   - If the call fails, record `Linear: unknown`.
3. **Dirty status**: `git -C "<path>" status --porcelain`. Non-empty → `dirty (N files)`.

### Step A3: Classify

For each worktree, assign exactly one bucket:

- **RECOMMENDED** — requires **all three** of:
  - PR state is `MERGED`, AND
  - Linear status matches `Ready for QA` (case-insensitive, trimmed), AND
  - Working tree is clean.
- **KEEP** — anything else. Compute a single short `reason` from the first applicable rule below (top-down):
  1. `PR or Linear status unavailable` — any UNKNOWN signal.
  2. `dirty working tree` — uncommitted changes present.
  3. `PR not merged` — PR exists but state ≠ `MERGED`.
  4. `no PR` — no PR found for the branch.
  5. `Linear status: <name>` — PR merged but Linear status is not `Ready for QA`.
  6. `no ticket ID in branch` / `detached HEAD` — set in Step A1.

### Step A4: Present the report

Print two sorted sections (RECOMMENDED first, then KEEP, each sorted by ticket ID). Format:

```
RECOMMENDED for deletion (N):
  <ticket-id>  <branch>            PR #<num> merged · Linear: Ready for QA · clean    → SAFE
  ...

KEEP (M):
  <ticket-id>  <branch>            PR #<num> <state>  · Linear: <status>     · <dirty?>  → <reason>
  ...
```

- Always include the PR URL (or "No PR") and the Linear status name.
- If RECOMMENDED is empty, print `No safe-to-delete candidates found.` and stop — no human gate, no deletion.

### Step A5: Human gate (mandatory)

When at least one RECOMMENDED candidate exists, call `AskUserQuestion`:

- Question: `Delete all N recommended worktrees?`
- Options:
  1. **Yes — delete all N** (Recommended)
  2. **Select subset** — opens a follow-up multi-select.
  3. **Abort**

If the user picks **Select subset**, immediately call a second `AskUserQuestion` with `multiSelect: true` whose options are the RECOMMENDED entries (label = `<ticket-id> (<branch>)`). The selected entries form the deletion list; deselected entries fall through to the skipped count.

If the user picks **Abort**, print `Aborted. No worktrees were removed.` and stop.

Never proceed to Step A6 without a positive selection from this gate.

### Step A6: Execute removals

For each worktree in the final deletion list, **in sequence** (git worktree state is shared — do not parallelize):

1. If the current session is inside this worktree (i.e. `pwd` is inside `<path>`), call `ExitWorktree` with `action: "keep"` before removing.
2. Run the **Targeted mode Step 4** logic (worktree-remove → safe branch-delete → prune). In auto mode:
   - Never use `--force` for worktree removal. Candidates were already filtered to clean; if state changed under us and the worktree is now dirty, skip with `Skipped: <ticket-id> — became dirty mid-run`.
   - Never force-delete the branch. If `git branch -d` rejects the branch as not merged into the local tracking branch (can happen when the local main hasn't pulled the merge commit yet), skip with `Skipped: <ticket-id> — branch not merged locally; run 'git pull' then 'git branch -d <branch>'`.
3. After each item, print one line: `Removed: <ticket-id> (<branch>)` or `Skipped: <ticket-id> — <reason>`.

After the loop:

1. Run `git worktree prune` once.
2. Print a summary line: `Removed <N> worktree(s), skipped <M>.`
3. Append the standard tips:
   ```
   Tip: run `/list-worktrees` to verify. Remote branches were kept — delete via `git push origin --delete <branch>` after each PR is fully closed.
   ```

---

## Targeted mode

### Step 1: Identify the worktree to remove

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

### Step 2: Pre-flight checks

1. Run `git -C "$WORKTREE_PATH" status --porcelain` to check for uncommitted changes.
   - If changes exist, warn the user and list the changed files.
   - Ask: proceed with force removal, or abort?
   - **Do not proceed without explicit confirmation.**

### Step 3: Move session back

1. If the current session is inside the worktree (entered via `EnterWorktree`), use `ExitWorktree` with `action: "keep"` to return to the main repo directory.
2. Otherwise, `cd "$MAIN_REPO_PATH"` to ensure the session is in the main repo.

### Step 4: Remove worktree and branch

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

---

## Rules

- Never force-remove or force-delete branches without explicit user confirmation. In **auto mode**, `--force` is never used — affected worktrees are skipped with a reason instead.
- Never delete remote branches — only clean up local worktree and local branch.
- Always move the session out of a worktree before removing it.
- **Auto mode** must always present the human gate (`AskUserQuestion`) before any deletion. Empty RECOMMENDED list ⇒ stop without prompting.
- **Auto mode** classifies a worktree as RECOMMENDED only when PR state, Linear status, and clean state are all known and all match. Any UNKNOWN ⇒ KEEP.
- If worktree removal fails, do not attempt to delete the branch.
- If branch deletion fails after worktree removal, report partial completion status.
