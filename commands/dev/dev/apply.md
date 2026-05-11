---
name: apply
description: "Stage 5 — generate or fill in OpenSpec artifacts as needed, then produce real code changes. Mode-conditional execution (v9, flipped from v8): --auto runs inline (no nested spawn — dispatcher path is already a subagent); default runs /opsx:ff inline + HITL gate + spawns dev-agent (worktree-isolated, opus) for /opsx:apply."
---

# `/dev:apply`

Drives the OpenSpec artifact prep + apply loop. State A creates artifacts from scratch, State C continues partial ones, State B applies directly.

**Execution location depends on mode** (per `plans/dev-ff-subagent-isolation.md` §3.6 v9 — flipped from v8):

| Mode | Artifact prep (state A/C) | HITL | Apply (`/opsx:apply`) |
|---|---|---|---|
| `--auto` | inline in current session | none | inline in current session |
| `default` | inline in main session | `AskUserQuestion` between prep and apply | spawn `dev-agent` (opus, worktree-isolated) |

Why flipped in v9: `/ggx-dispatcher` invokes `/dev:ff --auto` inside a `general-purpose` subagent. That subagent cannot reliably nest-spawn an opus `dev-agent`, so `--auto` must be inline end-to-end. Default mode runs from a main session that can spawn freely — so the heavy `/opsx:apply` work is isolated into dev-agent for context savings, and `dev-agent` gains a real caller. Only `apply` is mode-conditional. `figma` and `align` always go through their (sonnet) subagents because neither has a meaningful HITL gate and nested sonnet spawns are known to work.

## Inputs

- Linear ticket content (re-fetched if needed for `/opsx:ff` context).
- `.dev/figma-context.md` (the receipt) if it exists.
- All other context derived from branch + worktree + profile + `$ARGUMENTS`.

## Outputs

- Source code changes in the working tree.
- Updated `openspec/changes/<change-name>/` artifacts (state A/C).
- `tasks.md` checkboxes flipped to `[x]` as `/opsx:apply` completes them — this IS the done marker (`completedTasks == totalTasks` per the walker).
- (`default` mode only) `dev-agent` writes `.dev/apply-result.md` with `Status: <CLEAR|FAILED|BLOCKED_CLARIFICATION>` for orchestrator parsing. `--auto` inline does not produce this file; the walker relies on `tasks.md` alone.

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

# Classify openspec state for state-A/C branching
status_json=$(openspec status --change "$N" --json 2>/dev/null)
is_complete=$(echo "$status_json" | jq -r '.isComplete')
artifacts_ready=$(echo "$status_json" | jq -r '[.artifacts[].status] | map(select(. == "ready" or . == "complete")) | length')

