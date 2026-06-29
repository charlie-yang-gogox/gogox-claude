#!/usr/bin/env python3
"""ticket-flow renderer — flow-out.json (from analyze.py) → self-contained HTML.

Emits a single HTML body (no <html>/<head>/<body> — the Artifact harness wraps it)
with: a headline bottleneck banner, an aggregate per-state bar chart (total time +
p50/p90), per-ticket swimlanes (normalized from each ticket's creation so stage
widths are directly comparable), an aging-WIP table, and a worst-offenders table.

Self-contained by construction: inline CSS, hand-rolled flexbox/SVG bars, NO
external libs or fonts (the Artifact CSP blocks every external host). stdlib only.

Usage: render.py <flow-out.json> [--title "..."] [--scope "..."] > page.html
"""

import json
import sys
import html

# Stable palette assigned per distinct state NAME (consistent across bars + swimlanes).
PALETTE = [
    "#3b82f6", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6",
    "#06b6d4", "#ec4899", "#84cc16", "#f97316", "#6366f1",
]
TYPE_FALLBACK = {
    "backlog": "#94a3b8", "triage": "#fbbf24", "unstarted": "#64748b",
    "started": "#3b82f6", "completed": "#10b981", "canceled": "#ef4444",
    "uncategorized": "#cbd5e1",
}


def fmt_dur(hours):
    if hours is None:
        return "—"
    if hours >= 48:
        return f"{hours / 24:.1f}d"
    if hours >= 1:
        return f"{hours:.1f}h"
    return f"{hours * 60:.0f}m"


def esc(s):
    return html.escape(str(s if s is not None else ""))


def build_color_map(report):
    names = []
    for row in report["aggregate"]["byState"]:
        if row["state"] not in names:
            names.append(row["state"])
    for t in report["perTicket"]:
        for seg in t["segments"]:
            if not seg["terminal"] and seg["state"] not in names:
                names.append(seg["state"])
    cmap = {}
    for i, n in enumerate(names):
        cmap[n] = PALETTE[i % len(PALETTE)]
    return cmap


def bar_chart(report, cmap):
    rows = report["aggregate"]["byState"]
    if not rows:
        return "<p class='muted'>No state data.</p>"
    mx = max(r["totalHours"] for r in rows) or 1
    out = ["<div class='bars'>"]
    for r in rows:
        w = 100.0 * r["totalHours"] / mx
        p90w = 100.0 * r["p90Hours"] / mx
        out.append(
            f"<div class='bar-row'>"
            f"<div class='bar-label'>{esc(r['state'])}</div>"
            f"<div class='bar-track'>"
            f"<div class='bar-fill' style='width:{w:.1f}%;background:{cmap.get(r['state'], '#3b82f6')}'></div>"
            f"<div class='bar-p90' style='left:{p90w:.1f}%' title='p90 {fmt_dur(r['p90Hours'])}'></div>"
            f"</div>"
            f"<div class='bar-val'>{fmt_dur(r['totalHours'])} "
            f"<span class='muted'>· p50 {fmt_dur(r['p50Hours'])} · {r['sharePct']}% · n={r['ticketCount']}</span></div>"
            f"</div>"
        )
    out.append("</div>")
    out.append("<p class='muted small'>Bar = total time-in-state across tickets; "
               "tick = p90. Hover a tick for the value.</p>")
    return "".join(out)


