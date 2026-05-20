---
name: apply
description: "Stage 5 — generate or fill in OpenSpec artifacts as needed, then produce real code changes. Mode-conditional execution: --auto runs inline (no nested spawn — dispatcher path is already a subagent); default runs /opsx:ff inline + HITL gate + spawns dev-agent (worktree-isolated, opus) for /opsx:apply."
---

# `/dev:apply`

Drives the OpenSpec artifact prep + apply loop. State A creates artifacts from scratch, State C continues partial ones, State B applies directly.

**Execution location depends on mode**:

| Mode | Artifact prep (state A/C) | HITL | Apply (`/opsx:apply`) |
|---|---|---|---|
| `--auto` | inline in current session | none | inline in current session |
| `default` | inline in main session | `AskUserQuestion` between prep and apply | spawn `dev-agent` (opus, worktree-isolated) |

Why flipped: `/ggx-dispatcher` invokes `/dev:ff --auto` inside a `general-purpose` subagent. That subagent cannot reliably nest-spawn an opus `dev-agent`, so `--auto` must be inline end-to-end. Default mode runs from a main session that can spawn freely — so the heavy `/opsx:apply` work is isolated into dev-agent for context savings, and `dev-agent` gains a real caller. Only `apply` is mode-conditional. `figma` and `align` always go through their (sonnet) subagents because neither has a meaningful HITL gate and nested sonnet spawns are known to work.

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
MODE=$(echo "$ARGUMENTS" | grep -q -- '--auto' && echo auto || echo default)

# Pipeline mode: bug vs feature. Resolved by pipe_mode (lib/dev-mode.sh);
# .dev/mode.md is written by /dev:start --bug.
source "$HOME/.claude/lib/dev-mode.sh"
PIPE_MODE=$(pipe_mode "$WT")

# Resolve project profile
if [ -f "$WT/.gogox-claude.yaml" ]; then
  PLATFORM=$(yq -r '.platform' "$WT/.gogox-claude.yaml")
else
  PLATFORM=$(yq -r '.platform' "$HOME/.claude/commands/profiles/registry/$(basename "$WT").yaml")
fi

[ -n "$TICKET_ID" ] || { echo "FAIL: cannot derive ticket_id from branch name" >&2; exit 1; }
```

**Mode dispatch**:

- `PIPE_MODE == bug` → jump to **Step 0-bug** below. Steps 1–5 (OpenSpec artifact prep + apply) are SKIPPED entirely. There is no `change_name`, no `tasks.md`, no `/opsx:*` invocation.
- `PIPE_MODE == feature` → continue with the OpenSpec precondition below, then Steps 1–5.

```bash
# Feature mode only — bug mode does not need an OpenSpec change directory.
N=$(ls "$WT/openspec/changes" 2>/dev/null | grep -v '^archive$' | head -1)
[ -n "$N" ] || { echo "FAIL: no openspec change directory" >&2; exit 1; }

# Classify openspec state for state-A/C branching
status_json=$(openspec status --change "$N" --json 2>/dev/null)
is_complete=$(echo "$status_json" | jq -r '.isComplete')
artifacts_ready=$(echo "$status_json" | jq -r '[.artifacts[].status] | map(select(. == "ready" or . == "complete")) | length')

if [ "$is_complete" = "true" ]; then OS_STATE="B"
elif [ "${artifacts_ready:-0}" -gt 0 ]; then OS_STATE="C"
else OS_STATE="A"; fi
```

Feature mode shares artifact prep (Steps 1–2). It diverges at Step 3 (HITL gate, default only) and Step 4 (apply path).

---

## Step 0-bug: Agent-autonomous bug fix (bug mode only)

_Run when `PIPE_MODE == bug`. This entire section REPLACES Steps 1–5. After it finishes, return to `/dev:ff` (same "in-loop signal" contract as feature mode)._

The agent is responsible for the **full fix loop** — investigate, hypothesize, implement, commit. The user is NOT asked to find root cause, write code, or pick files. In `--auto` mode there is no HITL at all. In `default` mode there is ONE HITL gate to confirm the agent's fix plan (not to delegate work back to the human).

### Step 0-bug.1: Refresh ticket context

Re-fetch the ticket via `mcp__claude_ai_Linear__get_issue` so the agent has the latest description, comments, and any attached repro steps. Capture into local variables `ticket_title`, `ticket_description`, `ticket_comments`.

**Spec-review overrides (authoritative — override conflicting ticket / OpenSpec guidance).** After the re-fetch, read `.dev/spec-review-directives.md` (written one-shot by `/dev:start` Step 4c). If its first line is `Status: PRESENT`, include the entire file contents in your working context under the explicit marker `## Spec-review overrides (authoritative — override conflicting design.md / tasks.md / ticket-description guidance)`. Each `### [REVISED]` block's `Directive:` line is a human override that supersedes any conflicting guidance found elsewhere — apply it verbatim. If `Status: NONE`, ignore the file. If the file is missing entirely, treat it as `Status: NONE` (legacy worktree created before this fix landed) — do NOT re-fetch comments to compensate; `/dev:start` is the sole writer.

