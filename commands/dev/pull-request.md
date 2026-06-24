---
name: pull-request
description: >
  Push the current branch and create or update its PR, then post implementation
  notes to the ticket. Resolves the repo's default branch dynamically for the PR
  base and diff base (works on any repo, not just trunk-default).
---

# Pull Request — Create or Update PR

Push the current branch and create a new PR or update the existing one. Automatically posts implementation notes to the ticket (Linear or Jira), comparing what was planned (port artifacts) against what was actually built.

**Arguments:** `--dry-run` to preview PR title, body, and implementation notes without pushing, creating, or posting. `--draft` to create the PR as a draft.

---

## Steps

### 0. Resolve Project Profile

1. Read `<repo-root>/.gogox-claude.yaml` (source of truth). It contains `platform`, `product`, `branch_prefix`, `ticket_system`.
2. If not found, fallback: read `~/.claude/commands/profiles/registry/{repo-name}.yaml` — same fields.
3. If neither found, error: "Run `/init-project` to set up this repo."
4. Read `~/.claude/commands/profiles/org.yaml`.
5. If `branch_prefix` and `ticket_system` are `auto`:
   - Extract the ticket prefix from the branch name (e.g., `CET` from `feat/CET-1234`).
   - Look up the prefix in `org.yaml` → `jira.prefixes` or `linear.prefixes` to resolve `ticket_system`.
   - If prefix not found in org.yaml, skip ticket integration.
6. Resolve org constants for the resolved `ticket_system`:
   - If `jira`: `jira.cloud_id` and `jira.base_url`
   - If `linear`: `linear.base_url`
7. If `ticket_system` is not set or unresolved, warn and skip ticket integration (Steps 4, 9).
8. Resolve the repo's default branch (used as the PR base + diff base below — works on any repo, not just trunk-default):
   ```bash
   source "$HOME/.claude/lib/dev-mode.sh"
   DEFAULT_BRANCH=$(default_branch)   # e.g. trunk (flutter) or main (gogox-claude)
   BASE_REF=$(trunk_ref)              # origin/$DEFAULT_BRANCH
   ```

### 1. Check for Existing PR

```bash
gh pr view --json url,number,state 2>/dev/null
```

- If a PR exists and its state is `OPEN`, skip to Step 5 (update flow).
- If a PR exists but its state is `CLOSED` or `MERGED`, treat it as no PR — continue to Step 2 (create flow).
- If no PR exists, continue to Step 2 (create flow).

### 2. Pre-flight Checks (create flow only)

Run these checks sequentially. Stop on first failure.

1. Invoke `/check-clean`. If it fails, stop.
2. Invoke `/check-archive`. If it fails, stop.

> **Formatting is NOT here anymore.** It used to live in this create-only block,
> which meant the update flow (Step 1 routes an already-OPEN PR straight to
> Step 5) pushed re-runs without ever formatting — the dominant cause of
> `dart format` CI failures on re-pushed PRs. `/format` now runs in **Step 7,
> before every push, on both the create and update flows.**

### 3. Extract Ticket ID

Parse the branch name to extract a ticket ID (e.g., `CAF-272` from `feat/CAF-272`, or `CET-7911` from `feat/CET-7911`).

```bash
git rev-parse --abbrev-ref HEAD
```

Extract pattern `[A-Z]+-\d+` from the branch name. If no match, use the branch name as-is.

### 4. Resolve Ticket Title (if ticket ID found)

The PR title MUST be `{TICKET_ID}: {tracker title}` (see the CRITICAL block
below). Resolve the title in the order below — do **not** stop at the first
silent failure, and do **not** drop to the branch name until every source is
exhausted.

**4a. Cache first.** If `/tmp/{TICKET_ID}.md` exists (the ticket dump written by
`/dev:start` in auto mode), read the ticket title from it and use
`"{TICKET_ID}: {title}"`. This is the only reliable source in headless / cloud
runs where the tracker MCP is not authenticated, and it avoids a redundant MCP
round-trip otherwise.

**4b. Otherwise fetch from the tracker**, branching on `ticket_system` from the
product profile (resolve `TICKET_SYSTEM` per `_ticket-lib.md`):
- **`linear`** → call `get_issue` with `id: {TICKET_ID}` on the **resolved
  Linear MCP server** (see the note below) → use `"{TICKET_ID}: {title}"`.
