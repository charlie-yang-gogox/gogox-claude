---
name: ggx-dispatcher
description: >
  Manual batch worker for actionable Linear tickets. Sweeps the cwd repo's
  team for `ready-to-port` and `ready-to-dev` tickets, race-locks them, and
  fans the whole batch out (which in turn routes to
  `/port:ff` / `/dev:ff` / `/bug:ff` / `/ui-tweak:ff` via `/route`).
  The fan-out — all lanes incl. `design bug` — runs via a single `Workflow`
  tool call (§5.2): script-spawned agents are level-1, so the ui-tweak opus
  judge spawns cleanly and there is no nested-spawn problem. This is the ONLY
  fan-out path (the legacy N×`Agent` `--classic` path was retired in GGC-55;
  reversion = `git revert` from tag `pre-classic-removal`). Single-repo,
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

Find every actionable ticket in the cwd repo's Linear team and dispatch the whole batch — all lanes — via a single `Workflow` tool call (§5.2). The script fans out one `/ggx-work <ID> --auto` agent per dev/port/bug ticket and runs `design bug` tickets as script-spawned dual-judge ui-tweak legs; each `/ggx-work` agent then calls `/route --non-interactive` to pick the right `/port:ff` / `/dev:ff` / `/bug:ff` / `/ui-tweak:ff` based on the ticket's classification label and worktree state. The dispatcher consumes the script's structured `{ counts, rows }` return, posts fallbacks, and emits a summary.

**Usage**: `/ggx-dispatcher [--dry-run] [--test] [--max-parallel:<N>] [--team:<KEY>]`

- `--dry-run` — Print the planned dispatch and STOP. No Linear writes, no agent spawn.
- `--test` — Skip the default-branch + clean-tree pre-flight checks (still requires main worktree, gh auth, and lockfile).
- `--max-parallel:<N>` — Concurrent dispatch cap. Default `3`, hard cap `20`. Out of range → abort. This bounds the roster handed to the script; the script's `pipeline()` self-caps concurrency at `min(16, cores-2)` on top.
- `--workflow` — **Redundant no-op (the `Workflow` path is now the only path).** Accepted for back-compat and changes nothing. The fan-out always runs via §5.2; requires `~/.claude/workflows/dispatch-fanout.workflow.js` (installed by `install.sh`).
- `--launch-only` — After §5.2 fires the `Workflow` tool and records the `runId`, **return immediately** — skip the §5.2 step-4 heartbeat and step-5 result consumption, and release the run-lock (§6.5) on return. The CALLER owns the workflow's lifecycle (polling, result consumption, lock-free concurrency guarding). Intended for `/ggx-on-duty` (D22): the dispatch fan-out becomes a `Workflow` task owned by the on-duty session — visible in its `/workflows` — and on-duty consumes the `{counts, rows}` return on the completion notification. The durable concurrency guard for the still-running workflow is the `dispatcher-*-in-flight` labels set in §4.1 (already written before launch), NOT the run-lock. **Also emits a sibling `<RUN_TS>-<PID>.args.json` artifact** (§5.2 step 2a) holding the EXACT `{ trunkSha, roster }` payload passed to the `Workflow` tool — the durable record of the dispatch args the inline `--launch-only` run does NOT otherwise hold, so `/ggx-on-duty`'s RECONCILE resume (GGC-41) can re-supply identical args via `resumeFromRunId` and hit the journal cache. Discovered by the same newest-mtime glob on-duty already uses for the in-flight TSV.
- `--demo` — After the run, run a serial demo-capture pass (§6.6) via `/ggx-demo --batch` (GGC-66 — absorbed `/_ui-demo-batch`), which self-discovers every open `design bug` PR of mine still lacking a demo (incl. the ones just shipped) and captures them serially on one device. **Skipped under `--launch-only`** — the dispatcher has already returned, so the caller (`/ggx-on-duty --demo`) owns the demo pass. Best-effort / fail-soft: never blocks or fails the run. Off by default (design-bug PRs ship without an auto-captured demo, as today). See GGC-29 / GGC-66.
- `--team:<KEY>` — Required when the cwd repo's `branch_prefix` is `auto`. Allowed (but must equal `branch_prefix`) when `branch_prefix` is concrete.

---

## Label ownership boundary

Two distinct label namespaces drive this pipeline; mixing them up is the
single most common reason for incorrect routing. They are **orthogonal**.

