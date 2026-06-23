---
name: audit
description: "Phase-2 stage of the /ui-tweak pipeline (DELIVER path only) — the deferred LLM logic gate, and the SOLE logic enforcement in the skill (there is no edit-time hook). Reached after the designer confirms the look and picks 'Ship it' (R18), or under --auto via the orchestrator's direct-ship auto-decision (same panel, both judges, no override; BLOCKED = loud-fail, no repair loop). Formats, then runs the decorrelated dual-judge panel (ui-verify-agent sonnet UI-lens + dev-reviewer opus behavior-lens, with a deterministic structural pre-pass) ONCE on the final cumulative diff (base_ref → working tree); BOTH must return CLEAR. CLEAR → writes .dev/ui-verify-pass.md (Status: CLEAR) → orchestrator advances to commit. BLOCKED → writes repair-context + bumps repair-count → orchestrator routes back to /ui-tweak:apply for an agent UI-only fix (max 3, then engineer card Ce); no standalone C2 card. Internal stage — designers run /ui-tweak."
---

<!-- RULE: command content is English. Designer-facing CARD text may be Traditional Chinese. -->

<!-- SYNC: workflows/dispatch-fanout.workflow.js `runUiTweak` (Phase B of the R5
     migration) re-implements this panel's contract for the /ggx-dispatcher
     Workflow fan-out: the SCRIPT spawns ui-verify-agent (sonnet) + dev-reviewer
     (opus) as level-1 agents, BOTH must be CLEAR, --auto is loud-fail (no
     repair loop). If the judge contract here changes (figma-context read,
     WILL-EDIT coverage assertion, both-must-be-CLEAR, loud-fail semantics,
     diff-computed-once-and-fed-inline, deterministic structural pre-pass
     short-circuit), update runUiTweak's judge prompts TOO. See
     ggx-dispatcher.md §5.2 (Phase B) and ARCHITECTURE.md "Nested-spawn
     constraint" R5. -->

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

## Step 1b — compute the diff ONCE (feed inline to both judges)

Compute the final cumulative diff and the changed-file list a **single time**, AFTER the format pass,
and hold both in memory. Both judges receive this precomputed text inline — neither re-runs `git diff`
nor re-reads the changed files, so the only per-judge cost is the model call itself (the git/file IO
happens once, not once per judge):

```bash
CHANGED_FILES=$(git diff "$BASE" --name-only)
DIFF_TEXT=$(git diff "$BASE")          # the exact text that ships; computed once, fed to both judges
```

`$DIFF_TEXT` + `$CHANGED_FILES` are the judges' sole diff input below. (For a very large diff —
many changed files — you MAY additionally fan the judges out per-file, but a UI tweak diff is almost
always tiny, so the default single-diff-inline path is both correct and faster; the bottleneck is the
opus model latency, not the diff size, which is exactly what Step 1c targets.)

## Step 1c — deterministic structural pre-pass (fast; may short-circuit the opus call)

Before spawning the judges, run a **deterministic, LLM-free structural scan** over `$DIFF_TEXT`
(grep only — no model call). It short-circuits to BLOCKED **only** on added-line signals that are
**unambiguously runtime behavior** — never on inert UI. This is a **latency optimization, not the
gate**: when it fires, the slow opus `dev-reviewer` call is skipped; when it does NOT fire, BOTH
judges still run on the full diff (Step 2/3) and that panel — opus `dev-reviewer` doing the real
structural reasoning — is the actual arbiter. So **under-matching here is safe** (the panel catches
it); **over-matching is the harm** (a false BLOCK reverts a legitimate UI change). The signal set is
therefore deliberately tight: allow-by-default, deny only unambiguous behavior.

**Not scanned / not a signal** (these are inert UI — they fall through to the panel):

- **Any `import`.** An import line is inert; behavior comes from *using* the symbol, which the
  behavior signals below (or the panel) catch. (Generalizes GGC-38 — which previously allowlisted
  only design-system style/token imports — to: no import trips the scan at all. A behavioral import
  like a service / controller is caught by the *use* of its symbol, not the import line. CAF-555's
  `import '.../gestures.dart'` no longer trips the scan; it still BLOCKs, but on its
  `TapGestureRecognizer` + `initState`/`dispose`, which is genuine behavior.)
- **Pure layout / structure widgets** — `LayoutBuilder`, `ConstrainedBox`, `IntrinsicHeight`, `Row`,
  `Column`, `Stack`, `Padding`, `SizedBox`, `Align`, `Center`, `Expanded`, `Flexible`, `Wrap`, …
  and their `builder:` closures (`=>`), `BoxConstraints`, and collection-`if` / `for` inside a widget
  list. These ARE ui-tweak's allowed scope (visual / layout / structure). (This is why CAF-540 —
  `LayoutBuilder` + `ConstrainedBox` + `IntrinsicHeight` for vertical centering — must NOT be blocked
  here: it reaches the panel and, being pure layout, clears.)
- **Style / token references** — `App*`-prefixed const accessors (`AppColors.blue100`,
  `AppTypography.fontSizeCaption`, `AppSpacing.medium`).
