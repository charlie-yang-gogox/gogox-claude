#!/usr/bin/env python3
"""
Claude Code session metrics — tracks time, tokens, costs, and agent usage.

Usage:
    python3 session_metrics.py                     # Auto-detect current session
    python3 session_metrics.py --pid 12345         # Find session by PID
    python3 session_metrics.py --session-id UUID   # Use specific session
    python3 session_metrics.py --no-linear         # Skip Linear posting
    python3 session_metrics.py --no-csv            # Skip CSV output
    python3 session_metrics.py --json              # Output JSON instead of Markdown
"""

import argparse
import csv
import fcntl
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Pricing ($ per million tokens)
# ---------------------------------------------------------------------------
PRICING = {
    "opus": {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50},
    "sonnet": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    "haiku": {"input": 0.80, "output": 4.0, "cache_write": 1.0, "cache_read": 0.08},
}

# ---------------------------------------------------------------------------
# Story Point → Estimated Manual Hours
# ---------------------------------------------------------------------------
STORY_POINT_HOURS = {
    1: 1.5,    # 1-2 hours
    2: 4.0,    # 0.5 day
    3: 8.0,    # 1 day
    5: 16.0,   # 2 days
    8: 24.0,   # 3 days
    13: 40.0,  # 5 days
}


def story_point_seconds(sp: int | None) -> float | None:
    """Convert story points to estimated manual seconds, or None."""
    if sp is not None and sp in STORY_POINT_HOURS:
        return STORY_POINT_HOURS[sp] * 3600
    return None


def hours_to_story_point(hours: float | None) -> int | None:
    """Snap an LLM-estimated pure-manual hour figure to the nearest story-point bucket."""
    if hours is None:
        return None
    return min(STORY_POINT_HOURS, key=lambda sp: abs(STORY_POINT_HOURS[sp] - hours))

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
CLAUDE_DIR = Path.home() / ".claude"
METRICS_DIR = CLAUDE_DIR / "metrics"
CSV_PATH = METRICS_DIR / "session_metrics.csv"
SESSIONS_DIR = CLAUDE_DIR / "sessions"
PROJECTS_DIR = CLAUDE_DIR / "projects"
SETTINGS_PATH = CLAUDE_DIR / "settings.json"

# ---------------------------------------------------------------------------
# CSV schema
# ---------------------------------------------------------------------------
CSV_FIELDS = [
    "timestamp",
    "ticket_id",
    "session_id",
    "git_branch",
    "wall_clock_sec",
    "active_sec",
    "idle_sec",
    "user_msgs",
    "assistant_msgs",
    "tool_calls",
    "total_turns",
    "model",
    "input_tokens",
    "output_tokens",
    "cache_write_tokens",
    "cache_read_tokens",
    "total_tokens",
    "estimated_cost",
    "agent_count",
    "agent_total_wallclock_sec",
    "agent_total_tokens",
    "agent_total_cost",
    "story_points",
    "manual_hours",
    "estimated_manual_sec",
    "time_saved_multiplier",
    "claude_code_version",
    # GGC-74: appended at END for backward compat (DictWriter backfills empty
    # for old rows). run_stem = the dispatcher (ticket,run) upsert key;
    # metrics_provenance = compact "stem:cost;stem:cost" of the summed
    # subagent transcripts so a double-count is visible, not hidden in a scalar.
    "run_stem",
    "metrics_provenance",
    # Appended at END (DictWriter backfills empty for old rows). active_proxy_sec
    # = summed capped inter-record gaps — a busy-time proxy for the batch scan
    # path where active_sec is structurally 0 (no turn_duration in subagent
    # transcripts). Distinct from active_sec: includes tool wall time.
    "active_proxy_sec",
    # Outcome signals (PR2): value-delivered dimensions gathered at done-time
    # from the ticket's PR (gh) by the finalize stage and passed via
    # --outcome-json. These convert the cost columns above into ROI: cost is an
    # INPUT, a merged PR is delivered VALUE. All empty when no PR/outcome is
    # available (standalone runs, non-shipping tickets) — never fabricated.
    "pr_url",
    "pr_merged",            # 1 / 0 / "" — the one hard-to-fake value signal
    "pr_state",             # MERGED | OPEN | CLOSED | DRAFT
    "cycle_time_sec",       # PR createdAt -> mergedAt; "" until actually merged (GGC-125 R5)
    "review_rounds",        # count of CHANGES_REQUESTED review submissions
    "first_pass_accepted",  # 1 if merged with zero CHANGES_REQUESTED, else 0
    "reviewer_comments",    # review/issue comments not authored by the PR author
    "ci_fail_count",        # failed check-runs on the head sha (best-effort)
    # Which denominator time_saved_multiplier used: active | active_proxy | "".
    # Without it, a consumer that averages the multiplier across rows mixes
    # turn-duration-based (main loop) and gap-proxy-based (batch) denominators —
    # different quantities. Written on the first model row only (see write_csv).
    "multiplier_basis",
]


# ===================================================================
# Helpers
# ===================================================================


def cwd_to_project_dir_name(cwd: str) -> str:
    """Convert a cwd path to Claude's project directory name.

    Claude replaces both ``/`` and ``_`` with ``-``.
    """
    return cwd.replace("/", "-").replace("_", "-")


def get_model_family(model_name: str) -> str:
    if not model_name or model_name == "<synthetic>":
        return "synthetic"
    m = model_name.lower()
    if "opus" in m:
        return "opus"
    if "sonnet" in m:
        return "sonnet"
    if "haiku" in m:
        return "haiku"
    return "unknown"


_warned_unknown_models: set[str] = set()


def calculate_cost(usage: dict, model_family: str, model_name: str = "") -> float:
    if model_family == "unknown" and model_name and model_name not in _warned_unknown_models:
        _warned_unknown_models.add(model_name)
        print(
            f"Warning: unknown model '{model_name}', cost will be $0",
            file=sys.stderr,
        )
    if model_family not in PRICING:
        return 0.0
    p = PRICING[model_family]
    return (
        usage.get("input_tokens", 0) * p["input"]
        + usage.get("output_tokens", 0) * p["output"]
        + usage.get("cache_creation_input_tokens", 0) * p["cache_write"]
        + usage.get("cache_read_input_tokens", 0) * p["cache_read"]
    ) / 1_000_000


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"


def format_tokens(n: int) -> str:
    return f"{n:,}"


def format_cost(c: float) -> str:
    return f"${c:.2f}"


def parse_ts(ts):
    """Parse an ISO-8601 string or epoch-ms into a UTC datetime."""
    if isinstance(ts, str):
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    return None


# ===================================================================
# Session discovery
# ===================================================================


def find_session_by_pid(pid: int):
    p = SESSIONS_DIR / f"{pid}.json"
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return None


