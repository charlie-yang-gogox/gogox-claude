---
name: monthly-summary
description: |
  Generate a monthly usage analysis from Claude Code transcripts.
  Reads config from ~/.claude/monthly-summary-config.json (auto-creates on first run).
  Writes to Notion: Monthly Metrics DB row (numbers) + page body (full report).
  If a previous month exists in the DB, includes MoM trend comparison.
  Use when asked to "monthly summary", "月度總結", "monthly report",
  "this month's summary", or "monthly analysis".
allowed-tools:
  - Bash
  - Read
  - Glob
  - Grep
---

## Monthly Summary Skill

Generate a monthly usage analysis from Claude Code transcripts, with deep
behavioral insights (not just cost/token metrics), and write to Notion.

### Step 0: Load Config (or First-Time Setup)

Read the user's config file:

```bash
bash ~/.claude/skills/monthly-summary/setup.sh --get-config
```

Parse the JSON. Required fields:
- `notion_parent_page_id` — parent page for reports
- `notion_monthly_db_id` — Monthly Metrics database ID
- `language` — report language (zh-TW or en-US)

Optional fields:
- `notion_monthly_ds_id` — data source ID for collection:// search (if null, use notion-fetch instead)
- `notion_work_items_ds_id` — Work Items DB data source ID (if null, skip Work Items and use parse.py for all data)

**If config exists and has required fields:** proceed to Step 1.

**If config is missing or empty:** run the **First-Time Setup** flow below.

---

#### First-Time Setup

Goal: get the user from zero to a working config in under 2 minutes.

##### 0a. Notion MCP Connectivity (Fail-Fast)

**Before anything else**, verify Notion MCP is reachable. Call
`mcp__notion__notion-get-teams` (or any simple Notion MCP call).

- **If it succeeds:** proceed to 0b.
- **If it fails:** STOP immediately and show:

```
Notion MCP is not connected. Monthly summary requires Notion to store reports.

To enable Notion MCP in Claude Code:
  1. Open Claude Code settings (or ~/.claude/settings.json)
  2. Add the Notion MCP integration
  3. Authorize the Notion workspace where you want reports stored
  4. Restart Claude Code and re-run /monthly-summary

Cannot continue without Notion MCP.
```

Do NOT proceed if Notion MCP is dead.

##### 0b. Environment Check

Run the setup script:

```bash
bash ~/.claude/skills/monthly-summary/setup.sh --check-env
```

Parse the JSON output. For each **missing** optional dependency, show:

```
⚠ <dependency name>
  What it does: <one-line description>
  Install:      <platform-appropriate install command>
  If skipped:   <what features are unavailable>
```

| Dependency | Required | What it does | Install | If skipped |
|-----------|----------|-------------|---------|------------|
| python3 | Yes (for parse.py) | Parses transcript .jsonl files to extract session metrics | `brew install python3` (macOS) / `apt install python3` (Linux) | Behavioral analysis (TOP 5, session patterns, slash command stats) unavailable |
| parse.py | Yes | Transcript-scan helper at ~/.claude/skills/_lib/parse.py | Shipped with gogox-claude install.sh — re-run `./install.sh` if missing | Same as python3 missing |
| gh CLI | Optional | Fetches PR data (PRs Merged/Opened) from GitHub | `brew install gh && gh auth login` | PR metrics will not be included |

If python3 is missing, give an extra warning that most behavioral insights
will be unavailable. Ask: "Continue with available tools? (y/n)"

##### 0c. Collect User Input

**Privacy disclosure** — show BEFORE asking for the Notion URL:

```
Note: This skill analyzes your Claude Code session patterns (prompt length,
correction rate, session frequency) to generate behavioral insights.
Only aggregate statistics are stored — no message content is saved.
The report is written to YOUR Notion page only.
```

Then ask TWO questions:

**Q1: Notion page URL**
```
Paste the Notion page URL where you want monthly reports:
(e.g., https://www.notion.so/your-workspace/My-Page-abc123...)
```

Extract the page ID from the URL (last 32 hex chars, insert hyphens to make UUID).

**Q2: Language**
```
Report language:
  1. 繁體中文 (zh-TW)
  2. English (en-US)
```

##### 0d. Detect Existing Database

Before creating a new database, check if one already exists under the parent
page. Call `mcp__notion__notion-fetch` with the parent page URL to inspect
its children. Look for a child database named "Monthly Metrics".

- **If found:** ask: "Found existing Monthly Metrics DB. Use it? (y/n)"
  If yes: capture its database ID, skip 0e, proceed to 0f.
- **If not found:** proceed to 0e.

##### 0e. Create Monthly Metrics DB

Use `mcp__notion__notion-create-database` under the user's chosen parent page.

**Database title:** `Monthly Metrics`

