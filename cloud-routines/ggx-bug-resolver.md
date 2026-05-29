---
name: ggx-bug-resolver
description: >
  Anthropic Claude Code cloud routine (CCR) that runs the gogox-claude
  `/ggx-work` bug lane on an hourly cron inside Anthropic's cloud sandbox.
  Self-discovering: finds the routine owner's actionable bug tickets in a
  Linear team and drives each one to a DRAFT PR on
  `gogovan/gogox-client-flutter`. The win over a plain GitHub Action is that
  a cloud routine can use the account's Linear MCP connector, so the full
  `/ggx-work` Linear lifecycle (claim → fix → review → ship → In Review)
  works unattended.
Prerequisite: >
  - A claude.ai account with a CCR environment and a Linear MCP connector.
  - GitHub access provided to the environment (Claude GitHub App or an
    injected `GH_TOKEN`) for `gogovan/gogox-client-flutter`.
  - The routine is created per-person (account-bound — see Onboarding).
---

# ggx-bug-resolver — cloud bug-resolver routine

A canonical template + onboarding guide for running the gogox-claude
`/ggx-work` **bug lane** as an Anthropic Claude Code **cloud routine**
("CCR" — a cron- or API-triggered remote Claude Code session in Anthropic's
cloud sandbox, created via the `RemoteTrigger` claude.ai API or the
`/schedule` skill).

This doc is the template: §3 has the full routine prompt and
`ggx-bug-resolver.routine.json` (next to this file) has the matching
`RemoteTrigger action:create` body. A colleague copies both, swaps the
placeholders in §4, and creates their own routine.

---

## 1. Purpose & architecture

### What it is

`ggx-bug-resolver` is an hourly remote Claude Code session. Every fire it:

1. Asks Linear (via the account's MCP connector) for the routine owner's
   actionable bug tickets in one team.
2. If any match, bootstraps the sandbox (installs gogox-claude + Flutter,
   clones nothing extra — the declared sources are already cloned) and runs
   the local `/ggx-work <ID> --auto` **bug lane** on each ticket in turn.
3. Drives each ticket to a **draft PR** on `gogovan/gogox-client-flutter`
   and flips its Linear status to In Review.
4. Always emits an outcome report — even on a zero-match no-op fire.

First end-to-end proof: ticket CAF-600 (a subtitle copy bug) → draft PR
gogovan/gogox-client-flutter#446 → Linear In Review, with the real pipeline
(route → ticket-init → worktree → fix → verify → review → ship) executed by
reading the gogox-claude command `.md` files inside the sandbox.

### The Linear-MCP win over GitHub Actions

A plain GitHub Action could run `/ggx-work` headlessly, but it cannot reach
the account's Linear MCP connector — so the ticket lifecycle (claim → status
moves → comments → labels) breaks, and `/ggx-work` is built around that
lifecycle. A cloud routine runs under the creator's claude.ai account and
**can** use the account's Linear MCP connector. That is the entire reason
this lives as a CCR routine and not a GitHub Action: the full `/ggx-work`
Linear lifecycle works.

### Self-discovering hourly flow

The routine is **self-discovering** — it carries no ticket list. Each fire
runs the Phase 1 discovery query (`assignee=me`, `label` ⊇ {`bug`,
`ready-to-dev`}, `state=To-do`, team `<TEAM_KEY>`) and processes whatever it
finds. The claim itself (ticket-init flips the ticket to In Progress) is the
de-dup mechanism: once claimed, the next fire's `To-do` filter no longer
matches it, so the same ticket is never picked up twice. Phase 1 runs
**before** any clone / Flutter install, so a zero-match fire is a cheap
no-op.

### Framing: keep the local dispatcher; cloud runs a single-ticket worker

The local `/ggx-dispatcher` (manual, cwd-driven, fans out parallel
`/ggx-work` agents across a whole team) is **unchanged** and stays the
primary batch tool for humans at a workstation. The cloud routine is
deliberately the simpler shape: a **single-ticket-style sequential worker**
that loops over matches one at a time, one worktree alive at a time. It does
not re-implement the dispatcher's race-locking or parallel fan-out — the
hourly cadence plus the claim-on-init de-dup is enough for unattended use,
and sequential processing keeps the 30G sandbox disk under control.

---

## 2. CCR environment gotchas

These are validated facts about the Anthropic cloud sandbox. They are the
highest-value part of this doc: a future maintainer who skips them will
re-discover each one the hard way. The routine prompt in §3 already encodes
the workarounds.