- **Generic syntactic shapes** — bare `=>`, `return `, `new X`, `class `, `if (` / `for (` are NOT
  flagged: they appear constantly in declarative widget trees (builder closures, collection-`if`,
  `return SomeWidget(...)`). A genuine behavioral conditional / closure is caught by the panel.

**Behavior signal set (the ONLY short-circuit triggers).** Scan ADDED lines for tokens that are
essentially always runtime behavior — Flutter lifecycle, state mutation, gesture recognizers,
event-listener wiring, async, navigation/routing, and provider/state-container access:

```bash
# Examine ADDED lines only (leading '+', excluding the '+++' file header).
ADDED=$(printf '%s\n' "$DIFF_TEXT" | grep -E '^\+' | grep -vE '^\+\+\+')

# Unambiguous runtime-behavior signals only. macOS/BSD grep -E safe (no \b). Tight
# by design: imports, layout/structure widgets, style tokens, and generic syntactic
# shapes (=>, return, if (, for (, new X, class) are intentionally ABSENT — they are
# inert UI and are left for the dual-judge panel. Add a token here ONLY if it is
# essentially never pure UI (a miss just defers to the panel; a false hit reverts a
# legit UI change).
# This pattern is kept BYTE-EQUAL with the inline copy in
# workflows/dispatch-fanout.workflow.js (the dispatcher/on-duty fan-out path); the
# GGC-63 sync check in scripts/prompt-lint.sh fails if the two ever diverge.
BEHAVIOR_RE='(initState|dispose|didChangeDependencies|didUpdateWidget|deactivate|setState|notifyListeners|addListener|removeListener)\(|GestureRecognizer|await |async[ ({]|\.then\(|ref\.(read|watch|listen)\(|Navigator\.|GoRouter|context\.(go|push|pop)|\.pushNamed\(|\.pushReplacement|StreamSubscription|StreamController'

STRUCTURAL_HIT=$(printf '%s\n' "$ADDED" | grep -cE "$BEHAVIOR_RE")
```

- `STRUCTURAL_HIT > 0` → **short-circuit to BLOCKED** without spawning the opus `dev-reviewer` at all
  (the obvious-logic-change fast path — saves the slow opus call). Record the matched signal as the
  block reason and go straight to Step 4 (agent repair) / the `--auto` loud-fail path. This NEVER
  produces an early CLEAR — it only ever BLOCKs earlier, so the both-must-be-CLEAR contract in Step 3
  is unchanged (a CLEAR still requires BOTH judges to actually run and return CLEAR).
- `STRUCTURAL_HIT == 0` → proceed to Step 2 and spawn BOTH judges normally.

> **A non-hit is NOT a CLEAR (GGC-57).** `STRUCTURAL_HIT == 0` only means the deterministic
> short-circuit did not fire — it does **not** pass the change. The decorrelated dual-judge panel
> (Step 2/3) still runs in full on the complete diff and BOTH judges must return CLEAR. So a
> pure-layout diff (CAF-540: `LayoutBuilder`/`ConstrainedBox`/`IntrinsicHeight`) or an import-only
> diff now reaches the panel instead of being reverted outright, while genuine behavior (CAF-555's
> `TapGestureRecognizer` + `initState`/`dispose`) still short-circuits to BLOCK exactly as before.
> Tightening the pre-pass shifts borderline diffs from a false deterministic BLOCK to the opus
> `dev-reviewer`'s judgement — strictly more scrutiny, never less.

## Step 2 — dual judge, decorrelated (R6), on the FINAL cumulative diff

Spawn judges with the **Agent tool**, inputs the **precomputed** `$DIFF_TEXT` + `$CHANGED_FILES`
from Step 1b (the pre-edit SHA `$BASE` is passed only as provenance metadata — the judges do NOT
re-run `git diff`). The judges audit the **final cumulative diff** (everything from the original
baseline through every correction and the format pass — this is what actually ships) as supplied
inline. The judges are **read-only by tool grant** (no Write) and **return** their verdict text —
this stage persists it.

**Always run BOTH judges in parallel** (one message, two Agent calls) — and feed both the SAME
precomputed `$DIFF_TEXT` inline, so the git/file IO is paid once for the whole panel rather than once
per judge. Since the skill has no edit-time hook, there is no upstream proof that the diff is
value-only — so neither judge may be skipped (except the Step 1c structural short-circuit, which only
ever BLOCKs, never CLEARs). They are decorrelated by model tier (`ui-verify-agent` = sonnet,
`dev-reviewer` = opus) so their misses are not positively correlated; the Step 1c deterministic
structural pre-pass is the fast cousin of `dev-reviewer`'s own structural reasoning (added imports /
new call heads / renamed identifiers / changed `@+id`) — when it fires, the opus call is skipped
entirely; when it does not, `dev-reviewer` still applies its full structural + behavioral lens.

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
dispatcher's `Workflow` script runs that lane as a SCRIPT-spawned level-1 leg precisely so this
stage's opus judge can spawn, see `ggx-dispatcher.md` §5.2). **The panel is UNCHANGED under `--auto`**: both judges always spawn,
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
