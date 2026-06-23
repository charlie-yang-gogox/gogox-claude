---
name: ggx-on-duty
description: >
  [DEPRECATED] Working-hours on-duty loop on /loop DYNAMIC mode (self-paced wakeups,
  no fixed interval). Two legs: (1) classify + dispatch — DECOUPLED
  cadences (GGC-70): /ticket-analyze in REAL WRITE mode runs slowly
  (~2-3h, so it never spams stuck tickets) to keep the ready-to-* queue
  classified, while /ggx-dispatcher sweeps that queue on a faster ~1h
  tick (label-driven, so running it without a fresh analyze is safe);
  (2) PR health poll (~4h) — ONE pass covering CI transition alerts,
  /ggx-pr-resolver --batch (whose two-stage gate — mechanical pre-filter
  then an LLM judge over ALL open threads — skips PRs with nothing
  actionable, so polling everything is cheap). Local always-on worker
  replacing the (currently disabled) 2x/day cloud routines while the
  user is at their desk.
Prerequisite: >
  Same as /ggx-dispatcher: cwd is the MAIN worktree of a registered repo,
  on default branch, clean tree, Linear MCP + gh auth, USER_NAME set.
  The TARGET repo keeps `.ggx-on-duty/` out of git via `.git/info/exclude`
  (loop state must never be git-tracked; the command self-ensures this at
  runtime through the LOCAL exclude file — NOT the tracked `.gitignore`,
  which would dirty the tree and abort the dispatcher — see "On invocation"
  step 2). Assumes a SINGLE on-duty session per repo (no
  concurrent-run locking — do not start two).
---

# /ggx-on-duty — start the working-hours watch loop

> **⚠️ DEPRECATED / PAUSED (GGC-79, 2026-06-23).** The on-duty watch loop is a
> paused initiative — no longer actively maintained. It remains fully runnable
> (this is a soft deprecation, not a removal), but expect the section numbers,
> contracts, and decisions referenced below to drift from the live
> `/ggx-dispatcher` · `/ticket-analyze` · `/ggx-pr-resolver` commands over time.
> Re-validate before relying on it. The once-planned `--metric` passthrough into
> this loop was dropped with the pause. Note: `/ggx-dispatcher --launch-only`
> exists solely for this loop's D22 owned-Workflow dispatch and now has no active
> consumer — it is kept (harmless) in case on-duty is revived.

**Usage**: `/ggx-on-duty [--team:<KEY>] [--no-dispatch] [--no-analyze] [--demo] [--until:HH:MM]`

