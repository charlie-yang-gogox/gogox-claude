---
name: _ticket-init
description: "Internal helper invoked by /port:start, /dev:start, /ggx-dispatcher, and /ggx-work. Performs the idempotent ticket-lifecycle init (status → In Progress, assignee → self, starting comment, plus Linear-only label / estimate writes when applicable). Supports both Linear and Jira via the abstraction documented in /_ticket-lib. Every write reads current state first and skips when already at target — second call on the same ticket is a no-op. Not user-invoked."
---

# `/_ticket-init <ticket-id> <lane>`

Single source of truth for the ticket-lifecycle init that fires when a
pipeline (port or dev) claims a ticket. Replaces the four duplicated inline
blocks previously kept in sync via `<!-- SYNC: -->` anchors in `/port:start`
Step 5a, `/dev:start` Step 3b, `/ggx-dispatcher` Step 4.1, and `/ggx-work`
Step 2.5.

**Ticket-system support**: Linear and Jira. The skill resolves
`ticket_system` from the project profile (see `_ticket-lib.md` "Resolution
flow") and branches each write step on the result. Jira-only differences:
no `ready-to-<lane>` label to drop, no estimate field write, status moves
require the two-call `getTransitionsForJiraIssue` → `transitionJiraIssue`
sequence. The starting-comment marker and idempotency contract are
identical across trackers.

**Underscore prefix** marks this skill as internal — callers are other skills
(see below), never the user directly.

## Inputs

- `<ticket-id>` — Linear ticket id (e.g. `CAF-370`). Required.
- `<lane>` — exactly one of `port` or `dev`. Required. Determines which
  `ready-to-*` label to drop and which lane name appears in the starting
  comment body.

## Idempotency contract

Every write below is gated on a read of the current ticket state. **Second
invocation on the same ticket MUST be a no-op** so the dispatcher →
`/ggx-work` → `/dev:start` (or `/port:start`) chain collapses to exactly one
effective init. The chain in practice:

- `/ggx-dispatcher` §4.1 invokes `/_ticket-init` while locking the batch.
- The spawned `/ggx-work` subagent invokes `/_ticket-init` in its Step 2.5.
- `/ggx-work` then runs `/route` → `/port:ff` or `/dev:ff`, whose `:start`
  stage invokes `/_ticket-init` again.

Each later call MUST short-circuit on the per-write skip conditions; never
overwrite a value a human deliberately changed (notably `estimate`).

## Steps

### Step 1: Resolve ticket_system + read current ticket state

```bash
TICKET_ID="$1"
LANE="$2"   # `port` or `dev`

# Argument validation.
case "$LANE" in
  port|dev) ;;
  *)
    echo "FAIL: /_ticket-init requires <lane> ∈ {port, dev}; got '$LANE'" >&2
    exit 1 ;;
esac

if [ -z "$TICKET_ID" ]; then
  echo "FAIL: /_ticket-init requires <ticket-id>" >&2
  exit 1
fi
```

**Resolve `TICKET_SYSTEM` and (if Jira) `JIRA_CLOUD_ID`** by running the
"Resolution flow" block from `_ticket-lib.md`. If `TICKET_SYSTEM == unknown`
→ STOP with `FAIL: /_ticket-init cannot resolve ticket_system for <ticket-id>`
(do not silently default to Linear). For `port` lane, Jira is rejected
because Jira repos do not use the port pipeline:

```bash
if [ "$TICKET_SYSTEM" = "jira" ] && [ "$LANE" = "port" ]; then
  echo "FAIL: /_ticket-init lane=port is not supported on Jira tickets (no port pipeline)" >&2
  exit 1
fi
```

**Single read of the ticket** — every skip condition below evaluates against
this snapshot:

- **Linear**:
  ```
  ISSUE=$(mcp__claude_ai_Linear__get_issue --id "$TICKET_ID")
  CURRENT_STATUS=$(jq -r '.state.name // ""'    <<<"$ISSUE")
  CURRENT_ASSIGNEE=$(jq -r '.assignee.id // ""' <<<"$ISSUE")
  CURRENT_ESTIMATE=$(jq -r '.estimate'          <<<"$ISSUE")              # numeric or the literal "null"
  CURRENT_LABELS_JSON=$(jq -c '[.labels[].name]' <<<"$ISSUE")
  HAS_READY_LABEL=$(jq --arg t "ready-to-$LANE" 'index($t) != null' <<<"$CURRENT_LABELS_JSON")
  ```
- **Jira**:
  ```
  ISSUE=$(mcp__claude_ai_Atlassian_Rovo__getJiraIssue \
            --cloudId "$JIRA_CLOUD_ID" --issueIdOrKey "$TICKET_ID" \
            --responseContentFormat markdown)
  CURRENT_STATUS=$(jq -r '.fields.status.name   // ""' <<<"$ISSUE")
  CURRENT_ASSIGNEE=$(jq -r '.fields.assignee.accountId // ""' <<<"$ISSUE")
  CURRENT_ESTIMATE="not-applicable"                                       # Jira has no canonical estimate field; treat as already-set
  CURRENT_LABELS_JSON="[]"                                                # Jira workflow labels are not used here (see _ticket-lib parity table)
  HAS_READY_LABEL=false
  # For assignee writes: discover own accountId once.
  CURRENT_USER_ACCOUNT_ID=$(mcp__claude_ai_Atlassian_Rovo__atlassianUserInfo | jq -r '.account_id // ""')
  ```

If `get_issue` / `getJiraIssue` fails (network, permission, missing ticket)
→ STOP with the verbatim MCP error. This is the ONE hard-stop in this skill:
every other step degrades to a soft WARN. Without a readable issue we cannot
evaluate skip conditions, so blind writes would risk clobbering human-set
values.

The labels variable above holds the FULL current label set (Linear only) so
Step 3 can rewrite-while-preserving without clobbering anything we didn't
intend to drop (notably `dispatcher-*-in-flight`).

### Step 2: Status → `In Progress`

**Skip condition**: `CURRENT_STATUS == "In Progress"` (case-insensitive — Jira
sometimes returns `In progress` with lower-case p).

Otherwise:

- **Linear**:
  ```
  mcp__claude_ai_Linear__save_issue --id "$TICKET_ID" --status "In Progress" \
    || echo "WARN: /_ticket-init: status update failed for $TICKET_ID — continuing." >&2
  ```
- **Jira** — two-call transition sequence:
  ```
  TRANSITIONS=$(mcp__claude_ai_Atlassian_Rovo__getTransitionsForJiraIssue \
                  --cloudId "$JIRA_CLOUD_ID" --issueIdOrKey "$TICKET_ID")
  TARGET_ID=$(printf '%s' "$TRANSITIONS" \
    | jq -r '.transitions[] | select(.to.name | ascii_downcase == "in progress") | .id' \
    | head -1)
  if [ -n "$TARGET_ID" ]; then
    mcp__claude_ai_Atlassian_Rovo__transitionJiraIssue \
      --cloudId "$JIRA_CLOUD_ID" --issueIdOrKey "$TICKET_ID" \
      --transition "{\"id\":\"$TARGET_ID\"}" \
      || echo "WARN: /_ticket-init: Jira transition failed for $TICKET_ID — continuing." >&2
  else
    echo "WARN: /_ticket-init: no 'In Progress' transition available for $TICKET_ID (project workflow may require manual move) — continuing." >&2
  fi
  ```

### Step 3: Drop `ready-to-<lane>` label (Linear only)

**Jira**: skip entirely (`ready-to-*` workflow labels do not exist on Jira
tickets — see `_ticket-lib.md` "Workflow-label parity table"). Log one line
`ticket-init: label-drop skipped (jira)` and continue.

**Linear** — **Skip condition**: `HAS_READY_LABEL == "false"` (i.e.
`ready-to-<lane>` ∉ `CURRENT_LABELS_JSON`).

Otherwise derive `NEW_LABELS` from the snapshot's full label array, **structurally
preserving every other label** (including `dispatcher-*-in-flight` when the
dispatcher set it before invoking this skill) — no reliance on prose-described
shell arrays:

```bash
if [ "$HAS_READY_LABEL" = "true" ]; then
  NEW_LABELS=$(printf '%s' "$CURRENT_LABELS_JSON" \
    | jq -c --arg t "ready-to-$LANE" '[ .[] | select(. != $t) ]')
  mcp__claude_ai_Linear__save_issue --id "$TICKET_ID" --labels "$NEW_LABELS" \
    || echo "WARN: /_ticket-init: label drop (ready-to-$LANE) failed for $TICKET_ID — continuing." >&2
fi
```

**Never** add a `dispatcher-*-in-flight` label here. Those are exclusively
`/ggx-dispatcher` §4.1's resume signal — adding them from a `*:start` path
would silently flip manual runs into dispatcher-recoverable state, breaking
the user's "if I stopped, it stays stopped" expectation (see
`ggx-dispatcher.md` Guardrails for the canonical statement).

