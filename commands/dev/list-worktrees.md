---
name: list-worktrees
description: >
  List all active worktrees with ticket info, PR links, and status.
---

# List Worktrees — Show All Active Worktrees

List all secondary worktrees with their Linear ticket link, PR link, and working tree status.

**Usage**: `/list-worktrees`

No arguments.

---

## Step 1: Gather worktree data

1. Run `git worktree list --porcelain` to get all worktrees.
2. Parse the output into a list of entries. Each entry has:
   - `path` — from the `worktree <path>` line
   - `branch` — from the `branch refs/heads/<branch>` line (may be absent if detached HEAD)
   - `HEAD` — from the `HEAD <sha>` line
3. Identify the **main worktree** (the first entry) and store its path. Skip it from the display list — only show secondary worktrees.
4. If no secondary worktrees exist, report "No active worktrees." and stop.

## Step 2: Enrich each worktree

For each secondary worktree, collect the following. Run all worktree enrichments **in parallel** (not sequentially) to minimize latency:

1. **Ticket ID**: extract `[A-Z]+-\d+` pattern from the branch name (e.g. `CAF-123` from `feat/CAF-123`).
2. **Linear link**: if ticket ID found, format as `https://linear.app/gogox/issue/<ticket-id>`.
3. **PR link and review status**: run `gh pr list --head "<branch>" --json url,number,state,reviewDecision --limit 1`.
   - If found, store the PR URL, state (open/merged/closed), and review decision (approved/changes_requested/review_required).
   - If not found, show "No PR".
4. **Dirty status**: run `git -C "<path>" status --porcelain` to check for uncommitted changes.
   - If output is non-empty, mark as "dirty" with file count.
   - If empty, mark as "clean".
5. **Commit delta**:
   - Ahead: `git log origin/trunk..<branch> --oneline | wc -l`
   - Behind: `git log <branch>..origin/trunk --oneline | wc -l`
   - If `origin/trunk` is not available, show "unknown (run git fetch)".
6. **Last activity**: run `git -C "<path>" log -1 --format='%ar'` to get the time of the last commit (e.g. "3 days ago").
7. **Current worktree**: compare the worktree path with `pwd` to determine if the user is currently inside this worktree.

## Step 3: Display results

Sort entries by last activity (most recent first). Print a summary for each worktree:

```
<ticket-id> (<branch>) [← you are here]
  Path:     <path>
  Linear:   <linear-link or "N/A">
  PR:       <pr-url> (<state>, <reviewDecision>) or "No PR"
  Status:   <clean or "X uncommitted changes">
  Commits:  <N> ahead, <M> behind trunk
  Activity: <last-commit-time, e.g. "3 days ago">
```

- Only show `[← you are here]` for the worktree matching the current directory.
- For PR review decision, show: `approved`, `changes requested`, `review required`, or omit if not available.

Separate each entry with a blank line.

At the end, show a total count: `<N> active worktree(s)`.

## Rules

- This is a read-only command — never modify any worktree, branch, or file.
- If `gh` CLI fails (e.g. not authenticated), show "PR: unknown (gh CLI error)" and continue.
- If a worktree has no branch (detached HEAD), show "detached" and skip Linear/PR lookup.
