---
name: ggx-cloud-onboard-review
argument-hint: "[--team:<KEY>]"
description: >
  Semi-automated onboarding for YOUR OWN cloud PR-code-review routine
  (`<team>-flutter-code-review`): a weekday-scheduled CCR routine that
  sweeps the target Flutter repo's open PRs, cross-references the ticket
  (Linear CAF/DAF, Jira CET/DET), reviews the diff against the ticket +
  repo conventions, and posts a structured `# Internal code review`
  comment on each PR (dedup + CI + bot-author gated). It is the CLOUD
  counterpart of the local `/code-review --batch` command — same review
  contract, but fired on cron under the routine owner's account. This
  skill parameterises the ONLY parts that differ per person / per team —
  environment_id, the Linear + Atlassian connector uuids, the team→repo
  mapping, and the cron slot — and ASKS for them at first setup, then
  creates + test-fires + enables the routine. It automates the mechanical,
  error-prone parts (correct create-body nesting, team→repo substitution,
  connector auto-discovery, the test fire, the enable flip) but CANNOT do
  the two per-account interactive prerequisites (connect the Linear /
  Atlassian connectors, create a CCR environment whose `gh` is authed to
  the repo) — those you do first. The canonical proven shape is the live
  `ca/da-flutter-code-review` routines this skill was distilled from; the
  create body below is embedded inline because install.sh does NOT symlink
  anything extra into `~/.claude`.
Prerequisite: >
  - This must be run in YOUR OWN Claude Code session (research-preview
    Claude Code Routines access) — the routine is account-bound to you
    (it posts reviews as you, under your `gh` identity).
  - Linear MCP connector connected in claude.ai (for CAF/DAF ticket
    cross-reference). Atlassian-Rovo connector too if you want CET/DET
    (Jira) cross-reference — optional, the routine degrades gracefully
    without it.
  - A CCR environment whose `gh` CLI is authenticated to the target repo
    with permission to post PR reviews (pull-requests: write). The review
    is posted via `gh pr review`, NOT git push — the source is cloned
    read-only.
---

# `/ggx-cloud-onboard-review [--team:<KEY>]`

> Creates + tests YOUR own cloud **PR-code-review** routine:
> `<team>-flutter-code-review` — on a weekday cron it lists the target
> repo's open PRs, filters out bot authors and CI-red PRs, skips PRs it
> already reviewed (dedup on the `# Internal code review` comment marker vs
> latest commit date), reads each diff against the linked ticket + repo
> `CLAUDE.md`, and posts a structured review comment. **Read-only on the
> repo (no push); its only writes are `gh pr review` comments.**
>
> **Cloud twin of `/code-review --batch`.** The local command reviews the
> cwd repo's open PRs on demand; this routine runs the same review contract
> unattended on cron. Nothing here invokes a GGC pipeline — the routine is
> a fully self-contained prompt (no install.sh, no `/route`, no worktrees).
>
> **What differs per person / per team is ASKED at first setup** (Step 3):
> the routine name, environment_id, the Linear + Atlassian connector uuids,
> the team→repo mapping, and the cron slot. Everything else — the review
> prompt, the model, the read-only single-source shape — is fixed from the
> proven routine.
>
> **Run this in YOUR OWN Claude Code session.** The `RemoteTrigger` tool
> authenticates with the CURRENT session's OAuth token, so the routine is
> created under whoever runs this skill, and its `gh` reviews are posted as
> that account. A teammate cannot create it for you — each person runs it
> once in their own session.
>
> **What it does NOT automate** (interactive, per-account — do these first):
> 1. Connecting your Linear (and optionally Atlassian-Rovo) connector.
> 2. Creating a CCR environment whose `gh` is authed to the repo.
> This skill gates on both (Step 2) and STOPS if a required one is missing.

---

## Steps

### Step 0: Parse flags

```
--team:<KEY>   → pre-seed TEAM_KEY (still confirmed in Step 3d)
(none)         → TEAM_KEY asked in Step 3d, default CAF
```

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
  Request access, then re-run /ggx-cloud-onboard-review in a fresh session.
  ```

Do NOT attempt any `curl` to the triggers API — always use the
`RemoteTrigger` tool so the OAuth token is added in-process and never
exposed.

### Step 2: Prerequisite gate (interactive — cannot be automated)

Confirm the following with the colleague (use `AskUserQuestion` or ask
plainly). If a REQUIRED one is not done, print the matching how-to and
**STOP** — do NOT create anything.

**(a) Linear connector connected in claude.ai.** *(required)*
Needed so CAF/DAF PRs can be cross-referenced to their ticket.
> How-to: claude.ai → Settings → Connectors → connect **Linear**
> (`https://mcp.linear.app/mcp`). Authorize with the account that owns your
> CAF/DAF tickets.

