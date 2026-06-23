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
      "$REPO_ROOT/.dev/ui-tweak/direct-ship" \
      "$REPO_ROOT/.dev/ui-verify-pass.md" "$REPO_ROOT/.dev/dev-reviewer-pass.md"
# keep preview-requested (still heading to preview) and repair-count (caps at 3 → engineer card Ce).
# direct-ship is cleared: after a fix the designer re-decides at C1 (show-me) — see /ui-tweak:ff R20.
```
Then Stop. (The cap is enforced by the orchestrator: at `repair-count >= 3` it renders Ce instead of
re-entering apply.) The rest of this file is the normal first-pass path.

## Step 3 — parse source (read-only; cache the ticket) [O1]

- Ticket (ID/URL): **reuse `.dev/ui-tweak/ticket.json` if `/ui-tweak:start` already cached it**
  (Step 0 / R19 splits the worktree up-front and snapshots the ticket there — no re-fetch). Otherwise
  fetch via `mcp__claude_ai_Linear__get_issue` or Jira (`_ticket-lib.md`), **read-only** (never change
  status/assignee, never comment), and cache it to `.dev/ui-tweak/ticket.json` so the deliver path does
  not re-fetch.
- **Read the comment thread too, not just the description (GGC-84).** The `get_issue` /
  `getJiraIssue` snapshot in `ticket.json` carries the description + attachments but NOT the Linear
  comment thread, so a follow-up comment that refines or reverses the spec is invisible to a
  description-only read — and the earned-no-op grounded off it then validates against a **stale**
  spec (CAF-632: the merged en-route fix earned a no-op against the description's en-route reference
  image while a follow-up comment had already specified the opposite for the *completed* state). To
  prevent that:
  - Reuse `.dev/ui-tweak/comments.json` if `/ui-tweak:start` cached it (the normal path — no
    re-fetch). If it is absent or its `comments` array is empty with a `note` (a fetch failure at
    start time), re-fetch the thread yourself: `mcp__claude_ai_Linear__list_comments --issueId
    <id> --orderBy createdAt` (Linear) or read `.fields.comment.comments[]` from `ticket.json`
    (Jira), **read-only**, and refresh the cache. A re-fetch failure is fail-soft — proceed with
    the description alone and stamp the grounding provenance as `⚠ comments-unavailable` so the
    audit/no-op verdict downstream is not trusted as comment-aware.
  - **Derive the requirement from the UNION of title + description + the full comment thread.** When
    a later comment refines or contradicts the description, **the most-recent comment is
    authoritative** (it is the live intent; the description is the stale baseline). This mirrors the
    `/dev:start` Step 4 precedent that scans description AND comments. Note the spec can be
    **state-dependent** (CAF-632: the *completed* state needs the opposite section order from the
    *en-route* state) — keep every state's requirement, do not collapse them into one.
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
- **Ticket reference image is the spec (GGC-62)** — for `design bug` tickets the attached screenshot is
  almost always more precise than the one-line text: it encodes zoned/partial scope a sentence loses
  (CAF-609 said "page should be grey" but the image showed only the vehicle-list zone grey, the
  Date/Hourly rows staying white — a whole-page reading was wrong, and the designer rejected v1). So
  whenever the ticket carries an image — an attachment in `.dev/ui-tweak/ticket.json` (`.attachments[]`)
  or an inline `![](https://uploads.linear.app/...)` in the description — BEFORE finalizing scope:
  1. `curl -fsSL` the signed asset URL to `.dev/ui-tweak/ref-<n>.png`;
  2. **Read it as an image** (the Read tool renders it); when the designer pasted a before/after pair,
     compare the panels;
  3. build / refine the checklist from what the PICTURE shows — which regions change and which do NOT —
     not just the sentence.
  This COMPLEMENTS Figma rather than replacing it: if both exist, Figma pins exact values and the
  screenshot pins scope. Stamp `ref-image: <file>` on the receipt header for provenance.

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

**Earned-no-op must be validated against the LATEST spec (GGC-84).** When **every** `Ti` resolves to
`ALREADY-MATCHES` (no planned edit — an earned no-op), that verdict is only trustworthy if the target
checklist was grounded against the **current** spec, i.e. description **+ the full comment thread**
(Step 3), not the description alone. Before treating an all-`ALREADY-MATCHES` outcome as a real no-op:
- Confirm the requirement that produced the checklist included the comment thread (the
  `comments.json` cache read in Step 3, or a successful re-fetch). If the grounding provenance is
  `⚠ comments-unavailable` (comments could not be read), **do NOT earn a no-op** — the spec may have
  evolved past trunk in a comment the pipeline never saw. Refuse, leave the run for a human, and let
  the sibling no-op→`failed with reason` mechanism surface it.
- Watch for **state-dependent** specs (CAF-632): an `ALREADY-MATCHES` against one state's reference
  (e.g. en-route) says nothing about another state a later comment introduced (e.g. completed). A
  no-op is earned only when every state named across description + comments is satisfied — never when
  a comment lists a state the description's reference image did not cover.

**Present the plan** (coverage table — `Ti | property | target | code site | current | new | shared? |
status`): in default mode take one plan confirmation (the designer may adjust a target in place —
correction Level A, no re-arm). `--auto` skips the interactive confirm but still records the plan.

**Skip the plan-confirm when it would be redundant (P5, GGC-14).** In default (interactive) mode, if
**exactly ONE target** resolved (a single `Ti`) **AND its grounding source is the ticket** (the
description/Figma gave the values — provenance `figma-confirmed` or `from the work-item description`,
NOT `⚠ estimated`), skip the plan-confirm and go straight to the edit → card C1 (show-me). Rationale:
C1 (show-me) is the next stop anyway and its **Other** field is already the in-place correction escape,
so a separate plan-confirm for a single ticket-specified target is one prompt too many (it was the
back-to-back double-ask observed in the GGC-14 dogfood). Still **record** the plan (for the PR body /
audit). Keep the plan-confirm whenever there are ≥2 targets, OR any target is `⚠ estimated`, OR the
source is free-text (the designer benefits from confirming scope/inference before the edit).

**Record the base ref** (`base_ref` must survive to preview/audit as the cumulative-diff baseline).
If `base_ref` already exists (a correction or repair re-run), do **not** overwrite it (keep the true
pre-edit baseline so preview/audit always diff the cumulative change):

On **flutter**, restore environment-setup noise before recording `base_ref`: `/add-worktree` runs
`flutter pub get`, which dirties `pubspec.lock`. Since `base_ref` points at the pre-`pub get` HEAD,
that lockfile change would otherwise land in the `base_ref → working` diff and pollute the audit's
frozen set. Restore it here so the frozen set contains only the designer's UI edits (the restore
targets environment-setup noise only, never the designer's edits — which have not been made yet at
this point in the stage):

