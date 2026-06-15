---
name: _file-followup
description: "Internal helper invoked on pipeline failure paths (/ggx-dispatcher §6.2 infra-class failures, dispatch-fanout.workflow.js worker-died / platform-bug + inline design-bug lane, /dev:verify BLOCKED abort, /monkey-test crash report). Appends ONE markdown breadcrumb per failure to a LOCAL, gitignored sink (.ggx-followups/followups.md) — NO Linear ticket creation, NO GitHub, NO network. v1 closes the loop's missing stage-8 (feedback → follow-up capture) with a zero-risk local file the maintainer reviews later; promoting any entry to a real ticket stays a manual human action. Dedup by a stable class+signature key carried in an HTML marker; whitelisted failure classes only; hard cap of 3 appends per run. Fail-soft: any helper failure logs ONE WARN and NEVER blocks or fails the calling pipeline. Not user-invoked."
---

# `/_file-followup <class> summary=<text> [report=<path>] [signature=<text>]`

Single source of truth for capturing pipeline failures as **local
breadcrumbs**. Callers pass a whitelisted failure `<class>` plus a short
`summary` and (optionally) the path to the source report and a stable
`signature`. This helper appends exactly one markdown entry to a
**gitignored local file** — it NEVER creates a Linear ticket, NEVER calls
GitHub, NEVER makes any network call.

**Underscore prefix** marks this skill as internal — callers are other
skills / the dispatch workflow, never the user directly.

**Why local-only (v1, narrowed 2026-06-15)**: the goal is a zero-risk
breadcrumb the maintainer reviews later. Auto-filing tickets was considered
and rejected for v1 (it needed `save_issue` creation permissions and could
spam the tracker on a flaky-infra day). Promoting any breadcrumb to a real
ticket / label stays a **manual human action** — this helper only records.

## Inputs

```
/_file-followup <class> summary="<one-line what failed>" \
                [report=<path to the source report, e.g. .dev/verify-pass.md>] \
                [signature="<stable failure signature, e.g. ticket-id+stage>"]
```

- `<class>` — REQUIRED. Must be one of the **whitelist** (see below). Any
  other value → single WARN, no append, exit 0.
- `summary=` — REQUIRED. One-line human-readable description of the failure.
  Truncated to 200 chars in the entry.
- `report=` — OPTIONAL. Path to the source report / marker file so the
  maintainer can jump straight to the evidence. Recorded verbatim.
- `signature=` — OPTIONAL. A stable identifier for this specific failure
  (e.g. `<ticket-id>:<stage>` or a crash signature). Used — together with
  `<class>` — to build the dedup key. When absent, the dedup key falls back
  to `<class>:<sha256(summary)[:12]>` so an identical summary still dedups.

### Failure-class whitelist

| `<class>`          | Fired by                                                                 |
|--------------------|--------------------------------------------------------------------------|
| `dispatcher-infra` | `/ggx-dispatcher` §6.2 — a derived `failed` outcome (infra-class).        |
| `worker-died`      | `dispatch-fanout.workflow.js` `triageUnknownFallback` — worker agent died.   |
| `platform-bug`     | `dispatch-fanout.workflow.js` `triagePlatformBug` — suspected platform defect.|
| `design-bug-failed`| `dispatch-fanout.workflow.js` inline design-bug (`runUiTweak`) lane failure. |
| `verify-blocked`   | `/dev:verify` Step 2a — `Status: BLOCKED` still present after recovery.    |
| `audit-blocked`    | ui-tweak audit panel BLOCKED (the dispatcher inline design-bug handler).   |
| `monkey-crash`     | `/monkey-test` Step 5 — crashes remain (`--no-fix` or unfixable).          |

Any `<class>` not in this table is rejected (single WARN, no append). The
whitelist is the guard against an LLM misjudging an arbitrary failure as
loggable — only these classes ever produce a breadcrumb.

## Outputs

- `.ggx-followups/followups.md` — appended-to local sink, **gitignored**
  (`.ggx-followups/` is in `.gitignore`). Per-repo, never committed, never
  pushed. Created with `mkdir -p` on first use.
