---
name: _slack-notify
description: "Internal helper invoked by /ticket-analyze (Step 10), /ggx-dispatcher (§4.2, §6.5), and /ggx-on-duty (Finalize). Posts run-level Slack digests / batch-abort alerts to the user's personal pipeline channel via their own Slack bot (chat.postMessage). Opt-in per user: config lives in-repo at commands/dev/profiles/ggx-slack.json (gitignored, org.yaml pattern, symlinked by install.sh); if absent or disabled, every call is a completely silent no-op — users who never configured a Slack bot see zero behavior change. Fail-soft single-WARN on send errors; NEVER blocks or fails the calling pipeline. Callers pass raw signals; the status→emoji/token mapping lives ONLY in this file. Not user-invoked."
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
  `<source>` ∈ `ticket-analyzer` | `ggx-dispatcher` | `on-duty`. The
  caller passes a header-stats object and one raw-signal line per item
  (see Inputs). The first two sources are **ticket-keyed** (one line per
  Linear ticket); `on-duty` is **PR-keyed** (one line per PR, plus
  ticket-keyed analyzer-verdict-change lines) — it is `/ggx-on-duty`'s
  ONE batched per-wake notification, not a per-ticket sweep.
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

### `digest on-duty`

The on-duty loop's ONE batched per-wake notification (`/ggx-on-duty`
Finalize step 1). Unlike the two ticket-keyed sources above, the
per-item lines are **PR-keyed** (`<pr#> <url>` identifies a PR, not a
Linear ticket) — except the analyzer-verdict-change lines, which are
ticket-keyed (they report a Leg-1 verdict that flipped since last wake).
A wake with nothing to report still calls (the renderer emits a quiet
"all clear" line); duplication with the analyzer/dispatcher built-in
digests fired by the same wake's Leg-1 chain is accepted (D17 — no
suppression here).

Header stats: `team`, `repo`, `prs_open` (count of open PRs polled this
wake), optional `chain` (one of `in-flight` | `idle` | `disabled` —
mirrors the cycle summary). Per-item lines (raw signals; emit only the
lines that apply this wake):

```
<pr#> <url> ci-red check=<check-name> sha=<short-sha> title="<pr-title>"
<pr#> <url> ci-red check=<check-name> sha=<short-sha> self-pushed title="<pr-title>"
<pr#> <url> resolver-needs-human reason=<conflict|tests-failed|worktree-dirty|comment-fix-failed-tests|push-failed> title="<pr-title>"
<pr#> <url> resolver-done rebased=<yes|no> fixed=<n> replied=<n> title="<pr-title>"
<ticket-id> <url> verdict-change from=<prev-verdict|none> to=<new-verdict> title="<title>"
```

- `ci-red` carries `self-pushed` when the red SHA is the loop's own
  pushed SHA (`ggx-on-duty.md` Leg-2 step 1 — `(self-pushed — rerun from
  our own push)`); it never swallows the alert, only tags it.
- `resolver-needs-human` `reason` is verbatim one of the resolver's five
  `needs-human:` exit reasons (`/ggx-pr-resolver` step 8).
- (`review-posted` / `review-capped` lines were removed with the on-duty
  code-review leg — D6 REVERSED 2026-06-06, `plans/ggx-on-duty.md`; the
  loop has no review emitter, so this skill defines no vocabulary for it.)
- `verdict-change` reports only analyzer verdicts that CHANGED since the
  prior wake (`analyzer_verdicts` diff) — steady-state verdicts are not
  re-announced (the analyzer's own built-in digest already re-announces
  stuck tickets; this line is the loop's change-only view).

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
| on-duty `ci-red` | `CI-RED` | 🔴 | yes | `check <check> on PR <pr#>` |
| on-duty `ci-red` + `self-pushed` | `CI-RED` | 🔴 | yes | `check <check> — rerun from our own push` |
| on-duty `resolver-needs-human` | `RESOLVER` | 🛠️ | yes | per-reason: conflict → `resolve the rebase conflict on PR <pr#>`; tests-failed → `fix the failing tests in ../<ticket> (rebased cleanly, suite red)`; worktree-dirty → `clean ../<ticket> then re-run`; comment-fix-failed-tests → `fix the failing tests in ../<ticket> (worktree left dirty)`; push-failed → `someone pushed concurrently; next poll re-rebases` |
| on-duty `resolver-done` | `RESOLVED` | 🟢 | no | — (FYI line, no action) |
| on-duty `verdict-change` | `VERDICT` | 🔁 | no | — (FYI; change since last wake) |
| `batch-abort` | `BATCH-ABORT` | ⛔ | yes | `manually unlock <ids>` |

Notes:

- `REVIEW` carries `#needs-human` deliberately — "next action = a human
  reviews the PR" (soft). Decided 2026-06-05: digest line only, never an
  individual broadcast.
- Reasons mentioning a missing lane classification (`UNKNOWN_LANE`,
  `missing classification`) stay `FAILED` in v1 — the reason text already
  says what to do. The full design taxonomy (incl. `CLASSIFY` ❓) lives in
  `plans/slack-notifier-design.md` §2 for future expansion.
- On-duty `RESOLVED` / `VERDICT` are deliberately `#needs-human: no` —
  they are FYI lines that close the loop's feedback (a resolver pushed,
  an analyzer verdict flipped). They render in the info footer, not
  the "Needs your action" block. `CI-RED` and `RESOLVER` are the only
  on-duty signals that demand a human.

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

