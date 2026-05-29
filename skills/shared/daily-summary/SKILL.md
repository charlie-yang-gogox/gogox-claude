---
name: daily-summary
description: |
  Generate a daily work summary from Claude Code execution history.
  Source: scans ~/.claude/projects/*/*.jsonl transcripts directly — no hook needed.
  Covers every cwd (work_project, vault, Desktop, subagents) automatically.
  Writes to Notion automatically.
  Use when asked to "daily summary", "work summary",
  "what did I do today", "today's work report", or "每日摘要".
allowed-tools:
  - Bash
  - Read
  - Glob
  - Grep
---

## Daily Summary Skill

Generate a daily work summary from Claude Code's local transcripts, display as a table, and write to Notion.

**Source change (post-2026-04-26):** This skill no longer reads `session_metrics.csv` (Stop-hook output). It scans `~/.claude/projects/*/*.jsonl` transcripts directly. Every Claude Code session writes a transcript regardless of hook configuration, so coverage is complete (vault / Desktop / subagents / work_project all included).

**Personal IDs live in `~/.claude/daily-summary-config.json`** (auto-created by `parse.py` on first run). Five Notion IDs — `parent_page_id`, `work_items_db_id`, `inner_page_id`, `weekly_prs_db_id`, `weekly_metrics_db_id` — are surfaced in the JSON output under `user_config.notion.*`. Step 0 below describes the first-run wizard that fills them. **Do NOT hardcode any IDs in this file** — anyone reading the diff later will assume they belong to the team.

### Step 0: First-run bootstrap (only when `user_config` has empty IDs)

After Step 2 produces the JSON, check `user_config.notion`. If **all five** IDs are non-empty strings → skip this section, go straight to Step 3.

If any are empty, run the bootstrap wizard:

1. **Resolve Notion MCP prefix.** Try `mcp__claude_ai_Notion__notion-*` first (interactive), then `mcp__notion-hosted__notion-*` (headless via `claude mcp add notion-hosted`). If NEITHER is present:
   - Emit: `> Notion MCP 未連線，跳過 Notion 寫入。請先連接 Notion MCP，或手動填 ~/.claude/daily-summary-config.json 的 notion.* 欄位。`
   - Skip all of Step 4. Display the table (4a) only.

2. **Headless guard.** If the session is running headlessly (`claude -p` from launchd / cron / CI — no interactive stdin), DO NOT prompt. Print the same skip message as step 1 and exit Step 4. **Bootstrap requires an interactive session.** Detection heuristic: assume headless if the invocation looks like a scheduled run (no human turn before this skill fired).

3. **Prompt the user once** for a parent location:
   > Paste the Notion page URL where I should create your Daily Summary Work Record (e.g. your Personal / Work parent page):

   Extract the 32-char hex (with or without dashes) from the URL.

4. **Load schema.** Read `skills/shared/daily-summary/notion-schema.json`.

5. **Create in `bootstrap_order`.** For each entry:
   - `parent_page` and `inner_page` → call `notion-create-pages` with the resolved parent ID (user-pasted for parent_page; the just-created `parent_page_id` for inner_page). Use `default_title` and `icon` from the schema.
   - `work_items_db`, `weekly_prs_db`, `weekly_metrics_db` → call `notion-create-database` with the resolved parent ID (parent_page or inner_page per schema's `parent` field) and the `properties` block. Skip the `_*` meta keys (those are doc-only).
   - Capture each returned ID.

6. **Persist.** Overwrite `~/.claude/daily-summary-config.json` with the five resolved IDs under `notion.*`. Keep the `_comment` field.

7. **Confirm.** Print:
   ```
   ✅ Bootstrap complete. Created in Notion:
      parent_page:        <id>
      work_items_db:      <id>
      inner_page:         <id>
      weekly_prs_db:      <id>
      weekly_metrics_db:  <id>
   IDs saved to ~/.claude/daily-summary-config.json.
   ```

8. **Re-read the config** before Step 4 (or just use the in-memory IDs from the wizard). Continue with the normal flow.

The wizard is one-shot — once the five IDs are saved, subsequent runs skip Step 0 entirely.

### Step 1: Parse Arguments

- `/daily-summary` → today, full output
- `/daily-summary YYYY-MM-DD` → specific date, full output
- `/daily-summary yesterday` → yesterday, full output
- `/daily-summary --demo` → today, demo mode (table without 工作摘要 column)
- `/daily-summary YYYY-MM-DD --demo` → specific date, demo mode

Resolve date to `YYYY-MM-DD` in local timezone. If `--demo` flag is present, set demo mode = true.

### Step 2: Extract, Group & Classify

Run the canonical CLI. It scans transcripts, dedupes per requestId, applies
pricing, groups, classifies, and additionally fetches gh PR data, Linear
tickets, weekly aggregates, and 90-day historical averages. Substitute the
resolved date for `$TARGET_DATE`:

```bash
python3 ~/.claude/skills/daily-summary/parse.py --target-date "$TARGET_DATE" --json
```

**Do NOT inline a Python script here.** `parse.py` is the single source of
truth for the data pipeline. Any logic that lives only in this file is wrong
by definition.

The CLI outputs JSON with `schema_version: "1.2"` and these top-level keys:

| Key | Type | Notes |
|-----|------|-------|
| `schema_version` | string | `"1.2"` |
| `target_range` | `{start, end}` | Both equal in single-day mode |
| `rows` | array | Pre-grouped, pre-classified work items (see below) |
| `daily_stats` | object | Aggregate totals for the day (cost, tokens, sessions, cache_hit_rate, output_per_turn, …) |
| `daily_breakdown` | array | Per-date breakdown (single entry in single-day mode) |
| `warnings` | array | Non-fatal warnings from the scan / gh / Linear |
| `user_config` | object | Loaded from `~/.claude/daily-summary-config.json`. Contains `notion.parent_page_id`, `notion.work_items_db_id`, `notion.inner_page_id`, `notion.weekly_prs_db_id`, `notion.weekly_metrics_db_id`. Empty strings indicate first-run bootstrap is needed (see Step 0). |
| `prs_opened` / `prs_merged` / `prs_in_progress` | int\|null | gh-derived; null if `gh` unavailable |
| `prs_this_week_list` | array | PRs in the ISO week of TARGET (sorted merged-desc) |
| `tickets_closed_today` / `tickets_reopened_today` | int\|null | Linear-derived |
| `linear_tickets_this_week` | array | Linear issues assigned to me, updatedAt ≥ ISO Monday |
| `weekly_aggregates` | array | 12 ISO weeks, oldest first, each with `wow_delta` and `row_health` |
| `historical_averages` | object | `{avg_7d, avg_30d, avg_90d, daily_series[90]}` |

**Each row in `rows`** carries: `ticket`, `tickets[]`, `is_dispatcher`,
`branch_prefix`, `first_time`, `last_time` (ISO 8601), `wall_sec`, `ai_sec`,
`think_sec`, `session_count`, `subagent_spawns`, `cost_total`,
`cost_by_model`, `tokens_total`, `tokens_by_kind`, `user_msgs`, `messages`
(snippets), `suggested_output` (one of: `PR shipped`, `feature dev`,
`bug fix`, `code review`, `PRD/spec`, `tooling`, `research`, `devops`),
`session_ids`, `cwd`.

The LLM only needs to **synthesize 工作摘要** for each row, sanity-check
ticket attribution, and write to Notion.

<!-- Internals (`scan_session`, `build_rows`, `classify`, pricing tables,
GAP_CAP, row_health thresholds) are documented in parse.py. -->

```bash
# Reference values used inside parse.py (do NOT redefine here):
#   TRIVIAL slash commands: /mcp, /daily-summary, /session-metrics
#   GAP_CAP = 600s (gap > this is treated as AFK)
#   Pricing per 1M tokens: opus 15/75, sonnet 3/15, haiku 1/5 (input/output)
```

### Step 3: Synthesize 工作摘要 + sanity-check ticket attribution

The script outputs `rows` with `suggested_output`, `messages`, `ticket`, `cost`, etc.

**Your job (two parts):**

1. **Synthesize 工作摘要** — for each row, write ONE short phrase based on `messages`. Don't re-derive `suggested_output`; use as-is unless it's clearly wrong after reading messages.

2. **Sanity-check ticket attribution before writing.** The script's `extract_ticket()` uses a generic regex `[A-Z]{2,}-\d+` and falls back to scanning user messages when `cwd` and `git_branch` don't contain a ticket ID. This fallback over-attributes — common false positives:
   - SKILL.md / other skill text leaking into messages (the skill is injected when invoked)
   - Subagent reports quoting "Top Cost Tickets" tables or archive listings (`[已合併] CAF-XXX`, `[已合併] CET-XXX`, etc.)
   - Pasted historical context referencing past tickets
   - `<teammate-message>` blocks in split-panel / team workflows — teammates quote ticket IDs from their analysis, but the parent session is coordinating, not doing that ticket's work
   - gen-su-report / daily-summary formatted output — these tools emit `CAF-xxx: description` ticket lists or Linear links in their display output
   - Notion MCP content — when fetching/updating Notion pages (dashboard refresh, page analysis), ticket IDs in the page content get captured by regex
   - Schedule / routine discussion — conversation about scheduling agents to review pages or data that references tickets

   For each row where `ticket` is non-empty AND `cwd`/`git_branch` don't contain that ticket ID (i.e., it came from message regex), read the `messages` snippets. If the ticket appears only inside one of the patterns above and the row's actual work is meta-tooling / vault / Desktop research / dashboard — **set `ticket=""` and clear `tickets` list before writing to Notion**. Real ticket work happens in worktrees where the cwd path itself contains the ticket id, so genuine attribution survives this check.

   **Special cases — clear primary `ticket` but PRESERVE `tickets` list:** Some commands legitimately operate on multiple tickets at once. The session is not "doing" any single ticket, but the ticket list IS the work product and must be retained:

   - **`/ggx-dispatcher` / `<task-notification>` orchestration** — the dispatcher fans out work to multiple tickets. The tickets it dispatched are real work-in-flight, not noise. Set `ticket=""` (no primary) but **keep** all dispatched tickets in `tickets[]` so the Notion row's multi_select preserves the dispatch manifest. Title prefix: `Dispatched: CAF-xxx, CAF-yyy, ...` (list first 3-5 in title).
   - **`/code-review` batch sessions reviewing multiple PRs** — when one session runs `/code-review` against several PRs, each PR's ticket is a legitimate review subject. Set `ticket=""` (no primary) but **keep** the reviewed tickets in `tickets[]`. Title: `Code review: CAF-xxx, CAF-yyy, ...` (list first 3-5).
   - **Worktree management (`/list-worktrees`, `/remove-worktree --auto`)** — these list/clean ticket worktrees. The listed tickets are NOT current work — they were past work being cleaned up. **Clear both `ticket` and `tickets[]`**. (This is the original false-positive rule; kept here for contrast.)

   Rule of thumb: if the row's existence is *about* the ticket list (dispatcher, multi-PR review), preserve `tickets[]`. If the tickets are merely *referenced* during meta-tooling (cleanup, dashboard refresh), clear them.

3. **Validate tickets via API (regex-derived only).** After the pattern-based sanity check above, validate each surviving ticket that came from message regex (NOT from cwd/branch — those are inherently reliable). This catches false positives that pass pattern checks but reference non-existent tickets.

   **Skip validation for cwd/branch-derived tickets:** If a ticket ID appears in the row's `cwd` or `branch_prefix`, it was extracted from the filesystem path and is reliable — do not call any API for it.

   **For each regex-derived ticket that survived step 2:**
   1. Resolve the Linear `get_issue` tool name first — call ToolSearch with query `select:mcp__claude_ai_Linear__get_issue`; if no match (Claude desktop routine context exposes Linear tools under a UUID prefix instead of the friendly name), fall back to ToolSearch with query `+linear get_issue` and pick the first result whose name ends with `__get_issue`. Cache the resolved tool name and reuse it across all tickets in this run. Same for Jira: try `mcp__claude_ai_Atlassian_Rovo__getJiraIssue` first, otherwise resolve via `+jira getJiraIssue`.
   2. Call the resolved Linear tool with the ticket identifier (e.g. `CAF-355`, `CET-8360`).
   3. If Linear returns a valid issue → keep the ticket (confirmed real).
   4. If Linear returns not found → call the resolved Jira tool with the same identifier (some prefixes like `CET-xxx` may live in Jira rather than Linear).
   5. If neither Linear nor Jira finds the ticket → it is a false positive. Set `ticket=""` and remove it from `tickets` list.

   **Efficiency notes:**
   - Collect all unique regex-derived tickets across all rows first, then validate each ticket once. Apply the result to all rows referencing that ticket.
   - Typically only 0-3 tickets need validation per run (most real tickets come from cwd/branch).
   - Track which tickets were validated so the same ticket is not checked twice across rows.

   **Fallback:** If neither tool-name resolution path returns a Linear `get_issue` tool (truly disconnected, not just renamed), skip API validation entirely and rely on the pattern-based check from step 2 only. Log a warning: `Linear MCP 未連線，跳過 ticket API 驗證。`. Do NOT log this warning when the friendly name is missing but the UUID name resolves — that's a routine-namespace artifact, not a disconnection.

### Step 3.5: Fetch Remote Routine Reviews

After local session data, also fetch reviews posted by scheduled RemoteTrigger routines.

**Trigger IDs:**
- `trig_018dZjktd9e3H1z8QHMTqV1D` — `ca-flutter-code-review` → `gogovan/gogox-client-flutter`
- `trig_01FCgVkCxhX2zm46wVDgNyaM` — `da-flutter-code-reivew` → `gogovan/gogox-driver-flutter`

**Date attribution rule:** The routine for date D fires at D 19:00 UTC. Reviews land D 19:00 – D+1 05:00 UTC. **Always attribute to date D** (the cron schedule date, not HK calendar date).

**Fetch logic for target date D (only if TARGET is today or yesterday):**

1. **D's routine:** Search for comments posted between `D 19:00 UTC` and `D+1 05:00 UTC`.
   - If current time < D 19:00 UTC → routine hasn't fired. Add a note: `(routine 尚未執行)`.
   - If current time >= D+1 05:00 UTC → fetch and include.
   - If in between → fetch what's available so far.

2. **Backfill D-1 (only if TARGET is today):** Check if D-1's routine was already written to Notion. If not, fetch and write with date = D-1.

**Fetch command per repo:**
```bash
for pr in $(gh pr list --repo REPO --state all --limit 30 --json number --jq '.[].number'); do
  gh api "repos/REPO/issues/$pr/comments" \
    --jq ".[] | select(.body | test(\"Internal code review\")) | select(.created_at >= \"START_UTC\" and .created_at < \"END_UTC\") | {pr: $pr, created_at, body_preview: (.body | split(\"\n\")[0:5] | join(\" | \"))}" 2>/dev/null
