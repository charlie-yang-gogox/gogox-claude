Perform a code review of:
- the current git branch (no args),
- a single remote PR by number / URL / branch name, or
- all open PRs in the current repo (`--batch`).

Then optionally post the result as a PR comment.

## Usage

Single-PR mode:

- `/code-review` — review the current git branch
- `/code-review 228` — review PR #228 remotely without checkout
- `/code-review https://github.com/gogovan/.../pull/228` — same, extracted from URL
- `/code-review feat/CAF-100` — review by branch name (looks up the PR)
- `/code-review --auto` — review the current branch inline (no `Agent` spawn). Required when this command runs inside a `/ggx-dispatcher`-spawned `general-purpose` subagent, where nested-Agent spawns are officially unsupported (see `ARCHITECTURE.md` "Nested-spawn constraint"). `/dev:review` passes `--auto` automatically. Can be combined with a PR number / branch name.

Batch mode (auto-scans all open PRs of the cwd repo, mirrors the `ca/da-flutter-code-review` claude.ai routines):

- `/code-review --batch` — review every reviewable open PR in the cwd repo
- `/code-review --batch --dry-run` — list candidates, do not review
- `/code-review --batch --force` — bypass the already-reviewed dedup check
- `/code-review --batch --limit=N` — cap the number of PRs reviewed this run
- `/code-review --batch --exclude-user=<user>` — skip all PRs authored by `<user>`. Accepts a comma-separated list (`--exclude-user=alice,bob`) to exclude several authors. Matched against `author.login` (GitHub username), case-insensitively.
- `/code-review --batch --exclude-draft` — skip PRs that are not ready for review (GitHub draft status). Default batch behavior **keeps** drafts; this flag drops them.

## Step 1: Parse argument and pick mode

If the first argument starts with `--batch`, go to **Batch mode**. Otherwise process it under **Single-PR mode**.

## Single-PR mode

1. Parse the argument (if provided). Check these in order — **first match wins**:
   1. If it looks like a number (e.g. `228`), treat it as a PR number.
   2. If it looks like a GitHub PR URL (contains `pull/`), extract the PR number from the URL path (the last numeric segment after `/pull/`).
   3. If it looks like a branch name, resolve it to a PR number:
      ```
      gh pr list --head "<branch_name>" --json number --jq '.[0].number'
      ```
      If the result is empty or null, stop and tell the user no PR was found for that branch.
   4. If no argument is provided, this is a local review of the current branch.

2. **Run the review (mode-conditional).**

   The review contract — Local mode (no PR number) vs Remote mode (PR number resolved) per `agents/dev/git-branch-code-reviewer.md` — runs differently depending on whether `--auto` was passed:

   | Mode | Execution path | Why |
   | -- | -- | -- |
   | default (no `--auto`) | spawn `git-branch-code-reviewer` via the `Agent` tool. If a PR number was resolved, pass `"Review PR #<number> remotely. Do NOT use local git branch — fetch all information via gh CLI."` in the prompt. If no PR number (local mode), use the existing prompt without a PR number. Wait for the agent's review output. | Main session has `Agent` available; isolating into an opus subagent keeps review-prompt context out of the orchestrator. |
   | `--auto` | run the review **inline** in the current session — no `Agent` spawn | `--auto` is set by `/dev:review` (auto-only stage) when the entire `/dev:ff` pipeline is running inside a `/ggx-dispatcher`-spawned `general-purpose` subagent. Nested-Agent spawns from a subagent are officially unsupported (see `ARCHITECTURE.md` "Nested-spawn constraint") — inlining is the only safe path. Same constraint as `/port:explore --auto`, `/port:synth --auto`, and `/dev:apply --auto`. |

   **`--auto` mode — inline execution.** Do NOT call the `Agent` tool. The current session executes the git-branch-code-reviewer contract directly:
   1. Read `agents/dev/git-branch-code-reviewer.md` (`<gogox-claude-repo>/agents/dev/git-branch-code-reviewer.md`) once. Treat its `## Modes` / `### Step 0` → `### Step 6` / Output format as binding.
   2. Resolve `{platform}` per Step 0 of the agent definition (`.gogox-claude.yaml` → `platform`; fall back to `~/.claude/commands/profiles/registry/`).
   3. Pick Local vs Remote mode by whether a PR number was resolved at step 1.
      - **Local mode**: `git diff trunk...HEAD`, then `git diff --name-only trunk...HEAD` and `git log --oneline trunk..HEAD` for changed files + commit list. Read source files for context via the `Read` tool.
      - **Remote mode**: `gh pr view <pr_number> --json headRefName,title,body,url`, then `git fetch origin <branch_name>` once, then `gh pr diff <pr_number>` (+ `--name-only`) and `gh pr view <pr_number> --json commits --jq '.commits[].messageHeadline'`. Read files via `git show origin/<branch_name>:<file_path>`.
   4. Extract the Linear ticket identifier (`[A-Z]+-\d+` from the branch name, uppercased). Fetch ticket title + description + comments via `mcp__claude_ai_Linear__get_issue` / `mcp__claude_ai_Linear__list_comments`.
   5. Review per `### Step 5: Review` of the agent definition (correctness vs ticket, OpenSpec coverage, tests, code issues, security, conventions, error handling — pick test vocabulary by `{platform}`).
   6. Emit the structured output verbatim per `### Step 6: Output` — `## Summary`, `## Overall Rating`, `## Critical Issues 🔴`, `## Improvements 🟡`, `## Minor Notes 🟢`, `## Positive Highlights ✅`. This is the same structure that step 3 below expects.
   7. Do NOT post a PR comment from inside the inline review — step 5 below owns the comment-posting decision (which is skipped in `--auto` anyway since there is no user to confirm).