```bash
mkdir -p "$REPO_ROOT/.dev/ui-tweak"
if [ "$PLATFORM" = "flutter" ]; then
  git -C "$REPO_ROOT" checkout -- pubspec.lock 2>/dev/null || true
fi
# GGC-49 — a no-op must be EARNED against CLEAN TRUNK, never a contaminated base. Before recording
# base_ref (the cumulative-diff baseline the audit/no-op verdict trusts), assert HEAD is the freshly
# fetched default-branch tip. Under the parallel fan-out a worktree's HEAD can leak to a sibling
# ticket's commit (CAF-625: base_ref was 321be8fc, NOT trunk a6c525c7), making any "already
# matches / no source changes" verdict computed against it WORTHLESS. Refuse to record a poisoned
# base_ref. Only assert on the FIRST record (a correction/repair re-run keeps the original base_ref,
# which was already validated when first written).
if [ ! -f "$REPO_ROOT/.dev/ui-tweak/base_ref" ]; then
  source "$HOME/.claude/lib/dev-mode.sh"
  DEFAULT_BRANCH=$(default_branch)
  EXPECTED_TIP=$(git -C "$REPO_ROOT" rev-parse "origin/$DEFAULT_BRANCH" 2>/dev/null || true)
  ACTUAL_HEAD=$(git -C "$REPO_ROOT" rev-parse HEAD)
  if [ -n "$EXPECTED_TIP" ] && [ "$ACTUAL_HEAD" != "$EXPECTED_TIP" ]; then
    echo "FAIL: refusing to record base_ref — HEAD ($ACTUAL_HEAD) != fresh origin/$DEFAULT_BRANCH tip ($EXPECTED_TIP)." >&2
    echo "Cross-worktree contamination (GGC-49): a no-op/diff computed against a non-trunk base is invalid." >&2
    echo "Recreate the worktree off clean trunk (/add-worktree re-fetches + asserts) and re-run /ui-tweak." >&2
    exit 1
  fi
  git -C "$REPO_ROOT" rev-parse HEAD > "$REPO_ROOT/.dev/ui-tweak/base_ref"
fi
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
the plan; **default ALSO skips it for a single ticket-sourced target — P5, see Step 5**). `--auto`
NEVER causes build/audit/commit (those are preview / audit).

## Stop

Print: `UI diff produced (N files). base_ref recorded. Next: card C1 (the orchestrator asks
"show me on a phone / more changes"; building happens in /ui-tweak:preview only when the designer
picks "show me").` (A designer never types the internal stages.)
