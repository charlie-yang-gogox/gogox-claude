---
name: add-worktree
description: >
  Create a git worktree at ../TICKET-ID with a branch based on the latest default branch,
  then move the session into the new worktree. Platform-aware: runs the right
  dependency-install command and prints the right IDE hint per project.
---

# Add Worktree — Create Isolated Worktree for Ticket

Create a worktree for parallel ticket development. Use `/list-worktrees` to see all active worktrees, `/remove-worktree` to clean up when done.

**Usage**: `/add-worktree <ticket-id> [--type feat|fix|chore|test|ci]`

- `<ticket-id>` — Ticket ID (e.g. `<PREFIX>-<n>` — a CAF / CET / DET key). Ask if omitted.
- `--type` — Branch prefix. Inferred from ticket nature when omitted (default `feat`).

Parse `$ARGUMENTS` to extract the ticket ID and optional `--type` flag.

---

## Step 0: Resolve project profile

Before any other step, determine the active project profile so later steps know which dependency-install command to run and which IDE hint to print.

**Resolution order:**

1. **Repo self-describes** — read `<repo-root>/.gogox-claude.yaml` if present. Use its `platform`, `product`, and `branch_prefix` fields.
2. **Central mapping** — else, read `~/.claude/commands/profiles/registry/$(basename "$(git rev-parse --show-toplevel)").yaml` for the same fields.
3. **Error** — if neither resolves, stop and tell the user:
   > Cannot resolve gogox project profile. Either add `~/.claude/commands/profiles/registry/<basename>.yaml`, or create `<repo>/.gogox-claude.yaml` with `platform:` and `product:`.

After resolution, read the platform profile:

- `~/.claude/commands/profiles/platform/<platform>.yaml` — exposes `deps_install`, `test_cmd`, `format_cmd`, `ide_open_hint`.

Hold these values in memory for use in later steps where you see `{deps_install}`, `{ide_open_hint}`, etc.

## Step 1: Parse arguments and infer branch type

- Extract ticket ID from `$ARGUMENTS`. Ask the user if not provided.
- Validate the ticket ID matches the pattern `[A-Z]+-\d+` (e.g. `<PREFIX>-<n>`). If invalid, show error and stop.
- If `--type` is provided, use that value directly.
- If `--type` is omitted, infer from the ticket's nature:
  - Bug fix / defect → `fix`
  - New feature / requirement → `feat`
  - Test-related → `test`
  - CI/CD → `ci`
  - Other → `chore`
  - When uncertain, default to `feat`
- Derive:
  - `BRANCH` = `<type>/<ticket-id>` (e.g. `feat/<PREFIX>-<n>`)
  - `WORKTREE_PATH` = `../<ticket-id>` (e.g. `../<PREFIX>-<n>`), resolved to absolute path

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

0. Resolve the repo's default branch (works on any repo, not just trunk-default):
   ```bash
   source "$HOME/.claude/lib/dev-mode.sh"
   DEFAULT_BRANCH=$(default_branch)   # e.g. trunk (flutter) or main (gogox-claude)
   ```
1. `git fetch origin "$DEFAULT_BRANCH"` — fetch the latest default branch.
   - If fetch fails (e.g. network error), show the error and stop. Do not proceed with a stale `origin/$DEFAULT_BRANCH`.
2. Create the worktree. Set `FRESH_BRANCH=1` for the default case (a brand-new branch cut off
   `origin/$DEFAULT_BRANCH` this run); set `FRESH_BRANCH=0` on the two reuse paths below (an existing /
   remote branch legitimately carries its own commits beyond trunk, so the Step 3 clean-trunk assertion
   does not apply to them):
   - Default (new branch): `FRESH_BRANCH=1; git worktree add -b "$BRANCH" "$WORKTREE_PATH" "origin/$DEFAULT_BRANCH"`.
   - If Step 2 chose to reuse an existing local branch: `FRESH_BRANCH=0; git worktree add "$WORKTREE_PATH" "$BRANCH"` (without `-b`).
   - If Step 2 chose to track a remote branch: `FRESH_BRANCH=0; git worktree add --track -b "$BRANCH" "$WORKTREE_PATH" "origin/$BRANCH"`.
3. **Assert the new worktree is based on clean trunk** (worktree/branch isolation). Under a
   parallel fan-out, a per-ticket worktree's HEAD can leak to a *sibling* ticket's commit instead of the
   freshly-fetched default-branch tip; a contaminated base silently poisons every downstream diff baseline
   (e.g. `/ui-tweak`'s `base_ref`), which is exactly how a ticket can be falsely closed as a no-op. For the
   fresh-branch case, verify the resulting HEAD equals the tip we just fetched and STOP loudly on
   mismatch — never proceed on a contaminated base:
   ```bash
   # Only assert for the FRESH branch case (created off origin/$DEFAULT_BRANCH this run).
   if [ "${FRESH_BRANCH:-1}" = "1" ]; then
     EXPECTED_TIP=$(git rev-parse "origin/$DEFAULT_BRANCH")
     ACTUAL_HEAD=$(git -C "$WORKTREE_PATH" rev-parse HEAD)
     if [ "$ACTUAL_HEAD" != "$EXPECTED_TIP" ]; then
       echo "FAIL: worktree $WORKTREE_PATH HEAD ($ACTUAL_HEAD) != fresh origin/$DEFAULT_BRANCH tip ($EXPECTED_TIP)." >&2
       echo "Cross-worktree branch contamination — refusing to proceed on a non-trunk base." >&2
       echo "Remove the worktree (git worktree remove --force $WORKTREE_PATH), re-fetch, and retry." >&2
       exit 1
     fi
   fi
   ```

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
   Branch: <BRANCH> (based on latest $DEFAULT_BRANCH)
   Original project: <path from port-settings.json, or "not configured">
   Dependencies: <installed | failed — run {deps_install} manually | skipped (no install command for this platform)>

   Next: {ide_open_hint}
   ```

## Rules

- Never auto-stash or auto-commit — warn only.
- If creation fails, show the error and stay in the original directory.
- Always ask for user confirmation before any destructive action.