3. Output the review result directly to the user.

4. Determine the PR for commenting:
   - If a PR number was already resolved from the argument, use it.
   - Otherwise, check if there is an open PR for the current branch:
     ```
     gh pr view --json number,url 2>/dev/null
     ```
   - If no PR exists, skip step 5.

5. Ask the user whether to post the review as a PR comment.
   - If yes, post the review content as a PR comment prefixed with `# Internal code review`:
     ```
     gh pr comment <pr_number> --body "# Internal code review\n\n<review content>"
     ```
   - If no, skip.
   - **`--auto` mode**: skip this step entirely. `/dev:review` consumes the review output from step 2 directly (via `claude-reports/<ticket-id>/code-review.md`); no PR-comment side-effect should happen here.

## Batch mode

Idempotency strategy: source of truth is the PR's last `# Internal code review` issue comment timestamp on GitHub — not a local SHA cache. This is the same dedup mechanism used by the `ca/da-flutter-code-review` claude.ai routines, so manual `/code-review --batch` runs and the scheduled routines do not duplicate each other's work.

### Step B0: Resolve repo + platform

```bash
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
WT=$(git rev-parse --show-toplevel)

# Platform comes from the repo profile, same lookup chain as other gogox-claude commands.
PLATFORM=""
if [ -f "$WT/.gogox-claude.yaml" ]; then
  PLATFORM=$(awk '/^platform:/ {print $2}' "$WT/.gogox-claude.yaml")
fi
if [ -z "$PLATFORM" ]; then
  PROFILE="$HOME/.claude/commands/profiles/registry/$(basename "$WT").yaml"
  [ -f "$PROFILE" ] && PLATFORM=$(awk '/^platform:/ {print $2}' "$PROFILE")
fi
PLATFORM=${PLATFORM:-generic}

TS=$(date -u +%Y%m%d-%H%M%S)
REPORT_DIR="claude-reports/code-review/batch-$TS"
mkdir -p "$REPORT_DIR"
```

If `REPO` cannot be resolved (not in a GitHub-backed repo), stop and tell the user.

### Step B1: List open PRs and filter bots

```bash
gh pr list --repo "$REPO" --state open \
  --json number,headRefName,updatedAt,author,isDraft,title \
  --limit 50
```

Drop PRs whose `author.login` is `dependabot`, `renovate`, `github-actions`, or ends in `[bot]`. **Drafts are kept by default** (same policy as the routines).

