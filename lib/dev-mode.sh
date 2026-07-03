#!/usr/bin/env bash
# Shared helpers for the /dev:* and /bug:* pipelines.
#
# Sourced by every stage in commands/dev/dev/*.md so the "is this pipeline
# in bug mode?" parse grammar lives in ONE place. If future modes are added
# (hotfix, chore, etc.), only this file needs to change.
#
# Source path: $HOME/.claude/lib/dev-mode.sh (symlinked from this repo by
# install.sh — see install.sh's `lib/` block).

# pipe_mode [<worktree_root>]
#
# Echoes the pipeline mode:
#   `bug`            — .dev/mode.md exists and its first line is exactly `bug`
#                      (written by /dev:start --bug; always wins).
#   `feature-direct` — no bug marker AND the worktree has no `openspec/` dir:
#                      feature work on a repo that does not use OpenSpec
#                      (e.g. gogox-claude, platform `prompt` — GGC-17).
#                      Rides the bug-mode FLOW (direct edit, no /opsx:*
#                      stages, no figma/detect/align) with feature SEMANTICS
#                      (feat:-typed commits, feature wording in reports).
#                      Detected dynamically (the `! -d openspec` branch below),
#                      so legacy/unmarked worktrees resolve correctly with zero
#                      migration. As of GGC-76, /dev:start ALSO writes a
#                      `feature-direct`-valued .dev/mode.md — required by the
#                      direct-mode walker's start→apply gate, NOT by pipe_mode:
#                      the value is not a positive bug/port-handoff match, so it
#                      still falls through to the `! -d openspec` branch here.
#                      OpenSpec-initialized repos always have `openspec/`
#                      committed at the root, so they can never misdetect into
#                      this branch.
#   `port-handoff`   — .dev/mode.md exists and its first line is exactly
#                      `port-handoff` (written by /dev:start --port-handoff,
#                      GGC-56). Rides the FEATURE flow — the OpenSpec apply
#                      Steps 1-5 and the normal figma / detect / align chain —
#                      but is REACHED via the port→need-spec-review→ready-to-dev
#                      handoff entry (a committed openspec change adopted from
#                      /port) rather than a fresh /dev:start scaffold. The
#                      distinct marker lets /dev:apply hard-fail if it ever runs
#                      without the spec-review directives /dev:start captured.
#   `feature`        — everything else (the OpenSpec-driven default).
# Absent / unreadable / unexpected mode.md content falls through to the
# openspec-dir check, so unmarked legacy worktrees keep their behavior.
#
# Usage:
#   PIPE_MODE=$(pipe_mode)            # uses git toplevel
#   PIPE_MODE=$(pipe_mode "$WT")      # explicit worktree path
pipe_mode() {
  local wt
  wt="${1:-$(git rev-parse --show-toplevel 2>/dev/null)}"
  [ -z "$wt" ] && { echo feature; return; }
  if [ -f "$wt/.dev/mode.md" ] \
     && [ "$(head -1 "$wt/.dev/mode.md" 2>/dev/null)" = "bug" ]; then
    echo bug
  elif [ -f "$wt/.dev/mode.md" ] \
     && [ "$(head -1 "$wt/.dev/mode.md" 2>/dev/null)" = "port-handoff" ]; then
    echo port-handoff
  elif [ ! -d "$wt/openspec" ]; then
    echo feature-direct
  else
    echo feature
  fi
}

# is_direct_mode <pipe-mode>
#
# Single source of truth for the question "does this pipeline mode ride the
# OpenSpec flow, or is it a direct-edit mode with no openspec/changes/<name>
# dir?". Returns 0 (true) for the TRUE direct modes, 1 (false) otherwise:
#
#   bug            → direct  (LLM investigates + edits; no change dir)
#   feature-direct → direct  (feature work on a no-OpenSpec repo; no change dir)
#   port-handoff   → NOT direct (rides the feature OpenSpec flow: adopts a
#                    committed openspec/changes/<name> and applies Steps 1-5 —
#                    so it HAS a change dir that must be archived/verified)
#   feature        → NOT direct (the OpenSpec-driven default)
#
# INVARIANT: is_direct_mode(m) is true  ⇔  mode m has NO OpenSpec change dir.
# Every stage that used to hand-roll `[ "$PIPE_MODE" != "feature" ]` as a proxy
# for "no change to handle" MUST route through this predicate instead — that
# proxy silently misclassified port-handoff (feature semantics, not named
# `feature`) as a direct mode. Adding a future mode = add one case here, and
# lib/dev-mode.test.sh's truth-table asserts it was wired in.
is_direct_mode() {
  case "$1" in
    bug|feature-direct) return 0 ;;
    *) return 1 ;;
  esac
}

