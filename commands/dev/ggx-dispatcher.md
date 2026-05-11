---
name: ggx-dispatcher
description: >
  Manual batch worker for actionable Linear tickets. Sweeps the cwd repo's
  team for `ready-to-port` and `ready-to-dev` tickets, race-locks them, and
  fans out parallel `/port:ff --auto` / `/dev:ff --auto` agents. Single-repo,
  cwd-driven; user-invoked from a Claude session opened in the target repo
  on its default branch.
Prerequisite: >
  - Linear MCP authenticated; gh CLI authenticated.
  - cwd is the main worktree of a registered Linear repo, on default branch,
    clean tree (use `--test` to skip branch/clean checks).
  - Environment variable USER_NAME set.
  - For `branch_prefix: auto` repos: pass `--team:<KEY>` (e.g. `--team:CET`).
---

# /ggx-dispatcher — manual batch worker

Find every actionable ticket in the cwd repo's Linear team and dispatch each through `/port:ff --auto` or `/dev:ff --auto` in parallel. Each spawned agent runs in `run_in_background: true`; the dispatcher waits for all to complete before posting fallbacks and emitting a summary.

**Usage**: `/ggx-dispatcher [--dry-run] [--test] [--max-parallel:<N>] [--team:<KEY>]`

- `--dry-run` — Print the planned dispatch and STOP. No Linear writes, no agent spawn.
- `--test` — Skip the default-branch + clean-tree pre-flight checks (still requires main worktree, gh auth, and lockfile).
- `--max-parallel:<N>` — Concurrent dispatch cap. Default `10`, hard cap `20`. Out of range → abort.
- `--team:<KEY>` — Required when the cwd repo's `branch_prefix` is `auto`. Allowed (but must equal `branch_prefix`) when `branch_prefix` is concrete.


---

## Execution rules

- **No `AskUserQuestion`.** Dispatcher never prompts. Every gate either auto-resolves or aborts with a paste-ready remediation message.
- **All MCP tool calls use `mcp__claude_ai_Linear__*`.** Never the legacy `mcp__linear-server__*`.
- **stdout is the audit trail.** Every per-ticket lock attempt prints `<ticket>: locked ✓` or `<ticket>: failed (<reason>)`. If MCP outage breaks the recovery chain, the user has the terminal scrollback to fix manually via Linear UI. Cron usage (where stdout is invisible) is explicitly NOT supported.
- **Lockfile.** Step 1 acquires `claude-reports/dispatcher/.lock`; every exit path (success, abort, MCP error) releases it.

---

## Step 0: Resolve profile

1. Read `~/.claude/commands/profiles/registry/$(basename "$(git rev-parse --show-toplevel)").yaml`. Fall back to `<repo-root>/.gogox-claude.yaml` if the registry has no entry.
2. Validate `ticket_system`. If not `linear`, STOP with:
   > `/ggx-dispatcher supports ticket_system: linear only. This repo is configured for: <value>.`
3. Resolve `team_key`:
   - If `branch_prefix` is concrete (e.g. `CAF`, `CET`):
     - If `--team:<KEY>` was passed, normalize via `tr '[:lower:]' '[:upper:]'`. Mismatch with `branch_prefix` (case-insensitive) → STOP with:
       > `--team:<KEY> mismatch — flag value '<KEY>' does not equal repo branch_prefix '<prefix>'. Drop the flag or align it.`
     - Otherwise `team_key = branch_prefix`.
   - If `branch_prefix == auto`:
     - `--team:<KEY>` missing → STOP with:
       > `This repo uses branch_prefix: auto. Pass --team:<KEY> (e.g. --team:CET) to scope dispatch to one team.`
     - With flag: normalize via `tr '[:lower:]' '[:upper:]'` and validate against the union of known prefixes in `~/.claude/commands/profiles/org.yaml`. Unknown → STOP with:
       > `--team:<KEY> '<KEY>' is not a known prefix in org.yaml.`
     - `team_key = upper(KEY)`.
4. Resolve `default_branch`:
   ```bash
   default_branch=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||')
   if [ -z "$default_branch" ]; then
     default_branch=$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name' 2>/dev/null)
   fi
   [ -z "$default_branch" ] && abort "Cannot detect default branch. Run: git remote set-head origin -a"
   ```

---

## Step 1: Pre-flight