**`--exclude-draft` filter.** If `--exclude-draft` was passed, additionally drop every PR whose `isDraft` is `true` (already fetched in the `gh pr list` JSON above — no extra API call). Mark each dropped PR as `excluded-draft` in the Step B7 summary table. Apply this filter here, before CI/dedup checks, so draft PRs never trigger `gh pr checks` / `gh api` calls.

**`--exclude-user=<user>` filter.** If `--exclude-user=<value>` was passed, additionally drop every PR whose `author.login` matches any entry in `<value>`:

- Split `<value>` on commas into a list of usernames.
- Match against `author.login` **case-insensitively** (GitHub usernames are case-insensitive), trimming surrounding whitespace on each entry.
- Mark each dropped PR as `excluded-user` in the Step B7 summary table (distinct from `bot author`) so the user can see which PRs were skipped by the filter.

Apply this filter here, before CI/dedup checks, so excluded PRs never trigger `gh pr checks` / `gh api` calls.

If the filtered list is empty, print `No reviewable open PRs in $REPO.` and stop.

### Step B2: CI status per PR

For each PR:

```bash
gh pr checks <pr> --repo "$REPO" --json name,state \
  --jq '[.[].state] | if all(. == "SUCCESS") then "green"
                     elif any(. == "FAILURE") then "red"
                     else "pending" end'
```

- `red` → skip the PR, mark `ci-red` in the summary table
- `pending` → continue; the review body must note `CI Status: Pending`
- `green` → continue

### Step B3: Already-reviewed dedup (comment timestamp)

Skip this step entirely if `--force` was passed.

For each survivor:

```bash
LAST_REVIEW=$(gh api "repos/$REPO/issues/<pr>/comments" \
  --jq '[.[] | select(.body | startswith("# Internal code review"))] | sort_by(.created_at) | last | .created_at')

LATEST_COMMIT=$(gh pr view <pr> --repo "$REPO" \
  --json commits --jq '[.commits[].committedDate] | sort | last')
```

- `LAST_REVIEW` null/empty → needs review
- `LATEST_COMMIT > LAST_REVIEW` (string compare on ISO-8601 is correct) → needs review
- Otherwise → skip, mark `cached` in the summary table

Both endpoints use ISO-8601 UTC, so direct string comparison is safe.

### Step B4: Apply `--limit` and `--dry-run`

- `--limit=N`: truncate the needs-review list to the first N (preserve `updatedAt` order — most recently updated first).
- `--dry-run`: write the candidate list to `$REPORT_DIR/dry-run.md` with rows for each PR (number / title / CI / dedup decision) and stop **before** spawning any agent.

### Step B5: Fan out reviewer agents (concurrency = 3)

For each PR remaining, build the spawn prompt:

```
Review PR #<pr> remotely on repo <REPO>. Do NOT use local git branch — fetch all information via gh CLI.

<platform-specific-fragment>
```

Where `<platform-specific-fragment>` is selected by `$PLATFORM` from the table in **Platform-specific checks** below (omit the fragment if `PLATFORM=generic`).

Spawn `git-branch-code-reviewer` agents in **parallel batches of 3**: send a single assistant message containing up to 3 Agent tool blocks, wait for all 3 to return, then dispatch the next batch of up to 3, repeating until done. Capture each agent's structured review output keyed by PR number.

### Step B6: Post and archive each review