if [ "$is_complete" = "true" ]; then OS_STATE="B"
elif [ "${artifacts_ready:-0}" -gt 0 ]; then OS_STATE="C"
else OS_STATE="A"; fi
```

Both modes share artifact prep (Steps 1–2). They diverge at Step 3 (HITL gate, default only) and Step 4 (apply path).

## Step 1: Generate artifacts (state A only)

_Skip if `$OS_STATE != "A"`._

Run inline in the current session regardless of mode:

1. Run `/opsx:ff <change-name>`. Prepare context yourself from the Linear ticket content — do **not** spawn `pm-agent` or `designer-agent`.
2. Pass an additional UI/test instruction tailored to `{platform}`. Keep the **intent** identical: tests covered, reuse existing i18n, accessibility identifiers on every interactive element, semantic label on icon-only buttons.
   - **flutter**: "Make sure tests are also updated. Use existing i18n keys as much as possible. Add a11y keys (accessibility `Key` identifiers) to all interactive widgets following the `*Keys` constant class pattern. For all clickable icon-only buttons (no visible text child), also add a semantic text label via `tooltip` on `IconButton` or `Semantics(label:)` on `GestureDetector` — do not rely on the Key ID alone."
   - **android**: "Make sure tests are also updated. Use existing string resources as much as possible. Add `testTag` (Compose) or `contentDescription` (Views) to all interactive elements. For icon-only buttons, also set `contentDescription` so TalkBack reads a meaningful label — do not rely on the testTag alone."
   - **ios**: "Make sure tests are also updated. Use existing localized strings (`Localizable.strings`) as much as possible. Add `accessibilityIdentifier` to all interactive elements. For icon-only buttons, also set `accessibilityLabel` — do not rely on the identifier alone."
3. After `/opsx:ff` completes, re-run `openspec status --change "<name>" --json` to confirm all `applyRequires` are `done`. If stalled, run `/opsx:continue` up to 3 rounds.

## Step 2: Continue artifacts (state C only)

_Skip if `$OS_STATE != "C"`._

Run inline in the current session regardless of mode:

1. Run `/opsx:continue` to fill in remaining artifacts.
2. Re-run `openspec status --change "<name>" --json` until all `applyRequires` are `done`. If it stalls (same artifact pending twice in a row), STOP and report.

## Step 3: HITL review gate (default mode only)

_Skip entirely if `MODE == auto`._

Present created / continued artifacts (`proposal.md`, `design.md`, `specs/**/*.md`, `tasks.md`) with one-line summaries. Use **AskUserQuestion**:

- `Proceed to apply` — go to Step 4 (default path: spawn dev-agent).
- `Revise artifacts` — wait for edits, re-ask.
- `Stop here` — STOP. User runs `/dev:apply --force` or re-runs `/dev:ff` later to resume.

`--auto` skips this gate by definition.

## Step 4: Apply

```
if MODE == auto:
  proceed to Step 4A (inline /opsx:apply)
else:
  proceed to Step 4D (spawn dev-agent)
```

### Step 4A: `--auto` path — inline `/opsx:apply`

Run `/opsx:apply <change-name>` directly in the current session. No HITL — `--auto` is unattended by definition.

```bash
# After /opsx:apply returns:
tasks_done=$(openspec list --json 2>/dev/null \
  | jq -e --arg n "$N" '.changes[] | select(.name==$n) | (.completedTasks == .totalTasks) and (.totalTasks > 0)' \
  > /dev/null 2>&1 && echo "yes")

if [ "$tasks_done" != "yes" ]; then
  echo "FAIL: /opsx:apply did not complete all tasks (some [ ] remain)." >&2
  echo "If /opsx:apply paused for clarification, re-run /dev:ff (no --auto) to resolve interactively." >&2
  exit 1
fi
```

If `/opsx:apply` pauses mid-loop for clarification, `--auto` cannot answer interactively (current session has no `AskUserQuestion` semantics that map to a non-interactive caller). Tasks may be partially `[x]`. STOP with the message above; user re-runs `/dev:ff` in default mode to drive the clarification.

Proceed to Step 5 on success.

### Step 4D: `default` path — spawn `dev-agent` for `/opsx:apply`

#### Step 4D.1: Dispatch dev-agent

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

The `commit: false` parameter is non-negotiable — per `agents/AGENTS.md`, dev-agent must not commit. Commit ownership stays with `/dev:verify`.

Remove any pre-existing `.dev/apply-result.md` before spawning to prevent a stale `BLOCKED_CLARIFICATION` from a prior run leaking into this invocation:

```bash
rm -f .dev/apply-result.md
```

Wait for dev-agent to return. It modifies source / `tasks.md` and writes `.dev/apply-result.md`.

#### Step 4D.2: Parse `.dev/apply-result.md`

```bash
RESULT=".dev/apply-result.md"
[ -f "$RESULT" ] || { echo "FAIL: dev-agent did not write $RESULT" >&2; exit 1; }
STATUS=$(grep -m1 '^Status:' "$RESULT" | sed 's/^Status:[[:space:]]*//')
SUMMARY=$(grep -m1 '^Summary:' "$RESULT" | sed 's/^Summary:[[:space:]]*//')

