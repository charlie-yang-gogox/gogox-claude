---
name: test-fix-loop
description: >
  Unattended run→diagnose→fix→re-run loop that drives a project's test suite to
  green by editing TEST code only — it never changes production behavior. Any
  failure that can only be resolved by touching production source is reported and
  skipped. Stops when all tests pass OR every remaining failure requires human
  input. Use when asked to "get the tests green", "fix the failing tests
  unattended", "loop the test suite", or "auto-fix test code".
---

<!--
  RULE: All skill content must be written in English.
  This applies to frontmatter, body, code comments, and examples.
  No exceptions. PRs with non-English content will be rejected.
-->

# Test Fix Loop

> **One-line summary**: Resolve the canonical test command + result-extraction recipe for the active project, then loop `run → extract failures → classify → fix test-side only → re-run` without human prompts, until the suite is green or the only failures left are ones a human must decide.
>
> **Locked design decisions** (do not re-litigate):
> - **Unattended.** No HITL gate. The loop runs to a terminal state on its own. The single exception is the abort safeguards in §6 (no-progress / max-iterations / non-test run failure).
> - **Test-code-only fixes.** The loop may edit *test* sources, test fixtures, mocks/stubs, golden/snapshot files, and test-scoped config. It must **never** edit production source, and must never change observable production behavior to make a test pass. The classifier in Step 4 is the gate.
> - **Report-and-skip, never disable.** A failure that needs a production change is recorded as `REQUIRES-HUMAN` and excluded from the pass target. The loop does **not** `@Ignore`/`skip`/comment-out/delete the test to make the suite "green" — that hides signal and is forbidden.
> - **Snapshot/golden updates are production-behavior-neutral only.** Regenerating a golden is allowed *only* when the new output reflects an intended, already-merged production change the test simply hasn't caught up to. If the golden diff reveals a real production regression, that is `REQUIRES-HUMAN`.
> - **Isolated worktree.** All work happens in a dedicated git worktree created at the start (Step 0.5). The skill never mutates the caller's current checkout.
> - **Commit per test fix, then push at the end.** Each accepted test-side fix is committed in the worktree (one commit per fixed test, see Step 5). When the loop reaches a terminal state with at least one commit, it **pushes** the worktree branch once (Step 8) so the work is shareable and any open PR can be commented on. Push happens once, at the terminal state — not per-commit inside the loop.
> - **REQUIRES-HUMAN ⇒ publish the evidence.** When the loop ends in a state that still needs a human (the 🟡 / 🔴 terminal states in §7), it uploads the run artifacts — including the Maestro output for e2e runs — to GCS, then, **only if a PR exists** for the branch, posts the terminal report plus the GCS link as a PR comment (Step 8). No PR → the report stays local and the GCS link is printed for the user instead.

## Inputs

- **Optional path scope** as the first argument — a directory or test-file glob to restrict the loop to (e.g. `test/features/booking`). If omitted, the loop targets the whole suite (or the incremental set, see Step 2).
- **`--type <kind>`** (optional) — the type of test to run: `unit`, `widget`, `integration`, or `e2e` (`all` = no filter). This selects *which* runner/target the contract resolves in Step 1 (e.g. `flutter test test/` vs `integration_test/`; gradle `testDebugUnitTest` vs `connectedAndroidTest`; xcodebuild `…Tests` vs `…UITests`; node `test` vs `test:e2e`). If omitted, defaults to the project's primary unit-test target and the contract records the resolved type. If the requested `--type` has no resolvable runner for `{platform}`, abort in Step 1 and say so.
- **`--incremental`** (optional) — only run tests affected by the current branch's diff (delegates detection to `/check-test`'s incremental logic) instead of the full suite. Default is full suite, because unattended runs want a complete picture.
- **`--max-iters N`** (optional, default `8`) — hard cap on loop iterations (see §6).
- **Optional ticket id** (e.g. `CAF-123`) — used only to name the worktree/branch (Step 0.5). If omitted, the skill derives a `test/test-fix-loop-<scope>` branch name.

## Prerequisites

- cwd is inside the target project's git checkout (or worktree).
- The project is onboarded to a gogox-claude profile (Step 0 resolves it). If no profile resolves and no test command can be discovered, the skill aborts with a clear message — it does not guess.

## Steps

### 0. Log usage

```bash
echo "{\"skill\":\"test-fix-loop\",\"user\":\"$(whoami)\",\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" >> ~/.gogox-claude-usage.jsonl 2>/dev/null || true
```

### 0.5. Create and enter an isolated worktree

All editing, running, and committing happens here — never in the caller's checkout.

