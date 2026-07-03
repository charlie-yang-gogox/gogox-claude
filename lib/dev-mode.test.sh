#!/usr/bin/env bash
# Regression tests for lib/dev-mode.sh `pipe_mode` (GGC-76).
#
# Guards the load-bearing invariant the GGC-76 fix relies on: /dev:start now
# writes a `feature-direct`-valued .dev/mode.md so the direct-mode walker's
# start→apply gate fires — and that marker MUST NOT change pipe_mode's result
# (it is not a positive bug/port-handoff match, so it falls through to the
# `! -d openspec` branch and still resolves `feature-direct`).
#
# No external test runner exists yet (a full walker golden-state suite is
# GGC-27). scripts/prompt-lint.sh invokes this file as a whole-repo invariant,
# so it runs as part of the `prompt` platform's verify-stage test_cmd.
#
# Run directly:  bash lib/dev-mode.test.sh   (exit 0 = pass, 1 = fail)
# Portable to macOS bash 3.2 (no associative arrays / mapfile).

set -u
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=/dev/null
. "$HERE/dev-mode.sh"

FAILS=0
PASSES=0

# mk_wt <name> [openspec] [mode-value]
#   Build a throwaway worktree-shaped dir under $ROOT. `openspec` (literal) adds
#   an openspec/ dir; a non-empty third arg writes .dev/mode.md with that value.
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/dev-mode-test.XXXXXX")
trap 'rm -rf "$ROOT"' EXIT

mk_wt() {
  local name="$1" want_openspec="${2:-}" mode_val="${3:-}"
  local wt="$ROOT/$name"
  mkdir -p "$wt"
  [ "$want_openspec" = "openspec" ] && mkdir -p "$wt/openspec/changes"
  if [ -n "$mode_val" ]; then
    mkdir -p "$wt/.dev"
    printf '%s\n' "$mode_val" > "$wt/.dev/mode.md"
  fi
  printf '%s\n' "$wt"
}

# expect <description> <expected> <actual>
expect() {
  local desc="$1" exp="$2" act="$3"
  if [ "$exp" = "$act" ]; then
    PASSES=$((PASSES + 1))
    printf 'ok    %s (pipe_mode=%s)\n' "$desc" "$act"
  else
    FAILS=$((FAILS + 1))
    printf 'FAIL  %s — expected %s, got %s\n' "$desc" "$exp" "$act" >&2
  fi
}

# --- cases -------------------------------------------------------------------

# 1. feature-direct, legacy/unmarked: no openspec/, no mode.md → feature-direct
wt=$(mk_wt fd_legacy "" "")
expect "no openspec/ + no mode.md → feature-direct (dynamic)" feature-direct "$(pipe_mode "$wt")"

# 2. THE GGC-76 INVARIANT: no openspec/ + mode.md=feature-direct → feature-direct
wt=$(mk_wt fd_marked "" "feature-direct")
expect "no openspec/ + mode.md=feature-direct → feature-direct (marker safe)" feature-direct "$(pipe_mode "$wt")"

# 3. bug marker wins even with no openspec/
wt=$(mk_wt bug_nood "" "bug")
expect "no openspec/ + mode.md=bug → bug" bug "$(pipe_mode "$wt")"

# 4. port-handoff marker wins even with no openspec/
wt=$(mk_wt ph_nood "" "port-handoff")
expect "no openspec/ + mode.md=port-handoff → port-handoff" port-handoff "$(pipe_mode "$wt")"

# 5. OpenSpec repo, unmarked → feature
wt=$(mk_wt feat_os "openspec" "")
expect "openspec/ + no mode.md → feature" feature "$(pipe_mode "$wt")"

# 6. bug marker wins even on an OpenSpec repo
wt=$(mk_wt bug_os "openspec" "bug")
expect "openspec/ + mode.md=bug → bug" bug "$(pipe_mode "$wt")"

# --- is_direct_mode: apply-path classification -------------------------------
# is_direct_mode answers "direct Edit vs /opsx:apply" (and the --auto gate),
# NOT "is there a change dir". Truth table (a future mode defaults to the `*)`
# NOT-direct branch — if wrong for it, the case below FAILs loudly):
#   direct-apply: bug, feature-direct
#   OpenSpec /opsx:apply flow: port-handoff, feature

# expect_direct <mode> <expected: direct|openspec>
expect_direct() {
  local mode="$1" want="$2" got
  if is_direct_mode "$mode"; then got=direct; else got=openspec; fi
  expect "is_direct_mode $mode → $want" "$want" "$got"
}

expect_direct bug            direct
expect_direct feature-direct direct
expect_direct port-handoff   openspec
expect_direct feature        openspec

# --- has_change_dir: orthogonal to is_direct_mode ----------------------------
# The bug lane can author an OpenSpec delta, so "direct mode" no longer implies
# "no change dir". ship/verify detect the dir via has_change_dir, not the mode.
# expect_haschange <description> <expected: yes|no> <worktree>
expect_haschange() {
  local desc="$1" want="$2" wt="$3" got
  if has_change_dir "$wt"; then got=yes; else got=no; fi
  expect "$desc" "$want" "$got"
}

# no openspec/ dir at all → no change dir (feature-direct / gogox-claude)
wt=$(mk_wt hcd_noopenspec "" "")
expect_haschange "no openspec/ → has_change_dir no" no "$wt"

# openspec/ present but changes/ empty → no change dir (plain bug, no delta)
wt=$(mk_wt hcd_empty "openspec" "bug")
expect_haschange "openspec/ empty changes → has_change_dir no" no "$wt"

# a bug worktree carrying an authored delta dir → has_change_dir yes
wt=$(mk_wt hcd_bugdelta "openspec" "bug")
mkdir -p "$wt/openspec/changes/ggc-123-some-fix"
expect_haschange "bug + authored delta dir → has_change_dir yes" yes "$wt"

# only archive/ present → no live change dir
wt=$(mk_wt hcd_archived "openspec" "")
mkdir -p "$wt/openspec/changes/archive/2026-01-01-old"
expect_haschange "only archive/ → has_change_dir no" no "$wt"

# --- summary -----------------------------------------------------------------
printf 'dev-mode.test: %d passed, %d failed\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] || exit 1
exit 0
