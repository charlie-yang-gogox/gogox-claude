---
name: dev
description: >
  Dev loop for a Linear ticket via OpenSpec. Two modes:
  default — lightweight HITL loop (artifact prep + apply only).
  --auto — full autonomous pipeline (worktree → spec → apply → test → PR).
Prerequisite: >
  - MCP servers for Linear and Figma authenticated.
  - Default mode: already on the branch/worktree for the ticket. Git clean.
  - --auto mode: on trunk with clean working tree. gh CLI authenticated.
    Environment variables USER_NAME and GH_USER_NAME set.
---

# Dev

Orchestrator for the OpenSpec dev loop of a Linear ticket. Project-agnostic — resolves the active repo profile at runtime to pick the right dependency-install / test / format commands.

**Usage**: `/dev <ticket-id> [--auto]`

- `<ticket-id>` — Linear ticket ID (e.g. `CAF-207`). Required in `--auto`; if omitted in default mode, ask.
- `--auto` — Full autonomous pipeline. Adds worktree, tests, format, commit, code review, and PR creation. No human prompts. Designed for unattended/overnight execution.

---

## Step 0: Resolve project profile

1. Determine the active repo:
   - If `<repo-root>/.gogox-claude.yaml` exists, read its `platform` and `product`.
   - Else look up `basename "$(git rev-parse --show-toplevel)"` in `~/.claude/commands/profiles/repos.yaml`.
2. Read `~/.claude/commands/profiles/platform/{platform}.yaml` to obtain `{deps_install}`, `{test_cmd}`, `{format_cmd}`.
3. Hold these values for the rest of the run. Use `{platform}` to scope tool authorizations and UI guidance correctly.

## Mode detection

Parse `$ARGUMENTS`:

- If `--auto` is present → set `<auto-mode>` = true.
- Otherwise → set `<auto-mode>` = false.

---

## Auto mode execution rules

When `<auto-mode>` is true, apply the following throughout all steps:

- **Skip all confirmation prompts** — never call `AskUserQuestion`. Proceed by default.
- **Trusted permissions** — all MCP tool calls, git commands, file operations, and the `{platform}`-native build/test toolchain are pre-authorized (e.g. `fvm`/`flutter`/`dart` for flutter, `./gradlew` for android, `xcodebuild`/`swift` for ios). **All Agent tool calls MUST include `mode: "bypassPermissions"`** to prevent permission prompts during unattended execution.
- **Report output** — save all reports to `claude-reports/<ticket-id>/`.
- **Error handling** — on MCP or network errors, retry once after 30 seconds. If still failing, note the error and proceed if non-critical, or abort with report if critical.
- **Label transitions** — on start: remove `ready-to-dev` label, set ticket status to `In Progress`, assign to self. On PR creation (Step 7): set ticket status to `In Review`. On abort: post failure comment (ticket has no actionable label, human must re-add to retry).

---

## Step 1: Parse input and verify ownership

- Extract `<ticket-id>` from `$ARGUMENTS`.
- **If `<auto-mode>`**: missing ticket-id → STOP immediately.
- **If not `<auto-mode>`**: missing ticket-id → use **AskUserQuestion** to ask. Stop if still missing.
- **Assignee check**: Fetch ticket via `mcp__claude_ai_Linear__get_issue`. If the ticket is not assigned to the current user, STOP with:
  > "Ticket `<ticket-id>` is assigned to `<assignee>`, not you. Aborting to avoid working on someone else's ticket."

## Step 2: Pre-flight checks

**If `<auto-mode>`**:

- Verify git is clean and on `trunk`. If not → STOP with error.
- Skip branch name check (worktree will be created in Step 2A).

**If not `<auto-mode>`**:

- Run `/check-clean` to verify git working tree is clean.
  - _Stop_ if not clean and report.
