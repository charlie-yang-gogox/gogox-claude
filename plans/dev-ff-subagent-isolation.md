# `/dev:ff` subagent isolation plan (v9)

**Status**: Design v9 (May 2026). v9 **flips the mode-conditional `/dev:apply` execution** documented in v8 §3.6 after observing that `/ggx-dispatcher` dispatches `/dev:ff --auto` inside a `general-purpose` subagent, which cannot reliably nest-spawn the opus `dev-agent` (CAF-370 dispatcher run on 2026-05-11 blocked at apply for exactly this reason). New shape: `--auto` runs `/opsx:apply` inline in the caller's session (whether main or dispatcher subagent); `default` mode spawns `dev-agent` from main where the spawn is single-level and reliable. dev-agent now has a real live caller (default mode) instead of zero. See §3.6 v9 and §12 for full rationale.

**v8 history** (still relevant for archive: §3.6 v8 reverted v7's `.dev/apply-clarifications.jsonl` answer-channel — `--auto` mid-apply clarification now degrades to fail-fast + user re-runs `/dev:ff` in default mode where `AskUserQuestion` is natural. Other v7 closures (walker malformed-marker STOP, verify path pin) retained.
**Owner**: Charlie
**Trigger**: User asked whether `/dev:ff` puts too much work in the main session, and which stages should be moved into spawned subagents to save main-session context (and where applicable, model cost).

This is a paired plan with `ff-state-rationalization.md` v3. That plan eliminates `state.json` entirely; this plan describes which `/dev:*` stages become subagents. Subagent result files in this plan are also the **stage markers** referenced in the rationalization plan §3 — one artifact, two purposes.

---

## 1. Goal

Move heavy or right-sized work out of the main session:
- **Save main-session context** — Figma `get_design_context` payloads, `/opsx:apply` test loops, and align's full-receipt reads burn main context fast
- **Right-size models** — main session is opus by user default; tasks that don't need opus get downgraded to sonnet inside a subagent
- **Preserve HITL** — default mode's interactive review gates still work; user is never "stuck waiting on a subagent"

## 2. Discipline principles

Validated against the 6 existing subagents:

1. **Subagents do NOT write state outside their declared output lane.** Each subagent owns exactly one output path (or a guarded directory like synth-agent's `outputPath`). With state.json gone (per the rationalization plan), there is no shared state writer to worry about — the principle reduces to "stay in your lane."

2. **Each subagent has a single output lane** with explicit write-protection (path-escape guards, "do NOT edit X" clauses). The result file under `.dev/` is part of that lane (see §6).

3. **HITL interaction belongs in main session.** No subagent calls `AskUserQuestion`. Repo evidence: `synth-agent.md:98` ("**No questions.**"), `dev-consult-agent.md:50` ("**The orchestrator** will AskUserQuestion"), zero subagents declare `AskUserQuestion` in `tools:`. Subagents that hit a need-input branch return a sentinel status and the user resumes via `/dev:ff`.

### Enforcement: CI grep (two checks)

**Check A — no subagent writes state.json** (defensive; v4 line-level):

```bash
# Each line containing 'state.json' in any agent file must be in a prohibition context.
violations=$(grep -nE 'state\.json' agents/**/*.md \
  | grep -vE '(do NOT|never|MUST NOT|prohibited|refuses|stops at|read-only|does not (touch|write)).*state\.json')
[ -z "$violations" ] || { echo "FAIL state.json mentions: $violations"; exit 1; }
```

**Check B — each subagent's declared result-file path appears literally in its body** (v4, prevents silent path drift per ai-expert):

```bash
# Map: subagent file → expected result path (pinned in this plan §6)
declare -A pins=(
  [agents/dev/figma-subagent.md]=".dev/figma/result.md"
  [agents/dev/align-subagent.md]=".dev/align-result.md"
  [agents/dev/dev-agent.md]=".dev/apply-result.md"
  [agents/dev/verify-agent.md]=".dev/verify-pass.md"
)
fail=0
for f in "${!pins[@]}"; do
  grep -qF "${pins[$f]}" "$f" || { echo "FAIL: $f does not mention ${pins[$f]}"; fail=1; }
done
[ "$fail" -eq 0 ]
```

Without Check B, a subagent refactor changing its result path would silently break the orchestrator's stage marker check — walker would treat the stage as undone and re-run forever.

## 3. Per-stage decisions

### 3.1 `/dev:start` — stay inline (no subagent)

Tool dispatch only (Linear MCP, `/check-clean`, `/add-worktree`). Subagent overhead exceeds savings. **No change.**

### 3.2 `/dev:figma` — spawn `figma-subagent` (sonnet, worktree-isolated)

- **Why isolate**: per-node Figma MCP responses are large (hundreds of lines per node × multiple nodes).
- **Why sonnet**: receipt building is structured aggregation, not adversarial. Right-sized; downgrade from main-opus saves $.
- **Linear fetch stays in main**: main extracts URLs from already-fetched ticket data; subagent gets the URL list as input.
- **Subagent contract** (see §6 file format):
  - Inputs: `ticket_id`, list of figma URLs, worktree path
  - Output writes: `.dev/figma/raw/<node>.json` per node, `.dev/figma/receipt.md` (consolidated), **`.dev/figma/result.md`** (sentinel file)
  - The `result.md` file is also `/dev:figma`'s **done marker** per `ff-state-rationalization.md` §3.2

### 3.3 `/dev:detect` — stay inline

Cheap classification via `openspec status --json`. No marker needed (re-derived each run). **No change.**

### 3.4 `/dev:figma` ‖ `/dev:detect` parallelization (`--auto` only)

Both stages have disjoint outputs and no state.json contention (because state.json doesn't exist). True parallelism via Claude Code's multi-tool-call message support:

```
case current_stage in
  ...
  figma)
    if [[ $MODE == "auto" ]]; then
      # SAME message, TWO tool calls — actual parallelism.
      # detect's "tool call" is the openspec status Bash invocation;
      # main reasons over the JSON to classify A/B/C.
      
      SAME MESSAGE:
        Agent[figma-subagent](ticket_id, urls, worktree)
        Bash: openspec status --change <change-name> --json
      
      After both return:
        # figma side: subagent's .dev/figma/result.md is its done marker.
        # detect side: classify A/B/C from real schema:
        #   isComplete=true                           → state B
        #   any artifacts[].status in {ready,complete} → state C
        #   else                                       → state A
        # Neither side writes state.json (none exists).
      
      advance current_stage to align (re-inferred from filesystem on next iteration)
    else
      # default mode — sequential (preserves any state-A HITL gate UX in detect)
      Agent[figma-subagent](inputs)
      wait, verify .dev/figma/result.md present with Status: CLEAR
      Bash: openspec status --change <change-name> --json
      classify; if state-A and not --no-figma: AskUserQuestion (state-A gate in detect)
      advance to align
    fi
    ;;
  ...
esac
```

Race-safety: nothing concurrent writes the same file. figma-subagent writes `.dev/figma/*`; detect's `openspec status` is a stdout-only read. No lock needed for this concurrency.

Failure handling: if figma-subagent fails (its result.md says `Status: FAILED`, or the file isn't written and retry exhausts — see §6 failure modes), main records nothing (no state.json) and STOPs. `/dev:ff` resume re-infers stage as `figma` (no receipt.md present) and re-runs.

### 3.5 `/dev:align` — spawn `align-subagent` (sonnet)

- **Why isolate**: reading full receipt + full artifact prose is heavy; conclusion is short.
- **Why sonnet**: rubric-based grep + judgment. Sonnet right-sized.
- **No worktree isolation**: align is read-only on artifacts; only write-out is the result file (and conflict report on CONFLICT path).
- **Explicit prohibitions** (must appear in subagent prompt body):
  - **MUST NOT** call `/opsx:rebuild`, `/opsx:apply`, or any `/opsx:*` command
  - **MUST NOT** edit any file under `openspec/changes/`
  - **MUST NOT** write outside `.dev/align-result.md` and `claude-reports/<ticket_id>/figma-alignment.md`
- **Subagent contract**:
  - Inputs: receipt path, change-dir path, list of figma node IDs
  - Output writes: **`.dev/align-result.md`** (sentinel; doubles as `/dev:align` done marker), and on CONFLICT also `claude-reports/<ticket_id>/figma-alignment.md`
  - Conflict resolution is delegated to main session, which decides whether to call `/opsx:rebuild` or STOP per `--auto`/default mode

### 3.6 `/dev:apply` — mode-conditional execution (v9, flipped from v8)

`/dev:apply` is logically a single step but its **execution location depends on mode**:

| Mode | Artifact prep (state A/C) | HITL | Apply (`/opsx:apply`) |
|---|---|---|---|
| `--auto` | inline in current session | none — `--auto` is unattended by definition | inline in current session (no agent spawn) |
| `default` | inline in main session | `AskUserQuestion` (approve / revise / stop) | spawn `dev-agent` (opus, worktree-isolated) |

#### Why flipped in v9 (the headline change)

v8 had it the other way around: `--auto` spawned `dev-agent`; default ran inline. That worked when callers invoked `/dev:ff --auto` from a main session — main has the Agent tool and could spawn opus freely. It **broke** the moment `/ggx-dispatcher` started invoking `/dev:ff --auto` inside a `general-purpose` subagent (the dispatch wrapper for parallel ticket runs): the subagent could not nest-spawn opus `dev-agent`. CAF-370 (2026-05-11) was the first observed blocker — figma + align completed (sonnet subagents nest fine), but apply hit the limit and bailed.

v9 inverts the placement to honor a stricter invariant: **the heavy spawn happens at the level where the spawner is known to have full Agent capability** — main session, not nested. The user is in default mode → user is at the keyboard → user invoked from main → main spawns dev-agent fine. The dispatcher is in `--auto` → dispatcher subagent is the wrapper → no further nesting → apply runs inline at that level.

| Comparison | v8 (broke at dispatcher) | v9 (this plan) |
|---|---|---|
| `--auto` execution | spawn dev-agent (opus) | inline in caller's session |
| default execution | inline in main | spawn dev-agent (opus) from main |
| dev-agent live callers | 1 (`--auto` only) | 1 (`default` only) — same count, different mode |
| Dispatcher path | nested spawn fails | inline in dispatcher subagent ✓ |
| Main-direct `/dev:ff --auto` | isolated opus subagent | inline in main (main is already opus) |
| Main-direct `/dev:ff` default | inline in main (no isolation) | dev-agent isolation → ~30–80K saved |

#### Why dispatcher upgrades its subagent to `model: opus`

With v9, `--auto`'s `/opsx:apply` runs inline inside the dispatcher's `general-purpose` subagent. Without a model override that subagent would default to sonnet (or whatever general-purpose's default is), losing the opus reasoning quality that v8's spawn-dev-agent path guaranteed. `commands/dev/ggx-dispatcher.md` §5.3 v9 therefore sets `model: "opus"` on every spawn. Port tickets technically don't need opus, but a uniform spawn shape avoids drift.

Main-session direct callers of `/dev:ff --auto` already run opus by user default, so no extra hop is needed there.

#### Why ONLY apply is mode-conditional (figma + align stay subagent-isolated regardless of mode)

Same answer as v5/v8: **only apply has a meaningful HITL gate**, and only apply is heavy enough that we care about isolation. figma and align are sonnet subagents — nested sonnet spawn from `general-purpose` works (CAF-370 confirmed: figma and align both completed); only opus nesting from general-purpose hits the limit, which is exactly what we removed by flipping `--auto` to inline.

- `/dev:figma`: spawn figma-subagent (sonnet) in BOTH modes
- `/dev:align`: spawn align-subagent (sonnet) in BOTH modes
- `/dev:apply`: mode-conditional (only opus-class spawn in the system, only from main)

#### `default` path: spawn dev-agent

- Main runs `/opsx:ff` (+ `/opsx:continue` ≤3) inline to prepare artifacts (state A/C). Cheap; no isolation needed.
- Main calls `AskUserQuestion` (approve / revise / stop) as the HITL gate.
- On approve: main spawns `dev-agent` (opus, worktree-isolated, `commit: false`) for `/opsx:apply` only.
  - **Why opus**: highest-stakes stage; weak reasoning here means broken code or missed test signals.
  - **Worktree isolation**: yes.
  - **Commit semantics**: dev-agent does NOT commit. Existing `agents/dev/dev-agent.md` step 8 says commit only when `commit: true`, default `false`. Commit ownership stays with `/dev:verify`.
- dev-agent output:
  - Modifies source code (handed back via `git status`)
  - Marks tasks `[x]` in `tasks.md` as it goes
  - Writes **`.dev/apply-result.md`** with `Status: <CLEAR|FAILED|BLOCKED_CLARIFICATION>` (per agent step 9 v9 + §6)
- Done marker is `openspec list --json | jq '.changes[] | select(.name==$n) | .completedTasks == .totalTasks AND .totalTasks > 0'`. `.dev/apply-result.md` is supplementary signal for FAILED/BLOCKED reasons.

#### `--auto` path: inline in caller's session

- Caller (main or dispatcher subagent) runs `/opsx:ff` (+ `/opsx:continue` ≤3) inline.
- No HITL gate — `--auto` is unattended by definition.
- Caller runs `/opsx:apply <change-name>` inline; tasks get marked `[x]` as they complete.
- Done marker is the same `tasks.md` checkbox check. `.dev/apply-result.md` is NOT written on the inline path — there's no agent boundary to write a sentinel across.

#### Mid-apply pause behavior

`/opsx:apply` may pause mid-test-loop for clarification:

- **`default` path (dev-agent spawn)**: dev-agent has no `AskUserQuestion` (per §2 discipline #3). It writes `Status: BLOCKED_CLARIFICATION` + the question in `Summary` to `.dev/apply-result.md` and returns. Main session reads the file, surfaces the question via `AskUserQuestion`, and then **falls back to inline `/opsx:apply` in main** to resume from the next `[ ]` (the same clarification re-prompts naturally in main and the user answers). One-time fallback per invocation — see `commands/dev/dev/apply.md` Step 4D.3.
- **`--auto` path (inline)**: `/opsx:apply` may pause for clarification but no `AskUserQuestion`-equivalent UI is available in an unattended dispatcher subagent. STOP with a clear FAIL message; user re-runs `/dev:ff` (no `--auto`) and the default-mode dev-agent + main-fallback path resolves the question naturally.

#### Why fail-fast in `--auto` instead of an answer-channel

Same reasoning as v8 — well-written specs rarely trigger `/opsx:apply` clarifications; the rare miss is cheaper to surface and re-run than to engineer a JSONL answer-channel for. OpenSpec's `tasks.md` checkbox resume makes this safe — no work is lost.

#### Honest cost note for the `edit` resume path

User picks `edit`, edits artifacts, re-runs `/dev:ff`. Main re-runs `/opsx:ff`:

| User edit type | `/opsx:ff` behavior | Cost |
|---|---|---|
| Cosmetic (e.g. proposal.md prose) | `openspec status --json` reports applyRequires all done → `/opsx:ff` exits cheap | ~5K |
| Invalidating (e.g. renamed Capability, broke tasks.md cross-references) | status reports incomplete → `/opsx:ff` regenerates affected artifacts | ~20–30K |

**No fast path is hardcoded** — `/opsx:ff` does whatever its own logic dictates. v3 ai-expert flagged that the cost split is assumed not measured; first real cosmetic-edit ticket should log re-run cost (acceptance §8).

### 3.7 `/dev:verify` — keep main + existing verify-agent (opus)

verify-agent already writes `.dev/verify-pass.md` with `Status: CLEAR/BLOCKED` — this file IS the existing precedent for the §6 contract and is also `/dev:verify`'s done marker per rationalization plan §3.2. **No change.** v7 pinned the canonical path as `.dev/verify-pass.md` (the file already on disk in production); both plans now reference it consistently.

### 3.8 `/dev:review` — pin `/code-review` to opus

`/code-review` currently invoked without explicit model. Pin opus in `commands/dev/code-review.md` frontmatter (or via explicit `model: opus` at invocation). Adversarial diff review is exactly opus's strength.

### 3.9 `/dev:ship` — stay inline

Pure tool orchestration. **No change.**

## 4. Final layout

```
/dev:ff (main · opus, runs infer_dev_stage from filesystem)
│
├── /dev:start                       main · opus
│
├── PARALLEL (--auto only):
│   ├── /dev:figma  → figma-subagent (sonnet, worktree-iso)
│   └── /dev:detect → main inline (Bash: openspec status --json)
│
├── /dev:align       → align-subagent (sonnet)
│
├── /dev:apply       (mode-conditional — v9 flipped)
│      ├── --auto:    inline in caller's session (no agent spawn — dispatcher subagent already provides isolation; main is opus by default)
│      └── default:   inline /opsx:ff in main → AskUserQuestion → dev-agent (opus, worktree-iso, commit:false) for /opsx:apply
│
├── /dev:verify                     main · opus → verify-agent (opus, existing)
├── /dev:review                     main · opus → /code-review (opus, newly pinned)
└── /dev:ship                       main · opus
```

## 5. Token & cost impact (honest accounting)

Per typical ticket (1 figma node, ~10 files changed, no flaky tests):

| Source | Main saved | $ effect |
|---|---|---|
| `/dev:figma` → sonnet subagent | ~3–10K (Figma MCP volume; Linear payload stays in main) | small downgrade |
| `/dev:align` → sonnet subagent | ~3–8K (full receipt + artifacts read in subagent) | small downgrade |
| `/dev:apply` (default — dev-agent) | ~30–80K (`/opsx:apply` chain isolated; `/opsx:ff` prep stays in main, cheap) | none — same model (opus) |
| `/dev:apply` (`--auto` — inline in caller) | 0 saving when caller is main; saving accrues via dispatcher's outer subagent isolation, not via this stage | none — caller is opus (main default; dispatcher subagent pinned `model: opus` in §5.3) |
| **Per typical ticket main saving** | **~30–60K** when running default from main; **~80–150K** when running --auto from dispatcher (outer-subagent isolation covers the whole `/dev:ff` chain) | figma + align downgraded; apply matches caller |

Per worst-case ticket (multi-node figma, large diff, retry loops): ~80–150K main saved. Ranges are estimates; ai-expert flagged that worst-case figures aren't backed by measured data — first real ticket should log token usage to validate.

**What stays in main**: subagent return summaries (each result.md `Summary:` line is a few hundred chars; `Data:` JSON for dev-agent's `files_changed[]` is ~1–2K). The savings are tool-output verbosity, not the diff itself.

## 6. Subagent return contract — file-based line sentinels

Every spawned-by-/dev:* subagent writes its result to a fixed file path BEFORE returning. The file is the contract — the chat return is informational only.

| Subagent | Result file (also stage marker per rationalization plan) |
|---|---|
| `figma-subagent` | `.dev/figma/result.md` |
| `align-subagent` | `.dev/align-result.md` |
| `dev-agent` | `.dev/apply-result.md` (v9: written on `default` path; `--auto` runs inline in caller and has no agent result file — done marker is `tasks.md` checkboxes) |
| `verify-agent` (existing) | **`.dev/verify-pass.md`** (canonical; v7 pin) |

**Path constants are pinned** (v4 fix per ai-expert): each subagent's `.md` body MUST contain its result file path as a literal string. CI grep enforces — see §2 enforcement.

### File format

```
Status: <CLEAR | CONFLICT | BLOCKED_<reason> | ABORTED | FAILED | STALLED>
Outputs: <comma-separated absolute paths, or "none">
Summary: <one line, ≤200 chars>
Data: <single-line compact JSON, optional>
```

Atomic write: subagent writes `<file>.tmp`, then `mv` to `<file>`. Pre-existing repo convention (D16).

### Why file-based, not chat return

ai-expert v2 review flagged that chat-return parsing has framing holes:
- Mid-narration `Status: working...` tricks `grep -m1`
- Quoted user content (e.g. quoted Linear comment with "Status: BLOCKED") collides
- No guaranteed location

verify-agent's pre-existing pattern is **file-based** for exactly these reasons. The file is structured by the subagent's deliberate write, not narrated. v3 extends the same pattern to all `/dev:*` subagents.

### Orchestrator parsing (inline in each `/dev:*` stage body)

```bash
RESULT=".dev/<agent>-result.md"
[ -f "$RESULT" ] || { /* failure handling, see below */ }
STATUS=$(grep -m1 '^Status:' "$RESULT" | sed 's/^Status:[[:space:]]*//')
SUMMARY=$(grep -m1 '^Summary:' "$RESULT" | sed 's/^Summary:[[:space:]]*//')
OUTPUTS=$(grep -m1 '^Outputs:' "$RESULT" | sed 's/^Outputs:[[:space:]]*//')

# Optional structured data
DATA=$(grep -m1 '^Data:' "$RESULT" | sed 's/^Data:[[:space:]]*//')
[ -n "$DATA" ] && FILES=$(echo "$DATA" | jq -r '.files_changed[]?')
```

`grep`, `sed`, `jq` are existing deps. No new tooling. No helper script.

### Failure handling

| Failure mode | Detection | Recovery |
|---|---|---|
| Result file missing | `[ ! -f "$RESULT" ]` | **Retry once** with prompt prefix "you must write your result file at $RESULT before returning"; second failure → STOP + write the subagent's chat output to `claude-reports/<id>/subagent-malformed-<agent>.md` |
| `Status:` line missing in file | `grep -m1` returns empty | Same as missing file |
| `Status` value not in enum | string equality fails | Treat as `FAILED`. No retry. STOP. |
| `Data:` JSON invalid | `jq -e .` non-zero | Same as missing — retry once. |

## 7. Test plan

For each new / modified subagent and stage, hand-craft inputs and confirm. **No automated test harness required.**

| Surface | Test |
|---|---|
| **figma-subagent** (CLEAR) | 2-node ticket → `.dev/figma/raw/` has 2 files, `receipt.md` complete, `.dev/figma/result.md` has `Status: CLEAR` |
| **align-subagent** (CLEAR) | matching receipt + artifacts → `.dev/align-result.md` `Status: CLEAR`, no `claude-reports/` write |
| **align-subagent** (CONFLICT) | mismatched → `.dev/align-result.md` `Status: CONFLICT`, `claude-reports/<id>/figma-alignment.md` written, no artifact edits |
| **align-subagent prohibition guard** | `git status` after run shows only `.dev/align-result.md` and possibly `claude-reports/`; no `openspec/changes/` modifications |
| **dev-agent (`default`)** | state-B change → main runs `/opsx:ff` (skipped, already done) → AskUserQuestion approve → main spawns dev-agent → dev-agent runs `/opsx:apply`, `tasks.md` all `[x]`, source modified, **no `git commit`** (verify with `git status` showing uncommitted), `.dev/apply-result.md` `Status: CLEAR` |
| **dev-agent commit safety** | `commit: false` default; no commit invocation |
| **/dev:apply default — approve** | main runs `/opsx:ff` → AskUserQuestion → approve → main spawns dev-agent → tasks all `[x]` |
| **/dev:apply default — edit (cosmetic)** | gate → edit → STOP. user edits text. resume `/dev:ff` → main re-runs `/opsx:ff` (cheap), gate fires, approve, dev-agent spawn runs |
| **/dev:apply default — edit (invalidating)** | gate → edit changes Capability name. resume → main re-runs `/opsx:ff` (~20K), gate fires; user notices regenerated artifacts, approves or re-edits |
| **/dev:apply default — abort** | abort → STOP; dev-agent never spawned |
| **/dev:apply default — dev-agent BLOCKED_CLARIFICATION** | dev-agent writes `.dev/apply-result.md` `Status: BLOCKED_CLARIFICATION` + question in `Summary`. Main reads file, surfaces question via `AskUserQuestion`, then runs `/opsx:apply` inline in main to resume from next `[ ]`. Same clarification re-prompts in main; user answers; tasks complete. One-time fallback (no re-spawn loop). |
| **/dev:apply --auto — inline `/opsx:apply`** | caller (main or dispatcher subagent) runs `/opsx:apply` directly; no agent spawn; tasks all `[x]`; no `.dev/apply-result.md` written |
| **/dev:apply --auto — `/opsx:apply` mid-pause** | inline `/opsx:apply` pause has no `AskUserQuestion` in unattended caller; STOP with FAIL message. User re-runs `/dev:ff` in default mode where dev-agent path + BLOCKED_CLARIFICATION fallback resolves naturally |
| **/dev:apply mid-flight mode switch** | `/dev:ff` (default) → gate → approve → dev-agent runs `/opsx:apply` partially → user kills it → `/dev:ff --auto`. Walker infers `apply` (tasks not all `[x]`); --auto path runs inline `/opsx:apply` and resumes from next `[ ]`. Tasks already `[x]` are skipped naturally |
| **--auto from dispatcher subagent (regression for CAF-370)** | dispatcher spawns `general-purpose` subagent at `model: opus` → subagent runs `/dev:ff --auto` → apply runs inline at subagent level (no nested spawn) → tasks complete → verify / review / ship proceed |
| **/dev:figma + /dev:detect parallel** | --auto run → both complete; figma's receipt.md exists; detect's classification reflected in next-stage decision (no race because no shared writer) |
| **/code-review opus pin** | confirm spawned at opus model |

### Negative tests

| Test | Setup | Expected |
|---|---|---|
| Result file missing | hand-craft subagent that returns chat without writing file | retry once with prefix; second failure writes `claude-reports/<id>/subagent-malformed-<agent>.md`; STOP |
| Status enum violation | hand-craft `Status: WEIRDVAL` in result file | treat as FAILED, no retry, STOP |
| Data invalid JSON | hand-craft `Data: {bad json}` | retry once, then STOP |
| Subagent edits forbidden path | align-subagent attempts edit of `openspec/changes/<n>/proposal.md` | `git status` audit catches it post-run; subagent prompt prohibits this; CI grep enforces no `state.json` writes |
| Dispatcher collision | `/ggx-dispatcher` runs `/dev:ff CAF-X --auto`; user manually runs `/dev:ff CAF-X` from another terminal | No lock in v5; user is expected not to do this (Linear label state machine prevents dispatcher itself from double-dispatching). If it happens, user sees concurrent terminal output and kills one. Recovery: `/dev:ff` resumes from filesystem markers naturally |

## 8. Acceptance checklist

- [ ] `agents/AGENTS.md` exists with the three discipline principles (§2)
- [ ] CI grep check (§2) added; passes against current `agents/`
- [ ] `figma-subagent.md`, `align-subagent.md` exist; all reference `AGENTS.md`; all use the §6 file-based contract; all write their result file atomically
- [ ] `align-subagent.md` prohibition list explicit per §3.5
- [ ] `dev-agent.md` updated: `commit` parameter (default false), §6 result file, no `AskUserQuestion`
- [ ] All 3 modified `/dev:*` stages (`figma.md`, `align.md`, `apply.md`) dispatch the subagent and parse its result file with inline `grep | sed`; no chat-return parsing
- [ ] `/dev:apply` mode-conditional implemented (v9 flipped): `default` runs `/opsx:ff` inline + AskUserQuestion + spawns dev-agent for `/opsx:apply`; `--auto` runs `/opsx:ff` + `/opsx:apply` inline in caller's session (no nested spawn)
- [ ] `/ggx-dispatcher` Step 5.3 spawns each dispatch subagent with `model: "opus"` (compensates for `--auto` no longer spawning dev-agent)
- [ ] CAF-370 regression test: dispatcher → `/dev:ff CAF-X --auto` → apply runs inline at subagent level without nested-spawn failure
- [ ] `/dev:ff` parallelizes figma+detect in `--auto` only (single message, two tool calls per §3.4); default mode sequential
- [ ] `/code-review` pinned to opus
- [ ] All §6 result files verified manually against §7 test matrix
- [ ] Negative tests (§7) verified manually
- [ ] No subagent writes `state.json` (CI grep confirms; `state.json` should not exist anywhere given rationalization plan)
- [ ] No subagent edits files outside its declared output lane (manual audit + path-escape guards)
- [ ] HITL paths verified: edit (cosmetic + invalidating), abort branches return cleanly without partial code changes
- [ ] `dev-agent` invocation removes any pre-existing `.dev/apply-result.md` before spawning (prevents stale `BLOCKED_CLARIFICATION` from a prior run leaking into the next `default` invocation — v9; was `--auto` pre-v9)
- [ ] First real ticket end-to-end: log per-stage token usage to validate §5 ranges; record in `.dev/metrics.jsonl` if telemetry desired
- [ ] **First cosmetic-edit resume measured** (per ai-expert v3 concern): when user picks `edit` for a cosmetic-only change in default mode, log the `/opsx:ff` re-run cost. If >10K, §3.6's optimistic split into "cosmetic ~5K vs invalidating ~20–30K" is wrong; revisit by making `/opsx:ff` cheaper on no-op or adding a fast-path

## 9. PR sequencing

Land in stages, smallest blast radius first:

### PR-1a: `align-subagent` only (single new caller of §6 contract)
- `agents/AGENTS.md` + CI grep
- `align-subagent.md` (§3.5)
- `/dev:align` body changes
- §6 contract documented; align-subagent is the first new consumer

PR-1a verifies the file-based result contract on one caller before adding more.

### PR-1b: `figma-subagent` + parallelization + code-review opus
- `figma-subagent.md` (§3.2)
- `/dev:figma` body changes
- `/code-review` opus pin (§3.8)
- `/dev:ff` Step 1: parallel figma+detect dispatch in `--auto` (§3.4 pseudocode)

PR-1b extends §6 to figma and adds the parallel pseudocode. Lands after PR-1a has bedded in.

### PR-2: `/dev:apply` mode-conditional execution
- `dev-agent.md` modifications (§3.6 `--auto` path): `commit` param default false, §6 result file
- `/dev:apply` rewrite (v9 shape): branch by mode — `default` runs `/opsx:ff` inline + AskUserQuestion + spawns dev-agent for `/opsx:apply`; `--auto` runs both inline in caller's session
- `commands/dev/ggx-dispatcher.md` §5.3 sets `model: "opus"` on every spawn (compensates for inline apply losing the spawn-time opus pin)
- Negative tests for default-mode approve/edit/abort paths
- First-cosmetic-edit token cost measurement

PR-2 carries the largest token saving but also the most behavior change. Land after PR-1b.

## 10. Out of scope

- Subagent-internal `AskUserQuestion` (rejected per §3.6 + repo convention)
- Token-budget enforcement / circuit breakers inside subagents
- Migration of port pipeline subagents (existing port subagents remain on their current contracts unless touched for other reasons; v3 contract is forward-compatible)
- Automated test harness (manual contract verification per §7 is sufficient)
- Helper script for parsing (rejected — `grep | sed` inline is simpler than a script)

## 11. Cross-references

- `ff-state-rationalization.md` v3 — defines stage markers; this plan's subagent result files ARE those markers
- `agents/dev/verify-agent.md` — the file-based result precedent this plan generalizes
- `agents/dev/synth-agent.md`, `dev-consult-agent.md`, `pm-agent.md`, `designer-agent.md` — exemplars of single-output-lane discipline

## 12. ai-expert review history

| v1 finding | v2 resolution | v3 status |
|---|---|---|
| Internal-HITL in dev-agent unverified | v2 split into apply-prep + dev-agent + main HITL | ❌ v5 replaced with mode-conditional: `--auto` spawns dev-agent end-to-end (no HITL needed); `default` runs inline in main with HITL. One subagent, simpler dispatch, no re-spawn dance. v6 added: mid-apply pause spec (default: natural; `--auto`: BLOCKED_CLARIFICATION return + main re-spawn) |
| v5 lock-removal claim "Linear label is mutex" structurally wrong | n/a (v5 issue) | ✅ v6 reframed as **operator-discipline contract** (rationalization §6) — no implicit guarantee; user controls via 3 explicit rules |
| v5 ship-pending.md resume entry-point unspecified | n/a (v5 issue) | ✅ v6 added explicit Step 0 entry-point check spec (rationalization §6) — reads file, retries Linear writes with saved payload, skips push |
| v5 .dev/figma/.skipped writer ambiguity | n/a (v5 issue) | ✅ v6 pinned `/dev:start` as SOLE writer (rationalization §3.5); `/dev:figma` MUST NOT create it; figma-subagent returns FAILED on empty URL list |
| v5 figma/align mode-symmetry rationale missing | n/a (v5 issue) | ✅ v6 §3.6 explains why ONLY apply is mode-conditional (only stage with HITL gate worth main-session execution) |
| v6 #1: ship-pending.md partial-success rebuild bug | n/a (v6 issue) | ✅ v8 sidestepped — single-payload `KIND:` format makes partial-success unreachable (description-then-comment ordering means only one payload is ever in flight). Section-aware helper removed (rationalization §6) |
| v6 #2: --auto mid-apply answer plumbing hand-waved | n/a (v6 issue) | ✅ v8 simplified — dev-agent fails fast on clarification need; user re-runs `/dev:ff` in default mode where `AskUserQuestion` is natural. Trades elegance for fewer moving parts; v7's JSONL answer-channel removed (§3.6) |
| v6 #3: verify path inconsistency (verify.md vs verify-pass.md) | n/a (v6 issue) | ✅ v7 pinned canonical `.dev/verify-pass.md` everywhere; CI Check B enforces |
| v6 #4: walker too lenient on malformed marker | n/a (v6 issue) | ✅ v7 walker STOPs on malformed verify-pass.md instead of silent re-run (rationalization §4) |
| v6 #5: ship-pending.md malformed-file behavior unspecified | n/a (v6 issue) | ✅ v7 Step 0 spec includes explicit malformed-file STOP path (rationalization §6) |
| Return contract has no parser | v2 line-sentinel chat return | ❌ ai-expert v2 flagged framing holes; v3 switched to file-based result files (verify-agent precedent) |
| Cost numbers aspirational | v2 honest accounting + worst-case bounds | ✅ Kept; first ticket telemetry will validate |
| §3.4 parallel pseudocode missing | v2 added pseudocode | ⚠️ ai-expert v2 flagged incoherence (detect was inline, not subagent); v3 rewrites pseudocode showing Agent + Bash multi-call |
| `align-subagent` could call `/opsx:rebuild` | v2 explicit prohibition list | ✅ Kept |
| dev-agent commit-flip safety | v2 noted zero-callers | ✅ Kept; in v9 dev-agent has 1 live caller (`/dev:apply` default mode) so `commit: false` default is now load-bearing, not a no-op |
| v8 architectural flaw: `--auto` spawns opus dev-agent from a `general-purpose` dispatcher subagent | not anticipated in v5–v8 (assumed `--auto` always ran from main) | ✅ **v9** flipped mode-conditional: `--auto` inline in caller, `default` spawns dev-agent. Eliminates nested opus spawn. dev-agent stays a live caller via default mode. Dispatcher pins `model: opus` to compensate for the lost-isolation model upgrade |
| AGENTS.md not enforced | v2 added file-level CI grep | ❌ ai-expert v2 flagged false-negative; v3 switched to line-level grep |
| §3.6 fast no-op resume claim was fiction | (not addressed in v2) | ✅ v3 §3.6d honest cost model split by edit type; OpenSpec's tasks.md checkbox makes resume natural |
| state.json complexity feeding back into subagent design | (not addressed in v2) | ✅ v3 paired with rationalization v3 — no state.json anywhere; result files double as stage markers |
