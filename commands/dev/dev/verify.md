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

# Pipeline mode: bug / feature-direct (both: no openspec) vs feature
# (openspec-driven). Resolved by pipe_mode (lib/dev-mode.sh); .dev/mode.md
# is written by /dev:start when --bug; feature-direct is detected
# dynamically (no openspec/ dir — GGC-17).
source "$HOME/.claude/lib/dev-mode.sh"
PIPE_MODE=$(pipe_mode "$WT")

# Resolve base_ref from profile
if [ -f "$WT/.gogox-claude.yaml" ]; then
  PLATFORM=$(yq -r '.platform' "$WT/.gogox-claude.yaml")
else
  PLATFORM=$(yq -r '.platform' "$HOME/.claude/commands/profiles/registry/$(basename "$WT").yaml")
fi
BASE_REF=$(trunk_ref)   # origin/<default branch> — trunk (flutter) or main (gogox-claude); see lib/dev-mode.sh

if [ "$MODE" != "auto" ] && [ "$PIPE_MODE" = "feature" ]; then
  echo "FAIL: /dev:verify requires --auto (or a direct mode: bug via /bug:ff, feature-direct on no-OpenSpec repos). Default-mode feature pipelines terminate at /dev:apply." >&2
  exit 1
fi

if [ "$PIPE_MODE" != "feature" ]; then
  # Direct modes (bug / feature-direct): no openspec change dir, no tasks.md.
  # Sanity-check something was committed.
  N=""
  COMMITS_AHEAD=$(git rev-list --count "$BASE_REF..HEAD" 2>/dev/null || echo 0)
  [ "$COMMITS_AHEAD" -gt 0 ] || {
    echo "FAIL: direct-mode /dev:verify requires at least one commit beyond $BASE_REF. Commit your change first." >&2
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
- `change name` — `$N` if non-empty, else pass `(bug-mode: no openspec change)` so the auditor knows to skip openspec cross-checks and rely on diff + tests alone. (Same form for both direct modes — `feature-direct` runs also have no openspec change; the string is a contract with the auditor, do not vary it per mode.)
- `figma raw directory` — `$FIGMA_RAW` if non-empty, else omit

**Prompt-platform repos (`{platform}` = `prompt`, e.g. gogox-claude).** Also tell
the auditor the diff is prompts / markdown / bash / workflow JS, NOT app code — so
"changed identifiers / call sites" means slash-command names, `.dev/*` marker
paths, profile keys, and cross-file references (grep for stale references to
those, not code symbols). The "build" that confirms compilation is
`scripts/prompt-lint.sh`, already run in Step 1 via `{test_cmd}`. There are no
openspec artifacts on this platform (skill-edits ride the direct modes — bug, or
feature-direct per GGC-17), so pass the `(bug-mode: no openspec change)`
change-name form.

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
  **NO inline fallback** — an inline verify would be the implementer auditing
  its own code, collapsing the decorrelation this stage exists to provide (the
  self-audit asymmetry). Instead, run the **R4 headless auditor** (GGC-19): a
  separate-OS-process `claude -p` is naturally level-1, so the nested-spawn
  ban does not apply and the auditor keeps a genuinely separate context — both
  decorrelation properties preserved (`ARCHITECTURE.md` R4). Inlining stays
  opt-in per platform (`prompt` only, above), never the silent default.

  **Pre-flight — binary gate (no-regression path).** If the `claude` CLI is
  missing/unauthenticated, or the verify-agent contract file cannot be
  resolved, do NOT attempt the headless run and do NOT write any report:
  spawn failure stays today's **BLOCKED hard-fail** (an absent report is
  itself the BLOCKED signal — do NOT fall back to "trust the implementer");
  the dispatcher's §6.2 fallback handles it from there. This is the expected
  degradation on the cloud ggx-dev-agent lane, which may lack an
  authenticated `claude` binary — the headless path targets the local
  dispatcher lane.

  ```bash
  # --- R4 headless auditor (GGC-19) — code platforms only --------------------
  # Contract file: installed flat by install.sh; repo-local fallback covers
  # dev checkouts of gogox-claude itself.
  R4_CONTRACT="$HOME/.claude/agents/verify-agent.md"
  [ -f "$R4_CONTRACT" ] || R4_CONTRACT="$WT/agents/dev/verify-agent.md"

  if ! command -v claude >/dev/null 2>&1 || [ ! -f "$R4_CONTRACT" ]; then
    echo "FAIL: verify-agent spawn unavailable AND R4 headless auditor cannot run" >&2
    echo "      (claude binary or verify-agent contract missing). BLOCKED hard-fail —" >&2
    echo "      absent report is the BLOCKED signal; dispatcher §6.2 takes over." >&2
    exit 1
  fi

  # 1. Assemble the audit prompt into a temp file: the full verify-agent
  #    contract + the same three inputs Step 2 passes + the strict stdout
  #    contract the wrapper parses mechanically below.
  R4_DIR=$(mktemp -d "${TMPDIR:-/tmp}/r4-verify.XXXXXX")
  R4_PROMPT="$R4_DIR/prompt.md"
  R4_OUT="$R4_DIR/stdout.txt"
  {
    cat "$R4_CONTRACT"
    printf '\n---\n\n## Run inputs (from /dev:verify Step 2)\n\n'
    printf -- '- base: %s\n' "$BASE_REF"
    if [ -n "$N" ]; then printf -- '- change name: %s\n' "$N"; \
    else printf -- '- change name: (bug-mode: no openspec change)\n'; fi
    if [ -n "$FIGMA_RAW" ]; then printf -- '- figma raw directory: %s\n' "$FIGMA_RAW"; \
    else printf -- '- figma raw directory: (none)\n'; fi
    printf '\n## Output contract (headless run — parsed mechanically)\n\n'
    printf 'Work from the current directory (%s). Write .dev/verify-pass.md per the\n' "$WT"
    printf 'contract above. Your FINAL message MUST have, as its FIRST line, exactly\n'
    printf '`Status: CLEAR` or `Status: BLOCKED` and nothing else on that line.\n'
  } > "$R4_PROMPT"

  # 2. Headless spawn — separate OS process, naturally level-1 (R4). The
  #    auditor contract is filesystem-only (Bash/Glob/Grep/Read/Write), so
  #    claude -p's no-MCP limitation is irrelevant. Step 2b is only reached
  #    when no report exists, so the rm below can only clear stale partials.
  #    `exec` is load-bearing: it makes the backgrounded subshell BECOME the
  #    claude process, so $! is the real auditor PID — without it the
  #    watchdog's kill -9 would only reap the subshell wrapper, orphaning a
  #    live auditor that could overwrite the fail-closed BLOCKED report with
  #    CLEAR after the bound elapsed (a silent-CLEAR race).
  rm -f "$WT/.dev/verify-pass.md"
  ( cd "$WT" && exec claude -p \
      --permission-mode bypassPermissions \
      --model sonnet \
      < "$R4_PROMPT" > "$R4_OUT" 2>"$R4_DIR/stderr.txt" ) &
  R4_PID=$!

  # 3. Counter-bounded watchdog — no `timeout` (absent on stock macOS; the
  #    GGC-2 / F1 rule). Bound = R4_MAX_SECS wall clock, then hard-kill.
  R4_MAX_SECS=900
  R4_TIMED_OUT=0
  i=0
  while kill -0 "$R4_PID" 2>/dev/null; do
    if [ "$i" -ge "$R4_MAX_SECS" ]; then
      kill -9 "$R4_PID" 2>/dev/null
      R4_TIMED_OUT=1
      break
    fi
    sleep 5; i=$((i + 5))
  done
  wait "$R4_PID" 2>/dev/null

  # 4. Strict first-line parse — fail-closed. CLEAR requires BOTH the stdout
  #    first line `Status: CLEAR` AND a `Status: CLEAR` report on disk; every
  #    other shape (timeout, hard-kill, parse failure, missing or
  #    contradictory report) defaults to BLOCKED. Never silent CLEAR.
  R4_FIRST=$(head -n 1 "$R4_OUT" 2>/dev/null)
  R4_STATUS=BLOCKED
  R4_REASON=""
  if [ "$R4_TIMED_OUT" = "1" ]; then
    R4_REASON="headless auditor exceeded ${R4_MAX_SECS}s wall clock — hard-killed"
  elif [ "$R4_FIRST" = "Status: CLEAR" ] \
       && grep -q '^Status: CLEAR' "$WT/.dev/verify-pass.md" 2>/dev/null; then
    R4_STATUS=CLEAR
  elif [ "$R4_FIRST" = "Status: BLOCKED" ]; then
    R4_REASON="auditor returned BLOCKED"
  else
    R4_REASON="unparsable first line ('${R4_FIRST:-empty}') or stdout/report contradiction"
  fi

  # 5. Provenance — wrapper-injected on line 2 of verify-pass.md (never
  #    trusted to the auditor's memory; legal — downstream parsers read only
  #    the Status: line). If the report is missing or contradicts the
  #    fail-closed verdict, atomic-write a BLOCKED report so the walker has a
  #    deterministic marker.
  R4_PROV='Provenance: headless-r4-auditor — decorrelation PRESERVED via separate-process `claude -p --model sonnet` (Agent spawn unavailable in this session; see ARCHITECTURE.md R4 / GGC-19).'
  if [ "$R4_STATUS" = "BLOCKED" ] \
     && ! grep -q '^Status: BLOCKED' "$WT/.dev/verify-pass.md" 2>/dev/null; then
    {
      printf '# Verify pass — %s (R4 headless, fail-closed)\n' \
        "${N:-$(git -C "$WT" rev-parse --abbrev-ref HEAD)}"
      printf '%s\n\n' "$R4_PROV"
      printf '## Findings\n- R4 fail-closed: %s\n\n' "$R4_REASON"
      printf 'Status: BLOCKED\n'
    } > "$WT/.dev/verify-pass.md.tmp"
    mv "$WT/.dev/verify-pass.md.tmp" "$WT/.dev/verify-pass.md"
  elif ! grep -qF 'Provenance: headless-r4-auditor' "$WT/.dev/verify-pass.md" 2>/dev/null; then
    { head -n 1 "$WT/.dev/verify-pass.md"; printf '%s\n' "$R4_PROV"; \
      tail -n +2 "$WT/.dev/verify-pass.md"; } > "$WT/.dev/verify-pass.md.tmp"
    mv "$WT/.dev/verify-pass.md.tmp" "$WT/.dev/verify-pass.md"
  fi
  rm -rf "$R4_DIR"
  # ----------------------------------------------------------------------------
  ```

  `Status: CLEAR` → proceed to Step 3. `Status: BLOCKED` → Step 2a, whose
  re-audit re-runs this same headless path with the same inputs (the
  provenance line is re-injected by the wrapper on every pass).