### Step 4: Assignee → self

- **Linear** — **Skip condition**: `CURRENT_ASSIGNEE == "$USER_NAME"` (case-sensitive id match).
  ```
  if [ "$CURRENT_ASSIGNEE" != "$USER_NAME" ]; then
    mcp__claude_ai_Linear__save_issue --id "$TICKET_ID" --assignee "$USER_NAME" \
      || echo "WARN: /_ticket-init: assignee update failed for $TICKET_ID — continuing." >&2
  fi
  ```
- **Jira** — **Skip condition**: `CURRENT_ASSIGNEE == "$CURRENT_USER_ACCOUNT_ID"`.
  ```
  if [ "$CURRENT_ASSIGNEE" != "$CURRENT_USER_ACCOUNT_ID" ] && [ -n "$CURRENT_USER_ACCOUNT_ID" ]; then
    mcp__claude_ai_Atlassian_Rovo__editJiraIssue \
      --cloudId "$JIRA_CLOUD_ID" --issueIdOrKey "$TICKET_ID" \
      --fields "{\"assignee\":{\"accountId\":\"$CURRENT_USER_ACCOUNT_ID\"}}" \
      || echo "WARN: /_ticket-init: Jira assignee update failed for $TICKET_ID — continuing." >&2
  fi
  ```