- Check current branch name. If it does **NOT** contain `<ticket-id>` (case-insensitive), warn:
  > "Current branch is `<branch>`, which does not match ticket `<ticket-id>`. Proceed anyway?"
  - Use **AskUserQuestion** to confirm. Stop on "No".

## Step 2A: Setup worktree and assign ticket (auto mode only)

_Skip this step entirely if `<auto-mode>` is false._

1. Read the Linear ticket to determine branch type (`feat`, `fix`, `test`, `ci`, `chore`).
2. Invoke `/add-worktree <ticket-id> --type <type>` — this handles fetch, branch creation, EnterWorktree, port-settings, and `{deps_install}`.
3. Assign the ticket to self via Linear MCP:
   - Set status to **In Progress**.
   - Set estimate to 1 point if none exists.
   - Find user via `$USER_NAME`.
4. Write the full ticket content to `/tmp/<ticket-id>.md`.

## Step 3: Read Linear ticket and fetch Figma design

- Use Linear MCP (`mcp__claude_ai_Linear__get_issue`) to fetch the ticket (title, description, comments, attachments).
- Keep title and description in context — they are the source of truth for `/opsx:ff`.
- Derive a kebab-case change name from the ticket title. Strip leading ticket IDs / prefixes. E.g.
  - `CAF-207: Add favourite driver from rating screen` → `add-favourite-driver-from-rating-screen`

### 3a: Extract and fetch Figma design (mandatory when URL exists)

Scan the ticket description, comments, and attachments for any Figma URL (`figma.com/design/...`).

**If a Figma URL is found:**

1. Parse the URL to extract `fileKey` and `nodeId`:
   - `figma.com/design/:fileKey/:fileName?node-id=:nodeId` → convert `-` to `:` in nodeId.
   - `figma.com/design/:fileKey/branch/:branchKey/:fileName` → use branchKey as fileKey.
2. Call `mcp__plugin_figma_figma__get_design_context` with the fileKey and nodeId — returns the design spec and screenshot. This is the **authoritative visual reference** for implementation.
3. Call `mcp__plugin_figma_figma__get_screenshot` for a visual snapshot.
4. Call `mcp__plugin_figma_figma__search_design_system` for relevant components.
5. Call `mcp__plugin_figma_figma__get_variable_defs` for design tokens (colors, spacing, typography).
6. Store all Figma data as `<figma-context>` — passed to `/opsx:ff` and used during `/opsx:apply`.

**If a Figma API call fails:**

- Retry once. If it fails again, log: "Figma design was requested but unavailable due to API error. Proceeding with OpenSpec artifacts only. Visual discrepancies should be reviewed manually."
- Set `<figma-context>` to empty and continue — do NOT block the entire workflow on a transient Figma error.

**If no Figma URL is found:**

- **If `<auto-mode>`**: Log "No Figma URL found in ticket. Proceeding without Figma design reference." Set `<figma-context>` to empty.
- **If not `<auto-mode>`**: Use **AskUserQuestion** to ask: "No Figma URL found in the ticket. Do you have a Figma link for this feature?"
  - If the user provides one, fetch it as above.
  - If the user says no, proceed without Figma — but note in artifacts that no Figma design was available.

**Figma provenance check**: If `<figma-context>` exists but existing OpenSpec artifacts (State B/C in Step 4) were created without Figma reference, warn:
> "Artifacts exist but may have been created without Figma design reference. Recommend reviewing artifacts against the Figma design before proceeding."

## Step 4: Detect OpenSpec state

Run in order:

1. `openspec list --json` — list active changes.
2. Match against the derived change name. Accept exact matches; if only one loose candidate obviously relates to the ticket, accept that too.
3. **If `<auto-mode>`**: If multiple plausible candidates, pick the first match.
   - If an existing change has **all `applyRequires` done** (State B) → reuse it, go to Step 5B. Do NOT `rm -rf` — this preserves port-generated artifacts.
   - If an existing change is **partial** (State C) → reuse it, go to Step 5C.
   - If an existing change is **empty or broken** (no valid artifacts) → `rm -rf` and start fresh (State A).

   **If not `<auto-mode>`**: If multiple plausible candidates exist, use **AskUserQuestion** to let the user pick one.
