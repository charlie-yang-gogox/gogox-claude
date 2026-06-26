---
name: pull-request-review-loop
description: >
  Drive the current branch's PR to a reviewer-ready state. By default it STOPS
  at draft: opens a draft PR if the branch has none, runs the @claude AI-review
  loop until clean (auto-fixing mechanical findings, pausing for human-decision
  ones), and waits for CI to go green while the PR stays a draft — then prints a
  handoff checklist. Promoting a draft to ready is a human's sign-off, so it is
  never automatic — but the human can opt in with --assign=<logins> to un-draft
  and assign those reviewers once the review is clean and CI is green, and add
  --notify=<channel> to post a PR-review request to that Slack channel after
  assigning. The skill never merges. Use after finishing a feature/fix to
  automate the PR handoff.
---

# Pull Request Review Loop

> **One-line summary**: For the current branch's PR (created as a draft if none exists), post the `@claude` review trigger and loop fix→re-trigger until the AI review is clean, then wait for CI to go green — all while the PR stays a **draft**. By default the skill stops there and prints a handoff checklist. Promoting is opt-in: `--assign=<logins>` un-drafts and assigns once clean, and `--notify=<channel>` then posts a PR-review request to that Slack channel. The skill never merges.
>
> **MCP prerequisites**: none for the default flow (`gh` CLI only, authenticated via `gh auth status`). `--notify=<channel>` additionally needs a Slack MCP connection (it calls `slack_send_message`); without one, the notify step fails loudly while the rest still completes.
>
> **Locked design decisions** (do not re-litigate):
> - **No PR id argument.** The PR is always resolved from the current branch; if there is none, the skill creates one via `/pull-request --draft`. The PR number is never known in advance.
> - **English only — interface and artifacts.** Every progress line, commit, and review reply is English. The one exception is the `--notify` Slack message, which is Chinese by team convention (see Step 4 / CLAUDE.md §8) — but this file itself stays English.
> - **Default = stop at draft; `--assign` is the human's explicit opt-in to promote.** The team uses a PR's draft/ready state as the marker for "AI-written, not yet verified" (draft) vs "a human has checked it" (ready), so the skill never *auto*-promotes. By default it takes the PR only as far as "AI review clean + CI green" and stops, leaving it a draft. When the human runs it with `--assign=<logins>`, that invocation *is* the human sign-off — so the skill then un-drafts and assigns those reviewers. (Caveat: `--assign` is pre-authorization given at invocation, before the final diff exists — a deliberate, opt-in trade-off.) Merging is always the human's.
> - **Slack is opt-in via `--notify`, never on the skill's own initiative.** By default the skill posts nothing to Slack. It posts one PR-review request only when the human passes `--notify=<channel>` alongside `--assign`, and only to the channel the human named — the same "the invocation is the authorization" logic as `--assign`.
> - **CI is waited for *while still a draft*.** The build/test CI (e.g. Codemagic on `gogox-driver-flutter`) runs on draft PRs, so the skill can confirm it green without un-drafting. (Verified: `gogox-driver-flutter` PR #83 was a draft yet its `PR Check` had passed.)
> - **Human-decision findings stop the loop — and this skill, not the delegate, catches them.** A review finding that needs a product/AC call (or that cannot be fixed in code) is surfaced and the loop halts for the human. Because `/resolve-pr-comments --auto` runs unattended and never pauses, the triage that catches these findings happens in Step 2 *before* delegating (see Step 2.3). A halt here means no promotion and no notification, even under `--assign` / `--notify`.

## Inputs

Invoke directly — no input required for the common (stop-at-draft) case.

- **`--max-rounds=N`** (optional, default `5`) — hard cap on AI-review iterations, so a never-converging review can't loop forever.
- **`--assign=<logins>`** (optional) — comma-separated GitHub logins (e.g. `--assign=AlexWangGoGoX,AlanCHTseng`). The human's explicit opt-in to promote: **only after** the review loop is clean and CI is green, the skill un-drafts the PR and requests review from these logins. A leading `@` and surrounding whitespace are tolerated. Without this flag the skill stops at draft and promotes nothing.
- **`--notify=<channel>`** (optional, requires `--assign`) — a Slack channel ID (e.g. `C0APU1TJ98Q`) or `#name`. After the un-draft + assign succeeds, post a PR-review request to this channel so the team is pinged to review. Without it, the skill assigns but posts nothing. Passing it *without* `--assign` is a no-op — notification follows assignment, so the skill warns and skips it.

## Steps

### 0. Log usage

```bash
echo "{\"skill\":\"pull-request-review-loop\",\"user\":\"$(whoami)\",\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" >> ~/.gogox-claude-usage.jsonl 2>/dev/null || true
```

### 1. Resolve context

1. **gh auth.** Abort early with a clear message if `gh` is not authenticated (`gh auth status`).
2. **Branch + PR.** `gh pr view --json number,url,state,isDraft 2>/dev/null`.
   - A draft PR that is `OPEN` → use it.
   - No PR (or the last one is `MERGED`/`CLOSED`) → run `/pull-request --draft` to push the branch and open a draft PR, then re-resolve. Capture `PR_NUMBER`, `PR_URL`, `REPO`.
   - The PR is `OPEN` but already **ready** (a human promoted it) → use it, do **not** re-draft it. Run the review loop and CI wait as normal; under `--assign` it skips the un-draft step (already ready) and goes straight to assigning.
3. **Assignees + notify.** If `--assign` is given, parse it into a deduped list of bare GitHub logins (strip a leading `@` and whitespace). If `--notify` is given without `--assign`, warn and ignore it (notification follows assignment). Hold both for Step 4.

### 2. AI-review loop

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

### 4. Hand off

The PR now has a clean AI review and green CI.

**Default (no `--assign`)** — stop at draft. Print the handoff checklist; every item is the human's sign-off, and the skill performs none of them:

```
PR #<n> — <url>
Left as a DRAFT on purpose. AI review: clean. CI: green.

Your turn (a human's sign-off — the skill does none of these):
  1. Review the changes.
  2. Un-draft the PR (e.g. gh pr ready <n>) — your signal that a person has checked it.
  3. Assign reviewers and (optionally) ping the channel.
  4. Merge once approved.
```

**With `--assign=<logins>`** — the invocation is the human's pre-authorization to promote, so carry out the un-draft + assign on their behalf:

1. **Un-draft** (skip if the PR is already ready): `gh pr ready "$PR_NUMBER"`.
2. **WIP nudge.** Un-drafting leaves this repo's Marketplace WIP check stale at `pending` (it doesn't listen for `ready_for_review`). If `gh pr checks "$PR_NUMBER"` shows WIP `pending`, nudge the `edited` event it does listen for via a REST title append-and-revert:

   ```bash
   gh api -X PATCH "repos/$REPO/pulls/$PR_NUMBER" -f title="$ORIG_TITLE "   # trailing space
   sleep 3
   gh api -X PATCH "repos/$REPO/pulls/$PR_NUMBER" -f title="$ORIG_TITLE"    # revert
   ```
