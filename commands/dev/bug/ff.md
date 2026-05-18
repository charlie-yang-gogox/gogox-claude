---
name: ff
description: >
  Bug-fix fast-forward orchestrator. Thin alias that invokes `/dev:ff` with
  the `--bug` flag added, so the bug-mode walker branch runs (skips OpenSpec
  stages: figma / detect / align / apply). The actual stage chain — `/dev:start`
  → human-write-fix → `/dev:verify` → `/dev:review` → `/dev:ship` — lives in
  the `/dev/*` pipeline files; this file's only job is flag injection so users
  do not have to remember `--bug`. Pairs with `/route` and `/ggx-work` which
  recommend `/bug:ff` for bug-classified tickets.
Prerequisite: >
  Same as `/dev:ff` plus a `bug` classification label on the Linear ticket
  (the classification is read by `/route`, not by this wrapper).
---

# `/bug:ff <ticket-id> [--auto]`

`/bug:ff` is a 1:1 wrapper around `/dev:ff` that always adds `--bug` to the
argument list. The wrapper exists so the user-facing surface for bug fixes
matches the classification vocabulary (`bug` label → `/bug:ff`), without
duplicating the pipeline logic.

## Usage

- `/bug:ff <ticket-id>` — start a fresh bug-fix pipeline in default mode.
- `/bug:ff <ticket-id> --auto` — start a fresh bug-fix pipeline in unattended
  mode (dispatcher / `/ggx-work --auto` path). The agent runs end-to-end:
  `/dev:start --bug` → `/dev:apply` (bug branch, autonomous fix) → `/dev:verify`
  → `/dev:review` → `/dev:ship`. No HITL gates fire. Stops only on failure
  (e.g., agent emits `Status: FAILED` in `.dev/apply-result.md`).
- `/bug:ff` (no ticket-id, inside a bug-mode worktree) — resume. `/dev:ff`
  reads `.dev/mode.md` and resumes via the bug-mode walker.

## Behavior

Forward every argument the user passed to `/dev:ff`, prefixed with `--bug`:

```
/bug:ff <args...>  ≡  /dev:ff --bug <args...>
```

That is the entire body. No precondition checks, no walker logic, no Linear
calls — all of that lives in `/dev:ff` and its sub-stages, which gate on
`.dev/mode.md == bug` to take the bug-mode branch.

## What changes vs `/dev:ff` (in bug mode)

The bug-mode walker in `/dev:ff` (see `commands/dev/dev/ff.md` Step 1
`infer_bug_stage`) emits only these stages:

| Stage    | What it does                                                                              |
|----------|-------------------------------------------------------------------------------------------|
| `start`  | Runs `/dev:start --bug <ticket-id>` → worktree + Linear assign + `.dev/mode.md`           |
| `apply`  | Runs `/dev:apply` which takes its **Step 0-bug** branch: agent investigates the codebase, writes `.dev/bug-analysis.md`, applies the fix (Edit/Write), commits via `/commit`, writes `.dev/apply-result.md` with `Status: CLEAR`. Default mode includes ONE HITL gate confirming the agent's plan; `--auto` skips that gate. |
| `verify` | Runs `/dev:verify` (which reads mode, skips OpenSpec checks, requires `commits > 0`)     |
| `review` | Runs `/dev:review` unchanged                                                              |
| `ship`   | Runs `/dev:ship` (which reads mode, skips OpenSpec archive)                               |
| `done`   | PR open + Linear `In Review` — terminal                                                   |

The OpenSpec stages (`figma`, `detect`, `align`) are NEVER emitted in bug mode.
`/dev:apply` IS emitted but takes its bug-specific Step 0-bug branch — no
`/opsx:apply`, no spec-driven workflow.

**Bug fix is agent-autonomous**, not human-driven. The agent reads the ticket,
finds the root cause, writes the patch, and commits. The user supervises in
default mode (one plan-confirmation HITL) but never has to find the bug or
write code themselves. In `--auto` mode there is no HITL at all — used by
`/ggx-work --auto` and (eventually) the dispatcher.

## Resume semantics

If `/bug:ff` is interrupted or the agent's `Step 0-bug` apply fails, re-invoking
`/bug:ff` (no args needed from inside the worktree) resumes:

- `.dev/mode.md == bug` → bug branch
- `infer_bug_stage` checks markers in order: PR / code-review / verify-pass /
  apply-result / mode marker
- Walker advances to the first unmet marker, dispatches that stage

`.dev/apply-result.md` is the apply-stage done marker. If it says `Status: CLEAR`
the walker moves to `verify`. If it says `Status: FAILED` the walker stops with
an explicit error — the user inspects `.dev/bug-analysis.md` + `apply-result.md`,
fixes manually, and runs `/dev:ff --from apply` to retry.

## Guardrails

- **No logic duplication.** This file is a flag-injection alias. Any
  behavior change for bug fixes happens in `/dev/*` stages, not here.
- **No state file.** `.dev/mode.md` is the only mode marker; this wrapper
  does not read or write it.
- **No Linear calls.** All Linear interactions happen inside `/dev:start`,
  `/dev:ship`, etc.

## Relationship to `/route` and `/ggx-work`

`/route` recommends `/bug:ff <ticket-id>` when classification == `bug`.
`/ggx-work` spawns the recommendation. Both treat `/bug:ff` as a normal
pipeline FF command (matches `^/bug:ff ` in `/ggx-work` Step 3.3), so no
changes to those callers are required.
