---
name: audit
description: "Phase-2 stage of the /ui-tweak pipeline (DELIVER path only) — the deferred LLM logic gate, and the SOLE logic enforcement in the skill (there is no edit-time hook). Reached after the designer confirms the look and picks 'Ship it' (R18), or under --auto via the orchestrator's direct-ship auto-decision (same panel, both judges, no override; BLOCKED = loud-fail, no repair loop). Formats, then runs the decorrelated dual-judge panel (ui-verify-agent sonnet UI-lens + dev-reviewer opus behavior-lens, with a deterministic structural pre-pass) ONCE on the final cumulative diff (base_ref → working tree); BOTH must return CLEAR. CLEAR → writes .dev/ui-verify-pass.md (Status: CLEAR) → orchestrator advances to commit. BLOCKED → writes repair-context + bumps repair-count → orchestrator routes back to /ui-tweak:apply for an agent UI-only fix (max 3, then engineer card Ce); no standalone C2 card. Internal stage — designers run /ui-tweak."
---

<!-- RULE: command content is English. Designer-facing CARD text may be Traditional Chinese. -->

# `/ui-tweak:audit`

> **Single responsibility**: the deferred logic gate (Phase 2). Phase 1 (`/ui-tweak:preview`) already
> built the change onto a device and proved it compiles; this stage proves it changes **no logic**,
> ONCE, on the final cumulative diff, right before anything is committed or a PR is opened. It is the
> first link in the **pre-PR check series** (audit → commit → pr → review). Reached only on the
> deliver path (`.dev/ui-tweak/deliver` exists, set when the designer picks "Ship it"). Because the
> skill has no edit-time hook, this panel is the **only** thing standing between a logic change and a
> commit — so it always runs BOTH judges.

## Inputs

The final working-tree diff relative to `base_ref`; the platform.

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

Enumerate the changed files for the judges' context: `git diff "$BASE" --name-only`.

## Step 1 — format FIRST (part of the pre-PR series)

Run `/format --skip-commit`. The formatter may touch the changed files (whitespace) or introduce
small non-whitespace hunks; auditing AFTER format means the judges see exactly what will be committed
— no separate post-format re-audit is needed (the single audit below covers the final tree).

## Step 2 — dual judge, decorrelated (R6), on the FINAL cumulative diff

Spawn judges with the **Agent tool**, inputs `base=$BASE` (the pre-edit SHA, NOT HEAD) + platform.
The judges audit the **final cumulative diff** `git diff "$BASE"` (everything from the original
baseline through every correction and the format pass — this is what actually ships). The judges are
**read-only by tool grant** (no Write) and **return** their verdict text — this stage persists it.

**Always run BOTH judges in parallel** (one message, two Agent calls). Since the skill has no
edit-time hook, there is no upstream proof that the diff is value-only — so neither judge may be
skipped. They are decorrelated by model tier (`ui-verify-agent` = sonnet, `dev-reviewer` = opus) so
their misses are not positively correlated; `dev-reviewer` additionally runs a deterministic
structural pre-pass (added imports / new call heads / renamed identifiers / changed `@+id`) that
BLOCKs non-inert-UI structural edits regardless of how plausible they read.

**Persist the verdicts** from each judge's returned text:
- `ui-verify-agent` text → `.dev/ui-verify-pass.md`
- `dev-reviewer` text → `.dev/dev-reviewer-pass.md`

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

`--auto` DOES reach this stage (D7, revised): `/ui-tweak:ff --auto` auto-takes the direct-ship path
after its single apply (used by `/ggx-work` / `/ggx-dispatcher` for `design bug` tickets — the
dispatcher runs that lane inline in its main session precisely so this stage's opus judge can spawn,
see `ggx-dispatcher.md` §5.0). **The panel is UNCHANGED under `--auto`**: both judges always spawn,
same tiers (`ui-verify-agent` sonnet + `dev-reviewer` opus), same unanimous-CLEAR rule — full
tier-decorrelation, no model override, neither judge skipped.

The only `--auto` difference is BLOCKED handling: print exactly one deterministic stderr line and
exit non-zero (no agent-repair loop under `--auto` — v1 keeps loud-fail; the caller classifies the
ticket `failed` and leaves `dispatcher-dev-in-flight` as the human-resume signal):

```
UI-TWEAK BLOCKED (<ui-verify|dev-reviewer>): <one-line reason> — reverted, no changes kept.
```

(Still run the Step-4 revert — `git checkout -- $(git diff "$BASE" --name-only)` — before exiting,
so nothing un-audited is left in the tree; skip the repair-context/repair-count writes.)

## HITL / Stop

audit is a mechanical gate — no HITL gate, no designer-facing card here (the orchestrator owns the
wayfinding cards). On success print: `Audit CLEAR (dual-judge on final diff). Next: commit.` On block
print: `Audit BLOCKED → repair attempt <n>/3 (agent fix in apply).` — the orchestrator routes to the
agent-repair loop (or the engineer card Ce at 3).