For each completed agent output (let `BODY` be the agent's full structured review):

1. Archive locally:
   ```bash
   printf '%s\n' "$BODY" > "$REPORT_DIR/PR-<pr>.md"
   ```

2. Post the review back to the PR as an **issue comment** (same channel the single-PR mode uses — this is what the dedup query in Step B3 searches):
   ```bash
   gh pr comment <pr> --repo "$REPO" --body "$(printf '# Internal code review\n\n%s' "$BODY")"
   ```

   Always post — `--batch` implies post. Do not prompt the user per PR.

3. Count `Critical Issues` entries in `BODY` (lines under `## Critical Issues 🔴` that are not literally `None.`). Store the count for the summary table.

If `gh pr comment` fails for a PR, log the error, leave the local archive in place, and continue with the next PR. Do not abort the whole batch on a single posting failure.

### Step B7: Write summary

Write `$REPORT_DIR/summary.md` with one row per PR seen in Step B1 (including those filtered/skipped):

```markdown
# Batch code review — <REPO> @ <TS>

Platform: <PLATFORM>
Mode: <force? / limit=N? / dry-run? / exclude-user=<value>? / exclude-draft? — note any applied flag>

| PR | Title | Ticket | CI | Status | Critical | Report |
| --- | --- | --- | --- | --- | --- | --- |
| 228 | feat: ...  | CAF-139 | green   | reviewed | 2 | [PR-228.md](PR-228.md) |
| 229 | fix: ...   | CAF-141 | pending | reviewed | 0 | [PR-229.md](PR-229.md) |
| 231 | chore: ... | —       | red     | skipped (ci-red)        | — | — |
| 232 | feat: ...  | DAF-22  | green   | skipped (already reviewed) | — | — |
| 240 | bump deps  | —       | green   | skipped (bot author)       | — | — |
| 241 | feat: ...  | CAF-150 | green   | skipped (excluded-user)    | — | — |
| 242 | wip: ...   | CAF-151 | green   | skipped (excluded-draft)   | — | — |
```

Ticket column: extract `[A-Z]+-\d+` from the PR title or `headRefName`; `—` if no match.

Then print to the user a one-screen summary:

```
Batch review complete — <REPO>
  Open PRs found:        N
  Bot filtered:          A
  Excluded by user:      X   (--exclude-user=<value>; omit this line if the flag was not passed)
  Excluded drafts:       Y   (--exclude-draft; omit this line if the flag was not passed)
  CI red skipped:        B
  Already reviewed:      C   (use --force to redo)
  Newly reviewed:        D
  Total Critical issues: E   (across D reviews)
  Report: <REPORT_DIR>/summary.md
```

If `--dry-run`, the printed line set is shortened to the candidate counts and the path to `dry-run.md`.

### Platform-specific checks

When `$PLATFORM` matches, append exactly the corresponding fragment to the agent spawn prompt in Step B5. If `PLATFORM=generic` (or unrecognized), omit the fragment.

#### flutter

```
Additionally check Flutter/Dart-specific items:
- Idiomatic Flutter/Dart patterns.
- Widget lifecycle: dispose controllers, subscriptions, streams in State.dispose().
- State management consistent with the repo's pattern (Bloc / Provider / Riverpod — see CLAUDE.md if present).
- No build-time side effects; long-running work belongs in state objects, not build().
- Null safety / late-init usage: avoid `!` and `late` where a nullable + null-check would be safer.
```

#### android

```
Additionally check Android / Kotlin-specific items:
- Coroutine scope management: no `GlobalScope`; structured concurrency tied to viewModelScope / lifecycleScope.
- Lifecycle awareness: no Activity/Fragment captured in long-lived references; ViewModel survives configuration change correctly.
- DI consistency: Hilt/Dagger modules follow the repo's existing pattern.
- Resource cleanup: Closeable, Cursor, FileInputStream, registered receivers / observers all released.
- Threading: no UI work on background threads, no blocking the main looper, no network on main thread.
```

#### ios

```
Additionally check iOS / Swift-specific items:
- Memory management: closures capture `[weak self]` where retain cycles are possible; delegates are `weak`.
- Threading: UI mutations on the main actor / main queue; no `DispatchQueue.main.sync` from main.
- Optional handling: avoid force unwraps (`!`, `try!`) unless an invariant clearly guarantees non-nil.
- Combine / async-await usage consistent with the repo's reactive pattern.
- Lifecycle cleanup: observers, KVO, NotificationCenter, Combine cancellables removed in `deinit` or `onDisappear`.
```

#### node

```
Additionally check Node / TypeScript-specific items:
- Async/await error handling: every async call path has a catch or a documented unhandled-rejection boundary.
- Type safety: no implicit `any`; explicit return types on exported functions; no `as` casts that lose information.
- Resource cleanup: DB connections, file handles, stream listeners released; no leaked timers.
- Input validation at API boundaries (zod / class-validator / manual guard) before any DB or external call.
- No synchronous filesystem / crypto / large-loop work on the request path.
```

#### generic

No fragment. The base agent checks (a)–(g) apply unchanged.
