---
name: port:revise
description: >
  Stage 5 of the port pipeline. Reads `.port/synth-report.md`, auto-fixes
  machine-resolvable findings (forbidden markers, cascade staleness),
  drives a HITL clarification loop for human-judgment findings, re-runs
  /spec-lint after every edit, and gates on a final approve / revise more
  / abort decision. In `--auto` mode, all findings are auto-accepted per
  the G7/G8 decision rules and a `Review approved` sentinel is written
  for /port:ship to consume.
---

# /port:revise — Pre-Review Clarification + Review Gate

Read the synth report, apply machine-resolvable fixes, walk the user through clarifications, and gate on the final review decision. After this stage either `/port:ship` is the next step (approve) or the worktree is preserved for manual cleanup (abort).

**Usage**: `/port:revise [--ticket:<ID>] [--auto]`

- `--ticket:<ID>` — Override auto-detection. Otherwise resolved per §4.4.
- `--auto` — Unattended mode. Skip every `AskUserQuestion`; auto-accept every assumption (G7) and auto-approve the final gate (G8). Append per-fix and per-acceptance audit lines under `claude-reports/<session>/`.

---

## Steps

1. **Resolve ticket-id (§4.4).**
   - If `--ticket:<ID>` provided → use it.
   - Else basename of `git rev-parse --show-toplevel` regex `[A-Z]+-[0-9]+`.
   - Else `git branch --show-current` regex `[A-Z]+-[0-9]+`.
   - Else HITL → `AskUserQuestion`. `--auto` → STOP with: `cannot resolve ticket-id; pass --ticket:<ID>`.

2. **Locate worktree + change dir.**
   - Confirm cwd is the ticket worktree. Find single `openspec/changes/<change-name>/.port/`. Hold `<change-name>` and `<port-dir>`.

3. **Pre-flight.**
   - `<port-dir>/synth-report.md` MUST exist. Missing → STOP with: `synth-report.md missing — run /port:synth first`.
   - Read into `<report>`.

4. **Partition findings.**
   - Walk the report's check sections. Tag each finding as one of:
     - **machine-resolvable**:
       - **Check 5 — Forbidden markers** (`## Open Questions`, `TBD`, `TODO`, `FIXME`, `待確認`). Convert each hit to a labeled `**AU-N**` `ri:v1` Assumption (D10) — see step 5.
       - **Check 9 — Cascade scan** stale terms from a prior `--after-edit` run. Targeted Edit removing the stale term, replacing with the new term carried in the report.
     - **needs user input** (everything else):
       - Check 1 capability-name mismatches.
       - Check 2 FR-vs-scenario coverage warnings.
       - Check 3 hardcoded drift.
       - Check 4 divergence-marker silence.
       - Check 6 B-citation forward (synthesis omission — human must decide whether to cite or drop).
       - Check 7 B-citation reverse (synthesis hallucination — HIGH severity; human must decide whether to add a notes-side definition or remove the artifact reference).
       - Check 8 zero-ID warnings (drift in agent prompt — human must regenerate notes).
   - Hold the two queues as `<machine-queue>` and `<user-queue>`.

5. **Apply machine fixes (D10).**
   - Iterate `<machine-queue>` deterministically.

   **Forbidden marker → Assumption conversion:**
   - For each hit: read the artifact line via `Read`. Identify the next available `AU-N` index by grepping for existing `**AU-\d+**` IDs in the artifact (and `.port/*-notes.md` if the marker lives there) — pick `max + 1`. The `AU` ("unresolved") namespace marks assumptions minted here from a forbidden marker, distinct from the authoring agents' `AD-`/`AP-`/`AG-` namespaces; `/spec-lint` matches it and `/spec-review` parses it.
   - Reframe the marker line as a positive Assumption sentence and emit it as a structured `ri:v1` record (the same shape the authoring agents write). Because a converted open-question is by definition unsettled, it is `kind=judgment` (or `kind=empirical verify=unconfirmed` if it is a code-fact) so the human adjudicates it at review:
     ```
     <!-- ri:v1 id=AU-7 kind=judgment sev=medium verify=n/a -->
     - **AU-7** — users cannot delete in v1; revisit when the delete capability ships.
       - Why: converted from a forbidden `## Open Questions` marker during /port:revise
       - Impact: <the artifact + section where the marker lived>
       - Evidence: (none)
       - Reality: (n/a)
     ```
     Never carry the original `TBD` / `TODO` / `## Open Questions` / `待確認` wording into any field — that re-trips Check 5.
   - Targeted `Edit` that replaces the original marker line(s) with the new `ri:v1` record. NEVER `Write` the whole file.
   - Print a one-liner: `auto-fixed: <file>:L<line> forbidden-marker → AU-N assumption`.

   **Cascade staleness:**
   - Targeted `Edit` replacing every stale `<old-term>` with `<new-term>` across the artifacts cited by the cascade scan finding. One Edit per file (use `replace_all` if every occurrence in a file is genuinely stale).
   - Print a one-liner: `auto-fixed: <file> cascade <old-term> → <new-term>`.

   **Audit trail in `--auto` mode:**
   - Append every one-liner (with ISO timestamp) to `claude-reports/<session>/auto-fixes.md`. Atomic write via `mktemp` + `mv` for the whole file (rewrite-on-every-line is fine — the file grows linearly and the audit writes are not on a hot path). The `<session>` segment is the active Claude Code session id (`$CLAUDE_SESSION_ID` or `git rev-parse --short HEAD`-`<unix-ts>` fallback).
   - Skip in HITL — the one-liners themselves are the audit.

