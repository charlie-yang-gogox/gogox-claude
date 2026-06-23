---
name: start
description: "Stage 1 of the /dev:* atomic pipeline. Resolves the project profile, parses the ticket, runs pre-flight checks, optionally creates a worktree (auto mode), and assigns the ticket on the tracker (Linear or Jira). This stage no longer creates state.json — pipeline progress is derived from filesystem markers by /dev:ff's walker. Supports both Linear and Jira via the abstraction documented in `_ticket-lib.md`."
Prerequisite: >
  - Linear MCP authenticated for CAF/DAF tickets; Atlassian Rovo MCP
    authenticated for CET/DET tickets.
  - Default mode: already on the branch/worktree for the ticket. Git clean.
  - --auto mode: on trunk with no tracked modifications (untracked files are
    tolerated — the worker branches its worktree off origin/<default> and never
    touches the main worktree). gh CLI authenticated.
    Environment variables USER_NAME and GH_USER_NAME set.
---

# `/dev:start <ticket-id> [--auto] [--no-figma] [--bug]`

Prepares the working environment for the dev loop. The done marker for this stage is the worktree (auto) or the on-branch + openspec change dir (default). No `.dev/state.json` is created — `infer_dev_stage` derives the next stage from filesystem.

## Inputs

- `<ticket-id>` — Linear ticket ID (e.g. `CAF-207`). Required.
- `--auto` — full autonomous pipeline.
- `--no-figma` — pre-declare that this ticket has no Figma source. Atomic-writes `.dev/figma-context.md` with first line `Fetched: SKIPPED — <reason>` so `/dev:figma` is skipped by the walker. **`/dev:start` is the SOLE writer of the SKIPPED first-line variant** (figma-subagent only writes `Fetched: <ISO>` or `Fetched: FAILED`).
- `--bug` — bug-fix mode. Skips `/dev:detect` / `/dev:align` (no OpenSpec change to align), and `/dev:apply` takes its Step 0-bug branch: the agent re-fetches the ticket, investigates the codebase, writes the fix, and commits — autonomously. The human is NOT asked to find root cause or write code. In `default` mode there is one HITL gate confirming the agent's fix plan; in `--auto` even that is skipped. Writes `.dev/mode.md` so downstream stages take the bug branch.
- `--no-ticket-init` — Skip the Linear ticket-init step (status → `In Progress`, drop `ready-to-dev` label, assignee → self, estimate=1, starting comment). Use when running the pipeline locally for inspection / debugging without flipping the ticket on Linear. Default: enabled (init runs in both default and `--auto` modes; the underlying `/_ticket-init` skill is idempotent).
- `--port-handoff` — port→need-spec-review→ready-to-dev handoff entry (GGC-56). Set by `/dev:ff --port-handoff` when adopting a committed `openspec/changes/<name>` that `/port:ship` produced. Adopts the existing port worktree/branch instead of creating a fresh one (Step 3), exempts the committed openspec change from the re-entry refusal (Step 2), and writes `.dev/mode.md` = `port-handoff` so `/dev:apply` rides the feature OpenSpec flow but can hard-fail if it ever runs without the spec-review directives this stage captures (Step 4c). Rides the feature flow — does NOT combine with `--bug`.
- Linear ticket (fetched).
- Project profile (`{platform}`, `{deps_install}`, `{test_cmd}`).

## Outputs

- Worktree at `../<ticket-id>` (auto only).
- `.dev/figma-context.md` with first line `Fetched: SKIPPED — <reason>` (when `--no-figma` OR ticket has no Figma URL after parsing).
- `.dev/spec-review-directives.md` — first line `Status: PRESENT` (latest `<!-- spec-review:v1 -->` Linear comment captured verbatim) or `Status: NONE` (no such comment). Always written. Consumed by `/dev:apply` Step 0-bug.1 and Step 4D.1 to surface `[REVISED]` directives to whichever agent authors code.
- `.dev/mode.md` with single line `bug` (when `--bug`), `port-handoff` (when `--port-handoff`), or `feature-direct` (when neither flag is set AND the worktree has no `openspec/` dir — GGC-76). Absent only for the OpenSpec `feature` path. `pipe_mode` (lib/dev-mode.sh) resolves the mode: a `feature-direct`-valued marker is NOT a positive `bug`/`port-handoff` match, so it falls through to the `! -d openspec` branch and still resolves `feature-direct`. `pipe_mode` also detects feature-direct dynamically for legacy/unmarked worktrees, so the marker is required only by the direct-mode walker's `start→apply` gate (`infer_bug_stage` in `/dev:ff`), never by `pipe_mode`.
- Linear ticket: assigned to self, status `In Progress`, `ready-to-dev` label removed, estimate=1 if null, starting comment posted (both modes; skipped only with `--no-ticket-init`). Driven by `/_ticket-init` (idempotent).
- `/tmp/<ticket-id>.md` — ticket dump (auto only).

