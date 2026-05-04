# Check Archive — Verify OpenSpec Changes are Archived

Check that all OpenSpec change directories under `openspec/changes/` have been archived.

**Arguments:** `--skip-openspec` to skip the check (only prints a warning instead of failing).

---

## Steps

1. List directories under `openspec/changes/` excluding `archive/`:
   ```bash
   find openspec/changes -mindepth 1 -maxdepth 1 -type d ! -name archive 2>/dev/null
   ```

2. If **no unarchived directories found**: report `✅ Archive check passed` and succeed.

3. If **unarchived directories found**:
   - If `--skip-openspec` argument is provided:
     ```
     ⚠️  Archive check skipped. The following specs are not archived:
     - openspec/changes/<name>/
     ```
     Succeed with warning.
   - If `--skip-openspec` is NOT provided:
     ```
     ❌ Archive check failed. The following specs are not archived:
     - openspec/changes/<name>/

     Archive them with /opsx:archive or pass --skip-openspec to bypass.
     ```
     Fail.

## Rules

- This is a read-only check — never move or archive directories automatically.
- Always list the unarchived directory names so the caller knows what's pending.
