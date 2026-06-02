---
name: start
description: "Stage 1 of the /ui-tweak pipeline (DELIVER path only) — environment prep before a draft PR: resolve the platform profile, fetch+cache the ticket (read-only), and create the ../<ticket-id> worktree via /add-worktree. The default designer path (leave a clean diff) never needs this — there is no worktree. Invoked by the orchestrator only when the deliver marker exists and no worktree is on the ticket yet; designers never type it directly (a misdirect guard routes them back to /ui-tweak). Does NOT touch ticket status/assignee (no /_ticket-init)."
---

<!-- RULE: command content is English. Designer-facing CARD text may be Traditional Chinese. -->

# `/ui-tweak:start`

> **Single responsibility**: prepare the deliver-path environment (profile + ticket + worktree).
> Reached only when `.dev/ui-tweak/deliver` exists and there is no worktree yet. The 90% default
> designer path (leave a clean diff) does not use this stage.

## Inputs

`<source> [figma-url] [--auto]` — `<source>` MUST be a ticket (ID/URL); free text cannot name a
branch/PR (the deliver marker's ticket id comes from card C3 when the designer chose `[3]`).

## Step 0a — misdirect guard (R5/D11)

If `UI_TWEAK_FF` is not set, print **C-MISDIRECT** (see `/ui-tweak:apply` Step 0a) and STOP — a
designer never types `/ui-tweak:start`.

## Step 0 — precondition

- Parse `<source>`. Free text → STOP:
  `FAIL: /ui-tweak:start (deliver path) requires a ticket source for the branch/PR name.`
- Resolve the ticket prefix at runtime from `~/.claude/commands/profiles/org.yaml`
  (`linear.prefixes` / `jira.prefixes`) — never hardcode.

## Read / write

- Resolve profile: `<repo>/.gogox-claude.yaml` → `platform`; fallback
  `~/.claude/commands/profiles/registry/<basename>.yaml`.
- Fetch the ticket (`mcp__claude_ai_Linear__get_issue` or Jira per `_ticket-lib.md`), **read-only**
  (no status/assignee change, no comment). Reuse `.dev/ui-tweak/ticket.json` if `/ui-tweak:apply`
  already cached it; otherwise write it now (O1 — downstream avoids a re-fetch).
- Create the worktree: `/add-worktree <ticket-id> --type <feat|fix>` (the `../<ticket-id>`
  convention). If it already exists, `/add-worktree` detects and asks (or enters it under `--auto`).

## Not the ticket lifecycle

Explicitly do **NOT** call `/_ticket-init` — that is `/dev:start`'s ticket lifecycle. `/ui-tweak:*`
keeps ticket reads read-only.

## Failure / HITL / `--auto`

No destructive action; failure → STOP, leave no marker. `/add-worktree` owns its own
existing-branch prompt (default); `--auto` passes through to `/add-worktree` (enter the existing
worktree instead of asking).

## Stop

Print: `Worktree ready for <ticket-id>. Next: /ui-tweak:apply.`