1. **Lockfile.** Acquire `claude-reports/dispatcher/.lock`:
   ```bash
   mkdir -p claude-reports/dispatcher
   LOCK=claude-reports/dispatcher/.lock
   if [ -f "$LOCK" ]; then
     LOCK_TS=$(awk -F= '/^ts=/{print $2}' "$LOCK")
     LOCK_PID=$(awk -F= '/^pid=/{print $2}' "$LOCK")
     LOCK_AGE=$(( $(date -u +%s) - $(date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$LOCK_TS" +%s 2>/dev/null || echo 0) ))
     if [ "$LOCK_AGE" -lt 600 ]; then
       abort "/ggx-dispatcher already running (PID $LOCK_PID, started $LOCK_TS). If stuck, rm $LOCK and retry."
     fi
   fi
   printf 'pid=%d\nts=%s\n' $$ "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$LOCK"
   ```
   Stale (> 10 min) lockfiles are overwritten. Every exit path below removes `$LOCK`.

2. **Worktree guard.** dispatcher must run from the main repo, not a linked worktree:
   ```bash
   if [ "$(git rev-parse --git-common-dir)" != "$(git rev-parse --git-dir)" ]; then
     MAIN=$(git rev-parse --git-common-dir | sed 's|/\.git$||')
     abort "/ggx-dispatcher must run from the main repo, not a worktree.
            Run: cd \"$MAIN\"
            then re-invoke /ggx-dispatcher."
   fi
   ```

3. **Branch + clean checks** (skipped when `--test`):
   - On default branch? `[ "$(git branch --show-current)" = "$default_branch" ]` else STOP with:
     > `Switch to <default_branch> first: git checkout <default_branch> && git pull`
   - Clean tree? `[ -z "$(git status --porcelain)" ]` else STOP with:
     > `Working tree is dirty. Stash or commit first: git stash`

4. **Worktree prune.** `git worktree prune` (silent).

5. **gh auth.** `gh auth status >/dev/null 2>&1` else STOP with:
   > `gh CLI is not authenticated. Run: gh auth login`

6. **Open PR count** (informational, non-blocking):
   ```bash
   PR_COUNT=$(gh pr list --author "@me" --state open --json number | jq length)
   echo "Pre-flight: $PR_COUNT open PRs authored by me (informational only)."
   ```

---

## Step 2: Find tickets

Run **four** independent `mcp__claude_ai_Linear__list_issues` queries — `label` and `state` are singular per the MCP schema:

| # | label                         | state          | team       | assignee | Catches                                  |
|---|-------------------------------|----------------|------------|----------|------------------------------------------|
| Q1 | `ready-to-port`              | `unstarted`    | `<team_key>` | `me`     | Fresh port tickets (`To-do`, `Reopened`) |
| Q2 | `dispatcher-port-in-flight`  | `In Progress`  | `<team_key>` | `me`     | Crash-recovery: port mid-pipeline        |
| Q3 | `ready-to-dev`               | `unstarted`    | `<team_key>` | `me`     | Fresh dev tickets (`To-do`, `Reopened`)  |
| Q4 | `dispatcher-dev-in-flight`   | `In Progress`  | `<team_key>` | `me`     | Crash-recovery: dev mid-pipeline         |

Selection model (Plan X, May 2026):

- **Fresh dispatch** = ticket has `ready-to-port` / `ready-to-dev`. At lock time the dispatcher swaps the actionable label for the corresponding `dispatcher-*-in-flight` label (§4.1).
- **Crash recovery** = ticket has `dispatcher-port-in-flight` / `dispatcher-dev-in-flight` left over from a prior run that didn't reach ship. Q2/Q4 catch these. `/port:ship` and `/dev:ship` remove the in-flight label only on full success, so its presence is a hard signal that "dispatcher claimed this and didn't finish."
- The two-label split (port vs dev) means the dispatcher can route to `/port:ff` vs `/dev:ff` from the Linear signal alone, without re-deriving the pipeline type from the worktree.
- Q1/Q3 use state type `unstarted` so renamed statuses (e.g. `Reopened`, `Up Next`) still resolve. Q2/Q4 use state name `In Progress` exactly — `In Review` / `Ready for QA` are post-work and must NOT be re-dispatched. (If a team renames `In Progress`, Q2/Q4 silently miss; verify with `mcp__claude_ai_Linear__list_issue_statuses` on onboarding.)

States explicitly EXCLUDED: `Triage`, `Backlog`, `In Review`, `Ready for QA`, `Done`, `Canceled`, `Duplicate`.

### 2.1 Dedup

Merge Q1-Q4 results into one list keyed by ticket id. When the same id appears in multiple queries, **union the `labels[]` arrays** so the dedup result preserves which labels (actionable or in-flight) are present.

Each surviving entry is tagged with its **selection lane**:

- `fresh-port` — picked up by Q1 (`ready-to-port` present).
- `fresh-dev` — picked up by Q3 (`ready-to-dev` present).
- `recovery-port` — picked up by Q2 (`dispatcher-port-in-flight` present).
- `recovery-dev` — picked up by Q4 (`dispatcher-dev-in-flight` present).

The lane controls (a) which command Step 5 dispatches and (b) which lock transitions Step 4.1 performs.

### 2.2 Conflict checks

Three malformed shapes — drop the ticket, post a comment, remove all conflicting labels:

a. **Both fresh labels**: `ready-to-port` AND `ready-to-dev` present →
   > `Dispatcher: skipped — ticket has both ready-to-port and ready-to-dev labels. Cannot determine intent. Re-add the correct single label to retry.`

b. **Fresh + in-flight on same lane** (e.g. `ready-to-dev` + `dispatcher-dev-in-flight`) →
   > `Dispatcher: skipped — ticket has both ready-to-dev and dispatcher-dev-in-flight labels. Lock state inconsistent (prior run was interrupted mid-lock?). Remove one and re-add the right one to retry.`

c. **Both in-flight labels**: `dispatcher-port-in-flight` AND `dispatcher-dev-in-flight` →
   > `Dispatcher: skipped — ticket has both port and dev in-flight labels. Cannot route. Inspect and remove one manually.`

All three shapes drop the ticket from the batch.

### 2.3 Priority sort + cap

Sort by priority (`urgent` > `high` > `medium` > `low` > `none`), then by `createdAt` ascending.

Cap to `--max-parallel`. Validate the value: `1 <= N <= 20`. Out of range → abort. Default `10`.

If the surviving list is empty:

```
No actionable tickets in team <team_key>. Nothing to dispatch.
```

Release lock and STOP cleanly.

---

## Step 3: Anti-duplicate (per ticket)

Run sequentially over the surviving list:

1. **PR check** (word-boundary regex prevents `CAF-27` matching `CAF-279`):
   ```bash
   gh pr list --state open --json title,headRefName \
     | jq -r '.[] | "\(.title) \(.headRefName)"' \
     | grep -iE "(^|[^0-9])$TICKET_ID($|[^0-9])"
   ```
   On match: `save_issue` to remove **every dispatcher-tracked label** present on the ticket (the actionable `ready-to-*` AND any leftover `dispatcher-*-in-flight`), post `Dispatcher: skipped — open PR already exists for this ticket.`, drop from batch.

   Removing the in-flight label here is what closes the loop for two known recovery shapes: (i) `/dev:ship` / `/port:ship` crashed after opening the PR but before removing the label; (ii) the user took the worktree over manually and opened a PR by hand without driving `/dev:ship`. In both cases the PR is the canonical "done" signal — the in-flight label is stale and must not feed Q2/Q4 next time.

2. **Remote branch check** (uses `ls-remote` — local refs may be stale):
   ```bash
   git ls-remote --heads origin \
     | grep -iE "[/-]$TICKET_ID($|[^0-9])"
   ```
   On match, behavior depends on the selection lane recorded in §2.1:
   - `fresh-port` (Q1): drop + remove the `ready-to-port` label + comment `Dispatcher: skipped — branch already exists for this ticket.` (port should not run against a pre-existing remote branch.)
   - `fresh-dev` (Q3): **proceed** (port created the branch; dev is expected to continue on it).
   - `recovery-port` / `recovery-dev` (Q2/Q4): **proceed** — the in-flight label IS the resume signal; the existing branch is exactly what we expect to find.

If the surviving list becomes empty → STOP cleanly (release lock).

---

## Step 3.5: Port config pre-check

If the surviving list contains **any** ticket with `ready-to-port`, verify the origin project path is set up before any locking begins. Skipping this means a misconfigured machine produces N zombie tickets (locked + worktree built + agent stops on missing config).

```bash
HAS_PORT=$(printf '%s\n' "$SURVIVORS" | jq -r '.[] | select(.labels[]? == "ready-to-port") | .id' | head -1)
if [ -n "$HAS_PORT" ]; then
  CFG="<repo-root>/.claude/port-settings.json"
  [ -f "$CFG" ] || abort "Port config missing: $CFG. Run: /port:start --ticket:<any-port-ticket> once interactively to set originalProjectPath, then re-invoke /ggx-dispatcher."
  ORIGIN=$(jq -r '.originalProjectPath // empty' "$CFG")
  ORIGIN_EXPANDED=$(eval echo "$ORIGIN")
  [ -n "$ORIGIN" ] || abort "Port config has empty originalProjectPath: $CFG. Fix and re-invoke."
  [ -d "$ORIGIN_EXPANDED" ] || abort "Port config originalProjectPath does not exist on disk: $ORIGIN_EXPANDED. Update $CFG and re-invoke."
fi
```

