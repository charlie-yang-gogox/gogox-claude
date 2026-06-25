---
name: pull-request-review-loop
description: >
  Drive the current branch's PR to a reviewer-ready state — but deliberately
  STOP at draft. Opens a draft PR if the branch has none, runs the @claude
  AI-review loop until clean (auto-fixing mechanical findings, pausing for
  human-decision ones), and waits for CI to go green while the PR stays a
  draft — then stops and prints a handoff checklist. It does NOT un-draft,
  assign reviewers, post to Slack, or merge: promoting a draft to a ready PR
  is a human's sign-off that the AI-produced work has been checked, and that
  sign-off is never automated. Use after finishing a feature/fix to automate
  the tedious half of the PR handoff.
---

# Pull Request Review Loop

> **One-line summary**: For the current branch's PR (created as a draft if none exists), post the `@claude` review trigger and loop fix→re-trigger until the AI review is clean, then wait for CI to go green — all while the PR stays a **draft**. The skill stops there and prints a handoff checklist; un-drafting, assigning reviewers, notifying the channel, and merging are deliberately left to a human, because promoting a draft to a ready PR is the team's signal that a person has verified the work.
>
> **MCP prerequisites**: none. Uses the `gh` CLI only (must be authenticated: `gh auth status`). No Slack / Linear / Atlassian MCP is required.
>
> **Locked design decisions** (do not re-litigate):
> - **No PR id argument.** The PR is always resolved from the current branch; if there is none, the skill creates one via `/pull-request --draft`. The PR number is never known in advance.
> - **English only — interface and artifacts.** Every progress line, commit, and review reply is English.
> - **STOP at draft — never un-draft, assign reviewers, notify, or merge.** The team uses a PR's draft/ready state as the marker for "AI-written, not yet verified" (draft) vs "a human has checked it and it is ready" (ready). If the skill auto-un-drafted, the AI would be stamping its own output as human-verified — destroying that signal. So the skill takes the PR only as far as "AI review clean + CI green" and stops, **leaving it a draft**. Un-drafting, assigning reviewers, the channel notification, and the merge are the human's sign-off and are never automated.
> - **CI is waited for *while still a draft*.** The build/test CI (e.g. Codemagic on `gogox-driver-flutter`) runs on draft PRs, so the skill can confirm it green without un-drafting. (Verified: `gogox-driver-flutter` PR #83 was a draft yet its `PR Check` had passed.) Any check that only runs *after* un-draft is, by definition, part of the human's post-handoff sign-off.
> - **Human-decision findings stop the loop — and this skill, not the delegate, catches them.** A review finding that needs a product/AC call (or that cannot be fixed in code) is surfaced and the loop halts for the human. Because `/resolve-pr-comments --auto` runs unattended and never pauses, the triage that catches these findings happens in Step 2 *before* delegating (see Step 2.3) — the delegate handles only the mechanical fixes.

## Inputs

Invoke directly — no input required for the common case.

- **`--max-rounds=N`** (optional, default `5`) — hard cap on AI-review iterations, so a never-converging review can't loop forever.
- **`--no-review`** (optional) — skip the `@claude` AI-review loop (Step 2) and go straight to waiting for CI green, then stop at draft. For when you have already reviewed the code yourself and only want the skill to confirm the build is green.

There is no reviewer argument — the skill never assigns reviewers (see the locked decisions), so it needs none.

## Steps

### 0. Log usage

```bash
echo "{\"skill\":\"pull-request-review-loop\",\"user\":\"$(whoami)\",\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" >> ~/.gogox-claude-usage.jsonl 2>/dev/null || true
```

### 1. Resolve context

1. **gh auth.** Abort early with a clear message if `gh` is not authenticated (`gh auth status`).
2. **Branch + PR.** `gh pr view --json number,url,state,isDraft 2>/dev/null`.
   - A draft PR that is `OPEN` → use it.
   - No PR (or the last one is `MERGED`/`CLOSED`) → run `/pull-request --draft` to push the branch and open a draft PR, then re-resolve. Capture `PR_NUMBER`, `PR_URL`.
   - The PR is `OPEN` but already **ready** (a human promoted it) → use it, but do **not** re-draft it. The skill still runs the review loop and the CI wait; it simply never changes the draft/ready state in either direction.

### 2. AI-review loop (skip if `--no-review`)

Repeat up to `--max-rounds` times:

1. **Trigger.** Post the fixed trigger comment to the PR (verbatim — see Gogox Context):

   ```
   @claude please review this PR — focus on critical issues and security.
   ```
2. **Wait for the bot.** Poll the PR's comments/reviews until the `@claude` bot leaves a review dated after the trigger (bounded wait; if it never arrives, stop and report — do not silently proceed).
3. **Triage for human-decision findings (this skill's own gate — do this BEFORE delegating).** Read the bot's review yourself and decide whether any finding needs a *human* call: an acceptance-criteria mismatch, a product trade-off, or anything that cannot be settled in code. If so → **STOP**. Print those finding(s) and what the human must decide. The PR stays a draft.

   This gate lives here, not downstream: `/resolve-pr-comments --auto` (next step) runs fully unattended and never pauses to ask — it sorts every comment into FIX/REPLY/STALE/DEFER and acts on all of them. So the only place a human-decision finding can halt the loop is this triage, *before* the delegation.
4. **Delegate the mechanical fixes.** When triage finds nothing needing a human, hand the bot's findings to `/resolve-pr-comments --auto`. It reads the unresolved threads, applies the code fixes in one combined commit, replies in English, runs its build-sanity gate (`/format` + `/check-test`), and pushes. If that gate fails it stops with `needs-human: comment-fix-failed-tests` and leaves the tree dirty — surface that as a STOP too (a red build is not something this loop fixes).
5. **Decide loop vs stop:**
   - The round produced fixes and the build-sanity gate passed → push has happened; **go to round N+1** (re-trigger, so the bot reviews the fix).
   - The bot's review is **clean** (nothing left to address) → exit the loop, continue to Step 3.
   - Triage flagged a human-decision finding (step 3), or the delegated fix hit `needs-human: comment-fix-failed-tests` (step 4) → **STOP** and report. The PR stays a draft.
6. If `--max-rounds` is hit while findings remain → STOP and report (same as the human-decision halt, with the round cap noted).

### 3. Wait for CI to go green (while still a draft)

The build/test CI runs on draft PRs, so wait for it here — no un-draft needed. Poll until terminal:

```bash
gh pr checks "$PR_NUMBER" --repo "$REPO" --json name,state \
  --jq '[.[] | select(.name | test("WIP") | not) | .state]
        | if length == 0 then "pending"
          elif all(. == "SUCCESS") then "green"
          elif any(. == "FAILURE") then "red"
          else "pending" end'
```

The `WIP` check is excluded on purpose: it stays `pending` for as long as the PR is a draft (that is its whole job), so leaving it in would mean the all-green test never passes. WIP is the human's concern at un-draft time, not the skill's.

- `green` → continue to Step 4.
- `pending` → keep polling (bounded; print progress). If the build check has not even appeared yet, give it a few seconds to register before judging.
- `red` → **STOP**. Print which check failed. Fixing CI failures (test/build) is out of this skill's scope. The PR stays a draft and is not handed off as ready.

### 4. Stop at draft — hand off to the human

The PR now has a clean AI review and green CI, and it is still a draft. **Stop here.** Print a handoff checklist; every item is the human's sign-off, and the skill performs none of them:

```
PR #<n> — <url>
Left as a DRAFT on purpose. AI review: clean. CI: green.

Your turn (a human's sign-off — the skill does none of these):
  1. Review the changes.
  2. Un-draft the PR (e.g. gh pr ready <n>) — your signal that a person has checked it.
  3. Assign reviewers and let the team's notify flow announce it.
  4. Merge once approved.
```

The skill never un-drafts, assigns reviewers, posts to Slack, or merges. No state is persisted; every marker lives in GitHub.

## Gogox Context

- **Trigger comment (verbatim):** `@claude please review this PR — focus on critical issues and security.` Summons the Claude GitHub App. This is a machine instruction to a bot — it stays English and fixed, never localized or templated per-user.
- **Why stop at draft.** The team reads a PR's draft/ready state as "AI-written, not yet human-verified" (draft) vs "a person has checked it and it is ready" (ready). Promoting draft→ready is therefore a human sign-off; automating it would let the AI vouch for its own output. The skill stops at a clean, green draft and hands off.
- **CI runs on drafts here.** On `gogox-driver-flutter` the real build/test check, `PR Check (format, analyze, test, build-verify)`, is run by Codemagic (an external CI) and fires on draft PRs — so the skill can confirm it green without un-drafting. The `WIP` marketplace check, by contrast, stays `pending` until the PR is un-drafted; Step 3 excludes it for that reason.
- **No reviewer assignment, no Slack.** Both are part of the human's post-handoff sign-off. The skill assigns no one and posts nothing; whatever notify flow the repo has (a GitHub Action, or a person posting to the channel) fires from the human's assignment, not from the skill.

## Output

A short summary:

```
PR #<n> — <url>
AI review: <clean in N rounds | stopped: needs human (…) | skipped (--no-review)>
CI: <green | red (<check>) | timed out>
Status: DRAFT (left on purpose)
Next (human): review → un-draft → assign reviewers → merge
```

## How this was used last

> Update this footer when you use the skill, so the next person knows the real-world use case.
> Format: `YYYY-MM-DD by @username — one-line context`

- 2026-06-25 by @broccoli.huang — reworked to STOP at draft: the skill runs the AI-review loop and waits for CI green, then leaves the PR a draft. Auto un-draft / assign / notify were removed after team feedback that draft↔ready is the human-verified marker, and after confirming the build CI (Codemagic) runs on drafts. Shipped as GGC-88 / PR #137. Not yet dogfooded end-to-end on a live PR.
