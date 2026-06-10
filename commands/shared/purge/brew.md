---
name: purge:brew
description: >
  Reclaim Homebrew disk: clear the download cache and outdated formula/cask
  versions (brew cleanup), and optionally remove unused dependencies
  (brew autoremove). Interactive by default with a dry-run preview; cleanup is
  always safe (only removes regenerable downloads + superseded versions),
  autoremove is opt-in and shown as an explicit list before removal.
---

# Purge:brew — Clean Homebrew Caches & Old Versions

Homebrew keeps every downloaded bottle and old version of each formula/cask
until told otherwise. `~/Library/Caches/Homebrew` and superseded versions in
the Cellar/Caskroom add up to gigabytes over time.

**Interactive by default**: measure → dry-run preview → confirm → clean.

**Arguments** (all optional):
- `--yes` — skip prompts; run `brew cleanup` (safe). Does **not** imply
  autoremove or scrub-latest — those stay opt-in.
- `--scrub` — also remove the latest cached downloads (`brew cleanup -s`), not
  just stale ones.
- `--autoremove` — also `brew autoremove` (remove unused dependencies). Always
  shows the list and confirms first unless combined with `--yes`.

---

## Step 0: Preconditions

- `brew` must be on PATH (`command -v brew`), else report
  `Homebrew not installed.` and stop.

## Step 1: Measure (dry-run, no changes)

```bash
brew cleanup --dry-run            # lists what would be removed + total reclaimable
du -sh "$(brew --cache)" 2>/dev/null   # ~/Library/Caches/Homebrew download cache
brew autoremove --dry-run         # unused dependencies that COULD be removed (only if --autoremove or interactive)
```

Capture: the cleanup reclaimable total, the cache size, and the autoremove
candidate list (formula names).

## Step 2: Preview

```
Homebrew cleanup preview

  Download cache (~/Library/Caches/Homebrew):   702 MB
  Stale versions + cache reclaimable:           ~1.3 GB   (brew cleanup)
  With --scrub (also latest downloads):         ~1.9 GB   (brew cleanup -s)

  Unused dependencies (brew autoremove) — OPT-IN:
    formula-a, formula-b, formula-c   (3 packages)
```

If `brew cleanup --dry-run` reports nothing and the cache is empty, print
`Nothing to clean.` and stop.

## Step 3: Choose

If `--yes`, run `brew cleanup` (+ `-s` if `--scrub`, + autoremove if
`--autoremove`) and skip to Step 4. Otherwise **AskUserQuestion**
(`multiSelect`), only including rows that have something to do:

```yaml
question: "What should I clean from Homebrew?"
header: "Homebrew"
multiSelect: true
options:
  - label: "Cleanup: stale versions + cache (1.3 GB) — recommended"
    description: "brew cleanup — removes superseded versions and old downloads. Always safe; nothing installed is affected."
  - label: "Also scrub latest downloads (+0.6 GB)"
    description: "brew cleanup -s — also drops the most-recent cached bottles. Re-downloaded on next install/upgrade."
  - label: "Autoremove unused dependencies (3 packages)"
    description: "brew autoremove — uninstalls formulae kept only as now-unneeded deps. Shows the exact list; never touches packages you installed directly (brew leaves)."
```

## Step 4: Execute

Run the selected actions, capturing Homebrew's own freed-space output:
- Cleanup → `brew cleanup` (or `brew cleanup -s` if scrub selected).
- Autoremove → `brew autoremove` (only the selected/confirmed packages).

On error, report it and continue with the remaining selected actions.

## Step 5: Report

```
Purge:brew complete.

  brew cleanup:    freed ~1.3 GB
  brew autoremove: removed 3 packages (formula-a, formula-b, formula-c)
  Cache now:       ~/Library/Caches/Homebrew = 40 MB

  Everything removed is re-downloaded automatically on the next
  brew install / brew upgrade.
```

---

## Rules

- **`brew cleanup` is always safe** — it only removes superseded versions and
  cached downloads, never anything currently installed. It is the recommended
  default.
- **`brew autoremove` is opt-in and shown as an explicit list** before running.
  It removes only formulae that were installed as dependencies and are no
  longer needed — never packages in `brew leaves` (your direct installs). Still,
  confirm the list with the user (unless `--yes --autoremove`).
- Never `brew uninstall` a user-requested package.
- Always measure with `--dry-run` first and show the preview before changing
  anything (unless `--yes`).
- If there is nothing to clean, report `Nothing to clean.` and stop.