If the cwd repo only has `ready-to-dev` survivors (no port), this check is skipped — `/dev:ff` does not need origin path.

This check is read-only against `port-settings.json`. No mutations. Lockfile released on abort.

---

## Step 4: Race-lock

Apply the init protocol to every surviving ticket BEFORE spawning any agent.

### 4.0 Dry-run gate

If `--dry-run` is set: print the dispatch table (§4.3 format, with `status` column reading `planned` instead of `locked ✓`) and STOP. **No mutations from Step 4 onward.** Release lock.

```
Planned dispatch (--dry-run, no mutations):

| ticket  | lane       | status  | command                              | link                                  |
|---------|------------|---------|--------------------------------------|---------------------------------------|
| CAF-212 | fresh-port | planned | /port:ff --ticket:CAF-212 --auto     | https://linear.app/.../CAF-212        |
| CAF-198 | fresh-dev  | planned | /dev:ff CAF-198 --auto --no-figma    | https://linear.app/.../CAF-198        |

Total: <N>. Re-run without --dry-run to execute.
```

The command string is built per ticket via the same §5.1 / §5.2 rules used for live dispatch; the `--no-figma` auto-detection runs here too so the dry-run preview matches what would actually be spawned.

### 4.1 Init protocol — apply per ticket, sequentially

<!-- SYNC: steps 2–5 below (status / assignee / estimate / comment) are duplicated in:
     - /dev:start Auto-mode item 4 (commands/dev/dev/start.md)
     - /port:start Step 5a       (commands/dev/port/start.md)
     Drift between these breaks dispatcher idempotency.

     Step 1 (the label swap) is INTENTIONALLY dispatcher-only under Plan X. The *:start
     commands continue to do a plain `remove ready-to-*` and MUST NOT add any
     `dispatcher-*-in-flight` label — adding it there would silently flip manual
     runs into dispatcher-recoverable state, which is not what the user asked for.
     Net effect on the dispatcher path: dispatcher swaps to in-flight; subsequent
     *:start invocations inside the subagent try to remove ready-to-* (no-op,
     already gone) and leave the in-flight label alone (correct). -->

Each ticket's lock depends on its selection lane (§2.1). `<inflight>` resolves to `dispatcher-port-in-flight` for port lanes, `dispatcher-dev-in-flight` for dev lanes.

For each ticket, in order:

1. **Label swap** via `mcp__claude_ai_Linear__save_issue`:
   - Lanes `fresh-port` / `fresh-dev`: remove `ready-to-port` / `ready-to-dev`, **add `<inflight>`**. Atomic compound op in a single `save_issue` call.
   - Lanes `recovery-port` / `recovery-dev`: in-flight label already present — call is a no-op idempotently (still send the same payload so the contract is uniform).
2. `mcp__claude_ai_Linear__save_issue`: status → `In Progress`.
3. `mcp__claude_ai_Linear__save_issue`: assignee = `$USER_NAME`.
4. `mcp__claude_ai_Linear__save_issue`: estimate = `1` if currently null.
5. `mcp__claude_ai_Linear__save_comment`: `Dispatcher: starting <port|dev> for this ticket.` (fresh lanes) or `Dispatcher: resuming <port|dev> for this ticket.` (recovery lanes).

After each ticket, print to stdout:

```
<ticket-id>: locked ✓ (<lane>)
```

or on failure:

```
<ticket-id>: failed (<reason>)
```

### 4.2 Mid-batch failure recovery

If any ticket fails any of steps 1-5 mid-batch:

1. Best-effort **unlock** previously-locked tickets `#1..#N-1` — reverse the lane-specific lock transition:
   - Fresh lanes: `save_issue` to remove `<inflight>` AND re-add the original `ready-to-*` label (return to pre-lock state).
   - Recovery lanes: `save_issue` is a no-op — the in-flight label was already present before the run, so leaving it as-is preserves the recovery signal for next time. Do NOT remove the in-flight label here; doing so would lose the only marker that says "this ticket needs another dispatcher pass."
   - Post `Dispatcher: aborting batch — Linear MCP failure on <failed-ticket>.` comment.
2. If unlock itself fails for any ticket: post `Dispatcher: PARTIAL LOCK — manual recovery needed for <ticket-id>.` comment.
3. STOP — release lock — do NOT spawn any agents.

