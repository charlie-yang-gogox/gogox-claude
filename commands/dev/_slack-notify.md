---
name: _slack-notify
description: "Internal helper invoked by /ticket-analyze (Step 10) and /ggx-dispatcher (§4.2, §6.5). Posts run-level Slack digests / batch-abort alerts to the user's personal pipeline channel via their own Slack bot (chat.postMessage). Opt-in per user: config lives in-repo at commands/dev/profiles/ggx-slack.json (gitignored, org.yaml pattern, symlinked by install.sh); if absent or disabled, every call is a completely silent no-op — users who never configured a Slack bot see zero behavior change. Fail-soft single-WARN on send errors; NEVER blocks or fails the calling pipeline. Callers pass raw signals; the status→emoji/token mapping lives ONLY in this file. Not user-invoked."
---

# `/_slack-notify <shape> ...`

Single source of truth for pipeline → Slack notifications. Callers pass
**raw signals** (verdicts, outcomes, flags) — they never pick a status
token or emoji themselves. The mapping table below is the one chokepoint;
when a pipeline changes its outcome vocabulary, this file is the only
place the Slack rendering needs to follow.

**Underscore prefix** marks this skill as internal — callers are other
skills (see Callers), never the user directly.

**Opt-in contract (foolproofing)**: this repo is installed by multiple
users via `install.sh`. Slack notification is strictly per-user opt-in:
the config file lives in-repo at `commands/dev/profiles/ggx-slack.json`
(same pattern as `org.yaml` — symlinked by `install.sh` into
`~/.claude/commands/profiles/`) but is **gitignored** because it holds a
bot token. A fresh clone therefore has no config → every call is a
silent no-op with a one-line audit. A user who never heard of this
feature must see zero behavior change in every pipeline.

## Shapes

- `/_slack-notify digest <source>` — one run-level digest message.
  `<source>` ∈ `ticket-analyzer` | `ggx-dispatcher`. The caller passes a
  header-stats object and one raw-signal line per ticket (see Inputs).
- `/_slack-notify batch-abort detail=<text>` — dispatcher §4.2 only.
  Batch-level alert; there is no single ticket id.

## Inputs

### `digest ticket-analyzer`

Header stats: `team`, `analyzed`, `ready`, `need_revision`, `blocked`,
`errored`, optional `best_start`. Per-ticket lines (raw signals, one per
analyzed ticket; `title` is the ticket title, required — the renderer
truncates it to 60 chars):

```
<ticket-id> <url> <lane> ready title="<title>"
<ticket-id> <url> <lane> need-revision reasons=<comma-list> title="<title>"
<ticket-id> <url> <lane> need-dependency blockers=<comma-list> title="<title>"
<ticket-id> <url> <lane> cycle ids=<id1↔id2> title="<title>"
<ticket-id> <url> <lane> errored detail=<what failed> title="<title>"
```

### `digest ggx-dispatcher`

Header stats: `team`, `processed`, `done`, `port_paused`, `failed`,
`skipped`. Per-ticket lines — built from the **§6.2-derived authoritative
outcome + Flags + pr** carried in memory for the §6.4 table (NEVER from
the cosmetic `[ggx-work-result]` lines); `title` comes from the same
§6.2 `get_issue` snapshot:

```
<ticket-id> <url> <lane> done flags=<In-Review|-> pr=<pr-url|-> title="<title>"
<ticket-id> <url> <lane> port-paused flags=<need-spec-review|-> title="<title>"
<ticket-id> <url> <lane> failed flags=<in-flight-residue|-> stage=<stage_reached> reason=<short> title="<title>"
```

### `batch-abort`

`detail=<failed-ticket id, which MCP call failed, list of tickets needing
manual unlock>`.

## Step 0: Config + guard gates (in order, all exit 0)

Config file: read via the fixed deployed path
`$HOME/.claude/commands/profiles/ggx-slack.json` — a symlink created by
`install.sh` pointing at `commands/dev/profiles/ggx-slack.json` in the
repo (gitignored, chmod 600; no committed template — opt in by creating
the file by hand from the schema below, then re-run `install.sh` to get
the symlink). Read the deployed path, never a repo-relative path:
pipelines run with cwd in target repos and worktrees, where the
gogox-claude checkout location is unknowable. Schema:

```json
{
  "version": 1,
  "enabled": true,
  "channel_id": "C0XXXXXXXXX",
  "bot_token": "xoxb-...",
  "liveness_message_ts": ""
}
```

