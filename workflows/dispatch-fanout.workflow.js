// dispatch-fanout.workflow.js
//
// Phases A+B of the /ggx-dispatcher fan-out → Workflow migration (R5 in
// ARCHITECTURE.md "Nested-spawn constraint"). Replaces the §5.3 N×Agent
// fan-out + §6.1 wait loop + §6.2 per-ticket fallback + §6.4 aggregation
// for ALL lanes. GGC-21 adds an evidence cross-check between work and fallback
// so a worker that hallucinates success without a real terminal state (open PR
// / need-spec-review label) is demoted to failed rather than silently shipped.
//
// Scope:
//   - dev / port / bug lane tickets → one /ggx-work --auto agent each (runWork).
//   - ui-tweak (design bug) tickets → apply/preview → dual-judge → finisher,
//     all as SCRIPT-spawned level-1 agents (runUiTweak). This is the Phase-B
//     dissolution of the dispatcher §5.0 inline exception: the opus judge
//     (dev-reviewer) that could NOT be a level-2 spawn inside a worker
//     (worker→judge = nested opus, broken) spawns cleanly when the SCRIPT
//     spawns it. See commands/dev/ui-tweak/audit.md (kept in SYNC).
//
// Why this is safe: every agent() below is spawned BY THE SCRIPT, so it is
// level-1 (the nested-spawn constraint does not apply between a workflow script
// and its agents). The heavy ff stages (R1: /opsx:apply, /code-review,
// /port:explore, /port:synth) still inline INSIDE each worker agent — migration
// does not change R1.
//
// NOTE on level: only what the SCRIPT spawns directly is level-1. Agents the
// WORKER spawns (e.g. verify-agent inside /dev:verify) remain level-2 in BOTH
// the default and --workflow paths — Phase B does NOT change their depth.
//
// args = DISPATCH_ROSTER: JSON array of
//   { ticketId, lane, worktreePath, url, uiTweak: boolean }
// serialized by the markdown main session after §4.3 locking completes.
//
// GGC-49 (worktree/branch isolation + earned no-op). The args may ALSO carry an
// optional top-level `trunkSha` — the freshly-fetched default-branch tip captured
// by the dispatcher (§5.2) the moment before the roster was built. When present it
// is the ground-truth CLEAN-TRUNK baseline every leg's worktree must be based on.
// Under the parallel fan-out a per-ticket worktree's HEAD/base_ref can leak to a
// SIBLING ticket's commit (CAF-625: base_ref was 321be8fc, not trunk a6c525c7),
// silently poisoning the diff baseline so an "already matches / no source changes"
// verdict is computed against the wrong tree and a live bug is closed as a no-op.
// Each leg now (1) asserts its base == trunkSha before any analysis is trusted,
// (2) treats an empty-diff no-op as something to be EARNED — validated against the
// ticket target and ALWAYS returned as a structured result, never a silent finish,
// and (3) is DEMOTED to failed if the orchestrator detects its base is contaminated.
// `trunkSha` is OPTIONAL for backward-compat: an older roster without it degrades to
// a loud per-leg warn (assertion skipped) rather than crashing the whole batch.

export const meta = {
  name: "dispatch-fanout",
  description:
    "Fan out one /ggx-work --auto agent per locked dev/port/bug ticket; design-bug tickets run apply->dual-judge(sonnet+opus)->finish as script-spawned level-1 agents (dissolves dispatcher §5.0 inline lane). Structured per-ticket result replaces §6.1 text parsing; per-ticket failure fallback writes Linear immediately.",
  // phases is a pure literal — /workflows progress view only, no exec semantics.
  phases: [
    { title: "work", detail: "Drive each dev/port/bug ticket through /ggx-work --auto" },
    { title: "ui-judge", detail: "ui-tweak lane: apply/preview, dual-judge panel, finisher" },
    { title: "evidence", detail: "Cross-check terminal evidence for each success row (GGC-21)" },
    { title: "fallback", detail: "Triage each failure (classify, bounded-retry, comment per class)" },
    { title: "aggregate", detail: "Collect structured outcomes into summary" },
  ],
};

// ── Structured per-ticket result — replaces §6.1's [ggx-work-result] text
// parse. The schema forces a StructuredOutput tool call so agent() returns a
// validated object, not free text. ──
const WORK_SCHEMA = {
  type: "object",
  required: ["ticketId", "outcome"],
  additionalProperties: false,
  properties: {
    ticketId: { type: "string" },
    // Aligned with §6.2's authoritative outcome vocabulary.
    outcome: { type: "string", enum: ["done", "port-paused", "failed"] },
    prUrl: { type: ["string", "null"] },
    // The final infer_*_stage value, for §6.4's stage_reached column.
    stage: { type: ["string", "null"] },
    error: { type: ["string", "null"] },
  },
};

// ── Evidence cross-check verdict (GGC-21) — confirm a success row's terminal
// state actually exists before the digest trusts it. ──
const EVIDENCE_SCHEMA = {
  type: "object",
  required: ["verdict"],
  additionalProperties: false,
  properties: {
    // confirmed = terminal evidence found; missing = checks ran cleanly and
    // found NONE (the hallucinated-done case → demote); inconclusive = the
    // check itself errored (auth/network) so we genuinely cannot tell → keep
    // the success (AC2: never false-demote a genuine ship over infra flake).
    verdict: { type: "string", enum: ["confirmed", "missing", "inconclusive"] },
    detail: { type: ["string", "null"] },
  },
};

// ── ui-tweak judge verdict — terse CLEAR/BLOCKED + one-line reason. ──
// Mirrors audit.md's panel contract: each judge returns a single status; the
// panel BLOCKS unless BOTH return CLEAR (kept in SYNC with audit.md Step 2/3).
const JUDGE_SCHEMA = {
  type: "object",
  required: ["status"],
  additionalProperties: false,
  properties: {
    status: { type: "string", enum: ["CLEAR", "BLOCKED"] },
    reason: { type: ["string", "null"] },
  },
};

// ── Failure-triage verdict (GGC-40) — classify a failed row so the dispatcher
// can act: retry transient infra flakes, flag thin tickets, surface platform
// defects. The cheap sonnet triage agent only runs for ambiguous error strings;
// the deterministic flag pre-pass (classifyFailure) resolves the common cases
// first. confidence drives the ambiguity default (low ⇒ unknown). ──
const TRIAGE_SCHEMA = {
  type: "object",
  required: ["class", "confidence"],
  additionalProperties: false,
  properties: {
    class: {
      type: "string",
      enum: ["transient-infra", "ticket-content", "platform-bug", "unknown"],
    },
    confidence: { type: "string", enum: ["high", "low"] },
    reason: { type: ["string", "null"] },
  },
};

// One opus worker's typical cost — the reserve we keep free before allowing a
// transient-infra retry to spend another worker (GGC-40 Q5). When no budget
// target is configured the retry is always allowed (see budgetAllowsRetry).
const RETRY_RESERVE = 100_000;

