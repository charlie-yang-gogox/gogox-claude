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

**Usage**: `/ggx-on-duty [--team:<KEY>] [--no-dispatch] [--no-analyze] [--classic] [--until:HH:MM]`

- `--team:<KEY>` — passed through to /ticket-analyze and /ggx-dispatcher (required when the repo's `branch_prefix` is `auto`).
- `--no-dispatch` — disable Leg 1 entirely (watch-only mode).
- `--no-analyze` — Leg 1 runs the dispatcher without the preceding analyze step.
- `--classic` — **fallback (D21).** Force Leg 1's old single headless `claude -p` chain (analyze + dispatch chained in one detached child) instead of the **default D22 path**. Use only if the default workflow path misbehaves; the per-ticket fan-out is then NOT visible from this session. (`--workflow` is still accepted as a redundant no-op — the D22 path is the default now.) See Leg 1 → "Leg 1 invocation decision (D22)".
- `--until:HH:MM` — optional auto-stop. Default: none (run until the user interrupts or closes the session).

## Non-negotiable guardrails

- The on-duty loop introduces **no Linear label writes and no new pipeline entry points of its own** — it only invokes existing commands (`/ticket-analyze`, `/ggx-dispatcher`, `/ggx-pr-resolver`), which own all label / worktree / branch mutation. The label ownership table in `ggx-dispatcher.md` stays authoritative.
- **Never inline-drive blocking commands — and never wrap a fan-out command in an Agent-tool subagent.** `/ggx-dispatcher` waits in its §6.1 join barrier for all spawned pipeline agents (tens of minutes); `/ggx-pr-resolver` runs tests. Both also FAN OUT their own subagents, and nested `Agent`/`Task` spawns from inside a subagent FAIL (`ggx-dispatcher.md` §5.3; confirmed live 2026-06-05 — a chain wrapped in an Agent subagent batch-aborted with "spawn tool unavailable"; locks rolled back cleanly). So both legs are spawned as background **headless CLI sessions** via Bash `run_in_background`: `claude -p --permission-mode bypassPermissions "<leg prompt>"`, cwd = this main worktree — a headless session is TOP-LEVEL, so the fan-out works natively, and the Linear MCP connector (account-auth) is available headlessly (probe-verified). A wake cycle always completes in ~1-2 minutes. **D22 (default since 2026-06-12):** Leg 1's dispatch becomes a top-level `Workflow` task owned by the on-duty session — script-spawned agents are level-1 (no nested-spawn problem) and on-duty is already top-level, so no headless child is needed for it; the only inline work is the dispatcher's bounded Step 1-4 launch (not the join — see Leg 1), and worker contexts never flow back (only the `{counts, rows}` return). The Phase-A analyze step still runs in a headless child; the `--classic` fallback uses the old single headless chain for both analyze and dispatch.
- **No lockfile probing.** Liveness of spawned background agents is tracked exclusively via the harness's background-completion notifications, recorded as booleans in the state file. Never read or remove `/ggx-dispatcher`'s internal lock — its 600s staleness TTL is far shorter than a real run and external reads of it misjudge.
- **A leg failure never ends the loop.** Each leg runs in its own try/continue boundary; an error becomes one WARN line in the cycle summary. The loop ends only on user interrupt or `--until`.
- **Keep the session lean** (the loop lives 8+ hours in one context): each cycle contributes a one-line summary; full dispatcher / pr-resolver output stays in the spawned agents' own contexts and report files — cite paths, never paste tables back into the on-duty session.

## On invocation (once)

1. Run the /ggx-dispatcher pre-flight SUBSET: main worktree, default branch, clean tree, worktree prune, gh auth — its lockfile step is explicitly EXCLUDED (on-duty never touches the dispatcher lock). The dispatcher has NO Linear probe, so add on-duty's own: one Linear MCP call (e.g. `list_teams`) must succeed, and fail fast on a missing/mismatched `--team` for the repo's prefix. Abort start if any fail.
2. Ensure `.ggx-on-duty/` exists and is gitignored (state must never be git-tracked; same in-repo-but-untracked pattern as `claude-reports/`).
3. Init `.ggx-on-duty/state.json` if absent (resume cursors if present). On resume, **load-and-merge over defaults** — read the file, then fill in any key absent from it from the default skeleton below (a state file written by an older on-duty version, or hand-truncated, must never crash a wake cycle: a missing `self_pushed` is an empty map, not undefined). The `v` field carries the schema version; if a future bump changes a key's shape, the merge is the place to migrate. **On resume, also unconditionally reset `chain.running` and `health.running` to `false`** — a fresh session has no in-flight background agents by definition; a laptop shutdown kills agents WITH the session, so a `running=true` carried over from a dead session would otherwise make both legs skip forever (the completion notification that flips it back will never arrive).

```json
{
  "v": 1,
  "last_wake_wallclock": "<now ISO>",
  "chain":  { "running": false, "phase": null, "task_id": null, "next_due": "<now>" },
  "health": { "running": false, "task_id": null, "next_due": "<now>" },
  "notified": {},
  "self_pushed": {},
  "analyzer_verdicts": {}
}
```

   - `chain` / `health`: background-agent liveness + **time-based** due timestamps (dynamic mode has no fixed tick to take a modulus of). Every spawn records the harness task id into `task_id` so RECONCILE can verify liveness via TaskList. `chain.phase` (default D22 path `"analyze"`→`"dispatch"` | `--classic` path `"chain"` | `null` when idle) drives the completion-notification branch in Leg 1; it is absent on `health` (health has no sub-phases). Older state files without `phase` load as `null` (the on-invocation merge fills it) — treat a `running=true` chain with `phase=null` as `"chain"` (the pre-D22 headless shape) for back-compat.
   - `notified`: `"<pr#>:<check>:<sha>" -> status` (CI dedup). `self_pushed`: `pr# -> sha` (suppress self-induced CI-rerun alerts). `analyzer_verdicts`: `ticketId -> verdict` (highlight changes only). No comment cursor needed — comment-actionability is judged statelessly inside /ggx-pr-resolver's two-stage gate (mechanical pre-filter + LLM judge over all open threads).

4. Start the loop: invoke `/loop` **with no interval** (dynamic mode) with the wake procedure below as the recurring prompt.

## Each wake cycle

**Gap detection first**: if `now - last_wake_wallclock` exceeds 2× the delay the previous wake scheduled (laptop slept), run in RECONCILE mode — re-list all PRs fresh, rebuild `notified` from current CI state, and frame the comment backlog as "while you were away". For any `running=true` flag, **verify the background agent actually still exists via TaskList using the stored `task_id` — TaskList is the SOLE liveness authority; never infer from report files** (the dispatcher's report is written only after its join barrier, so a live chain looks dead and a report-file probe would double-spawn); reset to false if it doesn't exist — but do NOT blind-reset, a sleep the session survived resumes its agents too and a blind reset would double-spawn. (The unconditional reset belongs to on-invocation only, where a fresh session provably has no agents.)

Wake cycles are SERIAL: the recurring prompt is never re-entrant; completion notifications arriving mid-cycle are folded into the current cycle's summary, and state is written exactly once, at Finalize.

### Leg 1 — Classify → Dispatch chain (when `now >= chain.next_due`, ~every 2h; unless --no-dispatch)

**Invariant: the dispatcher NEVER runs on an unclassified queue — every dispatch cycle is immediately preceded by an analyze run. There is no standalone dispatch.**

- `chain.running` → the chain is in flight; **skip starting a new one.** On the completion notification, branch on `chain.phase`:
  - `"chain"` (classic) or `"dispatch"` (`--workflow`) → the whole chain is done: fold the result into the next summary (classic: the `claude-reports/dispatcher/<RUN_TS>-<PID>.md` path; `--workflow`: the `{counts, rows}` return value), then set `chain.next_due = now + 2h`, `chain.running=false`, `chain.phase=null`.
  - `"analyze"` (`--workflow` only) → analyze finished; **advance to Phase B (inline launch-only dispatch) THIS cycle** (see below); do NOT bump `next_due` yet.
- Else **start the chain.** Default is the D22 workflow path; `--classic` forces the old single headless chain:

  **`--classic` (D21 fallback): ONE background headless CLI session.** `Bash run_in_background`, cwd = this main worktree; NOT an Agent-tool subagent — the dispatcher's §5.3 fan-out needs a top-level session (see guardrails): `claude -p --permission-mode bypassPermissions "<chain prompt>"`, whose prompt runs, in order:
  1. `/ticket-analyze --non-interactive [--team:<KEY>]` — REAL WRITE mode, this IS the ticket classifier (user's explicit decision: writes verdict labels + analysis comments like the disabled cloud analyzer; the analyze→dispatch human review window is deliberately waived). `--non-interactive` is mandatory (default mode raises AskUserQuestion gates that would stall an unattended agent). Skipped under `--no-analyze`.
  2. `/ggx-dispatcher [--team:<KEY>]` — consumes the freshly written `ready-to-*` labels (LLM-judged and human-labeled alike) in the same cycle.

  Set `chain.running=true`, `chain.phase="chain"`, store the headless `task_id`.

  **Default (D22): two phases; the dispatch fan-out is owned by THIS session for live `/workflows` visibility.**
  - **Phase A — analyze (headless):** spawn the SAME background headless `claude -p` session, but running ONLY `/ticket-analyze --non-interactive [--team:<KEY>]` — this keeps the analyzer's per-ticket sweep context OUT of the on-duty session. Set `chain.running=true`, `chain.phase="analyze"`, store `task_id`. (Under `--no-analyze`, skip Phase A and go straight to Phase B this cycle.)
  - **Phase B — dispatch (inline, launch-only):** on Phase-A completion, invoke `/ggx-dispatcher --workflow --launch-only [--team:<KEY>]` **INLINE in this session**. Its Step 1-4 (pre-flight incl. the §1.7 P2 permissions gate, discovery, race-lock) run synchronously — bounded and fast — then §5.2 fires the `Workflow` tool, now **owned by the on-duty session**, so the per-ticket fan-out tree renders in your `/workflows`. `--launch-only` returns the workflow `runId` immediately and does NOT babysit (no §5.2 step-4 heartbeat / step-5 consume — on-duty owns those). Record the `runId` into `chain.task_id`, set `chain.phase="dispatch"` (keep `chain.running=true`). Inline-driving Step 1-4 here is allowed: the "never inline-drive blocking commands" guardrail targets the tens-of-minutes JOIN, not this bounded launch.
  - On the dispatch workflow's completion notification: consume ONLY the structured `{counts, rows}` return (`TaskOutput` tail — never the per-ticket transcripts, so the session stays lean), then close out the chain as in the `chain.running` branch above.
- **Leg 1 invocation decision (D22 — promoted to DEFAULT 2026-06-12, supersedes D21).** The two-phase workflow path (headless analyze → inline `/ggx-dispatcher --workflow --launch-only` dispatch owned by the on-duty session, live `/workflows`) is now the **default**. Promotion followed several live validation rounds (2026-06-12) that confirmed both gating criteria: (a) on-duty context stays lean with the workflow tree owned in-session; (b) the inline Phase-B Step 1-4 launch does not stall the wake cadence. It became safe to default because D21's two blockers had already cleared — the P2 silent-stall hard-aborts **before any ticket is locked** (`ggx-dispatcher.md` Step 1.7, GGC-20), and the verify-agent level-2 deadlock is solved by the R4 headless auditor on **both** paths (GGC-19 / PR #81). **`--classic` remains as a per-run fallback** (the old single headless chain, `chain.phase="chain"`) for zero-downside reversion if a future regression appears — D21's "classic, never --workflow" reasoning survives only as the `--classic` rationale.
- **Why chained is safe**: ticket-analyze posts the analysis comment BEFORE the label write, so the dispatcher never consumes a half-written verdict; analyzer and dispatcher touch disjoint ticket sets by construction (analyzer's Step 1.5.5 skips `ready-to-*` / `dispatcher-*-in-flight`; Step 8.2 pre-write re-check guards the reverse direction).
- **Known edge — never hand-label verdict labels while the chain is running**: the analyzer's label write computes `current − analyzer-owned-labels + verdict` and will overwrite a `ready-to-dev` you add mid-run. The summary must state when the chain is in flight.
- **~2h cadence rationale**: analysis comments are append-only (a fresh comment each run) and `need-revision`/`need-dependency` tickets are re-analyzed every run — tighter cadence spams stuck tickets. Tune `next_due` increment if latency vs noise balance shifts.

### Leg 2 — PR health poll (when `now >= health.next_due`, ~every 1h)

ONE pass over `gh pr list --author @me --state open` covering CI and resolution. No separate event-triggered path — `/ggx-pr-resolver`'s two-stage gate (mechanical pre-filter, then LLM judge over ALL open threads deciding ACT/HANDLED/HOLD by substance) makes polling every PR affordable, so the poll IS the event detection.

**Degraded-read guard**: if the `gh pr list` call fails, or returns empty when the previous poll was non-empty, treat this poll as DEGRADED — retry next wake, and **skip ALL evictions/seeding this cycle** (otherwise an API blip looks like "every PR merged" and purges `notified`/`self_pushed`, causing a re-notify storm). A gh secondary-rate-limit response (HTTP 403 + `Retry-After`) is treated the same as DEGRADED (skip evictions, stretch `next_due`) — never per-PR silent-skip, which is indistinguishable from all-clean.

**In-session vs spawned (so the wake cycle stays ~1-2 min)**: step 1 (CI check) runs IN-SESSION — it is pure `gh` reads + dedup bookkeeping, no blocking work. Step 2 (resolve) is SPAWNED as a background **headless CLI session** (the batch fans out per-PR subagents — same nested-spawn constraint as Leg 1); it does blocking work (the resolver runs tests), so it is never inline-driven; the session only records its `task_id` and folds its completion into a later cycle's summary.

1. **CI check (in-session, pure reads)**: key `<pr#>:<check>:<headSha>`; notify only on transition (absent|green → red), dedup via `notified`. `headSha == self_pushed[pr#]` → do NOT swallow: still report a red, but tagged `(self-pushed — rerun from our own push)`, and clear `self_pushed[pr#]` once that SHA's CI reaches a terminal state (success/failure). Never suppress by "one cycle" — cycle length is dynamic and CI duration is not. Evict keys for MERGED/CLOSED/superseded SHAs (unless DEGRADED).
2. **Resolve (background)**: skip if `health.running`; else spawn ONE background headless CLI session (`claude -p --permission-mode bypassPermissions`, same mechanism as Leg 1) running `/ggx-pr-resolver --batch --user=@me --auto`, passing a **skip-set of branch names** (headRefName is the canonical key — PR numbers cannot work: chain-in-flight tickets may have no PR yet, `/dev:ship` opens it last): (a) branches the Leg-1 chain currently has in flight — while `chain.running`, read the dispatcher's early in-flight file `claude-reports/dispatcher/<RUN_TS>-<PID>.inflight.tsv` (written right after its §4.1 ticket race-locks, before any §5 agent spawns — see `ggx-dispatcher.md` §4.4; discover via newest-mtime glob — on-duty cannot know the background subagent's RUN_TS/PID and must never read the dispatcher's `.lock` file); do NOT rely on the resolver's own in-flight-label read — `/dev:ship` can remove the label mid-sweep and the resolver would walk into a worktree `/dev:ff` is still writing to —, and (b) nothing else — the resolver's two-stage gate and ownership guard handle the rest. On completion: flip flag, **set `health.next_due = now + 1h` HERE (on completion, not at spawn** — a 70-min batch would otherwise re-fire immediately and compress the interval), record pushed SHAs into `self_pushed`, fold `needs-human` PRs into the notification.

### Finalize (every wake)

1. ONE batched notification (red CI, resolver reports, needs-human PRs, analyzer verdict CHANGES). Channel `/_slack-notify`; ALSO append to `.ggx-on-duty/digest.md` (durable fallback — Slack-unconfigured is a silent no-op).
2. Persist state (`last_wake_wallclock`, cursors — comment cursors only post-notify). Write deterministically: render the FULL JSON to a temp file and `mv` over `state.json` (atomic; never hand-reprint partial JSON — silently dropped keys corrupt dedup).
3. One-line summary: `wake 14:32 | chain: in-flight (CAF-583, CAF-643) | PRs: 5 green, #492 resolver spawned | sweep: due 15:10`. When `chain.running`, **surface the in-flight ticket IDs** in the `chain:` segment by reading column 1 (`<ticket-id>`) of the dispatcher's early in-flight file `claude-reports/dispatcher/<RUN_TS>-<PID>.inflight.tsv` — the SAME newest-mtime glob Leg 2 already uses for its skip-set (§ "Leg 2 — Resolve"; on-duty cannot know the background subagent's `RUN_TS`/`PID` and must never read the `.lock`). This is the only ticket-level visibility that crosses the headless-process boundary — the spawned chain's own fan-out progress tree lives in its detached `claude -p` session and is NOT visible from the on-duty session. Keep it to IDs only; per-ticket detail stays in the chain's context + its report file (lean-session guardrail). If the glob finds no current file (chain just spawned, not yet past its §4.1 race-lock), print `chain: in-flight (roster pending)` rather than guessing. _(Full per-ticket live visibility is GGC-28's job — it needs the Leg-1 fan-out moved top-level under `--workflow`, blocked by GGC-20.)_
4. `--until` reached → **DRAIN, don't hard-stop** (owner decision D15): spawn nothing new from this point; if background agents are still in flight, keep waking only to collect their completions (fallback heartbeat 1800s), then write the final summary and do NOT reschedule. A clean join preserves the completion notifications and the `self_pushed` writes they carry (labels would self-recover via Q2/Q4 anyway, but drained state needs no recovery). Otherwise **choose the next wakeup dynamically**:

## Dynamic pacing (how to pick the next wakeup)

- Background agents in flight → their completion already re-wakes the session; schedule only a long fallback heartbeat (**1800s**) in case one hangs.
- Otherwise sleep until the earliest of `chain.next_due` / `health.next_due` (**~3600s** cap when the board is quiet: no open PRs, all green, nothing due soon).
- Exception — an imminent external state change we cannot be notified about (e.g. CI running on a SHA we just pushed and want to confirm green): a short in-cache poll (**240–270s**) is allowed until it settles.
- Never pick ~300s (worst case for prompt-cache economics: pays the cache miss without amortizing it). State the reason in each reschedule.

## Stopping / resuming

Interrupt the session or say "stop the loop" anytime. State persists; the next `/ggx-on-duty` resumes cursors instead of re-notifying. Restarting the session mid-day is cheap and is the recommended fix if the session context has grown heavy.
