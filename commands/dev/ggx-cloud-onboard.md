---
name: ggx-cloud-onboard
argument-hint: "[--dev-only | --analyzer-only]"
description: >
  Semi-automated onboarding for YOUR OWN cloud routine PAIR (CCR):
  `ticket-analyzer-agent` (judges To-Do tickets, writes `ready-to-*` /
  `need-*` verdict labels) + `ggx-dev-agent` (the unified all-lane
  port / dev / bug entry point to the gogox-claude `/ggx-work` pipeline,
  consuming those labels). The two are deliberately separate routines —
  the label window between analyze and dispatch is the human review point
  — but they share every placeholder and a schedule invariant (analyzer
  fires 30 minutes before each dev-agent slot), so this skill provisions
  them TOGETHER: gather ids once, derive the analyzer cron from the
  dev-agent cron, create + test + enable both. Pass `--dev-only` or
  `--analyzer-only` to provision just one. Run this in YOUR Claude Code
  session — the `RemoteTrigger` tool uses the CURRENT session's OAuth
  token, so the routines are created under whoever runs this skill. It
  automates the mechanical, error-prone parts (correct opus bodies,
  namespace overrides, team→repo substitution, the cron offset, test runs)
  but CANNOT do the two per-account interactive steps (connect Linear,
  create a CCR environment with GitHub auth) — those are prerequisites you
  do first. Canonical source of truth for the routine shapes + prompts:
  `cloud-routines/ggx-dev-agent.{md,routine.json}` and
  `cloud-routines/ticket-analyzer-agent.{md,routine.json}`. The bodies
  below are embedded inline (kept in sync with those JSONs) because
  install.sh does NOT symlink `cloud-routines/` into `~/.claude`.
Prerequisite: >
  - This must be run in YOUR own Claude Code session (research-preview
    Claude Code Routines access) — the routine is account-bound to you.
  - Linear MCP connector connected in claude.ai (so `assignee=me` resolves
    to you).
  - A CCR environment with GitHub auth to the 4 source repos (Claude GitHub
    App authorization preferred, else an env `GH_TOKEN`). Read access to
    `charlie-yang-gogox/gogox-claude` + the origin Android repo; write to the
    port-target Flutter repo + `gogovan/flutter-core-sdk`.
---

# `/ggx-cloud-onboard [--dev-only|--analyzer-only]`

> Creates + tests YOUR own cloud routine **pair**:
> - `ticket-analyzer-agent-<team>` — judges your To-Do tickets and writes
>   the verdict labels (`ready-to-*` / `need-*`). Analysis only; Linear
>   writes only; no push permission anywhere.
> - `ggx-dev-agent-<team>` — the unified all-lane worker that drives
>   `/ggx-work` for **port, dev, and bug** tickets, consuming `ready-to-*`.
>   (Supersedes the old `ggx-bug-resolver`, which only handled the bug lane.)
>
> **The pair contract (why one skill provisions both):** the routines stay
> separate at runtime — the label window between a verdict and its pickup
> is the human review point — but they share every placeholder (team, repos,
> environment, connector) and one schedule invariant: **the analyzer fires
> 30 minutes before each dev-agent slot** (TW 11:30→12:00 lunch,
> 17:30→18:00 after-work), so fresh `ready-to-*` labels are consumed by the
> very next dev fire. This skill gathers ids once, derives the analyzer
> cron from the dev-agent cron, and creates/tests/enables both — so the
> offset can never drift apart at provisioning time.
>
> **Flags:** `--dev-only` / `--analyzer-only` skip the other routine
> (e.g. `--analyzer-only` for a verdict-quality observation period before
> trusting automation end-to-end). Default: both.
>
> **Run this in YOUR OWN Claude Code session.** The `RemoteTrigger` tool
> authenticates with the CURRENT session's OAuth token, so the routines are
> created under whoever runs this skill. `assignee=me` inside each routine
> then resolves to YOU and discovery finds YOUR tickets. A teammate cannot
> create these for you — you run it yourself, once.
>
> **What it does NOT automate** (interactive, per-account — do these first):
> 1. Connecting your Linear MCP connector in claude.ai.
> 2. Creating a CCR environment with GitHub auth to the 4 repos.
> This skill gates on both (Step 2) and STOPS if either is missing.