## Step 0: Resolve project profile

1. If `<repo-root>/.gogox-claude.yaml` exists, read its `platform` and `product`. Else look up `basename "$(git rev-parse --show-toplevel)"` in `~/.claude/commands/profiles/repos.yaml`.
2. Read `~/.claude/commands/profiles/platform/{platform}.yaml` for `{deps_install}`, `{test_cmd}`, `{format_cmd}`.

## Step 1: Parse input

- Extract `<ticket-id>` from `$ARGUMENTS`. Detect `--auto`. Detect `--no-figma`. Detect `--bug`. Detect `--no-ticket-init`. Detect `--port-handoff` → `PORT_HANDOFF` (1/0).
- Missing ticket-id in `<auto-mode>` → STOP.
- Missing ticket-id in default mode → use **AskUserQuestion**. Stop if still missing.

## Step 2: Refuse re-entry

A pipeline is in flight in this worktree if any of these are true:

- `openspec/changes/` contains a non-archive change directory.
- `.dev/` contains any marker file (`.dev/figma-context.md`, `.dev/align-result.md`, `.dev/verify-pass.md`). Note: `.dev/spec-review-directives.md` is intentionally NOT in this list — it is written every run by Step 4c and would otherwise refuse re-entry on a re-run of `/dev:start`.

If in flight, STOP with: `Pipeline already in flight in this worktree. Resume with /dev:ff, or /dev:ff --from <stage> to reset.`

**Port-handoff exemption (GGC-56)**: when `PORT_HANDOFF == 1`, EXEMPT only the first bullet — a committed `openspec/changes/<name>` is the EXPECTED input of a port handoff (`/port:ship` produced it), not an in-flight dev pipeline, so its presence must NOT refuse re-entry. The second bullet (`.dev/` marker files) stays active: if a dev marker like `.dev/figma-context.md` or `.dev/verify-pass.md` already exists, a real dev pipeline is genuinely in flight and `/dev:start` must still refuse (resume via `/dev:ff` instead). In short: `--port-handoff` only waives the openspec-change-dir signal, never the dev-marker signal.

## Step 3: Pre-flight + ticket assignment

**Resolve `TICKET_SYSTEM`** via the `_ticket-lib.md` resolution flow first.
If `unknown` → STOP. For Jira, capture `JIRA_CLOUD_ID` and discover the
current user's `accountId` via `mcp__claude_ai_Atlassian_Rovo__atlassianUserInfo`.

**Ownership check** (both modes):

- **Linear**: `mcp__claude_ai_Linear__get_issue --id "$TICKET_ID"` →
  if `.assignee.id` (or `.assignee.name`) ≠ current user, STOP with
  `FAIL: ticket <ticket-id> is not assigned to you on Linear.`
- **Jira**: `mcp__claude_ai_Atlassian_Rovo__getJiraIssue --cloudId "$JIRA_CLOUD_ID" --issueIdOrKey "$TICKET_ID" --responseContentFormat markdown` →
  if `.fields.assignee.accountId` ≠ the current `accountId` from
  `atlassianUserInfo`, STOP with the equivalent Jira message.

`/_ticket-init` (Step 3c) handles the post-check assignment for both
trackers, so this gate is purely defensive: a ticket assigned to someone
else should not be claimed silently.

### Step 3a: Runtime artifact residue handling

Before the strict cleanliness check, scan for runtime artifacts left over by prior pipeline runs that may not have cleaned up. These are NOT real source modifications — they are observability / state files.

