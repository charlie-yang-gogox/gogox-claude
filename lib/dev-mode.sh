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
# Echoes `bug` if .dev/mode.md exists and its first line is exactly `bug`.
# Echoes `feature` otherwise. Absent / unreadable / unexpected content all
# fall through to `feature` so unmarked legacy worktrees keep their
# existing behavior.
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
  else
    echo feature
  fi
}
