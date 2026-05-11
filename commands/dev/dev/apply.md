---
name: apply
description: "Stage 5 — generate or fill in OpenSpec artifacts as needed, then produce real code changes. Mode-conditional execution: --auto spawns dev-agent end-to-end (worktree-isolated, opus); default runs /opsx:ff inline + HITL gate + /opsx:apply in main."
---

# `/dev:apply`

Drives the OpenSpec artifact prep + apply loop. State A creates artifacts from scratch, State C continues partial ones, State B applies directly.

**Execution location depends on mode** (per `plans/dev-ff-subagent-isolation.md` §3.6):

| Mode | Execution | HITL |
|---|---|---|
| `--auto` | spawn `dev-agent` (opus, worktree-isolated) end-to-end | none |
| `default` | inline in main session: `/opsx:ff` + `AskUserQuestion` + `/opsx:apply` | natural — gate fires between artifact gen and apply |

Only `apply` is mode-conditional. `figma` and `align` always go through their subagents because neither has a meaningful HITL gate.

## Inputs

- Linear ticket content (re-fetched if needed for `/opsx:ff` context).
- `.dev/figma-context.md` (the receipt) if it exists.
- All other context derived from branch + worktree + profile + `$ARGUMENTS`.

## Outputs

- Source code changes in the working tree.
- Updated `openspec/changes/<change-name>/` artifacts (state A/C).
- `tasks.md` checkboxes flipped to `[x]` as `/opsx:apply` completes them — this IS the done marker (`completedTasks == totalTasks` per the walker).
- (`--auto` only) dev-agent emits `Final Status: <CLEAR|FAILED|BLOCKED_CLARIFICATION> — <reason>` as its last chat line for FAILED/BLOCKED diagnostics. CLEAR is implicit from all-`[x]`.

## Step 0: Inline precondition

```bash
WT=$(git rev-parse --show-toplevel)
TICKET_ID=$(git rev-parse --abbrev-ref HEAD | grep -oE '[A-Z]+-[0-9]+' | head -1)
N=$(ls "$WT/openspec/changes" 2>/dev/null | grep -v '^archive$' | head -1)
MODE=$(echo "$ARGUMENTS" | grep -q -- '--auto' && echo auto || echo default)

# Resolve project profile
if [ -f "$WT/.gogox-claude.yaml" ]; then
  PLATFORM=$(yq -r '.platform' "$WT/.gogox-claude.yaml")
else
  PLATFORM=$(yq -r '.platform' "$HOME/.claude/commands/profiles/registry/$(basename "$WT").yaml")
fi

[ -n "$N" ] || { echo "FAIL: no openspec change directory" >&2; exit 1; }
[ -n "$TICKET_ID" ] || { echo "FAIL: cannot derive ticket_id from branch name" >&2; exit 1; }

# Classify openspec state for state-A/C branching in default mode
status_json=$(openspec status --change "$N" --json 2>/dev/null)
is_complete=$(echo "$status_json" | jq -r '.isComplete')
artifacts_ready=$(echo "$status_json" | jq -r '[.artifacts[].status] | map(select(. == "ready" or . == "complete")) | length')

if [ "$is_complete" = "true" ]; then OS_STATE="B"
elif [ "${artifacts_ready:-0}" -gt 0 ]; then OS_STATE="C"
else OS_STATE="A"; fi
```

## Step 1: Branch on mode

```
if MODE == "auto":
  proceed to Step 2A (spawn dev-agent)
else:
  proceed to Step 2D (inline default flow)
```

## Step 2A: --auto path — spawn dev-agent

### Step 2A.1: Dispatch dev-agent

Spawn `dev-agent` (opus, worktree-isolated) with these inputs:

```
ticket_id: $TICKET_ID
ticket_title: <re-fetched from Linear>
ticket_description: <re-fetched from Linear>
branch_name: $(git rev-parse --abbrev-ref HEAD)
worktree_path: $WT
figma_receipt: .dev/figma-context.md  (pass "none" if first line is `Fetched: SKIPPED`)
figma_raw_dir: .dev/figma-raw/        (or "none")
openspec_change_name: $N
openspec_state: $OS_STATE
platform: $PLATFORM
commit: false
```

The `commit: false` parameter is non-negotiable — per `agents/AGENTS.md`, dev-agent must not commit. Commit ownership stays with `/dev:verify`. If you find yourself wanting to set `commit: true`, you have misread the contract.

Wait for dev-agent to return. It modifies source / `tasks.md`, prints `Final Status: <CLEAR|FAILED|BLOCKED_CLARIFICATION> — <reason>` as its last chat line.

### Step 2A.2: Detect outcome via Final Status line + tasks.md

**Always parse `Final Status:` first** — `/opsx:apply` flips `[x]` *before* `{test_cmd}` runs inside dev-agent, so all-`[x]` does NOT imply tests passed. If dev-agent emitted FAILED/BLOCKED, honor that even when tasks look complete.

