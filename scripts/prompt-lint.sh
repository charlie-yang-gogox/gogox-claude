#!/usr/bin/env bash
# prompt-lint — deterministic verification for the gogox-claude "prompt" platform.
#
# This repo has no compiler: its artifacts are prompts (commands/*.md), markdown-
# embedded bash, and workflow scripts (workflows/*.js). prompt-lint is the "build"
# surrogate the dev pipeline runs as test_cmd
# (see commands/dev/profiles/platform/prompt.yaml).
#
# It is deliberately HIGH-SIGNAL — it only flags real defects, so a green run means
# something. It does NOT shellcheck every markdown bash block (those are fragments
# full of undefined vars — that would be all noise). Instead:
#   1. node --check     on changed *.js   — real JS syntax errors
#   2. bash -n          on changed *.sh   — real shell syntax errors
#   3. frontmatter lint on changed command/skill/agent *.md — must have name + description
#   4. footgun scan     inside ```bash blocks of changed *.md — macOS/portability traps
#                       (v1: `timeout`, the F1 class — GGC-2)
# shellcheck, if installed, additionally lints *.sh (optional — absent = skipped, not failed).
#
# Scope: default = files changed vs the default branch (committed + uncommitted +
# untracked). `--all` = every tracked file. Exit 1 on any ERROR; warnings never fail.
#
# Portable to macOS bash 3.2 (no associative arrays / mapfile).

set -uo pipefail

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "prompt-lint: not in a git repo" >&2; exit 2; }
cd "$ROOT"

MODE_ALL=0
[ "${1:-}" = "--all" ] && MODE_ALL=1

DEFAULT_BRANCH=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||')
[ -z "$DEFAULT_BRANCH" ] && DEFAULT_BRANCH=main

ERRORS=0
WARNS=0
err()  { printf 'ERROR  %s\n' "$*" >&2; ERRORS=$((ERRORS + 1)); }
warn() { printf 'WARN   %s\n' "$*" >&2; WARNS=$((WARNS + 1)); }
ok()   { printf 'ok     %s\n' "$*"; }

HAVE_SHELLCHECK=0; command -v shellcheck >/dev/null 2>&1 && HAVE_SHELLCHECK=1
HAVE_NODE=0;       command -v node       >/dev/null 2>&1 && HAVE_NODE=1

TMPLIST=$(mktemp); TMPERR=$(mktemp)
trap 'rm -f "$TMPLIST" "$TMPERR"' EXIT

# --- collect the file list ---------------------------------------------------
if [ "$MODE_ALL" = 1 ]; then
  git ls-files > "$TMPLIST"
else
  BASE=$(git merge-base HEAD "$DEFAULT_BRANCH" 2>/dev/null || true)
  {
    [ -n "$BASE" ] && git diff --name-only "$BASE"..HEAD
    git diff --name-only HEAD                    # uncommitted (staged + unstaged)
    git ls-files --others --exclude-standard     # untracked
  } | sort -u > "$TMPLIST"
fi

if [ ! -s "$TMPLIST" ]; then
  echo "prompt-lint: no changed files vs $DEFAULT_BRANCH — nothing to lint (use --all to lint everything)."
  exit 0
fi

# --- helpers -----------------------------------------------------------------
needs_frontmatter() {   # command (not profiles/), skill SKILL.md, or agent .md
  case "$1" in
    commands/*/profiles/*)                           return 1 ;;
    commands/*.md|commands/*/*.md|commands/*/*/*.md)  return 0 ;;
    skills/*/SKILL.md|skills/*/*/SKILL.md)            return 0 ;;
    agents/*.md|agents/*/*.md)                        return 0 ;;
  esac
  return 1
}

check_frontmatter() {
  local f=$1 fm
  # First line must be '---' (portable check — BSD sed has no `q1`).
  case "$(head -1 "$f" | tr -d '[:space:]')" in
    ---) : ;;
    *) err "$f: missing YAML frontmatter (file must start with '---')"; return ;;
  esac
  fm=$(awk 'NR==1{next} /^---[[:space:]]*$/{exit} {print}' "$f")
  printf '%s\n' "$fm" | grep -qE '^name:'        || err "$f: frontmatter missing 'name:'"
  printf '%s\n' "$fm" | grep -qE '^description:' || err "$f: frontmatter missing 'description:'"
}

# Footgun scan — only inside ```bash / ```sh fenced blocks. Prints "file:line: message".
# Extend the awk rules deliberately: each must be a genuine macOS/portability defect.
scan_footguns() {
  awk '
    /^[[:space:]]*```/ {
      if (inblk) inblk=0
      else if ($0 ~ /```(bash|sh)[[:space:]]*$/) inblk=1
      next
    }
    inblk && $0 ~ /(^|[;&|(]|&&|\|\|)[[:space:]]*timeout[[:space:]]+[0-9]/ {
      printf "%s:%d: `timeout` is not present on stock macOS — use a poll-loop or gtimeout fallback (GGC-2 / F1)\n", FILENAME, FNR
    }
  ' "$1"
}

# --- per-file checks ---------------------------------------------------------
while IFS= read -r f; do
  [ -z "$f" ] && continue
  [ -f "$f" ] || continue        # skip deletions
  case "$f" in
    *.js)
      if [ "$HAVE_NODE" = 0 ]; then warn "$f: node not installed — skipped JS syntax check"; continue; fi
      # Best-effort syntax pass. NOTE the two known limits, both deliberate:
      #   - `node --check` is shallow on ESM (`export …`) files — it catches gross errors
      #     (unbalanced braces) but not every ESM-specific one.
      #   - We do NOT force module-mode (`--input-type=module`): workflows/*.js are run by
      #     the Workflow harness inside a wrapper function, so they legitimately use
      #     top-level `return`/`await`. Module-mode would FALSE-POSITIVE on valid scripts —
      #     worse than a miss. Deep validation of workflow scripts is the dispatcher e2e.
      if node --check "$f" 2>"$TMPERR"; then ok "node --check  $f"
      else err "$f: JS syntax error"; sed 's/^/         /' "$TMPERR" >&2; fi
      ;;
    *.sh)
      if bash -n "$f" 2>"$TMPERR"; then ok "bash -n       $f"
      else err "$f: shell syntax error"; sed 's/^/         /' "$TMPERR" >&2; fi
      if [ "$HAVE_SHELLCHECK" = 1 ]; then
        shellcheck -S warning "$f" >"$TMPERR" 2>&1 || { warn "$f: shellcheck findings"; sed 's/^/         /' "$TMPERR" >&2; }
      fi
      ;;
    *.md)
      md_errs_before=$ERRORS
      needs_frontmatter "$f" && check_frontmatter "$f"
      fg=$(scan_footguns "$f")
      if [ -n "$fg" ]; then
        while IFS= read -r line; do [ -n "$line" ] && err "$line"; done < <(printf '%s\n' "$fg")
      fi
      # Emit a per-.md success line on a clean pass so a green run is self-
      # evidencing, matching the ok lines the .js / .sh branches print. Gated
      # on the ERRORS delta so an erroring file gets only its ERROR line(s).
      [ "$ERRORS" -eq "$md_errs_before" ] && ok "md-scan       $f"
      ;;
  esac
done < "$TMPLIST"

echo
echo "prompt-lint: $ERRORS error(s), $WARNS warning(s)  [shellcheck: $([ "$HAVE_SHELLCHECK" = 1 ] && echo present || echo absent)]"
[ "$ERRORS" -gt 0 ] && exit 1
exit 0
