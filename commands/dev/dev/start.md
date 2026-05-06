---
name: start
description: "Stage 1 of the /dev:* atomic pipeline. Resolves the project profile, parses the ticket, runs pre-flight checks, optionally creates a worktree (auto mode), assigns the ticket on Linear, and creates `.dev/state.json` with the initial pipeline state. Required before any other /dev:* stage."
Prerequisite: >
  - Linear MCP authenticated.
  - Default mode: already on the branch/worktree for the ticket. Git clean.
  - --auto mode: on trunk with clean working tree. gh CLI authenticated.
    Environment variables USER_NAME and GH_USER_NAME set.
---

# `/dev:start <ticket-id> [--auto]`

Creates `.dev/state.json` and prepares the working environment for the dev loop. This is the only stage that creates state — every other stage refuses to run without it.

## Inputs

- `<ticket-id>` — Linear ticket ID (e.g. `CAF-207`). Required.
- `--auto` — full autonomous pipeline.
- Linear ticket (fetched).
- Project profile (`{platform}`, `{deps_install}`, `{test_cmd}`).

## Outputs

- `.dev/state.json` (initial state, see `/dev:_state-schema`).
- Worktree at `../<ticket-id>` (auto only).
- Linear ticket: assigned to self, status `In Progress`, `ready-to-dev` label removed (auto only).
- `/tmp/<ticket-id>.md` — ticket dump (auto only).

## Step 0: Resolve project profile

1. If `<repo-root>/.gogox-claude.yaml` exists, read its `platform` and `product`. Else look up `basename "$(git rev-parse --show-toplevel)"` in `~/.claude/commands/profiles/repos.yaml`.
2. Read `~/.claude/commands/profiles/platform/{platform}.yaml` for `{deps_install}`, `{test_cmd}`, `{format_cmd}`.

## Step 1: Parse input

- Extract `<ticket-id>` from `$ARGUMENTS`. Detect `--auto`.
- Missing ticket-id in `<auto-mode>` → STOP.
- Missing ticket-id in default mode → use **AskUserQuestion**. Stop if still missing.

## Step 2: Refuse re-entry

If `.dev/state.json` already exists:

- If its `ticket_id` matches the argument and `current_stage != "done"`: STOP with a message pointing to `/dev:<current_stage>` or `/dev:ff` to resume.
- If its `ticket_id` differs: STOP with "state.json belongs to ticket X; finish or remove it before starting Y."
- If `current_stage == "done"`: STOP with "pipeline already complete; remove `.dev/` to start over."

This stage is the creator. It refuses to overwrite an in-flight pipeline.

## Step 3: Pre-flight + ticket assignment

**Linear ownership check** (both modes): fetch via `mcp__claude_ai_Linear__get_issue`. If the ticket is not assigned to the current user, STOP.

**Auto mode**:

1. Verify git is clean and on `trunk`. If not → STOP.
2. Read the Linear ticket to determine branch type (`feat`, `fix`, `test`, `ci`, `chore`).
3. Invoke `/add-worktree <ticket-id> --type <type>` — handles fetch, branch, EnterWorktree, port-settings, `{deps_install}`.
4. Linear MCP transitions: status → `In Progress`, remove `ready-to-dev` label, assign to self via `$USER_NAME`. Set estimate to 1 point if none.
5. Write the full ticket content to `/tmp/<ticket-id>.md`.

**Default mode**:

1. Run `/check-clean`. Stop if not clean.
2. Check current branch contains `<ticket-id>` (case-insensitive). If not, **AskUserQuestion** to confirm; stop on No.

## Step 4: Derive change name and base ref

- `change_name` — kebab-case from ticket title, strip leading ticket IDs/prefixes.
- `base_ref` — auto: `origin/trunk`. Default: trunk for the project (typically `origin/main` or `origin/trunk` per profile).

## Step 5: Write initial state

Write `.dev/state.json` atomically:

```bash
mkdir -p .dev
cat > .dev/state.json.tmp <<EOF
{
  "schema_version": 1,
  "ticket_id": "<ticket-id>",
  "ticket_title": "<title>",
  "change_name": "<change-name>",
  "mode": "<auto|default>",
  "platform": "<platform>",
  "base_ref": "<base-ref>",
  "worktree_path": "<abs path or null>",
  "current_stage": "figma",
  "stage_history": [
    { "stage": "start", "status": "done", "ts": "<iso8601 utc>" }
  ]
}
EOF
mv .dev/state.json.tmp .dev/state.json
```

Use real values, not placeholders. `worktree_path` is null in default mode.

## Step 6: Announce and stop

Print:
> `Started /dev pipeline for <ticket-id> (<title>). Mode: <auto|default>. Next: /dev:figma`

In auto mode, the chain orchestrator (`/dev:ff`) will continue automatically. STOP this stage's body.