3. **Assign reviewers** via REST — NOT `gh pr edit --add-reviewer` (its GraphQL path fails when the token lacks `read:org`):

   ```bash
   # one -f per login parsed from --assign
   gh api "repos/$REPO/pulls/$PR_NUMBER/requested_reviewers" \
     -f 'reviewers[]=AlexWangGoGoX' -f 'reviewers[]=AlanCHTseng'
   ```
4. **Notify (only if `--notify=<channel>` is given).** Post a PR-review request to that Slack channel (the id or `#name` passed in) via `slack_send_message`. Write the message in **Chinese** — this is the team's internal channel and the documented exception to English-only (CLAUDE.md §8). Structure it like the channel's existing convention: a short greeting with a thank-you emoji (e.g. `:gogobear_thankful:`), the PR URL on its own line, then one `•` bullet describing in Chinese what the PR does (ticket id welcome). Do **not** @-mention anyone — reviewers were assigned on GitHub in step 3, and the channel claims PRs by emoji reaction. Post **once** per PR (on a re-run, do not repost). If `--notify` is absent, post nothing — assignment alone is the handoff.
5. **Merge** is still the human's — the skill never merges.

**Neither `--assign` nor `--notify` overrides a STOP.** They only reach this step when the review loop AND CI are both clean. If the loop stopped on a human-decision finding, or CI is red, the skill never un-drafts, assigns, or notifies — the PR stays a draft.

## Gogox Context

- **Trigger comment (verbatim):** `@claude please review this PR — focus on critical issues and security.` Summons the Claude GitHub App. This is a machine instruction to a bot — it stays English and fixed, never localized or templated per-user.
- **Why stop at draft by default.** The team reads a PR's draft/ready state as "AI-written, not yet human-verified" (draft) vs "a person has checked it and it is ready" (ready). Promoting draft→ready is therefore a human sign-off; the skill automates it only when the human explicitly opts in via `--assign`.
- **CI runs on drafts here.** On `gogox-driver-flutter` the real build/test check, `PR Check (format, analyze, test, build-verify)`, is run by Codemagic (an external CI) and fires on draft PRs — so the skill can confirm it green without un-drafting. The `WIP` marketplace check, by contrast, stays `pending` until the PR is un-drafted; Step 3 excludes it for that reason.
- **Assign via REST, not `gh pr edit`.** `gh pr edit --add-reviewer` uses GraphQL and fails when the local token lacks the `read:org` scope; the `requested_reviewers` REST endpoint works regardless.
- **WIP nudge after un-draft.** The Marketplace WIP check leaves a stale `pending` after un-draft because it does not listen for `ready_for_review`; the title append-and-revert in Step 4 triggers the `edited` event it does listen for.
- **Slack notification (`--notify`) follows §8 convention.** The message is Chinese (the target channels are the team's internal Chinese channels — the documented §8 exception to English-only), with a greeting + thank-you emoji, the PR URL, and one `•` Chinese bullet, no @-mention, once per PR. The channel is always whatever the human passes to `--notify`; the skill never picks one on its own, and posts nothing without the flag.

## Output

A short summary:

```
PR #<n> — <url>
AI review: <clean in N rounds | stopped: needs human (…)>
CI: <green | red (<check>) | timed out>
Status: <DRAFT (left on purpose) | READY — un-drafted, assigned: a, b>
Notify: <posted to <channel> | not requested | skipped (no --assign)>
Next: <human: review → un-draft → assign → merge | merge once approved>
```

## How this was used last

> Update this footer when you use the skill, so the next person knows the real-world use case.
> Format: `YYYY-MM-DD by @username — one-line context`

- 2026-06-25 by @broccoli.huang — reworked to stop at draft by default (runs the AI-review loop, waits for CI green, then leaves the PR a draft), after team feedback that draft↔ready is the team's human-verified marker. Added `--assign=<logins>` (opt-in un-draft + assign) and `--notify=<channel>` (opt-in Slack PR-review ping after assigning — added because the repo's assumed notify Action turned out not to exist, so the notification had to be the skill's own step). CI is waited for in draft because the build CI (Codemagic) runs on drafts. Shipped as GGC-88 / PR #137. Not yet dogfooded end-to-end on a live PR.
