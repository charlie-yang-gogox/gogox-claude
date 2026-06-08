---
name: ggx-dispatcher
description: >
  Manual batch worker for actionable Linear tickets. Sweeps the cwd repo's
  team for `ready-to-port` and `ready-to-dev` tickets, race-locks them, and
  fans out parallel `/ggx-work <ID> --auto` agents (which in turn route to
  `/port:ff` / `/dev:ff` / `/bug:ff` / `/ui-tweak:ff` via `/route`).
  Exception (§5.0): tickets labeled `design bug` (the ui-tweak lane) run
  `/ggx-work <ID> --auto` INLINE in the dispatcher main session instead of
  a spawned subagent, because the ui-tweak audit panel spawns an opus
  judge and nested opus spawns from a general-purpose subagent are
  officially unsupported (sub-agents docs: subagents cannot spawn other
  subagents) — sonnet nesting works in practice today but is undefined
  behavior (see ARCHITECTURE.md "Nested-spawn constraint"). Single-repo,
  cwd-driven; user-invoked from a Claude session opened in the target
  repo on its default branch.
Prerequisite: >
  - Linear MCP authenticated; gh CLI authenticated.
  - cwd is the main worktree of a registered Linear repo, on default branch,
    clean tree (use `--test` to skip branch/clean checks).
  - Environment variable USER_NAME set.
  - For `branch_prefix: auto` repos: pass `--team:<KEY>` (e.g. `--team:CET`).
---

# /ggx-dispatcher — manual batch worker

Find every actionable ticket in the cwd repo's Linear team and dispatch each through `/ggx-work <ID> --auto` in parallel. The `/ggx-work` subagent then calls `/route --non-interactive` to pick the right `/port:ff` / `/dev:ff` / `/bug:ff` / `/ui-tweak:ff` based on the ticket's classification label and worktree state. Each spawned agent runs in `run_in_background: true` — except `design bug` tickets, which run `/ggx-work --auto` inline in the dispatcher main session (§5.0). The dispatcher waits for all to complete before posting fallbacks and emitting a summary.

**Usage**: `/ggx-dispatcher [--dry-run] [--test] [--max-parallel:<N>] [--team:<KEY>]`

- `--dry-run` — Print the planned dispatch and STOP. No Linear writes, no agent spawn.
- `--test` — Skip the default-branch + clean-tree pre-flight checks (still requires main worktree, gh auth, and lockfile).
- `--max-parallel:<N>` — Concurrent dispatch cap. Default `10`, hard cap `20`. Out of range → abort.
- `--workflow` — **Opt-in (Phase A, R5).** Replace the §5.3 N×`Agent` fan-out + §6.1 wait loop with a single `Workflow` tool call driving the dev/port/bug lane (see §5.2). ui-tweak rows still run §5.0-inline. Unset → today's path verbatim. Requires `~/.claude/workflows/ggx-dispatch.workflow.js` (installed by `install.sh`). See `ARCHITECTURE.md` "Nested-spawn constraint" R5 and the Phase-A migration notes.
- `--team:<KEY>` — Required when the cwd repo's `branch_prefix` is `auto`. Allowed (but must equal `branch_prefix`) when `branch_prefix` is concrete.


---

## Label ownership boundary

Two distinct label namespaces drive this pipeline; mixing them up is the
single most common reason for incorrect routing. They are **orthogonal**.

| Namespace                  | Examples                                                       | Owned by                                                                                       | Read by                                                                                       |
|----------------------------|----------------------------------------------------------------|------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------|
| **Workflow labels**        | `ready-to-port`, `ready-to-dev`, `dispatcher-port-in-flight`, `dispatcher-dev-in-flight`, `need-spec-review` | dispatcher + `/port:ship` + `/dev:ship` + `/ggx-work` (scoped — see below) + `/ticket-analyze` (writes `ready-to-*` only) | dispatcher (Q1–Q4 discovery, §4.1 lock, §6.2 fallback); `/ggx-work` Step 2.5 + Step 4.4a; `/spec-review` batch fetch; `/ticket-analyze` Step 1.5 skip filter |
| **Analyzer labels**        | `need-revision`, `need-dependency`                             | `/ticket-analyze` exclusively                                                                  | humans (revision checklist / blocker visibility); `/ticket-analyze` re-run filter. The dispatcher ignores them — a ticket carrying one is by definition not `ready-to-*`. |
| **Classification labels**  | `bug`, `port`, `feature`, `design bug`                         | humans (PM/eng)                                                                                | `/route`; `/ggx-work` Step 2.5 (read-only, lane derivation); `/ticket-analyze` Step 2 (read-only, lane derivation); dispatcher §2.1/§5.0 (read-only, `design bug` ONLY — spawn-shape decision) |

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
- The dispatcher does NOT route by classification labels — once a ticket
  is locked, the `/ggx-work` orchestrator calls `/route`, which reads the
  classification label and decides which `/port:ff` / `/dev:ff` /
  `/bug:ff` / `/ui-tweak:ff` to run. `/ggx-work` Step 2.5 also reads the
  classification label to derive lane for its lifecycle init, but does
  not write it. **One narrow exception (§5.0)**: the dispatcher reads the
  single classification label `design bug` (read-only, whole-string
  case-insensitive) to decide the *spawn shape* — `design bug` tickets
  run `/ggx-work --auto` inline in the main session instead of in a
  spawned subagent. This is a spawn-mechanics decision, not routing: the
  pipeline choice still happens inside `/ggx-work` via `/route`.
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

**Upstream of the dispatcher**: `/ticket-analyze` is the automated
replacement for the manual "human marks ready" step. It sweeps To-Do
tickets assigned to me, judges content completeness + dependency state,
and writes `ready-to-port` / `ready-to-dev` for tickets that pass —
feeding Q1/Q3 discovery directly. Tickets that don't pass get
`need-revision` or `need-dependency` instead (analyzer-owned, mutually
exclusive with `ready-to-*`), which the dispatcher never matches. The
analyzer skips anything already `ready-to-*` or `dispatcher-*-in-flight`,
and re-checks `dispatcher-*-in-flight` immediately before each write, so
the two commands can run concurrently without racing. Manual `ready-to-*`
labeling still works — the analyzer is additive, not mandatory.