def find_current_session(cwd: str):
    """Return (session_id, jsonl_path) for the most recent non-trivial
    session in the given project directory."""
    project_name = cwd_to_project_dir_name(cwd)
    project_dir = PROJECTS_DIR / project_name
    if not project_dir.exists():
        return None, None

    jsonl_files = sorted(
        project_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # Prefer files > 1 KB (skip metadata-only)
    for jf in jsonl_files:
        if jf.stat().st_size > 1024:
            return jf.stem, jf
    if jsonl_files:
        return jsonl_files[0].stem, jsonl_files[0]
    return None, None


def find_jsonl_by_session_id(session_id: str):
    """Locate a transcript by session id across ALL project dirs — cwd-independent.

    The transcript is filed under the hash of the session's STARTUP cwd, which
    diverges from the live cwd after an EnterWorktree. Globbing by the (unique)
    session id sidesteps the cwd-hash entirely. Top-level ``*.jsonl`` only —
    subagent transcripts live under ``<dir>/subagents/`` or ``<dir>/<parent-sid>/``
    and are intentionally excluded. Returns (stem, path) or (None, None)."""
    if not session_id:
        return None, None
    for p in sorted(
        PROJECTS_DIR.glob(f"*/{session_id}.jsonl"),
        key=lambda p: p.stat().st_size,
        reverse=True,
    ):
        if "subagents" not in p.parts:
            return p.stem, p
    return None, None


# ===================================================================
# Ticket / branch detection
# ===================================================================

TICKET_RE = re.compile(r"([A-Z]{2,}-\d+)", re.IGNORECASE)


def anchored_ticket_re(ticket_id: str) -> re.Pattern:
    """Anchored, case-insensitive matcher for a ticket id so `GGC-7` never
    matches inside `GGC-74`. The negative lookbehind blocks an alnum prefix and
    the negative lookahead blocks a trailing digit (the only ambiguous suffix
    for the `<LETTERS>-<DIGITS>` shape)."""
    return re.compile(
        r"(?<![A-Za-z0-9])" + re.escape(ticket_id) + r"(?![0-9])",
        re.IGNORECASE,
    )


def detect_ticket_id(cwd: str, git_branch: str | None = None, explicit: str | None = None) -> str:
    if explicit:
        return explicit.upper()
    if git_branch:
        m = TICKET_RE.search(git_branch)
        if m:
            return m.group(1).upper()
    if cwd:
        m = TICKET_RE.search(cwd)
        if m:
            return m.group(1).upper()
    return "UNKNOWN"


def get_git_branch(cwd: str):
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return None


# ===================================================================
# Agent model lookup (subagent JSONL)
# ===================================================================


def get_agent_model(session_dir: Path, agent_id: str) -> str:
    sub = session_dir / "subagents" / f"agent-{agent_id}.jsonl"
    if not sub.exists():
        return "unknown"
    try:
        with open(sub) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("type") == "assistant":
                    msg = d.get("message", {})
                    if isinstance(msg, dict):
                        return msg.get("model", "unknown")
    except Exception:
        pass
    return "unknown"


# ===================================================================
# GGC-74: batch subagent scan + aggregate
# ===================================================================


def _run_ts_to_epoch(run_ts: str) -> float | None:
    """Parse a compact RUN_TS (``YYYYMMDDTHHMMSSZ``, treated as UTC) to epoch
    seconds. Returns None when unparseable."""
    if not run_ts:
        return None
    s = run_ts.strip()
    try:
        dt = datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _first_record(jsonl_path: Path) -> dict | None:
    """Return the first parseable JSON record of a transcript, or None."""
    try:
        with open(jsonl_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return None
    return None


def _invoked_scan_subagents(jsonl_path: Path) -> bool:
    """True if this transcript ITSELF ran ``session_metrics.py --scan-subagents``
    — i.e. it IS the batch metric scanner (GGC-78). Such a transcript is a
    sibling subagent under the same parent session, so without this guard the
    scanner would sum ITSELF into a ticket's total (a small but real
    over-count, visible via the provenance line).

    Detection is by an actual ``Bash`` tool invocation — the command string
    must contain BOTH ``session_metrics`` and ``--scan-subagents`` — NOT a bare
    substring scan. A worker that merely EDITED ``session_metrics.py`` also
    carries the literal ``--scan-subagents`` in its transcript (in an Edit/Write
    tool_use), and must NOT be excluded. Being content-based, this holds
    regardless of the scanner's process cwd — replacing the fragile, incidental
    cwd-based exclusion the bug report flags.
    """
    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                # Cheap pre-filter: skip the JSON parse unless the token is present.
                if "--scan-subagents" not in line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("type") != "assistant":
                    continue
                msg = data.get("message", {})
                if not isinstance(msg, dict):
                    continue
                for block in msg.get("content", []):
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") != "tool_use" or block.get("name") != "Bash":
                        continue
                    cmd = (block.get("input") or {}).get("command", "")
                    if (
                        isinstance(cmd, str)
                        and "--scan-subagents" in cmd
                        and "session_metrics" in cmd
                    ):
                        return True
    except OSError:
        return False
    return False


def _workflow_agent_ticket_map(parent_session: str) -> dict[str, str]:
    """Map ``agentId -> TICKET`` for Workflow-spawned worker transcripts, read
    from the run journal(s) at
    ``<project>/<parent_session>/subagents/workflows/*/journal.jsonl``.

    Why this exists (GGC-86): every ``Workflow``-spawned agent records
    ``cwd=<main repo>`` and ``gitBranch=<default branch>`` on *every* record —
    the per-ticket ``/add-worktree`` ``cd`` moves only the shell, not the agent
    process the harness records. So first-record cwd/gitBranch cannot tell which
    ticket a transcript belongs to. The journal, however, persists each work
    agent's structured result, whose ``ticketId`` is the authoritative signal.
    This builds the ``agentId -> ticket`` lookup the scan prefers.

    Returns an empty dict for a non-workflow session (no journal) — callers then
    fall back to the legacy cwd/gitBranch match. Fully fail-soft.
    """
    out: dict[str, str] = {}
    if not PROJECTS_DIR.exists() or not parent_session:
        return out
    for journal in PROJECTS_DIR.glob(
        f"*/{parent_session}/subagents/workflows/*/journal.jsonl"
    ):
        try:
            with journal.open() as fh:
                for line in fh:
                    if '"ticketId"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    agent_id = rec.get("agentId")
                    result = rec.get("result")
                    if not agent_id or not isinstance(result, dict):
                        continue
                    tid = result.get("ticketId")
                    if isinstance(tid, str) and tid.strip():
                        out[agent_id] = tid.strip().upper()
        except OSError:
            continue
    return out


def scan_subagents_aggregate(
    parent_session: str,
    ticket_id: str,
    run_ts: str,
    exclude_agents: set[str] | None = None,
) -> dict | None:
    """Scan ``PROJECTS_DIR/*/<parent_session>/subagents/agent-*.jsonl`` for the
    given ticket and aggregate (SUM) cost/tokens/durations/model_usage across
    every matching sibling subagent into a single synthetic metrics object.

    Match = (anchored ticket regex hits the transcript's first-record
    ``gitBranch`` OR ``cwd``) AND (first-record ``timestamp`` >= ``run_ts``
    cutoff, both normalized to epoch seconds). Unparseable timestamps are
    EXCLUDED (safer than over-counting).

    Two transcripts are always excluded (GGC-78): any stem in ``exclude_agents``
    (explicit caller lever) and the scanner's OWN transcript — detected by it
    having invoked ``--scan-subagents`` (``_invoked_scan_subagents``) — so the
    batch metric agent never self-counts regardless of its process cwd.

    Returns None when ZERO transcripts match (the detectable-failure contract);
    otherwise a dict with the combined ``metrics`` (parse_session shape),
    ``durations``, ``total_cost``, ``total_tokens``, ``git_branch``, ``cwd``,
    and ``provenance`` (list of {stem, cost, tokens}).
    """
    if not PROJECTS_DIR.exists() or not parent_session or ticket_id == "UNKNOWN":
        return None

    exclude_agents = exclude_agents or set()
    cutoff = _run_ts_to_epoch(run_ts)
    tre = anchored_ticket_re(ticket_id)

    # Glob across all project dirs — the parent session UUID is unique, so this
    # finds the right subagents/ dir regardless of which project launched it.
    # Recursive (**): Workflow-spawned workers nest under
    # subagents/workflows/<runId>/agent-*.jsonl; pathlib ** also matches the
    # zero-intermediate-dir top-level layout, so plain (non-workflow) subagents
    # still match. Without the recursion, --scan-subagents found ZERO transcripts
    # for every Workflow-fan-out ticket (the only dispatcher fan-out path since
    # GGC-55), making --metric a silent no-op on Linear.
    sub_files = sorted(
        PROJECTS_DIR.glob(f"*/{parent_session}/subagents/**/agent-*.jsonl")
    )

    # GGC-86: authoritative agentId -> ticket map from the Workflow journal.
    # Empty for non-workflow sessions, in which case the per-file loop falls
    # back to the legacy first-record cwd/gitBranch match below.
    wf_ticket_map = _workflow_agent_ticket_map(parent_session)

    # Combined (synthetic) metrics object in parse_session() shape.
    combined: dict = {
        "timestamps": [],
        "model_usage": defaultdict(
            lambda: {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_write_tokens": 0,
                "cache_read_tokens": 0,
            }
        ),
        "user_msgs": 0,
        "assistant_msgs": 0,
        "tool_calls": 0,
        "total_turns": 0,
        "turn_durations_ms": [],
        "agents": [],
        "in_progress_agents": [],
        "git_branch": None,
        "claude_code_version": None,
        "cwd": None,
    }

    provenance: list[dict] = []
    total_cost = 0.0
    total_tokens = 0
    # Per-subagent active-time proxy, accumulated across matching transcripts.
    # Summed per-transcript (NOT on the merged combined["timestamps"], which
    # interleaves parallel agents and distorts the gaps).
    active_proxy_total = 0.0
    matched = 0

    for sub in sub_files:
        # GGC-78: explicit exclusion lever — the caller (e.g. the Workflow
        # finalize stage) can name transcript stems to skip.
        if sub.stem in exclude_agents:
            print(
                f"Warning: excluding {sub.name} — named in --exclude-agent",
                file=sys.stderr,
            )
            continue
        head = _first_record(sub)
        if not head:
            continue
        # Ticket attribution (GGC-86). Prefer the Workflow journal's authoritative
        # agentId -> ticket mapping: Workflow workers all record cwd=<main repo> /
        # gitBranch=<default>, so first-record cwd/gitBranch cannot distinguish
        # them. Only when the agent has no journal entry (a non-workflow, top-level
        # subagent) do we fall back to the legacy cwd/gitBranch match.
        agent_id = sub.stem[len("agent-"):] if sub.stem.startswith("agent-") else sub.stem
        mapped_ticket = wf_ticket_map.get(agent_id)
        if mapped_ticket is not None:
            if mapped_ticket != ticket_id:
                continue
        elif wf_ticket_map:
            # Workflow run WITH a journal: attribution is journal-authoritative.
            # An agent absent from the journal is a non-work dispatcher STAGE agent
            # whose result carries no ticketId — e.g. review/verify agents AND the
            # GGC-21 evidence cross-check agent ("Cross-check terminal evidence for
            # ticket …"). These are DELIBERATELY EXCLUDED as dispatcher verification
            # overhead, exactly like the session_metrics finalize agent (which is
            # excluded by _invoked_scan_subagents below) — they are not the ticket's
            # product work, so their cost is not attributed to the ticket's summed
            # total (GGC-125 Residual 2: this exclusion is intentional, not a bug).
            # The old cwd/gitBranch fallback below matched NOTHING on this path
            # anyway (every Workflow agent records cwd=main repo / gitBranch=trunk),
            # so skipping it here only strips latent risk of a false match.
            continue
        else:
            # Legacy (non-Workflow) path: no journal, so fall back to the
            # first-record cwd/gitBranch ticket match.
            branch = head.get("gitBranch") or ""
            cwd_field = head.get("cwd") or ""
            if not (tre.search(branch) or tre.search(cwd_field)):
                continue
        # Time cutoff (epoch-normalized). Unparseable transcript ts → exclude.
        if cutoff is not None:
            tdt = parse_ts(head.get("timestamp"))
            if tdt is None:
                print(
                    f"Warning: excluding {sub.name} — unparseable first-record timestamp",
                    file=sys.stderr,
                )
                continue
            if tdt.timestamp() < cutoff:
                continue

        # GGC-78: never sum the batch scanner's OWN transcript — it is a sibling
        # subagent that would otherwise self-count. Content-based, so it holds
        # regardless of the scanner's process cwd (the fragile incidental
        # exclusion this fix replaces). Checked only for transcripts that already
        # matched the ticket + time cutoff above, so the extra read is rare.
        if _invoked_scan_subagents(sub):
            print(
                f"Warning: excluding {sub.name} — it invoked --scan-subagents "
                f"(the metric scanner's own transcript; GGC-78)",
                file=sys.stderr,
            )
            continue

        try:
            sub_metrics = parse_session(sub)
        except Exception:
            continue

        # Merge into the combined object.
        combined["timestamps"].extend(sub_metrics["timestamps"])
        combined["turn_durations_ms"].extend(sub_metrics["turn_durations_ms"])
        combined["user_msgs"] += sub_metrics["user_msgs"]
        combined["assistant_msgs"] += sub_metrics["assistant_msgs"]
        combined["tool_calls"] += sub_metrics["tool_calls"]
        combined["total_turns"] += sub_metrics["total_turns"]
        combined["agents"].extend(sub_metrics["agents"])
        combined["in_progress_agents"].extend(sub_metrics["in_progress_agents"])
        if sub_metrics["git_branch"] and not combined["git_branch"]:
            combined["git_branch"] = sub_metrics["git_branch"]
        if sub_metrics["claude_code_version"] and not combined["claude_code_version"]:
            combined["claude_code_version"] = sub_metrics["claude_code_version"]
        if sub_metrics["cwd"] and not combined["cwd"]:
            combined["cwd"] = sub_metrics["cwd"]

        sub_cost = 0.0
        sub_tokens = 0
        for model_name, u in sub_metrics["model_usage"].items():
            mu = combined["model_usage"][model_name]
            mu["input_tokens"] += u["input_tokens"]
            mu["output_tokens"] += u["output_tokens"]
            mu["cache_write_tokens"] += u["cache_write_tokens"]
            mu["cache_read_tokens"] += u["cache_read_tokens"]
            family = get_model_family(model_name)
            sub_tokens += sum(u.values())
            sub_cost += calculate_cost(
                {
                    "input_tokens": u["input_tokens"],
                    "output_tokens": u["output_tokens"],
                    "cache_creation_input_tokens": u["cache_write_tokens"],
                    "cache_read_input_tokens": u["cache_read_tokens"],
                },
                family,
                model_name,
            )

        sub_active_proxy = compute_active_proxy(sub_metrics["timestamps"])
        active_proxy_total += sub_active_proxy

        provenance.append(
            {
                "stem": sub.stem,
                "cost": round(sub_cost, 4),
                "tokens": sub_tokens,
                "active_proxy_sec": round(sub_active_proxy),
            }
        )
        total_cost += sub_cost
        total_tokens += sub_tokens
        matched += 1

    if matched == 0:
        return None

    durations = compute_durations(combined)
    # Override the envelope-based proxy that compute_durations() computed on the
    # merged timestamps with the per-subagent accumulation (envelope-safe).
    durations["active_proxy_sec"] = active_proxy_total
    return {
        "metrics": combined,
        "durations": durations,
        "total_cost": total_cost,
        "total_tokens": total_tokens,
        "git_branch": combined["git_branch"],
        "cwd": combined["cwd"],
        "provenance": provenance,
    }


# ===================================================================
# Parse session JSONL
# ===================================================================


def parse_session(jsonl_path: Path) -> dict:
    metrics = {
        "timestamps": [],
        "model_usage": defaultdict(
            lambda: {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_write_tokens": 0,
                "cache_read_tokens": 0,
            }
        ),
        "user_msgs": 0,
        "assistant_msgs": 0,
        "tool_calls": 0,
        "total_turns": 0,
        "turn_durations_ms": [],
        "agents": [],
        "in_progress_agents": [],
        "git_branch": None,
        "claude_code_version": None,
        "cwd": None,
    }

    # GGC-125 Residual 4: accumulate usage per requestId rather than reading the
    # FIRST record per request. `input`/`cache_*` are set once (first == last), but
    # streaming emits the cumulative `output_tokens` only in the FINAL record — so
    # a first-record read undercounted output by ~8%. We keep input/cache from the
    # first record and take the running MAX of output across all records for the
    # request, then fold into `model_usage` after the file loop.
    request_usage: dict[str, dict] = {}  # requestId -> per-request usage
    agent_tool_uses: dict[str, dict] = {}  # tool_use_id -> info

    session_id = jsonl_path.stem
    session_dir = jsonl_path.parent / session_id

    with open(jsonl_path) as f:
        for line in f:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type")
            timestamp = data.get("timestamp")

            if timestamp:
                metrics["timestamps"].append(timestamp)
            if data.get("gitBranch"):
                metrics["git_branch"] = data["gitBranch"]
            if data.get("version"):
                metrics["claude_code_version"] = data["version"]
            if data.get("cwd"):
                metrics["cwd"] = data["cwd"]

            # ----------------------------------------------------------
            # ASSISTANT messages
            # ----------------------------------------------------------
            if msg_type == "assistant":
                metrics["assistant_msgs"] += 1
                msg = data.get("message", {})
                if not isinstance(msg, dict):
                    continue

                request_id = data.get("requestId")
                model = msg.get("model", "unknown")
                usage = msg.get("usage", {})

                # Accumulate per requestId (GGC-125 Residual 4). First record wins
                # for input/cache (constant across the request's records); output
                # takes the running max (cumulative total lands in the LAST record).
                if request_id:
                    ru = request_usage.get(request_id)
                    if ru is None:
                        request_usage[request_id] = {
                            "model": model,
                            "input_tokens": usage.get("input_tokens", 0),
                            "output_tokens": usage.get("output_tokens", 0),
                            "cache_write_tokens": usage.get(
                                "cache_creation_input_tokens", 0
                            ),
                            "cache_read_tokens": usage.get(
                                "cache_read_input_tokens", 0
                            ),
                        }
                    else:
                        ru["output_tokens"] = max(
                            ru["output_tokens"], usage.get("output_tokens", 0)
                        )

                # Scan content blocks for tool_use
                for block in msg.get("content", []):
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        metrics["tool_calls"] += 1
                        if block.get("name") == "Agent":
                            inp = block.get("input", {})
                            agent_tool_uses[block.get("id", "")] = {
                                "start_timestamp": timestamp,
                                "description": inp.get("description", ""),
                                "subagent_type": inp.get("subagent_type", ""),
                            }

            # ----------------------------------------------------------
            # USER messages
            # ----------------------------------------------------------
            elif msg_type == "user":
                metrics["user_msgs"] += 1

                # Count real user prompts (not tool results)
                if data.get("promptId"):
                    inner = data.get("message", {})
                    content = inner.get("content", []) if isinstance(inner, dict) else []
                    is_tool_result_only = False
                    if isinstance(content, list) and content:
                        is_tool_result_only = all(
                            isinstance(b, dict) and b.get("type") == "tool_result"
                            for b in content
                        )
                    if not is_tool_result_only:
                        metrics["total_turns"] += 1

                # Agent tool results
                tur = data.get("toolUseResult")
                if tur and isinstance(tur, dict) and "totalDurationMs" in tur:
                    agent_id = tur.get("agentId", "")
                    agent_info = {
                        "agent_type": tur.get("agentType", "unknown"),
                        "agent_id": agent_id,
                        "description": "",
                        "total_duration_ms": tur.get("totalDurationMs", 0),
                        "total_tokens": tur.get("totalTokens", 0),
                        "total_tool_use_count": tur.get("totalToolUseCount", 0),
                        "usage": tur.get("usage", {}),
                        "tool_stats": tur.get("toolStats", {}),
                        "model": get_agent_model(session_dir, agent_id)
                        if agent_id
                        else "unknown",
                    }

                    # Match Agent tool_use for description
                    inner = data.get("message", {})
                    if isinstance(inner, dict):
                        for block in inner.get("content", []):
                            if (
                                isinstance(block, dict)
                                and block.get("type") == "tool_result"
                            ):
                                tu_id = block.get("tool_use_id", "")
                                if tu_id in agent_tool_uses:
                                    agent_info["description"] = agent_tool_uses[
                                        tu_id
                                    ]["description"]
                                    agent_tool_uses.pop(tu_id)

                    metrics["agents"].append(agent_info)

            # ----------------------------------------------------------
            # SYSTEM messages (turn_duration)
            # ----------------------------------------------------------
            elif msg_type == "system" and data.get("subtype") == "turn_duration":
                metrics["turn_durations_ms"].append(data.get("durationMs", 0))

    # GGC-125 Residual 4: fold the per-request usage (output = max across the
    # request's streamed records) into model_usage now that every record is seen.
    for ru in request_usage.values():
        mu = metrics["model_usage"][ru["model"]]
        mu["input_tokens"] += ru["input_tokens"]
        mu["output_tokens"] += ru["output_tokens"]
        mu["cache_write_tokens"] += ru["cache_write_tokens"]
        mu["cache_read_tokens"] += ru["cache_read_tokens"]

    # Agents that were launched but never returned
    for tu_id, info in agent_tool_uses.items():
        metrics["in_progress_agents"].append(
            {
                "description": info["description"],
                "subagent_type": info["subagent_type"],
                "start_timestamp": info["start_timestamp"],
            }
        )

    return metrics


# ===================================================================
# Durations
# ===================================================================

# Per-gap cap for the active-time PROXY. `turn_duration` system records exist
# ONLY in the main interactive loop, never in subagent transcripts (a harness
# fact), so the summed dispatcher-scan path has no real active signal and its
# `active_sec` is always 0. The proxy fills that gap: it sums the wall-time gaps
# between consecutive transcript records, capping each gap so a long idle stretch
# (the model waiting on a human, or between-stage dead time) contributes at most
# CAP rather than inflating "busy" time. It is a DIFFERENT quantity from
# `active_sec` (turn_durations): the proxy INCLUDES tool-execution wall time (a
# 3-minute flutter build counts as "busy"), so the two must never share a "Speed"
# cell. CAP is a lopsided knob — too low truncates legitimate long tool runs
# (undercount), too high leaks idle. 120s is chosen as a balance: longer than
# almost any single model turn, shorter than typical between-stage idle.
ACTIVE_PROXY_GAP_CAP_SEC = 120


def compute_active_proxy(timestamps: list, cap_sec: float = ACTIVE_PROXY_GAP_CAP_SEC) -> float:
    """Sum of capped gaps between consecutive record timestamps — a proxy for
    model+tool busy time when real `turn_duration` records are absent (every
    subagent transcript). MUST be computed per-transcript and accumulated by the
    caller; computing it on a merged multi-agent timestamp pool interleaves
    parallel agents and distorts the gaps."""
    parsed = sorted(dt for ts in timestamps if (dt := parse_ts(ts)) is not None)
    if len(parsed) < 2:
        return 0.0
    total = 0.0
    for a, b in zip(parsed, parsed[1:]):
        gap = (b - a).total_seconds()
        if gap > 0:
            total += min(gap, cap_sec)
    return total


def compute_durations(metrics: dict) -> dict:
    result = {
        "wall_clock_sec": 0,
        "active_sec": 0,
        "active_proxy_sec": 0,
        "idle_sec": 0,
        "start_time": None,
        "end_time": None,
    }
    parsed = []
    for ts in metrics["timestamps"]:
        dt = parse_ts(ts)
        if dt:
            parsed.append(dt)
    if not parsed:
        return result

    parsed.sort()
    result["start_time"] = parsed[0]
    result["end_time"] = parsed[-1]
    result["wall_clock_sec"] = (parsed[-1] - parsed[0]).total_seconds()

    active_ms = sum(metrics["turn_durations_ms"])
    result["active_sec"] = active_ms / 1000
    # Single-transcript proxy (envelope-safe here: one transcript, no parallel
    # interleave). On the batch scan path the caller OVERRIDES this with a
    # per-subagent accumulation (see scan_subagents_aggregate).
    result["active_proxy_sec"] = compute_active_proxy(metrics["timestamps"])
    result["idle_sec"] = max(0, result["wall_clock_sec"] - result["active_sec"])
    return result


# ===================================================================
# Session number (from CSV history)
# ===================================================================


def get_session_number(ticket_id: str, session_id: str) -> int:
    if not CSV_PATH.exists():
        return 1
    seen: set[str] = set()
    try:
        with open(CSV_PATH, newline="") as f:
            for row in csv.DictReader(f):
                if row.get("ticket_id") == ticket_id:
                    seen.add(row.get("session_id", ""))
    except Exception:
        pass
    if session_id not in seen:
        return len(seen) + 1
    return len(seen)


# ===================================================================
# Cumulative ticket stats (from CSV history)
# ===================================================================


def get_ticket_cumulative(
    ticket_id: str,
    current_session_id: str,
    cwd: str,
    run_stem: str | None = None,
) -> dict | None:
    """Aggregate stats across all sessions for a ticket from CSV.

    Returns None if no CSV or no prior sessions exist.
    The current session is excluded — the caller merges it in.
    Also loads agents from prior session JSONLs.

    GGC-125 Residual 3: when ``run_stem`` is given (the batch --scan-subagents
    path), aggregate ONLY prior DISPATCH rows — rows with a non-empty ``run_stem``
    other than the current one — and group "sessions" by ``run_stem``. This keeps
    the report header derived from the SAME transcript set as its provenance:
    stray per-session hook rows (no run_stem — e.g. a cross-check agent's own
    session that ended in the worktree) no longer leak into the cumulative total
    and make the header disagree with provenance. When ``run_stem`` is None (the
    standalone path) the legacy session_id grouping is used, unchanged.
    """
    if not CSV_PATH.exists():
        return None

    total_active = 0.0
    total_active_proxy = 0.0
    total_wall = 0.0
    total_cost = 0.0
    session_ids: set[str] = set()
    stored_sp: int | None = None

    try:
        with open(CSV_PATH, newline="") as f:
            for row in csv.DictReader(f):
                if row.get("ticket_id") != ticket_id:
                    continue
                # Story points: scan ALL rows (including current session)
                sp_val = row.get("story_points", "")
                if sp_val and stored_sp is None:
                    try:
                        stored_sp = int(sp_val)
                    except ValueError:
                        pass
                if run_stem is not None:
                    # Batch path: only prior DISPATCH rows, keyed by run_stem.
                    rs = row.get("run_stem", "")
                    if not rs or rs == run_stem:
                        continue  # stray non-dispatch row, or the current run
                    sid = rs
                else:
                    sid = row.get("session_id", "")
                    if sid == current_session_id:
                        continue  # exclude current for aggregation — caller merges it
                if sid not in session_ids:
                    # Per-session fields (same across model rows): take first.
                    # `or 0` guards empty cells (old rows lack active_proxy_sec).
                    total_active += float(row.get("active_sec") or 0)
                    total_active_proxy += float(row.get("active_proxy_sec") or 0)
                    total_wall += float(row.get("wall_clock_sec") or 0)
                    session_ids.add(sid)
                # Cost is per-model row, always sum
                total_cost += float(row.get("estimated_cost", 0))
    except Exception:
        return None

    # Return even if no prior sessions — stored_sp may still be useful
    if not session_ids and stored_sp is None:
        return None

    # Load agents from prior session JSONLs
    prior_agents: list[dict] = []
    project_name = cwd_to_project_dir_name(cwd)
    for sid in sorted(session_ids):
        jsonl_path = PROJECTS_DIR / project_name / f"{sid}.jsonl"
        if not jsonl_path.exists():
            continue
        try:
            prior_metrics = parse_session(jsonl_path)
            prior_agents.extend(prior_metrics["agents"])
        except Exception:
            pass

    return {
        "prior_session_count": len(session_ids),
        "prior_active_sec": total_active,
        "prior_active_proxy_sec": total_active_proxy,
        "prior_wall_sec": total_wall,
        "prior_cost": total_cost,
        "stored_story_points": stored_sp,
        "prior_agents": prior_agents,
    }


# ===================================================================
# Outcome signals (PR2)
# ===================================================================


def load_outcome_json(path: str | None) -> dict | None:
    """Read the outcome bundle the finalize stage gathered from the ticket's PR.
    Fail-soft: any read/parse error → None (metrics still post without outcomes)."""
    if not path:
        return None
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception as e:
        print(f"Warning: --outcome-json unreadable ({e}); continuing without outcomes", file=sys.stderr)
        return None


def _bool01(v) -> str | int:
    """Normalize a truthy/None into 1 / 0 / "" for a CSV cell."""
    if v is None or v == "":
        return ""
    return 1 if v in (True, 1, "1", "true", "True", "yes", "YES") else 0


def _num_or_blank(v) -> str | int | float:
    """Coerce to a number for a CSV cell, else "". Accepts numeric STRINGS —
    the finalize stage builds the outcome bundle via gh + shell, so numbers
    routinely arrive as strings ("3600"); without this they'd be silently
    dropped from the CSV (the SSOT) while still rendering in the report. bool
    is excluded (True/False are not counts)."""
    if isinstance(v, bool):
        return ""
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        s = v.strip()
        for cast in (int, float):
            try:
                return cast(s)
            except ValueError:
                pass
    return ""


def outcome_to_csv(outcome: dict | None) -> dict:
    """Map an outcome dict (finalize-gathered) to the CSV outcome columns —
    every field "" when absent, never fabricated."""
    o = outcome or {}
    # GGC-125 Residual 5: cycle time is meaningful ONLY for a merged PR
    # (open→merge). For an unmerged PR the upstream gather used to compute
    # now−createdAt (a naive local-vs-UTC subtraction that produced a bogus ~8h
    # TZ-offset value), so blank it unless the PR actually merged — regardless of
    # what the gather emitted.
    merged = _bool01(o.get("merged")) == 1
    return {
        "pr_url": o.get("pr_url", "") or "",
        "pr_merged": _bool01(o.get("merged")),
        "pr_state": o.get("pr_state", "") or "",
        "cycle_time_sec": _num_or_blank(o.get("cycle_time_sec")) if merged else "",
        "review_rounds": _num_or_blank(o.get("review_rounds")),
        "first_pass_accepted": _bool01(o.get("first_pass")),
        "reviewer_comments": _num_or_blank(o.get("reviewer_comments")),
        "ci_fail_count": _num_or_blank(o.get("ci_fail_count")),
    }


def format_outcome_section(outcome: dict | None, current_cost: float) -> list[str]:
    """Render the '### Outcome' report block. Returns [] when no outcome data —
    the section is simply omitted (no misleading zeros)."""
    o = outcome or {}
    if not o or not (o.get("pr_url") or o.get("pr_state") or o.get("merged") is not None):
        return []
    lines = ["", "### Outcome"]
    lines.append("| Signal | Value |")
    lines.append("|--------|-------|")
    state = o.get("pr_state") or ("MERGED" if o.get("merged") else "?")
    merged = bool(o.get("merged"))
    lines.append(f"| PR | {state}{' ✅' if merged else ''} |")
    # Coerce numerics through the same hardened path as the CSV (strings from
    # gh are common); "" means absent/unparseable → omit the row (never float()
    # a raw string, which would crash the whole metrics run mid-report).
    # GGC-125 Residual 5: only an open→merge cycle time is real; skip it for an
    # unmerged PR (the upstream now−createdAt value was a bogus TZ-offset artifact).
    cyc = _num_or_blank(o.get("cycle_time_sec")) if merged else ""
    if cyc != "":
        lines.append(f"| Cycle time (open→merge) | {format_duration(float(cyc))} |")
    rr = _num_or_blank(o.get("review_rounds"))
    if rr != "":
        fp = o.get("first_pass")
        note = " (first-pass ✅)" if (merged and (fp or rr == 0)) else ""
        lines.append(f"| Review rounds (changes-requested) | {rr}{note} |")
    rc = _num_or_blank(o.get("reviewer_comments"))
    if rc != "":
        lines.append(f"| Reviewer comments | {rc} |")
    cf = _num_or_blank(o.get("ci_fail_count"))
    if cf != "":
        lines.append(f"| CI failures (head) | {cf} |")
    # NOTE: the cost→value ROI ratio (cost per merged PR) is deliberately NOT
    # computed here — it is a FLEET aggregate (a ratio over many runs) that
    # belongs in the GGC-54 roll-up, not in a single per-run report where n=1
    # would masquerade as a rate. The raw inputs it needs — this run's cost (the
    # report header) and pr_merged (the CSV column) — are both emitted; the
    # aggregator divides.
    return lines


# ===================================================================
# Report formatter
# ===================================================================


def format_report(
    metrics: dict,
    durations: dict,
    ticket_id: str,
    session_number: int,
    git_branch: str | None,
    session_id: str = "",
    summary_file: str | None = None,
    story_points: int | None = None,
    cumulative: dict | None = None,
    current_cost: float = 0.0,
    run_stem: str | None = None,
    provenance: list[dict] | None = None,
    outcome: dict | None = None,
) -> str:
    lines: list[str] = []
    prior = cumulative if cumulative and cumulative.get("prior_session_count", 0) > 0 else None
    total_sessions = (prior["prior_session_count"] + 1) if prior else 1

    # ---- Header (cumulative) ----
    lines.append(f"## 🤖 AI Session Report (#{session_number})")
    lines.append("")
    lines.append(
        f"**Ticket:** {ticket_id} | **Branch:** {git_branch or 'N/A'}"
    )
    # GGC-89: the "(AI active: …)" parenthetical is only honest when active
    # time was actually measured. Summed subagent transcripts (the batch scan
    # path) carry no `turn_duration` system records — those come only from the
    # main interactive loop — so active is always 0 there; printing
    # "AI active: 0s" reads as "instant". Suppress the parenthetical when there
    # is no active signal rather than render a misleading zero.
    if prior:
        total_wall = prior["prior_wall_sec"] + durations["wall_clock_sec"]
        total_active = prior["prior_active_sec"] + durations["active_sec"]
        total_cost = prior["prior_cost"] + current_cost
        active_note = f" (AI active: {format_duration(total_active)})" if total_active > 0 else ""
        lines.append(
            f"**Duration:** {format_duration(total_wall)}{active_note} "
            f"across {total_sessions} sessions"
        )
        lines.append(f"**Cost:** {format_cost(total_cost)}")
    else:
        active_note = f" (AI active: {format_duration(durations['active_sec'])})" if durations["active_sec"] > 0 else ""
        lines.append(
            f"**Duration:** {format_duration(durations['wall_clock_sec'])}{active_note}"
        )
        lines.append(f"**Cost:** {format_cost(current_cost)}")

    # ---- Token + cache breakdown (GGC-89) ----
    # Surfaced on BOTH the standalone and batch paths so the data survives even
    # when no LLM Summary is attached (the batch finalize path posts none). The
    # report skeleton never carried tokens before — the numbers reached the
    # reader only via the Summary, which the batch path drops. Reflects THIS
    # run's metrics (current session, or the summed subagents on the scan
    # path), not the cumulative cost above.
    tok_in = tok_out = tok_cw = tok_cr = 0
    for u in metrics["model_usage"].values():
        tok_in += u["input_tokens"]
        tok_out += u["output_tokens"]
        tok_cw += u["cache_write_tokens"]
        tok_cr += u["cache_read_tokens"]
    tok_total = tok_in + tok_out + tok_cw + tok_cr
    if tok_total > 0:
        cache_read_pct = round(100 * tok_cr / tok_total)
        lines.append(
            f"**Tokens:** {format_tokens(tok_total)} "
            f"({cache_read_pct}% cache-read · in {format_tokens(tok_in)} · "
            f"out {format_tokens(tok_out)} · cache-write {format_tokens(tok_cw)})"
        )
    lines.append("")

    # ---- Agent summary (all sessions) ----
    all_agents: list[dict] = []
    if prior and prior.get("prior_agents"):
        all_agents.extend(prior["prior_agents"])
    all_agents.extend(metrics["agents"])
    in_progress = metrics["in_progress_agents"]

    if all_agents or in_progress:
        agent_wall = sum(a["total_duration_ms"] for a in all_agents) / 1000
        lines.append(
            f"### Agents ({len(all_agents)} completed, "
            f"{format_duration(agent_wall)})"
        )
        for a in all_agents:
            dur = format_duration(a["total_duration_ms"] / 1000)
            family = get_model_family(a["model"])
            cost = (
                a["precomputed_cost"]
                if a.get("precomputed_cost") is not None
                else calculate_cost(a["usage"], family, a["model"])
            )
            desc = a["description"] or a["agent_type"]
            model_short = family if family not in ("unknown", "synthetic") else a["model"]
            tag = " [dispatcher]" if a.get("agent_type") == "dispatcher-spawn" else ""
            lines.append(f"- {desc}{tag} ({model_short}) — {dur}, {format_cost(cost)}")
        for a in in_progress:
            desc = a["description"] or a["subagent_type"]
            lines.append(f"- {desc} — ⏳ in progress")
    else:
        lines.append("### Agents")
        lines.append("No agents used.")

    # ---- Time Analysis (story points vs AI time) ----
    manual_sec = story_point_seconds(story_points)
    if manual_sec is not None:
        ai_sec = durations["active_sec"]
        wall_sec = durations["wall_clock_sec"]
        proxy_sec = durations.get("active_proxy_sec", 0)
        cum_ai = (prior["prior_active_sec"] + ai_sec) if prior else ai_sec
        cum_wall = (prior["prior_wall_sec"] + wall_sec) if prior else wall_sec
        cum_proxy = (prior.get("prior_active_proxy_sec", 0) + proxy_sec) if prior else proxy_sec
        # Basis for the Speed multiplier, in order of fidelity:
        #   1. real active_sec (turn_durations) — main interactive loop only;
        #   2. active_proxy_sec (summed capped inter-record gaps) — the batch
        #      scan path, where turn_duration is structurally absent. Includes
        #      tool wall time, so it is labeled "approx"; still a real measurement
        #      of elapsed busy time, unlike the 0 it replaces;
        #   3. wall clock — last-resort basis when neither is available.
        active_available = cum_ai > 0
        proxy_available = (not active_available) and cum_proxy > 0

        lines.append("")
        lines.append("### ⏱ Time Analysis")
        sess_label = f" ({total_sessions} sessions)" if total_sessions > 1 else ""
        lines.append("| Metric | Duration |")
        lines.append("|--------|---------|")
        lines.append(f"| Story Points (AI-estimated) | **{story_points}** (≈ {format_duration(manual_sec)} manual) |")
        if active_available:
            lines.append(f"| AI Active Time{sess_label} | {format_duration(cum_ai)} |")
        elif proxy_available:
            lines.append(f"| AI Active Time{sess_label} | ≈ {format_duration(cum_proxy)} (approx, incl. tool time) |")
        else:
            lines.append(f"| AI Active Time{sess_label} | n/a (no turn-duration records; proxy unavailable) |")
        lines.append(f"| Wall Clock{sess_label} | {format_duration(cum_wall)} |")
        if active_available:
            multiplier = manual_sec / cum_ai
            lines.append(f"| **Speed** | **~{multiplier:.1f}x** vs AI-estimated manual |")
        elif proxy_available:
            multiplier = manual_sec / cum_proxy
            lines.append(f"| **Speed** | **~{multiplier:.1f}x** vs AI-estimated manual (approx active basis) |")
        elif cum_wall > 0:
            multiplier = manual_sec / cum_wall
            lines.append(f"| **Speed** | **~{multiplier:.1f}x** vs AI-estimated manual (wall-clock basis) |")

    # ---- Outcome signals (PR2): value delivered, next to the Speed above ----
    lines.extend(format_outcome_section(outcome, current_cost))

    # ---- Provenance (GGC-74 batch scan) — list each summed subagent so an
    # accidental double-count is visible, not hidden in a summed scalar. ----
    if provenance:
        lines.append("")
        lines.append(f"### Provenance ({len(provenance)} subagent transcript(s) summed)")
        for p in provenance:
            lines.append(f"- {p['stem']} — {format_cost(p['cost'])}")

    # ---- AI Summary (from external file) ----
    if summary_file:
        try:
            with open(summary_file) as f:
                summary_text = f.read().strip()
            if summary_text:
                lines.append("")
                lines.append("### Summary")
                lines.append(summary_text)
        except Exception:
            pass

    # Hidden marker for upsert. GGC-74: batch finalize keys on (run_stem,ticket);
    # the legacy per-session path keys on session_id.
    if run_stem:
        lines.append("")
        lines.append(f"<!-- dispatch-run:{run_stem}/{ticket_id} -->")
    elif session_id:
        lines.append("")
        lines.append(f"<!-- session:{session_id} -->")

    return "\n".join(lines)


# ===================================================================
# CSV output
# ===================================================================


def write_csv(
    metrics: dict,
    durations: dict,
    ticket_id: str,
    session_id: str,
    git_branch: str | None,
    story_points: int | None = None,
    manual_hours: float | None = None,
    run_stem: str | None = None,
    metrics_provenance: str = "",
    outcome: dict | None = None,
):
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = CSV_PATH.with_suffix(".lock")

    with open(lock_path, "w") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            # Read existing rows, filtering out the current row's key (upsert).
            # GGC-74: batch finalize keys on (ticket_id, run_stem); the legacy
            # per-session path keys on session_id.
            existing_rows: list[dict] = []
            if CSV_PATH.exists():
                try:
                    with open(CSV_PATH, newline="") as f:
                        if run_stem:
                            existing_rows = [
                                row
                                for row in csv.DictReader(f)
                                if not (
                                    row.get("ticket_id") == ticket_id
                                    and row.get("run_stem") == run_stem
                                )
                            ]
                        else:
                            existing_rows = [
                                row
                                for row in csv.DictReader(f)
                                if row.get("session_id") != session_id
                            ]
                except Exception:
                    pass

            now = datetime.now(timezone.utc).isoformat()
            agents = metrics["agents"]
            agent_count = len(agents)
            agent_wall = sum(a["total_duration_ms"] for a in agents) / 1000
            agent_tokens = sum(a["total_tokens"] for a in agents)
            agent_cost = sum(
                calculate_cost(a["usage"], get_model_family(a["model"]), a["model"])
                for a in agents
            )

            # Per-(session|run) SCALARS — identical across this run's model rows.
            # active_sec: prefer real turn-duration active; fall back to the
            # busy-time proxy when active is structurally 0 (batch scan path) so
            # the multiplier is populated there too. multiplier_basis records
            # which denominator was used (active | active_proxy) so a consumer
            # never conflates the two.
            sp_sec = story_point_seconds(story_points)
            active_basis = (
                durations["active_sec"] if durations["active_sec"] > 0
                else durations.get("active_proxy_sec", 0)
            )
            multiplier = (
                round(sp_sec / active_basis, 1)
                if (sp_sec is not None and active_basis > 0) else ""
            )
            multiplier_basis = ""
            if multiplier != "":
                multiplier_basis = "active" if durations["active_sec"] > 0 else "active_proxy"
            est_manual = round(sp_sec) if sp_sec is not None else ""
            # GGC-127: reconcile manual_hours to the SNAPPED bucket. estimated_manual_sec,
            # the report "(≈ Nh manual)" line, and the speed multiplier all derive from
            # story_point_seconds (post-snap). The raw --manual-hours estimate is pre-snap,
            # so writing it verbatim disagreed with estimated_manual_sec by the snap delta
            # whenever the blind estimate did not land exactly on a bucket. Snap it here so
            # all three representations agree; keep the raw value only when there is no
            # story point to snap to (est_manual is "" in that case anyway).
            manual_hours_out = (
                STORY_POINT_HOURS[story_points]
                if (story_points is not None and story_points in STORY_POINT_HOURS)
                else (manual_hours if manual_hours is not None else "")
            )
            outcome_cols = outcome_to_csv(outcome)

            # These SESSION-SCALAR columns are written ONLY on the first model
            # row of the run; subsequent model rows carry "". This stops a naive
            # aggregator (the GGC-54 grep/awk fleet roll-up) from N×-counting a
            # per-run fact across a run's sonnet+haiku+opus rows (e.g. summing
            # pr_merged to 3). Read-first / dedup-by-(session_id|run_stem) before
            # aggregating; the pre-existing per-session columns (wall/active/
            # tokens/…) remain duplicated per row for backward-compat, so those
            # too must be dedup'd, not summed.
            session_scalars = {
                "time_saved_multiplier": multiplier,
                "multiplier_basis": multiplier_basis,
                "active_proxy_sec": round(durations.get("active_proxy_sec", 0)),
                **outcome_cols,
            }

            # Build new rows for this session
            new_rows: list[dict] = []
            for model in sorted(metrics["model_usage"]):
                u = metrics["model_usage"][model]
                family = get_model_family(model)
                tokens = sum(u.values())
                cost = calculate_cost(
                    {
                        "input_tokens": u["input_tokens"],
                        "output_tokens": u["output_tokens"],
                        "cache_creation_input_tokens": u["cache_write_tokens"],
                        "cache_read_input_tokens": u["cache_read_tokens"],
                    },
                    family,
                    model,
                )
                # Session-scalar columns only on the first model row (see above).
                scalars = session_scalars if not new_rows else {
                    k: "" for k in session_scalars
                }
                new_rows.append(
                    {
                        "timestamp": now,
                        "ticket_id": ticket_id,
                        "session_id": session_id,
                        "git_branch": git_branch or "",
                        "wall_clock_sec": round(durations["wall_clock_sec"]),
                        "active_sec": round(durations["active_sec"]),
                        "idle_sec": round(durations["idle_sec"]),
                        "user_msgs": metrics["user_msgs"],
                        "assistant_msgs": metrics["assistant_msgs"],
                        "tool_calls": metrics["tool_calls"],
                        "total_turns": metrics["total_turns"],
                        "model": model,
                        "input_tokens": u["input_tokens"],
                        "output_tokens": u["output_tokens"],
                        "cache_write_tokens": u["cache_write_tokens"],
                        "cache_read_tokens": u["cache_read_tokens"],
                        "total_tokens": tokens,
                        "estimated_cost": round(cost, 4),
                        "agent_count": agent_count,
                        "agent_total_wallclock_sec": round(agent_wall),
                        "agent_total_tokens": agent_tokens,
                        "agent_total_cost": round(agent_cost, 4),
                        "story_points": story_points if story_points is not None else "",
                        "manual_hours": manual_hours_out,
                        "estimated_manual_sec": est_manual,
                        "claude_code_version": metrics.get(
                            "claude_code_version", ""
                        ),
                        "run_stem": run_stem or "",
                        "metrics_provenance": metrics_provenance,
                        **scalars,
                    }
                )

            # Atomic write: temp file then rename
            fd, tmp_path = tempfile.mkstemp(
                dir=str(METRICS_DIR), suffix=".csv"
            )
            try:
                with os.fdopen(fd, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                    writer.writeheader()
                    for row in existing_rows:
                        writer.writerow(row)
                    for row in new_rows:
                        writer.writerow(row)
                os.replace(tmp_path, str(CSV_PATH))
            except Exception:
                os.unlink(tmp_path)
                raise
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)


# ===================================================================
# Linear API
# ===================================================================


def get_linear_api_key() -> str | None:
    try:
        with open(SETTINGS_PATH) as f:
            settings = json.load(f)
        return (
            settings.get("mcpServers", {})
            .get("linear-server", {})
            .get("env", {})
            .get("LINEAR_API_KEY")
        )
    except Exception:
        return None


_cached_ssl_ctx: ssl.SSLContext | None = None


def _ssl_context() -> ssl.SSLContext:
    """Return a cached SSL context — tries certifi, then system CA paths, then default."""
    global _cached_ssl_ctx
    if _cached_ssl_ctx is not None:
        return _cached_ssl_ctx
    try:
        import certifi

        _cached_ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        return _cached_ssl_ctx
    except ImportError:
        pass
    for ca_path in ["/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"]:
        if os.path.exists(ca_path):
            _cached_ssl_ctx = ssl.create_default_context(cafile=ca_path)
            return _cached_ssl_ctx
    _cached_ssl_ctx = ssl.create_default_context()
    return _cached_ssl_ctx


def _linear_graphql(query: str, variables: dict, api_key: str):
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": api_key,
        },
    )
    with urllib.request.urlopen(req, timeout=15, context=_ssl_context()) as resp:
        data = json.loads(resp.read())
    if data.get("errors"):
        msgs = "; ".join(e.get("message", "") for e in data["errors"])
        print(f"Warning: GraphQL errors: {msgs}", file=sys.stderr)
    return data