```bash
CONFIG="$HOME/.claude/commands/profiles/ggx-slack.json"

# G1 — no config file → silent no-op. This is the default experience for
#      every user who has not opted in. Audit line only, NO warning.
if [ ! -f "$CONFIG" ]; then
  echo "slack-notify: disabled (no config)"
  exit 0
fi

# G2a — file exists but is not valid JSON → the user DID opt in (a config
#       exists), so a corrupted file deserves one WARN, not silence.
if ! jq -e . "$CONFIG" >/dev/null 2>&1; then
  echo "WARN: /_slack-notify: $CONFIG is not valid JSON — skipping notification." >&2
  exit 0
fi

# G2 — disabled by config → same silent no-op as G1.
ENABLED=$(jq -r '.enabled // false' "$CONFIG")
if [ "$ENABLED" != "true" ]; then
  echo "slack-notify: disabled (enabled != true)"
  exit 0
fi

# G3 — opted in but misconfigured → the user DOES want notifications, so
#      surface one WARN; still a no-op, still exit 0.
CHANNEL=$(jq -r '.channel_id // ""' "$CONFIG")
TOKEN=$(jq -r '.bot_token // ""' "$CONFIG")
if [ -z "$CHANNEL" ] || [ -z "$TOKEN" ]; then
  echo "WARN: /_slack-notify: config invalid (missing channel_id or bot_token in $CONFIG) — skipping notification." >&2
  exit 0
fi
```

G4 (send failure) is handled in Step 2. No gate ever changes the calling
pipeline's exit code.

## Step 1: Build the message

### Mapping table (the ONE chokepoint — callers never pick these)

| Raw signal (from caller) | Token | Emoji | `#needs-human` | `(next: ...)` template |
|---|---|---|---|---|
| analyzer `ready` | `READY` | ✅ | no | — (counted in header, no per-ticket line) |
| analyzer `need-revision` | `NEEDS-REVISION` | 🟠 | yes | `edit the ticket; next sweep re-scans automatically` |
| analyzer `need-dependency` | `BLOCKED` | 🟣 | yes | `close blocker <ids>; next sweep unblocks automatically` |
| analyzer `cycle` | `CYCLE` | 🔁 | yes | `break the dependency cycle manually` |
| analyzer `errored` | `FAILED` | 🔴 | yes | `re-run /ticket-analyze <id>` |
| dispatcher `done` | `REVIEW` | 🟢 | yes | `review PR <number>` |
| dispatcher `port-paused` | `SPEC-REVIEW` | 🟡 | yes | `run /spec-review <id>` |
| dispatcher `failed` | `FAILED` | 🔴 | yes | `see claude-reports/<id>/, re-run /ggx-work <id>` |
| `batch-abort` | `BATCH-ABORT` | ⛔ | yes | `manually unlock <ids>` |

Notes:

- `REVIEW` carries `#needs-human` deliberately — "next action = a human
  reviews the PR" (soft). Decided 2026-06-05: digest line only, never an
  individual broadcast.
- Reasons mentioning a missing lane classification (`UNKNOWN_LANE`,
  `missing classification`) stay `FAILED` in v1 — the reason text already
  says what to do. The full design taxonomy (incl. `CLASSIFY` ❓) lives in
  `plans/slack-notifier-design.md` §2 for future expansion.

### Rendering — Block Kit (format v2, decided 2026-06-05)

One `chat.postMessage` with a `blocks` array plus a one-line `text`
fallback (drives mobile notification previews and adds search
redundancy). Markdown tables do NOT render in Slack — always
line-based mrkdwn inside blocks.

**Digest blocks** (in order):

1. `header` block (plain_text, ≤150 chars — hard Slack limit):
   `📊 <source> · <team> team`
2. `section` (mrkdwn) — counts line:
   - ticket-analyzer: `*<X> analyzed* — <a> ready · <b> need-revision · <c> blocked · <z> errored`
   - ggx-dispatcher: `*<N> processed* — <d> done · <p> port-paused · <f> failed · skipped <s>`
3. `divider`
4. `section` (mrkdwn) — `*Needs your action (<n>)*` followed by
   **two-line items**, one blank line between items, ordered FAILED
   first, then SPEC-REVIEW / NEEDS-REVISION / BLOCKED / CYCLE, then
   REVIEW:

   ```
   <emoji> *<<url>|<ticket-id>>* <title, truncated to 60 chars with …>
           ↳ <status-word>: <summary> — _<next action>_
   ```

   `<<url>|<ticket-id>>` is Slack link syntax. `<summary>` is the raw
   signal's detail (reasons / blockers / stage+reason / PR state), one
   line. Omit this whole block when nothing needs action.
5. Optional `section` (mrkdwn) — info footer:
   - ticket-analyzer: `ready: <id, id, …>` (ids only — READY gets no
     item) and `Best start: <id>` when present.
   - ggx-dispatcher: `Report: claude-reports/dispatcher/<RUN_TS>-<PID>.md`.
6. `context` block — hashtags, ONCE per message (Slack search matches
   at message granularity, so per-line tags add nothing but noise):
   `#ggx-digest <#ggx-<token> for each token present> [#needs-human if any item needs action]`

**Fallback `text`** (one line): `📊 [DIGEST] <source> · <team> — <compact counts> [#needs-human]`.

**`batch-abort` blocks**: single `section` —
`⛔ *BATCH-ABORT* · ggx-dispatcher · <team> team\n        ↳ <detail> — _manually unlock <ids>_`
plus a `context` block `#ggx-batch-abort #needs-human`. Fallback text:
`⛔ [BATCH-ABORT] ggx-dispatcher · <team> — <detail> #needs-human`.

