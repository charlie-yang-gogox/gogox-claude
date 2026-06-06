---
name: port:plan
description: >
  Wave-2 of the port pipeline. Spawns pm-agent and designer-agent IN
  PARALLEL (single message, two Agent calls) to produce
  `.port/pm-notes.md` and `.port/design-notes.md` from the dev-notes
  grounding plus ticket / PRD / Figma context. Both agents must use
  bold labeled IDs (FR-N / AC-N / AP-N for pm, AG-N for designer) for /spec-lint traceability.
---

# /port:plan — PM + Designer Notes

Run `pm-agent` and `designer-agent` in parallel against the dev-notes grounding to produce `.port/pm-notes.md` and `.port/design-notes.md`. After this stage `/port:synth` has every input it needs.

**Usage**: `/port:plan [--ticket:<ID>] [--force] [--auto]`

- `--ticket:<ID>` — Override auto-detection. Otherwise resolved per §4.4.
- `--force` — Skip the staleness check on existing pm-notes / design-notes. Always regenerate both.
- `--auto` — Unattended mode. Skip every `AskUserQuestion`; resolve gates per the auto-decision table.

---

## Steps

1. **Resolve ticket-id (§4.4).**
   - If `--ticket:<ID>` provided → use it.
   - Else basename of `git rev-parse --show-toplevel` regex `[A-Z]+-[0-9]+`.
   - Else `git branch --show-current` regex `[A-Z]+-[0-9]+`.
   - Else HITL → `AskUserQuestion`. `--auto` → STOP with: `cannot resolve ticket-id; pass --ticket:<ID>`.

2. **Locate worktree + change dir.**
   - Confirm cwd is the ticket worktree. Find `openspec/changes/<change-name>/.port/`. Hold `<change-name>` and `<port-dir>`.
   - `<port-dir>/dev-notes.md` MUST exist — missing → STOP with: `dev-notes.md missing — run /port:explore first`.

3. **Fetch ticket + figma URL.**
   - `mcp__claude_ai_Linear__get_issue` for the ticket. Store `<ticket-context>`.
   - Extract first regex match `figma\.com/\S+` from description into `<figma-url>` (else empty).

4. **Read PRD (optional).**
   - If `<port-dir>/prd.md` exists, read into `<prd-text>`. Else `<prd-text>` = empty.

5. **Stale check (D18).**
   - Targets: `<port-dir>/pm-notes.md`, `<port-dir>/design-notes.md`. For each existing file, mtime older than `git log -1 --format=%ct HEAD` by more than 3600 s = stale.
   - If `--force` → ignore freshness, regenerate both.
   - HITL: any stale or missing → `AskUserQuestion`: `reuse existing / regenerate both`. `reuse` → skip to step 9 if both exist.
   - `--auto`: any stale or missing → regenerate both. Log: `Auto: regenerating pm-notes/design-notes.`