6. **HITL clarification loop (skip entirely in `--auto`).**
   - In `--auto`: jump to step 7 (re-lint) after capturing the auto-accepted assumptions per §G7 (see step 7 user-queue handling).
   - In HITL: walk `<user-queue>` in batches.
     - Build groups of ≤4 findings. Issue ONE `AskUserQuestion` per group.
     - Each finding becomes one question with this shape:
       ```
       Q: <one-line summary of finding>
       Current: <what the artifact / notes currently say>
       Affects: <file:line list>
       ```
       Options per question:
       - `accept-as-is` — keep current text; if the finding is a hallucinated artifact ID, also note "artifact citation will remain unresolved — re-lint will keep flagging".
       - `revise (provide value)` — capture a follow-up free-form answer.
       - `defer-to-later` — leave the finding open; re-runs of `/port:revise` will see it again.
     - For each `revise` answer:
       1. Identify target file + section from the finding's location.
       2. Apply targeted `Edit` (NEVER `Write` — preserves surrounding context, avoids LLM variance).
       3. Capture the term-diff: `{old: "<old-snippet>", new: "<new-snippet>"}`.
       4. Invoke `/spec-lint --after-edit --stale "<old-snippet>"` (single-term cascade scan). Any hits → apply targeted Edits to update the stale references. Re-lint after the fix until no stale hits. This is non-skippable per §4.4 of the source SKILL.
     - For each `accept-as-is` on an Assumption-style finding: rewrite the assumption sentence in the relevant `.port/*-notes.md` to make the acceptance explicit (e.g. add `(accepted as-is at review)`). Optional but preferred — keeps the audit visible inside the spec.
     - For each `defer-to-later`: do nothing. The finding survives into the next `/spec-lint` pass.

7. **Capture auto-accepted items (`--auto` only).**

   Two distinct sources feed `claude-reports/<session>/auto-accepted.md`. Both are written as full structured records (no lossy one-line compression — the human reviewer must be able to understand each item without re-deriving its context):

   **(a) Assumption records from the notes files.** Gather every `<!-- ri:v1 ... -->` record across `.port/*-notes.md` (the `AD-`/`AP-`/`AG-`/`AU-` assumptions). Route each by its `verify` attribute:
   - `verify=confirmed` or `verify=refuted` → these were already settled against the code in `/port:explore`. Emit them under a `## Verified (FYI)` heading — they are an audit trail, NOT items the human must adjudicate.
   - `verify=unconfirmed` or `verify=n/a` (judgment) → emit them under a `## Needs review` heading — these are the genuine human-decision items.
   - **Copy each record verbatim** (the whole `ri:v1` marker + block, joined by its ID — never re-summarise, never re-match by prose). This is what kills the old fuzzy-provenance problem: the ID is the join key end-to-end.

   **(b) Residual spec-lint `<user-queue>` findings** (Checks 1–8 that are not assumption records — coverage gaps, citation issues, drift). For each, classify impact (`high` if ≥3 artifacts, else `medium`/`low`) and emit under `## Needs review` as a labeled item with enough context to act on:
   ```
   <!-- ri:v1 id=LINT-<check>-<n> kind=lint sev=<impact> verify=n/a -->
   - **LINT-<check>-<n>** — <what the lint finding means in one specific line>
     - Why: spec-lint Check <n> (<check-name>)
     - Impact: <the artifact:line the finding points at>
     - Evidence: (none)
     - Reality: (n/a)
   ```

   - Atomic write the whole file via `mktemp` + `mv`. `/port:ship` consumes it for the Linear summary's `### Needs review` and `### Verified (FYI)` sections.

8. **Re-run `/spec-lint` (full check, no `--after-edit`).**
   - Invoke `/spec-lint --change <change-name> --report .port/synth-report.md` (writes a fresh `synth-report.md`). Atomic write per the spec-lint contract.
   - Read the new report.
     - Zero `❌` findings → continue to step 9.
     - New findings emerged → loop back to step 4 (re-partition the new report). Cap at 3 iterations to avoid spinning; on the 4th pass STOP with `revise loop did not converge — manual cleanup needed at <port-dir>` and append `outcome:"aborted-revise-nonconverge"` to timings.