The user sees the full failure trace in stdout per the audit-trail rule.

### 4.3 Dispatch table

After every surviving ticket is locked, **before** the Step 5 spawn, print the planned dispatch as a single markdown table so the user can audit the batch and click into each Linear issue:

```
Dispatching <N> tickets:

| ticket  | lane         | status   | command                              | link                                  |
|---------|--------------|----------|--------------------------------------|---------------------------------------|
| CAF-212 | fresh-port   | locked ✓ | /port:ff --ticket:CAF-212 --auto     | https://linear.app/.../CAF-212        |
| CAF-198 | fresh-dev    | locked ✓ | /dev:ff CAF-198 --auto --no-figma    | https://linear.app/.../CAF-198        |
| CAF-370 | recovery-dev | locked ✓ | /dev:ff CAF-370 --auto               | https://linear.app/.../CAF-370        |
```

- `ticket` = Linear ticket id (column ordering follows the §2.3 priority sort).
- `lane` = the §2.1 value (`fresh-port` / `fresh-dev` / `recovery-port` / `recovery-dev`).
- `status` = `locked ✓` for every row here; failed-lock tickets never reach this step (§4.2 aborts the whole batch).
- `command` = the exact string Step 5 will pass to its spawn — already computed via §5.1 / §5.2.
- `link` = the issue `url` field returned by the Step 2 `list_issues` calls. Cache the url alongside the ticket id from Step 2 so this column does not require a re-fetch.

The §4.0 dry-run path reuses this same table shape (with `status: planned`). Keeping one render keeps preview ↔ live output 1:1.

**Roster artifact for §6.1**: while building this table, also accumulate the same rows (without `status`, without `link`) into a `DISPATCH_ROSTER` variable as TSV — one line per ticket, format `<ticket-id>\t<lane>\t<absolute-worktree-path>`. The §6.1a progress poller persists this to `claude-reports/dispatcher/<RUN_TS>-<PID>/roster.tsv` and walks it every 30s. Worktree path = `realpath ../<TICKET-ID>` per the `/add-worktree` convention. Build the roster here (not in §6.1a) because §6.1a spawns in the same assistant message as Step 5 and has no separate "compute" turn.

For each locked ticket, build the dispatch command from its selection lane (§2.1):

### 5.1 Port lanes (`fresh-port`, `recovery-port`)

```
/port:ff --ticket:<ID> --auto
```

`/port:ff` is idempotent across stages, so recovery-port dispatches the same command as fresh-port. The pipeline auto-skips stages whose markers (`.port/dev-notes.md`, `.port/pm-notes.md`, etc.) already exist.

### 5.2 Dev lanes (`fresh-dev`, `recovery-dev`) — `--no-figma` auto-detection

Inspect the ticket description (already fetched in Step 2):

```
if echo "$TICKET_DESCRIPTION" | grep -qiE 'figma\.com/(design|board|slides|make)/'; then
  CMD="/dev:ff $ID --auto"
else
  CMD="/dev:ff $ID --auto --no-figma"
fi
```

Avoids `/dev:ff` stalling on missing Figma in unattended batches.

### 5.3 Spawn

**You MUST emit all N `Agent` tool calls in a single assistant message.** Do not narrate between calls, do not split across turns, do not group by team. The orchestrating LLM may be tempted to interleave prose ("now spawning ticket X...") between calls — this serializes the join and defeats the parallelism. Narration belongs after the join in Step 6.

Single message, N parallel `Agent` calls (one per ticket):

- `description`: `Dispatch <ticket-id> via <port|dev>:ff`
- `subagent_type`: `general-purpose`
- `model`: `"opus"` — required for dev tickets because `/dev:apply --auto` runs `/opsx:apply` inline inside this subagent (--auto no longer spawns `dev-agent`, to eliminate nested opus spawn that fails from subagent context). The implementation work needs opus quality reasoning. Port tickets technically don't need opus, but keeping one consistent spawn shape avoids drift.
- `prompt`: the dispatch command string built above, plus the explicit loop-driving guidance below. **A short "run X and report outcome" prompt is insufficient** — `/dev:ff` and `/port:ff` are LLM-interpreted dispatch loops (their dispatch steps are pseudocode walked by you, not real shell), and a vague prompt has been observed to make the agent stop after the first visibly-successful stage (e.g. `Apply complete. Next: /dev:verify.`) instead of continuing the loop. The text below MUST be included verbatim after the command:

  ```
  Execute the slash command above. Drive its pipeline to terminal state. The
  pipeline is NOT complete until one of these holds:
    (a) /dev:ff / /port:ff itself reports `done` (full chain finished, PR open or
        ship comment posted),
    (b) a stage fails to advance (infer_dev_stage returns the same value twice),
    (c) a HITL gate fires (default mode only — not applicable here since --auto is set),
    (d) a stage writes a Status: BLOCKED / FAILED / ABORTED marker file.

  Stage-level "complete" messages (e.g. "Apply complete. Next: /dev:verify.",
  "Verify CLEAR. Next: /dev:review.") are IN-LOOP signals — they mean the loop
  must continue to the next stage. They are NOT terminal.

  After each stage returns, re-run `infer_dev_stage` (as defined in
  commands/dev/dev/ff.md) and dispatch the next /dev:<stage> (or /port:<stage>).
  Repeat until one of (a)-(d) above is reached.

  When you stop, report which terminal condition was hit, which stage was
  current, and the relevant marker file path (if any).
  ```
