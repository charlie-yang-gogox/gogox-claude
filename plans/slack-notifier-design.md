# Slack Notifier — Design Discussion

> **Status (2026-06-05)**: design discussion complete (4 design lenses + 3 adversarial critiques + synthesis). The §8 open questions were decided by Charlie as follows:
>
> | # | Question | Decision |
> |---|---|---|
> | Q1 | Channel structure | **Single `#ggx-pipeline`**, CAF/CET distinguished by ticket-id prefix (channel id provided at implementation time) |
> | Q3 | REVIEW volume | **Digest line only**, no individual ping |
> | Q4 | Liveness pulse | **Yes**, once daily, `chat.update` editing the same pinned message in place |
> | Q6 | Dedup ledger | **No dedup (final, reversed 2026-06-05)**. Charlie was explicit: stuck tickets SHOULD be re-announced every run, as a standing reminder. → sidecar / ticket marker both cancelled; the §5.2 send-on-change-only rule is abolished; the digest re-lists every needs-human ticket each run. The BLOCKED "notify only on first entry" self-healing rule is cancelled along with it. The only remaining silence: empty-sweep no-ops and mid-pipeline progress. This decision also eliminates the "cloud sidecar doesn't survive sessions" future debt — the cloud-enablement PR no longer needs to revisit the ledger. |
> | Q2 | Sender identity | Not decided then — default to the existing Slack bot identity |
> | Q5 | Cloud enablement | Not decided then — default to the phasing in this doc: v1 local notifications, cloud graceful no-op, cloud enablement as a separate PR |

## 1. Core Design Decisions

All decisions below are final. Each notes WHY it was adopted and which critique killed the rejected alternative.

### 1.1 Transport: MCP locally, Bash curl in the cloud — and "can the cloud send at all" must be proven first

*[Superseded by §9.1: local transport was later switched to bot-token curl as well, per Charlie's explicit requirement that messages post under his existing bot identity.]*

- **Local (interactive / local dispatcher)**: use `mcp__claude_ai_Slack__slack_send_message`. This is the repo's established convention (every skill uses MCP tools only; grepping the repo finds no raw curl / webhook / bot token anywhere).
- **Cloud (headless routine)**: the only viable path is `Bash` curl to `chat.postMessage`. Verified: both `routine.json` files list `allowed_tools = [Bash, Read, Write, Edit, Glob, Grep]` and `mcp_connections` contains only Linear — the Slack MCP simply does not exist in the cloud.
- **Why curl was not made the local default**: Proposal 3 argued "use bot-token curl locally too, for uniform behavior" — rejected by the MAINTAINABILITY critique: it would introduce a long-lived-secret management surface the repo has never had, plus a second transport path, while local already has the interactively-authorized Slack MCP. curl was to be the exception only where MCP is absent (i.e. the cloud).
- **Key unproven premise**: the FAILURE-MODES critique pointed out there is no known mechanism to inject a custom secret (`GGX_SLACK_BOT_TOKEN`) into the CCR cloud sandbox — `GH_TOKEN` is injected exclusively by the GitHub integration, and routine.json has no env block. **Therefore v1 ships local notifications only; the cloud stays a graceful no-op**; cloud Slack delivery is a separate, to-be-proven PR (see §8 Q5). Until secret injection is empirically verified, do not claim cloud Slack works.

### 1.2 Where the helper lives: a single markdown helper-doc `commands/dev/_slack-notify.md`

Exactly the `_ticket-init.md` shape: underscore prefix (internal-only), versioned marker, fail-soft single-WARN, a "Callers (current)" footer, and the "reference, never re-inline" rule. Command files install automatically — **zero install.sh changes**. All four proposals agreed here, and it matches the verified repo convention.

It exposes **one conceptual entry point**: callers pass "raw signal + ticket-id", and the helper's single internal table does the `signal → status → emoji` mapping — **callers never pick a status**. The MAINTAINABILITY critique rejected Proposal 2's "8 statuses × 6 call sites each deciding for themselves" design (it would drift every time someone edits a pipeline — exactly the inlining this repo forbids). The mapping is a chokepoint, not a per-caller obligation.

### 1.3 Channel topology: one `#ggx-pipeline` channel + run-level digests + flat posts (no thread-per-ticket, no Canvas in v1)

- **Single channel**: volume is low (analyzer 2×/day, dev-agent 2×/day), and only a single channel delivers "know the whole pipeline state from one place in Slack" (requirement 3). Filtering relies on the `#needs-human` hashtag saved-search at the end of each line, not on splitting channels.
- **No thread-per-ticket (v1)**: Proposals 3/4 stored `thread_ts` as a `<!-- slack-thread:v1 ts=... -->` marker comment on the Linear/Jira ticket to restore threads across stateless restarts. Rejected by BOTH the MAINTAINABILITY and FAILURE-MODES critiques: (a) writing a Slack transport handle into the tracker is a layering violation, pollutes the human-facing ticket, and forces `_ticket-lib` to grow Slack obligations; (b) the cloud cannot read Slack (`slack_search` is also MCP, absent in the cloud), so it would always fall back to a new root — thread-per-ticket silently degrades to post-per-event on exactly the unattended path that matters most; (c) the Gap-A race (cloud dev-agent overlapping a local dispatcher) double-writes the marker and produces twin threads.
  → **v1 adopts the run-level digest model**: the only correlation key needed is "this run", which is resolved in-process — no cross-invocation ts restoration required.
- **No Canvas (v1)**: Proposal 4's pinned Canvas was unanimously rejected by all three critiques — it is MCP-only (the cloud can't write it, and the cloud is the main producer, so the "single pane" would be stalest exactly when freshness matters), requires a paid Slack plan, and needs multi-writer concurrent `slack_update_canvas` + cached section_ids (the repo has no shared-mutable-state precedent at all). A channel + saved-search already satisfies requirement 3.