| Namespace                  | Examples                                                       | Owned by                                                                                       | Read by                                                                                       |
|----------------------------|----------------------------------------------------------------|------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------|
| **Workflow labels**        | `ready-to-port`, `ready-to-dev`, `dispatcher-port-in-flight`, `dispatcher-dev-in-flight`, `need-spec-review` | dispatcher + `/port:ship` + `/dev:ship` + `/ggx-work` (scoped — see below) + `/ticket-analyze` (writes `ready-to-*` only) | dispatcher (Q1–Q4 discovery, §4.1 lock, §6.2 fallback); `/ggx-work` Step 2.5 + Step 4.4a; `/spec-review` batch fetch; `/ticket-analyze` Step 1.5 skip filter |
| **Analyzer labels**        | `need-revision`, `need-dependency`                             | `/ticket-analyze` exclusively                                                                  | humans (revision checklist / blocker visibility); `/ticket-analyze` re-run filter. The dispatcher ignores them — a ticket carrying one is by definition not `ready-to-*`. |
| **Classification labels**  | `bug`, `port`, `feature`, `design bug`                         | humans (PM/eng)                                                                                | `/route`; `/ggx-work` Step 2.5 (read-only, lane derivation); `/ticket-analyze` Step 2 (read-only, lane derivation); dispatcher §2.1 (read-only, `design bug` ONLY — sets the roster `uiTweak` flag the §5.2 script routes on) |

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
  not write it. **One narrow exception (§2.1)**: the dispatcher reads the
  single classification label `design bug` (read-only, whole-string
  case-insensitive) to set the roster `uiTweak` flag, which tells the
  §5.2 script to run that ticket as a script-spawned dual-judge ui-tweak
  leg (`runUiTweak`) instead of a plain `/ggx-work` agent. This is a
  spawn-mechanics decision, not routing: the pipeline choice still
  happens inside `/ggx-work` / the script via `/route`.
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
4. Resolve `default_branch` and capture the **clean-trunk tip** (GGC-49):
   ```bash
   default_branch=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||')
   if [ -z "$default_branch" ]; then
     default_branch=$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name' 2>/dev/null)
   fi
   [ -z "$default_branch" ] && abort "Cannot detect default branch. Run: git remote set-head origin -a"
   # GGC-49 — fetch and capture the default-branch tip NOW, before any worktree is
   # created. This SHA is the ground-truth clean-trunk baseline every fan-out leg's
   # worktree must be based on. Threaded into the roster (§5.2) so the workflow
   # script can assert each leg's base_ref against it and DEMOTE a contaminated leg
   # (the CAF-625 cross-worktree leak). Capture once here so the whole batch shares
   # one consistent baseline even if trunk moves mid-run.
   git fetch origin "$default_branch" 2>/dev/null || abort "Cannot fetch origin/$default_branch — refusing to dispatch on a stale trunk."
   trunk_sha=$(git rev-parse "origin/$default_branch")
   [ -z "$trunk_sha" ] && abort "Cannot resolve origin/$default_branch tip after fetch."
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
     # nothing; a flutter-build-rewritten AppFrameworkInfo.plist caused a
     # resolver false needs-human the same day). Deliberately narrow —
     # extend only for files that are (a) machine-written and
     # (b) reproducible from a clean checkout. Keep in sync with the
     # identical allowlist in ggx-pr-resolver.md step 4 (residue triage).
     RESIDUE_RE='^(pubspec\.lock|ios/Podfile\.lock|Gemfile\.lock|ios/Flutter/AppFrameworkInfo\.plist)$'
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

7. **Permission pre-flight (P2) — assert BEFORE any ticket is touched.**
   The §5.2 `Workflow` agents run at `acceptEdits`, inherit the session tool
   allowlist, and have **no way to answer a mid-run permission prompt**, so a
   required `git`/`gh`/`openspec`/Linear-MCP call that is not allowlisted
   **silently stalls the whole run**. The §4.1 race-lock happens *after*
   pre-flight, so a stall discovered later would already have stranded
   `dispatcher-*-in-flight` residue on locked tickets. The coverage check
   therefore lives HERE — a miss means **zero tickets touched** (relocated
   from the old §5.2 step 3, which ran *after* the roster was built).

   Scope the check to the `permissions.allow` array, **merged across the
   settings overlay chain** — a whole-file grep false-passes on `deny`
   entries, and the effective allowlist is the union of the user-global and
   project layers:
   ```bash
   # Merge permissions.allow from every settings layer that applies to THIS
   # repo (user-global first, then project overlays). Missing files and
   # malformed JSON contribute nothing — tolerated, never fatal.
   ALLOW=$(
     for f in "$HOME/.claude/settings.json" "$HOME/.claude/settings.local.json" \
              ".claude/settings.json" ".claude/settings.local.json"; do
       [ -f "$f" ] && jq -r '.permissions.allow[]?' "$f" 2>/dev/null
     done
   )
   # Linear MCP must be covered BOTH ways (claude.ai connector AND the
   # linear-server fallback — a background worker cannot fall back
   # interactively), and the cover must extend to save_issue *writes*: the
   # §6.2 per-ticket fallback uses save_issue to flip status / drop the
   # in-flight label, and /dev:ship / /port:ship do likewise, so a read-only
   # allowlist (…__list_issues only) would stall them. A wildcard (…__*) covers
   # it; otherwise the exact …__save_issue entry must be present.
   # NOTE: GGC-23's /_file-followup does NOT need this — it was narrowed
   # (2026-06-15) to a LOCAL gitignored file only (no save_issue / GitHub /
   # network), so it imposes no Linear-write permission requirement.
   linear_write_covered() {
     printf '%s\n' "$ALLOW" | grep -qE \
       'mcp__(claude_ai_Linear|linear-server)__(\*|save_issue)'
   }
   ```
   Decision (the ONLY P2 gate — §5.2 assumes coverage was asserted here).
   The fan-out always runs as background `Workflow` agents that cannot answer
   a mid-run prompt, so a coverage miss is fatal to an unattended run. The one
   exception is an **interactive** session run by a human at the keyboard, who
   CAN add the allowlist entry and re-confirm — there a miss is recoverable:
   - **Non-interactive / background (incl. `--launch-only`, `/ggx-on-duty`) → hard-abort**
     when `linear_write_covered` is false. Release the lock, leave every label
     untouched (nothing was dispatched):
     > `abort "ABORT (P2): permissions.allow does not cover Linear writes — need mcp__claude_ai_Linear__* (or …__save_issue) AND the mcp__linear-server__* fallback. A Workflow worker would stall silently on its first Linear write. Add them to ~/.claude/settings.json and re-invoke. No tickets were locked."`
   - **Interactive session → WARN-then-confirm.** The live session can answer
     a permission prompt, so a miss is recoverable: print the same gap as a
     `WARN (P2): …` line and proceed only on explicit human go-ahead. Never
     hard-abort the interactive path on this check. (A `--launch-only` /
     on-duty invocation is by definition unattended — it takes the hard-abort
     branch above regardless of TTY.)

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
- The two-label split (port vs dev) lets the dispatcher pick the right §4.1 lock transition and the right §6.4 walker (`infer_port_stage` vs `infer_dev_stage`; dev-lane tickets carrying the §2.1 ui-tweak flag use `infer_ui_stage` instead) at end-of-run table rendering without re-deriving pipeline type from worktree state. The roster handed to the §5.2 script is uniform across lanes — pipeline routing happens inside each `/ggx-work` agent via `/route`; the only lane-conditional execution is the script running ui-tweak-flagged rows as a dual-judge `runUiTweak` leg.
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
read-only; serialized as `uiTweak:true` in the §4.3 roster JSON). The flag
does NOT change the lock transition (§4.1 still swaps `ready-to-dev` ↔
`dispatcher-dev-in-flight`) — it changes the **execution shape** (§5.2: the
script runs the row as a dual-judge ui-tweak leg via `runUiTweak` instead of
a plain `/ggx-work` agent) and the **walker / outcome rules** (§6.2:
`infer_ui_stage` + the ui-tweak `done` predicate). On a re-sweep
(recovery-dev) the flag is re-derived from the same label — a failed
ui-tweak ticket re-enters the ui-tweak leg, never the dev walker (whose
`done` predicate expects openspec markers a ui-tweak worktree never
creates).

### 2.2 Conflict checks

Four malformed shapes — drop the ticket, post a comment, remove all conflicting labels:

a. **Both fresh labels**: `ready-to-port` AND `ready-to-dev` present →
   > `Dispatcher: skipped — ticket has both ready-to-port and ready-to-dev labels. Cannot determine intent. Re-add the correct single label to retry.`

b. **Fresh + in-flight on same lane** (e.g. `ready-to-dev` + `dispatcher-dev-in-flight`) →
   > `Dispatcher: skipped — ticket has both ready-to-dev and dispatcher-dev-in-flight labels. Lock state inconsistent (prior run was interrupted mid-lock?). Remove one and re-add the right one to retry.`

c. **Both in-flight labels**: `dispatcher-port-in-flight` AND `dispatcher-dev-in-flight` →
   > `Dispatcher: skipped — ticket has both port and dev in-flight labels. Cannot route. Inspect and remove one manually.`

d. **Workflow label contradicts classification (M3)**: `ready-to-port` present AND the classification label (§2.1) is NOT `port` (i.e. it is `bug` / `feature` / `design bug`) →
   > `Dispatcher: skipped — ticket has ready-to-port but its classification label is not 'port'. /route would recommend a non-port lane, and at ship time only the dev-lane in-flight label is cleared — leaving dispatcher-port-in-flight stuck forever. Fix the labels: set classification to 'port', or swap ready-to-port → ready-to-dev.`

   **Do NOT mirror this for `ready-to-dev`.** `ready-to-dev` legitimately pairs with classification `port`: the post-spec-review state, where port already ran, the human flipped `need-spec-review` → `ready-to-dev`, and the ticket now goes through the dev lane while still classified `port`. Only the `ready-to-port` + non-`port` direction is the stuck-label hazard.

All four shapes drop the ticket from the batch.

### 2.3 Priority sort + cap

Sort by priority (`urgent` > `high` > `medium` > `low` > `none`), then by `createdAt` ascending.

Cap to `--max-parallel`. Validate the value: `1 <= N <= 20`. Out of range → abort. Default `3`.

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

The command string is identical for every lane (`/ggx-work <ID> --auto`);
lane + ui-tweak flag drive the §4.1 lock-label transition, the §5.2 script's
choice of `runWork` vs `runUiTweak`, and the §6.4 end-of-run table's choice
of `infer_port_stage` vs `infer_dev_stage` vs `infer_ui_stage`.

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

**Required step, not optional preview prose.** After every surviving ticket is locked and before the §5.2 launch, the next thing emitted in stdout must be this table. **The table is text output and the `Workflow` tool call (§5.2) emit in the SAME single assistant message** — print the table, then immediately fire the `Workflow` tool in the same turn. Do NOT end the turn after the table to "let the user confirm". That artificial stop has been observed to force the user to type "are you done?" before any dispatch actually happens — by the time they nudge, the perceived dispatcher has been idle for minutes. This applies on every sweep, including a same-lock re-sweep where the batch is small (1–2 tickets) and the §4.1 `locked ✓` lines might feel sufficient — they are not.

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
- `command` = the exact string the §5.2 script will run per ticket — uniformly `/ggx-work <ID> --auto`; ui-tweak-flagged rows render it as `(ui-tweak) /ggx-work <ID> --auto` so the dual-judge execution shape is visible up-front.
- `link` = the issue `url` field returned by the Step 2 `list_issues` calls. Cache the url alongside the ticket id from Step 2 so this column does not require a re-fetch.

While building this table, accumulate the same rows into an in-memory `DISPATCH_ROSTER` value — TSV, one line per ticket, format `<ticket-id>\t<lane>\t<absolute-worktree-path>\t<url>\t<ui-tweak|->`. Worktree path = `realpath ../<TICKET-ID>` per the `/add-worktree` convention; `url` is the issue url cached from Step 2; the 5th field is `ui-tweak` when the §2.1 ui-tweak flag is set, else `-`. **Held in session state only**, with ONE deliberate exception: the early in-flight projection written in §4.4 below (the §6.4 final report is the only other on-disk roster, and it is unreadable until after the §5.2 run completes). §6.4 uses this roster to render the end-of-run table (lane lookup, walker selection, Ticket-link column).

In addition to the in-memory TSV, serialize **all** rows to a JSON array — `[{ "ticketId", "lane", "worktreePath", "url", "uiTweak" }, ...]` — for the §5.2 script's `args`. **Design-bug rows are INCLUDED with `uiTweak: true`** — the script's `runUiTweak` runs them as a level-1 dual-judge panel. The JSON is the `DISPATCH_ROSTER_JSON` value consumed by §5.2.

### 4.4 Early in-flight projection (read by /ggx-on-duty's skip-set — B1, D11)

After §4.1 has locked every surviving ticket and §4.3 has built `DISPATCH_ROSTER`, but **before the §5.2 `Workflow` launch**, project the claimed set to disk so an external reader can see it during the entire `dispatch.running` window (on-duty's state field — was `chain.running` pre-GGC-70). This is the ONLY on-disk roster the dispatcher writes during a run; the §6.4 final report is written only after the §5.2 `Workflow` run completes (tens of minutes later), so it cannot serve a reader watching a live dispatch.

```bash
# RUN_TS is this run's identifier, reused for the §6.3/§6.4 report paths
# (`<RUN_TS>-<PID>`). Establish it here, at the first on-disk write, so the
# inflight file and the final report share one stem.
: "${RUN_TS:=$(date -u +%Y%m%dT%H%M%SZ)}"
INFLIGHT="claude-reports/dispatcher/$RUN_TS-$$.inflight.tsv"
: > "$INFLIGHT"
# one line per claimed ticket: <ticket-id>\t<headRefName>\t<absolute-worktree-path>
```

For each row of `DISPATCH_ROSTER`, append `<ticket-id>\t<headRefName>\t<worktree-path>` where:

- `<ticket-id>` / `<worktree-path>` are the exact values already in the roster (`worktree-path = realpath ../<TICKET-ID>`).
- `<headRefName>` is the branch `/add-worktree` will create for this ticket. The naming convention is `<type>/<ticket-id>` (`add-worktree.md` Step 1), so the ticket-id segment is deterministic; the `<type>` segment is NOT predictable here (the dispatcher deliberately does not read classification labels — see the Label ownership boundary — and `/add-worktree` infers `<type>` from the ticket's nature downstream). Write the default-type prediction `feat/<ticket-id>`. Consumers that need an exact branch should match on the ticket-id segment or fall back to the worktree-path column, both of which ARE exact.

**What this file is FOR**: `/ggx-on-duty`'s Leg-2 resolver skip-set (E-3, D11) reads the newest one of these by mtime while `dispatch.running` (on-duty's state field — was `chain.running` pre-GGC-70) to avoid sending `/ggx-pr-resolver` into a worktree `/dev:ff` is still writing. The canonical skip-set key is the branch name (`headRefName`, D11). The on-duty reader discovers this file via newest-mtime glob — it cannot know the background subagent's `RUN_TS`/`PID` and must never read the lock. A crashed run's stale `*.inflight.tsv` is tolerated (consumers always take the newest-mtime file; a fresh dispatcher run writes a newer one, and §6.4 deletes this run's own file when the final report supersedes it). Rows with the §2.1 ui-tweak flag are included like any other — their worktrees are written by the script's `runUiTweak` leg, but they are equally in-flight and equally off-limits to the resolver.

The `RUN_TS`/`$$` stem established here is also the stem for the optional sibling `<RUN_TS>-<$$>.args.json` (GGC-41), written under `--launch-only` at §5.2 step 2a so `/ggx-on-duty`'s RECONCILE resume can find it via the SAME newest-mtime glob and re-supply identical dispatch args on `resumeFromRunId`. Keeping the two files on one stem means on-duty discovers both with one glob and never has to read the lock.

> **§5.0 and §5.3 retired in GGC-55.** The legacy `--classic` fan-out — §5.0
> (inline ui-tweak lane) and §5.3 (N×`Agent` parallel spawn) — was deleted when
> the `Workflow` path became the only path. §5.1 survives, trimmed to the
> per-ticket command + routing the §5.2 script now uses; §5.2 (below) is the sole
> fan-out section. The numbers are intentionally NOT re-flowed so cross-file
> references to §5.2 / §6.1 / §6.4 stay stable. Reversion = `git revert` from
> tag `pre-classic-removal`.

### 5.1 Spawn command + routing (per-ticket, inside the §5.2 script)

The §5.2 script runs one command per ticket, uniform across all lanes:

```
/ggx-work <ID> --auto
```

Each `/ggx-work` agent calls `/route --non-interactive` to decide which ff
to run; `/route` reads the ticket's classification label (`bug` / `port` /
`feature` / `design bug`) plus the worktree filesystem (port-ship marker,
`.dev/*` markers) and recommends `/port:ff`, `/dev:ff`, `/bug:ff`, or
`/ui-tweak:ff`. Recovery lanes (`recovery-port` / `recovery-dev`) run the
same command because the ff walkers (`infer_port_stage` / `infer_dev_stage`
/ `infer_ui_stage`) resume idempotently from their own marker files — the
in-flight label is the dispatcher's signal that the worktree exists, not a
routing hint that needs to be carried into the agent. Design-bug
(`uiTweak:true`) rows are the one execution-shape difference: the script
runs them through `runUiTweak` (a dual-judge ui-tweak leg) rather than a
plain `runWork` agent — same `/ggx-work` semantics, different wrapper (§5.2).

**Figma URL detection is no longer dispatcher's job.** Previously the
dispatcher pre-scanned the ticket description for `figma\.com/...` and
attached `--no-figma` to the dev spawn. That detection has moved into
`/dev:start` Step 4 and now scans description **and** comments, so a
designer dropping a Figma link as a follow-up comment no longer routes
the ticket through the SKIPPED short-circuit. Dispatcher just passes
`/ggx-work <ID> --auto`; the rest is determined downstream.

### 5.2 Workflow fan-out (the ONLY fan-out path — R5 migration complete)

The **entire fan-out — all four lanes** — is driven by a single `Workflow`
tool call. This is the sole fan-out path; the legacy N×`Agent` `--classic`
path was retired in GGC-55 (the R5 migration documented in `ARCHITECTURE.md`
"Nested-spawn constraint", completed here). Reversion, if the `Workflow` tool
is ever changed or removed upstream, is `git revert` from tag
`pre-classic-removal`, not a live fallback flag.

**Ui-tweak runs in-script as a dual-judge leg.** Design-bug rows are not
excluded: the script's `runUiTweak` runs them as apply/preview → decorrelated
dual-judge panel (`ui-verify-agent` sonnet + `dev-reviewer` opus) → finisher,
with **both judges spawned by the SCRIPT**. Because a script-spawned agent is
level-1, the opus judge spawns cleanly — the level-2 opus spawn inside a
worker that would otherwise be broken never occurs. The tier-pinned
decorrelation (sonnet vs opus, both-must-be-CLEAR) is preserved verbatim, in
lock-step with `commands/dev/ui-tweak/audit.md`.

**Why a script, not deeper nesting:** every agent the script spawns is
level-1 (the nested-spawn constraint does not apply between a workflow
script and its agents). This is what lets the script spawn the opus judge
directly. **R1 is unchanged**: the heavy ff stages still inline inside each
worker agent, and verify-agent (spawned by the worker inside `/dev:verify`)
stays level-2 — only what the SCRIPT spawns directly is level-1.

**Fail-fast guard — `Workflow` tool unavailable (mirror of the §1.7 abort).**
There is no runtime introspection in a prompt skill for "is the `Workflow`
tool present?", so the abort is wired as the **error branch of the step-3
`Workflow` invocation below**: if the tool call itself fails as
not-found / unavailable (the tool is not registered in this session, or
`~/.claude/workflows/dispatch-fanout.workflow.js` is missing), **hard-abort —
do NOT silently route anywhere.** Release the run-lock, leave the
`dispatcher-*-in-flight` labels in place (the tickets were locked but never
worked — they are re-pickable next sweep), and abort:
> `abort "ABORT: the Workflow tool is unavailable (not registered in this session, or ~/.claude/workflows/dispatch-fanout.workflow.js missing). The fan-out has no other path (the --classic fallback was retired in GGC-55). Run ./install.sh from gogox-claude to (re)install the workflow script, confirm the Workflow tool is available, and re-invoke. Locked tickets keep their dispatcher-*-in-flight label and re-dispatch next sweep."`

**Permissions precondition (load-bearing — read before first use).**
Workflow agents run at `acceptEdits` and inherit the **session's tool
allowlist**; they have **no way to answer a mid-run permission prompt**, so
any `git`/`gh`/`openspec`/Linear-MCP call not on the allowlist **silently
stalls the whole run**. Ensure the user-global `~/.claude/settings.json`
`permissions.allow` covers every shell/MCP family the ff pipelines touch
(git, gh, openspec, `yq`, the platform test/format toolchain). The allowlist
lives user-global, NOT in any repo's `.claude/settings.json`, because the
dispatcher runs in the **target repo**, not in gogox-claude.

**Linear MCP — BOTH prefixes must be allowlisted.** Per the §"Label ownership
boundary" rule, the run uses whichever Linear server is connected: it prefers
`mcp__claude_ai_Linear__*` and **falls back to `mcp__linear-server__*`** when
the claude.ai connector is not authenticated (both expose identical
capability). An interactive session resolves this fine, but a **background
workflow agent cannot fall back interactively** — so the allowlist MUST cover
**both** `mcp__claude_ai_Linear__*` AND `mcp__linear-server__*`, or a worker
silently stalls on the first Linear write whenever claude.ai is not authed.
Also allowlist `mcp__claude_ai_Atlassian_Rovo__*` (Jira) and
`mcp__plugin_figma_figma__*` (figma stage). An e2e against dummy tickets is
the gate that confirms coverage — watch for a background run that goes quiet.
(Observed in the 2026-06-08 CAF-548 run: claude.ai Linear was unauthed and the
pipeline fell back to `linear-server`; that ran inline so it survived, but a
workflow worker would have stalled.)

Steps:

1. **Build `DISPATCH_ROSTER_JSON`** — **all** rows from §4.3 as a JSON array
   (`{ticketId, lane, worktreePath, url, uiTweak}`), **including design-bug
   rows with `uiTweak:true`** (the script's `runUiTweak` handles them in-lane).
   Do NOT exclude ui-tweak rows.

2. **Persist `run.json`** for crash recovery (§4.2 / §5.2-resume). Write
   `claude-reports/dispatcher/run.json` with `{ scriptPath, roster:
   DISPATCH_ROSTER_JSON, trunkSha: $trunk_sha, ts }` (atomic `mktemp` + `mv`).
   `ts` comes from a shell `date` call — NOT from inside the script (the
   script's clock is frozen for resume determinism). `trunkSha` is the
   §4.2-captured clean-trunk tip (GGC-49) — persisted so a same-session resume
   reuses the SAME baseline the original launch asserted against.

   2a. **(`--launch-only` only.) Emit the dispatch-args artifact (GGC-41).**
   `run.json` is the dispatcher's OWN same-session resume cache and is
   overwritten by the next dispatcher run; it is not a durable, per-run record a
   *different* process (the on-duty session that fired `--launch-only` inline)
   can rely on across its wake cycles. Under `--launch-only`, also write the
   EXACT `args` object this launch will hand the `Workflow` tool to a per-run
   sibling of §4.4's in-flight TSV, so `/ggx-on-duty`'s RECONCILE resume can
   re-supply byte-identical args on `resumeFromRunId` (a bare
   `Workflow({scriptPath, resumeFromRunId})` with no `args` computes an empty
   roster → 0 agents → nothing matches the journal → resume silently does
   nothing). Skip this entirely when NOT `--launch-only` (the non-launch-only
   caller babysits the run in-session and never needs the artifact).

   Run this block ONLY when `--launch-only` is set (otherwise skip it — see
   above):

   ```bash
   # RUN_TS / $$ are the SAME values §4.4 used for the in-flight TSV — share
   # the stem so on-duty's newest-mtime glob finds both with one pattern.
   ARGS_JSON="claude-reports/dispatcher/$RUN_TS-$$.args.json"
   ARGS_TMP="$(mktemp)"
   # Identical shape to the Workflow `args` below: { trunkSha, roster }.
   printf '{"trunkSha":%s,"roster":%s}\n' \
     "$(jq -Rn --arg t "$trunk_sha" '$t')" \
     "$DISPATCH_ROSTER_JSON" > "$ARGS_TMP"
   mv "$ARGS_TMP" "$ARGS_JSON"   # atomic
   echo "dispatcher: wrote launch-only args artifact $ARGS_JSON" >&2
   ```

   The contents MUST equal the step-3 `args` object verbatim — if step 3's
   shape ever changes, change this in lockstep, or a resume re-supplies stale
   args and silently misses the journal cache. The args.json is written ONLY on
   the `--launch-only` path, which returns at step 3 below and never reaches
   §6.4 — so the §6.4 in-flight-TSV cleanup never sees an args.json to delete.
   The on-duty session that fired `--launch-only` owns this artifact's
   lifecycle: a stale `*.args.json` is tolerated exactly like a stale
   `*.inflight.tsv` (consumers — here, on-duty's RECONCILE resume — always glob
   the newest by mtime; the next dispatch run writes a newer pair). No
   dispatcher-side cleanup is required.

3. **Launch (P2 coverage already asserted in Step 1.7).** The Linear-MCP
   allowlist coverage gate runs in **Step 1 pre-flight, item 7** — BEFORE
   any ticket is locked — so by the time control reaches here a coverage
   miss has already hard-aborted (nothing dispatched, no labels touched). Do
   NOT re-grep here. **Print the §4.3 table** (the table is the review) and
   **in the same turn** invoke the `Workflow` tool. **If the tool call fails
   as not-found / unavailable, take the fail-fast guard abort above** (do not
   route anywhere else — there is no other path):
   - `scriptPath`: `$HOME/.claude/workflows/dispatch-fanout.workflow.js`
   - `args`: a JSON object `{ "trunkSha": "<$trunk_sha>", "roster":
     DISPATCH_ROSTER_JSON }` (GGC-49 — the wrapper shape carries the
     clean-trunk baseline alongside the roster so the script can assert each
     leg's base against it). Note that in THIS harness the `Workflow` tool
     delivers `args` to the script as a **JSON string** regardless (confirmed
     2026-06-08, CAF-371: even a live object/array arrived stringified). The
     script tolerates the wrapper object, a bare array (legacy, no trunkSha →
     contamination assertion skipped with a loud warn), and a stringified form
     of either via a `JSON.parse` fallback, so the roster reaches it either
     way. **Do not rely on the script seeing a live value.** If `args` carries
     content yet the script parses zero rows, it returns
     `error: "roster-parse-failed"`
     LOUDLY (smoke guard) rather than a silent empty no-op — §6.4 must treat
     that error field as a batch failure, not "no work".
   The tool returns immediately with a `runId` and runs in the background;
   record the `runId` into `run.json` (second atomic write) so a same-session
   resume can pass `resumeFromRunId`.

   **`--launch-only` returns HERE.** Once the `runId` is recorded, release the
   run-lock (§6.5) and return the `runId` to the caller — do NOT run step 4
   (heartbeat) or step 5 (consume). The caller (`/ggx-on-duty`, D22) owns the
   still-running workflow: it polls liveness on its own cadence
   and consumes `{counts, rows}` on the completion notification. The
   `dispatcher-*-in-flight` labels (written in §4.1, before this launch) are
   the durable concurrency guard while the workflow runs lock-free.

4. **(Skip under `--launch-only`.) Watch the background run for liveness (P3 heartbeat).**
   Design-bug rows are in the roster and run in-script via `runUiTweak`
   (level-1 dual-judge panel), so the whole batch runs inside the single
   `Workflow` call. That call is
   opaque until completion, so while it runs in the background, poll it on a
   **long interval (~5 min)** as a liveness + stall detector — the script
   emits a `log()` line per ticket-stage transition (`[work]`, `[ui]
   apply+preview`, `[ui] dual-judge`, `[ui] CLEAR -> commit`, `[fallback]`,
   `[aggregate]`):
   - Tail the workflow task's output (`TaskOutput`, tail only — keep main
     context lean; not accumulating per-ticket context is the whole point of
     the `Workflow` path) every ~5 min and surface a **one-line heartbeat** to the
     user: agents active + the latest `log()` line.
   - **Stall judgment:** a *single* worker can legitimately run >10 min with no
     new script `log()` line (the work is inside the worker agent), so do NOT
     treat log-line age alone as a stall. A stall is an agent **in-progress
     with no forward tool-use activity** for an extended window (~10 min) — the
     P2 failure mode (waiting on a prompt no one can answer). On that, **warn
     the user** and consider `TaskStop`; the stuck ticket's
     `dispatcher-*-in-flight` label keeps it re-pickable next sweep. With
     `--max-parallel` defaulting to **3**, a stall blocks at most a small
     batch.
   Outcomes come back in the script's `rows` (step 5).

5. **(Skip under `--launch-only` — the caller consumes instead.) Consume the workflow result** in place of §6.1's wait loop. The
   `Workflow` completion notification carries the script's return value:
   `{ counts, rows }` where each row is the validated `WORK_SCHEMA` object
   (`ticketId, outcome, prUrl, stage, error`). **First check for a top-level
   `error` field** (the script's smoke guard / P2): a return of
   `{ error: "roster-parse-failed", ... }` means a non-empty roster reached
   the script but parsed to zero rows (serialization mismatch — 0 agents
   spawned). This is a **batch failure, NOT a clean no-op**: do NOT report
   "0 done / nothing to do"; instead abort-flag the run, leave every locked
   `dispatcher-*-in-flight` label in place (the tickets were never worked),
   emit the §6.5 batch-abort Slack alert, and surface the parse mismatch in
   the §6.4 table. Only when there is no `error` field do you hand `rows`
   to §6.4 directly — **all lanes (incl. ui-tweak) are already in
   `rows`**, so there is nothing to merge in. **Do NOT re-derive outcomes
   via per-ticket `get_issue`** (the script's `outcome`/`stage` are
   authoritative; §6.2's per-ticket Linear failure write already ran INSIDE
   the script's `runFallback` stage — including the ui-tweak BLOCKED/failed
   case, which `runFallback` posts because the script owns that flow). There
   is no separate inline-row fallback path.

   **GGC-49 no-op rows.** A ui-tweak leg may return an EARNED no-op:
   `outcome:"done", prUrl:null, stage:"ui:noop", noop:true` with a
   `noopJustification` (validated against the ticket target on clean trunk).
   Render it in the §6.4 table as a `done` with PR column `— (no-op)` and
   surface the `noopJustification` so a human can sanity-check the claim — do
   NOT silently show a blank "done". An UNEARNED no-op (empty diff with no
   target validation) and a CONTAMINATED base both come back as
   `outcome:"failed"` already routed through `runFallback`, so they appear in
   the failed count with their reason — never as a silent close.

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

---

## Step 6: Wait, fallback, finalize

### 6.1 Wait for completion

There is no `joined`-counter wait loop and no per-agent
background-completion notification to fold in — the **entire batch runs
inside the single `Workflow` tool call** (§5.2). That call returns its
`{ counts, rows }` when the whole background run finishes, and §5.2 step 5
feeds it straight to §6.4. The only in-session waiting is the §5.2 step-4
heartbeat poll (liveness / stall detection), which is purely cosmetic; the
authoritative outcome of every ticket is the validated `rows[]` the script
returns. Skip straight to §6.2.

`/dev:*` / `/port:*` stages write authoritative marker files
(`.dev/verify-pass.md`, `.port/synth-report.md`, etc.) inside their
worktrees as they run. Those files remain the ground truth for "what stage
did this ticket reach", but the script's returned `rows[]` already carries
the settled `outcome`/`stage` — §6.4 reads marker files via `infer_*_stage`
only for the rare cases (`--dry-run`) where no script ran.

Closing the dispatcher session early still kills MCP connections and
leaves Linear in a half-finalized state — that constraint is unchanged.

### 6.2 Per-ticket fallback — authoritative outcome derivation

**Outcome is authoritative from the §5.2 script's `rows[]`.** Every lane —
dev, port, bug, AND ui-tweak — gets its `outcome`/`stage` from the script's
validated `rows[]`, and the per-ticket Linear failure write already ran
inside the script's `runFallback` stage — do NOT re-run the derivation below
or re-post a failure comment for any script row. The algorithm below is the
**reference for what `runFallback` does inside the script**, and the path
the dispatcher takes only when no script ran (`--dry-run` recompute in §6.4).

The authoritative classification of each ticket's outcome is derived from
three independent signals: settled Linear state, walker-read worktree
markers, and PR state. No new file, no new helper — everything runs inline
per ticket.

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
     `apply` / `verify` / `review` / `ship` / `done`. (Direct-mode tickets
     — bug, and feature-direct on no-OpenSpec repos per GGC-17 —
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
     exits non-zero with the loud stderr line).
     - **non-BLOCK failure** (apply / preview / GGC-49 contamination /
       null finisher): the in-flight label STAYS as the resume signal —
       it may be re-runnable.
     - **DETERMINISTIC dual-judge BLOCK** (structural pre-pass OR judge;
       `error` matches `UI-TWEAK BLOCKED`): the script sub-classifies it
       `terminal-ui-block` (GGC-37). Re-running reproduces the identical
       BLOCK, so the in-flight label is REMOVED (not kept), `need-revision`
       added, status reset to `To-do` — see the fallback table below. This
       is the sole failure sub-case that does NOT preserve the resume signal.
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
   | any `failed`       | `dispatcher-*-in-flight` STAYS (resume signal for Q2/Q4 on the next sweep)            | If no failure comment exists yet on the ticket, post one via `save_comment`. Do NOT remove the in-flight label — that's the resume signal. ALSO append a local breadcrumb via `/_file-followup dispatcher-infra summary="<ticket-id> failed: <short reason>" signature="<ticket-id>:<walker_stage>"` (GGC-23 — fail-soft, local gitignored sink only; NO ticket creation). |
   | ui-tweak `failed` — **terminal-ui-block** (GGC-37) | `dispatcher-dev-in-flight ∉ labels` AND `need-revision ∈ labels` AND status `To-do` AND a `<!-- dispatch-triage-ui-blocked -->` comment present | **The ONE `failed` sub-case that removes the in-flight label.** Triggered when `error` matches `UI-TWEAK BLOCKED` (deterministic structural / dual-judge BLOCK). The script's `triageTerminalUiBlock` removes `dispatcher-dev-in-flight`, adds `need-revision`, resets status to `To-do`, and posts/updates an idempotent `<!-- dispatch-triage-ui-blocked -->` comment (reason + templated suggestion + attempt count). Re-running would just re-block, so the resume signal MUST NOT stay. `/ticket-analyze` marker-skips a still-`Design bug` ticket carrying that marker (does not re-issue `ready-to-dev`); reclassify `Design bug` → `Bug` lifts the skip, and a human can force re-dispatch by adding `ready-to-dev` directly (Q3). |

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

**Outcome source:** for ALL rows (dev, port, bug, ui-tweak), `outcome`,
`stage_reached`, and `prUrl` come from the §5.2 script's returned `rows[]`
(validated `WORK_SCHEMA` objects) — NOT from a per-ticket `get_issue` /
walker re-derivation. The `labels` / `status.name` columns may still be
fetched here if you want the live Linear state for display, but the outcome
is authoritative from the script. **Only `--dry-run`** (which never runs the
script) recomputes inline from the §6.2 sources below.

| Signal           | Source                                                                 | Notes                                                                                          |
|------------------|------------------------------------------------------------------------|------------------------------------------------------------------------------------------------|
| `labels`         | §6.2-derived (shared) — `get_issue` was made there                      | shared with §6.2 — no extra MCP round trip. If §6.2 didn't run (e.g. `--dry-run`), re-fetch here. |
| `status.name`    | same call as `labels`                                                  | shared with §6.2 — `In Progress` / `In Review` etc.                                              |
| `url`            | from roster (cached at Step 2)                                         | no re-fetch                                                                                     |
| `outcome`        | §5.2 script's returned `rows[]` (`done` / `port-paused` / `failed`) | authoritative — the script derives it from `labels` + walker + PR per the §6.2 algorithm inside `runFallback`. Under `--dry-run` (no script ran), recompute inline from `labels` + walker + PR here. No free-text is consulted. |
| `stage_reached` | §6.2-derived walker output (`infer_port_stage` / `infer_dev_stage` / `infer_ui_stage` already ran there) | shared with §6.2 — no extra worktree shell-out. Walker selection follows the lane + ui-tweak flag tagged in §2.1. ui-tweak rows render their stage with a `ui:` prefix (e.g. `ui:audit`) so the table distinguishes them from dev stages. |
| `pr`             | §6.2-derived `pr_state` (shared) — augmented here with `number,url` via `gh pr list --head "$branch" --state all --json number,url,state -q '.[0]'` (branch-based, same as §6.2 step 3 — NOT `gh pr view <ticket-id>`, which fails when the branch is `<prefix>/<TICKET-ID>`) if needed for the link column | shared with §6.2 for the state; non-zero exit ⇒ no PR, render `—`                              |

**Render order**: collect all rows in memory first (parallel MCP+gh calls
allowed and encouraged), then emit the table in one block. Roster order
(priority sort from §2.3) is preserved.

The Result counts block below (`done` / `port-paused` / `failed`) sums
over the script's returned `outcome` values per ticket (or, under
`--dry-run`, the §6.2-recomputed values) — never over free-text.

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

Once this report is written, **delete this run's early in-flight projection** — the report supersedes it and the chain is no longer in flight:

```bash
rm -f "claude-reports/dispatcher/$RUN_TS-$$.inflight.tsv"
```

A crash before this line leaves a stale `*.inflight.tsv` behind; that is tolerated because consumers (the on-duty skip-set, §4.4) always glob the newest by mtime — the next dispatcher run writes a newer one, and a stale file at worst over-excludes a branch from one resolver pass (safe).

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
- One raw-signal line per §6.4 row, built from the **script-returned
  authoritative outcome + Flags + pr** already in memory (NEVER from
  free-text), format per `_slack-notify.md`
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

STOP — **unless `--demo` is set**, in which case run §6.6 first, then STOP.

**Render contract**: stdout and the md file render the **same six
columns in the same order**. Don't drop columns from stdout to fit
terminal width — Claude Code terminals wrap markdown tables fine, and
the value of the table is being identical across the two surfaces (you
can paste either into a PR comment / Slack thread without re-reading
the data).

### 6.6 Demo pass (`--demo` — best-effort, after the run)

Runs ONLY when `--demo` was passed AND this is NOT a `--launch-only`
invocation. After §6.5's summary, invoke:

```
/ggx-demo --batch
```

`/ggx-demo --batch` (GGC-66 — it absorbed the former `/_ui-demo-batch`)
**self-discovers** every open design-bug PR of mine that still lacks a demo —
which includes the design-bug PRs this run just shipped — so no `rows` array
need be built. It captures each **SERIALLY on the one simulator** (so the
parallel fan-out's N agents never drive the device at once — the race is gone by
construction), logging in via the Step 2.4 gate (GGC-65) when `demo_auth` is
configured, then attaches each idempotently to its PR/ticket. It is **fail-soft
and never blocks**: no device / capture error / login wall degrades to a WARN (a
login wall short-circuits the rest) and the run still completes. No undemoed
PRs → no-op.

Under `--launch-only` the dispatcher returns before the workflow finishes, so
there are no `rows` here — the caller that consumes the completion
(`/ggx-on-duty --demo`) runs the demo pass instead. Then STOP.

---

## Guardrails

- **Never fire the §5.2 `Workflow` launch before Step 4 lock completes.** A second invocation triggered seconds after the first must see locked tickets, not race-pickable ones.
- **The §5.2 script's agents must run at `bypassPermissions` and without worktree isolation** — these are set in `workflows/dispatch-fanout.workflow.js` (the only place agents are now spawned). Interactive permission prompts inside background agents would stall the whole batch, and the ff pipelines (`/port:ff` / `/dev:ff`) create their own worktrees so script-level isolation would collide. Do not reintroduce a dispatcher-level `Agent`-tool spawn (the legacy `--classic` path that did so was retired in GGC-55).
- **Never auto-checkout or auto-clean (discard) the user's tree.** The ONE permitted mutation is the Step 1.3 labeled auto-stash of **residue-allowlisted files only** (`pubspec.lock`-class machine-regenerated lockfiles) — non-destructive, announced on stdout, recoverable via `git stash pop`, never auto-popped. Tracked modifications OUTSIDE the allowlist are a human's in-progress work and still abort — the dispatcher never sweeps real edits into a stash the user didn't ask for. Checkout, reset, and clean stay forbidden; remaining pre-flight aborts are explicit and the user fixes their own state.
- **Never proceed past Step 4.0 dry-run gate.** `--dry-run` must be 100% read-only end-to-end.
- **Q1/Q3 omit the `state` filter — never re-add one.** The label is the dispatch signal; status is filtered post-fetch (§2.0). Re-adding `state: unstarted` to Q3 silently drops every post-port dev handoff (port leaves the ticket at `In Progress` and the human reviewer relabels without touching status). Q2/Q4 deliberately use the state name `In Progress` for recovery — that's the only path where a state-name filter is used.
- **Never write to a registered repo's `claude-reports/` from outside that repo.** Only the spawned ff agents touch their own worktree's reports; dispatcher's own writes stay in `<main>/claude-reports/dispatcher/`.
- **Lock is released on every exit path.** Including aborts in Step 0/1, empty-tickets in Step 2/3, port config missing in Step 3.5, dry-run in Step 4, partial lock in Step 4.2, and normal completion in Step 6.5.
- **`branch_prefix: auto` repos require `--team:<KEY>`.** No silent fan-out across teams.
- **All user-facing output is English.** Per repo convention.
- **Slack notify points are exclusively §4.2 (batch-abort) and §6.5 (digest).** Both go through `/_slack-notify` (opt-in via `~/.claude/commands/profiles/ggx-slack.json` — the install.sh symlink to `commands/dev/profiles/ggx-slack.json`; fail-soft, always exit 0). **Invoke the skill unconditionally — NEVER probe the config path yourself to decide whether to call it.** Config discovery, the enabled gate, and the silent no-op all live in `/_slack-notify` Step 0; a caller that hand-checks paths and guesses wrong silently drops the digest (this happened on 2026-06-05 — two stale paths were probed, the run mis-concluded "unconfigured", and the digest was skipped). NEVER insert a notify call between the §4.3 dispatch table and the §5.2 `Workflow` launch — the table + the `Workflow` tool call must stay in one assistant message; any tool call in between breaks that contract. No per-ticket or batch-start pings — the §6.5 digest is the batch's single Slack surface.
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
