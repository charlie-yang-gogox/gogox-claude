# /ggx-dispatcher — design plan

**Status**: Design — awaiting approval before implementation
**Owner**: Charlie
**Source**: `gogox-client-flutter/.claude/commands/dispatcher.md` (213 lines)
**Target**: `gogox-claude/commands/dev/ggx-dispatcher.md` → `/ggx-dispatcher`

---

## 1. Problem

Three things in flutter's dispatcher don't survive a move into gogox-claude:

1. **`trunk` is the default branch** — hardcoded; breaks for any non-flutter repo (most use `main`).
2. **Old MCP prefix** — `mcp__linear-server__*`; gogox-claude commands (`/dev`, `/port:*`) use `mcp__claude_ai_Linear__*`.
3. **Old dispatch targets** — `/port --ticket:<id> --auto` and `/dev <id> --auto` are flutter-local monolithic commands. In gogox-claude the canonical pipeline orchestrators are `/port:ff` and `/dev:ff`, with internal `state.json`, HITL gates, and G1-G9 auto-decision rules.

Plus one mechanical: flutter's dispatcher has no concept of a Linear team filter, so the `list_issues` queries depend on the assignee belonging to exactly one team. That's brittle for anyone who's also assigned tickets in other repos' teams.

## 2. Invocation model

User opens a Claude session inside the repo whose tickets they want dispatched, then runs `/ggx-dispatcher`. Single-repo per invocation — dispatcher acts only on the cwd repo's team. To dispatch another repo's tickets, open a session in that repo.

Not cron-fired. Not multi-repo fan-out.

## 3. Profile resolution

Dispatcher needs three things from the cwd repo: `team_key`, `default_branch`, and confirmation that the repo uses Linear.

- **`team_key` and `ticket_system`** — read from the registry yaml at `~/.claude/commands/profiles/registry/$(basename "$(git rev-parse --show-toplevel)").yaml`. Falls back to `<repo-root>/.gogox-claude.yaml` if registry has no entry.
  - `ticket_system` must be `linear`. If `jira` / `none` → abort with clear message.
  - `branch_prefix` resolution:
    - **Concrete prefix** (e.g. `CAF`, `CET`) → `team_key = branch_prefix`. `--team:<KEY>` may also be passed; if present it must equal `branch_prefix` (case-insensitive after upper-casing) — mismatch → abort with explicit message naming both values.
    - **`auto`** (shared/core repos like `ggx-core-ios`) → require `--team:<KEY>` flag. With flag: `team_key = <KEY>`. Without: abort with `This repo uses branch_prefix: auto. Pass --team:<KEY> (e.g. --team:CET) to scope dispatch to one team.`
  - `--team:<KEY>` value is normalized via `tr '[:lower:]' '[:upper:]'` and validated against the union of known prefixes in `commands/dev/profiles/org.yaml`. Unknown key → abort.
- **`default_branch`** — `git symbolic-ref refs/remotes/origin/HEAD`. Falls back to `gh repo view --json defaultBranchRef`. If still nothing → abort. No silent assumption of `main`.

## 4. Decisions (locked)

