---
name: verify
description: "Stage 6 — run tests, spawn the independent verify-agent to audit the diff, format/lint, sanitize the .dev/ runtime artifacts from the index, and commit. Gates the commit on Status: CLEAR from the auditor. Auto mode only."
---

# `/dev:verify`

Test → audit → format → commit. The verify-agent is the load-bearing piece: an independent (sonnet) auditor that re-greps stale call sites of every renamed/removed identifier in the diff. Same-session self-audit cannot catch the misses it produced — that's the whole reason this stage exists separately.

## Inputs

- `.dev/state.json` (read for `base_ref`, `change_name`, `mode`, `figma.receipt`).
- The current working tree's diff vs `base_ref`.
- Project profile (for `{test_cmd}`).

## Outputs

- Tests pass (or noted as failing in `state.verify`).
- `.dev/verify-pass.md` with `Status: CLEAR`.
- `.gitignore` updated to include `.dev/`; any pre-tracked `.dev/` paths evicted from index.
- A commit on the current branch (excluding `.dev/`).
- `state.verify = { status: "CLEAR", report: ".dev/verify-pass.md", retry_count: 0|1 }`.
- `state.current_stage = "review"`.

## Step 0: Validate state

Run `/dev:_state-check verify`. STOP on non-zero. Parse for `base_ref`, `change_name`, `mode`, `figma`. This stage requires `mode == auto` — refuse if default (default-mode users drive verify manually).

## Step 1: Run tests

Run `/check-test --fix` if available for `{platform}`, else fall back to `{test_cmd}` directly. Auto-fix failures.

If still failing after fix attempts, note in `state.verify.test_status = "failed"` and proceed (the verify-agent + reviewer can still catch issues; abort decision is downstream).

## Step 2: Spawn verify-agent

Use the **Agent** tool with `subagent_type: "verify-agent"`, `mode: "bypassPermissions"`. Prompt with the three required inputs:

- `base` — `state.base_ref` (e.g. `origin/trunk`)
- `change name` — `state.change_name`
- `figma raw directory` — `state.figma.raw_dir` if present (typically `.dev/figma-raw/`), else omit

**Pass the raw dir, not the receipt path.** The auditor must read `.dev/figma-raw/*.json` directly, not `.dev/figma-context.md`. The receipt is a curated summary written by the implementing pipeline — sharing it with the auditor would re-converge auditor and implementer onto the same filtered view, defeating the whole point of the split. The receipt is referenced internally by verify-agent only for sha256 cross-check.

After the agent returns, read `.dev/verify-pass.md`:

- **`Status: CLEAR`** → proceed to Step 3.
- **`Status: BLOCKED`** → run BLOCKED recovery (Step 2a).
- **File missing** → treat as `BLOCKED`. Do NOT fall back to "trust the implementer" — absence of report IS the failure signal.

### Step 2a: BLOCKED recovery sequence (executed once)

1. For each finding in `.dev/verify-pass.md`, edit the affected files to address it.
2. Re-run `/check-test --fix` (or `{test_cmd}`) to confirm fixes did not regress tests.
3. Re-spawn `verify-agent` with the same inputs.
4. Read `.dev/verify-pass.md` again:
   - `Status: CLEAR` → proceed to Step 3. Set `state.verify.retry_count = 1`.
   - `Status: BLOCKED` (still) → ABORT. Write the verify report path into `state.verify`, set `state.current_stage` unchanged, append `{stage: "verify", status: "failed", ts, reason: "BLOCKED after retry"}` to history. STOP — do not loop further.

## Step 3: Format

Run `/format`. If lint issues remain that `/format` cannot auto-fix, fix them. If `/format` made changes, return to Step 1 (re-run tests) before committing.

## Step 4: Sanitize the index

```bash
# Add .dev/ to .gitignore if not already listed
if ! grep -qxF '.dev/' .gitignore 2>/dev/null; then
  printf '\n# /dev:* runtime artifacts (proof-of-work, not source)\n.dev/\n' >> .gitignore
fi

# Evict any already-tracked .dev/ paths from the index (one-time cleanup)
git ls-files .dev/ 2>/dev/null | xargs -r git rm --cached -- 2>/dev/null || true
```

`.dev/figma-context.md`, `.dev/figma-raw/**`, `.dev/state.json`, and `.dev/verify-pass.md` are runtime artifacts, not source. The previous commit's `.gitignore` does not retroactively untrack files; this eviction is required.

## Step 5: Commit

Run `/commit` to commit all changes. The commit must include the `.gitignore` update and the source diff, but exclude every path under `.dev/`.

## Step 6: Commit transition

```bash
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# RETRIES = 1 if Step 2a's BLOCKED-recovery sequence ran (regardless of outcome here),
# else 0. The orchestrator tracks this in a local shell variable set when entering 2a;
# default to 0 if the variable is unset.
RETRIES="${RETRIES_RAN:-0}"

jq --arg ts "$TS" --argjson r "$RETRIES" '
  .verify = { status: "CLEAR", report: ".dev/verify-pass.md", retry_count: $r }
  | .current_stage = "review"
  | .stage_history += [{ stage: "verify", status: "done", ts: $ts, result: "CLEAR" }]
' .dev/state.json > .dev/state.json.tmp && mv .dev/state.json.tmp .dev/state.json
```

In Step 2a's recovery sequence, set `RETRIES_RAN=1` before re-spawning the agent. If recovery is not entered, the variable stays unset and the default `0` applies.

Note: `.dev/state.json` itself is gitignored after Step 4, so this write happens after `/commit` — the state update lives only on the local working tree, which is correct (state is per-pipeline-run, not per-branch).

## Step 7: Stop

Print: `Verify CLEAR. Commit created. Next: /dev:review.`
