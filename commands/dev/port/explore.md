---
name: port:explore
description: >
  Wave-1 of the port pipeline. Produces `.port/dev-notes.md` (Locate + Source
  Analysis + Porting Recommendations + Risks + Assumptions) and hardens the
  Locate gate with deterministic file/symbol existence checks against the
  origin codebase. Mode-conditional execution: default spawns
  dev-consult-agent (opus, worktree-isolated) via the Agent tool; --auto runs
  the dev-consult logic inline in the current session (no nested-Agent
  spawn — required because /ggx-dispatcher invokes this via a general-purpose
  subagent; officially unsupported, see ARCHITECTURE.md "Nested-spawn
  constraint"). `--simple` mode runs a lean variant with no worktree, no
  OpenSpec scaffold, and an interactive Q&A loop that posts the final
  analysis as a Linear comment.
---

# /port:explore — Origin Codebase Consult

Run dev-consult-agent against the origin project to ground the rest of the port pipeline. Produces `.port/dev-notes.md` (full mode) or a marker-wrapped Linear comment (`--simple` mode).

**Usage**: `/port:explore [--ticket:<ID>] [--simple] [--force] [--auto]`

- `--ticket:<ID>` — Override auto-detection. Otherwise resolved per §4.4 of the plan.
- `--simple` — Lightweight mode (no worktree, no OpenSpec). Posts the analysis to Linear instead of writing dev-notes.
- `--force` — Skip the staleness check on existing `.port/dev-notes.md`. Always regenerate.
- `--auto` — Unattended mode. Skip every `AskUserQuestion`; resolve gates per the auto-decision table.

`--simple` and `--auto` are mutually exclusive — `--simple` requires interactive Q&A.

---

## Mode detection

Parse `$ARGUMENTS`. If `--simple` is present, jump to **Simple mode** below. Otherwise run **Full mode**.

---

## Full mode

### Steps

1. **Resolve ticket-id (§4.4).**
   - If `--ticket:<ID>` provided → use it.
   - Else basename of `git rev-parse --show-toplevel` regex `[A-Z]+-[0-9]+` → use match.
   - Else `git branch --show-current` regex `[A-Z]+-[0-9]+` → use match.
   - Else HITL → `AskUserQuestion` for ticket ID. `--auto` → STOP with: `cannot resolve ticket-id; pass --ticket:<ID>`.

2. **Locate worktree + change dir.**
   - Confirm cwd is the ticket worktree (basename matches ticket-id). If not, STOP with: `run /port:explore from inside the ticket worktree, or pass --ticket:<ID>`.
   - Find `openspec/changes/<change-name>/.port/` (single directory). Zero or multiple → STOP with a directive to re-run `/port:start` or pass `--ticket:`. Hold `<change-name>` and `<port-dir>`.

3. **Resolve origin project path.**
   - Read `<repo-root>/.claude/port-settings.json`. Expand `~` and `$ENV_VAR` in `originalProjectPath`. Path missing OR not a directory:
     - HITL → `AskUserQuestion`, validate, atomic-write back via `mktemp` + `mv` (writing the JSON `{ "originalProjectPath": "<answer>" }`).
     - `--auto` → STOP with: `set originalProjectPath in .claude/port-settings.json before re-running`.

4. **Read PRD (optional).**
   - If `<port-dir>/prd.md` exists, read into `<prd-text>`. Else `<prd-text>` = empty.

5. **Stale check (D18).**
   - If `<port-dir>/dev-notes.md` exists AND `--force` is not set:
     - Compare its mtime to `git log -1 --format=%ct HEAD`. Stale = mtime is more than 3600 s older than HEAD.
     - HITL → `AskUserQuestion`: `reuse / regenerate`. On `reuse`, skip to step 9.
     - `--auto` → auto-regenerate. Log: `Auto: regenerating stale dev-notes.md`.

6. **Run dev-consult (mode-conditional).**

   The dev-consult contract — Locate the feature in the origin codebase, write `<port-dir>/dev-notes.md` per the `agents/dev/dev-consult-agent.md` definition (Locate / Source Analysis / Current Project Fit / Reuse Opportunities / Porting Recommendations / Migration Notes / Risks & Unknowns / Assumptions, with the bold labeled-ID convention `**R-N**` / `**P-N**`) — runs differently depending on mode:

   | Mode | Execution path | Why |
   | -- | -- | -- |
   | `default` | spawn `dev-consult-agent` via the `Agent` tool — `subagent_type: dev-consult-agent`, `model: opus`, `isolation: worktree` | Main session has `Agent` available; isolating into a worktree-scoped opus subagent keeps the heavy context out of the orchestrator. |
   | `--auto` | run the dev-consult logic **inline** in the current session — no `Agent` spawn | `--auto` may run inside a `general-purpose` subagent dispatched by `/ggx-dispatcher`. Nested-Agent spawns from a subagent are officially unsupported (see `ARCHITECTURE.md` "Nested-spawn constraint") — inlining is the only safe path. Same constraint as `/dev:apply` (see `commands/dev/dev/apply.md:15-17`). |

   **`default` mode — spawn the agent.** Include `mode: "bypassPermissions"` on the call only if any caller passed it through; otherwise omit. Pass this prompt body (verbatim shape — fill placeholders):

   ```
   CONSULT MODE: write only to <output-path>. Do not write any other OpenSpec file.

   ## Ticket
   <ticket-context>

   ## PRD (user-provided, if any)
   <prd-text or "(none — infer from ticket title alone)">

   ## Original project path
   <origin_project_path>

   ## Current project worktree
   <worktree-path>

   ## Output path
   <port-dir>/dev-notes.md

   Produce notes per your agent definition. First section MUST be `## Locate`
   with confidence (high/medium/low) and candidate matches.

   Numbered items in `## Risks & Unknowns` and `## Porting Recommendations`
   MUST use bold labeled IDs — `**R-1**`, `**R-2**`, `**P-1**`, `**P-2**`.
   Artifacts cite these IDs later for traceability; dropping the bold-and-ID
   format will trigger /spec-lint failures.
   ```

   **`--auto` mode — inline execution.** Do NOT call the `Agent` tool. The current session executes the dev-consult contract directly:
   1. Read `agents/dev/dev-consult-agent.md` once (`<repo-root>/agents/dev/dev-consult-agent.md` from this gogox-claude repo). Treat its `## Output format — MANDATORY section order`, `### Labeled-ID convention`, and `## Guardrails` sections as binding.
   2. Resolve the active repo profile per Step 0 of the agent definition (`.gogox-claude.yaml` → `platform` + `product`; fall back to `~/.claude/commands/profiles/registry/`).
   3. Inspect the origin codebase at `<origin_project_path>` and the current worktree at `<worktree-path>` using `Grep` / `Glob` / `Read` / `Bash`. Locate the feature from the ticket title + description; analyse it at the behavioural level.
   4. Compose the full notes content. First section MUST be `## Locate` with `Confidence: high|medium|low`. Numbered items in `## Risks & Unknowns` and `## Porting Recommendations` MUST use bold labeled IDs (`**R-1**`, `**P-1**`, ...) — these are cited downstream by `/spec-lint` and missing IDs will trigger lint failures.
   5. Atomic-write to `<port-dir>/dev-notes.md` via the D16 pattern:
      ```bash
      tmp=$(mktemp)
      # write composed notes content to $tmp via the Write tool
      mv "$tmp" "<port-dir>/dev-notes.md"
      ```
   6. Do NOT write to any other file — no OpenSpec artifacts, no Linear comment, no edits under `lib/` / source / test paths. The guardrails in `agents/dev/dev-consult-agent.md` apply verbatim to this inline path.

   **Failure handling (both modes).**
   - HITL: `AskUserQuestion`: `retry / abort` (skip is not offered).
   - `--auto`: retry once. Second failure → STOP with the auto-mode abort message + Linear comment per the §4.3 contract in `/ggx-work`.
   - In `--auto` inline mode, "failure" means the file was not written, the `## Locate` section is missing, or step 8's Locate-gate hardening flags an unrecoverable miss before the retry.

7. **Verify the output file.**
   - `default` mode: the agent owns the write; the agent prompt mandates the `mktemp` + `mv` pattern (`Write` tool already does atomic replace).
   - `--auto` mode: the orchestrator owns the write per step 6.5 above.
   - In both cases, verify `<port-dir>/dev-notes.md` exists after step 6 returns. Absent → treat as step 6 failure (retry per the failure-handling block).

8. **Locate gate hardening (D6).**
   - Parse the `## Locate` section: `<confidence>`, `<primary-match>` path, optional `<alternatives>` paths, `<main-symbols>` (any code identifier mentioned in `Why:` or in the Source Analysis section).
   - For the primary path: `[ -f "$ORIGIN/<primary-match>" ] || [ -d "$ORIGIN/<primary-match>" ]`. Miss → record `path-miss` finding.
   - For each main symbol: `grep -rn "<symbol>" "$ORIGIN/<primary-match>"`. Empty → record `symbol-miss` finding.
   - Any `path-miss` OR `symbol-miss` → downgrade confidence one tier: high → medium, medium → low, low stays low. Log every downgrade with the offending path/symbol.

9. **Confidence routing.**
   - `high` → proceed.
   - `medium`:
     - HITL → `AskUserQuestion`: `confirm primary / pick alternative <path> / clarify`. On `clarify`, capture free-form input, re-invoke `dev-consult-agent` with: `User clarified: <input>. Redo Locate + Source Analysis focused on this target.` Replace `dev-notes.md` (atomic via the agent's Write).
     - `--auto` → proceed with primary, log: `Auto: locate confidence=medium, proceeding (flagged for PR review).`
   - `low`:
     - HITL → `AskUserQuestion`: `pick alternative <path> / clarify`. On `clarify`, re-invoke as above; on alternative, instruct the agent to redo Locate + Source Analysis on that path.
     - `--auto` → ABORT. Post Linear comment: `Port aborted: locate confidence too low. Primary candidate: <path>. Add context to the ticket and re-add ready-to-port label to retry.` Append timings JSONL with `outcome:"aborted-low-confidence"`. STOP.

10. **Append timings JSONL.**
    - Append one line to `<port-dir>/timings.jsonl`:
      ```json
      {"stage":"explore","ticket":"<ticket-id>","start":"<ISO-start>","end":"<ISO-end>","duration_ms":<int>,"outcome":"ok"}
      ```

11. **Print next-step hint.**
    ```
    dev-notes.md ready: <port-dir>/dev-notes.md
    Locate confidence: <final-confidence> (downgrades: <list or "none">)

    Next: /port:plan
    ```

---

## Simple mode (`--simple`)

Lightweight explore-and-discuss. NO worktree creation, NO OpenSpec scaffold, NO Locate-gate hardening, NO timings file. Output is a Linear comment.

### Steps

1. **Parse `--ticket:<ID>` (required for simple mode).**
   - Missing → STOP with: `Usage: /port:explore --simple --ticket:<ID>`.

2. **Resolve profile + origin path.**
   - Same resolution as full mode steps 2–3 (read `.gogox-claude.yaml` + `.claude/port-settings.json`, expand path, prompt+write-back if missing).

3. **Fetch ticket.**
   - `mcp__claude_ai_Linear__get_issue`. Capture title / description / labels / AC into `<ticket-context>`.

4. **Spawn `dev-consult-agent` (lean prompt).**
   - Agent settings: `subagent_type: dev-consult-agent`, `model: opus`. NOT inside a worktree.
   - Prompt:
     ```
     You are analyzing a feature in the origin codebase that may be ported.
     This is SIMPLE MODE — produce a feature analysis only. Do not write
     any file; return your analysis in your final message.

     ## Ticket
     <ticket-context>

     ## Original project path
     <origin_project_path>

     Produce sections:
     ## Feature Overview
     ## User-Facing Behavior
     ## Business Rules   (numbered, exhaustive)
     ## Data Models
     ## API Contracts
     ## UI States & Flows
     ## Open Questions   (this is the only mode where Open Questions is allowed)

     Focus on WHAT the feature does, not HOW the origin app implements it.
     Do not describe origin-specific architecture patterns.
     ```
   - Capture the agent's final message as `<feature-analysis>`.

5. **Interactive Q&A loop.**
   - Parse the `## Open Questions` section. If empty, skip to step 6.
   - Loop:
     - Batch up to 4 questions per `AskUserQuestion`. Show: `Q1: ... / Q2: ... / Q3: ... / Q4: ...`.
     - For each answered question, edit `<feature-analysis>` in memory: incorporate the answer into the relevant section, remove from `## Open Questions`.
     - For `skip` / `not sure`, keep the question with suffix `(unresolved — needs PM/designer input)`.
     - If a follow-up question arises, append to the queue. Loop until the queue is empty (typically 1–2 rounds).

6. **Re-fetch ticket + post Linear comment.**
   - `mcp__claude_ai_Linear__get_issue` again to read the current description (avoid clobbering PM edits since step 3).
   - Build the marker block:
     ```
     <!-- port:simple:start -->
     > Feature analysis generated by `/port:explore --simple` on <YYYY-MM-DD>.
     > Re-running replaces only the content between these markers.

     <feature-analysis after Q&A>
     <!-- port:simple:end -->
     ```
   - Post via `mcp__claude_ai_Linear__save_comment` (`issueId: <ticket-id>`, `body: <block>`). Use a fresh comment per run — the markers identify the block for any later automation.

7. **Print final hint.**
   ```
   Analysis posted to <ticket-id>.
   Open questions remaining: <N or "none">

   Run /port:start --ticket:<ticket-id> for full mode.
   ```

---

## Atomic write pattern (D16)

The dev-consult agent owns the `Write` to `dev-notes.md`. The agent prompt instructs it to write directly; the `Write` tool replaces atomically. When the orchestrator itself rewrites the file (e.g., after a re-invocation following user clarification), use:

```bash
tmp=$(mktemp)
# write new content to $tmp
mv "$tmp" "<port-dir>/dev-notes.md"
```

The local-yaml write-back uses the same pattern — never write to the yaml in place.

---

## Guardrails

- Full mode requires the cwd to be inside a ticket worktree with an existing `openspec/changes/<change-name>/.port/`. Pre-`/port:start` invocations STOP early.
- Ticket-id resolution (§4.4): cwd basename → branch name → ask. `--auto` skips the ask.
- Simple mode requires `--ticket:<ID>` because there is no worktree to infer from.
- `--simple` and `--auto` are mutually exclusive (simple needs interactive Q&A).
- Locate gate hardening is mandatory in full mode. A path-miss or symbol-miss always downgrades confidence.
- `low` confidence in `--auto` ABORTS with a Linear comment. Never proceed silently on low confidence.
- `--force` skips the staleness check (D18) but never skips the Locate gate.
- All Linear MCP calls use `mcp__claude_ai_Linear__*`. Never `mcp__linear-server__*`.
- All `.port/` writes from this stage are atomic via `mktemp` + `mv`.
- The labeled-ID convention (R-N, P-N) MUST appear in `dev-notes.md` regardless of execution path — agent spawn (default mode) or inline orchestrator (`--auto` mode). `/spec-lint` Check 8 fires when notes have zero labeled IDs.
- In `--auto` mode the dev-consult logic runs inline in the current session — do NOT call the `Agent` tool. Nested-Agent spawns from a `/ggx-dispatcher`-spawned `general-purpose` subagent fail (`Task`/`Agent` not available); inline execution is the only working path. Same constraint as `/dev:apply --auto` (see `commands/dev/dev/apply.md:15-17`); officially unsupported — see `ARCHITECTURE.md` "Nested-spawn constraint".
- Simple mode posts a comment, never edits the description (full mode owns description writes).
- Timings JSONL is appended in full mode only; simple mode is best-effort and ephemeral.
