---
name: session-metrics
description: Collect and report metrics for the current Claude Code session — cost, cache hit rate, turns, and (optionally) compare AI time vs a story-point estimate. Generates an AI summary and posts the report to the active Linear ticket. Use when the user says "session metrics", "session report", "session cost", or asks to wrap up a session with metrics.
allowed-tools:
  - Bash
  - Read
  - mcp__claude_ai_Linear__get_issue
---

# Session Metrics

Collect raw metrics from the current Claude Code session, optionally compare AI time against a story-point estimate, generate an AI summary, write CSV history, and post a report to the active Linear ticket.

> **Script location**: `~/.claude/skills/session-metrics/session_metrics.py` (installed via the gogox-claude symlink). The script reads only from `~/.claude/` (sessions, settings, CSV history) — it is project-agnostic.

**Arguments:** `$ARGUMENTS`

- (none): full flow — metrics + AI summary + post to Linear
- `--no-linear`: skip posting to Linear
- `--no-csv`: skip CSV output
- `--include-dispatcher` / `--no-include-dispatcher`: attribute /ggx-dispatcher subagent runs that targeted this ticket within the last 7 days. **Default: on** — covers the typical flow where the dispatcher ran in the main-repo session and the per-ticket work happened in a separate worktree session. The dispatcher's subagent JSONL is parsed in full, so its direct token usage AND any nested figma/verify/dev-agent work is included. **CSV is unaffected** (it stays a per-session ledger); the additions appear in the report and Linear comment only. Pass `--no-include-dispatcher` to disable.
- `--dispatcher-lookback-days N`: tune the lookback window (default 7).

## Steps

### Step 1: Collect raw metrics (first pass)

Run the script in JSON mode to check if story points already exist from a previous session:

```
python3 ~/.claude/skills/session-metrics/session_metrics.py --json --no-linear --no-csv $ARGUMENTS
```

Check the JSON output:

- If `time_analysis.story_points_from_history` is `true` → story points were auto-loaded from CSV. Set `SP_ARG` to `--story-points <value from time_analysis.story_points>`. Skip Step 2.
- If `time_analysis` exists but `story_points_from_history` is `false` → user already passed `--story-points`. Set `SP_ARG` accordingly. Skip Step 2.
- If `time_analysis` does not exist → no story points yet. Go to Step 2.

### Step 2: Estimate story points (LLM blind-estimate — only if not already stored)

Reached only when Step 1 found no stored story points. **Estimate them yourself — do NOT ask the user.**

**Blind-estimate rule (important):** base the estimate ONLY on the ticket's scope — its title, description, and acceptance criteria. Do **NOT** read the git diff, the changed files, or this session's work. The diff reflects how the AI solved it; anchoring the estimate on it makes the speed multiplier a self-graded number.

1. Fetch the ticket text with `mcp__claude_ai_Linear__get_issue` using the `ticket_id` from Step 1's JSON. If it can't be fetched, set `SP_ARG=""` and proceed to Step 3 without an estimate.
2. Judge: **how many hours would a normal-competence engineer take to implement this ticket entirely by hand, with no AI?** Calibration anchors:

   | ≈ hours | scope |
   |--------:|-------|
   | 1.5 | trivial — copy/config/flag tweak, obvious one-line fix |
   | 4   | small localized change, simple logic |
   | 8   | self-contained feature or non-trivial bug, needs tests |
   | 16  | feature spanning components, new UI + logic |
   | 24  | larger / cross-module, several integration points |
   | 40  | architectural change, broad refactor |

3. Emit your estimate as a **single JSON line and nothing else**, e.g.:
   `{"manual_hours": 8, "rationale": "self-contained skill+script change across 3 files"}`
   Estimate **human manual effort**, never AI time. Any positive hour value is fine — the script snaps it to the nearest story-point bucket.
4. Set `SP_ARG="--manual-hours <manual_hours>"` (the raw number from your JSON). Proceed to Step 3 — do NOT re-run Step 1.

### Step 3: Analyze and generate AI summary

Read the JSON output. Analyze the session data and write a concise English summary (3–5 bullet points) to a temp file:

```
SUMMARY_FILE=$(mktemp -t session-metrics-summary.XXXXXX)
```

Write the summary as markdown bullet points to `$SUMMARY_FILE` (no heading — the script adds `### Summary`). Focus on:

- **Cost efficiency**: cost per turn, total cost, which model drove most spend
- **Cache utilization**: cache hit rate and what it means
- **Agent ROI**: were agents cost-effective relative to their contribution
- **Prompt efficiency**: number of turns — fewer turns = better prompts
- **Time saved**: if `time_analysis` exists, highlight cumulative stats if available (total AI time across all sessions vs manual estimate). If only single session, show that session's comparison.
- **Actionable insight**: one concrete suggestion to improve next session

### Step 4: Generate report with summary and post

Run the script again with the summary file. This writes CSV and posts to Linear:

```
python3 ~/.claude/skills/session-metrics/session_metrics.py --summary-file "$SUMMARY_FILE" $ARGUMENTS $SP_ARG
```

### Step 5: Display the report output to the user

Show the Markdown report from Step 4 output. If errors appear on stderr, relay them.

### Step 6: Clean up

Delete `$SUMMARY_FILE`.
