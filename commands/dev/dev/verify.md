---
name: verify
description: "Stage 6 — run tests, spawn the independent verify-agent to audit the diff, format/lint, sanitize the .dev/ runtime artifacts from the index, and commit. Gates the commit on Status: CLEAR from the auditor. Auto mode only."
---

# `/dev:verify`

Test → audit → format → commit. The verify-agent is the load-bearing piece: an independent (sonnet) auditor that re-greps stale call sites of every renamed/removed identifier in the diff. Same-session self-audit cannot catch the misses it produced — that's the whole reason this stage exists separately.

## Inputs

- The current working tree's diff vs `base_ref`.
- Project profile (for `{test_cmd}`).
- `base_ref`, `change_name`, `figma_raw_dir` derived from environment.

## Outputs

- Tests pass (or noted as failing in the verify report).
- `.dev/verify-pass.md` with `Status: CLEAR`. **This file IS the stage's done marker.**
- `.gitignore` updated to include `.dev/`; any pre-tracked `.dev/` paths evicted from index.
- A commit on the current branch (excluding `.dev/`).

## Step 0: Inline precondition

```bash
WT=$(git rev-parse --show-toplevel)
MODE=$(echo "$ARGUMENTS" | grep -q -- '--auto' && echo auto || echo default)

# Pipeline mode: bug (no openspec) vs feature (openspec-driven).
# Resolved by pipe_mode (lib/dev-mode.sh); .dev/mode.md is written by
# /dev:start when --bug; absent ⇒ feature.
source "$HOME/.claude/lib/dev-mode.sh"
PIPE_MODE=$(pipe_mode "$WT")

# Resolve base_ref from profile
if [ -f "$WT/.gogox-claude.yaml" ]; then
  PLATFORM=$(yq -r '.platform' "$WT/.gogox-claude.yaml")
else
  PLATFORM=$(yq -r '.platform' "$HOME/.claude/commands/profiles/registry/$(basename "$WT").yaml")
fi
BASE_REF=$(trunk_ref)   # origin/<default branch> — trunk (flutter) or main (gogox-claude); see lib/dev-mode.sh

if [ "$MODE" != "auto" ] && [ "$PIPE_MODE" != "bug" ]; then
  echo "FAIL: /dev:verify requires --auto (or bug mode via /bug:ff). Default-mode feature pipelines terminate at /dev:apply." >&2
  exit 1
fi

if [ "$PIPE_MODE" = "bug" ]; then
  # Bug mode: no openspec change dir, no tasks.md. Sanity-check the human committed something.
  N=""
  COMMITS_AHEAD=$(git rev-list --count "$BASE_REF..HEAD" 2>/dev/null || echo 0)
  [ "$COMMITS_AHEAD" -gt 0 ] || {
    echo "FAIL: bug-mode /dev:verify requires at least one commit beyond $BASE_REF. Commit your fix first." >&2
    exit 1; }
else
  # Feature mode: require openspec change dir + completed tasks.
  N=$(ls "$WT/openspec/changes" 2>/dev/null | grep -v '^archive$' | head -1)
  [ -n "$N" ] || { echo "FAIL: no openspec change directory" >&2; exit 1; }
  tasks_done=$(openspec list --json 2>/dev/null \
    | jq -e --arg n "$N" '.changes[] | select(.name==$n) | (.completedTasks == .totalTasks) and (.totalTasks > 0)' \
    > /dev/null 2>&1 && echo "yes")
  [ "$tasks_done" = "yes" ] || { echo "FAIL: apply not complete (tasks not all [x]). Run /dev:apply first." >&2; exit 1; }
fi

# Figma raw dir is the auditor's input — pass it through if present
FIGMA_RAW=""
[ -d "$WT/.dev/figma-raw" ] && FIGMA_RAW="$WT/.dev/figma-raw/"
```

## Step 1: Run tests

Run `/check-test --fix` if available for `{platform}`, else fall back to `{test_cmd}` directly. Auto-fix failures.

If still failing after fix attempts, note in the eventual verify report and proceed (the verify-agent + reviewer can still catch issues; abort decision is downstream).

## Step 2: Spawn verify-agent

Use the **Agent** tool with `subagent_type: "verify-agent"`, `mode: "bypassPermissions"`. Prompt with the three required inputs:

- `base` — `$BASE_REF` (e.g. `origin/trunk` or `origin/main`)
- `change name` — `$N` if non-empty, else pass `(bug-mode: no openspec change)` so the auditor knows to skip openspec cross-checks and rely on diff + tests alone.
- `figma raw directory` — `$FIGMA_RAW` if non-empty, else omit

**Prompt-platform repos (`{platform}` = `prompt`, e.g. gogox-claude).** Also tell
the auditor the diff is prompts / markdown / bash / workflow JS, NOT app code — so
"changed identifiers / call sites" means slash-command names, `.dev/*` marker
paths, profile keys, and cross-file references (grep for stale references to
those, not code symbols). The "build" that confirms compilation is
`scripts/prompt-lint.sh`, already run in Step 1 via `{test_cmd}`. There are no
openspec artifacts on this platform (skill-edits use the bug lane), so pass the
`(bug-mode: no openspec change)` change-name form.

