---
name: remove-worktree
description: >
  Remove a git worktree and its local branch. Targeted mode accepts a ticket ID
  or auto-detects from the current directory. --auto mode sweeps every
  secondary worktree, joins PR state with the ticket status (Linear or Jira,
  resolved from the repo profile), and recommends removal for worktrees whose
  PR is merged and ticket is "Ready for QA" — always behind a human
  confirmation gate. --auto --aggressive drops the bar to clean-and-pushed
  (unmerged/open PRs included) and keeps the local branch — never removing a
  worktree with uncommitted or unpushed work, so no local work is lost.
---

# Remove Worktree — Clean Up Worktree and Local Branch

Clean up a worktree after development is done. Counterpart of `/add-worktree`.

**Usage**:

- `/remove-worktree [<ticket-id>]` — targeted mode. Removes one worktree.
- `/remove-worktree --auto` — sweep mode. Lists PR + ticket status across all worktrees, recommends safe-to-delete candidates (PR merged + ticket Ready for QA + clean tree), and gates on human confirmation before any deletion.
- `/remove-worktree --auto --aggressive` — same sweep, but the RECOMMENDED bar drops to **clean working tree + branch fully pushed to remote**: worktrees with an unmerged PR, no PR, or any ticket status all qualify. The two inviolable guards stay (see the principle below). Still gated on human confirmation.

> **Principle: never lose local work.** A worktree is removable only when its work is safely recoverable from the remote. Two guards enforce this in every mode and are *never* relaxed, even by `--aggressive`:
> 1. **Clean working tree** — uncommitted changes ⇒ KEEP.
> 2. **Branch fully pushed** — the local branch must exist on `origin` *and* have no commits ahead of `origin/<branch>` (no unpushed commits). A branch that was never pushed, or that is ahead of its remote, ⇒ KEEP.

Parse `$ARGUMENTS`:

- If it contains the token `--auto`, run **Auto mode (sweep)** below. Additionally set `AGGRESSIVE = true` if the token `--aggressive` is also present (`--aggressive` has no effect without `--auto`).
- Otherwise, treat the argument as an optional ticket ID and run **Targeted mode**.

---

## Auto mode (sweep)

### Step A0: Resolve project profile

Before touching any worktree, resolve the active repo's ticket-tracker so the sweep knows whether to call Linear or Jira and which branch prefix(es) count as ticket IDs.

1. Read `<repo-root>/.gogox-claude.yaml` (source of truth). It contains `platform`, `product`, `branch_prefix`, `ticket_system`.
2. If not found, fallback: read `~/.claude/commands/profiles/registry/{repo-name}.yaml` — same fields.
3. If neither exists, error: `Run /init-project to set up this repo.` and stop.
4. Read `~/.claude/commands/profiles/org.yaml` — needed for `jira.cloud_id`, prefix → ticket_system mapping, and the union of recognized prefixes.
5. Build `ALLOWED_PREFIXES`:
   - If `branch_prefix` is a concrete value (e.g. `CAF`, `CET`, `DAF`, `DET`): `ALLOWED_PREFIXES = [branch_prefix]`.
   - If `branch_prefix` is `auto`: `ALLOWED_PREFIXES = org.jira.prefixes ∪ org.linear.prefixes` (the full union of recognized prefixes).
6. Resolver function `resolve_ticket_system(prefix)`:
   - If profile `ticket_system` is `linear` or `jira`, return it (single-system repo).
   - If profile `ticket_system` is `auto`, look up `prefix` in `org.jira.prefixes` → return `jira`; else in `org.linear.prefixes` → return `linear`; else return `unknown`.
7. If `ticket_system` resolves to `jira`, capture `JIRA_CLOUD_ID = org.jira.cloud_id` for use in Step A2.

### Step A1: Enumerate worktrees

1. Run `git worktree list --porcelain`.
2. Drop the first entry — that is the main worktree, never removed by this skill.
3. For each remaining (secondary) entry, parse:
   - `path` — from the `worktree <path>` line.
   - `branch` — from the `branch refs/heads/<branch>` line. If absent (detached HEAD), the worktree is excluded from auto-removal because it cannot be mapped back to a ticket — list it under KEEP with reason `detached HEAD`.
   - `ticket-id` — first match of `(<ALLOWED_PREFIXES joined with |>)-\d+` in the branch name (e.g. for a Linear `ca-revamp` repo: `CAF-\d+`; for an `auto` repo: `(CET|DET|CAF|DAF)-\d+`). If none, list under KEEP with reason `no ticket ID in branch`.
   - `ticket-prefix` — the matched prefix (e.g. `CAF`). Pass through `resolve_ticket_system(prefix)` to get the per-worktree `ticket_system`. If it resolves to `unknown`, list under KEEP with reason `unknown ticket prefix: <prefix>`.
4. Store `MAIN_REPO_PATH` (path of the dropped main-worktree entry) for later session-return.
5. If there are zero secondary worktrees, print `No active worktrees.` and stop.

### Step A2: Fetch PR + ticket + dirty status (in parallel)

For every secondary worktree that has a ticket ID, gather the following — **in parallel across worktrees** (a single tool-call batch per worktree, but all worktrees fan out together):