**On-duty digest blocks** (PR-keyed; same v2 skeleton as the digest
blocks above, only the line vocabulary differs):

1. `header` block (plain_text, ≤150 chars): `🟢 on-duty · <repo> · <team> team`.
2. `section` (mrkdwn) — counts line:
   `*<prs_open> PRs* — <r> ci-red · <h> needs-human · <p> resolved · <v> verdict changes` (chain: `<chain>` appended when present, e.g. `· chain: in-flight`). Each count is derived from the per-item lines.
3. `divider`.
4. `section` (mrkdwn) — `*Needs your action (<n>)*` with the same
   **two-line item** format as the digest block, ordered CI-RED first,
   then RESOLVER (the only `#needs-human` on-duty
   signals). PR items use the PR url + `#<pr#>` as the link label:

   ```
   <emoji> *<<url>|#<pr#>>* <pr-title, truncated to 60 chars with …>
           ↳ <status-word>: <summary> — _<next action>_
   ```

   `<summary>` is the raw signal's detail (red check + sha + `(self-pushed
   — rerun from our own push)` when tagged / the resolver `needs-human`
   reason). Omit this whole block when nothing needs
   action.
5. Optional `section` (mrkdwn) — info footer, the FYI (`#needs-human: no`)
   lines, each one line, omit the footer entirely if none:
   - `Resolved: #<pr#> (rebased, <n> fixed)` per `resolver-done`.
   - `Verdict changes: <ticket-id> <prev>→<new>, …` per `verdict-change`.
   - `Digest also appended to .ggx-on-duty/digest.md` is NOT printed here
     — the durable fallback is a caller-side write (see Callers), not a
     line this skill renders.
6. `context` block — hashtags ONCE: `#ggx-on-duty <#ggx-<token> for each token present> [#needs-human if any action item]`.

When the wake had nothing to report (no per-item lines), blocks 3-5 are
omitted and block 2 renders `*<prs_open> PRs* — all clear` so the loop's
heartbeat is still visible.

**Fallback `text`** (one line): `🟢 [STANDBY] <repo> · <team> — <prs_open> PRs, <r> red, <h> needs-human [#needs-human]`.

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

## Callers (4 sites, 3 files)

- `/ticket-analyze` Step 10 — `commands/dev/ticket-analyze.md` (batch
  digest after the `Summary:` line)
- `/ggx-dispatcher` §4.2 — `commands/dev/ggx-dispatcher.md` (batch-abort)
- `/ggx-dispatcher` §6.5 — `commands/dev/ggx-dispatcher.md` (end-of-run
  digest)
- `/ggx-on-duty` Finalize step 1 — `commands/dev/ggx-on-duty.md` (the ONE
  batched per-wake `digest on-duty`). This caller stands apart in two
  ways, both deliberate:
  - **Duplication is accepted (D17)**: the same wake's Leg-1 chain fires
    the analyzer's Step-10 and the dispatcher's §6.5 built-in digests too.
    On-duty's `digest on-duty` is the loop's own PR-centric view and is
    NOT suppressed — do NOT add cross-source dedup here.
  - **Caller-side durable fallback**: `/ggx-on-duty` ALSO appends the same
    summary to `.ggx-on-duty/digest.md` itself, in its Finalize step —
    that append is owned by the caller, NOT this skill. When Slack is
    unconfigured this skill is a silent no-op (G1) yet the digest still
    lands on disk, so the loop's record is never lost. This skill writes
    only to Slack; it never touches `.ggx-on-duty/`.

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
- **Never send between the dispatcher's §4.3 table and the §5.2 `Workflow` launch.**
  The table + the `Workflow` tool call must stay in one assistant message; notify
  points in the dispatcher are exclusively §4.2 and §6.5.
- **Raw signals in, rendering out.** If a caller starts passing
  pre-rendered emoji/tokens, that is drift — fix the caller.
- **Messages are English** (repo convention: all user-facing output is
  English).
