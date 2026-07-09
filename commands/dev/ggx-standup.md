---
name: ggx-standup
argument-hint: "[--date=YYYY-MM-DD] [--org O] [--repo R] [--tz TZ]"
description: >
  Manual, read-only daily standup built from GitHub PRs + Linear as the single
  source of truth. Answers "what did I finish yesterday / what am I on today",
  grouped BY TICKET, and emits a paste-ready two-section block aligned to an
  async standup bot's default Scrum questions (Yesterday / Today) for the user
  to paste in. "Yesterday (Done)" is anchored on GitHub PR EVENTS — PRs the
  user authored that were opened OR merged in the window — because dev work
  legitimately stops at In Review / Ready for QA, so PR events (not a Linear
  terminal state) are the reliable "done" signal; each ticket shows its current
  Linear state as a chip. "Today (In progress)" is the union of open PRs with a
  fresh commit by the user and Linear tickets assigned to the user currently in
  a started-type state. All deterministic logic (window/timezone math, PR-title
  ticket-id extraction, repo allow-list, set union/dedup, rendering) lives in a
  tested Python core (`skills/shared/ggx-standup/standup.py`); this command only
  fetches and writes. Window = [previous-working-day 00:00 .. today 00:00) in
  Asia/Hong_Kong (Monday spans the weekend). Repo scope defaults to work repos
  only (the gogovan org + the gogox-claude repo); personal projects are filtered
  out. Does NOT auto-submit to any standup bot, does NOT post via any Slack bot,
  and never writes to Linear or Git.
Prerequisite: >
  - `gh` CLI authenticated (the identity whose PRs to report — resolved via
    `gh api user`). Required.
  - Linear MCP authenticated — OPTIONAL. If unavailable the report degrades to
    PR-only (state chips and the Linear in-progress signal are dropped with one
    WARN line), it does not abort.
  - Run from a gogox-claude checkout so `skills/shared/ggx-standup/standup.py`
    resolves (either the main checkout or any worktree).
---

<!-- RULE: command content is English. No literal ticket ids in this body
     (prompt-lint hard rule); use <TEAM>-<n> placeholders. -->

# `/ggx-standup [--date=YYYY-MM-DD] [--org O] [--repo R] [--tz TZ]`

Build a manager-facing standup from the systems that already hold the truth.
**Read-only**: no Linear writes, no Git mutations, no auto-submit. The output
is a chat report + a paste-ready block + a markdown file; the user pastes the
block into their standup bot themselves.

**Usage**

- `/ggx-standup` — standup for the last working day.
- `/ggx-standup --date=YYYY-MM-DD` — treat that date as "today" (re-run for a
  past day). The window becomes the working day before it.
- `/ggx-standup --org O` / `--repo OWNER/REPO` — override the default work-repo
  allow-list (repeatable in spirit; comma-separate).
- `/ggx-standup --tz TZ` — override the timezone (default `Asia/Hong_Kong`).

---

## Step 1: Log usage (always first)

```bash
echo "{\"skill\":\"ggx-standup\",\"user\":\"$(whoami)\",\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" >> ~/.gogox-claude-usage.jsonl 2>/dev/null || true
```

## Step 2: Resolve identity, script path, and the window

```bash
STANDUP_PY="$(git rev-parse --show-toplevel)/skills/shared/ggx-standup/standup.py"
[ -f "$STANDUP_PY" ] || { echo "FAIL: standup.py not found at $STANDUP_PY — run from a gogox-claude checkout." >&2; exit 1; }

ME=$(gh api user --jq '.login' 2>/dev/null) || { echo "FAIL: gh not authenticated (gh api user failed)." >&2; exit 1; }
TZ_ARG="Asia/Hong_Kong"   # override with --tz
# DATE_ARG from --date=... (may be empty)

WIN=$(python3 "$STANDUP_PY" window --now "$(date -Iseconds)" --tz "$TZ_ARG" ${DATE_ARG:+--date "$DATE_ARG"})
GH_START=$(printf '%s' "$WIN" | python3 -c 'import json,sys;print(json.load(sys.stdin)["gh_start"])')
GH_END=$(printf '%s'   "$WIN" | python3 -c 'import json,sys;print(json.load(sys.stdin)["gh_end"])')
```

`GH_START` / `GH_END` are offset-aware ISO timestamps (e.g. `...+08:00`);
GitHub search honors the offset, giving precise day-boundary handling (a PR
merged 23:30 HK on the start day is classified correctly).

## Step 3: Fetch GitHub PR events

Run three `gh search prs` queries. The repo allow-list is applied inside
`standup.py` (default: the gogovan org + the gogox-claude repo), so DO NOT
`--owner`/`--repo`-filter here unless the user passed `--org` / `--repo`
(then forward them as `allow_orgs` / `allow_repos` in the bundle).

```bash
# Opened in window (author = me)
OPENED=$(gh search prs --author "$ME" "created:${GH_START}..${GH_END}" \
  --json number,title,url,repository,body,state --limit 100)

# Merged in window (author = me). The offset-aware `merged:` qualifier does the
# precise time filter; no mergedAt field is needed from search.
MERGED=$(gh search prs --author "$ME" "merged:${GH_START}..${GH_END}" \
  --json number,title,url,repository,body,state --limit 100)

# Currently open (author = me) — Today signal source.
OPEN=$(gh search prs --author "$ME" --state open \
  --json number,title,url,repository,body --limit 100)
```

