---
name: apply
description: "Stage 3 of the /ui-tweak pipeline — produce ONE UI diff and nothing else (iteration is build-free, R18). Parse → ground (inline Figma into a structured target checklist) → locate+map → bidirectional coverage gate → value/UI-only Edit. Never builds, audits, or commits (building is /ui-tweak:preview, Phase 1; the logic audit is /ui-tweak:audit, Phase 2). Logic is enforced by the deferred dual-judge panel in /ui-tweak:audit — there is NO edit-time hook. Also runs in REPAIR MODE when .dev/ui-tweak/repair-context exists (a build-fail or audit-block the agent fixes UI-only, max 3). Internal stage: designers run /ui-tweak, not this directly — a misdirect guard routes them back."
---

<!-- RULE: ALL content, including designer-facing CARD text, is English. No Chinese / non-ASCII. -->

# `/ui-tweak:apply`

> **Single responsibility**: produce exactly one UI diff. It does NOT build, audit, or commit —
> `/ui-tweak:preview` builds (into a device, Phase 1), `/ui-tweak:audit` runs the logic audit
> (Phase 2). Logic is not enforced at edit time — the deferred dual-judge panel in `/ui-tweak:audit`
> is the only gate (a finding there reverts the whole run). Mirrors `/dev:apply` granularity.

## Inputs

`<source> [figma-url] [--auto]` — `<source>` is free text or a ticket (ID/URL). Optional trailing
`[figma-url]`. `--auto` skips only the interactive plan confirmation.

## Outputs

- A working-tree UI diff (uncommitted).
- `.dev/ui-tweak/base_ref` — pre-edit SHA (the cumulative-diff baseline for preview/audit).
- `.dev/ui-tweak/figma-context.md` — the structured target checklist (grounding receipt).
- `.dev/ui-tweak/.not-deliverable` — written iff any target is NOT-FOUND (quality-bar marker, R1).

## Step 0a — misdirect guard (R5/D11)

If the environment variable `UI_TWEAK_FF` is **not set**, a designer typed `/ui-tweak:apply`
directly (this stage is internal). Do **not** execute. Print card **C-MISDIRECT** and STOP:

```
📍 Looks like you called an internal step directly.
📦 No worries — nothing has changed.
👉 Just type:  /ui-tweak "describe what you want to change in one sentence"  and I'll run the whole flow for you.
```
(C-MISDIRECT is a plain-text notice — it offers no choice, so no `AskUserQuestion` and no footer.)

A standalone `apply` must NEVER leave an unverified diff as a terminal designer state — the deferred
audit panel runs only through the orchestrator. Only proceed past 0a when `UI_TWEAK_FF=1` (the
orchestrator sets it).

## Step 1 — usage log + resolve profile

```bash
echo "{\"skill\":\"ui-tweak:apply\",\"ts\":\"$(date -u +%FT%TZ)\"}" >> ~/.gogox-claude-usage.jsonl 2>/dev/null || true
REPO_ROOT=$(git rev-parse --show-toplevel)
```

Resolve the platform profile: `<repo>/.gogox-claude.yaml` → `platform`; fallback
`~/.claude/commands/profiles/registry/<basename>.yaml`. Note the friendly repo / screen name for
designer-facing cards.

## Step 2b — REPAIR MODE (R18): if `.dev/ui-tweak/repair-context` exists

A prior stage failed and the orchestrator routed back here for an **agent fix** (NOT a new designer
requirement). Read `.dev/ui-tweak/repair-context`:
- `kind: build` → fix the compile error UI-only (correct the value/property that broke the build).
- `kind: audit` → redo the change as **pure UI**, removing whatever the dual-judge flagged as touching
  how the program runs.