### Step 5: Estimate → 1 (Linear only)

**Jira**: skip entirely (no canonical estimate field across all Jira
projects — log `ticket-init: estimate skipped (jira)` and continue).

**Linear** — **Skip condition**: `CURRENT_ESTIMATE != "null"` (any numeric value, including
0, counts as set). Humans set estimates deliberately — never overwrite.

```bash
if [ "$CURRENT_ESTIMATE" = "null" ]; then
  mcp__claude_ai_Linear__save_issue --id "$TICKET_ID" --estimate 1 \
    || echo "WARN: /_ticket-init: estimate update failed for $TICKET_ID — continuing." >&2
fi
```

### Step 6: Starting comment

**Skip condition**: any existing comment body on the ticket starts with the
marker line

```
<!-- ticket-init:v1 lane=<lane> -->
```

(literal first line, no leading whitespace, exact `<lane>` value substituted).
Find via:

- **Linear**:
  ```
  COMMENTS=$(mcp__claude_ai_Linear__list_comments --issueId "$TICKET_ID" 2>/dev/null || echo '{"comments":[]}')
  HAS_MARKER=$(printf '%s' "$COMMENTS" | jq -r --arg m "<!-- ticket-init:v1 lane=$LANE -->" \
    '[.comments[]? | select(.body | startswith($m))] | length')
  ```
- **Jira** — comments are embedded in `getJiraIssue` (use the snapshot from Step 1):
  ```
  HAS_MARKER=$(printf '%s' "$ISSUE" | jq -r --arg m "<!-- ticket-init:v1 lane=$LANE -->" \
    '[.fields.comment.comments[]? | select(.body | startswith($m))] | length')
  ```

If `$HAS_MARKER >= 1` → skip. Otherwise post:

