---
name: resolve-conflict
description: >
  Merge/Rebase current branch onto trunk, resolve any merge conflicts,
  run tests until green, format, and commit. Single mode does NOT push.
  Pass --skip-test to bypass the tests-green gate (single + batch only; never
  callee). Platform-aware: uses the right test and format commands per project. With
  --batch, sweeps open GitHub PRs (optionally filtered by --user) and fans out
  one parallel subagent per PR — each works in the PR's worktree, merges onto
  its base, tests, and pushes only if clean (conflicted PRs are flagged for a
  human). With --callee (invoked by /ggx-pr-resolver, not user-facing), a hybrid
  variant rebases one caller-supplied worktree onto an explicit base ref,
  resolves non-trivial conflicts by default via documented best-effort
  assumptions (auto-assume; never prompts), falls back to auto-abort only when
  no defensible default exists, and does NOT push.
---

# Resolve Conflict — Rebase onto Trunk & Fix Conflicts

Pull latest trunk via merge/rebase, resolve conflicts, verify tests pass, format, and commit.

---

## Modes

| Mode | Trigger | Scope |
|------|---------|-------|
| **Single** (default) | no `--batch` | Resolves the **current branch** against the repo's default branch ref (`origin/trunk` on flutter, `origin/main` on gogox-claude — see **Base branch** below). Runs Steps 0–8 below. |
| **Batch** | `--batch` | Sweeps open GitHub PRs (optionally filtered by `--user`) and fans out **one parallel subagent per PR**. Each subagent works in that PR's own git worktree (reused if it exists, created otherwise), merges/rebases onto the PR's base branch, runs tests, and **pushes only if the merge was clean**. Conflicted PRs are aborted and flagged for human review. See the **Batch Mode** section near the end. |
| **Callee** (hybrid) | `--callee` | Invoked by `/ggx-pr-resolver` step 5 — **not** a user-facing mode. Operates on **one explicit worktree path** (the caller already created/reused it), **rebase-only** onto an **explicit base ref the caller passes** (never assumes `origin/trunk`), with **non-interactive auto-assume conflict resolution** (DEFAULT — make a documented best-effort assumption about merge intent and proceed; NEVER `AskUserQuestion`), falling back to **auto-abort + restore worktree + report `needs-human: conflict`** only when the conflict is genuinely ambiguous with no defensible default; then tests + format + commit, **NO push** (the caller owns the single push). Records every assumption it made. See the **Callee Mode** section near the end. |

**Usage**:

```
/resolve-conflict [--rebase | --merge] [--skip-test]
/resolve-conflict --batch [--user=<login> | --user=@me] [--limit=<N>] [--rebase | --merge] [--dry-run] [--skip-test]
/resolve-conflict --callee --worktree=<path> --base=<ref>   # hybrid; invoked by /ggx-pr-resolver only
```