```bash
# 1. Surface the reason line first (load-bearing — overrides tasks.md count)
REASON=$(echo "$AGENT_RETURN" | grep -m1 '^Final Status:' | sed 's/^Final Status:[[:space:]]*//')

case "$REASON" in
  "BLOCKED_CLARIFICATION"*)
    echo "BLOCKED at /opsx:apply clarification: $REASON" >&2
    echo "Re-run /dev:ff (without --auto) to resolve the question interactively in default mode." >&2
    exit 1 ;;
  "FAILED"*|"ABORTED"*)
    echo "FAIL: dev-agent reported $REASON" >&2
    exit 1 ;;
  "CLEAR"*) : ;;       # explicit success — proceed to tasks check
  "")     : ;;         # no Final Status line — fall through to tasks check (legacy / older agent)
  *)
    echo "FAIL: unknown Final Status: $REASON" >&2
    exit 1 ;;
esac

# 2. Then verify the primary done signal: tasks.md checkboxes
tasks_done=$(openspec list --json 2>/dev/null \
  | jq -e --arg n "$N" '.changes[] | select(.name==$n) | (.completedTasks == .totalTasks) and (.totalTasks > 0)' \
  > /dev/null 2>&1 && echo "yes")

if [ "$tasks_done" != "yes" ]; then
  echo "FAIL: tasks.md not complete (some [ ] remain) and dev-agent did not emit a FAILED/BLOCKED Final Status — inconsistent return" >&2
  exit 1
fi
# CLEAR — proceed to Step 3.
```

The walker (`infer_dev_stage`) reads `tasks.md` on next iteration — if all `[x]`, advance to `verify`; otherwise stay at `apply`. No supplementary sentinel file is written or read.

## Step 2D: default path — inline in main session

Skipped if `mode == auto`. Default mode keeps the existing two-phase flow with HITL gate between artifact prep and apply.

### Step 2D.1: Generate artifacts (state A only)

_Skip if `$OS_STATE != "A"`._

1. Run `/opsx:ff <change-name>`. Prepare context yourself from the Linear ticket content — do **not** spawn `pm-agent` or `designer-agent`.
2. Pass an additional UI/test instruction tailored to `{platform}`. Keep the **intent** identical: tests covered, reuse existing i18n, accessibility identifiers on every interactive element, semantic label on icon-only buttons.
   - **flutter**: "Make sure tests are also updated. Use existing i18n keys as much as possible. Add a11y keys (accessibility `Key` identifiers) to all interactive widgets following the `*Keys` constant class pattern. For all clickable icon-only buttons (no visible text child), also add a semantic text label via `tooltip` on `IconButton` or `Semantics(label:)` on `GestureDetector` — do not rely on the Key ID alone."
   - **android**: "Make sure tests are also updated. Use existing string resources as much as possible. Add `testTag` (Compose) or `contentDescription` (Views) to all interactive elements. For icon-only buttons, also set `contentDescription` so TalkBack reads a meaningful label — do not rely on the testTag alone."
   - **ios**: "Make sure tests are also updated. Use existing localized strings (`Localizable.strings`) as much as possible. Add `accessibilityIdentifier` to all interactive elements. For icon-only buttons, also set `accessibilityLabel` — do not rely on the identifier alone."
3. After `/opsx:ff` completes, re-run `openspec status --change "<name>" --json` to confirm all `applyRequires` are `done`. If stalled, run `/opsx:continue` up to 3 rounds.
4. **Review gate** — present created artifacts (`proposal.md`, `design.md`, `specs/**/*.md`, `tasks.md`) with one-line summaries. Use **AskUserQuestion**:
   - `Proceed to apply` — go to Step 2D.3.
   - `Revise artifacts` — wait for edits, re-ask.
   - `Stop here` — STOP. Do not advance state. User runs `/dev:apply --force` later to resume.

### Step 2D.2: Continue artifacts (state C only)

_Skip if `$OS_STATE != "C"`._

1. Run `/opsx:continue` to fill in remaining artifacts.
2. Re-run `openspec status --change "<name>" --json` until all `applyRequires` are `done`. If it stalls (same artifact pending twice in a row), STOP and report.
3. Same review gate behavior as Step 2D.1.4 (three-option AskUserQuestion).

### Step 2D.3: Apply

Run `/opsx:apply <change-name>` directly in the main session. Stop when all tasks complete, or when `/opsx:apply` pauses for clarification (which surfaces as a natural user-facing prompt — answer it and `/opsx:apply` continues).

## Step 3: Stop

No state mutation. The done marker is `tasks.md` checkboxes (`completedTasks == totalTasks`) per `infer_dev_stage`.

Print one of:

- `mode == auto`: `Apply complete. Next: /dev:verify.`
- `mode == default`: `Apply complete. Default mode terminal at apply — drive next steps manually: /format → /commit → /pull-request. To upgrade to auto and chain through verify/review/ship: /dev:ff --auto.`

`/dev:ff --auto` from this point picks up at `verify` automatically (walker sees tasks all `[x]` + the new invocation passes `--auto`, so verify runs). Default mode is NOT a dead-end; the same worktree resumes at verify when `--auto` is added on a later `/dev:ff` invocation.