1. Pre-flight the current checkout: `git worktree prune`, then `git status --porcelain`. If the caller's tree is dirty, this skill still proceeds (the worktree branches from the latest trunk, not the dirty state) but notes it in the report.
2. Create the worktree:
   - **If a ticket id was provided** → delegate to `/add-worktree <ticket-id> --type test` (handles branch naming `test/<ticket-id>`, worktree at `../<ticket-id>`, dependency install, and moving the session in).
   - **Else** → create one directly: branch `test/test-fix-loop-<scope>` (scope = sanitized path arg or `suite`) off the latest trunk, worktree at `../test-fix-loop-<scope>`, then run the platform `{deps_install}` command and move the session into it.
3. From here on, every command in this skill runs **inside the worktree**. Record the worktree path and branch in the contract (Step 1.4).

### 1. Resolve the test script (execution + result extraction)

This is the contract the unattended loop runs against. Resolve it **once** and record it so the run is reproducible and auditable.

1. **Resolve project profile** (same convention as `/check-test`):
   - If `<repo-root>/.gogox-claude.yaml` exists, read `platform` and `product` from it.
   - Else read `~/.claude/commands/profiles/registry/$(basename "$(git rev-parse --show-toplevel)").yaml`.
   - Then read the platform profile `~/.claude/commands/profiles/platform/{platform}.yaml` for `test_cmd`.