```bash
# Match any path under .dev/ (whole directory is gitignored runtime workspace)
RUNTIME_REGEX='(^|/)\.dev/'
PORCELAIN=$(git status --porcelain)
RUNTIME_DIRT=$(printf '%s\n' "$PORCELAIN" | grep -E "$RUNTIME_REGEX" || true)
OTHER_DIRT=$(printf '%s\n' "$PORCELAIN" | grep -vE "$RUNTIME_REGEX" || true)

# Split the non-.dev dirt into TRACKED modifications vs UNTRACKED files.
# Porcelain marks untracked entries with a leading `??`; everything else is a
# tracked modification (M/A/D/R/C, staged or unstaged). Only TRACKED
# modifications are a hard-STOP — they may be a human's in-progress work.
# UNTRACKED files are harmless to an auto run: the worker branches its
# worktree off origin/<default> and edits only in ../<TICKET-ID>, never the
# main worktree, so untracked files in main cannot be clobbered or carried in.
# This aligns /dev:start's clean-tree precondition with /ggx-dispatcher
# Step 1.3's policy ("Untracked files warn but proceed — agents work in
# separate worktrees"); the two checks previously disagreed, which let a
# benign untracked file (e.g. claude-reports/, scripts/measure-ios-startup.sh)
# in main false-positive an auto worker's first stage (GGC-13).
OTHER_TRACKED_DIRT=$(printf '%s\n' "$OTHER_DIRT" | grep -vE '^\?\?' || true)
OTHER_UNTRACKED_DIRT=$(printf '%s\n' "$OTHER_DIRT" | grep -E '^\?\?' || true)

# Legacy .dev/state.json residue (one-time cleanup safety net for pipelines started under v7)
if [ -f .dev/state.json ]; then
  echo "INFO: legacy .dev/state.json detected; removing (filesystem-as-state model)" >&2
  rm -f .dev/state.json
fi
```

- If `$OTHER_TRACKED_DIRT` is non-empty: real source changes exist (a human's
  in-progress work); STOP with the standard "uncommitted changes" error
  regardless of residue. `/dev:start` never sweeps tracked edits into a stash
  the user didn't ask for.
- Else (no tracked modifications outside `.dev/`):
  - If `$OTHER_UNTRACKED_DIRT` is non-empty: untracked files in the main
    worktree — **warn and proceed**, never abort. Mirror the dispatcher's
    Step 1.3 note:
    > `note: <N> untracked file(s) present outside .dev/ — proceeding (worker branches its worktree off origin/<default>; main-worktree untracked files are never touched).`
    Do NOT remove them (they may be a human's scratch files, and the worker
    cannot be harmed by them either way).
  - If `$RUNTIME_DIRT` is non-empty: pure leftover `.dev/` residue.
    - **Auto mode**: log the list, then `git checkout -- <files>` for
      tracked-and-modified entries and `rm -f <files>` for untracked ones. Proceed.
    - **Default mode**: list the residue files. **AskUserQuestion** with options:
      - `Discard residue and continue` (default) — same cleanup as auto.
      - `Inspect first (abort)` — STOP so user can review manually.

### Step 3b: Mode-specific pre-flight

**Auto mode**:

1. Verify the repo is on its default branch AND has no **tracked** modifications. If not → STOP. (Resolve the default branch dynamically: `source "$HOME/.claude/lib/dev-mode.sh"; default_branch` — `trunk` on flutter, `main` on gogox-claude.) This is the tracked-only re-check that pairs with Step 3a: untracked files were already triaged there as warn-and-proceed, so test only tracked dirt here — `git status --porcelain --untracked-files=no` (empty ⇒ clean) — never the whole porcelain. Counting untracked files here would re-introduce the GGC-13 false-positive that Step 3a just resolved.
2. Read the ticket to determine branch type (`feat`, `fix`, `test`, `ci`, `chore`):
   - **Linear**: `mcp__claude_ai_Linear__get_issue` (already done in ownership check; reuse the snapshot). Branch type heuristic: `bug` label → `fix`; otherwise default to `feat`.
   - **Jira**: `mcp__claude_ai_Atlassian_Rovo__getJiraIssue` (already done; reuse). Branch type heuristic: `.fields.issuetype.name == "Bug"` → `fix`; otherwise `feat`. The `--bug` flag (when set by `/bug:ff`) is the authoritative override in both trackers.