- One audit line to stdout (`file-followup: …`).
- NOTHING else — no Linear write, no `gh`, no network, no label change.

## Steps

### Step 0: Resolve the sink and the run cap

```bash
CLASS="<parsed class>"
SUMMARY="<parsed summary>"
REPORT="<parsed report or empty>"
SIGNATURE="<parsed signature or empty>"

# The sink lives at the repo root (git toplevel) so every call site — whether
# a worktree or the main repo — writes to the same per-repo file.
ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
SINK_DIR="$ROOT/.ggx-followups"
SINK="$SINK_DIR/followups.md"

# Per-run cap: 3 appends per dispatch run. A "run" is identified by the
# GGX_RUN_ID env var when the caller sets one (dispatcher / workflow), else a
# per-process fallback so a standalone caller still gets a cap. The counter is
# a tiny sidecar file, itself under the gitignored .ggx-followups/ dir.
RUN_ID="${GGX_RUN_ID:-pid-$PPID}"
CAP_FILE="$SINK_DIR/.run-$RUN_ID.count"
CAP=3
```

### Step 1: Validate class (fail-soft)

```bash
case "$CLASS" in
  dispatcher-infra|worker-died|platform-bug|design-bug-failed|verify-blocked|audit-blocked|monkey-crash) : ;;
  *)
    echo "WARN: /_file-followup: class '$CLASS' not in whitelist — no breadcrumb written." >&2
    exit 0 ;;   # fail-soft: never block the caller
esac

if [ -z "$SUMMARY" ]; then
  echo "WARN: /_file-followup: empty summary for class '$CLASS' — no breadcrumb written." >&2
  exit 0
fi
```

### Step 2: Enforce the per-run cap (fail-soft)

```bash
mkdir -p "$SINK_DIR" 2>/dev/null || {
  echo "WARN: /_file-followup: cannot mkdir $SINK_DIR — no breadcrumb written." >&2
  exit 0
}

COUNT=$(cat "$CAP_FILE" 2>/dev/null || echo 0)
case "$COUNT" in (*[!0-9]*) COUNT=0 ;; esac   # guard against a corrupt counter
if [ "$COUNT" -ge "$CAP" ]; then
  echo "file-followup: per-run cap ($CAP) reached for run $RUN_ID — skipping class=$CLASS (audit-only)." >&2
  exit 0
fi
```

### Step 3: Build the dedup key and skip if present (fail-soft)

```bash
# Stable key: class + signature (or sha of the summary when no signature given).
if [ -n "$SIGNATURE" ]; then
  SIG_PART=$(printf '%s' "$SIGNATURE" | shasum -a 256 2>/dev/null | cut -c1-12)
else
  SIG_PART=$(printf '%s' "$SUMMARY"   | shasum -a 256 2>/dev/null | cut -c1-12)
fi
[ -z "$SIG_PART" ] && SIG_PART="nosig"
KEY="$CLASS:$SIG_PART"
MARKER="<!-- followup:v1 key=$KEY -->"

# Dedup: if an entry with this exact marker already exists, do NOT append again.
if [ -f "$SINK" ] && grep -qF "$MARKER" "$SINK" 2>/dev/null; then
  echo "file-followup: duplicate (key=$KEY) already in $SINK — skipped." >&2
  exit 0
fi
```

### Step 4: Append exactly one entry (fail-soft)

