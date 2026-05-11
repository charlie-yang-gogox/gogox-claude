# ff state rationalization — filesystem-as-state plan (v7)

**Status**: Design v8 (May 2026). v8 simplifies two redundancy areas the user flagged: (a) `/port:ship` resume marker degraded to single-payload format (no section-aware helper, no partial-success idempotency layer) — description-fails-first ordering means only one payload is ever in flight, so sections were unnecessary; (b) `--auto` mid-apply clarification protocol replaced with fail-fast + user-re-runs-in-default-mode (no `.dev/apply-clarifications.jsonl`, no answer-channel re-spawn dance — see `dev-ff-subagent-isolation.md` v8 §3.6). Other v7 closures retained: walker malformed-marker → STOP (§4), verify path pinned to `.dev/verify-pass.md` (§3.2), Step 0 malformed-file → STOP (§6).
**Owner**: Charlie
**Trigger**: User asked whether `state.json` and the various runtime files are all genuinely necessary. Iterations across v1 and v2 attempted to consolidate the schema; v3 abandons the schema entirely after recognizing that **OpenSpec's filesystem-as-state pattern already solves what state.json was trying to do**, with substantially less complexity.

This plan replaces v1 (schema consolidation) and v2 (schema v2 + lock pipeline discrimination + delete-and-rebuild fallback). It also obsoletes `_state-check` and `_state-schema` skills.

The companion plan `dev-ff-subagent-isolation.md` (v3) consumes the stage definitions in §3 below — subagent result files double as stage markers.

---

## 1. Why eliminate state.json

Each iteration of the schema approach generated new problems:

| v1/v2 attempted | Problem it created |
|---|---|
| Bumping schema_version | Migration story for in-flight worktrees; users hit `_state-check` FAIL on upgrade day |
| `mode` field | Default vs auto persisted vs. per-invocation conflict; "default → auto upgrade is permitted, auto → default forbidden" rule |
| `pipeline: port \| dev` field | Lock pipeline discrimination; cross-pipeline state mutation rules |
| `stage_history[]` | `--from` rewind semantics + duration accounting + audit trail truncation |
| Atomic temp+mv writes on every transition | Race-free but adds bookkeeping at every stage |
| Subagent return contract feeding state.json | Parser fragility, "subagents must not write state.json" enforcement, single-writer arbitration |

In every case the underlying constraint was real (resume across sessions, dispatcher concurrency, HITL pauses) but the chosen solution (a typed JSON state file) imported its own complexity. **OpenSpec demonstrates that filesystem artifacts already encode pipeline progress**; we adopt that pattern.

## 2. The OpenSpec design philosophy we adopt

Per OpenSpec's "fluid not rigid":

1. **File-based state** — pipeline progress lives in directories and Markdown artifacts (`proposal.md`, `tasks.md`, `design.md`, etc.). No memoized JSON state.
2. **Read before act** — every stage's first action is reading the current artifact set; if preconditions are absent, the stage refuses.
3. **Checkbox-driven sub-tasks** — within a stage that has many sub-steps (notably `/dev:apply` via `tasks.md`), `[ ]` / `[x]` provides fine-grained progress that survives across sessions and crashes naturally.

These three together replace `state.json`, `_state-check`, `stage_history`, and the schema-versioning concerns.

## 3. Stage definitions

Each stage has a **done marker** — a deterministic on-disk artifact whose presence (and optionally content) proves "this stage's work is complete." `/dev:ff` and `/port:ff` infer the next stage by checking markers in dependency order.

### 3.1 Port pipeline

