---
name: ggx-cloud-onboard
description: >
  Semi-automated onboarding for YOUR OWN hourly `ggx-bug-resolver` cloud
  routine (CCR). Run this in YOUR Claude Code session — the `RemoteTrigger`
  tool uses the CURRENT session's OAuth token, so the routine is created
  under whoever runs this skill. It automates the mechanical, error-prone
  parts (correct opus body, namespace overrides, a test run) but CANNOT do
  the two per-account interactive steps (connect Linear, create a CCR
  environment with GitHub auth) — those are prerequisites you do first.
  Canonical source of truth for the routine shape + prompt:
  `cloud-routines/ggx-bug-resolver.routine.json` and
  `cloud-routines/ggx-bug-resolver.md`. The body below is embedded inline
  (kept in sync with that JSON) because install.sh does NOT symlink
  `cloud-routines/` into `~/.claude`.
Prerequisite: >
  - This must be run in YOUR own Claude Code session (research-preview
    Claude Code Routines access) — the routine is account-bound to you.
  - Linear MCP connector connected in claude.ai (so `assignee=me` resolves
    to you).
  - A CCR environment with GitHub auth to the 3 source repos (Claude GitHub
    App authorization preferred, else an env `GH_TOKEN`). Read access to
    `charlie-yang-gogox/gogox-claude` + write to
    `gogovan/gogox-client-flutter` required.
---

# `/ggx-cloud-onboard`

> Creates + tests YOUR own hourly `ggx-bug-resolver` cloud routine.
>
> **Run this in YOUR OWN Claude Code session.** The `RemoteTrigger` tool
> authenticates with the CURRENT session's OAuth token, so the routine is
> created under whoever runs this skill. `assignee=me` inside the routine
> then resolves to YOU and discovery finds YOUR tickets. A teammate cannot
> create this for you — you run it yourself, once.
>
> **What it does NOT automate** (interactive, per-account — do these first):
> 1. Connecting your Linear MCP connector in claude.ai.
> 2. Creating a CCR environment with GitHub auth to the 3 repos.
> This skill gates on both (Step 2) and STOPS if either is missing.

---

## Steps

### Step 1: Tool check

Confirm the `RemoteTrigger` tool is available in this session:

```
ToolSearch  query="select:RemoteTrigger"  max_results=1
```

- If the schema loads → continue.
- If it does NOT load (no match) → **STOP**:
  ```
  STOP: RemoteTrigger is not available in this session.
  Your account needs Claude Code Routines (research-preview) access.
  Request access, then re-run /ggx-cloud-onboard in a fresh session.
  ```

Do NOT attempt any `curl` to the triggers API — always use the
`RemoteTrigger` tool so the OAuth token is added in-process and never
exposed.

### Step 2: Prerequisite gate (interactive — cannot be automated)

These two steps require a human in claude.ai; this skill cannot perform
them. Confirm BOTH are done (use `AskUserQuestion`, one question with two
checkboxes, or ask plainly). If either is not done, print the matching
how-to below and **STOP** — do NOT create anything.

**(a) Linear connector connected in claude.ai.**
Required so the routine's `assignee=me` resolves to you and discovery finds
YOUR tickets.
> How-to: claude.ai → Settings → Connectors → connect **Linear**
> (`https://mcp.linear.app/mcp`). Authorize with the account that owns your
> CAF/DAF tickets.

**(b) A CCR environment exists with GitHub auth to the 3 repos.**
The environment must reach all three declared sources:
`charlie-yang-gogox/gogox-claude` (read), `gogovan/gogox-client-flutter`
(write), `gogovan/flutter-core-sdk` (write).
> How-to (preferred): authorize the **Claude GitHub App** on those repos —
> no PAT needed. Fallback: inject a **service-account / org `GH_TOKEN`**
> (NOT a personal PAT) into the environment config. **NEVER** inline a token
> into the routine prompt — GitHub auth comes from the environment only.

If either gate fails:
```
STOP: prerequisite not met (<which one>).
Complete the how-to above, then re-run /ggx-cloud-onboard.
```

> Note on `charlie-yang-gogox/gogox-claude`: this is currently a **temporary
> personal repo**. You need read access to it. (TODO upstream: mirror
> gogox-claude to a shared org location and switch this URL before broad
> rollout — see `cloud-routines/ggx-bug-resolver.md` §4(b).)

### Step 3: Gather ids (semi-automated)

Collect three values. Validate each before continuing.

