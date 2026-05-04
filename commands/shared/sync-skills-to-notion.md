---
name: sync-skills-to-notion
description: >
  Sync all Claude Code skills from .claude/commands/ to the Moonshot Note page in Notion.
  Scans skill files, categorizes them, creates/updates child pages, rebuilds main page sections.
---

# Sync Skills to Notion

Scan all skill files in `.claude/commands/`, compare with the Moonshot Note page in Notion,
and create/update child pages. Rebuilds category sections and dependency graph on the main page.

**Target Notion page ID:** `31bf54d1149880f1922ad891e6cf538f`

---

## Step 1: Scan all skill files

1. Glob `.claude/commands/**/*.md` to find all skill files.
2. For each file, read its content and extract:

| Field | How to extract |
|-------|----------------|
| **slug** | Filename without `.md`. For `opsx/*.md`, use `opsx:<name>` (e.g., `opsx:apply`). |
| **display_name** | `/<slug>` (e.g., `/commit`, `/opsx:apply`) |
| **title** | From frontmatter `name`, or from first `# heading`, or derive from filename. |
| **description** | From frontmatter `description`, or first non-heading paragraph. |
| **category** | See categorization rules below. |
| **arguments** | From `**Arguments:**` line if present. |
| **dependencies** | Scan for patterns: `Invoke /xxx`, `/xxx` as sub-skill call, `Use /xxx`. Collect skill slugs. |
| **content** | Full markdown body after frontmatter. |

### Categorization rules

| Pattern | Category |
|---------|----------|
| `opsx/*.md` | **OpenSpec** |
| `check-*.md` | **Atomic** |
| Everything else | **Orchestration** |

## Step 2: Fetch existing Notion state

1. Fetch the Moonshot Note page (`31bf54d1149880f1922ad891e6cf538f`).
2. Extract all child page URLs and titles from `<page url="...">` tags in the content.
3. Build a lookup map: match each child page title to a skill slug.
   - Title `"Use /commit with Claude skills"` -> slug `commit`
   - Title `"Use /opsx:apply with Claude skills"` -> slug `opsx:apply`

## Step 3: Create or update each child page

For each skill from Step 1:

### If a matching Notion page exists:

1. Fetch the existing page content.
2. Compare with the new content (below). If materially different, update using `replace_content`.
3. If substantially the same, skip.

### If no matching page exists:

Create a new child page under the Moonshot Note page with:

- **Title:** `Use <display_name> with Claude skills`
- **Icon:** `🪄`
- **Parent:** `{ "type": "page_id", "page_id": "31bf54d1149880f1922ad891e6cf538f" }`

### Child page content template

Generate this content for each skill. Adapt the sections based on what's available in the
skill file — omit sections that have no content rather than leaving them empty.

IMPORTANT: Before writing any Notion content, fetch the Notion Markdown spec at
`notion://docs/enhanced-markdown-spec` to ensure correct syntax.

```markdown
# `<display_name>` — <title>

<description>

---

## Category

<Orchestration | Atomic | OpenSpec>

## Usage

\`\`\`
<display_name> [arguments if any]
\`\`\`

<argument details if present>

---

## What It Does

<Summarize the Steps section from the skill file as a concise numbered list.
Each step should be 1-2 sentences. Aim for 3-8 steps. Do NOT copy verbatim —
rewrite for a reader who wants to understand the skill, not execute it.>

---

## Dependencies

<List of other skills this one calls, formatted as bullet points:>
- `/check-clean` — verifies working tree is clean
- `/format` — runs dart format pipeline

<If no dependencies: "None — standalone skill.">

---

## Rules

<Key rules from the skill file, reformatted as bullet points. Keep the important
constraints; drop implementation details. Max 5-8 bullets.>
```

## Step 4: Rebuild main page content

After all child pages are handled, rebuild the Moonshot Note main page.

1. Re-fetch the Moonshot Note page to get all current child page `<page url="...">` tags.
2. Build the new content using this structure:

```markdown
## Orchestration Skills

High-level commands that coordinate multiple sub-skills to complete a workflow.

<page url="...">Use /work with Claude skills</page>
<page url="...">Use /commit with Claude skills</page>
...sorted alphabetically by skill name...

## Atomic Skills

Single-purpose checks and operations, reusable by orchestration skills.

<page url="...">Use /check-archive with Claude skills</page>
...sorted alphabetically...

## OpenSpec Skills

Artifact workflow commands for the OpenSpec development process.

<page url="...">Use /opsx:new with Claude skills</page>
<page url="...">Use /opsx:continue with Claude skills</page>
<page url="...">Use /opsx:ff with Claude skills</page>
<page url="...">Use /opsx:apply with Claude skills</page>
<page url="...">Use /opsx:verify with Claude skills</page>
<page url="...">Use /opsx:archive with Claude skills</page>
<page url="...">Use /opsx:bulk-archive with Claude skills</page>
<page url="...">Use /opsx:sync with Claude skills</page>
<page url="...">Use /opsx:onboard with Claude skills</page>
<page url="...">Use /opsx:explore with Claude skills</page>

## Dependency Graph

\`\`\`plain text
<Generate a dependency graph from all skills' dependency data.
Format like the existing graph — show Orchestration skills on the left
with their dependency trees, Atomic/OpenSpec on the right.>
\`\`\`
```

3. Use `replace_content` on the Moonshot Note page. CRITICAL: preserve all `<page url="...">` tags — these are references to child pages and must not be lost.

## Step 5: Report

Show a summary:

```
Sync complete.

Created: <N> new pages
  - /dev
  - /opsx:apply
  ...
Updated: <N> pages
  - /commit (content changed)
  ...
Unchanged: <N> pages

Moonshot Note: https://www.notion.so/31bf54d1149880f1922ad891e6cf538f
```

---

## Rules

- Always fetch existing pages before creating to avoid duplicates.
- Preserve all `<page url="...">` references when rebuilding the main page.
- If a skill file has merge conflict markers (`<<<<<<<`), skip it and report a warning.
- If a Notion MCP call fails, log the error and continue with remaining skills.
- OpenSpec skills sort order: new → continue → ff → apply → verify → archive → bulk-archive → sync → onboard → explore.
- Do NOT create a child page for `sync-skills-to-notion` itself — skip this skill file.
