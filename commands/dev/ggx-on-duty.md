---
name: ggx-on-duty
description: >
  Working-hours on-duty loop on /loop DYNAMIC mode (self-paced wakeups,
  no fixed interval). Two legs: (1) classify→dispatch chain —
  /ticket-analyze in REAL WRITE mode always runs immediately before
  /ggx-dispatcher so the dispatcher never sweeps an unclassified queue;
  (2) PR health poll (~1h) — ONE pass covering CI transition alerts,
  /ggx-pr-resolver --batch (whose two-stage gate — mechanical pre-filter
  then an LLM judge over ALL open threads — skips PRs with nothing
  actionable, so polling everything is cheap). Local always-on worker
  replacing the (currently disabled) 2x/day cloud routines while the
  user is at their desk.
Prerequisite: >
  Same as /ggx-dispatcher: cwd is the MAIN worktree of a registered repo,
  on default branch, clean tree, Linear MCP + gh auth, USER_NAME set.
  The TARGET repo should gitignore `.ggx-on-duty/` (loop state must never
  be git-tracked; the command also self-ensures this at runtime — see "On
  invocation" step 2). Assumes a SINGLE on-duty session per repo (no
  concurrent-run locking — do not start two).
---

# /ggx-on-duty — start the working-hours watch loop

**Usage**: `/ggx-on-duty [--team:<KEY>] [--no-dispatch] [--no-analyze] [--demo] [--until:HH:MM]`