2. **Discover the canonical test script** for the requested `--type` (Inputs) — when a project exposes distinct runners/targets per type, pick the one matching `--type` (default: the primary unit target). Search in this priority order, stopping at the first hit:
   1. A repo-local test runner script that self-documents how to run + how to read results — look for `scripts/test.sh`, `bin/test`, `Makefile` (`test:` target), or a `test`/`test:ci` script in `package.json`. If found, **read its header/comments** for the documented invocation and result-extraction notes; that documentation is authoritative over the generic profile command.
   2. The profile `test_cmd` from Step 1.1.
   3. Platform default runner (`flutter test`, `./gradlew testDebugUnitTest`, `xcodebuild test …`, `npm test`).
   - If `test_cmd` itself points at another gogox-claude command (e.g. flutter's `test_cmd: /check-test --all --fix`), do **not** delegate to that command's `--fix` (its fix scope includes production code, which violates this skill's locked decision). Use only its *runner* invocation (`flutter test -j …`), and apply this skill's test-only fix policy instead.
3. **Determine the result-extraction recipe** for `{platform}` — how to turn a run into a structured failure list. Reuse `/check-test`'s documented extraction per platform:
   - **flutter**: parse `flutter test` stdout (`--reporter expanded` for stable parsing); each failure gives test name, `file:line` from the stack top, and the assertion/exception message.
   - **android**: read the JUnit XML / HTML under each module's `build/reports/tests/` and `build/test-results/`; each `<testcase>` with a `<failure>` gives class, method, and message.
   - **ios**: parse the `.xcresult` bundle (`xcrun xcresulttool`) or the xcodebuild log for `error:`/failing assertions; map to test class/method.
   - **node**: prefer a machine-readable reporter (`--reporter json` / `--json`) when the runner supports it; else parse the failure block from stdout.
   - **e2e (Maestro)**: run `maestro test --format junit --output .dev/test-fix-loop/maestro/` so the run emits a JUnit report plus its recordings/screenshots into a known directory; parse the JUnit `<testcase>`/`<failure>` entries — each failing flow gives the flow file, the failing step/assertion, and the message. Keep the `--output` directory: it is the **Maestro artifact bundle** uploaded to GCS in Step 8.
4. **Record the contract** to the run journal so the loop (and any later reader) knows exactly what is being executed and how results are read:

```bash
mkdir -p .dev/test-fix-loop
```

   Write `.dev/test-fix-loop/contract.md` with: the worktree path + branch (from Step 0.5), resolved `{platform}` + `{product}`, the **type of test** (the `--type` argument, or the resolved default when omitted — one of `unit` / `widget` / `integration` / `e2e` / `all`), the exact run command (the runner/target chosen for that type), the result-extraction recipe, the scope (arg / `--incremental` / full), the production-vs-test path boundary from Step 4, and the publish targets from Step 8 (the GCS bucket + object-key convention reused from the e2e runner, and, for e2e, the Maestro `--output` directory). If any of these could not be resolved, abort here and tell the user what was missing.

### 2. Establish scope and the production/test boundary

- **Scope**: the path arg if given; else `--incremental` set (delegate detection to `/check-test`'s incremental skeleton) ; else the whole suite.
- **Production/test boundary** by `{platform}` — the loop may edit only the *test* side:

  | platform | production (NEVER edit) | test-side (editable) |
  |----------|------------------------|----------------------|
  | flutter  | `lib/**` | `test/**`, `integration_test/**`, `test_driver/**`, mocks/fixtures/`*.mocks.dart` under those, golden files |
  | android  | `**/src/main/**` | `**/src/test/**`, `**/src/androidTest/**`, test fixtures/resources under those |
  | ios      | app-target sources | `*Tests/**`, `*UITests/**` targets and their fixtures |
  | node     | `src/**`, `lib/**` (published code) | `test/**`, `__tests__/**`, `*.test.*`, `*.spec.*`, `__mocks__/**`, fixtures |

  Shared/build config (`pubspec.yaml`, `build.gradle`, `package.json` deps, CI yaml) is **boundary-sensitive**: editing it to add a missing *test* dependency is allowed; changing it in a way that alters how production is built/shipped is `REQUIRES-HUMAN`.

### 3. Run the suite

Execute the command recorded in the contract. Capture full stdout/stderr to `.dev/test-fix-loop/run-<iter>.log`.

- If the run **passes** entirely → go to §7 (success).
- If the run **fails to even execute** (compile error in *production* code, gradle sync failure, missing toolchain, xcodebuild config error) → this is not a test failure the loop can own. Record it and abort per §6 ("non-test run failure"). Do **not** start editing test files to paper over a broken build.
- If the run executes and produces test failures → go to Step 4.

### 4. Extract and classify failures

Apply the result-extraction recipe to build a failure list. For **each** failing test, classify into exactly one bucket:

- **`TEST-FIXABLE`** — the production code is behaving correctly; the *test* is wrong, stale, or flaky-by-construction. Signals:
  - Test references a renamed/moved symbol, outdated API signature, or removed constant.
  - Assertion encodes an expectation that an intended, already-merged production change deliberately changed (stale expectation / stale golden).
  - Broken/missing mock setup, wrong fixture data, incorrect test wiring, missing `await`, bad test-only import.
  - Order-dependence / shared-state bleakage between tests fixable purely in test setup/teardown.
- **`REQUIRES-HUMAN`** — making this pass would require editing production code or changing production behavior, OR you cannot determine the correct fix from the test side with confidence. Signals:
  - The test asserts a correct requirement and production output is actually wrong (a real bug/regression).
  - The fix would mean editing a production path from the Step 2 boundary.
  - Ambiguous intent — the "right" expected value is a product decision, not a mechanical update.
  - A golden/snapshot diff that reflects an *unintended* production change.

Write the classified list to `.dev/test-fix-loop/failures-<iter>.md` (one row per test: name, `file:line`, message, bucket, one-line rationale).

**Tie-break rule:** when unsure whether a failure is `TEST-FIXABLE` or `REQUIRES-HUMAN`, classify it `REQUIRES-HUMAN`. The cost of a wrong test edit (masking a real bug) is far higher than the cost of skipping.

### 5. Fix the `TEST-FIXABLE` set (test-side only)

For each `TEST-FIXABLE` failure, one at a time:

1. Make the minimal edit on the **test side only** (per the Step 2 boundary). Never touch production files. Never weaken an assertion just to make it pass — fix it to assert the *correct* current behavior.
2. If applying the fix would require a production edit after all → reclassify the failure as `REQUIRES-HUMAN` and move on (do not edit production).
3. **Re-run just that test** to confirm the fix turns it green and (regression guard, §6) breaks nothing that was passing. If it does not go green, revert the edit and reclassify as `REQUIRES-HUMAN`.
4. **Commit the fix** in the worktree — one commit per fixed test. Stage only the test-side files this fix touched:

   ```bash
   git add <test-side-paths>
   git commit -m "test: fix <test name> — <one-line why>"
   ```

   Do not push inside the loop. Commits accumulate on the worktree branch; the single push happens once at the terminal state (Step 8).
5. Record each edit + commit sha (file, what changed, why) into `.dev/test-fix-loop/fixes-<iter>.md`.

If there were **zero** `TEST-FIXABLE` failures this iteration (every failure is `REQUIRES-HUMAN`), do not edit or commit anything → go to §7 (terminal: only-human-input-remains).

### 6. Loop / abort safeguards

After fixing, increment the iteration counter and return to **Step 3** (re-run). The loop is bounded by these guards:

- **Max iterations**: stop after `--max-iters` (default 8) iterations regardless of state, and report. Prevents runaway unattended loops.
- **No-progress detection**: if an iteration's set of failing tests is identical to the previous iteration's *and* no test-side edit was applied (or the same failure reappears after a fix), stop — the loop is not converging. Reclassify the stuck failures as `REQUIRES-HUMAN` and report.
- **Regression guard**: if a fix causes a *new* test (that was passing) to fail, revert that fix, reclassify its original failure as `REQUIRES-HUMAN`, and continue. Net test count passing must be monotonic; never trade one red for another.
- **Non-test run failure**: a production-side compile/build/config error (Step 3) aborts the loop immediately with the captured error — this is a human/build problem, not a test-fix problem.

### 7. Report (terminal states)

Write `.dev/test-fix-loop/report.md` and print a summary. There are exactly three terminal states:

1. **✅ All green** — every targeted test passes. Report iterations used, the list of fix commits (sha + test + why, from `fixes-*.md`), the worktree path/branch, and confirm no production file was touched.
2. **🟡 Only-human-input-remains** — no `TEST-FIXABLE` failures left; the suite still has `REQUIRES-HUMAN` reds. Report the green delta + fix commits achieved, plus a table of skipped failures with their rationale and what a human must decide/fix in production.
3. **🔴 Aborted** — a §6 guard fired (max-iters, no-progress, or non-test run failure). Report the guard that fired, the current failure list, the fix commits made so far, and the captured run log path.

Every report **must** list the worktree branch's commits since trunk and the cumulative `git diff --name-only $(git merge-base HEAD trunk)..HEAD` so the reader can verify, at a glance, that the branch (a) was pushed (or, if the push failed / there is no remote, is local-only — say which, per Step 8) and (b) touched only test-side paths. Flag loudly if any production path appears.

### 8. Publish — push, and (on REQUIRES-HUMAN states) upload artifacts + comment on the PR

Runs once, after §7 settles. Skip the whole step if the loop made **zero** commits (nothing to publish) — note "nothing to push" in the report and stop.

1. **Push the branch.** When at least one fix was committed:

   ```bash
   git push -u origin "$(git rev-parse --abbrev-ref HEAD)"
   ```

   If the push fails (no remote, auth, non-fast-forward), do **not** abort — record the failure in `report.md` and keep the commits local. The branch is still on disk for a human.

2. **Decide whether to publish evidence.** Only the human-input terminal states do this:
   - ✅ **All green** → push only (step 1). No upload, no comment — there is nothing for a human to decide.
   - 🟡 **Only-human-input-remains** / 🔴 **Aborted** → continue to steps 3–5.

3. **Resolve the PR** (needed for the GCS object key *and* the comment):

   ```bash
   gh pr view --json number,url 2>/dev/null
   ```

4. **Upload the run artifacts to GCS — reuse the e2e runner's upload path.** The flutter repo's `scripts/e2e_pr_runner.py` already owns this: `upload_to_gcs()` + `_artifact_blob_name()`. Do **not** invent a new bucket/layout — match it so all Maestro artifacts land in one place:
   - **What to upload**: a single `.zip` of the Maestro debug-output dir (the `--output` bundle from Step 1.3 — recordings / screenshots / JUnit) together with `.dev/test-fix-loop/` (run logs, failure/fix lists, `report.md`), same as the e2e runner zips its debug-output dir.
   - **Bucket**: `${GCS_BUCKET:-ggx-e2e-testresult}` (optional `${GCS_PROJECT}`), via Application Default Credentials. Objects inherit the bucket's lifecycle policy for TTL — never set per-object deletion.
   - **Object key** (`_artifact_blob_name(repo, pr_number, sha)`): `<repo>/pr-<pr_number>/<short-sha>-<YYYYmmddTHHMMSS>.zip` — repo with `/`→`_`, short HEAD sha, local-time stamp. **No PR** (step 3 found none) → substitute `branch-<branch>` for the `pr-<pr_number>` segment.
   - **How**: prefer invoking the e2e runner's helper (import `package_and_upload_artifacts` / `upload_to_gcs` from `scripts/e2e_pr_runner.py`) rather than re-implementing; only if that module is not importable, fall back to `gsutil cp <zip> gs://${GCS_BUCKET:-ggx-e2e-testresult}/<key>`.
   - **Hand-off link**: the console URL the helper returns — `https://console.cloud.google.com/storage/browser/_details/<bucket>/<key>`.

   If the upload tooling is unavailable or the upload fails, record it in `report.md` and skip the link (do **not** abort) — the artifacts remain under `.dev/test-fix-loop/` for local inspection.

5. **Comment on the PR — only if one exists** (from step 3):
   - **PR exists** → post the §7 terminal report plus the GCS console URL as a single comment:

     ```bash
     gh pr comment "$PR_NUMBER" --body "$(printf '## test-fix-loop — %s\n\n%s\n\n**Run artifacts (GCS):** %s\n' "$TERMINAL_STATE" "$REPORT_SUMMARY" "$GCS_URL")"
     ```

   - **No PR** → do **not** create one. Print the same summary + GCS link to the user and note in `report.md` that no PR was found, so the GCS link is the hand-off.

   If `gh pr comment` fails, log the error, leave the report + GCS link in place, and do **not** abort.

## Rules

- The production-behavior invariant is absolute: if at any point the only way forward is a production edit, that failure is `REQUIRES-HUMAN` — full stop.
- Never make the suite "pass" by skipping, ignoring, deleting, or commenting out a test. Report-and-skip means *excluded from the fix target and surfaced in the report*, not *silenced in code*.
- Minimal edits only — fix the failing test, do not refactor unrelated test code.
- Every iteration's run log, failure list, and fix list is persisted under `.dev/test-fix-loop/` so an unattended run is fully auditable after the fact.
- All work happens in the Step 0.5 worktree — never in the caller's checkout.
- Commit per fixed test (Step 5); push once at the terminal state (Step 8) when the loop made at least one commit. Each commit must contain only test-side paths.
- Publishing evidence (GCS upload + PR comment) happens **only** on the human-input terminal states (🟡 / 🔴), and the PR comment is posted **only if** `gh pr view` resolves a PR for the branch — never create a PR. Every publish step is best-effort: a failure is recorded in the report, never aborts the run.
- If the project has no resolvable test command, abort in Step 1 — do not invent one.

## Gogox Context

- Profiles live at `~/.claude/commands/profiles/` (symlinked by `install.sh`): `registry/<repo>.yaml` → `{platform, product}`; `platform/{platform}.yaml` → `{test_cmd, format_cmd, …}`.
- Supported platforms today: `flutter`, `android`, `ios`, `node`. Flutter's profile `test_cmd` is `/check-test --all --fix` — this skill uses only its *runner* (`flutter test`), not its production-touching `--fix`.
- Result-extraction mechanics are shared with `/check-test` Step 5 — keep the two in sync if a runner's output format changes.
- Runtime artifacts go to `.dev/test-fix-loop/` (the `.dev/` convention used across the dev pipeline; gitignored in target repos).
- e2e on mobile runs via **Maestro**; `maestro test --format junit --output .dev/test-fix-loop/maestro/` yields both a parseable JUnit report (Step 1.3) and the recordings/screenshots bundle uploaded to GCS in Step 8.
- GCS upload **reuses the e2e runner's path** (`scripts/e2e_pr_runner.py` → `upload_to_gcs` / `_artifact_blob_name`): bucket `${GCS_BUCKET:-ggx-e2e-testresult}` (optional `${GCS_PROJECT}`), object key `<repo>/pr-<pr_number>/<short-sha>-<stamp>.zip` (or `branch-<branch>/…` when there is no PR), uploaded via google-cloud-storage + ADC; the hand-off link is the `https://console.cloud.google.com/storage/browser/_details/…` URL. PR comments are posted with `gh pr comment` only when `gh pr view` resolves a PR for the branch (same pattern as `/code-review` and `/resolve-pr-comments`).

## Output

A **pushed** worktree branch with one commit per fixed test — all touching test-side paths only — plus `.dev/test-fix-loop/report.md` summarizing one of the three terminal states, the fix commits applied, and the skipped `REQUIRES-HUMAN` failures with rationale. On the human-input terminal states (🟡 / 🔴) the run artifacts (including any Maestro output) are uploaded to GCS, and — only if a PR exists for the branch — the report + GCS link are posted as a PR comment.

## How this was used last

> Update this footer when you use the skill, so the next person knows the real-world use case.
> Format: `YYYY-MM-DD by @username — one-line context`

- 2026-06-08 by @peter.wong — initial authoring, not yet run on a real suite
- 2026-06-11 by @peter.wong — dropped never-push; added Step 8 publish (push at terminal state + Maestro/run-artifact GCS upload + PR comment, PR-only) for REQUIRES-HUMAN states (CAF-699); still not run on a real suite
- 2026-06-12 by @peter.wong — aligned the Step 8 GCS upload with the e2e runner's path (`scripts/e2e_pr_runner.py`: bucket `ggx-e2e-testresult`, key `<repo>/pr-<#>/<sha>-<stamp>.zip`) instead of a bespoke bucket/`gsutil` prefix (CAF-699)
