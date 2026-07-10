#!/usr/bin/env bash
# Frozen-fixture tests for skills/shared/ggx-standup/standup.py.
#
# scripts/prompt-lint.sh invokes every lib/*.test.sh as a whole-repo invariant,
# so this runs as part of the `prompt` platform's verify-stage test_cmd.
#
# Run directly:  bash lib/ggx-standup.test.sh   (exit 0 = pass, 1 = fail)
# Portable to macOS bash 3.2 (no associative arrays / mapfile).

set -u
HERE=$(cd "$(dirname "$0")" && pwd)
PY="$HERE/../skills/shared/ggx-standup/standup.py"

FAILS=0
PASSES=0

pass() { PASSES=$((PASSES + 1)); }
fail() { FAILS=$((FAILS + 1)); echo "  FAIL: $1"; }

# assert_json <label> <expr-jq> <expected> : run `window`, jq the field, compare.
assert_win() {
  local label="$1" got="$2" want="$3"
  if [ "$got" = "$want" ]; then pass; else fail "$label: got '$got' want '$want'"; fi
}

# assert_has <label> <text-blob> <needle>
assert_has() {
  if printf '%s' "$2" | grep -qF -- "$3"; then pass; else fail "$1: missing '$3'"; fi
}
# assert_absent <label> <text-blob> <needle>
assert_absent() {
  if printf '%s' "$2" | grep -qF -- "$3"; then fail "$1: unexpected '$3'"; else pass; fi
}

echo "== window: Tuesday run (no weekend) =="
W=$(python3 "$PY" window --now "2026-07-07T09:00:00+08:00")
assert_win "tue.start" "$(printf '%s' "$W" | python3 -c 'import json,sys;print(json.load(sys.stdin)["start"][:16])')" "2026-07-06T00:00"
assert_win "tue.end"   "$(printf '%s' "$W" | python3 -c 'import json,sys;print(json.load(sys.stdin)["end"][:16])')"   "2026-07-07T00:00"
assert_win "tue.weekend" "$(printf '%s' "$W" | python3 -c 'import json,sys;print(json.load(sys.stdin)["spans_weekend"])')" "False"

echo "== window: Monday run (spans Fri/Sat/Sun) =="
W=$(python3 "$PY" window --now "2026-07-06T09:00:00+08:00")
assert_win "mon.start" "$(printf '%s' "$W" | python3 -c 'import json,sys;print(json.load(sys.stdin)["start"][:16])')" "2026-07-03T00:00"
assert_win "mon.end"   "$(printf '%s' "$W" | python3 -c 'import json,sys;print(json.load(sys.stdin)["end"][:16])')"   "2026-07-06T00:00"
assert_win "mon.weekend" "$(printf '%s' "$W" | python3 -c 'import json,sys;print(json.load(sys.stdin)["spans_weekend"])')" "True"

echo "== window: --date override =="
W=$(python3 "$PY" window --now "2026-07-09T09:00:00+08:00" --date "2026-07-08")
assert_win "date.start" "$(printf '%s' "$W" | python3 -c 'import json,sys;print(json.load(sys.stdin)["start"][:16])')" "2026-07-07T00:00"
assert_win "date.end"   "$(printf '%s' "$W" | python3 -c 'import json,sys;print(json.load(sys.stdin)["end"][:16])')"   "2026-07-08T00:00"

echo "== window: gh bounds carry +08:00 offset (day-boundary precision) =="
assert_has "offset" "$W" "+08:00"