def find_linear_issue_id(ticket_id: str, api_key: str) -> str | None:
    query = """
    query($term: String!) {
      searchIssues(term: $term, first: 1) {
        nodes { id identifier }
      }
    }
    """
    try:
        data = _linear_graphql(query, {"term": ticket_id}, api_key)
        nodes = data.get("data", {}).get("searchIssues", {}).get("nodes", [])
        # Verify exact match
        for node in nodes:
            if node.get("identifier") == ticket_id:
                return node["id"]
        return None
    except Exception as e:
        print(f"Warning: Linear issue lookup failed: {e}", file=sys.stderr)
        return None


REPORT_MARKER = "## 🤖 AI Session Report"


def _find_existing_report_comment(
    issue_id: str, marker_tag: str, api_key: str
) -> str | None:
    """Find an existing session report comment on the issue.

    Matches by the report header marker AND the unique ``marker_tag`` embedded
    in the comment body (``<!-- session:<id> -->`` legacy, or GGC-74's
    ``<!-- dispatch-run:<run_stem>/<ticket> -->``). Returns the comment id or None.
    """
    query = """
    query($issueId: String!) {
      issue(id: $issueId) {
        comments { nodes { id body } }
      }
    }
    """
    try:
        data = _linear_graphql(query, {"issueId": issue_id}, api_key)
        comments = (
            data.get("data", {}).get("issue", {}).get("comments", {}).get("nodes", [])
        )
        for c in comments:
            body = c.get("body", "")
            if REPORT_MARKER in body and marker_tag and marker_tag in body:
                return c["id"]
    except Exception:
        pass
    return None