# default_branch
#
# Echoes the repo's default branch NAME (e.g. `trunk`, `main`) so the pipeline
# works on any repo, not just trunk-default flutter app repos. gogox-claude
# itself is `main` (GGC-10).
#
# Resolution order (first hit wins) — deliberately TRUNK-FIRST for backward
# safety, NOT pure auto-detection:
#   1. `.gogox-claude.yaml` `default_branch:` — explicit per-repo override
#      (escape hatch; almost never needed). Read without yq (grep one line).
#   2. **origin/trunk exists ⇒ `trunk`.** Every current flutter app repo has a
#      `trunk` integration branch, so this branch is taken and the result is
#      IDENTICAL to the old hardcoded `origin/trunk` — regardless of whether a
#      stale, unused `origin/main` also lingers, or GitHub's "default branch"
#      setting still says `main`. This is the key safety property: a repo that
#      works on trunk today keeps resolving to trunk, full stop. We never
#      consult GitHub's default for a repo that has trunk.
#   3. No trunk ⇒ resolve the real default dynamically: origin/HEAD, then
#      `gh repo view`. (This is the gogox-claude / non-trunk path.)
#   4. `trunk` — final fallback (offline + no gh + no trunk ref): preserves the
#      pre-GGC-10 default so nothing regresses to empty.
#
# Net effect vs the old code: behavior is UNCHANGED on every repo that has a
# trunk branch; it only diverges (to the true default) on repos that have no
# trunk at all — which is exactly the GGC-10 case this fix exists for.
default_branch() {
  local b root
  root=$(git rev-parse --show-toplevel 2>/dev/null)
  # 1. explicit profile override
  if [ -n "$root" ] && [ -f "$root/.gogox-claude.yaml" ]; then
    b=$(grep -E '^[[:space:]]*default_branch:' "$root/.gogox-claude.yaml" 2>/dev/null \
        | head -1 | sed -E 's/^[^:]*:[[:space:]]*//; s/[[:space:]]*$//; s/^["'\'']//; s/["'\'']$//')
    [ -n "$b" ] && { printf '%s\n' "$b"; return; }
  fi
  # 2. trunk-first: a trunk integration branch wins over any stale main / GitHub default
  if git show-ref --verify --quiet refs/remotes/origin/trunk; then
    printf 'trunk\n'; return
  fi
  # 3. no trunk → resolve the real default
  b=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||')
  [ -z "$b" ] && b=$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name' 2>/dev/null)
  # 4. final fallback
  [ -z "$b" ] && b=trunk
  printf '%s\n' "$b"
}

# trunk_ref
#
# Echoes the remote-tracking ref for the default branch (e.g. `origin/main`).
# Use this wherever the old code hardcoded `origin/trunk` as a diff/merge base.
trunk_ref() { printf 'origin/%s\n' "$(default_branch)"; }

# ---------------------------------------------------------------------------
# Per-repo test profile resolution (GGC-24)
# ---------------------------------------------------------------------------
# Single source of truth for the OPTIONAL per-repo test-profile keys that
# override / augment the platform yaml's `test_cmd`. All test-running
# consumers (`/check-test`, `/dev:verify` Step 1, `/ggx-pr-resolver` /
# `/resolve-conflict` callee tests-green gate) read these via the helpers
# below so the override lives in ONE place, not in N auto-memories.
#
# Resolution order for the profile file (first hit wins) — mirrors every
# command's "Step 0: Resolve project profile":
#   1. `<repo-root>/.gogox-claude.yaml`  (repo self-describes)
#   2. `~/.claude/commands/profiles/registry/<basename>.yaml`  (central map)
#
# Keys (all OPTIONAL, additive, empty-default → zero behavior change):
#   test_task:    <gradle unit-test task name>  e.g. testStandardStagingUnitTest
#                 Overrides android's default `testDebugUnitTest` task.
#   test_variant: <build variant>               e.g. standardStaging
#                 Convenience alias — when `test_task` is absent it is derived
#                 as `test${Variant^}UnitTest` (capitalized). `test_task` wins
#                 if both are set.
#   known_flaky_tests: <YAML list> of `Class#method` (or `Class` for a whole
#                 class) entries the flake-quarantine partition removes from the
#                 `--fix` budget. Matching is EXACT class[+method], never a
#                 name substring (see known_flaky_tests() below).

