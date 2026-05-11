---
name: align
description: "Stage 4 (B/C only) — structural Figma alignment check. Verifies that existing OpenSpec artifacts cite the freshly-fetched node IDs AND ground their narrative in tokens from the receipt's Components/Design tokens lists. Catches hallucinated narrative from upstream pipelines (e.g. /port) before /dev:apply builds on top of it."
---

# `/dev:align`

Catches the divergence pattern: artifacts authored by another agent (e.g. `/port`) that copied Figma node IDs as metadata without ever loading the design. Without this gate, `/opsx:apply` runs on top of inferred prose and visual hallucinations propagate into code.

The heavy lifting (full receipt + full artifact prose scan) runs inside `align-subagent` (sonnet, see `agents/dev/align-subagent.md`). This stage's body is dispatch + result parsing + HITL gating.

## Inputs

- `.dev/figma-context.md` (the receipt — passed to subagent).
- `openspec/changes/<change-name>/**/*.md` (read by subagent).

## Outputs

- `.dev/align-result.md` (subagent's structured return). **This file is the stage's done marker.**
- On conflict in auto: `claude-reports/<ticket-id>/figma-alignment.md` (subagent-written) and STOP.
- On conflict in default: HITL prompt with three options.

## Step 0: Inline precondition

```bash
WT=$(git rev-parse --show-toplevel)
TICKET_ID=$(git rev-parse --abbrev-ref HEAD | grep -oE '[A-Z]+-[0-9]+' | head -1)
N=$(ls "$WT/openspec/changes" 2>/dev/null | grep -v '^archive$' | head -1)
CHANGE_DIR="$WT/openspec/changes/$N"
MODE=$(echo "$ARGUMENTS" | grep -q -- '--auto' && echo auto || echo default)

[ -n "$N" ] && [ -d "$CHANGE_DIR" ] || { echo "FAIL: no openspec change directory" >&2; exit 1; }
[ -f "$WT/.dev/figma-context.md" ] || { echo "FAIL: .dev/figma-context.md not found — /dev:figma must run first" >&2; exit 1; }
# If first line is SKIPPED, walker should have routed past align — refuse if we got here anyway.
head -1 "$WT/.dev/figma-context.md" | grep -q '^Fetched: SKIPPED' && { echo "FAIL: figma-context.md is SKIPPED — align has nothing to compare. /dev:ff should route to apply directly." >&2; exit 1; }

# Classify state from real openspec status JSON
status_json=$(openspec status --change "$N" --json 2>/dev/null)
is_complete=$(echo "$status_json" | jq -r '.isComplete')
artifacts_ready=$(echo "$status_json" | jq -r '[.artifacts[].status] | map(select(. == "ready" or . == "complete")) | length')

if [ "$is_complete" = "true" ]; then OS_STATE="B"
elif [ "${artifacts_ready:-0}" -gt 0 ]; then OS_STATE="C"
else OS_STATE="A"; fi

case "$OS_STATE" in B|C) ;; *) echo "FAIL: align requires state B or C, got $OS_STATE" >&2; exit 1 ;; esac
```

## Step 1: Dispatch align-subagent

Spawn `align-subagent` (sonnet) with the three required inputs. Provide them as a single prompt block:

```
receipt_path: .dev/figma-context.md
change_dir: $CHANGE_DIR
ticket_id: $TICKET_ID
```

Wait for the subagent to return. It writes `.dev/align-result.md` atomically and returns control without narrating results in chat (per the contract in `agents/AGENTS.md`).

## Step 2: Parse the result file

```bash
RESULT=".dev/align-result.md"

if [ ! -f "$RESULT" ]; then
  # Per agents/AGENTS.md §6 failure handling: retry once with prefix instructing
  # the subagent to write the result file before returning. On second failure,
  # save its chat output to claude-reports/<ticket_id>/subagent-malformed-align.md and STOP.
  : retry-or-stop
fi

STATUS=$(grep -m1 '^Status:' "$RESULT" | sed 's/^Status:[[:space:]]*//')
SUMMARY=$(grep -m1 '^Summary:' "$RESULT" | sed 's/^Summary:[[:space:]]*//')

case "$STATUS" in
  CLEAR)    : ;;          # proceed to Step 4
  CONFLICT) : ;;          # branch on mode in Step 3
  ABORTED|FAILED)
    echo "FAIL: align-subagent returned $STATUS — $SUMMARY" >&2
    exit 1 ;;
  *)
    echo "FAIL: unknown Status '$STATUS' in $RESULT" >&2
    exit 1 ;;
esac
```

## Step 3: On CONFLICT

The subagent has already written `claude-reports/<ticket_id>/figma-alignment.md` with the per-node conflict list. Branch on mode:

- **`mode == auto`**: STOP. Do NOT run `/opsx:apply` on hallucinated artifacts. The conflict report is the user's recovery surface.
- **`mode == default`**: surface the conflict count from `Summary` and the report path. Use **AskUserQuestion** with three options:
  - `Rebuild affected sections` — call `/opsx:rebuild <change-name> --section <section>` for each conflict (or guide the user to do so), then re-run this stage from Step 1 (re-dispatch the subagent against the regenerated artifacts).
  - `Proceed anyway (accept risk)` — overwrite `.dev/align-result.md` with `Status: CLEAR` (with `Summary: opt-in override after CONFLICT`) and continue. The walker reads CLEAR and advances.
  - `Stop` — end the skill.

## Step 4: Stop

Print: `Figma alignment OK. Next: /dev:apply.`
