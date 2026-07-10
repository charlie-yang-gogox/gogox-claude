---
name: gen-su-report
description: "DEPRECATED — superseded by /ggx-standup, which now emits the same HTML rich-text output. Kept temporarily; scheduled for removal once /ggx-standup's HTML mode has been dogfooded. Prefer /ggx-standup. (Legacy: generate today's daily stand-up report by reading Claude Code activity from the last working day, cross-referencing Linear/Jira ticket statuses, grouped by project.)"
---

# Generate Stand-Up Report

> **⚠️ DEPRECATED — use `/ggx-standup` instead.** `/ggx-standup` produces the
> same browser-opened HTML with clickable ticket/PR links (rich-text Slack
> paste), built from GitHub PR events + Linear, on a tested deterministic core.
> This skill is retained only until `/ggx-standup`'s HTML mode has been
> dogfooded, then it will be removed. The only capabilities not carried over
> (accepted): Jira/Atlassian fallback, the 3 subjective questions
> (feeling / blockers / anything-else), transcript-based non-ticket-work
> extraction, and project-name grouping. If you rely on any of those, say so
> before this skill is deleted.

> **One-line summary**: Reads Claude Code transcripts from the last working day, extracts ticket-based and non-ticket-based work, queries Linear and Jira for current ticket statuses, and produces a Slack-ready SU report as an HTML file opened in the browser — copying from the rendered page preserves rich-text formatting (bold headers, italic project subtitles, inline-code status badges, clickable ticket anchors) when pasted into Slack.
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
   - Ticket regex: `[A-Z]{2,}-\d+` (e.g. `<ticket-id>`). Dedupe.
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
       success → capture { title, state.name, state.type, team.key, team.name, url }
       error "Entity not found" → fall through to Jira
       any other error → mark (status unknown), continue to next ticket
     try mcp__claude_ai_Atlassian_Rovo__getJiraIssue(cloudId, ticket_id):
       success → capture { summary, status.name, status.statusCategory, project.key, project.name }
                 build url = `https://gogotech.atlassian.net/browse/{ticket_id}`
       any error → mark (status unknown)
   ```

   **URL capture is required, not optional** — every successfully fetched ticket must carry its `url` through to the rendering step so the ticket ID can be wrapped in an HTML `<a href>` anchor. Linear's `get_issue` response exposes `url` directly; Jira's URL is built deterministically from the ticket key against the gogotech workspace.

   **cloudId caching**: Before the first Jira call, run `mcp__claude_ai_Atlassian_Rovo__getAccessibleAtlassianResources()` once and cache the **first** cloudId returned. gogox uses a single Atlassian workspace (gogotech.atlassian.net) so the first entry is correct. If gogox ever moves to multiple Atlassian clouds, this skill needs updating to iterate.

6. **Group tickets by project**:
   - If the ticket's prefix appears in the **Gogox Context** mapping table, use the mapped project name as the section header.
   - Otherwise, use the team/project name from the API response (`team.name` for Linear, `project.name` for Jira). Note in the user-facing output that the mapping is missing so the user can add it to Gogox Context if they want a friendlier name.
   - Non-ticket work goes under the catch-all bucket (default: `CA` — see Gogox Context).

7. **Determine "today's plan"** — list tickets the user is likely to work on today, querying **both** Linear and Jira:

   - Linear: `mcp__claude_ai_Linear__list_issues` with `assignee = me`, `state.type = "started"` (i.e. actively In Progress, not just triaged/todo), ordered by recently updated. Capture `updatedAt` and `url` for each issue.
   - Jira: `mcp__claude_ai_Atlassian_Rovo__searchJiraIssuesUsingJql(cloudId, "assignee = currentUser() AND statusCategory = \"In Progress\" ORDER BY updated DESC")`. Capture `fields.updated` and build `url = https://gogotech.atlassian.net/browse/{key}` for each issue.
   - Merge both result sets, sort by updated desc, take top 3–5 across the union.
   - Group by project using the same rules as step 6. Carry the `url` through so the rendering step can wrap each ticket ID in an HTML `<a href>` anchor.

   Why `started` only and not `unstarted` too: gogox SU "today's plan" lists what you're actually working on, not your full backlog. Tickets that haven't been started yet aren't "today's plan" candidates by convention.

8. **Ask the user three subjective questions** (these cannot be auto-extracted) — ask them one at a time, not batched:

   a. **Feeling**: "How are you feeling today? Reply with an emoji or short phrase (e.g. `:blobfly:`, `tired`, `energized`)."

   b. **Blockers**: "Anything blocking your progress? (Press Enter to skip)"

   c. **Anything else**: "Anything else you'd like to share? (Press Enter to skip)"

9. **Assemble the report as an HTML file** at `/tmp/su-report.html` using the template in **SU template format** below. Then `open /tmp/su-report.html` (macOS) so the user sees it rendered in their default browser. Pasting from the rendered page into Slack preserves rich text — bold question headers, italic project subtitles, inline-code status badges, and clickable ticket anchors all survive the paste because the clipboard carries HTML, not plain text. Plain-text / mrkdwn variants (`<URL|label>`, `[label](URL)`) all degrade to literal text when pasted into Slack and are therefore not used.

