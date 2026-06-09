# gogox-claude — Architecture & Rollout

One-pager for stakeholder discussion. Diagrams are ASCII so this renders anywhere (Slack, Confluence, GitHub, email).

## The problem

```
TODAY                                     WHAT WE WANT
─────────────────────                     ───────────────────────
Each gogox employee figures out           Workflows that work get
their own Claude Code workflow.           shared, reused, and improved.
Good prompts die in DMs.                  New hires inherit the playbook.
PMs don't know what Devs use.             Cross-role visibility.
No one knows what's working.              Usage data drives investment.
```

## Solution: a thin shared repo + install script

Not a platform. Not a service. A git repo + 80 lines of bash. The bet is that **content** (gogox-specific workflows + company context) is the moat — Anthropic's plugin marketplace will obsolete distribution mechanisms within 6 months, but it can never know who owns what at gogox.

## Architecture

```
                  ┌───────────────────────────────┐
                  │   gogox-claude (this repo)    │
                  │                                │
                  │   skills/{shared,pm,dev,design}│
                  │   agents/{shared,pm,dev,design}│
                  │   _template/SKILL.md           │
                  │   install.sh                   │
                  └───────────────────────────────┘
                                │
                                │  one-line curl
                                │  (run once per user)
                                ▼
                  ┌───────────────────────────────┐
                  │     User's laptop             │
                  │     ~/.claude/skills/         │
                  │       /prd-draft               │
                  │       /code-review             │
                  │       /linear-pickup           │
                  │       …                         │
                  │     ~/.claude/agents/         │
                  └───────────────────────────────┘
                                │
                                │  user invokes /<skill-name>
                                ▼
                  ┌───────────────────────────────┐
                  │      Claude Code              │
                  │      (CLI or desktop app)     │
                  │                                │
                  │  skill body runs:              │
                  │    1. log usage (jsonl)        │
                  │    2. read Gogox Context       │
                  │    3. call MCP tools           │
                  │       (Linear/Slack/Atlassian) │
                  │    4. produce output           │
                  └───────────────────────────────┘
                                │
                                │  every run appends one line
                                ▼
                  ~/.gogox-claude-usage.jsonl
                  (local file, user-owned, never sent anywhere)
                                │
                                │  quarterly review,
                                │  voluntary share-back
                                ▼
                  ┌───────────────────────────────┐
                  │   Sunset gates                │
                  │   Month 3: drop unused skills │
                  │   Month 6: <5 users → kill    │
                  └───────────────────────────────┘
```

## Why these design choices

| Decision | Why |
|---|---|
| **Manual install (no auto-update)** | Users own their copy. Local edits survive. No central server, no privacy story to manage. |
| **Folders are browsing-only** | After install, skills are flat (`/skill-name`). Categories are for picking what to install, not for invocation namespace. |
| **No central config skill** | MCP auth is per-user via Claude Code's `/mcp`. Team IDs and system owners are hardcoded into each skill's `# Gogox Context` section. |
| **Local jsonl logging, not central telemetry** | Zero infrastructure. No privacy questions. Users opt in by sharing during quarterly review. |
| **Pre-committed sunset gates** | Internal tools die from sprawl. Writing the kill criteria up front prevents "give it more time" rationalization later. |
| **Cross-role co-owners** | Bus factor 2. Forces multi-audience design — if a PM owner can't read a skill's SKILL.md, it doesn't merge. |

## Nested-spawn constraint (subagent depth)

The `--auto` dispatcher pipelines fan work out across nested agents. The official position bounds how deep that nesting may safely go.

