---
name: audit
description: "Phase-2 stage of the /ui-tweak pipeline (DELIVER path only) — the deferred LLM logic gate. Reached only after the designer confirms the look and picks 'Ship it' (R18). Formats, then runs the decorrelated dual-judge panel (ui-verify-agent sonnet UI-lens + dev-reviewer opus behavior-lens, with a deterministic structural pre-pass) ONCE on the final cumulative diff (base_ref → working tree); BOTH must return CLEAR. Risk-tiered: strict + no-MIXED-file diffs run only ui-verify-agent. CLEAR → writes .dev/ui-verify-pass.md (Status: CLEAR) → orchestrator advances to commit. BLOCKED → writes repair-context + bumps repair-count → orchestrator routes back to /ui-tweak:apply for an agent UI-only fix (max 3, then engineer card Ce); no standalone C2 card. Runs UNARMED. Internal stage — designers run /ui-tweak. Spec: §4.4a."
---

<!-- RULE: command content is English. Designer-facing CARD text may be Traditional Chinese. -->

# `/ui-tweak:audit`

> **Single responsibility**: the deferred logic gate (Phase 2). Phase 1 (`/ui-tweak:preview`) already
> built the change onto a device and proved it compiles; this stage proves it changes **no logic**,
> ONCE, on the final cumulative diff, right before anything is committed or a PR is opened. It is the
> first link in the **pre-PR check series** (audit → commit → pr → review). Reached only on the
> deliver path (`.dev/ui-tweak/deliver` exists, set when the designer picks "Ship it"). Runs while
> **unarmed**.

## Inputs

The final working-tree diff relative to `base_ref`; the disarmed sentinel's `policy`; the platform.

## Step 0a — misdirect guard (R5/D11)

If `UI_TWEAK_FF` is not set, print **C-MISDIRECT** (see `/ui-tweak:apply` Step 0a) and STOP — a
designer never types `/ui-tweak:audit`.

## Step 0 — precondition

```bash
WT=$(git rev-parse --show-toplevel)
[ -f "$WT/.dev/ui-tweak/deliver"   ] || { echo "FAIL: /ui-tweak:audit is deliver-path only (no .dev/ui-tweak/deliver)." >&2; exit 1; }
[ -f "$WT/.dev/ui-tweak/base_ref"  ] || { echo "FAIL: no .dev/ui-tweak/base_ref — run /ui-tweak:apply first." >&2; exit 1; }
grep -q '^Status: PASS' "$WT/.dev/ui-tweak/build-pass" 2>/dev/null || { echo "FAIL: build did not pass — run /ui-tweak:preview (Phase 1) first." >&2; exit 1; }
BASE=$(cat "$WT/.dev/ui-tweak/base_ref")
```

Confirm the sentinel is **disarmed** (audit needs a shell + spawns agents; it must run unarmed).
Determine the `policy` (read the disarmed sentinel) and whether the diff touches any **MIXED** file
(`*.kt` / `*.swift` / `*.dart`): `git diff "$BASE" --name-only`.

## Step 1 — format FIRST (part of the pre-PR series)

Run `/format --skip-commit`. The formatter may touch the changed files (whitespace) or introduce
small non-whitespace hunks; auditing AFTER format means the judges see exactly what will be committed
— no separate post-format re-audit is needed (the single audit below covers the final tree).

## Step 2 — dual judge, decorrelated (R6) + risk-tiered (R15), on the FINAL cumulative diff

Spawn judges with the **Agent tool**, inputs `base=$BASE` (the pre-edit SHA, NOT HEAD) + platform.
The judges audit the **final cumulative diff** `git diff "$BASE"` (everything from the original
baseline through every correction and the format pass — this is what actually ships). The judges are
**read-only by tool grant** (no Write) and **return** their verdict text — this stage persists it.

- **Risk tier (R15)**: if `policy == strict` **and** the diff contains **no MIXED file** (the hook's
  `value_only_ok` already proved value-only inside PURE_UI files) → run **only `ui-verify-agent`**;
  skip `dev-reviewer` (the hook is already the stronger guarantee — a second LLM is pure cost).