### 1.4 Notification policy: always-send (terminal-only), no-op fires fully silent 【revised 2026-06-05】

- ~~send-on-CHANGE-only~~ → **always-send**: Charlie decided "stuck tickets should be re-announced every run, as a standing reminder." The critiques had flagged always-send as a mute-channel risk; the user is informed and chose it deliberately. The digest re-lists all current needs-human tickets every run (a ticket stuck at `need-spec-review` being re-announced twice a day = desired behavior).
- Bonus of this revision: the entire dedup machinery (sidecar / ticket marker) is cancelled, the implementation gets simpler, and stateless cloud re-runs stop being a problem.
- No-op / zero-candidate fires remain **fully silent** (all four proposals agreed). Verified actual cadence is 2×/day (not the hourly assumed in the brief), and most fires are empty sweeps. Liveness is carried by the daily pulse.

### 1.5 Send origin: parent / orchestrator sessions only — ggx-work never touches the channel root

The ANNOYANCE critique showed Proposal 1's `GGX_NOTIFY_SUPPRESS` env had to be threaded and checked correctly at every spawn and every hook site — miss one and you get N duplicates. The FAILURE-MODES critique offered the cleaner scheme that was adopted:

- **ggx-work never posts to the channel root**; only when no run-level rollup exists (standalone, or cloud sequential) does it post its own ticket's terminal line under the digest model.
- **The dispatcher parent owns the batch digest and the channel-root needs-human lines**, derived at §6.2 (the only race-free authoritative classification point).
- Result: standalone ggx-work and cloud-sequential ggx-work behave identically, and the dispatcher merely layers a rollup on top — **no suppress flag, no missable site**. Three ownership regimes collapse into "one ggx-work behavior + one dispatcher behavior."

---

## 2. Unified Status Taxonomy

Design principle: **three tiers** — every `needs_human` line carries the `#needs-human` hashtag (the client-reliable, load-bearing filter key; emoji are only at-a-glance visual hints, because Slack emoji search is unreliable). Reuse the dispatcher's existing 🟢/🟡/🔴 primary colors and refine with text-token suffixes — **no separate emoji legend** (the MAINTAINABILITY critique rejected Proposal 2's legend fork: it would force edits to the dispatcher's Step 6.5 stdout table and make one run maintain two legends).

Fixed message grammar *(format v1)*: `<emoji> [TOKEN] <ticket-link> · <lane> — <summary> (next: <action>) #ggx-<token> [#needs-human]`

> **Format v2 (2026-06-05, after a live preview with real Linear data)**: Charlie picked **Block Kit** over the flat single-line grammar — header block + counts section + divider + a "Needs your action (N)" section of **two-line items** (line 1: emoji + bold ticket link + **title truncated to 60 chars**; line 2: `↳ status: summary — next action`) + a context block holding the hashtags **once per message** (Slack search matches at message granularity, so per-line tags were pure noise) + a one-line `text` fallback for mobile notification previews. The token/emoji taxonomy and `#needs-human` semantics in the table below are unchanged; only the rendering moved. The authoritative rendering spec lives in `_slack-notify.md` ("Rendering — Block Kit"); the §4 examples below show the v1 flat rendering and are kept for the historical record.