| Stage | Precondition (must exist) | Action (one-liner) | Done marker |
|---|---|---|---|
| `/port:start` | branch absent; Linear ticket has `ready-to-port` label | build worktree, scaffold openspec change, mutate Linear, write `.port/prd.md` | `<worktree>/openspec/changes/<n>/proposal.md` (skeleton) AND `<n>/.port/` dir |
| `/port:explore` | proposal skeleton exists | spawn dev-consult-agent → write dev-notes | `<n>/.port/dev-notes.md` exists with `## Locate` as first heading |
| `/port:plan` | dev-notes.md exists | spawn pm-agent ‖ designer-agent | both `<n>/.port/pm-notes.md` AND `design-notes.md` exist |
| `/port:synth` | three notes files exist | build context.md → spawn synth-agent → openspec validate → /spec-lint | all of `<n>/{proposal,design,tasks,specs/*/spec}.md` exist AND `<n>/.port/synth-report.md` exists |
| `/port:revise` | synth-report.md has findings | HITL/auto loop on artifacts + cascade scan | `<n>/.port/synth-report.md` contains a line beginning with `Review approved` (appended by step 10) |
| `/port:ship` | revise marker present | commit, push, write Linear PRD/comment, transition label | `gh pr view <branch>` returns `OPEN` AND (auto only) Linear label includes `need-spec-review` |

### 3.2 Dev pipeline

| Stage | Precondition (must exist) | Action | Done marker |
|---|---|---|---|
| `/dev:start` | branch absent; `ready-to-dev` label on Linear ticket | build worktree, mutate Linear assignee/status, write `.dev/figma/.skipped` if ticket has no Figma URL **(see §3.5 — `/dev:start` is the SOLE writer)** | worktree exists |
| `/dev:figma` | worktree; ticket has Figma URL (no `.dev/figma/.skipped`) | spawn figma-subagent → fetch design context | `.dev/figma/receipt.md` exists AND `.dev/figma/raw/<node>.json` ≥ 1 |
| `/dev:detect` | worktree | `openspec status --change <n> --json`, classify A/B/C from artifacts[] | (no persistent marker — re-derived each run; cheap) |
| `/dev:align` | figma receipt OR `.dev/figma/.skipped`; openspec state B or C | spawn align-subagent | `.dev/align-result.md` contains `Status: CLEAR` OR `.dev/figma/.skipped` (no align needed) |
| `/dev:apply` | align done OR no Figma | apply-prep-subagent → main HITL gate → dev-agent runs `/opsx:apply` | `openspec list --json \| jq -e '.changes[] \| select(.name==$n) \| .completedTasks == .totalTasks and .totalTasks > 0'` |
| `/dev:verify` | apply done | `/check-test` → verify-agent → `/format` → `/commit` | `.dev/verify-pass.md` contains `Status: CLEAR` AND new commit on top of base_ref |
| `/dev:review` | verify CLEAR | `/code-review` against diff | `claude-reports/<id>/code-review.md` exists AND no `^critical:` line |
| `/dev:ship` | review clean | `/opsx:archive` → `/check-archive` → `/pull-request --draft` → Linear update | `openspec/changes/archive/<n>/` exists AND PR is OPEN AND Linear status is `In Review` |

