# Plan: /ggx-on-duty — working-hours on-duty loop + /ggx-pr-resolver

> Status: **APPROVED FOR CONSTRUCTION** (2026-06-05)
> Author: Charlie Yang (design driven interactively with Claude; two review rounds passed)
> Branch: `feat/ggx-on-duty`

## 1. Goal & background

The 2x/day cloud routines (`ticket-analyzer-agent` + `ggx-dev-agent`) are **disabled**.
Their replacement is a local, working-hours `/loop` session the user starts on arrival:
`/ggx-on-duty`. One Claude session becomes a duty worker that

- classifies To-Do tickets and dispatches actionable ones (ticket → PR),
- keeps every open PR rebased and review-comment-free (PR → mergeable),

leaving the human three jobs: read notifications, fix `need-revision` tickets, merge PRs.

A second deliverable falls out of the design: **`/ggx-pr-resolver`**, a unified per-PR
health worker that merges `/resolve-conflict --batch` + `/resolve-pr-comments` into one
pass (rebase → resolve comments → single push → one CI run). It is independently useful
outside the loop.

## 2. Architecture

```
/ggx-on-duty 啟動 → pre-flight → /loop (NO interval — dynamic mode)

每次醒來 (wake cycle, ~1-2 min, never blocks):
  Leg 1  分類→派工鏈   (~2h due)  背景 agent 依序:
                       /ticket-analyze --non-interactive  ← REAL WRITE: 貼 verdict labels
                       → /ggx-dispatcher                  ← 立刻消費剛貼的 ready-to-*
  Leg 2  PR 健康輪詢   (~1h due)  一個 pass:
                       ① CI 變紅 → 通知 (transition-only, dedup)
                       ② /ggx-pr-resolver --batch --auto  ← 兩段式 gate,沒事跳過
  收尾: 一則彙總通知 → persist state → 動態決定下次喚醒
```

Key structural rules (full text lives in the two command specs):

- **Dynamic /loop pacing** — no fixed interval. Background-agent completions re-wake the
  session; ScheduleWakeup is only the fallback heartbeat (1800s while agents fly, sleep
  to earliest `next_due` otherwise, ~3600s cap when board is quiet, 240–270s short polls
  only when watching an imminent CI result; never ~300s).
- **Never inline-drive blocking commands** — dispatcher (§6.1 join barrier) and resolver
  (runs tests) are always `run_in_background`; a wake cycle always completes fast.
- **No lockfile probing** — agent liveness tracked exclusively via harness
  background-completion notifications + booleans in `.ggx-on-duty/state.json`
  (gitignored, in-repo-untracked like `claude-reports/`).
- **Classify before dispatch, always** — there is NO standalone dispatch; every dispatch
  cycle is one chained agent: analyze → dispatch. Replaces the cloud's 30-min time gap
  with a deterministic sequence.
- **Single health pass** — no event-triggered comment path; the resolver's cheap
  two-stage gate makes polling every PR affordable, so the poll IS the event detection.

## 3. Decision log (settled by the owner — do NOT re-litigate during construction)

| # | Decision |
|---|----------|
| D1 | `ticket-analyze` runs in REAL WRITE mode (labels + comments). It IS the classifier. The analyze→dispatch human-review window is deliberately waived (matches old cloud behavior). `--dry-run` rejected. |
| D2 | `resolve-pr-comments` strategy table is auto-approved inside the loop (`--auto`). |
| D3 | `resolve-conflict` does real rebase + push (dry-run/notify-only rejected). |
| D4 | analyze→dispatch strictly chained; dispatcher never sweeps an unclassified queue. |
| D5 | One merged hourly health poll; the separate event-triggered comment path was removed. |
| D6 | **REVERSED 2026-06-06 (owner)**: code-review leg removed — D6 was a design-round proposal the owner never requested; loop-pushed commits are human-reviewed at merge instead. Original: ~~code-review joins the loop; findings posted as inline PR comments feed the next resolver pass (same machinery as human comments, no separate apply path).~~ |
| D7 | Comment actionability is decided by an LLM **judge over ALL open threads by substance** (ACT / HANDLED / HOLD) — never by authorship/recency heuristics (those have blind spots; see E-1). |
| D8 | Dynamic /loop (no fixed interval). |
| D9 | No lockfile reading anywhere in on-duty (single-on-duty-per-repo assumed). |
| D10 | Drafts: resolver maintains them (rebase), code-review skips them. |

