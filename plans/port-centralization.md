# Port skill centralization & atomization

**Status**: IMPLEMENTED — atomic stages live at `commands/dev/port/{start,explore,plan,synth,revise,ship,ff}.md`. Kept as the design rationale / decision record (referenced from `README.md` + `USER_GUIDE.zh-TW.md`).
**Owner**: Charlie
**Source**: flutter `.claude/skills/port/SKILL.md` (879 lines, v6.0)
**Target**: `gogox-claude` (this repo), platform-agnostic via profile yaml

---

## 1. Problem

The `/port` orchestrator lives only in `gogox-client-flutter/.claude/skills/port/`. It has accreted four issues:

1. **Project-bound** — hardcoded `.claude/port-settings.json`, no concept of running the same flow from android↔flutter or ios. Cannot reuse.
2. **Monolithic** — 879 lines that bundle phases 0→5 into one entry point. Hard to re-run a single phase, hard to debug, hard to graft new review hooks.
3. **Stale tooling** — uses old MCP prefix `mcp__linear-server__*` (now `mcp__claude_ai_Linear__*`).
4. **No semantic guards** — only deterministic grep checks for synthesis. Synthesis hallucination (artifact says X, source notes don't support X) goes undetected until the human review gate, where it's easy to skim past.

## 2. Goals

1. Move the skill into `gogox-claude` so install.sh symlinks expose it to all repos.
2. Decompose into **atomic commands** with explicit I/O contracts so each phase can be re-run in isolation, debugged, or replaced.
3. Add **one targeted deterministic guard** (B-citation check) to catch synthesis omission. Skip illusion-of-rigor LLM reviewers.
4. Keep `--auto` mode as a flag on the wrapper for unattended/dispatcher use.
5. Add a generic `/test-plan` for the post-apply manual-acceptance gap (Step 6 in the team's dev flow). This lives outside `/port`.

Non-goals: rewriting `dev-consult-agent` / `pm-agent` / `designer-agent` semantics. They stay; only their prompts get a small ID-convention amendment.

## 3. Decisions (locked)

| ID | Decision | Rationale |
|---|---|---|
| D1 | **Filesystem state, no JSON manifest** | Aligned with opsx + existing skill philosophy; LLMs read `ls` reliably; one less schema to maintain |
| D2 | **Persistent config split across two files**: `.gogox-claude.yaml` (committed, holds existing `branch_prefix` which doubles as Linear team key when `ticket_system: linear`); **new `.gogox-claude.local.yaml`** (gitignored, per-machine, holds `origin_project_path` with `~`/`$ENV` expansion) | Per-machine paths can't be checked-in; reuse `branch_prefix` instead of new `linear_team_key` field |
| D3 | **6 atomic commands + 1 wrapper `/port:ff`** | Wrapper naming follows `/opsx:ff` convention |
| D4 | **B-citation check via labeled-item IDs** (`FR-N`, `AC-N`, `R-N`, `A-N`) | Stable across edits, regex-friendly, human-readable in PR review |
| D5 | **No A/B/C LLM reviewer agents in MVP** (per pragmatic-path decision) | Deterministic guards already cover most failure modes; revisit only after post-mortem evidence |
| D6 | **Locate gate hardening** — grep candidate file existence + symbol presence; downgrade confidence on miss | Pure deterministic, replaces fork-second-LLM proposal |
| D7 | **`/spec-lint` as separate gogox-claude command** holding parity + forbidden-marker + B-citation logic | Reusable for non-port OpenSpec changes; opsx is third-party so we cannot extend `/opsx:verify` |
| D8 | **`/test-plan` is generic, not port-scoped** | Step 6 of dev flow applies to any feature, not just port |
| D9 | **Auto-detect ticket-id in middle stages** (explore → ship), **require `--ticket:` in entry stages** (start, ff) | Match `/remove-worktree` UX; entry points have no cwd to infer from |
| D10 | **`/port:revise` auto-fixes are reported** (main-thread one-liner per fix; in `--auto`, accumulated to `claude-reports/<session>/auto-fixes.md` and folded into the Linear summary comment) | Audit trail; user must be able to see what AI changed |
| D11 | **`/spec-lint` findings written to Linear only in `--auto` mode** (HITL resolves them inline so writing them is outdated noise) | Preserve existing skill behavior; minimize Linear noise |
| D12 | **Keep `--simple` mode** as `/port:explore --simple` flag | Pre-port enrichment + thin-ticket scoping use cases are real; `~140` lines, low maintenance cost |
| D13 | ~~No further sub-agent delegation in MVP; defer synth-agent to PR 5~~ — **Superseded by D20** (independent review pushed back: split reduces PR 4 surface area instead of increasing it) | (overruled) |
| D14 | **Orchestrator stays main-thread for stages 1, 5, 6, W** | Stage 1/6/W are tool-call coordination — sub-agent dispatch overhead > model cost savings. Stage 5 has HITL — sub-agents cannot `AskUserQuestion` |
| D15 | **Existing three sub-agents get small prompt amendments only**: (a) `dev-consult-agent` — origin-path placeholder reads from profile yaml; add `**R-N**` ID rule. (b) `pm-agent` — add `**FR-N**` / `**AC-N**` / `**A-N**` ID rule. (c) `designer-agent` — add `**A-N**` ID rule. No semantic prompt change. **Add a test fixture (known-input → known-labeled-ID-output) and a `/spec-lint` warning when a notes file has zero labeled IDs** (catches sonnet drift on bold formatting before it cascades into false-orphan reports) | Keeps PR 4 surface area minimal; defends against prompt drift |
| D16 | **Atomic writes for `.port/*.md`** — every notes file write goes through `<file>.tmp` + `mv` rename (POSIX atomic on same filesystem). Existence-implies-completion model needs this protection from partial-write corruption | Filesystem-as-state (D1) breaks if a stage crashes mid-write; partial file looks complete to the next stage |
| D17 | **B-citation check is bidirectional**: (a) every labeled ID in `.port/*-notes.md` must be referenced by at least one artifact (catches synthesis omission); (b) every `(FR\|AC\|R\|A)-\d+` reference in artifacts must exist in some notes file (catches synthesis hallucination — the more dangerous failure) | Independent reviewer flagged: reverse direction is the bigger correctness risk |
| D18 | **Stale `.port/` policy**: `/port:start --recreate` cleans `.port/` to empty (after worktree confirmation). Middle stages (`explore` / `plan` / `synth`) check freshness — if any expected input file's mtime predates worktree HEAD commit by more than 1 hour, prompt `reuse / regenerate` (HITL) or auto-regenerate (`--auto`). Each stage also accepts `--force` to skip the freshness check | Aborted prior runs leave files that look complete; current spec handwaves this |
| D19 | **PR 4 splits into PR 4a (entry stages) + PR 4b (synth + ship)** — see §8 for new breakdown. PR 4 alone was 879 lines + refactor; bisect-unfriendly | Reduces high-risk PR's blast radius |
| D20 | **`synth-agent` ships in PR 4b**, not deferred. Stage 4 (b) artifact-generation loop runs in `agents/dev/synth-agent.md` with model frontmatter `model: opus`. Orchestrator still owns context.md build, validate, `/spec-lint`, forbidden-marker scan | Splitting is what *reduces* PR 4 surface — synth as separate file vs inline 200-line block |
| D21 | **`synth-agent` model pinned: `opus`** in agent frontmatter, ignoring user's session model | Hallucination-sensitive stage; cannot let user accidentally run synthesis on sonnet |
| D22 | **Observability via `.port/timings.jsonl`** — every stage append-writes a JSON line with `stage`, `start`, `end`, `duration_ms`, `tokens_in`, `tokens_out`, `model`, `outcome`. Read by future post-mortem tooling | 2026 baseline: no telemetry = no learning loop on what's slow / costly |

## 4. Architecture

### 4.1 Command set

| # | Command | Owner | Input | Output |
|---|---|---|---|---|
| 1 | `/port:start --ticket:X` | orchestrator | profile yaml + `--ticket:` | worktree, openspec scaffold, optional `.port/prd.md` |
| 2 | `/port:explore` | `dev-consult-agent` | cwd in worktree, prd.md (optional) | `.port/dev-notes.md` (Locate gate passed) |
| 3 | `/port:plan` | `pm-agent` + `designer-agent` (parallel) | dev-notes.md | `.port/pm-notes.md`, `.port/design-notes.md` |
| 4 | `/port:synth` | orchestrator + `/spec-lint` | three .port/*-notes.md | `.port/context.md`, `proposal.md`, `design.md`, `tasks.md`, `specs/*/spec.md`, `.port/synth-report.md` |
| 5 | `/port:revise` | orchestrator + HITL | synth-report + artifacts | revised artifacts; review gate verdict |
| 6 | `/port:ship` | orchestrator + `/commit` | reviewed artifacts | branch pushed; Linear description PRD region updated; Linear summary comment |
| W | `/port:ff --ticket:X [--auto]` | wrapper | `--ticket:` (+ optional `--auto`) | runs 1→6 sequentially |

### 4.2 Phase-to-command mapping (vs existing 879-line skill)

| Existing phase | Becomes |
|---|---|
| Phase 0 (Resolve) | `/port:start` (front half) |
| Phase 1 (Worktree + scaffold) | `/port:start` (back half) |
| Phase 2 Wave 1 (dev-consult) | `/port:explore` |
| Phase 2 Wave 2 (pm + designer) | `/port:plan` |
| Phase 3 (Synthesis + parity checks) | `/port:synth` |
| Phase 4 (Pre-review + review gate + revision loop) | `/port:revise` |
| Phase 5 (Ship) | `/port:ship` |
| `--auto` mode | `/port:ff --auto` (decision table G1-G9 preserved) |

### 4.3 State carrier (no JSON)

| Cross-stage signal | Where |
|---|---|
| Current ticket | `git rev-parse --show-toplevel` basename → regex `[A-Z]+-\d+` → fallback to `git branch --show-current` regex |
| change-name | single `openspec/changes/<change-name>/` directory |
| Stage progress | which `.port/*.md` files exist (existence implies completion) |
| Persistent config | `commands/dev/profiles/<repo>.yaml` |
| Cross-session signal | Linear labels (`ready-to-port` → `need-spec-review`) |
| Ephemeral values (figma_url etc) | re-fetch from Linear; do not cache |

### 4.4 Ticket-id resolution (each command)

```
Stages 1, W (entry): require --ticket:<ID>; reject without it.
Stages 2-6 (middle):
  1. Try cwd basename match against /[A-Z]+-\d+/
  2. Else try `git branch --show-current` match
  3. Else AskUserQuestion (HITL) | abort (--auto)
```

## 5. Per-command I/O contracts

### 5.1 `/port:start --ticket:CAF-XXX [--prd:"text"|--prd-file:path]`

**Pre**: at main repo root; profile yaml has `origin_project_path` + `linear_team_key`; ticket exists in Linear.

**Steps**:
1. Read profile yaml → resolve `<origin_project_path>` and `<linear_team_key>`. If either missing, AskUserQuestion once and write back.
2. `mcp__claude_ai_Linear__get_issue` → `<ticket-context>`.
3. Assignee check → STOP if not self.
4. Derive `<change-name>` = kebab-case(title) minus leading bracket prefix.
5. PRD enrichment: in HITL mode, AskUserQuestion if ticket is thin; auto: extract `<!-- port:simple:start -->` block if present, else proceed.
6. `/add-worktree <ticket-id> --type feat`.
7. `cd ../<ticket-id>` → `openspec new change <change-name>` → `mkdir openspec/changes/<change-name>/.port/`.
8. Write `prd.md` if user provided.
9. Print Announce block; exit.

**Post**: worktree at `../<ticket-id>`, scaffolded change, optional prd.md.

**Idempotent rule**: existing worktree → AskUserQuestion `reuse / recreate`; existing change → same.

### 5.2 `/port:explore`

**Pre**: cwd in worktree; `.port/` exists.

**Steps**:
1. Resolve ticket-id (per §4.4).
2. Resolve `<origin_project_path>` from profile yaml.
3. Read `.port/prd.md` (or treat as empty).
4. Spawn `dev-consult-agent` with `CONSULT MODE` prompt; output path = `.port/dev-notes.md`.
5. **Locate gate (hardened)**:
   a. Parse `## Locate` section: candidate paths + main symbols + confidence.
   b. For each candidate path: `[ -f "$ORIGIN/$path" ]` check.
   c. For each symbol: `grep -rn "<symbol>" $ORIGIN/$candidate-path`.
   d. Any path-or-symbol miss → downgrade confidence one tier.
   e. Apply Locate gate logic on the (possibly downgraded) confidence (high → proceed; medium → flag in HITL or proceed in auto; low → escalate or abort).

**Post**: `.port/dev-notes.md` exists with verified Locate.

### 5.3 `/port:plan`

**Pre**: `.port/dev-notes.md` exists.

**Steps**:
1. Resolve ticket-id, fetch ticket-context, resolve figma_url.
2. Spawn **in parallel**:
   - `pm-agent` → reads dev-notes + ticket + prd → writes `.port/pm-notes.md`
   - `designer-agent` → reads dev-notes + ticket + figma → writes `.port/design-notes.md`
3. Both prompts include: "use labeled IDs `**FR-N**`, `**AC-N**`, `**R-N**`, `**A-N**` for every numbered item — these are referenced from artifacts later".

**Post**: pm-notes.md, design-notes.md.

### 5.4 `/port:synth`

**Pre**: all three `.port/*-notes.md` exist.

**Steps**:
1. Build `.port/context.md` bundle (= ticket + prd + dev/pm/design notes + aggregated assumptions). Atomic write per D16.
2. `openspec status --change <name> --json` → artifact dependency order.
3. **Spawn `synth-agent`** (D20, opus per D21) with:
   - change-name, worktree path, context.md path, dependency order JSON
   - Contract: for each artifact in order → `openspec instructions <id>` → fill template → atomic write to `outputPath` → return file list
   - synth-agent owns ONLY this loop; not validate, not lint, not parity checks
4. Orchestrator runs `openspec validate <name> --type change --json`.
5. Orchestrator invokes `/spec-lint` (see §5.7) → produces `.port/synth-report.md` containing:
   - capability-name alignment findings
   - FR-vs-scenario count findings
   - hardcoded value drift findings
   - divergence-marker findings (port-specific)
   - forbidden-marker hits
   - **B-citation findings** (any `FR-N` / `AC-N` / `R-N` in notes not referenced in any artifact)

**Post**: artifacts + synth-report.md.

### 5.5 `/port:revise`

**Pre**: synth-report.md exists.

**Steps**:
1. Read synth-report; partition findings into "needs user input" vs "machine-resolvable".
2. For machine-resolvable: orchestrator applies targeted Edit + cascade scan.
3. For user-input: AskUserQuestion (batch up to 4 per prompt). After answer, orchestrator applies edits.
4. After all findings addressed: re-run `/spec-lint` to confirm clean.
5. Review gate AskUserQuestion: `approve / revise more / abort`.
6. On approve: print "Run `/port:ship` next."

**Post**: clean artifacts; review approved.

### 5.6 `/port:ship`

**Pre**: artifacts approved (no `synth-report.md` findings remaining, OR user has explicitly approved despite findings).

**Steps**:
1. `/commit` — analyze and commit OpenSpec changes.
2. `git push -u origin feat/<ticket-id>`.
3. Build `<spec-tree-url>` via `git config --get remote.origin.url`.
4. Re-fetch Linear ticket description (preserve PM edits outside markers).
5. Replace or append PRD region between `<!-- port:prd:start -->` / `<!-- port:prd:end -->`.
6. `mcp__claude_ai_Linear__save_issue` to update description.
7. `mcp__claude_ai_Linear__save_comment` with summary (capabilities, validation, files, next-step `/opsx:apply`).
8. In `--auto` mode: also add `need-spec-review` label.
9. Print final block with spec-tree URL + worktree path.

**Post**: branch pushed, Linear updated.

### 5.7 `/spec-lint` (new helper command)

Pure-deterministic grep-based linter over `openspec/changes/<change-name>/`. Runs:

| Check | Logic | Source |
|---|---|---|
| Capability-name alignment | grep pm-notes capabilities vs `ls specs/` | from existing Phase 3.4 #1 |
| FR vs scenario count | `grep -cE '^\d+\.' pm-notes` vs `grep -crE '#### Scenario:' specs/` | from existing Phase 3.4 #2 |
| Hardcoded drift | grep dev-notes codes vs spec literals | from existing Phase 3.4 #3 |
| Divergence markers | grep design.md for `Figma-driven`/`intentional improvement` tags | from existing Phase 3.4 #4 (port-specific, but cheap to keep) |
| Forbidden markers | grep `## Open Questions|TBD|TODO|FIXME|待確認` | from existing Phase 3.5 |
| **B-citation forward (new)** | Extract every `**(FR\|AC\|R\|A)-\d+**` from `.port/*-notes.md`; grep each ID across all artifacts; flag any **note ID with zero artifact references** (synthesis omission) | new |
| **B-citation reverse (new, D17)** | Extract every `(FR\|AC\|R\|A)-\d+` referenced from artifacts; verify each exists in some `.port/*-notes.md`; flag any **artifact-cited ID that has no notes-side definition** (synthesis hallucination — higher priority finding) | new |
| **Labeled-ID drift warning (D15)** | Count labeled IDs in each `.port/*-notes.md`; if any notes file has zero IDs → warn "agent likely dropped the labeled-ID convention; B-citation check may be unreliable" | new |
| Cascade scan (`--after-edit`) | grep all artifacts for stale terms passed via `--stale:foo,bar` | from existing Phase 4.4 |

Output: structured `.port/synth-report.md` (or stdout if invoked standalone).

### 5.8 `/port:ff [--ticket:X] [--auto]`

Wrapper. If `--ticket:` provided → run `/port:start` first; else expect to be inside an existing worktree and resolve ticket from cwd. Then sequentially: explore → plan → synth → revise → ship. Each step's pause behavior depends on `--auto` flag (HITL vs decision table).

`--auto` decision table preserved verbatim from existing skill (G1-G9).

## 6. Sub-agent prompt amendments (small)

`dev-consult-agent`, `pm-agent`, `designer-agent` get one new line in the CONSULT MODE prompt block:

> Numbered items MUST use labeled IDs in bold: `**FR-1**`, `**AC-2**`, `**R-3**`, `**A-4**`. Artifacts reference these IDs to prove traceability. Drop the bold and the citation check fails.

That's it. No semantic prompt changes.

## 7. Profile yaml schema delta

Add to each existing `commands/dev/profiles/<repo>.yaml` (top-level, optional):

```yaml
# Set if this repo is a port target. Omit on origin-only repos.
origin_project_path: /Users/.../gogovan-client-v2-android
linear_team_key: CAF
```

`/port:start` reads these. Missing → AskUserQuestion once, write back.

## 8. Phasing (PR breakdown)

| PR | Scope | Risk |
|---|---|---|
| **PR 1** (this plan) | This doc only | none |
| **PR 2** | New `commands/dev/profiles/<repo>.yaml` fields (`origin_project_path`, `linear_team_key`, with `~` / env-var expansion supported); document in README | low |
| **PR 3** | `/spec-lint` helper command — port over Phase 3.4/3.5/4.4 + **bidirectional** B-citation (D17) + warning when notes file has zero labeled IDs (D15). Standalone usable | medium |
| **PR 4a** | Entry-side atomic commands: `/port:start`, `/port:explore` (full + `--simple`), `/port:plan`. Includes: profile-yaml resolution, atomic writes (D16), stale-`.port/` policy (D18), Locate gate hardening (D6), labeled-ID prompt amendments to dev-consult / pm / designer agents (D15), `.port/timings.jsonl` writes (D22). Three sub-agents fully exercised against the new contracts | medium |
| **PR 4b** | Synthesis + ship side: `/port:synth`, `/port:revise`, `/port:ship`. Includes: new `agents/dev/synth-agent.md` (D20, opus pinned per D21), context.md bundle, artifact-generation loop delegated to synth-agent, validate, `/spec-lint` invocation, revise loop with cascade scan, ship Linear write-back. **Drop** flutter `.claude/skills/port/`, `.claude/commands/port.md`, `.claude/port-settings.json` only after PR 4b dogfood passes | high |
| **PR 5** | `/port:ff` wrapper | low |

**Dogfood gate for PR 4b (mandatory before deleting flutter copies)**:
1. Run one real port end-to-end (happy path) on a non-trivial ticket.
2. Run one port with a thin ticket (Locate-gate-medium path).
3. Run one port that requires revision-loop iteration (intentionally introduce a `## Open Questions` to trip Phase 3.5).
4. Run one `/port:ff --auto` against a ticket already enriched via `--simple`.

Keep flutter's local skill copy for **one full sprint after PR 4b lands** before deletion. Rollback path = `git revert` PR 4a + 4b on `gogox-claude`, restore flutter's `.claude/skills/port/` from history.

`/test-plan` + `test-planner-agent` and Flutter QA agent ports (`bug-reproducer-flutter`, `bug-fix-verifier-flutter`) are tracked separately — they belong to the broader dev flow, not the port scope.

## 9. Open questions

(All resolved — see D10/D11/D12 in §3.)

## 10. Risks

| Risk | Mitigation |
|---|---|
| 879 lines refactored inline introduces bugs | Four-scenario dogfood gate before deletion (see §8); flutter copy retained one full sprint |
| Sub-agent prompt drift breaks ID convention | Lock prompt amendment in PR 4a; test fixture + `/spec-lint` zero-ID warning (D15) catches drift early |
| `--auto` decision table drift during refactor | Copy verbatim; **add explicit regression test** that auto-mode rules match the original G1-G9 table 1:1 (snapshot test on the decision-rule code path) |
| Filesystem-state ambiguity + partial writes | Atomic writes (D16) + freshness check (D18); each command checks expected file pattern; missing → clear error + suggest entry point |
| Multiple worktrees for same ticket | Existing `/add-worktree` already detects; reuse its logic |
| **Concurrent `/port:ff` runs against the same ticket** | `.port/.lock` lockfile written at stage entry, removed on exit. Stale lock (mtime > 2h) → warn + AskUserQuestion `take-over / abort` (HITL) or take-over (`--auto`) |
| **Linear MCP failure mid-`/port:ship`** (e.g., 401 after `git push` succeeded) | Branch is already pushed at that point — orphan state. Handling: retry save_issue / save_comment up to 3× with exponential backoff. On final failure: write `.port/ship-pending.md` with the unsent payload + print `manual fix needed: <ticket-id> Linear sync incomplete` so subsequent `/port:ship` re-run can resume |
| **Cross-platform path portability** (profile yaml hardcoded `/Users/...`) | Profile yaml supports `~` and `$ENV_VAR` expansion. Lint at `/port:start`: if path doesn't exist after expansion → AskUserQuestion to fix profile, write back |
| **Sonnet drifts from labeled-ID convention** | Test fixture in PR 4a; `/spec-lint` warns on zero-ID notes (D15) |

## 11. Out of scope

- Multi-platform port targets (android↔flutter↔ios). Profile yaml supports this naturally but no flow change required.
- Reviewer agents A/B/C. Revisit after 5-10 real-world ports if post-mortems show specific gaps.
- Replacing opsx (it's third-party, untouchable).
- `/test-plan` + `test-planner-agent` (belongs to broader dev flow, separate plan).
- Centralizing `bug-reproducer-flutter` / `bug-fix-verifier-flutter` (separate plan).

## 12. Stage / agent / model assignment (consolidated)

| Stage | Owner | Model | Isolation | HITL? | Delegation rationale |
|---|---|---|---|---|---|
| 1 `/port:start` | orchestrator (main thread) | (user's session) | — | yes (PRD enrichment, worktree/change exists) | Tool-call coordination; HITL precludes sub-agent |
| 2 `/port:explore` | `dev-consult-agent` | opus | sub-agent | yes in HITL (Locate gate) | Origin codebase analysis needs strong reasoning + isolated context |
| 2-simple `/port:explore --simple` | `dev-consult-agent` (lean prompt, no worktree, no Locate gate) | opus | main context | yes (interactive Q&A) | No worktree to isolate into; HITL Q&A loop |
| 3 `/port:plan` | `pm-agent` + `designer-agent` (parallel) | sonnet | each sub-agent | no | Structured summarization from existing notes |
| 4 `/port:synth` | orchestrator (context.md / validate / lint) + `synth-agent` (artifact-generation loop) | orchestrator: user's session; synth-agent: **opus pinned** (D21) | synth-agent runs as sub-agent | no | Hallucination-sensitive; isolation keeps main context clean for Stage 5 HITL |
| 5 `/port:revise` | orchestrator (main thread) | (user's session) | — | yes (clarification + review gate) | Sub-agents cannot `AskUserQuestion`; HITL bound |
| 6 `/port:ship` | orchestrator (main thread) + `/commit` skill | (user's session) | — | no | ~5 tool calls; dispatch overhead > model savings |
| W `/port:ff` | orchestrator (main thread) wrapper | (user's session) | — | depends on `--auto` | Pure orchestration logic; must call other commands in sequence |

---

## Appendix: deferred reviewer-agent designs

Kept here for future reference if pragmatic-path evidence justifies adding them.

| Agent | Purpose | Trigger to revisit |
|---|---|---|
| `port-locate-reviewer` | Independent re-locate of dev-consult's Locate decision | Post-mortems show locate-misses despite hardened gate |
| `spec-judge-agent` (free-form) | Drift report on synthesis vs notes | B-citation check empirically misses semantic drift |
| `tech-lead-review-agent` | Senior-eng critique pre-review-gate | Reviewers consistently miss arch/scope issues at gate |