echo "== render: comprehensive fixture =="
BUNDLE=$(cat <<'JSON'
{
  "tz": "Asia/Hong_Kong",
  "me": "charlie-yang-gogox",
  "linear_ok": true,
  "window": {"start": "2026-07-06T00:00:00+08:00", "end": "2026-07-07T00:00:00+08:00", "spans_weekend": false},
  "ticket_states": {"CAF-987": "Ready for QA", "GGC-54": "Done", "CAF-1021": "In Review", "CAF-924": "In Review"},
  "merged_prs": [
    {"number": 801, "title": "CAF-987: Transport tunnels after order", "url": "u", "repository": {"name": "gogox-client-flutter", "nameWithOwner": "gogovan/gogox-client-flutter"}},
    {"number": 177, "title": "feat(GGC-54): telemetry aggregator", "url": "u", "repository": {"name": "gogox-claude", "nameWithOwner": "charlie-yang-gogox/gogox-claude"}},
    {"number": 181, "title": "fix(GGC-54): run-key ticket_id", "url": "u", "repository": {"name": "gogox-claude", "nameWithOwner": "charlie-yang-gogox/gogox-claude"}},
    {"number": 900, "title": "fix(CAF-100): also touches CAF-101", "url": "u", "repository": {"name": "gogox-client-flutter", "nameWithOwner": "gogovan/gogox-client-flutter"}},
    {"number": 842, "title": "fix(android): raise Gradle/R8 heap", "url": "u", "repository": {"name": "gogox-client-flutter", "nameWithOwner": "gogovan/gogox-client-flutter"}},
    {"number": 5, "title": "GGC-999 personal project change", "url": "u", "repository": {"name": "zip-crack", "nameWithOwner": "charlie-yang-gogox/zip-crack"}}
  ],
  "opened_prs": [
    {"number": 844, "title": "CAF-1021: Drop-off time not reset", "url": "u", "repository": {"name": "gogox-client-flutter", "nameWithOwner": "gogovan/gogox-client-flutter"}}
  ],
  "open_prs": [
    {"number": 844, "title": "CAF-1021: Drop-off time not reset", "url": "u", "repository": {"name": "gogox-client-flutter", "nameWithOwner": "gogovan/gogox-client-flutter"}, "commits": [{"committedDate": "2026-07-06T12:00:00+08:00", "authors": [{"login": "charlie-yang-gogox"}]}]},
    {"number": 843, "title": "CAF-924: coupon row tap", "url": "u", "repository": {"name": "gogox-client-flutter", "nameWithOwner": "gogovan/gogox-client-flutter"}, "commits": [{"committedDate": "2026-07-06T15:00:00+08:00", "authors": [{"login": "charlie-yang-gogox"}]}]},
    {"number": 700, "title": "CAF-777: stale before window", "url": "u", "repository": {"name": "gogox-client-flutter", "nameWithOwner": "gogovan/gogox-client-flutter"}, "commits": [{"committedDate": "2026-07-05T12:00:00+08:00", "authors": [{"login": "charlie-yang-gogox"}]}]},
    {"number": 710, "title": "CAF-888: someone else commit", "url": "u", "repository": {"name": "gogox-client-flutter", "nameWithOwner": "gogovan/gogox-client-flutter"}, "commits": [{"committedDate": "2026-07-06T12:00:00+08:00", "authors": [{"login": "another-dev"}]}]}
  ],
  "linear_started": [
    {"id": "CAF-924", "title": "coupon row tap", "state": "In Review", "stateType": "started", "updatedAt": "2026-07-06T16:00:00+08:00"},
    {"id": "CAF-500", "title": "no-PR in-progress ticket", "state": "In Progress", "stateType": "started", "updatedAt": "2026-07-06T11:00:00+08:00"},
    {"id": "CAF-501", "title": "already completed", "state": "Done", "stateType": "completed", "updatedAt": "2026-07-06T11:00:00+08:00"},
    {"id": "CAF-502", "title": "started but stale", "state": "In Progress", "stateType": "started", "updatedAt": "2026-07-01T11:00:00+08:00"}
  ]
}
JSON
)
OUT=$(printf '%s' "$BUNDLE" | python3 "$PY" render)

# Done section
assert_has    "done.caf987-chip"  "$OUT" "CAF-987"
assert_has    "done.caf987-state" "$OUT" "Ready for QA"
assert_has    "done.ggc54-pr177"  "$OUT" "#177"
assert_has    "done.ggc54-pr181"  "$OUT" "#181"
assert_has    "done.multi-100"    "$OUT" "CAF-100"
assert_has    "done.multi-101"    "$OUT" "CAF-101"
assert_has    "done.other-chore"  "$OUT" "raise Gradle"
# Personal-repo PR must never appear
assert_absent "filter.personal"   "$OUT" "CAF-999"
assert_absent "filter.personal2"  "$OUT" "GGC-999"