**Properties to create:**

| Property | Type | Description |
|----------|------|-------------|
| Period | title | "YYYY-MM" |
| Total Cost | number (USD, 2 decimals) | Monthly total cost |
| Cost Opus | number (USD, 2 decimals) | Opus model cost |
| Cost Sonnet | number (USD, 2 decimals) | Sonnet model cost |
| Cost Haiku | number (USD, 2 decimals) | Haiku model cost |
| Total Sessions | number | Session count |
| Active Days | number | Days with activity |
| Unique Tickets | number | Deduplicated ticket count |
| AI Active Hours | number (1 decimal) | Hours AI was actively working |
| Thinking Hours | number (1 decimal) | Hours in extended thinking |
| Subagent Spawns | number | Subagent spawn count |
| Cache Hit Rate | number (percent, 1 decimal) | Cache read / total tokens |
| $/AI Hour | number (USD, 2 decimals) | Cost efficiency |
| PRs Merged | number | PRs merged in the month |
| PRs Opened | number | PRs opened in the month |
| Tickets Closed | number | Linear tickets completed |
| Total Tokens | number | Total token usage |
| Cost/Ticket | number (USD, 2 decimals) | Cost per unique ticket |

##### 0f. Discover data_source_id

Call `mcp__notion__notion-fetch` on the database (created or reused) to
discover its `data_source_id` for `collection://` search.

- **If found:** store as `notion_monthly_ds_id`.
- **If not found:** store `null` (skill will fall back to notion-fetch).

##### 0g. Write Config

```bash
bash ~/.claude/skills/monthly-summary/setup.sh --write-config '{
  "schema_version": 1,
  "notion_parent_page_id": "<from 0c>",
  "notion_monthly_db_id": "<from 0d or 0e>",
  "notion_monthly_ds_id": "<from 0f, or null>",
  "notion_work_items_ds_id": null,
  "language": "<from 0c>"
}'
```

Show completion message:
```
Setup complete! Generating your first monthly report now.

Config saved to: ~/.claude/monthly-summary-config.json

Tip: For meaningful insights, use Claude Code for at least 5 working days
before generating your first report.
```

Then continue to Step 1 (do not stop — proceed to generate the report).

---

**Minimum data check** (runs for both new and existing configs):

```bash
python3 ~/.claude/skills/_lib/parse.py --month "$YYYY_MM" --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(len([r for r in d.get('rows',[]) if r.get('total_cost',0)>0]))"
```
If active days < 5, warn: "Only {N} active days found for this month. Report may lack meaningful behavioral insights. Continue? (The report will still be generated but TOP 5 analysis may be thin.)"

### Step 1: Parse Arguments

- `/monthly-summary` → previous month (if today is May, generate April)
- `/monthly-summary YYYY-MM` → specific month
- `/monthly-summary YYYY-MM YYYY-MM` → two months (second gets trend comparison to first)

### Step 2: Extract Data (Hybrid — Work Items DB + parse.py)

Data comes from **two sources** to ensure numbers match the daily dashboard:

#### 2a. Number Layer — from Work Items DB (if available)

**Skip this step if `notion_work_items_ds_id` is null in config.**
When skipped, all numeric data comes from parse.py (Step 2b) instead.

If `notion_work_items_ds_id` is set, query the Work Items DB
(`collection://{config.notion_work_items_ds_id}`) for rows where `Date`
falls within the target month (YYYY-MM-01 to YYYY-MM-{last}).

Use `mcp__notion__notion-search` with `data_source_url`.

Then **aggregate** the daily rows:

| Monthly Field | Aggregation | Work Items Field |
|---------------|-------------|------------------|
| Total Cost | SUM | `費用` |
| Cost Opus | SUM | `Cost Opus` |
| Cost Sonnet | SUM | `Cost Sonnet` |
| Cost Haiku | SUM | `Cost Haiku` |
| Total Sessions | SUM | `Sessions` |
| Total Tokens | SUM | `Tokens` |
| AI Active Hours | SUM | `Active Hours` |
| Thinking Hours | SUM | `Thinking Hours` |
| Subagent Spawns | SUM | `Subagent Spawns` |
| Active Days | COUNT | rows where `費用 > 0` |
| Cache Hit Rate | COMPUTE | `SUM(Cache Read Tokens) / SUM(Tokens)` |
| $/AI Hour | COMPUTE | `Total Cost / AI Active Hours` |
| Items | COUNT | total rows in month |

**Not in Work Items (use parse.py):** PRs Merged, PRs Opened, Tickets Closed
are not columns in Work Items. These come from `parse.py monthly_extras`.

**Coverage check:** Compare how many Work Items rows exist vs calendar days
in the month. If coverage < 80% of active days, log a warning that numbers
may be incomplete.

