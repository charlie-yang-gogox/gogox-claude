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
#                      Detected dynamically — no marker file is written, so
#                      pre-existing worktrees pick it up on re-run with zero
#                      migration. OpenSpec-initialized repos always have
#                      `openspec/` committed at the root, so they can never
#                      misdetect into this branch.
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
  elif [ ! -d "$wt/openspec" ]; then
    echo feature-direct
  else
    echo feature
  fi
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