6. **Spawn `pm-agent` and `designer-agent` IN PARALLEL.**
   - This is non-negotiable: send a SINGLE message containing TWO Agent tool calls. Do NOT issue them sequentially. Sequential dispatch defeats the parallelism that motivates Wave 2 design.
   - In `--auto` mode include `mode: "bypassPermissions"` on both calls.

   **`pm-agent` call** — `subagent_type: pm-agent`, `model: sonnet`:
   ```
   CONSULT MODE: write only to <output-path>. Do not write any other OpenSpec file.
   Assume aggressively — document assumptions inline instead of asking. The
   orchestrator synthesises artifacts from your notes.

   ## Ticket
   <ticket-context>

   ## Wave 1 grounding (READ FIRST)
   <port-dir>/dev-notes.md
   Ground every functional requirement in its Source Analysis section.

   ## PRD (if any)
   <prd-text or "(none)">

   ## Output path
   <port-dir>/pm-notes.md

   Write structured PM consult notes with sections:
   ## Problem & Motivation
   ## User Stories
   ## Acceptance Criteria Mapping  (table: AC | Source | Testable? | Notes)
   ## Proposed Capabilities  (kebab-case, new/modified, one-line description; backtick the names)
   ## Functional Requirements (draft)  (numbered, grounded in dev-notes)
   ## Out of Scope
   ## Assumptions  (every gap you filled — what / why / downstream impact)

   Numbered items MUST use bold labeled IDs:
     - Functional Requirements → **FR-1**, **FR-2**, ...
     - Acceptance Criteria rows → **AC-1**, **AC-2**, ...
     - Assumption bullets       → **AP-1**, **AP-2**, ... as structured
       `ri:v1` records (see your agent definition's "Assumptions" section —
       AP namespace, kind/sev/verify marker; empirical items are verify=unconfirmed).
   /spec-lint cites these IDs across artifacts. Drop the bold-and-ID format
   and downstream traceability checks misfire.

   Do NOT emit a `## Open Questions` section.
   ```

   **`designer-agent` call** — `subagent_type: designer-agent`, `model: sonnet`:
   ```
   CONSULT MODE: write only to <output-path>. Do not write any other OpenSpec file.
   Assume aggressively — document assumptions inline.

   ## Ticket
   <ticket-context>

   ## Wave 1 grounding (READ FIRST)
   <port-dir>/dev-notes.md

   ## Figma URL
   <figma-url or "none">

   ## PRD (if any)
   <prd-text or "(none)">

   ## Output path
   <port-dir>/design-notes.md

   Write design consult notes with sections:
   ## Screen Inventory
   ## User Flow
   ## Component Mapping  (table)
   ## Design System Gaps
   ## Figma Findings  (call figma MCP if URL present, else "none")
   ## Interaction & State Notes
   ## Adaptation Recommendations
   ## Assumptions  (what / why / downstream impact)

   Numbered items MUST use bold labeled IDs (`**AG-1**`, `**AG-2**`, ...) for
   every assumption, written as structured `ri:v1` records (see your agent
   definition's "Assumptions" section — AG namespace, kind/sev/verify marker;
   empirical items are verify=unconfirmed). If you introduce numbered design
   decisions, label them `**D-1**`, ... (free-form prose, not ri:v1).
   /spec-lint checks these IDs against artifacts; dropping the format breaks
   traceability.

   If the feature has no UI scope, write a short "No design scope —
   backend/data-only feature" note under each section and stop.
   Do NOT emit `## Open Questions`.
   ```

7. **Wait for both, handle failure.** Here "fail/empty" explicitly includes the case where the `Agent` tool itself is unavailable or errored (nested spawns are officially unsupported — see `ARCHITECTURE.md` "Nested-spawn constraint"); the existing retry-once (pm) / retry-once + placeholder (designer) paths below already cover it, so /port:plan adds no inline-fallback machinery (excluded per the `/port:plan` note under the R1–R5 table in `ARCHITECTURE.md` "Nested-spawn constraint" — its parallel-dispatch invariant resists inlining).
   - HITL:
     - `pm-agent` fail/empty → `AskUserQuestion`: `retry / abort` (skipping is fatal).
     - `designer-agent` fail/empty → `AskUserQuestion`: `retry / skip / abort`. On `skip`, atomic-write a placeholder note: `# Design notes unavailable\n\nAgent failed; user opted to skip. Treat as backend-only.\n` to `design-notes.md`.
   - `--auto`:
     - `pm-agent` → retry once, second failure → STOP with auto abort + Linear comment.
     - `designer-agent` → retry once, second failure → write placeholder (same content as the HITL `skip` path) and proceed. Log the failure in timings (`outcome:"ok-design-skipped"`).

8. **Atomic-write outputs.**
   - Both agents write directly via the `Write` tool (atomic by replace). Verify both files exist after agent return; missing → escalate per step 7.
   - If the orchestrator itself rewrites either file (e.g., after the placeholder path), use the standard `mktemp` + `mv` pattern.

9. **Append timings JSONL.**
   - Append one line to `<port-dir>/timings.jsonl`:
     ```json
     {"stage":"plan","ticket":"<ticket-id>","start":"<ISO-start>","end":"<ISO-end>","duration_ms":<int>,"outcome":"ok"}
     ```
   - Use `outcome:"ok-design-skipped"` for the auto-mode skipped-designer path.

10. **Print next-step hint.**
    ```
    pm-notes.md     : <port-dir>/pm-notes.md
    design-notes.md : <port-dir>/design-notes.md (or "(placeholder — designer skipped)")

    Next: /port:synth
    ```

---

## Atomic write pattern (D16)

Each agent uses the `Write` tool which performs an atomic replace. When the orchestrator must write or rewrite a `.port/*.md` file (e.g., the design-skip placeholder, or any rewrite after a clarification round), it MUST use:

```bash
tmp=$(mktemp)
# generate full file content into $tmp
mv "$tmp" "<port-dir>/<file>.md"
```

POSIX `mv` on the same filesystem is atomic. This protects existence-implies-completion (D1) from partial writes if the stage crashes mid-generation.

---

## Guardrails

- Wave 2 NEVER runs without `dev-notes.md`. If absent → STOP with `run /port:explore first`.
- The two agent calls MUST be issued in a SINGLE message (parallel dispatch). Sequential calls are a defect, not an optimization opportunity.
- Both agents are `model: sonnet`. Their job is structured summarization from existing inputs — opus would burn tokens for no quality gain.
- Both agent prompts include the labeled-ID convention reminder (`**FR-N**`, `**AC-N**`, pm `**AP-N**`, designer `**AG-N**`). `/spec-lint` Check 8 fires when notes lack labeled IDs; that's the early-warning for prompt drift.
- Designer always runs. A truly backend-only feature gets a "no design scope" note (still uses `**AG-N**` for any assumptions).
- All Linear MCP calls use `mcp__claude_ai_Linear__*`.
- `.port/` writes are atomic via `mktemp` + `mv` (D16).
- This stage never spawns synth, never validates, never lints — those belong to `/port:synth` (PR 4b).
- This stage never edits the Linear ticket — only `/port:ship` writes back to Linear.