# Today section
assert_has    "today.caf924"      "$OUT" "CAF-924"
assert_has    "today.caf500"      "$OUT" "CAF-500"
assert_has    "today.followup"    "$OUT" "follow-up"     # CAF-1021 in both -> flagged
assert_absent "today.stale-pr"    "$OUT" "CAF-777"       # commit before window
assert_absent "today.other-author" "$OUT" "CAF-888"      # commit not mine
assert_absent "today.completed"   "$OUT" "CAF-501"       # not a started state
assert_absent "today.stale-linear" "$OUT" "CAF-502"      # started but updated before window

# Paste block present
assert_has    "paste.yesterday"   "$OUT" "① Yesterday"
assert_has    "paste.today"       "$OUT" "② Today"

echo "== render: CAF-1021 not double-counted as an independent Done + Today =="
# It is opened yesterday (Done) and has a fresh commit (Today follow-up). In the
# REPORT it must appear exactly twice: once in Done, once in Today (follow-up).
OUT_R=$(printf '%s' "$BUNDLE" | python3 "$PY" render --report-only)
C=$(printf '%s' "$OUT_R" | grep -c "CAF-1021")
if [ "$C" -eq 2 ]; then pass; else fail "dedup: CAF-1021 appears $C times in report (want 2: Done + Today follow-up)"; fi

echo "== render: Linear unauthenticated -> PR-only + note =="
BUNDLE2=$(cat <<'JSON'
{
  "tz": "Asia/Hong_Kong",
  "me": "charlie-yang-gogox",
  "linear_ok": false,
  "window": {"start": "2026-07-06T00:00:00+08:00", "end": "2026-07-07T00:00:00+08:00", "spans_weekend": false},
  "ticket_states": {},
  "merged_prs": [{"number": 801, "title": "CAF-987: tunnels", "url": "u", "repository": {"name": "gogox-client-flutter", "nameWithOwner": "gogovan/gogox-client-flutter"}}],
  "opened_prs": [],
  "open_prs": [],
  "linear_started": [{"id": "CAF-500", "title": "should be ignored", "state": "In Progress", "stateType": "started", "updatedAt": "2026-07-06T11:00:00+08:00"}]
}
JSON
)
OUT2=$(printf '%s' "$BUNDLE2" | python3 "$PY" render)
assert_has    "degraded.pr"     "$OUT2" "CAF-987"
assert_has    "degraded.note"   "$OUT2" "Linear unauthenticated"
assert_absent "degraded.linear" "$OUT2" "CAF-500"

echo "== render: empty window (weekend / no activity) =="
BUNDLE3='{"tz":"Asia/Hong_Kong","me":"x","linear_ok":true,"window":{"start":"2026-07-06T00:00:00+08:00","end":"2026-07-07T00:00:00+08:00","spans_weekend":false},"ticket_states":{},"merged_prs":[],"opened_prs":[],"open_prs":[],"linear_started":[]}'
OUT3=$(printf '%s' "$BUNDLE3" | python3 "$PY" render)
assert_has "empty.done"  "$OUT3" "no PRs opened or merged"
assert_has "empty.today" "$OUT3" "nothing in progress"