| Topic | Fact | Consequence / workaround |
|---|---|---|
| **Clone location** | Declared `sources[]` auto-clone to `/home/user/<repo>`, **not** `~/sources/`. The session runs as **root** (`HOME=/root`). | Reference repos as `/home/user/gogox-client-flutter` and `/home/user/gogox-claude`. `~/.claude/...` resolves under `/root`. |
| **Linear MCP namespace** | In CCR the Linear MCP namespace is **`mcp__Linear__*`**, NOT the local `mcp__claude_ai_Linear__*`. | Every routine prompt must remap. The §3 prompt's "Cloud-specific overrides" item 1 does this for every `/ggx-work` call. |
| **GitHub access** | GitHub is via **`mcp__github__*`**; the `gh` CLI is **absent**. The github MCP is env-provided regardless of `mcp_connections`. | Use `mcp__github__*` (create_pull_request draft=true, list_pull_requests, pull_request_read, update_pull_request). Never call `gh`. |
| **`GH_TOKEN`** | Injected into the environment (not the prompt). Private repos are reachable. gogovan sources use `allow_unrestricted_git_push: true`. | git push works. The token is never in the prompt — keep it that way. |
| **Commit signing** | Broken in the sandbox (0-byte key / HTTP 400). | Commits need `-c commit.gpgsign=false`. Cloud commits are **unsigned**. The §3 prompt retries with this flag on a signing failure. |
| **Custom commands** | gogox-claude commands run by "**Read the `.md` and follow it**", NOT as registered `/slash` commands — install.sh symlinks created mid-session do not register as slash triggers. | Run `bash /home/user/gogox-claude/install.sh` to lay down `~/.claude/{commands,agents,lib,…}`, then read e.g. `~/.claude/commands/ggx-work.md` and follow it inline. |
| **Subagent spawns** | The Agent / Task tool is not available for nested spawns in this context. | For verify-agent, git-branch-code-reviewer, etc.: read `~/.claude/agents/<name>.md` and follow it inline (same as `/ggx-dispatcher`'s `--auto` inlining rationale). |
| **Worktree helpers** | `EnterWorktree` is unavailable. | Use `cd ../<TICKET>`; re-export `PATH="/opt/flutter/bin:$PATH"` after every `cd`. |
| **Sandbox spec** | Ubuntu 24.04, 4 vCPU, 15Gi RAM, 30G disk. | Process one ticket / one worktree at a time; clean each worktree before the next. |
| **Flutter** | Not preinstalled. | Install 3.38.7 via curl (the §3 Phase 0 block does this); the result is cached for ~7 days. |
| **No macOS** | Linux-only sandbox. | No iOS builds possible. Bug lane verification is Flutter analyze / unit-test scope only. |
| **Model pinning** | `session_context.model` is one-model-per-session. The opus pinning in agent frontmatter is bypassed because spawns are inlined into this session. | Set the whole session to opus (`model: "claude-opus-4-8"`) for dev-quality work — see the JSON template. |

---

## 3. The routine prompt (canonical, de-personalized)

This is the exact text of the live routine, de-personalized per §4(b): the
assignee uses the Linear **"me" / current-user** filter (no hardcoded
email), the team is the `<TEAM_KEY>` placeholder (CAF as the example), and
the gogox-claude source is the org-hosted copy (declared in the JSON, not in
the prompt). Embed it verbatim as
`job_config.ccr.events[0].data.message.content` (it is already there in
`ggx-bug-resolver.routine.json`).

````text
Hourly cloud bug-worker for team <TEAM_KEY>. SELF-DISCOVERING: find the routine-owner's actionable bug tickets and drive each to a DRAFT PR via the local /ggx-work pipeline. You ARE allowed to write code, commit, push, open draft PRs, and update Linear for MATCHED tickets — scoped to gogovan/gogox-client-flutter only.

## Phase 1 — DISCOVERY (do this FIRST, before any bootstrap / clone / flutter install)
Using the Linear MCP tools in this env (namespace `mcp__Linear__*`), find tickets matching ALL of:
- Team: <TEAM_KEY> (e.g. CAF = CA Flutter Revamp)
- Assignee: ME (the routine owner — resolve via the Linear "me"/current-user filter; do NOT hardcode an email)
- State: `To-do` (exact name, unstarted type)
- Labels: include BOTH `bug` AND `ready-to-dev`
Query: `mcp__Linear__list_issues` with assignee=me, label=ready-to-dev, state=To-do, team=<TEAM_KEY>; then POST-FILTER to those whose full label set ALSO contains `bug`. Sort by priority (Urgent>High>Medium>Low>None) then createdAt ascending.

If ZERO match → emit the Phase 4 report with `Matches: 0` and STOP IMMEDIATELY. Do NOT install Flutter, do NOT touch any repo.

## Phase 0 — BOOTSTRAP (only if >=1 match)
```bash
set -e
export PATH="/opt/flutter/bin:$PATH"
bash /home/user/gogox-claude/install.sh >/tmp/install.log 2>&1
ls ~/.claude/commands/ggx-work.md >/dev/null && echo 'gogox-claude: installed'
if ! command -v flutter >/dev/null 2>&1; then
  cd /tmp
  curl -fsSL -o flutter.tar.xz 'https://storage.googleapis.com/flutter_infra_release/releases/stable/linux/flutter_linux_3.38.7-stable.tar.xz'
  tar -xJf flutter.tar.xz -C /opt 2>&1 | tail -1; rm -f flutter.tar.xz
fi
git config --global --add safe.directory '*'
flutter config --no-analytics --no-cli-animations >/dev/null 2>&1
flutter --version | head -2
cd /home/user/gogox-client-flutter
PROXY_REMOTE=$(git config --get remote.origin.url)
PROXY_BASE=$(echo "$PROXY_REMOTE" | sed -E 's|/gogovan/gogox-client-flutter$||')
git config --global --add url."${PROXY_BASE}/".insteadOf 'git@github.com:'
git config --global --add url."${PROXY_BASE}/".insteadOf 'ssh://git@github.com/'
git fetch origin --quiet; git checkout trunk 2>&1 | tail -1; git pull --ff-only --quiet
```
Re-export `PATH="/opt/flutter/bin:$PATH"` at the top of every later bash block.

## Phase 2 — PROCESS EACH TICKET SEQUENTIALLY
For each matched ticket T (sorted), handle INDEPENDENTLY — one ticket's failure must NOT abort the rest:
  a. Anti-duplicate: `mcp__github__list_pull_requests` on gogovan/gogox-client-flutter; if an OPEN PR's head branch or title references T → skip T, go to next.
  b. Work it: read `~/.claude/commands/ggx-work.md` and follow it for `T --auto` (bug lane). ticket-init flips T to In Progress (= claim; a future fire's To-do filter then skips it). Apply the cloud overrides below.
  c. Record outcome. On FAILURE: leave T claimed (In Progress) + post a Linear failure comment; do NOT abort — continue to the next ticket.
  d. Reclaim disk: remove T's worktree (read `~/.claude/commands/remove-worktree.md`; if it refuses because the PR is unmerged, fall back to `cd /home/user/gogox-client-flutter && git worktree remove --force ../<T>`). ALWAYS clean before the next ticket so only ONE worktree exists at a time.
  e. Next ticket.

## Cloud-specific overrides (apply to every /ggx-work run)
1. Linear namespace: local .md uses `mcp__claude_ai_Linear__*` → use `mcp__Linear__*` here.
2. gh CLI NOT installed → use `mcp__github__*`: create_pull_request (draft=true), list_pull_requests, pull_request_read, update_pull_request.
3. Subagent spawns (verify-agent, git-branch-code-reviewer, etc.) → do NOT call the Agent tool; read `~/.claude/agents/<name>.md` and follow inline.
4. EnterWorktree unavailable → `cd ../<T>`; re-export the flutter PATH after cd.
5. git push is fine; NEVER force-push, NEVER push to trunk, NEVER delete remote branches you did not create.
6. PRs are DRAFT.
7. Commit signing may be broken (0-byte key / HTTP 400) → if a commit fails on signing, retry with `-c commit.gpgsign=false`.

## Phase 4 — REPORT (always emit, even on 0 matches)
=== HOURLY BUG-WORKER OUTCOME ===
Run (UTC)   : <timestamp>
Matches     : <N>
Processed   : <per ticket outcome>
PRs opened  : <urls | none>
Notes       : <notable>
=== HOURLY BUG-WORKER COMPLETE ===
````

---

## 4. Onboarding: create YOUR own routine

Routines are **account-bound**: they run under the creator's claude.ai
subscription, MCP connectors, and CCR environment, and **cannot be shared
directly**. Each colleague must create THEIR OWN routine so that
`assignee=me` resolves to them and discovery finds THEIR tickets.

### (a) The template

The two files in this directory ARE the template:

- `ggx-bug-resolver.md` (this file) — the guide + the canonical prompt (§3).
- `ggx-bug-resolver.routine.json` — the `RemoteTrigger action:create` body,
  with the full prompt already embedded as
  `job_config.ccr.events[0].data.message.content`.

Copy both, apply §4(b), then create the routine per §4(c).

### (b) De-personalization rules

JSON has no comments, so the placeholders are documented here. Apply each to
both the prompt and the JSON before creating your routine:

| Placeholder / field | Rule |
|---|---|
| **Assignee** | Use the Linear **"me" / current-user** filter only. Remove any hardcoded email — `me` auto-resolves to whoever OWNS the routine. (The §3 prompt already does this.) |
| **gogox-claude source** | **Currently `https://github.com/charlie-yang-gogox/gogox-claude`** (temporary). ⚠️ This is a personal repo — a colleague creating their own routine must have read access to it. **TODO: mirror/move gogox-claude to a shared org location (e.g. `gogovan/gogox-claude`) and switch this URL before broad team rollout.** |
| **`<TEAM_KEY>`** | Linear team key. `CAF` (CA Flutter Revamp) is the worked example; `DAF` and other teams reuse the same template by swapping this. |
| **`<ENVIRONMENT_ID>`** | Per-account CCR environment id. Leave as a placeholder; fill with your own. |
| **`<LINEAR_CONNECTOR_UUID>`** | Per-account Linear MCP connector UUID. Leave as a placeholder; fill with your own. |

### (c) Per-colleague identity setup

Because the routine is account-bound, every person wires up their own
identity. Cover all of:

1. **Linear connector.** Each person connects their own Linear MCP connector
   in claude.ai and references its UUID as `<LINEAR_CONNECTOR_UUID>` in
   `mcp_connections`. This is what makes `assignee=me` resolve to them, so
   discovery finds THEIR tickets.

2. **Environment + GitHub auth.** Each person creates their own CCR
   environment (`<ENVIRONMENT_ID>`) with a setup that provides GitHub access
   to `gogovan/gogox-client-flutter`. Recommended order of preference:
   - **(preferred) Claude GitHub App** authorization on the repos — no PAT
     needed.
   - A **service-account / org PAT** injected as the environment `GH_TOKEN`
     (NOT a personal PAT).
   - **NEVER inline a token into the routine prompt** — it lives only in the
     environment config.

3. **Model / cron / filter.** Copy `model: "claude-opus-4-8"`,
   `cron_expression: "23 * * * *"`, and the discovery filter as-is. Change
   `<TEAM_KEY>` if your tickets live on a different team.

4. **Team service-account option (and its caveat).** If the team wants ONE
   shared routine instead of one-per-person, run it under a **team service
   account**. But then `assignee=me` semantics change — `me` becomes the
   service account, not a human — so the discovery filter must switch from
   `assignee=me` to a **per-team / unassigned-or-team query** rather than
   "my tickets". Spell this out before adopting it: a shared routine
   processes the whole team's queue, which is a different operating model
   (and a different blast radius) from the per-person default.

5. **Creating + testing the routine.** Use the `/schedule` skill or the
   `RemoteTrigger` API:
   - First create with `run_once_at` (or trigger via `action:run`) to
     **TEST** — ideally at a moment when there are **0 matching tickets**, so
     the fire is a safe no-op. Confirm the model id is accepted and that
     discovery runs (Phase 1 → `Matches: 0` → clean stop).
   - THEN enable the hourly cron (`cron_expression: "23 * * * *"`).

---

## 5. Security notes

- **No secrets in the routine prompt** (verified — the only credential is the
  env-injected `GH_TOKEN`, which never appears in the prompt).
- **Rotate any PAT that has been exposed.** Prefer the Claude GitHub App or a
  service-account/org PAT over a personal PAT.
- **Cloud commits are unsigned** (signing is broken in the sandbox — 0-byte
  key / HTTP 400).
- **The routine acts with write access to a production repo + Linear.** Keep
  PRs as **drafts** and review before merge. Scope is hard-limited to
  `gogovan/gogox-client-flutter`; never force-push, never push to trunk,
  never delete branches the routine did not create.

---

## 6. Known limitations / follow-ups

- **iOS not possible** — Linux-only sandbox, no macOS, no iOS builds.
- **Idle hours still clone the repos.** Declared `sources[]` clone at session
  start regardless of the discovery-first gate, so a zero-match fire still
  pays the clone cost. Future optimization: declare no heavy source and clone
  in-prompt only when there are matches.
- **Linear namespace remap is per-prompt.** Long-term, make gogox-claude
  commands Linear-namespace-agnostic so the
  `mcp__claude_ai_Linear__*` → `mcp__Linear__*` remap is unnecessary.
- **Bug lane only for now** — no port / feature lane in the cloud yet.
