---
name: port:synth
description: >
  Wave-3 of the port pipeline. Builds `.port/context.md` from the three
  notes files, spawns synth-agent (opus pinned) to fill OpenSpec artifact
  templates in dependency order, runs `openspec validate`, and invokes
  `/spec-lint` to produce `.port/synth-report.md`. Findings are NOT
  auto-fixed here — `/port:revise` owns that loop.
---

# /port:synth — Artifact Synthesis + Lint

Synthesize OpenSpec artifacts (`proposal.md`, `design.md`, `tasks.md`, `specs/*/spec.md`) from `.port/dev-notes.md`, `.port/pm-notes.md`, and `.port/design-notes.md`. Validate with `openspec validate`. Lint with `/spec-lint`. Hand off to `/port:revise` when findings exist, or `/port:ship` when clean.

**Usage**: `/port:synth [--change <name>] [--auto]`

- `--change <name>` — Override auto-detection of the active change. Otherwise resolved from the single directory under `openspec/changes/`.
- `--auto` — Unattended mode. Skip every `AskUserQuestion`; pass `mode: "bypassPermissions"` to the spawned `synth-agent`.

---

## Steps

1. **Resolve ticket-id (§4.4).**
   - Basename of `git rev-parse --show-toplevel` regex `[A-Z]+-[0-9]+`.
   - Else `git branch --show-current` regex `[A-Z]+-[0-9]+`.
   - Else HITL → `AskUserQuestion`. `--auto` → STOP with: `cannot resolve ticket-id; run from inside the ticket worktree`.
   - Hold `<ticket-id>`.

2. **Resolve change-name + worktree.**
   - If `--change <name>` provided → use it.
   - Else `ls -1 openspec/changes/` from the worktree root. Exactly one directory → use it. Zero or multiple → STOP with: `cannot resolve active change; pass --change <name>`.
   - Hold `<change-name>`, `<worktree-path>` (= `git rev-parse --show-toplevel`), `<change-dir>` (= `<worktree-path>/openspec/changes/<change-name>`), `<port-dir>` (= `<change-dir>/.port`).

3. **Verify preconditions.**
   - All three notes files MUST exist:
     - `<port-dir>/dev-notes.md`
     - `<port-dir>/pm-notes.md`
     - `<port-dir>/design-notes.md`
   - Any missing → STOP with a clear directive:
     ```
     /port:synth requires dev-notes + pm-notes + design-notes. Missing: <list>.
     Run /port:plan first (which itself requires /port:explore).
     ```

4. **Build `.port/context.md` bundle (atomic, D16).**
   - Fetch `<ticket-context>` via `mcp__claude_ai_Linear__get_issue` for `<ticket-id>`.
   - Read `<port-dir>/prd.md` if present, else `<prd-text>` = `(none)`.
   - Read all three notes files into memory.
   - Aggregate `## Assumptions` blocks: `awk '/^## Assumptions/,/^## /' <file>` for each notes file, concatenate.
   - Build the bundle body:
     ```markdown
     # Port context bundle for <change-name>

     This file is mandatory reading for every OpenSpec artifact generation
     below. The orchestrator builds it from wave 1 + wave 2 notes so a
     single file answers "what is this feature and what should the
     artifact say".

     ---

     ## Ticket
     <ticket-context>

     ---

     ## PRD (user or empty)
     <prd-text>

     ---

     ## Source analysis + porting guidance (from dev-consult-agent)
     <dev-notes.md content>

     ---

     ## PM notes (from pm-agent)
     <pm-notes.md content>

     ---

     ## Design notes (from designer-agent)
     <design-notes.md content>

     ---

     ## All assumptions made so far
     <aggregated ## Assumptions sections>
     ```
   - Atomic write:
     ```bash
     tmp=$(mktemp)
     # write bundle body to $tmp
     mv "$tmp" "<port-dir>/context.md"
     ```

5. **Get artifact dependency order.**
   ```bash
   openspec status --change "<change-name>" --json
   ```
   - Parse the `applyRequires` array (leaves first → root). Hold `<dependency-order>` as a JSON array of artifact IDs.
   - Empty / missing array → STOP with: `openspec status returned no applyRequires; the change scaffold is incomplete — re-run /port:start with --recreate`.

6. **Spawn `synth-agent`.**
   - Use the `Agent` tool with `subagent_type: "synth-agent"`.
   - In `--auto` mode include `mode: "bypassPermissions"` on the call. The agent's frontmatter pins `model: opus` (D21) — do NOT pass a model override.
   - Prompt body (verbatim shape — fill placeholders):
     ```
     You are the synth-agent. Inputs:

     <change-name>: <change-name>
     <worktree-path>: <worktree-path>
     <context-md-path>: <port-dir>/context.md
     <dependency-order>: <dependency-order JSON array>

     Run your Step 0 → Step 3 loop per your agent definition. Atomic-write
     each artifact to its outputPath via .tmp + mv. Do not validate, do
     not lint, do not edit .port/ inputs. Return the file list when done.
     ```
   - Wait for return. Capture the agent's final message as `<synth-summary>`.
   - Agent failure / refusal:
     - HITL → `AskUserQuestion`: `retry / abort`.
     - `--auto` → retry once, second failure → STOP with: `synth-agent failed twice; aborting /port:synth` and proceed to step 9 with `outcome:"failed-synth"` so timings is still appended.