Round-3 decisions (pre-construction review, 2026-06-05):

| # | Decision |
|---|----------|
| D11 | Skip-set canonical key = **branch name (headRefName)**. The dispatcher persists an early in-flight file (after its §4.1 lock, before §5.3 spawn) at a deterministic path; on-duty and the resolver both consume it. PR numbers rejected — chain-in-flight tickets may have no PR yet (`/dev:ship` opens the PR last). |
| D12 | Resolver strategy is **REBASE-only** (no per-PR `--merge`) — step 7's unconditional `--force-with-lease` is correct by construction. Rebase target is the PR's `baseRef`, never assumed trunk. |
| D13 | Judge `HOLD` is **advisory screening only** (decides whether to pay worktree+tests). Once in the worktree, the resolve-pr-comments classifier is authoritative over every open thread; a HOLD/STALE-overlap thread being auto-resolved is accepted (resolved threads are reopenable; human-only HOLDs map to REPLY/DEFER and stay open). No blocklist handoff. |
| D14 | The single push is owned by **resolve-pr-comments** (its push step gains `--force-with-lease` support); the resolver pushes only in the rebase-only (no-comments) case. |
| D15 | `--until` **drains**: stop spawning new legs at the deadline, keep collecting in-flight agent completions, then final summary. |
| D16 | DEFER promise-replies ("will file a ticket after merge") are **suppressed under `--auto`** — nobody in the loop files them. |
| D17 | Accepted as deliberate (no construction work): duplicate digests (analyzer/dispatcher built-ins AND on-duty's summary all fire); double local test suite on rebase+comments PRs; best-effort live-HEAD code review (no SHA pinning) (obsolete — D6 reversed); no extra approval gate on self-review auto-fixes (obsolete — D6 reversed); manual session restart (no auto-compact trigger); Leg-2 scope stays `--author @me`. |
| D18 | Leg spawn mechanism = background **headless CLI sessions** (`claude -p --permission-mode bypassPermissions` via Bash `run_in_background`), NOT Agent-tool subagents, for both fan-out legs (Leg-1 chain, Leg-2 resolver batch). Found in live T-testing 2026-06-05: nested `Agent`/`Task` spawns fail inside a subagent (`ggx-dispatcher.md` §5.3), so an Agent-wrapped dispatcher batch-aborts ("spawn tool unavailable" — graceful: locks rolled back, Slack alert fired). Headless Linear MCP probe-verified working locally. The code-review leg (inline execution, no fan-out) stays an Agent spawn (obsolete — D6 reversed, the leg no longer exists). |
| D19 | Nested spawn is a **documented hard constraint** (official sub-agents.md: `Agent` tool unavailable in ALL subagents — uniformly, no type/background/model exceptions; no config bypass exists). Alternatives evaluated post-first-live-run (2026-06-06): orchestration-upmove (dispatcher phase-split + Workflow/on-duty top-level fan-out — large surgery), dropping on-duty for two independent top-level `/loop` sessions (loses CI-red alerts + the review→fix closed loop), agent-teams SendMessage broker (experimental, peer-only). **Owner: keep on-duty + D18 as-is** — no permission hardening, no refactor; revisit only on real pain. Side-finding: dispatcher §5.3's "nested sonnet works in practice" contradicts the docs — annotated for re-verification, behavior unchanged. Follow-up doc research: Anthropic's stated principle is "orchestrators and workers are separate layers; orchestration lives ONLY at the top" (skills/subagents are leaf workers by design; Managed Agents caps delegation at depth 1; headless `claude -p` from inside a session is an unsupported workaround) — so the future refactor, if pain appears, has official backing: lift fan-out into a top-level workflow script and demote the commands to leaf workers. |
| D20 | Resolver step-4 dirty guard adopts the dispatcher's **residue auto-stash semantics** (same allowlist, kept in sync; bar: machine-written + reproducible): residue-only dirt → labeled stash → proceed; anything else → `needs-human: worktree-dirty` untouched. Root cause of the 2026-06-05 #488 false positive: a dev-pipeline flutter build rewrote tracked `ios/Flutter/AppFrameworkInfo.plist` (now in both allowlists). True conflicts (#415 class) stay needs-human — verified working as designed via reflog forensics. |

## 4. Review history

1. **Workflow review (4-lens panel + adversarial verify, 24 agents)** — 18 confirmed
   findings, all folded in. Highlights: dispatcher §6.1 blocks its caller → background
   spawn architecture; dispatcher lock 600s TTL unreliable for external reads → harness
   notifications; `--non-interactive` mandatory for ticket-analyze in unattended runs;
   per-leg try/continue; session-lean summaries.
2. **ai-expert edge-case review** — 8 findings (E-1…E-8), all fixed in the specs:

| ID | Edge case | Fix in spec |
|----|-----------|-------------|
| E-1 | code-review posts comments as `@me` → "latest author != me" gate permanently hides them; review→fix loop silently dead | Two-stage gate: mechanical pre-filter (zero open threads → skip) + LLM judge reading EVERY open thread, classifying ACT/HANDLED/HOLD **by substance, not authorship** **(obsolete — D6 reversed 2026-06-06)** — the judge-by-substance design itself survives in the resolver (D7) |
| E-2 | review→fix ping-pong cap needs attribution a stateless batch can't provide | SHA-chain cap: `review_cycles[pr#]` counts consecutive reviews where ALL new commits came from `self_pushed`; any human commit resets; at 2 → notify-only + needs-human **(obsolete — D6 reversed 2026-06-06)** |
| E-3 | `/dev:ship` removes the in-flight label mid-sweep → resolver enters a worktree `/dev:ff` is still writing | on-duty passes the chain's claimed branches as an explicit **skip-set** to the resolver; no reliance on race-prone label reads |
| E-4 | "suppress self-pushed CI red for one cycle" swallows real failures (cycle length is dynamic) | Never swallow: report red tagged `(self-pushed rerun)`; clear the mark only when that SHA's CI reaches a terminal state |
| E-5 | laptop shutdown leaves `running=true` forever → both legs skip forever | On invocation: unconditional reset of running flags. On RECONCILE (session survived sleep): verify agent existence first, then reset — blind reset would double-spawn |
| E-6 | `gh pr list` failure looks like "all PRs merged" → state purge → re-notify/re-review storm | DEGRADED guard: call failed or empty-after-non-empty → skip ALL evictions/seeding this cycle |
| E-7 | code-review races resolver push; findings land on outdated diff | Review only **settled** SHAs: first poll where the resolver reports that PR `up-to-date`/`judged-clean`; `reviewed_shas` makes deferral lossless **(obsolete — D6 reversed 2026-06-06)** |
| E-8 | `health.next_due` set at spawn → a 70-min batch re-fires back-to-back | Set `next_due` on completion (aligns with chain) |

Low-risk fixes also applied: resolver pushes always `--force-with-lease` (rebase rewrites
history — resolve-pr-comments' plain `git push` would otherwise ALWAYS fail after a real
rebase; lease rejection → `push-failed`, never plain-force); code-review diffs against the
PR's `baseRef` (not hardcoded trunk) (obsolete — D6 reversed); bot-authored (`*[bot]`) latest comments don't count
as pending.

3. **Pre-construction review (6 grounding readers + 4-lens panel + adversarial verify,
   98 agents, 2026-06-05)** — full report:
   `claude-reports/ggx-on-duty/preconstruction-review.md`. No finding challenged the
   settled D/E decisions; all were "how the decision lands" spec gaps. 3 blockers:

| ID | Blocker | Fix |
|----|---------|-----|
| B1 | E-3 skip-set unconstructable: DISPATCH_ROSTER is session-state-only; the only on-disk claimed set is the §6.4 report, written AFTER the §6.1 join barrier — unreadable during the entire `chain.running` window | Dispatcher writes an early `*.inflight.tsv` (`ticket \t headRefName \t worktree`) right after its §4.1 lock, before §5.3 spawn; on-duty's skip-set reads it |
| B2 | Resolver borrowed the wrong halves of /resolve-conflict: `/add-worktree` is trunk-based + interactive + fork-blind; single mode is HITL (AskUserQuestion on doubt → unattended background agent deadlocks) and only batch has non-interactive give-up | Resolver step 4 uses the batch-B3 raw-worktree primitive; step 5 is a hybrid: worktree-scoped + batch-style auto-abort + NO push |
| B3 | Skip-set key mismatch: resolver spec said "PR numbers", but chain-in-flight tickets have no PR yet and the roster is ticket/branch-keyed | Canonical key = headRefName (D11) |

   Plus 7 majors (M1 Slack third source is a full design, not a row; M2 inline-PR-comment
   is a NEW code-review capability and `--auto` forbids posting today; M3 build-sanity
   abort needs a report state; M4 stale reused worktrees must fetch+reset the PR head or
   `--force-with-lease` carries an old lease and clobbers; M5 single-mode hardcodes
   origin/trunk — release-branch PRs corrupt; M6 HOLD-vs-STALE overlap → settled as D13;
   M7 the claimed "Linear auth" dispatcher pre-flight doesn't exist) — all folded into §6
   below — and a minor appendix (m1–m22) applied during construction (m1 — TaskList as
   the SOLE liveness authority — is mandatory: report-file probing would misjudge a live
   chain as dead and double-spawn). 13 owner decisions recorded as D11–D17.

## 5. Deliverables in this branch

| File | Role |
|------|------|
| `plans/ggx-on-duty.md` | this plan |
| `commands/dev/ggx-on-duty.md` | the loop orchestrator spec (reviewed draft) |
| `commands/dev/ggx-pr-resolver.md` | the unified per-PR worker spec (reviewed draft) |

The two specs were prototyped at `~/.claude/commands/` and are copied here verbatim as
the construction baseline.

## 6. Construction checklist (施工項目)

> **Phasing decision (2026-06-06, owner)**: build `/ggx-pr-resolver` STANDALONE first
> (Phase A); the `/ggx-on-duty` integration is DEFERRED (Phase B). Phase A = the three
> resolver-side items below + verification T1/T2, driven by direct user invocation
> (`/ggx-pr-resolver <PR#>` / `--batch`). Phase B (deferred) = M1 slack source, M7
> pre-flight, B1 inflight.tsv consumption, `.gitignore` guidance, T3–T5, plus two live-run
> findings from the 2026-06-06 first cycle: (i) **orphaned inflight.tsv rule** — while
> `chain.running`, on-duty must only trust a tsv whose mtime is later than its own chain
> spawn time; older orphans (a dead session's dispatcher) are ignored with a WARN — the
> first live run found `20260605T144344Z-71639.inflight.tsv` with no matching report and
> the agent had to improvise a one-cycle resolver deferral; (ii) **digest counter fix** —
> ci-red items must not also be counted as `needs-human` (the first digest reported the
> same 3 PRs under both counters; the categories are disjoint by construction since
> needs-human can only come from resolver exit shapes). In standalone Phase A use, the
> skip-set has no caller — the label-based secondary guard (step 2) is the only
> in-flight protection; avoid running `--batch` while a dispatcher batch is active.

Integration work the specs DEPEND ON but which does not exist yet (rescoped per the
round-3 pre-construction review):

- [ ] **(Phase A) `resolve-pr-comments` `--auto` mode.** The strategy-table HITL gate gets a bypass
      (auto-approve, capture the table in the report) without changing default behavior.
      Locked decisions in the SKILL header stay intact. Under `--auto` additionally:
      suppress DEFER promise-replies (D16), and treat a build-sanity (5b) failure as
      terminal `needs-human: comment-fix-failed-tests` — push NOTHING (not even the
      rebase commit), leave the worktree dirty for inspection, surface the state in the
      report (M3 — `--auto` does NOT bypass this second abort, by design).
- [ ] **(Phase A) `resolve-pr-comments` push step**: gains `--force-with-lease` support for the
      callee path — per D14 the SKILL owns the single push when the comments stage runs
      (`/ggx-pr-resolver` step 7). Default standalone behavior unchanged.
- [ ] **(Phase A) Resolver rebase hybrid (NEW — B2).** Neither /resolve-conflict mode fits: single
      is HITL (AskUserQuestion deadlocks an unattended agent), batch pushes. Build the
      hybrid (flagged /resolve-conflict variant, or inline in /ggx-pr-resolver): rebase
      onto the PR's **`baseRef`** (M5 — single mode hardcodes origin/trunk), batch-style
      non-interactive auto-abort on unresolvable/uncertain conflicts, NO push. Worktree
      primitive is batch-B3 (raw `git worktree add <path> <headRefName>`, reuse-or-create,
      fork via `gh pr checkout`, non-interactive dirty guard) — NOT `/add-worktree`.
      Reused worktrees MUST `git fetch origin <headRefName>` + reset before rebasing
      (M4 — stale lease silently clobbers otherwise).
- [ ] ~~**`code-review` inline-PR-comment capability (NEW scope — M2)**: parse findings →
      map to diff line/side → batch ONE `event=COMMENT` review via `gh api`; handle
      out-of-hunk lines. `--auto` today FORBIDS comment posting (`/dev:review` consumes
      the report) — add an explicit opt-in flag for on-duty that does not regress
      `/dev:review`. Severity mapping uses code-review's REAL buckets:
      Critical+Improvements → inline, Minor → digest, Positive → drop ("major"/"nit"
      don't exist). Diff base: the remote path is already server-side `baseRef`; no
      change needed unless SHA-pinning is added later (rejected for now, D17).~~
      **DROPPED — D6 reversed 2026-06-06.**
- [ ] **(Phase B) `_slack-notify` on-duty source (full third source — M1, not a table row)**: new
      Shapes entry, PR-keyed Input grammar (existing two sources are ticket-keyed), new
      mapping rows, new render branch, caller-registry entry. Analyzer/dispatcher
      built-in digests stay untouched (D17 — duplication accepted). Fallback remains
      `.ggx-on-duty/digest.md` (durable even with no Slack config).
- [ ] **(Phase B) Dispatcher early in-flight file (B1+B3)**: after the §4.1 lock, before §5.3
      spawn, write `claude-reports/dispatcher/<RUN_TS>-<PID>.inflight.tsv` with
      `ticket \t headRefName \t worktree` (roster must expose headRefName). On-duty's
      skip-set reads the newest one by mtime while `chain.running`; the §6.4 final
      report supersedes it.
- [x] **install.sh** — verified during review: the existing glob already covers the two
      new command files; `resolve-pr-comments` skill changes flow through automatically
      (symlinked directory). No change needed.
- [ ] **(Phase B) On-duty pre-flight (NEW — M7)**: the dispatcher pre-flight reuse is a SUBSET
      (worktree / branch / clean / gh auth; the lockfile step is explicitly EXCLUDED —
      on-duty never touches the dispatcher lock). The claimed "Linear auth" check does
      not exist in the dispatcher — add on-duty's own Linear MCP probe (one
      `list_teams`) plus fail-fast `--team` validation.
- [ ] **(Phase B)** `.gitignore` guidance: `.ggx-on-duty/` must be ignored in TARGET repos — document
      in ggx-on-duty.md prerequisites (the command also self-ensures at runtime).
- [ ] **(split)** Minor appendix m1–m22 of the review report, applied during construction —
      resolver-side minors in Phase A, on-duty-side in Phase B
      (m1 — TaskList-only liveness — is mandatory for Phase B, not optional: double-spawn risk).

Verification sequence (in a real repo, e.g. gogox-client-flutter):

- [ ] **(Phase A)** T1 `/ggx-pr-resolver <PR#>` (no `--auto`) on a PR with comments — HITL table shows,
      single push, one CI run.
- [ ] **(Phase A)** T2 `/ggx-pr-resolver --batch --user=@me --auto` — no-op gate skips clean PRs;
      judge HOLDs bot threads; report shape correct.
- [ ] **(Phase B)** T3 `/ggx-on-duty --no-dispatch` for a few cycles — CI notify dedup, DEGRADED guard
      (simulate gh-scoped: unset GH_TOKEN / point gh at a bad host for one poll, and
      separately stub a non-empty→empty transition — covers both trigger branches),
      state resume after restart (running flags reset).
- [ ] **(Phase B)** T4 Full `/ggx-on-duty` with one `ready-to-dev` ticket staged — chain runs
      analyze→dispatch, resolver skips the chain's branch (E-3).
- [ ] **(Phase B)** T5 Kill the session mid-chain, restart — no stuck `running`, no double dispatch.

Rollout: prototype stays user-level (`~/.claude/commands/`) during T1–T5; promote into
this repo's `commands/dev/` (this branch) once the tick behavior stabilizes; colleagues
get it via install.sh after merge.
