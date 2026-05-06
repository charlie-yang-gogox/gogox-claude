---
name: port:start
description: >
  Entry stage of the port pipeline. Resolves the active project profile,
  fetches the Linear ticket, derives the change-name, optionally captures
  PRD enrichment, creates a feature worktree via /add-worktree, and
  scaffolds the OpenSpec change directory plus a `.port/` working subdir.
  Required before /port:explore and /port:plan.
---

# /port:start — Worktree + OpenSpec Scaffold

Set up the worktree, OpenSpec change, and `.port/` working directory for a Linear ticket. After this stage the cwd is inside the new worktree and downstream stages (`/port:explore`, `/port:plan`, `/port:synth`, ...) can run.

**Usage**: `/port:start --ticket:<ID> [--prd:"text"|--prd-file:<path>] [--auto] [--recreate]`

- `--ticket:<ID>` (**required**) — Linear ticket ID, e.g. `--ticket:CAF-212`. Reject with usage message when missing.
- `--prd:"<text>"` / `--prd-file:<path>` — Optional PRD enrichment. Mutually exclusive. When omitted in HITL mode the user is asked; in `--auto` mode the ticket description's `<!-- port:simple:start -->` block is used if present.
- `--auto` — Unattended mode. Skip every `AskUserQuestion`; resolve every gate per the auto-decision table.
- `--recreate` — Always remove an existing worktree / change directory and re-scaffold.

---

## Steps

1. **Parse arguments.**
   - Read `--ticket:`. Missing → STOP with: `Usage: /port:start --ticket:<ID> [--prd:"..."|--prd-file:path] [--auto] [--recreate]`.
   - Validate ticket ID against `^[A-Z]+-[0-9]+$`. Invalid → STOP.
   - Set `<auto-mode>` from `--auto`. Set `<recreate>` from `--recreate`.

2. **Resolve project profile.**
   - Read `<repo-root>/.gogox-claude.yaml`: capture `platform`, `product`, `branch_prefix`, `ticket_system`. Missing → STOP with: `Cannot resolve gogox project profile. Run /init-project.`
   - When `ticket_system: linear`, `<linear-team-key>` = `branch_prefix`. When `branch_prefix: auto`, derive the team key from the ticket ID prefix (the chars before `-`).
   - When `ticket_system != linear`, STOP with: `/port:start currently supports ticket_system: linear only.`

3. **Resolve `origin_project_path`.**
   - Read `<repo-root>/.gogox-claude.local.yaml` if it exists. Expand leading `~` and `$ENV_VAR` references in `origin_project_path`.
   - If field missing OR expanded path does not exist (`[ -d "$ORIGIN" ]`):
     - HITL mode → `AskUserQuestion`: `Absolute path to the origin project being ported FROM?`. Validate with `ls`. On success, atomic-write back to `.gogox-claude.local.yaml`:
       ```bash
       tmp=$(mktemp)
       # write new yaml content (preserve any existing fields) to $tmp
       mv "$tmp" "<repo-root>/.gogox-claude.local.yaml"
       ```
     - `--auto` mode → STOP with: `set origin_project_path in .gogox-claude.local.yaml before re-running`.

4. **Fetch ticket.**
   - `mcp__claude_ai_Linear__get_issue` with the ticket ID. Capture title, description, labels, AC, assignee. Store as `<ticket-context>`.
   - Network/MCP failure → retry once. Second failure → STOP with the error.

5. **Assignee check.**
   - If the issue is not assigned to the current user, STOP with:
     > `Ticket <ticket-id> is assigned to <assignee>, not you. Aborting to avoid working on someone else's ticket.`

6. **Derive change name and figma URL.**
   - `<change-name>` = kebab-case(title) with any leading `[bracket]`/`[CAF-XXX]`/`<lang>` prefix stripped before slugifying. Lowercase, hyphen-separated, no leading/trailing hyphens.
   - `<figma-url>` = first regex match of `figma\.com/\S+` in description (else empty). Stored only for downstream stages.

7. **PRD enrichment.**
   - If `--prd:"<text>"` or `--prd-file:<path>` was provided → read into `<prd-text>`.
   - Else if `<auto-mode>`:
     - Scan ticket description for content between `<!-- port:simple:start -->` and `<!-- port:simple:end -->` markers; if found, use that block as `<prd-text>`. Log: `Auto: thin ticket — using simple-mode analysis as PRD context.`
     - Else proceed with empty PRD. Log: `Auto: thin ticket — proceeding without PRD, agents will infer.`
   - Else (HITL): if combined description+AC has < ~30 chars of real content, `AskUserQuestion`:
     > `Ticket is thin. Paste 2-5 sentences describing what this feature does — or 'go' to let agents infer — or 'abort' to stop.`
     Options: `paste / go / abort`. On `paste`, capture follow-up text into `<prd-text>`. On `go`, leave empty. On `abort`, STOP.