3. **Worktree.** Normal path: invoke `/add-worktree <ticket-id> --type <type>` — handles fetch, branch, EnterWorktree, port-settings, `{deps_install}`.

   **Port-handoff path (GGC-56)**: when `PORT_HANDOFF == 1`, do NOT `/add-worktree` — the port pipeline already created the worktree/branch that holds the committed openspec change. Reuse it instead of branching fresh (a fresh branch off `origin/<default>` would not contain the ported change):

   ```bash
   ticket_lc=$(echo "$TICKET_ID" | tr '[:upper:]' '[:lower:]')
   cwd_base=$(basename "$PWD" | tr '[:upper:]' '[:lower:]')
   branch="<the port worktree's branch, e.g. feat/$TICKET_ID — resolve from worktree list below>"

   if [ "$cwd_base" = "$ticket_lc" ]; then
     : # already inside ../<ticket-id> — no-op, we are in the port worktree.
   elif git worktree list --porcelain \
          | awk -v t="$ticket_lc" '/^worktree / && tolower($2) ~ t"$" {found=1} END{exit !found}'; then
     # Existing worktree at ../<ticket-id> → enter it (EnterWorktree).
     wt=$(git worktree list --porcelain \
            | awk -v t="$ticket_lc" '/^worktree / && tolower($2) ~ t"$" {print $2; exit}')
     # EnterWorktree "$wt"
   else
     # No worktree yet, but /port:ship pushed the branch. Create a worktree that
     # CHECKS OUT THE EXISTING branch (never `-b` a new one — that would lose the
     # ported change). Resolve the branch: prefer a local branch containing the
     # ticket id, else origin/<branch>.
     existing_branch=$(git for-each-ref --format='%(refname:short)' refs/heads \
                         | grep -iE "/${ticket_lc}$|${TICKET_ID}" | head -1)
     [ -z "$existing_branch" ] && existing_branch=$(git for-each-ref --format='%(refname:short)' refs/remotes/origin \
                         | grep -iE "/${ticket_lc}$|${TICKET_ID}" | head -1 | sed 's|^origin/||')
     if [ -n "$existing_branch" ]; then
       git fetch origin "$existing_branch" 2>/dev/null || true
       git worktree add "../$ticket_lc" "$existing_branch"
       # EnterWorktree "../$ticket_lc"
     else
       echo "abort: --port-handoff but no port worktree/branch for $TICKET_ID found" >&2
       exit 1
     fi
   fi
   ```

4. Write the full ticket content to `/tmp/<ticket-id>.md` (whatever tracker returned).

**Default mode**:

1. Run `/check-clean`. Stop if not clean.
2. Check current branch contains `<ticket-id>` (case-insensitive). If not, **AskUserQuestion** to confirm; stop on No.

### Step 3c: Linear ticket-init (both modes)

<!-- SYNC: ticket-init lives in commands/dev/_ticket-init.md. The 4 callers
     (port:start Step 5a, dev:start Step 3c, ggx-dispatcher Step 4.1,
     ggx-work Step 2.5) all invoke it; do not re-inline the block here. -->

Unless `--no-ticket-init` was passed, invoke `/_ticket-init <ticket-id> dev` (idempotent; safe to re-call). This runs in both auto and default modes so the ticket transitions to `In Progress`, drops `ready-to-dev`, assigns to self, sets estimate=1 if null, and posts a starting comment. The skill short-circuits each write on a skip condition so dispatcher-spawned chains (`/ggx-dispatcher` §4.1 → `/ggx-work` Step 2.5 → `/dev:start` Step 3c) collapse to one effective init.

When `--no-ticket-init` is set, log a single line `ticket-init: skipped (--no-ticket-init)` and continue.

## Step 4: Figma SKIPPED first line (when no source)