done
```

**Output:** Each routine run with reviews = one row:
- 時段: `"routine 03:00"`
- Output: `code review (routine)`
- 費用: null (remote execution, no local cost)
- Ticket: extract all `[A-Z]{2,}-\d+` from the reviewed PR branches/titles
- Sessions: 1 per routine run
- If no reviews were posted, skip (routine found nothing to review)

### Step 3.9: Pre-Step-4 safety checks (READ BEFORE EVERY RUN)

**A. Tool availability — interactive uses OAuth MCP, headless uses REST helper**

Two write paths, chosen by run mode. **Detect mode by tool list, then commit to
that path** — do not mix.

- **Interactive Claude Code session** — `mcp__claude_ai_Notion__notion-create-pages`
  IS in the tool list (the user's claude.ai catalog auto-loads the high-level
  Notion-flavored Markdown tools). Use `mcp__claude_ai_Notion__notion-*` for BOTH
  4b (Work Items) and 4d (dashboard refresh), exactly as documented below.

- **Headless `claude -p` from launchd** — `mcp__claude_ai_Notion__*` is ABSENT.
  Do NOT depend on OAuth here. The hosted `notion-hosted` OAuth token lapses
  periodically (refresh-token rotation + nights the Mac is asleep / the 23:30 job
  is missed), which silently broke past headless runs — they produced only the
  stdout table and skipped all Notion writes. Instead, run **BOTH 4b and 4d via
  the bundled REST helper** using the long-lived internal integration token
  (`ntn_...`), which never expires:

  ```bash
  # 4b — Work Items (one row per day)
  python3 ~/.claude/skills/daily-summary/notion_rest.py create-work-items <rows.json>
  # 4d — dashboard (📊 / 📈 section-replace + Weekly PRs / Weekly Metrics upsert)
  python3 ~/.claude/skills/daily-summary/notion_rest.py refresh-dashboard <dashboard-payload.json>
  ```

  See 4b **"Headless variant"** for `<rows.json>` and 4d **"Headless variant"**
  for `<dashboard-payload.json>`. The helper still computes nothing and generates
  no text — YOU (the headless LLM, which IS running; only the OAuth MCP is
  missing) build both JSON files: every number, every zh-TW AI 草稿 / AI 建議.
  The helper does only the mechanical Notion REST I/O. It is **chart-boundary
  safe**: section-replace collects blocks from the matched H2 only until the first
  barrier block (`heading_*` / `column_list` / `child_database` / `child_page` /
  `synced_block` / `table_of_contents`), so the 🔧 Debug View `column_list`
  directly below 📈 is never crossed (see section B for the same rule). Always
  build the payload, then **run `refresh-dashboard --dry-run` first** and confirm
  the printed delete/append plan before the real call.

- **Neither available** (headless AND the `ntn_` token cannot be resolved — file
  missing / integration revoked): output the 4a table to stdout, then emit the
  setup pointer and SKIP 4b/4d:
  `> Notion 寫入未執行 (headless 無 ntn_ token)。一次性設定見「Headless token setup」段，或執行 notion_rest.py 會印出步驟。`
  Do NOT fail silently — a new user of this shared repo must learn how to enable it.

The `ntn_` token is **per-user and is never committed to this repo**; the helper
resolves it at runtime (config file → `$NOTION_TOKEN` → `--token`). If it is
missing, `notion_rest.py` prints the full setup steps and exits non-zero. If a
headless run logs `401 unauthorized`, that user's integration was unshared from
their hub page or their token rotated — re-share the page or replace the token.

**Headless token setup (one-time, per-user) — required only to enable the launchd automation:**

Interactive runs need NOTHING here (they use the OAuth Notion MCP). A user only
needs a token to run the skill headless. Steps:

1. Create a Notion internal integration at <https://www.notion.so/profile/integrations>
   → **New integration** → copy its **Internal Integration Secret** (`ntn_...`).
2. Share your work-record hub page with that integration: open the page that owns
   your Work History DB (the `parent_page_id` in `~/.claude/daily-summary-config.json`)
   → top-right `⋯` → **Connections** → add your integration. Child DBs inherit.
3. Write `~/.claude/daily-summary-mcp.json`:
   ```json
   {
     "mcpServers": {
       "notion": {
         "env": {
           "OPENAPI_MCP_HEADERS": "{\"Authorization\":\"Bearer ntn_YOURTOKEN\",\"Notion-Version\":\"2022-06-28\"}"
         }
       }
     }
   }
   ```
4. `chmod 600 ~/.claude/daily-summary-mcp.json`

**When running INTERACTIVELY:** if `~/.claude/daily-summary-mcp.json` is absent AND
the user asks about / is setting up the nightly automation, offer to do step 3 for
them — prompt once for their `ntn_` value, then write the config file with the
shape above (do NOT echo the token back). Otherwise do not nag interactive users
about the missing token; it is irrelevant to interactive runs.

> **Why not OAuth headless:** `mcp.notion.com/mcp` only accepts claude.ai-issued
> OAuth tokens, and those lapse. The `ntn_` internal-integration token has no
> expiry, so the nightly job never needs re-auth. Both 4b and 4d now run over the
> `ntn_` REST helper headless (the helper hand-builds blocks instead of relying on
> the OAuth Markdown tools). Interactive runs still use the OAuth Markdown tools
> for 4d because they're already loaded and re-auth there is a trivial `/mcp` away.

**B. Chart-preservation rule — do NOT touch column_list / child_database / child_page / embed / image / video / toggle / synced_block**

The parent page (`user_config.notion.parent_page_id`) and the inner detail page contain
NON-MARKDOWN blocks that surround the H2 sections:

- `column_list` blocks holding embedded charts (the bar/line graphs visible in
  Notion UI under `📈 12 週趨勢` and elsewhere)
- `child_database` linked-database views (Work History, Weekly History, Monthly Metrics)
- `child_page` references (inner detail page link)
- `toggle` blocks (Debug View at page bottom)

These do NOT round-trip through markdown. Any range-replace or block-deletion
operation that touches them risks **permanent data loss** (one such loss
already happened — recovered via Notion page-history restore).

Before doing ANY 4d section refresh:

1. Read the target page's children via `notion-fetch`.
2. For the section you intend to update, identify the bounded range:
   - Section START = the H2 block matching the heading prefix
   - Section END = the LAST narrative block (paragraph / callout / table / list / heading_3 / divider) before:
     - the next H2 block, OR
     - the first non-narrative block (`column_list`, `child_*`, `embed`, `image`, `video`, `toggle`, `synced_block`)
   - **WHICHEVER COMES FIRST.**
3. If the proposed `old_str` (or block range) would include any non-narrative
   block, **abort 4d for that section** and emit:

   > ⚠️ 4d skipped for section `<heading>` — section boundary touches non-markdown blocks (column_list / toggle / embedded DB). Manual UI update required.

This is non-negotiable. The skill does NOT have logic to faithfully serialize
non-markdown blocks; touching them WILL lose user content.

---

### Step 4: Generate Table & Write to Notion

#### 4a. Display table

**Output format:**

```markdown
# 每日工作摘要 — YYYY-MM-DD (星期X)
**N tickets | N PRs shipped | N reviews | N bug fixes | N PRD/specs | N projects**