**(b) Atlassian-Rovo connector connected in claude.ai.** *(optional)*
Only needed if PRs in this repo reference Jira (CET/DET) tickets and you
want that cross-reference. If absent, the routine still reviews every PR;
it just prints "No ticket found" for Jira-only PRs instead of pulling Jira
context.
> How-to: claude.ai → Settings → Connectors → connect **Atlassian**
> (`https://mcp.atlassian.com/v1/mcp`).

**(c) A CCR environment with `gh` authed to the target repo.** *(required)*
The routine posts reviews via `gh pr review`, so the environment's `gh`
must be authenticated to `<ORG>/<TARGET_REPO>` (see Step 3d mapping) with
permission to post PR reviews.
> How-to (preferred): authorize the **Claude GitHub App** on the repo.
> Fallback: inject a **service-account / org `GH_TOKEN`** (NOT a personal
> PAT) into the environment config. **NEVER** inline a token into the
> routine prompt — GitHub auth comes from the environment only.

If a required gate fails:
```
STOP: prerequisite not met (<which one>).
Complete the how-to above, then re-run /ggx-cloud-onboard-review.
```

### Step 3: Gather the differing values (asked at first setup)

These are the only per-person / per-team values. Collect and validate each.

**3a. `environment_id`** — ask the colleague to paste it.
> "Paste the CCR `environment_id` this review routine should run in (from
> claude.ai/code → Environments). It must be an environment whose `gh` is
> authed to the target repo."

Validate it looks like `env_…` (starts with `env_`, non-empty). Re-ask once
on mismatch, then STOP.

**3b. Linear `connector_uuid`** — try to auto-discover first.

Call `RemoteTrigger action:list` and scan the returned triggers'
`mcp_connections[]` for an entry with `name == "Linear"`; reuse its
`connector_uuid` if found:
```
RemoteTrigger  action=list
# scan response[].mcp_connections[] for { name: "Linear" } → connector_uuid
```
- Exactly one found → use it; tell the colleague which routine it came from.
- Multiple distinct → show them and ask which to use.
- None found → ask the colleague to paste their Linear MCP `connector_uuid`
  (from the claude.ai Connectors page, or any routine's config).

Validate non-empty. STOP if still empty after one re-ask.

**3c. Atlassian-Rovo `connector_uuid`** — auto-discover, OPTIONAL.

Same scan for `name == "Atlassian-Rovo"` (or `Atlassian`). If found, include
it. If none found, ask ONCE whether they want Jira cross-reference:
- Yes → ask them to paste the Atlassian connector uuid.
- No / skip → build the body with the Linear connector ONLY (drop the
  Atlassian entry from `mcp_connections` and note in the prompt that Jira
  cross-reference is unavailable). This is a supported, degraded config.

**3d. `TEAM_KEY`** — confirm (default from `--team:` flag, else `CAF`).
> "Which team's Flutter repo should the routine review? [default: CAF]"

Uppercase it. Then **derive the repo (do NOT ask — look it up):**

| `TEAM_KEY` | `<ORG>` | `<TARGET_REPO>` (reviewed, read-only) |
|---|---|---|
| `CAF` | `gogovan` | `gogox-client-flutter` |
| `DAF` | `gogovan` | `gogox-driver-flutter`  |

If `TEAM_KEY` is neither `CAF` nor `DAF`, STOP and ask for the repo
explicitly (these are the only known Flutter review targets).

**3e. `cron_expression`** — default `0 19 * * 1-5`, ask if they want another.
> "When should the review sweep fire? [default: `0 19 * * 1-5` — weekdays
> 19:00 UTC]"

Accept a valid cron or default. Unlike the dev-agent pair, this routine has
NO cron-offset invariant — it is standalone, so any slot is fine.

**3f. `ROUTINE_NAME`** — ALWAYS ask; default `<team>-flutter-code-review`.
> "What should this routine be named? [default: `<ca|da>-flutter-code-review`]"