```bash
mkdir -p .dev
# system-aware fetch — Linear vs Jira (see _ticket-lib.md "Field mapping").
# COMMENTS_FETCH_OK distinguishes "fetched, genuinely no Figma URL" from
# "comments fetch failed" — only a CONFIRMED-empty scan may write the
# SKIPPED terminal state. SKIPPED is never retried downstream, so writing
# it off a transient MCP failure silently strips the design context from
# the whole pipeline (apply/align/verify all build without it).
COMMENTS_FETCH_OK=1
if [ "$TICKET_SYSTEM" = "linear" ]; then
  # Body fetch failure is FATAL — abort like Step 2's description-fetch
  # failure path (no ticket content means nothing downstream can run).
  TICKET_BODY=$(mcp__claude_ai_Linear__get_issue --id "$TICKET_ID" | jq -r '.description // ""') \
    || { echo "abort: get_issue failed for $TICKET_ID" >&2; exit 1; }
  # Comments fetch failure is fail-soft: scan is inconclusive, see below.
  TICKET_COMMENTS=$(mcp__claude_ai_Linear__list_comments --issueId "$TICKET_ID" | jq -r '.comments[].body // ""' | tr '\n' ' ') \
    || { COMMENTS_FETCH_OK=0; TICKET_COMMENTS=""; }
else  # jira — single call returns body + comments; failure is fatal (no body either)
  JIRA_ISSUE=$(mcp__claude_ai_Atlassian_Rovo__getJiraIssue \
                 --cloudId "$JIRA_CLOUD_ID" --issueIdOrKey "$TICKET_ID" \
                 --responseContentFormat markdown) \
    || { echo "abort: getJiraIssue failed for $TICKET_ID" >&2; exit 1; }
  TICKET_BODY=$(jq -r '.fields.description // ""' <<<"$JIRA_ISSUE")
  TICKET_COMMENTS=$(jq -r '.fields.comment.comments[]?.body // ""' <<<"$JIRA_ISSUE" | tr '\n' ' ')
fi

# Concatenate every comment body into one stream so the same regex catches
# Figma URLs whether they were placed in the description or added later via
# a comment. This is the move that retired the dispatcher's pre-detection
# of `--no-figma`: /dev:start is now authoritative.

HAS_FIGMA_URL=$(printf '%s\n%s\n' "$TICKET_BODY" "$TICKET_COMMENTS" \
  | grep -cE 'figma\.com/(design|file|board|slides|make)/')
NO_FIGMA_FLAG=$(echo "$ARGUMENTS" | grep -q -- '--no-figma' && echo 1 || echo 0)

if [ "$NO_FIGMA_FLAG" = "1" ]; then
  # Explicit human override — always honored, comments scan irrelevant.
  printf 'Fetched: SKIPPED — %s\n' "--no-figma flag at /dev:start" > .dev/figma-context.md.tmp
  mv .dev/figma-context.md.tmp .dev/figma-context.md   # atomic
elif [ "$HAS_FIGMA_URL" -eq 0 ] && [ "$COMMENTS_FETCH_OK" = "1" ]; then
  # Both surfaces fetched and confirmed empty — safe to short-circuit.
  printf 'Fetched: SKIPPED — %s\n' "no Figma URL in ticket description or comments" > .dev/figma-context.md.tmp
  mv .dev/figma-context.md.tmp .dev/figma-context.md   # atomic
elif [ "$HAS_FIGMA_URL" -eq 0 ] && [ "$COMMENTS_FETCH_OK" = "0" ]; then
  # Inconclusive: description has no URL but the comments scan failed —
  # a comment-only Figma ticket would be indistinguishable from a no-Figma
  # ticket here. Write NO marker: the walker advances to the figma stage,
  # where figma-subagent re-fetches the ticket itself and either finds the
  # URL or fails loudly with `Fetched: FAILED` (visible, retryable) instead
  # of this stage failing silently (invisible, terminal).
  echo "warn: list_comments failed — cannot confirm Figma absence; deferring to figma-subagent" >&2
fi
# (HAS_FIGMA_URL > 0 needs no marker regardless of COMMENTS_FETCH_OK —
#  the URL was found, the pipeline proceeds to /dev:figma normally.)
```

`/dev:start` is the SOLE writer of the `Fetched: SKIPPED` first-line variant. figma-subagent only writes `Fetched: <ISO>` (success) or `Fetched: FAILED` (MCP fail). If the SKIPPED first line is missing on a no-Figma ticket, `infer_dev_stage` advances to `figma`; figma-subagent then receives an empty URL list and refuses with FAILED. Recovery: re-run `/dev:start`. The fail-soft branch above leans on exactly this recovery path on purpose: a transient comments-fetch failure costs one loud figma-stage retry, never a silent design-context drop.

**Comment scan is intentional.** Designers and reviewers frequently drop Figma links into a follow-up comment rather than editing the ticket description. Looking only at the description silently routed those tickets into the SKIPPED short-circuit, which then forced callers like `/ggx-dispatcher` to maintain a parallel `--no-figma` pre-detection. Scanning both surfaces here means `/dev:start` is the single authority on "does this ticket have Figma source?"; the explicit `--no-figma` flag is preserved as the manual override.

## Step 4b: Bug-mode marker (when --bug)

