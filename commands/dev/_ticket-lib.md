---
name: _ticket-lib
description: "Internal reference for ticket-system abstraction across the /dev:* and /ggx-* pipelines. Documents the canonical resolution flow plus side-by-side Linear/Jira MCP call patterns for: get_ticket, list_comments, save_comment, transition_status, set_assignee, lane derivation. Other skills cite this file and inline the minimum branch logic rather than re-deriving the abstraction. Not user-invoked."
---

# `/_ticket-lib` — ticket-system abstraction reference

> Read-only reference. This file does not execute anything. Skills that
> interact with the ticket tracker cite this file at their first ticket-MCP
> call and follow the patterns below verbatim.

The repo's ticket tracker is either **Linear** (CAF / DAF prefixes) or
**Jira** (CET / DET prefixes). Every skill that calls a ticket MCP must
branch on the resolved `ticket_system` rather than hardcoding one tracker.

This file documents the canonical resolution flow and side-by-side call
patterns. **Each call site MUST replicate the resolution block and the
matching MCP branch** — do not assume an upstream caller already
resolved.

---

## Resolution flow (always run first)

```bash
# 1. Read project profile. .gogox-claude.yaml is source of truth.
PROFILE="$(git rev-parse --show-toplevel)/.gogox-claude.yaml"
if [ ! -f "$PROFILE" ]; then
  PROFILE="$HOME/.claude/commands/profiles/registry/$(basename "$(git rev-parse --show-toplevel)").yaml"
fi

TICKET_SYSTEM=$(grep -E '^ticket_system:' "$PROFILE" | awk '{print $2}')
BRANCH_PREFIX=$(grep -E '^branch_prefix:' "$PROFILE" | awk '{print $2}')

# 2. Resolve `auto` via org.yaml lookup of the ticket prefix.
if [ "$TICKET_SYSTEM" = "auto" ] || [ -z "$TICKET_SYSTEM" ]; then
  ORG="$HOME/.claude/commands/profiles/org.yaml"
  # Extract the ticket prefix from the ticket id passed in.
  PREFIX="${TICKET_ID%%-*}"        # e.g. CET-8362 → CET
  # Look up the prefix in the org file's jira/linear arrays.
  if grep -A1 '^jira:' "$ORG" | grep -A5 'prefixes:' "$ORG" | grep -qE "\\b$PREFIX\\b" \
       && grep -B5 "$PREFIX" "$ORG" | grep -q '^jira:'; then
    TICKET_SYSTEM=jira
  elif grep -A5 '^linear:' "$ORG" | grep -qE "\\b$PREFIX\\b"; then
    TICKET_SYSTEM=linear
  else
    TICKET_SYSTEM=unknown
  fi
fi

# 3. For Jira, capture cloud_id once.
if [ "$TICKET_SYSTEM" = "jira" ]; then
  JIRA_CLOUD_ID=$(awk '/^jira:/,/^[a-z]+:/' "$HOME/.claude/commands/profiles/org.yaml" \
    | grep -E '^[[:space:]]+cloud_id:' | awk '{print $2}')
fi
```

If `TICKET_SYSTEM == unknown`, the caller MUST stop with an explicit
error — never silently default to Linear.

---

## MCP call patterns (side-by-side)

### get_ticket — fetch one issue

| ticket_system | Tool                                              | Required args                                                                              |
|---------------|---------------------------------------------------|--------------------------------------------------------------------------------------------|
| `linear`      | `mcp__claude_ai_Linear__get_issue`                | `id: <ticket-id>`                                                                          |
| `jira`        | `mcp__claude_ai_Atlassian_Rovo__getJiraIssue`     | `cloudId: $JIRA_CLOUD_ID`, `issueIdOrKey: <ticket-id>`, `responseContentFormat: "markdown"` |

**Field mapping** (use these names in surrounding pseudocode regardless of tracker):

| Logical field   | Linear path                          | Jira path                                                  |
|-----------------|--------------------------------------|------------------------------------------------------------|
| `title`         | `.title`                             | `.fields.summary`                                          |
| `description`   | `.description`                       | `.fields.description` (markdown when `responseContentFormat=markdown`) |
| `url`           | `.url`                               | `${org.jira.base_url}/<ticket-id>` (constructed)            |
| `status_name`   | `.state.name`                        | `.fields.status.name`                                      |
| `assignee_id`   | `.assignee.id` / `.assignee.name`    | `.fields.assignee.accountId`                               |
| `labels`        | `.labels[].name` (array)             | `.fields.labels` (array of strings)                        |
| `issue_type`    | n/a (Linear uses classification labels) | `.fields.issuetype.name` (e.g. `Bug`, `Story`, `Task`)  |

