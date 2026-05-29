#!/usr/bin/env python3
"""Headless Notion writer for /daily-summary 4b + 4d (Work Items + dashboard).

WHY THIS EXISTS
---------------
The interactive skill writes via the high-level OAuth MCP tools
(`mcp__claude_ai_Notion__notion-create-pages` / `mcp__notion-hosted__notion-*`).
Those OAuth tokens periodically lapse (refresh-token rotation + missed nightly
runs when the Mac is asleep), which silently breaks the headless launchd run.

This helper does 4b AND 4d over the Notion REST API using a long-lived
internal-integration token (`ntn_...`), which never expires.

  create-work-items  — 4b. Creates one Work Items page per finalized row
                       (PROPERTIES ONLY, never block content).

  refresh-dashboard  — 4d. Section-replaces the parent page's 📊 / 📈 H2
                       sections and upserts the Weekly PRs + Weekly Metrics
                       databases. Takes a fully-prepared JSON payload (the
                       orchestrator computes every number and generates the
                       zh-TW AI text; this helper does only the mechanical
                       Notion REST I/O — no LLM, no markdown parsing).

CHART-BOUNDARY SAFETY (4d section-replace)
------------------------------------------
The parent page's 📈 section sits directly above the "🔧 Debug View"
column_list. A naive "delete to next heading" would destroy it. So section
content is collected from the matched heading until the FIRST barrier block
(another heading_*, or any column_list / child_database / child_page /
synced_block / table_of_contents / toggle / embed / image / video) — the
barrier is never crossed, never deleted, matching SKILL.md section B's
chart-preservation set. New content is inserted with the `after` cursor pinned to the
heading, so blocks land inside the section, not at page end. Use --dry-run
to print the exact delete/append plan before any mutation.

STALE-ID SELF-HEAL (4d db upsert)
---------------------------------
If a configured database id 404s, the helper locates the database by title
among the inner page's child_database blocks and persists the corrected id
back to daily-summary-config.json (mirrors the interactive [Locate-or-create]).

INPUT (create-work-items)
-------------------------
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
  python3 notion_rest.py refresh-dashboard payload.json
  python3 notion_rest.py refresh-dashboard payload.json --dry-run   # print plan, no writes
  python3 notion_rest.py archive <page_id>          # used by verification / cleanup

INPUT (refresh-dashboard)
-------------------------
A JSON file. `parent_page_id` / `inner_page_id` default to config when omitted.
Every value is pre-computed by the orchestrator; this helper just writes it.

  {
    "parent_page_id": "...",            # optional, defaults to config
    "inner_page_id":  "...",            # optional, defaults to config
    "sections": [                       # parent-page H2 section-replace (4d-2, 4d-5)
      {
        "locate_prefix": "📊 本週交付",  # match the heading_2 whose text startswith this
        "heading_text":  "📊 本週交付 (W22 · May 25 ~ May 31 · Day 4/5 進行中)",  # new H2 text
        "blocks": [                     # ordered content blocks, compact DSL (see below)
          {"callout": {"emoji": "🧠", "color": "gray_background", "text": "[AI草稿] ..."}},
          {"callout": {"emoji": "📊", "color": "blue_background",
                       "rich": [{"t": "淨交付效率 (NDE) "}, {"t": "0.1", "b": true}]}},
          {"table":  {"headers": ["指標","本週","vs 上週","vs 12週均"],
                       "rows": [["NDE","0.1","—","(均 0.3)"]]}},
          {"link":   {"text": "→ 本週明細 & 歷史", "url": "https://www.notion.so/..."}}
        ]
      }
    ],
    "databases": [                      # inner-page DB upserts (4d-3)
      {
        "db": "d46f8ecd-...",           # configured id; 404 -> locate by title + self-heal
        "db_title": "Weekly PR",        # used for title-search fallback
        "config_key": "weekly_prs_db_id",  # where to persist a healed id
        "key": "Title",                 # upsert key property (default: the title property)
        "rows": [
          {"Title": "...", "Week": "W22", "Repo": "gogox-client-flutter",
           "State": "merged", "Opened": "2026-05-25", "Merged": "2026-05-26",
           "Ticket": {"text": "CAF-232", "url": "https://github.com/..."}}  # rich_text + link
        ]
      }
    ]
  }

  Block DSL (expanded to Notion blocks in Python):
    callout   {emoji, color, text}  OR  {emoji, color, rich:[seg,...]}
    table     {headers:[...], rows:[[cell,...],...]}  (first row = column header)
    link      {text, url}                        (a paragraph with one linked run)
    paragraph {text}  OR  {rich:[seg,...]}
  rich segment: {t:"text", b:bool, i:bool, code:bool, color:"...", link:"url"}
  table cell: a plain string, OR a rich segment dict {t,b,...}, OR a list of
    segments. Bold etc. is structural — NOT markdown (pass {"t":"May","b":true},
    never "**May**", which would render with literal asterisks).
  Property values are coerced by the LIVE db schema's type, so callers pass
  plain values; rich_text hyperlinks pass {"text":..., "url":...}; null/"" on a
  select/date/url clears it, null on any other type omits the property.

Token resolution order:
  1. --token CLI flag
  2. $NOTION_TOKEN
  3. ~/.claude/daily-summary-mcp.json  (OPENAPI_MCP_HEADERS.Authorization "Bearer ...")
DB id resolution order:
  1. --db CLI flag
  2. ~/.claude/daily-summary-config.json  (notion.work_items_db_id)

Stdlib-only. create-work-items exits non-zero if any page fails; refresh-dashboard
is best-effort (4b is the primary deliverable) and always exits 0.
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

SETUP_GUIDE = f"""\
No Notion token found for the headless write path.