```bash
BUG_FLAG=$(echo "$ARGUMENTS" | grep -q -- '--bug' && echo 1 || echo 0)

if [ "$BUG_FLAG" = "1" ]; then
  mkdir -p .dev
  printf 'bug\n' > .dev/mode.md.tmp
  mv .dev/mode.md.tmp .dev/mode.md   # atomic
fi
```

`.dev/mode.md` presence with value `bug` is the canonical signal that downstream stages (`/dev:verify`, `/dev:ship`, `/dev:ff` walker) read to take the bug-mode branch. The OpenSpec `feature` path does NOT write this file — `pipe_mode` (lib/dev-mode.sh) resolves it as `feature` (repo has an `openspec/` dir). The `feature-direct` path (no `openspec/` dir — GGC-17) DOES write a marker as of GGC-76 (Step 4b-featuredirect below); see that step for why the marker is needed and why it is safe.

## Step 4b-port: Port-handoff marker (when --port-handoff)

```bash
if [ "$PORT_HANDOFF" = "1" ]; then
  mkdir -p .dev
  printf 'port-handoff\n' > .dev/mode.md.tmp
  mv .dev/mode.md.tmp .dev/mode.md   # atomic
fi
```

`.dev/mode.md` with value `port-handoff` (GGC-56) is the canonical signal that this dev run was reached via the port→need-spec-review→ready-to-dev handoff rather than a fresh scaffold. `pipe_mode` (lib/dev-mode.sh) returns `port-handoff`; the `/dev:apply` walker rides the FEATURE OpenSpec flow (Steps 1–5, normal figma/detect/align chain) for it, but `/dev:apply`'s feature spec-review consumers HARD-FAIL when this marker is present and `.dev/spec-review-directives.md` is absent — proof that `/dev:start` (and therefore Step 4c's directive capture) was skipped. Runs in both auto and default modes. Mutually exclusive with `--bug`; `/dev:ff` never passes both.

## Step 4b-featuredirect: Feature-direct marker (GGC-76)

```bash
# Runs only when NEITHER --bug NOR --port-handoff is set (those Steps already
# wrote .dev/mode.md and returned). A worktree with no `openspec/` dir is a
# feature-direct repo (the `prompt` platform — GGC-17). Write the marker so the
# direct-mode walker's start→apply gate can fire (see below).
if [ "$BUG_FLAG" != "1" ] && [ "$PORT_HANDOFF" != "1" ] && [ ! -d openspec ]; then
  mkdir -p .dev
  printf 'feature-direct\n' > .dev/mode.md.tmp
  mv .dev/mode.md.tmp .dev/mode.md   # atomic
fi
```

**Why this marker is needed (GGC-76).** `feature-direct` shares the bug-mode walker `infer_bug_stage` (in `/dev:ff`), whose `start→apply` gate is `[ -d .dev ] && [ -f .dev/mode.md ]`. Before GGC-76, `/dev:start` wrote `.dev/mode.md` only for `--bug`/`--port-handoff`, so on a feature-direct repo the gate never fired: after `/dev:start`, `infer_dev_stage` re-derived `start`, `/dev:ff` saw `NEW == CURRENT`, and the pipeline STOPped with no progress. Writing the marker here lets the gate advance to `apply`.

**Why it is safe for `pipe_mode`.** `pipe_mode` only treats `mode.md` values `bug` and `port-handoff` as positive matches; any other value (here, `feature-direct`) falls through to the `! -d openspec` branch and still resolves `feature-direct`. So the marker changes nothing for `pipe_mode` while unblocking the walker. `pipe_mode` also keeps its dynamic `! -d openspec` detection, so legacy/unmarked feature-direct worktrees (created before this fix) still resolve correctly. (Regression-guarded by `lib/dev-mode.test.sh`, run by `scripts/prompt-lint.sh`.)

## Step 4c: Capture spec-review directives (if any)

After `/spec-review` runs, the authoritative reviewer decisions live in a
**Linear comment** whose body starts with the marker
`<!-- spec-review:v1 ticket=$TICKET_ID -->` (see `commands/dev/spec-review.md`
§A — "Output comment schema"). The `need-spec-review` label has already
flipped to `ready-to-dev` by the time `/dev:ff` is dispatched, so label
probes alone cannot detect that revisions exist. This step captures the
latest such comment into `.dev/spec-review-directives.md` so every
downstream stage (`/dev:apply`, dev-agent, bug-mode agent) can honor
`[REVISED]` directives without re-fetching Linear comments.

