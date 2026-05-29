#!/usr/bin/env python3
"""Headless Notion writer for /daily-summary 4b (Work Items / Work History).

WHY THIS EXISTS
---------------
The interactive skill writes Work Items via the high-level OAuth MCP tools
(`mcp__claude_ai_Notion__notion-create-pages` / `mcp__notion-hosted__notion-*`).
Those OAuth tokens periodically lapse (refresh-token rotation + missed nightly
runs when the Mac is asleep), which silently breaks the headless launchd run.

This helper does 4b — and ONLY 4b — over the Notion REST API using a long-lived
internal-integration token (`ntn_...`), which never expires. It creates one
page per finalized row. It writes PROPERTIES ONLY, never block content, so it
cannot touch the dashboard's charts / column_list / child_database blocks —
the over-delete hazard that retired the old markdown->blocks helper does not
apply here.

4d (dashboard refresh) is intentionally NOT handled here; headless runs skip it
(documented in SKILL.md). Dashboard refresh stays interactive-only.

INPUT
-----
A JSON file: an array of finalized row objects. Each row is the LLM's Step-3
output (summary synthesized, tickets sanity-checked / API-validated). Fields:

  {
    "title":           "CET-8382: truck/mover reset 分析",   # 工作摘要, required, ticket-prefixed by caller
    "tickets":         ["CET-8382"],                          # multi_select names; [] for non-ticket rows
    "linear":          "https://linear.app/gogox/issue/CAF-232" | null,
    "date":            "2026-05-28",                          # YYYY-MM-DD, required
    "time_range":      "23:02-23:41",                         # 時段
    "sessions":        1,                                      # int | null
    "cost":            36.29,                                  # 費用, float | null
    "tokens":          16058275,                               # int | null
    "active_hours":    0.314,
    "thinking_hours":  0.346,
    "subagent_spawns": 0,
    "cost_opus":       36.29,                                  # null -> omit
    "cost_sonnet":     0,
    "cost_haiku":      0,
    "cache_read":      15737396,                               # null -> omit
    "cache_creation":  252607,                                 # null -> omit
    "output":          "research"                              # select, required
  }

USAGE
-----
  python3 notion_rest.py create-work-items rows.json
  python3 notion_rest.py create-work-items rows.json --dry-run
  python3 notion_rest.py create-work-items rows.json --db <database_id>
  python3 notion_rest.py archive <page_id>          # used by verification / cleanup

Token resolution order:
  1. --token CLI flag
  2. $NOTION_TOKEN
  3. ~/.claude/daily-summary-mcp.json  (OPENAPI_MCP_HEADERS.Authorization "Bearer ...")
DB id resolution order:
  1. --db CLI flag
  2. ~/.claude/daily-summary-config.json  (notion.work_items_db_id)

Stdlib-only. Exits non-zero if any page fails to create.
"""
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

NOTION_VERSION = "2022-06-28"
MCP_CONF = os.path.expanduser("~/.claude/daily-summary-mcp.json")
DS_CONF = os.path.expanduser("~/.claude/daily-summary-config.json")


