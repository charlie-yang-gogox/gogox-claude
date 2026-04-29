# Check Clean — Verify Git Working Tree is Clean

Verify there are no uncommitted changes in the working tree.

---

## Steps

1. Run `git status --porcelain`
2. If output is **empty**: report `✅ Working tree is clean` and succeed.
3. If output is **not empty**: report the changed files and **fail** with:
   ```
   ❌ Uncommitted changes detected. Please commit or stash before proceeding.
   ```

## Rules

- This is a read-only check — never stage, commit, or stash automatically.
- Return the list of changed files so the caller knows what's dirty.