// ── Drive one roster row through /ggx-work --auto (dev / port / bug lane). ──
// agentType general-purpose mirrors today's §5.3 subagent_type; model "opus"
// mirrors the §5.3 rationale (heavy R1 work inlines inside this worker, so it
// needs opus-class reasoning). isolation is omitted on purpose — the ff
// pipelines create their own ../<ID> worktree (same rule as §5.3).
async function runWork(item, trunkSha) {
  log(`[work] ${item.ticketId} lane=${item.lane}`);
  const result = await agent(
    [
      `Execute: /ggx-work ${item.ticketId} --auto`,
      ``,
      `/ggx-work is a single-ticket orchestrator. It repeatedly calls /route`,
      `--non-interactive and executes the recommended ff (/port:ff, /dev:ff,`,
      `/bug:ff). Drive it to a terminal condition; do NOT stop on intermediate`,
      `stage messages.`,
      ``,
      // GGC-49 — worktree/branch isolation. /add-worktree now self-asserts the
      // fresh worktree's HEAD == clean trunk, but state THE EXPECTED BASE here too
      // so the worker fails loudly rather than analyzing a contaminated tree under
      // the parallel fan-out (the CAF-625 cross-worktree leak).
      ...(trunkSha
        ? [
            `WORKTREE ISOLATION (GGC-49): once the ff pipeline has created/entered the`,
            `../${item.ticketId} worktree, ASSERT its base is clean trunk before trusting`,
            `any analysis: the worktree's merge-base with HEAD must descend from`,
            `${trunkSha} (the freshly-fetched default-branch tip this batch was built on).`,
            `If the worktree HEAD sits on an unrelated sibling commit, STOP and return`,
            `outcome="failed" with error naming the contaminated base — never analyze it.`,
            ``,
          ]
        : []),
      // GGC-68 — port ship-summary contract. The fan-out worker is a
      // general-purpose agent driving /ggx-work; left unpinned it free-texted its
      // own summary (CAF-744: invented `<!-- port:ship:v1 -->` / `## Port
      // complete`, zero ri:v1 records), so downstream /spec-review parsed 0 items
      // and the human-review gate was silently defeated. Pin the real renderer.
      ...(item.lane === "port"
        ? [
            `PORT SHIP CONTRACT (GGC-68): when /port:ff reaches its ship stage, let`,
            `the REAL /port:ship step-11 renderer post the Linear summary — do NOT`,
            `hand-write or free-text your own summary comment. The posted comment MUST`,
            "carry the marker `<!-- port:ship-summary -->`, the heading `## Port",
            "summary: <change-name>`, and `### Needs review` / `### Verified (FYI)`",
            "sections holding VERBATIM `<!-- ri:v1 id=... -->` records (that is what",
            `/spec-review joins on). NEVER invent a marker like \`<!-- port:ship:v1`,
            "-->` or a heading like `## Port complete`: a comment that bypasses the",
            `renderer makes /spec-review parse 0 items and skips the review gate.`,
            ``,
          ]
        : []),
      `The run ends by printing a deterministic machine line:`,
      `  [ggx-work-result] outcome=<done|port-paused|failed> ticket=<id>`,
      `Read that line VERBATIM and set outcome to its value — do NOT infer the`,
      `outcome from surrounding prose. (On failure /ggx-work also posts its own`,
      `<!-- ggx-work-error --> Linear comment before exiting; you do NOT need to`,
      `post anything.)`,
      ``,
      `Return the structured object: ticketId="${item.ticketId}", outcome (from`,
      `the machine line), prUrl (the draft PR url if one was opened, else null),`,
      `stage (the final infer_*_stage value, else null), error (one-line reason`,
      `when outcome="failed", else null).`,
    ].join("\n"),
    {
      label: `work:${item.ticketId}`,
      phase: "work",
      agentType: "general-purpose",
      model: "opus",
      schema: WORK_SCHEMA,
    },
  );
  // agent() returns null on user-skip or a terminal API error AFTER retries —
  // i.e. the worker DIED before /ggx-work could finish (so /ggx-work did NOT
  // post its own <!-- ggx-work-error --> comment). Map that to a synthetic
  // failed row flagged workerDied so (a) it never NPEs the triage stage and
  // (b) triageAndFallback knows it must post the failure itself. A normal completed
  // failure (validated object with outcome:"failed") is NOT flagged — its
  // comment was already posted by /ggx-work, so the script must not double-post.
  if (!result) {
    log(`[work] ${item.ticketId} agent returned null (skip / terminal API error)`);
    return {
      ticketId: item.ticketId,
      outcome: "failed",
      prUrl: null,
      stage: null,
      error: "worker agent returned null (skipped or terminal API error); /ggx-work did not complete",
      workerDied: true,
    };
  }
  return result; // validated against WORK_SCHEMA
}