4. For the chosen change: `openspec status --change "<name>" --json`.

Determine state from `applyRequires` and the artifacts array:

| State | Condition | Go to |
|---|---|---|
| **A** — no artifacts | no matching change directory for this ticket | Step 5A |
| **B** — apply-ready | all artifact IDs in `applyRequires` have `status: "done"` | Step 5B |
| **C** — partial | change exists but some `applyRequires` artifacts are still pending | Step 5C |

Announce the detection result, e.g.:
> "Detected state: B (apply-ready). Change: `add-favourite-driver-from-rating-screen`."

## Step 5A: Generate artifacts (no agents)

1. Run `/opsx:ff <change-name>`. Prepare the context yourself from the Linear ticket content in Step 3 — do **not** spawn `pm-agent` or `designer-agent`.
2. Pass an additional UI/test instruction into `/opsx:ff`. Tailor the wording to `{platform}` — keep the **intent** identical (tests covered; reuse existing i18n; accessibility identifiers on every interactive element; semantic label on icon-only buttons):
   - **flutter**: "Make sure tests are also updated. Use existing i18n keys as much as possible. Add a11y keys (accessibility `Key` identifiers) to all interactive widgets following the `*Keys` constant class pattern. For all clickable icon-only buttons (no visible text child), also add a semantic text label via `tooltip` on `IconButton` or `Semantics(label:)` on `GestureDetector` — do not rely on the Key ID alone."
   - **android**: "Make sure tests are also updated. Use existing string resources as much as possible. Add `testTag` (Compose) or `contentDescription` (Views) to all interactive elements. For icon-only buttons, also set `contentDescription` so TalkBack reads a meaningful label — do not rely on the testTag alone."
   - **ios**: "Make sure tests are also updated. Use existing localized strings (`Localizable.strings`) as much as possible. Add `accessibilityIdentifier` to all interactive elements. For icon-only buttons, also set `accessibilityLabel` — do not rely on the identifier alone."
3. After `/opsx:ff` completes, re-run `openspec status --change "<name>" --json` to confirm all `applyRequires` are `done`.
4. **If `<auto-mode>`**: No review gate. If stalled (some artifacts still pending), run `/opsx:continue` up to 3 rounds. Proceed directly to Step 5B.
   **If not `<auto-mode>`**: **Review gate — MUST pause here.** Present the list of created artifacts (`proposal.md`, `design.md`, `specs/**/*.md`, `tasks.md`) with a one-line summary of each, then use **AskUserQuestion** with these options:
   - `Proceed to apply` — continue to Step 5B.
   - `Revise artifacts` — wait for user to request specific edits, apply them, then re-ask.
   - `Stop here` — end the skill; user will run `/opsx:apply` manually later.

   Do NOT auto-advance to Step 5B without explicit user consent.

## Step 5C: Continue artifacts

1. Run `/opsx:continue` to fill in the remaining artifacts.
2. Re-run `openspec status --change "<name>" --json` until all `applyRequires` are `done`. If it stalls (same pending artifact twice in a row), stop and report.
3. **If `<auto-mode>`**: No review gate. Proceed directly to Step 5B.
   **If not `<auto-mode>`**: **Review gate — MUST pause here.** Present the artifacts that were *newly created or updated* by `/opsx:continue` (diff against the prior state, if known; otherwise list the full artifact set). Use **AskUserQuestion** with the same three options as Step 5A:
   - `Proceed to apply` — continue to Step 5B.
   - `Revise artifacts` — wait for user to request specific edits, apply them, then re-ask.
   - `Stop here` — end the skill.

   Do NOT auto-advance to Step 5B without explicit user consent.

## Step 5B: Apply