---

## Steps

### Step 0: Parse flags

```
--dev-only      → PROVISION = {dev}
--analyzer-only → PROVISION = {analyzer}
(neither)       → PROVISION = {analyzer, dev}
(both flags)    → STOP: "--dev-only and --analyzer-only are mutually exclusive."
```

Steps marked **(dev)** or **(analyzer)** below run only when that routine
is in `PROVISION`. Unmarked steps always run.

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

**(b) A CCR environment exists with GitHub auth to the 4 repos.**
The environment must reach all four declared sources (the exact repos depend
on the team — see the mapping in Step 3d):
- `charlie-yang-gogox/gogox-claude` — **read** (the pipeline commands).
- `gogovan/<TARGET_REPO>` — **write** (the port-target Flutter app).
- `gogovan/flutter-core-sdk` — **write** (shared Flutter core SDK).
- `gogovan/<ORIGIN_REPO>` — **read** (the origin Android app the port lane
  reads FROM).
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
> rollout — see `cloud-routines/ggx-dev-agent.md` §4(b).)

> **openspec — no setup needed.** The port lane installs the openspec CLI
> (`npm install -g @fission-ai/openspec@1.3.1`) at bootstrap; node 22 / npm 10
> are preinstalled in the reference CCR environment, so this is verified to
> work out of the box. If you use a *different* environment, smoke-test it
> first (`npm install -g @fission-ai/openspec@1.3.1 && openspec --version`)
> before trusting the port lane.

> **No Linear labels to set up.** The routine writes no labels of its own
> (no in-flight / lock label exists). Claiming and the label lifecycle are
> owned by `/ggx-work` + `/port:ship` / `/dev:ship`. Nothing to pre-create.

### Step 3: Gather ids (semi-automated)

Collect the values below. Validate each before continuing.

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

**3d. Derive the repos from `TEAM_KEY`** (do NOT ask — look them up):

| `TEAM_KEY` | `<ORG>` | `<TARGET_REPO>` (write, port-to) | `<ORIGIN_REPO>` (read, port-from) |
|---|---|---|---|
| `CAF` | `gogovan` | `gogox-client-flutter` | `gogovan-client-v2-android` |
| `DAF` | `gogovan` | `gogox-driver-flutter`  | `gogovan-driver-android` |

If `TEAM_KEY` is neither `CAF` nor `DAF`, STOP and ask the colleague for the
target + origin repos explicitly (port is Linear-only and CAF/DAF are the
only known port teams).

### Step 4 (dev): Build the dev-agent create body

Use the template below VERBATIM. It is the validated shape from
`cloud-routines/ggx-dev-agent.routine.json` — top-level
`{name, cron_expression, enabled, persist_session, job_config,
mcp_connections}`; `job_config.ccr = {environment_id, events,
session_context}`; `events[0].data = {type, message:{role, content}}`;
`session_context = {allowed_tools, model, sources}`. **Getting the nesting
wrong makes the API reject the body** (`unknown field` /
`event_type is required`), so do not restructure it.

Substitute these placeholders (some appear in BOTH the prompt and the JSON
fields — replace every occurrence):
- `<TEAM_KEY_LOWER>` (in `name`) → the lowercased team key, e.g. `caf` →
  routine name `ggx-dev-agent-caf`.
- `<ENVIRONMENT_ID>` → the value from Step 3a.
- `<LINEAR_CONNECTOR_UUID>` → the value from Step 3b.
- `<TEAM_KEY>` → Step 3c (appears in the prompt top-line `team <TEAM_KEY>`
  and the two Phase 1 query lines).
- `<ORG>` / `<TARGET_REPO>` / `<ORIGIN_REPO>` → Step 3d (appear in the prompt
  bootstrap/overrides AND in `sources[]`).