- `--rebase` (default) / `--merge` — strategy, passed through to every PR in batch mode.
- `--skip-test` — **single + batch only** (rejected in `--callee`). Skip Step 4 entirely — no `{test_cmd}` is run and the tests-green gate is bypassed. Use when the suite is known-broken for unrelated reasons, on docs-only branches, or for a fast conflict-resolution pass you will test separately. The skip is never silent: a banner is printed (single mode) / the outcome is marked `pushed (UNTESTED)` (batch mode). **Batch consequence:** a cleanly-rebased PR is still force-pushed **without** the tests-green requirement — you are opting into shipping untested code across every clean PR in the sweep. Ignored (and irrelevant) under `--dry-run`, which runs no tests regardless.
- `--user=<login>` — batch only; restrict to PRs **created by** that GitHub login. `@me` resolves to the authenticated user. Omit to sweep all PRs (bots excluded).
- `--limit=<N>` — batch only; **concurrency cap** — at most **N** subagents run at once. **All** eligible PRs are still processed; they are worked through in waves of N. Omit for no explicit cap (the harness's own concurrency cap still applies).
- `--dry-run` — batch only; **read-only preview**. Lists every matching PR and probes mergeability **locally with git** (`git merge-tree`, an in-memory merge that reports conflicts without touching the working tree, index, or any branch). Performs **no** checkout, real merge/rebase, conflict resolution, test run, commit, or push — nothing is mutated, locally or remotely (a read-only `git fetch` of the refs is the only network call).

**Base branch.** Throughout Steps 2–8, `{base_branch}` is the remote ref the branch is rebased/merged onto:

- Single mode → `{base_branch}` is the repo's default branch ref (`source "$HOME/.claude/lib/dev-mode.sh"; trunk_ref` → `origin/trunk` on flutter, `origin/main` on gogox-claude).
- Batch mode → `{base_branch}` is `origin/<the PR's baseRefName>` (i.e. the actual base the PR targets — `main`, `master`, `trunk`, or any release branch), set by the batch loop per PR.
- Callee mode → `{base_branch}` is the **explicit `--base=<ref>` the caller passes** (e.g. `origin/<the PR's baseRefName>`), **never `origin/trunk`** — `/ggx-pr-resolver` resolves the real `baseRef` per PR (release-branch PRs exist; assuming trunk would rebase onto the wrong base and corrupt the PR — owner decision D12, review item M5).

---

## Step 0: Resolve project profile

Before any other step, determine the active project profile so later steps know which test and format commands to run.

**Resolution order:**

1. **Repo self-describes** — read `<repo-root>/.gogox-claude.yaml` if present. Use its `platform` field.
2. **Central mapping** — else, read `~/.claude/commands/profiles/registry/$(basename "$(git rev-parse --show-toplevel)").yaml` for `platform` and `product`.
3. **Error** — if neither resolves, stop and tell the user:
   > Cannot resolve gogox project profile. Either add `~/.claude/commands/profiles/registry/<basename>.yaml`, or create `<repo>/.gogox-claude.yaml` with `platform:` and `product:`.

After resolution, read:

- `~/.claude/commands/profiles/platform/<platform>.yaml` — exposes `test_cmd`, `format_cmd`.

Hold these values in memory for use in Steps 4 and 5 where you see `{test_cmd}` and `{format_cmd}`.

## Steps

### 1. Pre-check

If `--merge` is provided, use `merge` instead of `rebase` in all git commands in this skill.

If `--rebase` is provided, use `rebase` in all git commands in this skill.

If `--skip-test` is provided, remember it for Step 4 (and, in batch mode, pass it to every subagent — see **Batch Mode**). `--skip-test` is **rejected in `--callee` mode** (see **Callee Mode**): the callee's test run is the safety net for its auto-assume conflict resolution and must never be bypassable.

Invoke `/check-clean`. If it fails, stop and ask the user to commit or stash before proceeding.

### 2. Fetch & Merge/Rebase onto trunk

`{base_branch}` is the repo's default branch ref in single mode (`source "$HOME/.claude/lib/dev-mode.sh"; trunk_ref` → `origin/trunk` on flutter, `origin/main` on gogox-claude), or `origin/<the PR's baseRefName>` in batch mode (see **Modes** above). Substitute the bare branch name (e.g. `trunk`, `main`) for the `git fetch` refspec.

#### merge commands
```bash
git fetch origin <base-branch-name>
git merge {base_branch}
```

#### rebase commands
```bash
git fetch origin <base-branch-name>
git rebase {base_branch}
```

If the merge/rebase succeeds with no conflicts, skip to Step 4.

### 3. Resolve Conflicts

While the merge/rebase is paused due to conflicts:

1. Run `git diff --name-only --diff-filter=U` to list all conflicted files.
2. For **each** conflicted file:
   a. **Read** the file to understand the conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`).
   b. **Read** the surrounding code context and the intent of both sides (ours = current branch, theirs = trunk).
   c. Resolve the conflict by choosing the correct merge of both sides. Prefer preserving both sides' intent; if in doubt, ask the user via AskUserQuestion.
   d. After editing, stage the resolved file: `git add <file>`.
3. Continue the merge/rebase:
   ```bash
   git merge --continue
   ```
   ```bash
   git rebase --continue
   ```
4. If further conflicts arise, repeat from step 3.1.

