#!/usr/bin/env python3
"""ticket-flow analyzer — turn Linear `stateHistory` into a dev-flow bottleneck report.

Pure, deterministic, stdlib-only. The SKILL gathers tickets via the Linear MCP and
writes them to a JSON file; this script crunches durations and emits a machine JSON
report that the SKILL renders as an HTML Artifact. No network, no formatting here
(mirrors the parse.py-emits-JSON pattern in skills/shared/daily-summary/).

Usage:
    analyze.py <input.json> [--now <ISO8601>] [--top N] [--include-canceled]

Input: JSON array of tickets, each:
    { "identifier": "CAF-489", "title": "...", "assignee": "Jane"|null,
      "labels": [...], "createdAt": "<ISO>",
      "stateHistory": [ { "state": {"name": "...", "type": "..."}|null,
                         "startedAt": "<ISO>", "endedAt": "<ISO>|null" }, ... ] }

`--now` is REQUIRED for determinism whenever any ticket is still open (an open
final segment's dwell is measured to `now`); the SKILL always passes it. Defaults
to the newest timestamp seen in the data if omitted (keeps the script runnable
stand-alone for tests/inspection).

Bottleneck semantics:
  * Terminal states (type `completed`/`canceled`) never accrue "waiting" time — a
    ticket resting in Done is not a bottleneck — so they are excluded from the
    aggregate ranking and from aging-WIP.
  * Re-entries are summed: a ticket that bounces In Review→In Progress→In Review
    has its In Review dwell totalled.
  * The bottleneck ranking is per-ticket-per-state dwell, aggregated across tickets
    (count of tickets touching the state, total / mean / p50 / p90), ranked by
    total hours. The headline bottleneck is the non-terminal state with the largest
    total share of cycle time.
"""

import json
import sys
from datetime import datetime, timezone

TERMINAL_TYPES = {"completed", "canceled", "cancelled"}
# Linear state.type values: backlog | triage | unstarted | started | completed | canceled.
# (triage is its own type on some teams; treat anything non-terminal as flow time.)


def parse_iso(s):
    """Parse a Linear ISO-8601 timestamp (handles the trailing Z) → aware UTC datetime."""
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def percentile(values, p):
    """Nearest-rank-ish percentile with linear interpolation; safe for tiny N."""
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    frac = k - lo
    return xs[lo] + (xs[hi] - xs[lo]) * frac


def seg_state(seg):
    st = seg.get("state")
    if not st:
        return ("(uncategorized)", "uncategorized")
    return (st.get("name") or "(uncategorized)", st.get("type") or "uncategorized")