| Ticket | 時段 | 工作摘要 | Output | 費用 |
|--------|------|----------|--------|------|
| XXX-### | HH:MM-HH:MM | <one-phrase summary> | feature dev | XX.XX |
| **合計** | **HH:MM-HH:MM** | **N items** | | **XXX.XX** |
```

**Table rules:**
- Day of week: run `date -j -f '%Y-%m-%d' 'YYYY-MM-DD' '+%u'` to get ISO weekday (1=Mon). Map: 1→一, 2→二, 3→三, 4→四, 5→五, 6→六, 7→日. Do NOT calculate manually.
- 「工作摘要」: synthesize into ONE short phrase, not raw messages
- Sort by first_time ascending (routine rows at top since "routine 03:00" sorts first)
- Last row = 合計 (only sum local sessions for 時段, exclude routine from time range)
- **Demo mode:** remove 工作摘要 column from display, but still write it to Notion.

**KPI line:** Count unique `[A-Z]{2,}-\d+` tickets, count rows per Output type (only >0), count unique project paths.

#### 4b. Write to Notion (one row per day, always CREATE)

**Each parse.py row → one new Notion page.** No search, no upsert, no
accumulation across days. Running `/daily-summary` for the same date twice
will produce duplicate rows — that's an operator concern, not a code path.

**Work Items data source ID:** read from `user_config.notion.work_items_db_id`. (Notion title: `Work History` — historical naming inconsistency, do not rename in Notion.)

**Schema (confirmed 2026-05-01):**
| Property | Type | Notes |
|----------|------|-------|
| `工作摘要` | title | required |
| `Ticket` | multi_select | options auto-extended for new ticket IDs (CAF-XXX, CET-XXX, etc.) |
| `Linear` | url | `https://linear.app/gogox/issue/<ticket>` (only for Linear-validated tickets; null for Jira-only tickets) |
| `Date` | date | use `date:Date:start` / `date:Date:end` |
| `時段` | rich_text | `"HH:MM-HH:MM"` |
| `Sessions` | number | int |
| `費用` | number (dollar) | float, nullable |
| `Tokens` | number | int |
| `Active Hours` | number | `ai_sec / 3600` — AI active time in hours |
| `Thinking Hours` | number | `think_sec / 3600` — extended thinking time in hours |
| `Subagent Spawns` | number | `subagent_spawns` — count of subagent processes spawned |
| `Cost Opus` | number (dollar) | `cost_by_model.get("opus", 0)` — per-model cost breakdown |
| `Cost Sonnet` | number (dollar) | `cost_by_model.get("sonnet", 0)` — per-model cost breakdown |
| `Cost Haiku` | number (dollar) | `cost_by_model.get("haiku", 0)` — per-model cost breakdown |
| `Cache Read Tokens` | number | `tokens_by_kind["cache_read"]` |
| `Cache Creation Tokens` | number | `tokens_by_kind["cache_creation_5m"] + tokens_by_kind["cache_creation_1h"]` |
| `Output` | select | one of: `PR shipped`, `feature dev`, `bug fix`, `code review`, `code review (routine)`, `PRD/spec`, `tooling`, `research`, `devops` |
| `Week` | formula (readonly) | auto |

