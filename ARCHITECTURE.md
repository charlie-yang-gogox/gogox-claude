# gogox-claude — Architecture & Rollout

One-pager for stakeholder discussion. Diagrams are ASCII so this renders anywhere (Slack, Confluence, GitHub, email).

## The problem

```
TODAY                                     WHAT WE WANT
─────────────────────                     ───────────────────────
Each gogox employee figures out           Workflows that work get
their own Claude Code workflow.           shared, reused, and improved.
Good prompts die in DMs.                  New hires inherit the playbook.
PMs don't know what Devs use.             Cross-role visibility.
No one knows what's working.              Usage data drives investment.
```

## Solution: a thin shared repo + install script

Not a platform. Not a service. A git repo + 80 lines of bash. The bet is that **content** (gogox-specific workflows + company context) is the moat — Anthropic's plugin marketplace will obsolete distribution mechanisms within 6 months, but it can never know who owns what at gogox.

## Architecture

```
                  ┌───────────────────────────────┐
                  │   gogox-claude (this repo)    │
                  │                                │
                  │   skills/{shared,pm,dev,design}│
                  │   agents/{shared,pm,dev,design}│
                  │   _template/SKILL.md           │
                  │   install.sh                   │
                  └───────────────────────────────┘
                                │
                                │  one-line curl
                                │  (run once per user)
                                ▼
                  ┌───────────────────────────────┐
                  │     User's laptop             │
                  │     ~/.claude/skills/         │
                  │       /prd-draft               │
                  │       /code-review             │
                  │       /linear-pickup           │
                  │       …                         │
                  │     ~/.claude/agents/         │
                  └───────────────────────────────┘
                                │
                                │  user invokes /<skill-name>
                                ▼
                  ┌───────────────────────────────┐
                  │      Claude Code              │
                  │      (CLI or desktop app)     │
                  │                                │
                  │  skill body runs:              │
                  │    1. log usage (jsonl)        │
                  │    2. read Gogox Context       │
                  │    3. call MCP tools           │
                  │       (Linear/Slack/Atlassian) │
                  │    4. produce output           │
                  └───────────────────────────────┘
                                │
                                │  every run appends one line
                                ▼
                  ~/.gogox-claude-usage.jsonl
                  (local file, user-owned, never sent anywhere)
                                │
                                │  quarterly review,
                                │  voluntary share-back
                                ▼
                  ┌───────────────────────────────┐
                  │   Sunset gates                │
                  │   Month 3: drop unused skills │
                  │   Month 6: <5 users → kill    │
                  └───────────────────────────────┘
```

## Why these design choices

| Decision | Why |
|---|---|
| **Manual install (no auto-update)** | Users own their copy. Local edits survive. No central server, no privacy story to manage. |
| **Folders are browsing-only** | After install, skills are flat (`/skill-name`). Categories are for picking what to install, not for invocation namespace. |
| **No central config skill** | MCP auth is per-user via Claude Code's `/mcp`. Team IDs and system owners are hardcoded into each skill's `# Gogox Context` section. |
| **Local jsonl logging, not central telemetry** | Zero infrastructure. No privacy questions. Users opt in by sharing during quarterly review. |
| **Pre-committed sunset gates** | Internal tools die from sprawl. Writing the kill criteria up front prevents "give it more time" rationalization later. |
| **Cross-role co-owners** | Bus factor 2. Forces multi-audience design — if a PM owner can't read a skill's SKILL.md, it doesn't merge. |

## Rollout

```
Week 1                Week 2               Week 3              Month 3              Month 6
──────                ──────               ──────              ───────              ───────
Open empty repo.      Synthesize 1-on-1s.  Write 3 lighthouse  Sunset gate 1:       Sunset gate 2:
Write template,       Pick 3 highest-      skills with Gogox    drop unused          if active users
install.sh, README.   leverage workflow    Context.             skills.              < 5, kill the
Run 3 × 30-min        nodes.               Soft-launch to 3                          project.
1-on-1s with PM/      Find non-Dev         interviewees.        Decide if any
Dev/Designer.         co-owner from        Co-owner co-signs    skill earns Notion
                      interview pool.      first PR.            mirror or data
                                                                integration.
```

## What we'll do later (not in v1)

- Validation CI / GitHub Action for SKILL.md format. Defer until first real PR breaks something.
- CODEOWNERS file. Defer until skill count > 20.
- Notion mirror or Slack bot. Defer until a specific high-frequency skill proves PM/Designer demand.
- Data integration layer (skills reading past PRs/PRDs/postmortems). Gated on v1 usage data — only invest in integrations for skills that prove themselves used.
- Central telemetry endpoint. Local jsonl is sufficient for v1.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Bus factor 1 → repo dies when owner gets busy | Cross-role co-owner, identified during Week 1 1-on-1s. |
| Skills written by power users don't fit average employees' needs | Workflow-map interviews drive content selection, not the owner's intuition. |
| PM/Designer won't run a terminal install | One-line curl + Claude Code desktop app. Onboarding doc is one paragraph. |
| Anthropic plugin marketplace obsoletes our distribution mechanism | We bet on content (gogox-specific context) not infra. Distribution can be ported when marketplace lands. |
| Repo becomes a dotfiles graveyard | Pre-committed sunset gates at Month 3 and Month 6. |

## Ask

- **10% time for 2 co-owners** for the first 3 months. After Month 3 review, re-evaluate.
- **Buy-in on the Month 6 sunset rule.** If we miss the < 5 user threshold, we kill it without further discussion. Pre-committing to this protects the team from sunk-cost rationalization.
