Perform a code review of the current git branch or a remote PR by spawning an independent agent, then optionally post the result as a PR comment.

Accepts an optional argument: a PR number, PR URL, or branch name.

- `/code-review` — review the current git branch (existing behavior)
- `/code-review 228` — review PR #228 remotely without checkout
- `/code-review https://github.com/gogovan/.../pull/228` — same, extracted from URL
- `/code-review feat/CAF-100` — review by branch name (looks up the PR)

## Steps

1. Parse the argument (if provided). Check these in order — **first match wins**:
   1. If it looks like a number (e.g. `228`), treat it as a PR number.
   2. If it looks like a GitHub PR URL (contains `pull/`), extract the PR number from the URL path (the last numeric segment after `/pull/`).
   3. If it looks like a branch name, resolve it to a PR number:
      ```
      gh pr list --head "<branch_name>" --json number --jq '.[0].number'
      ```
      If the result is empty or null, stop and tell the user no PR was found for that branch.
   4. If no argument is provided, this is a local review of the current branch (existing behavior).

2. Use the Agent tool to spawn the `git-branch-code-reviewer` agent.
   - If a PR number was resolved, pass it in the prompt: `"Review PR #<number> remotely. Do NOT use local git branch — fetch all information via gh CLI."`
   - If no PR number (local mode), use the existing prompt without a PR number.
   Wait for it to complete and return its review output.

3. Output the review result directly to the user.

4. Determine the PR for commenting:
   - If a PR number was already resolved from the argument, use it.
   - Otherwise, check if there is an open PR for the current branch:
     ```
     gh pr view --json number,url 2>/dev/null
     ```
   - If no PR exists, skip step 5.

5. Ask the user whether to post the review as a PR comment.
   - If yes, post the review content as a PR comment prefixed with `# Internal code review`:
     ```
     gh pr comment <pr_number> --body "# Internal code review\n\n<review content>"
     ```
   - If no, skip.