Keep these FIXED — do NOT change them:
- `model: "claude-opus-4-8"` — see "Why opus" below.
- `cron_expression: "0 4,10 * * *"` (Taiwan time 12:00 lunch / 18:00
  after-work = 04/10 UTC). If the colleague wants different slots, accept
  them BUT note the analyzer cron in Step 4b is derived from this one —
  never set the two independently.
- `enabled: false` — initial create is disabled; we test before enabling.
- The `charlie-yang-gogox/gogox-claude` (read) and `gogovan/flutter-core-sdk`
  (write) sources — constant across teams.
- The full prompt text exactly as below (it already remaps
  `mcp__claude_ai_Linear__*` → `mcp__Linear__*`, `gh` → `mcp__github__*`,
  Agent spawns → inline, and uses `assignee=me`).

**Why opus (always set it explicitly):** `session_context.model` is
one-model-per-session. The claude.ai UI may default to **sonnet**; this
skill always pins **opus** because subagent spawns are inlined into this one
session (so the opus pinning in agent frontmatter is bypassed) and the
`/ggx-work` port/dev/bug work needs opus quality.

```json
{
  "name": "ggx-dev-agent-<TEAM_KEY_LOWER>",
  "cron_expression": "0 4,10 * * *",
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
              "content": "Hourly cloud dev-agent for team <TEAM_KEY>. SELF-DISCOVERING + LABEL-DRIVEN: find the routine-owner's actionable tickets and drive each through the local /ggx-work pipeline, which self-routes to port / dev / bug. You ARE allowed to write code, commit, push, open DRAFT PRs, and update Linear for MATCHED tickets — scoped to the declared <TARGET_REPO> only.\n\n## Phase 1 — DISCOVERY (do this FIRST, before any bootstrap / clone / toolchain install)\nUsing the Linear MCP tools in this env (namespace `mcp__Linear__*`), run TWO queries (state filter OMITTED on purpose — the port→dev hand-off keeps status at In Progress, so a To-do filter would drop wave-2 port tickets):\n- Q_port: `mcp__Linear__list_issues` assignee=me, team=<TEAM_KEY>, label=`ready-to-port`\n- Q_dev : `mcp__Linear__list_issues` assignee=me, team=<TEAM_KEY>, label=`ready-to-dev`\nMerge by ticket id (union the label arrays). POST-FILTER OUT any ticket whose status type is `completed` / `canceled`. Sort by priority (Urgent>High>Medium>Low>None) then createdAt ascending. Tag each survivor with its lane bucket: `port` (had ready-to-port) or `dev` (had ready-to-dev). A ticket cannot be in both buckets at once (the labels are mutually exclusive across waves).\n\nIf ZERO match → emit the Phase 4 report with `Matches: 0` and STOP IMMEDIATELY. Do NOT install anything, do NOT touch any repo.\n\n## Phase 0 — BOOTSTRAP (only if >=1 match; install CONDITIONALLY on matched buckets)\n```bash\nset -e\nbash /home/user/gogox-claude/install.sh >/tmp/install.log 2>&1\nls ~/.claude/commands/ggx-work.md >/dev/null && echo 'gogox-claude: installed'   # flattened: NOT dev/ggx-work.md\ngit config --global --add safe.directory '*'\ngit config --global commit.gpgsign false   # sandbox signing is broken (0-byte key / HTTP 400) → commit unsigned from the start; do not rely on a per-commit fallback\ncd /home/user/<TARGET_REPO>\nPROXY_REMOTE=$(git config --get remote.origin.url)\nPROXY_BASE=$(echo \"$PROXY_REMOTE\" | sed -E 's|/<ORG>/<TARGET_REPO>$||')\ngit config --global --add url.\"${PROXY_BASE}/\".insteadOf 'git@github.com:'\ngit config --global --add url.\"${PROXY_BASE}/\".insteadOf 'ssh://git@github.com/'\ngit fetch origin --quiet; git checkout trunk 2>&1 | tail -1; git pull --ff-only --quiet\n```\n- If the `ready-to-dev` bucket is non-empty (dev/bug lane builds + opens a PR):\n```bash\nexport PATH=\"/opt/flutter/bin:$PATH\"\nif ! command -v flutter >/dev/null 2>&1; then\n  cd /tmp\n  curl -fsSL -o flutter.tar.xz 'https://storage.googleapis.com/flutter_infra_release/releases/stable/linux/flutter_linux_3.38.7-stable.tar.xz'\n  tar -xJf flutter.tar.xz -C /opt 2>&1 | tail -1; rm -f flutter.tar.xz\nfi\nflutter config --no-analytics --no-cli-animations >/dev/null 2>&1\nflutter --version | head -2\n```\nRe-export `PATH=\"/opt/flutter/bin:$PATH\"` at the top of every later bash block when the dev/bug lane runs.\n- If the `ready-to-port` bucket is non-empty (port wave-1 synth needs the openspec CLI):\n```bash\n# openspec CLI = npm package @fission-ai/openspec (bin `openspec`). Pinned to the\n# version local dev uses (1.3.1) so the CLI output the synth loop parses (openspec\n# status/instructions/validate --json) does not drift. /port:start|synth|ship call:\n# openspec new change | status | instructions | validate | archive.\nif ! command -v openspec >/dev/null 2>&1; then\n  command -v npm >/dev/null 2>&1 || { echo 'FATAL: npm/node absent in sandbox — add a node bootstrap before installing openspec'; }\n  npm install -g @fission-ai/openspec@1.3.1 2>&1 | tail -3\nfi\nopenspec --version || { echo 'FATAL: openspec CLI unavailable — cannot run port lane'; }\n# Seed the MAIN-repo port-settings.json NOW. /port:start Step 3 reads\n# <main-repo>/.claude/port-settings.json BEFORE the worktree exists and --auto\n# STOPs if it is missing. (The worktree copy is seeded later by override #5,\n# because git worktrees do NOT inherit this gitignored file.)\nmkdir -p /home/user/<TARGET_REPO>/.claude\nprintf '{\\n  \"originalProjectPath\": \"/home/user/<ORIGIN_REPO>\"\\n}\\n' > /home/user/<TARGET_REPO>/.claude/port-settings.json\n```\nFlutter is NOT needed for port wave-1 (no build).\n\n## Phase 2 — PROCESS EACH TICKET SEQUENTIALLY\nThis routine writes NO Linear labels of its own. Claiming + the label/status lifecycle are owned entirely by /ggx-work (its ticket-init) and /port:ship / /dev:ship. There is NO in-flight label and NO crash recovery — if a fire dies mid-pipeline the ticket is just left at In Progress (orphan) and a human re-adds `ready-to-*` to restart it from scratch.\nFor each matched ticket T (sorted), handle INDEPENDENTLY — one ticket's failure must NOT abort the rest. `<lane>` = T's bucket (`port` or `dev`).\n  a. ANTI-DUPLICATE (durable completion check — skip work that's already done):\n     - port bucket: `git ls-remote --heads origin \"feat/<T>\"` exists, OR T's labels already include `need-spec-review` → skip T (already shipped / awaiting human spec-review).\n     - dev bucket: `mcp__github__list_pull_requests` on <TARGET_REPO>; an OPEN PR's head branch or title references T → skip T (already shipped).\n  b. WORK IT: read `~/.claude/commands/ggx-work.md` (flattened — NOT `dev/ggx-work.md`) and follow it for `T --auto`. /ggx-work's ticket-init (Step 2.5) is the CLAIM: it flips status To-do→In Progress and removes `ready-to-*`, so the next fire's discovery won't re-match T. Apply the cloud overrides below. /ggx-work → /route self-selects /port:ff (wave-1) or /dev:ff (wave-2 / feature) or /bug:ff.\n  c. RECORD the `[ggx-work-result] outcome=<...>` line — informational only, NO label writes by this routine:\n     - `done` (dev/bug PR open) / `port-paused` (port shipped, /port:ship already added need-spec-review) → nothing to do; labels are already correct.\n     - `failed` → post a Linear failure comment; leave T at In Progress (orphan, no `ready-to-*`). Do NOT abort the batch; continue to the next ticket. (A human re-adds `ready-to-*` later to restart T from scratch.)\n  d. RECLAIM DISK: remove T's worktree (read `~/.claude/commands/remove-worktree.md`; if it refuses because the PR is unmerged, fall back to `cd /home/user/<TARGET_REPO> && git worktree remove --force ../<T>`). ALWAYS clean before the next ticket so only ONE worktree exists at a time.\n  e. Next ticket.\n\n## Cloud-specific overrides (apply to every /ggx-work run)\n1. Linear namespace: local .md uses `mcp__claude_ai_Linear__*` → use `mcp__Linear__*` here (for /ggx-work's calls, and for this routine's only direct Linear write — the Phase 2c failure comment).\n2. gh CLI NOT installed → use `mcp__github__*` for dev/bug PRs: create_pull_request (draft=true), list_pull_requests, pull_request_read, update_pull_request. The PORT lane opens NO PR — /port:ship only `git push`es feat/<T>.\n3. Subagent spawns are inlined by `--auto` (dev-consult / pm / designer / synth / verify / review). Do NOT call the Agent tool; if a step still references an agent, read `~/.claude/agents/<name>.md` and follow inline.\n4. `/add-worktree` (invoked by /dev:start and /port:start) is INTERACTIVE and has NO `--auto` mode — in the cloud there is no human, so resolve its prompts deterministically as you follow it:\n   - Its Step 4 says \"use the EnterWorktree tool\" → EnterWorktree is unavailable here; instead `cd ../<T>` and re-export `PATH=\"/opt/flutter/bin:$PATH\"` after cd (dev/bug lane).\n   - Its Step 2 branch/worktree-state questions → resolve WITHOUT prompting: only the **remote** `feat/<T>` exists → `git worktree add --track -b feat/<T> ../<T> origin/feat/<T>` (this is the **port wave-2 / resume** case — restores the committed OpenSpec change); local branch exists (with or without remote) → reuse it (`git worktree add ../<T> feat/<T>`); `../<T>` already a registered worktree → enter it (skip create); `../<T>` is a non-worktree dir → STOP (never overwrite). Neither branch nor worktree exists → normal create from `origin/trunk`.\n5. PORT origin path: the port pipeline reads `<worktree>/.claude/port-settings.json`. Git worktrees do NOT inherit it. So IMMEDIATELY AFTER /port:start (via /add-worktree) creates `../<T>`, and BEFORE /port:explore runs, write `../<T>/.claude/port-settings.json` containing exactly `{\"originalProjectPath\": \"/home/user/<ORIGIN_REPO>\"}`. In --auto /port:explore STOPs if this file is missing, so this seed is mandatory for the port lane.\n6. git push is fine; NEVER force-push, NEVER push to trunk, NEVER delete remote branches you did not create. PRs (dev/bug only) are DRAFT.\n7. Commit signing is broken in the sandbox (0-byte key / HTTP 400). Phase 0 already sets `git config --global commit.gpgsign false`, so every commit is unsigned from the start. Belt-and-suspenders: if a commit STILL fails on signing (e.g. a sub-command overrides config), retry with `-c commit.gpgsign=false`. (Do NOT rely on a piped `... | tail` masking the exit code — set the global config, don't depend on the fallback firing.)\n\n## Phase 4 — REPORT (always emit, even on 0 matches)\n=== HOURLY DEV-AGENT OUTCOME ===\nRun (UTC)            : <timestamp>\nMatches              : port=<n> dev=<m>\nProcessed            : <per ticket: id lane outcome>\nPRs opened (dev/bug) : <urls | none>\nSpecs → spec-review  : <port ids now at need-spec-review | none>\nOrphaned (failed)    : <ids left In Progress | none>\nNotes                : <notable>\n=== HOURLY DEV-AGENT COMPLETE ==="
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
              "url": "https://github.com/<ORG>/<TARGET_REPO>"
            }
          },
          {
            "git_repository": {
              "allow_unrestricted_git_push": true,
              "url": "https://github.com/gogovan/flutter-core-sdk"
            }
          },
          {
            "git_repository": {
              "url": "https://github.com/<ORG>/<ORIGIN_REPO>"
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

### Step 4b (analyzer): Build the analyzer create body

**Derive the cron — never ask for it.** The analyzer fires 30 minutes
before each dev-agent slot. Rule: for every hour `H` in the dev-agent
cron's hour list, the analyzer gets hour `(H - 1) mod 24` with minute `30`:

```
dev cron      0 4,10 * * *        (TW 12:00 / 18:00)
analyzer cron 30 3,9 * * *        (TW 11:30 / 17:30)
```

(With `--analyzer-only` and no existing dev-agent routine, derive from the
default dev cron `0 4,10 * * *`. With `--analyzer-only` and an existing
`ggx-dev-agent-<TEAM_KEY_LOWER>` routine, fetch its live cron via
`RemoteTrigger action:list` and derive from THAT — the offset must track
reality, not the default.)

Then build the body from the validated shape in
`cloud-routines/ticket-analyzer-agent.routine.json` (same top-level
nesting as Step 4). Substitute the same Step 3 values:
`<TEAM_KEY>` / `<TEAM_KEY_LOWER>` / `<ENVIRONMENT_ID>` /
`<LINEAR_CONNECTOR_UUID>` / `<ORG>` / `<TARGET_REPO>`.

Keep these FIXED:
- `model: "claude-opus-4-8"` — verdicts are judgment work; same reasoning
  as the dev-agent's opus pin.
- `enabled: false` on create — test before enabling.
- **Sources are READ-ONLY** — `gogox-claude` + `<ORG>/<TARGET_REPO>`,
  NEITHER with `allow_unrestricted_git_push`. The analyzer has no
  legitimate push; do not copy the dev-agent's source permissions.
- No `<ORIGIN_REPO>` and no `flutter-core-sdk` source — the analyzer
  never reads code, it only needs the target repo for profile resolution.
- The prompt text exactly as in the JSON template (it already remaps
  `mcp__claude_ai_Linear__*` → `mcp__Linear__*`, mandates
  `--non-interactive`, and forbids invoking any pipeline).

```json
{
  "name": "ticket-analyzer-agent-<TEAM_KEY_LOWER>",
  "cron_expression": "<derived above>",
  "enabled": false,
  "persist_session": false,
  "job_config": {
    "ccr": {
      "environment_id": "<ENVIRONMENT_ID>",
      "events": [ { "data": { "type": "user", "message": { "role": "user",
        "content": "<the prompt from cloud-routines/ticket-analyzer-agent.routine.json, with <TEAM_KEY> and <TARGET_REPO> substituted>"
      } } } ],
      "session_context": {
        "allowed_tools": ["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
        "model": "claude-opus-4-8",
        "sources": [
          { "git_repository": { "url": "https://github.com/charlie-yang-gogox/gogox-claude" } },
          { "git_repository": { "url": "https://github.com/<ORG>/<TARGET_REPO>" } }
        ]
      }
    }
  },
  "mcp_connections": [
    { "connector_uuid": "<LINEAR_CONNECTOR_UUID>", "name": "Linear",
      "transport_type": "http", "url": "https://mcp.linear.app/mcp" }
  ]
}
```

If this session has the repo checked out, read the canonical prompt from
`cloud-routines/ticket-analyzer-agent.routine.json` directly (it is NOT
symlinked into `~/.claude`); otherwise ask the colleague to paste it from
GitHub. Do NOT improvise the prompt from memory.

### Step 5: Create + test (analyzer first — it is the cheaper, safer probe)

**5a (analyzer). Create disabled, fire a test.**
```
RemoteTrigger  action=create  body=<the substituted body from Step 4b>
RemoteTrigger  action=run     trigger_id=<analyzer trigger_id>
```
Hold the returned `trigger_id`. Tell the colleague:
> Open **claude.ai/code → the `ticket-analyzer-agent-<TEAM_KEY_LOWER>`
> session** and read the `=== TICKET-ANALYZER OUTCOME ===` block.
> - **0 candidates** is a clean no-op — it still validates the whole stack
>   (model accepted, Linear connector resolves `assignee=me`, clean stop
>   before any install).
> - With candidates: check the verdicts and the Linear comments it posted.
>   Wrong verdicts at this stage are FEEDBACK, not failure — fix the ticket
>   content or labels and note the misjudgment (this is the data the
>   observation period exists to collect).

The analyzer test doubles as the **Linear-connector probe for the pair**:
if `assignee=me` resolves wrongly here, fix Step 2a before wasting a
dev-agent test fire on the same problem.

**5b (dev). Create disabled, fire a test.**
```
RemoteTrigger  action=create  body=<the substituted body from Step 4>
RemoteTrigger  action=run     trigger_id=<dev trigger_id>
```
If create fails with a field/shape error (`unknown field`,
`event_type is required`, etc.) → the body was restructured; re-check the
nesting against Step 4 and retry. Do NOT enable on a failed create.

Then tell the colleague:
> Open **claude.ai/code → the `ggx-dev-agent-<TEAM_KEY_LOWER>` session** and
> read the `=== HOURLY DEV-AGENT OUTCOME ===` block.
> - If you currently have **0 matching tickets**, this is a clean no-op
>   (`Matches: port=0 dev=0`) — it still validates the setup (model accepted,
>   Linear discovery ran, clean stop before any clone / toolchain install).
> - If you have a matching ticket on team `<TEAM_KEY>` (assignee=me):
>   - `ready-to-port` → runs **port wave-1**, pushes a `feat/<ID>` branch on
>     `<TARGET_REPO>` and flips the ticket to `need-spec-review` (no PR).
>   - `ready-to-dev` → runs the **dev/bug** lane and opens a real **DRAFT PR**
>     on `<TARGET_REPO>`.

> First-run tip: the safest validation is a **0-match** fire (clean no-op).
> For an end-to-end check, prefer testing the **port wave-1** path first
> (no build, no PR), then — after a human `/spec-review` flips the ticket to
> `ready-to-dev` — the **port wave-2 / dev** path, before enabling the cron.

Wait for the colleague to confirm the test(s) look right before Step 6.

### Step 6: Enable the cron(s)

After the colleague confirms the test outcomes look right, enable each
created routine:
```
RemoteTrigger  action=update  trigger_id=<analyzer trigger_id>  body={ "enabled": true }
RemoteTrigger  action=update  trigger_id=<dev trigger_id>       body={ "enabled": true }
```
Then print (omit the line for a routine not in `PROVISION`):
```
Routine pair for team <TEAM_KEY> is LIVE.

ticket-analyzer-agent-<TEAM_KEY_LOWER>
  Trigger id  : <analyzer trigger_id>
  Cron        : 30 3,9 * * *  (TW 11:30 / 17:30 — fires 30 min before each dev slot)
  Writes      : Linear labels + comments ONLY (no code, no PRs)

ggx-dev-agent-<TEAM_KEY_LOWER>
  Trigger id  : <dev trigger_id>
  Cron        : 0 4,10 * * *  (TW 12:00 lunch / 18:00 after-work)
  Target repo : <ORG>/<TARGET_REPO>   (write, draft PRs / port branches)
  Origin repo : <ORG>/<ORIGIN_REPO>   (read, port source)

Model    : claude-opus-4-8 (both)
Next runs: <server-parsed next-run lines from the update summaries>
Results appear at: <claude.ai/code routine session URLs>

Funnel: analyzer judges To-Do → ready-to-* labels → dev-agent executes.
The 30-min offset is the pair contract — if you ever change one cron,
re-run /ggx-cloud-onboard (or re-derive by the Step 4b rule) so they
move together.
```

---

## What this does NOT automate

- **Connecting your Linear connector** in claude.ai (Step 2a) — interactive,
  per-account.
- **Creating the CCR environment + GitHub auth** (Step 2b) — interactive,
  per-account. The Claude GitHub App authorization (or an env `GH_TOKEN`)
  lives in the environment config, never in this skill.

This skill automates the mechanical, error-prone parts: the correct opus
bodies, the team→repo substitution, the namespace/inlining overrides baked
into the prompts, the analyzer-before-dev cron offset, the test fires, and
the enable flips.

---

## Troubleshooting

- **Clone fails in the routine session** → the environment lacks GitHub
  access to one of the 4 repos: **read** on `charlie-yang-gogox/gogox-claude`
  + `<ORG>/<ORIGIN_REPO>`, **write** on `<ORG>/<TARGET_REPO>` +
  `gogovan/flutter-core-sdk`. Fix the environment's GitHub auth (Step 2b),
  not the routine body.
- **Port lane STOPs at explore / "port-settings.json missing"** → the origin
  repo isn't reachable, or the team→repo mapping (Step 3d) was wrong so
  `<ORIGIN_REPO>` points nowhere. Re-check Step 3d and the origin repo's read
  access.
- **`openspec: command not found` in the port lane** → the environment's node
  bootstrap differs from the reference env. Smoke-test
  `npm install -g @fission-ai/openspec@1.3.1 && openspec --version` in the
  environment.
- **Linear finds the wrong tickets, or none when you expect some** → the
  Linear connector is not connected, or it is the **wrong account** (so
  `assignee=me` resolves to someone else). Re-check Step 2a; confirm the
  connector belongs to the account that owns your CAF/DAF tickets.
- **Routine ran as sonnet** → `model` was not set to opus. Re-run
  `RemoteTrigger action:update` with the body's
  `job_config.ccr.session_context.model = "claude-opus-4-8"`. The UI default
  is the usual culprit.
- **Create rejected (`unknown field` / `event_type is required`)** → the
  body nesting was changed. Use the Step 4 / 4b templates verbatim.
- **Analyzer STOPs at "Cannot resolve gogox project profile"** → the
  `cd /home/user/<TARGET_REPO>` in its bootstrap didn't happen, or the
  repo basename has no registry entry. The analyzer resolves the team key
  from cwd; re-check the Step 3d mapping and that install.sh ran.
- **Analyzer labels tickets the dev-agent then ignores** → check the
  classification labels (`bug`/`port`/`feature`): the analyzer writes
  workflow labels, but `/route` routes by classification. A ticket the
  analyzer marked ready that lacks classification will dead-end in
  `/ggx-work` — that's a ticket-content problem, not a routine problem.
- **Verdicts disagree with your judgment** → expected during the
  observation period. Note them (the misjudgment rate is the input to the
  deferred `--analyze-first` dispatcher-integration decision); fix the
  ticket and let the next fire re-analyze.

---

## Guardrails

- **Never inline tokens.** GitHub auth comes from the environment (Claude
  GitHub App or env `GH_TOKEN`); it must never appear in the routine body or
  in this skill's output. Always use the `RemoteTrigger` tool (in-process
  OAuth), never raw `curl`.
- **PRs stay draft; scope is hard-limited.** The dev/bug lane opens draft PRs
  only; the port lane opens no PR (it only pushes a `feat/<ID>` branch). All
  writes are scoped to `<ORG>/<TARGET_REPO>` (+ `flutter-core-sdk`); the
  origin repo is **read-only**. The routine never force-pushes, never pushes
  to trunk, never deletes branches it did not create.
- **The routine is account-bound.** It runs under whoever ran this skill.
  A teammate must run `/ggx-cloud-onboard` in their own session to get
  their own routine — it cannot be shared.
- **Test before enable.** Create with `enabled: false`, fire one
  `action:run`, confirm the outcome, then flip `enabled: true`.
- **Crash = restart from scratch (v1).** There is no in-flight tracking and
  no auto-recovery: a failed fire orphans the ticket at In Progress with no
  `ready-to-*`; a human re-adds the label to re-run it.
- **The pair stays a pair, loosely.** The analyzer and dev-agent are
  separate routines ON PURPOSE — the label window between a verdict and
  its pickup is the human review point, and one routine failing must not
  stall the other. The ONLY coupling this skill enforces is the
  provisioning-time cron offset (analyzer = dev hours − 1, minute 30).
  Never collapse the two into one routine, and never edit one cron without
  re-deriving the other.
- **The analyzer never escalates.** Its sources carry no push permission
  and its prompt forbids invoking `/ggx-work` / any pipeline. If a future
  edit adds either, that's the line being crossed — stop and rethink.
