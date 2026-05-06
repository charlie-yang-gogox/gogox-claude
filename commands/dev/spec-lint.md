---
name: spec-lint
description: |
  Pure-grep deterministic linter for OpenSpec change directories. Runs nine
  checks: capability-name alignment, FR-vs-scenario count, hardcoded drift,
  divergence markers, forbidden markers, bidirectional B-citation (forward
  catches synthesis omission, reverse catches synthesis hallucination),
  zero-ID drift warning, and post-edit cascade scan for stale terms. No LLM
  calls — every check is implementable with grep / awk / wc.
---

# Spec Lint — Deterministic Synthesis Guard

Lint an OpenSpec change directory for parity, traceability, and drift before it ships. Pure grep — no LLM calls, no model costs.

**Arguments:**
- *(none)* — run checks 1–8 on the active change (resolved via `openspec status --json`).
- `--change <name>` — explicitly target `openspec/changes/<name>/`.
- `--after-edit --stale "term1,term2"` — run check 9 only (cascade scan for stale terms after a rename / refactor).

---

## Steps

1. **Resolve target change directory.**
   - If `--change <name>` provided → use `openspec/changes/<name>/`.
   - Else run `openspec status --json` and read the single active change name. If zero or multiple → fail with `❌ ambiguous active change; pass --change <name>`.
   - Verify the directory exists and contains `proposal.md`, `design.md`, `tasks.md`, `specs/`, and `.port/`. Missing files are tolerated per check (each check fails closed on its own missing inputs).

2. **Determine output sink.**
   - When invoked from `/port:synth` (env `PORT_SYNTH=1` or caller passes `--report .port/synth-report.md`): atomic-write findings to `.port/synth-report.md` (write `.port/synth-report.md.tmp` then `mv`).
   - Standalone: print to stdout in the same format.

3. **Check 1 — Capability-name alignment.**
   - Extract proposed capability names from `.port/pm-notes.md`'s "Proposed Capabilities" section: `awk '/Proposed Capabilities/,/^## /' .port/pm-notes.md | grep -oE '\*\*[a-z][a-z0-9-]+\*\*' | tr -d '*' | sort -u`.
   - List spec dirs: `ls -1 specs/ 2>/dev/null | sort -u`.
   - Diff both directions. Names in pm-notes with no matching `specs/<name>/` → flag. Names in `specs/` with no matching pm-notes entry → flag.

4. **Check 2 — FR-vs-scenario count.**
   - `frs=$(grep -cE '^[0-9]+\.' .port/pm-notes.md)`.
   - `scenarios=$(grep -crE '^#### Scenario:' specs/ | awk -F: '{s+=$2} END {print s+0}')`.
   - If `scenarios < frs` → warn `⚠️ <frs> FRs but only <scenarios> scenarios — possible missing coverage`. Else pass.

5. **Check 3 — Hardcoded drift.**
   - Extract identifier patterns from `.port/dev-notes.md` (e.g. `tier\d+`, `level\d+`, named enums) via `grep -oE '\b[a-z]+[0-9]+\b' .port/dev-notes.md | sort -u`.
   - For each identifier, grep `proposal.md design.md specs/` for `always [0-9]+|exactly [0-9]+|all [0-9]+ <id-stem>s?` near the identifier. Mismatch (e.g. dev-notes lists `tier1..tier4` but spec says `all 3 tiers`) → flag with file:line.

6. **Check 4 — Divergence markers (port-only).**
   - Skip if `.port/` directory absent (non-port OpenSpec change).
   - `grep -niE 'Figma-driven|intentional improvement|net-new|not Android-parity' design.md` → collect markers.
   - If a `## Decision` (or `## Decisions`) section exists in `design.md` but zero markers found → flag `⚠️ design.md has decisions but no divergence markers — silent divergence risk`.

7. **Check 5 — Forbidden markers.**
   - `grep -rniE '## Open Questions|TBD|TODO|FIXME|待確認' .` (within the change dir, excluding `.port/synth-report.md` itself).
   - Any hit → flag with file:line. Must be resolved before ship.

8. **Check 6 — B-citation forward (synthesis OMISSION, per D17).**
   - For each `.port/<role>-notes.md` (pm, dev, design):
     - Extract IDs: `grep -noE '\*\*(FR|AC|R|A)-[0-9]+\*\*' <file> | sort -u` → list of `<file>:<line>:**ID**`.
     - For each ID (strip the `**` bold), grep across `proposal.md design.md tasks.md specs/`:
       `grep -rnE '\b(FR|AC|R|A)-[0-9]+\b' proposal.md design.md tasks.md specs/ | grep -wE '<ID>' | head -1`.
     - Zero matches → flag `❌ <ID> (<notes-file>:L<line>) — no artifact reference`.
   - All IDs referenced → `✅ all labeled IDs in notes are referenced`.

