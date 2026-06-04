---
name: start
description: "Stage 1 of the /ui-tweak pipeline — the up-front worktree split (R19), mirroring /dev:start and /port:start. Resolves the platform profile, fetches+caches the ticket (read-only), creates+enters the ../<ticket-id> worktree via /add-worktree, and writes the worktree-ready marker so the orchestrator never re-splits. On flutter repos it also writes the resolved-env block: the fvm-aware flutter binary marker (.dev/ui-tweak/flutter-bin) + a non-blocking iOS-simulator pre-warm, so preview never rediscovers fvm or cold-boots on the critical path. Invoked by /ui-tweak:ff Step 0 before the first edit (every run splits up-front — there is no in-place path, B3). Designers never type it directly (a misdirect guard routes them back to /ui-tweak). Does NOT touch ticket status/assignee (no /_ticket-init)."
---

<!-- RULE: command content is English. Designer-facing CARD text may be Traditional Chinese. -->

# `/ui-tweak:start`

> **Single responsibility**: prepare the workspace (profile + ticket + worktree) and write the
> `worktree-ready` marker. Run **first**, by `/ui-tweak:ff` Step 0 (R19), before any edit — exactly
> like `/dev:start` / `/port:start` create their worktree up-front. Every `/ui-tweak` run splits a
> worktree here; there is no in-place path (B3).

## Inputs

`<source> [figma-url] [--auto]` — `<source>` MUST resolve to a ticket id (ID/URL); free text cannot
name a branch/PR. `/ui-tweak:ff` Step 0 only ever invokes this stage with an id already parsed out of
the designer's input (or supplied via card C-WT), so a free-text arrival here is a misdirect.

## Step 0a — misdirect guard (R5/D11)

If `UI_TWEAK_FF` is not set, print **C-MISDIRECT** (see `/ui-tweak:apply` Step 0a) and STOP — a
designer never types `/ui-tweak:start`.

## Step 0 — precondition

- Parse `<source>`. Free text (no `[A-Z]+-[0-9]+` id and no Linear issue URL) → STOP:
  `FAIL: /ui-tweak:start requires a ticket source to name the worktree branch/PR.`
- Resolve the ticket prefix at runtime from `~/.claude/commands/profiles/org.yaml`
  (`linear.prefixes` / `jira.prefixes`) — never hardcode.

## Read / write

1. Resolve profile: `<repo>/.gogox-claude.yaml` → `platform`; fallback
   `~/.claude/commands/profiles/registry/<basename>.yaml`.
2. Fetch the ticket (`mcp__claude_ai_Linear__get_issue` or Jira per `_ticket-lib.md`), **read-only**
   (no status/assignee change, no comment). Determine branch type (`fix` for a bug-labelled ticket,
   else `feat`).
3. Create+enter the worktree: `/add-worktree <ticket-id> --type <feat|fix>` (the `../<ticket-id>`
   convention; off latest trunk). If it already exists, `/add-worktree` detects and asks (or enters it
   under `--auto`). After this returns the session is **inside** the worktree.
4. **Inside the worktree**, cache the ticket and write the up-front marker (both under the worktree's
   `.dev/ui-tweak/`, so the orchestrator's walker and `/ui-tweak:apply` find them locally — O1 avoids a
   re-fetch downstream):
   ```bash
   mkdir -p .dev/ui-tweak
   # ticket.json — read-only snapshot for apply/deliver (skip the re-fetch)
   printf '%s\n' "$TICKET_JSON" > .dev/ui-tweak/ticket.json
   # worktree-ready — Step-0 idempotency marker (ff.md skips re-split when present)
   printf 'ticket=%s\n' "$TICKET_ID" > .dev/ui-tweak/worktree-ready
   ```
5. **Resolved-env block (flutter platform only — skip for android/ios)** — resolve the build tooling
   ONCE here so no later stage rediscovers it per run:
   ```bash
   # (a) fvm resolution — on an fvm-pinned repo bare `flutter` fails (wrong/no SDK on PATH) and the
   #     agent wastes a failed run rediscovering `fvm` every time. Detect once, persist; every later
   #     flutter/dart invocation (preview's devices/run/build, audit's /format) reads this marker.
   if [ -f .fvmrc ] || [ -f .fvm/fvm_config.json ]; then FLUTTER_BIN="fvm flutter"; else FLUTTER_BIN="flutter"; fi
   printf '%s\n' "$FLUTTER_BIN" > .dev/ui-tweak/flutter-bin
   # (b) iOS simulator pre-warm (macOS only) — NON-BLOCKING + fail-silent: kick the boot off in the
   #     background so the cold boot overlaps ticket analysis + the first apply, and a later "show me"
   #     finds a warm simulator. NEVER wait on it, NEVER fail or warn because of it (a designer may
   #     preview on Android or a physical phone instead — the warm sim is a bonus, not a requirement).
   if [ "$(uname)" = "Darwin" ] && ! xcrun simctl list devices booted 2>/dev/null | grep -q '(Booted)'; then
     udid=$(xcrun simctl list devices available 2>/dev/null | grep iPhone | grep -m1 -oE '[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}')
     [ -n "$udid" ] && { (xcrun simctl boot "$udid" >/dev/null 2>&1 &) ; }
   fi
   ```

## Not the ticket lifecycle

Explicitly do **NOT** call `/_ticket-init` — that is `/dev:start`'s ticket lifecycle. `/ui-tweak:*`
keeps ticket reads read-only.

## Failure / HITL / `--auto`

No destructive action; failure → STOP, leave no `worktree-ready` marker (only write it after
`/add-worktree` succeeds, so a failed split never looks done to `/ui-tweak:ff` Step 0). `/add-worktree`
owns its own dirty-tree warning and existing-branch / existing-worktree prompts (default); `--auto`
passes through to `/add-worktree` (enter the existing worktree instead of asking).

## Stop

Print: `Worktree ready for <ticket-id>. Next: /ui-tweak:apply.`
