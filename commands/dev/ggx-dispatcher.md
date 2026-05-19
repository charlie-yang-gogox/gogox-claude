---
name: ggx-dispatcher
description: >
  Manual batch worker for actionable Linear tickets. Sweeps the cwd repo's
  team for `ready-to-port` and `ready-to-dev` tickets, race-locks them, and
  fans out parallel `/ggx-work <ID> --auto` agents (which in turn route to
  `/port:ff` / `/dev:ff` / `/bug:ff` via `/route`). Single-repo, cwd-driven;
  user-invoked from a Claude session opened in the target repo on its
  default branch.
Prerequisite: >
  - Linear MCP authenticated; gh CLI authenticated.
  - cwd is the main worktree of a registered Linear repo, on default branch,
    clean tree (use `--test` to skip branch/clean checks).
  - Environment variable USER_NAME set.
  - For `branch_prefix: auto` repos: pass `--team:<KEY>` (e.g. `--team:CET`).
---

# /ggx-dispatcher — manual batch worker

Find every actionable ticket in the cwd repo's Linear team and dispatch each through `/ggx-work <ID> --auto` in parallel. The `/ggx-work` subagent then calls `/route --non-interactive` to pick the right `/port:ff` / `/dev:ff` / `/bug:ff` based on the ticket's classification label and worktree state. Each spawned agent runs in `run_in_background: true`; the dispatcher waits for all to complete before posting fallbacks and emitting a summary.

**Usage**: `/ggx-dispatcher [--dry-run] [--test] [--max-parallel:<N>] [--team:<KEY>]`

- `--dry-run` — Print the planned dispatch and STOP. No Linear writes, no agent spawn.
- `--test` — Skip the default-branch + clean-tree pre-flight checks (still requires main worktree, gh auth, and lockfile).
- `--max-parallel:<N>` — Concurrent dispatch cap. Default `10`, hard cap `20`. Out of range → abort.
- `--team:<KEY>` — Required when the cwd repo's `branch_prefix` is `auto`. Allowed (but must equal `branch_prefix`) when `branch_prefix` is concrete.


---

## Label ownership boundary

Two distinct label namespaces drive this pipeline; mixing them up is the
single most common reason for incorrect routing. They are **orthogonal**.

| Namespace                  | Examples                                                       | Owned by                                                                                       | Read by                                                                                       |
|----------------------------|----------------------------------------------------------------|------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------|
| **Workflow labels**        | `ready-to-port`, `ready-to-dev`, `dispatcher-port-in-flight`, `dispatcher-dev-in-flight`, `need-spec-review` | dispatcher + `/port:ship` + `/dev:ship` + `/ggx-work` (scoped — see below)                     | dispatcher (Q1–Q4 discovery, §4.1 lock, §6.2 fallback); `/ggx-work` Step 2.5 + Step 4.4a; `/spec-review` batch fetch |
| **Classification labels**  | `bug`, `port`, `feature`                                       | humans (PM/eng)                                                                                | `/route`; `/ggx-work` Step 2.5 (read-only, lane derivation)                                   |

`/ggx-work`'s write scope inside the workflow namespace is deliberately
narrow:

- **Step 2.5** removes `ready-to-port` / `ready-to-dev` after lane is
  derived — same as `/port:start` / `/dev:start` auto-mode item 4 and
  the dispatcher §4.1 fresh-lane swap. Idempotent across all three
  writers; whichever runs first wins.
- **Step 4.4a HITL fallback** adds `need-spec-review` after a
  successful `/port:ff` if absent. Compensates for `/port:ship`'s
  HITL-mode skip (its step 13 documents the rationale). Never fires
  when `/port:ship --auto` already added the label.
- **`/ggx-work` NEVER writes `dispatcher-*-in-flight`.** Those labels
  remain exclusively dispatcher's resume signal — see the Guardrails
  section below.

What this means concretely:

- The dispatcher continues to find work, race-lock, and reconcile success
  via workflow labels. Q1–Q4 still filter on `ready-to-*` /
  `dispatcher-*-in-flight`; §4.1 still swaps `ready-to-*` →
  `dispatcher-*-in-flight`; §6.2 still inspects in-flight residue to
  decide success vs. failure. `/ggx-work`'s expanded write authority
  covers the orchestrator's own lifecycle moves (Step 2.5, Step 4.4a);
  the dispatcher's contracts are unchanged.