### list_comments — fetch all comments newest-first

| ticket_system | Tool                                              | Required args                                                            |
|---------------|---------------------------------------------------|--------------------------------------------------------------------------|
| `linear`      | `mcp__claude_ai_Linear__list_comments`            | `issueId: <ticket-id>`, `orderBy: createdAt`                             |
| `jira`        | `mcp__claude_ai_Atlassian_Rovo__getJiraIssue`     | Same as `get_ticket`; Jira returns recent comments embedded in `.fields.comment.comments[]`. For older comments use the full issue body — the Atlassian MCP currently has no dedicated `list_comments`. |

Response body field for the comment text:

- Linear: `.comments[].body`
- Jira: `.fields.comment.comments[].body` (ADF or markdown depending on `responseContentFormat`)

### save_comment — post a new comment

| ticket_system | Tool                                                       | Required args                                                                   |
|---------------|------------------------------------------------------------|---------------------------------------------------------------------------------|
| `linear`      | `mcp__claude_ai_Linear__save_comment`                      | `issueId: <ticket-id>`, `body: <markdown>`                                      |
| `jira`        | `mcp__claude_ai_Atlassian_Rovo__addCommentToJiraIssue`     | `cloudId: $JIRA_CLOUD_ID`, `issueIdOrKey: <ticket-id>`, `commentBody: <markdown>` |

### transition_status — move to a workflow state

| ticket_system | Tool                                                       | Required args                                                                  |
|---------------|------------------------------------------------------------|--------------------------------------------------------------------------------|
| `linear`      | `mcp__claude_ai_Linear__save_issue`                        | `id: <ticket-id>`, `status: <state-name>` (e.g. `In Progress`, `In Review`)    |
| `jira`        | Two-call sequence: (1) `getTransitionsForJiraIssue` → find the transition id whose `to.name` matches target; (2) `transitionJiraIssue` to apply | (1) `cloudId`, `issueIdOrKey`. (2) `cloudId`, `issueIdOrKey`, `transition: { id: <id> }` |

Jira transitions are workflow-specific — the available transition list
varies per project. Always resolve via `getTransitionsForJiraIssue` first;
do not hardcode transition IDs.

### set_assignee

| ticket_system | Tool                                                | Required args                                              |
|---------------|-----------------------------------------------------|------------------------------------------------------------|
| `linear`      | `mcp__claude_ai_Linear__save_issue`                 | `id: <ticket-id>`, `assignee: <user-name-or-id>`           |
| `jira`        | `mcp__claude_ai_Atlassian_Rovo__editJiraIssue`      | `cloudId`, `issueIdOrKey`, `fields: { assignee: { accountId: <id> } }` |

For Jira, the current-user accountId can be discovered via
`mcp__claude_ai_Atlassian_Rovo__atlassianUserInfo` (cache it; the value
is stable per session).

### get_relations — read inter-ticket links

| ticket_system | Source field                                      | Notes                                                                          |
|---------------|---------------------------------------------------|--------------------------------------------------------------------------------|
| `linear`      | `.relations[]` on the `get_issue` response        | `type ∈ {blocks, blocked_by, related, duplicate}` + related issue id           |
| `jira`        | `.fields.issuelinks[]` on the `getJiraIssue` response | `type.name` (e.g. `Blocks`) + `inwardIssue`/`outwardIssue` key; normalize the inward/outward phrasing ("is blocked by" / "blocks") onto the Linear kind names |

Normalize both into `{from, to, kind: blocks|blocked-by|related}` records.
Only `blocks`/`blocked-by` kinds carry ordering semantics; treat
`related`/`duplicate` as informational. Currently read by
`/ticket-analyze` only — relations are never *written* by automation
(humans create them in the tracker UI).

### labels (write)

- **Linear**: `save_issue --labels <json-array>` (rewrites the full label set).
- **Jira**: `editJiraIssue --fields { labels: [...] }` — but the workflow
  labels Linear uses (`ready-to-port`, `ready-to-dev`, `need-spec-review`,
  `dispatcher-*-in-flight`) **do NOT exist on Jira tickets**. The Jira
  branch of every caller MUST skip these writes entirely — they are
  Linear-specific workflow signals, not portable across trackers.

---

## Lane derivation (used by `/route`)