1. **PR**:
   - `gh pr list --head "<branch>" --state all --json number,url,state,mergedAt --limit 1`
   - **`--state all` is mandatory.** `gh pr list` defaults to `--state open`, so a merged or closed PR comes back as an empty array and gets misreported as `No PR` — the exact failure that hides a merged PR from the RECOMMENDED bucket. Always pass `--state all`.
   - Capture `state` ∈ {`OPEN`, `MERGED`, `CLOSED`} plus `number` and `url`.
   - If the array is empty (genuinely no PR for the branch in any state), record `No PR`.
   - If `gh` exits non-zero, record `PR: unknown` and capture the stderr snippet.
2. **Ticket** — branch on the worktree's resolved `ticket_system`:
   - **`linear`**: call `mcp__claude_ai_Linear__get_issue` with `id: "<ticket-id>"`. Capture the workflow state name (e.g. `Ready for QA`, `In Review`, `Done`).
   - **`jira`**: call `mcp__claude_ai_Atlassian_Rovo__getJiraIssue` with `cloudId: JIRA_CLOUD_ID`, `issueIdOrKey: "<ticket-id>"`, `responseContentFormat: "markdown"`. Capture `fields.status.name` (e.g. `Ready for QA`, `In Review`, `Done`).
   - On failure or `unknown` ticket_system, record `Ticket: unknown` with the failure reason.
3. **Dirty status**: `git -C "<path>" status --porcelain`. Non-empty → `dirty (N files)`.
4. **Push state** (the "never lose local work" remote guard — skip if `branch` is empty / detached HEAD, which is already KEEP):
   - Remote ref exists? `git -C "<path>" rev-parse --verify --quiet "refs/remotes/origin/<branch>"`. Non-zero exit → no remote branch → `unpushed (no remote)`.
   - If it exists, count unpushed commits: `git -C "<path>" rev-list --count "refs/remotes/origin/<branch>..<branch>"`. `> 0` → `unpushed (N commits ahead)`. `0` → `pushed`.
   - These checks read local remote-tracking refs and do **not** fetch — so a branch merged-and-deleted on the remote but still tracked locally reads as `pushed` (correct: its commits are recoverable). A truly stale read only ever errs toward KEEP, never toward deletion.

### Step A3: Classify

For each worktree, assign exactly one bucket. The two work-loss guards (clean tree, branch fully pushed) apply in **both** modes; `--aggressive` only relaxes the PR-merged and ticket-status requirements.

**Default mode** — **RECOMMENDED** requires **all** of:
  - Working tree is clean, AND
  - Push state is `pushed` (branch on remote, no unpushed commits), AND
  - PR state is `MERGED`, AND
  - Ticket status matches `Ready for QA` (case-insensitive, trimmed) — applies to both Linear workflow states and Jira `fields.status.name`.

**Aggressive mode** (`AGGRESSIVE = true`) — **RECOMMENDED** requires only the two work-loss guards:
  - Working tree is clean, AND
  - Push state is `pushed`.
  - PR state and ticket status are ignored for the verdict (still shown in the report for context).

- **KEEP** — anything else. Compute a single short `reason` from the first applicable rule below (top-down). Rules 1–3 are the work-loss guards and fire in both modes; rules 4–6 are evaluated **only in default mode** (in aggressive mode a clean, pushed worktree is RECOMMENDED regardless):
  1. `dirty working tree` — uncommitted changes present.
  2. `unpushed: no remote branch` — branch never pushed to `origin`.
  3. `unpushed: N commit(s) ahead` — branch on remote but has local commits not yet pushed.
  4. `PR or ticket status unavailable` — any UNKNOWN signal.
  5. `PR not merged` — PR exists but state ≠ `MERGED`.
  6. `no PR` — no PR found for the branch.
  7. `<system> status: <name>` — PR merged but ticket status is not `Ready for QA` (use the resolved tracker name, e.g. `Linear status: In Review` or `Jira status: In Progress`).
  8. `no ticket ID in branch` / `unknown ticket prefix: <prefix>` / `detached HEAD` — set in Step A1 (KEEP in both modes; these cannot be mapped to a ticket and detached HEADs cannot be push-verified by branch).

### Step A4: Present the report

Build a **ticket URL** per worktree from the per-worktree resolved `ticket_system` plus the org constants captured in Step A0:

- `linear` → `{org.linear.base_url}/{TICKET-ID}` (e.g. `https://linear.app/gogox/issue/CAF-272`).
- `jira` → `{org.jira.base_url}/{TICKET-ID}` (e.g. `https://gogotech.atlassian.net/browse/CET-7911`).
- `unknown` → leave blank (`—`).

Build a **PR cell** as a single markdown link wrapping both the number and the state: `[#<num> <state>](<pr-url>)`. If no PR was found, show `No PR` (no link); if `gh` errored, show `unknown` (no link).

First print one header line stating the active mode so the user knows which bar is in effect:

- Default: `Mode: default — RECOMMENDED = PR merged + Ready for QA + clean + pushed.`
- Aggressive: `Mode: AGGRESSIVE — RECOMMENDED = clean + pushed (PR/ticket status ignored). Dirty or unpushed worktrees are always kept.`