| status (token) | emoji | needs_human | Trigger (subsystem / event) | Jira | Example message |
|---|---|---|---|---|---|
| `READY` | ✅ | false | ticket-analyzer verdict ready-to-port / ready-to-dev (counted in the digest, never pinged individually) | yes (degraded: `ticket-analysis-ready` string label maps here) | `✅ [READY] CAF-1310 · feature — complete + unblocked (next: dispatcher picks it up automatically) #ggx-ready` |
| `REVIEW` | 🟢 | true | ggx-work / dispatcher outcome=done (draft PR, In Review). Soft needs-human = reviewer | yes | `🟢 [REVIEW] CET-842 · bug — draft PR open, In Review (next: review PR #842) #ggx-review #needs-human` |
| `SPEC-REVIEW` | 🟡 | true | ggx-work Step 4.4a/4.2 port-paused (need-spec-review HITL) | **n/a (Jira has no port lane / no spec-review gate)** | `🟡 [SPEC-REVIEW] CAF-1260 · port — port complete, paused at spec-review gate (next: run /spec-review CAF-1260) #ggx-spec-review #needs-human` |
| `NEEDS-REVISION` | 🟠 | true | ticket-analyzer verdict incomplete (need-revision + reasoned comment) | yes (degraded: `ticket-analysis-need-revision`) | `🟠 [NEEDS-REVISION] CAF-1234 · bug — incomplete: missing repro steps, AC (next: edit the ticket; next sweep re-scans automatically) #ggx-needs-revision #needs-human` |
| `BLOCKED` | 🟣 | true | ticket-analyzer verdict complete-but-blocked (need-dependency). ~~Self-healing: sent only on first entry~~ *[cancelled with the no-dedup decision — re-listed every run]* | yes (degraded: `ticket-analysis-need-dependency`) | `🟣 [BLOCKED] CAF-1240 · port — blocked by CAF-1200 (open) (next: close the blocker; next sweep unblocks automatically) #ggx-blocked #needs-human` |
| `CLASSIFY` | ❓ | true | ggx-work /route UNKNOWN_LANE (missing bug/port/feature classification) | yes | `❓ [CLASSIFY] CET-567 · ? — no lane classification (next: add a bug \| port \| feature label) #ggx-classify #needs-human` |
| `CYCLE` | 🔁 | true | ticket-analyzer Step 5.2 dependency cycle | n/a (workflow-label cycle detection is Linear-only) | `🔁 [CYCLE] CAF-1251 ↔ CAF-1252 — dependency cycle, both excluded from ordering (next: break the cycle manually) #ggx-cycle #needs-human` |
| `FAILED` | 🔴 | true | ggx-work Step 4.3 (all failure classes converge: pipeline-failed / route / loop-cap) + Step 2 pre-flight hard-stop; dispatcher §6.2 failed/orphan/ambiguous; ticket-analyzer errored (write failure) | yes | `🔴 [FAILED] CAF-1272 · dev — pipeline-failed at /dev:ff (test stage) (next: see claude-reports/CAF-1272/report.md, re-run /ggx-work) #ggx-failed #needs-human` |
| `BATCH-ABORT` | ⛔ | true | dispatcher Step 4.2 mid-lock MCP failure / partial lock | yes | `⛔ [BATCH-ABORT] dispatcher (CAF) — Linear MCP failed mid-lock (next: manually unlock CAF-1280, CAF-1281) #ggx-batch-abort #needs-human` |
| `DIGEST` | 📊 | false | run-level rollup from ticket-analyzer Step 10 / dispatcher Step 6.5 / routine Phase 3-4 | yes | see §4 |

> Jira parity (required by the FAILURE-MODES critique): Jira (CET/DET) has no port lane and no spec-review gate; the analyzer runs in degraded mode (`fields.labels` string labels). The mapping table must translate the degraded string labels into the same canonical tokens; `SPEC-REVIEW` / `CYCLE` (workflow-label based) are marked **n/a** for Jira. Jira tickets can only terminate as `READY` / `REVIEW` / `NEEDS-REVISION` / `BLOCKED` / `CLASSIFY` / `FAILED`.

> **Requirement 4 deliverable**: one saved search for `#needs-human` lists everything needing human action. `READY` / `DIGEST` don't carry the tag, so they separate naturally.

---

## 3. Hook Points per Integration

General rule: each subsystem has **exactly one run-level send point** (the MAINTAINABILITY critique's maintainable chokepoint — one place for the cloud transport override, one place for fail-soft, and a future ui-tweak only needs to reference the helper from its own rollup). Individual needs-human tickets are embedded as **lines inside that one digest** — never a separate top-level per-ticket message (the ANNOYANCE critique rejected Proposal 2's digest + per-ticket double-send: seeing the same thing twice in the same second).

### 3.1 ticket-analyzer (`/ticket-analyze` + `ticket-analyzer-agent`)

- **Hook**: `commands/dev/ticket-analyze.md` Step 10 (after `Summary: X analyzed, Y skipped, Z errored.` is computed) → send **1 `DIGEST`**. Cloud mirror: `cloud-routines/ticket-analyzer-agent.routine.json` Phase 3 report block (curl path; the §4 'No Slack' scope note needs relaxing — separate PR).
- **Digest contains**: per-verdict tallies + one line per needs-human ticket (`NEEDS-REVISION` / `CYCLE` / errored → `FAILED`); `READY` is counted only; ~~`BLOCKED` lines only on first entry~~ *[superseded: re-listed every run]*.
- **Suppressed**: zero-candidate no-op fires (fully silent); per-ticket fetched / lane-derived progress (never sent); Jira degraded-mode notice (at most one footnote line inside the digest).
- **Noise math**: quiet day **0 messages**; active day **1 per fire × 2 fires = 2/day**.

### 3.2 ggx-work (single-ticket orchestrator)

*[v1 NOTE: this whole subsection was descoped by the §9.4 simplification — ggx-work sends nothing in v1. Kept as the design for a possible future per-ticket expansion.]*

