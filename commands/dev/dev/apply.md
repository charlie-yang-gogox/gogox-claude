---
name: apply
description: "Stage 5 — generate (state A) or fill in (state C) OpenSpec artifacts as needed, then run /opsx:apply to produce real code changes. In default mode, pauses for user review of artifacts before applying. Auto mode chains through without prompts."
---

# `/dev:apply`

Drives the OpenSpec artifact prep + apply loop. State A creates artifacts from scratch, State C continues partial ones, State B applies directly. Apply runs in the main session — does NOT spawn `dev-agent`.

## Inputs

- `.dev/state.json` (read for `change_name`, `openspec.state`, `platform`, `mode`).
- Linear ticket content (re-fetched if needed for `/opsx:ff` context).
- `<figma-context>` from `.dev/figma-context.md` if it exists.

## Outputs

- Source code changes in the working tree.
- Updated `openspec/changes/<change-name>/` artifacts (state A/C).
- `state.current_stage = "verify"`.

## Step 0: Validate state

Run `/dev:_state-check apply`. STOP on non-zero. Parse for `change_name`, `openspec.state`, `platform`, `mode`.

## Step 1A: Generate artifacts (state A only)

_Skip if `openspec.state != "A"`._

1. Run `/opsx:ff <change-name>`. Prepare the context yourself from the Linear ticket content — do **not** spawn `pm-agent` or `designer-agent`.
2. Pass an additional UI/test instruction tailored to `{platform}`. Keep the **intent** identical: tests covered, reuse existing i18n, accessibility identifiers on every interactive element, semantic label on icon-only buttons.
   - **flutter**: "Make sure tests are also updated. Use existing i18n keys as much as possible. Add a11y keys (accessibility `Key` identifiers) to all interactive widgets following the `*Keys` constant class pattern. For all clickable icon-only buttons (no visible text child), also add a semantic text label via `tooltip` on `IconButton` or `Semantics(label:)` on `GestureDetector` — do not rely on the Key ID alone."
   - **android**: "Make sure tests are also updated. Use existing string resources as much as possible. Add `testTag` (Compose) or `contentDescription` (Views) to all interactive elements. For icon-only buttons, also set `contentDescription` so TalkBack reads a meaningful label — do not rely on the testTag alone."
   - **ios**: "Make sure tests are also updated. Use existing localized strings (`Localizable.strings`) as much as possible. Add `accessibilityIdentifier` to all interactive elements. For icon-only buttons, also set `accessibilityLabel` — do not rely on the identifier alone."
3. After `/opsx:ff` completes, re-run `openspec status --change "<name>" --json` to confirm all `applyRequires` are `done`.
4. **`mode == auto`**: if stalled (some artifacts still pending), run `/opsx:continue` up to 3 rounds. Then proceed to Step 2.
5. **`mode == default`**: review gate — present created artifacts (`proposal.md`, `design.md`, `specs/**/*.md`, `tasks.md`) with one-line summaries. Use **AskUserQuestion**:
   - `Proceed to apply` — go to Step 2.
   - `Revise artifacts` — wait for edits, re-ask.
   - `Stop here` — STOP. Do not advance state. User runs `/dev:apply --force` later to resume.

## Step 1C: Continue artifacts (state C only)

_Skip if `openspec.state != "C"`._

1. Run `/opsx:continue` to fill in remaining artifacts.
2. Re-run `openspec status --change "<name>" --json` until all `applyRequires` are `done`. If it stalls (same artifact pending twice in a row), STOP and report.
3. Same review gate behavior as Step 1A.5 (auto = no gate; default = three-option AskUserQuestion).

## Step 2: Apply

Run `/opsx:apply <change-name>` directly in the main session. Do **not** spawn `dev-agent`.

Stop when all tasks complete, or when `/opsx:apply` pauses for clarification (in which case STOP this stage and return control — the user resumes manually, or `/dev:ff` retries).

## Step 3: Commit transition

```bash
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
jq --arg ts "$TS" '
  .current_stage = "verify"
  | .stage_history += [{ stage: "apply", status: "done", ts: $ts }]
' .dev/state.json > .dev/state.json.tmp && mv .dev/state.json.tmp .dev/state.json
```

## Step 4: Stop

Print: `Apply complete. Next: /dev:verify.`

In `mode == default`, also note that `/dev:verify` is auto-mode-only territory — the default-mode user typically stops here and drives commit / format / PR manually. To proceed, run `/dev:verify --force-default` (NOT IMPLEMENTED YET — currently default mode terminates after apply).