Treat the repair-context as the requirement (skip the ticket/Figma re-parse — `base_ref`,
`figma-context.md`, and `ticket.json` already exist). Keep `base_ref`. Fix via Edit (Step 6), then
clear the downstream so the change re-validates from Phase 1:
```bash
rm -f "$REPO_ROOT/.dev/ui-tweak/repair-context" "$REPO_ROOT/.dev/ui-tweak/build-pass" \
      "$REPO_ROOT/.dev/ui-tweak/preview-shown" "$REPO_ROOT/.dev/ui-tweak/deliver" \
      "$REPO_ROOT/.dev/ui-verify-pass.md" "$REPO_ROOT/.dev/dev-reviewer-pass.md"
# keep preview-requested (still heading to preview) and repair-count (caps at 3 → engineer card Ce)
```
Then Stop. (The cap is enforced by the orchestrator: at `repair-count >= 3` it renders Ce instead of
re-entering apply.) The rest of this file is the normal first-pass path.

## Step 3 — parse source (read-only; cache the ticket) [O1]

- Ticket (ID/URL): fetch via `mcp__claude_ai_Linear__get_issue` or Jira (`_ticket-lib.md`),
  **read-only** (never change status/assignee, never comment). Derive the requirement from
  title+description+comments. Cache the fetched ticket to `.dev/ui-tweak/ticket.json` so the deliver
  path does not re-fetch.
- Free text: use it verbatim as the requirement.

## Step 3a — ground: inline Figma → structured target checklist (D2)

Figma URL precedence: trailing `[figma-url]` (highest) > a `figma.com/(design|file|board|slides|make)/`
URL extracted from the ticket. **Figma is OPTIONAL (D10)** — the requirement is often fully specified
by the ticket text.

- **Has a Figma URL** → spawn `figma-subagent` (Agent tool — keeps the heavy per-node JSON in the
  subagent; only the distilled checklist returns). Persist `.dev/ui-tweak/figma-context.md`:
  ```
  Fetched: <ISO> <node-ids>
  ## Target values (checklist)
  - [ ] T1  property=button.height  target=48dp     node=123:45
  - [ ] T2  property=button.corner  target=8dp      node=123:45
  - [ ] T3  property=button.bg      target=#0A7CFF  node=123:46
  ```
  Each row is one concrete, codeable target. The subagent's job is to **enumerate every visual
  property the frame pins** — completeness becomes countable rows.
- **No Figma URL** → derive the checklist from the requirement (`button +5dp taller` → `- [ ] T1
  height +5dp`); first line `Fetched: SKIPPED — derived from requirement`.
- **Figma fetch FAILED (DEGRADED) → never blocks (D10)**: write first line
  `Fetched: DEGRADED — <err>`, fall back to a requirement-derived checklist, print a warning, and
  **continue** (Figma is optional — a fetch failure never blocks the run). The
  grounding provenance (figma-confirmed / SKIPPED / ⚠ DEGRADED-estimated) is stamped on card C1 and
  in the PR body (R2) so an engineer knows whether values were Figma-confirmed.

## Step 4 — locate + map (+ shared-token blast radius, R12)

For each checklist row `Ti`: grep the target screen/component, record
`{Ti, file, current, target}`, classify the file (UI-eligible vs forbidden), tick the box when a
code site is found.

- **Shared-token check (R12)**: grep the reference count of each edited token/resource key across the
  UI surface. If `>1`, mark the coverage row `SHARED (N refs)` and list the other affected screens —
  this is the cheapest defense against the most common over-scope vector (one `dimens` entry
  restyling five screens). Feeds C1/C4 and the PR body.
