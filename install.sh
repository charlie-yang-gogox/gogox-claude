#!/usr/bin/env bash
# gogox-claude installer
#
# Usage: ./install.sh
#
# Installs every category (shared, pm, dev, design). Folder split inside the
# repo is for organization only — onboarding shouldn't have to decide.
# Skills, agents, and commands are symlinked (not copied) so `git pull`
# updates everything instantly — no need to re-run install after pulling.

set -eo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS_DIR="$HOME/.claude/skills"
AGENTS_DIR="$HOME/.claude/agents"
COMMANDS_DIR="$HOME/.claude/commands"

mkdir -p "$SKILLS_DIR" "$AGENTS_DIR" "$COMMANDS_DIR"

CATEGORIES=("shared" "pm" "dev" "design")

INSTALLED_SKILLS=()
INSTALLED_AGENTS=()
INSTALLED_COMMANDS=()
INSTALLED_PROFILES=()
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
      rm -rf "$SKILLS_DIR/$skill_name"
      ln -s "${skill_path%/}" "$SKILLS_DIR/$skill_name"
      INSTALLED_SKILLS+=("$skill_name")
    done
  fi

  if [ -d "$src_agents" ]; then
    for agent_file in "$src_agents"/*.md; do
      [ -f "$agent_file" ] || continue
      agent_name="$(basename "$agent_file" .md)"
      rm -f "$AGENTS_DIR/$agent_name.md"
      ln -s "$agent_file" "$AGENTS_DIR/$agent_name.md"
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
      rm -f "$COMMANDS_DIR/$command_name.md"
      ln -s "$command_file" "$COMMANDS_DIR/$command_name.md"
      INSTALLED_COMMANDS+=("$command_name")
    done

    # Profiles: data files (yaml) consumed by commands at runtime. Symlinked
    # into ~/.claude/commands/profiles/ so commands can read them by a fixed path.
    # Non-.md so they do NOT register as slash commands.
    if [ -d "$src_commands/profiles" ]; then
      mkdir -p "$COMMANDS_DIR/profiles"
      while IFS= read -r p; do
        rel="${p#$src_commands/profiles/}"
        target_dir="$COMMANDS_DIR/profiles/$(dirname "$rel")"
        mkdir -p "$target_dir"
        rm -f "$COMMANDS_DIR/profiles/$rel"
        ln -s "$p" "$COMMANDS_DIR/profiles/$rel"
        INSTALLED_PROFILES+=("$rel")
      done < <(find "$src_commands/profiles" -type f \( -name '*.yaml' -o -name '*.yml' -o -name '*.json' -o -name '*.toml' \))
    fi
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
echo " commit: $COMMIT"
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

if [ "${#INSTALLED_PROFILES[@]}" -gt 0 ]; then
  echo "Profiles (${#INSTALLED_PROFILES[@]}) — read at runtime by commands:"
  for p in "${INSTALLED_PROFILES[@]}"; do
    echo "  ~/.claude/commands/profiles/$p"
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

if [ "${#INSTALLED_SKILLS[@]}" -eq 0 ] && [ "${#INSTALLED_AGENTS[@]}" -eq 0 ] && [ "${#INSTALLED_COMMANDS[@]}" -eq 0 ] && [ "${#INSTALLED_PROFILES[@]}" -eq 0 ]; then
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
echo "  cd $(basename "$REPO_DIR") && git pull"
echo "  (skills are symlinked — git pull updates them instantly)"
echo
