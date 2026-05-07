---
name: port:ship
description: >
  Stage 6 of the port pipeline. Acquires `.port/.lock`, commits via /commit,
  pushes the feature branch, re-fetches the Linear ticket, replaces or
  appends the PRD region between port markers, posts a structured summary
  comment with retry, and (in --auto) flips the ticket label to
  `need-spec-review`. Releases the lock only on full success.
---

# /port:ship — Commit, Push, Linear Write-back

Ship the reviewed OpenSpec change. After this stage the branch is on origin, Linear has the updated description + summary comment, and `/dev:ff` is the next step for the implementer (it will detect the spec as state B and route directly to `/dev:apply`).

**Usage**: `/port:ship [--ticket:<ID>] [--auto]`

- `--ticket:<ID>` — Override auto-detection. Otherwise resolved per §4.4.
- `--auto` — Unattended mode. Skip every `AskUserQuestion`; resolve gates per the auto-decision table. Includes auto-fix audit + auto-accepted assumptions in the Linear summary, and adds the `need-spec-review` label.

---

## Steps

1. **Resolve ticket-id (§4.4).**
   - If `--ticket:<ID>` provided → use it.
   - Else basename of `git rev-parse --show-toplevel` regex `[A-Z]+-[0-9]+`.
   - Else `git branch --show-current` regex `[A-Z]+-[0-9]+`.
   - Else HITL → `AskUserQuestion`. `--auto` → STOP with: `cannot resolve ticket-id; pass --ticket:<ID>`.

2. **Locate worktree + change dir.**
   - Confirm cwd is the ticket worktree. Find single `openspec/changes/<change-name>/.port/`. Hold `<change-name>`, `<port-dir>`, `<worktree-path>`.
   - Verify branch: `<expected-branch>` = `feat/<ticket-id>`. If `git branch --show-current` differs:
     - HITL → `AskUserQuestion`: `checkout <expected-branch> / abort`.
     - `--auto` → `git checkout -B "<expected-branch>"` and proceed (worktree was created with that name; mismatch implies a manual rename).

3. **Pre-flight: review approved.**
   - `<port-dir>/synth-report.md` MUST exist and contain the literal sentinel `Review approved` (written by `/port:revise` step 10):
     ```bash
     grep -q '^Review approved' "<port-dir>/synth-report.md"
     ```
   - Missing or sentinel absent:
     - HITL → `AskUserQuestion`: `read report and confirm / abort`. On `confirm`, append the sentinel block in-place (atomic) so subsequent re-runs don't re-prompt. On `abort`, STOP.
     - `--auto` → STOP with: `synth-report.md missing or not approved — run /port:revise first`. Do NOT auto-approve here; that decision belongs to revise.