- `--team:<KEY>` — passed through to /ticket-analyze and /ggx-dispatcher (required when the repo's `branch_prefix` is `auto`).
- `--no-dispatch` — disable Leg 1 entirely (both the analyze and dispatch ticks; watch-only mode).
- `--no-analyze` — skip the analyze tick (Leg 1a) entirely; dispatch (Leg 1b) still runs on its own ~1h cadence off existing `ready-to-*` labels.
- `--demo` — after a Leg-1 dispatch completes, spawn a background serial demo-capture pass (`/ggx-demo --batch`, GGC-66 — absorbed `/_ui-demo-batch`; self-discovers every open design-bug PR of mine still lacking a demo), so `design bug` PRs from the local on-duty loop carry a demo recording (GGC-29). Off by default. Requires a flutter repo + a simulator the local headless child can reach (it boots the persistent sim if none is running; the device need not be pre-logged-in — the Step 2.4 gate auto-resolves a staging account from Notion and logs in, no `demo_auth` config required, GGC-65). dev/port/bug lanes unaffected.
- `--until:HH:MM` — optional auto-stop. Default: none (run until the user interrupts or closes the session).

## Non-negotiable guardrails

- The on-duty loop introduces **no Linear label writes and no new pipeline entry points of its own** — it only invokes existing commands (`/ticket-analyze`, `/ggx-dispatcher`, `/ggx-pr-resolver`), which own all label / worktree / branch mutation. The label ownership table in `ggx-dispatcher.md` stays authoritative.
- **Never inline-drive blocking commands — and never wrap a fan-out command in an Agent-tool subagent.** `/ggx-pr-resolver` runs tests and FANS OUT its own per-PR subagents, and nested `Agent`/`Task` spawns from inside a subagent FAIL (confirmed live 2026-06-05 — a chain wrapped in an Agent subagent batch-aborted with "spawn tool unavailable"; locks rolled back cleanly). So Leg 2 is spawned as a background **headless CLI session** via Bash `run_in_background` (plain `run_in_background` — NEVER `nohup`/`&`, which would make the harness track the launcher shell instead of the real `claude -p` child and lose its completion notification): `claude -p --permission-mode bypassPermissions "<leg prompt>"`, cwd = this main worktree — a headless session is TOP-LEVEL, so the fan-out works natively, and the Linear MCP connector (account-auth) is available headlessly (probe-verified). A wake cycle always completes in ~1-2 minutes. **D22 (default since 2026-06-12):** Leg 1's dispatch is a top-level `Workflow` task owned by the on-duty session — script-spawned agents are level-1 (no nested-spawn problem) and on-duty is already top-level, so no headless child is needed for it; the only inline work is the dispatcher's bounded Step 1-4 launch (not the join — see Leg 1), and worker contexts never flow back (only the `{counts, rows}` return). The analyze tick (Leg 1a) still runs in a headless child.
- **No lockfile probing.** Liveness of spawned background agents is tracked exclusively via the harness's background-completion notifications, recorded as booleans in the state file. Never read or remove `/ggx-dispatcher`'s internal lock — its 600s staleness TTL is far shorter than a real run and external reads of it misjudge.
- **A leg failure never ends the loop.** Each leg runs in its own try/continue boundary; an error becomes one WARN line in the cycle summary. The loop ends only on user interrupt or `--until`.
- **Keep the session lean** (the loop lives 8+ hours in one context): each cycle contributes a one-line summary; full dispatcher / pr-resolver output stays in the spawned agents' own contexts and report files — cite paths, never paste tables back into the on-duty session.

## Demo capture prerequisite (login-gated screens — GGC-65)

ui-tweak (`design bug`) tickets run under `--auto` with GGC-14 navigate+capture ON, so a wake cycle may
dispatch a `demo` pass. Capturing a **login-gated** screen (booking flow, order tracking, payment, …)
needs the app logged in. Since **GGC-65** this is handled automatically — no one-time human OTP login:

- **No config required (GGC-65 auto-resolve).** The preview Step 2.4 gate is **always active**: it
  derives `app` from the profile `product`, infers `region` from the ticket (fallback `hk`), and fetches
  a **staging** QA automation account from the Notion "Testing accounts" page at runtime, logging in on a
  fresh device (creds never stored in the repo). The demo build is the staging flavor (`--flavor stag`),
  so the staging accounts fit — no `flavor: dev` pin needed.
- **Optional `demo_auth` override** in `<repo>/.gogox-claude.yaml` (`app` / `region` / `notion_page` /
  `account_label` / `login_probe_host`) pins any field when the derived default is wrong (e.g. force an
  SG account for an SG-only ticket the inference misreads).
- **Gating spike (GGC-65)**: this assumes the staging build accepts **password-only** login with no OTP.
  If an account requires OTP/2FA, auto-login is blocked and capture fail-silents (login wall) — verify
  one automation account password-logs-in cleanly before relying on it.

When auto-login **fails** (Notion fetch failed / creds rejected / login UI not found / OTP wall),
navigate+capture **fail-silents** on a
login-gated screen (see `commands/design/ui-tweak/preview.md` Step 2.4/2.5 — capture is the sole job of
`preview`, and the batch path reuses that same procedure via `/ggx-demo --batch`). A missing demo never
blocks a PR — the draft PR still opens via the normal Demo fallback chain. So this is an optional
capture-quality prerequisite, not a loop requirement.

## On invocation (once)

1. Run the /ggx-dispatcher pre-flight SUBSET: main worktree, default branch, clean tree, worktree prune, gh auth — its lockfile step is explicitly EXCLUDED (on-duty never touches the dispatcher lock). The dispatcher has NO Linear probe, so add on-duty's own: one Linear MCP call (e.g. `list_teams`) must succeed, and fail fast on a missing/mismatched `--team` for the repo's prefix. Abort start if any fail.
2. Ensure `.ggx-on-duty/` exists and is git-ignored **via `.git/info/exclude` (local, per-clone, UNTRACKED) — NOT the tracked `.gitignore`** (GGC-64). Editing the tracked `.gitignore` leaves an uncommitted diff that trips the `/ggx-dispatcher` clean-tree pre-flight gate → it aborts and dispatches ZERO tickets (2026-06-11: CAF-649/622/233/636/632 all stranded this way); committing the rule to the default branch is also disallowed, so the tracked path is a dead end. Instead, idempotently append the rule to the local exclude file, then VERIFY:

   ```bash
   EXCL="$(git rev-parse --git-dir)/info/exclude"
   grep -qxF '.ggx-on-duty/' "$EXCL" 2>/dev/null || echo '.ggx-on-duty/' >> "$EXCL"
   git check-ignore .ggx-on-duty/state.json   # must print the path (exit 0) → ignored
   git status --porcelain                       # must show ONLY allowlisted residue the dispatcher auto-stashes (e.g. pubspec.lock)
   ```

   State must never be git-tracked (same intent as `claude-reports/`, but achieved per-clone so no target repo's tracked tree is dirtied).
3. Init `.ggx-on-duty/state.json` if absent (resume cursors if present). On resume, **load-and-merge over defaults** — read the file, then fill in any key absent from it from the default skeleton below (a state file written by an older on-duty version, or hand-truncated, must never crash a wake cycle: a missing `self_pushed` is an empty map, not undefined). The merge is also where the GGC-41 resume artifacts on `dispatch` are back-filled: a state file whose `dispatch` lacks `script_path` / `args_path` / `resume_attempts` loads with `script_path:null, args_path:null, resume_attempts:0` — so it never crashes the RECONCILE resume branch and simply takes the defensive reset+wait path (null script_path → fall back, see RECONCILE below). The `v` field carries the schema version; **a v1 state file (single `chain` object) is migrated by DROPPING `chain` and re-initialising fresh `analyze` + `dispatch` objects** (`next_due = now` each) — no work is lost because the `dispatcher-*-in-flight` labels re-drive any interrupted dispatch on the next sweep (GGC-70 decoupled the old `chain` into separate `analyze` / `dispatch` cadences). If a future bump changes another key's shape, the merge is the place to migrate. **On resume, also unconditionally reset `analyze.running`, `dispatch.running`, `health.running`, and `demo.running` to `false`** — a fresh session has no in-flight background agents by definition; a laptop shutdown kills agents WITH the session, so a `running=true` carried over from a dead session would otherwise make those legs skip forever (the completion notification that flips it back will never arrive).

   **On-invocation is the reboot / new-session path — it NEVER attempts resume (GGC-41 Q1 boundary).** The unconditional `dispatch.running=false` reset above is the post-reboot case: the previous session (and therefore the `Workflow` journal) is GONE — `resumeFromRunId` is same-session-only, so there is nothing to resume against. This path keeps today's behavior: reset + wait for the next dispatch sweep. **No work is lost** — the `dispatcher-*-in-flight` labels persist on Linear, so the next Leg-1b sweep re-discovers and re-dispatches those tickets (work is redone from each ticket's worktree filesystem markers, not dropped). Journal-based resume is RECONCILE-exclusive (the same-session, journal-reachable path — see "Each wake cycle" below); on-invocation does not even read `dispatch.script_path` / `dispatch.args_path`.

```json
{
  "v": 2,
  "last_wake_wallclock": "<now ISO>",
  "analyze":  { "running": false, "task_id": null, "next_due": "<now>" },
  "dispatch": { "running": false, "task_id": null, "script_path": null, "args_path": null, "resume_attempts": 0, "next_due": "<now>" },
  "health":   { "running": false, "task_id": null, "next_due": "<now>" },
  "demo":     { "running": false, "task_id": null },
  "notified": {},
  "self_pushed": {},
  "analyzer_verdicts": {}
}
```

   - `analyze` / `dispatch` / `health`: background-agent liveness + **time-based** due timestamps (dynamic mode has no fixed tick to take a modulus of). Every spawn records the harness task id into `task_id` so RECONCILE can verify liveness via TaskList. **`analyze` and `dispatch` are INDEPENDENT cadences (GGC-70)** — `analyze` (~2-3h) keeps the `ready-to-*` queue classified; `dispatch` (~1h) sweeps it. There is no shared `phase` field any more (the pre-GGC-70 single `chain` object used `phase` to sequence analyze→dispatch within one object; separate objects with their own `next_due` replace it). A legacy `chain` object (with or without `phase`, incl. the retired pre-GGC-55 `"chain"` phase value) is migrated away on invocation (see step 3) — its phase value is simply discarded.
     - **GGC-41 resume artifacts (on `dispatch`).** `dispatch.script_path` = the `scriptPath` the `Workflow` tool was launched with (needed to relaunch with `resumeFromRunId`); `dispatch.args_path` = the path to the dispatcher's `<RUN_TS>-<PID>.args.json` (the durable `{trunkSha, roster}` payload, discovered via the SAME newest-mtime glob Leg 2 / Finalize use); `dispatch.resume_attempts` = count of journal resumes already tried on the current dispatch (default `0`, **cap = 1** — a second death falls back to reset+wait so a poisoned roster row cannot crash-loop). All three are recorded at the dispatch launch (Leg 1b) and consumed by the RECONCILE resume branch. Legacy files lacking them load as `null/null/0` via the on-invocation merge and therefore take the defensive reset+wait path.
   - `demo` (`--demo` only): liveness of the post-dispatch serial demo-capture background session (GGC-29). No `next_due` — it is event-triggered (spawned on a dispatch completion that shipped design-bug PRs), not time-paced; `running` is the single-flight guard so two demo passes never drive the simulator at once. Absent on older state files → load as `{ running: false, task_id: null }`.
   - `notified`: `"<pr#>:<check>:<sha>" -> status` (CI dedup). `self_pushed`: `pr# -> sha` (suppress self-induced CI-rerun alerts). `analyzer_verdicts`: `ticketId -> verdict` (highlight changes only). No comment cursor needed — comment-actionability is judged statelessly inside /ggx-pr-resolver's two-stage gate (mechanical pre-filter + LLM judge over all open threads).

4. Start the loop: invoke `/loop` **with no interval** (dynamic mode) with the wake procedure below as the recurring prompt.

## Each wake cycle

**Gap detection first**: if `now - last_wake_wallclock` exceeds 2× the delay the previous wake scheduled (laptop slept), run in RECONCILE mode — re-list all PRs fresh, rebuild `notified` from current CI state, and frame the comment backlog as "while you were away". For any `running=true` flag, **verify the background agent actually still exists via TaskList using the stored `task_id` — TaskList is the SOLE liveness authority; never infer from report files** (the dispatcher's report is written only after the §5.2 `Workflow` run completes, so a live dispatch looks dead and a report-file probe would double-spawn); reset to false if it doesn't exist — but do NOT blind-reset, a sleep the session survived resumes its agents too and a blind reset would double-spawn. (The unconditional reset belongs to on-invocation only, where a fresh session provably has no agents.)

**RECONCILE dispatch resume (GGC-41).** The reset-to-false above is the OLD behavior for a dead dispatch: drop the in-flight flag and wait for the next dispatch sweep to re-discover the in-flight-labelled tickets and re-do every worker from scratch. RECONCILE is the SAME session that launched the dispatch, so its `Workflow` journal is still reachable — `resumeFromRunId` recovers every completed `agent()` call at zero cost. So before resetting a dead dispatch, attempt a journal resume. This applies **only** when ALL of:

- `dispatch.running === true` (a dispatch fan-out was in flight — a dead `analyze` is always just reset, since analyze has no `Workflow` journal to resume), AND
- TaskList shows `dispatch.task_id` is **gone** (the dispatch `Workflow` task died — same liveness probe as above; a LIVE task is left running untouched, never resumed), AND
- `dispatch.resume_attempts < 1` (cap = 1).

When all hold, resume instead of reset:

1. **Read the artifacts.** `dispatch.script_path` and the args from `dispatch.args_path` (the dispatcher's `<RUN_TS>-<PID>.args.json`). If `dispatch.script_path` is null/empty, OR `dispatch.args_path` is null/empty, OR the args file is missing/unreadable/not valid JSON → **fall back to today's reset + wait** (set `dispatch.running=false`; log one WARN line naming which artifact was missing). Defensive: never block the wake cycle on a missing artifact.
2. **Relaunch:** `Workflow({ scriptPath: dispatch.script_path, resumeFromRunId: dispatch.task_id, args: <parsed args.json> })`. Re-supplying the IDENTICAL `{ trunkSha, roster }` is what makes completed `agent()` calls hit the journal cache — a bare resume with no `args` would compute an empty roster (0 agents) and match nothing. The Workflow SCRIPT is unchanged; this is purely a relaunch with the original args + `resumeFromRunId`.
3. **On a successful relaunch:** set `dispatch.task_id` = the NEW run's task id, keep `dispatch.running=true`, and **increment `dispatch.resume_attempts`** (now 1). Completed workers return cached; only in-flight / unstarted roster rows re-run. The resumed run completes via the normal Leg-1b dispatch completion-notification close-out (which then clears the resume artifacts). `dispatch.args_path` / `dispatch.script_path` are unchanged (the resumed run uses the same roster + script).
4. **If the relaunch tool call itself errors** (journal not found, cross-session, tool unavailable) → **fall back to reset + wait** (set `dispatch.running=false`; log the error). No regression vs the old behavior — the in-flight labels still drive a fresh re-dispatch next sweep.
5. **If `dispatch.resume_attempts >= 1` already** (a prior resume of THIS dispatch also died): do NOT resume again — reset + wait. The cap stops a poisoned roster row from crash-looping the dispatch; the in-flight labels re-dispatch it cleanly next sweep.

**Idempotency on a resumed worker (GGC-41 AC8 — verification, not new code).** A resumed worker may re-touch a ticket `/ggx-work` had already partially processed, but it does not double-post or redo finished work: (a) the ff walkers (`infer_dev_stage` / `infer_port_stage` / `infer_ui_stage`) derive stage from the worktree's filesystem markers, so a re-run resumes mid-pipeline rather than redoing from scratch; (b) the idempotent comment markers — `<!-- ggx-work-error -->` (`/ggx-work` Step 4.3), `<!-- dispatch-fallback-error -->`, and GGC-40's `<!-- dispatch-triage-* -->` — are list-then-skip guarded, so a second pass posts nothing new; (c) the `dispatcher-*-in-flight` label is the resume signal and is never removed by the script. These hold by construction of the existing pipeline — the resume path adds no new dedup requirement.

Wake cycles are SERIAL: the recurring prompt is never re-entrant; completion notifications arriving mid-cycle are folded into the current cycle's summary, and state is written exactly once, at Finalize.

### Leg 1 — Classify (analyze) + Dispatch — DECOUPLED cadences (GGC-70; unless --no-dispatch)

**Decoupled-cadence invariant (GGC-70 — replaces the old "every dispatch is immediately preceded by an analyze").** `analyze` (Leg 1a) and `dispatch` (Leg 1b) run on their OWN `next_due` timestamps. `dispatch` no longer requires an immediately-preceding analyze: the dispatcher is purely **label-driven** (it sweeps existing `ready-to-*` labels), so running it without a fresh analyze is SAFE — newly-arrived unclassified tickets are simply invisible to it (not harmful) and enter the pool on the next `analyze` tick (latency ≤ analyze cadence). `analyze` is kept on a SLOW tick precisely because it re-posts append-only comments on stuck tickets; `dispatch` is the high-value, high-frequency leg. An `analyze` completion pulls the next `dispatch` forward (`dispatch.next_due = now`) so freshly classified tickets dispatch promptly — this preserves the old analyze→dispatch ordering as a special case (e.g. on cold start, where both due-times are `now`).

#### Leg 1a — analyze tick (when `now >= analyze.next_due`, ~every 2-3h; skipped under `--no-analyze` or `--no-dispatch`)

- `analyze.running` → an analyze pass is in flight; **skip starting a new one.** On its completion notification: set `analyze.running=false`, `analyze.next_due = now + 2.5h`, and **pull the next dispatch forward** by setting `dispatch.next_due = now` (so the tickets this pass just classified get swept on the next Leg-1b tick).
- Else **spawn analyze (headless):** a background headless `claude -p` session running ONLY `/ticket-analyze --non-interactive [--team:<KEY>]` — this keeps the analyzer's per-ticket sweep context OUT of the on-duty session. Set `analyze.running=true`, store `task_id`.
- analyze has no `Workflow` journal — a dead analyze is simply reset on RECONCILE (never resumed) and re-runs on its next tick.

#### Leg 1b — dispatch tick (when `now >= dispatch.next_due`, ~every 1h; unless `--no-dispatch`)

**Gate: skip this tick while `analyze.running`** — never launch a dispatch against a queue analyze is mid-write. (They touch disjoint sets by construction, so this is belt-and-suspenders; waiting one cycle is simpler and lets the analyze-completion pull-forward drive the ordering.) Otherwise:

- `dispatch.running` → a dispatch is in flight; **skip starting a new one.** On its completion notification: consume ONLY the structured `{counts, rows}` return (`TaskOutput` tail — never the per-ticket transcripts, so the session stays lean), fold it into the next summary, then set `dispatch.running=false`, `dispatch.next_due = now + 1h`, and clear the GGC-41 resume artifacts (`dispatch.script_path=null`, `dispatch.args_path=null`, `dispatch.resume_attempts=0`) — the dispatch finished, so there is nothing left to resume; a stale path here would only mislead a future RECONCILE. **Then, when `--demo` is set, trigger the demo pass** (see "Demo pass" below) over the design-bug rows just shipped — do NOT block the close-out on it.
- Else **launch dispatch (inline, launch-only):** invoke `/ggx-dispatcher --launch-only [--team:<KEY>]` **INLINE in this session**. Its Step 1-4 (pre-flight incl. the §1.7 P2 permissions gate, discovery, race-lock) run synchronously — bounded and fast — then §5.2 fires the `Workflow` tool, now **owned by the on-duty session**, so the per-ticket fan-out tree renders in your `/workflows`. `--launch-only` returns the workflow `runId` immediately and does NOT babysit (no §5.2 step-4 heartbeat / step-5 consume — on-duty owns those). Record the `runId` into `dispatch.task_id`, set `dispatch.running=true`. Inline-driving Step 1-4 here is allowed: the "never inline-drive blocking commands" guardrail targets the tens-of-minutes JOIN, not this bounded launch.
  - **Record the GGC-41 resume artifacts at this launch.** Set `dispatch.script_path = $HOME/.claude/workflows/dispatch-fanout.workflow.js` (the `scriptPath` `--launch-only` used), and `dispatch.args_path` = the dispatcher's `<RUN_TS>-<PID>.args.json` (emitted by `/ggx-dispatcher --launch-only` §5.2 step 2a, alongside the in-flight TSV) — discover it with the SAME newest-mtime glob Leg 2 / Finalize already use: `ls -t claude-reports/dispatcher/*.args.json 2>/dev/null | head -1`. If the glob finds no args.json (the dispatcher aborted before §5.2 step 2a, or an older dispatcher version that predates GGC-41), set `dispatch.args_path = null` — RECONCILE will then take the defensive reset+wait path. Also set `dispatch.resume_attempts = 0` on a FRESH dispatch (this is a new run, not a resume), so the cap counts only resumes of THIS dispatch.
- **Dispatch invocation decision (D22 — promoted to DEFAULT 2026-06-12; classic retired GGC-55).** The inline `/ggx-dispatcher --launch-only` dispatch owned by the on-duty session (live `/workflows`) is the **only** path. It became safe as the sole path because D21's two blockers had already cleared — the P2 silent-stall hard-aborts **before any ticket is locked** (`ggx-dispatcher.md` Step 1.7, GGC-20), and the verify-agent level-2 deadlock is solved by the R4 headless auditor (GGC-19 / PR #81). The old single headless `claude -p` chain (`--classic`, `chain.phase="chain"`) was deleted in GGC-55; reversion, if ever needed, is `git revert` from tag `pre-classic-removal`, not a live fallback flag. If headless dispatch is ever required again, run `/ggx-dispatcher` inside a headless `claude -p` child — the `Workflow` tool is available there (probe-confirmed, GGC-55).
- **Why decoupling is safe**: the dispatcher only consumes `ready-to-*` labels, which analyze writes AFTER its analysis comment, so the dispatcher never consumes a half-written verdict; analyzer and dispatcher touch disjoint ticket sets by construction (analyzer's Step 1.5.5 skips `ready-to-*` / `dispatcher-*-in-flight`; Step 8.2 pre-write re-check guards the reverse direction). A dispatch firing between analyze ticks just sees the queue as analyze last left it.
- **Known edge — never hand-label verdict labels while analyze is running**: the analyzer's label write computes `current − analyzer-owned-labels + verdict` and will overwrite a `ready-to-dev` you add mid-run. The summary must state when analyze is in flight.
- **Cadence rationale**: `analyze` ~2-3h because analysis comments are append-only (a fresh comment each run) and `need-revision`/`need-dependency` tickets are re-analyzed every run — a tighter analyze cadence spams stuck tickets. `dispatch` ~1h because it is comment-free (label-driven) and is the high-value leg. Tune each `next_due` increment independently if the latency vs noise balance shifts.

### Leg 2 — PR health poll (when `now >= health.next_due`, ~every 4h)

> **Cadence (GGC-70):** ~4h, deliberately slow. The resolver does actionable work only ~1-2×/day, so a tight poll just burns headless resolver-batch spawns (each runs tests) on an empty queue. A few-hour latency on PR-comment resolution is fine; ~4h catches the real passes the same working day. (The cheap in-session CI check in step 1 still runs every Leg-2 tick, so red-CI alerts are not delayed beyond this cadence either — if faster CI alerting is ever needed, that is the lever to split, not the resolver.)

ONE pass over `gh pr list --author @me --state open` covering CI and resolution. No separate event-triggered path — `/ggx-pr-resolver`'s two-stage gate (mechanical pre-filter, then LLM judge over ALL open threads deciding ACT/HANDLED/HOLD by substance) makes polling every PR affordable, so the poll IS the event detection.

**Degraded-read guard**: if the `gh pr list` call fails, or returns empty when the previous poll was non-empty, treat this poll as DEGRADED — retry next wake, and **skip ALL evictions/seeding this cycle** (otherwise an API blip looks like "every PR merged" and purges `notified`/`self_pushed`, causing a re-notify storm). A gh secondary-rate-limit response (HTTP 403 + `Retry-After`) is treated the same as DEGRADED (skip evictions, stretch `next_due`) — never per-PR silent-skip, which is indistinguishable from all-clean.

**In-session vs spawned (so the wake cycle stays ~1-2 min)**: step 1 (CI check) runs IN-SESSION — it is pure `gh` reads + dedup bookkeeping, no blocking work. Step 2 (resolve) is SPAWNED as a background **headless CLI session** (the batch fans out per-PR subagents — same nested-spawn constraint as Leg 1); it does blocking work (the resolver runs tests), so it is never inline-driven; the session only records its `task_id` and folds its completion into a later cycle's summary.

1. **CI check (in-session, pure reads)**: key `<pr#>:<check>:<headSha>`; notify only on transition (absent|green → red), dedup via `notified`. `headSha == self_pushed[pr#]` → do NOT swallow: still report a red, but tagged `(self-pushed — rerun from our own push)`, and clear `self_pushed[pr#]` once that SHA's CI reaches a terminal state (success/failure). Never suppress by "one cycle" — cycle length is dynamic and CI duration is not. Evict keys for MERGED/CLOSED/superseded SHAs (unless DEGRADED).
2. **Resolve (background)**: skip if `health.running`; else spawn ONE background headless CLI session (`claude -p --permission-mode bypassPermissions`, same mechanism as Leg 1) running `/ggx-pr-resolver --batch --user=@me --auto`, passing a **skip-set of branch names** (headRefName is the canonical key — PR numbers cannot work: dispatch-in-flight tickets may have no PR yet, `/dev:ship` opens it last): (a) branches the Leg-1b dispatch currently has in flight — while `dispatch.running`, read the dispatcher's early in-flight file `claude-reports/dispatcher/<RUN_TS>-<PID>.inflight.tsv` (written right after its §4.1 ticket race-locks, before any §5 agent spawns — see `ggx-dispatcher.md` §4.4; discover via newest-mtime glob — on-duty cannot know the background subagent's RUN_TS/PID and must never read the dispatcher's `.lock` file); do NOT rely on the resolver's own in-flight-label read — `/dev:ship` can remove the label mid-sweep and the resolver would walk into a worktree `/dev:ff` is still writing to —, and (b) nothing else — the resolver's two-stage gate and ownership guard handle the rest. On completion: flip flag, **set `health.next_due = now + 4h` HERE (on completion, not at spawn** — setting it at spawn would let a long batch re-fire immediately and compress the interval), record pushed SHAs into `self_pushed`, fold `needs-human` PRs into the notification.

### Demo pass (`--demo` only — event-triggered by a Leg-1 dispatch completion)

Spawned (not time-paced) when a Leg-1 **workflow** dispatch completes with ≥1
shipped design-bug PR. Mirrors GGC-29's "serial pass" design: on-duty owns the
workflow completion (D22 `--launch-only`), so on-duty — not the dispatcher —
triggers the demo capture.

- **Trigger** (in the Leg-1 dispatch close-out, when `--demo`): fire when the
  dispatch shipped ≥1 design-bug PR. No `{rows}` filtering is needed — the demo
  skill self-discovers (below); an empty queue is its own no-op.
- **Single-flight**: skip if `demo.running` (a prior batch's demo pass is still
  capturing — only one actor may drive the simulator at a time; this guard is
  what makes the serial-by-construction guarantee hold across wake cycles).
- **Spawn** ONE background headless CLI session (`claude -p --permission-mode
  bypassPermissions`, same mechanism as the legs — keeps the wake cycle ~1-2 min;
  a blocking inline pass would stall it) running
  `/ggx-demo --batch` (GGC-66 — absorbed `/_ui-demo-batch`; self-discovers every
  open design-bug PR of mine still lacking a demo, no JSON input), cwd = this main
  worktree. Set `demo.running=true`, store its `task_id`. The local headless child
  shares this laptop, so it reaches the simulator (a cloud run could not —
  documented no-op there).
- **On its completion notification**: set `demo.running=false` and fold its
  one-line summary (`<C> captured, <S> skipped`) into the next cycle's summary.
- **Fail-soft**: `/ggx-demo --batch` always exits 0; a demo failure never blocks
  ship, never fails a wake cycle, and never touches ticket/PR state beyond an
  idempotent PR comment. A leg failure still never ends the loop (guardrail).

### Finalize (every wake)

1. ONE batched notification (red CI, resolver reports, needs-human PRs, analyzer verdict CHANGES). Channel `/_slack-notify`; ALSO append to `.ggx-on-duty/digest.md` (durable fallback — Slack-unconfigured is a silent no-op).
2. Persist state (`last_wake_wallclock`, cursors — comment cursors only post-notify). Write deterministically: render the FULL JSON to a temp file and `mv` over `state.json` (atomic; never hand-reprint partial JSON — silently dropped keys corrupt dedup).
3. One-line summary: `wake 14:32 | dispatch: in-flight (CAF-583, CAF-643) | PRs: 5 green, #492 resolver spawned | sweep: due 15:10`. When `dispatch.running`, **surface the in-flight ticket IDs** in the `dispatch:` segment by reading column 1 (`<ticket-id>`) of the dispatcher's early in-flight file `claude-reports/dispatcher/<RUN_TS>-<PID>.inflight.tsv` — the SAME newest-mtime glob Leg 2 already uses for its skip-set (§ "Leg 2 — Resolve"; on-duty cannot know the background subagent's `RUN_TS`/`PID` and must never read the `.lock`). This is the cheapest ticket-level visibility. Under D22 the dispatch `Workflow` task is owned by the on-duty session (so its fan-out tree renders in your `/workflows`), but the in-flight TSV remains the lean way to surface IDs in the one-line summary without tailing the workflow output; keep it to IDs only — per-ticket detail stays in the workflow's own context + its report file (lean-session guardrail). If the glob finds no current file (dispatch just launched, not yet past its §4.1 race-lock), print `dispatch: in-flight (roster pending)` rather than guessing. When `analyze.running`, append an `analyze: in-flight` segment (it has no per-ticket TSV — analyze writes labels, not a roster). When `demo.running` (or a demo pass completed this cycle), append a `demo:` segment (e.g. `demo: capturing (2 PRs)` / `demo: 2 captured, 1 skipped`).
4. `--until` reached → **DRAIN, don't hard-stop** (owner decision D15): spawn nothing new from this point; if background agents are still in flight, keep waking only to collect their completions (fallback heartbeat 1800s), then write the final summary and do NOT reschedule. A clean join preserves the completion notifications and the `self_pushed` writes they carry (labels would self-recover via Q2/Q4 anyway, but drained state needs no recovery). Otherwise **choose the next wakeup dynamically**:

## Dynamic pacing (how to pick the next wakeup)

- Background agents in flight → their completion already re-wakes the session; schedule only a long fallback heartbeat (**1800s**) in case one hangs.
- Otherwise sleep until the earliest of `analyze.next_due` / `dispatch.next_due` / `health.next_due` (**~3600s** cap when the board is quiet: no open PRs, all green, nothing due soon).
- Exception — an imminent external state change we cannot be notified about (e.g. CI running on a SHA we just pushed and want to confirm green): a short in-cache poll (**240–270s**) is allowed until it settles.
- Never pick ~300s (worst case for prompt-cache economics: pays the cache miss without amortizing it). State the reason in each reschedule.

## Stopping / resuming

Interrupt the session or say "stop the loop" anytime. State persists; the next `/ggx-on-duty` resumes cursors instead of re-notifying. Restarting the session mid-day is cheap and is the recommended fix if the session context has grown heavy.
