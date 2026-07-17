---
name: ticket-flow
description: |
  Pull a set of Linear tickets, reconstruct each one's status journey over time
  from its stateHistory, and render an HTML Artifact showing where the dev flow
  is stuck — a per-ticket swimlane timeline plus an aggregate "which stage eats
  the most time" bottleneck ranking (time-in-status p50/p90 + an aging-WIP
  snapshot of what's stuck now).
  Use when asked to "ticket flow", "status timeline", "cycle time",
  "where's the bottleneck", "開發流程瓶頸", "票卡在哪", or to visualize how long
  tickets spend in each Linear state.
  Linear only (Jira has no stateHistory via MCP).
allowed-tools:
  - Bash
  - Read
  - Glob
  - ToolSearch
  - Artifact
  - Write
---

## Ticket Flow — dev-flow bottleneck analyzer

Turn Linear `stateHistory` into a timeline + bottleneck report. Three layers
(mirrors `/daily-summary`): **gather** (Linear MCP) → **analyze** (`analyze.py`,
pure) → **render** (`render.py` → HTML → `Artifact`).

**Invocation:** `/ticket-flow [team] [--since 30d] [--project NAME] [--assignee me|<name>] [--ids A-1,A-2,...] [--include-canceled] [--cap N]`

### Step 0 — parse args + resolve team

```bash
SKILL_DIR="$HOME/.claude/skills/shared/ticket-flow"   # symlinked by install.sh
[ -f "$SKILL_DIR/analyze.py" ] || SKILL_DIR="$(git rev-parse --show-toplevel)/skills/shared/ticket-flow"
SCRATCH="${SCRATCH:-/tmp}"   # use the session scratchpad dir if provided
```

- **team**: the first bare arg; if absent, read `<cwd>/.gogox-claude.yaml` →
  `branch_prefix` (e.g. `CAF` for gogox-client-flutter, `GGC` for this repo). If
  neither resolves, ask the user for the team key.
- `--since` (default `30d`) → an ISO duration `-P<N>D` for the `createdAt` filter.
- `--project`, `--assignee`, `--ids` (comma list — when given, SKIP the list query
  and fetch those ids directly), `--include-canceled`, `--cap` (default `60`).

### Step 1 — gather the ticket set (Linear MCP)

Load the Linear tools via `ToolSearch` (`select:mcp__claude_ai_Linear__list_issues,mcp__claude_ai_Linear__get_issue`).

- **`--ids` given** → skip listing; the id list IS the set.
- **otherwise** → `list_issues(team=<team>, createdAt="-P<N>D", orderBy="createdAt",
  limit=250, includeArchived=true [, project] [, assignee])`. Page on
  `hasNextPage`/`cursor`, collecting `id`/`identifier`.

**Cost guard:** if the set size exceeds `--cap` (default 60), STOP and tell the
user — suggest a smaller `--since`, a `--project`, an `--assignee`, or running the
fan-out workflow (`workflows/ticket-flow-fanout.workflow.js`) which parallelizes
the per-ticket fetch. Do NOT silently fetch hundreds serially.

### Step 2 — fetch per-ticket stateHistory

`stateHistory` is returned ONLY by `get_issue` (not by `list_issues`). For each id,
call `get_issue(id)` and keep just the fields the analyzer needs (drop the big
description to save context). Build a JSON array and write it to the scratchpad:

```jsonc
[ { "identifier": "CAF-489", "title": "...", "assignee": "Jane"|null,
    "labels": ["Bug"], "createdAt": "<ISO>",
    "stateHistory": [ {"state":{"name":"...","type":"..."}|null,
                      "startedAt":"<ISO>","endedAt":"<ISO>|null"}, ... ] }, ... ]
```

Write it with the `Write` tool to `$SCRATCH/flow-data.json` (one clean array — do
not hand-edit timestamps; copy them verbatim from the MCP responses).

### Step 3 — analyze (pure, deterministic)

```bash
NOW=$(date -u +%FT%TZ)
python3 "$SKILL_DIR/analyze.py" "$SCRATCH/flow-data.json" --now "$NOW" \
  $( [ -n "$INCLUDE_CANCELED" ] && echo --include-canceled ) > "$SCRATCH/flow-out.json"
```

`--now` makes open-ticket dwell deterministic (an open final segment is measured to
`now`). Terminal states (Done/Canceled) accrue no dwell and are excluded from the
ranking; re-entries are summed; canceled tickets are skipped unless
`--include-canceled`.

### Step 4 — render the Artifact

1. **Load the `artifact-design` skill FIRST** (required before building any
   Artifact) to calibrate the design pass.
2. Generate the HTML deterministically:

   ```bash
   python3 "$SKILL_DIR/render.py" "$SCRATCH/flow-out.json" \
     --title "Ticket Flow — <TEAM>" \
     --scope "<TEAM> · last <N>d · <count> tickets" > "$SCRATCH/ticket-flow.html"
   ```

   `render.py` emits a self-contained body (inline CSS, hand-rolled SVG/flex bars,
   NO external libs — the Artifact CSP blocks external hosts). If the user wants a
   different emphasis, you MAY edit the produced HTML before publishing.
3. Call `Artifact` with `file_path: $SCRATCH/ticket-flow.html`, a stable
   `favicon` (e.g. `"📊"`) and a concise `<title>`.

### Step 5 — terminal summary

Print a short text recap so the answer stands alone without opening the Artifact:
the headline bottleneck (state + p50/p90 + share), the flow-efficiency %, and the
top 2-3 worst single-stage dwells (from `flow-out.json` → `bottleneck`,
`totals`, `worstOffenders`). Then add a one-line human reading of where to focus.

## What it measures (and the honest limits)

- ✅ Time between Linear **workflow states** (Backlog/Triage/To-do → In Progress →
  In Review → Ready for QA → Done), with re-entries summed and p50/p90 per state.
- ✅ Aging-WIP snapshot (open tickets ranked by current dwell) — "what's stuck now".
- ⚠️ **Not** label transitions (`ready-to-port`/`need-spec-review`/`ready-to-dev`)
  and **not** the exact assigned-to-person moment — the MCP exposes no timestamped
  history for those. The exact git-merge time is a GitHub event; the Linear proxy
  is the In Review → Ready for QA transition (optionally join `gh pr view <n>
  --json mergedAt` via the linked PR attachment).
- ⚠️ Calendar time (includes weekends). Jira is out of scope (no stateHistory MCP).

## Files
- `analyze.py` — pure analyzer (JSON in → JSON out). Tested by `lib/ticket-flow.test.sh`.
- `render.py` — flow-out.json → self-contained HTML.
- `workflows/ticket-flow-fanout.workflow.js` — optional parallel gather for large sets.