**Pre-write: register new tickets.** Collect all unique `[A-Z]{2,}-\d+` tickets from this run (e.g. CAF-355, CET-8360). If any are NOT in existing Ticket multi_select options, run `mcp__notion__notion-update-data-source`:
```
ALTER COLUMN "Ticket" SET MULTI_SELECT('existing1':blue, ..., 'NEW_TICKET':blue)
```

**Write logic per row:**

For **every** row in `rows` (ticket and non-ticket alike), call
`mcp__notion__notion-create-pages` once with the property mapping below.
No search step. No update path. No body content — title + properties are
self-describing.

If the same ticket appears in multiple rows on the same day (multiple
sessions, or dispatcher + worker rows), each row produces its own Notion
page. That is the intended behavior — the Notion-side detail accurately
reflects the per-session cadence.

**Properties for CREATE (uniform schema for ticket and non-ticket rows):**

| Property | How to set |
|----------|------------|
| `工作摘要` | Ticket row: `{TICKET-ID}: [summary phrase]` (e.g. `CAF-355: ...` or `CET-8360: ...`). Non-ticket row: `[summary phrase]` (no prefix). |
| `Ticket` | JSON array of ALL `[A-Z]{2,}-\d+` from this row; `[]` for non-ticket rows. |
| `Linear` | `https://linear.app/gogox/issue/{first ticket}` for Linear-validated ticket rows; `null` for Jira-only or non-ticket rows. If the ticket was validated via Jira (not Linear), set to `null`. For cwd/branch-derived tickets (not API-validated), generate the Linear URL optimistically. |
| `date:Date:start` | TARGET_DATE (YYYY-MM-DD). |
| `date:Date:end` | **`null`** — never set. Each row is fixed to a single day. |
| `時段` | `"HH:MM-HH:MM"` from this row's `first_time`/`last_time`. Multiple sessions on the same ticket each carry their own time range — do not merge. |
| `Sessions` | `session_count` |
| `費用` | `cost_total` (null for routine rows from Step 3.5) |
| `Tokens` | `tokens_total` (null for routine rows) |
| `Active Hours` | `ai_sec / 3600` |
| `Thinking Hours` | `think_sec / 3600` |
| `Subagent Spawns` | `subagent_spawns` |
| `Cost Opus` | `cost_by_model.get("opus", 0)` (omit for routine rows with null cost) |
| `Cost Sonnet` | `cost_by_model.get("sonnet", 0)` (omit for routine rows with null cost) |
| `Cost Haiku` | `cost_by_model.get("haiku", 0)` (omit for routine rows with null cost) |
| `Cache Read Tokens` | `tokens_by_kind["cache_read"]` (omit for routine rows with null tokens) |
| `Cache Creation Tokens` | `tokens_by_kind["cache_creation_5m"] + tokens_by_kind["cache_creation_1h"]` (omit for routine rows with null tokens) |
| `Output` | `suggested_output` (use the Step 3 sanity-checked value as-is — no override across rows). |

Do not write any body content. Title + properties are sufficient.

**Headless variant (REST + `ntn_` token) — use this when `mcp__claude_ai_Notion__*` is ABSENT.**

Same per-row data, written via `notion_rest.py` instead of the OAuth tool. No
pre-register-tickets step is needed — the REST API auto-creates `Ticket`
multi_select options for unseen IDs.

