# Registry profile schema

Per-repo profiles resolved by every gogox-claude command's "Step 0: Resolve
project profile". Resolution order (first hit wins):

1. `<repo-root>/.gogox-claude.yaml` — the repo self-describes (committed; co-owners inherit it).
2. `~/.claude/commands/profiles/registry/<basename>.yaml` — central per-repo map (this directory; symlinked / installed by `install.sh`).

`<basename>` is `basename "$(git rev-parse --show-toplevel)"`.

## Required keys

| Key | Type | Meaning |
|-----|------|---------|
| `platform` | `flutter` \| `android` \| `ios` \| `node` \| `prompt` | Drives every platform-branched step (test runner, formatter, build). |
| `product` | string | Product short code (e.g. `ca`, `da`). |
| `branch_prefix` | string | Ticket team key (e.g. `CET`, `GGC`). |
| `ticket_system` | `linear` \| `jira` | Which tracker MCP to use. |

## Optional keys

All optional keys are **additive with empty defaults** — a repo that omits them
behaves exactly as before (zero diff). The platform yaml
(`profiles/platform/<platform>.yaml`) supplies the defaults these override.

### Per-repo test profile (GGC-24)

Resolved centrally by `lib/dev-mode.sh` (`resolved_test_task`,
`resolved_android_test_task`, `known_flaky_tests`) so the override flows to
EVERY test consumer — `/check-test`, `/dev:verify` Step 1, and the
`/ggx-pr-resolver` / `/resolve-conflict` callee tests-green gate — from one
place instead of an individual's auto-memory.

| Key | Type | Meaning |
|-----|------|---------|
| `test_task` | string | Gradle unit-test task name (android). Overrides the platform default `testDebugUnitTest`. Example: `testStandardStagingUnitTest` (gogovan-client-v2-android does NOT have `testDebugUnitTest`). Wins over `test_variant`. |
| `test_variant` | string | Build variant convenience alias. When `test_task` is absent, the task is derived as `test<Variant>UnitTest` with the first letter capitalized (e.g. `standardStaging` → `testStandardStagingUnitTest`). |
| `known_flaky_tests` | list of strings | Tests that fail in a full-suite run but pass individually (e.g. CET-8234/8424). The flake-quarantine partition removes these from the `--fix` budget BEFORE the loop runs, and the suppression banner lists each one verbatim. Each entry is `Class#method` (single method) or `Class` (whole class). **Matching is EXACT** on the fully-qualified class (and method, when given) — never a name substring — so a real regression in a different method of a flaky class is NOT masked. |

Both YAML list shapes are accepted for `known_flaky_tests`:

```yaml
# inline
known_flaky_tests: [com.gogovan.FooTest#flakyA, com.gogovan.BarTest]
```

```yaml
# block
known_flaky_tests:
  - com.gogovan.FooTest#flakyA
  - com.gogovan.BarTest
```

#### Suppression is never invisible

Whenever the partition removes any known flake, the consumer prints a banner:

```
Known flakes suppressed: 2
  - com.gogovan.FooTest#flakyA   (CET-8234)
  - com.gogovan.BarTest          (CET-8424)
```

and `/dev:verify` records the same `Known flakes suppressed: N` line in
`.dev/verify-pass.md`. If `known_flaky_tests` is empty, no banner is printed
and the budget is unchanged.
