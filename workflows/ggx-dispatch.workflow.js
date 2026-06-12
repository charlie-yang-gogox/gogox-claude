// ggx-dispatch.workflow.js
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

export const meta = {
  name: "ggx-dispatch",
  description:
    "Fan out one /ggx-work --auto agent per locked dev/port/bug ticket; design-bug tickets run apply->dual-judge(sonnet+opus)->finish as script-spawned level-1 agents (dissolves dispatcher §5.0 inline lane). Structured per-ticket result replaces §6.1 text parsing; per-ticket failure fallback writes Linear immediately.",
  // phases is a pure literal — /workflows progress view only, no exec semantics.
  phases: [
    { title: "work", detail: "Drive each dev/port/bug ticket through /ggx-work --auto" },
    { title: "ui-judge", detail: "ui-tweak lane: apply/preview, dual-judge panel, finisher" },
    { title: "evidence", detail: "Cross-check terminal evidence for each success row (GGC-21)" },
    { title: "fallback", detail: "Per-ticket Linear writes on failure" },
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

// ── Drive one roster row through /ggx-work --auto (dev / port / bug lane). ──
// agentType general-purpose mirrors today's §5.3 subagent_type; model "opus"
// mirrors the §5.3 rationale (heavy R1 work inlines inside this worker, so it
// needs opus-class reasoning). isolation is omitted on purpose — the ff
// pipelines create their own ../<ID> worktree (same rule as §5.3).
async function runWork(item) {
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
  // failed row flagged workerDied so (a) it never NPEs the fallback stage and
  // (b) runFallback knows it must post the failure itself. A normal completed
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
// change BOTH. Failures set uiTweakFailed so runFallback posts Linear (no
// sub-pipeline posted its own error — the script owns this flow end-to-end).
async function runUiTweak(item) {
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
      `THEN compute the audit diff ONCE so the dual-judge panel does not make`,
      `each judge re-run git: read base from .dev/ui-tweak/base_ref, run`,
      "`git diff \"$BASE\"` (full text) and `git diff \"$BASE\" --name-only`",
      `(changed-file list), and return both verbatim. Do NOT truncate diffText`,
      `unless it exceeds ~200KB (then return the first ~200KB and set`,
      `diffTruncated:true so the judges know to read remaining files themselves).`,
      ``,
      `Return { ok: boolean, baseRef: string|null, diffText: string|null,`,
      `changedFiles: string|null, diffTruncated: boolean, error: string|null }.`,
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
      uiTweakFailed: true,
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
    const STRUCTURAL_RE =
      /\bimport\b|require\(|=>|\bfunction\b|\bdef\b|\bclass\b|\breturn\b|\bif\s*\(|\bfor\s*\(|\bwhile\s*\(|\bswitch\b|\bawait\b|\basync\b|\bnew\s+[A-Z]|@\+id\//;
    const structuralHit = added.some((l) => STRUCTURAL_RE.test(l));
    if (structuralHit) {
      log(`[ui] ${item.ticketId} structural pre-pass BLOCKED (logic signal in diff) — opus judge skipped`);
      return {
        ticketId: item.ticketId,
        outcome: "failed",
        prUrl: null,
        stage: "ui:audit",
        error:
          "UI-TWEAK BLOCKED (structural pre-pass): added lines contain non-inert-UI logic signals (import/call/control-flow/identifier) — reverted, no changes kept.",
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
      uiTweakFailed: true,
    };
  }

  // Stage 3: finisher — both judges CLEAR ⇒ commit → pr → review (--auto).
  log(`[ui] ${item.ticketId} CLEAR -> commit + PR`);
  const ship = await agent(
    [
      `In ${item.worktreePath} for ${item.ticketId}: the dual-judge panel is`,
      `CLEAR. Run the remaining /ui-tweak:ff stages with --auto semantics:`,
      `audit (write .dev/ui-verify-pass.md Status: CLEAR) → commit → pr → review.`,
      `Open a draft PR. Return { prUrl: string|null, stage: string, error: string|null }.`,
    ].join("\n"),
    {
      label: `ui-ship:${item.ticketId}`,
      phase: "ui-judge",
      agentType: "general-purpose",
      model: "opus",
      schema: {
        type: "object",
        required: ["stage"],
        additionalProperties: false,
        properties: {
          prUrl: { type: ["string", "null"] },
          stage: { type: "string" },
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
      uiTweakFailed: true,
    };
  }
  return {
    ticketId: item.ticketId,
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
// because runFallback only fires on outcome=="failed". This stage demands cheap
// TERMINAL evidence for every success row and demotes a row to "failed" when
// that evidence is definitively absent — routing it through runFallback's
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
//   │ port     │ port-paused  │ the `need-spec-review` label on the issue      │
//   │          │              │   (HITL spec-review gate handoff; no PR yet)   │
//   │ ui-tweak │ done         │ an OPEN draft PR — ui-tweak's PR-open IS the    │
//   │          │              │   terminal, so the same `done` check covers it │
//   │          │              │   (AC2 walker-evidence edge case, no special)  │
//   └──────────┴──────────────┴───────────────────────────────────────────────┘
// "failed" rows are not re-derived — they already route to runFallback. Only
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
          `read-only verifier — make ONE read call, then return a verdict. Do NOT`,
          `fix anything and do NOT comment on Linear.`,
          ``,
          `Expected terminal evidence for "port-paused": the ${res.ticketId} issue`,
          `carries the \`need-spec-review\` label (port handed the work to the HITL`,
          `spec-review gate; there is no PR yet, so do NOT look for one).`,
          `Make ONE read call via the connected Linear MCP (find it with ToolSearch;`,
          `prefer mcp__claude_ai_Linear__get_issue, fall back to`,
          `mcp__linear-server__get_issue) and inspect the issue's labels.`,
          ``,
          `verdict="confirmed" if the need-spec-review label is present; "missing"`,
          `if the call succeeded and the label is absent; "inconclusive" if the`,
          `call errored.`,
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
  // routes through runFallback's dispatch-fallback-error branch (evidenceDemoted
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

// ── Per-ticket fallback (§6.2): write Linear ONLY for the worker-died case. ──
// Idempotency is structural, not advisory: on a NORMAL completed failure
// (outcome:"failed", no workerDied flag) /ggx-work --auto already posted its
// own <!-- ggx-work-error --> comment (ggx-work.md:347) before exiting, so the
// script must NOT post again (that was the double-post bug). The script only
// posts when the worker DIED before /ggx-work could write its comment
// (res.workerDied) — and it uses a DISTINCT marker so the two writers can never
// collide. Neither path removes the in-flight label (the durable §6.2 resume
// signal); the script never touches labels.
async function runFallback(res) {
  if (!res) return res;                       // belt-and-suspenders (should not happen after runWork's null map)
  if (res.outcome !== "failed") return res;   // done / port-paused already wrote their own state
  // Post ONLY when no sub-pipeline wrote its own error comment:
  //   - workerDied: the worker agent died before /ggx-work could post.
  //   - uiTweakFailed: the script owns the ui-tweak flow end-to-end (prep →
  //     panel → finisher), so a BLOCKED/failed run has no /ggx-work and no
  //     <!-- ggx-work-error --> behind it — the dispatcher must post.
  //   - evidenceDemoted (GGC-21): the worker reported a SUCCESS that failed the
  //     terminal-evidence cross-check. It thought it succeeded, so it posted no
  //     error comment — the dispatcher must post so the misreport is visible.
  // A NORMAL dev/port/bug failure (outcome:"failed", none of these flags)
  // already has /ggx-work's own <!-- ggx-work-error --> comment → no-op.
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
// Tolerate a stringified array: the Workflow tool's `args` is delivered to the
// script as a JSON STRING in this harness (confirmed 2026-06-08 CAF-371 run —
// passing a live array still arrived as a string), so parse-and-retry rather
// than silently falling through to the empty-roster no-op.
if (roster.length === 0 && typeof args === "string") {
  try {
    const parsed = JSON.parse(args);
    if (Array.isArray(parsed)) roster = parsed;
  } catch (_) { /* not JSON — fall through to empty-roster handling */ }
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
      "[ggx-dispatch] ERROR: args carried a roster but parsed to 0 rows — " +
        "serialization/parse mismatch (P2 class), NOT a no-op. Spawned 0 agents.",
    );
    return {
      error: "roster-parse-failed",
      counts: { done: 0, "port-paused": 0, failed: 0 },
      rows: [],
    };
  }
  log("[ggx-dispatch] empty roster — nothing to fan out");
  return { counts: { done: 0, "port-paused": 0, failed: 0 }, rows: [] };
}

const results = await pipeline(
  roster,
  // stage 1: route by lane. design-bug → runUiTweak (script-spawned level-1
  // dual-judge panel, Phase B); everything else → runWork (/ggx-work --auto).
  (item) => (item.uiTweak ? runUiTweak(item) : runWork(item)),
  // stage 2: evidence cross-check (GGC-21) — demote any success row whose
  // terminal state (open PR / need-spec-review label) is definitively absent.
  // Failed rows pass straight through. originalItem carries worktreePath/lane.
  (res, item) => verifyEvidence(res, item),
  // stage 3: per-item fallback — immediacy comes from pipeline's per-item chaining.
  (res) => runFallback(res),
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
