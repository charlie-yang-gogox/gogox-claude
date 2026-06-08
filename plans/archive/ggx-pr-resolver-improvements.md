# Improvement Plan — `/ggx-pr-resolver` batch flow

> Source: retro on a `/loop /ggx-pr-resolver --batch --auto` run (2026-06-08) that
> fanned out 13 PRs after trunk moved. Implement in the **gogox-claude** repo
> (skill definitions). Most fixes land in `ggx-pr-resolver.md`'s per-PR procedure,
> which is the authoritative definition of per-PR worker behavior.
>
> **Status (2026-06-08, branch `feat/ggx-pr-resolver-improvements`)**: P0-1, P0-2,
> P1-1, P1-2, P1-3 ✅ IMPLEMENTED — in `commands/dev/ggx-pr-resolver.md` (step 5
> foreground+incremental, step 7 push ownership, Batch-mode pre-flight /
> pre-spawn filter / concurrency cap) and `commands/dev/resolve-conflict.md`
> Callee Step 3 (foreground test mandate at the executor). **P2-1 ✅ DECIDED:
> option B (keep current behavior — rebase all PRs incl. drafts)** — Charlie's
> drafts are nearly-ready, just unverified, so they should stay mergeable. No
> code change (status quo); the whole plan is now resolved.

## Goal
Eliminate the token blow-up and orphaned processes caused by workers backgrounding
their tests and "parking", cut machine contention during fan-out, and filter out
PRs that can't be acted on **before** spawning a worker.

---

## P0 — Must fix (most painful this run)