- **Never touches the channel root** (§1.5). Only when no run-level rollup exists (standalone invocation, or cloud sequential) does it post its own ticket's terminal line under the digest model.
- **Hooks (terminal branches; all read authoritative state, never parse the cosmetic `[ggx-work-result]` line)**:
  - Step 4.1 done → `REVIEW` (with PR URL)
  - Step 4.4a / 4.2 port-paused → `SPEC-REVIEW`
  - Step 4.3 failed / unknown-lane → `FAILED` / `CLASSIFY`
  - Step 2 pre-flight hard-stop → `FAILED`
- ~~Dedup marker~~ **【abolished】**: terminal lines send directly; re-runs/resumes re-announcing the same state is the deliberate reminder behavior.
- **Suppressed**: Step 2.5 started, Step 4.4 pipeline-launch and other progress (not sent in the v1 flat model; keeps the channel clean).
- **Under the dispatcher**: fully silent (the parent's §6.2 owns reporting). Since ggx-work never touches the root, this holds naturally — no suppress env needed.
- **Noise math**: standalone interactive runs — **at most 1 terminal line per ticket**; under the dispatcher — **0** (folded into the dispatcher digest).

### 3.3 ggx-dispatcher (local batch) + ggx-dev-agent (cloud)

- **Send from the parent session only, never from the N fanned-out background agents** (verified: the dispatcher keeps all classification in the parent for exactly this reason).
- **Hooks**:
  - dispatcher Step 6.5 (6-column table built, race-free) → send **1 `DIGEST`** (batch summary), needs-human lines at the top.
  - dispatcher Step 4.2 batch-abort → `BATCH-ABORT` (must surface even if nothing was ever spawned; may send in a turn before any spawn message).
  - cloud ggx-dev-agent Phase 4 → curl digest (covers the no-op fire check and per-ticket outcomes). *(separate PR)*
- **Strictly forbidden** to insert any send between the Step 4.3 dispatch table and the N Agent spawns. The FAILURE-MODES critique proved it: an MCP send is a tool call and forces a turn boundary, breaking the hard constraint that "table + N spawns emit in ONE message" — "fire-and-forget" does not exist in this harness. The batch-start ping is therefore **cancelled** (the Step 6.5 digest covers it).
- **Authoritativeness**: every status keys off the §6.2-derived outcome + Flags — **never** the cosmetic `[joined/N]` or `[ggx-work-result]` lines.
- **Suppressed**: all skips (PR-exists / branch-exists / duplicate / concurrent-lock) → only the `skipped: N` count inside the digest, no individual lines.
- **Noise math**: **1 digest per batch** (+ the rare batch-abort).

### 3.4 Global noise math (aligned to the actual 2×/day cadence, not hourly)

- Cloud: analyzer 14 fires/week + dev-agent 14 fires/week = **28 fires/week**, most of them silent no-ops.
- Estimate: a quiet week approaches **0**; an active day **2–6 messages**; ~~stuck tickets are not re-announced thanks to send-on-change-only~~ *[superseded by the no-dedup decision: stuck tickets ARE re-announced each run — deliberately]*.

---

## 4. Example Messages

**(A) ticket-analyzer sweep digest (Step 10)**

```
📊 [DIGEST] ticket-analyzer · CAF team — 6 analyzed (3 ready, 2 need-revision, 1 blocked, 0 errored)
Best start: CAF-1310
🟠 [NEEDS-REVISION] CAF-1234 · bug — missing repro steps, AC (next: edit the ticket) #ggx-needs-revision #needs-human
🟠 [NEEDS-REVISION] CAF-1239 · feature — missing Figma, scope (next: edit the ticket) #ggx-needs-revision #needs-human
🟣 [BLOCKED] CAF-1240 · port — blocked by CAF-1200 (open) (next: close the blocker) #ggx-blocked #needs-human
ready: CAF-1310, CAF-1312, CAF-1315 · skipped: 0
#ggx-digest
```

**(B) ggx-work HITL stop (standalone, Step 4.4a port-paused)** *(descoped in v1 — see §9.4)*

```
🟡 [SPEC-REVIEW] CAF-1260 · port — port complete, paused at spec-review gate; feat/CAF-1260 pushed, no PR
(next: run /spec-review CAF-1260; PRD posted on the ticket)
#ggx-spec-review #needs-human
```

**(C) ggx-work failure (standalone, Step 4.3)** *(descoped in v1 — see §9.4)*

```
🔴 [FAILED] CAF-1272 · dev — pipeline-failed at /dev:ff (test stage, iter 2); stuck at In Progress, no PR
(next: see claude-reports/CAF-1272/report.md, fix and re-run /ggx-work CAF-1272)
#ggx-failed #needs-human
```

**(D) dispatcher batch summary (Step 6.5)**

```
📊 [DIGEST] ggx-dispatcher · CAF team — 5 processed (2 done, 1 spec-review, 2 failed) · skipped 1 (PR-exists)
🔴 [FAILED] CAF-1272 · dev — pipeline-failed at /dev:ff (next: re-run /ggx-work CAF-1272) #ggx-failed #needs-human
🔴 [FAILED] CAF-1280 · bug — outcome-derivation-ambiguous (next: manually reconcile labels vs PR) #ggx-failed #needs-human
🟡 [SPEC-REVIEW] CAF-1260 · port — paused at spec-review gate (next: run /spec-review CAF-1260) #ggx-spec-review #needs-human
🟢 [REVIEW] CAF-1310 · feature — draft PR #501 open, In Review #ggx-review #needs-human
🟢 [REVIEW] CET-842 · bug — draft PR #842 open, In Review #ggx-review #needs-human
#ggx-digest
```

---

## 5. Anti-Noise Mechanisms

1. ~~send-on-CHANGE-only~~ **【abolished 2026-06-05 by Charlie's decision】**: the original design notified only on state flips; Charlie explicitly wants stuck tickets **re-announced every run** as a standing reminder. The digest re-lists all needs-human tickets each run (need-revision / blocked / spec-review…) — deliberate behavior, not a bug. The mute-channel risk the critiques flagged is knowingly accepted by the user. If it ever gets too noisy, this rule can be reintroduced without architectural change (the digest model is unaffected).
2. ~~Dedup ledger~~ **【abolished】**: no sidecar / ticket marker of any kind. The digest is a run-level rebuild (re-derived from current tracker state each run), naturally idempotent as a "snapshot of now" — a re-run/resume re-sending the same snapshot is exactly the desired behavior.
3. **No-op full silence**: zero-candidate / empty sweeps send nothing and emit no per-fire heartbeat.
4. ~~need-dependency self-healing (first-entry-only notification)~~ **【abolished along with the no-dedup decision】**: blocked tickets are re-listed in every digest until the blocker closes and they flip to ready.
5. **Run-level batching**: one sweep / batch collapses into **1 digest**; needs-human items are embedded as lines — **no** separate per-ticket top-level posts (avoids double-sends).
6. **Single surface per item per run**: every actionable item appears in exactly one place (its digest line), never digest + individual post side by side.
7. **`#needs-human` saved-search**: turns requirement 4 into one fixed search; emoji are visual support only.
8. **Rate-limit protection**: cap = 1 digest per batch (multi-line single message), avoiding chat.postMessage's ~1 msg/sec/channel 429s; on 429 respect Retry-After with ONE bounded sleep, then drop — **never** retry-storm.
9. **`--dry-run` fully silent** (verified: dispatcher --dry-run is read-only end-to-end; ticket-analyze has --dry-run too).

---

## 6. Failure Isolation

1. **Fail-silent contract (copied verbatim from `_ticket-init`)**: every send is wrapped as `... || echo 'WARN: /_slack-notify: send failed for <id> — continuing.' >&2`. A Slack failure **never** changes exit codes, **never** blocks the pipeline, **never** aborts a batch. Only the pipeline's own initial ticket read may hard-stop.
2. **Default-OFF kill switch**: with no opt-in config the helper no-ops immediately. Installing the helper before configuring is a **zero-risk no-op**. A single enable flag + a single channel id are the only config knobs (the MAINTAINABILITY critique rejected Proposal 3's four-knob config sprawl). *[Implementation note: the original `GGX_SLACK_ENABLE` env became the `enabled` field of the config file — see §9.]*
3. **Headless cloud handling**:
   - The Slack MCP is **proven absent** in the cloud; when the helper detects no Slack tool / token it silently no-ops (same pattern as Jira missing-fields).
   - The cloud's only path is Bash curl, but the secret-injection mechanism is **unproven** (§1.1). v1 ships the local path; the cloud stays a graceful no-op. The re-permissioning of the two routine.json files + the scope-note relaxation are a **separate, explicitly labeled** PR, gated on proving secret injection (not bundled into v1's invasive edits).
4. **Concurrency**:
   - Send from parent sessions only (§1.5); the dispatcher's N fanned-out background agents never send → no interleaving, no duplicates.
   - Never insert a send between the dispatch table and the N spawns (breaks the single-message spawn constraint).
   - Gap-A race (cloud dev-agent and local dispatcher grabbing the same ticket): since v1 uses run-level digests (no per-ticket thread roots), no twin threads can open. ~~The dedup ledger further guarantees no same-state re-announcement~~ *[ledger abolished — same-state re-announcement is now deliberate]*.
5. **Authoritativeness**: every classification keys off authoritative state (analyzer labels / dispatcher §6.2 outcome+Flags / ggx-work Step 4.x branches) — **never** parsed from cosmetic lines.

---

## 7. Rejected Alternatives

- **Proposal 4 — pinned Slack Canvas as the single pane**: unanimously rejected by all three critiques. MCP-only (the cloud can't write it, so it's stalest exactly when freshness matters), requires a paid Slack plan, and multi-writer concurrent `slack_update_canvas` + cached section_ids is shared-mutable-state the repo has never had.
- **Proposals 3/4 — thread_ts stored as a ticket marker comment (thread-per-ticket)**: rejected by MAINTAINABILITY + FAILURE-MODES. Layering violation polluting the ticket; the cloud can't read Slack so it must fall back to post-per-event; the Gap-A race produces twin threads. v1 uses run-level digests instead.
- **Proposals 2/3/4 — unconditional `always-send` for terminal events**: rejected by ANNOYANCE + FAILURE-MODES as the mute-channel path; changed to send-on-change-only. *[Later reversed: Charlie deliberately chose always-send — see §1.4. The historical rejection is kept for the reasoning record.]*
- **Proposal 2 — digest + per-ticket double-send + an 8-emoji legend fork**: rejected by ANNOYANCE + MAINTAINABILITY. The same item appears twice within a second; forking the legend forces edits to the dispatcher stdout table and makes one run maintain two legends. Changed to single-surface + reuse of the existing 🟢/🟡/🔴 + text-token refinement.
- **Proposal 3 — bot-token curl as the local default**: rejected by MAINTAINABILITY at the time (introduces a long-lived-secret surface and a second transport path the repo never had; local already has the Slack MCP). *[Later reversed by Charlie's explicit requirement — see §9.1: messages must post under HIS bot identity, so local uses bot-token curl after all. The secret stays out of git.]*
- **Proposal 1 — no liveness signal at all**: challenged by FAILURE-MODES (minor). Total silence makes "the routine died" indistinguishable from "quiet but healthy", violating requirement 3. Mitigation: §8 Q4 (one daily, low-key chat.update-in-place liveness line).
- **Batch-start ping (Proposals 1/4)**: rejected by FAILURE-MODES. An MCP send forces a turn boundary and breaks the single-message spawn constraint; "fire-and-forget" does not exist in this harness. Cancelled.
- **`GGX_NOTIFY_SUPPRESS` env threaded through spawns (Proposal 1)**: replaced by FAILURE-MODES' cleaner scheme — "ggx-work never touches the channel root" eliminates both the suppress flag and the missable-site risk.

---

## 8. Open Questions (as posed to the user; decisions recorded in the Status header)

1. **Target channel**: confirm a single `#ggx-pipeline` (recommended) covering CAF (Linear) and CET (Jira), distinguished by ticket-id prefix? Or per-team channels (`#ggx-pipeline-caf` / `-cet`)? Please provide the channel id.
2. **Sender identity**: post as your existing Slack bot? The helper should take the channel from config, never hardcode it.
3. **`REVIEW` (draft PR / In Review) volume**: v1 puts it in a digest line (with `#needs-human`, since a reviewer is needed), **not** an individual broadcast. Acceptable? (Say so if you want every done pinged separately and more prominently.)
4. **Liveness pulse**: one **daily** (not per-fire) low-key line — "pipeline alive, last sweep HH:MM, nothing actionable" (recommended: chat.update editing the same pinned message; reachable from cloud curl)? Or accept fully-silent no-op fires with liveness left to routine logs? (Recommended: yes, ~7 messages/week.)
5. **Cloud Slack enablement**: allow a **separate PR** to (a) prove whether the CCR sandbox can inject a custom secret env (`GGX_SLACK_BOT_TOKEN`), (b) relax the `ticket-analyzer-agent` "No Slack" scope note, (c) add curl sends to both routine.json files? Until proven, v1 stays cloud-Slack-silent with local dispatcher / analyze notifications only — is this phasing acceptable?
6. **Dedup ledger marker**: accept a `<!-- ggx-slack:v1 status=... sent=... -->` **notification-state** marker comment on the ticket (not a thread_ts, not a transport handle — just "has this state been announced")? Or prefer never writing the tracker and using a local sidecar JSONL (which doesn't survive cloud sessions)? *(Final answer: NEITHER — Charlie chose no dedup at all; see the Status header.)*

---

## 9. Implementation Plan (v1, local) 【IMPLEMENTED ✅ 2026-06-05 — landed per the §9.4 simplified scope: `_slack-notify.md` added; `ticket-analyze.md` Step 10.1; `ggx-dispatcher.md` §4.2/§6.5/Guardrail. Gate logic (G1/G2a/G2/G3) bash-tested, every path exits 0】

> **§9.1 location revision (2026-06-05, Charlie: "it should live inside this repo")**: the config moved from `~/.claude/ggx-slack.json` to **`commands/dev/profiles/ggx-slack.json` (in-repo, same directory and pattern as org.yaml)** — the repo's existing convention is precisely that profiles live in the repo and `install.sh` **symlinks** them into `~/.claude/commands/profiles/` for fixed-path reads from any cwd (symlink = edits in the repo take effect immediately). The original home-dir design was actually the off-convention one. The token still must never be committed: the real file is in `.gitignore` (committing would leak the token AND force one user's config onto every installer, breaking the opt-in foolproofing). *(An `.example` template was initially added, then deliberately deleted by Charlie — the schema in `_slack-notify.md` is the reference.)* A fresh clone has no real file → no symlink → G1 silent no-op, so the foolproofing semantics are unchanged. The helper reads the fixed deployed path `$HOME/.claude/commands/profiles/ggx-slack.json`. The skeleton was created (enabled:false), the symlink created manually, and after Charlie filled in channel_id + bot_token a live test message posted successfully (bot "Planner agent", `ok:true`).

New requirements from Charlie: (a) **foolproofing** — the repo is a shared multi-user install; users without a Slack bot must be completely unaffected; (b) **the Slack bot config needs a durable home**.

### 9.1 Config file + transport revision ✅ (2026-06-05, confirmed by Charlie: bot token + curl, plaintext chmod 600)

**Location**: *[original: `~/.claude/ggx-slack.json`, per-user home dir — superseded by the in-repo location in the revision note above]*.

- Why not in the repo *(original reasoning)*: the repo is shared via `install.sh`; any config in the repo becomes everyone's default, violating foolproofing; and channel/token are personal. Precedent: `~/.claude/monthly-summary-config.json` (monthly-summary skill). *(The in-repo + gitignore + symlink scheme later satisfied both this concern and Charlie's discoverability requirement.)*
- The home dir also naturally dodges the worktree problem (ggx-work worktrees get deleted; home is unaffected). *(The deployed symlink path retains this property.)*

**Schema v1** (minimal, anti-config-sprawl):

```json
{
  "version": 1,
  "enabled": true,
  "channel_id": "C0XXXXXXXXX",
  "bot_token": "xoxb-...",
  "liveness_message_ts": ""
}
```

File `chmod 600`. `liveness_message_ts` is reserved for the v1.1 daily pulse (needed by chat.update).

**⚠️ Transport revision (overturns §1.1's "MCP locally")**: Charlie explicitly said "I already have a Slack bot — integrate with it" and "the bot config needs a durable home" — **messages sent via the Slack MCP do NOT post under his bot's identity** (they post under the claude.ai Slack integration's authorized identity). Therefore:

- **Revision**: local AND cloud uniformly use `Bash curl https://slack.com/api/chat.postMessage` authenticated with `bot_token` → messages post under **his existing bot**, and there is a single transport path (the future cloud PR needs no second one).
- §1.1's original MAINTAINABILITY rejection ("the repo has no secret-management surface") is overridden by the user's requirement; the secret stays out of the repo's git history (gitignored, chmod 600 — same class as `~/.netrc`, `gh hosts.yml`). macOS Keychain remains a future hardening option; not in v1.
- Bonus: curl is not an MCP tool call → the dispatcher's "no send between table and spawns" turn-boundary concern technically disappears (the §6.5-only design is kept anyway — the digest model itself is the reason).

### 9.2 Foolproofing gate chain (helper Step 0, short-circuit in order, all exit 0)

| Gate | Condition | Behavior |
|---|---|---|
| G1 | config file absent | **Fully silent no-op.** One stdout audit line `slack-notify: disabled (no config)`, no WARN. → the default experience for every other user |
| G2a | file exists but is not valid JSON | One WARN (a config exists = the user opted in; a corrupted file must not be silent), no-op |
| G2 | `enabled != true` | Same silence as G1 (`disabled (enabled != true)`) |
| G3 | `channel_id` or `bot_token` empty | One WARN (the user opted in but misconfigured — they need to know), no-op |
| G4 | curl failure / non-2xx / `ok:false` | One WARN (with the Slack error code), no-op. 429 → respect Retry-After once, then drop; never retry-storm |

Foolproofing semantics: G1/G2 use an audit line rather than zero output — "why didn't I get a notification" stays debuggable, but it's not a WARN and won't alarm users who never opted in. No gate ever affects the pipeline's exit code.

### 9.3 Helper interface (`commands/dev/_slack-notify.md`, modeled on `_ticket-init.md`) 【simplified 2026-06-05】

Two call shapes; callers always pass **raw signals** and never pick a status (the mapping is the helper's single internal table):

- `/_slack-notify digest <source>` + per-ticket signal lines
  source ∈ `ticket-analyzer` (lines: `ready` / `need-revision reasons=<..>` / `need-dependency blockers=<..>` / `cycle ids=<..>` / `errored`) | `ggx-dispatcher` (lines from the §6.2 authoritative outcome + Flags: `done flags=In-Review pr=<url>` / `port-paused flags=need-spec-review` / `failed flags=in-flight-residue stage=<s>`)
- `/_slack-notify batch-abort detail=<...>` — dispatcher §4.2 only (batch-level event, no single ticket-id)

Section structure: frontmatter / Inputs / Config + foolproofing gates (§9.2) / mapping table (signal → §2 taxonomy token/emoji/#needs-human/next-action template) / message grammar (§2) / Send (curl + fail-soft) / Audit line / Failure-handling table / Callers (3 sites, 2 files) / Guardrails.

Guardrails must include: **no-dedup is deliberate (do not "helpfully" add it back)**, never block the pipeline, the config is never committed, `--dry-run` paths must not reach the send, and never insert a send between the dispatcher table and the spawns.

### 9.4 Scope-simplification decision (2026-06-05, Charlie): digest-only, ggx-work untouched

Charlie: "I usually do batch work through the dispatcher — can we just print from ggx-dispatcher, and only the final table it emits at the end?" + follow-up "let's also add the ticket-analyzer messages." Final:

- **v1 has exactly three notify points**: dispatcher §6.5 digest, dispatcher §4.2 batch-abort, ticket-analyze Step 10 digest.
- **All 7 ggx-work terminal hooks are cancelled**: standalone `/ggx-work` is interactive (the output is right in front of you; a Slack ping adds nothing); results under the dispatcher are covered by the §6.5 table.
- **The `--dispatched` flag and the 5 example-string syncs are cancelled along with them** — if ggx-work never notifies, the "am I under the dispatcher" detection problem disappears entirely. (Historical note: the candidate "read the `dispatcher-*-in-flight` label" was disproven — `/dev:ship` removes that label on success, and Step 4.1 Terminal is only reached after ship; if per-ticket notifications are ever restored, use a CLI flag, never the label.)
- Why §4.2 batch-abort is kept: it is the only abnormal exit that **never reaches §6.5** — when a batch dies mid-lock, "the final table" never prints, and without this hook Slack would have zero record.
- Tokens actually emitted in v1: `DIGEST`, `REVIEW` / `SPEC-REVIEW` / `FAILED` (dispatcher lines), `NEEDS-REVISION` / `BLOCKED` / `CYCLE` / `FAILED` (analyzer lines; `READY` counted only), `BATCH-ABORT`. `CLASSIFY` folds into `FAILED` from the dispatcher's viewpoint (the reason text is still visible); the full §2 taxonomy is kept for future expansion.

### 9.5 Exact edit shapes per file (3 files)

**(1) `commands/dev/_slack-notify.md`** — new, §9.3 structure.

**(2) `commands/dev/ticket-analyze.md`** — add a "Slack digest (best-effort)" subsection at the end of Step 10 (L402-413):
- Gates: `--dry-run` (dry-run already short-circuits to the report at Step 7, never entering the write loop) or `analyzed + errored == 0` (empty sweep / everything skipped) → skip with one audit line.
- Otherwise: build header counts + per-ticket signal lines from the Step 9 report data (needs-human lines on top, ready as a count only, best-start as one line) → `/_slack-notify digest ticket-analyzer` (1 message).

**(3) `commands/dev/ggx-dispatcher.md`** — 2 spots + 1 guardrail:
- §6.5 (L748-770): after printing `Counts/Report`, before `STOP.` → build digest lines from the §6.4 in-memory rows (§6.2 authoritative outcome + Flags + pr) → `/_slack-notify digest ggx-dispatcher` (1 message). `--dry-run` stops at the §4.0 gate and never reaches §6.5 → naturally silent.
- §4.2 (L367-378): after item 2 (`PARTIAL LOCK`), before `STOP — release lock` → `/_slack-notify batch-abort detail=<failed ticket + tickets needing manual unlock>` (best-effort).
- End of the Guardrails list: one new guardrail — Slack notify exists ONLY at §4.2 / §6.5; never insert any send between the §4.3 table and the §5.3 spawns.

**Untouched**: `commands/dev/ggx-work.md`, `cloud-routines/*` (separate PR), `install.sh` (command files auto-install), `_ticket-lib.md`.

### 9.6 v1.1 follow-ups (not bundled into v1)

- **Daily liveness pulse**: mechanism = a local cron (or a schedule routine) invoking the helper's pulse mode daily; `chat.update` edits the pinned message referenced by `liveness_message_ts` (ts stored in the config). Separate small PR.
- **Cloud enablement**: prove CCR secret injection → both routine.json files + scope-note relaxation. Separate PR (§8 Q5).

### 9.7 Verification plan (executed in order after implementation)

1. **Foolproofing**: with no config, run `/ticket-analyze <id>` → zero Slack messages, the audit line appears, exit codes unchanged. ✅ *(bash-tested)*
2. `enabled:false` → same. ✅
3. With a real channel configured, manually invoke the helper → verify bot identity, format, hashtags. ✅ *(2026-06-05: bot "Planner agent" posted to C0AQG1DR8RY, `ok:true` — invalid_auth caught and fixed along the way: the first paste was a signing secret, not an `xoxb-` bot token)*
4. Deliberately corrupt the token → single WARN, pipeline unaffected. *(invalid_auth path exercised in step 3)*
5. A small `/ticket-analyze` batch → exactly 1 digest, needs-human lines on top. *(pending first real batch)*
6. `/ggx-dispatcher --dry-run` → 0 messages. *(structural: dry-run stops at §4.0)*
7. A real dispatcher batch → exactly 1 digest at §6.5 matching the stdout table. *(pending first real batch)*

### 9.8 Decisions Charlie provided before/at implementation

1. **Transport revision confirmed** (§9.1): local also uses his bot-token curl (messages = his bot identity). ✅
2. `#ggx-pipeline` **channel id** — provided when filling the config. ✅ *(C0AQG1DR8RY)*
3. Bot token as plaintext in the gitignored config (chmod 600) — accepted over macOS Keychain. ✅
