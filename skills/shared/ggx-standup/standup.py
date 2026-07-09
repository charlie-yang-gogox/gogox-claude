#!/usr/bin/env python3
"""Deterministic core for /ggx-standup.

The LLM-executed command shell (commands/dev/ggx-standup.md) owns ONLY the
fetches (gh + Linear MCP) and file writes. Every piece of logic that could
drift if an LLM did it by hand lives here:

  * window / timezone math (working-day rollback, offset-aware gh bounds),
  * PR title -> ticket-id extraction with a fallback chain,
  * repo allow-list filtering,
  * Done / Today set construction, aggregation by ticket, and dedup,
  * rendering both the human report and the paste-ready standup block.

Two subcommands:

  window   Compute the standup window. Reads no stdin. Prints a JSON object
           with ISO bounds (including offset-aware bounds to hand to
           `gh search`). Pure function of (--now, --tz, --date).

  render   Read a JSON bundle on stdin (window + raw gh/Linear payloads),
           print the report + paste block to stdout. Pure function of input.

No network, no clock reads inside `render` (the caller injects `now`), so the
whole thing is unit-testable against frozen fixtures (lib/ggx-standup.test.sh).
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Ticket-id shapes across the tracked teams. Whole-token, case-insensitive.
TICKET_RE = re.compile(r"\b(CAF|DAF|CET|DET|GGC)-(\d+)\b", re.IGNORECASE)

DEFAULT_TZ = "Asia/Hong_Kong"
# Default repo allow-list: the gogovan org + the tooling repo. Personal
# projects under the same GitHub account are excluded. Overridable via the
# bundle's allow_orgs / allow_repos (populated from --org / --repo).
DEFAULT_ALLOW_ORGS = ["gogovan"]
DEFAULT_ALLOW_REPOS = ["charlie-yang-gogox/gogox-claude"]


# --------------------------------------------------------------------------
# Window / timezone
# --------------------------------------------------------------------------
def compute_window(now_iso, tz_name=DEFAULT_TZ, date_override=None):
    """Return the standup window.

    Window = [previous-working-day 00:00 .. today 00:00) in tz.
    Weekends are skipped: on Monday the lower bound rolls back to Friday
    00:00 so the whole Fri/Sat/Sun gap is covered. `date_override`
    (YYYY-MM-DD) replaces "today".
    """
    tz = ZoneInfo(tz_name)
    now = datetime.fromisoformat(now_iso)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    now = now.astimezone(tz)

    if date_override:
        today0 = datetime.fromisoformat(date_override).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=tz
        )
    else:
        today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Walk back from yesterday to the most recent weekday (Mon=0 .. Sun=6).
    start_day = today0 - timedelta(days=1)
    while start_day.weekday() >= 5:  # Sat(5) / Sun(6)
        start_day -= timedelta(days=1)

    return {
        "tz": tz_name,
        "start": start_day.isoformat(),
        "end": today0.isoformat(),
        # Offset-aware bounds for GitHub search qualifiers
        # (created:START..END, merged:START..END). GitHub honors the
        # explicit +HH:MM offset, so no manual UTC conversion is needed.
        "gh_start": start_day.isoformat(),
        "gh_end": today0.isoformat(),
        "spans_weekend": (today0 - start_day).days > 1,
    }


def _parse_dt(value):
    """Parse an ISO timestamp (Zulu or offset) to an aware datetime, or None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _in_window(dt, start, end):
    return dt is not None and start <= dt < end


# --------------------------------------------------------------------------
# Extraction / filtering
# --------------------------------------------------------------------------
def extract_ticket_ids(pr):
    """Ticket ids for a PR via a fallback chain: title -> headRefName -> body.

    FIRST source that yields any id wins — we do NOT accumulate across sources.
    This is deliberate: a PR title (or branch) names the PR's OWN ticket(s),
    but a PR *body* routinely references unrelated tickets (a "related",
    "enables", "closes also", or changelog line). Scanning the whole body and
    unioning would mis-attribute one PR to 3-4 tickets — observed on live data
    (e.g. a GGC-tagged PR whose body mentioned several CAF tickets). So the body
    is a last resort, used only when neither title nor branch carries an id.
    Within the winning source, ALL matches are kept (a title legitimately
    closing two tickets, e.g. "CAF-100 / CAF-101", yields both), uppercased,
    de-duplicated, order-preserving.
    """
    for field in ("title", "headRefName", "body"):
        text = pr.get(field) or ""
        ids = []
        seen = set()
        for m in TICKET_RE.finditer(text):
            tid = f"{m.group(1).upper()}-{m.group(2)}"
            if tid not in seen:
                seen.add(tid)
                ids.append(tid)
        if ids:
            return ids
    return []


def repo_allowed(name_with_owner, allow_orgs, allow_repos):
    if not name_with_owner:
        return False
    owner = name_with_owner.split("/", 1)[0].lower()
    if owner in {o.lower() for o in allow_orgs}:
        return True
    return name_with_owner.lower() in {r.lower() for r in allow_repos}


