---
name: _state-schema
description: "Reference doc for `.dev/state.json` — the single source of truth shared by all `/dev:*` atomic commands. Not meant to be invoked as a workflow; read this when designing or debugging stage contracts. The `_` prefix marks it internal."
---

# `.dev/state.json` schema (v1)

Every `/dev:*` atomic command reads this file to validate its preconditions and writes a transition on success. No cross-stage information lives anywhere else — if a fact must survive across stages, it goes in `state.json`.

## Top-level fields

| Field | Type | Required | First set by | Notes |
|---|---|---|---|---|
| `schema_version` | int | yes | `/dev:start` | Currently `1`. Bump on breaking changes. |
| `ticket_id` | string | yes | `/dev:start` | e.g. `CAF-207` |
| `ticket_title` | string | yes | `/dev:start` | From Linear |
| `change_name` | string | yes | `/dev:start` | kebab-case derived from ticket title |
| `mode` | enum: `auto` \| `default` | yes | `/dev:start` | Set at start. `default → auto` upgrade IS permitted on resume via `/dev:ff --auto` (the orchestrator mutates `mode = auto`). `auto → default` downgrade is NOT permitted — auto-only state like `verify` would be orphaned. |
| `platform` | enum: `flutter` \| `android` \| `ios` | yes | `/dev:start` | From profile resolution |
| `base_ref` | string | yes | `/dev:start` | e.g. `origin/trunk` |
| `worktree_path` | string | auto only | `/dev:start` | Absolute path; required when `mode == auto` |
| `current_stage` | enum (see below) | yes | every stage | Pointer to next-to-run |
| `stage_history` | array | yes | every stage | Append-only, never edited in place |
| `figma` | object | conditional | `/dev:figma` | Present iff at least one Figma URL was found |
| `openspec` | object | yes after detect | `/dev:detect` | `{state, change_dir}` |
| `verify` | object | yes after verify | `/dev:verify` | `{status, report, retry_count}` |

## Stage names and transitions

```
default mode: start → figma → detect → align? → apply → done_default
auto mode:    start → figma → detect → align? → apply → verify → review → ship → done
                                          ↑                       ↓
                                       (B/C only)          (BLOCKED → loop once)
```

| Stage | Predecessor | Successor on success | Skip if |
|---|---|---|---|
| `start` | — | `figma` (or `detect` if `--no-figma`) | — |
| `figma` | `start` | `detect` | `--no-figma` was passed at `/dev:start` (skip set by start, never enters this stage) |
| `detect` | `figma` | `align` if state ∈ {B, C}; `apply` if state == A | — |
| `align` | `detect` | `apply` | `openspec.state == A` OR `figma.receipt` is null |
| `apply` | `align` (or `detect` if skipped) | `verify` if `mode == auto`; `done_default` if `mode == default` | — |
| `verify` | `apply` | `review` | `mode == default` (terminal: pipeline ends at `done_default`) |
| `review` | `verify` | `ship` | `mode == default` |
| `ship` | `review` | `done` | `mode == default` |

**Terminal states**:
- `done` — auto mode complete (PR opened, Linear updated).
- `done_default` — default mode complete (artifacts applied; user owns format/commit/PR manually).

A `default → auto` upgrade via `/dev:ff --auto` from `done_default` resets `current_stage` to `verify` and mutates `mode = auto`.

When a stage is skipped, append `{stage, status: "skipped", ts, reason}` to `stage_history` and advance `current_stage` to the successor.

## stage_history entry shape

```json
{ "stage": "verify", "status": "done", "ts": "2026-05-06T12:00:00Z", "result": "CLEAR" }
```

- `stage` — name from the table above
- `status` — `done` | `failed` | `skipped`
- `ts` — ISO-8601 UTC
- `result` — optional, stage-specific (e.g. `detect` → `A`/`B`/`C`, `verify` → `CLEAR`/`BLOCKED`)
- `reason` — optional, used on `skipped` and `failed`

## Per-stage required state fields

`/dev:_state-check <stage>` validates each stage has its prerequisite state populated before the stage's body runs:

| Stage | Required fields in state |
|---|---|
| `start` | (creator — no prior state) |
| `figma` | `ticket_id`, `mode` |
| `detect` | `change_name`, `platform` |
| `align` | `figma.receipt`, `openspec.state ∈ {B,C}`, `openspec.change_dir` |
| `apply` | `change_name`, `openspec.state` |
| `verify` | `base_ref`, `change_name` (`figma.receipt` optional) |
| `review` | `verify.status == "CLEAR"` |
| `ship` | `verify.status == "CLEAR"`; `worktree_path` if `mode == auto` |

## Atomic write pattern

Every state mutation MUST use temp-file-then-rename, to avoid leaving half-written JSON if the process is killed mid-write:

```bash
jq '<mutation>' .dev/state.json > .dev/state.json.tmp \
  && mv .dev/state.json.tmp .dev/state.json
```

A stage that fails mid-mutation must NOT leave `state.json` in a partial state. If `jq` returns non-zero, abort without renaming.

## Override semantics for re-runs

The strict `current_stage == EXPECTED` rule can be bypassed only via explicit flags on the calling stage:

- `--force` — re-run current stage. The `current_stage` check is skipped, but per-stage required-fields check still runs. The matching `stage_history` entry is overwritten.
- `--from <stage>` — reset `current_stage = <stage>` and **drop the `<stage>` entry plus everything after it** from `stage_history`. Then proceed. The next run re-appends a fresh entry on completion (no duplicate "done" entries). Used to redo from an earlier point (e.g. re-fetch Figma after design update).

  Implementation requires last-index slicing — there may be earlier `<stage>` entries from prior `--from` cycles, and we want to truncate at the most recent one. See `/dev:ff` Step 0a for the canonical jq filter.

Both flags are surface-level; the validator `_state-check` does not interpret them — the calling stage decides whether to skip the strict check.

## Initial state (after `/dev:start`)

```json
{
  "schema_version": 1,
  "ticket_id": "CAF-207",
  "ticket_title": "Add favourite driver from rating screen",
  "change_name": "add-favourite-driver-from-rating-screen",
  "mode": "auto",
  "platform": "flutter",
  "base_ref": "origin/trunk",
  "worktree_path": "/Users/charlie/Projects/CAF-207",
  "current_stage": "figma",
  "stage_history": [
    { "stage": "start", "status": "done", "ts": "2026-05-06T12:00:00Z" }
  ]
}
```

## Mid-flow state (after `/dev:detect` on a State-B change)

```json
{
  "schema_version": 1,
  "ticket_id": "CAF-207",
  "ticket_title": "Add favourite driver from rating screen",
  "change_name": "add-favourite-driver-from-rating-screen",
  "mode": "auto",
  "platform": "flutter",
  "base_ref": "origin/trunk",
  "worktree_path": "/Users/charlie/Projects/CAF-207",
  "current_stage": "align",
  "stage_history": [
    { "stage": "start",  "status": "done", "ts": "2026-05-06T12:00:00Z" },
    { "stage": "figma",  "status": "done", "ts": "2026-05-06T12:03:00Z" },
    { "stage": "detect", "status": "done", "ts": "2026-05-06T12:03:30Z", "result": "B" }
  ],
  "figma": {
    "node_ids": ["713:12154", "713:12515"],
    "receipt": ".dev/figma-context.md",
    "raw_dir": ".dev/figma-raw/"
  },
  "openspec": {
    "state": "B",
    "change_dir": "openspec/changes/add-favourite-driver-from-rating-screen/"
  }
}
```