def post_to_linear(
    ticket_id: str,
    report: str,
    api_key: str,
    session_id: str = "",
    run_stem: str | None = None,
) -> bool:
    issue_id = find_linear_issue_id(ticket_id, api_key)
    if not issue_id:
        print(
            f"Warning: Could not find Linear issue for {ticket_id}",
            file=sys.stderr,
        )
        return False

    # Check for existing report comment to update. GGC-74: batch finalize
    # upserts by (run_stem, ticket); the legacy per-session path by session_id.
    marker_tag = (
        f"<!-- dispatch-run:{run_stem}/{ticket_id} -->"
        if run_stem
        else f"<!-- session:{session_id} -->"
    )
    existing_comment_id = _find_existing_report_comment(
        issue_id, marker_tag, api_key
    )

    if existing_comment_id:
        query = """
        mutation($id: String!, $input: CommentUpdateInput!) {
          commentUpdate(id: $id, input: $input) {
            success
            comment { id }
          }
        }
        """
        try:
            data = _linear_graphql(
                query,
                {"id": existing_comment_id, "input": {"body": report}},
                api_key,
            )
            return (
                data.get("data", {})
                .get("commentUpdate", {})
                .get("success", False)
            )
        except Exception as e:
            print(f"Warning: Linear comment update failed: {e}", file=sys.stderr)
            return False
    else:
        query = """
        mutation($input: CommentCreateInput!) {
          commentCreate(input: $input) {
            success
            comment { id }
          }
        }
        """
        try:
            data = _linear_graphql(
                query, {"input": {"issueId": issue_id, "body": report}}, api_key
            )
            return (
                data.get("data", {})
                .get("commentCreate", {})
                .get("success", False)
            )
        except Exception as e:
            print(f"Warning: Linear comment create failed: {e}", file=sys.stderr)
            return False