10. **Confirm with the user** before finishing: tell them the file is open in the browser and ask "Edit anything before you post? (yes/no)". If yes, ask what to change, rewrite `/tmp/su-report.html`, and re-open it. If no, the skill is done — the user does Cmd+A → Cmd+C in the browser tab and Cmd+V into Slack. **Do NOT auto-post to Slack**; posting is a user action, not the skill's job.

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

**SU template format** (HTML — written to `/tmp/su-report.html` and opened in the browser):

```html
<!doctype html>
<html><head><meta charset="utf-8"><title>SU {YYYY-MM-DD}</title>
<style>body{font:14px -apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif;line-height:1.5;padding:24px;max-width:820px;color:#1d1c1d}p{margin:0}code{background:#f4f4f4;border:1px solid #e0e0e0;border-radius:3px;padding:1px 4px;font:12px ui-monospace,Menlo,monospace;color:#c0341d}</style>
</head><body>
<p><strong>How are you feeling today?</strong></p>
<p>{feeling emoji or phrase}</p>
<p>&nbsp;</p>
<p><strong>What did you do on the last working day?</strong></p>
<p><em>{Project A}</em></p>
<p>•&nbsp;<a href="{ticket URL}">{TICKET-ID}</a>: {title} <code>{Status}</code></p>
<p><em>{Project B}</em></p>
<p>•&nbsp;<a href="{PR or commit URL}">{label}</a>: {non-ticket bullet}</p>
<p>&nbsp;</p>
<p><strong>What will you do today?</strong></p>
<p><em>{Project A}</em></p>
<p>•&nbsp;<a href="{ticket URL}">{TICKET-ID}</a>: {title} <code>{Status}</code></p>
<p><strong>Anything blocking your progress?</strong></p>
<p>{blockers text — omit this and the heading above if none}</p>
<p><strong>Anything else you'd like to share?</strong></p>
<p>{anything-else text — omit this and the heading above if none}</p>
</body></html>
```

Concrete rendered example (so the format is unambiguous):

```html
<p>•&nbsp;<a href="https://linear.app/gogox/issue/<ticket-id>/support-fixed-fee-moving-additional-requirement"><ticket-id></a>: Support fixed fee moving additional requirement <code>Ready for QA</code></p>
<p>•&nbsp;<a href="https://gogotech.atlassian.net/browse/<ticket-id>"><ticket-id></a>: Fix push token refresh <code>In Progress</code></p>
```

Rules for rendering:
- **Visual hierarchy** (all three styles survive Slack paste): question headers use `<strong>`, project subtitles use `<em>`, status badges use `<code>`. Ticket IDs are wrapped in `<a href="…">` for clickable anchor text.
- Bullet character is `•` (U+2022) followed by `&nbsp;` for spacing — not `-` or `*`.
- **Every ticket bullet must wrap the ticket ID in an `<a href>` anchor** using the `url` captured in step 5 (or step 7 for today's plan). The clickable anchor text is preserved by Slack on paste because the clipboard carries HTML; mrkdwn forms (`<URL|label>`, `[label](URL)`) are NOT used because they only render via API send, not paste.
- For non-ticket items with a natural URL (PRs, commits, dashboards), wrap in the same `<a href>`. If a non-ticket bullet has no URL, leave the text plain inside the `<p>•&nbsp;…</p>`.
- For tickets that fell back to `(status unknown)` because both Linear and Jira fetches failed, render as `<p>•&nbsp;{TICKET-ID}: <code>(status unknown)</code></p>` without an anchor — the skill doesn't know the tracker, so a deterministic URL would mislead.
- Status text inside `<code>` has no parentheses (e.g. `In Review`, not `(In Review)`).
- If the user skipped Blockers or Anything-else, omit BOTH the `<strong>` heading line AND the answer paragraph — don't leave a blank `<p></p>`.
- Empty-line spacing between sections uses `<p>&nbsp;</p>` (a literal `<p></p>` collapses in some browsers' copy).

## Output

An HTML file written to `/tmp/su-report.html` and opened in the user's default browser. The user does Cmd+A → Cmd+C in the rendered page and Cmd+V into Slack — the clipboard carries HTML, so bold question headers, italic project subtitles, inline-code status badges, and clickable ticket anchors all survive the paste. The skill rewrites and re-opens the file once on first render, again after any edits, and stops there — the user does the actual posting.

## How this was used last

> Update this footer when you use the skill, so the next person knows the real-world use case.
> Format: `YYYY-MM-DD by @username — one-line context`

- 2026-04-29 by @template — placeholder, replace on first real use
- 2026-04-30 by @charlie — switched output format from plain-text mrkdwn code block to HTML file opened in browser; rich-text paste into Slack now preserves bold question headers, italic project subtitles, inline-code status badges, and clickable ticket anchors