- **Linear**:
  ```
  mcp__claude_ai_Linear__save_comment --issueId "$TICKET_ID" --body "$(cat <<EOF
  <!-- ticket-init:v1 lane=$LANE -->
  Starting $LANE for this ticket.
  EOF
  )" || echo "WARN: /_ticket-init: starting comment failed for $TICKET_ID — continuing." >&2
  ```
- **Jira**:
  ```
  mcp__claude_ai_Atlassian_Rovo__addCommentToJiraIssue \
    --cloudId "$JIRA_CLOUD_ID" --issueIdOrKey "$TICKET_ID" \
    --commentBody "$(cat <<EOF
  <!-- ticket-init:v1 lane=$LANE -->
  Starting $LANE for this ticket.
  EOF
  )" || echo "WARN: /_ticket-init: Jira starting comment failed for $TICKET_ID — continuing." >&2
  ```

The marker line is the literal first line of the comment body. Idempotency
key is `(ticket-id, lane)` — a ticket that passes through port and then
later through dev will get one comment per lane (the lane in the marker
differs), which is the intended audit trail.

`list_comments` failure (e.g. network) is treated as "no marker found" and
the comment will be posted; a subsequent successful run will then short-
circuit. This trades one possible duplicate comment on a flaky network for
not blocking the pipeline on read failures.

### Step 7: Audit line

Emit a single stdout summary so callers (and humans tailing logs) can see
what happened:

```
ticket-init: <ticket-id> system=<linear|jira> lane=<lane> status=<written|skipped> label=<written|skipped|n/a> assignee=<written|skipped> estimate=<written|skipped|n/a> comment=<written|skipped>
```

Use `n/a` for the label/estimate columns on Jira (these are Linear-only writes).

## Failure handling summary

| Failure | Behavior |
|---|---|
| `get_issue` / `getJiraIssue` (Step 1) | STOP — cannot evaluate skip conditions. |
| status / assignee / label / estimate write | WARN one line, continue. |
| Jira `getTransitionsForJiraIssue` returns no matching transition | WARN one line, continue. Manual status move expected. |
| comment fetch (Step 6) | Treat as "no marker", post comment anyway. |
| comment post (Step 6) | WARN one line, continue. |

Partial init is acceptable; pipeline must not block on flaky MCP calls. The
next caller in the chain (`/dev:start`, `/port:start`) re-invokes `/_ticket-init`
and retries any unfinished writes naturally via the skip conditions.

## Callers (4 sites)

- `/port:start` Step 5a — `commands/dev/port/start.md`
- `/dev:start` Step 3b — `commands/dev/dev/start.md`
- `/ggx-dispatcher` Step 4.1 — `commands/dev/ggx-dispatcher.md`
- `/ggx-work` Step 2.5 — `commands/dev/ggx-work.md`

All four invoke `/_ticket-init <ticket-id> <lane>`. Drift between any caller
and this file breaks dispatcher idempotency and the HITL orchestrator
lifecycle — do NOT re-inline the block in a caller; extend this skill
instead.

## Guardrails

- This skill writes ticket-tracker state (Linear or Jira). It does NOT touch
  the filesystem, git, or worktrees.
- Linear MCP tool calls use `mcp__claude_ai_Linear__*`. Never the legacy
  `mcp__linear-server__*`. Jira MCP tool calls use
  `mcp__claude_ai_Atlassian_Rovo__*`.
- Always run the `_ticket-lib.md` resolution flow first. Never silently
  default to Linear when `ticket_system` is unset or unknown.
- The marker shape `<!-- ticket-init:v1 lane=<lane> -->` is the canonical
  idempotency anchor for the starting comment. If you need to change the
  semantics in a backward-incompatible way, bump to `v2` so existing
  tickets with v1 markers do not silently double-comment.
- Never add `dispatcher-*-in-flight` labels here (see Step 3 rationale).
- Never overwrite a non-null estimate (see Step 5 rationale).
- Reject `lane=port` on Jira tickets (see Step 1). The port pipeline is
  Linear-specific; allowing it on Jira would silently bypass the
  `/ggx-dispatcher`'s Linear-only validation.
