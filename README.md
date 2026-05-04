# gogox-claude

Internal shared Claude Code skills and agents for gogox. Manual install, override-friendly.

> 繁中新手導覽：[USER_GUIDE.zh-TW.md](./USER_GUIDE.zh-TW.md)

## Install

```bash
git clone <repo-url> gogox-claude
cd gogox-claude
./install.sh
```

One command, installs everything. After install, skills appear in `~/.claude/skills/`, commands in `~/.claude/commands/`, and agents in `~/.claude/agents/`. Skills and commands are both invoked as `/<name>` (folder categories inside the repo are organization-only — they collapse flat at install time).

To update: `git pull`. Files are symlinked into `~/.claude/`, so the pull is the upgrade — no need to re-run `install.sh` unless you want to pick up newly-added top-level files.

## Layout

```
skills/
  shared/   workflows used across roles (e.g. ship, review)
  pm/       PM-specific workflows
  dev/      engineering workflows
  design/   designer workflows
commands/
  shared/  pm/  dev/  design/    slash commands — single .md file per command
  <category>/profiles/           data files (.yaml) read by commands at runtime
agents/
  shared/  pm/  dev/  design/    same shape, but each agent is a single .md file
_template/
  SKILL.md  canonical skill template — copy this when adding a new skill
```

## Project-aware commands

Some commands change behavior based on the current project — e.g. `/add-worktree` runs `flutter pub get` in a Flutter repo and skips that step in a native Android repo.

Per-platform/per-product details live in YAML profiles under `commands/<category>/profiles/`, installed alongside commands at `~/.claude/commands/profiles/`. They are **data files**, not slash commands (`.yaml`, not `.md`).

The shape:

```
commands/dev/profiles/
  platform/{flutter,android,ios}.yaml      deps_install, test_cmd, format_cmd, ide_open_hint
  product/{ca,da,ca-revamp,da-revamp}.yaml branch_prefix
  repos.yaml                                central repo → (platform, product) mapping
```

A command resolves the current project's profile at runtime:

1. Reads `<repo-root>/.gogox-claude.yaml` if it exists (repo self-describes).
2. Else looks up `basename $(git rev-parse --show-toplevel)` in `~/.claude/commands/profiles/repos.yaml`.
3. Else errors with a hint to add the repo.

Adding a new repo: append to `commands/dev/profiles/repos.yaml`. Adding a new platform: drop a yaml into `commands/dev/profiles/platform/`. Adding a new product: drop a yaml into `commands/dev/profiles/product/`.

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

## Usage logging

Every skill template logs a single line to `~/.gogox-claude-usage.jsonl` on each run. This is a **local file owned by the user** — nothing is sent anywhere. We use it for the quarterly review (see Lifecycle below).

## Lifecycle and sunset

- **Quarterly review** (every 3 months): we ask volunteers to share their `~/.gogox-claude-usage.jsonl`. Skills with zero usage get deleted.
- **Month 3 gate**: any skill no one has used gets dropped.
- **Month 6 gate**: if repo-wide active users < 5, the project is sunset. No "give it more time."

These gates are intentional. Internal tools die from sprawl, not from lack of skills. Pre-committing to the kill criteria keeps the repo lean.

## Ownership

This repo has two co-owners across different roles. Bus factor 2. Both review PRs; both run the quarterly review.

Current owners: TBD (filled in after Week 1 1-on-1s).

## Status

v1 skeleton — no skills shipped yet. Real skills land after the workflow-mapping interviews finish. See `ARCHITECTURE.md` for the design and rollout plan.