## Execution rules

- **No `AskUserQuestion`.** Dispatcher never prompts. Every gate either auto-resolves or aborts with a paste-ready remediation message.
- **All MCP tool calls use whichever Linear MCP server is connected in the session.** Prefer `mcp__claude_ai_Linear__*` (the claude.ai account connector) when present; otherwise fall back to `mcp__linear-server__*` (the project `.mcp.json` server at `https://mcp.linear.app/mcp`). Both target the same Linear workspace and expose identical `list_issues` / `get_issue` / `save_issue` / `save_comment` capability — the prefix difference is purely how the connector was wired up, not a capability or correctness difference. Resolve the prefix once at the start of the run and use it uniformly for every Linear call below.
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
   - Clean tree? Tracked modifications are triaged against a **residue
     allowlist** — known machine-regenerated files are auto-stashed (labeled,
     recoverable) so they cannot waste a batch; anything else is a human's
     in-progress work and still aborts. Untracked files warn but proceed:
     ```bash
     # Residue allowlist: files dependency tooling rewrites on its own (a
     # regenerated pubspec.lock aborted the 2026-06-05 scheduled run for
     # nothing). Deliberately narrow — extend only for files that are
     # (a) machine-written and (b) reproducible from a clean checkout.
     RESIDUE_RE='^(pubspec\.lock|ios/Podfile\.lock|Gemfile\.lock)$'
     CHANGED=$(git diff --name-only HEAD)
     if [ -n "$CHANGED" ]; then
       NON_RESIDUE=$(printf '%s\n' "$CHANGED" | grep -Ev "$RESIDUE_RE" || true)
       # Anything outside the allowlist = a human's in-progress work — STOP.
       # The dispatcher must never sweep real edits into a stash the user
       # didn't ask for ("if I stopped, it stays stopped").
       [ -z "$NON_RESIDUE" ] || abort \
         "Working tree has tracked modifications outside the residue allowlist: $NON_RESIDUE. Stash or commit first: git stash"
       STASH_MSG="ggx-dispatcher residue auto-stash $(date -u +%Y-%m-%dT%H:%M:%SZ)"
       git stash push --message "$STASH_MSG" -- $CHANGED >/dev/null \
         || abort "Auto-stash of residue files failed. Stash manually: git stash"
       # Belt-and-braces: nothing tracked may survive the stash.
       [ -z "$(git status --porcelain --untracked-files=no)" ] || abort \
         "Tracked modifications survived the residue auto-stash. Fix manually: git stash"
       echo "note: residue file(s) auto-stashed as \"$STASH_MSG\" ($(echo $CHANGED)) — recover with: git stash pop (usually unwanted; these files regenerate)"
     fi
     # Untracked files cannot be clobbered by the dispatcher (it never edits the
     # main worktree; agents work in their own worktrees) — note and continue.
     # Known shape: harness runtime residue like .claude/scheduled_tasks.lock
     # (a short-lived lock that aborted the 2026-06-05 06:00 scheduled fire
     # under the old whole-porcelain check, wasting the slot for nothing).
     UNTRACKED=$(git status --porcelain | grep -c '^??')
     [ "$UNTRACKED" -gt 0 ] && echo "note: $UNTRACKED untracked file(s) present — proceeding (agents work in separate worktrees)"
     ```
     The dispatcher does NOT auto-pop the stash at end-of-run — popping onto a
     trunk that moved during the batch could conflict, and a regenerable
     lockfile is rarely wanted back; the stdout note is the handoff. `git
     stash push` without `-u` leaves untracked files in place, so the
     untracked count below is computed after the stash.

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
- The two-label split (port vs dev) lets the dispatcher pick the right §4.1 lock transition and the right §6.4 walker (`infer_port_stage` vs `infer_dev_stage`; dev-lane tickets carrying the §2.1 ui-tweak flag use `infer_ui_stage` instead) at end-of-run table rendering without re-deriving pipeline type from worktree state. Spawn target itself is uniformly `/ggx-work` (§5.1) regardless of lane — pipeline routing happens inside the worker via `/route`; the only lane-conditional spawn mechanics is §5.0's inline execution for ui-tweak-flagged tickets.
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

Additionally, tag each dev-lane entry (`fresh-dev` / `recovery-dev`) with
an orthogonal **`ui-tweak` flag** when its merged `labels[]` contains the
classification label `design bug` (whole-string, case-insensitive —
read-only; see §5.0). The flag does NOT change the lock transition (§4.1
still swaps `ready-to-dev` ↔ `dispatcher-dev-in-flight`) — it changes the
**spawn shape** (§5.0: inline instead of spawned) and the **walker /
outcome rules** (§6.2: `infer_ui_stage` + the ui-tweak `done` predicate).
On a re-sweep (recovery-dev) the flag is re-derived from the same label —
a failed ui-tweak ticket re-enters the inline lane, never the spawned dev
walker (whose `done` predicate expects openspec markers a ui-tweak
worktree never creates).

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
per §5.1 (ui-tweak-flagged rows show the `(inline)` prefix per §5.0); lane
+ ui-tweak flag drive the §4.1 lock-label transition, the §5.0 spawn shape,
and the §6.4 end-of-run table's choice of `infer_port_stage` vs
`infer_dev_stage` vs `infer_ui_stage`.

### 4.1 Init protocol — apply per ticket, sequentially

<!-- SYNC: ticket-init lives in commands/dev/_ticket-init.md. The 4 callers
     (port:start Step 5a, dev:start Step 3c, ggx-dispatcher Step 4.1,
     ggx-work Step 2.5) all invoke it; do not re-inline the block here.

     Step 1 (the dispatcher-specific label swap to `dispatcher-*-in-flight`)
     remains inline — it is INTENTIONALLY dispatcher-only under Plan X.
     /_ticket-init handles only the lane-agnostic moves (status / assignee /
     estimate / starting comment / drop ready-to-*). It MUST NOT add any
     `dispatcher-*-in-flight` label — adding that outside the dispatcher
     would silently flip manual runs into dispatcher-recoverable state. -->