### Step 2a: BLOCKED recovery sequence (executed once)

1. For each finding in `.dev/verify-pass.md`, edit the affected files to address it.
2. Re-run `/check-test --fix` (or `{test_cmd}`) to confirm fixes did not regress tests.
3. Re-run the auditor with the same inputs — re-spawn `verify-agent`; or, if the spawn was unavailable and Step 2b's inline path (prompt platform) produced the report, re-run that same inline audit (preserving its `Provenance:` banner); or, if Step 2b's R4 headless path (code platforms) produced it, re-run the same `claude -p` headless audit (the wrapper re-injects the `headless-r4-auditor` provenance line).
4. Read `.dev/verify-pass.md` again:
   - `Status: CLEAR` → proceed to Step 3.
   - `Status: BLOCKED` (still) → ABORT. STOP. The walker will see `Status: BLOCKED` next iteration and refuse to advance until the report is fixed (or `/dev:ff --from verify` is used to discard and re-run). Before stopping, append a local breadcrumb (GGC-23): run `/_file-followup verify-blocked summary="<ticket-id>: verify still BLOCKED after recovery" report=.dev/verify-pass.md signature="<ticket-id>:verify"`. It is fail-soft (never blocks the abort) and writes only the local gitignored `.ggx-followups/followups.md` — NO Linear ticket / GitHub.

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
