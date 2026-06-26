---
name: ticket-analyzer-agent
description: >
  Cloud routine template that runs `/ticket-analyze --non-interactive` in
  batch mode, judging the team's **actionable pool** (Triage/Backlog/To-Do,
  any assignee — GGC-95) for pipeline readiness, auto-classifying strong
  signals (GGC-96), and writing the verdict labels
  (`ready-to-port` / `ready-to-dev` / `need-revision` / `need-dependency`)
  + structured comments back to Linear. Pure ANALYSIS — never writes code,
  never opens PRs, never invokes any pipeline. Designed to fire 30 minutes
  BEFORE each `ggx-dev-agent` slot so freshly-labeled tickets are picked
  up by the very next dev-agent fire. Pair file:
  `ticket-analyzer-agent.routine.json`.
---

# ticket-analyzer-agent — upstream cloud analyzer (feeds ggx-dev-agent)

This doc is the template: §2 has the canonical routine prompt and
`ticket-analyzer-agent.routine.json` (next to this file) has the matching
`RemoteTrigger action:create` body. A colleague copies both, swaps the
placeholders in §3, and creates their own routine.

> **Companion of [`ggx-dev-agent`](ggx-dev-agent.md).** That routine
> EXECUTES (`ready-to-*` → pipelines → PRs); this one JUDGES (To-Do →
> verdict labels). They communicate exclusively through the analyzer
> labels — see the "Label ownership boundary" table in
> `commands/dev/ggx-dispatcher.md`. Deliberately NOT merged into the
> dispatcher/dev-agent: the label window between analyze and dispatch is
> the human review point for LLM verdicts (see the 2026-06-04 evaluation —
> revisit only after the analyzer's misjudgment rate is known).

## 1. Purpose & schedule design

### What it is

An unattended cloud run of `/ticket-analyze` batch mode
(`commands/dev/ticket-analyze.md`). Per fire it:

1. Discovers analysis candidates across the team **actionable pool**
   (Triage/Backlog/To-Do, **any assignee** — GGC-95): fresh tickets +
   `need-revision` / `need-dependency` re-runs. Output is label-only — the
   routine never assigns (pull model).
2. Bootstraps gogox-claude (install.sh ONLY — no Flutter, no openspec,
   no gh; the analyzer never builds or pushes).
3. Follows `/ticket-analyze --non-interactive` from the target repo's
   cwd (profile resolution needs it).
4. Emits a structured outcome report.

### The slots + 30-minute offset (intentional)

```
TW  11:30  17:30   ← this routine (analyze, label)
TW  12:00  18:00   ← ggx-dev-agent (pick up ready-to-*, execute)
    └lunch┘ └after-work┘
```

Two slots a day, both chosen so the agents work while the human is away:
the lunch break (TW 12:00) and after work (TW 18:00). Each analyzer fire
completes ~30 minutes before its dev-agent fire, so a verdict's
`ready-to-*` label is consumed by the very next execution slot. If you
change one schedule, keep the offset: analyzer BEFORE dev-agent, with
enough gap for the batch to finish (a typical batch is minutes, not
hours — it does no builds).

### Concurrency safety (validated in the command itself)

`/ticket-analyze` skips anything `ready-to-*` / `dispatcher-*-in-flight` /
`need-spec-review` and re-checks dispatcher locks immediately before each
write (its Step 8), so this routine can overlap a running `ggx-dev-agent`
fire without racing it. The prompt additionally instructs: on conflict,
skip the ticket — never fight the dev-agent for it.

## 2. The routine prompt (canonical, de-personalized)

The live prompt is embedded in `ticket-analyzer-agent.routine.json`. Key
structural choices, mirrored from `ggx-dev-agent`:

- **Phase 1 DISCOVERY first** — zero candidates → report + STOP before
  installing anything. Keeps the common no-op fire cheap.
- **Phase 0 BOOTSTRAP is minimal** — `install.sh` + `cd <target-repo>`.
  The analyzer is Linear-only: no toolchain installs at all.
- **Phase 2 follows the local command file** —
  `~/.claude/commands/ticket-analyze.md` (flattened path), with the cloud
  overrides listed below.
- **Phase 3 REPORT always emits**, even on 0 candidates.

Cloud-specific overrides (same family as ggx-dev-agent's):

1. Linear MCP namespace: `mcp__claude_ai_Linear__*` → `mcp__Linear__*`.
2. `--team:<KEY>` fallback if profile resolution can't derive the team.
3. `--non-interactive` is mandatory — confirm gates skipped, inferred
   dependencies recorded but never blocking (the command's own contract).
4. Respect every skip filter; per-ticket failures never abort the batch.
5. Label writes are exactly the command's mutually-exclusive full-set
   rewrites — nothing outside that contract.
6. Step 8 conflict → skip the ticket (a dev-agent fire may be running).

## 3. De-personalization rules (create YOUR own routine)

Swap these placeholders in `ticket-analyzer-agent.routine.json`:

| Placeholder | Live example (Charlie / CAF) | Notes |
|---|---|---|
| `<TEAM_KEY>` | `CAF` | Linear team key; also the `--team:` fallback value |
| `<TARGET_REPO>` | `gogox-client-flutter` | repo whose profile resolves the team; cloned WITHOUT push permission |
| `<ENVIRONMENT_ID>` | `env_01WsWtJi19fQNPnJGzww3X7p` (dispatcher-env) | any anthropic_cloud env works |
| `<LINEAR_CONNECTOR_UUID>` | per-user | your own claude.ai Linear connector |
| cron | `30 3,9 * * *` (UTC) = TW 11:30 / 17:30 | keep the 30-min offset before your dev-agent slots (`0 4,10` = TW 12:00 / 18:00) |

"assignee=me" resolves through the Linear connector's OAuth identity, so
each colleague's routine analyzes their own queue automatically — no
hardcoded user ids.

Live instance (Charlie): `trig_01VxjNnJXw3F3y9qe3fgmYAj`, created
2026-06-04, model `claude-opus-4-8`.

## 4. Security & scope notes

- Sources: `gogox-claude` + `<TARGET_REPO>`, **neither with
  `allow_unrestricted_git_push`** — the routine has no legitimate push.
- Only MCP connection is Linear. No GitHub MCP, no Slack.
- The only writes are `/ticket-analyze`'s own Linear label/comment writes;
  the prompt forbids invoking `/ggx-work`, `/ggx-dispatcher`, or any ff
  pipeline.

## 5. Known limitations / follow-ups

- **Verdict quality is unproven** — this routine exists precisely to
  collect that data. Review the analyzer's Linear comments for a few
  weeks before trusting it enough to consider `--analyze-first`
  integration into `/ggx-dispatcher` (evaluated and deferred 2026-06-04).
- Jira teams run in the command's degraded mode (string labels only);
  this template targets Linear teams.
- `--dry-run` exists on the command — flip the prompt to it if you want a
  no-write observation period first.

## 6. Fan-out execution + raised cadence (GGC-97 / P2)

The batch sweep is now backed by `workflows/ticket-analyze-fanout.workflow.js`
— a thin Workflow harness that fans out per-ticket analysis (Step 2.7 classify +
Step 3 completeness) in parallel, computes the Step-5 dependency graph in a
single JS barrier, then fans out the label-only writes. The per-ticket judgment
still lives in `commands/dev/ticket-analyze.md` (the harness only orchestrates),
so there is no logic to keep in sync. Model tiering: classify+completeness on
sonnet, writes on haiku, **no opus** — the analyzer judges, it never implements.

**Execution path:** the routine runs `/ticket-analyze --non-interactive`, which
does discovery + the re-analysis filter (its Step 1.5) and then drives the
fan-out workflow over the surviving roster. Per-ticket failure resolves to an
`errored` row and never aborts the batch (the barrier filters nulls).

**Raised cadence:** because the fan-out completes in **minutes** (no builds, no
opus, parallel agents), the old 2×/day slots are no longer the constraint — the
queue can be kept continuously classified. Recommended: tighten the cron to
hourly during working hours (e.g. `0 1-10 * * *` UTC ≈ TW 09:00–18:00), keeping
the 30-min offset before each `ggx-dev-agent` slot if you still pair them. Keep
2×/day only if you deliberately want a slower, lower-cost cadence.

**F2 watch (load-bearing, per the plan review):** the analyzer auto-tags the
pool `ready-to-*` **label-only** — a ticket then waits unassigned until a human
pulls it (assigns). The workflow logs an **unclaimed-ready** list each run; the
routine report must surface it so a `ready` + unassigned ticket never silently
looks done. Track the `ready-to-*` → assigned latency before treating throughput
as "solved"; if that tail grows, revisit the capped-auto-pull alternative
(`plans/ticket-analyzer-evolution.md` §2.5-F2).
