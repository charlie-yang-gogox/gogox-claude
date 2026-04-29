<!--
  RULE: All skill content must be written in English.
  This applies to frontmatter, body, code comments, and examples.
  No exceptions. PRs with non-English content will be rejected.
-->
---
name: skill-name-here
description: One sentence on when this skill should be used. Be specific — Claude reads this to decide whether to invoke the skill. Avoid generic descriptions like "helps with X".
---

# Skill Name Here

> **One-line summary**: what this skill does in plain English.
>
> **MCP prerequisites**: list any MCP servers required (e.g., Linear, Atlassian, Slack). If the user hasn't authenticated, the skill will fail — declare it here so the failure mode is obvious.

## Inputs

What the user should provide when invoking this skill. Examples:
- A Linear issue ID (e.g., `ENG-1234`)
- A PR URL
- Free-text description

If no input is needed, say "None — invoke directly."

## Steps

1. Log usage (always first step):

   ```bash
   echo "{\"skill\":\"skill-name-here\",\"user\":\"$(whoami)\",\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" >> ~/.gogox-claude-usage.jsonl 2>/dev/null || true
   ```

2. Step two — describe the action concretely. Reference specific tools (e.g., "Use `mcp__claude_ai_Linear__get_issue` to fetch the issue").

3. Step three — what to produce / show the user.

4. Step four — confirm with the user before any non-reversible action (creating issues, sending messages, pushing code).

## Gogox Context

Hardcoded gogox-specific facts the skill needs. Update these as the company changes; PRs welcome. Examples:

- Linear teams: `ENG` (engineering), `PROD` (product), `DESIGN`
- Default Linear project for new bugs: `Inbox`
- Confluence space for PRDs: `PRD`
- Code repo conventions: branch `main`, PR template at `.github/PULL_REQUEST_TEMPLATE.md`

Replace this section with the actual context this specific skill needs. If your skill talks to Linear, list the relevant team/project IDs. If it touches Confluence, list the space keys.

## Output

What the user sees when the skill finishes. A summary, a link, a created artifact, etc.

## How this was used last

> Update this footer when you use the skill, so the next person knows the real-world use case.
> Format: `YYYY-MM-DD by @username — one-line context`

- 2026-04-29 by @template — placeholder, replace on first real use