// ── ui-tweak lane (Phase B): apply/preview → dual-judge → finisher. ──
// THE §5.0 DISSOLUTION POINT. The two judges are spawned BY THE SCRIPT, so they
// are level-1: the opus judge (dev-reviewer) that could NOT be a level-2 spawn
// inside a worker (worker→judge = nested opus, broken — the reason §5.0 ran the
// lane inline in the dispatcher main session) spawns cleanly here. Tier-pinned
// decorrelation is preserved verbatim: ui-verify=sonnet, dev-reviewer=opus,
// BOTH always run, BOTH must be CLEAR.
//
// Honest coupling cost: this re-implements the slice of /ui-tweak:ff's
// infer_ui_stage walker around the audit (prep stops before audit; the script
// orchestrates the panel; the finisher resumes at commit→pr→review). The judge
// contract here is kept in lock-step with commands/dev/ui-tweak/audit.md — there
// is a SYNC note at the top of audit.md. If audit.md's judge contract changes
// (figma-context read, WILL-EDIT coverage assertion, loud-fail semantics),
// change BOTH. Failures set uiTweakFailed so triageAndFallback posts Linear (no
// sub-pipeline posted its own error — the script owns this flow end-to-end).
async function runUiTweak(item, trunkSha) {
  log(`[ui] ${item.ticketId} apply+preview`);

  // Stage 1: /ui-tweak:start → :apply → :preview (R20 direct-ship build-only
  // gate; --auto never reaches a device preview). STOP before :audit.
  // The prep agent also computes the final cumulative diff ONCE (after the
  // build-only compile gate / format) and returns it inline, so the panel below
  // never makes either judge re-run `git diff` or re-read the changed files
  // (GGC-5: diff-once, fed inline to both judges). diffText is the exact text
  // that ships; changedFiles is the name-only list for the judges' context.
  const prep = await agent(
    [
      `For ticket ${item.ticketId} (target worktree ${item.worktreePath}):`,
      `run /ui-tweak:start FIRST — it creates and enters the ../${item.ticketId}`,
      `worktree (do not cd there yourself; the worktree may not exist yet).`,
      `Then run /ui-tweak:apply, then /ui-tweak:preview with --auto semantics`,
      `(R20 direct-ship → build-only compile gate, no device preview). STOP`,
      `before /ui-tweak:audit. Leave .dev/ui-tweak/base_ref and`,
      `.dev/ui-tweak/build-pass in place for the panel.`,
      ``,
      // GGC-49 (fix 1) — worktree/branch isolation: the base_ref the whole
      // no-op/audit verdict trusts must be CLEAN TRUNK, not a sibling ticket's
      // commit that leaked in under the parallel fan-out (CAF-625).
      ...(trunkSha
        ? [
            `WORKTREE ISOLATION (GGC-49): after /ui-tweak:start enters the worktree and`,
            `BEFORE any analysis, assert .dev/ui-tweak/base_ref equals the freshly-fetched`,
            `clean-trunk tip ${trunkSha}. If it differs, the worktree base is contaminated`,
            `(a no-op or diff computed against it is invalid) — return ok:false with`,
            `error="base_ref <sha> != clean trunk ${trunkSha} (cross-worktree contamination)"`,
            `and baseRef set to the contaminated sha. Do NOT proceed to the diff.`,
            ``,
          ]
        : []),
      `THEN compute the audit diff ONCE so the dual-judge panel does not make`,
      `each judge re-run git: read base from .dev/ui-tweak/base_ref, run`,
      "`git diff \"$BASE\"` (full text) and `git diff \"$BASE\" --name-only`",
      `(changed-file list), and return both verbatim. Do NOT truncate diffText`,
      `unless it exceeds ~200KB (then return the first ~200KB and set`,
      `diffTruncated:true so the judges know to read remaining files themselves).`,
      ``,
      // GGC-49 (fix 2+4) — a no-op must be EARNED, never silently dropped.
      `EARNED NO-OP (GGC-49): if the diff is EMPTY (apply produced no source change`,
      `because the screen "already matches" the target), do NOT silently finish.`,
      `Set emptyDiff:true and VALIDATE the claim against the ticket's actual target:`,
      `re-read the ticket reference (screenshot / Figma / described target order) and`,
      `the live code, and decide whether the requested change is genuinely already`,
      `present. Put that determination in noopJustification: state the target you`,
      `verified against and why the current code already satisfies it (or does not).`,
      `An empty diff with no justification is treated as an UNVERIFIED no-op (failed).`,
      ``,
      `Return { ok: boolean, baseRef: string|null, diffText: string|null,`,
      `changedFiles: string|null, diffTruncated: boolean, emptyDiff: boolean,`,
      `noopJustification: string|null, error: string|null }.`,
    ].join("\n"),
    {
      label: `ui-prep:${item.ticketId}`,
      phase: "ui-judge",
      agentType: "general-purpose",
      model: "opus",
      schema: {
        type: "object",
        required: ["ok"],
        additionalProperties: false,
        properties: {
          ok: { type: "boolean" },
          baseRef: { type: ["string", "null"] },
          diffText: { type: ["string", "null"] },
          changedFiles: { type: ["string", "null"] },
          diffTruncated: { type: "boolean" },
          emptyDiff: { type: "boolean" },
          noopJustification: { type: ["string", "null"] },
          error: { type: ["string", "null"] },
        },
      },
    },
  );
  if (!prep || !prep.ok) {
    return {
      ticketId: item.ticketId,
      outcome: "failed",
      prUrl: null,
      stage: "ui:apply",
      error: prep?.error || "ui-tweak apply/preview failed (or prep agent returned null)",
      uiTweak: true, // design-bug row marker (GGC-29) — lets callers filter for the demo pass
      uiTweakFailed: true,
    };
  }

  // GGC-49 (fix 3) — orchestrator DISTRUSTS contamination. Even if the prep agent
  // returned ok:true, deterministically re-check the base it reported against the
  // ground-truth clean-trunk tip. A base_ref that does not match trunkSha is an
  // INVALID run (the CAF-625 pattern: the leg ran, but on the wrong tree). Demote
  // to failed — never let a result derived from a contaminated base ship.
  if (trunkSha && prep.baseRef && prep.baseRef.trim() !== trunkSha) {
    log(`[ui] ${item.ticketId} base_ref ${prep.baseRef} != clean trunk ${trunkSha} -> DEMOTE (contaminated)`);
    return {
      ticketId: item.ticketId,
      outcome: "failed",
      prUrl: null,
      stage: "ui:apply",
      error:
        `UI-TWEAK INVALID (GGC-49 contamination): base_ref ${prep.baseRef} != clean trunk ` +
        `${trunkSha} — result computed against a non-trunk base, re-run clean.`,
      uiTweakFailed: true,
    };
  }

  // GGC-49 (fix 2+4) — an EMPTY-DIFF no-op must be EARNED. A ui-tweak run that
  // produced no source change is only legitimate when validated against the
  // ticket's actual target; an unjustified empty diff is the silent-no-op trap
  // that falsely closed CAF-625. The verdict is ALWAYS a structured result here
  // (never a silent finish): justified ⇒ outcome "done" (no PR — nothing to ship);
  // unjustified ⇒ failed so triageAndFallback posts a visible Linear comment.
  const computedEmpty =
    prep.emptyDiff === true ||
    (prep.diffText != null && prep.diffText.trim() === "" && !prep.diffTruncated);
  if (computedEmpty) {
    const justified = prep.noopJustification && prep.noopJustification.trim() !== "";
    if (!justified) {
      log(`[ui] ${item.ticketId} EMPTY diff with no target validation -> failed (unearned no-op)`);
      return {
        ticketId: item.ticketId,
        outcome: "failed",
        prUrl: null,
        stage: "ui:apply",
        error:
          "UI-TWEAK UNEARNED NO-OP (GGC-49): empty diff but no validation against the ticket " +
          "target — refusing to close a possibly-live bug as a no-op. Re-run and verify the target.",
        uiTweakFailed: true,
      };
    }
    log(`[ui] ${item.ticketId} EARNED no-op (validated against target): ${prep.noopJustification}`);
    return {
      ticketId: item.ticketId,
      outcome: "done",
      prUrl: null,
      stage: "ui:noop",
      error: null,
      noop: true,
      noopJustification: prep.noopJustification.slice(0, 300),
    };
  }

  // Stage 1c: deterministic structural pre-pass (LLM-free; mirrors audit.md
  // Step 1c). Scan the ADDED lines of the precomputed diff for unambiguous
  // non-inert-UI signals; a hit short-circuits to BLOCKED WITHOUT spawning the
  // slow opus dev-reviewer (the obvious-logic-change fast path). This only ever
  // produces an EARLY BLOCKED, never an early CLEAR, so the both-must-be-CLEAR
  // contract is unchanged (a CLEAR still requires BOTH judges to run + return
  // CLEAR). Skipped when the diff was truncated (can't scan what we don't have).
  if (prep.diffText && !prep.diffTruncated) {
    const added = prep.diffText
      .split("\n")
      .filter((l) => l.startsWith("+") && !l.startsWith("+++"));
    // GGC-57: synced to audit.md Step 1c. The deterministic pre-pass scans ONLY
    // for UNAMBIGUOUS runtime-behavior signals (Flutter lifecycle, state mutation,
    // gesture recognizers, async, navigation/routing, provider/state access). It is
    // allow-by-default: imports, pure layout/structure widgets (LayoutBuilder,
    // ConstrainedBox, IntrinsicHeight, Row, Column, …), style/token `App*` const
    // accessors, and generic syntactic shapes (=>, return, new X, class, if (, for ()
    // are NOT signals — they fall through to the dual-judge panel, which is the real
    // gate (under-match is safe; over-match falsely reverts a legit UI change). This
    // is why CAF-540 (pure layout: LayoutBuilder/ConstrainedBox/IntrinsicHeight) and
    // an import-only diff reach the panel instead of being reverted here, while
    // genuine behavior (CAF-555's TapGestureRecognizer + initState/dispose) still
    // short-circuits to BLOCK. Keep in SYNC with audit.md BEHAVIOR_RE.
    const BEHAVIOR_RE =
      /(initState|dispose|didChangeDependencies|didUpdateWidget|deactivate|setState|notifyListeners|addListener|removeListener)\(|GestureRecognizer|await |async[ ({]|\.then\(|ref\.(read|watch|listen)\(|Navigator\.|GoRouter|context\.(go|push|pop)|\.pushNamed\(|\.pushReplacement|StreamSubscription|StreamController/;
    const structuralHit = added.some((l) => BEHAVIOR_RE.test(l));
    if (structuralHit) {
      log(`[ui] ${item.ticketId} structural pre-pass BLOCKED (logic signal in diff) — opus judge skipped`);
      return {
        ticketId: item.ticketId,
        outcome: "failed",
        prUrl: null,
        stage: "ui:audit",
        error:
          "UI-TWEAK BLOCKED (structural pre-pass): added lines contain non-inert-UI logic signals (import/call/control-flow/identifier) — reverted, no changes kept.",
        uiTweak: true, // design-bug row marker (GGC-29) — lets callers filter for the demo pass
      uiTweakFailed: true,
      };
    }
  }

  // Stage 2: decorrelated dual-judge panel — BOTH must be CLEAR (audit.md
  // Step 2/3). Spawned in parallel; tiers PINNED (sonnet vs opus), no downgrade.
  // The precomputed diff (prep.diffText + prep.changedFiles) is fed INLINE to
  // both judges (GGC-5) — neither re-runs git diff nor re-reads files.
  log(`[ui] ${item.ticketId} dual-judge (sonnet + opus)`);
  const diffBlock =
    prep.diffText && !prep.diffTruncated
      ? [
          `Changed files:`,
          prep.changedFiles || "(none reported)",
          ``,
          `Final cumulative diff (precomputed once — do NOT re-run git diff):`,
          "```diff",
          prep.diffText,
          "```",
        ].join("\n")
      : // Fallback only if prep could not supply the diff (or it was truncated):
        `Audit the final cumulative diff in ${item.worktreePath}` +
        (prep.baseRef ? ` (git diff ${prep.baseRef})` : "") +
        (prep.diffTruncated ? " — the diff was too large to inline, read the changed files directly." : ".");
  const judgePrompt = (lens) =>
    [
      `Audit the change for ticket ${item.ticketId} through the ${lens} lens.`,
      diffBlock,
      ``,
      `Read .dev/ui-tweak/figma-context.md in ${item.worktreePath} (if present)`,
      `and assert every WILL-EDIT target is covered — a miss is BLOCKED. Per`,
      `audit.md: a purely visual/layout/structure change is CLEAR; any`,
      `logic/behavior change is BLOCKED. Return { status: "CLEAR"|"BLOCKED", reason }.`,
    ].join("\n");

  const [uiVerify, devReview] = await parallel([
    () =>
      agent(judgePrompt("UI-only / visual"), {
        label: `ui-verify:${item.ticketId}`,
        phase: "ui-judge",
        agentType: "ui-verify-agent",
        model: "sonnet",
        schema: JUDGE_SCHEMA,
      }),
    () =>
      agent(judgePrompt("behavior / logic, with the deterministic structural pre-pass"), {
        label: `dev-review:${item.ticketId}`,
        phase: "ui-judge",
        agentType: "dev-reviewer",
        model: "opus",
        schema: JUDGE_SCHEMA,
      }),
  ]);

  // parallel() resolves a failed thunk to null — aligned with audit.md
  // "missing report / agent error ⇒ BLOCKED". BOTH must be a present CLEAR.
  const blocked =
    !uiVerify || uiVerify.status !== "CLEAR" || !devReview || devReview.status !== "CLEAR";
  if (blocked) {
    const who = !uiVerify || uiVerify.status !== "CLEAR" ? "ui-verify" : "dev-reviewer";
    const reason =
      (!uiVerify || uiVerify.status !== "CLEAR" ? uiVerify?.reason : devReview?.reason) ||
      "judge error / null verdict";
    // --auto is loud-fail (audit.md): no repair loop. Mark failed; the
    // dispatcher-dev-in-flight label stays as the human-resume signal.
    log(`[ui] ${item.ticketId} BLOCKED by ${who}: ${reason}`);
    return {
      ticketId: item.ticketId,
      outcome: "failed",
      prUrl: null,
      stage: "ui:audit",
      error: `UI-TWEAK BLOCKED (${who}): ${reason}`,
      uiTweak: true, // design-bug row marker (GGC-29) — lets callers filter for the demo pass
      uiTweakFailed: true,
    };
  }

  // Stage 3: finisher — both judges CLEAR ⇒ commit → pr → review, FAITHFUL to
  // the /ui-tweak:ff `pr`-stage contract (GGC-67). That stage carries the
  // designer-facing contract: a DRAFT PR whose title is prefixed `[ui-tweak]`
  // and whose body is the pre-built `## UI Tweak — designer-verifiable summary`
  // (commands/design/ui-tweak/ff.md L292 + "Deliver PR body" L494). A free-text
  // "open a draft PR" instruction let the finisher drift to a /dev:ship-style PR
  // (CAF-556 #629: `fix(...)` title, `### Change` body; CAF-564 #628: prefix but
  // generic body). The prompt now PINS the contract, FORBIDS an improvised
  // `gh pr create` / `/dev:ship`, and has the agent SELF-VERIFY + auto-correct via
  // gh, returning booleans the script asserts on below (deterministic fail-loud).
  log(`[ui] ${item.ticketId} CLEAR -> commit + PR`);
  const ship = await agent(
    [
      `In ${item.worktreePath} for ${item.ticketId}: the dual-judge panel is`,
      `CLEAR. Run the remaining /ui-tweak:ff stages with --auto semantics,`,
      `FAITHFULLY — do NOT improvise a /dev:ship-style PR or a bare`,
      "`gh pr create`. Steps, in order:",
      `1. /ui-tweak:audit — write .dev/ui-verify-pass.md Status: CLEAR.`,
      `2. /commit — commit only the audited files.`,
      `3. The \`pr\` stage EXACTLY as commands/design/ui-tweak/ff.md defines it`,
      `   (L292 + "Deliver PR body" L494): open a DRAFT PR via \`/pull-request --draft\``,
      `   whose TITLE is prefixed \`[ui-tweak] ${item.ticketId}: <summary>\` and whose`,
      `   BODY is the pre-built \`## UI Tweak — designer-verifiable summary\` body`,
      `   (Source / Grounding-provenance / Audit verdict / Coverage table + the`,
      `   marker-wrapped \`## Demo\` region). Then transition the ticket: status →`,
      `   In Review, remove \`ready-to-dev\` (keep \`design bug\`).`,
      `4. SELF-VERIFY the contract deterministically with`,
      "   `gh pr view <pr> --json title,body`:",
      `   - title NOT starting with \`[ui-tweak]\` → \`gh pr edit <pr> --title "[ui-tweak] ${item.ticketId}: <summary>"\`.`,
      `   - body NOT containing \`## UI Tweak\` → rebuild the contract body and \`gh pr edit <pr> --body <body>\`.`,
      `   Re-fetch after any edit so the returned booleans reflect the FINAL on-PR state.`,
      `Return { prUrl, stage, titleHasPrefix, bodyHasMarker, finalTitle, error }.`,
    ].join("\n"),
    {
      label: `ui-ship:${item.ticketId}`,
      phase: "ui-judge",
      agentType: "general-purpose",
      model: "opus",
      schema: {
        type: "object",
        required: ["stage", "titleHasPrefix", "bodyHasMarker"],
        additionalProperties: false,
        properties: {
          prUrl: { type: ["string", "null"] },
          stage: { type: "string" },
          // GGC-67: FINAL on-PR contract state (after any agent auto-correct).
          titleHasPrefix: { type: "boolean" },
          bodyHasMarker: { type: "boolean" },
          finalTitle: { type: ["string", "null"] },
          error: { type: ["string", "null"] },
        },
      },
    },
  );
  if (!ship) {
    return {
      ticketId: item.ticketId,
      outcome: "failed",
      prUrl: null,
      stage: "ui:ship",
      error: "ui-tweak finisher agent returned null (skip / terminal API error)",
      uiTweak: true, // design-bug row marker (GGC-29) — lets callers filter for the demo pass
      uiTweakFailed: true,
    };
  }
  // GGC-67: deterministic PR-contract gate. The finisher self-corrects, but the
  // script is the source of truth — a PR that STILL lacks the `[ui-tweak]` title
  // prefix or the `## UI Tweak` body marker is a contract violation. Fail loud so
  // triageAndFallback posts Linear, rather than shipping a malformed designer PR
  // that breaks `/ggx-demo --batch` discovery and the reviewer's expectations.
  if (!ship.error && (!ship.titleHasPrefix || !ship.bodyHasMarker)) {
    const missing = [
      ship.titleHasPrefix ? null : "title missing `[ui-tweak]` prefix",
      ship.bodyHasMarker ? null : "body missing `## UI Tweak — designer-verifiable summary`",
    ]
      .filter(Boolean)
      .join("; ");
    log(`[ui] ${item.ticketId} finisher PR-contract violation: ${missing}`);
    return {
      ticketId: item.ticketId,
      uiTweak: true,
      outcome: "failed",
      prUrl: ship.prUrl ?? null,
      stage: "ui:pr-contract",
      error:
        `UI-TWEAK PR CONTRACT (GGC-67): ${missing} ` +
        `(finalTitle=${ship.finalTitle ?? "?"}). The pr stage must follow ` +
        `commands/design/ui-tweak/ff.md (title \`[ui-tweak]\` + \`## UI Tweak\` body).`,
      uiTweakFailed: true,
    };
  }
  return {
    ticketId: item.ticketId,
    uiTweak: true, // design-bug row marker (GGC-29) — the demo pass filters on uiTweak && outcome==="done" && prUrl
    outcome: ship.error ? "failed" : "done", // ui-tweak has no port-paused
    prUrl: ship.prUrl ?? null,
    stage: ship.stage || "ui:review",
    error: ship.error ?? null,
    ...(ship.error ? { uiTweakFailed: true } : {}),
  };
}

// ── Evidence cross-check for SUCCESS rows (GGC-21). ──
// runWork/runUiTweak trust the worker's self-reported outcome (WORK_SCHEMA);
// a worker that dies mid-run yet hallucinates outcome="done" (the 2026-05-15
// misreport pattern) otherwise produces no failure comment and a wrong digest,
// because triageAndFallback only fires on outcome=="failed". This stage demands cheap
// TERMINAL evidence for every success row and demotes a row to "failed" when
// that evidence is definitively absent — routing it through triageAndFallback's
// dispatch-fallback-error branch like any other worker-died failure.
//
// Per-lane terminal-state enumeration (Risks/Guards: completeness is the risk —
// keep this table in lock-step with the WORK_SCHEMA outcome enum):
//   ┌──────────┬──────────────┬───────────────────────────────────────────────┐
//   │ lane     │ success out. │ terminal evidence checked here                 │
//   ├──────────┼──────────────┼───────────────────────────────────────────────┤
//   │ dev      │ done         │ an OPEN draft PR for the ticket                │
//   │ bug      │ done         │ an OPEN draft PR for the ticket                │
//   │ port     │ done         │ an OPEN draft PR for the ticket                │
//   │ port     │ port-paused  │ the `need-spec-review` label on the issue AND  │
//   │          │              │   a contract `<!-- port:ship-summary -->` +    │
//   │          │              │   `ri:v1` comment (GGC-68; no PR yet)          │
//   │ ui-tweak │ done         │ an OPEN draft PR — ui-tweak's PR-open IS the    │
//   │          │              │   terminal, so the same `done` check covers it │
//   │          │              │   (AC2 walker-evidence edge case, no special)  │
//   └──────────┴──────────────┴───────────────────────────────────────────────┘
// "failed" rows are not re-derived — they already route to triageAndFallback. Only
// "done"/"port-paused" reach an evidence agent.
//
// Fail-SAFE direction (AC2): demote ONLY on verdict=="missing" (checks ran and
// found nothing). verdict=="inconclusive" (gh/git/MCP errored — auth/network)
// keeps the success and flags evidenceUnchecked, so a real ship is never
// false-failed over infra flake. The in-flight label is never touched here, so
// a demoted row still carries the resume signal (AC3).
async function verifyEvidence(res, item) {
  if (!res) return res;
  if (res.outcome !== "done" && res.outcome !== "port-paused") return res;
  // GGC-49 — an EARNED no-op (validated against the ticket target in runUiTweak)
  // legitimately has NO PR: there was nothing to ship. The "done" terminal here is
  // the target-validation, not an open PR, so the open-PR cross-check below would
  // false-DEMOTE it. The earned-no-op gate already did the only meaningful check
  // (justification present + base == clean trunk), so accept it as-is.
  if (res.noop === true) {
    log(`[evidence] ${res.ticketId} earned no-op — PR cross-check N/A (validated against target)`);
    return res;
  }

  const checkPrompt =
    res.outcome === "done"
      ? [
          `Cross-check terminal evidence for ticket ${res.ticketId} (lane=${item.lane}),`,
          `which a worker reported as outcome="done". You are a CHEAP read-only`,
          `verifier: run at most the commands below, then return a verdict. Do NOT`,
          `fix anything and do NOT comment on Linear.`,
          ``,
          `Expected terminal evidence for "done": an OPEN draft PR for this ticket.`,
          res.prUrl
            ? `The worker reported prUrl=${res.prUrl} — verify it with ` +
              "`gh pr view " + res.prUrl + " --json state,isDraft`. state==OPEN ⇒ confirmed."
            : `The worker reported NO prUrl (already suspicious for a "done").`,
          `Independently derive the branch from the ff worktree: ` +
            "`git -C " + item.worktreePath + " branch --show-current`, then " +
            "`gh pr list --head <branch> --state open --json number,url`. A non-empty list ⇒ confirmed.",
          `If the worktree is gone, fall back to ` +
            "`gh pr list --search \"" + res.ticketId + "\" --state open --json number,url`.",
          ``,
          `verdict="confirmed" if ANY check shows an OPEN PR; "missing" if every`,
          `command ran cleanly and found NO open PR (the hallucinated-done case);`,
          `"inconclusive" ONLY if gh/git errored (auth/network) so you truly cannot tell.`,
        ].join("\n")
      : [
          `Cross-check terminal evidence for ticket ${res.ticketId} (lane=${item.lane}),`,
          `which a worker reported as outcome="port-paused". You are a CHEAP`,
          `read-only verifier — make the read calls below, then return a verdict. Do`,
          `NOT fix anything and do NOT comment on Linear.`,
          ``,
          `Expected terminal evidence for "port-paused" — BOTH must hold:`,
          `  1. the ${res.ticketId} issue carries the \`need-spec-review\` label`,
          `     (port handed the work to the HITL spec-review gate; there is no PR`,
          `     yet, so do NOT look for one);`,
          `  2. /port:ship step-11 posted its summary comment (GGC-68): a comment`,
          `     containing the marker \`<!-- port:ship-summary -->\` AND at least one`,
          `     \`<!-- ri:v1 \` record. A comment with an invented marker (e.g.`,
          `     \`<!-- port:ship:v1 -->\` or a \`## Port complete\` heading) or with no`,
          `     ri:v1 record does NOT count — /spec-review would parse 0 items.`,
          `Use the connected Linear MCP (find it with ToolSearch; prefer`,
          `mcp__claude_ai_Linear__get_issue for the labels and`,
          `mcp__claude_ai_Linear__list_comments for the summary comment, falling back`,
          `to mcp__linear-server__*).`,
          ``,
          `verdict="confirmed" if BOTH hold; "missing" if the calls succeeded and`,
          `EITHER the label is absent OR no contract-compliant ship-summary comment`,
          `exists; "inconclusive" if a call errored. Name which of 1/2 failed in detail.`,
        ].join("\n");

  const check = await agent(
    [checkPrompt, ``, `Return { verdict: "confirmed"|"missing"|"inconclusive", detail: one short line }.`].join("\n"),
    {
      label: `evidence:${res.ticketId}`,
      phase: "evidence",
      agentType: "general-purpose",
      model: "sonnet",
      schema: EVIDENCE_SCHEMA,
    },
  );

  // Agent died/skipped, or could not determine — treat as inconclusive: keep
  // the success, flag it, do NOT demote (AC2). The flag is observable in the
  // returned rows so a future sweep / the digest can note the unverified ship.
  if (!check || check.verdict === "inconclusive") {
    log(
      `[evidence] ${res.ticketId} reported ${res.outcome} but evidence check was ` +
        `INCONCLUSIVE (${check?.detail || "agent returned null"}) — keeping success, not demoting`,
    );
    return { ...res, evidenceUnchecked: true };
  }
  if (check.verdict === "confirmed") {
    log(`[evidence] ${res.ticketId} ${res.outcome} confirmed (${check.detail || "terminal evidence found"})`);
    return res;
  }
  // verdict === "missing": definitive negative ⇒ demote to failed. The row now
  // routes through triageAndFallback's dispatch-fallback-error branch (evidenceDemoted
  // flag below), the digest counts it under `failed`, and the in-flight label is
  // left untouched so the next sweep can resume.
  log(`[evidence] ${res.ticketId} reported ${res.outcome} but terminal evidence MISSING -> DEMOTE to failed`);
  return {
    ...res,
    outcome: "failed",
    evidenceDemoted: true,
    digestNote: "no-terminal-evidence", // §6.4 may surface this on the row
    error: ("[evidence-demoted] reported \"" + res.outcome + "\" but " +
      (check.detail || "no terminal evidence found") +
      " (no-terminal-evidence; prior: " + (res.error || "none") + ")").slice(0, 300),
  };
}

// ── Failure triage (GGC-40) — classify a failed row's cause. ──
// Deterministic pre-pass on the structured flags FIRST (cheap, reliable); only
// an ambiguous error-string case reaches the sonnet agent. NO repo/Bash access:
// the classifier sees only signals we already hold ({ error, stage, lane,
// outcome } + flags). Returns { class, confidence, reason }.
//
// Deterministic rules (Q2/Q3 resolved):
//   - workerDied === true            → transient-infra (agent died / terminal
//                                       API error after retries), high confidence.
//   - uiTweakFailed && /UI-TWEAK      → terminal-ui-block (GGC-37): a DETERMINISTIC
//     BLOCK/.test(error)                dual-judge BLOCK (structural pre-pass OR
//                                       judge). Re-running reproduces the identical
//                                       BLOCK, so leaving the in-flight resume signal
//                                       causes infinite recovery spin — route to a
//                                       terminal cleanup, not today's comment-only path.
//   - uiTweakFailed (any other shape) → unknown: apply/preview fail, GGC-49
//                                       contamination ("UI-TWEAK INVALID"), or a null
//                                       finisher are NOT a BLOCK — they may be
//                                       re-runnable, so keep in-flight (today's comment).
//   - evidenceDemoted === true       → unknown (a demoted success is not
//                                       obviously any single class).
// Anything else with an error string → ask the sonnet agent; low confidence or
// content/platform both-plausible ⇒ default `unknown` (the safe low-noise path).
async function classifyFailure(res) {
  // Strong deterministic signal: a dead worker is a transient infra failure.
  if (res.workerDied === true) {
    return { class: "transient-infra", confidence: "high", reason: "worker agent died / terminal API error after retries" };
  }
  // ui-tweak DETERMINISTIC dual-judge BLOCK (structural pre-pass OR judge) — GGC-37.
  // Re-running reproduces the identical BLOCK (the script path has no repair loop;
  // GGC-44, if built, inserts a repair attempt BEFORE this point so only
  // repair-exhausted blocks reach here), so leaving the dispatcher-dev-in-flight
  // resume signal causes an infinite recovery spin under the on-duty loop
  // (CAF-514 / CAF-561). Route to triageTerminalUiBlock: remove in-flight, set
  // need-revision, reset To-Do. Gate on the error STRING (both BLOCK returns start
  // with "UI-TWEAK BLOCKED"), NOT all uiTweakFailed — see the non-BLOCK branch below.
  if (res.uiTweakFailed === true && /UI-TWEAK BLOCKED/.test(res.error || "")) {
    return { class: "terminal-ui-block", confidence: "high", reason: "deterministic ui-tweak BLOCK (structural pre-pass / dual-judge) — re-run reproduces it; clean up to stop infinite recovery spin" };
  }
  // Other ui-tweak loud-fails — apply/preview fail, GGC-49 contamination
  // ("UI-TWEAK INVALID"), a null finisher — are NOT a deterministic BLOCK; they may
  // be re-runnable, so keep today's unknown ⇒ fallback comment with the in-flight
  // label preserved. (ui-tweak transient retry/repair is GGC-44 scope.)
  if (res.uiTweakFailed === true) {
    return { class: "unknown", confidence: "high", reason: "ui-tweak loud-fail (non-BLOCK: apply / contamination / finisher null) — keep in-flight, may be re-runnable" };
  }
  // A demoted success (GGC-21) is ambiguous by default.
  if (res.evidenceDemoted === true) {
    return { class: "unknown", confidence: "high", reason: "evidence-demoted success — not obviously any single class" };
  }
  // Ambiguous error-string case → one cheap read-only sonnet call (no repo access).
  const triage = await agent(
    [
      `Classify why ticket ${res.ticketId} FAILED in the dispatcher batch. You are`,
      `a CHEAP read-only classifier: decide from the signals below ONLY. Do NOT`,
      `read the repo, run any command, or comment on Linear.`,
      ``,
      `Signals:`,
      `  outcome: ${res.outcome}`,
      `  lane:    ${res.lane || "unknown"}`,
      `  stage:   ${res.stage || "unknown"}`,
      `  error:   ${(res.error || "(none)").slice(0, 400)}`,
      ``,
      `Classes:`,
      `  transient-infra — an infra flake: API 5xx / 503, gh/network hiccup, a`,
      `    rate-limit or timeout, an agent that died with no ticket-content or`,
      `    code cause. Retryable.`,
      `  ticket-content  — the ticket itself is incomplete/contradictory/too thin`,
      `    to implement (missing repro, no acceptance criteria, ambiguous ask).`,
      `  platform-bug    — a defect in the PIPELINE/platform itself: a walker`,
      `    misfire, an R-rule violation, a skill/command bug — not the ticket and`,
      `    not infra.`,
      `  unknown         — none clearly dominates, OR ticket-content vs`,
      `    platform-bug are both plausible. This is the safe default.`,
      ``,
      `Default to "unknown" with confidence "low" whenever you are unsure or two`,
      `classes are both plausible. Only return "high" confidence when one class is`,
      `unmistakable from the error string.`,
    ].join("\n"),
    {
      label: `triage:${res.ticketId}`,
      phase: "fallback",
      agentType: "general-purpose",
      model: "sonnet",
      schema: TRIAGE_SCHEMA,
    },
  );
  // Agent died/skipped, or low-confidence, or content↔platform ambiguity ⇒
  // fall back to the safe low-noise default (today's behavior).
  if (!triage || triage.confidence === "low") {
    return { class: "unknown", confidence: "low", reason: triage?.reason || "triage agent returned null / low-confidence — defaulting to unknown" };
  }
  return triage;
}

// Budget gate for the transient-infra retry (GGC-40 Q5). A retry spends roughly
// one more opus worker (~RETRY_RESERVE), so skip it when the remaining budget
// cannot cover the reserve. The workflow harness here exposes no `budget` global
// (confirmed: grep found none) — so probe defensively: when a budget object IS
// present at runtime and carries a total, honor remaining() < RETRY_RESERVE;
// when there is NO budget target (the global is absent, or budget.total is
// falsy), the retry is ALWAYS allowed (ticket Q5 — "allowed when no budget
// target"). Never throws.
function budgetAllowsRetry() {
  try {
    const b = typeof budget !== "undefined" ? budget : null;
    if (!b || !b.total) return { allowed: true, reason: "no budget target — retry allowed" };
    if (typeof b.remaining !== "function") return { allowed: true, reason: "budget present but no remaining() — retry allowed" };
    const remaining = b.remaining();
    if (typeof remaining === "number" && remaining < RETRY_RESERVE) {
      return { allowed: false, reason: `remaining ${remaining} < RETRY_RESERVE ${RETRY_RESERVE}` };
    }
    return { allowed: true, reason: `remaining ${remaining} >= RETRY_RESERVE ${RETRY_RESERVE}` };
  } catch (_) {
    // A budget-probe error must never block the pipeline — fail open (allow).
    return { allowed: true, reason: "budget probe errored — failing open (retry allowed)" };
  }
}

// ── Per-ticket triage + fallback (§6.2 + GGC-40): classify, bounded-retry, and
// write Linear on failure. Renamed from runFallback; on outcome!=="failed" it
// passes straight through unchanged. On "failed" it classifies the row, then
// acts per class.
//
// Idempotency is structural, not advisory: on a NORMAL completed failure
// (outcome:"failed", no workerDied flag) /ggx-work --auto already posted its
// own <!-- ggx-work-error --> comment (ggx-work.md:347) before exiting, so the
// script must NOT post again (that was the double-post bug). The unknown branch
// posts ONLY when the worker DIED / a ui-tweak run was script-orchestrated /
// a success was evidence-demoted — and EACH triage class uses its OWN DISTINCT
// marker so the writers can never collide. With ONE deliberate exception
// (terminal-ui-block, GGC-37 — see triageTerminalUiBlock), no triage path removes
// the in-flight label (the durable §6.2 resume signal) or touches status: a
// DETERMINISTIC ui-tweak BLOCK is the sole case where leaving the resume signal
// causes an infinite recovery spin, so that one class removes dispatcher-dev-in-flight,
// adds need-revision, and resets status to To-Do. Every other class preserves the
// "script does not touch labels/status" invariant.
//
// `item` (roster row) and `trunkSha` are threaded in so a transient-infra row
// can be retried with a fresh runWork(item, trunkSha) + verifyEvidence(res,item).
async function triageAndFallback(res, item, trunkSha) {
  if (!res) return res;                       // belt-and-suspenders (should not happen after runWork's null map)
  if (res.outcome !== "failed") return res;   // done / port-paused already wrote their own state

  const verdict = await classifyFailure(res);
  log(`[triage] ${res.ticketId} class=${verdict.class} confidence=${verdict.confidence} (${verdict.reason || ""})`);

  switch (verdict.class) {
    case "transient-infra":
      return triageTransientInfra(res, item, trunkSha);
    case "ticket-content":
      return triageTicketContent(res, verdict);
    case "platform-bug":
      return triagePlatformBug(res, verdict);
    case "terminal-ui-block":
      return triageTerminalUiBlock(res, item);
    default: // "unknown"
      return triageUnknownFallback(res);
  }
}

// transient-infra → ONE fresh in-run retry, dev/port/bug (runWork) ONLY.
// ui-tweak rows are NOT retried here (GGC-44 scope) — they fall through to the
// unknown fallback comment. Cap = 1 (a row carrying retried:true is never
// retried again, and never re-retry when the retry would be a 2nd workerDied).
// Budget-gated (skip logged, never silent). Success on retry ⇒ no fallback.
async function triageTransientInfra(res, item, trunkSha) {
  // Scope guard: ui-tweak transient retry/repair is GGC-44, out of scope here.
  if (res.uiTweak === true || res.uiTweakFailed === true) {
    log(`[triage] ${res.ticketId} transient but ui-tweak lane — NOT retried here (GGC-44 scope), falling through to fallback`);
    return triageUnknownFallback(res);
  }
  // Cap = 1: never retry a row already retried once.
  if (res.retried === true) {
    log(`[triage] ${res.ticketId} transient but already retried once (cap=1) — falling through to fallback`);
    return triageUnknownFallback(res);
  }
  // Budget gate (Q5).
  const gate = budgetAllowsRetry();
  if (!gate.allowed) {
    log(`[triage] ${res.ticketId} transient retry SKIPPED — budget gate (${gate.reason})`);
    return triageUnknownFallback({
      ...res,
      error: `[retry-skipped: ${gate.reason}] ${res.error || "transient-infra"}`.slice(0, 300),
    });
  }
  if (!item) {
    // No roster row in scope (should not happen — stage callback passes it) —
    // cannot re-invoke runWork, so just post the fallback.
    log(`[triage] ${res.ticketId} transient but no roster item in scope — cannot retry, falling through to fallback`);
    return triageUnknownFallback(res);
  }

  log(`[triage] ${res.ticketId} transient-infra -> ONE fresh runWork retry (${gate.reason})`);
  // Fresh re-invoke (Q1): the ff walkers infer state from the persistent
  // ../<ID> worktree's filesystem markers, so a fresh /ggx-work --auto resumes
  // naturally — no new resume logic. Then re-run the evidence cross-check.
  let retryRes = await runWork(item, trunkSha);
  retryRes = await verifyEvidence(retryRes, item);

  // Never re-retry: if the retry itself died (2nd consecutive workerDied) or
  // failed again, do NOT loop — mark retried and fall through to fallback.
  if (retryRes && retryRes.outcome !== "failed") {
    log(`[triage] ${res.ticketId} retry SUCCEEDED (${retryRes.outcome}) — suppressing fallback comment`);
    return { ...retryRes, retried: true };
  }
  log(`[triage] ${res.ticketId} retry still failed — falling through to fallback (retried:true)`);
  return triageUnknownFallback({
    ...(retryRes || res),
    retried: true,
    error: `[retried once, still failed] ${(retryRes && retryRes.error) || res.error || "transient-infra"}`.slice(0, 300),
  });
}

// ticket-content → comment only, NO label write (decision 1 = A). Distinct
// marker <!-- dispatch-triage-content -->; idempotent on the marker. Preserves
// the "script never touches labels" invariant — /ticket-analyze or a human
// flips need-revision next cycle.
async function triageTicketContent(res, verdict) {
  log(`[triage] ${res.ticketId} ticket-content -> comment (dispatch-triage-content), NO label change`);
  await agent(
    [
      `Ticket ${res.ticketId} failed in the dispatcher batch and was triaged as`,
      `TICKET-CONTENT incomplete: ${(verdict.reason || "the ticket looks too thin / ambiguous to implement").slice(0, 200)}.`,
      `Via the connected Linear MCP (find it with ToolSearch): prefer`,
      `mcp__claude_ai_Linear__*, and fall back to mcp__linear-server__* if the`,
      `claude.ai connector is not authenticated. If no comment containing the`,
      `marker <!-- dispatch-triage-content --> exists on this ticket yet, post one`,
      `that: (1) starts with the literal marker line "<!-- dispatch-triage-content -->",`,
      `(2) recommends a human / /ticket-analyze flip the ticket to need-revision,`,
      `(3) explains what looks incomplete: "${(res.error || verdict.reason || "incomplete ticket").slice(0, 200)}".`,
      `DO NOT add, remove, or change ANY label — labels are human/ /ticket-analyze`,
      `owned. DO NOT remove the dispatcher-*-in-flight resume label.`,
    ].join("\n"),
    {
      label: `triage-content:${res.ticketId}`,
      phase: "fallback",
      agentType: "general-purpose",
      model: "sonnet",
    },
  );
  return res;
}

// platform-bug → visible/searchable Linear comment (Q4 = A) PLUS a local
// /_file-followup breadcrumb (GGC-23). GGC-23 narrowed (2026-06-15) to a LOCAL
// gitignored sink only — it does NOT auto-file a Linear ticket; promotion to a
// real ticket stays a manual human action. So the comment stays (it is the
// visible signal) and /_file-followup adds the durable local record that closes
// the loop's stage-8 (feedback capture).
async function triagePlatformBug(res, verdict) {
  log(`[triage] ${res.ticketId} platform-bug -> comment (dispatch-triage-platform) + /_file-followup`);
  await agent(
    [
      `Ticket ${res.ticketId} failed in the dispatcher batch and was triaged as a`,
      `suspected PLATFORM-BUG (a defect in the pipeline/platform itself, not the`,
      `ticket or infra): ${(verdict.reason || "suspected platform defect").slice(0, 200)}.`,
      `Via the connected Linear MCP (find it with ToolSearch): prefer`,
      `mcp__claude_ai_Linear__*, and fall back to mcp__linear-server__*. If no`,
      `comment containing the marker <!-- dispatch-triage-platform --> exists on`,
      `this ticket yet, post one that: (1) starts with the literal marker line`,
      `"<!-- dispatch-triage-platform -->", (2) flags the suspected platform`,
      `defect with class=platform-bug, the stage=${res.stage || "unknown"}, and`,
      `the reason "${(res.error || verdict.reason || "suspected platform defect").slice(0, 200)}".`,
      `DO NOT change ANY label. DO NOT remove the dispatcher-*-in-flight label.`,
      ``,
      `THEN, as a SEPARATE local breadcrumb (GGC-23), run the skill`,
      `/_file-followup platform-bug summary="${res.ticketId} platform-bug: ${(res.error || verdict.reason || "suspected platform defect").slice(0, 120)}" signature="${res.ticketId}:${res.stage || "unknown"}"`,
      `It appends ONE entry to the local gitignored .ggx-followups/followups.md and`,
      `is fail-soft (never blocks). NO Linear ticket / GitHub — local file only.`,
    ].join("\n"),
    {
      label: `triage-platform:${res.ticketId}`,
      phase: "fallback",
      agentType: "general-purpose",
      model: "sonnet",
    },
  );
  return res;
}

// terminal-ui-block (GGC-37) → the ONE triage class that deliberately amends the
// "script never touches labels/status" invariant. A ui-tweak dual-judge BLOCK is
// DETERMINISTIC: re-running reproduces it, so the dispatcher-dev-in-flight resume
// signal would cause an infinite recovery spin (CAF-514 / CAF-561). Clean up to a
// single visible human-attention state — remove in-flight, add need-revision, reset
// status To-Do — and post an idempotent failure comment (distinct marker
// <!-- dispatch-triage-ui-blocked -->) carrying the reason, a templated suggested
// action, and an attempt count. /ticket-analyze marker-skips a still-`Design bug`
// ticket carrying this marker (ticket-analyze.md Step 1.5) so the To-Do reset does
// NOT re-trigger a slow re-dispatch loop; reclassify Design bug → Bug lifts the skip,
// and a human can always force re-dispatch by adding ready-to-dev directly (Q3).
// The suggested action is templated from the error string (structural vs judge) — it
// does NOT read the diff (that is GGC-44 / GGC-58 scope). need-revision is reused (not
// a new tag) and is excluded from Q1–Q4 discovery, so adding it stops the spin.
async function triageTerminalUiBlock(res, item) {
  log(`[triage] ${res.ticketId} terminal-ui-block -> cleanup (remove in-flight, add need-revision, reset To-Do) + dispatch-triage-ui-blocked comment`);
  const isStructural = /structural pre-pass/.test(res.error || "");
  const worktree = (item && item.worktreePath) || `../${res.ticketId}`;
  await agent(
    [
      `Ticket ${res.ticketId} was auto-dispatched to the ui-tweak lane and was`,
      `BLOCKED by the dual-judge panel: "${(res.error || "UI-TWEAK BLOCKED").slice(0, 240)}".`,
      `This is a DETERMINISTIC terminal failure (re-running reproduces it), so the`,
      `dispatcher is cleaning it up to stop an infinite recovery spin.`,
      ``,
      `Use the connected Linear MCP (find it with ToolSearch): prefer`,
      `mcp__claude_ai_Linear__*, fall back to mcp__linear-server__* if the claude.ai`,
      `connector is not authenticated. Do ALL THREE of the following, each idempotent:`,
      ``,
      `1. LABELS (this is the whole point — the in-flight label MUST come off, or the`,
      `   ticket re-spins next sweep): read the issue's CURRENT labels via get_issue,`,
      `   then call save_issue with the FULL desired label set = (current labels)`,
      `   MINUS "dispatcher-dev-in-flight" PLUS "need-revision". save_issue's labels`,
      `   param replaces the whole set, so send every label you want to keep. Then`,
      `   re-read and VERIFY dispatcher-dev-in-flight is gone and need-revision is present.`,
      `2. STATUS: set the issue's status to the team's unstarted "To-do" state (the one`,
      `   fresh tickets sit in before work starts). If "To-do" is not an exact match,`,
      `   resolve it via list_issue_statuses → the status with type "unstarted" named`,
      `   To-do / Todo.`,
      `3. COMMENT (idempotent on the marker): list_comments on the issue. If a comment`,
      `   containing the literal marker "<!-- dispatch-triage-ui-blocked -->" ALREADY`,
      `   exists, this is a REPEAT block (a human re-armed it and it failed again) —`,
      `   UPDATE that same comment, incrementing its "attempt N" number by 1. Otherwise`,
      `   CREATE a new comment with attempt number 1. Replace every "N" below with the`,
      `   actual attempt number; keep the marker line EXACTLY as written:`,
      ``,
      `<!-- dispatch-triage-ui-blocked -->`,
      `🚫 ui-tweak auto-handling BLOCKED (attempt N)`,
      ``,
      `(1) Reason: ${(res.error || "UI-TWEAK BLOCKED").slice(0, 240)}`,
      ``,
      `(2) Suggested action:`,
      ...(isStructural
        ? [
            `    The change needs real code logic (the diff added behavior signals —`,
            `    import / call / control-flow / lifecycle / gesture), so the ui-tweak`,
            `    dual-judge panel can never pass it. Recommend reclassify \`Design bug\``,
            `    → \`Bug\` and route to the dev lane (which CAN handle logic). Do NOT`,
            `    re-add ready-to-dev while it is still a Design bug — it will just re-block.`,
          ]
        : [
            `    A judge found the change alters behavior, not just visuals. If you`,
            `    believe it is purely visual, re-run /ui-tweak:apply in ${worktree} to`,
            `    reproduce the change locally and review it; otherwise reclassify`,
            `    \`Design bug\` → \`Bug\` and route to the dev lane.`,
          ]),
      ``,
      `(3) Context:`,
      `    - failed stage: ${res.stage || "ui:audit"}`,
      `    - worktree: ${worktree} (the change was REVERTED — nothing is committed;`,
      `      the judge reason above is the authoritative signal)`,
      `    - dispatcher auto-actions: removed dispatcher-dev-in-flight, added`,
      `      need-revision, reset status to To-do`,
      `    - attempt N: first time = review / maybe reclassify; repeat = stop sending`,
      `      to ui-tweak, reclassify to Bug`,
      ``,
      `Do NOT change any label other than the two named in step 1. Do NOT remove the`,
      `marker line — /ticket-analyze detects it to skip auto re-dispatch.`,
    ].join("\n"),
    {
      label: `triage-ui-blocked:${res.ticketId}`,
      phase: "fallback",
      agentType: "general-purpose",
      model: "sonnet",
    },
  );
  return res;
}

// unknown → today's runFallback behavior verbatim. Post ONLY when no sub-pipeline
// wrote its own error comment (workerDied / uiTweakFailed / evidenceDemoted);
// a NORMAL dev/port/bug failure already has /ggx-work's <!-- ggx-work-error -->
// comment → no-op. Distinct marker <!-- dispatch-fallback-error -->.
async function triageUnknownFallback(res) {
  if (!res.workerDied && !res.uiTweakFailed && !res.evidenceDemoted) {
    log(`[fallback] ${res.ticketId} failed (sub-pipeline posted its own error) — no double-post`);
    return res;
  }
  log(`[fallback] ${res.ticketId} -> post dispatch-fallback-error (distinct marker)`);
  await agent(
    [
      `Ticket ${res.ticketId} failed in the dispatcher batch and no sub-pipeline`,
      `posted its own <!-- ggx-work-error --> comment (worker died; a`,
      `script-orchestrated ui-tweak run was BLOCKED/failed by the audit panel; or`,
      `a reported success was demoted by the terminal-evidence cross-check).`,
      `Via the connected Linear MCP (find it with ToolSearch): prefer`,
      `mcp__claude_ai_Linear__*, and fall back to mcp__linear-server__* if the`,
      `claude.ai connector is not authenticated — both target the same workspace`,
      `(per the dispatcher's Label-ownership Linear-prefix rule). If no comment`,
      `containing the marker <!-- dispatch-fallback-error --> exists on this`,
      `ticket yet, post one summarizing: "${(res.error || "worker died").slice(0, 200)}".`,
      `DO NOT remove the dispatcher-dev-in-flight / dispatcher-port-in-flight`,
      `label — it is the resume signal for the next sweep.`,
      ``,
      `THEN, as a SEPARATE local breadcrumb (GGC-23), run the skill`,
      `/_file-followup ${res.uiTweakFailed ? "design-bug-failed" : "worker-died"} summary="${res.ticketId}: ${(res.error || "worker died").slice(0, 120)}" signature="${res.ticketId}:${res.stage || "unknown"}"`,
      `It appends ONE entry to the local gitignored .ggx-followups/followups.md and`,
      `is fail-soft (never blocks). NO Linear ticket / GitHub — local file only.`,
    ].join("\n"),
    {
      label: `fallback:${res.ticketId}`,
      phase: "fallback",
      agentType: "general-purpose",
      model: "sonnet",
    },
  );
  return res;
}

// ── Main orchestration. ──
// pipeline() runs each row through both stages with NO batch barrier: a failed
// ticket's fallback runs as soon as its work stage returns (the §6.2 immediacy
// requirement), while other tickets are still in their work stage.
let roster = Array.isArray(args) ? args : [];
// GGC-49: the clean-trunk tip the dispatcher fetched before building the roster.
// May arrive as a top-level field on a wrapper object `{ trunkSha, roster }`, or
// be absent entirely (older callers passing a bare array — backward-compat).
let trunkSha = null;
// Accept the wrapper-object shape `{ trunkSha, roster: [...] }`.
const adoptWrapper = (obj) => {
  if (obj && typeof obj === "object" && Array.isArray(obj.roster)) {
    roster = obj.roster;
    if (typeof obj.trunkSha === "string" && obj.trunkSha.trim() !== "") {
      trunkSha = obj.trunkSha.trim();
    }
    return true;
  }
  return false;
};
adoptWrapper(args);
// Tolerate a stringified payload: the Workflow tool's `args` is delivered to the
// script as a JSON STRING in this harness (confirmed 2026-06-08 CAF-371 run —
// passing a live array still arrived as a string), so parse-and-retry rather
// than silently falling through to the empty-roster no-op. The parsed value may
// be a bare array (legacy) OR the `{ trunkSha, roster }` wrapper (GGC-49).
if (roster.length === 0 && typeof args === "string") {
  try {
    const parsed = JSON.parse(args);
    if (Array.isArray(parsed)) roster = parsed;
    else adoptWrapper(parsed);
  } catch (_) { /* not JSON — fall through to empty-roster handling */ }
}
if (!trunkSha) {
  log(
    "[dispatch-fanout] WARN: no trunkSha in args — worktree/branch contamination assertion (GGC-49) " +
      "will be SKIPPED for every leg (legacy roster shape). Each leg still self-asserts at base_ref time.",
  );
}

if (roster.length === 0) {
  // Smoke guard (Phase A, P2). Distinguish a GENUINELY empty roster (no
  // actionable tickets — a legitimate no-op) from `args` that DID carry work
  // but parsed to zero rows. The latter is the P2 silent-failure class: a
  // serialization/parse mismatch spawned 0 agents and looked identical to
  // "no work". Assert agent_count >= 1 whenever args carried a roster — and
  // surface the violation LOUDLY (distinct log + error field the §6.4 caller
  // inspects) instead of returning a clean no-op.
  const argsCarriedWork =
    (typeof args === "string" && args.trim() !== "" && args.trim() !== "[]") ||
    (Array.isArray(args) && args.length > 0);
  if (argsCarriedWork) {
    log(
      "[dispatch-fanout] ERROR: args carried a roster but parsed to 0 rows — " +
        "serialization/parse mismatch (P2 class), NOT a no-op. Spawned 0 agents.",
    );
    return {
      error: "roster-parse-failed",
      counts: { done: 0, "port-paused": 0, failed: 0 },
      rows: [],
    };
  }
  log("[dispatch-fanout] empty roster — nothing to fan out");
  return { counts: { done: 0, "port-paused": 0, failed: 0 }, rows: [] };
}

const results = await pipeline(
  roster,
  // stage 1: route by lane. design-bug → runUiTweak (script-spawned level-1
  // dual-judge panel, Phase B); everything else → runWork (/ggx-work --auto).
  (item) => (item.uiTweak ? runUiTweak(item, trunkSha) : runWork(item, trunkSha)),
  // stage 2: evidence cross-check (GGC-21) — demote any success row whose
  // terminal state (open PR / need-spec-review label) is definitively absent.
  // Failed rows pass straight through. originalItem carries worktreePath/lane.
  (res, item) => verifyEvidence(res, item),
  // stage 3: per-item triage + fallback (GGC-40) — classify the failed row,
  // bounded-retry transient infra (dev/port/bug only), comment per class.
  // Immediacy comes from pipeline's per-item chaining; item + trunkSha are
  // threaded so a transient row can re-invoke runWork(item, trunkSha).
  (res, item) => triageAndFallback(res, item, trunkSha),
);

// pipeline drops a throwing item to null; filter so aggregation never NPEs.
const rows = results.filter(Boolean);

// ── Final aggregation (§6.4 feed). The returned object is what the calling
// session receives; §6.4 renders its table from it directly. Outcomes are
// authoritative AFTER the GGC-21 evidence stage: success rows have had their
// terminal state cross-checked (a demoted row now reads outcome:"failed" with
// digestNote:"no-terminal-evidence"; an unverifiable-but-kept ship carries
// evidenceUnchecked), so §6.4 still needs no per-ticket get_issue re-derivation. ──
phase("aggregate");
const counts = rows.reduce(
  (a, r) => ((a[r.outcome] = (a[r.outcome] || 0) + 1), a),
  { done: 0, "port-paused": 0, failed: 0 },
);
log(
  `[aggregate] done=${counts.done} port-paused=${counts["port-paused"]} failed=${counts.failed}` +
    (rows.length !== roster.length ? ` (dropped ${roster.length - rows.length} to null)` : ""),
);

return { counts, rows };