- If a `Ti` has no code site → mark it `NOT-FOUND` (feeds the Step-5 quality bar).
- If a value lives **only** in a forbidden file → STOP and render card **C6** (this is not a pure-UI
  change; never emit a raw `FAIL:` string to a designer). **C6 is an `AskUserQuestion`** (`header:
  "What next"`), unless reached under `--auto` (then print the question line to stderr and stop —
  `--auto` never prompts):
  - **question**: "I can't find that place in "<friendly screen/repo name>", or it touches how the
    program runs. Nothing has changed. How would you like to go on? (Or pick Other and describe it a
    different way.)"
  - **options**: `Describe it differently` — "Tell me another way to say it and I'll try again." /
    `This may need an engineer` — "I'll write up a short summary for an engineer."
  - **routing**: `Describe it differently` / **Other** → re-run apply with the new wording;
    `This may need an engineer` → prepare the summary. Remove the `base_ref` marker either way (see
    Failure / revert) so the walker doesn't think a diff exists.

## Step 5 — coverage gate (D2) + quality-bar marker (R1) + present plan

**Bidirectional coverage gate** (mirrors spec-lint B-citation):

- **Forward (catches misses)**: every `Ti` must resolve to (a) a planned edit, (b) `ALREADY-MATCHES`
  (code already equals target, with the matched site), or (c) `NOT-FOUND`. No `Ti` may be silently
  dropped.
- **Reverse (catches hallucination / scope-creep)**: every planned edit must cite a backing `Ti`
  (free-text-only runs cite the requirement). An edit with no backing target is flagged.
- **Framing (R14)**: the coverage gate is a **completeness (G3) control, not a logic (G2) control** —
  it ensures the right things changed and nothing extra, but it does NOT constrain logic (the dual
  judge in `/ui-tweak:audit` does, at handoff). Do not present it as a logic guarantee.

**Quality-bar marker (R1/D10)**: if **any `Ti` is NOT-FOUND**, write `.dev/ui-tweak/.not-deliverable`
listing the unmet targets; otherwise ensure that file does **not** exist. `Fetched: SKIPPED/DEGRADED`
does **not** trip the bar (Figma is optional). This marker lets the orchestrator structurally hide
card C1's "I'm done — show me" / "Ship it" option — the designer never has to read "NOT-FOUND".

**Present the plan** (coverage table — `Ti | property | target | code site | current | new | shared? |
status`): in default mode take one plan confirmation (the designer may adjust a target in place —
correction Level A, no re-arm). `--auto` skips the interactive confirm but still records the plan.

**Record the base ref** (`base_ref` must survive to preview/audit as the cumulative-diff baseline).
If `base_ref` already exists (a correction or repair re-run), do **not** overwrite it (keep the true
pre-edit baseline so preview/audit always diff the cumulative change):

```bash
mkdir -p "$REPO_ROOT/.dev/ui-tweak"
[ -f "$REPO_ROOT/.dev/ui-tweak/base_ref" ] || git rev-parse HEAD > "$REPO_ROOT/.dev/ui-tweak/base_ref"
```

## Step 6 — edit, value/UI-only

Make the planned change with `Edit` / `Write`; inspect with `Read` / `Grep` / `Glob`. Keep the diff
to **pure-visual values, layout, and structure — no logic, no build config, no source rewrites**.
There is no edit-time hook: logic is enforced by the deferred dual-judge panel in `/ui-tweak:audit`,
which runs once on the cumulative diff at handoff and reverts the whole run on any finding (max 3
agent repairs, then the engineer card Ce). Prefer the narrowest change that satisfies the targets so
the audit stays clean, and never route an edit through Bash (use `Edit` / `Write`).

## Failure / revert

`apply` does no build/audit, so there is no "revert the whole run" here. If Step 4 determined the
change is not pure-UI → STOP (card C6) and remove the `base_ref` marker you just wrote (so the walker
does not think a diff exists).

## HITL / `--auto`

Plan-confirmation gate (default keeps it; `--auto` skips the interactive confirm but still records
the plan). `--auto` NEVER causes build/audit/commit (those are preview / audit).

## Stop

Print: `UI diff produced (N files). base_ref recorded. Next: card C1 (the orchestrator asks
"show me on a phone / more changes"; building happens in /ui-tweak:preview only when the designer
picks "show me").` (A designer never types the internal stages.)
