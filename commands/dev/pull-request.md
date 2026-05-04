# Pull Request — Create or Update PR

Push the current branch and create a new PR or update the existing one. Automatically posts implementation notes to the Linear ticket, comparing what was planned (port artifacts) against what was actually built.

**Arguments:** `--dry-run` to preview PR title, body, and implementation notes without pushing, creating, or posting. `--draft` to create the PR as a draft.

---

## Steps

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
2. Invoke `/format`. If formatting changes exist, it will auto-commit them.
3. Invoke `/check-archive`. If it fails, stop.

### 3. Extract Ticket ID

Parse the branch name to extract a Linear ticket ID (e.g., `CAF-272` from `feat/CAF-272`).

```bash
git rev-parse --abbrev-ref HEAD
```

Extract pattern `[A-Z]+-\d+` from the branch name. If no match, use the branch name as-is.

### 4. Fetch Ticket Title (if ticket ID found)

Use the Linear MCP tool to fetch the issue title:

- Tool: `mcp__linear-server__get_issue` with the extracted ticket ID
- If successful, use `"{TICKET_ID}: {title}"` as the PR title
- If failed or no ticket ID, use the branch name as the PR title

### 5. Build PR Body

Collect commit messages:

```bash
git log origin/trunk..HEAD --pretty='format:- %s' --reverse --no-merges
```

Generate a **Summary** section by reading the commit messages and writing a plain-English description of what this PR does. Be concise — 2-5 bullet points. Write from the perspective of a reviewer who needs to understand the "why" and "what", not the "how".

Build the PR body using this template:

```markdown
#### Ticket ####
[{TICKET_ID}]({LINEAR_URL})

## Summary

{GENERATED_SUMMARY_BULLETS}

## What Changes

{COMMIT_MESSAGES_AS_CHECKLIST}

## Test Plan

{GENERATED_TEST_PLAN}

## Demo

<!-- Add screenshots or screen recordings here -->
```

Where:
- `{TICKET_ID}` is the extracted ticket ID (e.g., `CAF-272`)
- `{LINEAR_URL}` is `https://linear.app/gogox/issue/{TICKET_ID}`
- `{GENERATED_SUMMARY_BULLETS}` is a plain-English summary generated from the commits
- `{COMMIT_MESSAGES_AS_CHECKLIST}` is each commit message formatted as `- [x] <message>`
- `{GENERATED_TEST_PLAN}` is a QA-oriented checklist generated from the diff and commits (see Step 6d)

**If `--dry-run`**: print the PR title, body, and implementation notes (Step 6), then stop. Do not push, create, or post.

### 6. Generate Implementation Notes

This step always runs. It produces a structured comment for the Linear ticket.

#### 6a. Gather context

- Read the full diff: `git diff origin/trunk...HEAD`
- Read commit messages: `git log origin/trunk..HEAD --pretty='format:%s' --reverse --no-merges`

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
- Write as a numbered list (e.g., `1. Open the order screen and verify...`) — no checkboxes in the PR body; the Linear comment uses checkboxes (`1. [ ] ...`)
- Cover the **happy path** first, then **edge cases** and **regression risks**
- If the PR touches UI, include visual verification steps (layout, text, interactions)
- If the PR touches data/logic, include input/output verification steps
- If the PR touches navigation, include flow verification steps
- Keep each step specific — "Verify the button works" is too vague; "Tap the 'Confirm' button and verify it navigates to the order summary screen" is good
- Aim for 3-10 steps depending on PR scope
- Mark any steps that require specific test data or environment setup with a note

The test plan is included in both the PR body (`## Test Plan`, before Demo) and the Linear implementation notes (Step 9).

### 7. Push Branch

```bash
git push -u origin <branch-name>
```

If push fails, surface the error and stop.
If already up-to-date, skip this step.

### 8. Create or Update PR

**If no existing PR (create flow):**
```bash
gh pr create --base trunk --title "<PR_TITLE>" --body "<PR_BODY>" [--draft if flag was passed]
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
5. Taking the **Demo** section **from the existing PR body** (if it has real content), otherwise use the default template

```bash
gh pr edit <number> --body "<MERGED_PR_BODY>"
```

Use a HEREDOC for the body to ensure correct formatting.

### 9. Post Implementation Notes to Linear

If a ticket ID was extracted in Step 3, post the implementation notes as a comment on the Linear ticket.

Use `mcp__linear-server__save_comment` with the following format:

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

If no ticket ID was found, skip this step.

### 10. Report Result

Show:
- PR URL (clickable)
- PR title
- Number of commits included
- Whether the PR was **created** or **updated**
- Whether a Linear comment was **posted** (with link) or **skipped** (no ticket ID)

---

## Rules

- Automatically detects create vs update — no flags needed
- Pre-flight checks (`/check-clean`, `/format`, `/check-archive`) only run on create, not update
- Always push before creating or updating
- Use `gh` CLI (not `hub`) for PR operations
- Summary should be in English, plain language, reviewer-friendly
- Update flow preserves the PR title — only refreshes Summary and What Changes
- Update flow preserves the existing Demo section if it contains real content (images, videos, links, text beyond the placeholder comment)
- Implementation notes are always generated and posted to Linear (when ticket ID exists)
- Deviation analysis requires planning artifacts (port or OpenSpec); without them, only a plain summary is posted
- The `### Deviations from Plan` section should be honest and specific — its purpose is to keep the Linear ticket aligned with reality, not to justify decisions