| # | Decision | Rationale |
|---|---|---|
| D1 | Single-repo, cwd-driven | User opens a session in the target repo and runs `/ggx-dispatcher`. Multi-repo fan-out would need `repo_path` injection into the registry — out of scope, and the user's workflow already opens per-repo sessions. |
| D2 | Dispatch to `/port:ff` + `/dev:ff` | ff is the canonical orchestrator with state + auto-rules. No reason to invent a different surface. |
| D3 | Command location: `commands/dev/ggx-dispatcher.md` → `/ggx-dispatcher` | Top-level slash command. `ggx-` prefix follows the org-wide convention (cf. `/ggx-design-review`) and avoids colliding with the legacy flutter `/dispatcher` symlink without needing a `-v2` suffix. |
| D4 | Default branch via `git symbolic-ref` (gh fallback) | Zero-config per repo. flutter's `trunk` and other repos' `main` both work. |
| D5 | **Symmetric label ownership: both `/port:start` and `/dev:start` remove their actionable label at start. Dispatcher Step 4 does the same as a race-lock**. ff is idempotent safety net. | Dev's `/dev:start` already removes `ready-to-dev` at start (line 82). `/port:start` is augmented to mirror this — remove `ready-to-port` + assign + post comment + In Progress. Eliminates the asymmetric mid-pipeline label state that earlier drafts of this plan accepted. Side benefit: any mid-pipeline failure (locate-low, lint reject, MCP timeout) leaves the ticket in a "no actionable label, human must re-add to retry" state — same semantics dev already has. |
| D6 | Linear team key sourced from registry yaml `branch_prefix`, with `--team:<KEY>` flag override | `auto` repos (e.g. `ggx-core-ios`) require `--team:<KEY>` per invocation. In fixed-mode repos, `--team` is allowed but must equal the yaml's `branch_prefix` — mismatch → abort with explicit message. Prevents accidentally sweeping the wrong team's tickets. |
| D7 | MCP migration: `mcp__linear-server__*` → `mcp__claude_ai_Linear__*` | Aligns with `/dev` and `/port:*`. Schema for `list_issues` (new under this prefix) verified before implementation — see §8. |
| D8 | `/port:start` augmented (in `--auto` mode only) to perform ticket-init protocol; `/port:ship` keeps `need-spec-review` add | Splits port label transition cleanly: start does the init (mirrors `/dev:start`'s auto-mode init at line 82), ship adds the next-stage label. **HITL mode (`/port:start` without `--auto`) does NOT run the init**, preserving its current scaffold-only behavior. Avoids silent regression for users invoking `/port:start` interactively. The init step list (label remove, status In Progress, assign self, estimate=1 if none, post dispatch-start comment) is inlined in three places: `/dev:start`, `/port:start --auto`, `/ggx-dispatcher` Step 4. Each copy has a top-of-block comment: `# SYNC: when changing this list, also update /dev:start, /port:start --auto, /ggx-dispatcher Step 4`. Drift risk accepted in exchange for not introducing a new internal-fragment convention to the repo. |
| D9 | Pre-flight aborts if not in main repo's default branch | Dispatcher must run from the main repo root, on the default branch, with a clean tree (unless `--test`). Worktrees, feature branches, and dirty trees → abort with explicit guidance. Closes the worktree-cwd-drift edge case. |
| D10 | `--max-parallel` default 10, hard cap 20 | User wants aggressive batching at launch (overnight queues). 10 covers a typical day's actionable ticket count; cap 20 prevents accidental `--max-parallel:100` rate-limit incidents. Tune after first week of real usage. |
| D11 | Always dispatch via ff `--auto` only — no other flags except auto-detected `--no-figma` | Dispatcher's job is unattended batching; interactive flags (`--simple`, `--from`, `--prd:*`) are user-driven and don't belong here. `--no-figma` is auto-applied to dev tickets whose Linear description has no `figma.com/...` URL, since `/dev:ff` would otherwise stall waiting for Figma in a batch. |

## 5. Architecture

```
[User opens Claude session in target repo's main worktree, default branch]
            │
            ▼
   Step 0: Resolve profile
     - registry/<basename(cwd-repo)>.yaml → branch_prefix → team_key
       (fallback to <repo-root>/.gogox-claude.yaml)
     - ticket_system != linear → abort
     - branch_prefix == auto + no --team:<KEY> → abort with guidance
     - default_branch via git symbolic-ref (gh fallback)
            │
            ▼
   Step 1: Pre-flight (D9 guard)
     - acquire filesystem lock at claude-reports/dispatcher/.lock
       (write PID + ISO timestamp; >10 min old → stale, overwrite)
       fail → abort with "Another /ggx-dispatcher run in progress (PID X, started Y)"
     - assert NOT inside a worktree:
         [ "$(git rev-parse --git-common-dir)" = "$(git rev-parse --git-dir)" ]
       fail → abort with:
         "/ggx-dispatcher must run from the main repo, not a worktree.
          Run: cd $(git rev-parse --git-common-dir | sed 's|/.git||')
          then re-invoke /ggx-dispatcher."
       (The previous spec used --show-superproject-working-tree, which detects
        submodules, not worktrees — wrong primitive.)
     - if --test absent:
         on default_branch? → not on default: abort with "Switch to <default>"
         clean tree?         → dirty: abort with "Stash or commit first"
     - git worktree prune
     - gh auth status        → not authed: abort
     - log open PRs (informational)
            │
            ▼
   Step 2: Find tickets
     `mcp__claude_ai_Linear__list_issues` schema is { label: string,
     state: string, team: string, assignee: string } — `label` and `state`
     are SINGULAR, hence 4 separate queries:
       Q1: { label: "ready-to-port", state: "unstarted",   team, assignee: "me" }
       Q2: { label: "ready-to-port", state: "In Progress", team, assignee: "me" }
       Q3: { label: "ready-to-dev",  state: "unstarted",   team, assignee: "me" }
       Q4: { label: "ready-to-dev",  state: "In Progress", team, assignee: "me" }
     Mixed strategy: Q1/Q3 use type "unstarted" (catches To-do + Reopened);
     Q2/Q4 use name "In Progress" — type "started" would also catch In Review
     and Ready for QA, both post-work states that should NOT be re-dispatched.
     Tickets in Triage / Backlog / In Review / Ready for QA / Done / Canceled
     / Duplicate are excluded by design.
     - dedup by ticket id; merge labels arrays during dedup
     - duplicate-label detection: a ticket appearing in BOTH (Q1∪Q2) and
       (Q3∪Q4), OR whose merged labels contain both, is flagged as duplicate-
       labelled. Action per v1: remove both labels, post explanation comment,
       drop from batch.
     - priority sort, take up to --max-parallel (default 10, hard cap 20)
            │
            ▼
   Step 3: Anti-duplicate (per ticket)
     - PR check (gh pr list, word-boundary regex)
     - branch check via `git ls-remote --heads origin` (not local branch -r):
         port + remote branch exists → skip + remove ready-to-port + comment
         dev  + remote branch exists → proceed (expected: port created it)
            │
            ▼
   Step 3.5: Port config pre-check (only if any port ticket survives)
     - Read .claude/port-settings.json — file missing, originalProjectPath
       absent/empty, or expanded path not on disk → ABORT batch BEFORE any
       lock. Closes the failure mode where a misconfigured machine produces
       N zombie tickets (lock-removed + worktree-built + agent stops on
       missing config in /port:start).
     - Skipped entirely if survivors are dev-only.
            │
            ▼
   Step 4: Race-lock ALL surviving tickets (symmetric per D5)
     If --dry-run is set: print the planned dispatch table from Step 3 and
     STOP. Do NOT execute any of the mutations below — dry-run must be
     read-only end-to-end.
     For each ticket (port and dev alike), invoke the init-protocol from D12:
       - remove the actionable label (ready-to-port or ready-to-dev)
       - set status In Progress, assign self
       - set estimate to 1 if none
       - post "Dispatcher: starting <port/dev>…" comment
     ff (/port:start, /dev:start) repeats these idempotently as safety net.
            │
            ▼
     (Step 4 partial-failure recovery — see §7)
            │
            ▼
   Step 5: Parallel dispatch
     - Single message, N Agent calls, run_in_background: true
     - Per-ticket dispatch command:
         port label  → /port:ff --ticket:<ID> --auto
         dev  label  → /dev:ff <ID> --auto [--no-figma if applicable]
     - --no-figma auto-detection (dev only): if the fetched ticket description
       contains no `figma.com/(design|board|slides|make)/` URL, append --no-figma.
       Avoids /dev:ff stalling on missing figma in unattended batches.
     - All commands: --auto only. No --simple, no --from, no --prd:* — those
       are interactive flows the dispatcher does not own.
     - mode: bypassPermissions, no isolation worktree (ff manages its own)
            │
            ▼
   Step 6: Post-dispatch
     - WAIT for all spawned ff agents to complete (synchronous join).
       Implication: dispatcher session must remain open until the last ff
       agent finishes. For overnight batches, user must keep the machine
       awake (no sleep). Background agents disconnected by a closed session
       lose their MCP connection and their post-stage Linear writes will
       fail silently — explicitly NOT a supported flow.
     - Dev fallback: if /dev:ff did not set In Review, set it.
     - Port fallback: if /port:ship did not add need-spec-review, add it.
       (Both are belt-and-suspenders — ff usually handles its own end-state.)
     - Aggregate per-ticket reports: for each dispatched ticket, copy
       (or symlink) <worktree>/claude-reports/<session>/* into
       <main-repo>/claude-reports/dispatcher/<timestamp>-<pid>/<ticket-id>/
       so one batch's full audit trail lives under one directory.
     - Write claude-reports/dispatcher/<timestamp>-<pid>.md run summary
       (timestamp + pid suffix prevents collision under concurrent invocations).
     - Release filesystem lock (Step 1).
     - Summary line: dispatched / skipped (per reason) / failed
```

## 6. Anti-duplicate layers

Two filtering layers (Step 3) plus one symmetric locking layer (Step 4).

**Filtering (Step 3)**:

| Check | Action on hit | Why |
|---|---|---|
| PR exists for ticket-id | Skip + remove label + comment | Work landed/in-flight |
| Remote branch exists, label = `ready-to-port` | Skip + remove `ready-to-port` + comment | Port already produced a branch; re-running clobbers |
| Remote branch exists, label = `ready-to-dev` | **Proceed** | Expected — port created the branch, dev continues |

**Locking (Step 4)**: same action for both labels — remove + In Progress + comment. Closed by dispatcher; ff repeats idempotently.

Word-boundary regex `[/-]<ticket-id>($|[^0-9])` against `git ls-remote --heads origin` output (not `git branch -r` — local stale state unreliable). The CAF-27 / CAF-279 false-match bug from flutter is preserved as a regression test.

## 7. Failure handling

- **Concurrent invocation** → Step 1 lockfile (PID + timestamp). Stale > 10 min: overwrite. Otherwise abort with PID + start time.
- **MCP error fetching tickets** → retry once after 30s, then STOP with summary. Lockfile released.
- **Step 4 partial lock failure** — symmetric for port and dev. Recovery flow:
  1. If lock for ticket #N fails after #1..#N-1 already locked: best-effort unlock #1..#N-1 (re-add original label, post `Dispatcher: aborting batch — Linear MCP failure on <ticket>` comment).
  2. If unlock itself fails: post `Dispatcher: PARTIAL LOCK — manual recovery needed` comment.
  3. STOP. Lockfile released.
  - **stdout IS the audit trail in v1**. Every per-ticket lock attempt prints `<ticket-id>: locked ✓` or `<ticket-id>: failed (<reason>)` to the dispatcher session. If MCP is fully down and recovery writes also fail, the user is watching the terminal — they scroll up, see which tickets were locked before the failure, and manually fix via Linear UI. No filesystem intent log in v1.
  - This trade-off only holds while invocation is **manual + user-supervised**. Cron revival (§10 Q2) makes stdout invisible to the operator and is the trigger for adding intent-log persistence — see O7.
- **Agent dispatch failure (port or dev)** → ticket has no actionable label (removed in Step 4). Post failure comment via fallback. Human re-adds label to retry. Same semantics dev pipeline already has — locate-low / lint-reject / mid-pipeline crashes all yield "label off, comment posted" and require human triage.
- **`git symbolic-ref` returns nothing** → fallback to `gh repo view --json defaultBranchRef`. If still nothing → STOP. No silent `main`.
- **Inside a worktree** → STOP with the ready-to-paste `cd` command per Step 1.
- **Dirty tree / wrong branch (no `--test`)** → STOP, surface to user with explicit fix command. Do NOT auto-stash, do NOT auto-checkout (D9: explicit > clever).
- **`gh auth status` fails** → STOP. Dispatcher fully depends on gh for branch/PR checks; silent fallback would let duplicates through.
- **`--team:<KEY>` mismatch in fixed-mode repo** → STOP, name both the flag value and the yaml's `branch_prefix`.

## 8. Migration plan

1. **Verify `list_issues` MCP schema** (D7 prereq). Use `ToolSearch` to load `mcp__claude_ai_Linear__list_issues` and confirm it accepts: `team`, `assignee`, label filter, state filter. If schema diverges from the v1 dispatcher's call shape, pin the actual call signature in this plan **before writing the command**. No existing gogox-claude command uses `list_issues` — dispatcher is the first.
2. **Augment `/port:start`** (D8). Add an `--auto`-only init block: remove `ready-to-port`, set `In Progress`, assign self, estimate=1 if none, post dispatch-start comment. Mirror `/dev:start` line 82's auto-mode block. **HITL `/port:start` keeps current scaffold-only behavior** — no Linear writes outside `--auto`. Add `# SYNC: when changing this list, also update /dev:start line 82 and /ggx-dispatcher Step 4` comment at the top of the new block.
3. **Annotate `/dev:start`** (D8 sync). At the top of its existing line 82 auto-init block, add the matching sync comment listing the other two locations. No logic change.
4. **Write** `commands/dev/ggx-dispatcher.md` per this design. Step 4's mutation sequence carries the same sync comment.
5. **Hand-test (`--dry-run`)**: from cwd repo's main worktree on default branch, `/ggx-dispatcher --dry-run`. Should print the dispatch plan with no side effects (Step 4 mutations gated by dry-run flag).
6. **Hand-test (`--test --dry-run`)**: bypass branch/clean checks while inspecting plan.
7. **Hand-test guards**: cd into a worktree → confirm dispatcher refuses with paste-ready cd command; checkout a feature branch → confirm refusal; dirty tree → confirm refusal; `gh` not authed → confirm refusal; second instance while one running → confirm lockfile abort.
8. **Live test (port)**: ready-to-port one ticket, run `/ggx-dispatcher`. Verify Step 4 lock, `/port:ff` runs to `/port:ship`, and `/port:ship` adds `need-spec-review`.
9. **Live test (dev)**: ready-to-dev one ticket, run `/ggx-dispatcher`. Verify Step 4 lock + `/dev:ff` end-to-end. Test both with and without figma URL in description (latter should auto-pass `--no-figma`).
10. **Live test (auto repo)**: in `ggx-core-ios` (or any `branch_prefix: auto` repo), run `/ggx-dispatcher` without `--team` → expect abort with guidance. Run with `--team:CET` → expect scoped dispatch. Run with `--team:caf` (lowercase) → expect normalized + accepted. Run with `--team:XXX` (unknown prefix) → expect abort.
11. **Adopt elsewhere**: open a session in another linear-using repo, verify portability.
12. **Deprecate flutter copy**: delete `gogox-client-flutter/.claude/commands/dispatcher.md`. A deprecation note alone won't work — install.sh from gogox-claude doesn't manage that path, so flutter's local `/dispatcher` only goes away by removing the source file.

## 9. File inventory

New:
- `commands/dev/ggx-dispatcher.md` — the new command (~280 lines). Includes inline init-protocol mutation list with SYNC comment.

Touched:
- `commands/dev/dev/start.md` — add SYNC comment above existing line 82 auto-init block (no logic change).
- `commands/dev/port/start.md` — add `--auto`-only init block + SYNC comment (D8).
- `README.md` — add `/ggx-dispatcher` to the layout / skills note. Short "Dispatcher" subsection under Port pipeline.

Untouched:
- `install.sh` — no change. Earlier draft proposed `_*` namespace skip for a shared-fragment convention; that approach was rejected in favor of inline duplication with SYNC comments.
- `/port:ff`, `/dev:ff`, every other `/port:*` and `/dev:*` atomic stage — interface stable.
- `/init-project`, `commands/profiles/{org,platform/*,registry/*}.yaml` — no schema change.

## 10. Open questions / TODO

1. **Naming rationale (locked)** — `ggx-` prefix follows the org-wide convention (cf. `/ggx-design-review`). The `dispatcher` noun describes the intended end-state once cron is wired (Q2); until then this is effectively a manual batch worker, but the name scales when cron returns. Earlier drafts considered `/dispatcher-v2` (avoiding flutter's legacy `/dispatcher`) and `/batch-tickets` / `/work-queue`; the `ggx-` namespace makes those workarounds unnecessary.
2. **Cron re-introduction** — eventually wire `/ggx-dispatcher` (or its renamed successor) back to a cron service for unattended overnight runs. Prereqs: `Step 4` lock proven race-safe under concurrent invocations, partial-failure recovery battle-tested, **and** intent-log persistence added (Q7) — without that, cron failures are invisible.
3. **3-level agent nesting cost** — accepted as known cost. L1 dispatcher → L2 ff agents (×N) → L3 ff sub-agents (×~4 per ff). At `--max-parallel:10` peak we may hit ~40+ concurrent sub-sub-agents. Watch for rate-limit incidents in first week of usage; if recurrent, drop default to 5.
4. **`port-scoping-needed` label support** — flutter v1 doesn't dispatch this. Easy add via `/port:ff --simple`. Defer until someone asks.
5. **Profile resolution order** — registry-first vs local-yaml-first. `/dev` does local-first; dispatcher is suggested registry-first since it's global tooling. Tiny inconsistency. Worth flipping `/dev` for symmetry, or accept the divergence?
6. **`claude-reports/` location** — Step 6 now copies/symlinks per-worktree reports back to `<main>/claude-reports/dispatcher/<ts>-<pid>/<ticket-id>/`. Decide between hard copy vs symlink — symlink saves space but breaks if user later removes the worktree.
7. **Filesystem intent log — cron prerequisite, not v1 scope** — when invocation moves from manual to cron (Q2), stdout is no longer a viable audit trail. At that point, Step 4 needs to atomic-write a pre-fanout intent file (e.g. `claude-reports/dispatcher/<ts>-lock-attempt.md`) so the operator who wakes up tomorrow can see what dispatcher tried even if MCP was fully down. Out of v1 scope on purpose: stdout suffices for the supervised manual flow.
8. **ff state.json rationalization** — observation during this design: dispatcher's stdout-only model raised the question whether `/port:ff` and `/dev:ff` could similarly drop their `state.json` files in favor of artifact-derived stage detection. ff has cross-session resume + HITL pause + `--from` requirements that dispatcher does not, so the answer isn't symmetric. Captured as a separate study at `plans/ff-state-rationalization.md`. Not blocking ggx-dispatcher.
9. **`In Progress` name hard-coded** — Step 2's Q2/Q4 use the literal name `In Progress` to exclude `In Review` / `Ready for QA` (both type `started`). If a team renames `In Progress`, those queries return empty for that team and crash-recovery dispatch silently breaks for it. Today CAF / CET / DAF / DET keep the default name; verify with `mcp__claude_ai_Linear__list_issue_statuses` when onboarding a new team. Future fix if it becomes a real problem: switch to type filter + client-side post-filter on name.

## 11. Plan X — `dispatcher-*-in-flight` labels (May 2026)

**Trigger**: CAF-370 (2026-05-11) was dispatched twice. The first run hit the v8 nested-opus-spawn limit at apply; the v9 fix unblocked apply but the second run still stopped before verify. In both cases the dispatcher race-lock had removed `ready-to-dev` at lock time, so the partially-progressed ticket was invisible to subsequent dispatcher runs — the user had to manually re-add `ready-to-dev` to retry, or skip the dispatcher entirely and resume from a main session. The friction surfaced a deeper asymmetry: the `/dev:ff` walker is fully filesystem-driven and would correctly resume from any partial state, but the dispatcher pickup logic was Linear-label-driven and threw away its own resume signal at lock time.

**Change**: introduce two in-flight labels `dispatcher-port-in-flight` / `dispatcher-dev-in-flight`. Lock-time label swap (remove `ready-to-*`, add `dispatcher-*-in-flight`) keeps a persistent "claimed by dispatcher" marker on the ticket until `*:ship` removes it on full success. Crash-recovery queries (Q2/Q4) target the in-flight label directly instead of relying on `ready-to-*` + `In Progress` heuristics. Two labels (not one) so the dispatcher can route port vs dev from Linear state alone without filesystem inspection.

**Files touched**:

- `commands/dev/ggx-dispatcher.md` — §2 Q2/Q4 retargeted to in-flight labels; §2.2 conflict checks expanded; §3.1 PR-exists strips both actionable + in-flight; §3.2 lane-aware branch behavior; §4.1 lock swap; §4.2 rollback by lane; §5.1/5.2 lane-aware dispatch routing; §6.2 success fallback strips in-flight; new label state machine table in Guardrails.
- `commands/dev/dev/ship.md` Step 3 — removes `dispatcher-dev-in-flight` alongside the `In Review` status flip.
- `commands/dev/port/ship.md` Step 13 — drops `dispatcher-port-in-flight` alongside adding `need-spec-review`; HITL path drops in-flight even when skipping `need-spec-review`.
- `commands/dev/dev/start.md` + `commands/dev/port/start.md` — INTENTIONALLY untouched. The in-flight label is dispatcher-only: adding it from `*:start` would silently mark manual runs as dispatcher-recoverable, which is not what manual users want.

**Invariants** (encoded in §2.2 conflict checks):

1. A ticket has at most one of `ready-to-port` / `dispatcher-port-in-flight` at any time. Both = inconsistent lock state (skip + comment).
2. A ticket has at most one of `ready-to-dev` / `dispatcher-dev-in-flight` at any time.
3. A ticket never has BOTH `dispatcher-port-in-flight` AND `dispatcher-dev-in-flight` — the two pipelines do not share a ticket.
4. `dispatcher-*-in-flight` is added by dispatcher §4.1 and removed by exactly four code paths: `*:ship` on success, dispatcher §3.1 on PR-exists cleanup, dispatcher §4.2 rollback on fresh lanes, dispatcher §6.2 success fallback.

**Trade-offs accepted**:

- Two new Linear labels need to exist on each team's workspace. Cheap (one-time setup).
- `/port:ff` and `/dev:ff` must be idempotent enough to be safely re-dispatched on recovery — they already are. Both pipelines derive stage from filesystem markers, so a recovery dispatch skips completed stages naturally.
- Manual users (`/dev:start` direct, `/port:start` direct) do NOT get auto-recovery via dispatcher. Their failures stay where they failed; they have to re-add `ready-to-*` if they decide to dispatcher-recover later. This is intentional — manual route opt-out of dispatcher state is the simplest mental model.
- Workspace setup: each Linear team must have both labels defined. Onboarding step (or future `/init-project`-style helper) should auto-create them.