# ===================================================================
# Main
# ===================================================================


def read_hook_stdin() -> dict | None:
    """Read hook context from stdin (non-blocking).

    Claude Code hooks receive a JSON object on stdin with fields like
    ``session_id``, ``transcript_path``, ``cwd``, ``hook_event_name``.
    """
    if sys.stdin.isatty():
        return None
    try:
        raw = sys.stdin.read()
        if raw.strip():
            return json.loads(raw)
    except Exception:
        pass
    return None


def run_scan_subagents_mode(args) -> None:
    """GGC-74 batch finalize: scan a parent session's sibling subagents for one
    ticket, SUM their metrics, write ONE CSV row + post ONE Linear report.

    Fail-soft contract: ZERO matches → non-zero exit with a clear stderr line so
    the Workflow finalize stage catches it. The (ticket, run_stem) pair is the
    CSV/Linear upsert key (not session_id)."""
    cwd = args.cwd or os.getcwd()
    if not args.ticket_id:
        print("Error: --scan-subagents requires --ticket-id", file=sys.stderr)
        sys.exit(2)
    if not args.parent_session:
        print("Error: --scan-subagents requires --parent-session", file=sys.stderr)
        sys.exit(2)
    ticket_id = args.ticket_id.upper()
    run_stem = args.run_stem
    outcome = load_outcome_json(args.outcome_json)

    agg = scan_subagents_aggregate(
        args.parent_session,
        ticket_id,
        args.run_ts or "",
        exclude_agents=set(args.exclude_agent or []),
    )
    if agg is None:
        print(
            f"Error: --scan-subagents found ZERO matching subagent transcripts for "
            f"{ticket_id} under parent session {args.parent_session} "
            f"(run-ts cutoff {args.run_ts!r}). Nothing to finalize.",
            file=sys.stderr,
        )
        sys.exit(4)

    metrics = agg["metrics"]
    durations = agg["durations"]
    git_branch = agg["git_branch"]
    # GGC-125 Residual 1: the summed subagents all record gitBranch=<default>
    # (Workflow workers run with cwd=main repo), so agg["git_branch"] is
    # "trunk"/"main", never the ticket's worktree branch. --cwd IS the ticket
    # worktree here, so resolve the real branch from it; keep the transcript
    # branch as a fail-soft fallback when the worktree has already been removed.
    worktree_branch = get_git_branch(cwd)
    if worktree_branch:
        git_branch = worktree_branch
    provenance = agg["provenance"]
    current_total_cost = agg["total_cost"]
    # Compact, round-trippable provenance string for the CSV column.
    prov_str = ";".join(f"{p['stem']}:{p['cost']}" for p in provenance)

    # ---- Story points (GGC-71 reuse): explicit --story-points > stored CSV
    # value > snapped --manual-hours blind estimate. ----
    cumulative = get_ticket_cumulative(ticket_id, "", cwd, run_stem=run_stem)
    if args.story_points is None and cumulative and cumulative.get("stored_story_points"):
        args.story_points = cumulative["stored_story_points"]
    if args.story_points is None and args.manual_hours is not None:
        args.story_points = hours_to_story_point(args.manual_hours)

    # The synthetic "session_id" for this batch row is the run_stem (the legacy
    # session_id column is unused on this path — the upsert keys on run_stem).
    session_label = run_stem or args.parent_session

    if not args.no_csv:
        write_csv(
            metrics,
            durations,
            ticket_id,
            session_label,
            git_branch,
            args.story_points,
            args.manual_hours,
            run_stem=run_stem,
            metrics_provenance=prov_str,
            outcome=outcome,
        )
        print(f"\nCSV updated at {CSV_PATH} (run_stem={run_stem})", file=sys.stderr)

    session_number = get_session_number(ticket_id, session_label)
    report = format_report(
        metrics, durations, ticket_id, session_number, git_branch,
        session_label, args.summary_file, args.story_points,
        cumulative, current_total_cost,
        run_stem=run_stem, provenance=provenance, outcome=outcome,
    )

    if args.json:
        # GGC-89: include a token/cache breakdown + turns so the batch finalize
        # agent can write a substantive `### Summary` (parity with the
        # standalone /session-metrics flow). The minimal pre-GGC-89 payload
        # (cost + total_tokens only) gave the summary agent nothing to say
        # about cache utilization or prompt efficiency.
        j_in = j_out = j_cw = j_cr = 0
        for u in metrics["model_usage"].values():
            j_in += u["input_tokens"]
            j_out += u["output_tokens"]
            j_cw += u["cache_write_tokens"]
            j_cr += u["cache_read_tokens"]
        j_total = j_in + j_out + j_cw + j_cr
        print(json.dumps(
            {
                "ticket_id": ticket_id,
                "run_stem": run_stem,
                "parent_session": args.parent_session,
                "subagents_matched": len(provenance),
                "total_cost": round(current_total_cost, 4),
                "total_tokens": agg["total_tokens"],
                "total_turns": metrics["total_turns"],
                "wall_clock_sec": round(durations["wall_clock_sec"]),
                "active_proxy_sec": round(durations.get("active_proxy_sec", 0)),
                "tokens": {
                    "input": j_in,
                    "output": j_out,
                    "cache_write": j_cw,
                    "cache_read": j_cr,
                    "cache_read_pct": round(100 * j_cr / j_total) if j_total > 0 else 0,
                },
                "provenance": provenance,
            },
            indent=2,
            default=str,
        ))
    else:
        print(report)

    if not args.no_linear:
        api_key = get_linear_api_key()
        if api_key:
            ok = post_to_linear(ticket_id, report, api_key, session_label, run_stem=run_stem)
            if ok:
                print(f"Posted to Linear: {ticket_id} (run_stem={run_stem})", file=sys.stderr)
            else:
                print(f"Failed to post to Linear: {ticket_id}", file=sys.stderr)
        else:
            print("Warning: No Linear API key found in settings", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Claude Code session metrics")
    parser.add_argument("--pid", type=int, help="Process ID to look up session")
    parser.add_argument("--session-id", help="Session ID to analyze")
    parser.add_argument("--cwd", help="Working directory (for auto-detection)")
    parser.add_argument("--no-linear", action="store_true", help="Skip Linear posting")
    parser.add_argument("--no-csv", action="store_true", help="Skip CSV output")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument(
        "--summary-file",
        help="Path to file containing AI-generated summary to include in report",
    )
    parser.add_argument(
        "--story-points",
        type=int,
        choices=sorted(STORY_POINT_HOURS.keys()),
        help="Story point estimate for manual time comparison (1,2,3,5,8,13)",
    )
    parser.add_argument(
        "--ticket-id",
        help="Explicit ticket id (e.g. GGC-71); overrides cwd/branch auto-detection",
    )
    parser.add_argument(
        "--manual-hours",
        type=float,
        help="LLM-estimated pure-manual hours; snapped to the nearest story-point bucket. Ignored if --story-points is given.",
    )
    parser.add_argument(
        "--outcome-json",
        help=(
            "Path to a JSON file of PR outcome signals gathered by the finalize "
            "stage (keys: pr_url, merged, pr_state, cycle_time_sec, review_rounds, "
            "first_pass, reviewer_comments, ci_fail_count). Stored to the CSV "
            "outcome columns and rendered as an '### Outcome' report section. "
            "Absent/unreadable → metrics still post, outcome columns blank."
        ),
    )
    parser.add_argument(
        "--hook",
        action="store_true",
        help="Hook mode: read context from stdin, CSV only, no Linear",
    )
    # ---- GGC-74: batch dispatcher finalize mode ----
    # Scan a parent session's sibling subagent transcripts for one ticket and
    # aggregate (SUM) their cost/tokens/durations into a single synthetic
    # metrics object. Used by the /ggx-dispatcher --metric finalize stage where
    # a worker's transcript lives under the PARENT's subagents/ dir and is
    # invisible to find_current_session (top-level glob only).
    parser.add_argument(
        "--scan-subagents",
        action="store_true",
        help=(
            "Batch finalize mode: scan <parent-session>/subagents/agent-*.jsonl "
            "for --ticket-id, sum their metrics, and post one CSV row + Linear "
            "report. Requires --parent-session, --run-ts, --run-stem."
        ),
    )
    parser.add_argument(
        "--parent-session",
        help="Parent (launching) session UUID whose subagents/ dir to scan (--scan-subagents).",
    )
    parser.add_argument(
        "--run-ts",
        help="Run-start UTC cutoff (compact YYYYMMDDTHHMMSSZ); only subagents started at/after it count.",
    )
    parser.add_argument(
        "--run-stem",
        help="Upsert key (the RUN_TS-$$ stem) used for the (ticket,run) CSV/Linear dedup in --scan-subagents.",
    )
    parser.add_argument(
        "--exclude-agent",
        action="append",
        metavar="STEM",
        help=(
            "Subagent transcript stem to exclude from --scan-subagents "
            "aggregation (repeatable). The scanner's own transcript is also "
            "auto-excluded by content detection (GGC-78), so this is an explicit "
            "belt-and-braces lever, not the primary guard."
        ),
    )
    args = parser.parse_args()

    # Hook mode: read session info from stdin, force CSV-only
    hook_ctx = None
    if args.hook:
        hook_ctx = read_hook_stdin()
        args.no_linear = True  # Never post to Linear from a hook

    # ---- GGC-74: batch dispatcher finalize mode ----
    # Scan the parent session's sibling subagents for this ticket, SUM them, and
    # emit ONE CSV row + Linear "AI Session Report". Returns before the legacy
    # single-session resolution (and so never reaches the P2 subagent guard).
    if args.scan_subagents:
        run_scan_subagents_mode(args)
        return

    # ---- Resolve session ----
    cwd = args.cwd or (hook_ctx or {}).get("cwd") or os.getcwd()
    session_id = None
    jsonl_path = None

    if hook_ctx and hook_ctx.get("session_id"):
        session_id = hook_ctx["session_id"]
        # Locate by session id first (cwd-independent); the cwd-hash path is only
        # correct when the live cwd is still the session's startup dir.
        _, jsonl_path = find_jsonl_by_session_id(session_id)
        if not jsonl_path:
            jsonl_path = PROJECTS_DIR / cwd_to_project_dir_name(cwd) / f"{session_id}.jsonl"
    elif args.session_id:
        session_id = args.session_id
        _, jsonl_path = find_jsonl_by_session_id(session_id)
        if not jsonl_path:
            jsonl_path = PROJECTS_DIR / cwd_to_project_dir_name(cwd) / f"{session_id}.jsonl"
    elif args.pid:
        info = find_session_by_pid(args.pid)
        if info:
            session_id = info["sessionId"]
            cwd = info.get("cwd", cwd)
            project_name = cwd_to_project_dir_name(cwd)
            jsonl_path = PROJECTS_DIR / project_name / f"{session_id}.jsonl"
    else:
        # Prefer the live session id (cwd-independent) over a cwd-hash glob: a
        # finalize that runs from a worktree (EnterWorktree moved cwd off the
        # startup dir where the transcript is filed) still resolves. Guards:
        #  - skip inside a child/subagent session — there CLAUDE_CODE_SESSION_ID is
        #    the SUBAGENT's id; the dispatcher batch path (--scan-subagents) owns it.
        #  - skip when --cwd was explicit — the caller pinned a dir on purpose.
        env_sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
        if env_sid and not os.environ.get("CLAUDE_CODE_CHILD_SESSION") and not args.cwd:
            session_id, jsonl_path = find_jsonl_by_session_id(env_sid)
        if not jsonl_path:
            session_id, jsonl_path = find_current_session(cwd)

    if not jsonl_path or not jsonl_path.exists():
        print("Error: Could not find session JSONL", file=sys.stderr)
        print(f"  session_id: {session_id}", file=sys.stderr)
        print(f"  jsonl_path: {jsonl_path}", file=sys.stderr)
        sys.exit(1)

    # ---- Parse ----
    metrics = parse_session(jsonl_path)
    if not metrics["timestamps"]:
        print("Warning: Empty session — nothing to report.", file=sys.stderr)
        sys.exit(0)

    durations = compute_durations(metrics)
    git_branch = metrics["git_branch"] or get_git_branch(cwd)
    ticket_id = detect_ticket_id(cwd, git_branch, args.ticket_id)
    # P2 (GGC-71): when the caller passed an explicit --ticket-id, refuse to post
    # metrics for a session that clearly is not this ticket's — better a detectable
    # failure than silently measuring the wrong (e.g. parent/sibling) session.
    if args.ticket_id:
        if "subagents" in jsonl_path.parts:
            print(
                f"Error: resolved session {jsonl_path} is a subagent transcript; "
                f"refusing to post wrong-session metrics for {args.ticket_id.upper()}. "
                f"Pass --session-id explicitly.",
                file=sys.stderr,
            )
            sys.exit(3)
        session_ticket = detect_ticket_id(cwd, git_branch)  # no explicit override
        if session_ticket != "UNKNOWN" and session_ticket != args.ticket_id.upper():
            print(
                f"Error: --ticket-id {args.ticket_id.upper()} disagrees with the "
                f"session-resolved ticket {session_ticket}; refusing to post "
                f"wrong-session metrics. Pass --session-id to disambiguate.",
                file=sys.stderr,
            )
            sys.exit(3)
    session_number = get_session_number(ticket_id, session_id)

    # ---- Cumulative + story point reuse ----
    cumulative = get_ticket_cumulative(ticket_id, session_id, cwd)
    if args.story_points is None and cumulative and cumulative.get("stored_story_points"):
        args.story_points = cumulative["stored_story_points"]
    # GGC-71: if still no SP (explicit/stored), snap the LLM's manual-hours estimate.
    if args.story_points is None and args.manual_hours is not None:
        args.story_points = hours_to_story_point(args.manual_hours)

    # ---- Current session cost ----
    current_total_cost = 0.0
    for model in metrics["model_usage"]:
        u = metrics["model_usage"][model]
        family = get_model_family(model)
        current_total_cost += calculate_cost(
            {
                "input_tokens": u["input_tokens"],
                "output_tokens": u["output_tokens"],
                "cache_creation_input_tokens": u["cache_write_tokens"],
                "cache_read_input_tokens": u["cache_read_tokens"],
            },
            family,
            model,
        )

    # ---- Outcome signals (PR2) ----
    outcome = load_outcome_json(args.outcome_json)

    # ---- CSV (raw session metrics, pre-enrichment) ----
    if not args.no_csv:
        write_csv(metrics, durations, ticket_id, session_id, git_branch, args.story_points, args.manual_hours, outcome=outcome)
        print(f"\nCSV updated at {CSV_PATH}", file=sys.stderr)

    # ---- Output ----
    if args.json:
        output = {
            "ticket_id": ticket_id,
            "session_id": session_id,
            "session_number": session_number,
            "git_branch": git_branch,
            "durations": {
                "wall_clock_sec": durations["wall_clock_sec"],
                "active_sec": durations["active_sec"],
                "active_proxy_sec": round(durations.get("active_proxy_sec", 0)),
                "idle_sec": durations["idle_sec"],
            },
            "model_usage": {k: dict(v) for k, v in metrics["model_usage"].items()},
            "agents": [
                {
                    "type": a["agent_type"],
                    "description": a["description"],
                    "duration_sec": a["total_duration_ms"] / 1000,
                    "tokens": a["total_tokens"],
                    "model": a["model"],
                }
                for a in metrics["agents"]
            ],
            "message_counts": {
                "user": metrics["user_msgs"],
                "assistant": metrics["assistant_msgs"],
                "tool_calls": metrics["tool_calls"],
                "turns": metrics["total_turns"],
            },
        }
        manual_sec = story_point_seconds(args.story_points)
        if manual_sec is not None:
            ai_sec = durations["active_sec"]
            proxy_sec = durations.get("active_proxy_sec", 0)
            basis = ai_sec if ai_sec > 0 else proxy_sec
            ta: dict = {
                "story_points": args.story_points,
                "story_points_from_history": args.story_points == (cumulative or {}).get("stored_story_points"),
                "estimated_manual_sec": manual_sec,
                "ai_active_sec": ai_sec,
                "ai_active_proxy_sec": round(proxy_sec),
                "multiplier": round(manual_sec / basis, 1) if basis > 0 else None,
                "multiplier_basis": "active" if ai_sec > 0 else ("active_proxy" if proxy_sec > 0 else None),
            }
            if cumulative and cumulative["prior_session_count"] > 0:
                cum_ai = cumulative["prior_active_sec"] + ai_sec
                ta["cumulative"] = {
                    "total_sessions": cumulative["prior_session_count"] + 1,
                    "total_active_sec": cum_ai,
                    "total_wall_sec": cumulative["prior_wall_sec"] + durations["wall_clock_sec"],
                    "total_cost": round(cumulative["prior_cost"] + current_total_cost, 2),
                    "multiplier": round(manual_sec / cum_ai, 1) if cum_ai > 0 else None,
                }
            output["time_analysis"] = ta
        print(json.dumps(output, indent=2, default=str))
    else:
        report = format_report(
            metrics, durations, ticket_id, session_number, git_branch,
            session_id, args.summary_file, args.story_points,
            cumulative, current_total_cost, outcome=outcome,
        )
        print(report)

    # ---- Linear ----
    if not args.no_linear and ticket_id != "UNKNOWN":
        api_key = get_linear_api_key()
        if api_key:
            # Reuse cached report if available, otherwise generate
            report_md = report if not args.json else format_report(
                metrics, durations, ticket_id, session_number, git_branch,
                session_id, args.summary_file, args.story_points,
                cumulative, current_total_cost, outcome=outcome,
            )
            ok = post_to_linear(ticket_id, report_md, api_key, session_id)
            if ok:
                print(f"Posted to Linear: {ticket_id}", file=sys.stderr)
            else:
                print(f"Failed to post to Linear: {ticket_id}", file=sys.stderr)
        else:
            print("Warning: No Linear API key found in settings", file=sys.stderr)


if __name__ == "__main__":
    main()
