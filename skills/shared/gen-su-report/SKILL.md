---
name: gen-su-report
description: Generate today's daily stand-up (SU) report by reading the user's Claude Code activity from the last working day, cross-referencing Linear ticket statuses, and grouping output by project. Use this when the user says "write my SU", "stand-up report", "daily SU", "generate standup", or asks for help drafting their daily check-in.
---

# Generate Stand-Up Report

> **One-line summary**: Reads Claude Code transcripts from the last working day, extracts ticket-based and non-ticket-based work, queries Linear and Jira for current ticket statuses, and produces a Slack-ready SU report.
>
> **MCP prerequisites**: Both Linear (`mcp__claude_ai_Linear__*`) and Jira via Atlassian Rovo (`mcp__claude_ai_Atlassian_Rovo__*`) — gogox uses both systems and tickets land in either one. User must have completed `/mcp` auth for both before running.
>
> **Graceful degradation**: Per-ticket fetch failures (Entity not found, timeouts, auth issues) mark that single ticket as `(status unknown)` and the skill continues. The skill still runs if one MCP is entirely unavailable, but tickets routed to that system will all show `(status unknown)`.

## Inputs

None required. Optional inputs the user may provide:

- **Date override**: e.g. "use Friday's activity" if the auto-detected last working day is wrong.
- **Extra context**: free text the user wants merged into the "Anything else" section.

## Steps

1. **Log usage** (always first step):

   ```bash
   echo "{\"skill\":\"gen-su-report\",\"user\":\"$(whoami)\",\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" >> ~/.gogox-claude-usage.jsonl 2>/dev/null || true
   ```

2. **Determine the last working day**:
   - If today is Tuesday–Friday: last working day = yesterday.
   - If today is Monday: last working day = last Friday.
   - If today is Saturday or Sunday: ask the user which day to report on.
   - Show the resolved date to the user before proceeding so they can override.

3. **Read Claude Code transcripts for that date**:
   - Path: `~/.claude/projects/*/*.jsonl`
   - Filter lines whose `timestamp` falls within the resolved date (00:00–23:59 local time).
   - Skip empty/system-only transcripts.

4. **Extract ticket IDs and non-ticket work** from the transcripts:
   - Ticket regex: `[A-Z]{2,}-\d+` (e.g. `CAF-229`, `ENG-1234`). Dedupe.
   - Non-ticket work: significant work the user did that does not reference a ticket. Look for signals like:
     - Commits, PRs created/merged (search transcript for `git commit`, `gh pr create`, etc.)
     - Bug fixes the user described in plain language ("fixed X", "patched Y")
     - Releases, deploys, hotfixes
     - Cross-team support work, code reviews, debugging sessions
   - Aim for 1–3 non-ticket bullets max. Don't list every micro-action — this is a stand-up, not an audit.

5. **Fetch ticket status — Linear first, Jira as fallback**:

   For each extracted ticket ID, try Linear first; if Linear says the entity does not exist, try Jira. Only "Entity not found" triggers the fallback — timeout / auth / network errors mark the ticket as `(status unknown)` immediately and do NOT cascade to Jira (avoids doubling latency when Linear itself is degraded).

   ```
   for each ticket_id:
     try mcp__claude_ai_Linear__get_issue(ticket_id):
       success → capture { title, state.name, state.type, team.key, team.name }
       error "Entity not found" → fall through to Jira
       any other error → mark (status unknown), continue to next ticket
     try mcp__claude_ai_Atlassian_Rovo__getJiraIssue(cloudId, ticket_id):
       success → capture { summary, status.name, status.statusCategory, project.key, project.name }
       any error → mark (status unknown)
   ```

   **cloudId caching**: Before the first Jira call, run `mcp__claude_ai_Atlassian_Rovo__getAccessibleAtlassianResources()` once and cache the **first** cloudId returned. gogox uses a single Atlassian workspace (gogotech.atlassian.net) so the first entry is correct. If gogox ever moves to multiple Atlassian clouds, this skill needs updating to iterate.

6. **Group tickets by project**:
   - If the ticket's prefix appears in the **Gogox Context** mapping table, use the mapped project name as the section header.
   - Otherwise, use the team/project name from the API response (`team.name` for Linear, `project.name` for Jira). Note in the user-facing output that the mapping is missing so the user can add it to Gogox Context if they want a friendlier name.
   - Non-ticket work goes under the catch-all bucket (default: `CA` — see Gogox Context).