**Official stance.** The [sub-agents docs](https://code.claude.com/docs/en/sub-agents) state plainly that "subagents cannot spawn other subagents" — nesting is unsupported. The [multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) post describes the supported shape: a lead (opus) orchestrator that spawns a flat layer of (sonnet) workers — depth-1, no deeper. Our `--auto` pipelines run one level deeper than that: `/ggx-dispatcher` (main) spawns a `general-purpose` worker (level-1), and a few stages inside that worker still spawn their own (level-2) subagents. We have observed empirically that **nested sonnet spawns work in practice today while nested opus spawns fail** — but that observation is reliance on undefined behavior, not a contract. Any Claude Code update may remove it without notice.

```
                  DEPTH        AGENT                        STATUS
─────────────────────────────────────────────────────────────────────────────
level-0   main / dispatcher    /ggx-dispatcher              supported
            │
            ▼  spawns
level-1   worker               general-purpose (/ggx-work)  supported (depth-1)
            │
            ▼  spawns           ┌─────────────────────────────────────────────┐
level-2   leaf subagent        │ opus  → BROKEN (fails inside a spawned worker)│
                               │ sonnet→ UNDEFINED but working today           │
                               └─────────────────────────────────────────────┘
                                 ↑ the danger zone — officially unsupported
```

**Why we still rely on level-2 sonnet:** the few remaining level-2 sonnet spawns (`/dev:figma`, `/dev:align`, `/dev:verify`, `/port:plan`) keep heavy per-node I/O out of the worker's main context. The R1–R5 rules below say which spawns were inlined away, which kept a fallback, and which deliberately did not.

| Rule | Scope | Decision | Pointer |
|---|---|---|---|
| **R1** | Heavy stages in `--auto` (`/opsx:apply`, `/code-review`, `/port:explore`, `/port:synth`) | Inlined — run in the level-1 worker itself, no level-2 spawn. The worker is opus-class so the reasoning quality is preserved. | `commands/dev/dev/apply.md:15-17`, `commands/dev/code-review.md` step 2, `commands/dev/port/explore.md` step 6, `commands/dev/port/synth.md` step 6 |
| **R2** | Surviving level-2 sonnet spawns (`/dev:figma`, `/dev:align`) | Kept as spawns, but each carries a **one-time inline fallback**: on spawn-failure the stage runs the subagent's contract inline in the worker, writing the SAME output files with the SAME status encoding so downstream parsers need zero changes. The fallback is not free: the raw payloads the subagent exists to absorb land in the worker's context window (read-once-write-discard mitigates; a high-node-count fallback hit is a data point for R5). | `commands/dev/dev/figma.md` Step 4b, `commands/dev/dev/align.md` Step 2b |
| **R3** | Independence-load-bearing auditors | NO inline fallback **on code platforms**. `verify-agent` audits code the worker itself wrote in `--auto`, so an inline verify would be the implementer self-auditing — collapsing the decorrelation the stage exists to provide. On `{platform}` ∈ {flutter, android, ios} (or any non-`prompt` / unresolved platform) spawn-failure stays a BLOCKED hard-fail. **Platform carve-out (GGC-11):** on `{platform}` = `prompt` (markdown/bash/workflow-JS diffs, already gated by the deterministic `scripts/prompt-lint.sh`) spawn-failure degrades to an **inline self-audit** carrying a loud `Provenance: inline-self-audit — DECORRELATION LOST` banner (never silent — the GGC-2 run self-audited invisibly, which is the failure mode the banner exists to surface). Decorrelation's value ∝ diff-size × logic-complexity × absence-of-deterministic-gate — all low on the prompt platform, so the trade is cheap there and unacceptable on code. (`/port:plan` is excluded for a different reason — see the note directly below this table.) | `commands/dev/dev/verify.md` Step 2/2b |
| **R4** | `claude -p` headless spawn | The officially supported non-nested escape hatch when a genuinely separate agent is needed (a separate OS process, so subagent depth does not apply). **Not yet wired** — documented as the forward-looking path so nobody re-invents an unsupported nesting trick. The intended first use is restoring `verify-agent` decorrelation on **code platforms** when run from a spawned worker (the R3 BLOCKED case): a headless `claude -p` auditor keeps both parallelism and a genuinely separate context, where the prompt-platform inline self-audit would not be safe. | `commands/dev/dev/verify.md` Step 2b (code-platform path) |
| **R5** | Migrating fan-out to Workflow | **Phase A validated (CAF-371, 2026-06-08); Phase B implemented (opt-in, pending flutter-repo validation).** `/ggx-dispatcher --workflow` drives the fan-out via `workflows/ggx-dispatch.workflow.js` instead of N×`Agent`; unset = today's path (incl. §5.0 inline ui-tweak). Phase B: under `--workflow`, ui-tweak runs in-script (`runUiTweak`) as a SCRIPT-spawned level-1 dual-judge panel — the opus judge spawns cleanly, dissolving §5.0 for the workflow path (§5.0 now applies to the default path only). Note: verify-agent (spawned by the worker inside `/dev:verify`) stays level-2 in BOTH paths — only what the SCRIPT spawns directly is level-1, so Phase B does NOT restore verify-agent independence. Phase C flips the default and removes §5.0/§6.1. | `commands/dev/ggx-dispatcher.md` §5.0/§5.2, `commands/design/ui-tweak/audit.md` (SYNC), `workflows/ggx-dispatch.workflow.js` |

`/port:plan` is excluded from R2's fallback for its own reason: its parallel-dispatch invariant (two agents in one message) resists inlining, and its existing retry-once + designer-placeholder degradation is already graceful. See `commands/dev/port/plan.md` Step 7.

The machine-checkable subagent invariants (output-file contracts, prohibitions) live in `agents/AGENTS.md`; the per-stage spawn-shape rationale lives in `commands/dev/ggx-dispatcher.md` §5.0 / §5.2 / §5.3.

## Rollout

```
Week 1                Week 2               Week 3              Month 3              Month 6
──────                ──────               ──────              ───────              ───────
Open empty repo.      Synthesize 1-on-1s.  Write 3 lighthouse  Sunset gate 1:       Sunset gate 2:
Write template,       Pick 3 highest-      skills with Gogox    drop unused          if active users
install.sh, README.   leverage workflow    Context.             skills.              < 5, kill the
Run 3 × 30-min        nodes.               Soft-launch to 3                          project.
1-on-1s with PM/      Find non-Dev         interviewees.        Decide if any
Dev/Designer.         co-owner from        Co-owner co-signs    skill earns Notion
                      interview pool.      first PR.            mirror or data
                                                                integration.
```

## What we'll do later (not in v1)

- Validation CI / GitHub Action for SKILL.md format. Defer until first real PR breaks something.
- CODEOWNERS file. Defer until skill count > 20.
- Slack bot. Defer until a specific high-frequency skill proves PM/Designer demand. (Notion mirror was built 2026-05 via `/sync-skills-to-notion` — see README § Skill discovery.)
- Data integration layer (skills reading past PRs/PRDs/postmortems). Gated on v1 usage data — only invest in integrations for skills that prove themselves used.
- Central telemetry endpoint. Local jsonl is sufficient for v1.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Bus factor 1 → repo dies when owner gets busy | Cross-role co-owner, identified during Week 1 1-on-1s. |
| Skills written by power users don't fit average employees' needs | Workflow-map interviews drive content selection, not the owner's intuition. |
| PM/Designer won't run a terminal install | One-line curl + Claude Code desktop app. Onboarding doc is one paragraph. |
| Anthropic plugin marketplace obsoletes our distribution mechanism | We bet on content (gogox-specific context) not infra. Distribution can be ported when marketplace lands. |
| Repo becomes a dotfiles graveyard | Pre-committed sunset gates at Month 3 and Month 6. |

## Ask

- **10% time for 2 co-owners** for the first 3 months. After Month 3 review, re-evaluate.
- **Buy-in on the Month 6 sunset rule.** If we miss the < 5 user threshold, we kill it without further discussion. Pre-committing to this protects the team from sunk-cost rationalization.
