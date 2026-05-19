---
name: session-metrics
description: Collect and report metrics for the current Claude Code session — cost, cache hit rate, turns, and (optionally) compare AI time vs a story-point estimate. Generates an AI summary and posts the report to the active Linear ticket. Use when the user says "session metrics", "session report", "session cost", or asks to wrap up a session with metrics.
allowed-tools:
  - Bash
  - Read
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

### Step 2: Ask for story point estimate (only if not already stored)

Ask the user:

> If this ticket were done purely by hand (no AI), how many **story points** would you estimate?
>
> | SP | Estimated Time |
> |---:|----------------|
> | 1 | 1–2 hours |
> | 2 | Half a day |
> | 3 | 1 day |
> | 5 | 2 days |
> | 8 | 3 days |
> | 13 | 5 days |

Store their answer as `SP_VALUE` (must be one of: 1, 2, 3, 5, 8, 13). If they give an invalid number, re-ask once. If they skip or say "no", set `SP_ARG` to empty string and proceed without it.

If they gave a valid answer, set `SP_ARG` to `--story-points SP_VALUE`.

Then re-run Step 1 with `SP_ARG` appended to get updated JSON with `time_analysis`.

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