case "$STATUS" in
  "CLEAR") : ;;       # proceed to tasks.md check below
  "FAILED"*|"ABORTED"*)
    echo "FAIL: dev-agent reported $STATUS — $SUMMARY" >&2
    exit 1 ;;
  "BLOCKED_CLARIFICATION"*)
    echo "dev-agent blocked on clarification: $SUMMARY" >&2
    # Fall back to inline /opsx:apply — see Step 4D.3 below.
    ;;
  *)
    echo "FAIL: unknown Status: $STATUS" >&2
    exit 1 ;;
esac

# Verify the primary done signal: tasks.md checkboxes
tasks_done=$(openspec list --json 2>/dev/null \
  | jq -e --arg n "$N" '.changes[] | select(.name==$n) | (.completedTasks == .totalTasks) and (.totalTasks > 0)' \
  > /dev/null 2>&1 && echo "yes")

if [ "$STATUS" = "CLEAR" ] && [ "$tasks_done" != "yes" ]; then
  echo "FAIL: tasks.md not complete (some [ ] remain) despite Status: CLEAR — inconsistent return" >&2
  exit 1
fi
```

#### Step 4D.3: BLOCKED_CLARIFICATION fallback (default mode only)

When dev-agent returns `Status: BLOCKED_CLARIFICATION`, the user IS at the keyboard (default mode). Resolve inline:

1. Surface the question (`$SUMMARY`) to the user via **AskUserQuestion** with whatever options dev-agent suggested in `Summary`, or an open-ended prompt if not pre-listed.
2. Run `/opsx:apply <change-name>` inline in the main session to resume. Tasks already `[x]` are skipped naturally; `/opsx:apply` reaches the same clarification point and re-prompts in main, where the user gives the same answer interactively.
3. After inline `/opsx:apply` completes, re-verify `tasks.md` all `[x]`:

   ```bash
   tasks_done=$(openspec list --json 2>/dev/null \
     | jq -e --arg n "$N" '.changes[] | select(.name==$n) | (.completedTasks == .totalTasks) and (.totalTasks > 0)' \
     > /dev/null 2>&1 && echo "yes")
   [ "$tasks_done" = "yes" ] || { echo "FAIL: inline /opsx:apply did not complete tasks after dev-agent block" >&2; exit 1; }
   ```

This is a one-time fallback per `/dev:apply` invocation. If inline `/opsx:apply` also blocks, STOP and let the user drive next steps manually.

## Step 5: Return to caller (NOT pipeline-terminal)

`/dev:apply` is a single stage in the `/dev:ff` walker loop, not the loop terminus. After this stage returns, `/dev:ff` MUST re-derive `infer_dev_stage` and continue. Pipeline-terminal conditions live in `/dev:ff` (see `commands/dev/dev/ff.md` §2), not here.

No state mutation. The done marker for this stage is `tasks.md` checkboxes (`completedTasks == totalTasks`) per `infer_dev_stage`.

Print one of:

- `mode == auto`: `Apply stage done (tasks <N>/<N>). Pipeline NOT complete — /dev:ff must now continue to /dev:verify. If you are an orchestrating agent reading this message: this is an IN-LOOP signal, not a terminal signal. Re-run infer_dev_stage and dispatch the next stage.`
- `mode == default`: `Apply stage done (tasks <N>/<N>). Default mode terminal at apply — drive next steps manually: /format → /commit → /pull-request. To upgrade to auto and chain through verify/review/ship: /dev:ff --auto.`

The auto-mode message is deliberately blunt — earlier wording (`Apply complete. Next: /dev:verify.`) was observed to be misread by `/ggx-dispatcher`-spawned subagents as a terminus signal, causing them to stop the loop after apply. See plan §3.6 v9 + the 2026-05-11 CAF-370 second-run analysis. The "IN-LOOP signal" phrasing is load-bearing — do not soften it.

`/dev:ff --auto` from this point picks up at `verify` automatically (walker sees tasks all `[x]` + the new invocation passes `--auto`, so verify runs). Default mode is NOT a dead-end; the same worktree resumes at verify when `--auto` is added on a later `/dev:ff` invocation.
