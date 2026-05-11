# Subagent discipline

This file is read by every `/dev:*` and `/port:*` subagent in this repo. It encodes three invariants that keep the orchestrator-subagent contract safe to evolve. Per `plans/dev-ff-subagent-isolation.md` §2.

## 1. Stay in your lane

A subagent owns exactly **one** output path (or one guarded directory). Do not write outside it. The result file under `.dev/` (see §6 of the plan) is part of that lane.

Concretely:
- `figma-subagent` writes only `.dev/figma-raw/<node>.json` and `.dev/figma-context.md` (status encoded in the receipt's first line — no separate result file).
- `align-subagent` writes only `.dev/align-result.md` and (on CONFLICT) `claude-reports/<ticket_id>/figma-alignment.md`.
- `dev-agent` writes source / test / `openspec/` files (the project's own lanes) only; status is reported via chat return + tasks.md `[x]` count (no separate result file).
- `verify-agent` writes only `.dev/verify-pass.md`.

If you need to surface information outside your lane, return it via your result file and let the orchestrator (main session) act on it.

## 2. No state.json writes

`state.json` (or any `.dev/state*.json`) is read-write only by the orchestrator. Subagents must never write it. This includes appending to `stage_history`, mutating `current_stage`, or setting any nested field.

A subagent that needs to communicate "I am done / I failed / I need clarification" does so through its result file's `Status:` line, not by mutating shared state.

(Once `plans/ff-state-rationalization.md` lands, `state.json` will be removed entirely; until then this rule is enforced by CI grep.)

## 3. HITL belongs in the main session

No subagent calls `AskUserQuestion`. If a subagent hits a decision branch that needs user input, it returns a sentinel `Status:` (e.g. `BLOCKED_<reason>`, `CONFLICT`) and the orchestrator decides what to do — including, if appropriate, asking the user via `AskUserQuestion` itself.

This is non-negotiable. Repo precedent: `synth-agent.md`, `dev-consult-agent.md`, `verify-agent.md` all follow this rule. Zero subagents declare `AskUserQuestion` in their `tools:` frontmatter.

## CI enforcement (two grep checks)

These are run from `tools/check-agents.sh` (or directly in CI) before any agent change is merged.

### Check A — no subagent writes state.json

```bash
violations=$(grep -nE 'state\.json' agents/**/*.md \
  | grep -v '^agents/AGENTS\.md:' \
  | grep -viE '(do not|never|must not|prohibited|refuses|stops at|read-only|does not (touch|write)).*state\.json')
[ -z "$violations" ] || { echo "FAIL state.json mentions: $violations"; exit 1; }
```

`AGENTS.md` is excluded (it is the spec, not a subagent body). Elsewhere, mentions of `state.json` are allowed only in prohibition contexts (any of the listed phrases must appear before `state.json` on the same line). Case-insensitive (`grep -i`) so "Do NOT", "do not", "Must Not" all match. The bare word "no" is intentionally NOT a prohibition token — too permissive (would pass "no reason state.json should be touched"); use "must not" instead.

### Check B — declared result-file path appears literally in subagent body

```bash
fail=0
while IFS='|' read -r f pin; do
  [ -f "$f" ] || continue   # skip files not yet introduced
  grep -qF "$pin" "$f" || { echo "FAIL: $f does not mention $pin"; fail=1; }
done <<'EOF'
agents/dev/figma-subagent.md|.dev/figma-context.md
agents/dev/align-subagent.md|.dev/align-result.md
agents/dev/verify-agent.md|.dev/verify-pass.md
EOF
# dev-agent.md has no result file — status flows via chat return + tasks.md checkboxes.
# figma-subagent.md's pin is the receipt itself (its first line carries the status enum).
[ "$fail" -eq 0 ]
```

POSIX-portable form (no `declare -A`) — macOS ships bash 3.2 which lacks associative arrays. The `|`-delimited heredoc serves the same map.

A subagent refactor that silently changes its result path would otherwise break the orchestrator's stage-marker check (walker treats stage as undone and re-runs forever). This grep makes such drift loud.

## Result file contract

Every subagent result file under `.dev/` follows this format (per plan §6):

```
Status: <CLEAR | CONFLICT | BLOCKED_<reason> | ABORTED | FAILED | STALLED>
Outputs: <comma-separated absolute paths, or "none">
Summary: <one line, ≤200 chars>
Data: <single-line compact JSON, optional>
```

Atomic write: write `<file>.tmp`, then `mv` to `<file>`. Never write `<file>` directly — a kill mid-write leaves a half-formed file the orchestrator will misparse.