9. **Check 7 — B-citation reverse (synthesis HALLUCINATION, per D17 — higher severity).**
   - From artifacts: `grep -rnoE '\b(FR|AC|R|A)-[0-9]+\b' proposal.md design.md tasks.md specs/ | sort -u`.
   - For each artifact-cited ID, verify it is defined as a labeled `**ID**` in some `.port/*-notes.md`:
     `grep -lE '\*\*<ID>\*\*' .port/*-notes.md`.
   - Zero hits → flag `❌ HIGH: <artifact-file>:L<line> references <ID>, but <ID> not defined in any notes file`.
   - All cited IDs defined → `✅ all artifact ID references exist in notes`.

10. **Check 8 — Zero-ID warning (D15).**
    - For each `.port/<role>-notes.md`:
      - `count=$(grep -cE '\*\*(FR|AC|R|A)-[0-9]+\*\*' <file>)`.
      - `count == 0` → warn `⚠️ <file> has zero labeled IDs — checks 6 and 7 will be unreliable for this file (agent likely dropped the labeled-ID convention)`.

11. **Check 9 — Cascade scan (`--after-edit --stale "term1,term2"`).**
    - Only runs when invoked with these flags. Skips checks 1–8.
    - Split the comma-separated list into terms.
    - For each term: `grep -rniF "<term>" proposal.md design.md tasks.md specs/ .port/`.
    - Any hit → flag `❌ stale term '<term>' still referenced at <file>:L<line>`.
    - Zero hits across all terms → `✅ no stale references`.

12. **Emit report.** Format below. Atomic-write to `.port/synth-report.md` when invoked from `/port:synth`, else stdout.

```markdown
# /spec-lint — <change-name>

Run at: <ISO timestamp>

## 1. Capability-name alignment
✅ pass
   (or)
❌ pm-notes proposes `cargo-comp` but no `specs/cargo-comp/` exists
❌ specs/auth-revamp/ has no matching capability in pm-notes "Proposed Capabilities"

## 2. FR-vs-scenario count
✅ 8 FRs covered by 12 scenarios
   (or)
⚠️ 8 FRs but only 5 scenarios — possible missing coverage

## 3. Hardcoded drift
✅ pass
   (or)
❌ dev-notes.md lists tier1..tier4 but specs/cargo-comp/spec.md:L23 says "all 3 tiers"

## 4. Divergence markers
✅ pass (3 markers found)
   (or)
⚠️ design.md has decisions but no divergence markers — silent divergence risk

## 5. Forbidden markers
✅ pass
   (or)
❌ proposal.md:L18 — TBD
❌ design.md:L42 — ## Open Questions

## 6. B-citation forward (synthesis omission)
✅ all labeled IDs in notes are referenced
   (or)
❌ FR-3 (pm-notes.md:L42) — no artifact reference
❌ R-2 (dev-notes.md:L88) — no artifact reference

## 7. B-citation reverse (synthesis hallucination)
✅ all artifact ID references exist in notes
   (or)
❌ HIGH: tasks.md:L17 references FR-99, but FR-99 not defined in any notes file
❌ HIGH: design.md:L34 references AC-7, but AC-7 not defined

## 8. Zero-ID warning
✅ all notes files have labeled IDs
   (or)
⚠️ pm-notes.md has zero labeled IDs — checks 6/7 unreliable for this file

---

Summary: <total> findings (hallucination: <H>, omission: <O>, others: <X>)
```

13. **Exit code.**
    - Zero findings → exit 0.
    - Any finding with `❌` (errors) → exit 1.
    - Only `⚠️` warnings → exit 0 with the warnings printed (callers can decide).

---

## Guardrails

- **Read-only.** Never edit, stage, or rewrite artifacts. Even when called by `/port:synth`, the only file written is `.port/synth-report.md`.
- **Atomic write for the report.** Always write `.port/synth-report.md.tmp` then `mv` (POSIX atomic) — partial reports must never be visible to the next stage. Mirrors D16.
- **No LLM calls.** Every check must be implementable with `grep` / `awk` / `wc` / `sort` / `comm`. If a check needs semantic judgment, it does not belong here — file a follow-up for a reviewer-agent design.
- **Fail closed per check.** If `.port/pm-notes.md` is missing, checks 1/2/6/8 emit `⚠️ skipped: pm-notes.md missing` rather than crashing the whole lint. Other checks proceed.
- **Severity ordering.** Reverse B-citation (check 7) is the highest-severity finding — it indicates synthesis hallucinated an ID that nobody wrote. Always print check 7 errors first in the summary line: `Summary: <N> findings (hallucination: <H>, omission: <O>, others: <X>)`.
- **No commit.** This skill never runs `git add` or `git commit`. The caller (`/port:synth` → `/port:revise` → `/port:ship` → `/commit`) owns commit boundaries.
- **Cascade scan is exclusive.** When `--after-edit --stale ...` is passed, ONLY check 9 runs. Do not also run checks 1–8 (they will see stale-but-being-removed terms and double-flag).
- **Pure-grep means pure-grep.** No `jq` for parsing notes content (only allowed for `openspec status --json`). Notes structure is free-form markdown — anchor on `**ID**` regex and section headers, never a schema.
- **Standalone usage.** This command must be runnable on any OpenSpec change directory, not just `/port` ones. Checks 4 (divergence markers) and 6/7 (B-citation) silently skip when `.port/` is absent.