This is a per-user, one-time setup (the token is personal and is NOT stored in
the repo). Interactive `/daily-summary` runs do NOT need this — they use the
OAuth Notion MCP. You only need a token if you run the skill headless (the
nightly launchd job), which writes Work Items via a Notion internal-integration
token (no expiry, so the job never needs re-auth).

To set it up:

  1. Create a Notion internal integration:
       https://www.notion.so/profile/integrations  ->  New integration
     Copy its "Internal Integration Secret" (starts with `ntn_`).

  2. Share your work-record hub page with that integration:
     open the Notion page that owns your Work History DB (the
     `parent_page_id` in {DS_CONF}) -> top-right ... -> Connections ->
     add your integration. Child databases inherit access.

  3. Create {MCP_CONF} with this exact shape (replace ntn_YOURTOKEN):

     {{
       "mcpServers": {{
         "notion": {{
           "env": {{
             "OPENAPI_MCP_HEADERS": "{{\\"Authorization\\":\\"Bearer ntn_YOURTOKEN\\",\\"Notion-Version\\":\\"2022-06-28\\"}}"
           }}
         }}
       }}
     }}

  4. chmod 600 {MCP_CONF}

Alternatively pass --token ntn_... or export NOTION_TOKEN=ntn_... for a one-off run.
"""


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
        sys.stderr.write(SETUP_GUIDE)
        sys.stderr.write(f"\n(could not read {MCP_CONF}: {e})\n")
        sys.exit(2)


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


# ───────────────────────── 4d: refresh-dashboard ─────────────────────────
#
# Mechanical Notion REST I/O only. No LLM, no markdown parsing. The orchestrator
# hands us a fully-prepared payload (see module docstring) and we translate it
# into block / property writes.

API = "https://api.notion.com/v1"

# Block types that END a section. The section-replace never crosses or deletes
# one of these — this is what protects the 🔧 Debug View column_list that sits
# directly below the 📈 section on the parent page.
# Mirrors SKILL.md section B's chart-preservation set (column_list / child_* /
# embed / image / video / toggle / synced_block) plus headings + table_of_contents.
BARRIER_TYPES = {
    "heading_1", "heading_2", "heading_3",
    "column_list", "child_database", "child_page",
    "synced_block", "table_of_contents",
    "toggle", "embed", "image", "video",
}


def _plain(block):
    """Concatenated plain_text of a block's rich_text (empty for non-text blocks)."""
    body = block.get(block.get("type"), {})
    return "".join(x.get("plain_text", "") for x in body.get("rich_text", []))


