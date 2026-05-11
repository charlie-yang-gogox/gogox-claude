# gogox-claude

Internal shared Claude Code skills and agents for gogox. Manual install, override-friendly.

> 繁中新手導覽：[USER_GUIDE.zh-TW.md](./USER_GUIDE.zh-TW.md)

## Install

### 1. Install gogox-claude (once per machine)

```bash
git clone <repo-url> gogox-claude
cd gogox-claude
./install.sh
```

Skills appear in `~/.claude/skills/`, commands in `~/.claude/commands/`, and agents in `~/.claude/agents/`. All are symlinked — `git pull` is the upgrade, no need to re-run `install.sh`.

### 2. Set up a project (once per project)

In any project repo, run:

```
/init-project
```

This generates a `.gogox-claude.yaml` in the project root. **Commit this file to git** — once pushed, everyone on the team gets the config automatically.

Example `.gogox-claude.yaml`:

```yaml
# Fixed mode (single-product repo)
platform: ios
product: ca
branch_prefix: CET
ticket_system: jira

# Auto mode (shared/core repo — tickets from multiple products)
platform: ios
product: ggx-core-ios
branch_prefix: auto
ticket_system: auto
```

| Field | Values | Description |
|-------|--------|-------------|
| `platform` | `ios`, `android`, `flutter` | Determines which test/format/deps commands to use |
| `product` | `ca`, `da`, `ca-revamp`, `da-revamp`, or custom | Product name |
| `branch_prefix` | `CET`, `DET`, `CAF`, `DAF`, or `auto` | `auto` detects prefix from branch name at runtime |
| `ticket_system` | `jira`, `linear`, `auto`, or `none` | `auto` resolves from branch prefix via `org.yaml` |

### 3. Update

```bash
cd gogox-claude && git pull
```

Files are symlinked — the pull is the upgrade.

## Layout

```
skills/
  shared/   workflows used across roles (e.g. ship, review)
  pm/       PM-specific workflows
  dev/      engineering workflows
  design/   designer workflows
commands/
  shared/  pm/  dev/  design/    slash commands — single .md file per command
  <category>/<namespace>/        namespaced commands like commands/dev/port/*.md → /port:start
  <category>/profiles/           data files (.yaml) read by commands at runtime
agents/
  shared/  pm/  dev/  design/    same shape, but each agent is a single .md file
_template/
  SKILL.md  canonical skill template — copy this when adding a new skill
```

## Skill discovery

The full list of installables — every command, skill, and agent in this repo — lives as a Notion database for browsing and search:

**[claude-gogox / Claude Skills](https://www.notion.so/gogox/claude-gogox-357f54d1149880c98674f8b1218ee1f1)**

Auto-maintained by `/sync-skills-to-notion` (run from the gogox-claude repo root). Filter by `Category` (`dev` / `shared` / `design` / `pm` / `agent`) to scope by team or asset type, or open any row to read the full skill detail without checking out the repo.

## Port pipeline

Port a feature from one codebase to another (e.g. Android → Flutter) via OpenSpec. The pipeline reads the origin codebase, produces planning notes via three sub-agents, synthesises OpenSpec artifacts, runs deterministic guards, and pushes a feature branch with a Linear summary — all under one slash command.

### Quick start

```
/port:ff --ticket:CAF-212
```

That runs the full pipeline (`start → explore → plan → synth → revise → ship`) honoring HITL gates. End state: a `feat/<ticket-id>` branch is pushed with `openspec/changes/<change-name>/{proposal,design,tasks,specs}` ready for `/opsx:apply`.

For unattended dispatcher use:

```
/port:ff --ticket:CAF-212 --auto
```

For a lightweight scoping pass (no worktree, just a Linear analysis comment):

```
/port:ff --ticket:CAF-212 --simple
```

### Atomic stages

Each stage is independently re-runnable. Use them when you want to pause / iterate between phases.

| Command | Stage | Owner | Output |
|---|---|---|---|
| `/port:start` | resolve + worktree + scaffold | orchestrator | `.port/` initialized; cwd inside worktree |
| `/port:explore` | source-codebase consult | `dev-consult-agent` (opus) | `.port/dev-notes.md` (Locate gate hardened) |
| `/port:plan` | PM + design notes | `pm-agent` + `designer-agent` parallel (sonnet) | `.port/{pm-notes,design-notes}.md` |
| `/port:synth` | OpenSpec artifact synthesis | `synth-agent` (opus pinned) + `/spec-lint` | artifacts + `.port/synth-report.md` |
| `/port:revise` | HITL clarification + review gate | orchestrator | revised artifacts; `Review approved` sentinel |
| `/port:ship` | commit + push + Linear write-back | orchestrator | branch pushed; Linear PRD region updated |

`/port:explore --simple` is also valid as a standalone — exploration only, no worktree, no spec.

### Setup (per-machine, once per port-target repo)

The pipeline ports FROM an origin codebase that lives somewhere on your disk. Set the path in `.claude/port-settings.json` (gitignored):

```json
{
  "originalProjectPath": "/Users/me/Projects/work_project/gogovan-client-v2-android"
}
```

Supports `~` and `$ENV_VAR` expansion. `/port:start` validates the path on first run; if missing or invalid, it prompts and writes back. The format is inherited verbatim from the v1 flutter `/port` skill — repos that already have this file from before the gogox-claude migration keep working with no changes.

### Deterministic guard: `/spec-lint`

`/port:synth` invokes `/spec-lint` automatically, but you can run it standalone on any OpenSpec change:

```
/spec-lint --change <change-name>
```

Nine pure-grep checks: capability-name alignment, FR-vs-scenario count, hardcoded drift, divergence markers, forbidden markers (TBD/TODO/FIXME), bidirectional B-citation (catches synthesis omission AND hallucination via labeled `**FR-N**` / `**AC-N**` / `**R-N**` / `**A-N**` IDs), zero-ID drift warning, and post-edit cascade scan (`--after-edit --stale "term1,term2"`).

Design rationale, decisions, and risk register: see [`plans/port-centralization.md`](./plans/port-centralization.md).

## Project-aware commands

Some commands change behavior based on the current project — e.g. `/add-worktree` runs `flutter pub get` in a Flutter repo and skips that step in a native Android repo.

### Config resolution

Commands resolve the current project's config at runtime:

1. `<repo-root>/.gogox-claude.yaml` — **source of truth** (committed), generated by `/init-project`. Holds `platform`, `product`, `branch_prefix`, `ticket_system`.
2. `<repo-root>/.claude/port-settings.json` — **per-machine override** (gitignored). Holds `originalProjectPath` for `/port:start`. First-run writes from `AskUserQuestion`. Each dev's machine has its own.
3. `~/.claude/commands/profiles/registry/{repo-name}.yaml` — **fallback** for (1), auto-maintained.
4. Neither (1) nor (3) found → error: "Run `/init-project` to set up this repo."

#### `.claude/port-settings.json` — fields

| Field | Used by | Notes |
|---|---|---|
| `originalProjectPath` | `/port:start` | Absolute path to the origin codebase (the project being ported FROM). Supports `~` and `$ENV_VAR` expansion. Validated on first read; if path doesn't exist after expansion, `/port:start` prompts to fix |

Format is inherited verbatim from the legacy flutter v1 `/port` skill so existing repos continue to work. New repos get the file written on first `/port:start` run.

Add to your repo's `.gitignore`:
```
/.claude/port-settings.json
```

#### Linear team key

`linear_team_key` is **not a separate field** — it derives from `branch_prefix` in `.gogox-claude.yaml` when `ticket_system: linear`. E.g. `branch_prefix: CAF` → Linear team key is `CAF`.

### Profiles (read-only reference data)

```
commands/dev/profiles/
  org.yaml                                  organization-level constants (Jira/Linear URLs, prefix mapping)
  platform/{flutter,android,ios}.yaml      deps_install, test_cmd, format_cmd, ide_open_hint
  registry/{repo-name}.yaml                 auto-maintained project registry (one file per repo)
```

### Ticket system integration

Each project declares its ticket system in `.gogox-claude.yaml`. Commands like `/pull-request` branch on this value to fetch ticket titles, build URLs, and post implementation notes.

- **Fixed mode** (`ticket_system: jira` or `linear`) — single ticket system per repo.
- **Auto mode** (`ticket_system: auto`) — detects from branch name. E.g., `feat/CET-1234` → looks up `CET` in `org.yaml` → resolves to `jira`.

Organization-level constants (Jira cloud ID, base URLs, prefix mapping) live in `commands/dev/profiles/org.yaml`.

## Adding a skill

1. Copy `_template/SKILL.md` into the right category folder:
   ```bash
   mkdir -p skills/<category>/<your-skill-name>
   cp _template/SKILL.md skills/<category>/<your-skill-name>/SKILL.md
   ```
2. Edit the frontmatter (`name`, `description`) and body.
3. Open a PR. PR rules:
   - **All skill content must be written in English** — frontmatter, body, comments, examples. No exceptions. This applies to skill names, file content, and PR descriptions.
   - Every skill must have a `description` and at least one usage example.
   - Skills going into `shared/` need +1 from at least two different roles.
   - Declare any required MCP servers in the skill body (Linear, Atlassian, etc).
4. Skill names are kebab-case and globally unique across categories — `shared/` wins on collision.
5. After the PR is merged, run `/sync-skills-to-notion` from the gogox-claude repo root to update the team's Notion index.

## Usage logging

Every skill template logs a single line to `~/.gogox-claude-usage.jsonl` on each run. This is a **local file owned by the user** — nothing is sent anywhere. We use it for the quarterly review (see Lifecycle below).

## Lifecycle and sunset

- **Quarterly review** (every 3 months): we ask volunteers to share their `~/.gogox-claude-usage.jsonl`. Skills with zero usage get deleted.
- **Month 3 gate**: any skill no one has used gets dropped.
- **Month 6 gate**: if repo-wide active users < 5, the project is sunset. No "give it more time."

These gates are intentional. Internal tools die from sprawl, not from lack of skills. Pre-committing to the kill criteria keeps the repo lean.

## Roadmap: Auto-sync (Phase 2)

Currently, `git pull` is manual. When team size warrants it, a gstack-style `SessionStart` hook will auto-pull gogox-claude and keep the registry in sync. See `commands/dev/init-project.md` § Phase 2 for the full spec. This is additive — no changes to the current install or profile resolution.

## Ownership

This repo has two co-owners across different roles. Bus factor 2. Both review PRs; both run the quarterly review.

Current owners: TBD (filled in after Week 1 1-on-1s).

## Status

v1 skeleton — no skills shipped yet. Real skills land after the workflow-mapping interviews finish. See `ARCHITECTURE.md` for the design and rollout plan.