# --------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------
def _pr_ref(pr):
    repo = (pr.get("repository") or {}).get("name") or ""
    return f"#{pr.get('number')}" + (f" ({repo})" if repo else "")


def build(bundle):
    """Turn a raw fetch bundle into structured Done / Today sections.

    Returns a dict with keys `done_tickets`, `done_other`, `today_tickets`,
    `notes`, `linear_ok`. Each ticket entry: {id, title, state, prs:
    [{number, repo, event, url, title}]} (today entries also carry follow_up).
    """
    tz_name = bundle.get("tz", DEFAULT_TZ)
    start = _parse_dt(bundle["window"]["start"])
    end = _parse_dt(bundle["window"]["end"])
    allow_orgs = bundle.get("allow_orgs") or DEFAULT_ALLOW_ORGS
    allow_repos = bundle.get("allow_repos") or DEFAULT_ALLOW_REPOS
    me = (bundle.get("me") or "").lower()
    linear_ok = bundle.get("linear_ok", True)
    ticket_states = bundle.get("ticket_states") or {}
    notes = []

    def allowed(pr):
        return repo_allowed(
            (pr.get("repository") or {}).get("nameWithOwner"), allow_orgs, allow_repos
        )

    # ---- DONE: PRs opened OR merged in window (allow-listed), by ticket ----
    # merged_prs / opened_prs are pre-filtered to the window by the gh query
    # (offset-aware qualifiers); we still tag the event and drop non-allowed
    # repos here so the script is the single source of the allow-list.
    done_tickets = {}       # id -> entry
    done_other = []         # PRs with no ticket id
    seen_done_pr = set()    # (repo, number) already placed

    def place_done(pr, event):
        if not allowed(pr):
            return
        repo = (pr.get("repository") or {}).get("nameWithOwner") or ""
        key = (repo, pr.get("number"))
        if key in seen_done_pr:
            return
        seen_done_pr.add(key)
        ids = extract_ticket_ids(pr)
        pr_row = {
            "number": pr.get("number"),
            "repo": (pr.get("repository") or {}).get("name") or "",
            "event": event,
            "url": pr.get("url"),
            "title": pr.get("title"),
        }
        if not ids:
            done_other.append(pr_row)
            return
        for tid in ids:
            entry = done_tickets.setdefault(
                tid,
                {"id": tid, "title": None, "state": ticket_states.get(tid), "prs": []},
            )
            entry["prs"].append(pr_row)

    # Merged first so a PR both merged and (re)opened in window reads as merged.
    for pr in bundle.get("merged_prs", []):
        place_done(pr, "merged")
    for pr in bundle.get("opened_prs", []):
        place_done(pr, "opened")

    done_ids = set(done_tickets)

    # ---- TODAY: open PR w/ my commit in window  UNION  started+updated ----
    today_tickets = {}

    def today_entry(tid):
        return today_tickets.setdefault(
            tid,
            {"id": tid, "title": None, "state": ticket_states.get(tid), "prs": [],
             "follow_up": tid in done_ids},
        )

    # signal 1: open PRs with a commit authored by me inside the window
    for pr in bundle.get("open_prs", []):
        if not allowed(pr):
            continue
        commits = pr.get("commits") or []
        mine_in_window = False
        for c in commits:
            authors = [a.get("login", "").lower() for a in (c.get("authors") or [])]
            cdt = _parse_dt(c.get("committedDate") or c.get("authoredDate"))
            if (not me or me in authors) and _in_window(cdt, start, end):
                mine_in_window = True
                break
        if not mine_in_window:
            continue
        ids = extract_ticket_ids(pr)
        pr_row = {
            "number": pr.get("number"),
            "repo": (pr.get("repository") or {}).get("name") or "",
            "event": "open",
            "url": pr.get("url"),
            "title": pr.get("title"),
        }
        if not ids:
            # An open PR with no ticket id still signals active work.
            e = today_tickets.setdefault(
                f"__pr_{pr_row['repo']}_{pr_row['number']}",
                {"id": None, "title": pr.get("title"), "state": None, "prs": [],
                 "follow_up": False},
            )
            e["prs"].append(pr_row)
            continue
        for tid in ids:
            today_entry(tid)["prs"].append(pr_row)

    # signal 2: Linear tickets assigned to me, current state started, updated in window
    if linear_ok:
        for t in bundle.get("linear_started", []):
            if (t.get("stateType") or "").lower() != "started":
                continue
            if not _in_window(_parse_dt(t.get("updatedAt")), start, end):
                continue
            tid = t.get("id")
            if not tid:
                continue
            e = today_entry(tid)
            e["title"] = e["title"] or t.get("title")
            e["state"] = e["state"] or t.get("state")
    else:
        notes.append(
            "Linear unauthenticated — Today's in-progress tickets and Done "
            "state chips are omitted (PR-only report)."
        )

    return {
        "done_tickets": done_tickets,
        "done_other": done_other,
        "today_tickets": today_tickets,
        "notes": notes,
        "linear_ok": linear_ok,
    }