def _list_children(token, block_id):
    """All direct children of a block/page, following pagination."""
    out, cursor = [], None
    while True:
        url = f"{API}/blocks/{block_id}/children?page_size=100"
        if cursor:
            url += f"&start_cursor={cursor}"
        status, body = _api("GET", url, token)
        if status != 200:
            msg = body.get("message") if isinstance(body, dict) else body
            raise RuntimeError(f"list children {block_id}: HTTP {status} {msg}")
        out += body["results"]
        if not body.get("has_more"):
            return out
        cursor = body["next_cursor"]


def _query_db(token, db_id):
    """All rows of a database, following pagination."""
    out, cursor = [], None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        status, body = _api("POST", f"{API}/databases/{db_id}/query", token, payload)
        if status != 200:
            msg = body.get("message") if isinstance(body, dict) else body
            raise RuntimeError(f"query db {db_id}: HTTP {status} {msg}")
        out += body["results"]
        if not body.get("has_more"):
            return out
        cursor = body["next_cursor"]


# ---- compact block DSL -> Notion block JSON ----

def _rich(text=None, segs=None):
    """Build a rich_text array from either a plain string or a list of segments
    {t, b, i, code, color, link}."""
    if segs is not None:
        out = []
        for s in segs:
            rt = {"type": "text", "text": {"content": s.get("t", "")}}
            if s.get("link"):
                rt["text"]["link"] = {"url": s["link"]}
            ann = {}
            if s.get("b"):
                ann["bold"] = True
            if s.get("i"):
                ann["italic"] = True
            if s.get("code"):
                ann["code"] = True
            if s.get("color"):
                ann["color"] = s["color"]
            if ann:
                rt["annotations"] = ann
            out.append(rt)
        return out
    return [{"type": "text", "text": {"content": text or ""}}]


def _cell_rich(cell):
    """A table cell is a plain string, a single rich segment dict {t,b,...}, or
    a list of such segments. No markdown is parsed — bold/italic etc. are carried
    structurally (so e.g. a current-week row passes {"t": "May 2026", "b": true}
    rather than the literal "**May 2026**")."""
    if isinstance(cell, list):
        return _rich(segs=cell)
    if isinstance(cell, dict):
        return _rich(segs=[cell])
    return _rich(str(cell))


def _dsl_to_block(spec):
    if "callout" in spec:
        c = spec["callout"]
        return {"type": "callout", "callout": {
            "rich_text": _rich(c.get("text"), c.get("rich")),
            "icon": {"type": "emoji", "emoji": c.get("emoji", "💡")},
            "color": c.get("color", "default")}}
    if "link" in spec:
        l = spec["link"]
        return {"type": "paragraph", "paragraph": {"rich_text": [
            {"type": "text", "text": {"content": l["text"], "link": {"url": l["url"]}}}]}}
    if "paragraph" in spec:
        p = spec["paragraph"]
        rich = _rich(p.get("text"), p.get("rich")) if isinstance(p, dict) else _rich(p)
        return {"type": "paragraph", "paragraph": {"rich_text": rich}}
    if "table" in spec:
        t = spec["table"]
        headers, rows = t["headers"], t.get("rows", [])
        width = len(headers)

        def row_block(cells):
            cells = list(cells) + [""] * (width - len(cells))
            return {"type": "table_row",
                    "table_row": {"cells": [_cell_rich(c) for c in cells[:width]]}}

        children = [row_block(headers)] + [row_block(r) for r in rows]
        return {"type": "table", "table": {
            "table_width": width, "has_column_header": True,
            "has_row_header": False, "children": children}}
    if "heading_2" in spec:
        return {"type": "heading_2", "heading_2": {"rich_text": _rich(spec["heading_2"])}}
    raise ValueError(f"unknown block spec keys: {list(spec)}")


