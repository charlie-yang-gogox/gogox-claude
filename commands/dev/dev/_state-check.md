---
name: _state-check
description: "Internal validator — confirms `.dev/state.json` is in the expected stage and has the required fields populated for that stage. Every `/dev:<stage>` command must run this first to refuse out-of-order execution. Not user-facing; the `_` prefix marks it internal."
---

# `/dev:_state-check`

Strict precondition validator for the `/dev:*` atomic command pipeline. Refuses to let a stage run unless the state file says it's that stage's turn AND the inputs that stage needs are present.

**Usage**: `/dev:_state-check <expected-stage>`

`<expected-stage>` ∈ `{start, figma, detect, align, apply, verify, review, ship}`

`done_default` and `done` are terminal states — `_state-check` is never called against them by a stage's body. If a caller ever passes them, the validator falls through to the unknown-stage branch and FAILs.

## Behavior

1. Read `.dev/state.json`. Missing → FAIL.
2. Parse JSON. Invalid → FAIL.
3. Check `schema_version == 1`. Mismatch → FAIL.
4. Check `current_stage == <expected-stage>`. Mismatch → FAIL with a precise next-step hint.
5. Check that the per-stage required fields (per `/dev:_state-schema`) are populated. Missing → FAIL.
6. On success: print `OK` then the parsed state JSON to stdout. The calling stage parses this to load context.

On any FAIL, exit non-zero and surface the reason on stderr. The calling stage MUST stop on non-zero exit — do NOT proceed to its body.

## Implementation

Run this bash block exactly. Substitute `$EXPECTED` with the stage argument. Do NOT replace `jq` with hand-rolled parsing — `jq` is the contract; it must be available in the environment.

```bash
EXPECTED="$1"   # e.g. "verify"
STATE_FILE=".dev/state.json"

# 1. file existence
if [ ! -f "$STATE_FILE" ]; then
  echo "FAIL: $STATE_FILE not found. Run /dev:start <ticket-id> first." >&2
  exit 1
fi

# 2. JSON validity
if ! jq -e . "$STATE_FILE" > /dev/null 2>&1; then
  echo "FAIL: $STATE_FILE is not valid JSON. Inspect manually." >&2
  exit 1
fi

# 3. schema version
VERSION=$(jq -r '.schema_version // 0' "$STATE_FILE")
if [ "$VERSION" != "1" ]; then
  echo "FAIL: state.json schema_version=$VERSION, expected 1. See /dev:_state-schema for migration." >&2
  exit 1
fi

# 4. current_stage match (caller may bypass this with --force; this validator does not interpret flags)
CURRENT=$(jq -r '.current_stage // ""' "$STATE_FILE")
if [ "$CURRENT" != "$EXPECTED" ]; then
  echo "FAIL: current_stage=\"$CURRENT\", expected \"$EXPECTED\"." >&2
  echo "      Run /dev:$CURRENT first, or /dev:ff to chain, or /dev:<stage> --from $EXPECTED to reset." >&2
  exit 1
fi

# 5. per-stage required fields
case "$EXPECTED" in
  start)
    : # creator stage — no prior state required
    ;;
  figma)
    jq -e '.ticket_id and .mode' "$STATE_FILE" > /dev/null \
      || { echo "FAIL: figma requires ticket_id and mode." >&2; exit 1; }
    ;;
  detect)
    jq -e '.change_name and .platform' "$STATE_FILE" > /dev/null \
      || { echo "FAIL: detect requires change_name and platform." >&2; exit 1; }
    ;;
  align)
    jq -e '.figma.receipt and (.openspec.state == "B" or .openspec.state == "C") and .openspec.change_dir' "$STATE_FILE" > /dev/null \
      || {
        echo "FAIL: align requires figma.receipt, openspec.state in {B,C}, and openspec.change_dir." >&2
        echo "      If this run has no Figma source (--no-figma at /dev:start, or no URL in ticket)," >&2
        echo "      align is not the right stage. Advance to apply directly: /dev:apply --from apply" >&2
        echo "      then run from there." >&2
        exit 1
      }
    ;;
  apply)
    jq -e '.change_name and .openspec.state' "$STATE_FILE" > /dev/null \
      || { echo "FAIL: apply requires change_name and openspec.state." >&2; exit 1; }
    ;;
  verify)
    jq -e '.base_ref and .change_name' "$STATE_FILE" > /dev/null \
      || { echo "FAIL: verify requires base_ref and change_name." >&2; exit 1; }
    ;;
  review)
    jq -e '.verify.status == "CLEAR"' "$STATE_FILE" > /dev/null \
      || { echo "FAIL: review requires verify.status == \"CLEAR\"." >&2; exit 1; }
    ;;
  ship)
    jq -e '.verify.status == "CLEAR"' "$STATE_FILE" > /dev/null \
      || { echo "FAIL: ship requires verify.status == \"CLEAR\"." >&2; exit 1; }
    if [ "$(jq -r '.mode' "$STATE_FILE")" = "auto" ]; then
      jq -e '.worktree_path' "$STATE_FILE" > /dev/null \
        || { echo "FAIL: ship in auto mode requires worktree_path." >&2; exit 1; }
    fi
    ;;
  *)
    echo "FAIL: unknown stage \"$EXPECTED\"." >&2
    exit 1
    ;;
esac

# 6. emit OK + state for caller
echo "OK"
jq . "$STATE_FILE"
```

## How calling stages should use this

Every `/dev:<stage>` command starts with:

```markdown
## Step 0: Validate state

Run `/dev:_state-check <stage>`. If exit code != 0, surface stderr to the user and STOP. Do NOT proceed.

If exit code == 0, parse the JSON state from stdout and use it as the source of truth for `ticket_id`, `change_name`, `base_ref`, `mode`, etc. Do NOT re-derive these from Linear or git — that is the responsibility of `/dev:start`.
```

## What this validator does NOT do

- Does not interpret `--force` or `--from <stage>`. The calling stage decides whether to skip the strict check.
- Does not write to state. State mutations happen in the calling stage's body, after the stage's work succeeds.
- Does not validate downstream invariants (e.g. "verify-pass.md exists on disk"). It only checks the JSON structure of `state.json`.
- Does not run side effects, network calls, or fetch external resources.

## Common failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `state.json not found` | Pipeline not started, or wrong cwd | `/dev:start <ticket-id>` from the worktree root |
| `current_stage="X", expected "Y"` | User invoked an out-of-order stage | Either run the missing stage(s), or `/dev:Y --from Y` to reset |
| `schema_version=N, expected 1` | State written by a different pipeline version | Migrate or restart the pipeline |
| `<stage> requires <field>` | Prior stage didn't write its outputs | Re-run the prior stage with `--force` |