**Rules for conflict resolution:**
- Never blindly accept "ours" or "theirs" — always read and understand both sides.
- If a conflict involves non-trivial logic changes from both sides, **ask the user** before resolving.
- Ensure no conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`) remain in any file after resolution.

### 4. Verify — Run Tests until Green

**`--skip-test` short-circuit.** If `--skip-test` was passed, skip this entire step — do **not** run `{test_cmd}`, and proceed straight to Step 5 (Format). Print a prominent banner so the skip is never silent, e.g.:

```
⚠️  --skip-test: test verification (Step 4) was SKIPPED — no tests were run. Verify manually before relying on this branch.
```

Otherwise, run the gate as normal:

Run `{test_cmd}` to verify the test suite passes after the merge/rebase. Fix any rebase-related test failures before continuing.

Note: on platforms where `{test_cmd}` is itself a slash command (e.g. Flutter's `/check-test --all --fix`), invoke it as a slash command. On platforms where it's a raw shell command (e.g. Android's `./gradlew testDebugUnitTest`), run it via Bash.

**Per-repo test profile.** When `{test_cmd}` is a slash command
(`/check-test`), the android variant override (`test_task` / `test_variant`)
and the `known_flaky_tests` quarantine are resolved inside it (its Step 0.3) —
nothing extra to do. When `{test_cmd}` is a RAW gradle command, resolve the
override yourself so the gate is not deaf to it, and apply the same exact-match
flake partition before judging green/red:

```bash
source "$HOME/.claude/lib/dev-mode.sh"
TEST_TASK=$(resolved_android_test_task "$(git rev-parse --show-toplevel)")
# substitute $TEST_TASK for the gradle task in {test_cmd} (the repo may have
# no testDebugUnitTest task — e.g. gogovan-client-v2-android needs
# testStandardStagingUnitTest), then partition failures against
# known_flaky_tests: a run whose only failures are known flakes is GREEN.
```

This is the fix the ticket calls out: the rebase tests-green gate must pick up
the same resolved command + flake quarantine, or the android rebase lane stays
permanently red on the known environment-flaky tests. Every
suppression is printed verbatim in the banner.

### 5. Format

Run `{format_cmd}` to apply formatter and lint fixes — without committing.

Same note as Step 4: invoke as slash command when it starts with `/`, otherwise run via Bash.

### 6. Commit

Invoke `/commit` to create atomic, well-scoped commits for all changes (including formatting fixes).

If `/commit` is not available in this project, fall back to creating a single conventional-commit message that summarizes the merge/rebase and any fix-ups.

### 7. Conflict Summary Report

After all conflicts are resolved and commits are done, produce a summary table of every conflict that was resolved during the merge/rebase. Track this information as you work through Step 3.

Format:

```
## Conflict Resolution Summary