- **Otherwise** (any MIXED file in the diff, or `policy == open`) → run **both** judges in parallel
  (one message, two Agent calls). They are decorrelated by model tier (`ui-verify-agent` = sonnet,
  `dev-reviewer` = opus) so their misses are not positively correlated; `dev-reviewer` additionally
  runs a deterministic structural pre-pass (added imports / new call heads / renamed identifiers /
  changed `@+id`) that BLOCKs non-inert-UI structural edits regardless of how plausible they read.

**Persist the verdicts** from each judge's returned text:
- `ui-verify-agent` text → `.dev/ui-verify-pass.md`
- `dev-reviewer` text → `.dev/dev-reviewer-pass.md` (omit when skipped by the risk tier; record
  `Status: CLEAR (skipped — strict PURE_UI-only)` so the adjudication is unambiguous).

Each persisted file's first line must be `Status: CLEAR | BLOCKED`.

**coverage assertion (D2, defense-in-depth)**: instruct the judge(s) to read
`.dev/ui-tweak/figma-context.md` and assert the diff covers every `WILL-EDIT` row; a miss → BLOCKED.
When `Fetched: DEGRADED/SKIPPED`, reverse-citation is non-grounding — judge on UI-vs-logic merits.

## Step 3 — adjudicate (unanimous CLEAR)

- Require **every run judge `Status: CLEAR`**. Any `BLOCKED` (or a missing file / agent error) →
  treat the whole run as BLOCKED → Step 4 (agent repair).
- All CLEAR → ensure `.dev/ui-verify-pass.md` first line is `Status: CLEAR` (append the dev-reviewer
  conclusion so the file records both judges). Then:
  ```bash
  rm -f "$WT/.dev/ui-tweak/.not-deliverable"   # R1: audit CLEAR confirms the change is deliverable
  ```
  STOP — the orchestrator advances to `commit` (card C4 → `/commit` → pr → review).

## Step 4 — audit BLOCKED → agent repair (R18, max 3; NOT a designer card)

A logic block is the agent's implementation problem — the designer never sees a raw "blocked" card.
Hand it to the agent-repair loop instead:

```bash
git checkout -- $(git diff "$BASE" --name-only)                    # drop the flagged edit + format pass
n=$(cat "$WT/.dev/ui-tweak/repair-count" 2>/dev/null || echo 0); echo $((n+1)) > "$WT/.dev/ui-tweak/repair-count"
{ echo "kind: audit"; echo "finding:"; <one-line plain summary of what touched logic>; } > "$WT/.dev/ui-tweak/repair-context"
```

The orchestrator's loop sees `repair-context` and routes back to `/ui-tweak:apply` (repair mode) when
`repair-count < 3` — `apply` redoes the change as pure UI and clears the downstream markers (see
`/ui-tweak:apply` Step 2b). At `repair-count >= 3` the loop renders the **engineer card Ce** instead
(the change likely can't be done without touching how the program runs). Do NOT write a `BLOCKED`
`ui-verify-pass.md` (there is no standalone C2 card anymore). STOP and report.

## `--auto` — failures must be LOUD (R13)

`--auto` cannot reach this stage normally (handoff requires a human picking "Ship it" on card C1, and
`--auto` shows no cards — D7). If audit is ever reached under `--auto`, a BLOCKED result prints exactly
one deterministic stderr line and exits non-zero (no agent-repair loop under `--auto`):

```
UI-TWEAK BLOCKED (<ui-verify|dev-reviewer>): <one-line reason> — reverted, no changes kept.
```

## HITL / Stop

audit is a mechanical gate — no HITL gate, no designer-facing card here (the orchestrator owns the
wayfinding cards). On success print: `Audit CLEAR (dual-judge on final diff). Next: commit.` On block
print: `Audit BLOCKED → repair attempt <n>/3 (agent fix in apply).` — the orchestrator routes to the
agent-repair loop (or the engineer card Ce at 3).
