---
name: pull-request-review-loop
description: >
  Drive a PR from draft to ready-for-review, unattended. Opens the PR if the
  current branch has none, runs the @claude AI-review loop until clean
  (auto-fixing mechanical findings, pausing for human-decision ones), un-drafts,
  waits for CI to go green, then assigns reviewers — which lets the repo's
  existing notify bot announce the PR. Reviewers come from --reviewers or the
  repo's .gogox-claude.yaml. Use when you have finished a feature/fix and want
  the whole PR-handoff sequence automated. Does NOT post to Slack itself.
---

# Pull Request Review Loop

> **One-line summary**: From the current branch's PR (created as a draft if none exists), post the `@claude` review trigger and loop fix→re-trigger until the AI review is clean; then un-draft, wait for CI green, and assign reviewers. The repo's existing `pr-review-notify` GitHub Action turns that assignment into the Slack notification — this skill never touches Slack.
>
> **MCP prerequisites**: none. Uses the `gh` CLI only (must be authenticated: `gh auth status`). No Slack / Linear / Atlassian MCP is required in this version.
>
> **Locked design decisions** (do not re-litigate):
> - **No PR id argument.** The PR is always resolved from the current branch; if there is none, the skill creates one via `/pull-request --draft`. The PR number is never known in advance and never needs to be.
> - **English only — interface and artifacts.** Every progress line, commit, PR body, and review reply is English. There is no per-user language switch; English is the team lingua franca.
> - **This skill never posts to Slack.** Channel notification is owned by the repo's existing `pr-review-notify.yml` GitHub Action (the `code-review-help-help` bot), which fires on the `review_requested` event and posts to the channel it maps for that repo. This skill's only job toward notification is to *assign the reviewer*; the Action does the rest. (Posting from the skill itself — to support a per-run `--channel` — is the deferred "B version"; it needs a Slack bot token and is out of scope here.)
> - **Assign reviewers AFTER CI is green.** Because the notify bot fires on assignment, delaying the assignment until CI passes makes the announcement land only once the PR is green — preserving the "don't ping reviewers on a red PR" intent, without the skill sending anything.
> - **Human-decision findings stop the loop — and this skill, not the delegate, catches them.** A review finding that needs a product/AC call (or that cannot be fixed in code) is surfaced and the loop halts for the human. The skill never rubber-stamps past it. Because `/resolve-pr-comments --auto` runs unattended and never pauses, the triage that catches these findings happens in Step 2 *before* delegating (see Step 2.3) — the delegate handles only the mechanical fixes.

## Inputs

Invoke directly — no input required for the common case.

- **`--reviewers=a,b,c`** (optional) — comma-separated GitHub logins to request review from, overriding the repo default for this run. Whitespace and a leading `@` are tolerated (`@AlexWangGoGoX, AlanCHTseng` → `AlexWangGoGoX`, `AlanCHTseng`).
- **`--max-rounds=N`** (optional, default `5`) — hard cap on AI-review iterations, so a never-converging review can't loop forever.
- **`--no-review`** (optional) — skip the `@claude` AI-review loop (Step 2) and go straight to un-draft → CI → assign. For when you only want the handoff half.

When `--reviewers` is absent, the reviewer list comes from the repo profile (Step 1). When neither is present, the assign step is skipped with a printed note (and so no notification fires).

## Steps

### 0. Log usage

```bash
echo "{\"skill\":\"pull-request-review-loop\",\"user\":\"$(whoami)\",\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" >> ~/.gogox-claude-usage.jsonl 2>/dev/null || true
```

### 1. Resolve context

1. **Profile.** Read `<repo-root>/.gogox-claude.yaml` (the same file every gogox-claude command resolves). The new optional block this skill reads:

   ```yaml
   pr_review:
     reviewers: [AlexWangGoGoX, AlanCHTseng]   # default reviewers for this repo
   ```

   `pr_review` is entirely optional — a repo without it just relies on `--reviewers` (and skips the assign step if neither is given).
2. **Branch + PR.** `gh pr view --json number,url,state,isDraft 2>/dev/null`.
   - PR exists and `OPEN` → use it.
   - No PR (or last one `MERGED`/`CLOSED`) → run `/pull-request --draft` to push the branch and open a draft PR, then re-resolve. Capture `PR_NUMBER`, `PR_URL`.
3. **Reviewers.** Resolve in priority order: `--reviewers` flag → `pr_review.reviewers` from the profile → empty. Normalize to a deduped list of bare GitHub logins.
4. Abort early with a clear message if `gh` is not authenticated.

### 2. AI-review loop (skip if `--no-review`)

Repeat up to `--max-rounds` times:

1. **Trigger.** Post the fixed trigger comment to the PR (verbatim — see Gogox Context):

   ```
   @claude please review this PR — focus on critical issues and security.
   ```
