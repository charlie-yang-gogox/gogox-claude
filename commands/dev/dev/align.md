---
name: align
description: "Stage 4 (B/C only) — structural Figma alignment check. Verifies that existing OpenSpec artifacts cite the freshly-fetched node IDs AND ground their narrative in tokens from the receipt's Components/Design tokens lists. Catches hallucinated narrative from upstream pipelines (e.g. /port) before /dev:apply builds on top of it."
---

# `/dev:align`

Catches the divergence pattern: artifacts authored by another agent (e.g. `/port`) that copied Figma node IDs as metadata without ever loading the design. Without this gate, `/opsx:apply` runs on top of inferred prose and visual hallucinations propagate into code.

## Inputs

- `.dev/state.json` (read for `figma.receipt`, `openspec.state`, `openspec.change_dir`, `mode`).
- `.dev/figma-context.md` (the receipt).
- `openspec/changes/<change-name>/**/*.md`.

## Outputs

- On success: `state.current_stage = "apply"`.
- On conflict in auto: `claude-reports/<ticket-id>/figma-alignment.md` and STOP.
- On conflict in default: HITL prompt with three options.

## Step 0: Validate state

Run `/dev:_state-check align`. STOP on non-zero. Parse for `figma.receipt`, `openspec.change_dir`, `mode`, `ticket_id`.

## Step 1: Extract from artifacts

```bash
CHANGE_DIR="<openspec.change_dir>"
grep -rEn 'figma\.com/design/|node-id=|[0-9]+:[0-9]+' "$CHANGE_DIR" || true
```

Build a list of every `<fileKey>:<nodeId>` cited in the artifacts.

## Step 2: For each node in the receipt, run two checks

For every `### <fileKey>:<nodeId>` section in `.dev/figma-context.md`:

**2a. Node ID citation** — confirm the artifacts cite that exact node ID. If an artifact mentions Figma in narrative text but cites no node ID, treat as a conflict (the prose was likely inferred).

**2b. Token grounding (structural rule)** — from the receipt section, extract every token name listed under `Components used:` and `Design tokens:`. The artifacts' narrative for that node MUST cite at least one of those tokens **verbatim** (case-sensitive substring match). If zero tokens match → conflict.

This is an algorithmic check, not LLM-judged "does the prose feel right." Implementation:

```bash
# For each node in the receipt:
TOKENS=$(awk '/^### <fileKey>:<nodeId>/,/^### /' .dev/figma-context.md \
  | sed -nE 's/^- (Components used|Design tokens):\s*(.*)$/\2/p' \
  | tr ',' '\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | grep -v '^$')

HIT=0
for tok in $TOKENS; do
  if grep -rF "$tok" "$CHANGE_DIR" > /dev/null 2>&1; then HIT=1; break; fi
done
# HIT=0 → conflict for this node
```

Adapt the awk range to actual node ID; the goal is one section's tokens at a time.

## Step 3: On conflict

Collect the list of conflicts (per-node: missing citation, missing token grounding, or both).

- **`mode == auto`**: write `claude-reports/<ticket_id>/figma-alignment.md` with the conflict list. STOP — do NOT run `/opsx:apply` on hallucinated artifacts.
- **`mode == default`**: list the conflicts. Use **AskUserQuestion** with three options:
  - `Rebuild affected sections` — call `/opsx:rebuild <change-name> --section <section>` for each conflict (or guide the user to do so), then re-run this stage from Step 1.
  - `Proceed anyway (accept risk)` — record the opt-in in `state.figma.alignment_override = true`, then continue to Step 4.
  - `Stop` — end the skill.

## Step 4: Commit transition

```bash
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
jq --arg ts "$TS" '
  .current_stage = "apply"
  | .stage_history += [{ stage: "align", status: "done", ts: $ts, result: "OK" }]
' .dev/state.json > .dev/state.json.tmp && mv .dev/state.json.tmp .dev/state.json
```

If the user opted in via `Proceed anyway`, set `result: "OK (opt-in override)"`.

## Step 5: Stop

Print: `Figma alignment OK. Next: /dev:apply.`
