---
name: resolve-conflict
description: >
  Merge/Rebase current branch onto trunk, resolve any merge conflicts,
  run tests until green, format, and commit. Does NOT push. Platform-aware:
  uses the right test and format commands per project.
---

# Resolve Conflict — Rebase onto Trunk & Fix Conflicts

Pull latest trunk via merge/rebase, resolve conflicts, verify tests pass, format, and commit.

---

## Step 0: Resolve project profile

Before any other step, determine the active project profile so later steps know which test and format commands to run.

**Resolution order:**

1. **Repo self-describes** — read `<repo-root>/.gogox-claude.yaml` if present. Use its `platform` field.
2. **Central mapping** — else, read `~/.claude/commands/profiles/registry/$(basename "$(git rev-parse --show-toplevel)").yaml` for `platform` and `product`.
3. **Error** — if neither resolves, stop and tell the user:
   > Cannot resolve gogox project profile. Either add `~/.claude/commands/profiles/registry/<basename>.yaml`, or create `<repo>/.gogox-claude.yaml` with `platform:` and `product:`.

After resolution, read:

- `~/.claude/commands/profiles/platform/<platform>.yaml` — exposes `test_cmd`, `format_cmd`.

Hold these values in memory for use in Steps 4 and 5 where you see `{test_cmd}` and `{format_cmd}`.

## Steps

### 1. Pre-check

If `--merge` is provided, use `merge` instead of `rebase` in all git commands in this skill.

If `--rebase` is provided, use `rebase` in all git commands in this skill.

Invoke `/check-clean`. If it fails, stop and ask the user to commit or stash before proceeding.

### 2. Fetch & Merge/Rebase onto trunk

#### merge commands
```bash
git fetch origin trunk
git merge origin/trunk
```

#### rebase commands
```bash
git fetch origin trunk
git rebase origin/trunk
```

If the merge/rebase succeeds with no conflicts, skip to Step 4.

### 3. Resolve Conflicts

While the merge/rebase is paused due to conflicts:

1. Run `git diff --name-only --diff-filter=U` to list all conflicted files.
2. For **each** conflicted file:
   a. **Read** the file to understand the conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`).
   b. **Read** the surrounding code context and the intent of both sides (ours = current branch, theirs = trunk).
   c. Resolve the conflict by choosing the correct merge of both sides. Prefer preserving both sides' intent; if in doubt, ask the user via AskUserQuestion.
   d. After editing, stage the resolved file: `git add <file>`.
3. Continue the merge/rebase:
   ```bash
   git merge --continue
   ```
   ```bash
   git rebase --continue
   ```
4. If further conflicts arise, repeat from step 3.1.

**Rules for conflict resolution:**
- Never blindly accept "ours" or "theirs" — always read and understand both sides.
- If a conflict involves non-trivial logic changes from both sides, **ask the user** before resolving.
- Ensure no conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`) remain in any file after resolution.

### 4. Verify — Run Tests until Green

Run `{test_cmd}` to verify the test suite passes after the merge/rebase. Fix any rebase-related test failures before continuing.

Note: on platforms where `{test_cmd}` is itself a slash command (e.g. Flutter's `/check-test --all --fix`), invoke it as a slash command. On platforms where it's a raw shell command (e.g. Android's `./gradlew testDebugUnitTest`), run it via Bash.

### 5. Format

Run `{format_cmd}` to apply formatter and lint fixes — without committing.

Same note as Step 4: invoke as slash command when it starts with `/`, otherwise run via Bash.

### 6. Commit

Invoke `/commit` to create atomic, well-scoped commits for all changes (including formatting fixes).

If `/commit` is not available in this project, fall back to creating a single conventional-commit message that summarizes the merge/rebase and any fix-ups.

### 7. Conflict Summary Report

After all conflicts are resolved and commits are done, produce a summary table of every conflict that was resolved during the merge/rebase. Track this information as you work through Step 3.

Format:

```
## Conflict Resolution Summary

| # | File | Conflict Area | Ours (branch) | Theirs (trunk) | Resolution |
|---|------|--------------|----------------|----------------|------------|
| 1 | lib/features/auth/login.dart | import block | added `package:foo` | added `package:bar` | kept both imports |
| 2 | lib/core/api/client.dart | `fetchData()` method | changed return type to `Future<Result>` | added retry logic | merged both: kept new return type + retry logic |
| ... | ... | ... | ... | ... | ... |
```

- **File**: the conflicted file path
- **Conflict Area**: which section/function/block had the conflict
- **Ours (branch)**: what the current branch changed
- **Theirs (trunk)**: what trunk changed
- **Resolution**: how you resolved it (kept both, chose ours, chose theirs, merged manually, etc.)

If no conflicts occurred, report: "No conflicts — merge/rebase was clean."

### 8. Done — Do NOT Push

Report the result:
- Show the Conflict Summary from Step 7.
- Show `git log --oneline -10` so the user can review.
- Remind the user that changes have **not** been pushed.
- If the user wants to push, they should do so manually.

---

## Rules

- **Never force-push** or run `git push` automatically.
- **Never run `git rebase --abort`, `git merge --abort` nor `git cherry-pick --abort`** without asking the user first.
- If the merge/rebase hits more than 5 conflict rounds, pause and ask the user if they want to continue or abort.
- Do not modify files unrelated to the conflict resolution or test fixes.
- All conflict markers must be verified removed before continuing the merge/rebase.