def _fmt_pr(row):
    verb = {"merged": "merged", "opened": "opened", "open": "open"}.get(
        row["event"], row["event"]
    )
    return f"PR #{row['number']} {verb}"


def render_report(built, bundle):
    """Human-readable, by-ticket report (Traditional-Chinese-safe plain text)."""
    win = bundle["window"]
    lines = []
    lines.append(f"# Standup — window {win['start'][:10]} → {win['end'][:10]} ({bundle.get('tz', DEFAULT_TZ)})")
    if win.get("spans_weekend"):
        lines.append("_(Monday run — window spans the weekend)_")
    lines.append("")

    # Yesterday / Done
    lines.append("## Yesterday (Done)")
    done = built["done_tickets"]
    if not done and not built["done_other"]:
        lines.append("- (no PRs opened or merged in the window)")
    else:
        for tid in sorted(done):
            e = done[tid]
            state = f" — {e['state']}" if e["state"] else ""
            prs = ", ".join(_fmt_pr(p) for p in e["prs"])
            title = e["title"] or (e["prs"][0]["title"] if e["prs"] else "")
            lines.append(f"- **{tid}** {title}{state} · {prs}")
        if built["done_other"]:
            lines.append("- _Other (no ticket):_")
            for p in built["done_other"]:
                lines.append(f"    - {p['title']} · {_fmt_pr(p)} ({p['repo']})")
    lines.append("")

    # Today / In progress
    lines.append("## Today (In progress)")
    today = built["today_tickets"]
    real = {k: v for k, v in today.items() if v["id"]}
    prless = {k: v for k, v in today.items() if not v["id"]}
    if not today:
        lines.append("- (nothing in progress)")
    else:
        for tid in sorted(real):
            e = real[tid]
            state = f" — {e['state']}" if e["state"] else ""
            tag = " _(follow-up)_" if e["follow_up"] else ""
            prs = ", ".join(_fmt_pr(p) for p in e["prs"]) if e["prs"] else ""
            sep = " · " if prs else ""
            title = e["title"] or (e["prs"][0]["title"] if e["prs"] else "")
            lines.append(f"- **{tid}** {title}{state}{tag}{sep}{prs}")
        for e in prless.values():
            prs = ", ".join(_fmt_pr(p) for p in e["prs"])
            lines.append(f"- {e['title']} · {prs}")
    lines.append("")

    if built["notes"]:
        lines.append("## Notes")
        for n in built["notes"]:
            lines.append(f"- {n}")
        lines.append("")
    return "\n".join(lines)


def render_paste(built):
    """Paste-ready two-section block for the standup bot (English, plain)."""
    lines = []
    lines.append("① Yesterday")
    done = built["done_tickets"]
    if not done and not built["done_other"]:
        lines.append("- (nothing merged/opened)")
    else:
        for tid in sorted(done):
            e = done[tid]
            state = f" — {e['state']}" if e["state"] else ""
            title = e["title"] or (e["prs"][0]["title"] if e["prs"] else "")
            prs = ", ".join(f"#{p['number']} {p['event']}" for p in e["prs"])
            lines.append(f"- {tid} {title}{state} ({prs})")
        for p in built["done_other"]:
            lines.append(f"- {p['title']} (#{p['number']} {p['event']})")
    lines.append("")
    lines.append("② Today")
    today = built["today_tickets"]
    if not today:
        lines.append("- (nothing in progress)")
    else:
        for k in sorted(today, key=lambda x: (today[x]["id"] or "zzz", x)):
            e = today[k]
            title = e["title"] or (e["prs"][0]["title"] if e["prs"] else "")
            head = e["id"] or ""
            state = f" — {e['state']}" if e["state"] else ""
            tag = " (follow-up)" if e.get("follow_up") else ""
            prs = ", ".join(f"#{p['number']}" for p in e["prs"])
            sep = " " if head else ""
            prstr = f" ({prs})" if prs else ""
            lines.append(f"- {head}{sep}{title}{state}{tag}{prstr}".strip())
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(prog="standup.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("window", help="compute the standup window")
    w.add_argument("--now", required=True, help="ISO timestamp of 'now'")
    w.add_argument("--tz", default=DEFAULT_TZ)
    w.add_argument("--date", default=None, help="YYYY-MM-DD override for 'today'")

    r = sub.add_parser("render", help="render report + paste block from stdin bundle")
    r.add_argument("--paste-only", action="store_true")
    r.add_argument("--report-only", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "window":
        print(json.dumps(compute_window(args.now, args.tz, args.date), indent=2))
        return 0

    if args.cmd == "render":
        bundle = json.load(sys.stdin)
        built = build(bundle)
        report = render_report(built, bundle)
        paste = render_paste(built)
        if args.paste_only:
            print(paste)
        elif args.report_only:
            print(report)
        else:
            print(report)
            print("\n---\n\n## Paste-ready (standup bot)\n\n```\n" + paste + "\n```")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