1. Build a rows file. After Step 3 (summaries synthesized, tickets sanity-checked
   / API-validated), write the finalized rows to `/tmp/daily-summary-rows.json` as
   a JSON **array**, one object per `rows` entry, using this field mapping:

   | helper field | value (from the row) |
   |--------------|----------------------|
   | `title` | the `工作摘要` string — ticket-prefixed for ticket rows (`CET-8382: …`), bare for non-ticket rows |
   | `tickets` | array of ALL `[A-Z]{2,}-\d+` for this row (`[]` if none) — same list 4b's `Ticket` uses |
   | `linear` | `https://linear.app/gogox/issue/{first ticket}` for Linear-validated rows; `null` for Jira-only / non-ticket / unknown |
   | `date` | TARGET_DATE (`YYYY-MM-DD`) |
   | `time_range` | `"HH:MM-HH:MM"` |
   | `sessions` | `session_count` (`null` for routine rows) |
   | `cost` | `cost_total` (`null` for routine rows) |
   | `tokens` | `tokens_total` (`null` for routine rows) |
   | `active_hours` | `ai_sec / 3600` |
   | `thinking_hours` | `think_sec / 3600` |
   | `subagent_spawns` | `subagent_spawns` |
   | `cost_opus` / `cost_sonnet` / `cost_haiku` | `cost_by_model.get(...)` (`null` → omitted) |
   | `cache_read` | `tokens_by_kind["cache_read"]` (`null` → omitted) |
   | `cache_creation` | `cache_creation_5m + cache_creation_1h` (`null` → omitted) |
   | `output` | `suggested_output` (Step-3 sanity-checked value) |

2. Create the pages:

   ```bash
   python3 ~/.claude/skills/daily-summary/notion_rest.py create-work-items /tmp/daily-summary-rows.json
   ```

   The helper prints one line per row (`OK <url>` / `FAIL HTTP <code>`) and a
   final tally, exiting non-zero if any row failed. `--dry-run` validates the
   payload without writing. Like the OAuth path, this is always CREATE — no
   upsert, no dedupe; re-running the same date duplicates rows.

#### 4d. Refresh work record top sections (Phase 2 dashboard)

> **Headless path (launchd):** if `mcp__claude_ai_Notion__*` is absent, do NOT
> skip 4d. Build a `<dashboard-payload.json>` (see **"4d Headless variant"** at
> the end of this 4d block) carrying every number + zh-TW AI text, then run
> `notion_rest.py refresh-dashboard <payload> --dry-run` (confirm the plan) and
> the real call. The helper handles section-replace + chart-boundary safety +
> DB upsert + stale-id self-heal. The interactive OAuth-Markdown path below is
> still used when those tools ARE loaded.

After writing Work Items, regenerate two H2 sections on the parent page plus one inner detail page:
**📊 本週交付** (KPI table + conditional ⚠️ action callout + inner-page link),
**📈 12 週趨勢** (rolling monthly trend),
plus inner page **📋 本週明細 & 歷史** (Work Items chart views + Weekly PRs database + Weekly Metrics database).
The Debug View toggle at the bottom of the parent page already exists — leave it alone.

**Parent page ID:** read from `user_config.notion.parent_page_id`.