Each ticket's lock depends on its selection lane (§2.1). `<inflight>` resolves to `dispatcher-port-in-flight` for port lanes, `dispatcher-dev-in-flight` for dev lanes. `<lane-short>` resolves to `port` (for `fresh-port` / `recovery-port`) or `dev` (for `fresh-dev` / `recovery-dev`).

For each ticket, in order:

1. **Label swap** via `mcp__claude_ai_Linear__save_issue`:
   - Lanes `fresh-port` / `fresh-dev`: remove `ready-to-port` / `ready-to-dev`, **add `<inflight>`**. Atomic compound op in a single `save_issue` call.
   - Lanes `recovery-port` / `recovery-dev`: in-flight label already present — call is a no-op idempotently (still send the same payload so the contract is uniform).
2. Invoke `/_ticket-init <ticket-id> <lane-short>` (idempotent; safe to re-call). Drives status → `In Progress`, drops `ready-to-<lane-short>` (no-op for recovery lanes — already removed at original lock time), assignee → self, estimate=1 if null, and posts a `<!-- ticket-init:v1 lane=<lane-short> -->` starting comment if absent. For recovery lanes the comment-marker check short-circuits the comment write so resumed dispatchers do not double-comment.

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
3. **Slack alert (best-effort, opt-in)**: invoke
   `/_slack-notify batch-abort detail=<failed-ticket, which MCP call failed, tickets needing manual unlock>`.
   This is the only abnormal exit that never reaches the §6.5 digest —
   without it a mid-lock abort leaves zero Slack record. The helper is
   fail-soft and always exits 0; do not let its outcome change this
   abort path. (Mapping/config/send live in `commands/dev/_slack-notify.md`
   — do not re-inline.)
4. STOP — release lock — do NOT spawn any agents.

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
- `command` = the exact string Step 5 will pass to its spawn — uniformly `/ggx-work <ID> --auto` per §5.1; ui-tweak-flagged rows render it as `(inline) /ggx-work <ID> --auto` (§5.0 — same command, inline execution; see the roster note below).
- `link` = the issue `url` field returned by the Step 2 `list_issues` calls. Cache the url alongside the ticket id from Step 2 so this column does not require a re-fetch.

While building this table, accumulate the same rows into an in-memory `DISPATCH_ROSTER` value — TSV, one line per ticket, format `<ticket-id>\t<lane>\t<absolute-worktree-path>\t<url>\t<ui-tweak|->`. Worktree path = `realpath ../<TICKET-ID>` per the `/add-worktree` convention; `url` is the issue url cached from Step 2; the 5th field is `ui-tweak` when the §2.1 ui-tweak flag is set, else `-`. **Held in session state only — do NOT write a roster.tsv file.** §6.4 uses this roster to render the end-of-run table (lane lookup, walker selection, Ticket-link column). Rows with the ui-tweak flag render their `command` column as `(inline) /ggx-work <ID> --auto` in the §4.0/§4.3 tables so the spawn shape is visible up-front.

**Under `--workflow` (§5.2):** in addition to the in-memory TSV, serialize the **non-ui-tweak** rows to a JSON array — `[{ "ticketId", "lane", "worktreePath", "url", "uiTweak": false }, ...]` — for the script's `args`. ui-tweak rows are EXCLUDED from this JSON (they still run §5.0-inline in Phase A); they remain in the TSV so §6.4 can still render them. The JSON is the `DISPATCH_ROSTER_JSON` value consumed by §5.2.

### 5.0 Spawn-shape decision — `design bug` tickets run INLINE (ui-tweak lane)

Tickets whose §2.1 roster row carries the **ui-tweak flag** (`design bug`
classification label present) are **excluded from the §5.3 parallel spawn**.
For each of them, the dispatcher runs the SAME command — `/ggx-work <ID>
--auto` — **inline in its own main session**, sequentially, AFTER the §5.3
spawns have been emitted (so the spawned batch runs in the background while
the inline lane executes).

**Why inline.** `/route` resolves a `design bug` ticket to `/ui-tweak:ff`,
whose audit stage spawns a decorrelated dual-judge panel:
`ui-verify-agent` (**sonnet**) + `dev-reviewer` (**opus**). Nested spawns
are officially unsupported (sub-agents docs: subagents cannot spawn other
subagents — see `ARCHITECTURE.md` "Nested-spawn constraint"); nested sonnet
spawns from a general-purpose subagent work in practice today but are
undefined behavior (see the §5.3 `model` rationale below and
`commands/dev/dev/apply.md`), and **nested opus spawns do not work at
all** — inside a spawned worker the opus judge
would fail, and the panel's tier-decorrelation (`dev-reviewer.md`: the
tier-pin is why the two judges' misses are not positively correlated) is
load-bearing: downgrading the judge to sonnet would collapse both judges
to one tier. Running the lane in the main session — where opus nesting
works exactly as in the interactive designer flow — preserves the audit
guarantee verbatim: both judges, both tiers, both-must-be-CLEAR.

Consequences, stated honestly:

- This is the ONE lane-specific branch in the otherwise-uniform fan-out.
  The price of keeping the audit panel intact.
- Design-bug tickets run **serially** in the main session, not in
  parallel with each other (they do overlap with the spawned background
  batch). Acceptable while design bugs are a minority of any batch;
  revisit (model-override fallback: spawn the worker and pass
  `dev-reviewer` a `model: "sonnet"` override, accepting weaker
  lens-only decorrelation) only if volume makes this a bottleneck.
- The inline lane shares the dispatcher session's context budget. Keep
  inline tickets LAST (after spawns) so a context-heavy ui-tweak run can
  never delay the parallel batch.