echo "== extract: body cross-refs must NOT create false ticket attribution =="
# Regression for a live-data bug: PR bodies routinely reference unrelated
# tickets ("Related:", "Enables", changelog). First-source-wins means a PR
# with an id in its title is attributed ONLY to that id; the body is used
# solely as a fallback when title+branch carry none.
BUNDLEX=$(cat <<'JSON'
{
  "tz": "Asia/Hong_Kong", "me": "x", "linear_ok": true,
  "window": {"start": "2026-07-06T00:00:00+08:00", "end": "2026-07-07T00:00:00+08:00", "spans_weekend": false},
  "ticket_states": {},
  "merged_prs": [
    {"number": 181, "title": "fix(GGC-54): run-key ticket_id", "body": "Related: CAF-921. Enables CAF-1022 and DAF-77.", "url": "u", "repository": {"name": "gogox-claude", "nameWithOwner": "charlie-yang-gogox/gogox-claude"}},
    {"number": 50, "title": "chore: bump deps", "body": "part of CET-8462 rollout", "url": "u", "repository": {"name": "gogovan-client-v2-android", "nameWithOwner": "gogovan/gogovan-client-v2-android"}}
  ],
  "opened_prs": [], "open_prs": [], "linear_started": []
}
JSON
)
OUTX=$(printf '%s' "$BUNDLEX" | python3 "$PY" render --report-only)
assert_has    "xref.title-id"      "$OUTX" "GGC-54"     # from #181 title
assert_absent "xref.body-caf921"   "$OUTX" "CAF-921"    # #181 body ref — must be ignored
assert_absent "xref.body-caf1022"  "$OUTX" "CAF-1022"
assert_absent "xref.body-daf77"    "$OUTX" "DAF-77"
assert_has    "xref.body-fallback" "$OUTX" "CET-8462"   # #50 has no title/branch id → body fallback fires

echo "== render --html: anchors, code chips, escaping, no-url = no anchor =="
# CAF-987 has a ticket url -> its id becomes an <a href>. GGC-54 has NO ticket
# url -> its id must render WITHOUT an anchor (AC3: no misleading link). PR urls
# always anchor. Titles are HTML-escaped. Personal-repo PRs still filtered.
BUNDLEH=$(cat <<'JSON'
{
  "tz": "Asia/Hong_Kong", "me": "charlie-yang-gogox", "linear_ok": true,
  "window": {"start": "2026-07-06T00:00:00+08:00", "end": "2026-07-07T00:00:00+08:00", "spans_weekend": false},
  "ticket_states": {"CAF-987": "Ready for QA", "GGC-54": "Done"},
  "ticket_urls": {"CAF-987": "https://linear.app/gogox/issue/CAF-987/x"},
  "merged_prs": [
    {"number": 801, "title": "CAF-987: Tunnels <fee> & stuff", "url": "https://github.com/gogovan/gogox-client-flutter/pull/801", "repository": {"name": "gogox-client-flutter", "nameWithOwner": "gogovan/gogox-client-flutter"}},
    {"number": 177, "title": "feat(GGC-54): telemetry aggregator", "url": "https://github.com/x/gogox-claude/pull/177", "repository": {"name": "gogox-claude", "nameWithOwner": "charlie-yang-gogox/gogox-claude"}},
    {"number": 5, "title": "GGC-999 personal", "url": "u", "repository": {"name": "zip-crack", "nameWithOwner": "charlie-yang-gogox/zip-crack"}}
  ],
  "opened_prs": [], "open_prs": [], "linear_started": []
}
JSON
)
OUTH=$(printf '%s' "$BUNDLEH" | python3 "$PY" render --html)
assert_has    "html.doctype"       "$OUTH" "<!doctype html>"
assert_has    "html.strong-hdr"    "$OUTH" "<strong>Yesterday (Done)</strong>"
assert_has    "html.ticket-anchor" "$OUTH" 'href="https://linear.app/gogox/issue/CAF-987/x">CAF-987</a>'
assert_has    "html.state-code"    "$OUTH" "<code>Ready for QA</code>"
assert_has    "html.pr-anchor"     "$OUTH" 'href="https://github.com/gogovan/gogox-client-flutter/pull/801">#801</a>'
assert_absent "html.no-url-anchor" "$OUTH" ">GGC-54</a>"        # GGC-54 has no url -> bare, not an anchor
assert_has    "html.escape"        "$OUTH" "&lt;fee&gt;"        # title '<fee>' escaped
assert_absent "html.escape-raw"    "$OUTH" "<fee>"              # raw angle brackets must not leak
assert_absent "html.personal"      "$OUTH" "GGC-999"            # personal repo still filtered

echo
echo "ggx-standup.test.sh: $PASSES passed, $FAILS failed"
[ "$FAILS" -eq 0 ]