- The dispatcher does NOT read classification labels. It cannot route by
  pipeline type itself, and doesn't try — once a ticket is locked, the
  spawned `/ggx-work` subagent calls `/route`, which reads the
  classification label and decides which `/port:ff` / `/dev:ff` /
  `/bug:ff` to run. `/ggx-work` Step 2.5 also reads the classification
  label to derive lane for its lifecycle init, but does not write it.
- `/route` deliberately does NOT read workflow labels (see `/route`
  guardrails). It cannot tell a freshly-locked ticket apart from a
  recovery one — and shouldn't have to. The classification label
  combined with worktree filesystem state (`.port/synth-report.md`,
  `.dev/*` markers) is sufficient because the underlying ff walkers
  resume idempotently from those markers.
- `need-spec-review` has two writers: `/port:ship --auto` step 13
  (canonical dispatcher path) and `/ggx-work` Step 4.4a else-branch
  (canonical HITL path). Both produce the same end state — label
  present, ticket discoverable by `/spec-review`'s batch fetch and by
  `/ggx-work` Step 4.4a's short-circuit on the next invocation.

The dispatcher is the boundary process. Inside the dispatcher we speak
"workflow"; inside a spawned `/ggx-work` subagent we speak
"classification"; the only shared signal is the in-flight label, which
`/ggx-work` never writes and `/port:ship` / `/dev:ship` are the
authoritative removers of.

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

| # | label                         | state          | team       | assignee | Catches                                                                                  |
|---|-------------------------------|----------------|------------|----------|------------------------------------------------------------------------------------------|
| Q1 | `ready-to-port`              | *(omit)*       | `<team_key>` | `me`     | Fresh port tickets — any status; status post-filtered in §2.0                            |
| Q2 | `dispatcher-port-in-flight`  | `In Progress`  | `<team_key>` | `me`     | Crash-recovery: port mid-pipeline                                                        |
| Q3 | `ready-to-dev`               | *(omit)*       | `<team_key>` | `me`     | Fresh dev tickets — any status (incl. post-port handoff where status is still In Progress) |
| Q4 | `dispatcher-dev-in-flight`   | `In Progress`  | `<team_key>` | `me`     | Crash-recovery: dev mid-pipeline                                                         |

Selection model (Plan X, May 2026):

- **Fresh dispatch** = ticket has `ready-to-port` / `ready-to-dev`. The label IS the "ready to dispatch" signal — **Q1/Q3 deliberately omit the `state` filter** because the port→spec-review→dev handoff transitions the label without resetting status to `To-do`: port leaves status as `In Progress`, the human reviewer relabels `need-spec-review` → `ready-to-dev` without touching status, so any `state: unstarted` filter on Q3 silently drops every post-port dev ticket. The status-level exclusion of completed / in-review tickets is enforced post-fetch (§2.0) instead. At lock time the dispatcher swaps the actionable label for the corresponding `dispatcher-*-in-flight` label (§4.1).
- **Crash recovery** = ticket has `dispatcher-port-in-flight` / `dispatcher-dev-in-flight` left over from a prior run that didn't reach ship. Q2/Q4 catch these. `/port:ship` and `/dev:ship` remove the in-flight label only on full success, so its presence is a hard signal that "dispatcher claimed this and didn't finish."
- The two-label split (port vs dev) lets the dispatcher pick the right §4.1 lock transition and the right §6.4 walker (`infer_port_stage` vs `infer_dev_stage`) at end-of-run table rendering without re-deriving pipeline type from worktree state. Spawn target itself is uniformly `/ggx-work` (§5.1) regardless of lane — pipeline routing happens inside the subagent via `/route`.
- Q2/Q4 use state name `In Progress` exactly — `In Review` / `Ready for QA` are post-work and must NOT be re-dispatched. (If a team renames `In Progress`, Q2/Q4 silently miss; verify with `mcp__claude_ai_Linear__list_issue_statuses` on onboarding.)

### 2.0 Post-fetch status filter (Q1/Q3 only)

Because Q1/Q3 return tickets at any status, apply this filter on the merged Q1+Q3 result before §2.1 dedup:

- Drop survivors whose `statusType` is `completed` or `canceled` (the work is already done or thrown away).
- Drop survivors whose `status` name is `In Review` or `Ready for QA` (the work is past dispatcher scope — dispatching would dup-PR or no-op).
- Keep everything else: `To-do`, `In Progress`, `Reopened`, `Up Next`, `Backlog`, `Triage` are all acceptable starting states. Backlog/Triage are unusual but if a human explicitly labeled `ready-to-*` from those states it's intentional.

