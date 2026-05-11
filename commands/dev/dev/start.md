---
name: start
description: "Stage 1 of the /dev:* atomic pipeline. Resolves the project profile, parses the ticket, runs pre-flight checks, optionally creates a worktree (auto mode), and assigns the ticket on Linear. This stage no longer creates state.json — pipeline progress is derived from filesystem markers by /dev:ff's walker."
Prerequisite: >
  - Linear MCP authenticated.
  - Default mode: already on the branch/worktree for the ticket. Git clean.
  - --auto mode: on trunk with clean working tree. gh CLI authenticated.
    Environment variables USER_NAME and GH_USER_NAME set.
---

# `/dev:start <ticket-id> [--auto] [--no-figma]`

Prepares the working environment for the dev loop. The done marker for this stage is the worktree (auto) or the on-branch + openspec change dir (default). No `.dev/state.json` is created — `infer_dev_stage` derives the next stage from filesystem.

## Inputs

- `<ticket-id>` — Linear ticket ID (e.g. `CAF-207`). Required.
- `--auto` — full autonomous pipeline.
- `--no-figma` — pre-declare that this ticket has no Figma source. Atomic-writes `.dev/figma-context.md` with first line `Fetched: SKIPPED — <reason>` so `/dev:figma` is skipped by the walker. **`/dev:start` is the SOLE writer of the SKIPPED first-line variant** (figma-subagent only writes `Fetched: <ISO>` or `Fetched: FAILED`).
- Linear ticket (fetched).
- Project profile (`{platform}`, `{deps_install}`, `{test_cmd}`).

## Outputs

- Worktree at `../<ticket-id>` (auto only).
- `.dev/figma-context.md` with first line `Fetched: SKIPPED — <reason>` (when `--no-figma` OR ticket has no Figma URL after parsing).
- Linear ticket: assigned to self, status `In Progress`, `ready-to-dev` label removed (auto only).
- `/tmp/<ticket-id>.md` — ticket dump (auto only).

## Step 0: Resolve project profile

1. If `<repo-root>/.gogox-claude.yaml` exists, read its `platform` and `product`. Else look up `basename "$(git rev-parse --show-toplevel)"` in `~/.claude/commands/profiles/repos.yaml`.
2. Read `~/.claude/commands/profiles/platform/{platform}.yaml` for `{deps_install}`, `{test_cmd}`, `{format_cmd}`.

## Step 1: Parse input

- Extract `<ticket-id>` from `$ARGUMENTS`. Detect `--auto`. Detect `--no-figma`.
- Missing ticket-id in `<auto-mode>` → STOP.
- Missing ticket-id in default mode → use **AskUserQuestion**. Stop if still missing.

## Step 2: Refuse re-entry

A pipeline is in flight in this worktree if any of these are true:

- `openspec/changes/` contains a non-archive change directory.
- `.dev/` contains any marker file (`.dev/figma-context.md`, `.dev/align-result.md`, `.dev/verify-pass.md`).

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
4. <!-- SYNC: the Linear init below is duplicated in three places. When changing it, also update:
       - /port:start Step 5a  (commands/dev/port/start.md)
       - /ggx-dispatcher Step 4 (commands/dev/ggx-dispatcher.md)
       Drift between these breaks dispatcher idempotency. -->
   Linear MCP transitions: status → `In Progress`, remove `ready-to-dev` label, assign to self via `$USER_NAME`. Set estimate to 1 point if none.
5. Write the full ticket content to `/tmp/<ticket-id>.md`.

**Default mode**:

1. Run `/check-clean`. Stop if not clean.
2. Check current branch contains `<ticket-id>` (case-insensitive). If not, **AskUserQuestion** to confirm; stop on No.

## Step 4: Figma SKIPPED first line (when no source)

```bash
mkdir -p .dev
TICKET_BODY=$(mcp__claude_ai_Linear__get_issue ... | jq -r '.description // ""')
HAS_FIGMA_URL=$(echo "$TICKET_BODY" | grep -cE 'figma\.com/(design|file|board|slides|make)/')
NO_FIGMA_FLAG=$(echo "$ARGUMENTS" | grep -q -- '--no-figma' && echo 1 || echo 0)

if [ "$NO_FIGMA_FLAG" = "1" ] || [ "$HAS_FIGMA_URL" -eq 0 ]; then
  REASON=$([ "$NO_FIGMA_FLAG" = "1" ] && echo "--no-figma flag at /dev:start" || echo "no Figma URL in ticket description")
  printf 'Fetched: SKIPPED — %s\n' "$REASON" > .dev/figma-context.md.tmp
  mv .dev/figma-context.md.tmp .dev/figma-context.md   # atomic
fi
```

`/dev:start` is the SOLE writer of the `Fetched: SKIPPED` first-line variant. figma-subagent only writes `Fetched: <ISO>` (success) or `Fetched: FAILED` (MCP fail). If the SKIPPED first line is missing on a no-Figma ticket, `infer_dev_stage` advances to `figma`; figma-subagent then receives an empty URL list and refuses with FAILED. Recovery: re-run `/dev:start`.

## Step 5: Announce and stop

Print one of:

- Figma path: `Started /dev pipeline for <ticket-id> (<title>). Mode: <auto|default>. Next: /dev:figma`
- `--no-figma` path: `Started /dev pipeline for <ticket-id> (<title>). Mode: <auto|default>. Figma source pre-declared as none. Next: /dev:apply (figma + align skipped via .dev/figma-context.md SKIPPED first line)`

In auto mode, the chain orchestrator (`/dev:ff`) will continue automatically. STOP this stage's body.
