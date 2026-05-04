#!/usr/bin/env bash
# monthly-summary setup script
# Usage:
#   bash setup.sh --check-env              # JSON report of available tools
#   bash setup.sh --write-config '{...}'   # Write config JSON to disk
#   bash setup.sh --get-config             # Read existing config (if any)
#   bash setup.sh --version                # Print schema version

set -euo pipefail

CONFIG_PATH="${HOME}/.claude/monthly-summary-config.json"
SCHEMA_VERSION=1

# ── helpers ──────────────────────────────────────────────────────────────

# Escape a string for safe embedding in JSON.
# Uses python3 if available, otherwise strips double quotes.
json_escape_string() {
  local raw="$1"
  if command -v python3 &>/dev/null; then
    printf '%s' "$raw" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()), end='')"
  else
    # Fallback: escape backslashes and double quotes
    raw="${raw//\\/\\\\}"
    raw="${raw//\"/\\\"}"
    printf '"%s"' "$raw"
  fi
}

# ── check-env ────────────────────────────────────────────────────────────

check_env() {
  local python3_ok=false
  local python3_path=""
  local gh_ok=false
  local parse_py_ok=false
  local parse_py_path="${HOME}/.claude/skills/daily-summary/parse.py"

  if command -v python3 &>/dev/null; then
    python3_ok=true
    python3_path="$(command -v python3)"
  fi

  if command -v gh &>/dev/null; then
    gh_ok=true
  fi

  if [[ -f "$parse_py_path" ]]; then
    parse_py_ok=true
  fi

  local config_exists=false
  if [[ -f "$CONFIG_PATH" ]]; then
    config_exists=true
  fi

  # Escape paths for safe JSON embedding
  local escaped_python3_path
  escaped_python3_path="$(json_escape_string "$python3_path")"

  local escaped_parse_py_path
  escaped_parse_py_path="$(json_escape_string "$parse_py_path")"

  local escaped_config_path
  escaped_config_path="$(json_escape_string "$CONFIG_PATH")"

  cat <<ENDJSON
{
  "python3": { "available": $python3_ok, "path": $escaped_python3_path },
  "gh_cli": { "available": $gh_ok },
  "parse_py": { "available": $parse_py_ok, "path": $escaped_parse_py_path },
  "config_exists": $config_exists,
  "config_path": $escaped_config_path
}
ENDJSON
}

# ── write-config ─────────────────────────────────────────────────────────

write_config() {
  local json="$1"
  local validated=false

  # Validate JSON before writing
  if command -v python3 &>/dev/null; then
    if echo "$json" | python3 -m json.tool > /dev/null 2>&1; then
      validated=true
    fi
  elif command -v jq &>/dev/null; then
    if echo "$json" | jq . > /dev/null 2>&1; then
      validated=true
    fi
  fi

  if [[ "$validated" == false ]]; then
    if command -v python3 &>/dev/null || command -v jq &>/dev/null; then
      echo "Error: invalid JSON provided" >&2
      exit 1
    fi
    # Neither python3 nor jq available — write with warning
    echo "Warning: could not validate JSON (no python3 or jq). Writing as-is." >&2
  fi

  mkdir -p "$(dirname "$CONFIG_PATH")"
  echo "$json" > "$CONFIG_PATH"
  echo "Config written to $CONFIG_PATH"
}

# ── get-config ───────────────────────────────────────────────────────────

get_config() {
  if [[ -f "$CONFIG_PATH" ]]; then
    cat "$CONFIG_PATH"
  else
    echo "{}"
  fi
}

# ── main ─────────────────────────────────────────────────────────────────

case "${1:-}" in
  --check-env)    check_env ;;
  --write-config) write_config "${2:?missing JSON argument}" ;;
  --get-config)   get_config ;;
  --version)      echo "$SCHEMA_VERSION" ;;
  *)
    echo "Usage: bash setup.sh [--check-env | --write-config '{...}' | --get-config | --version]" >&2
    exit 1
    ;;
esac
