---
name: ff
description: "Fast-forward orchestrator — chains every /dev:* atomic stage from the current state to `done` (or to the next HITL gate / failure). Pass `<ticket-id> [--auto]` to start fresh; pass nothing to resume an in-progress pipeline. Mirrors the /port:ff pattern."
Prerequisite: >
  Same as /dev:start when starting fresh. When resuming, only requires that
  the working tree contains an in-progress pipeline (worktree + openspec change
  dir or any .dev/* marker file).
---

# `/dev:ff`

Single-command run-the-whole-pipeline. The orchestrator no longer reads `state.json` — it derives `current_stage` from filesystem markers via `infer_dev_stage`. Stops on the first failure, HITL gate (default mode), or completion.

## Usage

- `/dev:ff <ticket-id> --auto` — start a fresh auto-mode pipeline. Equivalent to: `/dev:start <ticket-id> --auto` followed by chaining all stages until `done`.
- `/dev:ff <ticket-id>` — start a fresh default-mode pipeline. Stops at the first HITL gate (`/dev:apply`'s review gate by default).
- `/dev:ff <ticket-id> --bug` — bug-fix pipeline. Skips `/dev:figma` / `/dev:detect` / `/dev:align` / `/dev:apply`; pauses after `/dev:start` for the human to write the fix; resumes when invoked again after a commit lands. Prefer the alias `/bug:ff` from `/route` / `/ggx-work` flows. Combinable with `--auto` for the dispatcher path.
- `/dev:ff` — resume. Runs `infer_dev_stage` and dispatches `/dev:<that-stage>`. Loops until `done`, failure, or HITL gate.
- `/dev:ff --from <stage>` — delete the marker files of `<stage>` and everything downstream, then resume. Next `infer_dev_stage` re-derives starting at `<stage>`.

## Step 0: Decide entry point

```bash
TICKET_FROM_ARGS="<parsed from $ARGUMENTS, may be empty>"
AUTO_FLAG=$(echo "$ARGUMENTS" | grep -q -- '--auto' && echo 1 || echo 0)
NO_FIGMA_FLAG=$(echo "$ARGUMENTS" | grep -q -- '--no-figma' && echo 1 || echo 0)
BUG_FLAG=$(echo "$ARGUMENTS" | grep -q -- '--bug' && echo 1 || echo 0)
FROM_STAGE="<parsed --from <stage>, may be empty>"

# Detect whether a pipeline is already in flight in this worktree.
PIPELINE_IN_FLIGHT="no"
[ -d "$(pwd)/openspec/changes" ] && [ -n "$(ls openspec/changes 2>/dev/null | grep -v '^archive$')" ] && PIPELINE_IN_FLIGHT="yes"
[ -d "$(pwd)/.dev" ] && [ -n "$(ls .dev 2>/dev/null)" ] && PIPELINE_IN_FLIGHT="yes"
```

- If `$TICKET_FROM_ARGS` is non-empty AND `PIPELINE_IN_FLIGHT == "no"`: invoke `/dev:start <ticket-id> [--auto] [--no-figma] [--bug]`, then continue with `infer_dev_stage`.
- If `$TICKET_FROM_ARGS` is non-empty AND `PIPELINE_IN_FLIGHT == "yes"`: refuse (use `/dev:start`'s re-entry rules — do not silently overwrite).
- If `$TICKET_FROM_ARGS` is empty AND `PIPELINE_IN_FLIGHT == "yes"`: resume via walker.
- If `$TICKET_FROM_ARGS` is empty AND `PIPELINE_IN_FLIGHT == "no"`: STOP with usage.
- `--from <stage>` flag: delete markers (Step 0a) before dispatching.
- `--auto` flag is per-invocation. There is no persisted mode; passing `--auto` on resume simply runs the rest of the pipeline in auto mode.
- `--bug` flag is **persisted via `.dev/mode.md`** (written by `/dev:start --bug`). Resume invocations do not need to re-pass `--bug`; the walker reads `.dev/mode.md` to branch into bug-mode logic automatically. Passing `--bug` on resume when `.dev/mode.md` is absent is a no-op (the walker would still take the feature path).

### Step 0a: --from handling

`--from <stage>` deletes the marker files of `<stage>` and everything downstream. The next `infer_dev_stage` re-derives because the precondition for `<stage>` is now unmet.

```bash
if [ -n "$FROM_STAGE" ]; then
  TICKET_ID=$(git rev-parse --abbrev-ref HEAD | grep -oE '[A-Z]+-[0-9]+' | head -1)
  case "$FROM_STAGE" in
    figma)
      rm -rf .dev/figma-raw .dev/figma-context.md \
             .dev/align-result.md .dev/verify-pass.md
      [ -n "$TICKET_ID" ] && rm -rf "claude-reports/$TICKET_ID"
      ;;
    detect|align)
      rm -f .dev/align-result.md .dev/verify-pass.md
      [ -n "$TICKET_ID" ] && rm -rf "claude-reports/$TICKET_ID"
      ;;
    apply)
      rm -f .dev/verify-pass.md
      [ -n "$TICKET_ID" ] && rm -rf "claude-reports/$TICKET_ID"
      ;;
    verify)
      rm -f .dev/verify-pass.md
      [ -n "$TICKET_ID" ] && rm -f "claude-reports/$TICKET_ID/code-review.md"
      ;;
    review)
      [ -n "$TICKET_ID" ] && rm -f "claude-reports/$TICKET_ID/code-review.md"
      ;;
    ship)
      : # ship is the last stage; nothing downstream to remove
      ;;
    *)
      echo "FAIL: --from <$FROM_STAGE> not recognized" >&2
      exit 1
      ;;
  esac
fi
```

> ⚠️ **Race warning**: do NOT use `--from` while another `/dev:ff` is running on the same worktree. The `rm` and the concurrent writer race; your `--from` intent may be silently lost.

## Step 1: Derive current stage via `infer_dev_stage`

```bash
source "$HOME/.claude/lib/dev-mode.sh"

infer_dev_stage() {
  local n id wt mode
  wt=$(git rev-parse --show-toplevel)
  id=$(git rev-parse --abbrev-ref HEAD | grep -oE '[A-Z]+-[0-9]+' | head -1)

  # Mode dispatch: bug vs feature. Resolved by pipe_mode (lib/dev-mode.sh);
  # .dev/mode.md is written by /dev:start --bug, absent ⇒ feature.
  mode=$(pipe_mode "$wt")
  if [ "$mode" = "bug" ]; then
    infer_bug_stage_safe; return
  fi

  n=$(ls "$wt/openspec/changes" 2>/dev/null | grep -v '^archive$' | head -1)

  # ship complete? Dual signal: archive dir + clean code-review.md + PR OPEN.
  # code-review.md is required to avoid false-positive done when the user
  # manually opened a draft PR before /dev:review ran (PR can be created at
  # any time; code-review.md only exists after a full /dev:review pass).
  if [ -n "$n" ] && [ -d "$wt/openspec/changes/archive/$n" ] \
     && [ -n "$id" ] && [ -f "claude-reports/$id/code-review.md" ] \
     && ! grep -qiE '^critical:' "claude-reports/$id/code-review.md" \
     && gh pr view "$id" --json state -q .state 2>/dev/null | grep -q OPEN; then
    echo done; return; fi

  # review complete? (claude-reports has a code-review.md without ^critical:)
  if [ -n "$id" ] && [ -f "claude-reports/$id/code-review.md" ] \
     && ! grep -qiE '^critical:' "claude-reports/$id/code-review.md"; then
    echo ship; return; fi

  # verify-pass.md exists? Consume — do NOT fall through.
  if [ -f "$wt/.dev/verify-pass.md" ]; then
    if grep -q '^Status: CLEAR' "$wt/.dev/verify-pass.md"; then echo review; return; fi
    if grep -q '^Status: BLOCKED' "$wt/.dev/verify-pass.md"; then echo verify; return; fi
    echo "FAIL: malformed .dev/verify-pass.md (no Status: CLEAR or BLOCKED line)" >&2
    echo "Inspect manually, or /dev:ff --from verify to discard and re-run." >&2
    return 1
  fi

  # apply complete? — real openspec list shape
  if [ -n "$n" ]; then
    local tasks_done
    tasks_done=$(openspec list --json 2>/dev/null \
      | jq -e --arg n "$n" '.changes[] | select(.name==$n) | (.completedTasks == .totalTasks) and (.totalTasks > 0)' \
      > /dev/null 2>&1 && echo "yes")
    if [ "$tasks_done" = "yes" ]; then echo verify; return; fi

    # apply in progress? tasks.md exists with any [x] mark, but not all done
    if [ -f "$wt/openspec/changes/$n/tasks.md" ] \
       && grep -qE '^- \[x\]' "$wt/openspec/changes/$n/tasks.md"; then
      echo apply; return; fi
  fi

  # align complete?
  if [ -f "$wt/.dev/align-result.md" ] \
     && grep -q '^Status: CLEAR' "$wt/.dev/align-result.md"; then
    echo apply; return; fi

  # figma stage: receipt present? Inspect first line for status.
  if [ -f "$wt/.dev/figma-context.md" ]; then
    local first_line
    first_line=$(head -1 "$wt/.dev/figma-context.md")
    case "$first_line" in
      "Fetched: SKIPPED"*)
        echo apply; return ;;                  # no figma source → align has nothing to compare
      "Fetched: FAILED"*)
        echo "FAIL: figma-context.md FAILED first line: $first_line" >&2
        echo "Inspect or /dev:ff --from figma to discard and re-run." >&2
        return 1 ;;
      "Fetched: "*)
        echo align; return ;;                  # normal receipt → align is next
      *)
        echo "FAIL: malformed .dev/figma-context.md first line: $first_line" >&2
        echo "Expected 'Fetched: <ISO|FAILED|SKIPPED> ...'." >&2
        echo "Inspect manually, or /dev:ff --from figma to discard and re-run." >&2
        return 1 ;;
    esac
  fi

  # change scaffolded but figma not yet run
  [ -n "$n" ] && [ -d "$wt/openspec/changes/$n" ] && { echo figma; return; }

  echo start
}

# Bug-mode walker — used when .dev/mode.md says `bug`.
# Skips figma / detect / align entirely. /dev:apply still runs but takes its
# bug-mode branch (Step 0-bug in commands/dev/dev/apply.md): the agent
# investigates, hypothesizes, writes the fix, and commits autonomously.
# Walker advances via .dev/apply-result.md (written by the apply step) just
# like feature mode advances via tasks.md completion.
infer_bug_stage() {
  local id wt
  wt=$(git rev-parse --show-toplevel)
  id=$(git rev-parse --abbrev-ref HEAD | grep -oE '[A-Z]+-[0-9]+' | head -1)

  # ship complete? Dual signal: clean code-review.md + PR OPEN. Bug mode has
  # no openspec archive, so code-review.md is the ONLY non-PR signal that
  # /dev:review actually ran. Without it, a manually-opened draft PR (very
  # common: user pushes early to let CI run) would false-positive to done
  # and skip the entire verify→review→ship chain.
  if [ -n "$id" ] && [ -f "claude-reports/$id/code-review.md" ] \
     && ! grep -qiE '^critical:' "claude-reports/$id/code-review.md" \
     && gh pr view "$id" --json state -q .state 2>/dev/null | grep -q OPEN; then
    echo done; return; fi

  # review complete? (claude-reports has a clean code-review.md)
  if [ -n "$id" ] && [ -f "claude-reports/$id/code-review.md" ] \
     && ! grep -qiE '^critical:' "claude-reports/$id/code-review.md"; then
    echo ship; return; fi

  # verify complete? Consume on existence (same rule as feature walker).
  if [ -f "$wt/.dev/verify-pass.md" ]; then
    if grep -q '^Status: CLEAR' "$wt/.dev/verify-pass.md"; then echo review; return; fi
    if grep -q '^Status: BLOCKED' "$wt/.dev/verify-pass.md"; then echo verify; return; fi
    echo "FAIL: malformed .dev/verify-pass.md (no Status: CLEAR or BLOCKED line)" >&2
    return 1
  fi

  # apply complete? .dev/apply-result.md Status: CLEAR is the bug-mode done marker
  # for the apply stage (parallel to feature mode's tasks.md completedTasks check).
  if [ -f "$wt/.dev/apply-result.md" ]; then
    if grep -q '^Status: CLEAR' "$wt/.dev/apply-result.md"; then echo verify; return; fi
    if grep -q '^Status: FAILED' "$wt/.dev/apply-result.md"; then
      echo "FAIL: .dev/apply-result.md reports FAILED — agent could not complete the bug fix autonomously." >&2
      echo "Inspect .dev/bug-analysis.md and apply-result.md, fix manually, then /dev:ff --from apply to retry." >&2
      return 1
    fi
    # Any other Status (BLOCKED_CLARIFICATION, etc.) → return same stage, dispatch loop will STOP.
    echo apply; return; fi

  # apply not yet run — worktree + mode marker present means /dev:start finished.
  if [ -d "$wt/.dev" ] && [ -f "$wt/.dev/mode.md" ]; then
    echo apply; return; fi

  # No mode marker means /dev:start hasn't run yet.
  echo start
}

# Walker-output whitelist. infer_bug_stage must NEVER emit feature-only stages
# (figma / detect / align) — those stages would invoke /dev:figma etc. which
# perform expensive MCP fetches and assume an openspec change dir exists.
# A typo, a refactor that forgets to update the bug walker, or a stale openspec/
# dir leaking into the bug-mode worktree could all cause the inner walker to
# return an illegal stage. This wrapper catches that before dispatch ever runs.
infer_bug_stage_safe() {
  local out
  out=$(infer_bug_stage) || return $?
  case "$out" in
    start|apply|verify|review|ship|done) echo "$out" ;;
    *)
      echo "FAIL: infer_bug_stage emitted illegal stage '$out'" >&2
      echo "Expected one of: start | apply | verify | review | ship | done" >&2
      echo "Likely cause: walker bug, mode.md parse failure, or stale openspec/changes/ dir in a bug-mode worktree." >&2
      return 1 ;;
  esac
}
```

**Critical rules** (per ai-expert v3 review of plan v3+):
- **Consume on existence**: when a marker file is present, decide based on its content; do NOT fall through.
- **In-progress detection for apply**: distinguish "started" (any `[x]` in tasks.md) from "done" (completedTasks == totalTasks).
- **Malformed verify marker → STOP**, not silent re-run. Walker writes evidence; never overwrite it.

## Step 2: Dispatch loop

Both `done` and the no-op resume cases (HITL gate) end the loop.

```
source "$HOME/.claude/lib/dev-mode.sh"
PIPE_MODE=$(pipe_mode)
CURRENT=$(infer_dev_stage)
while CURRENT != "done":
  # Dispatch-input guard: bug mode MUST NOT dispatch feature-only stages.
  # The walker's whitelist (infer_bug_stage_safe) catches this on the
  # walker-output side; this guard is the redundant second layer that
  # catches mode-dispatch routing errors — e.g. PIPE_MODE was resolved
  # to bug but somehow infer_dev_stage fell into the feature branch
  # (mode.md parse drift, future refactor mistake, etc.).
  if PIPE_MODE == "bug" and CURRENT in {"figma", "detect", "align"}:
    FAIL — print:
      "Dispatch refused: bug pipeline received feature-only stage '$CURRENT'.
       Walker/mode-routing bug. Inspect .dev/mode.md and openspec/changes/
       in this worktree. /dev:ff --from <stage> can re-derive after fixing."
    exit 1

  case CURRENT of:
    "start"       → /dev:start <ticket-id> [--auto] [--no-figma] [--bug if BUG_FLAG]
    "figma"       → /dev:figma   [--auto if AUTO_FLAG]
    "detect"      → /dev:detect  [--auto if AUTO_FLAG]
    "align"       → /dev:align   [--auto if AUTO_FLAG]
    "apply"       → /dev:apply   [--auto if AUTO_FLAG]
    "verify"      → /dev:verify
    "review"      → /dev:review
    "ship"        → /dev:ship    [--auto if AUTO_FLAG]
    (bug mode does not emit "fix-pending" — the agent does the fix
     autonomously inside /dev:apply's Step 0-bug. There is no human-fix gap.)

  After dispatch, re-derive:
    NEW=$(infer_dev_stage)
    if NEW == CURRENT:
        STOP — stage either failed, hit a HITL gate, or terminated default mode.
        Print the stage's stop message; let the user respond.
    if NEW == "done":
        break — pipeline complete.
    CURRENT=$NEW
```

**Note on bug mode stage emission**: `infer_bug_stage` emits `{start, apply, verify, review, ship, done}` — it never returns `figma`, `detect`, or `align`. When bug mode dispatches `apply`, `/dev:apply` reads `.dev/mode.md == bug` and takes its Step 0-bug branch: the agent re-fetches the ticket, investigates, writes the fix, commits, and emits `.dev/apply-result.md`. There is no human-fix HITL gate — bug mode trusts the LLM to handle the full investigation + implementation loop autonomously (the user's HITL gate, in `default` mode only, is to confirm the agent's plan, not to take over the work).

**Failure detection — read the filesystem, not exit codes.** Slash-command sub-invocations don't surface meaningful exit codes; the only reliable signal that a stage finished cleanly is that `infer_dev_stage` advances to a new value. If `NEW == CURRENT`, the stage didn't make progress (failure, HITL gate, or default-mode terminal — handled identically: STOP).

### Loop enforcement — instructions to the executing agent

The dispatch loop above is pseudocode that **you (the agent invoking `/dev:ff`)** must execute by hand: real Bash cannot dispatch slash commands or spawn subagents. This makes loop fidelity entirely a discipline problem, not a runtime guarantee.

CAF-370 (2026-05-11 second-run) failure mode: the `/ggx-dispatcher`-spawned subagent ran `/dev:start` → `/dev:figma` → `/dev:align` → `/dev:apply`, saw "Apply complete." in apply's terminal message, and stopped — never re-ran `infer_dev_stage`, never dispatched `/dev:verify`. Apply finished cleanly (27/27 tasks, tests green), but no commit, no PR, no Linear flip.

Hard rules when you execute this loop:

1. **Stage success is NOT loop terminus.** The only pipeline-terminal conditions are: `infer_dev_stage` echoes `done`; `NEW == CURRENT` (no-progress STOP); a `Status: BLOCKED/FAILED/ABORTED` marker file is written by a stage; or default mode reaches a HITL gate.
2. **Stage-completion messages are IN-LOOP signals.** Strings like `Apply stage done`, `Verify CLEAR`, `Review clean`, `Apply complete. Next: /dev:verify.` mean "this stage finished — proceed to the next one." They do NOT mean "the pipeline finished." Always re-run `infer_dev_stage` after a stage returns. Never treat a stage's output as the loop's final answer.
3. **Re-derive after every stage.** Even if you "know" what the next stage should be, run `infer_dev_stage` again. Stages may have written or cleared marker files in ways that change the next derivation (e.g. `/dev:verify` writes `verify-pass.md` with either `CLEAR` → next is `review`, or `BLOCKED` → next is `verify` again).
4. **When you stop, name the terminal condition.** Report which of (`done`, `NEW == CURRENT at <stage>`, `BLOCKED/FAILED marker at <path>`, `HITL gate at <stage>`) caused the stop. If you can't name one, you stopped early — go back and continue the loop.

### Default-mode terminal (`/dev:apply` exits without verify) — feature mode only

In default mode + feature pipeline, `/dev:apply` does NOT advance to verify (verify/review/ship are auto-only). After `/dev:apply` runs, `infer_dev_stage` returns `verify` (because tasks are all `[x]`) — but the user is not in `--auto`. The dispatch loop checks `AUTO_FLAG` before invoking auto-only stages:

```
source "$HOME/.claude/lib/dev-mode.sh"
PIPE_MODE=$(pipe_mode)

if PIPE_MODE == "feature" and CURRENT in {"verify", "review", "ship"} and AUTO_FLAG == 0:
  STOP — print:
    "Apply complete. Pipeline at <stage>. Default mode terminal — drive next steps manually:
     /format → /commit → /pull-request. To upgrade to auto and chain through verify/review/ship: /dev:ff --auto."
  break
```

This replaces the v7 `done_default` terminal that was tracked in `state.json`. Filesystem-as-state derives the same outcome from "tasks done + no `--auto` flag this invocation".

**Bug mode exception**: when `PIPE_MODE == "bug"`, the default-mode terminal does NOT fire. Bug mode runs end-to-end through `verify` → `review` → `ship` even without `--auto`, because `/dev:apply`'s Step 0-bug branch already commits the fix autonomously — there is no user-decision gap left after apply. The HITL in bug-default is exclusively the plan-confirmation gate inside Step 0-bug, before the apply runs; once that is past, the rest is mechanical (test + audit + push) and runs whether the user passed `--auto` or not. `/dev:verify`, `/dev:review`, and `/dev:ship` each gate their auto-only check on `MODE == auto OR PIPE_MODE == bug` to permit this.

## Mode-specific behavior

- **`--auto`**: no HITL gates fire. Stages stop only on failure. Loop runs end-to-end.
- **default (feature)**: HITL gates in `/dev:figma` (failure case), `/dev:align` (CONFLICT), and `/dev:apply` (review gate) will pause. Terminal at `/dev:apply`. The user resumes with `/dev:ff --auto` (or runs `/format` → `/commit` → `/pull-request` manually).
- **default (bug)**: one HITL gate inside `/dev:apply` Step 0-bug (plan confirmation). After that, runs end-to-end like `--auto`. To skip the plan-confirm gate too, pass `--auto`.

## Failure recovery

- A failed stage leaves its marker either absent or in a `BLOCKED`/`FAILED` state. `infer_dev_stage` returns the same stage on next run; user inspects, fixes, re-runs.
- For a clean reset of one stage: `/dev:<stage> --force`.
- For a clean reset back to an earlier point: `/dev:ff --from <stage>` (deletes markers).

## Output

Final status (whether success or pause): print the resolved `current_stage`, the relevant marker file's `Summary` if applicable, and a one-line "next action" hint.

## Guardrails

- Does NOT bypass any individual stage's inline preconditions — each stage refuses to run if its prerequisite markers are absent.
- Does NOT skip `verify`. The auditor/implementer split is non-negotiable.
- Does NOT modify Git settings, force-push, or operate destructively without explicit user request.