def _ssl_context():
    """Default context, but fall back to certifi's CA bundle so the framework
    python build (which ships without a usable CA store) still works."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def resolve_token(cli_token):
    if cli_token:
        return cli_token
    if os.environ.get("NOTION_TOKEN"):
        return os.environ["NOTION_TOKEN"]
    try:
        with open(MCP_CONF) as f:
            conf = json.load(f)
        hdrs = json.loads(conf["mcpServers"]["notion"]["env"]["OPENAPI_MCP_HEADERS"])
        auth = hdrs["Authorization"]
        return auth.split(" ", 1)[1] if auth.lower().startswith("bearer ") else auth
    except Exception as e:
        sys.exit(f"FATAL: could not resolve Notion token ({e}). "
                 f"Pass --token, set $NOTION_TOKEN, or fix {MCP_CONF}.")


def resolve_db(cli_db):
    if cli_db:
        return cli_db
    try:
        with open(DS_CONF) as f:
            conf = json.load(f)
        db = conf["notion"]["work_items_db_id"]
        if not db:
            raise ValueError("notion.work_items_db_id is empty")
        return db
    except Exception as e:
        sys.exit(f"FATAL: could not resolve work_items_db_id ({e}). "
                 f"Pass --db or fix {DS_CONF}.")


def _api(method, url, token, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    })
    ctx = _ssl_context()
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            body = json.loads(body)
        except Exception:
            pass
        return e.code, body


def _num(props, name, val):
    """Set a number property only when val is not None (omit -> empty in Notion)."""
    if val is not None:
        props[name] = {"number": val}


def build_properties(row):
    title = row.get("title")
    if not title:
        raise ValueError("row missing required 'title'")
    if not row.get("date"):
        raise ValueError(f"row '{title}' missing required 'date'")
    if not row.get("output"):
        raise ValueError(f"row '{title}' missing required 'output'")

    props = {
        "工作摘要": {"title": [{"text": {"content": title[:2000]}}]},
        "Ticket": {"multi_select": [{"name": t} for t in (row.get("tickets") or [])]},
        "Date": {"date": {"start": row["date"]}},
        "Output": {"select": {"name": row["output"]}},
    }
    if row.get("time_range"):
        props["時段"] = {"rich_text": [{"text": {"content": row["time_range"]}}]}
    # Linear url: explicit null clears; omit when absent
    if "linear" in row:
        props["Linear"] = {"url": row["linear"] or None}

    _num(props, "Sessions", row.get("sessions"))
    _num(props, "費用", row.get("cost"))
    _num(props, "Tokens", row.get("tokens"))
    _num(props, "Active Hours", row.get("active_hours"))
    _num(props, "Thinking Hours", row.get("thinking_hours"))
    _num(props, "Subagent Spawns", row.get("subagent_spawns"))
    _num(props, "Cost Opus", row.get("cost_opus"))
    _num(props, "Cost Sonnet", row.get("cost_sonnet"))
    _num(props, "Cost Haiku", row.get("cost_haiku"))
    _num(props, "Cache Read Tokens", row.get("cache_read"))
    _num(props, "Cache Creation Tokens", row.get("cache_creation"))
    return props


def cmd_create_work_items(args):
    token = resolve_token(args.get("token"))
    db = resolve_db(args.get("db"))
    with open(args["rows"]) as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        sys.exit("FATAL: rows file must be a JSON array.")

    dry = args.get("dry_run")
    ok, fail = 0, 0
    for i, row in enumerate(rows):
        try:
            props = build_properties(row)
        except Exception as e:
            print(f"[{i}] SKIP build error: {e}")
            fail += 1
            continue
        if dry:
            print(f"[{i}] DRY title={row.get('title')!r} "
                  f"tickets={row.get('tickets')} output={row.get('output')}")
            ok += 1
            continue
        status, body = _api("POST", "https://api.notion.com/v1/pages", token,
                            {"parent": {"database_id": db}, "properties": props})
        if status == 200:
            print(f"[{i}] OK   {body.get('url')}  ({row.get('title')!r})")
            ok += 1
        else:
            msg = body.get("message") if isinstance(body, dict) else body
            print(f"[{i}] FAIL HTTP {status}: {msg}  ({row.get('title')!r})")
            fail += 1

    print(f"\n{'DRY-RUN ' if dry else ''}create-work-items: {ok} ok, {fail} failed "
          f"(db={db})")
    sys.exit(1 if fail else 0)


def cmd_archive(args):
    token = resolve_token(args.get("token"))
    pid = args["page_id"]
    status, body = _api("PATCH", f"https://api.notion.com/v1/pages/{pid}", token,
                        {"archived": True})
    if status == 200:
        print(f"archived {pid}")
        sys.exit(0)
    msg = body.get("message") if isinstance(body, dict) else body
    sys.exit(f"FAIL archive HTTP {status}: {msg}")


def parse_argv(argv):
    if not argv:
        sys.exit(__doc__)
    cmd, rest = argv[0], argv[1:]
    args, positional = {}, []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--dry-run":
            args["dry_run"] = True
        elif a in ("--token", "--db"):
            args[a.lstrip("-")] = rest[i + 1]
            i += 1
        else:
            positional.append(a)
        i += 1
    return cmd, args, positional


def main():
    cmd, args, positional = parse_argv(sys.argv[1:])
    if cmd == "create-work-items":
        if not positional:
            sys.exit("usage: notion_rest.py create-work-items rows.json [--dry-run] [--db ID] [--token T]")
        args["rows"] = positional[0]
        cmd_create_work_items(args)
    elif cmd == "archive":
        if not positional:
            sys.exit("usage: notion_rest.py archive <page_id> [--token T]")
        args["page_id"] = positional[0]
        cmd_archive(args)
    else:
        sys.exit(f"unknown command {cmd!r}. Use create-work-items | archive.")


if __name__ == "__main__":
    main()
