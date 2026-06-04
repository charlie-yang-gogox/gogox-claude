---
name: ggx-dev-agent
description: >
  Anthropic Claude Code cloud routine (CCR) that runs the gogox-claude
  `/ggx-work` pipeline — ALL lanes (port / dev / bug) — twice a day
  (Taiwan time 12:00 lunch / 18:00 after-work = 04/10 UTC; each slot is
  fed by a `ticket-analyzer-agent` fire 30 minutes earlier)
  inside Anthropic's cloud sandbox. Self-discovering and label-driven: each
  fire finds the routine owner's actionable tickets (`ready-to-port` OR
  `ready-to-dev`) in a Linear team and drives each one through whatever
  pipeline `/route` selects — a DRAFT PR for dev/bug, or a `need-spec-review`
  spec hand-off for port. It is the sequential cloud analogue of the local
  `/ggx-dispatcher`. The win over a plain GitHub Action is that a cloud
  routine can use the account's Linear MCP connector, so the full `/ggx-work`
  Linear lifecycle (claim → status moves → comments → labels) works
  unattended.
Prerequisite: >
  - A claude.ai account with a CCR environment and a Linear MCP connector.
  - GitHub access provided to the environment (Claude GitHub App or an
    injected `GH_TOKEN`) for the port-target repo + its origin repo.
  - The routine is created per-person (account-bound — see Onboarding).
---

# ggx-dev-agent — unified cloud routine (entry point to /ggx-work)

A canonical template + onboarding guide for running the gogox-claude
`/ggx-work` pipeline **across every lane** as an Anthropic Claude Code
**cloud routine** ("CCR" — a cron- or API-triggered remote Claude Code
session in Anthropic's cloud sandbox, created via the `RemoteTrigger`
claude.ai API or the `/schedule` skill).

This doc is the template: §3 has the full routine prompt and
`ggx-dev-agent.routine.json` (next to this file) has the matching
`RemoteTrigger action:create` body. A colleague copies both, swaps the
placeholders in §4, and creates their own routine.

> **Supersedes `ggx-bug-resolver`.** `ggx-dev-agent` is a strict superset:
> bug tickets flow through the `ready-to-dev` discovery bucket and
> `/route` → `/bug:ff` exactly as before, plus port (`ready-to-port`) and
> feature lanes. The standalone `ggx-bug-resolver.*` template has been
> removed (see git history); new routines should be created from
> `ggx-dev-agent.*`.

---

## 1. Purpose & architecture

### What it is

`ggx-dev-agent` is a scheduled remote Claude Code session (two fires a
day: TW 12:00 / 18:00). Every fire it:

1. Asks Linear (via the account's MCP connector) for the routine owner's
   actionable tickets in one team — anything labelled `ready-to-port` OR
   `ready-to-dev` (**status-agnostic**; see "Label-driven discovery" below).
2. If any match, bootstraps the sandbox **conditionally on the matched
   workflow labels** (port toolchain for `ready-to-port`, Flutter for
   `ready-to-dev`), then processes each ticket sequentially by running the
   local `/ggx-work <ID> --auto`.
3. `/ggx-work` calls `/route` per ticket and self-selects the pipeline:
   - **dev / bug** (`/dev:ff` / `/bug:ff`) → a **draft PR** + Linear In Review.
   - **port wave-1** (`/port:ff`) → a pushed `feat/<ID>` branch + updated
     Linear PRD + `need-spec-review` label, paused at the human spec-review
     gate (`outcome=port-paused`).
   - **port wave-2** (`port`-classified ticket already at `ready-to-dev`
     after a human ran `/spec-review`) → `/route` sees the committed
     `port:ship` marker and routes to `/dev:ff` → a draft PR.
4. Always emits an outcome report — even on a zero-match no-op fire.

### The Linear-MCP win over GitHub Actions

A plain GitHub Action could run `/ggx-work` headlessly, but it cannot reach
the account's Linear MCP connector — so the ticket lifecycle (claim → status
moves → comments → labels) breaks, and `/ggx-work` is built around that
lifecycle. A cloud routine runs under the creator's claude.ai account and
**can** use the account's Linear MCP connector. That is the entire reason
this lives as a CCR routine and not a GitHub Action.

### The sequential cloud analogue of `/ggx-dispatcher`