def _replace_section(token, parent_id, sec, dry):
    """Replace one H2 section's content on the parent page, never crossing a
    barrier block (protects the Debug View column_list)."""
    prefix = sec["locate_prefix"]
    children = _list_children(token, parent_id)
    idx = None
    for i, b in enumerate(children):
        if b["type"].startswith("heading_") and _plain(b).startswith(prefix):
            idx, htype, hid = i, b["type"], b["id"]
            break
    if idx is None:
        print(f"  [section {prefix!r}] heading NOT FOUND — skip")
        return

    content = []
    for b in children[idx + 1:]:
        if b["type"] in BARRIER_TYPES:
            break
        content.append(b)
    new_blocks = [_dsl_to_block(x) for x in sec.get("blocks", [])]

    if dry:
        print(f"  [section {prefix!r}] heading={hid[:8]} ({htype}) "
              f"-> would DELETE {len(content)} block(s) "
              f"{[b['type'] for b in content]}, APPEND {len(new_blocks)} new, "
              f"set heading -> {sec.get('heading_text')!r}")
        return

    if sec.get("heading_text"):
        st, bd = _api("PATCH", f"{API}/blocks/{hid}", token,
                      {htype: {"rich_text": _rich(sec["heading_text"])}})
        if st != 200:
            print(f"    WARN update heading HTTP {st}: {bd.get('message') if isinstance(bd, dict) else bd}")
    for b in content:
        st, bd = _api("DELETE", f"{API}/blocks/{b['id']}", token)
        if st != 200:
            print(f"    WARN delete {b['id'][:8]} HTTP {st}: {bd.get('message') if isinstance(bd, dict) else bd}")
    if new_blocks:
        st, bd = _api("PATCH", f"{API}/blocks/{parent_id}/children", token,
                      {"children": new_blocks, "after": hid})
        if st != 200:
            raise RuntimeError(f"append HTTP {st}: {bd.get('message') if isinstance(bd, dict) else bd}")
    print(f"  [section {prefix!r}] replaced ({len(content)} deleted, {len(new_blocks)} appended)")


def _persist_db_id(config_key, db_id):
    if not config_key:
        return
    try:
        with open(DS_CONF) as f:
            conf = json.load(f)
        conf.setdefault("notion", {})[config_key] = db_id
        with open(DS_CONF, "w") as f:
            json.dump(conf, f, ensure_ascii=False, indent=2)
        print(f"    [config] self-healed notion.{config_key} = {db_id}")
    except Exception as e:
        print(f"    WARN could not persist {config_key}: {e}")


def _resolve_db(token, spec, inner_page_id):
    """Configured id, or locate-by-title under the inner page and self-heal."""
    db_id = spec.get("db")
    if db_id:
        st, _ = _api("GET", f"{API}/databases/{db_id}", token)
        if st == 200:
            return db_id
        print(f"    [locate] configured db {db_id[:8]} -> HTTP {st}, searching by title")
    title = spec.get("db_title")
    if not inner_page_id:
        raise RuntimeError(f"db {title!r} unresolved and no inner_page_id for title search")
    for b in _list_children(token, inner_page_id):
        if b["type"] == "child_database" and b["child_database"].get("title") == title:
            found = b["id"]
            print(f"    [locate] found {title!r} db = {found}")
            _persist_db_id(spec.get("config_key"), found)
            return found
    raise RuntimeError(f"could not locate db {title!r} under inner page {inner_page_id}")


def _coerce_prop(ptype, val):
    """Coerce a plain payload value to a Notion property value, driven by the
    live db schema's type. Returns None to OMIT the property."""
    if ptype == "title":
        return {"title": _rich(str(val))} if val is not None else None
    if ptype == "rich_text":
        if val is None:
            return None
        if isinstance(val, dict) and "text" in val:
            rt = {"type": "text", "text": {"content": val["text"]}}
            if val.get("url"):
                rt["text"]["link"] = {"url": val["url"]}
            return {"rich_text": [rt]}
        return {"rich_text": _rich(str(val))}
    if ptype == "number":
        return {"number": val} if val is not None else None
    if ptype == "select":
        return {"select": ({"name": str(val)} if val not in (None, "") else None)}
    if ptype == "multi_select":
        # null omits (per contract); [] explicitly clears.
        if val is None:
            return None
        return {"multi_select": [{"name": str(x)} for x in val]}
    if ptype == "date":
        return {"date": ({"start": val} if val else None)}
    if ptype == "url":
        return {"url": (val or None)}
    # unknown -> best-effort text
    return {"rich_text": _rich(str(val))} if val is not None else None


def _key_value(page, key_prop):
    pr = page["properties"].get(key_prop, {})
    t = pr.get("type")
    if t == "title":
        return "".join(x.get("plain_text", "") for x in pr["title"])
    if t == "rich_text":
        return "".join(x.get("plain_text", "") for x in pr["rich_text"])
    if t == "select":
        return (pr.get("select") or {}).get("name")
    return None


