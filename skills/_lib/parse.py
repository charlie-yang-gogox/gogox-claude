#!/usr/bin/env python3
"""
parse.py — shared transcript-scan CLI for gogox-claude skills

Vendored from gogox-claude-daily-summary/skills/shared/daily-summary/parse.py
and promoted to skills/_lib/ as the single canonical copy used by every skill
that needs to read Claude Code transcripts (currently monthly-summary; future
daily-summary, etc.). Keep this copy authoritative — do NOT re-vendor into
individual skill directories.

Scans Claude Code transcripts under ~/.claude/projects/*/*.jsonl, computes
per-session metrics, groups into rows (trivial filter -> dispatcher -> ticket
-> project), classifies, and emits JSON or human-readable text.

Algorithm preserved verbatim from the daily-summary SKILL.md transcript-mode
parser (post 2026-04-26 migration). Output schema: `schema_version: "1.0"`.

Usage:
  python3 parse.py --target-date 2026-04-28 --json
  python3 parse.py --range 2026-04-19:2026-04-28 --json
  python3 parse.py --month 2026-04 --json
  python3 parse.py                     # default: today (Asia/Hong_Kong), text output
"""

import argparse
import calendar
import json
import os
import re
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = "1.2"
USER_CONFIG_PATH = "~/.claude/daily-summary-config.json"
USER_CONFIG_SKELETON = {
    "_comment": "Personal IDs for /daily-summary. Auto-populated on first run. See skills/shared/daily-summary/SKILL.md `First-run bootstrap` for how the wizard fills these.",
    "notion": {
        "parent_page_id": "",
        "work_items_db_id": "",
        "inner_page_id": "",
        "weekly_prs_db_id": "",
        "weekly_metrics_db_id": "",
    },
}

# Asia/Hong_Kong is the cron's reference TZ (matches Asia/Taipei: both UTC+8)
HK_TZ = timezone(timedelta(hours=8))

TICKET_RE = re.compile(r"[A-Z]{2,}-\d+")
TRIVIAL = {"/mcp", "/daily-summary", "/session-metrics", ""}

# Pricing per 1M tokens (USD). cache_5m and cache_1h apply to ephemeral cache writes.
PRICING = {
    "opus":   {"input": 15.0, "output": 75.0, "cache_5m": 18.75, "cache_1h": 30.0, "cache_read": 1.50},
    "sonnet": {"input":  3.0, "output": 15.0, "cache_5m":  3.75, "cache_1h":  6.0, "cache_read": 0.30},
    "haiku":  {"input":  1.0, "output":  5.0, "cache_5m":  1.25, "cache_1h":  2.0, "cache_read": 0.10},
}

# Gap-based time accounting: gaps > 600s are treated as AFK and excluded
GAP_CAP = 600


def fam(model_str):
    """Infer model family from model name (case-insensitive substring match).

    Returns None for unknown models. Special model markers like '<synthetic>'
    (Claude Code's synthetic-response marker) also return None silently — these
    are not billable and the caller should not warn on them.
    """
    m = (model_str or "").lower()
    if "opus" in m: return "opus"
    if "sonnet" in m: return "sonnet"
    if "haiku" in m: return "haiku"
    return None


# Models that return None from fam() but are KNOWN to be non-billable; don't warn on them
KNOWN_NON_BILLABLE = {"<synthetic>"}


def turn_cost_split(usage, model_str):
    """Compute cost for a single assistant turn, returning (total, cache_5m_tokens, cache_1h_tokens).

    Returns: (cost_usd, c5_tokens, c1_tokens) so the caller can aggregate
    cache_creation breakdown for the spec's tokens_by_kind output.
    """
    f = fam(model_str)
    if not f:
        return 0.0, 0, 0
    p = PRICING[f]
    cc = usage.get("cache_creation") or {}
    c5 = cc.get("ephemeral_5m_input_tokens", 0) or 0
    c1 = cc.get("ephemeral_1h_input_tokens", 0) or 0
    if not (c5 or c1):
        # Legacy field: count all as 5m rate
        c5 = usage.get("cache_creation_input_tokens", 0) or 0
    cost = (
        (usage.get("input_tokens", 0) or 0) * p["input"]
        + (usage.get("output_tokens", 0) or 0) * p["output"]
        + c5 * p["cache_5m"]
        + c1 * p["cache_1h"]
        + (usage.get("cache_read_input_tokens", 0) or 0) * p["cache_read"]
    ) / 1_000_000
    return cost, c5, c1


def turn_tokens(usage):
    """Total tokens across input + output + cache_read + cache_creation."""
    return (
        (usage.get("input_tokens", 0) or 0)
        + (usage.get("output_tokens", 0) or 0)
        + (usage.get("cache_read_input_tokens", 0) or 0)
        + (usage.get("cache_creation_input_tokens", 0) or 0)
    )


