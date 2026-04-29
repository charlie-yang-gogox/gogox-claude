#!/usr/bin/env bash
# gogox-claude installer
#
# Usage:
#   ./install.sh                # installs shared/ only
#   ./install.sh pm             # installs shared + pm
#   ./install.sh pm dev design  # installs everything
#
# `shared` is always installed. Re-run any time to update — overwrites
# matching skill folders in ~/.claude/skills/. Your local edits to a
# skill are kept until you re-run install for that category.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS_DIR="$HOME/.claude/skills"
AGENTS_DIR="$HOME/.claude/agents"
COMMANDS_DIR="$HOME/.claude/commands"

mkdir -p "$SKILLS_DIR" "$AGENTS_DIR" "$COMMANDS_DIR"

# Shared always included. Dedupe user args.
CATEGORIES=("shared")
for arg in "$@"; do
  case "$arg" in
    shared|pm|dev|design)
      [[ " ${CATEGORIES[*]} " == *" $arg "* ]] || CATEGORIES+=("$arg")
      ;;
    *)
      echo "warning: unknown category '$arg' (valid: shared pm dev design)" >&2
      ;;
  esac
done

INSTALLED_SKILLS=()
INSTALLED_AGENTS=()
INSTALLED_COMMANDS=()
COLLISIONS=()

for cat in "${CATEGORIES[@]}"; do
  src_skills="$REPO_DIR/skills/$cat"
  src_agents="$REPO_DIR/agents/$cat"
  src_commands="$REPO_DIR/commands/$cat"

  if [ -d "$src_skills" ]; then
    for skill_path in "$src_skills"/*/; do
      [ -d "$skill_path" ] || continue
      skill_name="$(basename "$skill_path")"
      [ "$skill_name" = "_template" ] && continue
      [ -z "$skill_name" ] && continue
      if [ -d "$SKILLS_DIR/$skill_name" ] && [[ " ${INSTALLED_SKILLS[*]:-} " == *" $skill_name "* ]]; then
        COLLISIONS+=("skill:$skill_name")
      fi
      # Explicit destination path so trailing slash on $skill_path doesn't
      # cause BSD cp to copy contents-only into $SKILLS_DIR.
      rm -rf "$SKILLS_DIR/$skill_name"
      cp -R "${skill_path%/}" "$SKILLS_DIR/$skill_name"
      INSTALLED_SKILLS+=("$skill_name")
    done
  fi

  if [ -d "$src_agents" ]; then
    for agent_file in "$src_agents"/*.md; do
      [ -f "$agent_file" ] || continue
      agent_name="$(basename "$agent_file" .md)"
      cp "$agent_file" "$AGENTS_DIR/"
      INSTALLED_AGENTS+=("$agent_name")
    done
  fi

  if [ -d "$src_commands" ]; then
    for command_file in "$src_commands"/*.md; do
      [ -f "$command_file" ] || continue
      command_name="$(basename "$command_file" .md)"
      if [ -f "$COMMANDS_DIR/$command_name.md" ] && [[ " ${INSTALLED_COMMANDS[*]:-} " == *" $command_name "* ]]; then
        COLLISIONS+=("command:$command_name")
      fi
      cp "$command_file" "$COMMANDS_DIR/"
      INSTALLED_COMMANDS+=("$command_name")
    done
  fi
done

if git -C "$REPO_DIR" rev-parse --short HEAD >/dev/null 2>&1; then
  COMMIT="$(git -C "$REPO_DIR" rev-parse --short HEAD)"
else
  COMMIT="unknown (not a git repo)"
fi

echo
echo "============================================"
echo " gogox-claude installed"
echo " commit:     $COMMIT"
echo " categories: ${CATEGORIES[*]}"
echo "============================================"
echo

if [ "${#INSTALLED_SKILLS[@]}" -gt 0 ]; then
  echo "Skills (${#INSTALLED_SKILLS[@]}):"
  for s in "${INSTALLED_SKILLS[@]}"; do
    echo "  /$s"
  done
  echo
fi

if [ "${#INSTALLED_AGENTS[@]}" -gt 0 ]; then
  echo "Agents (${#INSTALLED_AGENTS[@]}):"
  for a in "${INSTALLED_AGENTS[@]}"; do
    echo "  $a"
  done
  echo
fi

if [ "${#INSTALLED_COMMANDS[@]}" -gt 0 ]; then
  echo "Commands (${#INSTALLED_COMMANDS[@]}):"
  for c in "${INSTALLED_COMMANDS[@]}"; do
    echo "  /$c"
  done
  echo
fi

if [ "${#COLLISIONS[@]}" -gt 0 ]; then
  echo "WARN: name collisions across categories (later wins):"
  for c in "${COLLISIONS[@]}"; do
    echo "  $c"
  done
  echo
fi

if [ "${#INSTALLED_SKILLS[@]}" -eq 0 ] && [ "${#INSTALLED_AGENTS[@]}" -eq 0 ] && [ "${#INSTALLED_COMMANDS[@]}" -eq 0 ]; then
  echo "No skills, agents, or commands installed yet — repo skeleton mode."
  echo "Add a skill: cp -r _template skills/<category>/<skill-name>"
  echo
else
  echo "Try one:"
  count=0
  for s in "${INSTALLED_SKILLS[@]}"; do
    echo "  /$s"
    count=$((count + 1))
    [ "$count" -ge 3 ] && break
  done
  for c in "${INSTALLED_COMMANDS[@]}"; do
    [ "$count" -ge 3 ] && break
    echo "  /$c"
    count=$((count + 1))
  done
  echo
fi

echo "To update later:"
echo "  cd $(basename "$REPO_DIR") && git pull && ./install.sh ${CATEGORIES[*]}"
echo