Q2/Q4 do not need this post-filter — their `state: In Progress` filter is already narrow enough.

States explicitly EXCLUDED from dispatch: `In Review`, `Ready for QA`, `Done`, `Canceled`, `Duplicate`. (Triage/Backlog are allowed; the label is what gates dispatch.)

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

| ticket  | lane       | status  | command                | link                                  |
|---------|------------|---------|------------------------|---------------------------------------|
| CAF-212 | fresh-port | planned | /ggx-work CAF-212 --auto | https://linear.app/.../CAF-212      |
| CAF-198 | fresh-dev  | planned | /ggx-work CAF-198 --auto | https://linear.app/.../CAF-198      |

Total: <N>. Re-run without --dry-run to execute.
```

The command string is identical for every lane (`/ggx-work <ID> --auto`)
per §5.1; lane only drives the §4.1 lock-label transition and the
§6.4 end-of-run table's choice of `infer_port_stage` vs `infer_dev_stage`.

### 4.1 Init protocol — apply per ticket, sequentially

<!-- SYNC: steps 2–5 below (status / assignee / estimate / comment) are duplicated in:
     - /dev:start Auto-mode item 4 (commands/dev/dev/start.md)
     - /port:start Step 5a       (commands/dev/port/start.md)
     - /ggx-work Step 2.5        (commands/dev/ggx-work.md — the HITL+auto orchestrator path)
     Drift between these breaks dispatcher idempotency and the HITL orchestrator lifecycle.

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

### 4.3 Print the dispatch table

**Required step, not optional preview prose.** After every surviving ticket is locked and before any Step 5 spawn, the next thing emitted in stdout must be this table. **The table is text output and the N `Agent` spawn calls (§5.3) all emit in the SAME single assistant message** — print the table, then immediately follow with the spawn tool calls in the same turn. Do NOT end the turn after the table to "let the user confirm". That artificial stop has been observed to force the user to type "are you done?" before any agent spawn actually happens — by the time they nudge, the perceived dispatcher has been idle for minutes. This applies on every sweep, including a same-lock re-sweep where the batch is small (1–2 tickets) and the §4.1 `locked ✓` lines might feel sufficient — they are not.

```
Dispatching <N> tickets:

| ticket  | lane         | status   | command                  | link                                  |
|---------|--------------|----------|--------------------------|---------------------------------------|
| CAF-212 | fresh-port   | locked ✓ | /ggx-work CAF-212 --auto | https://linear.app/.../CAF-212        |
| CAF-198 | fresh-dev    | locked ✓ | /ggx-work CAF-198 --auto | https://linear.app/.../CAF-198        |
| CAF-370 | recovery-dev | locked ✓ | /ggx-work CAF-370 --auto | https://linear.app/.../CAF-370        |
```

The §4.0 dry-run path emits the same table with `status: planned` instead of `locked ✓`. Same shape, one render path.

Column rules:

- `ticket` = Linear ticket id (column ordering follows the §2.3 priority sort).
- `lane` = the §2.1 value (`fresh-port` / `fresh-dev` / `recovery-port` / `recovery-dev`).
- `status` = `locked ✓` for every row here; failed-lock tickets never reach this step (§4.2 aborts the whole batch).
- `command` = the exact string Step 5 will pass to its spawn — uniformly `/ggx-work <ID> --auto` per §5.1.
- `link` = the issue `url` field returned by the Step 2 `list_issues` calls. Cache the url alongside the ticket id from Step 2 so this column does not require a re-fetch.

While building this table, accumulate the same rows into an in-memory `DISPATCH_ROSTER` value — TSV, one line per ticket, format `<ticket-id>\t<lane>\t<absolute-worktree-path>\t<url>`. Worktree path = `realpath ../<TICKET-ID>` per the `/add-worktree` convention; `url` is the issue url cached from Step 2. **Held in session state only — do NOT write a roster.tsv file.** §6.4 uses this roster to render the end-of-run table (lane lookup, walker selection, Ticket-link column).

### 5.1 Uniform spawn command (all four lanes)

```
/ggx-work <ID> --auto
```

Port and dev lanes share one spawn target. The `/ggx-work` subagent calls
`/route --non-interactive` to decide which ff to run; `/route` reads the
ticket's classification label (`bug` / `port` / `feature`) plus the
worktree filesystem (port-ship marker, `.dev/*` markers) and recommends
`/port:ff`, `/dev:ff`, or `/bug:ff`. Recovery lanes (`recovery-port` /
`recovery-dev`) dispatch the same command because the ff walkers
(`infer_port_stage` / `infer_dev_stage`) resume idempotently from their
own marker files — the in-flight label is the dispatcher's signal that
the worktree exists, not a routing hint that needs to be carried into
the spawned subagent.

**Figma URL detection is no longer dispatcher's job.** Previously the
dispatcher pre-scanned the ticket description for `figma\.com/...` and
attached `--no-figma` to the dev spawn. That detection has moved into
`/dev:start` Step 4 and now scans description **and** comments, so a
designer dropping a Figma link as a follow-up comment no longer routes
the ticket through the SKIPPED short-circuit. Dispatcher just passes
`/ggx-work <ID> --auto`; the rest is determined downstream.

### 5.3 Spawn

**You MUST emit the §4.3 dispatch table and all N `Agent` tool calls in a single assistant message.** Print the table text first, then the N parallel `Agent` calls — back-to-back, no turn break, no intermediate "ready to spawn?" pause. Do not narrate between calls, do not split across turns, do not group by team. The orchestrating LLM may be tempted to interleave prose ("now spawning ticket X...") between calls — this serializes the join and defeats the parallelism. It may also be tempted to end the turn after the table so the user can review — do not. The table is the review; spawning follows immediately in the same turn. Narration belongs after the join in Step 6.

Single message, N parallel `Agent` calls (one per ticket):

- `description`: `Dispatch <ticket-id> via /ggx-work`
- `subagent_type`: `general-purpose`
- `model`: `"opus"` — required because dev tickets routed through
  `/ggx-work` will eventually run `/dev:apply --auto` inline inside this
  subagent (--auto no longer spawns `dev-agent`, to eliminate nested
  opus spawn that fails from subagent context). The implementation work
  needs opus quality reasoning. Port tickets technically don't need
  opus, but keeping one consistent spawn shape avoids drift.
- `prompt`: the dispatch command plus a short loop-driving framing.
  `/ggx-work` itself owns the per-iteration loop discipline (call
  `/route` → execute → repeat); the framing below exists only so the
  subagent does not stop after `/ggx-work` reports a non-terminal stage
  message from a single ff invocation. Include verbatim after the
  command:

  ```
  Execute: /ggx-work <TICKET_ID> --auto

  /ggx-work is a single-ticket orchestrator that drives this ticket
  through every pipeline it needs (port → spec-review pause → dev, or
  just dev, or bug) by repeatedly calling /route and executing the
  recommended ff. Drive it to a terminal condition; do NOT stop on
  intermediate stage messages.

  Terminal conditions (any ONE of these ends the run):
    (a) /ggx-work reports `Ticket <id>: done.` (full chain finished, PR open)
    (b) /ggx-work reports `Ticket <id>: port complete, paused for human
        spec review.` (Step 4.4a short-circuit — port shipped, human gate)
    (c) /ggx-work exits non-zero with an abort message (Step 4.3 — pipeline
        failure, unknown classification, or route call failure)

  When you stop, report which of (a)/(b)/(c) was hit and quote the final
  /ggx-work output line.
  ```
- `mode`: `"bypassPermissions"`
- `run_in_background`: `true`
- `isolation`: **omit** — do NOT use `worktree` isolation. The ff
  pipelines invoked by `/ggx-work` create their own worktrees
  internally; nesting them under dispatcher-level isolation produces
  conflicting checkouts.

Print after spawn:

```
Spawned <N> agents in parallel. Completion lines will appear as each
finishes. Session must remain open. Do not let the machine sleep.
```

No separate progress poller. Per-ticket completion is surfaced by §6.1
when each agent's background notification arrives; end-of-run rendering
is the §6.4 summary table.

---

## Step 6: Wait, fallback, finalize

### 6.1 Wait for completions

The dispatcher session waits here for every spawned agent's
background-completion notification. **No sibling poller process.** Each
notification arrives event-driven from the harness; printing a 30s tick
table on top of those events was redundant noise — the table re-rendered
the same per-ticket state that the notification line already announces.

Maintain an in-memory `joined` counter. On each notification:

1. Increment `joined`.
2. Emit one short status line so the user sees progress live:
   ```
   [<joined>/<N>] <ticket-id> finished (<terminal-condition>).
   ```
   `<terminal-condition>` is parsed from the agent's return message —
   one of `done` / `port-paused` / `failed` (matching the three
   `/ggx-work` terminal conditions enumerated in §5.3's spawn prompt).
   Failed cases include a short reason if the agent provided one.

When `joined == N`, proceed to §6.2.

`/dev:*` / `/port:*` stages write authoritative marker files
(`.dev/verify-pass.md`, `.port/synth-report.md`, etc.) as they run.
Those files remain the ground truth for "what stage did this ticket
reach"; §6.4 reads them via `infer_*_stage` at end-of-run for the
summary table. Reading marker files mid-run via a polling loop would
produce stale-or-flickering values vs the notification, so the
dispatcher delegates that read to end-of-run when state is settled.

Closing the dispatcher session early still kills MCP connections and
leaves Linear in a half-finalized state — that constraint is unchanged.

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

### 6.4 End-of-run summary table

For each ticket in `DISPATCH_ROSTER` (§4.3), collect:

| Signal           | Source                                                                 | Notes                                                                                          |
|------------------|------------------------------------------------------------------------|------------------------------------------------------------------------------------------------|
| `labels`         | `mcp__claude_ai_Linear__get_issue <ticket-id>` — re-fetched at §6.4 time | settled state, after `/port:ship` / `/dev:ship` / fallback (§6.2) have written                  |
| `status.name`    | same call                                                              | `In Progress` / `In Review` etc.                                                                |
| `url`            | from roster (cached at Step 2)                                         | no re-fetch                                                                                     |
| `pipeline_outcome` | the agent's reported terminal condition (§6.1)                       | `done` / `port-paused` / `failed`                                                              |
| `stage_reached` | `infer_port_stage` (port lane) or `infer_dev_stage` (dev lane), run inside the ticket worktree | walker selection follows the lane tagged in §2.1                                                |
| `pr`             | `gh pr view <ticket-id> --json number,url,state` (per ticket) — best-effort | non-zero exit ⇒ no PR, render `—`                                                              |

**Render order**: collect all rows in memory first (parallel MCP+gh calls
allowed and encouraged), then emit the table in one block. Roster order
(priority sort from §2.3) is preserved.

Compute `Flags` for each row by combining the collected signals:

- `need-spec-review` — label present (port pipeline shipped, waiting on human review)
- `in-flight residue` — `dispatcher-port-in-flight` OR `dispatcher-dev-in-flight` still present (failure case — Q2/Q4 will re-pick next sweep)
- `In Review` — status `In Review` (dev shipped, PR open, ready for reviewer)
- empty cell — nothing actionable

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
  ↳ done        : <count>
  ↳ port-paused : <count>
  ↳ failed      : <count>
Skipped    : <count>
  ↳ PR-exists       : <count>
  ↳ branch-exists   : <count>
  ↳ duplicate-label : <count>

## Per-ticket result
| Ticket                    | Lane         | Status        | Stage reached | PR                | Flags             |
|---------------------------|--------------|---------------|---------------|-------------------|-------------------|
| [CAF-212](<url>)          | fresh-port   | 🟡 port-paused | port:ship     | —                 | need-spec-review  |
| [CAF-198](<url>)          | fresh-dev    | 🟢 done        | dev:ship      | [#842](<pr-url>)  | In Review         |
| [CAF-370](<url>)          | recovery-dev | 🔴 failed      | dev:verify    | —                 | in-flight residue |
```

Emoji legend: 🟢 `done` (PR opened, in review), 🟡 `port-paused`
(spec-review HITL), 🔴 `failed` (in-flight residue stays for next sweep).

`<RUN_TS>` is `date -u +%Y%m%dT%H%M%SZ`. The `<PID>` suffix prevents
collision when concurrent invocations slip past the lock (only possible
if the lock was forcibly removed).

### 6.5 Release lock + final stdout summary

```bash
rm -f claude-reports/dispatcher/.lock
```

Print the **same table** built in §6.4 plus a counts line and the
report path:

```
Dispatcher run complete (t+<MM:SS>). <N> tickets:

| Ticket                    | Lane         | Status        | Stage reached | PR                | Flags             |
|---------------------------|--------------|---------------|---------------|-------------------|-------------------|
| [CAF-212](<url>)          | fresh-port   | 🟡 port-paused | port:ship     | —                 | need-spec-review  |
| [CAF-198](<url>)          | fresh-dev    | 🟢 done        | dev:ship      | [#842](<pr-url>)  | In Review         |
| [CAF-370](<url>)          | recovery-dev | 🔴 failed      | dev:verify    | —                 | in-flight residue |

Counts : <N-done> done, <N-paused> port-paused, <N-failed> failed.
Report : claude-reports/dispatcher/<RUN_TS>-<PID>.md
```

STOP.

**Render contract**: stdout and the md file render the **same six
columns in the same order**. Don't drop columns from stdout to fit
terminal width — Claude Code terminals wrap markdown tables fine, and
the value of the table is being identical across the two surfaces (you
can paste either into a PR comment / Slack thread without re-reading
the data).

---

## Guardrails

- **Never spawn an agent before Step 4 lock completes.** A second invocation triggered seconds after the first must see locked tickets, not race-pickable ones.
- **Never use `mode != "bypassPermissions"` on the spawned Agents** — interactive permission prompts inside background agents stall the whole batch.
- **Never use `isolation: "worktree"` on the spawned Agents** — `/port:ff` and `/dev:ff` create their own worktrees; nesting collides.
- **Never auto-stash, auto-checkout, or auto-clean the user's tree.** Pre-flight aborts are explicit; the user fixes their own state.
- **Never proceed past Step 4.0 dry-run gate.** `--dry-run` must be 100% read-only end-to-end.
- **Q1/Q3 omit the `state` filter — never re-add one.** The label is the dispatch signal; status is filtered post-fetch (§2.0). Re-adding `state: unstarted` to Q3 silently drops every post-port dev handoff (port leaves the ticket at `In Progress` and the human reviewer relabels without touching status). Q2/Q4 deliberately use the state name `In Progress` for recovery — that's the only path where a state-name filter is used.
- **Never write to a registered repo's `claude-reports/` from outside that repo.** Only the spawned ff agents touch their own worktree's reports; dispatcher's own writes stay in `<main>/claude-reports/dispatcher/`.
- **Lock is released on every exit path.** Including aborts in Step 0/1, empty-tickets in Step 2/3, port config missing in Step 3.5, dry-run in Step 4, partial lock in Step 4.2, and normal completion in Step 6.5.
- **`branch_prefix: auto` repos require `--team:<KEY>`.** No silent fan-out across teams.
- **All user-facing output is English.** Per repo convention.
- **In-flight labels (Plan X) are managed exclusively by dispatcher + `*:ship`.** `dispatcher-port-in-flight` / `dispatcher-dev-in-flight` are added by §4.1 lock and removed by `/dev:ship` / `/port:ship` on success, by §3.1 PR-exists on stale-state cleanup, and by §4.2 rollback on fresh lanes. They are NEVER added by `/dev:start`, `/port:start`, or any other code path — those routes are for manual users who do not want dispatcher to auto-resume their work. Adding the label outside the dispatcher would silently make manual runs dispatcher-recoverable, breaking the user's expectation of "if I stopped, it stays stopped."

## Linear label state machine (Plan X)

| Label                              | Added by                  | Removed by                                                                              | Meaning                                  |
|------------------------------------|---------------------------|-----------------------------------------------------------------------------------------|------------------------------------------|
| `ready-to-port`                    | human (PM/eng)            | `/port:start` auto / `/ggx-dispatcher` §4.1 lock (fresh-port lane) / `/ggx-work` Step 2.5 | "this ticket is ready for the port pipeline" |
| `ready-to-dev`                     | human (PM/eng)            | `/dev:start` auto / `/ggx-dispatcher` §4.1 lock (fresh-dev lane) / `/ggx-work` Step 2.5 | "this ticket is ready for the dev pipeline" |
| `dispatcher-port-in-flight`        | `/ggx-dispatcher` §4.1 (fresh-port) | `/port:ship` step 13 (success) / `/ggx-dispatcher` §3.1 (PR exists) / `/ggx-dispatcher` §4.2 (rollback fresh-port only) | "dispatcher is mid-run on port; resume if seen next time" |
| `dispatcher-dev-in-flight`         | `/ggx-dispatcher` §4.1 (fresh-dev)  | `/dev:ship` step 3 (success) / `/ggx-dispatcher` §3.1 (PR exists) / `/ggx-dispatcher` §4.2 (rollback fresh-dev only)   | "dispatcher is mid-run on dev; resume if seen next time"  |
| `need-spec-review`                 | `/port:ship` step 13 (auto only) / `/ggx-work` Step 4.4a else-branch (HITL fallback) | human reviewer / `/spec-review` step 6                                                  | "ported spec ready for human spec review" |

Invariant: a ticket should never have both `ready-to-*` AND the matching `dispatcher-*-in-flight` simultaneously (§2.2 conflict check b). If it does, the lock state is inconsistent — dispatcher skips with a comment asking the human to resolve.