| ticket_system | Source                          | Mapping                                                                                  |
|---------------|---------------------------------|------------------------------------------------------------------------------------------|
| `linear`      | classification label `design bug` (**precedence — evaluated first**) | present (whole-string, case-insensitive) → `ui-tweak`, regardless of which canonical labels co-occur. Only if absent, fall through to the canonical row below. |
| `linear`      | classification label ∈ `{bug,port,feature}` | exactly one match → that lane; zero or multiple → `unknown`                  |
| `jira`        | `fields.issuetype.name`         | `Bug` → `bug`; `Story` / `Task` / `Sub-task` / `Improvement` / `New Feature` → `feature`. Anything else → `unknown`. **No `port` lane** — the port pipeline is Linear-specific (copy-from-source CAF / DAF tickets) and not used in Jira projects. **No `ui-tweak` lane** — Jira has no `design bug` classification; design-bug routing is Linear-only, like `port`. |

**`design bug` precedence rule (canonical statement — route.md, ggx-work.md,
and ticket-analyze.md cite this file and replicate it verbatim):** if the
Linear label set contains `design bug` (whole-string match after lowercasing
each label name — `Design bug`, `design bug`, `DESIGN BUG` all match; never a
substring match), the lane is `ui-tweak`, full stop — evaluated **before and
overriding** the canonical `{bug,port,feature}` set-count. All combinations
resolve the same way: `design bug`+`bug`, `design bug`+`feature`,
`design bug`+`port`, and `design bug` alone (zero canonical labels) → `ui-tweak`.
The `design bug` label is **human-owned**: automation reads it but never
writes it, and it must exist in the Linear workspace before routing works
(setup precondition, same as `bug`/`port`/`feature`).

`UNKNOWN_LANE` semantics are the same in both trackers — the caller
surfaces a structured error and exits non-zero.

---

## Workflow-label parity table

Linear's `/ggx-*` workflow uses these labels to coordinate state across
the dispatcher / port-ship handoff. Their Jira equivalents:

| Linear label                       | Jira equivalent                                                                                   |
|------------------------------------|---------------------------------------------------------------------------------------------------|
| `ready-to-port`, `ready-to-dev`    | none — Jira repos do not use the dispatcher's actionable-label sweep. Skip the drop in `/_ticket-init`. `/ticket-analyze` records its pass verdict via the `ticket-analysis-ready` string label in `fields.labels` + comment instead. |
| `need-spec-review`                 | none — spec-review gate is a port-pipeline concept; Jira has no port lane.                        |
| `dispatcher-*-in-flight`           | none — dispatcher is Linear-only (`/ggx-dispatcher` Step 1 validates `ticket_system == linear`).  |
| `need-revision`, `need-dependency` | none — analyzer verdicts degrade to `fields.labels` string labels (`ticket-analysis-need-revision` / `ticket-analysis-need-dependency`) + the `ticket-analysis:v1` comment as primary record. |
| classification labels (`bug` / `port` / `feature` / `design bug`) | replaced by `fields.issuetype.name` — read-only, never written. Jira has no `design bug` equivalent: no ui-tweak lane. |
| `estimate` field                   | Jira has story-point fields but project-specific. Skip the auto-set; rely on human estimation.    |

---

## Failure handling contract

Every MCP call wrapped by this abstraction:

- On network / auth failure: STOP the caller with the verbatim MCP error
  and a hint to verify the relevant MCP server is authenticated.
- On `unknown` ticket_system: STOP — never default to Linear silently.
- On Jira-only no-op fields (workflow labels, estimate): log one stdout
  line (`<field>: skipped (jira)`), continue. The caller's idempotency
  contract is preserved — the next caller in the chain sees the same
  no-op decision.

---

## Callers (current)

- `/route` — lane derivation (Step 3)
- `/_ticket-init` — lifecycle init writes (status / assignee / comment; labels + estimate skipped on Jira)
- `/ggx-work` — pre-flight (Step 2), error comments (Step 4.3), port short-circuit (Step 4.4a — Linear-only, skipped on Jira)
- `/dev:start` — ownership check, ticket fetch, spec-review comment capture (Jira: always Status: NONE)
- `/dev:apply` Step 0-bug — ticket re-fetch
- `/dev:ship` — status transition + summary comment
- `/ticket-analyze` — batch sweep (To-Do + assignee=me), relations read, verdict comment + label writes (Jira: string labels in `fields.labels`)

Adding a new caller? Cite this file and replicate the resolution block.
Do NOT re-derive the abstraction.