If the ticket description is empty or contains less than ~50 chars of substantive content, write `.dev/apply-result.md` with `Status: FAILED — bug ticket is too thin to act on autonomously; needs reproduction steps or symptoms` and STOP. The agent should not fabricate a bug from nothing.

### Step 0-bug.2: Investigate (LLM-driven)

The agent reads the codebase to locate the root cause. **You (the agent) do this yourself using Grep / Read / Glob tools** — do not ask the user where to look.

A minimal investigation procedure:

1. Extract symptoms, affected feature paths, and any file / class names from the ticket text.
2. Grep the worktree for those names + adjacent vocabulary (provider names, error messages, route names).
3. Read suspect files end-to-end (not snippets) — bugs in state management, lifecycle, async ordering are not visible in 20-line windows.
4. Form a hypothesis about where the bug lives and why. Bias toward state / lifecycle / ordering bugs over "wrong constant" bugs — they're more common in tickets that reach this pipeline.

Write `.dev/bug-analysis.md` with this structure (the agent owns the content):

```markdown
# Bug analysis: <ticket-id>

**Symptoms** (from ticket):
- ...

**Investigation**:
- Files read: <list>
- Key findings: <2-5 bullets about what state / flow is involved>

**Hypothesis**:
<one paragraph stating root cause>

**Planned fix**:
- File: <path>
  Change: <one-line description>
- File: <path>
  Change: <one-line description>

**Risk / tests to update**:
- <what could regress, which tests to add or modify>
```

This file is the agent's audit trail — useful for post-hoc review even if the fix is correct.

### Step 0-bug.3: HITL gate (default mode only)

_Skip entirely if `MODE == auto`._

This is the **only** HITL gate in bug-default mode. Surface the `.dev/bug-analysis.md` "Hypothesis" + "Planned fix" sections to the user via **AskUserQuestion**, and **explicitly warn them what `Proceed` will do**:

> **Warning to surface in the question text**: "Proceeding will run the full pipeline end-to-end — Edit files → `/commit` → `/dev:verify` (tests + audit) → `/dev:review` (code review) → `/dev:ship` (push branch, open draft PR, flip Linear to In Review). This is NOT a `default` pause-after-apply flow like feature mode. To inspect intermediate state, pick `Stop here` instead."

Options:

- `Proceed with this fix (runs end-to-end to draft PR)` — go to Step 0-bug.4. The pipeline will run all downstream stages without further prompts.
- `Revise the plan` — agent re-reads, updates `.dev/bug-analysis.md`, re-asks this gate.
- `Stop here` — STOP. User inspects manually. The walker will see no `apply-result.md` and re-emit `apply` on next `/dev:ff`, which re-enters this Step.

**This HITL is for confirming the agent's plan, NOT for the user to find root cause or pick a fix.** If the user picks "Stop here", that is their explicit choice to take over — not the agent giving up.

**Why bug-default runs end-to-end after this gate** (and feature-default stops at apply): bug mode has no user-decision gap left after apply — the agent already wrote the fix, committed it, and produced `.dev/apply-result.md`. The remaining verify/review/ship steps are mechanical (test + audit + push) with no further authoring. Feature mode, by contrast, stops at apply because the user typically wants to inspect the generated artifacts before committing.

