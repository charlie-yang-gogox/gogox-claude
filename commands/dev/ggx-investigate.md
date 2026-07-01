---
name: ggx-investigate
argument-hint: "<ticket-id> [--verify-fixed]"
description: >
  Investigate a Linear/Jira ticket and write a lane-aware **engineer note**
  back to it — the automated form of the operator's recurring "調查這張單，
  寫成 engineer note" request. Self-contained (GGC-114): reads the ticket,
  derives its lane, investigates the current project **read-only**
  (Grep/Glob/Read + reasoning — NO gstack, NO external skill, D4), auto-detects
  whether origin-project grounding is warranted and if so scans the origin repo
  read-only (fail-closed — D2), composes a lane-aware note in **English** (D6),
  gates on **HITL approval**, then writes the note **idempotently** into the
  ticket description's `ENGINEERING Notes` region via replace-between-markers
  (D1). In-scope lanes: `bug`, `design bug`, `feature`. `port` is out of scope
  (origin is the SSOT there) — STOP and point at `/port:explore`. `--verify-fixed`
  investigates whether a previously-described root cause is already resolved in
  current code and records `Status: fixed | not-fixed | partially`. READ-ONLY on
  code and git; the ONLY writes are the tracker note (D5). It invokes NO pipeline
  (`/route`, `/ggx-work`, `/bug:ff`, `/dev:ff`) — it produces the grounding those
  consume and stops. Linear is the primary tracker (description-region
  convention); Jira runs degraded (stable-marker comment only).
Prerequisite: >
  - Linear MCP authenticated for CAF/DAF tickets; Atlassian Rovo MCP
    authenticated for Jira tickets.
  - Run from the repo whose codebase should be investigated (its
    `.gogox-claude.yaml` / registry profile resolves the platform and
    `ticket_system`).
  - `<ticket-id>` references a real ticket with a derivable non-`port` lane
    (Linear: `design bug` label → design-bug lane by precedence, else exactly
    one of {`bug`,`feature`}; `port` STOPs). Origin grounding additionally
    needs `.claude/port-settings.json` `originalProjectPath` on disk — absent
    is a silent no-op, not an error.
---

<!-- RULE: command content is English. -->

# `/ggx-investigate <ticket-id> [--verify-fixed]`

Investigate one ticket and write an **engineer note** — Root Cause + a fix
*plan* (bug / design bug), or Current Behaviour + Suggested Changes
(feature / align) — back into the ticket. This replaces the manual
"調查某某單，並把它寫成 engineer note" loop the operator runs by hand across
many tickets. It is **investigation + note authoring only**: it never edits
source, never commits, never opens a PR, never changes status/assignee, and
never starts a pipeline (D5, §8). The note it writes becomes the grounding a
later `/bug:ff` / `/dev:ff` consumes.

**Usage**:

- `/ggx-investigate <ticket-id>` — investigate the ticket and write a
  lane-aware note (HITL-gated before any write).
- `/ggx-investigate <ticket-id> --verify-fixed` — verify variant: determine
  from **current** code whether a previously-known / described root cause is
  already resolved, and record `Status: fixed | not-fixed | partially` with
  code evidence at the top of the note.

**Locked decisions** (from the GGC-114 PRD — do not silently deviate):

| # | Decision |
|---|---|
| D1 | Write target = the description's `ENGINEERING Notes` region, replace-between-markers. Fallback to a stable-marker **comment** only when the description cannot be edited. |
| D2 | Origin grounding = auto-detect, **fail-closed**. Scan origin only for align/parity tickets or those that name the origin/native app; origin not on disk → silently skip. |
| D4 | **NO gstack dependency.** Investigation is internal (Grep/Glob/Read + reasoning). The skill must run on a machine with no gstack installed — it MUST contain zero references to gstack `/investigate` or any gstack skill. |
| D5 | **Read-only on code; tracker-write only.** Never edit source, commit, PR, or change status/assignee. Output is Root Cause + a fix *plan*, not an applied fix. |
| D6 | The written note is **English** (tracker convention). Interactive chat may be Traditional Chinese. |

---

## Steps

### Step 0: Parse arguments

