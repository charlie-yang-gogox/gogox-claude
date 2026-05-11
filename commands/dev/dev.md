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
   - Else read `~/.claude/commands/profiles/registry/$(basename "$(git rev-parse --show-toplevel)").yaml` for `platform` and `product`.
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
7. **Persist to disk (MANDATORY)**: Write two artifacts. Step 3b gates on both — a single-line stub no longer passes.

   a. **Raw response per node** — for each `<fileKey>:<nodeId>` fetched, save the raw `get_design_context` response JSON (the entire response body, unmodified) to `.dev/figma-raw/<sanitized-nodeId>.json`. Sanitize by replacing `:` with `_` for the filename. Compute the sha256 of each raw file:

      ```bash
      shasum -a 256 .dev/figma-raw/<sanitized-nodeId>.json | awk '{print $1}'
      ```

   b. **Summary** — write `.dev/figma-context.md`. The first line MUST be a `Fetched:` receipt of the form below. Every `<fileKey>:<nodeId>` parsed from the ticket's Figma URL(s) MUST appear at least once in the body. Step 3b validates both.

      ```markdown
      Fetched: <ISO-8601 timestamp> sha256=<rawNodeId1>=<hash1>,<rawNodeId2>=<hash2>

      # Figma context — <ticket-id>

      ## Nodes

      ### <fileKey>:<nodeId>
      - URL: <original Figma URL>
      - Title: <node title from get_design_context>
      - Key sections / layers: <bulleted list>
      - Components used: <from search_design_system — name + library match>
      - Design tokens: <from get_variable_defs — token name → value>
      - Notes / a11y / interaction: <anything else from the response that affects implementation>

      ### <next fileKey>:<nodeId>
      ...
      ```

      In the `sha256=` portion, list `<rawNodeId>=<hash>` pairs separated by commas. The `=` between id and hash is the unambiguous separator: node IDs may contain `:` (Figma chained sub-node syntax), but hashes are hex and never contain `=`. Use the original node ID (with `:`), not the sanitized filename form. Example: `Fetched: 2026-05-06T11:30:00Z sha256=713:12154=abc123…,713:12515=def456…`.

   Use **Write** even if `.dev/` or `.dev/figma-raw/` do not yet exist.

**If a Figma API call fails:**

- Retry once.
- **If still failing in `<auto-mode>`**: write `.dev/figma-context.md` with first line `Fetched: FAILED — <error message>` listing the attempted URL(s). Step 3b will STOP the pipeline. Do NOT silently continue to implementation — UI work without design context is the failure mode this gate exists to prevent.
- **If still failing in default mode**: write the same FAILED stub, then use **AskUserQuestion** to surface the failure with two options: `Abort` (stop the skill, retry later) or `Proceed without Figma` (artifact will be flagged; user must accept the visual risk explicitly).

**If no Figma URL is found:**

- **If `<auto-mode>`**: Log "No Figma URL found in ticket. Proceeding without Figma design reference." Do not create `.dev/figma-context.md`.
- **If not `<auto-mode>`**: Use **AskUserQuestion** to ask: "No Figma URL found in the ticket. Do you have a Figma link for this feature?"
  - If the user provides one, fetch it as above.
  - If the user says no, proceed without Figma — but note in artifacts that no Figma design was available.

### 3b: Figma provenance gate (hard block)

Before proceeding to Step 4, run all four checks below in order. The previous test-only check (`test -s`) was insufficient — a one-line stub passed it. None of the rules below can be skipped.

1. **File presence**: if a Figma URL was detected in 3a, `.dev/figma-context.md` must exist. Missing → STOP with:
   > "Figma URL was detected in the ticket but `.dev/figma-context.md` is missing. The skill refuses to proceed without a real `get_design_context` fetch."

2. **Failure stub**: if the file's first line starts with `Fetched: FAILED`:
   - **In `<auto-mode>`**: STOP. Auto pipelines do not silently fall back to Figma-less implementation.
   - **In default mode**: STOP and ask the user (via **AskUserQuestion**) whether to proceed without Figma. Continue only on explicit opt-in; record the opt-in in the final artifact summary.