2. **Wait for the bot.** Poll the PR's comments/reviews until the `@claude` bot leaves a review dated after the trigger (bounded wait; if it never arrives, stop and report — do not silently proceed).
3. **Triage for human-decision findings (this skill's own gate — do this BEFORE delegating).** Read the bot's review yourself and decide whether any finding needs a *human* call: an acceptance-criteria mismatch, a product trade-off, or anything that cannot be settled in code. If so → **STOP**. Print those finding(s) and what the human must decide. The PR stays draft; nothing is assigned; no notification fires.

   This gate lives here, not downstream: `/resolve-pr-comments --auto` (next step) runs fully unattended and never pauses to ask — it sorts every comment into FIX/REPLY/STALE/DEFER and acts on all of them. So the only place a human-decision finding can halt the loop is this triage, *before* the delegation.
4. **Delegate the mechanical fixes.** When triage finds nothing needing a human, hand the bot's findings to `/resolve-pr-comments --auto`. It reads the unresolved threads, applies the code fixes in one combined commit, replies in English, runs its build-sanity gate (`/format` + `/check-test`), and pushes. If that gate fails it stops with `needs-human: comment-fix-failed-tests` and leaves the tree dirty — surface that as a STOP too (a red build is not something this loop fixes).
5. **Decide loop vs stop:**
   - The round produced fixes and the build-sanity gate passed → push has happened; **go to round N+1** (re-trigger, so the bot reviews the fix).
   - The bot's review is **clean** (nothing left to address) → exit the loop, continue to Step 3.
   - Triage flagged a human-decision finding (step 3), or the delegated fix hit `needs-human: comment-fix-failed-tests` (step 4) → **STOP** and report. The PR stays draft; nothing is assigned; no notification fires.
6. If `--max-rounds` is hit while findings remain → STOP and report (same as the human-decision halt, with the round cap noted).

### 3. Ready for review (un-draft)

1. **Un-draft** the PR: `gh pr ready "$PR_NUMBER"`.
2. **WIP-check workaround.** This repo's Marketplace WIP app leaves a stale `pending` check after un-draft (it does not listen for `ready_for_review`). Check `gh pr checks "$PR_NUMBER"`; if a WIP check is `pending`, nudge the `edited` event it *does* listen for by appending a trailing space to the title via REST and then reverting it:

   ```bash
   gh api -X PATCH "repos/$REPO/pulls/$PR_NUMBER" -f title="$ORIG_TITLE "   # add trailing space
   sleep 3
   gh api -X PATCH "repos/$REPO/pulls/$PR_NUMBER" -f title="$ORIG_TITLE"    # revert
   ```

### 4. Wait for CI to go green

Poll until terminal, reusing the `/code-review` recipe:

```bash
gh pr checks "$PR_NUMBER" --repo "$REPO" --json name,state \
  --jq '[.[].state] | if all(. == "SUCCESS") then "green"
                     elif any(. == "FAILURE") then "red"
                     else "pending" end'
```

- `green` → continue to Step 5.
- `pending` → keep polling (bounded; print progress).
- `red` → **STOP**. Print which check failed. Fixing CtI failures (test/build) is out of this skill's scope — that is a human or a `/test-fix-loop` decision. Nothing is assigned; no notification fires on a red PR.

### 5. Assign reviewers → (existing bot announces)

Only reached when CI is green and the reviewer list is non-empty.

```bash
# one -f per reviewer; REST, NOT `gh pr edit --add-reviewer`
# (the GraphQL path fails when the token lacks read:org)
gh api "repos/$REPO/pulls/$PR_NUMBER/requested_reviewers" \
  -f 'reviewers[]=AlexWangGoGoX' -f 'reviewers[]=AlanCHTseng'
```

This assignment emits the `review_requested` event, which the repo's `pr-review-notify.yml` Action turns into the Slack message — so the channel notification happens here, automatically, on a green PR. The skill posts nothing itself.

If the reviewer list is empty (no flag, no profile default), skip this step and print: `no reviewers resolved — un-drafted & green, assign manually to trigger the notify bot`.

### 6. Done

Print a summary (see Output). No state is persisted; every marker lives in GitHub.

## Gogox Context

- **Trigger comment (verbatim):** `@claude please review this PR — focus on critical issues and security.` Summons the Claude GitHub App. This is a machine instruction to a bot — it stays English and fixed, never localized or templated per-user.
- **Reviewer default source:** `pr_review.reviewers` in the repo's `.gogox-claude.yaml`. GitHub logins seen in this org: `AlexWangGoGoX`, `AlanCHTseng`, `charlie-yang-gogox`.
- **Notification is NOT this skill's job.** The repo's `pr-review-notify.yml` GitHub Action (the `code-review-help-help` Slack app) fires on `review_requested` and posts to the channel it maps per base repo (e.g. `gogox-driver-flutter` → `#da-ai-revamp-dev`). This skill only assigns the reviewer; do not add a Slack call here (that is the deferred B version).
- **Assign via REST, not `gh pr edit`.** `gh pr edit --add-reviewer` uses GraphQL and fails when the local token lacks the `read:org` scope; the `requested_reviewers` REST endpoint works regardless.
- **WIP app quirk:** a `pending` WIP check after un-draft is cleared by an `edited` event — the title append-and-revert in Step 3 triggers it.

## Output

A short summary:

```
PR #<n> — <url>
AI review: <clean in N rounds | stopped: needs human (…) | skipped>
Un-draft: done · WIP check: <passed | nudged>
CI: <green | red (<check>) | timed out>
Reviewers: <assigned: a, b | skipped (none resolved)>
Notify: <handled by pr-review-notify Action on assignment | not fired (no assign / red CI)>
```

## How this was used last

> Update this footer when you use the skill, so the next person knows the real-world use case.
> Format: `YYYY-MM-DD by @username — one-line context`

- 2026-06-23 by @broccoli.huang — initial draft (A version: reviewers from --reviewers / .gogox-claude.yaml; channel left to the existing pr-review-notify bot; no Slack token needed). Not yet run on a real PR.