def parse_ts(s):
    """Parse ISO 8601 timestamp; return aware datetime in local TZ. None on failure."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone()
    except (ValueError, AttributeError):
        return None


def _count_behavioral(content, slash_cmds, counters):
    """Count behavioral metrics from a single user message.

    Mutates `slash_cmds` (dict) and `counters` (dict with keys:
    short_corrections, short_confirms, cjk_chars, alpha_chars,
    figma_urls, interruptions) in place.
    """
    stripped = content.strip()

    # Slash commands (same logic as compute_behavioral_metrics)
    _slash_re = re.compile(r"(?:^|\s)(/[a-zA-Z][a-zA-Z0-9_:.-]*)")
    _path_prefixes = {"/Users", "/opt", "/tmp", "/var", "/etc", "/bin", "/usr",
                      "/dev", "/proc", "/sys", "/home", "/Library",
                      "/Applications", "/Volumes", "/System"}
    _xml_tag_re = re.compile(r"^/[a-z]+-[a-z]+")
    _api_nouns = {"/api", "/v1", "/v2", "/v3", "/graphql", "/rest", "/webhook",
                  "/orders", "/data", "/users", "/items", "/products", "/token",
                  "/callback", "/endpoints", "/routes", "/schemas", "/drivers",
                  "/payments", "/shipments", "/deliveries", "/tracking"}
    _http_method_re = re.compile(
        r"\b(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s*$", re.IGNORECASE)
    for m in _slash_re.finditer(content):
        cmd = m.group(1)
        if cmd in _path_prefixes:
            continue
        if _xml_tag_re.match(cmd) and "-" in cmd:
            continue
        end_pos = m.end()
        # Filter API paths: followed by / ? = & (part of a URL path/query)
        if end_pos < len(content) and content[end_pos] in "/?=&":
            continue
        # Filter known REST resource nouns
        if cmd.lower() in _api_nouns:
            continue
        # Filter if preceded by HTTP method (GET /foo, POST /bar)
        preceding = content[max(0, m.start() - 12):m.start()]
        if _http_method_re.search(preceding):
            continue
        slash_cmds[cmd] = slash_cmds.get(cmd, 0) + 1

    # Short corrections (< 30 chars, matching fix/error patterns)
    _correction_re = re.compile(
        r"^(fix|不對|still|wrong|again|retry|redo|try again|do it|還是|依然|not work|failed|錯)",
        re.IGNORECASE,
    )
    if len(stripped) < 30 and _correction_re.search(stripped):
        counters["short_corrections"] += 1

    # Short confirmations (< 15 chars, matching ok/yes/continue patterns)
    _confirm_re = re.compile(
        r"^(ok|yes|好|可以|continue|push it|do it|go|ship it|lgtm)\s*$",
        re.IGNORECASE,
    )
    if len(stripped) < 15 and _confirm_re.match(stripped):
        counters["short_confirms"] += 1

    # CJK vs alpha characters for language ratio
    for ch in content:
        if ch.isalpha():
            counters["alpha_chars"] += 1
            if '一' <= ch <= '鿿' or '㐀' <= ch <= '䶿':
                counters["cjk_chars"] += 1

    # Figma URLs
    if "figma.com/" in content:
        counters["figma_urls"] += 1

    # Interruptions
    if "[Request interrupted" in content or "interrupted by user" in content.lower():
        counters["interruptions"] += 1


def scan_session(jp, target_dates, warnings, window_dates=None):
    """Scan a single per-session jsonl file. Returns session dict or None if no
    activity on any of the window dates.

    Args:
        jp: Path to the .jsonl file
        target_dates: set of "YYYY-MM-DD" strings — the "focus" set. Session-
            level fields (sess_cost, events, first/last_dt, msg_snippets,
            user_msgs, etc.) restrict to these dates only.
        warnings: list to append any non-fatal warnings to
        window_dates: optional superset of target_dates. When provided, the
            per_date bucket map and the inclusion check use this wider set —
            so the same single-pass scan can populate today's session view
            (`target_dates`) AND a 12-week + 90-day window aggregate
            (`window_dates`). Defaults to `target_dates` (backward-compat).
    """
    if window_dates is None:
        window_dates = target_dates
    sid = jp.stem
    encoded_cwd = jp.parent.name

    seen_reqs = {}              # requestId -> (usage, model, dt) — first wins
    no_req_counter = 0
    git_branch = ""
    cwd = ""
    first_dt = None
    last_dt = None
    user_msgs = 0
    msg_snippets = []
    tickets_from_msgs = set()        # focus (target_dates) — used for build_rows
    tickets_from_msgs_window = set() # superset (window_dates) — used by sessions_meta
    has_window_activity = False
    events = []                 # [(dt, type)] for target-date events (any target day)
    events_by_date: dict[str, list] = {}  # date_str -> [(dt, type)] for per-day time accounting
    user_msgs_by_date: dict[str, int] = {}  # date_str -> count (for output_per_turn)
    tool_results_by_date: dict[str, dict] = {}  # date_str -> {total, error} (for tool_error_rate)
    subagent_spawns = 0

    # Behavioral counters (process every user msg, not just snippets)
    behavioral_slash_cmds = {}      # {"/format": 3, ...}
    behavioral_counters = {
        "short_corrections": 0,
        "short_confirms": 0,
        "cjk_chars": 0,
        "alpha_chars": 0,
        "figma_urls": 0,
        "interruptions": 0,
    }

    try:
        with open(jp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if not git_branch:
                    gb = obj.get("gitBranch", "")
                    if gb and gb != "HEAD":
                        git_branch = gb
                if not cwd:
                    c = obj.get("cwd", "")
                    if c:
                        cwd = c

                dt = parse_ts(obj.get("timestamp", ""))
                t = obj.get("type")
                d_str_dt = dt.strftime("%Y-%m-%d") if dt else ""
                on_target = bool(dt and d_str_dt in target_dates)
                on_window = bool(dt and d_str_dt in window_dates)

                if t == "user":
                    if on_window:
                        has_window_activity = True
                        d_key = d_str_dt
                        events_by_date.setdefault(d_key, []).append((dt, "user"))
                        if on_target:
                            if first_dt is None or dt < first_dt:
                                first_dt = dt
                            if last_dt is None or dt > last_dt:
                                last_dt = dt
                            events.append((dt, "user"))
                        msg = obj.get("message", {})
                        content_raw = msg.get("content", "") if isinstance(msg, dict) else ""
                        # Detect tool_result content blocks; they ride on user
                        # turns in Claude Code transcripts. Don't count those as
                        # "real" user messages for output_per_turn — tool_result
                        # is automated continuation, not human prompt.
                        is_tool_result_only = False
                        if isinstance(content_raw, list):
                            tool_result_blocks = [
                                c for c in content_raw
                                if isinstance(c, dict) and c.get("type") == "tool_result"
                            ]
                            non_tr_text = [
                                c for c in content_raw
                                if isinstance(c, dict) and c.get("type") != "tool_result"
                            ]
                            is_tool_result_only = bool(tool_result_blocks) and not non_tr_text
                            for trb in tool_result_blocks:
                                bd = tool_results_by_date.setdefault(
                                    d_key, {"total": 0, "error": 0}
                                )
                                bd["total"] += 1
                                if trb.get("is_error"):
                                    bd["error"] += 1
                            content = " ".join(
                                c.get("text", "")
                                for c in content_raw
                                if isinstance(c, dict)
                            )
                        else:
                            content = content_raw
                        if not is_tool_result_only:
                            user_msgs_by_date[d_key] = user_msgs_by_date.get(d_key, 0) + 1
                            if on_target:
                                user_msgs += 1
                        if isinstance(content, str) and content:
                            if on_target and len(msg_snippets) < 8:
                                msg_snippets.append(content[:300])
                            # Always count behavioral metrics for target-date
                            # user messages (not gated by snippet limit)
                            if on_target and not is_tool_result_only:
                                _count_behavioral(
                                    content, behavioral_slash_cmds,
                                    behavioral_counters,
                                )
                            for tk in TICKET_RE.findall(content):
                                tickets_from_msgs_window.add(tk)
                                if on_target:
                                    tickets_from_msgs.add(tk)

                elif t == "assistant":
                    msg = obj.get("message", {})
                    if not isinstance(msg, dict):
                        continue
                    if on_window:
                        events_by_date.setdefault(d_str_dt, []).append((dt, "assistant"))
                    if on_target:
                        events.append((dt, "assistant"))
                        # Subagent spawn detection (Agent tool_use blocks)
                        content_blocks = msg.get("content", [])
                        if isinstance(content_blocks, list):
                            for blk in content_blocks:
                                if (
                                    isinstance(blk, dict)
                                    and blk.get("type") == "tool_use"
                                    and blk.get("name") == "Agent"
                                ):
                                    subagent_spawns += 1
                    usage = msg.get("usage")
                    if not usage:
                        continue
                    rid = obj.get("requestId") or msg.get("id")
                    if not rid:
                        no_req_counter += 1
                        rid = f"_NO_REQ_{no_req_counter}"
                    if rid in seen_reqs:
                        continue
                    if on_window:
                        has_window_activity = True
                    if on_target:
                        if first_dt is None or dt < first_dt:
                            first_dt = dt
                        if last_dt is None or dt > last_dt:
                            last_dt = dt
                    seen_reqs[rid] = (usage, msg.get("model", ""), dt)

                elif t == "tool_use":
                    if on_window:
                        events_by_date.setdefault(d_str_dt, []).append((dt, "tool_use"))
                    if on_target:
                        events.append((dt, "tool_use"))
                elif t == "tool_result":
                    if on_window:
                        events_by_date.setdefault(d_str_dt, []).append((dt, "tool_result"))
                    if on_target:
                        events.append((dt, "tool_result"))
    except OSError as e:
        warnings.append(f"session {sid}: read error: {e}")
        return None

    if not has_window_activity:
        return None

    # Aggregate per-session metrics from deduped requests on target dates.
    # Track per-date breakdown so range mode can produce daily_breakdown.
    sess_cost = 0.0
    sess_tokens = 0
    sess_assistant = 0
    sess_cost_by_family = {"opus": 0.0, "sonnet": 0.0, "haiku": 0.0}
    sess_input_tokens = 0
    sess_output_tokens = 0
    sess_cache_read = 0
    sess_cache_5m = 0
    sess_cache_1h = 0
    seen_unknown_models = set()
    models = set()
    # Per-date buckets
    per_date: dict[str, dict] = {}  # date_str -> {cost, tokens, input, cache_read, cache_5m, cache_1h, by_family, assistant_msgs}
    def _bucket(d):
        return per_date.setdefault(d, {
            "cost": 0.0,
            "late_night_cost": 0.0,  # cost from requests with HK hour in [22,23,0..5]
            "tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read": 0,
            "cache_5m": 0,
            "cache_1h": 0,
            "cost_by_family": {"opus": 0.0, "sonnet": 0.0, "haiku": 0.0},
            "assistant_msgs": 0,
            "user_msgs": 0,
            "tool_results_total": 0,
            "tool_results_error": 0,
        })
    for usage, model, dt in seen_reqs.values():
        if not dt:
            continue
        d_str = dt.strftime("%Y-%m-%d")
        if d_str not in window_dates:
            continue
        in_target = d_str in target_dates
        cost, c5, c1 = turn_cost_split(usage, model)
        f = fam(model)
        if not f and model and model not in seen_unknown_models and model not in KNOWN_NON_BILLABLE:
            seen_unknown_models.add(model)
            warnings.append(f"unknown model family for '{model}'")
        tokens = turn_tokens(usage)
        in_tok = usage.get("input_tokens", 0) or 0
        out_tok = usage.get("output_tokens", 0) or 0
        cr_tok = usage.get("cache_read_input_tokens", 0) or 0
        # Session-level: target_dates only
        if in_target:
            sess_cost += cost
            if f:
                sess_cost_by_family[f] += cost
            sess_tokens += tokens
            sess_input_tokens += in_tok
            sess_output_tokens += out_tok
            sess_cache_read += cr_tok
            sess_cache_5m += c5
            sess_cache_1h += c1
            sess_assistant += 1
            if model:
                models.add(model)
        # Per-date bucket: any window date
        b = _bucket(d_str)
        b["cost"] += cost
        # Late-night attribution: 22:00-05:59 HK time. Per-request precision
        # rather than per-session bucket — sessions spanning hours get accurate.
        hour_hk = dt.astimezone(HK_TZ).hour
        if hour_hk >= 22 or hour_hk <= 5:
            b["late_night_cost"] += cost
        b["tokens"] += tokens
        b["input_tokens"] += in_tok
        b["output_tokens"] += out_tok
        b["cache_read"] += cr_tok
        b["cache_5m"] += c5
        b["cache_1h"] += c1
        if f:
            b["cost_by_family"][f] += cost
        b["assistant_msgs"] += 1

    # Time accounting from sorted events (whole-session, used for legacy fields)
    events.sort(key=lambda e: e[0])
    wall_sec = 0.0
    ai_sec = 0.0
    think_sec = 0.0
    if events:
        wall_sec = (events[-1][0] - events[0][0]).total_seconds()
        for i in range(1, len(events)):
            gap = (events[i][0] - events[i - 1][0]).total_seconds()
            if gap > GAP_CAP:
                continue
            prev_t = events[i - 1][1]
            next_t = events[i][1]
            if prev_t == "assistant" and next_t == "user":
                think_sec += gap
            else:
                ai_sec += gap

    # Per-date time accounting (gap-based, isolated per date)
    per_date_time: dict[str, dict] = {}
    for d_str, ev_list in events_by_date.items():
        ev_list.sort(key=lambda e: e[0])
        d_wall = 0.0
        d_ai = 0.0
        d_think = 0.0
        d_subagent_events = 0  # subagent count is from main loop, not events
        if ev_list:
            d_wall = (ev_list[-1][0] - ev_list[0][0]).total_seconds()
            for i in range(1, len(ev_list)):
                gap = (ev_list[i][0] - ev_list[i - 1][0]).total_seconds()
                if gap > GAP_CAP:
                    continue
                prev_t = ev_list[i - 1][1]
                next_t = ev_list[i][1]
                if prev_t == "assistant" and next_t == "user":
                    d_think += gap
                else:
                    d_ai += gap
        per_date_time[d_str] = {
            "wall_sec": d_wall,
            "ai_sec": d_ai,
            "think_sec": d_think,
            "subagent_spawns": d_subagent_events,
        }

    # Merge per_date_time into per_date buckets
    for d_str, t_info in per_date_time.items():
        b = _bucket(d_str)
        b["wall_sec"] = t_info["wall_sec"]
        b["ai_sec"] = t_info["ai_sec"]
        b["think_sec"] = t_info["think_sec"]
    # Merge user_msgs_by_date (counted during streaming, not in seen_reqs loop)
    for d_str, n in user_msgs_by_date.items():
        _bucket(d_str)["user_msgs"] += n
    # Merge tool_results_by_date (also from streaming, peeking content blocks)
    for d_str, tr in tool_results_by_date.items():
        b = _bucket(d_str)
        b["tool_results_total"] += tr.get("total", 0)
        b["tool_results_error"] += tr.get("error", 0)
    # Subagent count is per-session (no per-event timestamp); attribute to first
    # date with activity. v1 trade-off: rare to span days so usually correct.
    if subagent_spawns and per_date:
        first_date = min(per_date.keys())
        per_date[first_date].setdefault("subagent_spawns", 0)
        per_date[first_date]["subagent_spawns"] += subagent_spawns

    return {
        "session_id": sid,
        "encoded_cwd": encoded_cwd,
        "cwd": cwd or encoded_cwd.replace("-", "/"),
        "git_branch": git_branch,
        "cost": sess_cost,
        "cost_by_family": sess_cost_by_family,
        "tokens": sess_tokens,
        "input_tokens": sess_input_tokens,
        "output_tokens": sess_output_tokens,
        "cache_read": sess_cache_read,
        "cache_5m": sess_cache_5m,
        "cache_1h": sess_cache_1h,
        "assistant_msgs": sess_assistant,
        "user_msgs": user_msgs,
        "models": sorted(models),
        "first_dt": first_dt,
        "last_dt": last_dt,
        "first_time": first_dt.strftime("%H:%M") if first_dt else "",
        "last_time": last_dt.strftime("%H:%M") if last_dt else "",
        "messages": msg_snippets,
        "tickets_from_msgs": sorted(tickets_from_msgs),
        "tickets_from_msgs_window": sorted(tickets_from_msgs_window),
        "wall_sec": wall_sec,
        "ai_sec": ai_sec,
        "think_sec": think_sec,
        "subagent_spawns": subagent_spawns,
        "per_date": per_date,  # date_str -> {cost, tokens, ai_sec, think_sec, ...}
        # Behavioral counters (full scan, not truncated by snippet limit)
        "behavioral_slash_cmds": behavioral_slash_cmds,
        "behavioral_short_corrections": behavioral_counters["short_corrections"],
        "behavioral_short_confirms": behavioral_counters["short_confirms"],
        "behavioral_cjk_chars": behavioral_counters["cjk_chars"],
        "behavioral_alpha_chars": behavioral_counters["alpha_chars"],
        "behavioral_figma_urls": behavioral_counters["figma_urls"],
        "behavioral_interruptions": behavioral_counters["interruptions"],
    }


def extract_ticket(info):
    """Priority: git_branch -> cwd -> encoded_cwd -> tickets_from_msgs."""
    for src in (info["git_branch"], info["cwd"], info["encoded_cwd"]):
        found = TICKET_RE.findall(src or "")
        if found:
            return found[0]
    if info["tickets_from_msgs"]:
        return info["tickets_from_msgs"][0]
    return ""


def branch_prefix(info):
    b = info["git_branch"]
    return b.split("/")[0] if "/" in b else ""


def is_trivial(info):
    if info["user_msgs"] > 1:
        return False
    if not info["messages"]:
        return True
    return info["messages"][0].strip() in TRIVIAL


def is_dispatcher(info):
    if not info["messages"]:
        return False
    first = info["messages"][0]
    return first.startswith("/dispatcher") or first.startswith("/add-worktree")


def classify(row):
    if row.get("is_dispatcher"):
        return "devops"
    bp = row.get("branch_prefix", "")
    msgs_lower = " ".join(m.lower() for m in row.get("messages", []))
    if bp == "fix":
        return "bug fix"
    if bp == "feat":
        fix_words = ["fix", "修復", "crash", "黑屏", "disable", "broken", "error"]
        build_words = [
            "add", "implement", "build", "create", "porting", "port",
            "design", "layout", "spec", "prd", "bottom sheet", "stepper",
        ]
        has_fix = any(w in msgs_lower for w in fix_words)
        has_build = any(w in msgs_lower for w in build_words)
        if has_fix and not has_build:
            return "bug fix"
        return "feature dev"
    if bp == "chore":
        return "tooling"
    if bp == "ci":
        return "devops"
    if any(w in msgs_lower for w in ["/code-review", "code review", "review pr", "spawn a dev-agent to code-review"]):
        if "plan-review" not in msgs_lower:
            return "code review"
    if any(w in msgs_lower for w in ["push it", "ship", "merge", "/create-pr", "pr created", "/commit and push"]):
        return "PR shipped"
    if any(w in msgs_lower for w in ["prd", "spec", "ticket porting"]):
        return "PRD/spec"
    if any(w in msgs_lower for w in ["研究", "分析", "確認", "explain", "compare", "整理"]):
        return "research"
    if any(w in msgs_lower for w in ["skill", "tool", "config", "排程", "launchagent", "notion"]):
        return "tooling"
    if any(w in msgs_lower for w in ["worktree", "archive", "deploy", "ci", "dispatcher"]):
        return "devops"
    return "feature dev"


def aggregate_group(group_sessions):
    """Sum metrics across a list of (sid, session_info) tuples."""
    sids = [s for s, _ in group_sessions]
    all_tk = set()
    msgs = []
    cost = 0.0
    tokens = 0
    ai_sec = 0.0
    think_sec = 0.0
    subagents = 0
    cost_by_family = {"opus": 0.0, "sonnet": 0.0, "haiku": 0.0}
    input_tokens = 0
    output_tokens = 0
    cache_read = 0
    cache_5m = 0
    cache_1h = 0
    user_msgs = 0
    cwds = defaultdict(int)
    bp = ""
    first_dts = []
    last_dts = []
    # Behavioral counter aggregation
    beh_slash_cmds = {}
    beh_short_corrections = 0
    beh_short_confirms = 0
    beh_cjk_chars = 0
    beh_alpha_chars = 0
    beh_figma_urls = 0
    beh_interruptions = 0
    for sid, info in group_sessions:
        if info["ticket"]:
            all_tk.add(info["ticket"])
        all_tk.update(info["tickets_from_msgs"])
        msgs.extend(info["messages"])
        cost += info["cost"]
        tokens += info["tokens"]
        ai_sec += info["ai_sec"]
        think_sec += info["think_sec"]
        subagents += info["subagent_spawns"]
        for f, c in info["cost_by_family"].items():
            cost_by_family[f] += c
        input_tokens += info["input_tokens"]
        output_tokens += info["output_tokens"]
        cache_read += info["cache_read"]
        cache_5m += info["cache_5m"]
        cache_1h += info["cache_1h"]
        user_msgs += info.get("user_msgs", 0)
        # Merge behavioral counters
        for cmd, count in info.get("behavioral_slash_cmds", {}).items():
            beh_slash_cmds[cmd] = beh_slash_cmds.get(cmd, 0) + count
        beh_short_corrections += info.get("behavioral_short_corrections", 0)
        beh_short_confirms += info.get("behavioral_short_confirms", 0)
        beh_cjk_chars += info.get("behavioral_cjk_chars", 0)
        beh_alpha_chars += info.get("behavioral_alpha_chars", 0)
        beh_figma_urls += info.get("behavioral_figma_urls", 0)
        beh_interruptions += info.get("behavioral_interruptions", 0)
        if info["cwd"]:
            cwds[info["cwd"]] += 1
        if info["first_dt"]:
            first_dts.append(info["first_dt"])
        if info["last_dt"]:
            last_dts.append(info["last_dt"])
        if not bp and info["branch_prefix"]:
            bp = info["branch_prefix"]
    most_common_cwd = max(cwds.items(), key=lambda kv: kv[1])[0] if cwds else ""
    return {
        "session_ids": sids,
        "session_count": len(sids),
        "tickets": sorted(all_tk),
        "branch_prefix": bp,
        "messages": msgs[:10],
        "cost_total": cost,
        "tokens_total": tokens,
        "ai_sec": ai_sec,
        "think_sec": think_sec,
        "subagent_spawns": subagents,
        "user_msgs": user_msgs,
        "cost_by_model": {k: round(v, 6) for k, v in cost_by_family.items()},
        "tokens_by_kind": {
            "input": input_tokens,
            "output": output_tokens,
            "cache_read": cache_read,
            "cache_creation_5m": cache_5m,
            "cache_creation_1h": cache_1h,
        },
        "cwd": most_common_cwd,
        "first_dt": min(first_dts) if first_dts else None,
        "last_dt": max(last_dts) if last_dts else None,
        # Behavioral counters (aggregated from full scan)
        "behavioral_slash_cmds": beh_slash_cmds,
        "behavioral_short_corrections": beh_short_corrections,
        "behavioral_short_confirms": beh_short_confirms,
        "behavioral_cjk_chars": beh_cjk_chars,
        "behavioral_alpha_chars": beh_alpha_chars,
        "behavioral_figma_urls": beh_figma_urls,
        "behavioral_interruptions": beh_interruptions,
    }


def build_rows(sessions, warnings):
    """4-phase grouping: trivial -> dispatcher -> ticket -> project."""
    tagged = {}
    for sid, info in sessions.items():
        tagged[sid] = {
            **info,
            "ticket": extract_ticket(info),
            "branch_prefix": branch_prefix(info),
            "is_trivial": is_trivial(info),
            "is_dispatcher": is_dispatcher(info),
        }

    trivial_count = sum(1 for t in tagged.values() if t["is_trivial"])
    if trivial_count:
        warnings.append(f"filtered {trivial_count} trivial sessions")

    rows = []
    used = set()

    # Phase 1: mark trivial as used (excluded)
    for sid, t in tagged.items():
        if t["is_trivial"]:
            used.add(sid)

    # Phase 2: dispatcher rows (one per session)
    for sid, t in tagged.items():
        if sid in used:
            continue
        if t["is_dispatcher"]:
            agg = aggregate_group([(sid, t)])
            rows.append({
                "ticket": agg["tickets"][0] if agg["tickets"] else "",
                "tickets": agg["tickets"],
                "is_dispatcher": True,
                "branch_prefix": "",
                "first_dt": agg["first_dt"],
                "last_dt": agg["last_dt"],
                "wall_sec": int(t["wall_sec"]),
                "ai_sec": int(agg["ai_sec"]),
                "think_sec": int(agg["think_sec"]),
                "session_count": 1,
                "subagent_spawns": agg["subagent_spawns"],
                "cost_total": agg["cost_total"],
                "cost_by_model": agg["cost_by_model"],
                "tokens_total": agg["tokens_total"],
                "tokens_by_kind": agg["tokens_by_kind"],
                "user_msgs": agg["user_msgs"],
                "messages": t["messages"],
                "suggested_output": "devops",
                "session_ids": [sid],
                "cwd": t["cwd"],
                "behavioral_slash_cmds": agg["behavioral_slash_cmds"],
                "behavioral_short_corrections": agg["behavioral_short_corrections"],
                "behavioral_short_confirms": agg["behavioral_short_confirms"],
                "behavioral_cjk_chars": agg["behavioral_cjk_chars"],
                "behavioral_alpha_chars": agg["behavioral_alpha_chars"],
                "behavioral_figma_urls": agg["behavioral_figma_urls"],
                "behavioral_interruptions": agg["behavioral_interruptions"],
            })
            used.add(sid)

    # Phase 3: group by ticket
    ticket_groups = defaultdict(list)
    for sid, t in tagged.items():
        if sid in used or not t["ticket"]:
            continue
        ticket_groups[t["ticket"]].append((sid, t))

    for tk, group in ticket_groups.items():
        agg = aggregate_group(group)
        # wall_sec for grouped row: max(last_dt) - min(first_dt) across sessions
        wall_sec = 0
        if agg["first_dt"] and agg["last_dt"]:
            wall_sec = int((agg["last_dt"] - agg["first_dt"]).total_seconds())
        rows.append({
            "ticket": tk,
            "tickets": agg["tickets"],
            "is_dispatcher": False,
            "branch_prefix": agg["branch_prefix"],
            "first_dt": agg["first_dt"],
            "last_dt": agg["last_dt"],
            "wall_sec": wall_sec,
            "ai_sec": int(agg["ai_sec"]),
            "think_sec": int(agg["think_sec"]),
            "session_count": agg["session_count"],
            "subagent_spawns": agg["subagent_spawns"],
            "cost_total": agg["cost_total"],
            "cost_by_model": agg["cost_by_model"],
            "tokens_total": agg["tokens_total"],
            "tokens_by_kind": agg["tokens_by_kind"],
            "user_msgs": agg["user_msgs"],
            "messages": agg["messages"],
            "suggested_output": "",
            "session_ids": agg["session_ids"],
            "cwd": agg["cwd"],
            "behavioral_slash_cmds": agg["behavioral_slash_cmds"],
            "behavioral_short_corrections": agg["behavioral_short_corrections"],
            "behavioral_short_confirms": agg["behavioral_short_confirms"],
            "behavioral_cjk_chars": agg["behavioral_cjk_chars"],
            "behavioral_alpha_chars": agg["behavioral_alpha_chars"],
            "behavioral_figma_urls": agg["behavioral_figma_urls"],
            "behavioral_interruptions": agg["behavioral_interruptions"],
        })
        for s in agg["session_ids"]:
            used.add(s)

    # Phase 4: group remaining by project (encoded_cwd)
    proj_groups = defaultdict(list)
    for sid, t in tagged.items():
        if sid in used:
            continue
        proj_groups[t["encoded_cwd"]].append((sid, t))

    for p, group in proj_groups.items():
        agg = aggregate_group(group)
        wall_sec = 0
        if agg["first_dt"] and agg["last_dt"]:
            wall_sec = int((agg["last_dt"] - agg["first_dt"]).total_seconds())
        rows.append({
            "ticket": "",
            "tickets": agg["tickets"],
            "is_dispatcher": False,
            "branch_prefix": agg["branch_prefix"],
            "first_dt": agg["first_dt"],
            "last_dt": agg["last_dt"],
            "wall_sec": wall_sec,
            "ai_sec": int(agg["ai_sec"]),
            "think_sec": int(agg["think_sec"]),
            "session_count": agg["session_count"],
            "subagent_spawns": agg["subagent_spawns"],
            "cost_total": agg["cost_total"],
            "cost_by_model": agg["cost_by_model"],
            "tokens_total": agg["tokens_total"],
            "tokens_by_kind": agg["tokens_by_kind"],
            "user_msgs": agg["user_msgs"],
            "messages": agg["messages"],
            "suggested_output": "",
            "session_ids": agg["session_ids"],
            "cwd": agg["cwd"],
            "behavioral_slash_cmds": agg["behavioral_slash_cmds"],
            "behavioral_short_corrections": agg["behavioral_short_corrections"],
            "behavioral_short_confirms": agg["behavioral_short_confirms"],
            "behavioral_cjk_chars": agg["behavioral_cjk_chars"],
            "behavioral_alpha_chars": agg["behavioral_alpha_chars"],
            "behavioral_figma_urls": agg["behavioral_figma_urls"],
            "behavioral_interruptions": agg["behavioral_interruptions"],
        })
        for s in agg["session_ids"]:
            used.add(s)

    # Classify rows that don't already have a suggested_output
    for row in rows:
        if not row["suggested_output"]:
            row["suggested_output"] = classify(row)

    # Sort by first activity
    rows.sort(key=lambda r: r["first_dt"] or datetime.min.replace(tzinfo=HK_TZ))
    return rows


def serialize_row(row):
    """Convert internal row dict to JSON-emittable shape per schema 1.0."""
    return {
        "ticket": row["ticket"],
        "tickets": row["tickets"],
        "is_dispatcher": row["is_dispatcher"],
        "branch_prefix": row["branch_prefix"],
        "first_time": row["first_dt"].isoformat() if row["first_dt"] else "",
        "last_time": row["last_dt"].isoformat() if row["last_dt"] else "",
        "wall_sec": row["wall_sec"],
        "ai_sec": row["ai_sec"],
        "think_sec": row["think_sec"],
        "session_count": row["session_count"],
        "subagent_spawns": row["subagent_spawns"],
        "cost_total": round(row["cost_total"], 6),
        "cost_by_model": row["cost_by_model"],
        "tokens_total": row["tokens_total"],
        "tokens_by_kind": row["tokens_by_kind"],
        "user_msgs": row.get("user_msgs", 0),
        "messages": row["messages"],
        "suggested_output": row["suggested_output"],
        "session_ids": row["session_ids"],
        "cwd": row["cwd"],
        "behavioral_slash_cmds": row.get("behavioral_slash_cmds", {}),
        "behavioral_short_corrections": row.get("behavioral_short_corrections", 0),
        "behavioral_short_confirms": row.get("behavioral_short_confirms", 0),
        "behavioral_cjk_chars": row.get("behavioral_cjk_chars", 0),
        "behavioral_alpha_chars": row.get("behavioral_alpha_chars", 0),
        "behavioral_figma_urls": row.get("behavioral_figma_urls", 0),
        "behavioral_interruptions": row.get("behavioral_interruptions", 0),
    }


def compute_daily_stats(rows, sessions):
    """Compute daily_stats from rows + per-session data."""
    total_cost = sum(r["cost_total"] for r in rows)
    total_tokens = sum(r["tokens_total"] for r in rows)
    total_sessions = sum(r["session_count"] for r in rows)
    total_ai = sum(r["ai_sec"] for r in rows)
    total_think = sum(r["think_sec"] for r in rows)
    total_subagents = sum(r["subagent_spawns"] for r in rows)
    all_tickets = set()
    for r in rows:
        all_tickets.update(r["tickets"])

    # Per-model cost split: aggregate from rows (which already aggregated from sessions
    # but excludes trivial-filtered sessions; that's correct — daily_stats reflects what's shown)
    cost_opus = sum(r["cost_by_model"].get("opus", 0) for r in rows)
    cost_sonnet = sum(r["cost_by_model"].get("sonnet", 0) for r in rows)
    cost_haiku = sum(r["cost_by_model"].get("haiku", 0) for r in rows)

    # Cache hit rate from rows tokens_by_kind aggregation
    total_input = sum(r["tokens_by_kind"]["input"] for r in rows)
    total_output = sum(r["tokens_by_kind"]["output"] for r in rows)
    total_cread = sum(r["tokens_by_kind"]["cache_read"] for r in rows)
    total_c5 = sum(r["tokens_by_kind"]["cache_creation_5m"] for r in rows)
    total_c1 = sum(r["tokens_by_kind"]["cache_creation_1h"] for r in rows)
    billed_input_total = total_input + total_cread + total_c5 + total_c1
    cache_hit_rate = round(total_cread / billed_input_total, 4) if billed_input_total > 0 else 0
    dollars_per_ai_hour = round(total_cost / (total_ai / 3600), 2) if total_ai > 0 else 0

    # output_per_turn — output tokens emitted per user message (single retained
    # productivity KPI; sums across non-trivial rows so trivial /mcp turns don't
    # dilute the denominator).
    total_user_msgs = sum(r.get("user_msgs", 0) for r in rows)
    output_per_turn = (
        int(total_output / total_user_msgs) if total_user_msgs > 0 else None
    )

    return {
        "total_cost": round(total_cost, 2),
        "cost_opus": round(cost_opus, 2),
        "cost_sonnet": round(cost_sonnet, 2),
        "cost_haiku": round(cost_haiku, 2),
        "total_tokens": total_tokens,
        "total_output_tokens": total_output,
        "total_user_msgs": total_user_msgs,
        "output_per_turn": output_per_turn,
        "total_sessions": total_sessions,
        "active_hours": round(total_ai / 3600, 2),
        "thinking_hours": round(total_think / 3600, 2),
        "subagent_spawns": total_subagents,
        "tickets_touched": len(all_tickets),
        "items": len(rows),
        "cache_hit_rate": cache_hit_rate,
        "dollars_per_ai_hour": dollars_per_ai_hour,
    }


def compute_daily_breakdown(sessions, target_dates):
    """Aggregate per-date breakdown across all sessions.

    Returns: list of dicts, one per date in target_dates (sorted), with daily_stats-like fields.
    Sessions without activity on a given date contribute zero to that date's bucket.
    """
    out = []
    for d_str in sorted(target_dates):
        cost = 0.0
        tokens = 0
        sessions_count = 0
        ai_sec_total = 0.0
        think_sec_total = 0.0
        subagent_total = 0
        cost_opus = 0.0
        cost_sonnet = 0.0
        cost_haiku = 0.0
        input_total = 0
        cread_total = 0
        c5_total = 0
        c1_total = 0
        for sess in sessions.values():
            pd = sess.get("per_date", {}).get(d_str)
            if not pd:
                continue
            sessions_count += 1
            cost += pd.get("cost", 0.0)
            tokens += pd.get("tokens", 0)
            ai_sec_total += pd.get("ai_sec", 0.0)
            think_sec_total += pd.get("think_sec", 0.0)
            subagent_total += pd.get("subagent_spawns", 0)
            by_fam = pd.get("cost_by_family", {})
            cost_opus += by_fam.get("opus", 0.0)
            cost_sonnet += by_fam.get("sonnet", 0.0)
            cost_haiku += by_fam.get("haiku", 0.0)
            input_total += pd.get("input_tokens", 0)
            cread_total += pd.get("cache_read", 0)
            c5_total += pd.get("cache_5m", 0)
            c1_total += pd.get("cache_1h", 0)
        billed = input_total + cread_total + c5_total + c1_total
        cache_hit = round(cread_total / billed, 4) if billed > 0 else 0
        dphr = round(cost / (ai_sec_total / 3600), 2) if ai_sec_total > 0 else 0
        out.append({
            "date": d_str,
            "total_cost": round(cost, 2),
            "cost_opus": round(cost_opus, 2),
            "cost_sonnet": round(cost_sonnet, 2),
            "cost_haiku": round(cost_haiku, 2),
            "total_tokens": tokens,
            "total_sessions": sessions_count,
            "active_hours": round(ai_sec_total / 3600, 2),
            "thinking_hours": round(think_sec_total / 3600, 2),
            "subagent_spawns": subagent_total,
            "cache_hit_rate": cache_hit,
            "dollars_per_ai_hour": dphr,
        })
    return out


def daterange_inclusive(start_str, end_str):
    """Return a sorted list of YYYY-MM-DD strings from start to end (inclusive)."""
    start = datetime.strptime(start_str, "%Y-%m-%d").date()
    end = datetime.strptime(end_str, "%Y-%m-%d").date()
    if end < start:
        raise ValueError(f"--range end ({end_str}) is before start ({start_str})")
    out = []
    d = start
    while d <= end:
        out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


def render_text(result):
    """Human-readable text output (lossy; --json is the canonical format)."""
    rng = result["target_range"]
    rows = result["rows"]
    ds = result["daily_stats"]
    lines = []
    if rng["start"] == rng["end"]:
        lines.append(f"# Daily summary {rng['start']}")
    else:
        lines.append(f"# Range summary {rng['start']} to {rng['end']}")
    lines.append("")
    lines.append(
        f"**{ds['tickets_touched']} tickets | "
        f"{ds['total_sessions']} sessions | "
        f"${ds['total_cost']:.2f} | "
        f"{ds['active_hours']}h active | "
        f"cache hit {ds['cache_hit_rate']*100:.0f}%**"
    )
    lines.append("")
    lines.append("| Time | Group | Output | Cost | Sessions |")
    lines.append("|------|-------|--------|------|----------|")
    for r in rows:
        time_range = f"{r['first_time'][11:16] if r['first_time'] else ''}-{r['last_time'][11:16] if r['last_time'] else ''}"
        group_label = r["ticket"] or r["cwd"].split("/")[-1] or "(unknown)"
        if r["is_dispatcher"]:
            group_label = f"dispatch:{r['session_ids'][0][:8]}"
        lines.append(
            f"| {time_range} | {group_label} | {r['suggested_output']} | "
            f"${r['cost_total']:.2f} | {r['session_count']} |"
        )
    lines.append("")
    lines.append(f"Models cost: opus ${ds['cost_opus']} / sonnet ${ds['cost_sonnet']} / haiku ${ds['cost_haiku']}")
    lines.append(f"$/AI hour: ${ds['dollars_per_ai_hour']}")
    if result["warnings"]:
        lines.append("")
        lines.append("Warnings:")
        for w in result["warnings"]:
            lines.append(f"  - {w}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# GitHub PR / Linear ticket integrations (Phase 1 dashboard redesign)
# ----------------------------------------------------------------------------

TICKET_PR_RE = re.compile(r"([A-Z]{2,}-\d+)", re.IGNORECASE)


def _gh_user(warnings):
    """Return current gh user login or None on failure."""
    try:
        r = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            warnings.append(f"gh api user failed: {r.stderr.strip()[:120]}")
            return None
        return r.stdout.strip() or None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        warnings.append(f"gh user lookup error: {e}")
        return None


def _gh_search_prs(gh_user, warnings, limit=100):
    """Fetch up to `limit` PRs authored by gh_user across all repos.

    Uses `gh api graphql` instead of `gh search prs --json` because the latter
    doesn't expose additions/deletions/headRefName/reviewDecision (verified
    2026-05-01). One HTTP call returns everything we need.

    Returns list of dicts shaped to match the older REST output:
        number, title, state (lowercase: open/closed/merged), createdAt,
        closedAt, url, repository.nameWithOwner, additions, deletions,
        headRefName, reviewDecision.
    Returns [] on any failure (with warning logged).
    """
    if not gh_user:
        return []
    query = (
        'query { search(query: "is:pr author:' + gh_user + ' sort:updated-desc"'
        ', type: ISSUE, first: ' + str(limit) + ') { '
        'nodes { ... on PullRequest { '
        'number title state url createdAt closedAt mergedAt '
        'additions deletions headRefName reviewDecision '
        'repository { nameWithOwner } '
        '} } } }'
    )
    try:
        r = subprocess.run(
            ["gh", "api", "graphql", "-f", "query=" + query],
            capture_output=True, text=True, timeout=45,
        )
        if r.returncode != 0:
            warnings.append(f"gh graphql prs failed: {r.stderr.strip()[:160]}")
            return []
        data = json.loads(r.stdout)
        nodes = (data.get("data") or {}).get("search", {}).get("nodes", []) or []
        out = []
        for n in nodes:
            if not n:
                continue
            # Normalize state to match prior REST shape (lowercase). GraphQL
            # PullRequestState is OPEN/CLOSED/MERGED uppercase.
            n["state"] = (n.get("state") or "").lower()
            out.append(n)
        return out
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, json.JSONDecodeError) as e:
        warnings.append(f"gh graphql prs error: {e}")
        return []


def _gh_search_review_requested_prs(gh_user, warnings, limit=200):
    """Fetch PRs where gh_user is requested as a reviewer (any state).

    Different question from _gh_search_prs: we need PRs *others want me to
    review*, not PRs I authored. Used for the PR Review Pressure metric.
    Returns list of dicts with: number, createdAt, closedAt, state, repository, url.
    Returns [] on failure.
    """
    if not gh_user:
        return []
    try:
        r = subprocess.run(
            [
                "gh", "search", "prs",
                "--review-requested", gh_user,
                "--json", "number,state,createdAt,closedAt,url,repository",
                "--limit", str(limit),
            ],
            capture_output=True, text=True, timeout=45,
        )
        if r.returncode != 0:
            warnings.append(f"gh search review-requested prs failed: {r.stderr.strip()[:160]}")
            return []
        return json.loads(r.stdout) or []
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, json.JSONDecodeError) as e:
        warnings.append(f"gh search review-requested error: {e}")
        return []


def _extract_ticket_from_pr(pr):
    """Extract first JIRA-style ticket (e.g. CAF-355, CET-8360) from PR title or branch."""
    title = pr.get("title") or ""
    m = TICKET_PR_RE.search(title)
    if m:
        return m.group(1).upper()
    branch = pr.get("headRefName") or ""
    m = TICKET_PR_RE.search(branch)
    if m:
        return m.group(1).upper()
    return ""


def _classify_pr_for_target(pr, target_date_str):
    """Classify a PR relative to target date.

    Returns dict with: opened_today, merged_today, in_progress.
    in_progress = state==open AND createdAt < target (i.e., open coming into the day).
    """
    created_ymd = (pr.get("createdAt") or "")[:10]
    closed_ymd = (pr.get("closedAt") or "")[:10] if pr.get("closedAt") else ""
    state = pr.get("state") or ""
    return {
        "opened_today": created_ymd == target_date_str,
        "merged_today": state == "merged" and closed_ymd == target_date_str,
        "in_progress": state == "open" and bool(created_ymd) and created_ymd < target_date_str,
    }


def compute_pr_metrics(target_date_str, all_prs):
    """Return (prs_opened, prs_merged, prs_in_progress) ints for target date."""
    op = me = ip = 0
    for pr in all_prs:
        c = _classify_pr_for_target(pr, target_date_str)
        if c["opened_today"]: op += 1
        if c["merged_today"]: me += 1
        if c["in_progress"]: ip += 1
    return op, me, ip


def compute_prs_this_week_list(target_date_str, all_prs):
    """List PRs opened or merged in ISO week containing target date."""
    try:
        target_d = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    except ValueError:
        return []
    iso_y, iso_w, _ = target_d.isocalendar()
    mon = date.fromisocalendar(iso_y, iso_w, 1)
    sun = mon + timedelta(days=6)
    out = []
    for pr in all_prs:
        created_ymd = (pr.get("createdAt") or "")[:10]
        closed_ymd = (pr.get("closedAt") or "")[:10] if pr.get("closedAt") else ""
        state = pr.get("state") or ""
        in_week_open = bool(created_ymd) and mon.strftime("%Y-%m-%d") <= created_ymd <= sun.strftime("%Y-%m-%d")
        in_week_merge = state == "merged" and bool(closed_ymd) and mon.strftime("%Y-%m-%d") <= closed_ymd <= sun.strftime("%Y-%m-%d")
        if in_week_open or in_week_merge:
            repo = (pr.get("repository") or {}).get("nameWithOwner") or ""
            out.append({
                "number": pr.get("number"),
                "title": pr.get("title", ""),
                "state": state,
                "created_date": created_ymd,
                "merged_date": closed_ymd if state == "merged" else "",
                "url": pr.get("url", ""),
                "repository": repo,
                "ticket": _extract_ticket_from_pr(pr),
            })
    out.sort(key=lambda p: (p.get("merged_date") or p.get("created_date") or ""), reverse=True)
    return out


def load_user_config(warnings, path=None):
    """Load ~/.claude/daily-summary-config.json. Auto-create skeleton if missing.

    Returns a dict with the same shape as USER_CONFIG_SKELETON, merged with any
    keys present in the on-disk file. Failures append to `warnings` and degrade
    to an in-memory skeleton without writing.

    The IDs surfaced under `notion.*` are consumed by SKILL.md (Step 0 bootstrap
    + Step 4 writes). Empty strings signal "not yet bootstrapped"; the LLM in
    SKILL.md is responsible for running the wizard and persisting back.
    """
    config_path = Path(os.path.expanduser(path or USER_CONFIG_PATH))
    if not config_path.exists():
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w") as f:
                json.dump(USER_CONFIG_SKELETON, f, indent=2, ensure_ascii=False)
            warnings.append(
                f"daily-summary-config.json not found — wrote skeleton to {config_path}"
            )
        except OSError as e:
            warnings.append(f"could not create config skeleton: {e}")
        return json.loads(json.dumps(USER_CONFIG_SKELETON))

    try:
        with open(config_path) as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        warnings.append(f"could not parse {config_path}: {e}; using empty config")
        return json.loads(json.dumps(USER_CONFIG_SKELETON))

    notion = dict(USER_CONFIG_SKELETON["notion"])
    notion.update(cfg.get("notion") or {})
    cfg["notion"] = notion
    return cfg


def _load_linear_api_key(warnings):
    """Read LINEAR_API_KEY from ~/.claude/settings.json mcpServers.linear-server.env.
    Falls back to LINEAR_API_KEY env var. Returns None if not found."""
    env_key = os.environ.get("LINEAR_API_KEY")
    if env_key:
        return env_key
    settings_path = os.path.expanduser("~/.claude/settings.json")
    try:
        with open(settings_path) as f:
            settings = json.load(f)
        return (
            settings.get("mcpServers", {})
            .get("linear-server", {})
            .get("env", {})
            .get("LINEAR_API_KEY")
        )
    except (OSError, json.JSONDecodeError) as e:
        warnings.append(f"linear api key lookup failed: {e}")
        return None


def _linear_query(query, warnings, timeout=20):
    """POST GraphQL query to Linear. Returns dict or None on failure.

    Uses curl (not urllib) to avoid macOS Python SSL cert issues — curl uses
    the system keychain, urllib does not unless certifi is installed."""
    api_key = _load_linear_api_key(warnings)
    if not api_key:
        return None
    body = json.dumps({"query": query})
    try:
        r = subprocess.run(
            [
                "curl", "-sS", "--max-time", str(timeout),
                "-X", "POST",
                "-H", "Content-Type: application/json",
                # Linear quirk: API keys passed without "Bearer " prefix
                "-H", f"Authorization: {api_key}",
                "-d", body,
                "https://api.linear.app/graphql",
            ],
            capture_output=True, text=True, timeout=timeout + 5,
        )
        if r.returncode != 0:
            warnings.append(f"linear curl exit {r.returncode}: {r.stderr.strip()[:160]}")
            return None
        return json.loads(r.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, json.JSONDecodeError) as e:
        warnings.append(f"linear query error: {e}")
        return None


def fetch_linear_tickets(since_iso, warnings):
    """Fetch issues assigned to me with updatedAt >= since_iso.
    Returns list of dicts with: identifier, title, state_name, state_type,
    completedAt, startedAt, updatedAt. Empty list on failure."""
    q = """
    query {
      issues(filter: {assignee: {isMe: {eq: true}}, updatedAt: {gte: "%s"}},
             first: 100, orderBy: updatedAt) {
        nodes {
          identifier title updatedAt completedAt startedAt
          state { name type }
        }
      }
    }
    """ % since_iso
    data = _linear_query(q, warnings)
    if not data or "data" not in data:
        if data and "errors" in data:
            warnings.append(f"linear graphql errors: {data['errors']}")
        return []
    nodes = (data.get("data", {}).get("issues") or {}).get("nodes") or []
    out = []
    for n in nodes:
        st = n.get("state") or {}
        out.append({
            "identifier": n.get("identifier", ""),
            "title": n.get("title", ""),
            "state_name": st.get("name", ""),
            "state_type": st.get("type", ""),
            "completedAt": n.get("completedAt"),
            "startedAt": n.get("startedAt"),
            "updatedAt": n.get("updatedAt"),
        })
    return out


def compute_linear_summary(target_date_str, linear_tickets):
    """Compute (tickets_closed_today, tickets_reopened_today) for target date."""
    closed = reopened = 0
    for t in linear_tickets:
        cmpl = (t.get("completedAt") or "")[:10] if t.get("completedAt") else ""
        upd = (t.get("updatedAt") or "")[:10] if t.get("updatedAt") else ""
        if t.get("state_type") == "completed" and cmpl == target_date_str:
            closed += 1
        if (t.get("state_name") or "").lower() == "reopened" and upd == target_date_str:
            reopened += 1
    return closed, reopened


def _pct_delta(curr, prev):
    """Percent change ((curr - prev) / prev) * 100, rounded to 1dp.

    Returns None when undefined: either operand is None, or prev is 0 and curr
    is non-zero (division by zero / undefined growth rate). Returns 0.0 when
    both are 0 (flat).
    """
    if curr is None or prev is None:
        return None
    if prev == 0:
        return 0.0 if curr == 0 else None
    return round(((curr - prev) / prev) * 100, 1)


def _abs_delta(curr, prev):
    """Absolute change curr - prev. Returns None if either operand is None.

    Floats are rounded to 2dp (cost/cost_per_ticket). Ints stay int.
    """
    if curr is None or prev is None:
        return None
    if isinstance(curr, float) or isinstance(prev, float):
        return round(curr - prev, 2)
    return curr - prev


def _empty_metrics_bucket():
    return {
        "cost": 0.0,
        "late_night_cost": 0.0,
        "tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read": 0,
        "cache_5m": 0,
        "cache_1h": 0,
        "user_msgs": 0,
        "tool_results_total": 0,
        "tool_results_error": 0,
        "session_ids": set(),
    }


def _fold_session_into_metrics(sess, metrics, sessions_meta):
    """Fold one scan_session() result into metrics_by_date + sessions_meta
    accumulators. Mutates `metrics` and `sessions_meta` in place.

    Used by main() so a single transcript walk feeds both today's `sessions`
    dict (for build_rows) and the 12w/90d window state needed by phase-1
    extras — no second pass over the project tree.
    """
    sid = sess["session_id"]
    per_date_cost = {}
    for d_str, pd in (sess.get("per_date") or {}).items():
        m = metrics[d_str]
        m["cost"] += pd.get("cost", 0.0)
        m["late_night_cost"] += pd.get("late_night_cost", 0.0)
        m["tokens"] += pd.get("tokens", 0)
        m["input_tokens"] += pd.get("input_tokens", 0)
        m["output_tokens"] += pd.get("output_tokens", 0)
        m["cache_read"] += pd.get("cache_read", 0)
        m["cache_5m"] += pd.get("cache_5m", 0)
        m["cache_1h"] += pd.get("cache_1h", 0)
        m["user_msgs"] += pd.get("user_msgs", 0)
        m["tool_results_total"] += pd.get("tool_results_total", 0)
        m["tool_results_error"] += pd.get("tool_results_error", 0)
        m["session_ids"].add(sid)
        per_date_cost[d_str] = pd.get("cost", 0.0)
    # Use window-wide tickets_from_msgs for ticket extraction so 12-week
    # sessions whose ticket only appears in old user messages still match the
    # ticket_with_merged_pr set (drives lost_work_cost).
    win_tickets = sess.get("tickets_from_msgs_window") or sess.get("tickets_from_msgs", [])
    win_info = {
        "git_branch": sess.get("git_branch", ""),
        "cwd": sess.get("cwd", ""),
        "encoded_cwd": sess.get("encoded_cwd", ""),
        "tickets_from_msgs": win_tickets,
    }
    sessions_meta[sid] = {
        "ticket": extract_ticket(win_info),
        "encoded_cwd": sess.get("encoded_cwd", ""),
        "git_branch": sess.get("git_branch", ""),
        "per_date_cost": per_date_cost,
        "total_cost": sess.get("cost", 0.0),
        "tickets_from_msgs": win_tickets,
    }


def compute_weekly_aggregates(
    target_date_str, all_prs, metrics_by_date, sessions_meta,
    review_requested_prs, tickets_12w, warnings, weeks=12,
):
    """Compute last `weeks` ISO weeks' aggregates ending in the week containing target_date.

    Each entry carries every metric driven by the new dashboard (Phase 2):
        Base counts: prs_merged, prs_opened, tickets_closed, tickets_reopened
        Cost/tokens: total_cost, cost_per_ticket, output_tokens, user_msgs,
                     output_per_turn, cache_hit_rate, sessions
        Phase-2 KPIs: net_delivery_efficiency (tickets_closed per $100),
                      pr_review_pressure, sessions_per_pr, ticket_to_pr_ratio,
                      cost_per_net_line (USD/line), tool_error_rate_pct,
                      late_night_cost_pct, lost_work_cost (USD), row_health
                      ('green'/'yellow'/'red')
        Per-week deltas vs previous week: wow_delta with `_pct` and `_abs`

    Costs/tokens/tools come from `metrics_by_date`; per-session cost+ticket
    join (lost_work_cost) walks `sessions_meta`. PR data from `all_prs`
    (authored by me; fields include additions, deletions, headRefName).
    `review_requested_prs` (separate gh query) drives PR Review Pressure.

    `prs_in_progress` per past week is intentionally NOT computed: it requires
    point-in-time state ("open at end of week W"), which `gh search prs` doesn't
    expose cleanly. Only the current target date emits a meaningful number,
    rendered separately via `prs_in_progress` at the top level.
    """
    try:
        target_d = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    except ValueError:
        return []
    target_y, target_w, _ = target_d.isocalendar()
    target_monday = date.fromisocalendar(target_y, target_w, 1)

    # Build week buckets (oldest -> newest)
    week_buckets = []
    for offset in range(weeks - 1, -1, -1):
        wm = target_monday - timedelta(weeks=offset)
        wsun = wm + timedelta(days=6)
        wy, ww, _ = wm.isocalendar()
        week_buckets.append({
            "monday": wm, "sunday": wsun, "iso_year": wy, "iso_week": ww,
            "dates": [(wm + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)],
        })

    # PRs merged + opened per ISO week, plus net additions per week (for cost_per_net_line)
    prs_merged_by_week: dict = defaultdict(int)
    prs_opened_by_week: dict = defaultdict(int)
    net_lines_by_week: dict = defaultdict(int)
    merged_prs_by_week: dict = defaultdict(list)  # for branch-join (lost_work)
    for pr in all_prs:
        # Opened: bucket by createdAt week regardless of current state
        cd_open = (pr.get("createdAt") or "")[:10]
        if cd_open:
            try:
                pd_ = datetime.strptime(cd_open, "%Y-%m-%d").date()
                py, pw, _ = pd_.isocalendar()
                prs_opened_by_week[(py, pw)] += 1
            except ValueError:
                pass
        if pr.get("state") == "merged":
            cd = (pr.get("closedAt") or "")[:10]
            if cd:
                try:
                    pd_ = datetime.strptime(cd, "%Y-%m-%d").date()
                    py, pw, _ = pd_.isocalendar()
                    prs_merged_by_week[(py, pw)] += 1
                    add = pr.get("additions", 0) or 0
                    delete = pr.get("deletions", 0) or 0
                    net_lines_by_week[(py, pw)] += (add - delete)
                    merged_prs_by_week[(py, pw)].append(pr)
                except ValueError:
                    continue

    # PR Review Pressure: number of PRs where I'm requested as reviewer,
    # bucketed by createdAt week (proxy for "incoming load this week").
    review_pressure_by_week: dict = defaultdict(int)
    for pr in (review_requested_prs or []):
        cd_open = (pr.get("createdAt") or "")[:10]
        if not cd_open:
            continue
        try:
            pd_ = datetime.strptime(cd_open, "%Y-%m-%d").date()
            py, pw, _ = pd_.isocalendar()
            review_pressure_by_week[(py, pw)] += 1
        except ValueError:
            pass

    # All-time ticket -> merged-PR map for lost_work_cost matching.
    # A session's ticket "has output" if any merged PR's title or headRefName
    # contains the same ticket. Window-bounded to all_prs (limit=200), which is
    # ample for 12-week lookback.
    ticket_with_merged_pr: set = set()
    for pr in all_prs:
        if pr.get("state") != "merged":
            continue
        for src in (pr.get("title", ""), pr.get("headRefName", "")):
            for m in TICKET_PR_RE.finditer(src or ""):
                ticket_with_merged_pr.add(m.group(1).upper())

    # Tickets closed + reopened per ISO week. tickets_12w is fetched once at
    # the compute_phase1_extras layer (covers the 12-week window) and passed
    # through here so we don't re-query Linear.
    tickets_closed_by_week: dict = defaultdict(int)
    tickets_reopened_by_week: dict = defaultdict(int)
    for t in tickets_12w:
        if t.get("state_type") == "completed":
            cd = (t.get("completedAt") or "")[:10]
            if cd:
                try:
                    td_ = datetime.strptime(cd, "%Y-%m-%d").date()
                    ty, tw, _ = td_.isocalendar()
                    tickets_closed_by_week[(ty, tw)] += 1
                except ValueError:
                    pass
        if (t.get("state_name") or "").lower() == "reopened":
            upd = (t.get("updatedAt") or "")[:10]
            if upd:
                try:
                    td_ = datetime.strptime(upd, "%Y-%m-%d").date()
                    ty, tw, _ = td_.isocalendar()
                    tickets_reopened_by_week[(ty, tw)] += 1
                except ValueError:
                    pass

    out = []
    for w in week_buckets:
        # Aggregate transcript-derived metrics across the 7 dates in this week
        cost = 0.0
        late_night = 0.0
        out_tokens = 0
        in_tokens = 0
        cache_read = 0
        cache_5m = 0
        cache_1h = 0
        u_msgs = 0
        tr_total = 0
        tr_error = 0
        sids: set = set()
        for d in w["dates"]:
            m = metrics_by_date.get(d)
            if not m:
                continue
            cost += m["cost"]
            late_night += m["late_night_cost"]
            out_tokens += m["output_tokens"]
            in_tokens += m["input_tokens"]
            cache_read += m["cache_read"]
            cache_5m += m["cache_5m"]
            cache_1h += m["cache_1h"]
            u_msgs += m["user_msgs"]
            tr_total += m["tool_results_total"]
            tr_error += m["tool_results_error"]
            sids.update(m["session_ids"])
        billed = in_tokens + cache_read + cache_5m + cache_1h
        chr_ = round(cache_read / billed, 4) if billed > 0 else 0
        opt = int(out_tokens / u_msgs) if u_msgs > 0 else None

        prs_m = prs_merged_by_week.get((w["iso_year"], w["iso_week"]), 0)
        prs_o = prs_opened_by_week.get((w["iso_year"], w["iso_week"]), 0)
        tx_c = tickets_closed_by_week.get((w["iso_year"], w["iso_week"]), 0)
        tx_r = tickets_reopened_by_week.get((w["iso_year"], w["iso_week"]), 0)
        cpt = round(cost / tx_c, 2) if tx_c > 0 else None
        net_lines = net_lines_by_week.get((w["iso_year"], w["iso_week"]), 0)
        rev_pressure = review_pressure_by_week.get((w["iso_year"], w["iso_week"]), 0)

        # Phase-2 KPIs
        # Net delivery efficiency: tickets closed per $100 spend
        nde = round(tx_c / (cost / 100.0), 1) if cost > 0 else None
        # Sessions per PR: total sessions / merged PRs
        spp = round(len(sids) / prs_m, 1) if prs_m > 0 else None
        # Ticket-to-PR ratio: closed tickets per merged PR
        tpr = round(tx_c / prs_m, 1) if prs_m > 0 else None
        # Cost per net line of code (USD/line). Negative net (deletions > adds)
        # is unusual — render as None and let SKILL.md show "—".
        cpnl = round(cost / net_lines, 4) if net_lines > 0 else None
        # Tool error rate as percent (0-100)
        ter = round(100.0 * tr_error / tr_total, 1) if tr_total > 0 else 0.0
        # Late-night cost as percent of week total
        lnp = round(100.0 * late_night / cost, 1) if cost > 0 else 0.0
        # Lost work cost: sessions in this week with a ticket but no
        # matching merged PR (anywhere in the 12w window).
        lost = 0.0
        for sid, meta in sessions_meta.items():
            tk = meta.get("ticket")
            if not tk:
                continue
            if tk in ticket_with_merged_pr:
                continue
            wcost = sum(
                meta.get("per_date_cost", {}).get(d, 0.0)
                for d in w["dates"]
            )
            lost += wcost

        out.append({
            "week_label": f"W{w['iso_week']:02d}",
            "iso_year": w["iso_year"],
            "iso_week": w["iso_week"],
            "week_start_date": w["monday"].strftime("%Y-%m-%d"),
            "prs_merged": prs_m,
            "prs_opened": prs_o,
            "tickets_closed": tx_c,
            "tickets_reopened": tx_r,
            "total_cost": round(cost, 2),
            "cost_per_ticket": cpt,
            "sessions": len(sids),
            "cache_hit_rate": chr_,
            "output_tokens": out_tokens,
            "user_msgs": u_msgs,
            "output_per_turn": opt,
            # Phase-2 fields
            "net_delivery_efficiency": nde,
            "pr_review_pressure": rev_pressure,
            "sessions_per_pr": spp,
            "ticket_to_pr_ratio": tpr,
            "net_lines": net_lines,
            "cost_per_net_line": cpnl,
            "tool_error_rate_pct": ter,
            "late_night_cost_pct": lnp,
            "lost_work_cost": round(lost, 2),
        })

    # Attach wow_delta (None for oldest entry). Reopened up is bad — we still
    # report it as a percent; downstream renderer decides ↑/↓ semantics.
    delta_fields = (
        "prs_merged", "prs_opened", "tickets_closed", "tickets_reopened",
        "total_cost", "cost_per_ticket", "sessions", "output_per_turn",
        "net_delivery_efficiency", "pr_review_pressure",
        "sessions_per_pr", "ticket_to_pr_ratio",
        "cost_per_net_line", "tool_error_rate_pct",
        "late_night_cost_pct", "lost_work_cost",
    )
    for i, entry in enumerate(out):
        if i == 0:
            entry["wow_delta"] = None
        else:
            prev = out[i - 1]
            entry["wow_delta"] = {}
            for k in delta_fields:
                entry["wow_delta"][f"{k}_pct"] = _pct_delta(entry[k], prev[k])
                entry["wow_delta"][f"{k}_abs"] = _abs_delta(entry[k], prev[k])

    # Row health: per-row deviation vs 12w MEDIAN of other rows. Median is more
    # robust than mean against outlier weeks. Bands per PM spec (2026-05-01):
    #   🟢 green:  all monitored metrics within median ± 50%
    #   🟡 yellow: 1+ metrics outside median ± 100% (i.e. > median×2 worse,
    #              or for "lower-is-better" metrics curr < median/2)
    #   🔴 red:    any metric > median × 3 worse, OR
    #              net_delivery_efficiency < median × 0.3
    # Filters:
    #   - Need >= 4 weeks of non-None values for the metric to count.
    #   - Skip metrics whose median absolute value < 3 (small-sample noise:
    #     e.g. reopened typically 1-2 — a jump to 3 would falsely red-flag).
    #     EXCEPT net_delivery_efficiency: NDE bypasses the floor for its own
    #     collapse check (typical NDE 1-3, but the < median × 0.3 rule is
    #     explicit user intent).
    #   - ticket_to_pr_ratio uses absolute bounds (< 0.5 OR > 3.0) — no
    #     median needed; it's a structural shape signal.
    # higher_is_worse=True means a higher value is worse (e.g. tool_error_rate).
    def _median(xs):
        s = sorted(xs)
        m = len(s)
        if m == 0:
            return None
        return s[m // 2] if m % 2 == 1 else (s[m // 2 - 1] + s[m // 2]) / 2.0

    # Per-metric small-sample floor: below this, the metric's typical value is
    # too small for relative deviations (×2/×3 of a tiny baseline) to be a
    # useful signal. Tuned per metric type:
    #   counts (3-5): tickets, sessions
    #   percentages (5-10): rates that swing naturally
    #   dollars (20): lost-work below $20/week is dust
    monitored = [
        # (field, higher_is_worse, floor)
        ("net_delivery_efficiency", False, 3),    # tickets per $100
        ("pr_review_pressure", True, 3),          # count of PRs
        ("sessions_per_pr", True, 5),             # typical 5-15
        ("ticket_to_pr_ratio", None, 0),          # absolute bounds path
        ("tool_error_rate_pct", True, 5),         # %, < 5% normal
        ("late_night_cost_pct", True, 10),        # %, swings 0-30 normally
        ("lost_work_cost", True, 20),             # USD; below $20 is dust
        ("tickets_reopened", True, 3),            # count
    ]
    # Metrics excluded from row_health scoring: still computed and emitted in
    # weekly_aggregates / Notion DB, but no longer flip green/yellow/red. PM
    # spec (2026-05-01): non-actionable signals shouldn't drive the health
    # status — only metrics with a clear "what to do about it" remain.
    #   cost_per_net_line:   no clear action path when anomalous
    #   late_night_cost_pct: not actionable (can't un-work nights)
    #   sessions:            only meaningful via sessions_per_pr ratio
    HEALTH_EXCLUDED_METRICS = {
        "cost_per_net_line",
        "late_night_cost_pct",
        "sessions",
    }
    monitored = [m for m in monitored if m[0] not in HEALTH_EXCLUDED_METRICS]
    MIN_HISTORY_WEEKS = 4
    n = len(out)
    for i, entry in enumerate(out):
        breaches_yellow = 0  # metrics outside median ± 100% (worse side)
        breaches_red = 0     # metrics > median × 3 worse
        nde_collapsed = False
        for field, hiw, floor in monitored:
            curr = entry.get(field)
            if curr is None:
                continue
            others = [
                out[j].get(field) for j in range(n)
                if j != i and out[j].get(field) is not None
            ]
            if len(others) < MIN_HISTORY_WEEKS:
                continue
            # ticket_to_pr_ratio: absolute bounds, no median path
            if field == "ticket_to_pr_ratio":
                if curr < 0.5 or curr > 3.0:
                    breaches_yellow += 1
                continue
            median = _median(others)
            if median is None or median == 0:
                continue
            # Small-sample floor: per-metric "noise threshold". Below this the
            # baseline is too small for relative deviations to be useful signal.
            if median < floor:
                continue
            ratio = (curr - median) / median
            pct_worse = (ratio if hiw else -ratio) * 100
            # Bands: > median × 3 worse → +200% (or worse). Yellow at > +100%.
            if pct_worse > 200:
                breaches_red += 1
            elif pct_worse > 100:
                breaches_yellow += 1
            # NDE collapse: < median × 0.3 → red. Only fires when team has a
            # healthy baseline (median >= SMALL_SAMPLE_FLOOR), which the floor
            # check above ensures.
            if field == "net_delivery_efficiency" and curr < median * 0.3:
                nde_collapsed = True

        if breaches_red > 0 or nde_collapsed:
            entry["row_health"] = "red"
        elif breaches_yellow > 0:
            entry["row_health"] = "yellow"
        else:
            entry["row_health"] = "green"

    return out


def compute_historical_averages(target_date_str, metrics_by_date):
    """Compute 7d/30d/90d rolling averages of cost (USD) and tokens, ending the
    day BEFORE target_date_str (so today's still-in-progress data doesn't skew
    the baseline against itself).

    Also includes `daily_series`: 90 entries (oldest first, last entry = target)
    of {date, cost, tokens} suitable for Mermaid xychart rendering.

    Returns dict, or None if target_date_str is malformed.
    """
    try:
        target_d = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    except ValueError:
        return None

    def _get(d_str, key, default):
        m = metrics_by_date.get(d_str)
        return m[key] if m else default

    def _avg_window(days):
        cost_sum = 0.0
        tok_sum = 0
        for i in range(1, days + 1):
            d_str = (target_d - timedelta(days=i)).strftime("%Y-%m-%d")
            cost_sum += _get(d_str, "cost", 0.0)
            tok_sum += _get(d_str, "tokens", 0)
        return {
            "cost": round(cost_sum / days, 2),
            "tokens": int(tok_sum / days),
        }

    daily_series = []
    for i in range(89, -1, -1):  # 90 entries, oldest first, last = target
        d_str = (target_d - timedelta(days=i)).strftime("%Y-%m-%d")
        daily_series.append({
            "date": d_str,
            "cost": round(_get(d_str, "cost", 0.0), 2),
            "tokens": _get(d_str, "tokens", 0),
        })

    return {
        "avg_7d": _avg_window(7),
        "avg_30d": _avg_window(30),
        "avg_90d": _avg_window(90),
        "daily_series": daily_series,
    }


def compute_phase1_extras(projects_dir, target_date_str, metrics_by_date,
                          sessions_meta, warnings):
    """Compute all Phase 1 dashboard extras: PR breakdown, Linear tickets,
    weekly aggregates, historical averages. Returns dict; any field is
    None/[] on failure.

    `metrics_by_date` and `sessions_meta` are pre-computed by main()'s single
    transcript walk (covering today + the 12-week + 90-day window) so we don't
    re-walk the projects tree here. Linear is also fetched only once — with
    the 12-week window, then filtered for the this-week subset.
    """
    gh_user = _gh_user(warnings)
    # GraphQL search connection caps `first` at 100; 12-week window of authored
    # PRs (~60-120 typical) fits within that.
    all_prs = _gh_search_prs(gh_user, warnings, limit=100) if gh_user else []
    review_requested_prs = (
        _gh_search_review_requested_prs(gh_user, warnings, limit=200)
        if gh_user else []
    )

    prs_opened, prs_merged, prs_in_progress = compute_pr_metrics(target_date_str, all_prs)
    prs_this_week_list = compute_prs_this_week_list(target_date_str, all_prs)

    # Linear: fetch once with the 12-week window (used by weekly_aggregates),
    # then derive the this-week subset locally so we only hit the API once.
    # Note: both queries use the same `first: 100` cap and updatedAt order, so
    # the 12w response is a strict superset of the this-week response.
    try:
        target_d = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        ty, tw, _ = target_d.isocalendar()
        this_mon = date.fromisocalendar(ty, tw, 1)
        target_monday = this_mon
        oldest_week_monday = target_monday - timedelta(weeks=11)
        since_iso_12w = oldest_week_monday.strftime("%Y-%m-%dT00:00:00Z")
        this_mon_str = this_mon.strftime("%Y-%m-%d")
    except ValueError:
        since_iso_12w = target_date_str + "T00:00:00Z"
        this_mon_str = target_date_str
    tickets_12w = fetch_linear_tickets(since_iso_12w, warnings)
    # this-week subset: tickets with updatedAt >= this Monday (lexicographic
    # YYYY-MM-DD compare matches date order).
    linear_tickets_this_week = [
        t for t in tickets_12w
        if (t.get("updatedAt") or "")[:10] >= this_mon_str
    ]
    tickets_closed_today, tickets_reopened_today = compute_linear_summary(
        target_date_str, linear_tickets_this_week
    )

    weekly_aggregates = compute_weekly_aggregates(
        target_date_str, all_prs, metrics_by_date, sessions_meta,
        review_requested_prs, tickets_12w, warnings, weeks=12,
    )
    historical_averages = compute_historical_averages(
        target_date_str, metrics_by_date
    )

    return {
        "prs_opened": prs_opened,
        "prs_merged": prs_merged,
        "prs_in_progress": prs_in_progress,
        "prs_this_week_list": prs_this_week_list,
        "tickets_closed_today": tickets_closed_today,
        "tickets_reopened_today": tickets_reopened_today,
        "linear_tickets_this_week": linear_tickets_this_week,
        "weekly_aggregates": weekly_aggregates,
        "historical_averages": historical_averages,
    }


def compute_monthly_stats(rows, sessions, target_dates):
    """Compute aggregated monthly stats from rows + sessions."""
    base = compute_daily_stats(rows, sessions)
    # Count active days from daily_breakdown
    breakdown = compute_daily_breakdown(sessions, target_dates)
    active_days = sum(1 for d in breakdown if d["total_cost"] > 0 or d["total_sessions"] > 0)
    base["active_days"] = active_days
    # Unique tickets
    all_tickets = set()
    for r in rows:
        all_tickets.update(r.get("tickets", []))
    base["unique_tickets"] = len(all_tickets)
    return base


def compute_monthly_extras(start_str, end_str, warnings):
    """Fetch PR and Linear data for a month range."""
    gh_user = _gh_user(warnings)
    all_prs = _gh_search_prs(gh_user, warnings, limit=100) if gh_user else []

    # Filter PRs to the month range
    prs_merged = 0
    prs_opened = 0
    for pr in all_prs:
        created = (pr.get("createdAt") or "")[:10]
        merged = (pr.get("mergedAt") or "")[:10]
        if created and start_str <= created <= end_str:
            prs_opened += 1
        if merged and start_str <= merged <= end_str:
            prs_merged += 1

    # Linear tickets
    since_iso = start_str + "T00:00:00Z"
    linear_tickets = fetch_linear_tickets(since_iso, warnings)
    # Filter to month range and count
    tickets_closed = 0
    tickets_reopened = 0
    for t in linear_tickets:
        cmpl = (t.get("completedAt") or "")[:10]
        if t.get("state_type") == "completed" and cmpl and start_str <= cmpl <= end_str:
            tickets_closed += 1
        upd = (t.get("updatedAt") or "")[:10]
        if (t.get("state_name") or "").lower() == "reopened" and upd and start_str <= upd <= end_str:
            tickets_reopened += 1

    return {
        "prs_merged": prs_merged,
        "prs_opened": prs_opened,
        "tickets_closed": tickets_closed,
        "tickets_reopened": tickets_reopened,
    }


def compute_behavioral_metrics(sessions, rows):
    """Aggregate behavioral metrics from row-level counters.

    Row-level behavioral_* fields are computed during scan_session() over
    EVERY user message (not limited to the 8-snippet truncation). This
    function aggregates them across all rows for the final output.
    """
    # Aggregate from row-level counters (computed during full .jsonl scan)
    slash_cmds = {}
    short_corrections = 0
    short_confirms = 0
    cjk_chars = 0
    alpha_chars = 0
    figma_urls = 0
    interruptions = 0
    total_msgs = 0

    for r in rows:
        for cmd, count in r.get("behavioral_slash_cmds", {}).items():
            slash_cmds[cmd] = slash_cmds.get(cmd, 0) + count
        short_corrections += r.get("behavioral_short_corrections", 0)
        short_confirms += r.get("behavioral_short_confirms", 0)
        cjk_chars += r.get("behavioral_cjk_chars", 0)
        alpha_chars += r.get("behavioral_alpha_chars", 0)
        figma_urls += r.get("behavioral_figma_urls", 0)
        interruptions += r.get("behavioral_interruptions", 0)
        total_msgs += r.get("user_msgs", 0)

    total_alpha = cjk_chars + alpha_chars
    zh_ratio = round(cjk_chars / total_alpha, 3) if total_alpha > 0 else 0

    # Session length distribution stays from rows (not affected by snippet limit)
    dist = {"0": 0, "1-3": 0, "4-10": 0, "11-20": 0, "21-50": 0, "50+": 0}
    for r in rows:
        um = r.get("user_msgs", 0)
        if um == 0:
            dist["0"] += 1
        elif um <= 3:
            dist["1-3"] += 1
        elif um <= 10:
            dist["4-10"] += 1
        elif um <= 20:
            dist["11-20"] += 1
        elif um <= 50:
            dist["21-50"] += 1
        else:
            dist["50+"] += 1

    return {
        "short_correction_count": short_corrections,
        "short_confirm_count": short_confirms,
        "slash_cmd_histogram": dict(sorted(slash_cmds.items(), key=lambda x: -x[1])),
        "language_ratio": {"zh": zh_ratio, "en": round(1 - zh_ratio, 3)},
        "figma_url_count": figma_urls,
        "session_length_dist": dist,
        "user_interruptions": interruptions,
        "total_user_messages": total_msgs,
    }


def main():
    parser = argparse.ArgumentParser(
        prog="parse.py",
        description="Daily-summary CLI: scan ~/.claude/projects/*/*.jsonl and emit metrics.",
    )
    parser.add_argument(
        "--target-date",
        metavar="YYYY-MM-DD",
        help="Single-day mode (Asia/Hong_Kong calendar). Default: today.",
    )
    parser.add_argument(
        "--range",
        metavar="START:END",
        help="Range mode, inclusive. Format: YYYY-MM-DD:YYYY-MM-DD",
    )
    parser.add_argument(
        "--month",
        metavar="YYYY-MM",
        help="Month mode. Expands to full-month range + enables PR/Linear + behavioral metrics.",
    )
    parser.add_argument(
        "--projects-dir",
        metavar="PATH",
        default=None,
        help="Override projects directory (default: ~/.claude/projects).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON to stdout (otherwise emit human-readable text).",
    )
    args = parser.parse_args()

    mode_count = sum(1 for x in [args.target_date, args.range, args.month] if x)
    if mode_count > 1:
        print("error: --target-date, --range, and --month are mutually exclusive", file=sys.stderr)
        sys.exit(2)

    # Resolve target dates
    if args.month:
        try:
            year, mon = args.month.split("-")
            year, mon = int(year), int(mon)
            last_day = calendar.monthrange(year, mon)[1]
            start = f"{year:04d}-{mon:02d}-01"
            end = f"{year:04d}-{mon:02d}-{last_day:02d}"
            target_dates_list = daterange_inclusive(start, end)
            target_range = {"start": start, "end": end}
        except (ValueError, IndexError) as e:
            print(f"error: invalid --month '{args.month}': {e}", file=sys.stderr)
            sys.exit(2)
        do_month = True
    elif args.range:
        try:
            start, end = args.range.split(":", 1)
            target_dates_list = daterange_inclusive(start, end)
        except ValueError as e:
            print(f"error: invalid --range '{args.range}': {e}", file=sys.stderr)
            sys.exit(2)
        target_range = {"start": start, "end": end}
        do_month = False
    elif args.target_date:
        try:
            datetime.strptime(args.target_date, "%Y-%m-%d")
        except ValueError:
            print(f"error: invalid --target-date '{args.target_date}'", file=sys.stderr)
            sys.exit(2)
        target_dates_list = [args.target_date]
        target_range = {"start": args.target_date, "end": args.target_date}
        do_month = False
    else:
        # Default: today in Asia/Hong_Kong
        today_hk = datetime.now(HK_TZ).strftime("%Y-%m-%d")
        target_dates_list = [today_hk]
        target_range = {"start": today_hk, "end": today_hk}
        do_month = False

    target_dates = set(target_dates_list)

    # Resolve projects dir
    projects_dir = Path(
        args.projects_dir or os.path.expanduser("~/.claude/projects")
    )
    if not projects_dir.exists():
        warnings = [f"projects directory not found: {projects_dir}"]
        user_config = load_user_config(warnings)
        result = {
            "schema_version": SCHEMA_VERSION,
            "target_range": target_range,
            "rows": [],
            "daily_stats": compute_daily_stats([], {}),
            "daily_breakdown": [{"date": d, "total_cost": 0, "cost_opus": 0, "cost_sonnet": 0, "cost_haiku": 0, "total_tokens": 0, "total_sessions": 0, "active_hours": 0, "thinking_hours": 0, "subagent_spawns": 0, "cache_hit_rate": 0, "dollars_per_ai_hour": 0} for d in sorted(target_dates)],
            "warnings": warnings,
            "user_config": user_config,
        }
    else:
        warnings = []
        # Single-target-date mode also needs the 12-week + 90-day window for
        # phase-1 extras. Build the union of dates upfront and walk the
        # projects tree exactly once — same scan_session() result feeds both
        # today's `sessions` dict (filtered by target_dates) and the window
        # state (`metrics_by_date` + `sessions_meta`) used by extras.
        do_extras = (not args.range and not args.month and target_range["start"] == target_range["end"])
        scan_dates = set(target_dates)
        if do_extras:
            try:
                target_d = datetime.strptime(target_range["start"], "%Y-%m-%d").date()
                ty, tw, _ = target_d.isocalendar()
                target_monday = date.fromisocalendar(ty, tw, 1)
                oldest_week_monday = target_monday - timedelta(weeks=11)
                oldest_90d = target_d - timedelta(days=90)
                window_start = min(oldest_week_monday, oldest_90d)
                d = window_start
                while d <= target_d:
                    scan_dates.add(d.strftime("%Y-%m-%d"))
                    d += timedelta(days=1)
            except ValueError:
                do_extras = False

        sessions = {}
        metrics_by_date = defaultdict(_empty_metrics_bucket)
        sessions_meta: dict = {}
        for jp in projects_dir.rglob("*.jsonl"):
            # Pass target_dates as the "focus" (drives session-level fields)
            # and scan_dates as the wider "window" (drives per_date buckets).
            # When do_extras is False, scan_dates == target_dates and the call
            # collapses to the original single-set behavior.
            sess = scan_session(jp, target_dates, warnings,
                                window_dates=scan_dates)
            if not sess:
                continue
            # Today's `sessions` dict: only sessions with activity on a
            # target_date. per_date keys span the window; intersect with
            # target_dates to keep just today-active sessions.
            sess_dates = set((sess.get("per_date") or {}).keys())
            if sess_dates & target_dates:
                sessions[sess["session_id"]] = sess
            # Always fold into the window-wide accumulators (covers union).
            if do_extras:
                _fold_session_into_metrics(sess, metrics_by_date, sessions_meta)

        rows_internal = build_rows(sessions, warnings)
        rows_serialized = [serialize_row(r) for r in rows_internal]
        daily_stats = compute_daily_stats(rows_internal, sessions)
        daily_breakdown = compute_daily_breakdown(sessions, target_dates)

        # Phase 1 dashboard extras: only computed in single-target-date mode.
        # Skipped for --range (range scans don't make sense for "today's PRs").
        if do_extras:
            extras = compute_phase1_extras(
                projects_dir, target_range["start"],
                metrics_by_date, sessions_meta, warnings,
            )
        elif do_month:
            month_extras = compute_monthly_extras(
                target_range["start"], target_range["end"], warnings,
            )
        else:
            extras = {
                "prs_opened": None,
                "prs_merged": None,
                "prs_in_progress": None,
                "prs_this_week_list": [],
                "tickets_closed_today": None,
                "tickets_reopened_today": None,
                "linear_tickets_this_week": [],
                "weekly_aggregates": [],
                "historical_averages": None,
            }

        user_config = load_user_config(warnings)
        result = {
            "schema_version": SCHEMA_VERSION,
            "target_range": target_range,
            "rows": rows_serialized,
            "daily_stats": daily_stats,
            "daily_breakdown": daily_breakdown,
            "warnings": warnings,
            "user_config": user_config,
        }
        if do_extras:
            result.update(extras)
        if do_month:
            result["monthly_extras"] = month_extras
            result["behavioral"] = compute_behavioral_metrics(sessions, rows_internal)
            result["monthly_stats"] = compute_monthly_stats(rows_internal, sessions, target_dates)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_text(result))


if __name__ == "__main__":
    main()