**Per-open-PR commits (Today signal).** For each OPEN PR that passes the
allow-list, fetch its commits so `standup.py` can keep only PRs with a commit
authored by `$ME` inside the window:

```bash
# For each allow-listed open PR <n> in repo <owner/repo>:
gh pr view <n> -R <owner/repo> --json commits \
  --jq '{number: <n>, commits: [.commits[] | {committedDate, authoredDate, authors: [.authors[] | {login: .login}]}]}'
```

Attach the resulting `commits` array onto the matching entry in `OPEN`.

**Branch-name fallback (small N).** The extractor is **first-source-wins**
(title → headRefName → body): the first source carrying an id wins, and the
body is used ONLY when title and branch have none — because a PR body routinely
references unrelated tickets (a "Related" / "Enables" / changelog line), which
would mis-attribute the PR. So for any MERGED/OPENED PR whose `title` yields NO
id (the `(CAF|DAF|CET|DET|GGC)-\d+` shapes), fetch its head branch so the
branch — preferred over the noisier body — can supply the id:

```bash
gh pr view <n> -R <owner/repo> --json headRefName --jq '.headRefName'
# → set entry.headRefName on that PR
```

Skip this fallback when the title already carries an id (the common case).

## Step 4: Fetch Linear (fail-soft, capped)

Set `LINEAR_OK=true`. If any Linear call fails (MCP unauthenticated / network),
set `LINEAR_OK=false`, skip the rest of this step, and continue — the report
degrades to PR-only.

1. **In-progress tickets** — `mcp__claude_ai_Linear__list_issues` with
   `assignee = "me"` and `updatedAt` ≥ the window start (pass the ISO start as
   the `updatedAt` filter to pre-narrow server-side). For each returned issue
   capture (NOTE the `list_issues` field shape — the human identifier is `.id`,
   and the current state is the flat `.status` / `.statusType`, NOT `.state.*`):
   `id` (`.id`, e.g. `<TEAM>-<n>`), `title`, `state` (`.status`),
   `stateType` (`.statusType`), `updatedAt`. **Cap: 50 issues.** If the result
   is capped, STOP-and-narrow: tell the user the assignee set exceeds the cap
   and to pass `--date`/narrower scope; never silently truncate.

2. **Current state chips** — collect the distinct ticket ids extracted from the
   MERGED + OPENED PRs (same `(CAF|DAF|CET|DET|GGC)-\d+` shapes). For each id
   (**cap: 40** `get_issue` calls; beyond that, list the joined-but-unfetched
   ids explicitly rather than dropping them), call
   `mcp__claude_ai_Linear__get_issue` and record `id → .status` (the flat
   current-state name; `get_issue` uses `.status` / `.statusType` too, and
   `.state.*` only inside `stateHistory[]`) into a `ticket_states` map. These
   are the chips shown next to each Done ticket.

## Step 5: Assemble the bundle and render

Assemble one JSON bundle and pipe it to `standup.py render`:

```
{
  "tz": "<TZ_ARG>",
  "me": "<ME>",
  "linear_ok": <LINEAR_OK>,
  "window": <the WIN object from Step 2>,
  "allow_orgs": [...],      // only if --org given; else omit (script default)
  "allow_repos": [...],     // only if --repo given; else omit
  "ticket_states": { "<TEAM>-<n>": "<state name>", ... },
  "merged_prs":  <MERGED>,
  "opened_prs":  <OPENED>,
  "open_prs":    <OPEN with commits[] attached>,
  "linear_started": [ {id,title,state,stateType,updatedAt}, ... ]
}
```

```bash
printf '%s' "$BUNDLE_JSON" | python3 "$STANDUP_PY" render | tee /dev/stderr > "$OUT_MD"
```

Then write the same content to a markdown file for easy copying, e.g.
`"$(git rev-parse --show-toplevel)/claude-reports/standup-$(date +%Y%m%d).md"`
(or the session scratchpad). Print the file path.

## Step 6: Hand-off note

Show the user the paste-ready `① Yesterday / ② Today` block and remind them:

> Paste `① Yesterday` and `② Today` into your standup bot when it prompts.
> This command does not submit for you (the bot collects answers via a DM to
> you; auto-submit is out of scope by design).

## Output

- A by-ticket chat report: `<TEAM>-<n> <title> — <current-state> · PR #<n> merged/opened`.
- A paste-ready two-section block (`① Yesterday` / `② Today`).
- A markdown file with both, path printed.

## Guardrails

- **Read-only.** No Linear writes, no Git mutations, no auto-submit, no Slack
  posting. The only writes are the usage log and the local markdown file.
- **All logic in the tested core.** Window/timezone, ticket-id extraction,
  allow-list, union/dedup, and rendering live in `standup.py`
  (`lib/ggx-standup.test.sh`). This command must not re-implement any of it by
  hand — fetch, pipe, print.
- **Work repos only by default.** Personal-account repos are filtered by the
  allow-list in the core; never widen it without an explicit `--org`/`--repo`.
- **Fail-soft on Linear.** A Linear outage degrades to a PR-only report with a
  visible note, never an abort.
- **No silent truncation.** Both Linear fetch caps STOP-and-narrow (or list the
  skipped ids) rather than dropping data invisibly.

## How this was used last

- 2026-07-09 by @charlie — initial ship (GGC self-dev pipeline).
