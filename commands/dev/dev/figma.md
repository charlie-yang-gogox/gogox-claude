---
name: figma
description: "Stage 2 — extract Figma URLs from the Linear ticket, dispatch figma-subagent to fetch design context for each node, then run the provenance gate. Skipping or faking this stage breaks the verify-pass workflow."
---

# `/dev:figma`

Authoritative Figma fetch + provenance gate for the dev pipeline. Either every Figma URL in the ticket has a real `get_design_context` payload on disk with a matching sha256, or the gate STOPs.

The heavy I/O (per-node MCP calls, raw JSON persistence, receipt assembly) runs inside `figma-subagent` (sonnet, see `agents/dev/figma-subagent.md`). This stage's body is URL extraction + dispatch + result parsing + provenance gate + state transition.

## Inputs

- Linear ticket (re-fetched here — do not trust prior conversation context).
- `ticket_id` derived from the branch name.
- `mode` from `$ARGUMENTS` (`--auto` flag).

## Outputs

- `.dev/figma-raw/<sanitized-nodeId>.json` — one per fetched node (subagent-written).
- `.dev/figma-context.md` — receipt + summary (subagent-written). **This file is the stage's done marker.** First line encodes status: `Fetched: <ISO> sha256=...` (success) or `Fetched: FAILED — <error>`.

## Step 0: Inline precondition

```bash
TICKET_ID=$(git rev-parse --abbrev-ref HEAD | grep -oE '[A-Z]+-[0-9]+' | head -1)
[ -n "$TICKET_ID" ] || { echo "FAIL: cannot derive ticket_id from branch name" >&2; exit 1; }
MODE=$(echo "$ARGUMENTS" | grep -q -- '--auto' && echo auto || echo default)
```

**Pre-declared no-source short-circuit**: if `.dev/figma-context.md` exists with first line starting `Fetched: SKIPPED` (written by `/dev:start --no-figma` or by `/dev:start` when no Figma URL was found in the ticket), this stage should never have entered — the walker treats SKIPPED as equivalent to a completed figma stage and routes directly to apply. If we do enter (e.g. `/dev:figma --force` against a SKIPPED worktree), refuse:

> ".dev/figma-context.md first line is `Fetched: SKIPPED` — user pre-declared no Figma source at /dev:start. To re-enable Figma fetch, rm .dev/figma-context.md first, then re-run /dev:figma."

STOP. Do not silently overwrite the prior decision.

## Step 1: Re-fetch ticket

Use `mcp__claude_ai_Linear__get_issue` with `$TICKET_ID`. Hold title, description, comments, attachments.

## Step 2: Extract Figma URLs

Scan description, comments, attachments for `figma.com/design/...`. For each match, parse:

- `figma.com/design/:fileKey/:fileName?node-id=:nodeId` → convert `-` to `:` in nodeId.
- `figma.com/design/:fileKey/branch/:branchKey/:fileName` → branchKey acts as fileKey.

Build a list of `<fileKey>:<nodeId>` pairs.

## Step 3: Failure / no-URL short-circuits (before dispatch)

**No Figma URL found**:

This case should not occur — `/dev:start` is the SOLE writer of `.dev/figma-context.md` first line `Fetched: SKIPPED` and writes it whenever the ticket has no Figma URL. If this stage enters with no URLs, it indicates a `/dev:start` bug. STOP with:

> "FAIL: figma stage entered with no URLs in ticket. /dev:start should have written `.dev/figma-context.md` with `Fetched: SKIPPED` first line. Recovery: write that file manually, then /dev:ff to resume."

## Step 4: Dispatch figma-subagent

When the URL list is non-empty, spawn `figma-subagent` (sonnet, worktree-isolated) with the three required inputs:

```
ticket_id: <ticket_id>
urls: <fileKey1>:<nodeId1>, <fileKey2>:<nodeId2>, ...
worktree_path: <abs path>
```

Wait for it to return. The subagent writes `.dev/figma-raw/*.json` and `.dev/figma-context.md` atomically and returns control without narrating in chat (per `agents/AGENTS.md`).

### Step 4a: Parse the receipt's first line for status

```bash
CTX=".dev/figma-context.md"

if [ ! -f "$CTX" ]; then
  # Subagent didn't write it — retry once with prefix instructing it to write the file
  # before returning. On second failure, save chat to claude-reports/<ticket_id>/subagent-malformed-figma.md and STOP.
  : retry-or-stop
fi

FIRST=$(head -1 "$CTX")

case "$FIRST" in
  "Fetched: SKIPPED"*)
    echo "FAIL: figma stage saw SKIPPED first line — /dev:start should have routed past figma. /dev:ff resume to recover." >&2
    exit 1 ;;
  "Fetched: FAILED"*)
    if [ "$MODE" = "default" ]; then
      # AskUserQuestion: Abort or Proceed without Figma
      :
    else
      echo "FAIL: figma-subagent reported FAILED — $FIRST" >&2
      exit 1
    fi
    ;;
  "Fetched: "*)
    : ;;          # success — proceed to Step 5 (provenance gate)
  *)
    echo "FAIL: malformed first line '$FIRST' — expected 'Fetched: ...'" >&2
    exit 1 ;;
esac
```

## Step 5: Provenance gate (hard block — main session, not subagent)

The gate runs in the orchestrator, not the subagent. Reasoning: the gate is read-only verification (file presence, sha256 cross-check, body coverage) — keeping it in main means the subagent cannot fake its own provenance.

Run all four checks in order. None can be skipped:

1. **File presence** — if a Figma URL was detected in Step 2, `.dev/figma-context.md` MUST exist. Missing → STOP.
2. **Failure stub** — if first line starts with `Fetched: FAILED`:
   - `auto`: STOP.
   - `default`: STOP and **AskUserQuestion** whether to proceed; record opt-in.
3. **Hash & content** — parse the receipt's hash portion in this exact order:
   1. **Strip the leading `sha256=` literal** from the hash portion of the receipt line. Without this step a naive splitter leaves the first pair as `sha256=<id1>=<hash1>` (two `=` characters) and "split on last `=`" works by luck rather than by contract.
   2. Split the remainder on `,` to get individual `<id>=<hash>` pairs.
   3. For each pair, split on the LAST `=` (since node IDs may contain `:` but hashes are hex and never contain `=`).

   Then for each pair:
   - Recompute `shasum -a 256 .dev/figma-raw/<sanitizedNodeId>.json | awk '{print $1}'` and compare. Mismatch → STOP.
   - The raw `<nodeId>` must appear at least once in the body. Missing → STOP.
   - Every `<fileKey>:<nodeId>` parsed in Step 2 must have an entry in the receipt AND a `### <fileKey>:<nodeId>` section. Any missing → STOP.
4. **Anti-pattern reminder** — `grep`-ing existing OpenSpec artifacts for figma node IDs does NOT satisfy this gate. The only acceptable proof is `.dev/figma-context.md` written this run.

## Step 6: Stop

No state mutation needed. The presence of `.dev/figma-context.md` with `Fetched: <ISO>` first line is the done marker that `infer_dev_stage` reads. (A `Fetched: SKIPPED` first line — written by `/dev:start` — also counts as "figma stage settled" and the walker routes past align directly to apply.)

Print: `Figma stage complete. Next: /dev:detect`.
