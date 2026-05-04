---
name: generate-test-cases
description: >
  Generate manual QA test cases from the current Claude Code session and
  append them to a Jira or Linear ticket's description. Use when asked to
  "add test steps to the ticket", "update the ticket with test cases",
  "post the test plan to Jira/Linear", "document what needs to be tested",
  or any variation of capturing manual verification steps from a dev
  session into an existing ticket.
---

# Generate Test Cases — Append Manual QA Plan to Ticket

Extract manual QA test scenarios from the current session and append them to an existing Jira or Linear ticket.

---

## Step 0: Resolve project profile

1. Determine the active repo:
   - If `<repo-root>/.gogox-claude.yaml` exists, read its `platform` and `product`.
   - Else look up `basename "$(git rev-parse --show-toplevel)"` in `~/.claude/commands/profiles/repos.yaml`.
2. Hold `{platform}` for the run. It is used only to fill the "Platform" line in the test plan template (e.g. `Flutter (Android + iOS)`, `Android`, `iOS`).

## Overview

This skill:

1. **Identifies** the target ticket — via user-provided ID/URL or auto-inferred from session context (Jira and Linear both supported).
2. **Fetches** the existing ticket content via MCP.
3. **Analyzes** the session to generate structured manual test scenarios.
4. **Confirms** the draft with the user.
5. **Appends** the test plan block to the ticket description.

---

## Step 1: Identify the target ticket

### Option A — User provides ticket ID or URL

Accept any of these formats:

- Jira: `CET-123`, `https://<org>.atlassian.net/browse/CET-123`
- Linear: `CAF-123`, `https://linear.app/<team>/issue/CAF-123`
- Just the number if project/team is known from context

Detect the system based on the ID prefix or URL domain. Both use the format `[A-Z]+-\d+`; Jira lives at `*.atlassian.net`, Linear at `linear.app`.

### Option B — Infer from session

If no ticket is mentioned, look through the session for:

- Any Jira or Linear ticket IDs or URLs referenced
- Branch names like `feat/CET-123-some-feature` or `feat/CAF-123-feature`
- PR descriptions or commit messages with ticket references

If found, confirm with the user: _"I see this session relates to CET-123 (Jira). Should I update that ticket?"_

If nothing is found, **ask the user**: _"Which ticket should I update? (e.g. CET-123 for Jira or CAF-123 for Linear)"_

---

## Step 2: Fetch the existing ticket

### For Jira

```
mcp__claude_ai_Atlassian_Rovo__getJiraIssue({ cloudId: "<cloud-id>", issueIdOrKey: "<issue-key>" })
```

Then **immediately** look up remote links to find any linked Linear ticket:

```
mcp__claude_ai_Atlassian_Rovo__getJiraIssueRemoteIssueLinks({ cloudId: "<cloud-id>", issueIdOrKey: "<issue-key>" })
```

Scan the remote links for any URL matching `https://linear.app/`. If found, extract the Linear issue ID from the URL.

If a linked Linear ticket is found, inform the user:
> "I found a linked Linear ticket [LINEAR-ID] on this Jira issue. I'll update both."

If no Linear ticket is linked, proceed with Jira only.

> Use `mcp__claude_ai_Atlassian_Rovo__getAccessibleAtlassianResources` once at the start of a session to obtain `cloudId`.

### For Linear (when user provides a Linear ID directly)

```
mcp__claude_ai_Linear__get_issue({ id: "<issue-id>" })
```

This lets you:

- Understand existing context (avoid duplicating info already in description).
- Preserve the ticket's current structure.
- Reference the issue title when composing the test plan header.

---

## Step 3: Analyze the session

Read through the conversation and identify:

- **What feature/change was discussed or built?**
- **What are the key behaviors introduced or modified?**
- **What edge cases or failure scenarios were mentioned?**
- **What surface is affected?** (e.g., specific screen, API, background job)

From this, draft a list of **test scenarios**. Each scenario must be:

- A real action a human tester performs (tap, input, navigate, observe).
- Verifiable without code.
- Scoped to one behavior at a time.

---

## Step 4: Structure the test plan

Format each scenario using this template:

```
### TC-[N]: [Short scenario name]

**Preconditions:** [What must be true / set up before starting]

**Steps:**
1. [Action]
2. [Action]
3. ...

**Expected Result:** [What the tester should see/experience]

**Notes:** [Optional — edge case hints, device specifics, known risks]
```

Group related scenarios under sections if there are more than 4:

- **Happy Path** — normal successful flows
- **Edge Cases** — boundary inputs, empty states, large data
- **Error Handling** — network failure, invalid input, permission denied

---

## Step 5: Compose the update content

Build the content block to be added to the ticket. Fill the Platform line based on `{platform}`:

```markdown
## Manual Test Plan
> Generated from dev session — [date]

### Context
<1–2 sentences summarizing what was built in this session>

### Test Environment
- Platform: <derived from {platform}: e.g. "Flutter (Android + iOS)" / "Android" / "iOS">
- Build: [tester fills in]
- Account type: [if relevant]

### Test Scenarios

<all TC blocks here>

### Out of Scope
<anything explicitly NOT covered by these test cases>
```

---

## Step 6: Confirm with user

**Show the full content draft in chat** before touching any ticket. Ask:

> "Here's the test plan I'll append to [TICKET-ID]'s description. Does this look right?"

Wait for confirmation before proceeding.

---

## Step 7: Append the test plan via MCP

Fetch the current description first (from Step 2), then append the test plan block after a `---` divider.

### For Jira

```
mcp__claude_ai_Atlassian_Rovo__editJiraIssue({
  cloudId: "<cloud-id>",
  issueIdOrKey: "<issue-key>",
  fields: {
    description: {
      type: "doc",
      version: 1,
      content: [
        ...existing description content nodes...,
        { type: "rule" },
        ...new test plan content nodes...
      ]
    }
  }
})
```

> **Note for Jira**: The description field uses Atlassian Document Format (ADF). Convert the markdown test plan to ADF nodes. Preserve all existing ADF content — only append new nodes.

### For Linear

```
mcp__claude_ai_Linear__save_issue({
  id: "<issue-id>",
  description: "<existing description>\n\n---\n\n<new test plan block>"
})
```

> **Note for Linear**: Always preserve the existing description content — never overwrite it. The test plan is always appended after a `---` divider.

After updating, share the ticket URL with the user.

---

## Tips for good test scenarios

- **Atomic steps** — one action per step, not "fill in the form and submit".
- **Include the unhappy path** — what happens on network error, invalid input, user cancels.
- **Specific data** — "use an account with 0 balance", not "use a test account".
- **Platform-aware** — note OS version, device size, or permissions if relevant to `{platform}`.
- **Don't duplicate automated coverage** — focus on visual glitches, flow continuity, edge states a human would catch.