The local `/ggx-dispatcher` (manual, cwd-driven, fans out parallel
`/ggx-work` agents across a whole team) is **unchanged** and stays the
primary batch tool for humans at a workstation. `ggx-dev-agent` is the
deliberately simpler cloud shape: a **single-ticket-style sequential
agent** that loops over matches one at a time, one worktree alive at a
time. It does not re-implement the dispatcher's parallel fan-out — the
twice-daily cadence plus the claim-on-lock de-dup is enough for unattended use,
and sequential processing keeps the 30G sandbox disk under control.

### Label-driven discovery (NOT status-driven)

The routine carries no ticket list. Each fire runs two discovery queries —
`ready-to-port` and `ready-to-dev` — **with the `state` filter omitted**.
This mirrors `/ggx-dispatcher` Q1/Q3 and is load-bearing: the
port→spec-review→dev hand-off moves the workflow label **without resetting
status to `To-do`** (port leaves status `In Progress`; the human reviewer
relabels `need-spec-review` → `ready-to-dev` without touching status). A
`state: To-do` filter would therefore **silently drop every post-port dev
ticket** (port wave-2). Status is used only to *post-filter out* `completed`
/ `canceled` tickets, never as the primary gate.

Discovery runs **before** any clone / toolchain install, so a zero-match
fire is a cheap no-op.

### De-dup & crash handling — NO in-flight label

The routine writes **zero** Linear labels of its own. The whole lifecycle
(claim, status moves, terminal labels) is owned by `/ggx-work` and
`/port:ship` / `/dev:ship`. De-dup falls out of that for free:

- **Claim = `/ggx-work`'s own ticket-init** (its Step 2.5): on first touch it
  flips status `To-do`→`In Progress` and **removes the `ready-to-*` label**.
  Because the actionable label is gone, the next fire's label-driven
  discovery (`ready-to-port` OR `ready-to-dev`) no longer matches the ticket.
  That IS the de-dup — no separate lock label needed.
- **Completed work** is doubly guarded by the durable anti-duplicate check
  (Phase 2b): a port ticket that already shipped has a `feat/<T>` branch +
  `need-spec-review`; a dev ticket has an open PR. Even if `ready-to-*`
  somehow lingered, that check skips it.

**Crash → restart from scratch (the deliberate v1 model).** If a fire dies
mid-pipeline, the ticket is left at `In Progress` with no `ready-to-*` label
(an orphan). The routine does **not** track in-flight state and does **not**
auto-recover — that is the explicit decision: *no in-flight series; if it
breaks midway, start over.* A human re-adds `ready-to-port` / `ready-to-dev`
to re-trigger, and the next fire runs the ticket again from the top. This
keeps the routine dead-simple and removes any infinite-retry risk (a
deterministically-failing ticket is never auto-re-picked).

**Trade-off (accepted):** without a claim lock there is a small race window
— between discovery and `/ggx-work`'s ticket-init removing `ready-to-*` — in
which a *concurrently running* local `/ggx-dispatcher` (same person, same
team) could also grab the ticket. Hourly sequential cloud fires don't overlap
each other, and same-minute cloud+local collisions are rare, so v1 accepts
this rather than reintroduce a lock-label namespace.

---

## 2. CCR environment gotchas

These are validated facts about the Anthropic cloud sandbox. They are the
highest-value part of this doc: a future maintainer who skips them will
re-discover each one the hard way. The routine prompt in §3 already encodes
the workarounds. Rows marked **(port)** are new relative to
`ggx-bug-resolver`.

