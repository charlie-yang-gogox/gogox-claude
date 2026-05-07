---
name: figma
description: "Stage 2 — extract Figma URLs from the Linear ticket, fetch design context for each node, persist raw responses + a hashed receipt, and pass the Step 3b provenance gate. Skipping or faking this stage breaks the verify-pass workflow."
---

# `/dev:figma`

Authoritative Figma fetch + provenance gate for the dev pipeline. Either every Figma URL in the ticket has a real `get_design_context` payload on disk with a matching sha256, or the gate STOPs.

## Inputs

- `.dev/state.json` (read for `ticket_id`, `mode`).
- Linear ticket (re-fetched here — do not trust prior conversation context).
- Figma MCP (`mcp__plugin_figma_figma__*`).

## Outputs

- `.dev/figma-raw/<sanitized-nodeId>.json` — one per fetched node.
- `.dev/figma-context.md` — receipt + summary.
- `state.figma = { node_ids, receipt, raw_dir }`.
- `state.current_stage = "detect"`.

## Step 0: Validate state

Run `/dev:_state-check figma`. If exit != 0, STOP. Parse the emitted JSON for `ticket_id`, `mode`, and `figma`.

**Pre-declared no-source short-circuit**: if `state.figma.declared_no_source == true` (set by `/dev:start --no-figma`), this stage should never have entered — `/dev:start` advances `current_stage` directly to `detect` when the flag is set. If we somehow do enter (e.g. `--from figma` after a `--no-figma` start), refuse:

> "state.figma.declared_no_source is true — user pre-declared no Figma source at /dev:start. To re-enable Figma fetch, restart the pipeline without --no-figma."

STOP. Do not silently overwrite the prior decision.

## Step 1: Re-fetch ticket

Use `mcp__claude_ai_Linear__get_issue` with the `ticket_id` from state. Hold title, description, comments, attachments.

## Step 2: Extract Figma URLs

Scan description, comments, attachments for `figma.com/design/...`. For each match, parse:

- `figma.com/design/:fileKey/:fileName?node-id=:nodeId` → convert `-` to `:` in nodeId.
- `figma.com/design/:fileKey/branch/:branchKey/:fileName` → branchKey acts as fileKey.

Build a list of `<fileKey>:<nodeId>` pairs.

## Step 3: Fetch and persist (when URLs exist)

For each `<fileKey>:<nodeId>`:

1. Call `mcp__plugin_figma_figma__get_design_context` with the fileKey and nodeId.
2. Call `mcp__plugin_figma_figma__get_screenshot` for the screenshot.
3. Call `mcp__plugin_figma_figma__search_design_system` for matching components.
4. Call `mcp__plugin_figma_figma__get_variable_defs` for tokens.
5. Persist the raw `get_design_context` response (entire body, unmodified) to `.dev/figma-raw/<sanitizedNodeId>.json`. Sanitize by replacing `:` with `_` for the filename.
6. Compute sha256: `shasum -a 256 .dev/figma-raw/<sanitizedNodeId>.json | awk '{print $1}'`.

Then write `.dev/figma-context.md`. The first line MUST be the receipt:

```
Fetched: <ISO-8601 UTC> sha256=<rawNodeId1>=<hash1>,<rawNodeId2>=<hash2>
```

Pairs are comma-separated. The `=` between id and hash is the unambiguous separator (node IDs may contain `:`; hashes are hex and never contain `=`). Use the original node ID with `:`, not the sanitized filename form.

The body must include one `### <fileKey>:<nodeId>` section per fetched node, each with:

```
- URL: <original Figma URL>
- Title: <node title from get_design_context>
- Key sections / layers: <bulleted list>
- Components used: <name + library match from search_design_system>
- Design tokens: <token name → value from get_variable_defs>
- Notes / a11y / interaction: <anything else affecting implementation>
```

## Step 4: Failure handling

**Figma API call fails** (after one retry):

- `mode == auto`: write `.dev/figma-context.md` with first line `Fetched: FAILED — <error>` listing attempted URL(s). Step 5 (gate) will STOP.
- `mode == default`: write the same FAILED stub, then **AskUserQuestion**: `Abort` or `Proceed without Figma`.

**No Figma URL found**:

- `mode == auto`: log "No Figma URL found. Proceeding without Figma reference." Do NOT create `.dev/figma-context.md`. Skip to Step 6.
- `mode == default`: **AskUserQuestion** "No Figma URL found. Do you have a Figma link?" If yes, fetch as above. If no, skip to Step 6 and note in artifacts.

## Step 5: Provenance gate (hard block)

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

## Step 6: Commit transition

```bash
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
NODE_IDS_JSON='<JSON array of node IDs, or [] if none>'
RECEIPT='<".dev/figma-context.md" or null>'

jq --arg ts "$TS" --argjson nodes "$NODE_IDS_JSON" --arg receipt "$RECEIPT" '
  .figma = (if $receipt == "null" then null else { node_ids: $nodes, receipt: $receipt, raw_dir: ".dev/figma-raw/" } end)
  | .current_stage = "detect"
  | .stage_history += [{ stage: "figma", status: "done", ts: $ts }]
' .dev/state.json > .dev/state.json.tmp && mv .dev/state.json.tmp .dev/state.json
```

If receipt is null (no Figma URL, default mode user opted out), set `state.figma = null` and still advance to detect.

## Step 7: Stop

Print: `Figma stage complete. Next: /dev:detect`.