### P0-1 Worker tests run in the FOREGROUND; ban backgrounding/Monitor
- **Symptom:** 4 of 13 workers (#404 #488 #492 #499) backgrounded `flutter test`, then
  returned "waiting for the monitor" without finishing. Resuming them just re-parked
  (#404, #488, #492, #499 each parked 2–3 times). Cost signature: clean rebases that
  should be ~40k tokens / <50 tool-uses ballooned to **120–140k tokens, 370–416
  tool-uses** (tight-loop polling of test status). Also left **22 orphan
  dart/flutter_tester processes** running after the agents "completed" — real CPU drain.
- **Fix:** In the rebase-stage test spec, state explicitly: *"Tests MUST run in the
  foreground and block until they exit. Do NOT use run_in_background, do NOT arm a
  Monitor, do NOT return control to wait for a notification. A subagent is a linear
  procedure; backgrounding belongs only to the top-level orchestrator."*
- **Where:** `ggx-pr-resolver.md` step 5; also audit `resolve-conflict.md --callee` if it
  is the one running tests.
- **Acceptance:** a clean-rebase worker uses < ~50 tool-uses and emits no "waiting for
  monitor" message; no orphan flutter_tester processes survive the worker.

### P0-2 Pre-spawn filter: dirty worktree + already-merged
- **Symptom:** 4 workers were spawned that could never do work — #480/#500/#502 had a
  dirty worktree (correctly caught by the in-worker guard, but only AFTER spawning), and
  #501 had been merged mid-run (only discovered after the worker rebased + tested).
- **Fix:** in the top-level batch sweep (next to stage 3a), add cheap checks:
  - for each branch with a worktree, run `git -C <wt> status --porcelain`; if any tracked
    change is outside the residue allowlist → mark `needs-human: worktree-dirty`, do NOT spawn.
  - re-check `gh pr view <n> --json state` right before spawning; `MERGED`/`CLOSED` → skip.
- **Where:** `ggx-pr-resolver.md` Batch mode section.
- **Acceptance:** dirty/merged PRs appear in the report with **zero** agents spawned.

---

## P1 — Should fix

### P1-1 Make the worker's push ownership explicit
- **Symptom:** #446's worker ran `resolve-conflict --callee`, got the "ready to push,
  HEAD <sha>" result, and returned — never executing its own step-4 push. Needed a manual
  nudge.
- **Fix:** step 7 must state: *"The callee returning is NOT completion. When there is no
  comments stage, the per-PR worker owns the `--force-with-lease` push; the callee never
  pushes."* List push as its own explicit numbered step in the worker template.
- **Where:** `ggx-pr-resolver.md` step 7.

### P1-2 Cap test concurrency and/or use incremental tests
- **Symptom:** 13 full `flutter test` suites in parallel thrashed the CPU; one worker
  reported a normally-few-minute suite taking **~33 minutes** due to "~7 sibling
  worktrees running tests in parallel".
- **Fix (use both):**
  - add a **concurrency cap** (suggest 4–5 simultaneous workers); queue the rest.
  - for a rebase (only trunk moved, branch's own diff is small) call `check-test`'s
    **flutter file-level incremental** path instead of a bare full `flutter test`.
    Semantic-conflict risk is still covered by the post-rebase incremental run + CI.
- **Where:** `ggx-pr-resolver.md` (concurrency policy) + step 5 (call `check-test`, not
  raw `flutter test`).
- **Acceptance:** wall-clock per batch drops materially; no worker reports 30-min-class
  CPU contention.

### P1-3 Resolve the flutter binary once; fail-fast on version mismatch
- **Symptom:** #482 and #404 found fvm unavailable and fell back to system flutter
  **3.41.6**, while others used fvm-pinned **3.38.7** — inconsistent toolchain across
  workers, which compounds flaky-test risk.
- **Fix:** resolve the fvm-aware binary once up front (reuse the ui-tweak
  `.dev/.../flutter-bin` pattern) and pass it to every worker; if the pinned version
  can't be resolved, **fail-fast with a report**, don't let each worker fall back
  independently.
- **Where:** `ggx-pr-resolver.md` pre-flight + worker template.

---

## P2 — Policy decision (owner must decide)

### P2-1 Should rebase exclude draft PRs?
- **Observation:** 8 of the 13 PRs were drafts. Every trunk move re-rebases + tests +
  pushes + triggers CI for all of them, yet a draft will go stale again before it's
  ready — that work is largely wasted.
- **Options:**
  - **(A)** add a gate: only rebase non-draft (or approved) PRs — large cost reduction.
  - **(B)** keep current behavior (rebase everything behind) — drafts always stay
    mergeable.
  - **(C)** compromise: drafts get behind-detection + reporting only, no auto-rebase,
    unless they have an ACT thread.
- **Decision (2026-06-08): option (B)** — keep current behavior, rebase everything
  behind incl. drafts. Charlie: "draft 只是我還沒驗證，但幾乎是 ready" — the drafts are
  effectively ready, so they must stay mergeable. No code change.

---

## Suggested sequencing
1. P0-1 (prompt-spec change, highest ROI)
2. P0-2 (saves wasted spawns)
3. P1-1 (one sentence)
4. P1-3 (toolchain consistency)
5. P1-2 (larger — changes the test strategy)
6. P2-1 (after owner decides)

## What worked (do not regress)
- The in-worker **dirty guard** protected uncommitted in-progress edits in 3 worktrees.
- `--force-with-lease` correctly rejected the #501 merge-race and the worker safely
  reset — no bad force-push.
- needs-human classification was accurate; no data loss.

---

## V2 — retro of the v1 run (2026-06-08, same branch)

> Source: `claude-reports/ggx-pr-resolver/` in `gogox-client-flutter` — three `/loop`
> iterations that ran v1 live (via the symlinked skill). v1 worked ~80%; these four
> residual leaks were the v1 fixes that relied on prose where a mechanism was needed.
> **Unifying principle: move authority from the N fan-out workers to the single
> orchestrator — "the commander decides, the worker obeys."** (#404 missed-push and
> the fvm re-discovery are the same root cause.)

| ID | Report finding | v1 item it leaked from | V2 fix | Status |
|----|----------------|------------------------|--------|--------|
| **V2-A** | P-A: 1/9 workers (#404) stopped at the callee's "ready to push" and skipped its own push — prose "callee return is NOT completion" did not hold | P1-1 | **Orchestrator-owned push** for the rebase-only path (worker returns `{rebased_sha, old_remote_sha, behind_after, tree_clean}`, top-level pushes). **Revises D14.** Comments path unchanged (resolve-pr-comments still owns its push). | ✅ |
| **V2-B** | P-B: orchestrator resolved `FLUTTER_BIN` once but wave-1 worker prompts dropped it → all 9 re-discovered fvm (same SDK by luck) | P1-3 | **Inject the resolved `FLUTTER_BIN` string verbatim into every worker prompt; worker MUST NOT re-resolve.** | ✅ |
| **V2-C** | P-C: `/check-test` widely-used-file fallback escalated a router touch to the full 4379-test suite on 2/9 workers, dominating wall-clock | P1-2 | **`--no-escalate` flag on `/check-test`** (additive, opt-in); resolver rebase stage + `resolve-conflict` callee pass it. | ✅ |
| **V2-D** | P-D: #480/#499 perma-`needs-human` (dirty worktree) re-flagged silently every loop iteration | (new) | (a) **notification dedup** via `claude-reports/ggx-pr-resolver/needs-human-state.json` (re-announce only on fileset change); (b) **report includes one-paste recovery hint**; (c) **`--stash-dirty` opt-in escape hatch** (auto-stash non-residue when YOU pass it — Charlie rarely has real WIP). | ✅ |

**Why /ggx-dispatcher workers don't hit P-A:** its push lives inside the dedicated
`/dev:ship` stage (a named stage = unmissable); the resolver's per-PR worker is too
thin to justify a ship stage, so V2-A hoists the push to the orchestrator instead.

**D14 revision (2026-06-08):** push ownership now splits by path — comments stage →
resolve-pr-comments pushes; rebase-only → the orchestrator pushes after re-verifying
`behind==0 && clean`. The per-PR worker never pushes on the rebase-only path.