Do NOT silently reuse the derived name: routine names are not unique, so a
plain `create` with an existing name adds a DUPLICATE (RemoteTrigger has no
delete — a duplicate is hard to undo). Before creating, `RemoteTrigger
action:list` and check the chosen name:
- Name already exists → tell the colleague and ask whether to (i) pick a
  different name, or (ii) `action:update` that existing trigger id instead
  of creating a second one. Never blind-`create` over an existing name.
- Name free → proceed to create.

### Step 4: Build the create body

Use the shape below VERBATIM — top-level
`{name, cron_expression, enabled, persist_session, job_config,
mcp_connections}`; `job_config.ccr = {environment_id, events,
session_context}`; `events[0].data = {type, message:{role, content}}`;
`session_context = {allowed_tools, model, sources}`. **Getting the nesting
wrong makes the API reject the body** (`unknown field` /
`event_type is required`) — do not restructure it.

Substitute:
- `<ROUTINE_NAME>` (the `name` field) → the name asked in Step 3f (default
  `<ca|da>-flutter-code-review`, two-letter form matching the proven
  routines — map `caf`→`ca`, `daf`→`da`).
- `<ENVIRONMENT_ID>` → Step 3a.
- `<LINEAR_CONNECTOR_UUID>` → Step 3b.
- `<ATLASSIAN_CONNECTOR_UUID>` → Step 3c (omit the whole `mcp_connections`
  entry if skipped).
- `<CRON>` → Step 3e.
- `<ORG>` / `<TARGET_REPO>` → Step 3d (appear in the prompt AND in
  `sources[0].git_repository.url`).

Keep these FIXED — do NOT change them:
- `model: "claude-sonnet-4-6"` — the proven review model (code review is a
  bounded read-and-annotate task; the live routines have run on sonnet for
  months). If a colleague wants a stronger model, that is a deliberate
  override, not a default.
- `enabled: false` on create — test before enabling.
- **The single source is READ-ONLY** — `<ORG>/<TARGET_REPO>` with NO
  `allow_unrestricted_git_push`. The routine never pushes; reviews are `gh`
  API writes, not git writes. Do NOT add push permission and do NOT add any
  other repo (no gogox-claude, no origin, no core-sdk — this routine runs
  no pipeline).
- The full review prompt exactly as in the block below.

```json
{
  "name": "<ROUTINE_NAME>",
  "cron_expression": "<CRON>",
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
              "content": "<the review-agent prompt below, with <ORG>/<TARGET_REPO> substituted, as a single JSON string>"
            }
          }
        }
      ],
      "session_context": {
        "allowed_tools": ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "WebSearch"],
        "model": "claude-sonnet-4-6",
        "sources": [
          { "git_repository": { "url": "https://github.com/<ORG>/<TARGET_REPO>" } }
        ]
      }
    }
  },
  "mcp_connections": [
    { "connector_uuid": "<LINEAR_CONNECTOR_UUID>", "name": "Linear",
      "transport_type": "http", "url": "https://mcp.linear.app/mcp" },
    { "connector_uuid": "<ATLASSIAN_CONNECTOR_UUID>", "name": "Atlassian-Rovo",
      "transport_type": "http", "url": "https://mcp.atlassian.com/v1/mcp" }
  ]
}
```

#### Review-agent prompt (substitute `<ORG>/<TARGET_REPO>`, then JSON-encode as one string)

