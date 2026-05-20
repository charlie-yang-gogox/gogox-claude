---
name: start
description: "Stage 1 of the /dev:* atomic pipeline. Resolves the project profile, parses the ticket, runs pre-flight checks, optionally creates a worktree (auto mode), and assigns the ticket on Linear. This stage no longer creates state.json — pipeline progress is derived from filesystem markers by /dev:ff's walker."
Prerequisite: >
  - Linear MCP authenticated.
  - Default mode: already on the branch/worktree for the ticket. Git clean.
  - --auto mode: on trunk with clean working tree. gh CLI authenticated.
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
- Linear ticket (fetched).
- Project profile (`{platform}`, `{deps_install}`, `{test_cmd}`).

## Outputs

- Worktree at `../<ticket-id>` (auto only).
- `.dev/figma-context.md` with first line `Fetched: SKIPPED — <reason>` (when `--no-figma` OR ticket has no Figma URL after parsing).
- `.dev/spec-review-directives.md` — first line `Status: PRESENT` (latest `<!-- spec-review:v1 -->` Linear comment captured verbatim) or `Status: NONE` (no such comment). Always written. Consumed by `/dev:apply` Step 0-bug.1 and Step 4D.1 to surface `[REVISED]` directives to whichever agent authors code.
- `.dev/mode.md` with single line `bug` (only when `--bug` is set). Absent for the default feature path — readers treat absent as `feature`.
- Linear ticket: assigned to self, status `In Progress`, `ready-to-dev` label removed, estimate=1 if null, starting comment posted (both modes; skipped only with `--no-ticket-init`). Driven by `/_ticket-init` (idempotent).
- `/tmp/<ticket-id>.md` — ticket dump (auto only).

## Step 0: Resolve project profile

1. If `<repo-root>/.gogox-claude.yaml` exists, read its `platform` and `product`. Else look up `basename "$(git rev-parse --show-toplevel)"` in `~/.claude/commands/profiles/repos.yaml`.
2. Read `~/.claude/commands/profiles/platform/{platform}.yaml` for `{deps_install}`, `{test_cmd}`, `{format_cmd}`.

## Step 1: Parse input

- Extract `<ticket-id>` from `$ARGUMENTS`. Detect `--auto`. Detect `--no-figma`. Detect `--bug`. Detect `--no-ticket-init`.
- Missing ticket-id in `<auto-mode>` → STOP.
- Missing ticket-id in default mode → use **AskUserQuestion**. Stop if still missing.

## Step 2: Refuse re-entry

A pipeline is in flight in this worktree if any of these are true:

- `openspec/changes/` contains a non-archive change directory.
- `.dev/` contains any marker file (`.dev/figma-context.md`, `.dev/align-result.md`, `.dev/verify-pass.md`). Note: `.dev/spec-review-directives.md` is intentionally NOT in this list — it is written every run by Step 4c and would otherwise refuse re-entry on a re-run of `/dev:start`.

If in flight, STOP with: `Pipeline already in flight in this worktree. Resume with /dev:ff, or /dev:ff --from <stage> to reset.`

## Step 3: Pre-flight + ticket assignment

**Linear ownership check** (both modes): fetch via `mcp__claude_ai_Linear__get_issue`. If the ticket is not assigned to the current user, STOP.

### Step 3a: Runtime artifact residue handling

Before the strict cleanliness check, scan for runtime artifacts left over by prior pipeline runs that may not have cleaned up. These are NOT real source modifications — they are observability / state files.

```bash
# Match any path under .dev/ (whole directory is gitignored runtime workspace)
RUNTIME_REGEX='(^|/)\.dev/'
PORCELAIN=$(git status --porcelain)
RUNTIME_DIRT=$(printf '%s\n' "$PORCELAIN" | grep -E "$RUNTIME_REGEX" || true)
OTHER_DIRT=$(printf '%s\n' "$PORCELAIN" | grep -vE "$RUNTIME_REGEX" || true)

# Legacy .dev/state.json residue (one-time cleanup safety net for pipelines started under v7)
if [ -f .dev/state.json ]; then
  echo "INFO: legacy .dev/state.json detected; removing (filesystem-as-state model)" >&2
  rm -f .dev/state.json
fi
```

- If `$OTHER_DIRT` is empty AND `$RUNTIME_DIRT` is non-empty: this is pure leftover residue.
  - **Auto mode**: log the list, then `git checkout -- <files>` for tracked-and-modified entries and `rm -f <files>` for untracked ones. Proceed.
  - **Default mode**: list the residue files. **AskUserQuestion** with options:
    - `Discard residue and continue` (default) — same cleanup as auto.
    - `Inspect first (abort)` — STOP so user can review manually.
- If `$OTHER_DIRT` is non-empty: real source changes exist; STOP with the standard "uncommitted changes" error regardless of residue.

### Step 3b: Mode-specific pre-flight

**Auto mode**:

1. Verify git is clean and on `trunk`. If not → STOP.
2. Read the Linear ticket to determine branch type (`feat`, `fix`, `test`, `ci`, `chore`).
3. Invoke `/add-worktree <ticket-id> --type <type>` — handles fetch, branch, EnterWorktree, port-settings, `{deps_install}`.
4. Write the full ticket content to `/tmp/<ticket-id>.md`.

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
TICKET_BODY=$(mcp__claude_ai_Linear__get_issue ... | jq -r '.description // ""')

# Concatenate every comment body into one stream so the same regex catches
# Figma URLs whether they were placed in the description or added later via
# a comment. This is the move that retired the dispatcher's pre-detection
# of `--no-figma`: /dev:start is now authoritative.
TICKET_COMMENTS=$(mcp__claude_ai_Linear__list_comments ... | jq -r '.comments[].body // ""' | tr '\n' ' ')

HAS_FIGMA_URL=$(printf '%s\n%s\n' "$TICKET_BODY" "$TICKET_COMMENTS" \
  | grep -cE 'figma\.com/(design|file|board|slides|make)/')
NO_FIGMA_FLAG=$(echo "$ARGUMENTS" | grep -q -- '--no-figma' && echo 1 || echo 0)

if [ "$NO_FIGMA_FLAG" = "1" ] || [ "$HAS_FIGMA_URL" -eq 0 ]; then
  REASON=$([ "$NO_FIGMA_FLAG" = "1" ] && echo "--no-figma flag at /dev:start" || echo "no Figma URL in ticket description or comments")
  printf 'Fetched: SKIPPED — %s\n' "$REASON" > .dev/figma-context.md.tmp
  mv .dev/figma-context.md.tmp .dev/figma-context.md   # atomic
fi
```

`/dev:start` is the SOLE writer of the `Fetched: SKIPPED` first-line variant. figma-subagent only writes `Fetched: <ISO>` (success) or `Fetched: FAILED` (MCP fail). If the SKIPPED first line is missing on a no-Figma ticket, `infer_dev_stage` advances to `figma`; figma-subagent then receives an empty URL list and refuses with FAILED. Recovery: re-run `/dev:start`.

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

`.dev/mode.md` presence with value `bug` is the canonical signal that downstream stages (`/dev:verify`, `/dev:ship`, `/dev:ff` walker) read to take the bug-mode branch. Default (feature) mode does NOT write this file — readers treat absent as `feature`. This keeps existing dev-pipeline runs unchanged and makes bug mode opt-in.

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

In auto mode, the chain orchestrator (`/dev:ff`) will continue automatically. STOP this stage's body.