- **`jira`** → `mcp__claude_ai_Atlassian_Rovo__getJiraIssue` with
  `cloudId: {jira_cloud_id}` from the product profile, `issueIdOrKey:
  {TICKET_ID}`, `responseContentFormat: markdown` → use
  `"{TICKET_ID}: {summary}"`.

> **Resolve the Linear MCP server once, then reuse it for every Linear call in
> this skill** (the Step 4 fetch AND the Step 10 comment). Use whichever server
> is connected this session — prefer `mcp__claude_ai_Linear__*`, otherwise fall
> back to `mcp__linear-server__*` (the project `.mcp.json` server). Both target
> the same workspace, but **only one is live in a given environment**: the
> claude.ai connector is auto-hidden when a project server with the same URL is
> configured (local runs), while headless / cloud runs typically have only the
> claude.ai connector. **Hardcoding either prefix is a silent fetch failure in
> the other environment** — which is exactly how the PR title silently drops to
> the branch name. Mirror `ggx-dispatcher.md` §"All MCP tool calls" (resolve the
> prefix once at the start, use it uniformly).

**4c. Last-resort fallback (must be visible).** Only if no `/tmp/{TICKET_ID}.md`
cache exists AND the tracker fetch fails (or `ticket_system` is unset / no
ticket ID was parsed from the branch), use the branch name as the PR title —
and **emit a visible warning** so the degraded title is never silent:

```
WARN: PR title fell back to branch name — ticket title unresolved
      (cache miss + tracker fetch failed). Expected format: {TICKET_ID}: {title}
```

**CRITICAL — PR title format:**
- The PR title MUST be exactly `{TICKET_ID}: {ticket title from tracker}`.
- Example: `CAF-593: "Account deactivated" dialog shows incorrect body text when user tries to edit payment settings`
- Do NOT use conventional-commit format (e.g., `fix(scope): ...`) for the PR title.
- Do NOT rephrase, summarize, or abbreviate the ticket title — use the exact title from the tracker.

### 5. Build PR Body

Collect commit messages:

```bash
git log "$BASE_REF..HEAD" --pretty='format:- %s' --reverse --no-merges
```

Generate a **Summary** section by reading the commit messages and writing a plain-English description of what this PR does. In plain language, explain **what aspects were fixed / changed** (e.g. which behaviour, screen, data flow, or edge case was corrected and why), not just a restatement of the commit subjects. Be concise — 2-5 bullet points (use bullets whenever there is more than one distinct aspect). Write from the perspective of a reviewer who needs to understand the "why" and "what", not the "how".

**CRITICAL — the PR body MUST use EXACTLY this template. All five sections are REQUIRED. Do NOT omit any section. Do NOT reorder sections. Do NOT rename headings.**

```markdown
#### Ticket ####
[{TICKET_ID}]({TICKET_URL})

## Summary

{GENERATED_SUMMARY_BULLETS}

## What Changes

{COMMIT_MESSAGES_AS_CHECKLIST}

## Test Plan

{GENERATED_TEST_PLAN}

## Demo

<!-- Add screenshots or screen recordings here -->
```

**Mandatory field definitions — follow exactly:**
- `{TICKET_ID}` is the extracted ticket ID (e.g., `CAF-272`, `CET-7911`)
- `{TICKET_URL}` is:
  - Linear: `{linear_base_url}/{TICKET_ID}` (e.g., `https://linear.app/gogox/issue/CAF-272`)
  - Jira: `{jira_base_url}/{TICKET_ID}` (e.g., `https://gogotech.atlassian.net/browse/CET-7911`)
- `{GENERATED_SUMMARY_BULLETS}` — plain-English summary as bullet points (not narrative paragraphs); each bullet states, in plain language, which aspect was fixed / changed and the reason
- `{COMMIT_MESSAGES_AS_CHECKLIST}` — each commit message formatted as `- [x] <message>` (one per commit, in chronological order)
- `{GENERATED_TEST_PLAN}` — QA-oriented numbered checklist generated from the diff and commits (see Step 6d)