| # | File | Conflict Area | Ours (branch) | Theirs (trunk) | Resolution |
|---|------|--------------|----------------|----------------|------------|
| 1 | lib/features/auth/login.dart | import block | added `package:foo` | added `package:bar` | kept both imports |
| 2 | lib/core/api/client.dart | `fetchData()` method | changed return type to `Future<Result>` | added retry logic | merged both: kept new return type + retry logic |
| ... | ... | ... | ... | ... | ... |
```

- **File**: the conflicted file path
- **Conflict Area**: which section/function/block had the conflict
- **Ours (branch)**: what the current branch changed
- **Theirs (trunk)**: what trunk changed
- **Resolution**: how you resolved it (kept both, chose ours, chose theirs, merged manually, etc.)

If no conflicts occurred, report: "No conflicts — merge/rebase was clean."

### 8. Done — Do NOT Push

Report the result:
- Show the Conflict Summary from Step 7.
- Show `git log --oneline -10` so the user can review.
- Remind the user that changes have **not** been pushed.
- If the user wants to push, they should do so manually.

---

## Rules

- **Single mode: never force-push** or run `git push` automatically.
- **Single mode: never run `git rebase --abort`, `git merge --abort` nor `git cherry-pick --abort`** without asking the user first.
- If the merge/rebase hits more than 5 conflict rounds (single mode), pause and ask the user if they want to continue or abort.
- Do not modify files unrelated to the conflict resolution or test fixes.
- All conflict markers must be verified removed before continuing the merge/rebase.
- **Batch mode push policy:** push a PR branch **only** when it came through **clean** — the rebase/merge applied with **zero conflicts** and `{test_cmd}` is green. PRs that hit conflicts, or whose tests failed, are **never** pushed; they are flagged for human review. **With `--skip-test`:** the tests-green half of the criterion is dropped — a cleanly-rebased PR is pushed on the strength of the clean merge/rebase alone (no tests run). Such pushes are surfaced as outcome `pushed (UNTESTED)` in the summary so the skip is never silent. Conflicted PRs are still never pushed.
- **Batch mode conflict handling:** the parallel subagents are non-interactive, so they do **not** attempt HITL conflict resolution. On the first conflict a subagent **aborts** its own merge/rebase (`--abort`) to leave the PR's worktree clean, then reports `conflicts`. This per-worktree auto-abort is the one sanctioned exception to the "never abort without asking" rule — it touches only that PR's isolated worktree, never the user's main worktree. Conflicted PRs are routed back to single-mode `/resolve-conflict` for a human.

---

## Batch Mode (`--batch`)

When `--batch` is present, **do not** run the single-branch flow against the current branch. Instead the **orchestrator** (this session) sweeps open GitHub PRs and fans out **one subagent per PR, in parallel**. Each subagent does its work inside that PR's own git worktree, so the orchestrator never switches the current branch and the subagents never collide (distinct branches → distinct worktrees).

### Step B0: Pre-flight (orchestrator)

1. Resolve the project profile exactly as in **Step 0** — capture `{test_cmd}` to hand to each subagent (unless `--skip-test` was passed, in which case no test command is needed — see below). **Dry-run:** skip — no tests are run. **`--skip-test`:** remember it and pass it to every subagent; they will skip their test run and push clean PRs untested.
2. Resolve the repo: `REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)`. If this fails (not a GitHub-backed repo), stop and tell the user.
3. Capture the repo root for worktree paths: `ROOT=$(git rev-parse --show-toplevel)`.

> No `/check-clean` and no branch-restore are needed: the orchestrator stays put on the current branch and never checks anything out — all mutation happens inside per-PR worktrees.

### Step B1: List & filter PRs

```bash
gh pr list --repo "$REPO" --state open \
  --json number,headRefName,baseRefName,author,isDraft,title,mergeable,mergeStateStatus,updatedAt \
  --limit 100
