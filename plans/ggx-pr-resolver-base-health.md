# Improvement Plan v2 — `/ggx-pr-resolver` base-health awareness

> Source: retro on a `/ggx-pr-resolver --batch --user=@me --auto` run (2026-06-09)
> over 10 open PRs in `gogox-client-flutter`. The run rebased every PR cleanly,
> but **7 of 9 spawned workers reported `tests-failed` for the _same_ root cause**:
> `origin/trunk` itself did not compile (`coupon_page.dart` used
> `AppSizes.borderWidthDefault` without importing `app_sizes.dart`; landed via
> trunk PR #492 / `1f433de5`). The breakage was only discovered _after_ fanning
> out and burning ~9 worker runs (~470k tokens), and the green-tests gate then
> pushed PRs inconsistently depending on which files each branch's incremental
> tests happened to compile.
>
> Follow-up to `plans/archive/ggx-pr-resolver-improvements.md` (2026-06-08, the
> foreground-tests / push-ownership / pre-spawn-filter / concurrency-cap fixes,
> all implemented). Those held this run — no parked workers, no orphan processes,
> waves of 5+4 respected. This plan adds the missing dimension that run did not
> cover: **the resolver has no notion of base health, so a broken base is paid
> for N times and then masked by a luck-based push gate.**
>
> **Status (2026-06-09):** PROPOSED. All fixes land in
> `commands/dev/ggx-pr-resolver.md` (and one assist in
> `commands/dev/resolve-conflict.md` callee mode). No code in this PR — design only.

## Goal

A broken base (trunk that does not compile, or is red on its own CI) should be
detected **once, before fan-out**, and should produce a **distinct, honest
outcome** for every blocked PR — never N independent rediscoveries of the same
bug, and never a push decision that depends on the accident of which files a
branch's incremental test set transitively compiles.

---

## P0 — Base-health pre-check before fan-out (highest leverage)

- **Symptom:** The mechanical pre-filter (§3a) checks `needs_rebase` and
  `open_threads` but **never checks whether `base` itself is healthy**. So the
  run fanned out 9 workers and only learned on aggregation that 7 fail for one
  trunk bug. Each worker independently re-derived the identical root cause
  (verified the broken file is byte-identical on trunk, cleared caches, re-ran
  `pub get`) — ~45–62k tokens apiece, ~470k total, to discover one missing
  import.
- **Fix:** In the top-level Batch sweep, **before any spawn**, run a one-time
  base-health probe and short-circuit the whole batch on failure:
  1. **Cheapest signal (near-zero cost):** query the base branch's own latest-commit
     CI conclusion — `gh api repos/{owner}/{repo}/commits/{base}/check-runs`
     (or `gh run list -b <base> -L1`). If base CI is **failing**, the batch is
     almost certainly chasing a base bug, not per-PR drift.
  2. **Stronger signal (optional, opt-in `--probe-base-compile`):** in the repo's
     primary worktree (or a throwaway detached one at `origin/<base>`), run the
     incremental compile/analyze the workers would run — `flutter analyze` /
     `dart analyze` on the changed-since-fork surface, or a single
     `/check-test --no-escalate` smoke — to confirm base compiles at all.
  3. On a RED base: **do not fan out.** Emit one report —
     `base-broken: <base> fails its own checks (<link/first-error>); rebasing
     onto it will fail every PR's test gate. Fix base first, then re-run.` —
     and stop. This mirrors the existing fail-fast on toolchain mismatch (P1-3):
     a bad shared precondition aborts the batch instead of being paid per-worker.
- **Acceptance:** when base is broken, the run spends **≤1 probe** and **0
  per-PR workers**, and names the base bug once.

---

## P1 — A distinct `base-broken` worker outcome (kills the luck-based push gate)

- **Symptom:** The push decision is *luck-based*. Whether a PR is pushed depends
  on whether its strict-incremental test set happens to transitively compile the
  broken base file — **not** on the correctness of its own rebase:
  - #482, #518 → incremental tests didn't touch `coupon_page.dart` → green → pushed.
  - #516, #505, #498, #488, #481, #446, #404 → tests pulled it in → red → held —
    even though their rebases were equally clean (and #516's even carried a real,
    hand-resolved `_buildFeeRows` conflict that nearly got stranded unpushed).

  The current contract collapses both "your branch broke the tests" and "base is
  broken and your tests merely compile it" into the single `needs-human:
  tests-failed` state, so the operator cannot treat them differently.
- **Fix:** Add a fourth post-rebase classification to step 5 / step 8:
  - After a clean rebase with a RED suite, the worker performs the **base-attribution
    check it already does ad hoc**: is the failing file/symbol **identical on
    `origin/<base>`** and **outside this branch's diff**? If yes → outcome is
    **`base-broken`** (root cause is base, not this PR), distinct from
    `tests-failed` (a genuine branch-side / semantic-conflict failure).
  - `base-broken` PRs are reported together under the **one** shared root cause
    (dedup by `(file, symbol)`), not as N separate failures.
  - **Push policy for `base-broken` is a single batch-level decision, applied
    uniformly** (orchestrator-owned, like the V2-A push hoist): either push all
    cleanly-rebased `base-broken` PRs (accepting red-until-base-fixed CI — which is
    what actually happened to #482/#518/#516 anyway) **or** hold all of them. Never
    push some and hold others for the same root cause. Default: **hold all** and
    point at the base fix — re-running after base is green pushes them with
    genuinely green tests, strictly better than a red-CI push. (If P0 lands,
    `base-broken` should rarely be reached, since the batch aborts pre-fan-out.)
- **Acceptance:** for a given base bug, every cleanly-rebased PR gets the **same**
  push verdict; the report shows one root cause, not seven.

---

## P2 — Harden the "do not fix out-of-scope code" rule (1/8 worker variance)

- **Symptom:** Of the 8 workers that hit the base bug, **7 correctly refused** to
  touch the out-of-scope file and reported the failure; **#518's worker added the
  missing `import` to `coupon_page.dart` itself** to force its suite green, then
  reported success. Same situation, opposite behavior — the exact non-deterministic
  1/N variance the 2026-06-08 plan fought elsewhere (the 1/9 missed-push). The
  result was a PR pushed with an unrelated file in its diff (a reviewer trap).
- **Fix:** Make the prohibition explicit and unmissable in the worker contract:
  > A compile/test failure in a file **outside this branch's diff** is NEVER the
  > worker's to fix — not even a one-line import. Editing it is a contract
  > violation, regardless of whether it makes the suite green. Such a failure is
  > `base-broken` (P1) or `tests-failed`; report it, do not patch it.

  This belongs in `ggx-pr-resolver.md` step 5/6 and is restated at the executor
  in `resolve-conflict.md` Callee Step 3 (same pattern as the foreground-test
  mandate, which is restated at both layers precisely because one statement did
  not hold across N workers).
- **Acceptance:** a worker facing an out-of-scope compile error never modifies the
  offending file; the diff it would push contains only its own branch's changes.

---

## Operator-side note (orchestrator behavior, not a skill change)

This run also surfaced an orchestrator inconsistency worth recording even though
it is not a `.md` edit: the main session pushed #482/#518 but **held #516 until
the human flagged it**, despite all three sharing the identical red-until-base-fix
condition — #516 was rescued only because the user asked "didn't #516 have a
conflict?". Lesson: when several PRs share one root-cause failure, the operator
must treat them as a **set** and either present the asymmetry as a single decision
or apply one uniform action — not act unevenly PR-by-PR. P1's "uniform batch-level
push verdict for `base-broken`" encodes this so it does not depend on operator
vigilance.

---

## This run's raw numbers (for the next retro's baseline)

- 10 open PRs; all behind base; **0** had open review threads → all rebase-only path.
- 1 skipped pre-spawn: #515 `worktree-dirty` (real WIP — `sqflite_android: 2.4.1`
  override in `pubspec.yaml`); dirty guard + residue allowlist worked correctly
  (#404 residue auto-stashed and proceeded).
- 9 workers spawned (waves of 5 + 4, concurrency cap respected); foreground tests,
  no parked workers, no orphan processes — 2026-06-08 fixes held.
- Outcomes: 2 clean→pushed (#482, #518†), 1 conflict-resolved→pushed (#516, after
  operator flag), 7 blocked by the single trunk bug.
  († #518 pushed with the P2 out-of-scope import still bundled, per an explicit
  operator decision this run.)
- Root cause fixed once via `gogox-client-flutter` PR #521 (add the missing
  `app_sizes` import to `coupon_page.dart`), which unblocks all 7.
- Cost of NOT having P0: ~9 worker runs (~470k tokens) to discover one missing
  import that a single base-CI query would have surfaced for free.