**DO NOT:**
- Omit the `#### Ticket ####` section or move the ticket link elsewhere (e.g., bottom of body)
- Omit the `## What Changes` section
- Omit the `## Demo` section
- Use `Test plan` (lowercase) — it MUST be `## Test Plan`
- Write the Summary as narrative paragraphs — use bullet points
- Add a `Linear:` or `Jira:` link outside the Ticket section

**Caller-supplied body**: an invoking command may pass a fully pre-built body (e.g. `/ui-tweak:ff`'s
`pr` stage passes its designer-verifiable summary, including a populated `## Demo` with image links).
Use it verbatim — skip body generation, but still verify the required sections are present. Demo
content must be image **URLs** (GitHub cannot render local file paths in a PR body); uploading local
captures to get a URL is the caller's job, not this command's.

**If `--dry-run`**: print the PR title, body, and implementation notes (Step 6), then stop. Do not push, create, or post.

### 6. Generate Implementation Notes

This step always runs. It produces a structured comment for the ticket.

#### 6a. Gather context

- Read the full diff: `git diff "$BASE_REF...HEAD"`
- Read commit messages: `git log "$BASE_REF..HEAD" --pretty='format:%s' --reverse --no-merges`

#### 6b. Search for planning artifacts

Look for planning files related to this ticket or branch. Search both port artifacts and OpenSpec artifacts.

**Port artifacts** (from `/port`):

```bash
find openspec/changes -name 'port-*.md' 2>/dev/null
```

Match files in `openspec/changes/` (active) or `openspec/changes/archive/` (archived) whose parent directory name contains the ticket ID or a keyword from the branch name. File names: `port-prd.md`, `port-source-analysis.md`, `port-design-changed.md`.

**OpenSpec artifacts** (from `/opsx:new`, `/opsx:ff`):

```bash
find openspec/changes -name 'proposal.md' -o -name 'design.md' -o -name 'tasks.md' 2>/dev/null
```

Same matching logic — look in active and archived directories for the ticket ID or branch keyword. Also check for `specs/*/spec.md` within the matching change directory.

Read all found artifacts from either source.

#### 6c. Produce implementation notes

**If planning artifacts were found** (port or OpenSpec), compare the original plan against the actual implementation and identify:

- **Added** — features, business rules, UI elements, or API calls implemented that were NOT in the plan
- **Changed** — things implemented differently from the plan (different data model, different UI flow, different API contract, etc.)
- **Deferred / Skipped** — items described in the plan that were intentionally not implemented in this PR (note why if discernible from commits)
- **Technical decisions** — architecture or pattern choices that differ from what the plan suggested (e.g., used a different state management approach, different widget structure)

Write in concise English. Each item should be 1-2 sentences. Group by category. Omit empty categories.

**If no planning artifacts were found**, produce a plain implementation summary: a concise description of what the PR implements, extracted from the diff and commits. No deviation analysis.

#### 6d. Generate Test Plan

Based on the diff, commit messages, and any planning artifacts found, produce a QA-oriented test plan. Each item should be a concrete, actionable step that a QA engineer can follow without reading the code.

Guidelines:
- Write as a numbered list (e.g., `1. Open the order screen and verify...`) — no checkboxes in the PR body; the ticket comment uses checkboxes (`1. [ ] ...`)
- Cover the **happy path** first, then **edge cases** and **regression risks**
- If the PR touches UI, include visual verification steps (layout, text, interactions)
- If the PR touches data/logic, include input/output verification steps
- If the PR touches navigation, include flow verification steps
- Keep each step specific — "Verify the button works" is too vague; "Tap the 'Confirm' button and verify it navigates to the order summary screen" is good
- Aim for 3-10 steps depending on PR scope
- Mark any steps that require specific test data or environment setup with a note

The test plan is included in both the PR body (`## Test Plan`, before Demo) and the ticket implementation notes (Step 9).

### 7. Format, then Push Branch

**Format first — both create AND update flows (this is the enforced format point).**
Invoke `/format` — **plain**. Do NOT pass `{format_cmd}` (on flutter that resolves
to `/format --skip-commit`, which aborts on analyze errors and skips its own
commit), and do NOT hand-roll a `git commit` afterward. `/format` resolves the
platform formatter, stages **only the files it changed**, and commits them with a
`style(format):` message (see `commands/dev/format.md`). Because this runs before
*every* push — including re-pushes to an already-open PR — a pushed branch can
never carry unformatted code. On the `prompt` platform (empty `format_cmd`) it is
a no-op. (`--dry-run` already returns at Step 5, so this never runs on a dry run.)