8. **Worktree handling.**
   - Check `git worktree list | grep -i "<ticket-id>"`.
   - Worktree exists AND `<recreate>` OR `<auto-mode>` → `git worktree remove --force "../<ticket-id>"`. Log: `Removing existing worktree and recreating.`
   - Worktree exists AND HITL AND not `<recreate>` → `AskUserQuestion`: `reuse / recreate`. On `reuse`, proceed to step 10 (skip the create + scaffold phases). On `recreate`, remove first.
   - Invoke `/add-worktree <ticket-id> --type feat`. After it completes, the session is inside `../<ticket-id>`.

9. **OpenSpec scaffold + `.port/` subdir.**
   - From inside the worktree:
     ```bash
     openspec new change "<change-name>"
     mkdir -p "openspec/changes/<change-name>/.port"
     ```
   - Existing `openspec/changes/<change-name>/`:
     - `<auto-mode>` OR `<recreate>` → `rm -rf openspec/changes/<change-name>` then re-run `openspec new change`. Empty `.port/` afterwards (D18: `--recreate` means clean slate).
     - HITL → `AskUserQuestion`: `continue / recreate / abort`. On `recreate`, remove + re-run. On `abort`, STOP. On `continue`, ensure `.port/` exists and proceed.

10. **Write `prd.md` (atomic).**
    - Only when `<prd-text>` is non-empty.
    - ```bash
      tmp=$(mktemp)
      printf '%s\n' "<prd-text>" > "$tmp"
      mv "$tmp" "openspec/changes/<change-name>/.port/prd.md"
      ```

11. **Append timings JSONL (atomic append).**
    - At stage end, write one line to `openspec/changes/<change-name>/.port/timings.jsonl`:
      ```json
      {"stage":"start","ticket":"<ticket-id>","start":"<ISO-start>","end":"<ISO-end>","duration_ms":<int>,"outcome":"ok"}
      ```
    - Atomic pattern for the whole file rewrite is overkill for an append; just use `>>`. Create the file with `: > timings.jsonl` first if it does not exist, then append the JSON line.

12. **Announce.**
    ```
    Ticket       : <ticket-id> — <title>
    Change name  : <change-name>
    Figma URL    : <figma-url or "none">
    PRD          : <"user-provided" | "from simple-mode block" | "none (agents inferring)">
    Worktree     : <worktree-path>
    Origin path  : <origin_project_path>

    Next: /port:explore
    ```

---

## Atomic write pattern (D16)

Every write to a `.port/<file>.md` file MUST go through `mktemp` + `mv`:

```bash
tmp=$(mktemp)
# generate full file content into $tmp
mv "$tmp" "openspec/changes/<change-name>/.port/<file>.md"
```

POSIX `mv` on the same filesystem is atomic — partial files are never visible to a downstream stage. This protects the existence-implies-completion contract that `/port:explore`, `/port:plan`, `/port:synth` rely on.

The `.port/timings.jsonl` log uses append (`>>`), not the atomic-write pattern, because each line is a single `write(2)` and stage-level corruption on a telemetry file is acceptable.

---

## Guardrails

- `--ticket:<ID>` is mandatory. No cwd-based inference for entry stages (per §4.4).
- `ticket_system` must be `linear` (per current scope). Other systems STOP early.
- Origin path is per-machine and lives in `.gogox-claude.local.yaml` (gitignored). It is NEVER committed and NEVER written to `.gogox-claude.yaml`.
- Assignee check is non-negotiable — never auto-claim someone else's ticket.
- All `.port/` writes are atomic via `mktemp` + `mv` (D16). Partial files break filesystem-state-as-progress (D1).
- `--recreate` means "clean slate": existing worktree + change dir are removed without further prompts.
- All Linear MCP calls use the `mcp__claude_ai_Linear__*` prefix. Never use the legacy `mcp__linear-server__*`.
- Timings line is appended even on early STOP paths so observability isn't lost (set `outcome` to `aborted:<reason>`).
- This stage NEVER writes outside the new worktree's `.port/` and the existing `.gogox-claude.local.yaml`.
- This stage never spawns sub-agents. It is pure orchestration.
