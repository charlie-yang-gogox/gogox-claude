---
name: sync-skills-to-notion
description: >
  Sync all Claude Code skills from gogox-claude into a single Notion database.
  Each skill becomes one row tagged with its source folder as Category. Filtering
  and grouping happens in Notion's native database views.
---

# Sync Skills to Notion

Maintain a Notion database of every gogox-claude installable as the team's living
index. Three asset trees are covered — slash **commands**, model-invoked **skills**,
and sub-**agents** — all upserted into one flat database. Each entry is a single
row; the row's page body holds the full detail.

This skill **must run from the `gogox-claude` repo root** — it relies on the
source folder structure to determine each entry's `Category`. The folders are
flattened by `install.sh` once symlinked into `~/.claude/`, so running from there
would lose category information.

## Step 1: Bootstrap

1. Verify cwd is the gogox-claude repo root:
   - Run `git rev-parse --show-toplevel` and confirm the basename is `gogox-claude`,
     OR confirm `install.sh` exists at the root with the gogox-claude header.
   - If not, abort with: `Run this skill from the gogox-claude repo root.`

2. Load `commands/shared/sync-skills-to-notion.config.yaml`.
   Required fields: `notion.parent_page_id`, `notion.database_title`.
   `notion.database_id` may be empty (first-run case).

3. Verify Notion MCP is authenticated. The `claude.ai Notion` server's concrete
   tool schemas (create database, query database, create page, update page,
   archive page) are only exposed after authentication. If the only Notion tool
   available is `mcp__claude_ai_Notion__authenticate`, prompt the user to run it
   and stop.

4. Use `ToolSearch` to load the Notion tool schemas you'll need this run
   (search terms: "notion database", "notion page create", "notion page update",
   "notion archive"). Do NOT hardcode tool names in this file — they may version.

## Step 2: Resolve the database

Goal: end this step with a known-good `database_id` pointing at a database under
`parent_page_id` whose title is `database_title`.

1. If `notion.database_id` is non-empty, fetch the database to confirm it still
   exists. If the fetch succeeds, proceed to Step 3.

2. Otherwise (empty or fetch failed), look for a child database of
   `parent_page_id` whose title matches `database_title`. If found, reuse that
   database's ID.

3. If still no match, **create** a new database under `parent_page_id` with this
   schema:

   | Property | Type | Notes |
   |---|---|---|
   | Name | Title | The skill display name, `/<slug>` |
   | Category | Select | Options: `dev`, `design`, `pm`, `shared` |
   | Description | Rich text | One-line summary |
   | Last Synced | Date | Timestamp of last sync |

4. Write the resulting database ID back into
   `commands/shared/sync-skills-to-notion.config.yaml` under `notion.database_id`.
   Use `Edit` to do an in-place replacement of the empty value — preserve all
   other lines and comments.

## Step 3: Scan asset files

1. Scan three asset trees (do NOT recurse beyond these patterns — `commands/dev/profiles/` holds YAML data, and skill directories may contain helper scripts that are not entries):
   - `commands/{dev,design,pm,shared}/*.md` → **command** entries
   - `skills/{dev,design,pm,shared}/*/SKILL.md` → **skill** entries
   - `agents/{dev,design,pm,shared}/*.md` → **agent** entries

2. Skip:
   - Any file containing merge-conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`). Record a warning and continue.

   The sync skill itself (`commands/shared/sync-skills-to-notion.md`) IS included — the team needs visibility into every installable, including this one.

3. For each remaining file, extract:

   | Field | How |
   |---|---|
   | `slug` | for commands/agents: filename without `.md`. For skills: parent directory name (since file is always `SKILL.md`). |
   | `display_name` | `/<slug>` (consistent prefix across all three asset types) |
   | `category` | for commands/agents: parent folder name. For skills: grandparent folder name (`skills/<category>/<slug>/SKILL.md`). |
   | `kind` | `command` / `skill` / `agent` — derived from which tree the file came from. Used in the row body, NOT a Notion property. |
   | `title` | frontmatter `name`, else first H1, else humanized slug |
   | `description` | first line of frontmatter `description` (collapse multi-line YAML scalars) |
   | `arguments` | text after `**Arguments:**` if present (commands/skills only) |
   | `dependencies` | scan body for `/<name>` references; keep only those whose `<name>` matches another scanned entry's slug (drops noise like `/path/to/file`) |
   | `body` | the post-frontmatter markdown — used to summarize "What It Does" and "Rules" sections |

## Step 4: Upsert each skill as a database row

For each scanned skill:

1. Query the database for an existing row where `Name == /<slug>` (or matching
   the title property exactly).

2. Build the new row content:
   - **Properties**:
     - `Name` = `/<slug>`
     - `Category` = `<category>`
     - `Description` = the one-line description
     - `Last Synced` = today's date
   - **Page body**:

     ```markdown
     # /<display_name> — <title>

     <description>

     ## Usage

     For commands/skills: `/<display_name> [arguments if any]`
     For agents: invoked via the Agent tool with `subagent_type: "<slug>"`.

     <argument detail line, omitted if none>

     ## What It Does

     <Numbered list, 3–8 items, summarizing the Steps section of the source
     skill file. Rewrite for a reader who wants to understand, not execute.
     1–2 sentences per item. Do NOT copy verbatim.>

     ## Dependencies

     <Bulleted list of `/xxx — purpose`, one per detected dependency.
     If none, write: "None — standalone skill.">

     ## Rules

     <Up to 5–8 bullets distilling the most important constraints from the
     source file's Rules section. Drop implementation detail.>

     ---

     Source: `<commands|skills|agents>/<category>/<slug>.md` (skills use `<slug>/SKILL.md`)
     ```

3. Decide action:
   - **No existing row** → create a new row with the above.
   - **Row exists** → fetch the existing row's properties + body. Diff against
     the new content (ignore the `Last Synced` field when diffing — it always
     changes). If material difference, update properties and replace the page
     body. If substantively identical, skip and count as unchanged.

## Step 5: Reconcile deletions

1. List all rows currently in the database.
2. For each row whose `Name` does NOT correspond to any scanned skill, archive
   it (Notion's standard delete — reversible from trash).
3. Record the archived names for the report.

## Step 6: Report

Print a summary like:

```
Sync complete.

Database: https://www.notion.so/<database_id>

Created:   <N>
  - /<slug>
  ...
Updated:   <N>
  - /<slug>
  ...
Unchanged: <N>
Archived:  <N>  (skill files removed from repo)
  - /<slug>
  ...
Warnings:  <N>
  - <message>
  ...
```

## Rules

- Always run from the gogox-claude repo root. Abort otherwise.
- Read config before any Notion call; never hardcode IDs in this file.
- Keep the database table view minimal — Name / Category / Description / Last Synced only. All other detail belongs in the row's page body.
- Skill detail belongs in the database **row's page body**, not as separate child pages of the parent.
- Include every command, skill, and agent — including `sync-skills-to-notion.md` itself. The team's index should be complete.
- If a Notion call fails for one skill, log the error and continue with the rest.
- Use Notion's archive (not destroy) when reconciling deletions — gives a recovery window.
- After auto-creating the database, write its ID back to the config file using `Edit` (preserve comments and surrounding lines).
- Do not regenerate or maintain a dependency graph — Notion DB views replace the old hand-curated section layout.