- `--team:<KEY>` — passed through to /ticket-analyze and /ggx-dispatcher (required when the repo's `branch_prefix` is `auto`).
- `--no-dispatch` — disable Leg 1 entirely (watch-only mode).
- `--no-analyze` — Leg 1 runs the dispatcher without the preceding analyze step.
- `--demo` — after a Leg-1 dispatch completes, spawn a background serial demo-capture pass (`/_ui-demo-batch`) over the design-bug PRs it just shipped, so `design bug` PRs from the local on-duty loop carry a demo recording (GGC-29). Off by default. Requires a flutter repo + a logged-in simulator the local headless child can reach (it boots the persistent sim if none is running). dev/port/bug lanes unaffected.
- `--until:HH:MM` — optional auto-stop. Default: none (run until the user interrupts or closes the session).

## Non-negotiable guardrails

- The on-duty loop introduces **no Linear label writes and no new pipeline entry points of its own** — it only invokes existing commands (`/ticket-analyze`, `/ggx-dispatcher`, `/ggx-pr-resolver`), which own all label / worktree / branch mutation. The label ownership table in `ggx-dispatcher.md` stays authoritative.
- **Never inline-drive blocking commands — and never wrap a fan-out command in an Agent-tool subagent.** `/ggx-pr-resolver` runs tests and FANS OUT its own per-PR subagents, and nested `Agent`/`Task` spawns from inside a subagent FAIL (confirmed live 2026-06-05 — a chain wrapped in an Agent subagent batch-aborted with "spawn tool unavailable"; locks rolled back cleanly). So Leg 2 is spawned as a background **headless CLI session** via Bash `run_in_background`: `claude -p --permission-mode bypassPermissions "<leg prompt>"`, cwd = this main worktree — a headless session is TOP-LEVEL, so the fan-out works natively, and the Linear MCP connector (account-auth) is available headlessly (probe-verified). A wake cycle always completes in ~1-2 minutes. **D22 (default since 2026-06-12):** Leg 1's dispatch is a top-level `Workflow` task owned by the on-duty session — script-spawned agents are level-1 (no nested-spawn problem) and on-duty is already top-level, so no headless child is needed for it; the only inline work is the dispatcher's bounded Step 1-4 launch (not the join — see Leg 1), and worker contexts never flow back (only the `{counts, rows}` return). The Phase-A analyze step still runs in a headless child.
- **No lockfile probing.** Liveness of spawned background agents is tracked exclusively via the harness's background-completion notifications, recorded as booleans in the state file. Never read or remove `/ggx-dispatcher`'s internal lock — its 600s staleness TTL is far shorter than a real run and external reads of it misjudge.
- **A leg failure never ends the loop.** Each leg runs in its own try/continue boundary; an error becomes one WARN line in the cycle summary. The loop ends only on user interrupt or `--until`.
- **Keep the session lean** (the loop lives 8+ hours in one context): each cycle contributes a one-line summary; full dispatcher / pr-resolver output stays in the spawned agents' own contexts and report files — cite paths, never paste tables back into the on-duty session.

## Demo capture prerequisite (login-gated screens — GGC-50)

ui-tweak (`design bug`) tickets run under `--auto` with GGC-14 navigate+capture ON, so a wake cycle may
dispatch a `demo` pass. Capturing a **login-gated** screen (booking flow, order tracking, payment, …)
only works when the demo device already holds a **logged-in debug `dev` build** the worktree build can
run on. This is a **one-time human setup**, never something the loop can do (OTP login is not
headless-drivable):

- Keep a logged-in debug `com.gogox.clientapp.dev` build on the target device (one-time manual OTP login).
- Pin the preview/demo flavor to `dev` so worktree builds don't collide on signature with the CI-signed
  staging app: add `flavor: dev` to the app repo's `<repo>/.gogox-claude.yaml` (GGC-7 override).

When this is not set up, the demo stage now **skips up-front with a surfaced reason** (no silent
"session-blocked"; see `commands/design/ui-tweak/demo.md` Step 1.4 + "Demo device prerequisite"). A missing
demo never blocks a PR — the draft PR still opens via the normal Demo fallback chain. So this is an
optional capture-quality prerequisite, not a loop requirement.

## On invocation (once)

1. Run the /ggx-dispatcher pre-flight SUBSET: main worktree, default branch, clean tree, worktree prune, gh auth — its lockfile step is explicitly EXCLUDED (on-duty never touches the dispatcher lock). The dispatcher has NO Linear probe, so add on-duty's own: one Linear MCP call (e.g. `list_teams`) must succeed, and fail fast on a missing/mismatched `--team` for the repo's prefix. Abort start if any fail.
2. Ensure `.ggx-on-duty/` exists and is gitignored (state must never be git-tracked; same in-repo-but-untracked pattern as `claude-reports/`).
3. Init `.ggx-on-duty/state.json` if absent (resume cursors if present). On resume, **load-and-merge over defaults** — read the file, then fill in any key absent from it from the default skeleton below (a state file written by an older on-duty version, or hand-truncated, must never crash a wake cycle: a missing `self_pushed` is an empty map, not undefined). The merge is also where the GGC-41 `chain` growth is back-filled: an older state file whose `chain` lacks `script_path` / `args_path` / `resume_attempts` loads with `script_path:null, args_path:null, resume_attempts:0` — so a legacy file never crashes the RECONCILE resume branch and simply takes the defensive reset+wait path (null script_path → fall back, see RECONCILE below). The `v` field carries the schema version; if a future bump changes a key's shape, the merge is the place to migrate. **On resume, also unconditionally reset `chain.running`, `health.running`, and `demo.running` to `false`** — a fresh session has no in-flight background agents by definition; a laptop shutdown kills agents WITH the session, so a `running=true` carried over from a dead session would otherwise make those legs skip forever (the completion notification that flips it back will never arrive).

   **On-invocation is the reboot / new-session path — it NEVER attempts resume (GGC-41 Q1 boundary).** The unconditional `chain.running=false` reset above is the post-reboot case: the previous session (and therefore the `Workflow` journal) is GONE — `resumeFromRunId` is same-session-only, so there is nothing to resume against. This path keeps today's behavior: reset + wait for the next ~2h sweep. **No work is lost** — the `dispatcher-*-in-flight` labels persist on Linear, so the next Leg-1 sweep re-discovers and re-dispatches those tickets (work is redone from each ticket's worktree filesystem markers, not dropped). Journal-based resume is RECONCILE-exclusive (the same-session, journal-reachable path — see "Each wake cycle" below); on-invocation does not even read `chain.script_path` / `chain.args_path`.