**Jira short-circuit**: `/spec-review` is a port-pipeline concept, and
Jira repos have no port lane. For `TICKET_SYSTEM == jira`, always write
`Status: NONE` and skip the comment fetch entirely:

```bash
if [ "$TICKET_SYSTEM" = "jira" ]; then
  mkdir -p .dev
  {
    printf 'Status: NONE\n'
    printf 'Spec-review is Linear-only (port pipeline concept). Jira ticket — nothing to capture.\n'
  } > .dev/spec-review-directives.md.tmp
  mv .dev/spec-review-directives.md.tmp .dev/spec-review-directives.md
  # Skip the rest of Step 4c.
fi
```

For `TICKET_SYSTEM == linear`, run the original fetch:

This step is one-shot — `/dev:apply` only reads the file; it never re-fetches.

```bash
mkdir -p .dev
TICKET_COMMENTS=$(mcp__claude_ai_Linear__list_comments \
  --issueId "$TICKET_ID" --orderBy createdAt 2>/dev/null || true)

# Find the MOST RECENT comment whose body starts with the spec-review:v1
# marker for this exact ticket. orderBy=createdAt returns newest first; we
# take the first match. The marker is anchored at the start of the body
# per the spec-review schema.
SPEC_REVIEW_BODY=$(printf '%s' "$TICKET_COMMENTS" \
  | jq -r --arg t "$TICKET_ID" '
      [.comments[]?
        | select(.body
            | test("^<!-- spec-review:v1 ticket=" + $t + " -->"))]
      | sort_by(.createdAt) | reverse | .[0].body // empty')

if [ -n "$SPEC_REVIEW_BODY" ]; then
  # Detect at least one [REVISED] directive; CONFIRMED-only comments are
  # not load-bearing for downstream stages but we still record the body so
  # the agent can see "spec review ran, no revisions" rather than guess.
  REVISED_COUNT=$(printf '%s\n' "$SPEC_REVIEW_BODY" \
    | grep -cE '^### \[REVISED\] ' || true)
  {
    printf 'Status: PRESENT\n'
    printf 'Revised directives: %s\n' "${REVISED_COUNT:-0}"
    printf 'Source: Linear comment matching marker `<!-- spec-review:v1 ticket=%s -->` (most recent)\n' "$TICKET_ID"
    printf '\n## Raw comment body\n\n'
    printf '```markdown\n%s\n```\n' "$SPEC_REVIEW_BODY"
  } > .dev/spec-review-directives.md.tmp
  mv .dev/spec-review-directives.md.tmp .dev/spec-review-directives.md
else
  {
    printf 'Status: NONE\n'
    printf 'No comment matching marker `<!-- spec-review:v1 ticket=%s -->` was found at /dev:start time.\n' "$TICKET_ID"
  } > .dev/spec-review-directives.md.tmp
  mv .dev/spec-review-directives.md.tmp .dev/spec-review-directives.md
fi
```

Failure handling: if `list_comments` errors (network, permission), write
`Status: NONE` with a one-line note `list_comments call failed: <error>`
and continue. A missing spec-review file is not fatal — feature tickets
that never went through `/spec-review` (no port stage) and bug tickets
both legitimately have no such comment. Downstream stages branch on
`Status: PRESENT` (single grep) and skip the file otherwise.

## Step 5: Announce and stop

Print one of:

- Figma path: `Started /dev pipeline for <ticket-id> (<title>). Mode: <auto|default>. Next: /dev:figma`
- `--no-figma` path: `Started /dev pipeline for <ticket-id> (<title>). Mode: <auto|default>. Figma source pre-declared as none. Next: /dev:apply (figma + align skipped via .dev/figma-context.md SKIPPED first line)`
- `--bug` path: `Started /bug pipeline for <ticket-id> (<title>). Mode: <auto|default>. Next: /dev:apply (bug branch) — the agent will investigate the codebase, write the fix, and commit autonomously. OpenSpec stages (detect / align) are skipped.`
- `--port-handoff` path: `Adopted port handoff for <ticket-id> (<title>). Mode: <auto|default>. Reusing the existing port worktree/branch; spec-review directives captured to .dev/spec-review-directives.md. Next: /dev:figma (rides the feature OpenSpec flow over the ported openspec change).`

In auto mode, the chain orchestrator (`/dev:ff`) will continue automatically. STOP this stage's body.