```
You are an automated PR code reviewer.

## Configuration

- **Repository**: <ORG>/<TARGET_REPO>
- **Language/Framework**: Flutter/Dart
- **Ticket System**: Linear (CAF/DAF prefix) + Jira via Atlassian-Rovo (CET/DET prefix)
- **Ticket Pattern**: (CAF|CET|DAF|DET)-\d+
- **Bot Authors to Skip**: `dependabot`, `renovate`, `github-actions`, any `[bot]` suffix

## Prerequisites

Before reviewing any PR, read the CLAUDE.md file at the repo root (if it exists) to understand project conventions, patterns, and architecture guidelines. Use this knowledge throughout your reviews.

## Steps

### 1. Find open PRs to review

List all open PRs (including drafts):
gh pr list --repo <ORG>/<TARGET_REPO> --state open --json number,headRefName,updatedAt,author,isDraft --limit 50

Filter out PRs authored by bots (see bot list above). Drafts ARE included — review them too.

### 2. Check CI status and filter out already-reviewed PRs

For each remaining PR:

a. **Check CI status:**
gh pr checks <PR> --repo <ORG>/<TARGET_REPO> --json name,state --jq '[.[].state] | if all(. == "SUCCESS") then "green" elif any(. == "FAILURE") then "red" else "pending" end'
- If CI is "red" (any check failed), skip this PR entirely.
- If CI is "pending", still review but note "CI pending" in your summary.

b. **Check if already reviewed:**
gh api repos/<ORG>/<TARGET_REPO>/issues/<PR>/comments --jq '[.[] | select(.body | startswith("# Internal code review"))] | sort_by(.created_at) | last | .created_at'
- If a review comment exists, get its timestamp.
- Then check latest commit date:
gh pr view <PR> --repo <ORG>/<TARGET_REPO> --json commits --jq '[.commits[].committedDate] | sort | last'
- If the latest commit is **after** the last review comment, re-review.
- If the latest commit is **before or equal**, skip.
- If no review comment exists, review is needed.

### 3. Cross-reference tickets

For each PR to review, extract the ticket ID from the PR title or branch name using the pattern (CAF|CET|DAF|DET)-\d+.

- CAF / DAF prefix → fetch via Linear MCP (title, description, acceptance criteria)
- CET / DET prefix → fetch via Atlassian-Rovo MCP (Jira)
- Use this context when reviewing: does the PR implementation match the ticket requirements?
- Note any gaps between ticket requirements and implementation

If no ticket ID is found, skip this step.

### 4. Review each PR

For each PR that needs review:

a. **Get PR metadata:**
gh pr view <PR> --repo <ORG>/<TARGET_REPO> --json headRefName,title,body,url,author,isDraft

b. **Get the full diff:**
gh pr diff <PR> --repo <ORG>/<TARGET_REPO>

c. **Get changed files list:**
gh pr diff <PR> --repo <ORG>/<TARGET_REPO> --name-only

d. **Get commit history:**
gh pr view <PR> --repo <ORG>/<TARGET_REPO> --json commits --jq '.commits[].messageHeadline'

e. **Read full file content when needed for deeper context:**
git fetch origin
git show origin/<branch>:<file>

### 5. Analyze the changes

Review each PR thoroughly, checking for:

**Universal checks:**
- Does the implementation match the PR title/description and intent?
- Does the implementation match the ticket requirements (if found)?
- Are there tests covering the changed logic?
- Code quality: unreachable code, duplicated logic, overly complex code?
- Security issues (injection, exposed secrets, unsafe operations)?
- Does the code follow existing patterns, naming conventions, and architectural style (from CLAUDE.md if present)?
- Are errors handled gracefully?

**Flutter/Dart-specific checks:**
- Follow idiomatic Flutter/Dart patterns
- Widget lifecycle: dispose controllers / subscriptions / streams
- State management consistent with repo's pattern (Bloc / Provider / Riverpod — see CLAUDE.md)
- Avoid build-time side effects; long-running work belongs in state objects
- Null safety / late init usage

### 6. Post the review

For PRs with no critical issues:
gh pr review <PR> --repo <ORG>/<TARGET_REPO> --comment --body '<body>'

For PRs with critical issues that must be resolved:
gh pr review <PR> --repo <ORG>/<TARGET_REPO> --request-changes --body '<body>'

Use this exact format for the review body:

# Internal code review

## Summary

[2-4 sentences describing what the changes accomplish and overall assessment]

## Ticket

[Ticket ID and whether implementation matches requirements. Write "No ticket found" if none.]

## CI Status

[Green / Pending / Note any issues]

## Overall Rating

[Approved / Approved with Suggestions / Needs Changes / Blocked]

## Critical Issues 🔴

[Issues that must be resolved before merging. If none, write "None."]
- File/Location: filename.ext:line_number
- Issue: [description]
- Impact: [why this matters]
- Suggestion: [specific fix]

## Improvements 🟡

[Non-blocking suggestions. If none, write "None."]
- File/Location: filename.ext:line_number
- Suggestion: [what and why]

## Minor Notes 🟢

[Style/consistency observations. If none, omit this section.]

## Positive Highlights ✅

[Things done well — good patterns, test coverage, clean logic, etc.]

### 7. Summary

After processing all PRs, output a summary:
- How many open PRs found (note how many were drafts)
- How many were bot PRs (skipped)
- How many had CI failures (skipped)
- How many were already reviewed (skipped)
- How many were newly reviewed
- Brief one-line note for each reviewed PR
```

> If the Atlassian connector was skipped in Step 3c, prepend one line to the
> Configuration block: "Jira (CET/DET) cross-reference is unavailable in
> this run — treat CET/DET PRs as 'No ticket found'." Everything else is
> unchanged.