## Step 2: Send (curl, fail-soft)

The transport is the user's own bot via `chat.postMessage` — messages
post under the bot identity. One message per invocation.

```bash
# $BLOCKS is the Block Kit array built per "Rendering" above (jq -n '[...]');
# $FALLBACK is the one-line text fallback.
PAYLOAD=$(jq -n --arg ch "$CHANNEL" --arg text "$FALLBACK" --argjson blocks "$BLOCKS" \
  '{channel: $ch, text: $text, blocks: $blocks, unfurl_links: false, unfurl_media: false}')

RESP=$(curl -sS -m 10 -X POST "https://slack.com/api/chat.postMessage" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  --data "$PAYLOAD" 2>&1)

if [ $? -ne 0 ]; then
  echo "WARN: /_slack-notify: send failed (curl: $RESP) — continuing." >&2
  exit 0
fi

OK=$(printf '%s' "$RESP" | jq -r '.ok // false' 2>/dev/null)
if [ "$OK" != "true" ]; then
  ERR=$(printf '%s' "$RESP" | jq -r '.error // "unknown"' 2>/dev/null)
  if [ "$ERR" = "ratelimited" ]; then
    # Respect Retry-After ONCE (bounded), then drop. Never retry-storm.
    sleep 5
    RESP=$(curl -sS -m 10 -X POST "https://slack.com/api/chat.postMessage" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json; charset=utf-8" \
      --data "$PAYLOAD" 2>&1)
    OK=$(printf '%s' "$RESP" | jq -r '.ok // false' 2>/dev/null)
  fi
  if [ "$OK" != "true" ]; then
    echo "WARN: /_slack-notify: send failed (slack: $ERR) — continuing." >&2
    exit 0
  fi
fi
```

## Step 3: Audit line

```
slack-notify: sent <shape> source=<source|-> lines=<N> channel=<channel_id>
```

(or the G1/G2 `disabled` line, or nothing extra after a WARN — one line
per invocation total.)

## Failure handling summary

| Failure | Behavior |
|---|---|
| config file absent (G1) | silent no-op, audit line, exit 0 |
| config file unparsable JSON (G2a) | one WARN (user opted in; corrupted file must not be silent), no-op, exit 0 |
| `enabled != true` (G2) | silent no-op, audit line, exit 0 |
| missing channel_id / bot_token (G3) | one WARN, no-op, exit 0 |
| curl error / timeout / non-2xx (G4) | one WARN, exit 0 |
| Slack `ok:false` (G4) | one WARN with the Slack error code, exit 0 |
| Slack `ratelimited` | bounded sleep + ONE retry, then WARN + drop |

There is NO failure mode that blocks, retries indefinitely, or changes
the calling pipeline's exit code.

## Callers (3 sites, 2 files)

- `/ticket-analyze` Step 10 — `commands/dev/ticket-analyze.md` (batch
  digest after the `Summary:` line)
- `/ggx-dispatcher` §4.2 — `commands/dev/ggx-dispatcher.md` (batch-abort)
- `/ggx-dispatcher` §6.5 — `commands/dev/ggx-dispatcher.md` (end-of-run
  digest)

Do NOT re-inline the mapping or the send block in a caller; extend this
skill instead. New pipelines (e.g. a future ui-tweak digest) add ONE
call site and, if needed, new rows to the mapping table here.

## Guardrails

- **No dedup is deliberate.** The user explicitly wants stuck tickets
  re-announced every run as a standing reminder (decision 2026-06-05,
  `plans/slack-notifier-design.md` §1.4). Do NOT add send-on-change-only
  logic, sidecar ledgers, or ticket markers here.
- **Never block the pipeline.** Every gate and failure path exits 0.
  Slack being down must be invisible to ticket processing.
- **The config is NEVER committed.** It lives in-repo at
  `commands/dev/profiles/ggx-slack.json` for discoverability (same
  pattern as `org.yaml`) but MUST stay in `.gitignore` — it holds a bot
  token, and committing it would both leak the token and force this
  user's config onto every installer (breaking the opt-in contract).
  No `.example` template is committed either (deliberate — the schema
  above is the reference). If `git check-ignore` ever stops matching
  the config path, fix `.gitignore` before anything else.
- **Per-user opt-in is the foolproofing contract.** G1/G2 must stay
  completely silent (audit line, no WARN) — other users of this repo
  must never be nagged about Slack.
- **`--dry-run` paths must not reach this skill.** Both callers gate the
  invocation (`/ticket-analyze` dry-run short-circuits at Step 7;
  `/ggx-dispatcher` dry-run stops at §4.0 before §4.2/§6.5).
- **Never send between the dispatcher's §4.3 table and §5.3 spawns.**
  The table + N Agent calls must stay in one assistant message; notify
  points in the dispatcher are exclusively §4.2 and §6.5.
- **Raw signals in, rendering out.** If a caller starts passing
  pre-rendered emoji/tokens, that is drift — fix the caller.
- **Messages are English** (repo convention: all user-facing output is
  English).
