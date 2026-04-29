---
name: add-worktree
description: >
  Create a git worktree at ../TICKET-ID with a branch based on latest trunk,
  then move the session into the new worktree. Platform-aware: runs the right
  dependency-install command and prints the right IDE hint per project.
---

# Add Worktree — Create Isolated Worktree for Ticket

Create a worktree for parallel ticket development. Use `/list-worktrees` to see all active worktrees, `/remove-worktree` to clean up when done.

**Usage**: `/add-worktree <ticket-id> [--type feat|fix|chore|test|ci]`

- `<ticket-id>` — Ticket ID (e.g. `CAF-123`, `CET-272`, `DET-89`). Ask if omitted.
- `--type` — Branch prefix. Inferred from ticket nature when omitted (default `feat`).

Parse `$ARGUMENTS` to extract the ticket ID and optional `--type` flag.

---

## Step 0: Resolve project profile

Before any other step, determine the active project profile so later steps know which dependency-install command to run and which IDE hint to print.

**Resolution order:**

1. **Repo self-describes** — read `<repo-root>/.gogox-claude.yaml` if present. Use its `platform` and `product` fields.
2. **Central mapping** — else, get repo basename via `basename "$(git rev-parse --show-toplevel)"` and look it up in `~/.claude/commands/profiles/repos.yaml` under `repos.<basename>`.
3. **Error** — if neither resolves, stop and tell the user:
   > Cannot resolve gogox project profile. Either add an entry for `<basename>` to `~/.claude/commands/profiles/repos.yaml`, or create `<repo>/.gogox-claude.yaml` with `platform:` and `product:`.

After resolution, read both profile files:

- `~/.claude/commands/profiles/platform/<platform>.yaml` — exposes `deps_install`, `test_cmd`, `format_cmd`, `ide_open_hint`.
- `~/.claude/commands/profiles/product/<product>.yaml` — exposes `branch_prefix`.

Hold these values in memory for use in later steps where you see `{deps_install}`, `{ide_open_hint}`, etc.

## Step 1: Parse arguments and infer branch type

- Extract ticket ID from `$ARGUMENTS`. Ask the user if not provided.
- Validate the ticket ID matches the pattern `[A-Z]+-\d+` (e.g. `CAF-123`). If invalid, show error and stop.
- If `--type` is provided, use that value directly.
- If `--type` is omitted, infer from the ticket's nature:
  - Bug fix / defect → `fix`
  - New feature / requirement → `feat`
  - Test-related → `test`
  - CI/CD → `ci`
  - Other → `chore`
  - When uncertain, default to `feat`
- Derive:
  - `BRANCH` = `<type>/<ticket-id>` (e.g. `feat/CAF-123`)
  - `WORKTREE_PATH` = `../<ticket-id>` (e.g. `../CAF-123`), resolved to absolute path

## Step 2: Pre-flight checks

1. Run `git worktree prune` to clean up stale worktree references.
2. Run `git status --porcelain` to check repo cleanliness.
   - If there are uncommitted changes, warn the user and ask whether to continue.
3. Check if the branch already exists locally (`git branch --list <BRANCH>`) and/or on remote (`git branch -r --list origin/<BRANCH>`).
   - If both local and remote exist: ask the user — reuse the local branch (it may already track remote), or abort?
   - If only local branch exists: ask the user — reuse the existing branch, or abort?
   - If only remote branch exists: ask the user — track the remote branch, or abort?
4. Check if `WORKTREE_PATH` is already in use: `git worktree list`
   - If the path is a registered worktree, ask the user: enter the existing worktree (skip to Step 4), recreate it (run `/remove-worktree` first then continue), or abort?
5. Check if `WORKTREE_PATH` already exists as a regular directory: `test -d "$WORKTREE_PATH"`
   - If it exists but is not a worktree, show error and stop. Do not overwrite.

## Step 3: Create worktree

1. `git fetch origin trunk` — fetch the latest trunk.
   - If fetch fails (e.g. network error), show the error and stop. Do not proceed with a stale `origin/trunk`.
2. `git worktree add -b "$BRANCH" "$WORKTREE_PATH" origin/trunk` — create the worktree.
   - If Step 2 chose to reuse an existing local branch, use `git worktree add "$WORKTREE_PATH" "$BRANCH"` (without `-b`).
   - If Step 2 chose to track a remote branch, use `git worktree add --track -b "$BRANCH" "$WORKTREE_PATH" "origin/$BRANCH"`.

## Step 4: Move session and setup

1. Use the `EnterWorktree` tool with `path` set to the absolute path of `WORKTREE_PATH` to move the session into the new worktree.
2. **Read project settings**: Check if `.claude/port-settings.json` exists in the worktree.
   - If it exists, parse it and log: `"Original project: <originalProjectPath>"`.
   - This is informational — makes the path available for subsequent `/port` or `/dev` commands in this session.
3. **Install dependencies** (only if `{deps_install}` is non-empty):
   - Run `{deps_install}` in the worktree.
   - If it fails, warn the user but do not abort — the worktree is still usable.
   - If `{deps_install}` is empty (e.g. native Android: Gradle auto-syncs in IDE), skip this step entirely.
4. Print confirmation:
   ```
   Worktree created
   Path:   <WORKTREE_PATH>
   Branch: <BRANCH> (based on latest trunk)
   Original project: <path from port-settings.json, or "not configured">
   Dependencies: <installed | failed — run {deps_install} manually | skipped (no install command for this platform)>

   Next: {ide_open_hint}
   ```

## Rules

- Never auto-stash or auto-commit — warn only.
- If creation fails, show the error and stay in the original directory.
- Always ask for user confirmation before any destructive action.