1. Extract `<ticket-id>` (trim, uppercase). Missing → STOP with the usage block.
2. Detect `--verify-fixed` → `<verify-fixed> = True/False`. Unknown flags → STOP
   with the usage block.

### Step 1: Resolve profile + fetch ticket + derive lane

1. **Resolve `ticket_system` + platform** per the `_ticket-lib.md` resolution
   block (read `<repo-root>/.gogox-claude.yaml`, else the registry entry keyed by
   repo basename; resolve `ticket_system` via the profile, falling back to the
   `org.yaml` prefix lookup for `auto`). `unknown` → STOP with the `_ticket-lib`
   unknown-system error (never default to Linear silently).
2. **Fetch the ticket** via the `_ticket-lib.md` `get_ticket` branch:
   - Linear: `mcp__claude_ai_Linear__get_issue --id <ticket-id>` (this also
     returns the current `description` — captured for the Step 6 write).
   - Jira: `mcp__claude_ai_Atlassian_Rovo__getJiraIssue` (degraded mode — see
     Step 6; Jira has no description-region convention here).
   Capture `<title>`, `<description>`, `<labels>` (Linear) / `<issue_type>`
   (Jira), `<url>`.
3. **Derive `<lane>`** per the `_ticket-lib.md` lane table:
   - Linear: **`design bug` precedence first** (whole-string, case-insensitive)
     → lane **`ui-tweak`** (the canonical lane string for the `design bug` label,
     matching `_ticket-lib.md` / `/route` / `/ggx-work` — do NOT invent a
     `design-bug` lane string), regardless of co-occurring labels. Else exactly
     one of {`bug`, `feature`} → that lane; `port` present → **STOP** (see below);
     zero or multiple → lane `unknown`.
   - Jira: `Bug` → `bug`; Story/Task/Sub-task/Improvement/New Feature →
     `feature`.
   - **`port` → STOP** (do NOT investigate, do NOT write) with:
     `port tickets use origin as the SSOT — run /port:explore instead.` (§8, D5).
   - **`unknown`** → interactive: ask the operator to set a classification label,
     OR proceed on an explicit best-guess lane with a stated caveat recorded in
     the note. Never guess silently.
4. Choose the note template by lane: `bug` or `ui-tweak` (a `design bug`) → the
   **bug template**; `feature` → the **feature/align template**
   (§ Engineer-note templates). (The `design bug` label maps to the canonical
   `ui-tweak` lane but uses the bug-style Findings/Root Cause template per the
   PRD §5 — the lane string and the template are chosen independently.)

### Step 2: Investigate (read-only, self-contained — D4)

Investigate the **current** repo with Grep / Glob / Read + reasoning ONLY. Do NOT
call gstack `/investigate` or any external skill; do NOT run a build; do NOT edit
any file or touch git (D4, D5, AC5, AC6).

- **bug / design bug (lane `ui-tweak`)**: from the ticket's described symptom, locate the relevant
  code path (screens, widgets, view-models, services, string keys), trace it, and
  determine the **root cause** — the specific code and *why* it misbehaves. Record
  concrete `file:line` citations. For a design bug, focus on the visual/layout
  code (styles, constraints, theming) but still identify the causing code.
- **feature / align**: locate how the behaviour works **today** in this project
  (or that it is absent), and identify the **change surface** — the files /
  widgets / view-models a future implementation would touch. Record `file:line`.
- **`--verify-fixed`**: treat the ticket's prior description / comments as the
  previously-known root cause; from **current** code decide whether it is already
  resolved. Classify `fixed` (the causing code is gone/corrected — cite it),
  `not-fixed` (still present — cite it), or `partially` (some but not all), each
  with evidence. If no prior root cause is described, investigate current
  behaviour and state plainly that no prior RC was found (§9 edge case).

Investigation is a genuine reasoning task — cite real code you actually read;
never fabricate a `file:line`. If you cannot locate the code with confidence, say
so in the note rather than inventing a cause (fail honest).

### Step 3: Origin grounding (auto-detect, fail-closed — D2)

Only for align/parity tickets or tickets whose text names the origin / native /
"same as v1" app — otherwise skip this step (most bugs need no origin).