3. **Hash & content validation**: parse the `sha256=<nodeId>=<hash>,...` portion of the first line by splitting on `,` first, then splitting each pair on the LAST `=` (since node IDs may contain `:` but hashes never contain `=`). For each pair:
   - Recompute `shasum -a 256 .dev/figma-raw/<sanitized-nodeId>.json | awk '{print $1}'` and compare to the recorded hash. Mismatch → STOP.
   - The raw `<nodeId>` (including the `:` separator) must appear at least once in the body of `.dev/figma-context.md`. Missing → STOP.

   Additionally, every `<fileKey>:<nodeId>` parsed from the ticket's Figma URL(s) must have an entry in the receipt AND a `### <fileKey>:<nodeId>` section in the body. Any URL-listed node not represented → STOP.

4. **Anti-pattern reminder**: `grep`-ing existing OpenSpec artifacts for figma node IDs does NOT satisfy this gate. Artifacts may have been authored by another agent (e.g. an upstream port pipeline) that copied node IDs as metadata without ever loading the design. The only acceptable proof is a fresh `.dev/figma-context.md` written this run that passes 1–3.

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

## Step 4.5: Figma alignment check (State B/C only)

_Skip this step entirely if state is A, or if `.dev/figma-context.md` does not exist._

Existing OpenSpec artifacts authored by an upstream pipeline (e.g. `/port`) often carry Figma node IDs as **copied metadata** without the original author ever having loaded the design. State B/C reuse means `/opsx:apply` will execute against those artifacts as-is — so any Figma narrative that was inferred rather than fetched will silently propagate into implementation. This step closes that gap.

Run the alignment check:

1. Extract every `fileKey:nodeId` and every `figma.com/design/...` URL referenced in the existing artifacts:
   ```bash
   grep -rEn 'figma\.com/design/|node-id=|[0-9]+:[0-9]+' openspec/changes/<change-name>/ || true
   ```
2. For each `<fileKey>:<nodeId>` that appears in `.dev/figma-context.md` (the freshly-fetched receipt):
   - **Node ID citation**: confirm the artifacts cite that exact node ID. If an artifact mentions Figma in narrative text but cites no node ID, treat as a conflict (the prose was likely inferred).
   - **Token grounding (structural rule)**: from the receipt's `### <fileKey>:<nodeId>` section, extract every token name listed under `Components used:` and `Design tokens:`. The artifacts' narrative for that node MUST cite at least one of those tokens **verbatim** (case-sensitive substring match). If zero tokens match, treat as a conflict — the narrative was generated without grounding in the actual fetch.

   This is an algorithmic check, not LLM-judged "does the prose feel right." Run it as: for each node, `grep -F` each token from the receipt against `openspec/changes/<change-name>/**/*.md` — at least one hit per node is required.
3. **If any inferred-but-not-supported narrative is found**:
   - **In `<auto-mode>`**: STOP and write the conflict list to `claude-reports/<ticket-id>/figma-alignment.md`. Do not run `/opsx:apply` on artifacts that contain hallucinated visual claims.
   - **In default mode**: list the conflicts to the user and use **AskUserQuestion**:
     - `Rebuild affected sections` — call `/opsx:rebuild <change-name> --section <section>` for each affected artifact (or guide the user to do so), then re-run Step 4.
     - `Proceed anyway (accept risk)` — record the opt-in in the artifact summary.
     - `Stop` — end the skill.
4. If the artifacts cite all node IDs and the spot-checks pass, log `Figma alignment: OK` and continue.

This step is the structural answer to "why did /port-generated specs need 80% rewrite during archive sync" — catch the divergence before `/opsx:apply` builds on top of it, not after.

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

Execution depends on mode (per `plans/dev-ff-subagent-isolation.md` §3.6 v9):

- **If `<auto-mode>`**: Run `/opsx:apply <change-name>` directly in the current session. No agent spawn. `--auto` may run inside a `general-purpose` subagent dispatched by `/ggx-dispatcher`, where nested opus spawn is unreliable — inline is the only safe path.
- **If not `<auto-mode>`**: Spawn `dev-agent` (opus, worktree-isolated, `commit: false`) to run `/opsx:apply`. Pass the inputs documented in `commands/dev/dev/apply.md` Step 4D.1. Parse `.dev/apply-result.md` afterwards. On `BLOCKED_CLARIFICATION`, surface the question via **AskUserQuestion** and fall back to inline `/opsx:apply` in main to resume from the next `[ ]`.