def swimlanes(report, cmap):
    tickets = sorted(report["perTicket"], key=lambda t: t["cycleHours"], reverse=True)
    if not tickets:
        return "<p class='muted'>No tickets.</p>"
    mx = max((t["cycleHours"] for t in tickets), default=1) or 1
    out = ["<div class='swim'>"]
    for t in tickets:
        segs = []
        for seg in t["segments"]:
            if seg["durationHours"] <= 0:
                continue
            left = 100.0 * seg["offsetHours"] / mx
            w = 100.0 * seg["durationHours"] / mx
            color = cmap.get(seg["state"], TYPE_FALLBACK.get(seg["type"], "#cbd5e1"))
            tip = f"{seg['state']}: {fmt_dur(seg['durationHours'])}"
            segs.append(
                f"<div class='seg' style='left:{left:.2f}%;width:{max(w, 0.4):.2f}%;"
                f"background:{color}' title='{esc(tip)}'></div>"
            )
        wip = " <span class='wip'>● open</span>" if t.get("currentType") not in ("completed", "canceled") else ""
        out.append(
            f"<div class='swim-row'>"
            f"<div class='swim-label' title='{esc(t['title'])}'>"
            f"<b>{esc(t['identifier'])}</b>{wip}<br><span class='muted small'>{esc(t['title'][:48])}</span></div>"
            f"<div class='swim-track'>{''.join(segs)}</div>"
            f"<div class='swim-total'>{fmt_dur(t['cycleHours'])}</div>"
            f"</div>"
        )
    out.append("</div>")
    return "".join(out)


def legend(cmap):
    items = "".join(
        f"<span class='leg'><span class='dot' style='background:{c}'></span>{esc(n)}</span>"
        for n, c in cmap.items()
    )
    return f"<div class='legend'>{items}</div>"