```bash
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
SUMMARY_TRUNC=$(printf '%s' "$SUMMARY" | cut -c1-200)

{
  printf '\n%s\n' "$MARKER"
  printf '## %s — %s\n\n' "$TS" "$CLASS"
  printf '- **Summary**: %s\n' "$SUMMARY_TRUNC"
  [ -n "$REPORT" ]    && printf '- **Source report**: `%s`\n' "$REPORT"
  [ -n "$SIGNATURE" ] && printf '- **Signature**: `%s`\n' "$SIGNATURE"
  printf '- **Suggested action**: review and, if this is a real recurring defect, MANUALLY file a ticket. Auto-filing is intentionally out of scope (v1 local-only).\n'
} >> "$SINK" 2>/dev/null || {
  echo "WARN: /_file-followup: append to $SINK failed — no breadcrumb written." >&2
  exit 0
}

# Bump the per-run counter (best-effort; a failed bump never blocks).
printf '%s\n' "$((COUNT + 1))" > "$CAP_FILE" 2>/dev/null || true

echo "file-followup: appended (class=$CLASS key=$KEY) to $SINK." >&2
exit 0
```

## Idempotency & dedup contract

The HTML marker `<!-- followup:v1 key=<class>:<sig-hash> -->` is the dedup
anchor. Re-running the SAME failure (same class + same signature, or same
class + same summary when no signature) produces the SAME key → the Step 3
grep matches → the append is skipped. This means re-running a dispatch batch
that hits the same stuck ticket does NOT duplicate the breadcrumb. The
acceptance test for this is: fire the same failure twice, confirm exactly one
entry exists.

## Failure handling summary

| Failure                          | Behavior                                    |
|----------------------------------|---------------------------------------------|
| `<class>` not in whitelist       | WARN, no append, exit 0.                     |
| empty `summary`                  | WARN, no append, exit 0.                     |
| `mkdir` / append I/O error       | WARN, no append, exit 0.                     |
| per-run cap reached              | audit line, no append, exit 0.              |
| duplicate key                    | audit line, no append, exit 0.              |

There is **no hard-stop path**. Every branch exits 0. A breadcrumb is a
best-effort observability artifact — losing one must never fail a pipeline.

## Callers (v1 call sites)

- `/ggx-dispatcher` §6.2 — `commands/dev/ggx-dispatcher.md` (`any failed`
  row → `class=dispatcher-infra`).
- `dispatch-fanout.workflow.js` — `triageUnknownFallback` (`class=worker-died`),
  `triagePlatformBug` (`class=platform-bug`), and the inline design-bug
  (`runUiTweak`) failure path (`class=design-bug-failed` / `audit-blocked`).
  The workflow is JS, so it appends inline via the same file + marker contract
  (it cannot invoke a markdown skill); this file is the canonical spec the JS
  mirrors. Do NOT diverge the JS append format from Steps 3–4 here.
- `/dev:verify` Step 2a — `commands/dev/dev/verify.md` (BLOCKED-still ABORT →
  `class=verify-blocked`).
- `/monkey-test` Step 5 — `commands/dev/monkey-test.md` (crashes remain →
  `class=monkey-crash`).

Do NOT re-inline the append logic in a caller; invoke this skill (or, for the
JS workflow, mirror Steps 3–4 verbatim). New call sites add ONE invocation and,
if needed, a new whitelist row here.

## Guardrails

- **Local file ONLY.** NO `save_issue`, NO Linear ticket creation, NO `gh` /
  GitHub, NO network call of any kind. If a future version wants to promote
  breadcrumbs to tickets, that is a NEW skill / explicit human action — never
  bolt it onto this helper (the whole point of v1 is zero-risk).
- **Never block the pipeline.** Every gate and failure path exits 0. The sink
  being unwritable must be invisible to ticket processing.
- **`.ggx-followups/` is NEVER committed.** It is gitignored. If
  `git check-ignore .ggx-followups/` ever stops matching, fix `.gitignore`
  before anything else — a committed breadcrumb file would leak per-run noise
  into the repo and onto every other installer.
- **Whitelist + cap are the flood guards.** Only whitelisted classes append;
  the per-run cap of 3 stops a pathological batch from filling the file.
- **No classification labels.** This helper records a SUGGESTED classification
  in the entry text only — it never writes a Linear label. Promotion stays
  manual.
- **Messages are English** (repo convention: all user-facing output is English).
- **Reviewing `.ggx-followups/` is an accepted maintainer responsibility** for
  v1 — a purely-local file can be overlooked; that tradeoff is deliberate.