```

`mergeable` is `MERGEABLE` / `CONFLICTING` / `UNKNOWN`; `mergeStateStatus` is `CLEAN` / `BEHIND` / `DIRTY` / `BLOCKED` / `UNSTABLE` / `UNKNOWN`. Carry these along as a coarse conflict hint in the summary, but the authoritative conflict check is the local `git merge-tree` probe (used by `--dry-run` in Step B3, and the real rebase/merge in the live run).

Filter the list:

- **`--user=<login>` (created-by).** If provided, keep only PRs whose `author.login` matches `<login>` case-insensitively. `--user=@me` → resolve the login first via `gh api user --jq .login` and match that. If `--user` is omitted, keep all PRs.
- **Drop bots.** Always drop PRs whose `author.login` is `dependabot`, `renovate`, `github-actions`, or ends in `[bot]` (consistent with `/code-review`).

If the filtered list is empty, print `No matching open PRs in $REPO.` and stop.

### Step B2: Pre-filter behind PRs (orchestrator)

To avoid spawning a subagent for a PR that is already current, the orchestrator does a cheap read-only behind-check per surviving PR (updates only remote-tracking refs — no working-tree/branch mutation):

```bash
git fetch origin <baseRefName> <headRefName>
BEHIND=$(git rev-list --count "origin/<headRefName>..origin/<baseRefName>")
```

- `BEHIND == 0` → already up to date → **skip**, mark `up-to-date`. No subagent spawned.
- `BEHIND > 0` → **queue** the PR for the fan-out in Step B3.

> Note on fork PRs: `headRefName` may not exist on `origin`. If the fetch of `<headRefName>` fails, do not pre-filter — queue the PR and let its subagent resolve the head via `gh pr checkout` (it handles forks).

> **Dry-run:** still run the read-only `git fetch` + `git rev-list` to get `BEHIND`. The mergeability check is done in Step B3 with `git merge-tree`; **no subagents are spawned in dry-run**.

### Step B3: Fan out one subagent per PR (parallel)

> **Dry-run short-circuit.** If `--dry-run` was passed, do **not** spawn subagents, check out, run a real merge/rebase, resolve, test, commit, or push anything. Instead probe each PR's mergeability locally and print the read-only preview table below, then stop. Nothing is mutated.
>
> For each PR, run an **in-memory merge** of base and head — no checkout, no working-tree/index/branch change:
>
> ```bash
> # git 2.38+: --write-tree writes only loose objects to the object store
> git merge-tree --write-tree --name-only "origin/<baseRefName>" "origin/<headRefName>"
> # exit 0 → clean (no conflicts); exit 1 → conflicts (stdout lists the conflicted paths)
> ```
>
> If `git merge-tree --write-tree` is unavailable (git < 2.38), fall back to the legacy form and grep its output for conflict markers (`<<<<<<<`):
>
> ```bash
> git merge-tree "$(git merge-base origin/<baseRefName> origin/<headRefName>)" \
>   origin/<baseRefName> origin/<headRefName> | grep -q '^<<<<<<<' && echo CONFLICTS || echo CLEAN
> ```
>
> ```
> ## Batch Dry-Run — open PRs in <owner/repo>
>
> Strategy if run: <rebase|merge>   Filter: <--user value or "all (bots excluded)">
>
> | # | PR | Branch ← Base | Behind | git merge-tree | Conflicted files | Would do |
> |---|----|--------------|--------|----------------|-------------------|----------|
> | 1 | #123 Add foo | feat/foo ← main | 7 | conflicts | lib/a.dart, lib/b.dart | flag for human — no auto-resolve |
> | 2 | #124 Fix bar | fix/bar ← trunk | 5 | clean | — | merge + test, push if green (--force-with-lease) |
> | 3 | #125 Tidy    | tidy ← main | 0 | — | — | skip (already current) |
> ```
>
> The **Would do** column follows directly from the probe: `behind 0` ⇒ skip; clean merge-tree ⇒ would merge, test, and push if green; conflicts ⇒ would abort and flag for a human (the live run does not auto-resolve in batch). List the conflicted paths so the user sees the blast radius before committing to a live run.
>
> Caveat to note in the output: `git merge-tree` models a **merge**; with `--rebase` the exact conflict set can differ, but it is a reliable mergeability signal.

**Spawn one subagent per queued PR, in parallel**, passing each the PR number, `headRefName`, `baseRefName`, the strategy (`rebase`/`merge`, default rebase), `{test_cmd}`, whether `--skip-test` is set, `REPO`, and `ROOT`. Each subagent is self-contained and **must return a structured result** (outcome + details) for the summary.

**Concurrency.** Process **all** queued PRs, but run at most `--limit=<N>` subagents at a time (default: no explicit cap — issue all `Agent` calls in one message and let the harness's own cap queue the overflow). When `--limit` is set, dispatch in **waves**: issue N `Agent` calls in a single message, wait for that wave to finish, then issue the next N, until every queued PR has been processed. Never drop a PR for being over the limit — the cap throttles *how many run at once*, not *how many run*.

Give each subagent these instructions:

1. **Find or create the PR's worktree.**
   - List worktrees: `git -C "$ROOT" worktree list --porcelain`. If an entry's `branch` is `refs/heads/<headRefName>`, use that worktree's path. Work there.
   - Otherwise create one. Derive a path from the ticket id in the branch (`[A-Z]+-[0-9]+`, e.g. `<PREFIX>-<n>`) → `<ROOT>/../<TICKET-ID>`; fall back to a sanitized branch name if there is no ticket id. Then:
     ```bash
     git -C "$ROOT" fetch origin <headRefName> <baseRefName>
     git -C "$ROOT" worktree add <path> <headRefName>   # creates a local branch tracking origin/<headRefName>
     ```
     For a **fork** PR (head not on `origin`), instead `git worktree add <path> --detach` then `gh pr checkout <number> --repo "$REPO"` inside `<path>`.
   - If the chosen worktree has **uncommitted changes** (`git -C <path> status --porcelain` non-empty), do not touch it — return `worktree-dirty` and stop.
2. **Perform the merge/rebase** inside the worktree, against `origin/<baseRefName>`, using the passed strategy:
   ```bash
   git -C <path> fetch origin <baseRefName>
   git -C <path> <rebase|merge> origin/<baseRefName>
   ```
3. **On conflict:** do **not** attempt to resolve (non-interactive). Abort to leave the worktree clean and return `conflicts` with the conflicted file list (`git -C <path> diff --name-only --diff-filter=U` captured before aborting):
   ```bash
   git -C <path> <rebase|merge> --abort
   ```
4. **On clean merge:** verify, then push.
   - **`--skip-test` set** → do **not** run `{test_cmd}`. A clean merge/rebase alone satisfies the (relaxed) push criterion, so push immediately using the strategy-appropriate command below and return `pushed-untested`. The green requirement is deliberately dropped — this is the batch consequence of `--skip-test`.
   - **`--skip-test` NOT set** → run `{test_cmd}` in the worktree.
     - Tests **red** → return `tests-failed` (leave the worktree as-is for inspection; do not push).
     - Tests **green** → push (clean + green is the push criterion) and return `pushed`.
   - Push commands (both the `pushed` and `pushed-untested` paths):
     - **rebase** → `git -C <path> push --force-with-lease origin <headRefName>` (history rewritten; `--force-with-lease`, never `--force`).
     - **merge** → `git -C <path> push origin <headRefName>` (fast-forward).
     - On push failure, return `push-failed` with the error.
5. Return a structured result: `{ pr, branch, base, outcome, conflicted_files?, test_summary?, skipped_test?: bool, push_error?, worktree_path, worktree_created: bool }`.

**Failure isolation:** a subagent that errors or returns a failure outcome must not affect the others — the orchestrator records its result and moves on.

### Step B4: Collect & report (orchestrator)

Wait for all subagents to finish, then print the **Batch Summary** table:

```
## Batch Summary