Print two markdown tables (RECOMMENDED first, then KEEP, each sorted by ticket ID). Both use the same column layout so the user can eyeball both halves side-by-side. The `Pushed` column makes the work-loss guard visible:

```
RECOMMENDED for deletion (N)

| Ticket | Branch | Tracker | Status | PR | Clean | Pushed | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [CAF-272](<ticket-url>) | feat/CAF-272 | Linear | Ready for QA | [#418 merged](<pr-url>) | ✓ | ✓ | SAFE |
| ...

KEEP (M)

| Ticket | Branch | Tracker | Status | PR | Clean | Pushed | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [CET-7911](<ticket-url>) | feat/CET-7911 | Jira | In Review | [#503 open](<pr-url>) | ✓ | ✓ | PR not merged |
| [DAF-12](<ticket-url>) | bug/DAF-12 | Linear | Done | [#290 merged](<pr-url>) | ✗ (3 files) | ✓ | dirty working tree |
| [CAF-9](<ticket-url>) | feat/CAF-9 | Linear | In Progress | No PR | ✓ | ✗ (2 ahead) | unpushed: 2 commit(s) ahead |
| ...
```

- The `Ticket` cell is a markdown link to the ticket URL built above; if `ticket-id` is missing (detached HEAD or no prefix match), show the raw branch identifier with no link.
- `Tracker` column shows `Linear` / `Jira` / `—` (the resolved per-worktree value, not the profile-level setting — relevant when `ticket_system: auto` mixes both).
- `Clean` column: `✓` for clean, `✗ (N files)` for dirty.
- `Pushed` column: `✓` for `pushed`; `✗ (no remote)` for no remote branch; `✗ (N ahead)` for unpushed commits.
- If RECOMMENDED is empty, print `No safe-to-delete candidates found.` and stop — no human gate, no deletion.

### Step A5: Human gate (mandatory)

When at least one RECOMMENDED candidate exists, call `AskUserQuestion`:

- Question:
  - Default mode: `Delete all N recommended worktrees?`
  - Aggressive mode: `AGGRESSIVE: delete all N worktrees with unmerged/open PRs included? (All are clean + fully pushed — no local work will be lost.)`
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
   - **Re-verify the push guard at execution time** (state may have changed since the sweep): re-run the Step A2 push-state checks. If the branch is no longer `pushed` (now unpushed, or commits ahead of remote), skip with `Skipped: <ticket-id> — became unpushed mid-run`.
   - **Branch deletion is mode-dependent:**
     - *Default mode:* try `git branch -d` (safe delete). If it rejects the branch as not merged into the local tracking branch (e.g. local main hasn't pulled the merge commit), skip the branch delete only (the worktree is already removed) and report `Removed: <ticket-id> (<branch>) — local branch kept (not merged locally; 'git pull' then 'git branch -d <branch>')`.
     - *Aggressive mode:* **keep the local branch** — remove the worktree only, never delete the branch. The branch (and any open PR) stays intact so the work is trivially recoverable; the execution-time push guard already guarantees the commits are on `origin`. Report `Removed: <ticket-id> (<branch>) — local branch kept`.
3. After each item, print one line: `Removed: <ticket-id> (<branch>)` or `Skipped: <ticket-id> — <reason>`.

After the loop:

1. Run `git worktree prune` once.
2. Print a summary line: `Removed <N> worktree(s), skipped <M>.`
3. Append the standard tips:
   ```
   Tip: run `/list-worktrees` to verify. Remote branches were kept — delete via `git push origin --delete <branch>` after each PR is fully closed.
   ```
   In aggressive mode, also note that local branches were kept:
   ```
   Aggressive mode kept the local branches (worktrees only were removed). Re-checkout any branch with `git worktree add ../<ticket-id> <branch>` or `git switch <branch>`.
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

- **Never lose local work.** A worktree is removable only when both work-loss guards hold — clean working tree AND branch fully pushed to `origin` (no unpushed commits). These two guards apply in every mode and are never relaxed, including by `--aggressive`. Re-verify the push guard at execution time before each removal.
- Never force-remove or force-delete branches without explicit user confirmation. In **auto mode**, `--force` is never used for worktree removal — affected worktrees are skipped with a reason instead.
- Never delete remote branches — only clean up the local worktree (and, in default mode, the local branch).
- **Aggressive mode (`--auto --aggressive`)** relaxes only the PR-merged and ticket-status requirements; it removes the worktree but **keeps the local branch** intact. Dirty or unpushed worktrees are still kept.
- Always move the session out of a worktree before removing it.
- **Auto mode** must always present the human gate (`AskUserQuestion`) before any deletion. Empty RECOMMENDED list ⇒ stop without prompting.
- **Default auto mode** classifies a worktree as RECOMMENDED only when PR state, ticket status (Linear or Jira, resolved per worktree via Step A0), clean state, and pushed state are all known and all match. Any UNKNOWN ⇒ KEEP.
- If worktree removal fails, do not attempt to delete the branch.
- If branch deletion fails after worktree removal, report partial completion status.