**3a. `environment_id`** — ask the colleague to paste it.
> "Paste your CCR `environment_id` (from claude.ai/code → Environments, or
> the `/remote-env` skill)."

Validate it looks like `env_…` (starts with `env_`, non-empty). If it does
not match, re-ask once, then STOP with a clear message.

**3b. Linear `connector_uuid`** — try to auto-discover first.

Call `RemoteTrigger action:list` and scan the returned triggers'
`mcp_connections[]` for an entry with `name == "Linear"`; if found, reuse
its `connector_uuid`:
```
RemoteTrigger  action=list
# scan response[].mcp_connections[] for { name: "Linear" } → connector_uuid
```
- If exactly one Linear `connector_uuid` is found → use it; tell the
  colleague which one ("reusing Linear connector `<uuid>` from your existing
  routine `<name>`").
- If multiple distinct ones are found → show them and ask which to use.
- If none found (fresh account, no routines yet) → ask:
  > "No existing routine to copy a Linear connector from. Paste your Linear
  > MCP `connector_uuid` (from the claude.ai Connectors page, or any
  > routine's config)."

Validate non-empty. STOP if still empty after one re-ask.

**3c. `TEAM_KEY`** — ask, default `CAF`.
> "Which Linear team key should the routine watch? [default: CAF]"

Accept the typed value or default to `CAF` on empty input. Uppercase it.

### Step 4: Build the create body

Use the template below VERBATIM. It is the validated shape from
`cloud-routines/ggx-bug-resolver.routine.json` — top-level
`{name, cron_expression, enabled, persist_session, job_config,
mcp_connections}`; `job_config.ccr = {environment_id, events,
session_context}`; `events[0].data = {type, message:{role, content}}`;
`session_context = {allowed_tools, model, sources}`. **Getting the nesting
wrong makes the API reject the body** (`unknown field` /
`event_type is required`), so do not restructure it.

Substitute exactly three placeholders:
- `<ENVIRONMENT_ID>` → the value from Step 3a.
- `<LINEAR_CONNECTOR_UUID>` → the value from Step 3b.
- `<TEAM_KEY>` → the value from Step 3c (appears in TWO places — the
  top-line `team <TEAM_KEY>` and the Phase 1 `Team:`/query lines).

Keep these FIXED — do NOT change them:
- `model: "claude-opus-4-8"` — see "Why opus" below.
- `cron_expression: "23 * * * *"`.
- `enabled: false` — initial create is disabled; we test before enabling.
- The three `sources[]` exactly as below.
- The full prompt text exactly as below (it already remaps
  `mcp__claude_ai_Linear__*` → `mcp__Linear__*`, `gh` → `mcp__github__*`,
  Agent spawns → inline, and uses `assignee=me`).

**Why opus (always set it explicitly):** `session_context.model` is
one-model-per-session. The claude.ai UI may default to **sonnet**; this
skill always pins **opus** because subagent spawns are inlined into this one
session (so the opus pinning in agent frontmatter is bypassed) and the
`/ggx-work` dev/bug work needs opus quality.

```json
{
  "name": "ggx-bug-resolver",
  "cron_expression": "23 * * * *",
  "enabled": false,
  "persist_session": false,
  "job_config": {
    "ccr": {
      "environment_id": "<ENVIRONMENT_ID>",
      "events": [
        {
          "data": {
            "type": "user",
            "message": {
              "role": "user",
              "content": "Hourly cloud bug-worker for team <TEAM_KEY>. SELF-DISCOVERING: find the routine-owner's actionable bug tickets and drive each to a DRAFT PR via the local /ggx-work pipeline. You ARE allowed to write code, commit, push, open draft PRs, and update Linear for MATCHED tickets — scoped to gogovan/gogox-client-flutter only.\n\n## Phase 1 — DISCOVERY (do this FIRST, before any bootstrap / clone / flutter install)\nUsing the Linear MCP tools in this env (namespace `mcp__Linear__*`), find tickets matching ALL of:\n- Team: <TEAM_KEY> (e.g. CAF = CA Flutter Revamp)\n- Assignee: ME (the routine owner — resolve via the Linear \"me\"/current-user filter; do NOT hardcode an email)\n- State: `To-do` (exact name, unstarted type)\n- Labels: include BOTH `bug` AND `ready-to-dev`\nQuery: `mcp__Linear__list_issues` with assignee=me, label=ready-to-dev, state=To-do, team=<TEAM_KEY>; then POST-FILTER to those whose full label set ALSO contains `bug`. Sort by priority (Urgent>High>Medium>Low>None) then createdAt ascending.\n\nIf ZERO match → emit the Phase 4 report with `Matches: 0` and STOP IMMEDIATELY. Do NOT install Flutter, do NOT touch any repo.\n\n## Phase 0 — BOOTSTRAP (only if >=1 match)\n```bash\nset -e\nexport PATH=\"/opt/flutter/bin:$PATH\"\nbash /home/user/gogox-claude/install.sh >/tmp/install.log 2>&1\nls ~/.claude/commands/ggx-work.md >/dev/null && echo 'gogox-claude: installed'\nif ! command -v flutter >/dev/null 2>&1; then\n  cd /tmp\n  curl -fsSL -o flutter.tar.xz 'https://storage.googleapis.com/flutter_infra_release/releases/stable/linux/flutter_linux_3.38.7-stable.tar.xz'\n  tar -xJf flutter.tar.xz -C /opt 2>&1 | tail -1; rm -f flutter.tar.xz\nfi\ngit config --global --add safe.directory '*'\nflutter config --no-analytics --no-cli-animations >/dev/null 2>&1\nflutter --version | head -2\ncd /home/user/gogox-client-flutter\nPROXY_REMOTE=$(git config --get remote.origin.url)\nPROXY_BASE=$(echo \"$PROXY_REMOTE\" | sed -E 's|/gogovan/gogox-client-flutter$||')\ngit config --global --add url.\"${PROXY_BASE}/\".insteadOf 'git@github.com:'\ngit config --global --add url.\"${PROXY_BASE}/\".insteadOf 'ssh://git@github.com/'\ngit fetch origin --quiet; git checkout trunk 2>&1 | tail -1; git pull --ff-only --quiet\n```\nRe-export `PATH=\"/opt/flutter/bin:$PATH\"` at the top of every later bash block.\n\n## Phase 2 — PROCESS EACH TICKET SEQUENTIALLY\nFor each matched ticket T (sorted), handle INDEPENDENTLY — one ticket's failure must NOT abort the rest:\n  a. Anti-duplicate: `mcp__github__list_pull_requests` on gogovan/gogox-client-flutter; if an OPEN PR's head branch or title references T → skip T, go to next.\n  b. Work it: read `~/.claude/commands/ggx-work.md` and follow it for `T --auto` (bug lane). ticket-init flips T to In Progress (= claim; a future fire's To-do filter then skips it). Apply the cloud overrides below.\n  c. Record outcome. On FAILURE: leave T claimed (In Progress) + post a Linear failure comment; do NOT abort — continue to the next ticket.\n  d. Reclaim disk: remove T's worktree (read `~/.claude/commands/remove-worktree.md`; if it refuses because the PR is unmerged, fall back to `cd /home/user/gogox-client-flutter && git worktree remove --force ../<T>`). ALWAYS clean before the next ticket so only ONE worktree exists at a time.\n  e. Next ticket.\n\n## Cloud-specific overrides (apply to every /ggx-work run)\n1. Linear namespace: local .md uses `mcp__claude_ai_Linear__*` → use `mcp__Linear__*` here.\n2. gh CLI NOT installed → use `mcp__github__*`: create_pull_request (draft=true), list_pull_requests, pull_request_read, update_pull_request.\n3. Subagent spawns (verify-agent, git-branch-code-reviewer, etc.) → do NOT call the Agent tool; read `~/.claude/agents/<name>.md` and follow inline.\n4. EnterWorktree unavailable → `cd ../<T>`; re-export the flutter PATH after cd.\n5. git push is fine; NEVER force-push, NEVER push to trunk, NEVER delete remote branches you did not create.\n6. PRs are DRAFT.\n7. Commit signing may be broken (0-byte key / HTTP 400) → if a commit fails on signing, retry with `-c commit.gpgsign=false`.\n\n## Phase 4 — REPORT (always emit, even on 0 matches)\n=== HOURLY BUG-WORKER OUTCOME ===\nRun (UTC)   : <timestamp>\nMatches     : <N>\nProcessed   : <per ticket outcome>\nPRs opened  : <urls | none>\nNotes       : <notable>\n=== HOURLY BUG-WORKER COMPLETE ==="
            }
          }
        }
      ],
      "session_context": {
        "allowed_tools": [
          "Bash",
          "Read",
          "Write",
          "Edit",
          "Glob",
          "Grep"
        ],
        "model": "claude-opus-4-8",
        "sources": [
          {
            "git_repository": {
              "url": "https://github.com/charlie-yang-gogox/gogox-claude"
            }
          },
          {
            "git_repository": {
              "allow_unrestricted_git_push": true,
              "url": "https://github.com/gogovan/gogox-client-flutter"
            }
          },
          {
            "git_repository": {
              "allow_unrestricted_git_push": true,
              "url": "https://github.com/gogovan/flutter-core-sdk"
            }
          }
        ]
      }
    }
  },
  "mcp_connections": [
    {
      "connector_uuid": "<LINEAR_CONNECTOR_UUID>",
      "name": "Linear",
      "transport_type": "http",
      "url": "https://mcp.linear.app/mcp"
    }
  ]
}
```

### Step 5: Create + test

**5a. Create (disabled).**
```
RemoteTrigger  action=create  body=<the substituted body from Step 4>
```
Hold the returned `trigger_id`. Relay the appended summary line (server-parsed
run time + claude.ai URL) to the colleague.

If create fails with a field/shape error (`unknown field`,
`event_type is required`, etc.) → the body was restructured; re-check the
nesting against Step 4 and retry. Do NOT enable on a failed create.

**5b. Fire one test run.**
```
RemoteTrigger  action=run  trigger_id=<trigger_id from 5a>
```
Then tell the colleague:
> Open **claude.ai/code → the `ggx-bug-resolver` session** and read the
> `=== HOURLY BUG-WORKER OUTCOME ===` block.
> - If you currently have **0 matching tickets**, this is a clean no-op
>   (`Matches: 0`) — it still validates the setup (model accepted, Linear
>   discovery ran, clean stop before any clone).
> - If you have a matching ticket (assignee=me, To-do, labels ⊇ {`bug`,
>   `ready-to-dev`} on team `<TEAM_KEY>`), it will run the bug lane and open
>   a real **DRAFT PR** on `gogovan/gogox-client-flutter`.

Wait for the colleague to confirm the test looks right before Step 6.

### Step 6: Enable the hourly cron

After the colleague confirms the test outcome looks right:
```
RemoteTrigger  action=update  trigger_id=<trigger_id>  body={ "enabled": true }
```
Then print:
```
ggx-bug-resolver is LIVE.
Trigger id : <trigger_id>
Cron       : 23 * * * *  (hourly, at minute 23)
Next run   : <server-parsed next run from the update summary line>
Model      : claude-opus-4-8
Team       : <TEAM_KEY>
Result appears at: <claude.ai/code routine session URL>
```

---

## What this does NOT automate

- **Connecting your Linear connector** in claude.ai (Step 2a) — interactive,
  per-account.
- **Creating the CCR environment + GitHub auth** (Step 2b) — interactive,
  per-account. The Claude GitHub App authorization (or an env `GH_TOKEN`)
  lives in the environment config, never in this skill.

This skill automates the mechanical, error-prone parts: the correct opus
body, the namespace/inlining overrides baked into the prompt, the test fire,
and the enable flip.

---

## Troubleshooting

- **Clone fails in the routine session** → the environment lacks **read
  access to `charlie-yang-gogox/gogox-claude`** (or write to
  `gogovan/gogox-client-flutter`). Fix the environment's GitHub auth
  (Step 2b), not the routine body.
- **Linear finds the wrong tickets, or none when you expect some** → the
  Linear connector is not connected, or it is the **wrong account** (so
  `assignee=me` resolves to someone else). Re-check Step 2a; confirm the
  connector belongs to the account that owns your CAF/DAF tickets.
- **Routine ran as sonnet** → `model` was not set to opus. Re-run
  `RemoteTrigger action:update` with the body's
  `job_config.ccr.session_context.model = "claude-opus-4-8"`. The UI default
  is the usual culprit.
- **Create rejected (`unknown field` / `event_type is required`)** → the
  body nesting was changed. Use the Step 4 template verbatim.

---

## Guardrails

- **Never inline tokens.** GitHub auth comes from the environment (Claude
  GitHub App or env `GH_TOKEN`); it must never appear in the routine body or
  in this skill's output. Always use the `RemoteTrigger` tool (in-process
  OAuth), never raw `curl`.
- **PRs stay draft.** The routine opens draft PRs only and is scoped to
  `gogovan/gogox-client-flutter`; it never force-pushes, never pushes to
  trunk, never deletes branches it did not create.
- **The routine is account-bound.** It runs under whoever ran this skill.
  A teammate must run `/ggx-cloud-onboard` in their own session to get
  their own routine — it cannot be shared.
- **Test before enable.** Create with `enabled: false`, fire one
  `action:run`, confirm the outcome, then flip `enabled: true`.