- Everything else is uniform: §4.1 lock (`ready-to-dev` ↔
  `dispatcher-dev-in-flight`), `/ggx-work`'s Step 2.5 `/_ticket-init
  lane=dev`, the §6.2 fallback writes, and the §6.4/§6.5 reporting all
  treat it as a dev-lane ticket — only the walker + `done` predicate
  differ (§6.2 step 2/4).

### 5.1 Uniform spawn command (all four lanes; §5.0 inline exception)

```
/ggx-work <ID> --auto
```

Port and dev lanes share one spawn target. The `/ggx-work` subagent calls
`/route --non-interactive` to decide which ff to run; `/route` reads the
ticket's classification label (`bug` / `port` / `feature` / `design bug`)
plus the worktree filesystem (port-ship marker, `.dev/*` markers) and
recommends `/port:ff`, `/dev:ff`, `/bug:ff`, or `/ui-tweak:ff`. Recovery
lanes (`recovery-port` / `recovery-dev`) dispatch the same command because
the ff walkers (`infer_port_stage` / `infer_dev_stage` / `infer_ui_stage`)
resume idempotently from their own marker files — the in-flight label is
the dispatcher's signal that the worktree exists, not a routing hint that
needs to be carried into the spawned subagent. The command string is the
same for ui-tweak-flagged tickets; only the execution context differs
(inline per §5.0 instead of a spawned subagent).

**Figma URL detection is no longer dispatcher's job.** Previously the
dispatcher pre-scanned the ticket description for `figma\.com/...` and
attached `--no-figma` to the dev spawn. That detection has moved into
`/dev:start` Step 4 and now scans description **and** comments, so a
designer dropping a Figma link as a follow-up comment no longer routes
the ticket through the SKIPPED short-circuit. Dispatcher just passes
`/ggx-work <ID> --auto`; the rest is determined downstream.

### 5.2 Workflow fan-out (opt-in — `--workflow`, Phase A of the R5 migration)

When `--workflow` is set, the dev/port/bug lane is driven by a single
`Workflow` tool call instead of the §5.3 N×`Agent` fan-out. This is the
Phase-A migration documented in `ARCHITECTURE.md` "Nested-spawn
constraint" R5. **ui-tweak rows are NOT affected** — they still run
§5.0-inline in this session (Phase B moves them into the script). When
`--workflow` is unset, skip this entire section and use §5.3 verbatim.

**Why a script, not deeper nesting:** every agent the script spawns is
level-1 (the nested-spawn constraint does not apply between a workflow
script and its agents). Phase A does not yet exploit that for ui-tweak —
it only moves the dev/port/bug fan-out + wait + per-ticket fallback +
aggregation into deterministic JS so intermediate results stay in script
variables instead of the dispatcher's context. **R1 is unchanged**: the
heavy ff stages still inline inside each worker agent.

**Permissions precondition (load-bearing — read before first use).**
Workflow agents run at `acceptEdits` and inherit the **session's tool
allowlist**; they have **no way to answer a mid-run permission prompt**, so
any `git`/`gh`/`openspec`/Linear-MCP call not on the allowlist **silently
stalls the whole run**. Before relying on `--workflow`, ensure the
user-global `~/.claude/settings.json` `permissions.allow` covers every
shell/MCP family the ff pipelines touch (git, gh, openspec, `yq`, the platform
test/format toolchain). The allowlist lives user-global, NOT in any
repo's `.claude/settings.json`, because the dispatcher runs in the **target
repo**, not in gogox-claude.

**Linear MCP — BOTH prefixes must be allowlisted.** Per the §"Label ownership
boundary" rule, the run uses whichever Linear server is connected: it prefers
`mcp__claude_ai_Linear__*` and **falls back to `mcp__linear-server__*`** when
the claude.ai connector is not authenticated (both expose identical
capability). An interactive session resolves this fine, but a **background
workflow agent cannot fall back interactively** — so the allowlist MUST cover
**both** `mcp__claude_ai_Linear__*` AND `mcp__linear-server__*`, or a worker
silently stalls on the first Linear write whenever claude.ai is not authed.
Also allowlist `mcp__claude_ai_Atlassian_Rovo__*` (Jira) and
`mcp__plugin_figma_figma__*` (figma stage). The Phase-A e2e (dummy tickets) is
the gate that confirms coverage — watch for a background run that goes quiet.
(Observed in the 2026-06-08 CAF-548 run: claude.ai Linear was unauthed and the
pipeline fell back to `linear-server`; that ran inline so it survived, but a
workflow worker would have stalled.)

Steps:

1. **Build `DISPATCH_ROSTER_JSON`** — the non-ui-tweak rows from §4.3 as a
   JSON array (`{ticketId, lane, worktreePath, url, uiTweak:false}`).

2. **Persist `run.json`** for crash recovery (§4.2 / §5.2-resume). Write
   `claude-reports/dispatcher/run.json` with `{ scriptPath, roster:
   DISPATCH_ROSTER_JSON, ts }` (atomic `mktemp` + `mv`). `ts` comes from a
   shell `date` call — NOT from inside the script (the script's clock is
   frozen for resume determinism).

3. **Print the §4.3 table** (same as §5.3 — the table is the review), then
   **in the same turn** invoke the `Workflow` tool:
   - `scriptPath`: `$HOME/.claude/workflows/ggx-dispatch.workflow.js`
   - `args`: the `DISPATCH_ROSTER_JSON` value (an actual JSON array, NOT a
     stringified one — the script reads `args` as a live array).
   The tool returns immediately with a `runId` and runs in the background;
   record the `runId` into `run.json` (second atomic write) so a same-session
   resume can pass `resumeFromRunId`.

4. **Run the ui-tweak inline lane** exactly as §5.3/§5.0 describe (each
   `design bug` ticket sequentially, in this session), concurrent with the
   background workflow. ui-tweak completions are tracked in-session as today.

5. **Consume the workflow result** in place of §6.1's wait loop. The
   `Workflow` completion notification carries the script's return value:
   `{ counts, rows }` where each row is the validated `WORK_SCHEMA` object
   (`ticketId, outcome, prUrl, stage, error`). Merge `rows` with the
   ui-tweak inline outcomes, then hand the combined set to §6.4 directly —
   **do NOT re-derive outcomes via per-ticket `get_issue`** (the script's
   `outcome`/`stage` are authoritative; §6.2's per-ticket Linear failure
   write already ran INSIDE the script's `runFallback` stage). §6.2's
   algorithm still applies as the fallback path for the ui-tweak inline
   rows only.

**Resume (same session only).** If the dispatcher is interrupted and
re-invoked in the same session, relaunch with
`Workflow({scriptPath, resumeFromRunId: <runId from run.json>})` — completed
`agent()` calls return cached results, only unfinished tickets re-run. After
the session exits, `resumeFromRunId` is dead; recovery falls back to the
§4.2 / §6.2 label-rescan path (the `dispatcher-*-in-flight` labels are the
durable resume signal — `run.json` is only a same-session cache). If `run.json`
has `roster` but no `runId` (the harness died between the `Workflow` call and
the second atomic write), `resumeFromRunId` is likewise unavailable — recovery
is exactly the §4.2 label-rescan path. A partially written `run.json` is
therefore a documented, safe state, not an error.

### 5.3 Spawn

**(Skip this entire section when `--workflow` is set — see §5.2.)**

**You MUST emit the §4.3 dispatch table and all N `Agent` tool calls in a single assistant message.** Print the table text first, then the N parallel `Agent` calls — back-to-back, no turn break, no intermediate "ready to spawn?" pause. Do not narrate between calls, do not split across turns, do not group by team. The orchestrating LLM may be tempted to interleave prose ("now spawning ticket X...") between calls — this serializes the join and defeats the parallelism. It may also be tempted to end the turn after the table so the user can review — do not. The table is the review; spawning follows immediately in the same turn. Narration belongs after the join in Step 6.

**ui-tweak-flagged tickets (§5.0) are NOT spawned here.** N = the roster
minus ui-tweak rows. After emitting the table + the N spawn calls, run
each ui-tweak-flagged ticket's `/ggx-work <ID> --auto` **inline in this
session, sequentially**, while the spawned agents run in the background.
The inline runs honor the same terminal conditions as the spawn prompt
below; each inline completion increments the same §6.1 `joined` counter
(over the FULL roster count, spawned + inline). If the roster is
ui-tweak-only (N=0), still print the table first, then run the inline
lane.

Single message, N parallel `Agent` calls (one per non-ui-tweak ticket):

- `description`: `Dispatch <ticket-id> via /ggx-work`
- `subagent_type`: `general-purpose`
- `model`: `"opus"` — required because every pipeline that runs through
  this dispatcher eventually does heavy reasoning inline in this
  `general-purpose` subagent:
    - Dev lane: `/dev:apply --auto` runs `/opsx:apply` inline (see
      `commands/dev/dev/apply.md:15-17`); `/dev:review` runs
      `/code-review --auto` which inlines the git-branch-code-reviewer
      contract (see `commands/dev/code-review.md` step 2 mode table).
    - Port lane: `/port:explore --auto` runs the dev-consult contract
      inline, and `/port:synth --auto` runs the synth loop inline (see
      `commands/dev/port/explore.md` step 6 and `commands/dev/port/synth.md`
      step 6 mode tables).
  All four paths exist because nested-Agent spawns from a subagent
  fail (`Task`/`Agent` not available), so the heavy work must run in
  the dispatcher-spawned subagent itself — which therefore needs opus
  quality reasoning. The `/port:plan` stage still spawns `pm-agent` /
  `designer-agent` (both sonnet) and `/dev:verify` still spawns
  `verify-agent` (sonnet) — nested spawns are officially unsupported
  (sub-agents docs: subagents cannot spawn other subagents — see
  `ARCHITECTURE.md` "Nested-spawn constraint"), but nested sonnet spawns
  from a subagent work in practice today (undefined behavior; see
  `commands/dev/dev/apply.md` rationale), so those stages are NOT
  inlined.
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

  Cosmetic contract: /ggx-work emits a line of the form
  `[ggx-work-result] outcome=<done|port-paused|failed> ticket=<ID>` as
  the last informational line before exit at every terminal point. Pass
  that line through verbatim in your return message — the dispatcher's
  §6.1 progress display reads it as a best-effort signal. The
  dispatcher's authoritative outcome classification (§6.2 / §6.4) is
  derived from filesystem markers + Linear labels + PR state, NOT from
  this line, so it is safe if the line is absent or your wording
  differs.
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

When the roster contains ui-tweak-flagged tickets, append:

```
Running <M> design-bug ticket(s) inline (ui-tweak lane, §5.0): <id, id, …>
```

then immediately begin the first inline `/ggx-work <ID> --auto` run.

No separate progress poller. Per-ticket completion is surfaced by §6.1
when each agent's background notification arrives; end-of-run rendering
is the §6.4 summary table.

---

## Step 6: Wait, fallback, finalize

### 6.1 Wait for completions

**Under `--workflow` (§5.2):** there is no `joined`-counter wait loop for
the spawned lane — the single `Workflow` tool call returns its
`{ counts, rows }` when the whole background run finishes, and §5.2 step 5
feeds it straight to §6.4. The only in-session waiting is for the ui-tweak
inline lane (which completes synchronously as today). Skip the rest of this
section for the spawned lane; it applies verbatim only on the non-`--workflow`
path.

The dispatcher session waits here for every spawned agent's
background-completion notification. **No sibling poller process.** Each
notification arrives event-driven from the harness; printing a 30s tick
table on top of those events was redundant noise — the table re-rendered
the same per-ticket state that the notification line already announces.

Inline ui-tweak runs (§5.0) complete synchronously in this session — when
each finishes, increment `joined` and emit the same `[<joined>/<N>]` line
(its `[ggx-work-result]` outcome line is read directly from the inline
output). `N` counts the FULL roster (spawned + inline).

Maintain an in-memory `joined` counter. On each background-completion
notification:

1. Increment `joined`.
2. **Best-effort cosmetic parse** of the outcome line from the agent's
   return message (`$AGENT_OUTPUT`):
   ```bash
   outcome=$(printf '%s\n' "$AGENT_OUTPUT" \
     | grep -oE '^\[ggx-work-result\] outcome=[a-z-]+' \
     | tail -1 \
     | awk -F= '{print $2}')
   ```
   If matched (`outcome` ∈ `{done, port-paused, failed}`), emit:
   ```
   [<joined>/<N>] <ticket-id> finished (<outcome>).
   ```
   If absent or malformed, emit (no parenthetical):
   ```
   [<joined>/<N>] <ticket-id> finished.
   ```

   The agent's text NEVER drives §6.2 / §6.4 classification — this parse
   feeds only this live UX line. **The §6.1 line is for live UX.** The
   settled outcome used by §6.2 fallback writes and §6.4 summary
   rendering is derived authoritatively in §6.2 from Linear labels +
   walker stage + PR state — not from this cosmetic parse. A future
   refactor that drops the `[ggx-work-result]` line from `/ggx-work`
   degrades only this progress line, never classification.

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

### 6.2 Per-ticket fallback — authoritative outcome derivation

**Under `--workflow` (§5.2): applies to the ui-tweak inline rows only.** The
spawned dev/port/bug rows get their authoritative `outcome`/`stage` from the
script's validated `rows[]`, and the per-ticket Linear failure write already
ran inside the script's `runFallback` stage — do NOT re-run this derivation
or re-post a failure comment for them. Run the algorithm below only for the
ui-tweak inline rows (and on the non-`--workflow` path, for all rows as today).

The agent's text from §6.1 is cosmetic. The authoritative classification
of each ticket's outcome is derived here from three independent signals
the dispatcher already has access to: settled Linear state, walker-read
worktree markers, and PR state. No new file, no new helper — everything
runs inline per ticket.

For each ticket in `DISPATCH_ROSTER` (carry the `lane` tagged at §2.1):

1. **Re-fetch Linear** via `mcp__claude_ai_Linear__get_issue <ticket-id>`
   → `labels[]`, `status.name`. This is a fresh read, settled after the
   agent's `/port:ship` / `/dev:ship` writes have completed. ONE call per
   ticket — the same call §6.4 used to make later; we just do it here
   instead. No new MCP cost.
2. **Run the walker** inside the ticket worktree (`../<ticket-id>`):
   - lane ∈ {`fresh-port`, `recovery-port`} → `infer_port_stage` →
     emits one of `start` / `explore` / `plan` / `synth` / `revise` /
     `ship` / `done`.
   - lane ∈ {`fresh-dev`, `recovery-dev`} **with the ui-tweak flag**
     (roster 5th field — §2.1) → `infer_ui_stage` (from
     `commands/design/ui-tweak/ff.md`) → emits one of `start` / `apply` /
     `preview` / `audit` / `commit` / `demo` / `pr` / `review` / `done`.
     **Never run `infer_dev_stage` on a ui-tweak worktree** — it has no
     `.dev/mode.md` (absent ⇒ "feature" branch) and no openspec dirs, so
     the dev walker would mis-walk it and its `done` could never fire.
   - lane ∈ {`fresh-dev`, `recovery-dev`} without the flag →
     `infer_dev_stage` → emits one of `start` / `figma` / `align` /
     `apply` / `verify` / `review` / `ship` / `done`. (Bug-mode tickets
     go through `infer_bug_stage_safe` from the same dispatch, which
     emits a subset of the same vocabulary; treat them uniformly.)
3. **Query PR state** — resolve by HEAD BRANCH, not ticket id. The worktree
   branch is `<prefix>/<TICKET-ID>` (e.g. `fix/CAF-548`), so `gh pr view
   "$TICKET_ID"` cannot find the PR and returns empty — which would make the
   §6.2 derivation mis-read a shipped ticket as having no PR. Resolve via the
   worktree's branch (`worktreePath` is the roster field from §4.3):
   ```bash
   branch=$(git -C "$worktreePath" branch --show-current 2>/dev/null)
   pr_state=$(gh pr list --head "$branch" --state all --json state -q '.[0].state' 2>/dev/null)
   ```
   Possible values: `OPEN` / `MERGED` / `CLOSED` / empty (no PR).
4. **Derive `outcome`** from the three signals — rules are lane-aware:

   **Dev lane** (`fresh-dev` / `recovery-dev`, no ui-tweak flag):
   - `outcome = done` ⟺ `walker == done` (the walker's own `done`
     predicate already requires the openspec archive dir + a clean
     `code-review.md` + `gh pr view ... state == OPEN`, so this is the
     strongest single signal; no need to re-check those components).
   - `outcome = failed` ⟺ `walker != done` AND
     `dispatcher-dev-in-flight ∈ labels` (the agent claimed it, the
     `/dev:ship` finalize never ran).
   - `port-paused` is impossible on the dev lane — exclude it from the
     decision table.

   **ui-tweak lane** (`fresh-dev` / `recovery-dev` WITH the ui-tweak
   flag) — a DISTINCT branch, never the dev rule (a ui-tweak worktree
   never creates `openspec/changes/archive/<n>`, so the dev `done`
   predicate can never fire and would misclassify every shipped design
   bug as `failed`):
   - `outcome = done` ⟺ `infer_ui_stage == done` AND `pr_state == OPEN`
     AND `claude-reports/<ticket-id>/code-review.md` present (the
     ui-tweak walker's own `done` predicate — PR open + code-review).
   - `outcome = failed` ⟺ not `done` AND
     `dispatcher-dev-in-flight ∈ labels` (e.g. apply failed, build
     repair budget exhausted, or audit BLOCKED — `/ui-tweak:ff --auto`
     exits non-zero with the loud stderr line; the in-flight label stays
     as the resume signal).
   - `port-paused` is impossible here too.

   **Port lane** (`fresh-port` / `recovery-port`):
   - `outcome = port-paused` ⟺ `need-spec-review ∈ labels` (canonical
     port handoff — either `/port:ship --auto` step 13 added it, or
     `/ggx-work` Step 4.4a's HITL fallback did).
   - `outcome = done` ⟺ walker reached a terminal port stage (`done` —
     i.e. PR OPEN) AND `need-spec-review ∉ labels`. Rare today
     (port-only tickets that ship a PR without triggering spec-review),
     but allowed.
   - `outcome = failed` ⟺ `walker != done` AND
     `dispatcher-port-in-flight ∈ labels` AND `need-spec-review ∉ labels`.

   If none of the rules above match — meaning the three signals
   disagree in an unexpected combination (e.g. dev lane with
   `walker == done` but `dispatcher-dev-in-flight` still present, or a
   port lane with no in-flight label, no `need-spec-review`, and walker
   not at terminal) — classify as `failed` and log a single WARN line so
   the user can investigate:
   ```
   WARN: outcome-derivation-ambiguous <ticket-id> lane=<lane> labels=<csv> walker=<stage> pr=<state>
   ```
   Continue processing other tickets; do not abort the batch.

5. **Fallback writes** based on the derived `outcome`:

   | derived `outcome`  | required Linear end state                                                            | fallback if missing                                                                                                                                                                                                                  |
   |--------------------|--------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
   | dev `done`         | status `In Review` AND `dispatcher-dev-in-flight ∉ labels`                           | `save_issue` to set status `In Review` and remove `dispatcher-dev-in-flight`. **Re-fetch labels via `get_issue` immediately before the write** to avoid racing a slow `/dev:ship` finalize (same diff-then-write idempotency pattern as `/port:ship` step 13). |
   | ui-tweak `done`    | status `In Review` AND `dispatcher-dev-in-flight ∉ labels`                           | Same write as dev `done`. NOTE: unlike `/dev:ship`, ui-tweak's `pr` stage deliberately does NOT transition ticket status (its only ticket write is the read-only PR-link comment), so for ui-tweak this fallback is the **primary** status writer, not a safety net — expect it to fire on every shipped design bug. |
   | port `port-paused` | `need-spec-review ∈ labels` AND `dispatcher-port-in-flight ∉ labels`                  | `save_issue` to add `need-spec-review` and remove `dispatcher-port-in-flight`. Same re-fetch-before-write to avoid racing a slow `/port:ship`.                                                                                          |
   | port `done`        | `dispatcher-port-in-flight ∉ labels`                                                  | `save_issue` to remove `dispatcher-port-in-flight`. Re-fetch labels first.                                                                                                                                                              |
   | any `failed`       | `dispatcher-*-in-flight` STAYS (resume signal for Q2/Q4 on the next sweep)            | If no failure comment exists yet on the ticket, post one via `save_comment`. Do NOT remove the in-flight label — that's the resume signal.                                                                                              |

6. Carry the derived `outcome` (plus the fresh `labels` / `status.name` /
   `walker_stage` / `pr_state` signals) into the in-memory roster row
   that §6.4 will consume. **No new file — bash variables / inline
   payload only.** §6.4 reuses these signals without a second MCP / gh
   round trip.

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

**Under `--workflow` (§5.2):** for the spawned dev/port/bug rows, `outcome`,
`stage_reached`, and `prUrl` come from the script's returned `rows[]`
(validated `WORK_SCHEMA` objects) — NOT from a per-ticket `get_issue` /
walker re-derivation. The `labels` / `status.name` columns may still be
fetched here if you want the live Linear state for display, but the outcome
is authoritative from the script. The ui-tweak inline rows use the §6.2
sources below as today.

| Signal           | Source                                                                 | Notes                                                                                          |
|------------------|------------------------------------------------------------------------|------------------------------------------------------------------------------------------------|
| `labels`         | §6.2-derived (shared) — `get_issue` was made there                      | shared with §6.2 — no extra MCP round trip. If §6.2 didn't run (e.g. `--dry-run`), re-fetch here. |
| `status.name`    | same call as `labels`                                                  | shared with §6.2 — `In Progress` / `In Review` etc.                                              |
| `url`            | from roster (cached at Step 2)                                         | no re-fetch                                                                                     |
| `outcome`        | §6.2-derived value (`done` / `port-paused` / `failed`) carried in-memory | authoritative — derived from `labels` + walker + PR per the §6.2 algorithm. If §6.2 didn't run (e.g. `--dry-run`), recompute inline from `labels` + walker + PR here. The agent's text is NOT consulted. |
| `stage_reached` | §6.2-derived walker output (`infer_port_stage` / `infer_dev_stage` / `infer_ui_stage` already ran there) | shared with §6.2 — no extra worktree shell-out. Walker selection follows the lane + ui-tweak flag tagged in §2.1. ui-tweak rows render their stage with a `ui:` prefix (e.g. `ui:audit`) so the table distinguishes them from dev stages. |
| `pr`             | §6.2-derived `pr_state` (shared) — augmented here with `number,url` via `gh pr list --head "$branch" --state all --json number,url,state -q '.[0]'` (branch-based, same as §6.2 step 3 — NOT `gh pr view <ticket-id>`, which fails when the branch is `<prefix>/<TICKET-ID>`) if needed for the link column | shared with §6.2 for the state; non-zero exit ⇒ no PR, render `—`                              |

**Render order**: collect all rows in memory first (parallel MCP+gh calls
allowed and encouraged), then emit the table in one block. Roster order
(priority sort from §2.3) is preserved.

The Result counts block below (`done` / `port-paused` / `failed`) sums
over the §6.2-derived `outcome` values per ticket — never over the
agent's text from §6.1.

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

**Slack digest (best-effort, opt-in)** — after printing the block above,
post ONE run-level digest via `/_slack-notify digest ggx-dispatcher`:

<!-- SYNC: the status mapping, message grammar, config gates, and send
     block live in commands/dev/_slack-notify.md. Do not re-inline. -->

- Header stats: `team=<team_key>`, `processed=<N>`, `done=<N-done>`,
  `port_paused=<N-paused>`, `failed=<N-failed>`, `skipped=<skip-count>`.
- One raw-signal line per §6.4 row, built from the **§6.2-derived
  authoritative outcome + Flags + pr** already in memory (NEVER from the
  cosmetic §6.1 `[ggx-work-result]` parse), format per `_slack-notify.md`
  Inputs (`title` = the ticket title from the same §6.2 `get_issue`
  snapshot — the helper truncates to 60 chars):
  `<ticket-id> <url> <lane> done flags=<In-Review|-> pr=<pr-url|-> title="<title>"` /
  `... port-paused flags=<need-spec-review|-> title="<title>"` /
  `... failed flags=<in-flight-residue|-> stage=<stage_reached> reason=<short> title="<title>"`.
  ui-tweak-flagged tickets ride the same dev-lane line format (`<lane>` is
  the selection lane, e.g. `fresh-dev`; the `ui:`-prefixed
  `stage_reached` is the only visible difference) — no new outcome token,
  no `_slack-notify.md` change.
- Include the `Report :` path so the Slack message links back to the
  full table.

The helper owns emoji/token mapping, `#needs-human` tagging, config
discovery (its Step 0 reads the fixed path
`~/.claude/commands/profiles/ggx-slack.json` and no-ops silently when
absent/disabled), and the fail-soft send; it always exits 0 — its
outcome never affects this step. **Invoke it unconditionally — do NOT
pre-check the config file yourself and skip the call when you don't
find it.** `--dry-run` stops at §4.0 and never reaches here, so dry
runs are naturally Slack-silent.

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
- **Never auto-checkout or auto-clean (discard) the user's tree.** The ONE permitted mutation is the Step 1.3 labeled auto-stash of **residue-allowlisted files only** (`pubspec.lock`-class machine-regenerated lockfiles) — non-destructive, announced on stdout, recoverable via `git stash pop`, never auto-popped. Tracked modifications OUTSIDE the allowlist are a human's in-progress work and still abort — the dispatcher never sweeps real edits into a stash the user didn't ask for. Checkout, reset, and clean stay forbidden; remaining pre-flight aborts are explicit and the user fixes their own state.
- **Never proceed past Step 4.0 dry-run gate.** `--dry-run` must be 100% read-only end-to-end.
- **Q1/Q3 omit the `state` filter — never re-add one.** The label is the dispatch signal; status is filtered post-fetch (§2.0). Re-adding `state: unstarted` to Q3 silently drops every post-port dev handoff (port leaves the ticket at `In Progress` and the human reviewer relabels without touching status). Q2/Q4 deliberately use the state name `In Progress` for recovery — that's the only path where a state-name filter is used.
- **Never write to a registered repo's `claude-reports/` from outside that repo.** Only the spawned ff agents touch their own worktree's reports; dispatcher's own writes stay in `<main>/claude-reports/dispatcher/`.
- **Lock is released on every exit path.** Including aborts in Step 0/1, empty-tickets in Step 2/3, port config missing in Step 3.5, dry-run in Step 4, partial lock in Step 4.2, and normal completion in Step 6.5.
- **`branch_prefix: auto` repos require `--team:<KEY>`.** No silent fan-out across teams.
- **All user-facing output is English.** Per repo convention.
- **Slack notify points are exclusively §4.2 (batch-abort) and §6.5 (digest).** Both go through `/_slack-notify` (opt-in via `~/.claude/commands/profiles/ggx-slack.json` — the install.sh symlink to `commands/dev/profiles/ggx-slack.json`; fail-soft, always exit 0). **Invoke the skill unconditionally — NEVER probe the config path yourself to decide whether to call it.** Config discovery, the enabled gate, and the silent no-op all live in `/_slack-notify` Step 0; a caller that hand-checks paths and guesses wrong silently drops the digest (this happened on 2026-06-05 — two stale paths were probed, the run mis-concluded "unconfigured", and the digest was skipped). NEVER insert a notify call between the §4.3 dispatch table and the §5.3 spawns — the table + N `Agent` calls must stay in one assistant message; any tool call in between breaks that contract. No per-ticket or batch-start pings — the §6.5 digest is the batch's single Slack surface.
- **In-flight labels (Plan X) are managed exclusively by dispatcher + `*:ship`.** `dispatcher-port-in-flight` / `dispatcher-dev-in-flight` are added by §4.1 lock and removed by `/dev:ship` / `/port:ship` on success, by §3.1 PR-exists on stale-state cleanup, and by §4.2 rollback on fresh lanes. They are NEVER added by `/dev:start`, `/port:start`, or any other code path — those routes are for manual users who do not want dispatcher to auto-resume their work. Adding the label outside the dispatcher would silently make manual runs dispatcher-recoverable, breaking the user's expectation of "if I stopped, it stays stopped."

## Linear label state machine (Plan X)

| Label                              | Added by                  | Removed by                                                                              | Meaning                                  |
|------------------------------------|---------------------------|-----------------------------------------------------------------------------------------|------------------------------------------|
| `ready-to-port`                    | human (PM/eng)            | `/_ticket-init` (via `/port:start`, `/ggx-work` Step 2.5, `/ggx-dispatcher` §4.1) / `/ggx-dispatcher` §4.1 lock (fresh-port lane, swaps to `<inflight>`) | "this ticket is ready for the port pipeline" |
| `ready-to-dev`                     | human (PM/eng)            | `/_ticket-init` (via `/dev:start`, `/ggx-work` Step 2.5, `/ggx-dispatcher` §4.1) / `/ggx-dispatcher` §4.1 lock (fresh-dev lane, swaps to `<inflight>`) | "this ticket is ready for the dev pipeline" (covers feature, bug, AND ui-tweak/`design bug` tickets — the classification label picks the pipeline downstream) |
| `dispatcher-port-in-flight`        | `/ggx-dispatcher` §4.1 (fresh-port) | `/port:ship` step 13 (success) / `/ggx-dispatcher` §3.1 (PR exists) / `/ggx-dispatcher` §4.2 (rollback fresh-port only) | "dispatcher is mid-run on port; resume if seen next time" |
| `dispatcher-dev-in-flight`         | `/ggx-dispatcher` §4.1 (fresh-dev)  | `/dev:ship` step 3 (success) / `/ggx-dispatcher` §3.1 (PR exists) / `/ggx-dispatcher` §4.2 (rollback fresh-dev only)   | "dispatcher is mid-run on dev; resume if seen next time"  |
| `need-spec-review`                 | `/port:ship` step 13 (auto only) / `/ggx-work` Step 4.4a else-branch (HITL fallback) | human reviewer / `/spec-review` step 6                                                  | "ported spec ready for human spec review" |

Invariant: a ticket should never have both `ready-to-*` AND the matching `dispatcher-*-in-flight` simultaneously (§2.2 conflict check b). If it does, the lock state is inconsistent — dispatcher skips with a comment asking the human to resolve.