```json
{
  "v": 1,
  "last_wake_wallclock": "<now ISO>",
  "chain":  { "running": false, "phase": null, "task_id": null, "script_path": null, "args_path": null, "resume_attempts": 0, "next_due": "<now>" },
  "health": { "running": false, "task_id": null, "next_due": "<now>" },
  "demo":   { "running": false, "task_id": null },
  "notified": {},
  "self_pushed": {},
  "analyzer_verdicts": {}
}
```

   - `chain` / `health`: background-agent liveness + **time-based** due timestamps (dynamic mode has no fixed tick to take a modulus of). Every spawn records the harness task id into `task_id` so RECONCILE can verify liveness via TaskList. `chain.phase` (`"analyze"`→`"dispatch"` | `null` when idle) drives the completion-notification branch in Leg 1; it is absent on `health` (health has no sub-phases). Older state files without `phase` (or carrying the retired `"chain"` value from a pre-GGC-55 session) load tolerantly — the on-invocation merge fills a missing `phase` with `null`, and a stale `running=true` chain is reset to `running=false` on resume anyway, so any legacy phase value is discarded next cycle.
     - **GGC-41 chain growth.** `chain.script_path` = the `scriptPath` the `Workflow` tool was launched with (needed to relaunch with `resumeFromRunId`); `chain.args_path` = the path to the dispatcher's `<RUN_TS>-<PID>.args.json` (the durable `{trunkSha, roster}` payload, discovered via the SAME newest-mtime glob Leg 2 / Finalize use); `chain.resume_attempts` = count of journal resumes already tried on the current dispatch (default `0`, **cap = 1** — a second death falls back to reset+wait so a poisoned roster row cannot crash-loop). All three are recorded at the Phase-B launch (Leg 1) and consumed by the RECONCILE resume branch. Legacy files lacking them load as `null/null/0` via the on-invocation merge and therefore take the defensive reset+wait path.
   - `demo` (`--demo` only): liveness of the post-dispatch serial demo-capture background session (GGC-29). No `next_due` — it is event-triggered (spawned on a dispatch completion that shipped design-bug PRs), not time-paced; `running` is the single-flight guard so two demo passes never drive the simulator at once. Absent on older state files → load as `{ running: false, task_id: null }`.
   - `notified`: `"<pr#>:<check>:<sha>" -> status` (CI dedup). `self_pushed`: `pr# -> sha` (suppress self-induced CI-rerun alerts). `analyzer_verdicts`: `ticketId -> verdict` (highlight changes only). No comment cursor needed — comment-actionability is judged statelessly inside /ggx-pr-resolver's two-stage gate (mechanical pre-filter + LLM judge over all open threads).

4. Start the loop: invoke `/loop` **with no interval** (dynamic mode) with the wake procedure below as the recurring prompt.

## Each wake cycle