1. **Auto-detect** the align/parity signal from the ticket text (phrases like
   "align with", "same as the native app", "parity", "origin", "v1", "as before
   the revamp"). No signal → skip; the note omits the origin section.
2. **Resolve the origin** the same way `/spec-review` (Step 3.5) and `/port` do —
   read `originalProjectPath`, expand `~`/`$ENV`, and require it to be a directory
   on disk:
   ```bash
   ORIGIN=$(jq -r '.originalProjectPath // ""' "$(git rev-parse --show-toplevel)/.claude/port-settings.json" 2>/dev/null)
   ORIGIN=$(eval printf '%s' "$ORIGIN")   # expand ~ / $ENV
   [ -n "$ORIGIN" ] && [ -d "$ORIGIN" ] || echo "origin not scanned (no-origin-path)"
   ```
   **Fail-closed:** file/key missing, empty, or the path is not a directory (the
   cloud-run case — no origin working tree) → **do NOT prompt, do NOT guess.**
   Skip grounding; the note records `Reference (origin project): (origin not
   scanned)`. This makes the whole step a no-op in the cloud routine.
3. **Scan (read-only).** Grep / Read the origin repo for how it implements the
   behaviour; summarize the reference implementation with `file:line` from the
   origin. Never write the origin filesystem or git.

### Step 4: Compose the lane-aware engineer note (English — D6)

Fill the lane's template (§ Engineer-note templates) from the Step 2 (+ Step 3)
findings. Every claim that references code carries a `file:line`. Keep it a fix
**plan**, not an applied fix (D5). English only.

### Step 5: HITL gate (confirm before any write)

Print the full composed note, then `AskUserQuestion` with:

- `Approve & write` (Recommended) — proceed to Step 6.
- `Revise` — take operator feedback, adjust the note, re-print, re-ask.
- `Abort` — STOP with no side effects (no write of any kind).

This honours the operator's "確認沒問題再 post". (`--non-interactive` is reserved
for a future version; v1 always confirms before posting.)

### Step 6: Idempotent write (replace-between-markers — D1)

All writes are **read-before-write**. Wrap the note body in a stable marker pair
that carries the ticket id AND an 8-char hash of the note body:

```
<!-- ggx-eng-note:v1 ticket=<ticket-id> sha=<first 8 chars of sha256(note-body)> -->
<the composed note>
<!-- /ggx-eng-note:v1 -->
```

The `sha` is the idempotency + change-detector: on any re-run, if an existing
marker block's `sha` equals the freshly-composed note's hash, **skip the write
entirely** and print `note unchanged — no write` (a clean no-op — this is the
fast-path that also makes re-runs cheap for the cloud/non-HITL case). Otherwise
proceed with the replace below.

**Markers are the SOLE authority once present.** If BOTH a `ggx-eng-note:v1`
marker block AND a bare `ENGINEERING Notes` heading exist (e.g. a human hand-wrote
notes after a prior run), treat the **marker block as canonical**: replace between
the markers and leave the bare heading untouched. Never do heading-replacement
while markers exist.

**Linear (primary) — write into the description:**

1. **Re-fetch** the ticket description immediately before writing; if it differs
   from the copy read at Step 1, a concurrent editor touched it → **STOP** with a
   clear message (single-ticket; never clobber). Otherwise operate on the fresh
   copy.
2. **Subsequent writes** (a `ggx-eng-note:v1` marker pair is present): replace
   **strictly the text between** `<!-- ggx-eng-note:v1 … -->` and
   `<!-- /ggx-eng-note:v1 -->` (inclusive of the marker lines, which are rewritten
   with the new `sha`). Never append a second block (AC2). This branch takes
   precedence over first-write, per "markers are the sole authority" above.
3. **First write** (NO markers present):
   - If the description contains an `ENGINEERING Notes` heading (e.g.
     `***ENGINEERING Notes & Associated TEST CASES*** :`), replace that block with
     the marker-wrapped note. **Boundary:** the heading line through the LAST line
     before the next line matching `^(---|#{1,6}\s|\*\*\*)` **that is outside the
     marker-wrapped body being inserted**, or end-of-description if none. Because
     the note body may itself contain a `---` divider or a `##` sub-heading, the
     boundary scan runs over the ORIGINAL description only (never over the new note
     text), so a note-internal `---` can never truncate the replacement.
   - Else append a new marker-wrapped `## Engineering Notes` section at the END of
     the description (preserve everything already there).