**Pass the raw dir, not the receipt path.** The auditor must read `.dev/figma-raw/*.json` directly, not `.dev/figma-context.md`. The receipt is a curated summary written by the implementing pipeline — sharing it with the auditor would re-converge auditor and implementer onto the same filtered view, defeating the whole point of the split. The receipt is referenced internally by verify-agent only for sha256 cross-check.

After the agent returns, read `.dev/verify-pass.md`:

- **`Status: CLEAR`** → proceed to Step 3.
- **`Status: BLOCKED`** → run BLOCKED recovery (Step 2a).

A **present** report is authoritative and is NEVER overridden by the Step 2b
fallback — only an **absent** report or an **errored / unavailable spawn**
triggers it. Distinguish the two failure shapes exactly as `/dev:figma` Step 4b:

- The `Agent` call itself **errors / is unavailable** — e.g. a nested level-2
  spawn inside a `/ggx-dispatcher`-spawned worker (opus nesting fails; see
  `ARCHITECTURE.md` "Nested-spawn constraint") → go straight to Step 2b (a
  retry is pointless — the depth limit will not change).
- The agent **returned but wrote no `.dev/verify-pass.md`** → retry the spawn
  once; still no file → Step 2b.

### Step 2b: Spawn-unavailable fallback (platform-gated — GGC-11, R3 revised)

`verify-agent` is the **decorrelation auditor** — it exists to be a *different*
context than the implementer — so it is NOT inlined as freely as the figma/align
sonnet spawns (R2). What happens on spawn-failure depends on `{platform}`
(`$PLATFORM`, resolved in Step 0):

- **`{platform}` = `prompt`** (e.g. gogox-claude — markdown / bash / workflow-JS
  diffs, already gated by the deterministic `scripts/prompt-lint.sh` run in
  Step 1): run the `agents/dev/verify-agent.md` contract **inline in this
  session** against the same inputs (`$BASE_REF`, `$N`, `$FIGMA_RAW`), then
  atomic-write `.dev/verify-pass.md` with the SAME `Status: CLEAR|BLOCKED`
  encoding so the walker needs zero changes. **Line 2 MUST carry this loud
  provenance banner** (a legal part of the report — downstream parsers read the
  `Status:` line, not line 2):

  ```
  Provenance: inline-self-audit — DECORRELATION LOST (Agent spawn unavailable in this session; the implementer audited its own diff). Acceptable ONLY on the prompt platform — diffs are tiny prose/bash and prompt-lint is the deterministic gate; NOT a substitute for the independent auditor on code platforms. See ARCHITECTURE.md R3.
  ```

  This is deliberately **not silent**: the GGC-2 dogfood run self-audited with no
  such marker, and making that invisible degradation visible is exactly why the
  banner exists. Proceed to Step 3 on `Status: CLEAR`; Step 2a on `BLOCKED`.

- **`{platform}` ∈ {`flutter`, `android`, `ios`} (or any non-`prompt` /
  unresolved platform)** — real code, large diffs, weaker deterministic gates:
  **NO inline fallback.** An inline verify would be the implementer auditing its
  own code, collapsing the decorrelation this stage exists to provide (the
  self-audit asymmetry). Spawn failure stays a **BLOCKED hard-fail** (an absent
  report is itself the BLOCKED signal — do NOT fall back to "trust the
  implementer"); the dispatcher's §6.2 fallback handles it from there. The
  decorrelation-preserving path for code platforms is the R4 `claude -p`
  headless auditor (a separate process, not a nested spawn) — documented in
  `ARCHITECTURE.md` R4, not yet wired. Inlining is opt-in per platform, never
  the silent default.

### Step 2a: BLOCKED recovery sequence (executed once)

1. For each finding in `.dev/verify-pass.md`, edit the affected files to address it.
2. Re-run `/check-test --fix` (or `{test_cmd}`) to confirm fixes did not regress tests.
3. Re-run the auditor with the same inputs — re-spawn `verify-agent`, or, if the spawn was unavailable and Step 2b's inline path produced the report, re-run that same inline audit (preserving its `Provenance:` banner).
4. Read `.dev/verify-pass.md` again:
   - `Status: CLEAR` → proceed to Step 3.
   - `Status: BLOCKED` (still) → ABORT. STOP. The walker will see `Status: BLOCKED` next iteration and refuse to advance until the report is fixed (or `/dev:ff --from verify` is used to discard and re-run).

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

`.dev/figma-context.md`, `.dev/figma-raw/**`, `.dev/verify-pass.md`, `.dev/align-result.md` are runtime artifacts, not source. The previous commit's `.gitignore` does not retroactively untrack files; this eviction is required.

## Step 5: Commit

Run `/commit` to commit all changes. The commit must include the `.gitignore` update and the source diff, but exclude every path under `.dev/`.

## Step 6: Stop

No state mutation. The done markers are: (1) `.dev/verify-pass.md` `Status: CLEAR`, and (2) a new commit on top of `$BASE_REF`. The walker advances to `review`.

Print: `Verify CLEAR. Commit created. Next: /dev:review.`