Notes:
- **Real `openspec` JSON shape verified locally** (May 2026, openspec 1.3.1): `openspec status --change <n> --json` returns `{changeName, isComplete, applyRequires[], artifacts[]}`; `openspec list --json` returns `{changes: [{name, completedTasks, totalTasks, lastModified, status}]}`. v3's `.tasks_remaining` and `.state` paths were fictional — v4 uses real fields.
- **`.dev/figma/.skipped`** is a sentinel file written by `/dev:start` ONLY (v6 fix per ai-expert #4). Two writer cases: (a) Linear ticket has no Figma URL after parsing description + comments + attachments; (b) `--no-figma` flag is passed. `/dev:figma` MUST NOT create this sentinel — if figma-subagent receives an empty URL list, it returns `Status: FAILED` (precondition violation: figma stage was scheduled but had no work to do, indicating a `/dev:start` bug). The walker (§4) treats `.skipped` as equivalent to a completed figma stage for purposes of advancing to align.
- **`.dev/verify-pass.md`** keeps its existing path (was tentatively `verify.md` in v3; v4 keeps existing for minimal churn).

### 3.3 Why detect has no marker

`/dev:detect` classifies the openspec change as A/B/C based on which artifacts exist. The classification is itself a function of `openspec/changes/<n>/`'s contents — calling `openspec status --json` is cheap (~1 second, no network). Persisting the result would just be a stale cache.

### 3.4 Read-before-act at the stage level

Each stage's body begins with the precondition check:

```bash
# Example: /dev:align body
{ [ -f .dev/figma/receipt.md ] || [ -f .dev/figma/.skipped ]; } \
  || { echo "FAIL: figma stage not done (no receipt and no .skipped sentinel)"; exit 1; }

# Classify state A/B/C from real openspec status JSON
status_json=$(openspec status --change "$CHANGE_NAME" --json 2>/dev/null)
is_complete=$(echo "$status_json" | jq -r '.isComplete')
artifacts_ready=$(echo "$status_json" | jq -r '[.artifacts[].status] | map(select(. == "ready" or . == "complete")) | length')

if [ "$is_complete" = "true" ]; then state="B"
elif [ "${artifacts_ready:-0}" -gt 0 ]; then state="C"
else state="A"; fi

case "$state" in B|C) ;; *) echo "FAIL: align requires state B or C, got $state"; exit 1 ;; esac
```

~10 lines of shell per stage. No jq schema, no `_state-check.md`, no schema_version dance. **Important**: the jq paths use the verified real schema (`isComplete`, `artifacts[].status`), not fictional ones.

## 4. `/dev:ff` and `/port:ff` infer-stage function

Replaces the current state.json read + dispatch loop. **Critical rules** (per ai-expert v3 review):
- **Consume on existence**: when a marker file is present, decide based on its content; do NOT fall through. Falling through caused v3's "BLOCKED verify gets re-classified as apply" bug.
- **In-progress detection**: apply has both a "started" signal (any `[x]` in tasks.md) and a "done" signal (completedTasks == totalTasks). Walker must distinguish; otherwise mid-apply resume mis-routes.

```bash
infer_dev_stage() {
  local n="$CHANGE_NAME" id="$TICKET_ID" wt="$WORKTREE"

  # ship complete?
  if [ -d "$wt/openspec/changes/archive/$n" ] \
     && gh pr view "$id" --json state -q .state 2>/dev/null | grep -q OPEN; then
    echo done; return; fi

  # review complete?
  if [ -f "claude-reports/$id/code-review.md" ] \
     && ! grep -qiE '^critical:' "claude-reports/$id/code-review.md"; then
    echo ship; return; fi

  # verify exists? Consume — do NOT fall through.
  if [ -f "$wt/.dev/verify-pass.md" ]; then
    if grep -q '^Status: CLEAR' "$wt/.dev/verify-pass.md"; then echo review; return; fi
    if grep -q '^Status: BLOCKED' "$wt/.dev/verify-pass.md"; then echo verify; return; fi
    # malformed verify-pass.md — DO NOT silently re-run; that overwrites evidence.
    echo "FAIL: malformed .dev/verify-pass.md (no Status: CLEAR or BLOCKED line)" >&2
    echo "Inspect manually, or /dev:ff --from verify to discard and re-run." >&2
    return 1
  fi

  # apply complete? — real openspec list shape
  local tasks_done
  tasks_done=$(openspec list --json 2>/dev/null \
    | jq -e --arg n "$n" '.changes[] | select(.name==$n) | (.completedTasks == .totalTasks) and (.totalTasks > 0)' \
    > /dev/null 2>&1 && echo "yes")
  if [ "$tasks_done" = "yes" ]; then echo verify; return; fi

  # apply in progress? tasks.md exists with any [x] mark, but not all done
  if [ -f "$wt/openspec/changes/$n/tasks.md" ] \
     && grep -qE '^- \[x\]' "$wt/openspec/changes/$n/tasks.md"; then
    echo apply; return; fi

  # align complete (or skipped)?
  if [ -f "$wt/.dev/align-result.md" ] \
     && grep -q '^Status: CLEAR' "$wt/.dev/align-result.md"; then
    echo apply; return; fi

  # figma stage: receipt present OR explicitly skipped → align is next
  if [ -f "$wt/.dev/figma/receipt.md" ] || [ -f "$wt/.dev/figma/.skipped" ]; then
    echo align; return; fi

  # change scaffolded but figma not yet decided
  [ -d "$wt/openspec/changes/$n" ] && { echo figma; return; }

  echo start
}
```

Port version is structurally identical, with port markers (`dev-notes.md`, `pm-notes.md`, etc.). ~60 lines per pipeline, pure shell, no JSON state. Each check is a `test -f` or `grep -q` or single `openspec` call against verified-real JSON paths.

### 3.5 `.dev/figma/.skipped` writer ownership (v6)

**ONLY `/dev:start` writes `.dev/figma/.skipped`.** This is a hard rule, not a convention. If the sentinel is missing on a no-Figma ticket, `infer_dev_stage` advances to `figma`; figma-subagent then receives an empty URL list and returns `Status: FAILED` (precondition violation: figma stage scheduled with no work). Recovery: user re-runs `/dev:start` (or manually `touch .dev/figma/.skipped`) to repair the sentinel.

This rule prevents the v5 ambiguity (multiple stages might race to create the sentinel) and prevents silent skips (figma-subagent silently writing `.skipped` would mask a real bug in URL detection).

## 5. The `--from <stage>` semantic

Old: rewrite `state.json.current_stage` and truncate `stage_history`.

New: **delete the marker file(s) of `<stage>` and everything downstream**. The next `/dev:ff` re-runs from `<stage>` because its precondition is now unmet.

```bash
# /dev:ff --from align
rm -f .dev/align-result.md .dev/verify-pass.md
rm -rf claude-reports/$TICKET_ID/
# next /dev:ff infer_dev_stage → align
```

This is **simpler** than the v2 jq history-truncation logic, and intuitive (delete what you want redone).

**Race note**: if user runs `--from align` while another `/dev:ff` is mid-flight on the same worktree, the rm + concurrent writer race produces undefined behavior (`--from` intent may be lost). This is item #3 in the operator-discipline contract (§6). The orchestrator's `--help` text MUST include this warning:

```
--from <stage>   Reset the pipeline to <stage>. Removes the marker files of <stage>
                 and everything downstream; next run resumes from <stage>.
                 WARNING: do NOT use this while another /<cmd> is running on the
                 same worktree. Concurrent writers and the --from rm will race;
                 your --from intent may be silently lost.
```

## 6. No lock — explicit operator-discipline contract (v6)

v4 kept a lock as cross-process mutex. v5 removed it claiming "Linear label is the mutex." ai-expert v5 review correctly flagged that this claim is structurally wrong: `commands/dev/port/start.md:69-74` makes **three sequential** `mcp__claude_ai_Linear__save_issue` calls (label remove, status set, assignee set) — not a transactional CAS. Two concurrent `/port:start` invocations against the same ticket can both observe `ready-to-port`, both call `save_issue`, and only collide at `git worktree add`.

v6 accepts this honestly. The lock-removal is grounded not in a Linear-atomicity guarantee but in an **operator-discipline contract**:

### Operator contract (must be followed)

1. **Run only one `/ggx-dispatcher` batch at a time.**
2. **Do not manually invoke `/port:ff` or `/dev:ff` against a ticket that is currently in a dispatcher batch.**
3. **Do not run two flows on the same worktree from two terminals.**

These are user-controllable for a one-developer tool. If broken, the failure modes are:

| Violation | Failure mode | Recovery |
|---|---|---|
| Two dispatchers, overlapping ticket sets | Both `/port:start` proceed past the Linear writes; second `git worktree add` fails | Discard one branch + worktree; ticket Linear state is mutated (assignee = self, label removed) — re-set manually if needed |
| Manual `/port:ff` against an in-flight dispatcher ticket | Same as above | Same as above |
| Two flows on one worktree | Filesystem markers race; behavior undefined | `/dev:ff` resume from filesystem will pick a coherent stage; user kills one, re-runs |

**No data loss in any case** (git tracks code; `.dev/` files just get rewritten). Worst case is wasted compute and Linear cleanup.

### What `--force-takeover`, lock acquisition rules, port→dev handoff, etc. all reduce to

Nothing. They were defending against scenarios this contract excludes.

The dispatcher itself still has its own batch-level lock (`plans/ggx-dispatcher.md`) — that prevents two `/ggx-dispatcher` runs from overlapping at the dispatcher layer. That's contract item #1 enforced mechanically.

### `/port:ship` STOP-path resume — single-payload marker (v8 simplification)

`/port:ship` writes `.dev/ship-pending.md` when a Linear MCP write retry-exhausts (3× retry). The file holds **at most one** unsent payload — either description or comment, never both.

#### Why single-payload is sufficient

`/port:ship` writes description first, then comment. If description retry-exhausts, ship STOPs **before** comment is ever attempted — only one payload can be in flight. If description succeeds and comment retry-exhausts, only the comment payload needs persisting. There is no scenario where both are pending simultaneously, so the v7 section-aware helper / partial-success idempotency layer was defending against an unreachable state.

#### File format

Plain text. First line is a `KIND:` sentinel; remainder is the verbatim payload.

```
KIND: description
<full unsent description markdown — verbatim, including marker block, timestamps as captured>
```

or:

```
KIND: comment
<full unsent comment text>
```

#### Original write-on-failure path

```bash
# When Linear MCP write retry-exhausts in normal flow:
{
  echo "KIND: $kind"          # "description" or "comment"
  echo "$payload"
} > .dev/ship-pending.md.tmp \
  && mv .dev/ship-pending.md.tmp .dev/ship-pending.md
# STOP with "<kind> retry failed; payload preserved; re-run /port:ship later"
```

Atomic write via temp+mv. No helper function.

#### Step 0 spec (must be added to `commands/dev/port/ship.md`)

```
Step 0: Resume check
  if .dev/ship-pending.md does NOT exist:
    proceed to normal step 1-14

  if exists:
    read first line; expect "KIND: description" or "KIND: comment"
    if neither (malformed, e.g. user hand-edited):
      STOP with "malformed .dev/ship-pending.md (KIND line missing or unknown).
                 Inspect manually. Either repair the file or rm it and re-run
                 /port:ship from scratch (note: re-running will rebuild description
                 with a new timestamp; the saved payload's exact content will be lost)."
    payload = remainder of file

    retry the matching Linear MCP write with EXACT payload (do NOT rebuild from local state)
      on success: rm .dev/ship-pending.md; SKIP push (already done); proceed to step 14
      on retry-exhausted: STOP with "<kind> retry failed; payload preserved; re-run /port:ship later"
                          (file already on disk; do not rewrite — the existing one IS the saved payload)

    NOTE: if Step 0 was for description and it succeeds, Step 0 returns to NORMAL flow at step where comment is written. A second failure (now on comment) takes the original write-on-failure path above, overwriting ship-pending.md with KIND: comment.
```

#### Why this is safe

- Description-then-comment ordering is enforced by the script — no partial-success-with-both-pending state exists.
- After Step 0's description retry succeeds, control returns to the comment-write step. If that fails, the file is overwritten with the comment payload (single-writer, atomic) — there is no stale description payload to cleared incorrectly because the file rewrite is wholesale, not section-merging.
- Malformed-file STOP prevents silent corruption when a user has hand-edited the file.

## 7. What gets removed

| Surface | Disposition |
|---|---|
| `commands/dev/dev/_state-schema.md` | **DELETE** entirely |
| `commands/dev/dev/_state-check.md` | **DELETE** entirely |
| `state.json` reads in every stage body | **REMOVE**; replaced with 3–5 lines of shell precondition check |
| `state.json` writes in every stage body | **REMOVE**; stage's done marker is its natural artifact |
| `state.json` itself (any path) | Never written |
| `stage_history[]` | Gone — git log + result files cover audit |
| `mode` persistence | Gone — per-invocation flag (already adopted in v2) |
| `pipeline` schema field | Gone — pipeline = command name (`/port:ff` vs `/dev:ff`) |
| `schema_version` migration tooling | N/A — no schema |

## 8. What is kept (and why)

| Surface | Why kept |
|---|---|
| ~~`.dev/.lock`~~ | **REMOVED in v5** — Linear label state machine is the actual mutex; per-file safety via atomic writes |
| `.dev/figma/receipt.md` + `raw/` | Subagent IO + anti-hallucination provenance |
| `.dev/verify-pass.md` | verify-agent's structured return; doubles as `/dev:verify` done marker |
| `.dev/align-result.md` | align-subagent's structured return; doubles as `/dev:align` done marker |
| `.dev/ship-pending.md` | Linear MCP retry-failure resume marker (rare path) |
| Atomic temp+mv writes on result files | Safety against mid-write crash |
| `openspec/changes/<n>/.port/{dev,pm,design}-notes.md`, `prd.md`, `synth-report.md`, `context.md` | **Committed** — PR reviewer reads these on GitHub |
| Two-tier git semantics (`/.dev/` gitignored, `<n>/.port/` mostly committed) | Unchanged |

## 9. Migration — what changes per file

### Removed
- `commands/dev/dev/_state-schema.md` — DELETE
- `commands/dev/dev/_state-check.md` — DELETE

### Modified
For each `/dev:<stage>` and `/port:<stage>` file (~14 files):

1. **Step 0 (state validation)** — replace `Run /dev:_state-check <stage>` block with inline 3–5-line shell precondition check (per §3.4 example).
2. **State writes (formerly mutating state.json mid-stage and at end)** — REMOVE. The stage's natural artifact (e.g. `.dev/verify-pass.md` for verify) already encodes completion.
3. **Read of state for `ticket_id`/`change_name`/`mode`/etc.** — replace with derivation:
   - `ticket_id`: parse from branch name (`feat/CAF-207-...` → `CAF-207`)
   - `change_name`: read `openspec/changes/` directory listing (single dir per worktree by convention)
   - `mode`: from `$ARGUMENTS` (already per-invocation in v2)
   - `worktree_path`: `git rev-parse --show-toplevel`
   - `base_ref`: from `.gogox-claude.yaml` profile or hardcoded `origin/trunk`
   - `platform`: from profile

### `/dev:ff` and `/port:ff` orchestrators

- Replace `current_stage = jq -r '.current_stage' .dev/state.json` with `current_stage=$(infer_dev_stage)` (per §4).
- Replace mode-upgrade Step 0b mutation with: there is no persisted mode, so nothing to upgrade.
- Replace `--from` jq history rewrite with `rm -f` of the markers from `<stage>` onward (per §5).
- **Remove all lock-related code** (acquisition, payload writes, take-over rules) — per §6.

### `/dev:start` body sketch (v6 — addresses ai-expert #9)

After this rationalization plan lands, `/dev:start`'s tail (after worktree creation + Linear writes) looks like:

```bash
# Step N: legacy state.json cleanup (one-time safety net)
if [ -f .dev/state.json ]; then
  echo "INFO: legacy .dev/state.json detected; removing (filesystem-as-state model)" >&2
  rm -f .dev/state.json
fi

# Step N+1: figma sentinel — write .skipped if ticket has no Figma URL or --no-figma was passed
mkdir -p .dev/figma
has_figma_url=$(echo "$TICKET_BODY" | grep -cE 'figma\.com/(design|file|board|slides|make)/')
if [ "$NO_FIGMA_FLAG" = "1" ] || [ "$has_figma_url" -eq 0 ]; then
  : > .dev/figma/.skipped.tmp
  mv .dev/figma/.skipped.tmp .dev/figma/.skipped   # atomic
fi

# /dev:start has no other markers to write — worktree existence is its done marker.
```

The `.skipped` write is exclusive to `/dev:start`; no other stage may create this sentinel.

### `/ggx-dispatcher`

- Lock acquisition uses new payload format (§6). Cross-command STOP rule applies.
- No state.json reads/writes anywhere in dispatcher.

### `commands/dev/port/ship.md` Step 5 (sanitize index)

- `.port/.lock` is now `.dev/.lock` — already gitignored under whole-of-`.dev/`, no per-file gitignore needed.
- `.port/timings.jsonl` removed (already done in v2's timings dissolution; unchanged here — telemetry was optional).
- `.port/ship-pending.md` is now `.dev/ship-pending.md` — same gitignore-by-parent.

## 10. Acceptance checklist

- [ ] `_state-schema.md` and `_state-check.md` deleted; no remaining references in any skill body
- [ ] All 14 stage files (`/port:*` ×6, `/dev:*` ×8) have inline precondition checks; no `state.json` reads
- [ ] No file in the repo writes to `state.json`
- [ ] `infer_dev_stage` and `infer_port_stage` shell functions present in `/dev:ff` and `/port:ff` respectively
- [ ] `--from <stage>` documented as "delete markers from `<stage>` onward; next ff resumes there"
- [ ] **Lock-related code removed** — `/port:ship`, `/dev:start`, `/dev:ff`, `/port:ff`, `/ggx-dispatcher` no longer reference `.dev/.lock`. `/port:ship` STOP-path uses `.dev/ship-pending.md` exclusively as the resume marker.
- [ ] **Operator contract documented** in user-facing help / README per §6: dispatcher single-run, no manual override of in-flight tickets, no two-terminal-same-worktree
- [ ] **`/port:ship` Step 0 entry-point check + single-payload marker** implemented per §6:
  - Step 0 reads `.dev/ship-pending.md`, parses `KIND:` line, retries the matching Linear MCP write with EXACT saved payload, skips push when resuming
  - File holds at most one payload (`KIND: description` | `KIND: comment`); no section helper, no partial-success layer
  - Malformed file (missing/unknown `KIND:` line) → STOP with explicit message
  - Description-then-comment ordering verified: only one payload is ever pending at a time
- [ ] **`--from <stage>` help text warns** about the same-worktree race per §5
- [ ] **`/dev:start` is verified as SOLE writer of `.dev/figma/.skipped`** — grep `agents/`, `commands/dev/`: no other file writes the path
- [ ] On a target repo: `.gitignore` contains `.dev/`; no `state.json` ever appears in any commit
- [ ] PR reviewer can still read `.port/{dev,pm,design}-notes.md`, `context.md`, `synth-report.md`, `prd.md` on GitHub after `/port:ship` (consultative artifacts unchanged)
- [ ] On a fresh ticket end-to-end (`/port:ff <id>` followed by `/dev:ff <id>`): pipeline completes, no state.json created, all done markers correct
- [ ] On a mid-pipeline crash: `kill -9` an in-progress `/dev:ff`, then re-run `/dev:ff`. The infer function correctly identifies the resume point from filesystem markers; pipeline resumes without intervention.
- [ ] On `--from align`: `.dev/align-result.md`, `.dev/verify-pass.md`, and `claude-reports/<id>/` removed; next `/dev:ff` runs from align

## 11. Out of scope

- Telemetry / per-stage duration metrics (gone with `stage_history`; can be added as `.dev/metrics.jsonl` later if needed — orthogonal to this plan)
- Cached Linear ticket data (gone with state.json's `ticket_title` etc.; main session refetches when needed)
- Renaming `openspec/changes/<n>/.port/` to a more semantic name (cosmetic; follow-up)
- Subagent contract details (owned by `dev-ff-subagent-isolation.md` v3)

## 12. Cross-references

- `dev-ff-subagent-isolation.md` v3 — uses §3 stage markers; subagent result files double as markers
- `plans/port-centralization.md` — D16 atomic write pattern (kept), D22 telemetry (dropped)
- `plans/ggx-dispatcher.md` §10 Q8 — dispatcher's no-state-file decision; this plan extends the same pattern to the orchestrators
- OpenSpec philosophy reference — file-based state, read-before-act, checkbox-driven (see ticket comment for full quote)

## 13. Rejected alternatives (audit trail)

| Approach | Why rejected |
|---|---|
| **v1**: consolidate `.port/` and `.dev/` into single dir | Conflated git semantics — would have gitignored consultative notes from PR review |
| **v2**: schema v2 + delete-and-rebuild on schema mismatch + lock pipeline discriminator | Each schema field generated more rules (mode upgrade/downgrade, transition rule, schema migration); ai-expert flagged compounding complexity |
| **Lazy in-place v1→v2 migrator** in `_state-check` | Over-engineering for a one-user tool where worktrees are few and cheap to rebuild; rejected by user before v3 |
| **JSON-fenced subagent return contract** | Parser fragility; ai-expert flagged framing issues with mid-narration sentinels and quoted content. v3 sidesteps by making subagent result files filesystem markers (no chat-return parsing) |
| **Single `state.json` for both pipelines with `pipeline` field** | Forced cross-pipeline state mutation rules and lock pipeline-discriminator logic; v3 makes pipeline = command name and avoids the entire problem |
