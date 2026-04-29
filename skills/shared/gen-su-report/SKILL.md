---
name: gen-su-report
description: Generate today's daily stand-up (SU) report by reading the user's Claude Code activity from the last working day, cross-referencing Linear ticket statuses, and grouping output by project. Use this when the user says "write my SU", "stand-up report", "daily SU", "generate standup", or asks for help drafting their daily check-in.
---

# Generate Stand-Up Report

> **One-line summary**: Reads Claude Code transcripts from the last working day, extracts ticket-based and non-ticket-based work, queries Linear for current ticket statuses, and produces a Slack-ready SU report.
>
> **MCP prerequisites**: Linear (`mcp__claude_ai_Linear__*`). User must have completed `/mcp` auth for Linear before running. If Linear is unavailable, the skill still produces a report from transcripts alone but ticket statuses will be marked as `(status unknown)`.

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

5. **Fetch ticket status from Linear** for each extracted ticket:
   - Use `mcp__claude_ai_Linear__get_issue` with the ticket identifier.
   - Capture: `title`, `state.name` (e.g. "In Progress", "In Review", "Done"), `team.key` (the prefix).
   - If a ticket can't be fetched (deleted, wrong workspace, etc.), mark its status as `(status unknown)` and continue.

6. **Group tickets by project** using the prefix → project mapping in **Gogox Context** below:
   - If the prefix is mapped, use the mapped project name as the section header.
   - If the prefix is NOT mapped, use the team name from Linear (`team.name`) as the header. Note in the user-facing output which mapping is missing so the user can update Gogox Context for next time.
   - Non-ticket work goes under the catch-all project bucket (default: `CA` — see Gogox Context).

7. **Determine "today's plan"** — list tickets the user is likely to work on today:
   - Use `mcp__claude_ai_Linear__list_issues` with filters: `assignee = me`, `state.type IN ("started", "unstarted")`, ordered by recently updated.
   - Limit to the top 3–5 most recently touched.
   - Group by project using the same mapping.

8. **Ask the user three subjective questions** (these cannot be auto-extracted) — ask them one at a time, not batched:

   a. **Feeling**: "How are you feeling today? Reply with an emoji or short phrase (e.g. `:blobfly:`, `tired`, `energized`)."

   b. **Blockers**: "Anything blocking your progress? (Press Enter to skip)"

   c. **Anything else**: "Anything else you'd like to share? (Press Enter to skip)"

9. **Assemble the report** in the gogox SU template format below. Render it in a single code block so the user can copy-paste into Slack.

10. **Confirm with the user** before finishing: show the rendered report, ask "Edit anything before you post? (yes/no)". If yes, ask what to change and re-render. If no, the skill is done — the user copy-pastes it themselves. **Do NOT auto-post to Slack**; posting is a user action, not the skill's job.

## Gogox Context

Update this section as new Linear teams are added or project names change. PRs welcome.

**Linear team prefix → project name** (used as section headers in the SU report):

| Prefix | Project name | Notes |
|--------|--------------|-------|
| `CAF` | Moonshot | Core fixed-fee work stream |
| _(add others as you encounter them)_ | | |

**Non-ticket bucket**: `CA` — work without a dedicated ticket (release bug fixes, ad-hoc support, code review, ops). Always use `CA` as the section header for non-ticket bullets unless the user overrides.

**Linear "in progress" state types**: `started` (active) and `unstarted` (planned/triaged). Both count as "today's plan" candidates.

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