9. **Review gate.**
   - **HITL**: `AskUserQuestion`:
     ```
     Q: Review decision?
     Current synth-report: <N findings remaining (warnings only) or "clean">.
     ```
     Options:
     - `approve` — write the `Review approved` sentinel (step 10), exit success.
     - `revise more` — clear `<machine-queue>` (machine fixes already applied), loop back to step 4 with the latest report. The user can request additional edits via free-form follow-up.
     - `abort` — append timings line with `outcome:"aborted-by-user"` and STOP with: `Cancelled. Worktree and .port/ preserved at <worktree-path>.` Do NOT write the sentinel.
   - **`--auto`**: skip the question per G8. Auto-approve. Write the sentinel.

10. **Write `Review approved` sentinel (atomic).**
    - Append (atomic rewrite) a final block to `<port-dir>/synth-report.md`:
      ```markdown

      ---
      ## Review gate

      Review approved at <ISO timestamp> via <hitl|--auto>.
      Findings remaining (warnings only): <N or "none">.
      ```
    - Use the standard `mktemp` + `mv` pattern. The literal phrase `Review approved` is the sentinel `/port:ship` greps for.

11. **Append timings JSONL.**
    - Append one line to `<port-dir>/timings.jsonl`:
      ```json
      {"stage":"revise","ticket":"<ticket-id>","start":"<ISO-start>","end":"<ISO-end>","duration_ms":<int>,"outcome":"ok"}
      ```
    - On HITL `revise more`-loop completion, the line is written ONCE per stage invocation, not per loop iteration.

12. **Print exit.**
    - HITL approve:
      ```
      Review approved. Findings remaining (warnings only): <N or "none">.

      Run /port:ship next.
      ```
    - `--auto`:
      ```
      Auto-approved per G7/G8. Auto-fixes: <count>. Auto-accepted assumptions: <count>.
      claude-reports/<session>/auto-fixes.md
      claude-reports/<session>/auto-accepted.md

      Auto-approved — proceeding to /port:ship.
      ```

---

## Atomic write pattern (D16)

All revisions to `openspec/changes/<change-name>/` files use the targeted `Edit` tool — atomic by design (whole-file replace with collision detection).

When this stage rewrites `.port/synth-report.md` (step 10), `claude-reports/<session>/auto-fixes.md` (step 5), or `claude-reports/<session>/auto-accepted.md` (step 7), use the standard pattern:

```bash
tmp=$(mktemp)
# write full content to $tmp
mv "$tmp" "<target-file>"
```

POSIX `mv` on the same filesystem is atomic — partial reports must never be visible to `/port:ship`.

---

## Guardrails

- **Targeted Edit, never full-file Write** for revisions inside `openspec/changes/<change-name>/`. Preserves surrounding context and removes LLM variance from the loop.
- **Cascade scan after every Edit** is non-negotiable. The orchestrator MUST invoke `/spec-lint --after-edit --stale "<old-term>"` after every revision Edit and apply targeted Edits for any stale hits before continuing. The grep decides — never judge an artifact "agnostic" and skip it.
- **Forbidden markers are converted to Assumptions, never silently dropped.** The marker text becomes a positive `**AU-N**` `ri:v1` record (the `AU` "unresolved" namespace). This preserves the question for downstream review while removing the lint failure.
- **Auto-fixes are reported (D10).** Every machine fix prints a one-liner; in `--auto` they are accumulated to `claude-reports/<session>/auto-fixes.md` for inclusion in the Linear summary by `/port:ship`.
- **In `--auto`, NEVER call `AskUserQuestion`.** Both step 6 (clarification loop) and step 9 (review gate) are skipped. Auto-accept all assumptions (G7); auto-approve the final gate (G8); record both audit trails for ship-side reporting.
- **`Review approved` sentinel** is the only handshake `/port:ship` uses to detect approval. Never write it on `abort` or on a non-converged loop.
- **Bounded loop** — cap iterations of the partition→fix→re-lint cycle at 3 (4th attempt aborts). Stops the orchestrator from thrashing on a finding that needs human judgment but the LLM keeps re-applying the same Edit.
- **Per-machine paths** stay in `.claude/port-settings.json`; this stage never reads or writes config files.
- All Linear MCP calls (none in this stage by design — Linear writes are owned by `/port:ship`) use `mcp__claude_ai_Linear__*` if ever needed.
- Timings JSONL is appended even on early STOP paths so observability isn't lost (set `outcome` to `aborted:<reason>`).
- This stage NEVER runs `git add` / `git commit` / `git push`. Those are owned by `/port:ship`.
- This stage never spawns sub-agents — the entire loop must be main-thread because of the HITL gates.