**Fallback:** If Work Items DB is empty or Notion MCP unavailable, fall back
to `parse.py --month` for all numbers. Log: `⚠️ Work Items unavailable,
numbers from .jsonl (may differ from daily dashboard)`.

#### 2b. Behavioral Layer + Row Context — from parse.py

Always run parse.py for behavioral metrics and row-level context:

```bash
python3 ~/.claude/skills/_lib/parse.py --month "$YYYY_MM" --json
```

Use from this output:
- `behavioral` — deterministic metrics (do NOT recompute via grep)
- `rows` — task-group context for synthesizing TOP 5
- `monthly_extras` — PR/Linear data (PRs Merged/Opened, Tickets Closed — not in Work Items)
- `monthly_stats.unique_tickets` — deduplicated ticket count (Work Items can't do this)

**`behavioral` fields:**

| Field | Description |
|-------|-------------|
| `short_correction_count` | User msgs < 30 chars matching fix/不對/wrong patterns |
| `short_confirm_count` | Short confirmations (ok, yes, continue, push it) |
| `slash_cmd_histogram` | `{"/format": 7, "/commit": 5, ...}` |
| `language_ratio` | `{"zh": 0.034, "en": 0.966}` |
| `figma_url_count` | Messages containing figma.com URLs |
| `session_length_dist` | `{"0": N, "1-3": N, ..., "50+": N}` |
| `user_interruptions` | Count of "[Request interrupted by user]" |
| `total_user_messages` | Total user messages across all rows |

#### 2c. Merge

| Field | Primary Source | Fallback |
|-------|---------------|----------|
| Cost, Tokens, Sessions, Hours | Work Items DB | parse.py monthly_stats |
| PRs, Tickets Closed | parse.py monthly_extras | — |
| Unique Tickets | parse.py (deduplicated) | — |
| Cost/Ticket | Total Cost / Unique Tickets | — |
| Behavioral metrics | parse.py behavioral | — |
| Row context (for TOP 5) | parse.py rows | — |

### Step 3: Fetch Previous Month (for MoM comparison)

If comparing to a previous month, search the Notion Monthly Metrics DB
(`collection://{config.notion_monthly_ds_id}`) for the previous
period's row. Read its page body for the previous month's full stats.

If `notion_monthly_ds_id` is null, use `mcp__notion__notion-fetch` with the database page URL instead of collection:// search.

Alternatively, run `parse.py --month $PREV_YYYY_MM --json` to get fresh data.

### Step 4: Write to Notion

**Two writes required:**

#### 4a. Upsert DB Row (numbers)

First, search the Monthly Metrics DB for an existing row with the target
period. If found → `update_page`. If not → `create_pages`.

Use `mcp__notion__notion-search` with `data_source_url: "collection://{config.notion_monthly_ds_id}"`
to find existing row by period.

If `notion_monthly_ds_id` is null, use `mcp__notion__notion-fetch` with the database page URL instead of collection:// search.

**Properties mapping (from Step 2c merged data):**

```
Period         = "YYYY-MM"
Total Cost     = SUM(Work Items.費用)              ← SSOT
Cost Opus      = SUM(Work Items.Cost Opus)         ← SSOT
Cost Sonnet    = SUM(Work Items.Cost Sonnet)       ← SSOT
Cost Haiku     = SUM(Work Items.Cost Haiku)        ← SSOT
Total Sessions = SUM(Work Items.Sessions)          ← SSOT
Active Days    = COUNT(Work Items rows where 費用 > 0) ← SSOT
Unique Tickets = parse.py monthly_stats.unique_tickets  ← deduplicated
AI Active Hours = SUM(Work Items.Active Hours)     ← SSOT
Thinking Hours = SUM(Work Items.Thinking Hours)    ← SSOT
Subagent Spawns = SUM(Work Items.Subagent Spawns)  ← SSOT
Cache Hit Rate = SUM(Cache Read Tokens)/SUM(Tokens) ← computed from Work Items
$/AI Hour      = Total Cost / AI Active Hours      ← computed
PRs Merged     = parse.py monthly_extras           ← not in Work Items
PRs Opened     = parse.py monthly_extras           ← not in Work Items
Tickets Closed = parse.py monthly_extras           ← not in Work Items
Total Tokens   = SUM(Work Items.Tokens)            ← SSOT
Cost/Ticket    = Total Cost / Unique Tickets       ← computed
```

#### 4b. Write Page Body (full report)

After the DB row exists, write the full monthly report as the page body
using `mcp__notion__notion-update-page` with `command: "replace_content"`.

<!-- ================================================================== -->
<!-- OUTPUT FORMAT — follow this structure exactly                       -->
<!-- ================================================================== -->

The page body MUST contain ALL of the following sections in this order:

#### Section 1: Header

```
# YYYY 年 M 月 — Claude Code 使用分析

> 資料範圍：{sessions} sessions、{active_days} 個活躍日、{tickets} 個 tickets、{tokens} tokens
```

If `config.language` is `en-US`, use English headers and labels:
- "YYYY-MM — Claude Code Usage Analysis" instead of "YYYY 年 M 月 — Claude Code 使用分析"
- "Monthly Overview" instead of "月度總覽"
- "TOP 5 Good Practices" / "TOP 5 Areas for Improvement"
- Table labels in English (Evidence/Data/Example/Why it matters / How to improve)
- "Supplementary Observations" instead of "補充觀察"

#### Section 2: 月度總覽

Table with key metrics. If comparing to a previous month, add columns for
previous month value and % change.

Include:
- 總 sessions, 用戶訊息, 總花費, Total tokens
- AI Active Hours, Thinking Hours, Subagent Spawns
- Cache Hit Rate, $/AI Hour, 活躍天數, Unique Tickets
- PRs Merged, PRs Opened, Tickets Closed

Then sub-tables for:
- **Model 分佈** (費用 + 佔比, with trend if comparing)
- **Output 分佈** (feature dev, code review, bug fix, etc.)
- **指令使用頻率** (from `behavioral.slash_cmd_histogram`)
- **Session 長度分佈** (from `behavioral.session_length_dist`)

<!-- ================================================================== -->
<!-- TOP 5 FORMAT — THIS IS THE MOST IMPORTANT SECTION                  -->
<!-- Never replace with "Top 5 花費項目" or cost rankings.              -->
<!-- These are behavioral insights, not accounting reports.             -->
<!-- ================================================================== -->

#### Section 3: TOP 5 做得好的地方

Exactly 5 items. Each item MUST use this table format:

```markdown
### {N}. {One-line title describing the good practice}

| 項目 | 內容 |
|------|------|
| **事蹟** | What was done well — concrete behavior, not vague praise |
| **數據** | Quantitative evidence: counts, frequencies, percentages |
| **實例** | Specific example: ticket ID, session, command, or quote |
| **為什麼好** | Why this matters — the impact on productivity/quality |
```

If comparing months, add a row:
```
| **vs {prev_month}** | How this changed from the previous month |
```

**What qualifies as "做得好":**
- Systematic workflows (OpenSpec, port, code review pipelines)
- Smart model selection (Haiku for exploration, Sonnet for routine)
- Providing real data during debug (API responses, screenshots, logs)
- Using worktrees for isolation
- Building reusable tools/skills
- Planning before coding
- Structured input with clear specs
- Figma integration with node IDs
- Agent team orchestration

**What does NOT qualify:**
- High spending (that's not an achievement)
- Large number of sessions (that's volume, not quality)
- Cost rankings or "most expensive tickets"

#### Section 4: TOP 5 可改進的地方

Exactly 5 items. Same table format as above:

```markdown
### {N}. {One-line title describing what needs improvement}

| 項目 | 內容 |
|------|------|
| **事蹟** | What happened — the problematic pattern |
| **數據** | Quantitative evidence of the problem |
| **實例** | Specific example showing the issue |
| **改進方式** | Actionable suggestion to fix it |
```

If comparing months, add:
```
| **vs {prev_month}** | Whether this got better or worse vs previous month |
```

**What qualifies as "可改進":**
- Opus overuse for tasks Sonnet/Haiku can handle
- Short/vague correction messages ("fix it", "不對", "still wrong")
- Session scope creep (50+ turns drifting across topics)
- Restarting sessions instead of continuing
- Skipping verification steps (opsx:verify)
- Security issues (tokens/credentials in messages)
- Cost concentration on single days
- Low Figma integration for UI tasks
- Inconsistent workflow adherence

**What does NOT qualify:**
- High total cost alone (without explaining WHY it's problematic)
- Low activity days (could be vacation)

#### Section 5: 效率指標趨勢 (only if comparing months)

Table comparing efficiency metrics: Sessions/Ticket, Cost/Ticket,
Subagents/Session, User Msgs/Session, Tokens/Session, Cost/Msg extremes.

#### Section 6: 補充觀察

2-4 bullet points on patterns not covered above (peak days, language
usage from `behavioral.language_ratio`, security notes, dispatcher status,
personal project ratio).

### Step 5: Idempotency

- **Re-run same month:** Search DB by Period → found = update row + replace
  page body. Not found = create new row. Result is identical either way.
- **Notion MCP unavailable:** Report error and stop. Do NOT fall back to
  Obsidian or any other destination.

### Step 6: Verify

Read the DB row back via `mcp__notion__notion-fetch` to confirm properties
and page body were saved correctly.