# profile_file [<worktree_root>]
#   Echoes the absolute path of the resolved profile yaml, or empty if none.
profile_file() {
  local wt root base reg
  wt="${1:-$(git rev-parse --show-toplevel 2>/dev/null)}"
  [ -z "$wt" ] && return 0
  if [ -f "$wt/.gogox-claude.yaml" ]; then
    printf '%s\n' "$wt/.gogox-claude.yaml"; return 0
  fi
  base=$(basename "$wt")
  reg="$HOME/.claude/commands/profiles/registry/$base.yaml"
  [ -f "$reg" ] && { printf '%s\n' "$reg"; return 0; }
  # Repo-local registry fallback (dev checkout of gogox-claude itself).
  root=$(git rev-parse --show-toplevel 2>/dev/null)
  reg="$root/commands/dev/profiles/registry/$base.yaml"
  [ -f "$reg" ] && printf '%s\n' "$reg"
}

# profile_value <key> [<worktree_root>]
#   Echoes the scalar value of <key> from the resolved profile, or empty.
#   Grep-based (no yq dependency) — reads a single top-level `key: value` line.
profile_value() {
  local key="$1" wt="$2" pf
  pf=$(profile_file "$wt")
  [ -n "$pf" ] && [ -f "$pf" ] || return 0
  grep -E "^[[:space:]]*$key:" "$pf" 2>/dev/null \
    | head -1 \
    | sed -E 's/^[^:]*:[[:space:]]*//; s/[[:space:]]*$//; s/^["'\'']//; s/["'\'']$//'
}

# capitalize <word>  — first letter upper, rest unchanged (portable, no ${^}).
_dm_capitalize() {
  local s="$1"
  [ -z "$s" ] && return 0
  printf '%s%s\n' "$(printf '%s' "${s%"${s#?}"}" | tr '[:lower:]' '[:upper:]')" "${s#?}"
}

# resolved_test_task [<worktree_root>]
#   Echoes the android gradle unit-test TASK NAME the repo should run:
#     1. profile `test_task`               (explicit, wins)
#     2. derived from profile `test_variant` as test${Variant^}UnitTest
#     3. empty → caller uses its platform default (testDebugUnitTest)
#   Empty output means "no override" so callers keep their existing default.
resolved_test_task() {
  local wt="$1" task variant
  task=$(profile_value test_task "$wt")
  [ -n "$task" ] && { printf '%s\n' "$task"; return 0; }
  variant=$(profile_value test_variant "$wt")
  [ -n "$variant" ] && printf 'test%sUnitTest\n' "$(_dm_capitalize "$variant")"
}

# resolved_android_test_task [<worktree_root>]
#   Like resolved_test_task but ALWAYS echoes a usable task name, falling back
#   to the platform default `testDebugUnitTest`. Use where a concrete task is
#   required (e.g. building the `./gradlew :module:<task>` command line).
resolved_android_test_task() {
  local t
  t=$(resolved_test_task "$1")
  [ -n "$t" ] && printf '%s\n' "$t" || printf 'testDebugUnitTest\n'
}

# known_flaky_tests [<worktree_root>]
#   Echoes the profile's known_flaky_tests entries, one per line (empty if
#   none). Accepts the YAML inline-list form `[A#x, B#y]` and the block-list
#   form (subsequent `  - A#x` lines). Entries are emitted verbatim, trimmed.
known_flaky_tests() {
  local wt="$1" pf
  pf=$(profile_file "$wt")
  [ -n "$pf" ] && [ -f "$pf" ] || return 0
  # Inline form: known_flaky_tests: [A#x, B#y]
  local inline
  inline=$(grep -E '^[[:space:]]*known_flaky_tests:[[:space:]]*\[' "$pf" 2>/dev/null \
    | head -1 | sed -E 's/^[^[]*\[//; s/\][[:space:]]*$//')
  if [ -n "$inline" ]; then
    printf '%s\n' "$inline" | tr ',' '\n' \
      | sed -E 's/^[[:space:]]*//; s/[[:space:]]*$//; s/^["'\'']//; s/["'\'']$//' \
      | grep -v '^$'
    return 0
  fi
  # Block form: known_flaky_tests:\n  - A#x\n  - B#y
  awk '
    /^[[:space:]]*known_flaky_tests:[[:space:]]*$/ { inblk=1; next }
    inblk && /^[[:space:]]*-[[:space:]]*/ {
      line=$0
      sub(/^[[:space:]]*-[[:space:]]*/, "", line)
      gsub(/^["'\'']|["'\'']$/, "", line)
      gsub(/[[:space:]]+$/, "", line)
      if (line != "") print line
      next
    }
    inblk && /^[^[:space:]-]/ { inblk=0 }
  ' "$pf" 2>/dev/null
}