def analyze(tickets, now, top_n=10, include_canceled=False):
    now_dt = parse_iso(now)
    per_ticket = []
    # per-ticket-per-state dwell hours, accumulated for the aggregate.
    state_to_durations = {}   # state name -> list[ hours ] (one entry per ticket)
    state_type = {}           # state name -> type (last seen)
    type_to_durations = {}    # type bucket -> list[ hours ] (one entry per ticket)
    aging_wip = []
    worst = []                # (hours, identifier, state)
    grand_total = 0.0
    active_total = 0.0
    wait_total = 0.0
    skipped_canceled = 0

    for t in tickets:
        ident = t.get("identifier") or t.get("id") or "?"
        history = t.get("stateHistory") or []
        # Determine the ticket's current (final) state.
        final = history[-1] if history else None
        final_name, final_type = seg_state(final) if final else (None, None)
        is_canceled = final_type in {"canceled", "cancelled"}
        if is_canceled and not include_canceled:
            skipped_canceled += 1
            continue

        segments = []
        per_state_this = {}    # state name -> hours (summed re-entries) for THIS ticket
        first_start = None
        last_end = None
        for i, seg in enumerate(history):
            name, typ = seg_state(seg)
            start = parse_iso(seg.get("startedAt"))
            if start is None:
                continue
            end = parse_iso(seg.get("endedAt"))
            is_open = end is None
            terminal = typ in TERMINAL_TYPES
            if is_open:
                # Open final segment: measure to `now` only if non-terminal flow
                # time. A terminal resting state (Done/Canceled) accrues no dwell.
                end = now_dt if not terminal else start
            dur_h = max(0.0, (end - start).total_seconds() / 3600.0)
            if first_start is None:
                first_start = start
            last_end = end
            segments.append({
                "state": name, "type": typ,
                "startISO": seg.get("startedAt"),
                "offsetHours": round((start - first_start).total_seconds() / 3600.0, 3),
                "durationHours": round(dur_h, 3),
                "open": is_open,
                "terminal": terminal,
            })
            if not terminal:
                per_state_this[name] = per_state_this.get(name, 0.0) + dur_h
                state_type[name] = typ
                # bucket active vs wait
                if typ == "started":
                    active_total += dur_h
                else:
                    wait_total += dur_h
            # aging WIP: open, non-terminal, final segment.
            if is_open and not terminal and i == len(history) - 1:
                aging_wip.append({
                    "identifier": ident,
                    "title": t.get("title", ""),
                    "state": name, "type": typ,
                    "ageHours": round(dur_h, 3),
                    "assignee": t.get("assignee"),
                })

        cycle_h = 0.0
        if first_start is not None and last_end is not None:
            cycle_h = max(0.0, (last_end - first_start).total_seconds() / 3600.0)
        grand_total += sum(per_state_this.values())

        for name, hrs in per_state_this.items():
            state_to_durations.setdefault(name, []).append(hrs)
            type_to_durations.setdefault(state_type[name], []).append(hrs)
            worst.append((hrs, ident, name))

        per_ticket.append({
            "identifier": ident,
            "title": t.get("title", ""),
            "assignee": t.get("assignee"),
            "labels": t.get("labels", []),
            "createdAt": t.get("createdAt"),
            "currentState": final_name,
            "currentType": final_type,
            "cycleHours": round(cycle_h, 3),
            "segments": segments,
            "byState": {k: round(v, 3) for k, v in per_state_this.items()},
        })

    def agg(table):
        rows = []
        for name, durs in table.items():
            rows.append({
                "state": name,
                "type": state_type.get(name, name),
                "ticketCount": len(durs),
                "totalHours": round(sum(durs), 3),
                "meanHours": round(sum(durs) / len(durs), 3),
                "p50Hours": round(percentile(durs, 50), 3),
                "p90Hours": round(percentile(durs, 90), 3),
                "sharePct": round(100.0 * sum(durs) / grand_total, 1) if grand_total else 0.0,
            })
        rows.sort(key=lambda r: r["totalHours"], reverse=True)
        return rows

    by_state = agg(state_to_durations)
    by_type = agg(type_to_durations)
    aging_wip.sort(key=lambda r: r["ageHours"], reverse=True)
    worst.sort(key=lambda x: x[0], reverse=True)
    worst_rows = [
        {"identifier": ident, "state": name, "hours": round(h, 3)}
        for (h, ident, name) in worst[:top_n]
    ]

    return {
        "now": now,
        "ticketCount": len(per_ticket),
        "skippedCanceled": skipped_canceled,
        "totals": {
            "grandTotalHours": round(grand_total, 3),
            "activeHours": round(active_total, 3),
            "waitHours": round(wait_total, 3),
            "flowEfficiencyPct": round(100.0 * active_total / (active_total + wait_total), 1)
            if (active_total + wait_total) > 0 else 0.0,
        },
        "bottleneck": by_state[0] if by_state else None,
        "aggregate": {"byState": by_state, "byType": by_type},
        "agingWip": aging_wip,
        "worstOffenders": worst_rows,
        "perTicket": per_ticket,
    }


def main(argv):
    args = argv[1:]
    if not args:
        print("usage: analyze.py <input.json> [--now ISO] [--top N] [--include-canceled]",
              file=sys.stderr)
        return 2
    path = None
    now = None
    top_n = 10
    include_canceled = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--now":
            i += 1
            now = args[i]
        elif a == "--top":
            i += 1
            top_n = int(args[i])
        elif a == "--include-canceled":
            include_canceled = True
        elif not a.startswith("--") and path is None:
            path = a
        i += 1
    if path is None:
        print("FAIL: no input file given", file=sys.stderr)
        return 2

    with open(path) as fh:
        tickets = json.load(fh)

    if now is None:
        # Fallback for stand-alone use: newest timestamp in the data.
        stamps = []
        for t in tickets:
            for seg in t.get("stateHistory") or []:
                for k in ("startedAt", "endedAt"):
                    v = seg.get(k)
                    if v:
                        stamps.append(v)
        now = max(stamps) if stamps else datetime.now(timezone.utc).isoformat()

    report = analyze(tickets, now, top_n=top_n, include_canceled=include_canceled)
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
