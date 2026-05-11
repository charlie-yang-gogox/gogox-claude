# /ggx-dispatcher — execution flow

Visual flow of the dispatcher command, down to `/port:ff` / `/dev:ff` invocation only. Companion to `ggx-dispatcher.md` (the design rationale).

```
                       /ggx-dispatcher [--dry-run] [--test]
                                      [--max-parallel:N]   (default 10, cap 20)
                                      [--team:<KEY>]
                                       │
                                       ▼
══════════════════════════════════════════════════════════════════════
  STEP 0 — Profile resolution
══════════════════════════════════════════════════════════════════════
                                       │
  Read  ~/.claude/commands/profiles/registry/$(basename cwd).yaml
        (fallback: <repo-root>/.gogox-claude.yaml)
                                       │
                       ticket_system == linear?
                            │NO                          │YES
                            ▼                            │
                       ABORT (not a linear repo)         │
                                                         │
                       branch_prefix value?              │
                            │ "auto"                     │ concrete (CAF/CET/...)
                            ▼                            ▼
                  --team:<KEY> given?           --team:<KEY> given?
                       │NO        │YES               │NO          │YES
                       ▼          ▼                  │            ▼
                    ABORT      team_key              │      KEY == prefix?
                  ("pass        = upper(KEY)         │       │NO       │YES
                   --team")     + validate           │       ▼         │
                                vs org.yaml          │     ABORT       │
                                       │             │   (mismatch)    │
                                       └──────┬──────┴─────────────────┘
                                              ▼
                       team_key resolved
                                       │
                                       ▼
            git symbolic-ref refs/remotes/origin/HEAD
                       │empty                  │ok
                       ▼                       │
            gh repo view --json defaultBranchRef
                       │empty   │ok            │
                       ▼        └──────────┬───┘
                    ABORT                  ▼
                                  default_branch resolved
                                       │
                                       ▼
══════════════════════════════════════════════════════════════════════
  STEP 1 — Pre-flight
══════════════════════════════════════════════════════════════════════
                                       │
  Acquire  claude-reports/dispatcher/.lock  (write PID + ISO ts)
                       │
            existing lock < 10min old?
                  │YES                        │NO (or stale)
                  ▼                           │
                ABORT                         │
            ("another run, PID X")            │
                                              │
                       [ git-common-dir == git-dir ]?
                                  │NO                  │YES
                                  ▼                    │
                            ABORT                      │
                  "in worktree — cd <main>"            │
                                                       │
                              --test set?
                          │YES                  │NO
                          │                     │
                          │           on default_branch?
                          │              │NO         │YES
                          │              ▼           │
                          │         ABORT            │
                          │     "switch to <d>"      │
                          │                          │
                          │                  clean tree?
                          │                     │NO       │YES
                          │                     ▼         │
                          │                 ABORT         │
                          │              "stash/commit"   │
                          │                               │
                          └───────────────┬───────────────┘
                                          ▼
                          git worktree prune
                          gh auth status?
                                │fail              │ok
                                ▼                  │
                              ABORT                │
                          "gh login required"      │
                                                   │
                          log open PRs (info only) │
                                                   ▼
══════════════════════════════════════════════════════════════════════
  STEP 2 — Find tickets
══════════════════════════════════════════════════════════════════════
                                       │
  Run 4× mcp__claude_ai_Linear__list_issues:
       Q1: { team, assignee:"me", label:"ready-to-port", state:"unstarted"   }   ← To-do + Reopened
       Q2: { team, assignee:"me", label:"ready-to-port", state:"In Progress" }   ← only In Progress (excludes In Review / Ready for QA)
       Q3: { team, assignee:"me", label:"ready-to-dev",  state:"unstarted"   }
       Q4: { team, assignee:"me", label:"ready-to-dev",  state:"In Progress" }
                                       │
            Dedup by ticket id  (preserve merged labels[])
                                       │
            Any ticket with BOTH ready-to-port AND ready-to-dev?
                       │YES                        │NO
                       ▼                           │
            remove BOTH labels                     │
            post "duplicate label" comment         │
            drop from batch                        │
                       └──────────┬────────────────┘
                                  ▼
            Priority sort (urgent > high > medium > low > none,
                           then oldest created)
                                  │
            Take top --max-parallel  (default 10, capped 20)
                                  │
                       Empty list?
                       │YES              │NO
                       ▼                 │
                  STOP cleanly           │
                  (release lock)         ▼
                                       │
══════════════════════════════════════════════════════════════════════
  STEP 3 — Anti-duplicate (per ticket)
══════════════════════════════════════════════════════════════════════
                                       │
            For each ticket  ──────────┐
                                       ▼
            gh pr list | grep [/-]<id>($|[^0-9])
                       │match              │no match
                       ▼                   │
            remove label                   │
            post "PR exists" comment       │
            drop ticket                    │
                       └──────┬────────────┘
                              ▼
            git ls-remote --heads origin | grep <id>
                       │match                │no match
                       ▼                     │
                label?                       │
                ├─ ready-to-port → drop + remove label + comment
                ├─ ready-to-dev  → KEEP  (expected: port created branch)
                                  └────────────┬───┘
                                               ▼
                                      ticket retained
                                               │
                              all tickets done? ─── no ─→ next ticket
                                       │yes
                                       ▼
                       Survivors empty? ─── yes → STOP (release lock)
                                       │ no
                                       ▼
══════════════════════════════════════════════════════════════════════
  STEP 3.5 — Port config pre-check  (only if any port ticket survives)
══════════════════════════════════════════════════════════════════════
                                       │
            Any survivor has label ready-to-port?
                       │NO                       │YES
                       │                         ▼
                       │           .claude/port-settings.json exists?
                       │                  │NO              │YES
                       │                  ▼                │
                       │              ABORT                │
                       │       "run /port:start once       │
                       │        interactively first"       │
                       │                                   │
                       │           originalProjectPath set?
                       │                  │NO              │YES
                       │                  ▼                │
                       │              ABORT                │
                       │            "fix config"           │
                       │                                   │
                       │           expanded path exists on disk?
                       │                  │NO              │YES
                       │                  ▼                │
                       │              ABORT                │
                       │        "path does not exist"      │
                       └────────────────────┬──────────────┘
                                            ▼
══════════════════════════════════════════════════════════════════════
  STEP 4 — Race-lock  (init protocol — D8 SYNC point)
══════════════════════════════════════════════════════════════════════
                                       │
                       --dry-run set?
                       │YES                        │NO
                       ▼                           │
            Print planned dispatch table           │
            STOP — release lock                    │
            (NO mutations)                         │
                                                   ▼
            For each surviving ticket SEQUENTIALLY:
              # SYNC: when changing this list, also update
              #   /dev:start  line 82
              #   /port:start line ___ (--auto block)
              1. save_issue: remove actionable label
              2. save_issue: status In Progress
              3. save_issue: assignee = $USER_NAME
              4. save_issue: estimate = 1  (if currently null)
              5. save_comment: "Dispatcher: starting <port|dev>..."
                                       │
            Mid-batch failure?
                       │YES                        │NO
                       ▼                           │
            For each #1..#N-1:                     │
              best-effort unlock                   │
              (re-add label, post "aborting"       │
               comment)                            │
            STOP — release lock                    │
            (stdout shows full trace per §7)       │
                                                   ▼
══════════════════════════════════════════════════════════════════════
  STEP 5 — Parallel dispatch
══════════════════════════════════════════════════════════════════════
                                       │
            Build per-ticket command:
              ┌──────────────┐                  ┌──────────────┐
              │ ready-to-port│                  │ ready-to-dev │
              └──────┬───────┘                  └──────┬───────┘
                     ▼                                 ▼
              /port:ff --ticket:<ID>              description has
                       --auto                     figma.com/(design|
                                                  board|slides|make)/?
                                                       │YES        │NO
                                                       ▼           ▼
                                            /dev:ff <ID>    /dev:ff <ID>
                                                  --auto     --auto
                                                              --no-figma
                                       │
                                       ▼
            Single Claude message containing N Agent calls
              run_in_background: true
              mode: "bypassPermissions"
              isolation: NONE   (ff creates its own worktree)
                                       │
                                       ▼
            (Each Agent runs ff end-to-end inside its own worktree;
             dispatcher does NOT see ff's internal stages.)
                                       │
══════════════════════════════════════════════════════════════════════
  STEP 6 — Wait, fallback, finalize
══════════════════════════════════════════════════════════════════════
                                       │
            WAIT for all N agents (synchronous join)
              ── session must stay open
              ── machine must not sleep
              ── closing terminal kills MCP, reports may be incomplete
                                       │
                                       ▼
            For each completed ticket:
              ┌─ port + success
              │     ├── /port:ship added need-spec-review? → ok
              │     └── missing? → fallback: add need-spec-review
              │
              ├─ dev + success
              │     ├── /dev:ff set status In Review? → ok
              │     └── missing? → fallback: set In Review
              │
              └─ failure
                    └── post failure comment via fallback
                        (label already removed in Step 4 →
                         human re-adds to retry)
                                       │
                                       ▼
            Aggregate reports:
              for each ticket:
                copy/symlink
                  <worktree>/claude-reports/<session>/*
                  →
                  <main>/claude-reports/dispatcher/<ts>-<pid>/<ticket>/
                                       │
                                       ▼
            Write run summary:
              claude-reports/dispatcher/<ts>-<pid>.md
                  · counts: dispatched / skipped / failed
                  · per-ticket result + worktree path
                                       │
                                       ▼
            Release  claude-reports/dispatcher/.lock
                                       │
                                       ▼
            Print summary line
                                       │
                                       ▼
                                    DONE
```

---

## Notes for review

- **Where the flow STOPs**: any ABORT in Step 0/1, empty-tickets in Step 2/3, dry-run in Step 4, lock-failure in Step 4, or completion in Step 6. All paths release the lockfile if it was acquired.
- **Where it WAITS**: only Step 6 (synchronous join on all N spawned ff agents).
- **Where it MUTATES Linear**: Step 2 (duplicate-label cleanup), Step 3 (drops with label removal), Step 4 (race-lock), Step 6 (fallbacks). Dry-run gates Step 4 and beyond.
- **States dispatched**: only `To-do`, `Reopened`, `In Progress`. Excluded: `Triage`, `Backlog`, `In Review`, `Ready for QA`, `Done`, `Canceled`, `Duplicate`.
- **Where it MUTATES git**: nowhere directly. ff agents do all git work inside their own worktrees.
- **Where stdout is the audit trail (per §7)**: Step 4 lock progress prints `<ticket>: locked ✓` or `<ticket>: failed (<reason>)` per ticket. Step 6 result prints similarly.