**Inputs (from `parse.py` JSON, schema 1.2):**
- `prs_opened`, `prs_merged`, `prs_in_progress` (ints; null → 0)
- `prs_this_week_list` — `{number, title, state, created_date, merged_date, url, repository, ticket_caf}`
- `tickets_closed_today`, `tickets_reopened_today`
- `linear_tickets_this_week` — `{identifier, title, state_name, state_type, completedAt}`
- `weekly_aggregates` (12 entries, oldest first; each: `prs_merged`, `prs_opened`, `tickets_closed`, `tickets_reopened`, `total_cost`, `cost_per_ticket`, `net_delivery_efficiency`, `sessions`, `cache_hit_rate`, `output_per_turn`, `week_label`, `week_start_date`, `wow_delta.{field}_abs`)
- `daily_stats` (today's totals)

**Shared patterns (referenced throughout 4d):**

**[Locate-or-create]** — resolve a page/database ID before first use:
1. Read the ID from `user_config.notion.<key>` (where `<key>` is one of `parent_page_id`, `inner_page_id`, `work_items_db_id`, `weekly_prs_db_id`, `weekly_metrics_db_id`). Non-empty → use directly (if `notion-fetch` 404s, treat as empty and fall through — the page was likely renamed/deleted).
2. Search parent/inner page's children for the target title (defaults from `notion-schema.json`; users may have customized).
3. Not found → create via `notion-create-pages` or `notion-create-database` using the schema in `notion-schema.json`.
4. Persist: rewrite `~/.claude/daily-summary-config.json` with the resolved ID merged in (keep other notion keys + `_comment` intact). Do NOT edit this SKILL.md file.

**[Delta format]** — for `wow_delta` fields: `↑+N` if positive, `↓-N` if negative, `—` if None or zero. For cost fields, prefix the number with `$` (e.g., `↓-$8.20`).

**The 4-step flow:**

##### 4d-2. Rewrite "📊 本週交付" section (parent page)

Three-layer layout: **(1)** AI 草稿 callout (LLM-generated 1–2 sentence
summary), **(2)** two hero KPI cards (NDE + Cost/Ticket), **(3)** a 6-row
detail table with `vs 上週` and `vs 12週均` comparison columns.

Build markdown bottom-up:

- **Heading:** base format `## 📊 本週交付 (W{iso_week:02d} · {Mon MMM DD} ~ {Sun MMM DD})`,
  where Mon/Sun derived from ISO week of TARGET. **Partial-week suffix:**
  if TARGET is a weekday (Mon–Fri), append ` · Day {N}/5 進行中` where
  `N` = ISO weekday of TARGET (Mon=1 … Fri=5). On Sat/Sun, omit the suffix
  entirely — the week is "complete" for display purposes. Examples:
  - Tue → `## 📊 本週交付 (W18 · May 04 ~ May 10 · Day 2/5 進行中)`
  - Sat → `## 📊 本週交付 (W18 · May 04 ~ May 10)`

- **AI 草稿 callout** (icon `🧠`, color `gray_background`) — LLM-generated
  1–2 sentence zh-TW summary of this week's delivery. Inputs: all 6 metrics
  from `weekly_aggregates[-1]`, `wow_delta`, and `prs_in_progress`. Highlight
  the biggest change vs last week and one actionable observation. Prefix with
  `[AI草稿]`. Example:

  > 🧠 [AI草稿] 本週合併 6 個 PR（↓7），關閉 19 張 ticket，NDE 0.9 較上週
  > 改善但仍偏低；每張成本 $108 較上週大幅下降，5 個 reopened ticket 待追蹤。

- **Hero KPI** — a callout (icon `📊`, color `blue_background`) showing the
  two north-star metrics side by side as bold text:

  > 📊 **淨交付效率 (NDE)** {nde:.1f} tickets/$100 　 **Cost/Ticket** ${cpt:.0f}

  If either value is null, render `—` in place of the number.

- **Detail table** — 6-row markdown table, columns `指標 | 本週 | vs 上週 | vs 12週均`.

  **12-week average computation:** For each metric, compute the arithmetic
  mean across all entries in `weekly_aggregates` (up to 12). Skip null entries
  in the average. Format as `(均 {avg:.1f})` for decimal metrics, `(均 ${avg:.0f})`
  for dollar metrics, `(均 {avg:.0f})` for integer metrics. If fewer than 2
  non-null entries exist → `—`.

  All current-week values from `weekly_aggregates[-1]`. `vs 上週` uses
  `wow_delta` with **[Delta format]**. `Open` uses top-level `prs_in_progress`
  (no historical data — `vs 上週` and `vs 12週均` are both `—`).

  | # | 指標 | 本週 source | 本週 format | vs 上週 source | vs 12週均 |
  |---|------|------------|------------|---------------|----------|
  | 1 | NDE | `net_delivery_efficiency` | `{nde:.1f}` | `wow_delta.net_delivery_efficiency_abs` | mean of `net_delivery_efficiency` |
  | 2 | Cost/Ticket | `cost_per_ticket` | `${cpt:.0f}` | `wow_delta.cost_per_ticket_abs` (prefix `$`) | mean of `cost_per_ticket` (prefix `$`) |
  | 3 | Merged | `prs_merged` | integer | `wow_delta.prs_merged_abs` | mean of `prs_merged` |
  | 4 | Open | `prs_in_progress` (top-level) | integer | `—` | `—` |
  | 5 | Tickets Closed | `tickets_closed` | integer | `wow_delta.tickets_closed_abs` | mean of `tickets_closed` |
  | 6 | Reopened | `tickets_reopened` | integer | `wow_delta.tickets_reopened_abs` | mean of `tickets_reopened` |

  Rendered template:

  ```
  | 指標 | 本週 | vs 上週 | vs 12週均 |
  |------|------|---------|----------|
  | NDE | {nde:.1f} | {Δ_nde} | (均 {avg_nde:.1f}) |
  | Cost/Ticket | ${cpt:.0f} | {Δ_cpt} | (均 ${avg_cpt:.0f}) |
  | Merged | {merged} | {Δ_merged} | (均 {avg_merged:.1f}) |
  | Open | {open} | — | — |
  | Tickets Closed | {closed} | {Δ_closed} | (均 {avg_closed:.1f}) |
  | Reopened | {reopened} | {Δ_reopened} | (均 {avg_reopened:.1f}) |
  ```

- **Inner-page link** — last block in section. A paragraph with link
  `→ 本週明細 & 歷史` pointing to the inner page (see 4d-3 for ID resolution).

**Block order in 📊 section:** H2 heading → AI 草稿 callout → Hero KPI callout → Detail table → inner-page link.

**Edge cases:**
- `net_delivery_efficiency = null` (e.g. `tickets_closed = 0`): 本週=`—`, vs 上週=`—`, vs 12週均 still computed from other weeks.
- `cost_per_ticket = null`: same treatment.
- `wow_delta.{field}_abs = None` (first week / Monday with no prior week): vs 上週=`—`.
- `weekly_aggregates` has only 1 entry (first run): all vs 上週=`—`, vs 12週均=`—` (fewer than 2 entries).
- `prs_in_progress = null`: 本週=`—`, vs 上週=`—`, vs 12週均=`—`.
- `prs_merged` / `tickets_closed = null`: 本週=`—`, vs 上週=`—`.

Replace the parent page section between `## 📊 本週交付` and the next `## `
heading via `mcp__notion__notion-update-page` with `command=update_content`,
`old_str` = whole current 📊 section, `new_str` = the markdown built above.

##### 4d-3. Rewrite inner page "📋 本週明細 & 歷史"

Inner page is a child of the parent, dedicated to weekly detail + history.

**Locate the inner page** via **[Locate-or-create]**: config key = `user_config.notion.inner_page_id`, title = `📋 本週明細 & 歷史` (if found as legacy `📋 PR 明細`, rename it first), create under `user_config.notion.parent_page_id`. Persist the resolved ID back to the config.

**Each run:** Sections 2 and 3 are rewritten via `update_content`. Section 1
(Work Items chart views) requires one-time manual recreation (see 4d-4). The two databases (Weekly PRs, Weekly Metrics) persist across runs — only their rows are upserted.

**Section A — Weekly PRs database** (heading `## 📋 本週 PRs`)

PR data lives in the **Weekly PRs** Notion database (config key =
`user_config.notion.weekly_prs_db_id`). Each PR is one row, keyed by `Title`
(PR title serves as upsert key — search by title substring match). Old weeks'
rows are never deleted — they accumulate, giving historical PR visibility.
Use `Week` select to filter by week in the Notion UI.

**Locate the database** via **[Locate-or-create]**: config key =
`user_config.notion.weekly_prs_db_id`, title = `Weekly PRs` (or whatever the
user renamed it to in Notion — title is doc; the config ID is the truth),
create under inner page if not found (schema from `notion-schema.json:weekly_prs_db`,
title-property = `Title`). Persist the resolved ID to the config.

**Schema (database properties):**

| Property | Type | Notes |
|----------|------|-------|
| `Title` | title | PR title, truncated to 80 chars; upsert key |
| `Week` | select | e.g. `W18` — options for last 12 weeks |
| `Repo` | select | `gogox-client-flutter`, `gogovan-client-v2-android`, `gogovan-driver-flutter` |
| `State` | select | `merged` / `open` / `closed` |
| `Opened` | date | `created_date` |
| `Merged` | date | `merged_date` (omit if null/empty) |
| `Ticket` | rich_text | Hyperlink: `[CAF-XXX](pr_url)` or `[CET-XXX](pr_url)`. Ticket ID from PR title/branch, linked to GitHub PR URL. If no ticket, use `[#number](pr_url)` |

**Upsert logic per PR** (loop over `prs_this_week_list`):

For each PR `p`:
1. Search the database for a row whose title matches `p.title` (truncated).
2. If found → `mcp__notion__notion-update-page` with the new property values
   (state may have changed from open to merged, merged_date may be new).
3. If not found → `mcp__notion__notion-create-pages` with full row.

Property mapping:
- `Title` = `p.title` (truncate to 80 chars)
- `Week` = the ISO week label (e.g. `W18` for this data)
- `Repo` = last segment of `p.repository` (split on `/`, take `[-1]`)
- `State` = `p.state`
- `date:Opened:start` = `p.created_date`
- `date:Merged:start` = `p.merged_date` (omit if null/empty)
- `Ticket` = `[{p.ticket_caf}]({p.url})` if ticket exists, else `[#{p.number}]({p.url})`

**Do NOT delete old weeks' PRs.** They accumulate across runs.

**Section B — Weekly Metrics database** (heading `## 📊 12 週指標`)

The 12-week history is stored in a Notion database (not a markdown table) so
the user can sort/filter/add views in Notion's UI. Each week is one row, with
`Week` as the title (primary key). Each `/daily-summary` run upserts: the
current week's row is updated, and any week not yet in the database is created.

**Schema (database properties):**

| Property | Type | Notes |
|----------|------|-------|
| `Week` | title | e.g. `W18`; primary key for upsert |
| `Week Start` | date | `week_start_date` (YYYY-MM-DD) |
| `Merged` | number | `prs_merged` |
| `Opened` | number | `prs_opened` |
| `Closed` | number | `tickets_closed` |
| `Reopened` | number | `tickets_reopened` |
| `Review Pressure` | number | `pr_review_pressure` |
| `Sessions/PR` | number (decimal) | `sessions_per_pr` |
| `Ticket-to-PR` | number (decimal) | `ticket_to_pr_ratio` |
| `Net Delivery Eff` | number (decimal) | `net_delivery_efficiency` (tickets per $100) |
| `Cost` | number (dollar) | `total_cost` |
| `Cost/Ticket` | number (dollar) | `cost_per_ticket` |
| `Cost/Net Line` | number (dollar) | `cost_per_net_line`; null → empty |
| `Cache %` | number (percent) | `cache_hit_rate` as-is (0-1 fraction; Notion percent displays ×100) |
| `Tool Error %` | number (percent) | `tool_error_rate_pct / 100` (parse.py outputs 0-100; Notion needs 0-1) |
| `Late Night %` | number (percent) | `late_night_cost_pct / 100` (parse.py outputs 0-100; Notion needs 0-1) |
| `Lost Work $` | number (dollar) | `lost_work_cost` |
| `Output/Turn` | number | `output_per_turn`; null → empty |
| `Sessions` | number | `sessions` |
| `Health` | select | one of `🟢 green`, `🟡 yellow`, `🔴 red` (from `row_health`) |
| `AI 建議` | rich_text | per-row LLM commentary; see "AI 建議 generation" below |

**Locate the database** via **[Locate-or-create]**: config key = `user_config.notion.weekly_metrics_db_id`, title = `Weekly Metrics`, create under inner page if not found (schema from `notion-schema.json:weekly_metrics_db`, title-property = `Week`). Persist the resolved ID to the config.

**Upsert per week** (loop over `weekly_aggregates`, oldest first):

For each entry `e`:
1. Search the database for a row whose title equals `e.week_label`.
2. If found → `mcp__notion__notion-update-page` with the new property values.
3. If not found → `mcp__notion__notion-create-pages` with full row.

Property mapping notes:
- `Cost/Net Line`: pass raw USD value (e.g. `0.18`); Notion's dollar formatter
  handles the display. Don't pre-convert to cents — the database stores USD
  for consistency with `Cost` and `Cost/Ticket`.
- `Cache %`: write `cache_hit_rate` as-is (already 0-1; Notion percent type
  stores 0-1 and displays as 0-100%).
- `Tool Error %` / `Late Night %`: divide by 100 before writing (parse.py
  outputs 0-100, but Notion percent type needs 0-1).
- `Health`: write `🟢 green` / `🟡 yellow` / `🔴 red` (with leading emoji) so
  Notion select swatches render the right colour.
- nulls (`cost_per_net_line`, `output_per_turn`, `sessions_per_pr`,
  `ticket_to_pr_ratio`): omit the property in the JSON, don't send `0`.

**AI 建議 generation (per-row commentary)**

Each row carries 2-3 sentences of zh-TW LLM-generated commentary. The
generation policy preserves history — old rows keep their original commentary
so the user can browse a real time-series of AI takes, not just the latest.

Decision per row (executed during the upsert loop):

| Row state | Is current week? | Action |
|-----------|------------------|--------|
| `AI 建議` is empty / missing | yes | generate fresh, write |
| `AI 建議` is empty / missing | no  | **backfill**: generate, write |
| `AI 建議` already has text | yes | regenerate, **overwrite** |
| `AI 建議` already has text | no  | **skip**, preserve existing text |

This means first run fills all 12 weeks (12 LLM calls — slower); subsequent
runs only regenerate the current week (1 call).

**Prompts (zh-TW, output without "[AI解讀] " prefix — the column name itself
labels it):**

For the *current* week (compares vs prior week + 12w median):
```
針對本週（{week_label}）的指標，用 2-3 句繁中總結：
1) 相對上一週的最大變化（用 wow_delta._abs 直接挑變化最大的 1-2 個指標）
2) 是否健康（參考 row_health 與超出 median 的指標）
3) 一句輕量建議（可選）
資料：{this_week_entry, prev_week_entry, 12w_medians}
要求：客觀、不誇張、不重複數字、不加 emoji、不加前綴。
```

For *historical* weeks (no prev-week comparison; vs 12w median only):
```
針對 {week_label} 那一週的指標，用 2-3 句繁中描述：
1) 該週相對 12w median 最突出的 1-2 個指標
2) row_health 為何該值
3) 如果某些指標當週缺值（如早期 sessions_per_pr / tool_error_rate），略過不評論
資料：{this_week_entry, 12w_medians}
要求：客觀、不誇張、不重複數字、不加 emoji、不加前綴。
```

Fallback if generation fails for any row: write
`(AI 建議生成失敗，將於下次 run 重試)`. The skip-if-non-empty rule treats
that fallback string as "non-empty" for current-week regeneration but the
explicit retry rule says: if you see that fallback string in a historical
row's `AI 建議`, treat it as empty and re-generate this run.

**Inner page body (in order):**
1. `## 📋 Daily Stats` — **MANUAL RECREATION REQUIRED** — charts must be rebuilt in Notion UI pointing to Work Items (see 4d-4)
2. `## 📋 本週 PRs` — inline-database embed pointing to `Weekly PRs` (Section A database; no markdown table)
3. `## 📊 12 週指標` — inline-database embed pointing to `Weekly Metrics` (Section B database; no markdown table)

Each run: upsert rows into the two databases (Weekly PRs via Section A logic, Weekly Metrics via Section B logic). The heading blocks and database embeds on the inner page persist — no content rewrite needed for these sections after initial setup.

##### 4d-4. "📋 Daily Stats" section — MANUAL RECREATION REQUIRED (lives on INNER page)

The Daily Stats database has been eliminated. The old linked-database chart
views (Cost / Tokens / Sessions / Items) that pointed to Daily Stats no
longer have a data source. They must be manually recreated in the Notion UI
to point to the **Work Items** database (`user_config.notion.work_items_db_id`)
instead.

**One-time manual steps:**
1. Open the inner page (`📋 本週明細 & 歷史`) in Notion.
2. Delete the old Daily Stats linked-database chart views.
3. Create new linked-database views pointing to Work Items, with chart
   views that SUM `費用`, `Tokens`, `Sessions` grouped by `Date`.
4. With the one-row-per-day model (4b), every Work Item has a single fixed
   `Date`, so Date-based aggregation is now correct directly on Work Items.
   The new columns `Cost Opus`, `Cost Sonnet`, `Cost Haiku`, `Cache Read Tokens`,
   and `Cache Creation Tokens` are available for additional chart breakdowns.

**Location:** inner page (`📋 本週明細 & 歷史`), first content block, above `## 📋 本週 PRs`. The parent page must NOT contain a `## 📋 Daily Stats` heading.

During 4d-3's inner-page rewrite: skip this section entirely — only `## 📋 本週 PRs` and `## 📊 12 週指標` are rewritten.

##### 4d-5. Rewrite "📈 12 週趨勢" section (parent page)

Build **calendar month** buckets from two data sources:

**Total Cost — from `historical_averages.daily_series` (precise daily cut):**
1. For each entry in `daily_series`, extract `date[:7]` (YYYY-MM).
2. Group by YYYY-MM, sum `cost` per group.

**PRs / Tickets — from `weekly_aggregates` (weekly granularity):**
1. For each entry, compute Thursday = `week_start_date` + 3 days.
2. Attribute the week to Thursday's calendar month.
3. Sum `prs_merged`, `tickets_closed` per month.

(PRs/tickets are event-based with specific dates, but daily_series
doesn't carry them yet. Thursday rule is a close approximation for
these small counts — the main cost column is exact.)

**Combine and render:**

For each calendar month:
- `total_cost` = from daily_series (exact)
- `prs_merged` = from weekly_aggregates (Thursday rule)
- `tickets_closed` = from weekly_aggregates (Thursday rule)
- `cost_per_ticket` = `total_cost / tickets_closed` (or `—` if 0)
- `month_label` = `Mon YYYY` (e.g. `Mar 2026`)

Render markdown table:

```
| 月區間 | PRs Merged | Tickets Closed | Cost/Ticket |
|-------|------------|----------------|-------------|
| {label1} | {sum} | {sum} | ${cpt or "—"} |
...
```

**Bold the current (last) row's cells** by wrapping each in `**…**` to mark
the in-progress bucket.

Replace the section between `## 📈 12 週趨勢` and the next `## ` heading via
`update_content`.

##### 4d-6. Verify Debug View, log completion

Fetch the parent page once more and confirm the bottom toggle `🔧 Debug View`
still has its 6 callouts. Don't modify it. Then print to stdout:

```
Updated work record (page_id={user_config.notion.parent_page_id})
  📊 本週交付: AI 草稿 + hero KPI + detail table (6 rows) + inner-page link
  📋 本週明細 & 歷史 (inner): Weekly PRs DB ({P} rows upserted) + Weekly Metrics DB
    Weekly Metrics: {created/upserted} 12 rows; AI 建議 generated for {M} rows
    (M=1 on steady-state run, M=12 on first run / backfill)
  📋 Daily Stats: ELIMINATED — charts require one-time manual recreation (see 4d-4)
  📈 12 週趨勢: {B} calendar-month buckets rendered (current bolded)
```

**Failure handling:** Each step wrapped in try/except. Step ordering
(4d-2 → 4d-3 → 4d-4 → 4d-5) is independent — a failure in one does
not skip the rest. If all UI rewrites fail, log error and exit 0 — the
Work Items write (4b) already succeeded and is the primary deliverable;
section refresh is best-effort.

##### 4d Headless variant (REST + `ntn_` token) — use when `mcp__claude_ai_Notion__*` is ABSENT

The interactive steps above use the OAuth Markdown tools' `update_content`
(markdown in, section-replace handled for you). Headless, you instead **build a
single `dashboard-payload.json`** with the SAME computed content and hand it to
the REST helper, which does the block-level plumbing. You (the LLM) still own
every number and every zh-TW string (AI 草稿, AI 建議) — the helper has no LLM.

Map the interactive sections onto the payload (full schema + block DSL in
`notion_rest.py`'s module docstring — read it before building):

- **4d-2 (📊 本週交付)** → one `sections[]` entry. `locate_prefix = "📊 本週交付"`,
  `heading_text` = the full computed H2 (with the `W{NN} · … · Day N/5` suffix
  rules from 4d-2). `blocks` in order: AI 草稿 `callout` (emoji `🧠`,
  `gray_background`), Hero KPI `callout` (emoji `📊`, `blue_background`, bold
  numbers via `rich` segments with `"b": true`), the 6-row detail `table`, and
  the inner-page `link`.
- **4d-5 (📈 12 週趨勢)** → one `sections[]` entry. `locate_prefix = "📈 12 週趨勢"`,
  `heading_text = "📈 12 週趨勢"`, `blocks` = the month-bucket `table` (bold the
  current row's cells by wrapping each cell string in `**…**`).
- **4d-3 (Weekly PRs / Weekly Metrics)** → two `databases[]` entries. Pass the
  configured id plus `db_title` + `config_key` so a stale id self-heals by title
  search. `key = "Title"` for Weekly PRs, `key = "Week"` for Weekly Metrics.
  Build `rows[]` exactly per the 4d-3 Section A / Section B property mappings
  (the helper coerces each value to the live schema's type; pass `Ticket` on
  Weekly PRs as `{"text": "CAF-XXX", "url": "<pr_url>"}` for the hyperlink). The
  helper upserts (match by key → update, else create); old weeks accumulate.
- **4d-4 (Daily Stats)** → omit. Still manual-recreation-only.

Then:

```bash
python3 ~/.claude/skills/daily-summary/notion_rest.py refresh-dashboard /tmp/dashboard-payload.json --dry-run
# confirm: 📈 plan shows it deletes ONLY [table, paragraph] (never column_list); DBs resolve
python3 ~/.claude/skills/daily-summary/notion_rest.py refresh-dashboard /tmp/dashboard-payload.json
```

`refresh-dashboard` is best-effort and always exits 0 (4b is the primary
deliverable). The `## 📋 本週 PRs` / `## 📊 12 週指標` heading + DB-embed blocks
on the inner page persist untouched — only their DB rows are upserted, so the
inner page needs no section-replace headless.

### Step 5: Edge Cases

- **No sessions:** `在 YYYY-MM-DD 沒有找到任何 Claude Code session。` Skip all Work Items CREATE calls; proceed directly to 4d dashboard refresh.
- **Notion MCP not connected:** Skip Notion write, output: `Notion MCP 未連線，跳過寫入。`
- **Re-running for the same date:** Each row is always CREATE, so re-running `/daily-summary YYYY-MM-DD` will produce duplicate Notion pages. There is no in-skill dedupe — if you need to re-run, delete the previous day's rows manually first. (This is a deliberate trade-off for the one-row-per-day model.)
- **Multiple rows for the same ticket on the same day:** Expected; each session row CREATEs its own page.
- **Historical Work Items pages:** Rows written before the 2026-05-01 switch to one-row-per-day still carry merged date ranges and accumulated values. They are left as-is — no backfill or migration. From the switch date forward, every new row is single-day.
- **Active Hours / Thinking Hours / Subagent Spawns:** Computed from each row's `ai_sec`, `think_sec`, and `subagent_spawns` fields. Written to Work Items.

### Appendix: Source migration notes (2026-04-26)

Old behavior read `~/.claude/metrics/session_metrics.csv`, written by `session_metrics.py` Stop hook. Hook only ran in repos with `.claude/settings.json` configured (work_project), so vault / Desktop / subagent sessions were missed. CSV stopped writing on 2026-04-24 23:08 HKT for unknown reasons. Hook also priced 1h ephemeral cache writes at 5m rates, under-counting cost ~5–10%.

New behavior reads transcripts directly. Validation against last 6 hook-recorded days shows token counts match within 0.3% (when session was not resumed) and costs are 4–8% higher than hook (correct 1h cache pricing).

`session_metrics.csv` is no longer consulted but is kept on disk as historical reference. The Stop hook can be left in place or removed — its output is now unused.