`--auto` skips this gate entirely. The Linear ticket already represents a human decision that this is a bug to fix; auto mode trusts that decision.

### Step 0-bug.4: Apply the fix

Edit / Write the files listed in `.dev/bug-analysis.md`'s "Planned fix" section. Keep changes minimal and focused on the root cause — do NOT take the opportunity to refactor surrounding code.

If the platform has a test target that maps obviously to the changed code path (e.g. a `*_test.dart` next to `*.dart` in flutter), add or modify a test that would have caught this bug. Bug fix without a regression test is acceptable but flagged in `apply-result.md`.

### Step 0-bug.5: Commit

Run `/commit` to stage and commit the changes. The commit message should reference `<ticket-id>` and summarize the fix in one sentence — `/commit` handles the formatting.

If `/commit` fails (pre-commit hook, etc.), do NOT amend, retry, or paper over. Write `.dev/apply-result.md` with `Status: FAILED — /commit failed: <hook output>` and STOP. The user resolves the hook issue and re-runs `/dev:ff` (which re-enters this stage; analysis + fix are still in working tree).

### Step 0-bug.6: Write `.dev/apply-result.md`

```markdown
Status: CLEAR
Summary: <one-line summary of fix>
Files changed: <list>
Regression test: <added | modified | not-applicable | none — reason>
Analysis: .dev/bug-analysis.md
```

This file is the walker's done marker for the bug-mode apply stage — `infer_bug_stage` sees `Status: CLEAR` and advances to `verify`.

### Step 0-bug.7: Return to /dev:ff (NOT pipeline-terminal)

Print: `Bug-apply stage done. Pipeline NOT complete — /dev:ff must now continue to /dev:verify. This is an IN-LOOP signal, not a terminal signal. Re-run infer_bug_stage and dispatch the next stage.`

STOP this body. The walker takes over.

---

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

**Spec-review overrides (authoritative — override conflicting design.md / tasks.md guidance).** Before invoking `/opsx:apply`, read `.dev/spec-review-directives.md` (written one-shot by `/dev:start` Step 4c). If its first line is `Status: PRESENT`, hold the entire file contents in your working context for the duration of `/opsx:apply`. Each `### [REVISED]` block's `Directive:` line is a human override that supersedes any conflicting guidance the `/opsx:apply` task list pulls from `design.md` / `tasks.md` / ticket description — apply it verbatim and reference it in any commit message touching the relevant task. If `Status: NONE`, no overrides apply. Missing file → treat as `Status: NONE` (legacy worktree).

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

**Spec-review overrides — resolve the path before dispatch.** Before spawning, check `.dev/spec-review-directives.md` (written one-shot by `/dev:start` Step 4c). If its first line is `Status: PRESENT`, pass the file path through the input block as `spec_review_directives: .dev/spec-review-directives.md`. If `Status: NONE` or the file is missing, pass `spec_review_directives: none`. The dev-agent (per `agents/dev/dev-agent.md`) is instructed to read this file and treat its `### [REVISED]` directives as authoritative overrides of conflicting `design.md` / `tasks.md` guidance.

Spawn `dev-agent` (opus, worktree-isolated) with these inputs:

```
ticket_id: $TICKET_ID
ticket_title: <re-fetched from Linear>
ticket_description: <re-fetched from Linear>
branch_name: $(git rev-parse --abbrev-ref HEAD)
worktree_path: $WT
figma_receipt: .dev/figma-context.md  (pass "none" if first line is `Fetched: SKIPPED`)
figma_raw_dir: .dev/figma-raw/        (or "none")
spec_review_directives: .dev/spec-review-directives.md  (pass "none" if first line is `Status: NONE` or the file is missing)
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

The auto-mode message is deliberately blunt — earlier wording (`Apply complete. Next: /dev:verify.`) was observed to be misread by `/ggx-dispatcher`-spawned subagents as a terminus signal, causing them to stop the loop after apply. The "IN-LOOP signal" phrasing is load-bearing — do not soften it.

`/dev:ff --auto` from this point picks up at `verify` automatically (walker sees tasks all `[x]` + the new invocation passes `--auto`, so verify runs). Default mode is NOT a dead-end; the same worktree resumes at verify when `--auto` is added on a later `/dev:ff` invocation.