- `mode`: `"bypassPermissions"`
- `run_in_background`: `true`
- `isolation`: **omit** — do NOT use `worktree` isolation. `/port:ff` and `/dev:ff` create their own worktrees internally; nesting them under dispatcher-level isolation produces conflicting checkouts.

Print after spawn:

```
Spawned <N> agents in parallel + 1 progress poller (re-renders every 30s).
Session must remain open. Do not let the machine sleep.
```

See §6.1a for the poller spec — it MUST be included in the same single assistant message as the N `Agent` spawn calls.

---

## Step 6: Wait, fallback, finalize

### 6.1 Progress poll loop

The dispatcher session waits here for every spawned agent's background-completion notification — but it does **not** sit silently. A sibling `Bash` poller (also `run_in_background: true`) re-renders a stage-progress table every 30s so the user can monitor the batch in real time. The poller is a separate background process, not LLM-driven, so its cadence is independent of how often agent notifications arrive.

#### 6.1a Spawn the poller (same message as Step 5)

In the same single assistant message that fans out the N `Agent` calls (§5.3), include one additional `Bash` tool call with `run_in_background: true` running the script below. Keeping spawn + poller in one message means the poller starts before the first agent's first stage transition.

```bash
RUN_DIR="claude-reports/dispatcher/$RUN_TS-$$"
mkdir -p "$RUN_DIR"

# Write the lane + worktree path + ticket id for each dispatched ticket. One line per ticket:
# <ticket-id>\t<lane>\t<absolute-worktree-path>
printf '%s\n' "$DISPATCH_ROSTER" > "$RUN_DIR/roster.tsv"
# DISPATCH_ROSTER is built in §4.3 alongside the dispatch table. Worktree path is
# realpath ../<TICKET-ID> from the main repo (the /add-worktree convention).

START_EPOCH=$(date -u +%s)

while true; do
  # Bail out if dispatcher has cleared the lockfile (= all agents joined, §6.5).
  [ -f claude-reports/dispatcher/.lock ] || exit 0

  now=$(date -u +%s)
  elapsed=$(( now - START_EPOCH ))
  mm=$(printf '%02d' $(( elapsed / 60 )))
  ss=$(printf '%02d' $(( elapsed % 60 )))

  echo
  echo "Progress (t+${mm}:${ss}):"
  echo "| ticket  | lane         | stage   | last marker                                  |"
  echo "|---------|--------------|---------|----------------------------------------------|"

  while IFS=$'\t' read -r tid lane wt; do
    # Pick the right walker based on lane (port walker for *-port, dev walker for *-dev).
    case "$lane" in
      *-port) stage=$(cd "$wt" 2>/dev/null && infer_port_stage 2>/dev/null || echo "?") ;;
      *-dev)  stage=$(cd "$wt" 2>/dev/null && infer_dev_stage  2>/dev/null || echo "?") ;;
    esac

    # last marker = most-recently-modified file under .dev/ or .port/, for the table's
    # human-readable "what just happened" column. Pure cosmetic — the stage column is the
    # ground-truth signal.
    marker=$(find "$wt/.dev" "$wt/openspec/changes"/*/.port -type f 2>/dev/null \
              | xargs ls -t 2>/dev/null | head -1 \
              | sed "s|^$wt/||")

    printf '| %-7s | %-12s | %-7s | %-44s |\n' "$tid" "$lane" "$stage" "${marker:-—}"
  done < "$RUN_DIR/roster.tsv"

  sleep 30
done
```

`infer_dev_stage` and `infer_port_stage` are the same walkers defined in `commands/dev/dev/ff.md` and `commands/dev/port/ff.md`. Either source them via a shared shell file or inline them into the poller — implementer's call.