4. **Concurrency lock (risks §10).**
   - Path: `<port-dir>/.lock`.
   - If file exists:
     - Read PID + ISO timestamp from it. Compute mtime age via `stat`.
     - mtime ≥ 2h → assume stale. HITL → `AskUserQuestion`: `take-over / abort`. `--auto` → take-over.
     - mtime < 2h → another `/port:ship` is running. HITL → `AskUserQuestion`: `take-over (other run will fail) / abort`. `--auto` → take-over (auto runs are dispatched serially per ticket; concurrent auto-runs imply a dispatcher bug worth surfacing in the next stage's logs but not blocking).
   - Atomic-write the lock:
     ```bash
     tmp=$(mktemp)
     printf '%s\n%s\n' "$$" "<ISO-now>" > "$tmp"
     mv "$tmp" "<port-dir>/.lock"
     ```
   - The lock is removed only on full success (step 14). Every STOP path between here and step 14 leaves it in place so the user can inspect.

5. **Sanitize the index of `.port/` runtime artifacts.**
   - `.port/` is a mixed directory: `{dev,pm,design}-notes.md`, `prd.md`, `synth-report.md`, and `context.md` are consultative artifacts that DO get committed. `.lock`, `timings.jsonl`, and `ship-pending.md` are pure runtime — they must NOT be tracked.
   - Step 14 below writes `.port/timings.jsonl` AFTER `/commit` returns. Without gitignoring + evicting, that write either leaks into a future commit or leaves the working tree dirty for the next pipeline run (the `/dev:start` and `/check-clean` failure mode users have hit). The chain-end deletion in `/port:ff` step 11 is the success-path cleanup; this step is the per-commit defense.
   - Run before `/commit` so the gitignore change + eviction land in the same commit:
     ```bash
     # Add the runtime entries if not already present. Patterns work at any depth
     # (gitignore patterns without a leading slash match anywhere in the tree).
     for entry in '.port/.lock' '.port/timings.jsonl' '.port/ship-pending.md'; do
       if ! grep -qxF "$entry" .gitignore 2>/dev/null; then
         printf '%s\n' "$entry" >> .gitignore
       fi
     done

     # Evict any already-tracked instances (one-time cleanup; safe no-op after).
     git ls-files \
       | grep -E '(^|/)\.port/(\.lock|timings\.jsonl|ship-pending\.md)$' \
       | xargs -r git rm --cached -- 2>/dev/null || true
     ```
   - Skipping this step lets `.port/timings.jsonl` and friends leak into every commit forever; the previous commit's `.gitignore` does not retroactively untrack files.

6. **Commit via `/commit`.**
   - Invoke the existing `/commit` skill. It analyses the working tree and writes one or more atomic commits.
   - Format-hook failure inside `/commit` → STOP. Surface the error verbatim. Do NOT amend, do NOT retry blindly. The user fixes the hook, re-runs `/port:ship`. Lock stays in place. Append timings with `outcome:"aborted-commit-failed"`.
   - After `/commit` returns, verify the working tree is clean: `git status --porcelain` must be empty. Non-empty → STOP with `working tree dirty after /commit; investigate`. Lock stays in place.

7. **Push.**
   - `git push -u origin "feat/<ticket-id>"`.
   - Push failure (auth, network, non-fast-forward, hook reject):
     - Keep `<port-dir>/.lock`.
     - Post a Linear comment via `mcp__claude_ai_Linear__save_comment` (with retry — see step 10 retry policy):
       ```
       Port aborted: push failed (auth/network/rejected). Worktree preserved at <worktree-path>.
       Branch is local-only; re-run /port:ship after fixing the push issue.
       ```
     - Append timings line: `{"stage":"ship","outcome":"aborted-push-failed",...}`.
     - STOP. Do NOT proceed to Linear description write — branch is local-only and writing the PRD now would advertise a non-existent spec tree URL.

8. **Build spec tree URL.**
   - `<repo-url-raw>` = `git config --get remote.origin.url`.
   - Convert SSH form `git@github.com:<org>/<repo>.git` → `https://github.com/<org>/<repo>`. HTTPS form: strip a trailing `.git`.
   - `<tree-url>` = `<repo-url>/tree/feat/<ticket-id>/openspec/changes/<change-name>`.

9. **Re-fetch Linear ticket description.**
   - `mcp__claude_ai_Linear__get_issue` with `id: <ticket-id>`. Capture `<current-description>`.
   - This is mandatory — never use a cached `<ticket-context>` from `/port:start`. PMs may have edited in the interim and those edits live OUTSIDE the port markers, which we must preserve.

10. **Build new description.**
   - Read `<port-dir>/prd.md`. If missing or whitespace-only, **skip this entire step** (no description write). Jump to step 11.
   - Build the marker block:
     ```
     <!-- port:prd:start -->
     > ℹ️ Auto-generated by `/port` on <YYYY-MM-DD>. PM may freely edit **outside** these markers; re-running `/port` replaces only the content between them.

     <contents of .port/prd.md>
     <!-- port:prd:end -->
     ```
   - Compute `<new-description>`:
     - If `<current-description>` contains `<!-- port:prd:start -->` → replace the inclusive region from `<!-- port:prd:start -->` to `<!-- port:prd:end -->` with the new block. Everything outside the markers is left byte-for-byte untouched.
     - Else → append the block at the end of `<current-description>`, separated by one blank line.
   - **Linear write-back with retry (risks §10).**
     - Call `mcp__claude_ai_Linear__save_issue` with `id: <ticket-id>` and `description: <new-description>`.
     - On failure: retry up to 3× with exponential backoff (1 s, 4 s, 16 s).
     - Final failure → atomic-write `<port-dir>/ship-pending.md`:
       ```markdown
       # Linear sync incomplete

       The branch is pushed but the Linear description write failed after 3 retries.

       ## Manual fix
       Open <ticket-id> in Linear and paste this between the port:prd markers
       (replacing any existing block):

       <!-- payload start -->
       <new-description>
       <!-- payload end -->

       Then re-run /port:ship to resume from the comment step.
       ```
       STOP with: `manual fix needed: <ticket-id> Linear sync incomplete (see <port-dir>/ship-pending.md)`. Append timings line `outcome:"aborted-linear-description-failed"`. Lock stays in place.

11. **Build summary comment body.**
    - Pull pieces from artifacts:
      - **Capabilities** — from `.port/pm-notes.md` "Proposed Capabilities" section: one bullet per capability (`` `<cap>` — <one-liner> ``).
      - **Validation** — read the `openspec validate` line preserved by `/port:synth` in `.port/synth-report.md` (or, if absent, run `openspec validate <change-name> --type change` and capture the summary).
      - **Files** — links to `proposal.md`, `design.md`, `tasks.md`, every `specs/<cap>/spec.md` (one per capability dir).
      - **Consult notes** — links to `.port/dev-notes.md`, `.port/pm-notes.md`, `.port/design-notes.md`, `.port/context.md`.
      - **PRD** — verbatim from `<port-dir>/prd.md` if present, else the literal string `(inferred from ticket; no user PRD)`.
    - Body shape:
      ```markdown
      ## Port summary: <change-name>

      **Branch**: `feat/<ticket-id>` (pushed)
      **Spec tree**: <tree-url>

      ### PRD
      <prd contents or "(inferred from ticket; no user PRD)">

      ### Capabilities
      - `<cap>` — <one-liner>

      ### Validation
      <openspec validate summary>
      ```
    - **`--auto` mode additions** (read from `claude-reports/<session>/`):
      ```markdown
      ### Auto-fixes applied
      <verbatim contents of claude-reports/<session>/auto-fixes.md, or "(none)">

      ### Auto-accepted assumptions (REVIEW REQUIRED)
      <verbatim contents of claude-reports/<session>/auto-accepted.md, or "(none)">
      ```
    - **HITL mode addition**:
      ```markdown
      ### Assumptions (post-clarification)
      <only the assumptions that survived the /port:revise loop, aggregated from .port/*-notes.md "## Assumptions" sections — items resolved during clarification are excluded>
      ```
    - Always append the trailing blocks:
      ```markdown
      ### Files
      - [proposal.md](<tree-url>/proposal.md)
      - [design.md](<tree-url>/design.md)
      - [tasks.md](<tree-url>/tasks.md)
      - [specs/<cap>/spec.md](<tree-url>/specs/<cap>/spec.md)   (× N)

      ### Consult notes
      - [.port/dev-notes.md](<tree-url>/.port/dev-notes.md)
      - [.port/pm-notes.md](<tree-url>/.port/pm-notes.md)
      - [.port/design-notes.md](<tree-url>/.port/design-notes.md)
      - [.port/context.md](<tree-url>/.port/context.md)

      ### Next step
      `cd <worktree-path> && /dev:ff`
      ```

12. **Post the summary comment with retry.**
    - `mcp__claude_ai_Linear__save_comment` with `issueId: <ticket-id>` and `body: <summary>`.
    - Retry policy: same exponential backoff as step 10 (1 s, 4 s, 16 s).
    - Final failure → atomic-write `<port-dir>/ship-pending.md` (or append a `## Comment payload` section if it already exists from step 10). STOP with `manual fix needed: <ticket-id> Linear comment failed (see <port-dir>/ship-pending.md)`. Append timings `outcome:"aborted-linear-comment-failed"`. Lock stays in place.

13. **Label transition (auto only).**
    - In `--auto` only: `mcp__claude_ai_Linear__save_issue` with `id: <ticket-id>` and an updated label set that adds `need-spec-review` (preserve all existing labels). Same 3× backoff retry policy. Final failure → log a soft warning to stdout but do NOT abort — the label is auxiliary; description + comment are the contract.
    - HITL: skip. The reviewer applies the label themselves after eyeballing the comment.

14. **Release lock + emit timings.**
    - Remove `<port-dir>/.lock` (only on full success).
    - Append one line to `<port-dir>/timings.jsonl`:
      ```json
      {"stage":"ship","ticket":"<ticket-id>","start":"<ISO-start>","end":"<ISO-end>","duration_ms":<int>,"outcome":"ok"}
      ```

15. **Print final block.**
    ```
    OpenSpec change '<change-name>' shipped.

    Spec URL    : <tree-url>
    Linear      : description updated (PRD region) + summary comment posted<auto: + label need-spec-review>
    Worktree    : <worktree-path> (disposable — spec is on origin)

    Next: cd <worktree-path> && /dev:ff
    ```

---

## Atomic write pattern (D16)

Every write to a `.port/<file>.md` (including `.lock`, `ship-pending.md`, and timings rewrites) goes through `mktemp` + `mv`:

```bash
tmp=$(mktemp)
# generate full content into $tmp
mv "$tmp" "<port-dir>/<file>"
```

POSIX `mv` on the same filesystem is atomic — partial files are never visible to a concurrent / restart-after-crash runner. The `.port/timings.jsonl` log uses append (`>>`) — single-line writes are atomic at the syscall level and stage-level corruption on telemetry is tolerable.

---

## Linear MCP retry policy (risks §10)

`mcp__claude_ai_Linear__save_issue` and `mcp__claude_ai_Linear__save_comment` are retried up to 3× with backoff `1 s → 4 s → 16 s`. On final failure the orchestrator atomic-writes the unsent payload to `<port-dir>/ship-pending.md` so a follow-up run can resume manually. The branch is already on origin at this point; that is recoverable. Re-pushing is not — never roll back the push.

---

## Guardrails

- **Push always required.** No local-only fallback. If push fails, the Linear description and comment writes are skipped and the lock + worktree are preserved.
- **Linear description is APPEND-ONLY between markers.** PM content outside `<!-- port:prd:start --> ... <!-- port:prd:end -->` is byte-for-byte preserved on every re-run. Re-fetch description before write — never use the cached `<ticket-context>` from `/port:start`.
- **Skip description write entirely if `.port/prd.md` is empty.** No empty marker block, no placeholder text. The description is left exactly as the PM wrote it.
- **Lock acquired at start, released only on full success.** Every STOP path between step 4 and step 14 leaves `.port/.lock` so the operator can inspect; the next run prompts `take-over / abort` (HITL) or auto-takes-over (`--auto`).
- **Format-hook failure stops the world.** `/commit` failures are surfaced verbatim; the orchestrator never amends or `--no-verify`s past them.
- **`Review approved` sentinel** is the only signal accepted from `/port:revise`. Without it, ship STOPs (HITL prompts to confirm + retro-write the sentinel; `--auto` stops outright).
- **Auto-fix + auto-accepted reports** are read from `claude-reports/<session>/auto-fixes.md` and `auto-accepted.md`. Both are written by `/port:revise --auto`. Missing files → render as `(none)` rather than crashing.
- **`need-spec-review` label is auto-mode-only.** HITL leaves the label transition to the human reviewer.
- **Linear MCP failures retry up to 3×.** Final failure writes `ship-pending.md` and STOPs without removing the lock — the next run resumes from the failed step.
- All Linear MCP calls use `mcp__claude_ai_Linear__*`. Never the legacy `mcp__linear-server__*`.
- Timings JSONL is appended even on early STOP paths so observability isn't lost (set `outcome` to `aborted:<reason>`).
- This stage never spawns sub-agents — pure tool-call orchestration. Sub-agent dispatch overhead exceeds the model-cost saving for ~5 tool calls.