Repo: <owner/repo>   Strategy: <rebase|merge>   Filter: <--user value or "all (bots excluded)">

| # | PR | Branch ← Base | Behind | Outcome | Pushed | Worktree | Notes |
|---|----|--------------|--------|---------|--------|----------|-------|
| 1 | #123 Add foo | feat/foo ← main | 7 | conflicts | no | ../<PREFIX>-<n> (reused) | 3 files conflict — run /resolve-conflict there |
| 2 | #124 Fix bar | fix/bar ← trunk | 5 | pushed | yes | ../<PREFIX>-<n> (created) | clean + tests green, force-with-lease |
| 3 | #125 Forky    | pr-125 ← master | 4 | tests-failed | no | ../pr-125 (created) | 2 tests red — left as-is |
| 4 | #126 Tidy     | tidy ← main | 0 | up-to-date | — | — | skipped (orchestrator pre-filter) |
```

Outcome values: `pushed`, `pushed-untested` (only under `--skip-test`), `tests-failed`, `conflicts`, `worktree-dirty`, `up-to-date`, `push-failed`, `error`. Render `pushed-untested` in the Outcome column as **`pushed (UNTESTED)`** so the skipped gate is visible at a glance.

Then print:
- **Pushed:** branches now current with their base on the remote (outcome `pushed`). Under `--skip-test`, list `pushed-untested` PRs in a **separate `⚠️ Pushed UNTESTED`** group with a one-line warning that no tests were run for them and CI is the only remaining gate.
- **Needs a human:** `conflicts` / `tests-failed` / `worktree-dirty` PRs, with the worktree path and the suggested next step — for conflicts, `cd <path> && /resolve-conflict` (single mode) to resolve interactively.
- **Worktrees created:** list the worktrees this run created (vs reused) so the user can `/remove-worktree` them when done.

---

## Callee Mode (`--callee`) — hybrid invoked by `/ggx-pr-resolver`

**Not a user-facing mode.** This is the callee-side contract for `/ggx-pr-resolver` step 5, which needs conflict-resolution mechanics that neither single nor batch mode provides as-is: single mode is HITL (it asks via `AskUserQuestion` on doubt — Steps 3.2c, 3.7, and the >5-rounds rule — which would deadlock the resolver's unattended background subagent); batch mode is non-interactive but **gives up on the first conflict** (auto-aborts, flags for a human) and owns its own worktree creation and **pushes**. Callee mode is the missing hybrid — **worktree-scoped + rebase-only onto an explicit base + non-interactive auto-assume conflict resolution (with auto-abort as the fallback tier) + NO push** — borrowing the conflict-handling mechanics of single mode (read both sides, merge preserving intent) by reference rather than re-stating them, but replacing its HITL give-up with a documented best-effort assumption so a determinable merge is actually resolved unattended rather than kicked back to a human.

**Inputs (all required, passed by the caller — this mode resolves nothing itself):**

- `--worktree=<path>` — the PR's worktree, **already created/reused/refreshed by the caller** (`/ggx-pr-resolver` step 4 owns the worktree primitive, the stale-reuse `git fetch` + reset, and the dirty guard). Callee mode does **not** create, fetch, reset, or clean the worktree; it operates strictly inside `<path>` (all git commands run with `git -C <path>`).
- `--base=<ref>` — the **explicit** ref to rebase onto (the PR's real base; see the Callee-mode line under **Base branch** above). Substitute the bare ref for the `git fetch` refspec.

Strategy is **rebase-only** (owner decision D12) — `--merge` is rejected in this mode; the caller's single push is unconditionally `--force-with-lease`, which is correct only for a rebase. **`--skip-test` is also rejected in this mode:** the Step 3 tests-green run below is the safety net that catches a Tier-1 auto-assume merge that turned out wrong, so it must never be bypassable — if `--skip-test` is somehow passed here, ignore it and run tests anyway. Resolve project profile exactly as in **Step 0** to obtain `{test_cmd}` / `{format_cmd}`.

### Procedure

1. **Rebase onto the explicit base** inside the worktree (the caller has already fetched `<base>`; re-fetching it is harmless and keeps the ref current):
   ```bash
   git -C <path> fetch origin <base-ref-name>
   git -C <path> rebase <base>
   ```
   Clean rebase (no conflicts) → go to step 3.
2. **On conflict — auto-assume resolution by DEFAULT (two tiers).** Apply the conflict-resolution logic of **Step 3** (read each conflicted file, understand both sides, merge preserving both intents) — with one overriding rule that **supersedes** Step 3.2c, Step 3's "ask the user" rule, the Rules-section "never `--abort` without asking" rule, and the >5-rounds rule **for this mode only**: this is an unattended background subagent, so it **NEVER** calls `AskUserQuestion`. There is **no opt-in flag** — auto-assume is the default behaviour; the conservative auto-abort survives only as Tier 2.

   **Tier 1 — auto-assume (default).** For a non-trivial conflict, do **not** give up at the first sign of uncertainty. Instead make a **reasonable, explicitly-stated assumption** about the merge intent and proceed. Default heuristics, in priority order:
   1. **Prefer the project's mandated direction.** If the conflict is between an old construct and a project-mandated replacement (e.g. trunk migrated `showArInfoDialog` → the unified `showAppDialog`/`AppDialog` enforced by the `dialog-usage` rule, while the branch still used a raw `AlertDialog`), keep the **mandated/base** side as the structural base. Lint rules, `CLAUDE.md` conventions, and deprecation comments are the signals here.
   2. **Graft the branch's net-new contribution on top of the base side.** The branch's new params, copy, design tweaks, and behaviour are the PR's actual intent — carry them over onto the chosen base (e.g. graft the branch's new `confirmLabel` into `AppDialogAction.label` and apply its copy/design changes on top of the `AppDialog` base).
   3. **Lean on the ticket as the source of intent.** Resolve `<TICKET-ID>` from the worktree branch name (`git -C <path> branch --show-current | grep -oE '[A-Z]+-[0-9]+'`); when a ticket is resolvable, its description is the authoritative statement of what the branch is trying to achieve — use it to disambiguate which side's intent to preserve.

   Record **every** assumption you make as you resolve (one entry per conflicted region): the file, the conflicting constructs (ours/base), the assumption applied, and which heuristic drove it. This ledger is returned in the Step 6 report (and posted by `/ggx-pr-resolver` to the PR's tracker ticket). After resolving all conflicts, `git add` each and `git rebase --continue`; if further conflicts arise, repeat Tier 1.

   **Tier 2 — auto-abort (fallback only).** Fall back to the conservative give-up **only** when the conflict is **genuinely ambiguous with no defensible default** — i.e. both sides make incompatible logic changes to the same region, no project rule / ticket intent / net-new-graft heuristic picks a winner, and a guess would be a coin-flip that could silently ship wrong behaviour. In that case, capture the conflicted file list (`git -C <path> diff --name-only --diff-filter=U`), then
   ```bash
   git -C <path> rebase --abort
   ```
   to restore the worktree to a clean pre-rebase state, and **report `needs-human: conflict`** (with the conflicted files) and STOP — push nothing, run no tests. This auto-abort is the same sanctioned exception batch mode relies on: it touches only the caller's isolated PR worktree, never the user's main worktree. Bias toward Tier 1 when a defensible assumption exists; reserve Tier 2 for true coin-flips (the push gate in Step 3 — tests green — is the safety net that catches a Tier-1 assumption that turns out wrong).
3. **Verify — tests green.** Run `{test_cmd}` in the worktree exactly as **Step 4**, **in the FOREGROUND, blocking until it exits**: never `run_in_background`, never arm a `Monitor`, never yield to wait for a notification. This is an unattended LINEAR subagent — a backgrounded suite parks it ("waiting for the monitor") and orphans `flutter_tester`/`dart` processes (2026-06-08 batch: 4 such parks, ~3× token blow-up, 22 orphan processes). Since callee mode is **rebase-only onto `base`** (the branch's own diff is what changed, not trunk's), use the **strict-incremental** test path where the platform supports it — `/check-test --fix --no-escalate` (file-level on flutter, module-level on android; omit `--all`). `--no-escalate` is load-bearing here: a rebase that happens to touch a widely-used file must NOT pull the full suite onto the critical path (CI re-runs it on push anyway). On platforms whose `{test_cmd}` is a raw shell command rather than `/check-test`, run it as-is. **This is the safety net for a Tier-1 auto-assume merge:** a documented assumption that turns out wrong surfaces here as a red suite. Tests red after the rebase → report `needs-human: tests-failed` (leave the worktree as-is for inspection, including any Tier-1 assumptions made so the human sees what was attempted) and STOP — push nothing. The push gate is unchanged: tests must be green (plus Step 4 `{format_cmd}` clean) before the caller pushes; an auto-assume merge never bypasses it.
4. **Format.** Run `{format_cmd}` in the worktree exactly as **Step 5** (no commit yet).
5. **Commit.** Commit the rebase + fix-ups exactly as **Step 6** (`/commit`, or the conventional-commit fallback).
6. **Report — NO push.** Return a structured result and **do not push** (owner decision D14: the caller — `/ggx-pr-resolver` step 7, or resolve-pr-comments' push step when a comments stage follows — owns the single `--force-with-lease` push). Report shape:
   `{ worktree: <path>, base: <ref>, outcome: rebased | no-op | needs-human, needs_human?: conflict | tests-failed, conflicted_files?: [...], conflict_summary?: <Step 7 table>, assumptions?: [ { file, region, ours, base, assumption, heuristic } ] }` — where `rebased` means commits were applied, `no-op` means already current (clean rebase with nothing to replay). Include the **Step 7** conflict-resolution summary table when conflicts were resolved. **`assumptions`** is the Tier-1 ledger from Step 2 — every documented best-effort assumption made while auto-resolving (empty / omitted when the rebase was clean or hit no conflicts); the caller (`/ggx-pr-resolver` step 5→7) posts it to the PR's tracker ticket and carries it into its step-8 run report.
