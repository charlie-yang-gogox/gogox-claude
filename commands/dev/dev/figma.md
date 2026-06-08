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
- `.dev/figma-context.md` — receipt + summary (subagent-written, or main-session-written on the Step 4b inline fallback). **This file is the stage's done marker.** First line encodes status: `Fetched: <ISO> sha256=...` (success) or `Fetched: FAILED — <error>`. When the inline fallback writes the file, **line 2** carries `Provenance: inline-fallback (figma-subagent contract run in main session; spawn unavailable)` — a legal part of the receipt that every downstream parser ignores (they read either `head -1` or `### <fileKey>:<nodeId>` body sections).

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
  # Subagent returned but wrote no file — retry once with prefix instructing it to write
  # the file before returning. Still no file → fall to the Step 4b inline fallback.
  # (If the Agent call itself errored / was unavailable, skip the retry and go straight
  # to Step 4b — retrying an unavailable spawn is pointless. See the failure ladder there.)
  : retry-or-fallback
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

### Step 4b: Inline fallback (one-time, on spawn failure)

Nested level-2 spawns are officially unsupported (`agents/AGENTS.md`; sub-agents docs: subagents cannot spawn other subagents). `figma-subagent` is a sonnet spawn that works in practice today but is undefined behavior — see `ARCHITECTURE.md` "Nested-spawn constraint" R2. When the spawn does not produce a receipt, this stage degrades to running the subagent's contract inline instead of hard-stopping. The failure ladder (the inline fallback is a **sibling branch** of the existing retry, not a replacement):

```
spawn → Agent call itself errors / unavailable?     → inline fallback now (retry is pointless)
      → returned but no .dev/figma-context.md?       → existing retry-once → still no file? → inline fallback
      → inline also produces no legal receipt?        → subagent-malformed-figma.md + STOP (no loop)
```

**A present receipt with `Fetched: FAILED` is NOT a spawn failure.** A `.dev/figma-context.md` whose first line is `Fetched: FAILED — <error>` means the subagent ran to completion and is honestly reporting an MCP outage. Do NOT run the fallback on it — Step 4a's FAILED branch owns that case (re-fetching inline would mask a real outage). The fallback fires only when the file is **absent**, or the `Agent` call itself errored.

**Precondition — figma MCP must be reachable inline.** The subagent contract pins the `mcp__plugin_figma_figma__*` tool prefix, but the session running this fallback (in `--auto`, the `/ggx-dispatcher`-spawned `general-purpose` worker) may expose the Figma MCP under a different prefix (e.g. `mcp__claude_ai_Figma__*`) or not at all. Resolve the connected Figma MCP prefix at runtime before fetching; if no Figma MCP tool is reachable in the current session, do NOT fake a fetch — atomic-write `Fetched: FAILED — figma MCP unavailable inline` as the receipt's first line (a legal receipt; Step 4a's FAILED branch then handles it honestly) and stop the fallback there.

**Inline execution.** In the current session, run the `agents/dev/figma-subagent.md` contract verbatim against the same `urls` list: per-node `get_design_context` / `get_screenshot` / `search_design_system` / `get_variable_defs` MCP fetch, persist each raw `get_design_context` body to `.dev/figma-raw/<sanitizedNodeId>.json` (atomic `mktemp` + `mv`), compute sha256, then atomic-write `.dev/figma-context.md` with the **identical first-line format** (`Fetched: <ISO> sha256=<id1>=<hash1>,...` on success, `Fetched: FAILED — <error>` on MCP failure) and the same `### <fileKey>:<nodeId>` body sections (including the `Components used:` / `Design tokens:` lines that `align-subagent` reads). Nothing about the file contract changes — the only difference is which session wrote it. **Context cost, stated honestly:** the fallback trades context safety for spawn resilience — the raw payloads the subagent exists to absorb now land in this session's window. Persist each raw body to disk immediately and do not retain it in working memory after its sha256 is computed (read once, write, discard). A high-node-count ticket hitting this fallback is a data point for the R5 Workflow revisit.

**Provenance — line 2, never line 1.** Write `Provenance: inline-fallback (figma-subagent contract run in main session; spawn unavailable)` as the receipt's **second** line. This is verified inert against every downstream consumer: `ff.md`'s walker and Step 4a both `head -1` the first line; Step 5's gate greps `### <fileKey>:<nodeId>` body sections and the `sha256=` portion of line 1; `align.md` reads `head -1`. None of them read line 2. (Deliberately different from align's marker: this receipt is free-form markdown so a bare-prefix line 2 is safe; `.dev/align-result.md` is a strict line-anchored contract, so its provenance is a trailing `#` comment instead — see `align.md` Step 2b. If you ever touch one format, check the other.)

**Re-verify, then proceed.** After the inline write, re-run the Step 4a first-line parse on the file you just wrote, then fall through to Step 5. The two nets divide the work: Step 4a's re-parse catches a FAILED or malformed first line (and `exit 1`s before Step 5 is ever reached); Step 5's gate additionally catches the subtler case of a well-formed receipt whose sha256 cross-check or body coverage is wrong.

**One-time guard** (mirrors `commands/dev/dev/apply.md:334`): the inline fallback runs at most once per `/dev:figma` invocation. If the inline run also fails to produce a legal receipt (first line not parseable as `Fetched: ...`), save the context to `claude-reports/<ticket_id>/subagent-malformed-figma.md` and STOP — do not loop. An inline run that completes with a legal `Fetched: FAILED — <error>` first line is NOT this guard's case — that receipt is terminated by Step 4a's FAILED branch on re-parse.

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