def _upsert_db(token, spec, inner_page_id, dry):
    db_id = _resolve_db(token, spec, inner_page_id)
    st, schema = _api("GET", f"{API}/databases/{db_id}", token)
    if st != 200:
        raise RuntimeError(f"get db {db_id}: HTTP {st} {schema}")
    types = {k: v["type"] for k, v in schema["properties"].items()}
    title_prop = next((k for k, t in types.items() if t == "title"), None)
    key = spec.get("key", title_prop)
    if not key or key not in types:
        raise RuntimeError(
            f"upsert key {key!r} not a property of db {db_id} "
            f"(schema: {sorted(types)}) — fix spec.key")

    existing = _query_db(token, db_id)
    index = {}
    for p in existing:
        kv = _key_value(p, key)
        if kv is not None:
            index.setdefault(kv, p["id"])

    created = updated = failed = 0
    for row in spec.get("rows", []):
        raw_key = row.get(key)
        if raw_key is None or str(raw_key).strip() == "":
            print(f"    WARN skip row with empty key {key!r}: {row!r:.80}")
            failed += 1
            continue
        kv = str(raw_key)
        props = {}
        for pname, pval in row.items():
            if pname not in types:
                continue
            cp = _coerce_prop(types[pname], pval)
            if cp is not None:
                props[pname] = cp
        existing_id = index.get(kv)
        if dry:
            print(f"    DRY {'update' if existing_id else 'create'} key={kv!r} "
                  f"({len(props)} props)")
            updated += bool(existing_id)
            created += not bool(existing_id)
            continue
        if existing_id:
            st, bd = _api("PATCH", f"{API}/pages/{existing_id}", token, {"properties": props})
            ok = st == 200
            updated += ok
        else:
            st, bd = _api("POST", f"{API}/pages", token,
                          {"parent": {"database_id": db_id}, "properties": props})
            ok = st == 200
            created += ok
        if not (st == 200):
            failed += 1
            msg = bd.get("message") if isinstance(bd, dict) else bd
            print(f"    WARN upsert key={kv!r} HTTP {st}: {msg}")
    print(f"  [db {spec.get('db_title', db_id)}] {'DRY ' if dry else ''}"
          f"created={created} updated={updated} failed={failed} (db={db_id})")


def cmd_refresh_dashboard(args):
    token = resolve_token(args.get("token"))
    with open(args["payload"]) as f:
        payload = json.load(f)
    dry = args.get("dry_run")
    try:
        with open(DS_CONF) as f:
            conf = json.load(f).get("notion", {})
    except Exception:
        conf = {}
    parent = payload.get("parent_page_id") or conf.get("parent_page_id")
    inner = payload.get("inner_page_id") or conf.get("inner_page_id")
    if not parent:
        sys.exit("FATAL: no parent_page_id (payload or config).")

    print(f"refresh-dashboard {'(DRY-RUN, no writes) ' if dry else ''}"
          f"parent={parent} inner={inner}")
    for sec in payload.get("sections", []):
        try:
            _replace_section(token, parent, sec, dry)
        except Exception as e:
            print(f"  [section {sec.get('locate_prefix')!r}] ERROR: {e}")
    for spec in payload.get("databases", []):
        try:
            _upsert_db(token, spec, inner, dry)
        except Exception as e:
            print(f"  [db {spec.get('db_title')!r}] ERROR: {e}")
    print("refresh-dashboard done" + (" (dry-run, nothing written)" if dry else ""))
    # Best-effort: 4b already wrote the primary deliverable. Never hard-fail 4d.
    sys.exit(0)


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
    elif cmd == "refresh-dashboard":
        if not positional:
            sys.exit("usage: notion_rest.py refresh-dashboard payload.json [--dry-run] [--token T]")
        args["payload"] = positional[0]
        cmd_refresh_dashboard(args)
    elif cmd == "archive":
        if not positional:
            sys.exit("usage: notion_rest.py archive <page_id> [--token T]")
        args["page_id"] = positional[0]
        cmd_archive(args)
    else:
        sys.exit(f"unknown command {cmd!r}. Use create-work-items | refresh-dashboard | archive.")


if __name__ == "__main__":
    main()