4. Persist with `mcp__claude_ai_Linear__save_issue --id <ticket-id>
   --description <new-full-markdown>` (Linear takes the full description; the
   surgery above is done in-memory first). Do NOT set assignee/status/labels.

**Fallback (Linear, description not editable — permissions) AND Jira (degraded,
no description-region convention):** write the note as a single stable-marker
**comment** instead, idempotently:

1. `list_comments` (Linear `mcp__claude_ai_Linear__list_comments --issueId
   <ticket-id>`; Jira: read the issue's comments) and scan for a comment whose
   body contains `<!-- ggx-eng-note:v1 -->`.
2. **Found + editable** → update THAT comment in place, passing its **comment id**:
   Linear `mcp__claude_ai_Linear__save_comment --id <comment-id> --body <new>`
   (Linear's `save_comment` updates when `id` is given — verified capability, not
   a new post). Apply the same `sha` skip-if-unchanged fast-path.
3. **Found but the tracker cannot edit a comment in place** (some Jira MCP setups
   expose only add-comment) → **STOP** with `a prior ggx-eng-note comment exists
   but this tracker cannot edit it in place — update it manually or run on Linear`.
   Do NOT post a duplicate (AC2 must hold on every path).
4. **Not found** → post a new marked comment (Linear `save_comment --issueId
   <ticket-id> --body <marked-note>`; Jira add-comment).

Print a final one-line confirmation with the ticket URL and whether the note
landed in the description region or a comment.

---

## Engineer-note templates (lane-aware)

**bug / design bug:**

```
**Findings:** <what the code path shows / how the symptom arises>
**Root Cause:** <the specific code + why it misbehaves; cite file:line>
**Solution Notes:** <fix plan — the concrete change(s), NOT applied>
**Supporting Tests required to close this issue:** <tests that would prove the fix>
```

`--verify-fixed` prepends:
`**Status:** fixed | not-fixed | partially — <evidence, cite file:line>`

When `--verify-fixed` runs but the ticket describes **no prior root cause** to
verify against (§9 edge case), set `**Status:** unknown (no prior root cause
described) — investigated current behaviour instead`, and fill `Root Cause:` with
the freshly-investigated cause if one is found, or `not located — see Findings`
if not. Never leave `Root Cause:` blank or fabricated.

**feature / align:**

```
**Current Behaviour (this project):** <how it works today; cite file:line>
**Reference (origin project):** <how the origin/native app does it — or "(origin not scanned)">
**Suggested Changes:** <the change approach for future implementation>
**Affected Areas:** <files / widgets / view-models likely touched>
```

---

## Guardrails

- **Read-only on code + git (D5, AC6).** Grep/Glob/Read only. No Edit/Write to
  source, no `git` mutations, no build, no PR, no status/assignee change. The
  ONLY mutation this skill makes is the tracker note.
- **No gstack, no external skill (D4, AC5).** Investigation reasoning is internal.
  This file contains zero references to gstack `/investigate`. It runs on a
  machine with no gstack installed.
- **No pipeline invocation (§8).** Never call `/route`, `/ggx-work`, `/bug:ff`,
  `/dev:ff`, `/port:*`. Write the note and stop.
- **`port` lane is out of scope.** STOP → `/port:explore` (origin is the SSOT).
- **Never fabricate evidence.** Every `file:line` must be code actually read; if
  the root cause can't be located confidently, say so in the note.
- **Idempotent, never clobber.** Replace-between-markers; concurrent-edit → STOP.

## Relationship to existing tooling (no duplication)

- **NOT** gstack `/investigate` (D4) — self-contained; reuse is forbidden.
- Reuses the **method** (not a call) of `/spec-review` Step 3.5 for origin
  grounding.
- Complements `/ticket-analyze` (which judges completeness) — this fills note
  *content*; they do not overlap.
- Feeds `/bug:ff` / `/dev:ff`: the note's Root Cause + Solution Notes become the
  implementer's grounding. `bug-reproducer-android` remains an opt-in when live
  reproduction is wanted — not required by this skill.
