# gogox-claude

Internal shared Claude Code skills and agents for gogox. Manual install, override-friendly.

## Install

```bash
git clone <repo-url> gogox-claude
cd gogox-claude
./install.sh                  # installs shared/ only
./install.sh pm dev           # installs shared + pm + dev
./install.sh pm dev design    # installs everything
```

`shared/` is always installed. After install, skills appear in `~/.claude/skills/` and are invoked as `/<skill-name>` (folder categories are browsing-only — they collapse flat at install time).

To update: `git pull && ./install.sh <same categories>`. There is no auto-update by design — your local edits to a skill stay until you re-run install for that category.

## Persona examples

- **PM**: `./install.sh pm`
- **Dev**: `./install.sh dev`
- **Designer**: `./install.sh design`
- **Dev who occasionally writes PRDs**: `./install.sh pm dev`
- **Anyone**: `./install.sh` gets you the shared workflows

## Layout

```
skills/
  shared/   workflows used across roles (e.g. ship, review)
  pm/       PM-specific workflows
  dev/      engineering workflows
  design/   designer workflows
agents/
  shared/  pm/  dev/  design/    same shape, but each agent is a single .md file
_template/
  SKILL.md  canonical skill template — copy this when adding a new skill
```

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