Stop when all tasks are complete (`tasks.md` checkboxes all `[x]`), or when `/opsx:apply` pauses for clarification that cannot be resolved.

---

## Steps 6–9: Post-implementation (auto mode only)

_Skip Steps 6–9 entirely if `<auto-mode>` is false. Jump to Output._

### Step 6: Test, verify, format, and commit

1. Run `/check-test --fix` (if available for `{platform}`) or fall back to `{test_cmd}` directly to run tests and auto-fix failures.
   - If still failing after fix attempts, note in report and proceed.
2. **Spawn `verify-agent`** to audit the diff. This is mandatory in auto mode — same-session self-audit cannot catch the misses it produced (CAF-467 checkbox case). Pass:
   - `base` — the trunk ref the worktree branched from (e.g. `origin/trunk`).
   - `change name` — the OpenSpec change name from Step 4.
   - `figma context path` — `.dev/figma-context.md` if it exists.
   Use `mode: "bypassPermissions"` per the auto-mode rules.

   After `verify-agent` returns, read `.dev/verify-pass.md`:
   - **`Status: CLEAR`** → proceed to step 3.
   - **`Status: BLOCKED`** → run the recovery sequence below.
   - **File missing** → treat as `BLOCKED`. Do NOT fall back to "trust the implementer" — the absence of the report is the failure signal.

   **BLOCKED recovery sequence (executed in order, exactly once):**
   1. For each finding in `.dev/verify-pass.md`, edit the affected files to address it.
   2. Re-run `/check-test --fix` (or `{test_cmd}`) to confirm the fixes did not regress tests.
   3. Re-spawn `verify-agent` with the same inputs as the first call.
   4. Read `.dev/verify-pass.md` again:
      - `Status: CLEAR` → proceed to step 3.
      - `Status: BLOCKED` (still) → ABORT. Attach the verify report to the final session report and STOP. Do not loop further — a second BLOCKED means the implementer cannot self-correct from auditor feedback in this session.
3. Run `/format` to format and lint the codebase.
   - If there are lint issues that `/format` cannot auto-fix, fix them.
   - If `/format` made changes, return to step 1 (re-run tests) before committing.
4. Sanitize the index of runtime artifacts before committing:
   - If `.dev/` is not yet listed in the project's `.gitignore`, add it in this commit. `.dev/figma-context.md`, `.dev/figma-raw/**`, and `.dev/verify-pass.md` are proof-of-work runtime artifacts, not source.
   - Evict any already-tracked `.dev/` paths from the index (one-time cleanup for repos that didn't yet have the gitignore):
     ```bash
     git ls-files .dev/ 2>/dev/null | xargs -r git rm --cached -- 2>/dev/null || true
     ```
   This must run before `/commit`. Skipping it lets stale `.dev/` files leak into the PR; the previous commit's `.gitignore` does not retroactively untrack files.
5. Run `/commit` to commit all changes.

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

- Does **NOT** spawn `pm-agent` or `designer-agent`. Artifact prep (`/opsx:ff` / `/opsx:continue`) runs in the main session.
- In default mode, **spawns `dev-agent`** (opus, worktree-isolated) for `/opsx:apply` after the HITL gate — see `commands/dev/dev/apply.md` §4D and `plans/dev-ff-subagent-isolation.md` §3.6 v9.
- In auto mode, `/opsx:apply` runs inline in the current session (no agent spawn — `--auto` may run inside a `general-purpose` dispatcher subagent where nested opus spawn is unreliable).
- Default mode scope: artifact prep + apply only. Does **NOT** handle branching, worktree, tests, format, commit, or PR.
- Auto mode scope: full lifecycle from worktree to PR. Adds Steps 2A and 6–9.
- Auto mode never calls `AskUserQuestion`. All decisions are pre-determined.
- On critical failure in auto mode (can't read ticket, can't create worktree): abort with report, STOP.
- On non-critical failure in auto mode (test failures after 3 attempts): note in report, proceed.
- Does NOT modify `/work` or `/work-v2`.