7. **Validate.**
   ```bash
   openspec validate "<change-name>" --type change --json
   ```
   - Parse the JSON. Capture `<validate-errors>` and `<validate-warnings>` arrays.
   - Do NOT auto-retry. Do NOT abort on errors — `/port:revise` is responsible for fixing them.

8. **Invoke `/spec-lint`.**
   - Call the `/spec-lint` command with `--change <change-name>`. Pass the env hint `PORT_SYNTH=1` (or pass `--report <port-dir>/synth-report.md` per the spec-lint contract) so it atomic-writes `.port/synth-report.md` instead of printing to stdout.
   - Wait for return. Capture exit code + summary line as `<lint-summary>`.

9. **Append validation result to `.port/synth-report.md`.**
   - Read `<port-dir>/synth-report.md` (must exist after step 8).
   - Append:
     ```markdown

     ---

     ## openspec validate

     Errors: <count>
     Warnings: <count>

     <each error / warning as bullet — one per line, with file:line if present>
     ```
   - Atomic-write back:
     ```bash
     tmp=$(mktemp)
     # write merged body to $tmp
     mv "$tmp" "<port-dir>/synth-report.md"
     ```

10. **Append timings JSONL (D22).**
    - Append one line to `<port-dir>/timings.jsonl`:
      ```json
      {"stage":"synth","ticket":"<ticket-id>","start":"<ISO-start>","end":"<ISO-end>","duration_ms":<int>,"model":"opus","outcome":"<ok|failed-synth|failed-validate>"}
      ```
    - `outcome` is `ok` when synth-agent returned cleanly. Validate errors and lint findings do NOT change `outcome` — they're routed to `/port:revise`, not classified as failures here.

11. **Print summary + next-step hint.**
    - Count lint findings by severity from `<port-dir>/synth-report.md`:
      - `errors`  = `grep -c '^❌' <port-dir>/synth-report.md`
      - `warns`   = `grep -c '^⚠️' <port-dir>/synth-report.md`
    - Print:
      ```
      Artifacts written:
      <list of paths from <synth-summary>>

      openspec validate: <validate-errors count> errors, <validate-warnings count> warnings
      /spec-lint        : <errors> errors, <warns> warnings  (see <port-dir>/synth-report.md)
      ```
    - Routing for the exit hint:
      - `errors == 0 AND warns == 0 AND validate-errors == 0` → `Run /port:ship to push.`
      - `errors == 0 AND (warns > 0 OR validate-warnings > 0) AND validate-errors == 0` → `Run /port:revise to clear the report.`
      - `errors > 0 OR validate-errors > 0` → `Findings present — run /port:revise.`
    - Validate errors are highlighted in the summary block but do NOT abort `/port:synth`. Review (`/port:revise`) will catch them.

---

## Atomic write pattern (D16)

`.port/context.md` and `.port/synth-report.md` are both written by this orchestrator. Both use `mktemp` + `mv`:

```bash
tmp=$(mktemp)
# generate full file content into $tmp
mv "$tmp" "<port-dir>/<file>.md"
```

The synth-agent is also bound to atomic writes for every artifact it produces (its agent definition mandates `<outputPath>.tmp` + `mv`). Do not bypass this even when the file is small — partial writes break filesystem-as-state (D1).

---

## Guardrails

- **Pre-conditions are non-negotiable.** All three notes files must exist. Missing any → STOP, do NOT spawn synth-agent. The error message must point the user at `/port:plan` (which itself points at `/port:explore` if needed).
- **synth-agent owns artifact generation; orchestrator owns context.md, validate, lint.** Never inline the artifact loop here. Never let synth-agent run validate or lint.
- **synth-agent model is `opus` (D21).** Do NOT pass a model override on the Agent call. Trust the agent frontmatter.
- **Validate errors do NOT abort.** They are surfaced in the report and the summary. `/port:revise` is the fix point — keeping the synth → revise boundary clean is the whole reason synth and revise are separate commands.
- **`/spec-lint` is invoked, never reimplemented inline.** All nine checks live in `commands/dev/spec-lint.md`. This command consumes its `.port/synth-report.md` output.
- **Append validation, do not replace the lint report.** Step 9 reads the existing `.port/synth-report.md` (lint output) and appends a `## openspec validate` section. Atomic-write the merged result back.
- **Timings JSONL is best-effort but always written.** Even on synth-agent failure, append the timings line with `outcome:"failed-synth"` so post-mortem tooling sees the failure.
- **Never edit `.port/dev-notes.md` / `.port/pm-notes.md` / `.port/design-notes.md`.** They are inputs. If lint flags an issue traceable to notes, `/port:revise` handles it (or escalates to a re-run of `/port:plan`).
- **No commit, no push.** This stage produces files only. `/port:ship` owns commit + push + Linear write-back.
- **All Linear MCP calls use `mcp__claude_ai_Linear__*`** for the ticket fetch in step 4. Never `mcp__linear-server__*`.
- **HITL vs `--auto`.** Only synth-agent failure triggers an `AskUserQuestion`. Validate errors and lint findings are routed silently to `/port:revise`.