### Step 5: Create disabled, then test-fire

```
RemoteTrigger  action=create  body=<the substituted body from Step 4>
RemoteTrigger  action=run     trigger_id=<returned trigger_id>
```
If create fails with a field/shape error (`unknown field`,
`event_type is required`, etc.) → the body was restructured; re-check the
nesting against Step 4 and retry. Do NOT enable on a failed create.

Then tell the colleague:
> Open **claude.ai/code → the `<ROUTINE_NAME>` session** and read
> the final summary block.
> - **0 reviewable PRs** (all bots / CI-red / already-reviewed) is a clean
>   no-op — it still validates the stack (model accepted, `gh` authed,
>   connectors resolve).
> - With PRs reviewed: open one and confirm the `# Internal code review`
>   comment posted and reads sensibly. A weak review is FEEDBACK for the
>   prompt, not a setup failure.

Wait for the colleague to confirm the test looks right before Step 6.

### Step 6: Enable the cron

After the colleague confirms, enable it:
```
RemoteTrigger  action=update  trigger_id=<trigger_id>  body={ "enabled": true }
```
Then print:
```
Review routine for team <TEAM_KEY> is LIVE.

<ROUTINE_NAME>
  Trigger id  : <trigger_id>
  Cron        : <CRON>
  Reviews     : <ORG>/<TARGET_REPO> open PRs (read-only clone)
  Writes      : `gh pr review` comments ONLY (no code, no push)
  Model       : claude-sonnet-4-6
  Tickets     : Linear (CAF/DAF)<+ Jira (CET/DET) if Atlassian connected>
Next run: <server-parsed next-run line from the update summary>
Results appear at: <claude.ai/code routine session URL>
```

---

## What this does NOT automate

- **Connecting your Linear / Atlassian connectors** in claude.ai (Step 2a/b)
  — interactive, per-account.
- **Creating the CCR environment + `gh` auth** (Step 2c) — interactive,
  per-account. GitHub auth (Claude GitHub App or env `GH_TOKEN`) lives in
  the environment config, never in this skill.

This skill automates the mechanical, error-prone parts: the correct create
body, the team→repo substitution, connector auto-discovery, the test fire,
and the enable flip.

---

## Troubleshooting

- **`gh: not authenticated` / PR review post fails** → the environment's
  `gh` is not authed to the repo, or lacks pull-requests: write. Fix the
  environment's GitHub auth (Step 2c), not the routine body.
- **Every PR shows "No ticket found"** → the Linear (or Atlassian) connector
  is not connected, or is the wrong account. Re-check Step 2a/b; confirm the
  connector belongs to the account that owns the tickets.
- **Routine ran as the wrong model** → `model` was not set to the fixed
  value. Re-run `RemoteTrigger action:update` with
  `job_config.ccr.session_context.model = "claude-sonnet-4-6"`.
- **Create rejected (`unknown field` / `event_type is required`)** → the
  body nesting was changed. Use the Step 4 template verbatim.
- **Reviews every PR every run (no dedup)** → the review body no longer
  starts with the exact marker `# Internal code review`, so the Step 2b
  dedup check never matches. Keep the marker line exact.
- **Nothing reviewed but PRs exist** → they were all bot-authored, CI-red,
  or already reviewed since their last commit — that is the intended filter.
  Check the summary block; it names each skip reason.

---

## Guardrails

- **Never inline tokens.** GitHub auth comes from the environment (Claude
  GitHub App or env `GH_TOKEN`); it must never appear in the routine body
  or in this skill's output. Always use the `RemoteTrigger` tool
  (in-process OAuth), never raw `curl`.
- **Read-only on the repo.** The single source carries NO
  `allow_unrestricted_git_push`; the routine never pushes, never opens or
  merges PRs, never deletes branches. Its only writes are `gh pr review`
  comments.
- **The routine is account-bound.** It runs — and posts reviews — as
  whoever ran this skill. A teammate must run `/ggx-cloud-onboard-review`
  in their own session to get their own routine; it cannot be shared.
- **Test before enable.** Create with `enabled: false`, fire one
  `action:run`, confirm the summary + a posted review, then flip
  `enabled: true`.
- **Standalone — no pair contract.** Unlike `/ggx-cloud-onboard`, this
  routine has no cron-offset invariant and no companion; it neither reads
  nor writes the analyzer's `ready-to-*` labels. Any slot is fine.