1. Run `/opsx:apply <change-name>` directly. Do **not** spawn `dev-agent`.
2. Stop when all tasks are complete, or when `/opsx:apply` pauses for clarification.

---

## Steps 6–9: Post-implementation (auto mode only)

_Skip Steps 6–9 entirely if `<auto-mode>` is false. Jump to Output._

### Step 6: Test, format, and commit

1. Run `/check-test --fix` (if available for `{platform}`) or fall back to `{test_cmd}` directly to run tests and auto-fix failures.
   - If still failing after fix attempts, note in report and proceed.
2. Run `/format` to format and lint the codebase.
   - If there are lint issues that `/format` cannot auto-fix, fix them.
3. Run `/commit` to commit all changes.

### Step 7: Code review

1. Run `/code-review` to perform self-review.
2. Address all **critical** issues found.
   - If changes are made, go back to Step 6.1 to re-test and re-commit.
3. Save the review report to `claude-reports/<ticket-id>/code-review.md`.

### Step 8: Create Pull Request

1. Run `/opsx:archive` to archive all OpenSpec changes.
2. Commit the archived changes.
3. Invoke `/check-archive` to verify.
4. Run `/pull-request --draft` to push and create the PR as a draft.
5. After PR is created:
   - Set ticket status to `In Review` via `mcp__claude_ai_Linear__save_issue`.
   - Reviewer assignment is intentionally not automated here — rely on `CODEOWNERS` (or the PR template) to invite reviewers, or assign manually after the PR is up.

### Step 9: Final report

Write a final session report to `claude-reports/<ticket-id>/report.md`:

```markdown
# Auto-dev report: <ticket-id>

**Ticket**: <id> — <title>
**Branch**: <type>/<ticket-id>
**Pull Request**: <PR URL>
**Mode**: --auto (unattended)
**Date**: <YYYY-MM-DD>

## Steps completed
- [ ] Step 1: Parse input — <pass/fail>
- [ ] Step 2A: Worktree + assign — <pass/fail>
- [ ] Step 3: Read ticket — <pass/fail>
- [ ] Step 4: Detect state — <state A/B/C>
- [ ] Step 5: Generate + apply — <pass/fail, N/M tasks>
- [ ] Step 6: Test, format, commit — <pass/fail + test summary>
- [ ] Step 7: Code review — <pass/fail + critical issues>
- [ ] Step 8: Create PR — <pass/fail + PR URL>

## Errors / warnings
<any errors encountered>
```

Post a summary comment to the Linear ticket via `mcp__claude_ai_Linear__save_comment`:

```markdown
## Auto-dev complete: <ticket-id>

**PR**: <PR URL>
**Branch**: <type>/<ticket-id>
**Tests**: <pass/fail summary>
**Review**: <N critical issues addressed>
```

STOP.

---

## Output (default mode only)

_Skip if `<auto-mode>` is true (Step 9 handles output)._

On completion, summarize:

- Ticket ID and title
- Change name and path (`openspec/changes/<name>/`)
- Which state (A / B / C) was taken
- Task progress: `N/M tasks complete`
- Note that format / commit / review / PR are intentionally not handled — user drives next steps manually or via `/format`, `/commit`, `/code-review`, `/pull-request`.

---

## Guardrails

- Does **NOT** spawn any sub-agents (no `pm-agent`, `designer-agent`, `dev-agent`). Everything runs in the main session.
- Default mode scope: artifact prep + apply only. Does **NOT** handle branching, worktree, tests, format, commit, or PR.
- Auto mode scope: full lifecycle from worktree to PR. Adds Steps 2A and 6–9.
- Auto mode never calls `AskUserQuestion`. All decisions are pre-determined.
- On critical failure in auto mode (can't read ticket, can't create worktree): abort with report, STOP.
- On non-critical failure in auto mode (test failures after 3 attempts): note in report, proceed.
- Does NOT modify `/work` or `/work-v2`.