**Gap detection first**: if `now - last_wake_wallclock` exceeds 2× the delay the previous wake scheduled (laptop slept), run in RECONCILE mode — re-list all PRs fresh, rebuild `notified` from current CI state, and frame the comment backlog as "while you were away". For any `running=true` flag, **verify the background agent actually still exists via TaskList using the stored `task_id` — TaskList is the SOLE liveness authority; never infer from report files** (the dispatcher's report is written only after the §5.2 `Workflow` run completes, so a live chain looks dead and a report-file probe would double-spawn); reset to false if it doesn't exist — but do NOT blind-reset, a sleep the session survived resumes its agents too and a blind reset would double-spawn. (The unconditional reset belongs to on-invocation only, where a fresh session provably has no agents.)

**RECONCILE dispatch resume (GGC-41).** The reset-to-false above is the OLD behavior for a dead chain: drop the in-flight flag and wait for the next ~2h sweep to re-discover the in-flight-labelled tickets and re-do every worker from scratch. RECONCILE is the SAME session that launched the dispatch, so its `Workflow` journal is still reachable — `resumeFromRunId` recovers every completed `agent()` call at zero cost. So before resetting a dead chain, attempt a journal resume. This applies **only** when ALL of:

- `chain.running === true` AND `chain.phase === "dispatch"` (the chain was in the fan-out phase, not the headless `"analyze"` phase — analyze has no `Workflow` journal to resume), AND
- TaskList shows `chain.task_id` is **gone** (the dispatch `Workflow` task died — same liveness probe as above; a LIVE task is left running untouched, never resumed), AND
- `chain.resume_attempts < 1` (cap = 1).

When all hold, resume instead of reset:

1. **Read the artifacts.** `chain.script_path` and the args from `chain.args_path` (the dispatcher's `<RUN_TS>-<PID>.args.json`). If `chain.script_path` is null/empty, OR `chain.args_path` is null/empty, OR the args file is missing/unreadable/not valid JSON → **fall back to today's reset + wait** (set `chain.running=false`, `chain.phase=null`; log one WARN line naming which artifact was missing). Defensive: never block the wake cycle on a missing artifact.
2. **Relaunch:** `Workflow({ scriptPath: chain.script_path, resumeFromRunId: chain.task_id, args: <parsed args.json> })`. Re-supplying the IDENTICAL `{ trunkSha, roster }` is what makes completed `agent()` calls hit the journal cache — a bare resume with no `args` would compute an empty roster (0 agents) and match nothing. The Workflow SCRIPT is unchanged; this is purely a relaunch with the original args + `resumeFromRunId`.
3. **On a successful relaunch:** set `chain.task_id` = the NEW run's task id, keep `chain.phase="dispatch"` and `chain.running=true`, and **increment `chain.resume_attempts`** (now 1). Completed workers return cached; only in-flight / unstarted roster rows re-run. The resumed run completes via the normal Leg-1 `"dispatch"` completion-notification close-out (which then clears the resume artifacts). `chain.args_path` / `chain.script_path` are unchanged (the resumed run uses the same roster + script).
4. **If the relaunch tool call itself errors** (journal not found, cross-session, tool unavailable) → **fall back to reset + wait** (set `chain.running=false`, `chain.phase=null`; log the error). No regression vs the old behavior — the in-flight labels still drive a fresh re-dispatch next sweep.
5. **If `chain.resume_attempts >= 1` already** (a prior resume of THIS dispatch also died): do NOT resume again — reset + wait. The cap stops a poisoned roster row from crash-looping the chain; the in-flight labels re-dispatch it cleanly next sweep.

**Idempotency on a resumed worker (GGC-41 AC8 — verification, not new code).** A resumed worker may re-touch a ticket `/ggx-work` had already partially processed, but it does not double-post or redo finished work: (a) the ff walkers (`infer_dev_stage` / `infer_port_stage` / `infer_ui_stage`) derive stage from the worktree's filesystem markers, so a re-run resumes mid-pipeline rather than redoing from scratch; (b) the idempotent comment markers — `<!-- ggx-work-error -->` (`/ggx-work` Step 4.3), `<!-- dispatch-fallback-error -->`, and GGC-40's `<!-- dispatch-triage-* -->` — are list-then-skip guarded, so a second pass posts nothing new; (c) the `dispatcher-*-in-flight` label is the resume signal and is never removed by the script. These hold by construction of the existing pipeline — the resume path adds no new dedup requirement.

Wake cycles are SERIAL: the recurring prompt is never re-entrant; completion notifications arriving mid-cycle are folded into the current cycle's summary, and state is written exactly once, at Finalize.

### Leg 1 — Classify → Dispatch chain (when `now >= chain.next_due`, ~every 2h; unless --no-dispatch)

**Invariant: the dispatcher NEVER runs on an unclassified queue — every dispatch cycle is immediately preceded by an analyze run. There is no standalone dispatch.**

- `chain.running` → the chain is in flight; **skip starting a new one.** On the completion notification, branch on `chain.phase`:
  - `"dispatch"` → the whole chain is done: fold the `{counts, rows}` return value into the next summary, then set `chain.next_due = now + 2h`, `chain.running=false`, `chain.phase=null`, and clear the GGC-41 resume artifacts (`chain.script_path=null`, `chain.args_path=null`, `chain.resume_attempts=0`) — the dispatch finished, so there is nothing left to resume; a stale path here would only mislead a future RECONCILE.
  - `"analyze"` → analyze finished; **advance to Phase B (inline launch-only dispatch) THIS cycle** (see below); do NOT bump `next_due` yet.
- Else **start the chain** via the two-phase workflow path (the only path):

  **Two phases; the dispatch fan-out is owned by THIS session for live `/workflows` visibility.**
  - **Phase A — analyze (headless):** spawn the SAME background headless `claude -p` session, but running ONLY `/ticket-analyze --non-interactive [--team:<KEY>]` — this keeps the analyzer's per-ticket sweep context OUT of the on-duty session. Set `chain.running=true`, `chain.phase="analyze"`, store `task_id`. (Under `--no-analyze`, skip Phase A and go straight to Phase B this cycle.)
  - **Phase B — dispatch (inline, launch-only):** on Phase-A completion, invoke `/ggx-dispatcher --launch-only [--team:<KEY>]` **INLINE in this session**. Its Step 1-4 (pre-flight incl. the §1.7 P2 permissions gate, discovery, race-lock) run synchronously — bounded and fast — then §5.2 fires the `Workflow` tool, now **owned by the on-duty session**, so the per-ticket fan-out tree renders in your `/workflows`. `--launch-only` returns the workflow `runId` immediately and does NOT babysit (no §5.2 step-4 heartbeat / step-5 consume — on-duty owns those). Record the `runId` into `chain.task_id`, set `chain.phase="dispatch"` (keep `chain.running=true`). Inline-driving Step 1-4 here is allowed: the "never inline-drive blocking commands" guardrail targets the tens-of-minutes JOIN, not this bounded launch.
    - **Record the GGC-41 resume artifacts at this launch.** Set `chain.script_path = $HOME/.claude/workflows/dispatch-fanout.workflow.js` (the `scriptPath` `--launch-only` used), and `chain.args_path` = the dispatcher's `<RUN_TS>-<PID>.args.json` (emitted by `/ggx-dispatcher --launch-only` §5.2 step 2a, alongside the in-flight TSV) — discover it with the SAME newest-mtime glob Leg 2 / Finalize already use: `ls -t claude-reports/dispatcher/*.args.json 2>/dev/null | head -1`. If the glob finds no args.json (the dispatcher aborted before §5.2 step 2a, or an older dispatcher version that predates GGC-41), set `chain.args_path = null` — RECONCILE will then take the defensive reset+wait path. Also set `chain.resume_attempts = 0` on a FRESH dispatch (this is a new run, not a resume), so the cap counts only resumes of THIS dispatch.
  - On the dispatch workflow's completion notification: consume ONLY the structured `{counts, rows}` return (`TaskOutput` tail — never the per-ticket transcripts, so the session stays lean), then close out the chain as in the `chain.running` branch above. **Then, when `--demo` is set, trigger the demo pass** (see "Demo pass" below) over the design-bug rows just shipped — do NOT block the close-out on it.
- **Leg 1 invocation decision (D22 — promoted to DEFAULT 2026-06-12; classic retired GGC-55).** The two-phase workflow path (headless analyze → inline `/ggx-dispatcher --launch-only` dispatch owned by the on-duty session, live `/workflows`) is now the **only** path. It became safe as the sole path because D21's two blockers had already cleared — the P2 silent-stall hard-aborts **before any ticket is locked** (`ggx-dispatcher.md` Step 1.7, GGC-20), and the verify-agent level-2 deadlock is solved by the R4 headless auditor (GGC-19 / PR #81). The old single headless `claude -p` chain (`--classic`, `chain.phase="chain"`) was deleted in GGC-55; reversion, if ever needed, is `git revert` from tag `pre-classic-removal`, not a live fallback flag. If headless dispatch is ever required again, run `/ggx-dispatcher` inside a headless `claude -p` child — the `Workflow` tool is available there (probe-confirmed, GGC-55).
- **Why chained is safe**: ticket-analyze posts the analysis comment BEFORE the label write, so the dispatcher never consumes a half-written verdict; analyzer and dispatcher touch disjoint ticket sets by construction (analyzer's Step 1.5.5 skips `ready-to-*` / `dispatcher-*-in-flight`; Step 8.2 pre-write re-check guards the reverse direction).
- **Known edge — never hand-label verdict labels while the chain is running**: the analyzer's label write computes `current − analyzer-owned-labels + verdict` and will overwrite a `ready-to-dev` you add mid-run. The summary must state when the chain is in flight.
- **~2h cadence rationale**: analysis comments are append-only (a fresh comment each run) and `need-revision`/`need-dependency` tickets are re-analyzed every run — tighter cadence spams stuck tickets. Tune `next_due` increment if latency vs noise balance shifts.

### Leg 2 — PR health poll (when `now >= health.next_due`, ~every 1h)

ONE pass over `gh pr list --author @me --state open` covering CI and resolution. No separate event-triggered path — `/ggx-pr-resolver`'s two-stage gate (mechanical pre-filter, then LLM judge over ALL open threads deciding ACT/HANDLED/HOLD by substance) makes polling every PR affordable, so the poll IS the event detection.

**Degraded-read guard**: if the `gh pr list` call fails, or returns empty when the previous poll was non-empty, treat this poll as DEGRADED — retry next wake, and **skip ALL evictions/seeding this cycle** (otherwise an API blip looks like "every PR merged" and purges `notified`/`self_pushed`, causing a re-notify storm). A gh secondary-rate-limit response (HTTP 403 + `Retry-After`) is treated the same as DEGRADED (skip evictions, stretch `next_due`) — never per-PR silent-skip, which is indistinguishable from all-clean.

**In-session vs spawned (so the wake cycle stays ~1-2 min)**: step 1 (CI check) runs IN-SESSION — it is pure `gh` reads + dedup bookkeeping, no blocking work. Step 2 (resolve) is SPAWNED as a background **headless CLI session** (the batch fans out per-PR subagents — same nested-spawn constraint as Leg 1); it does blocking work (the resolver runs tests), so it is never inline-driven; the session only records its `task_id` and folds its completion into a later cycle's summary.

1. **CI check (in-session, pure reads)**: key `<pr#>:<check>:<headSha>`; notify only on transition (absent|green → red), dedup via `notified`. `headSha == self_pushed[pr#]` → do NOT swallow: still report a red, but tagged `(self-pushed — rerun from our own push)`, and clear `self_pushed[pr#]` once that SHA's CI reaches a terminal state (success/failure). Never suppress by "one cycle" — cycle length is dynamic and CI duration is not. Evict keys for MERGED/CLOSED/superseded SHAs (unless DEGRADED).
2. **Resolve (background)**: skip if `health.running`; else spawn ONE background headless CLI session (`claude -p --permission-mode bypassPermissions`, same mechanism as Leg 1) running `/ggx-pr-resolver --batch --user=@me --auto`, passing a **skip-set of branch names** (headRefName is the canonical key — PR numbers cannot work: chain-in-flight tickets may have no PR yet, `/dev:ship` opens it last): (a) branches the Leg-1 chain currently has in flight — while `chain.running`, read the dispatcher's early in-flight file `claude-reports/dispatcher/<RUN_TS>-<PID>.inflight.tsv` (written right after its §4.1 ticket race-locks, before any §5 agent spawns — see `ggx-dispatcher.md` §4.4; discover via newest-mtime glob — on-duty cannot know the background subagent's RUN_TS/PID and must never read the dispatcher's `.lock` file); do NOT rely on the resolver's own in-flight-label read — `/dev:ship` can remove the label mid-sweep and the resolver would walk into a worktree `/dev:ff` is still writing to —, and (b) nothing else — the resolver's two-stage gate and ownership guard handle the rest. On completion: flip flag, **set `health.next_due = now + 1h` HERE (on completion, not at spawn** — a 70-min batch would otherwise re-fire immediately and compress the interval), record pushed SHAs into `self_pushed`, fold `needs-human` PRs into the notification.

### Demo pass (`--demo` only — event-triggered by a Leg-1 dispatch completion)

Spawned (not time-paced) when a Leg-1 **workflow** dispatch completes with ≥1
shipped design-bug PR. Mirrors GGC-29's "serial pass" design: on-duty owns the
workflow completion (D22 `--launch-only`), so on-duty — not the dispatcher —
triggers the demo capture.

- **Trigger** (in the Leg-1 dispatch close-out, when `--demo`): from the consumed
  `{rows}`, take rows where `uiTweak === true && outcome === "done"` with a
  non-null `prUrl`. None → nothing to do.
- **Single-flight**: skip if `demo.running` (a prior batch's demo pass is still
  capturing — only one actor may drive the simulator at a time; this guard is
  what makes the serial-by-construction guarantee hold across wake cycles).
- **Spawn** ONE background headless CLI session (`claude -p --permission-mode
  bypassPermissions`, same mechanism as the legs — keeps the wake cycle ~1-2 min;
  a blocking inline pass would stall it) running
  `/_ui-demo-batch '<json [{ticketId, prUrl}, …]>'`, cwd = this main worktree.
  Set `demo.running=true`, store its `task_id`. The local headless child shares
  this laptop, so it reaches the simulator (a cloud run could not — documented
  no-op there).
- **On its completion notification**: set `demo.running=false` and fold its
  one-line summary (`<C> captured, <S> skipped`) into the next cycle's summary.
- **Fail-soft**: `/_ui-demo-batch` always exits 0; a demo failure never blocks
  ship, never fails a wake cycle, and never touches ticket/PR state beyond an
  idempotent PR comment. A leg failure still never ends the loop (guardrail).

### Finalize (every wake)

1. ONE batched notification (red CI, resolver reports, needs-human PRs, analyzer verdict CHANGES). Channel `/_slack-notify`; ALSO append to `.ggx-on-duty/digest.md` (durable fallback — Slack-unconfigured is a silent no-op).
2. Persist state (`last_wake_wallclock`, cursors — comment cursors only post-notify). Write deterministically: render the FULL JSON to a temp file and `mv` over `state.json` (atomic; never hand-reprint partial JSON — silently dropped keys corrupt dedup).
3. One-line summary: `wake 14:32 | chain: in-flight (CAF-583, CAF-643) | PRs: 5 green, #492 resolver spawned | sweep: due 15:10`. When `chain.running`, **surface the in-flight ticket IDs** in the `chain:` segment by reading column 1 (`<ticket-id>`) of the dispatcher's early in-flight file `claude-reports/dispatcher/<RUN_TS>-<PID>.inflight.tsv` — the SAME newest-mtime glob Leg 2 already uses for its skip-set (§ "Leg 2 — Resolve"; on-duty cannot know the background subagent's `RUN_TS`/`PID` and must never read the `.lock`). This is the cheapest ticket-level visibility. Under D22 the dispatch `Workflow` task is owned by the on-duty session (so its fan-out tree renders in your `/workflows`), but the in-flight TSV remains the lean way to surface IDs in the one-line summary without tailing the workflow output; keep it to IDs only — per-ticket detail stays in the workflow's own context + its report file (lean-session guardrail). If the glob finds no current file (chain just spawned, not yet past its §4.1 race-lock), print `chain: in-flight (roster pending)` rather than guessing. When `demo.running` (or a demo pass completed this cycle), append a `demo:` segment (e.g. `demo: capturing (2 PRs)` / `demo: 2 captured, 1 skipped`).
4. `--until` reached → **DRAIN, don't hard-stop** (owner decision D15): spawn nothing new from this point; if background agents are still in flight, keep waking only to collect their completions (fallback heartbeat 1800s), then write the final summary and do NOT reschedule. A clean join preserves the completion notifications and the `self_pushed` writes they carry (labels would self-recover via Q2/Q4 anyway, but drained state needs no recovery). Otherwise **choose the next wakeup dynamically**:

## Dynamic pacing (how to pick the next wakeup)

- Background agents in flight → their completion already re-wakes the session; schedule only a long fallback heartbeat (**1800s**) in case one hangs.
- Otherwise sleep until the earliest of `chain.next_due` / `health.next_due` (**~3600s** cap when the board is quiet: no open PRs, all green, nothing due soon).
- Exception — an imminent external state change we cannot be notified about (e.g. CI running on a SHA we just pushed and want to confirm green): a short in-cache poll (**240–270s**) is allowed until it settles.
- Never pick ~300s (worst case for prompt-cache economics: pays the cache miss without amortizing it). State the reason in each reschedule.

## Stopping / resuming

Interrupt the session or say "stop the loop" anytime. State persists; the next `/ggx-on-duty` resumes cursors instead of re-notifying. Restarting the session mid-day is cheap and is the recommended fix if the session context has grown heavy.