#### 6.1b Join

The dispatcher LLM is notified per spawned agent as each completes. Maintain an in-memory `joined` counter; on each notification, increment. When `joined == N`, proceed to §6.2. The poller is terminated cleanly in §6.5 when the dispatcher removes the lockfile (the script's first check inside the `while` loop exits on missing lock).

#### 6.1c Why polling, not heartbeats

Spawned `/dev:*` / `/port:*` stages already write authoritative marker files (`.dev/figma-context.md`, `.dev/align-result.md`, `.dev/apply-result.md`, `.dev/verify-pass.md`, `.port/dev-notes.md`, `.port/pm-notes.md`, `.port/synth-report.md`, etc.). Reading those files is the dispatcher's only ground truth for "what stage are we in" — adding stage-transition heartbeats would couple every stage command to the dispatcher and drift on the next refactor. The 30s cadence is a compromise: stages take 1–10 min so transitions are caught within one tick; faster would burn terminal scrollback for no signal.

Closing the dispatcher session early still kills MCP connections and leaves Linear in a half-finalized state — that constraint is unchanged.

### 6.2 Per-ticket fallback

For each completed ticket, inspect its result (via the agent's return message) and Linear state:

| Type    | Expected end state                                                                                                  | Fallback if missing                                                          |
|---------|---------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------|
| port OK | `need-spec-review` label added by `/port:ship`; `dispatcher-port-in-flight` removed by `/port:ship`                 | `save_issue` → add `need-spec-review` AND remove `dispatcher-port-in-flight` |
| dev OK  | status `In Review` set by `/dev:ship`; `dispatcher-dev-in-flight` removed by `/dev:ship`                            | `save_issue` → set status `In Review` AND remove `dispatcher-dev-in-flight`  |
| failure | `dispatcher-*-in-flight` label still present (intentional — feeds Q2/Q4 on the next run); agent posted its own failure comment | If no failure comment exists, post one via `save_comment`. Do NOT remove the in-flight label — that's the resume signal. |

### 6.3 Aggregate reports

For each dispatched ticket:

```bash
WORKTREE=$(git worktree list --porcelain | awk -v t="$TICKET_ID" '/^worktree / && $0 ~ t {print $2}')
TARGET="claude-reports/dispatcher/$RUN_TS-$$/$TICKET_ID"
RUN_START_EPOCH=$(date -u -j -f "%Y%m%dT%H%M%SZ" "$RUN_TS" +%s 2>/dev/null || echo 0)

# Fresh start: target may already exist from a prior partial run with the same RUN_TS-PID.
# `cp -R src/. dst/` would merge with whatever's there. Clear it so this run's snapshot
# is unambiguous.
rm -rf "$TARGET"
mkdir -p "$TARGET"

# Also copy .dev/ — its result files (figma-context.md, align-result.md, apply-result.md,
# verify-pass.md) are the ground-truth stage markers. Without these, post-mortem analysis
# of where a partial run actually stopped is guesswork.
if [ -d "$WORKTREE/.dev" ]; then
  mkdir -p "$TARGET/.dev"
  cp -R "$WORKTREE/.dev/." "$TARGET/.dev/"
fi

if [ -d "$WORKTREE/claude-reports" ]; then
  # Skip stale files (mtime older than this dispatcher run's start). Without the filter,
  # a prior run's `dispatcher-blocked.md` will be carried into this run's bundle and
  # mislead anyone reading the report.
  while IFS= read -r src; do
    rel="${src#$WORKTREE/claude-reports/}"
    dst="$TARGET/$rel"
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
  done < <(find "$WORKTREE/claude-reports" -type f -newermt "@$RUN_START_EPOCH" 2>/dev/null)
fi
```

Copies (not symlinks — the worktree may be removed later) every spawned ff agent's reports written **during this dispatcher run** into one central place. Stale files from prior runs are filtered out by the `-newermt` predicate. `.dev/` is included so post-mortem can read the actual stage markers without going to the worktree.

### 6.4 Run summary

Write `claude-reports/dispatcher/<RUN_TS>-<PID>.md`:

```markdown
# Dispatcher run — <RUN_TS> (PID <PID>)

Team       : <team_key>
Repo       : <basename(cwd)>
Default br : <default_branch>
--max-parallel : <N>
--dry-run  : <true|false>
--test     : <true|false>

## Result counts
Dispatched : <N>
  ↳ port  : <count>
  ↳ dev   : <count>
Skipped    : <count>
  ↳ PR-exists       : <count>
  ↳ branch-exists   : <count>
  ↳ duplicate-label : <count>
Failed     : <count>

## Per-ticket result
| ticket | type | result | worktree |
|--------|------|--------|----------|
| ...    | ...  | ...    | ...      |
```

`<RUN_TS>` is `date -u +%Y%m%dT%H%M%SZ`. The `<PID>` suffix prevents collision when concurrent invocations slip past the lock (only possible if the lock was forcibly removed).

### 6.5 Release lock + final stdout summary

```bash
rm -f claude-reports/dispatcher/.lock
```

Print:

```
Dispatcher run complete.
  Dispatched : <N>  (<N-port> port, <N-dev> dev)
  Skipped    : <N>  (<reasons>)
  Failed     : <N>
  Report     : claude-reports/dispatcher/<RUN_TS>-<PID>.md
```

STOP.

---

## Guardrails

- **Never spawn an agent before Step 4 lock completes.** A second invocation triggered seconds after the first must see locked tickets, not race-pickable ones.
- **Never use `mode != "bypassPermissions"` on the spawned Agents** — interactive permission prompts inside background agents stall the whole batch.
- **Never use `isolation: "worktree"` on the spawned Agents** — `/port:ff` and `/dev:ff` create their own worktrees; nesting collides.
- **Never auto-stash, auto-checkout, or auto-clean the user's tree.** Pre-flight aborts are explicit; the user fixes their own state.
- **Never proceed past Step 4.0 dry-run gate.** `--dry-run` must be 100% read-only end-to-end.
- **Never assume `state` filter accepts names.** Always use the type (`unstarted` / `started`) so renamed Linear states across teams still resolve.
- **Never write to a registered repo's `claude-reports/` from outside that repo.** Only the spawned ff agents touch their own worktree's reports; dispatcher's own writes stay in `<main>/claude-reports/dispatcher/`.
- **Lock is released on every exit path.** Including aborts in Step 0/1, empty-tickets in Step 2/3, port config missing in Step 3.5, dry-run in Step 4, partial lock in Step 4.2, and normal completion in Step 6.5.
- **`branch_prefix: auto` repos require `--team:<KEY>`.** No silent fan-out across teams.
- **All user-facing output is English.** Per repo convention.
- **In-flight labels (Plan X) are managed exclusively by dispatcher + `*:ship`.** `dispatcher-port-in-flight` / `dispatcher-dev-in-flight` are added by §4.1 lock and removed by `/dev:ship` / `/port:ship` on success, by §3.1 PR-exists on stale-state cleanup, and by §4.2 rollback on fresh lanes. They are NEVER added by `/dev:start`, `/port:start`, or any other code path — those routes are for manual users who do not want dispatcher to auto-resume their work. Adding the label outside the dispatcher would silently make manual runs dispatcher-recoverable, breaking the user's expectation of "if I stopped, it stays stopped."

## Linear label state machine (Plan X)

| Label                              | Added by                  | Removed by                                                                              | Meaning                                  |
|------------------------------------|---------------------------|-----------------------------------------------------------------------------------------|------------------------------------------|
| `ready-to-port`                    | human (PM/eng)            | `/port:start` auto / `/ggx-dispatcher` §4.1 lock (fresh-port lane)                       | "this ticket is ready for the port pipeline" |
| `ready-to-dev`                     | human (PM/eng)            | `/dev:start` auto / `/ggx-dispatcher` §4.1 lock (fresh-dev lane)                         | "this ticket is ready for the dev pipeline" |
| `dispatcher-port-in-flight`        | `/ggx-dispatcher` §4.1 (fresh-port) | `/port:ship` step 13 (success) / `/ggx-dispatcher` §3.1 (PR exists) / `/ggx-dispatcher` §4.2 (rollback fresh-port only) | "dispatcher is mid-run on port; resume if seen next time" |
| `dispatcher-dev-in-flight`         | `/ggx-dispatcher` §4.1 (fresh-dev)  | `/dev:ship` step 3 (success) / `/ggx-dispatcher` §3.1 (PR exists) / `/ggx-dispatcher` §4.2 (rollback fresh-dev only)   | "dispatcher is mid-run on dev; resume if seen next time"  |
| `need-spec-review`                 | `/port:ship` step 13 (auto only) | human reviewer                                                                          | "ported spec ready for human spec review" |

Invariant: a ticket should never have both `ready-to-*` AND the matching `dispatcher-*-in-flight` simultaneously (§2.2 conflict check b). If it does, the lock state is inconsistent — dispatcher skips with a comment asking the human to resolve.