def table(rows, cols, empty="None."):
    if not rows:
        return f"<p class='muted'>{empty}</p>"
    head = "".join(f"<th>{esc(h)}</th>" for h, _ in cols)
    body = []
    for r in rows:
        body.append("<tr>" + "".join(f"<td>{esc(fn(r))}</td>" for _, fn in cols) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def render(report, title, scope):
    cmap = build_color_map(report)
    bn = report.get("bottleneck")
    banner = "<p class='muted'>No bottleneck (no flow data).</p>"
    if bn:
        banner = (
            f"<div class='headline'>"
            f"<span class='hl-label'>Bottleneck</span>"
            f"<span class='hl-state'>{esc(bn['state'])}</span>"
            f"<span class='hl-stat'>median {fmt_dur(bn['p50Hours'])} · p90 {fmt_dur(bn['p90Hours'])} · "
            f"{bn['sharePct']}% of all tracked time</span></div>"
        )
    tot = report["totals"]
    chips = (
        f"<div class='chips'>"
        f"<span class='chip'>{report['ticketCount']} tickets</span>"
        f"<span class='chip'>flow efficiency {tot['flowEfficiencyPct']}%</span>"
        f"<span class='chip'>active {fmt_dur(tot['activeHours'])}</span>"
        f"<span class='chip'>waiting {fmt_dur(tot['waitHours'])}</span>"
        + (f"<span class='chip muted'>{report['skippedCanceled']} canceled skipped</span>"
           if report.get('skippedCanceled') else "")
        + "</div>"
    )
    aging = table(
        report["agingWip"],
        [("Ticket", lambda r: r["identifier"]),
         ("State", lambda r: r["state"]),
         ("Age", lambda r: fmt_dur(r["ageHours"])),
         ("Assignee", lambda r: r.get("assignee") or "—"),
         ("Title", lambda r: r["title"][:60])],
        empty="No open tickets — nothing aging.",
    )
    worst = table(
        report["worstOffenders"],
        [("Ticket", lambda r: r["identifier"]),
         ("State", lambda r: r["state"]),
         ("Dwell", lambda r: fmt_dur(r["hours"]))],
    )
    return TEMPLATE.format(
        title=esc(title), scope=esc(scope), now=esc(report["now"]),
        banner=banner, chips=chips, legend=legend(cmap),
        bars=bar_chart(report, cmap), swim=swimlanes(report, cmap),
        aging=aging, worst=worst,
    )


TEMPLATE = """<style>
  :root {{ --bg:#0f172a; --card:#1e293b; --ink:#e2e8f0; --muted:#94a3b8; --line:#334155; }}
  * {{ box-sizing:border-box; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; color:#1e293b;
         margin:0; padding:24px; background:#f8fafc; line-height:1.45;
         font-variant-numeric:tabular-nums; }}
  h1 {{ font-size:22px; margin:0 0 4px; }} h2 {{ font-size:15px; margin:28px 0 10px; color:#334155; }}
  .muted {{ color:#64748b; }} .small {{ font-size:12px; }}
  .scope {{ color:#64748b; font-size:13px; margin-bottom:18px; }}
  .headline {{ display:flex; align-items:baseline; gap:12px; flex-wrap:wrap;
               background:linear-gradient(90deg,#fef3c7,#fff); border:1px solid #fcd34d;
               border-radius:10px; padding:14px 18px; margin-bottom:14px; }}
  .hl-label {{ font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:#b45309; font-weight:700; }}
  .hl-state {{ font-size:24px; font-weight:800; color:#78350f; }}
  .hl-stat {{ color:#92400e; font-size:13px; }}
  .chips {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:8px; }}
  .chip {{ background:#e2e8f0; border-radius:999px; padding:3px 11px; font-size:12px; color:#334155; }}
  .legend {{ display:flex; gap:14px; flex-wrap:wrap; margin:6px 0 14px; font-size:12px; color:#475569; }}
  .leg {{ display:inline-flex; align-items:center; gap:5px; }}
  .dot {{ width:11px; height:11px; border-radius:3px; display:inline-block; }}
  .bars {{ display:flex; flex-direction:column; gap:7px; }}
  .bar-row {{ display:grid; grid-template-columns:130px 1fr 230px; align-items:center; gap:10px; }}
  .bar-label {{ font-size:13px; font-weight:600; text-align:right; }}
  .bar-track {{ position:relative; height:20px; background:#e2e8f0; border-radius:5px; }}
  .bar-fill {{ height:100%; border-radius:5px; }}
  .bar-p90 {{ position:absolute; top:-3px; width:2px; height:26px; background:#0f172a; opacity:.55; }}
  .bar-val {{ font-size:12px; color:#334155; }}
  .swim {{ display:flex; flex-direction:column; gap:6px; overflow-x:auto; }}
  .swim-row {{ display:grid; grid-template-columns:210px 1fr 64px; align-items:center; gap:10px; min-width:680px; }}
  .swim-label {{ font-size:12px; }} .swim-label b {{ font-size:13px; }}
  .swim-track {{ position:relative; height:22px; background:#f1f5f9; border-radius:5px; }}
  .seg {{ position:absolute; top:0; height:100%; border-radius:3px; opacity:.9; }}
  .swim-total {{ font-size:12px; font-weight:600; text-align:right; color:#334155; }}
  .wip {{ color:#2563eb; font-size:11px; }}
  table {{ border-collapse:collapse; width:100%; font-size:13px; }}
  th,td {{ text-align:left; padding:6px 10px; border-bottom:1px solid #e2e8f0; }}
  th {{ color:#64748b; font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.03em; }}
  .wrap {{ overflow-x:auto; }}
</style>
<h1>{title}</h1>
<div class="scope">{scope} · generated {now}</div>
{banner}
{chips}
<h2>Where the time goes — time-in-status</h2>
{legend}
{bars}
<h2>Per-ticket timeline (aligned at each ticket's creation)</h2>
<div class="wrap">{swim}</div>
<h2>Aging WIP — open tickets, longest first</h2>
<div class="wrap">{aging}</div>
<h2>Worst single-stage dwells</h2>
<div class="wrap">{worst}</div>
"""


def main(argv):
    args = argv[1:]
    if not args:
        print("usage: render.py <flow-out.json> [--title ...] [--scope ...]", file=sys.stderr)
        return 2
    path = None
    title = "Ticket Flow — Dev Bottleneck Report"
    scope = ""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--title":
            i += 1; title = args[i]
        elif a == "--scope":
            i += 1; scope = args[i]
        elif not a.startswith("--") and path is None:
            path = a
        i += 1
    if path is None:
        print("FAIL: no input file", file=sys.stderr)
        return 2
    with open(path) as fh:
        report = json.load(fh)
    sys.stdout.write(render(report, title, scope))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