Then push:

```bash
git push -u origin <branch-name>
```

If push fails, surface the error and stop.
If already up-to-date, skip this step.

### 8. Create or Update PR

**If no existing PR (create flow):**
```bash
gh pr create --base "$DEFAULT_BRANCH" --title "<PR_TITLE>" --body "<PR_BODY>" [--draft if flag was passed]
```

**If PR exists (update flow):**

First, read the existing PR body:
```bash
gh pr view <number> --json body -q '.body'
```

Check if the existing `## Demo` section contains any **real content** (i.e., anything beyond the placeholder comment `<!-- Add screenshots or screen recordings here -->`). If it does, **preserve** the existing Demo section verbatim and only replace `## Summary` and `## What Changes`.

Build the updated body by:
1. Taking the **Ticket** section from the new body
2. Taking the **Summary** section from the new body
3. Taking the **What Changes** section from the new body
4. Taking the **Test Plan** section from the new body (always regenerated on update)
5. Taking the **Demo** section **from the existing PR body** if it has real content; **otherwise from
   the new body** if that one has real content (a caller-supplied Demo with image links counts —
   never downgrade it back to the placeholder); otherwise use the default template

```bash
gh pr edit <number> --body "<MERGED_PR_BODY>"
```

Use a HEREDOC for the body to ensure correct formatting.

### 9. Post Implementation Notes to Ticket

If a ticket ID was extracted in Step 3, post the implementation notes as a comment.

**If `ticket_system` is `linear`:**

Use `save_comment` on the **same resolved Linear MCP server** from Step 4b
(`mcp__claude_ai_Linear__save_comment` or its `mcp__linear-server__*`
equivalent — whichever was resolved), with the following format:

```markdown
## Implementation Notes — PR #{pr_number}

**PR:** {pr_url}

### Summary
{plain English summary of what was implemented — same as PR summary}

### Deviations from Plan
{deviation items grouped by category: Added / Changed / Deferred / Technical decisions}

If no planning artifacts were found:
> No planning artifacts found for this ticket. See PR for full details.

### Test Plan
{numbered checklist from Step 6d}

### Commits
{commit messages as a bullet list}
```

**If `ticket_system` is `jira`:**

Use `mcp__claude_ai_Atlassian_Rovo__addCommentToJiraIssue` with:
- `cloudId`: `{jira_cloud_id}` from product profile
- `issueIdOrKey`: the extracted ticket ID
- `contentFormat`: `markdown`
- `commentBody`: same format as above

**If `ticket_system` is not set:**
- Skip this step with a warning: "No ticket system configured. Skipping implementation notes."

If no ticket ID was found, skip this step.

### 10. Report Result

Show:
- PR URL (clickable)
- PR title
- Number of commits included
- Whether the PR was **created** or **updated**
- Whether a ticket comment was **posted** (with link) or **skipped** (no ticket ID / no ticket system)

---

## Rules

- Automatically detects create vs update — no flags needed
- Pre-flight checks (`/check-clean`, `/check-archive`) only run on create, not update
- `/format` runs on **both** create and update flows — in Step 7, immediately before the push — so every pushed branch (including re-pushes to an existing PR) is formatted. Invoked **plain** (never `--skip-commit`); `/format` does its own scoped `style(format):` commit. Skipped only on `--dry-run` (which returns at Step 5).
- Always push before creating or updating
- Use `gh` CLI (not `hub`) for PR operations
- Summary should be in English, plain language, reviewer-friendly
- Update flow preserves the PR title — only refreshes Summary and What Changes
- Update flow preserves the existing Demo section if it contains real content (images, videos, links, text beyond the placeholder comment)
- Implementation notes are always generated and posted to the ticket (when ticket ID exists and ticket system is configured)
- Deviation analysis requires planning artifacts (port or OpenSpec); without them, only a plain summary is posted
- The `### Deviations from Plan` section should be honest and specific — its purpose is to keep the ticket aligned with reality, not to justify decisions