7. **Determine "today's plan"** — list tickets the user is likely to work on today, querying **both** Linear and Jira:

   - Linear: `mcp__claude_ai_Linear__list_issues` with `assignee = me`, `state.type = "started"` (i.e. actively In Progress, not just triaged/todo), ordered by recently updated. Capture `updatedAt` for the merge step.
   - Jira: `mcp__claude_ai_Atlassian_Rovo__searchJiraIssuesUsingJql(cloudId, "assignee = currentUser() AND statusCategory = \"In Progress\" ORDER BY updated DESC")`. Capture `fields.updated` for the merge step.
   - Merge both result sets, sort by updated desc, take top 3–5 across the union.
   - Group by project using the same rules as step 6.

   Why `started` only and not `unstarted` too: gogox SU "today's plan" lists what you're actually working on, not your full backlog. Tickets that haven't been started yet aren't "today's plan" candidates by convention.

8. **Ask the user three subjective questions** (these cannot be auto-extracted) — ask them one at a time, not batched:

   a. **Feeling**: "How are you feeling today? Reply with an emoji or short phrase (e.g. `:blobfly:`, `tired`, `energized`)."

   b. **Blockers**: "Anything blocking your progress? (Press Enter to skip)"

   c. **Anything else**: "Anything else you'd like to share? (Press Enter to skip)"

9. **Assemble the report** in the gogox SU template format below. Render it in a single code block so the user can copy-paste into Slack.

10. **Confirm with the user** before finishing: show the rendered report, ask "Edit anything before you post? (yes/no)". If yes, ask what to change and re-render. If no, the skill is done — the user copy-pastes it themselves. **Do NOT auto-post to Slack**; posting is a user action, not the skill's job.

## Gogox Context

**Atlassian workspace**: `gogotech.atlassian.net` — single workspace assumption. cloudId is cached after first lookup.

**Ticket prefix → project name** (optional override table):

| Prefix | Project name | Source | Notes |
|--------|--------------|--------|-------|
| _(empty by default — add your team's prefixes as you encounter them)_ | | | |

This table is **documentation, not gating**. Tickets whose prefix is not listed here will still be fetched correctly by the Linear-first / Jira-fallback logic in step 5. The mapping is purely a display preference: if you want the SU report to group `CAF-*` tickets under "Moonshot" instead of the API's literal team name, add the row. If you don't care, leave the table empty and the skill uses the API team/project name verbatim.

The `Source` column documents where each prefix lives (Linear or Jira) for human readers — it does NOT route the API call. Step 5 always tries Linear first then falls back to Jira; the column is just so a new contributor reading the table can see the lay of the land.

**Non-ticket bucket**: `CA` — default header for work without a dedicated ticket (release bug fixes, ad-hoc support, code review, ops). Override per-user if your team uses a different name.

**Linear state semantics**: Linear states have both `name` (e.g. "In Progress", "In Review", "Done") and `type` (e.g. `started`, `unstarted`, `completed`). Filter by `type` for portability across teams that use different display names; show `name` in the report so the output matches what users see in Linear.

**SU template format** (matches gogox stand-up convention):

```
How are you feeling today?
{feeling emoji or phrase}

What did you do on the last working day?
{Project A}
• {TICKET-ID}: {title} {Status}
• {TICKET-ID}: {title} {Status}
{Project B}
• {non-ticket bullet}

What will you do today?
{Project A}
• {TICKET-ID}: {title} {Status}

Anything blocking your progress?
{blockers text, or omit this line if none}

Anything else you'd like to share?
{anything-else text, or omit this line if none}
```

Rules for rendering:
- Project headers are bolded by being on their own line (no markdown — Slack renders plain text).
- Ticket bullets use `•` (U+2022), not `-` or `*`.
- Status appears at the end of the line, no parentheses (e.g. `In Review`, not `(In Review)`).
- If the user skipped Blockers or Anything-else, omit the question heading entirely from the output (don't leave it blank).

## Output

A single code block containing the formatted SU report, ready to paste into Slack. The skill prints it once on first render, again after any edits, and stops there — the user copies and posts it.

## How this was used last

> Update this footer when you use the skill, so the next person knows the real-world use case.
> Format: `YYYY-MM-DD by @username — one-line context`

- 2026-04-29 by @template — placeholder, replace on first real use