| Topic | Fact | Consequence / workaround |
|---|---|---|
| **Clone location** | Declared `sources[]` auto-clone to `/home/user/<repo>`, **not** `~/sources/`. The session runs as **root** (`HOME=/root`). | Reference repos as `/home/user/<repo>`. `~/.claude/...` resolves under `/root`. |
| **Linear MCP namespace** | In CCR the Linear MCP namespace is **`mcp__Linear__*`**, NOT the local `mcp__claude_ai_Linear__*`. | Every routine prompt must remap. The §3 prompt's "Cloud-specific overrides" does this for every `/ggx-work` call and for the routine's own Linear write (the Phase 2c failure comment). |
| **GitHub access** | GitHub is via **`mcp__github__*`**; the `gh` CLI is **absent**. | Use `mcp__github__*` (create_pull_request draft=true, list_pull_requests, pull_request_read, update_pull_request). Never call `gh`. Port lane opens NO PR — it only `git push`es the branch. |
| **`GH_TOKEN`** | Injected into the environment (not the prompt). Private repos reachable. gogovan sources use `allow_unrestricted_git_push: true`. | git push works. The token is never in the prompt — keep it that way. |
| **Commit signing** | Broken in the sandbox (0-byte key / HTTP 400). | Commits need `-c commit.gpgsign=false`. Cloud commits are **unsigned**. |
| **Custom commands** | gogox-claude commands run by "**Read the `.md` and follow it**", NOT as registered `/slash` commands. `install.sh` **flattens** category-top-level files: `commands/dev/ggx-work.md` → `~/.claude/commands/ggx-work.md` (the `dev/` category is dropped). Only `commands/dev/<ns>/foo.md` keeps a namespace dir (→ `~/.claude/commands/<ns>/foo.md`, e.g. `dev/start.md`, `port/ff.md`). | Run `bash /home/user/gogox-claude/install.sh`, then read e.g. `~/.claude/commands/ggx-work.md` (NOT `dev/ggx-work.md`) and follow it inline. |
| **Subagent spawns** | The Agent / Task tool is not available for nested spawns. | `--auto` mode inlines every sub-agent (dev-consult / pm / designer / synth / verify / review) by design — no Agent calls. For any residual case, read `~/.claude/agents/<name>.md` and follow inline. |
| **Worktree helpers** | `EnterWorktree` is unavailable. | Use `cd ../<TICKET>`; re-export `PATH` after every `cd`. |
| **`openspec` CLI (port)** | **Not preinstalled, but node 22 + npm 10 ARE present** (Ubuntu 24.04). `/port:synth` / `/port:ship` call `openspec status` / `instructions` / `validate` / `archive`; `/port:start` calls `openspec new change`. | Install with `npm install -g @fission-ai/openspec@1.3.1` — **verified 2026-05-29**: clean global install to `/opt/node22/bin/openspec` (already on PATH), `openspec --version` → `1.3.1`, all subcommands present, no node bootstrap needed. Pin `1.3.1` to match local dev so the CLI `--json` output the synth loop parses does not drift. Install only when a `ready-to-port` ticket matched; bug/dev lanes do NOT need it. |
| **origin codebase (port)** | The port pipeline resolves the **origin** project (port FROM) from `.claude/port-settings.json` (`originalProjectPath`) — and reads it **TWICE**: `/port:start` Step 3 reads `<main-repo>/.claude/...` **before** the worktree exists (`--auto` STOPs if missing), then `/port:explore` reads `<worktree>/.claude/...`. | Declare the origin repo as a read-only `sources[]` entry (clones to `/home/user/<origin-repo>`) AND seed `port-settings.json` in **TWO** places: the main repo (Phase 0 port block) and the worktree (override #5 in §3). git worktrees do NOT inherit the gitignored file, so both are required. |
| **Flutter (dev/bug)** | Not preinstalled. | Install 3.38.7 via curl **only when a `ready-to-dev` ticket matched** (port wave-1 needs no build). Result cached ~7 days. |
| **No macOS** | Linux-only sandbox. | No iOS builds. Dev/bug verification is Flutter analyze / unit-test scope only. |
| **Model pinning** | `session_context.model` is one-model-per-session; agent-frontmatter opus pinning is bypassed because spawns are inlined. | Set the whole session to opus (`model: "claude-opus-4-8"`). |

---

## 3. The routine prompt (canonical, de-personalized)

Embed this verbatim as `job_config.ccr.events[0].data.message.content` (it is
already there in `ggx-dev-agent.routine.json`). The assignee uses the Linear
**"me" / current-user** filter (no hardcoded email); the team is the
`<TEAM_KEY>` placeholder; repo names come from the JSON `sources[]`.

````text
Hourly cloud dev-agent for team <TEAM_KEY>. SELF-DISCOVERING + LABEL-DRIVEN: find the routine-owner's actionable tickets and drive each through the local /ggx-work pipeline, which self-routes to port / dev / bug. You ARE allowed to write code, commit, push, open DRAFT PRs, and update Linear for MATCHED tickets — scoped to the declared <TARGET_REPO> only.

## Phase 1 — DISCOVERY (do this FIRST, before any bootstrap / clone / toolchain install)
Using the Linear MCP tools in this env (namespace `mcp__Linear__*`), run TWO queries (state filter OMITTED on purpose — the port→dev hand-off keeps status at In Progress, so a To-do filter would drop wave-2 port tickets):
- Q_port: `mcp__Linear__list_issues` assignee=me, team=<TEAM_KEY>, label=`ready-to-port`
- Q_dev : `mcp__Linear__list_issues` assignee=me, team=<TEAM_KEY>, label=`ready-to-dev`
Merge by ticket id (union the label arrays). POST-FILTER OUT any ticket whose status type is `completed` / `canceled`. Sort by priority (Urgent>High>Medium>Low>None) then createdAt ascending. Tag each survivor with its lane bucket: `port` (had ready-to-port) or `dev` (had ready-to-dev). A ticket cannot be in both buckets at once (the labels are mutually exclusive across waves).

If ZERO match → emit the Phase 4 report with `Matches: 0` and STOP IMMEDIATELY. Do NOT install anything, do NOT touch any repo.

## Phase 0 — BOOTSTRAP (only if >=1 match; install CONDITIONALLY on matched buckets)
```bash
set -e
bash /home/user/gogox-claude/install.sh >/tmp/install.log 2>&1
ls ~/.claude/commands/ggx-work.md >/dev/null && echo 'gogox-claude: installed'   # flattened: NOT dev/ggx-work.md
git config --global --add safe.directory '*'
git config --global commit.gpgsign false   # sandbox signing is broken (0-byte key / HTTP 400) → commit unsigned from the start; do not rely on a per-commit fallback
cd /home/user/<TARGET_REPO>
PROXY_REMOTE=$(git config --get remote.origin.url)
PROXY_BASE=$(echo "$PROXY_REMOTE" | sed -E 's|/<ORG>/<TARGET_REPO>$||')
git config --global --add url."${PROXY_BASE}/".insteadOf 'git@github.com:'
git config --global --add url."${PROXY_BASE}/".insteadOf 'ssh://git@github.com/'
git fetch origin --quiet; git checkout trunk 2>&1 | tail -1; git pull --ff-only --quiet
```
- If the `ready-to-dev` bucket is non-empty (dev/bug lane builds + opens a PR):
```bash
export PATH="/opt/flutter/bin:$PATH"
if ! command -v flutter >/dev/null 2>&1; then
  cd /tmp
  curl -fsSL -o flutter.tar.xz 'https://storage.googleapis.com/flutter_infra_release/releases/stable/linux/flutter_linux_3.38.7-stable.tar.xz'
  tar -xJf flutter.tar.xz -C /opt 2>&1 | tail -1; rm -f flutter.tar.xz
fi
flutter config --no-analytics --no-cli-animations >/dev/null 2>&1
flutter --version | head -2
```
Re-export `PATH="/opt/flutter/bin:$PATH"` at the top of every later bash block when the dev/bug lane runs.
- If the `ready-to-port` bucket is non-empty (port wave-1 synth needs the openspec CLI):
```bash
# openspec CLI = npm package @fission-ai/openspec (bin `openspec`). Pinned to the
# version local dev uses (1.3.1) so the CLI output the synth loop parses (openspec
# status/instructions/validate --json) does not drift. /port:start|synth|ship call:
# openspec new change | status | instructions | validate | archive.
if ! command -v openspec >/dev/null 2>&1; then
  command -v npm >/dev/null 2>&1 || { echo 'FATAL: npm/node absent in sandbox — add a node bootstrap before installing openspec'; }
  npm install -g @fission-ai/openspec@1.3.1 2>&1 | tail -3
fi
openspec --version || { echo 'FATAL: openspec CLI unavailable — cannot run port lane'; }
# Seed the MAIN-repo port-settings.json NOW. /port:start Step 3 reads
# <main-repo>/.claude/port-settings.json BEFORE the worktree exists and --auto
# STOPs if it is missing. (The worktree copy is seeded later by override #5,
# because git worktrees do NOT inherit this gitignored file.)
mkdir -p /home/user/<TARGET_REPO>/.claude
printf '{\n  "originalProjectPath": "/home/user/<ORIGIN_REPO>"\n}\n' > /home/user/<TARGET_REPO>/.claude/port-settings.json
```
Flutter is NOT needed for port wave-1 (no build).

## Phase 2 — PROCESS EACH TICKET SEQUENTIALLY
This routine writes NO Linear labels of its own. Claiming + the label/status lifecycle are owned entirely by /ggx-work (its ticket-init) and /port:ship / /dev:ship. There is NO in-flight label and NO crash recovery — if a fire dies mid-pipeline the ticket is just left at In Progress (orphan) and a human re-adds `ready-to-*` to restart it from scratch.
For each matched ticket T (sorted), handle INDEPENDENTLY — one ticket's failure must NOT abort the rest. `<lane>` = T's bucket (`port` or `dev`).
  a. ANTI-DUPLICATE (durable completion check — skip work that's already done):
     - port bucket: `git ls-remote --heads origin "feat/<T>"` exists, OR T's labels already include `need-spec-review` → skip T (already shipped / awaiting human spec-review).
     - dev bucket: `mcp__github__list_pull_requests` on <TARGET_REPO>; an OPEN PR's head branch or title references T → skip T (already shipped).
  b. WORK IT: read `~/.claude/commands/ggx-work.md` (flattened — NOT `dev/ggx-work.md`) and follow it for `T --auto`. /ggx-work's ticket-init (Step 2.5) is the CLAIM: it flips status To-do→In Progress and removes `ready-to-*`, so the next fire's discovery won't re-match T. Apply the cloud overrides below. /ggx-work → /route self-selects /port:ff (wave-1) or /dev:ff (wave-2 / feature) or /bug:ff.
  c. RECORD the `[ggx-work-result] outcome=<...>` line — informational only, NO label writes by this routine:
     - `done` (dev/bug PR open) / `port-paused` (port shipped, /port:ship already added need-spec-review) → nothing to do; labels are already correct.
     - `failed` → post a Linear failure comment; leave T at In Progress (orphan, no `ready-to-*`). Do NOT abort the batch; continue to the next ticket. (A human re-adds `ready-to-*` later to restart T from scratch.)
  d. RECLAIM DISK: remove T's worktree (read `~/.claude/commands/remove-worktree.md`; if it refuses because the PR is unmerged, fall back to `cd /home/user/<TARGET_REPO> && git worktree remove --force ../<T>`). ALWAYS clean before the next ticket so only ONE worktree exists at a time.
  e. Next ticket.

## Cloud-specific overrides (apply to every /ggx-work run)
1. Linear namespace: local .md uses `mcp__claude_ai_Linear__*` → use `mcp__Linear__*` here (for /ggx-work's calls, and for this routine's only direct Linear write — the Phase 2c failure comment).
2. gh CLI NOT installed → use `mcp__github__*` for dev/bug PRs: create_pull_request (draft=true), list_pull_requests, pull_request_read, update_pull_request. The PORT lane opens NO PR — /port:ship only `git push`es feat/<T>.
3. Subagent spawns are inlined by `--auto` (dev-consult / pm / designer / synth / verify / review). Do NOT call the Agent tool; if a step still references an agent, read `~/.claude/agents/<name>.md` and follow inline.
4. `/add-worktree` (invoked by /dev:start and /port:start) is INTERACTIVE and has NO `--auto` mode — in the cloud there is no human, so resolve its prompts deterministically as you follow it:
   - Its Step 4 says "use the EnterWorktree tool" → EnterWorktree is unavailable here; instead `cd ../<T>` and re-export `PATH="/opt/flutter/bin:$PATH"` after cd (dev/bug lane).
   - Its Step 2 branch/worktree-state questions → resolve WITHOUT prompting: only the **remote** `feat/<T>` exists → `git worktree add --track -b feat/<T> ../<T> origin/feat/<T>` (this is the **port wave-2 / resume** case — restores the committed OpenSpec change); local branch exists (with or without remote) → reuse it (`git worktree add ../<T> feat/<T>`); `../<T>` already a registered worktree → enter it (skip create); `../<T>` is a non-worktree dir → STOP (never overwrite). Neither branch nor worktree exists → normal create from `origin/trunk`.
5. PORT origin path: the port pipeline reads `<worktree>/.claude/port-settings.json`. Git worktrees do NOT inherit it. So IMMEDIATELY AFTER /port:start (via /add-worktree) creates `../<T>`, and BEFORE /port:explore runs, write `../<T>/.claude/port-settings.json` containing exactly `{"originalProjectPath": "/home/user/<ORIGIN_REPO>"}`. In --auto /port:explore STOPs if this file is missing, so this seed is mandatory for the port lane.
6. git push is fine; NEVER force-push, NEVER push to trunk, NEVER delete remote branches you did not create. PRs (dev/bug only) are DRAFT.
7. Commit signing is broken in the sandbox (0-byte key / HTTP 400). Phase 0 already sets `git config --global commit.gpgsign false`, so every commit is unsigned from the start. Belt-and-suspenders: if a commit STILL fails on signing (e.g. a sub-command overrides config), retry with `-c commit.gpgsign=false`. (Do NOT rely on a piped `... | tail` masking the exit code — set the global config, don't depend on the fallback firing.)

## Phase 4 — REPORT (always emit, even on 0 matches)
=== HOURLY DEV-AGENT OUTCOME ===
Run (UTC)            : <timestamp>
Matches              : port=<n> dev=<m>
Processed            : <per ticket: id lane outcome>
PRs opened (dev/bug) : <urls | none>
Specs → spec-review  : <port ids now at need-spec-review | none>
Orphaned (failed)    : <ids left In Progress | none>
Notes                : <notable>
=== HOURLY DEV-AGENT COMPLETE ===
````

---

## 4. Onboarding: create YOUR own routine

Routines are **account-bound**: they run under the creator's claude.ai
subscription, MCP connectors, and CCR environment, and **cannot be shared
directly**. Each colleague must create THEIR OWN routine so that
`assignee=me` resolves to them.

### (a) The template

The two files in this directory ARE the template:

- `ggx-dev-agent.md` (this file) — the guide + the canonical prompt (§3).
- `ggx-dev-agent.routine.json` — the `RemoteTrigger action:create` body, with
  the full prompt already embedded.

Copy both, apply §4(b), then create the routine per §4(c).

### (b) De-personalization rules

JSON has no comments, so the placeholders are documented here. Apply each to
both the prompt and the JSON before creating your routine:

| Placeholder / field | Rule |
|---|---|
| **Assignee** | Use the Linear **"me" / current-user** filter only. Never hardcode an email — `me` auto-resolves to whoever OWNS the routine. |
| **gogox-claude source** | **Currently `https://github.com/charlie-yang-gogox/gogox-claude`** (temporary personal repo). ⚠️ A colleague creating their own routine must have read access. **TODO: mirror to a shared org location and switch this URL before broad rollout.** |
| **`<TEAM_KEY>`** | Linear team key. `CAF` (CA Flutter Revamp) is the worked example; `DAF` reuses the template by swapping this. (Port is Linear-only; CAF/DAF are the only port teams.) |
| **`<ORG>` / `<TARGET_REPO>`** | The port-target repo to write to. CAF → `gogovan` / `gogox-client-flutter`; DAF → `gogovan` / `gogox-driver-flutter`. |
| **`<ORIGIN_REPO>`** | The origin codebase to port FROM (read-only source, cloned to `/home/user/<ORIGIN_REPO>`). CAF → `gogovan-client-v2-android`; DAF → `gogovan-driver-android`. Used in override #5's `port-settings.json`. |
| **`<ENVIRONMENT_ID>`** | Per-account CCR environment id. |
| **`<LINEAR_CONNECTOR_UUID>`** | Per-account Linear MCP connector UUID. |

### (c) Per-colleague identity setup

1. **Linear connector.** Connect your own Linear MCP connector in claude.ai
   and reference its UUID as `<LINEAR_CONNECTOR_UUID>` in `mcp_connections`.
   This is what makes `assignee=me` resolve to you.

2. **No labels to set up.** The routine writes no Linear labels of its own —
   no in-flight / lock label exists (§1). Claiming and the label lifecycle are
   owned by `/ggx-work` + `/port:ship` / `/dev:ship`. Nothing to pre-create.

3. **Environment + GitHub auth.** Create your own CCR environment
   (`<ENVIRONMENT_ID>`) with GitHub access to BOTH the target repo and the
   origin repo. Order of preference:
   - **(preferred) Claude GitHub App** authorization — no PAT.
   - A **service-account / org PAT** injected as the environment `GH_TOKEN`.
   - **NEVER inline a token into the routine prompt.**

4. **Model / cron / filter.** Copy `model: "claude-opus-4-8"`,
   `cron_expression: "0 4,10 * * *"` (Taiwan time 12:00 / 18:00 = 04/10 UTC; keep the
   paired `ticket-analyzer-agent` 30 minutes ahead — `30 3,9 * * *`), and
   the discovery queries as-is. Change
   `<TEAM_KEY>` / repos for your team.

5. **openspec install — already verified** in the reference CCR environment
   (`npm install -g @fission-ai/openspec@1.3.1`, node 22 / npm 10 preinstalled
   on Ubuntu 24.04). If you use a *different* environment, smoke-test it first
   with a bash-only probe (`npm install -g @fission-ai/openspec@1.3.1 &&
   openspec --version`) before trusting the port lane.

6. **Creating + testing the routine.** Use the `/schedule` skill or the
   `RemoteTrigger` API:
   - First create with `run_once_at` (or `action:run`) to **TEST** — ideally
     when there are **0 matching tickets**, so the fire is a safe no-op.
     Confirm the model id is accepted and Phase 1 → `Matches: 0` → clean stop
     (no toolchain installed).
   - Then a one-off **port wave-1** test, then (after a human `/spec-review`)
     a **port wave-2** test, then enable the twice-daily cron
     (`cron_expression: "0 4,10 * * *"`).

7. **Team service-account option (and its caveat).** For ONE shared routine
   instead of per-person, run under a **team service account** — but then
   `assignee=me` becomes the service account, so the discovery queries must
   switch from `assignee=me` to a per-team / unassigned query. Different
   operating model and blast radius; spell it out before adopting.

---

## 5. Security notes

- **No secrets in the routine prompt** (the only credential is the
  env-injected `GH_TOKEN`, which never appears in the prompt).
- **Rotate any exposed PAT.** Prefer the Claude GitHub App or a
  service-account/org PAT over a personal PAT.
- **Cloud commits are unsigned** (signing broken in the sandbox).
- **The routine acts with write access to a production repo + Linear.** Keep
  PRs as **drafts** and review before merge. Scope is hard-limited to the
  declared target repo; never force-push, never push to trunk, never delete
  branches the routine did not create.
- **The origin repo is declared read-only** (no `allow_unrestricted_git_push`).

---

## 6. Known limitations / follow-ups

- **No crash recovery / no in-flight tracking (v1, deliberate).** A failed or
  interrupted pipeline orphans the ticket at `In Progress` with no actionable
  label; a human re-adds `ready-to-*` to restart it from scratch. The routine
  keeps no in-flight/lock label at all — chosen for simplicity and to avoid any
  autonomous infinite-retry. To add recovery later: a status-`In Progress` +
  classification recovery query + a bounded retry cap.
- **No concurrent-claim lock (Gap A).** Because the routine sets no lock label,
  a *simultaneously running* local `/ggx-dispatcher` (same person+team) could
  double-claim a ticket in the brief window before `/ggx-work`'s ticket-init
  removes `ready-to-*`. Hourly sequential cloud fires don't overlap; same-minute
  cloud+local collisions are rare → accepted for v1.
- **iOS not possible** — Linux-only sandbox.
- **Idle hours still clone the repos** (including the origin Android repo),
  because declared `sources[]` clone at session start regardless of the
  discovery-first gate. Future: declare no heavy source, clone in-prompt only
  on a match.
- **Linear namespace remap is per-prompt.** Long-term, make gogox-claude
  commands Linear-namespace-agnostic so the
  `mcp__claude_ai_Linear__*` → `mcp__Linear__*` remap is unnecessary.
- **Figma in port `/port:plan`** — the designer-agent may want Figma context.
  The reference CCR environment **auto-attaches a Figma connector** (alongside
  Linear / Slack / Notion / Atlassian), so design grounding works out of the
  box there. If a different environment lacks it, design-notes degrade
  gracefully (agents infer from the ticket).
