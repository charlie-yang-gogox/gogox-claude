---
name: resolve-pr-comments
description: Resolve all unresolved comments on a GitHub PR end-to-end. Classifies each comment, presents a single strategy table for human approval, then auto-fixes code, posts replies (in English), batch-commits, pushes, and marks stale threads resolved. Use when asked to "resolve PR comments", "address PR feedback", "handle review comments", or "process PR comments".
---

# Resolve PR Comments

> **One-line summary**: Reads every unresolved comment on the target PR, classifies into FIX / REPLY / STALE / DEFER, gets one batch approval from the human, then auto-executes everything — code fixes, English replies, a single combined commit, push, and STALE-thread resolution.
>
> **Locked design decisions** (do not re-litigate):
> - Replies are always in **English**, regardless of reviewer's language.
> - **One** HITL gate only — the strategy table. After approval the skill runs to completion (push included) without further prompts.
> - All `[FIX]` changes land in **one combined commit**.
> - `[STALE]` threads are auto-resolved on GitHub; `[FIX]` and `[REPLY]` threads are **left open** for the reviewer to close.
> - No `/code-review` / `verify-agent` pass on the fixes — this skill is focused on comment turnaround, not correctness audit. But `format` + `check-test` **do** run as a build-sanity gate so we never push something that breaks the build.

## Inputs

- **Optional PR identifier** as the only argument:
  - `<number>` (e.g. `337`) — resolved against the cwd repo's default remote.
  - GitHub URL (e.g. `https://github.com/gogovan/gogox-client-flutter/pull/337`).
- If omitted, infer the PR from the cwd worktree's current branch via `gh pr view --json number,headRefName,url,baseRefName`. Abort if no PR is associated.

## Prerequisites

- `gh` CLI authenticated (`gh auth status`).
- cwd is inside a git repo. For the `<PR#>` form, cwd may be the main checkout — the skill does not require the matching worktree to exist unless `[FIX]` rows are present (see Step 5).

## Steps

### 1. Resolve context

```bash
# Parse argument; fall back to current branch.
# Store: PR_NUMBER, REPO (owner/name), HEAD_REF, BASE_REF, PR_URL
gh pr view "${ARG:-}" --json number,headRefName,baseRefName,url,headRepository,headRepositoryOwner
```

Abort early if:
- No PR found.
- PR is already `MERGED` or `CLOSED` — print status and exit.

### 2. Fetch all unresolved threads

Use the GraphQL API to get review threads with their `isResolved` flag and full comment bodies — REST does not expose thread resolution state.

```bash
gh api graphql -f query='
query($owner:String!, $name:String!, $number:Int!) {
  repository(owner:$owner, name:$name) {
    pullRequest(number:$number) {
      reviewThreads(first:100) {
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          comments(first:50) {
            nodes { id databaseId author{login} body createdAt diffHunk }
          }
        }
      }
      comments(first:100) {
        nodes { id databaseId author{login} body createdAt }
      }
    }
  }
}' -F owner="$OWNER" -F name="$NAME" -F number="$PR_NUMBER"
```

Collect two lists:
- **Review threads** where `isResolved == false` — these become the candidates for FIX/REPLY/STALE/DEFER.
- **Issue comments** (top-level PR conversation) — same classification, but no thread to resolve. Treat as REPLY/DEFER only (FIX is rare here; if the comment requests code change, classify FIX and reply on the issue comment).

**Self-authored comment handling** — do NOT blanket-exclude comments authored by the current `gh api user` login. The rule is finer:

- **Include** self-authored comments that are *thread starters* or *top-level PR comments* (issue comments / review summaries) — these are self-reviews and carry actionable findings. Self-reviewing your own PR before requesting review is a common pattern; those findings must be classified like any reviewer's.
- **Exclude** self-authored comments that are *replies inside an existing thread* (i.e. not the first comment in the thread) — those are the user's own prior responses, already accounted for.

Concretely: for each review thread, look at `comments.nodes[0]` (the starter). If `comments.nodes[0].author.login == <self>`, keep the thread. Only skip threads where the starter is someone else AND every later comment is self-authored (pure self-reply chain with no outstanding reviewer ask).

For top-level PR `issueComment`s and `pullRequestReview` bodies authored by self → always include.

### 3. Classify each comment

For each unresolved item, decide one of:

| Label   | Meaning                                                                             | Action in step 5                                       |
|---------|-------------------------------------------------------------------------------------|--------------------------------------------------------|
| `FIX`   | Reviewer wants a code change (bug, nit, suggestion that requires edit).             | Edit code + post reply citing the commit SHA.          |
| `REPLY` | Only needs a written response (clarification, disagreement, explanation).            | Post English reply.                                    |
| `STALE` | Already addressed by a later commit on this branch, or the line no longer exists.   | Post reply citing the resolving commit + auto-resolve. |
| `DEFER` | Out of scope for this PR — should become a follow-up ticket.                        | Post reply pointing to follow-up + leave open.         |

Classification heuristics:
- If `isOutdated == true` AND the diff hunk no longer matches HEAD → `STALE`.
- If the comment text is purely interrogative ("why did you …?", "is this intentional?") → `REPLY`.
- If the comment proposes a concrete change ("rename to X", "extract this", "this leaks Y") → `FIX`.
- If the requested change touches files outside the current PR's diff scope, or implies a broader refactor → `DEFER`.

### 4. HITL gate — the only one

Print a single Markdown table (do **not** use `AskUserQuestion` — the table is too wide). One row per comment:

```
| # | Label | File:Line              | Author    | Snippet (first 80 chars)            | Plan                                       |
|---|-------|------------------------|-----------|-------------------------------------|--------------------------------------------|
| 1 | FIX   | lib/foo.dart:42        | alice     | rename to handleSubmitTap…          | rename method + update 2 call sites        |
| 2 | REPLY | (PR conversation)      | bob       | why do we need this provider?…      | explain: needed because cascading refresh… |
| 3 | STALE | lib/bar.dart:117       | carol     | this null-check is redundant…       | already removed in commit 8a7c1f2          |
| 4 | DEFER | lib/baz.dart:88        | dave      | should also handle the V2 endpoint… | reply: follow-up ticket needed             |
```

Then ask the user in plain text:

> Approve all? Reply `yes` to execute, or list row numbers to skip (e.g. `skip 2,4`).

Parse the response:
- `yes` / `y` / `approve` → execute all rows.
- `skip N[,N…]` → execute all except listed.
- Anything else → abort with no side effects.

### 5. Execute (no further prompts after this point)

Order matters: apply `[FIX]` edits first so the commit SHA is known before posting replies.

**5a. Apply FIX edits**

For each `FIX` row not skipped:
- Read the file at `path:line`.
- Apply the smallest edit that satisfies the reviewer. If the reviewer's request is ambiguous, pick the most conservative interpretation — do not bundle unrelated cleanup.
- If a `FIX` row would require touching files outside the PR's diff scope, downgrade it to `DEFER` at execution time, log the downgrade, and continue.

**5b. Build sanity gate (only if any FIX was applied)**

Run, in order, and **abort before commit** if any step fails:

1. `/format` — platform-appropriate formatter + static analyzer (dart format / detekt / swiftformat etc., per repo profile).
2. `/check-test` — incremental test suite for affected files.

If `/format` produces additional formatting diffs, fold them into the same staged set and continue. If `/check-test` fails:
- Print the failing test output.
- Leave the working tree dirty (do **not** revert the FIX edits).
- Abort the skill with a clear message: "Build sanity failed — fix the failing tests/analyzer warnings, then re-run /resolve-pr-comments to continue." No replies are posted, no thread is resolved, nothing is pushed.

This is the only gate after the strategy-table approval, and it only triggers on real build failures — clean runs continue straight through to commit.

**5c. Commit**

Single commit with all FIX changes:

```bash
git add -p   # interactive only if absolutely needed; otherwise add the modified files explicitly
git commit -m "address PR #${PR_NUMBER} comments

- <one bullet per FIX row, file:line + one-line summary>
"
```

Skip the commit step entirely if no FIX rows were executed.

**5d. Post replies**

For each non-skipped row, post a reply on the originating thread / issue comment via:

- Review-thread reply: `gh api graphql` mutation `addPullRequestReviewThreadReply` with the thread ID (or REST `POST /repos/{owner}/{repo}/pulls/{pr}/comments/{comment_id}/replies` with the parent comment's `databaseId`).
- Issue-comment reply: `gh pr comment <PR#> --body "..."` (single new comment; GitHub has no inline reply for issue comments).

Reply templates (English, terse — no emoji, no "thanks for the review", no boilerplate):

- `FIX`:    `Done in <SHA>. <one-sentence what changed>.`
- `REPLY`:  `<direct answer, 1-3 sentences>.`
- `STALE`:  `Already addressed in <SHA>. <one-sentence what changed>.`
- `DEFER`:  `Out of scope for this PR — tracking as follow-up. Will file <ticket-prefix> after merge.` (If a ticket already exists, cite it.)

`<SHA>` for FIX = the commit just created in 5c. For STALE = the historical commit that resolved it (find via `git log -S '<symbol>' -- <path>` or `git blame`).

**5e. Resolve STALE threads**

Only for `STALE` rows. Use `gh api graphql` mutation `resolveReviewThread` with the thread ID:

```bash
gh api graphql -f query='
mutation($id:ID!) {
  resolveReviewThread(input:{threadId:$id}) { thread { isResolved } }
}' -F id="$THREAD_ID"
```

Do **not** resolve FIX/REPLY/DEFER threads — leave them for the reviewer.

**5f. Push**

```bash
git push
```

If the branch has divergent upstream, fail loudly — do not force-push.

### 6. Final report

Print a short summary to the terminal:

```
Resolved <N> PR comments on #<PR#>:
  FIX:   <a>  (commit <SHA>)
  REPLY: <b>
  STALE: <c>  (auto-resolved)
  DEFER: <d>
  SKIP:  <e>  (per your selection)

Pushed to <HEAD_REF>. PR: <PR_URL>
```

Done. No follow-up prompts.

## Failure modes

- **GraphQL rate limit / 5xx during fetch**: retry once with 2s backoff. If still failing, abort before step 4 (no side effects yet).
- **`git push` rejected**: leave the commit in place, print the rejection reason, and instruct the user to rebase. Do not auto-rebase, do not force-push.
- **Reply post fails for one row mid-batch**: continue with remaining rows, collect failures, surface them in the final report with thread IDs so the user can manually retry.
- **FIX edit fails (e.g. file no longer matches reviewer's quoted diff)**: downgrade to REPLY, post `<comment> — could not locate the referenced code at HEAD; please re-flag if still relevant.` and continue.

## Non-goals

- This skill does **not** run a `/code-review` / `verify-agent` correctness audit on the FIX edits. It only runs `/format` + `/check-test` as a build-sanity gate.
- This skill does **not** resolve threads on your behalf except for `STALE`. The reviewer owns thread closure on FIX/REPLY/DEFER.
- This skill does **not** force-push, rebase, or rewrite history.
- This skill does **not** post anything to Linear — it operates purely on GitHub.
- This skill does **not** ask follow-up questions after the strategy table is approved (except when the build-sanity gate fails, in which case it aborts cleanly without prompts).
