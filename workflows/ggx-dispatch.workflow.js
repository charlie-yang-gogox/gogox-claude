// ggx-dispatch.workflow.js
//
// Phase A of the /ggx-dispatcher fan-out → Workflow migration (R5 in
// ARCHITECTURE.md "Nested-spawn constraint"). Replaces the §5.3 N×Agent
// fan-out + §6.1 wait loop + §6.2 per-ticket fallback + §6.4 aggregation
// for the dev / port / bug lanes ONLY.
//
// Scope (Phase A):
//   - dev / port / bug lane tickets → one /ggx-work --auto agent each.
//   - ui-tweak (design bug) tickets are NOT handled here. The markdown caller
//     filters them out of the roster and runs them §5.0-inline as today
//     (Phase B moves them into runUiTweak — see the design doc). A uiTweak row
//     reaching this script is a caller bug; it is rejected loudly (see guard).
//
// Why this dissolves nothing in Phase A but is still safe: every agent() below
// is spawned BY THE SCRIPT, so it is level-1 (the nested-spawn constraint does
// not apply between a workflow script and its agents). The heavy ff stages
// (R1: /opsx:apply, /code-review, /port:explore, /port:synth) still inline
// INSIDE each worker agent — migration does not change R1.
//
// args = DISPATCH_ROSTER: JSON array of
//   { ticketId, lane, worktreePath, url, uiTweak: boolean }
// serialized by the markdown main session after §4.3 locking completes.

export const meta = {
  name: "ggx-dispatch",
  description:
    "Phase A: fan out one /ggx-work --auto agent per locked dev/port/bug " +
    "ticket; structured per-ticket result replaces §6.1 text parsing; " +
    "per-ticket failure fallback writes Linear immediately. ui-tweak rows " +
    "are rejected (they run §5.0-inline in the markdown caller until Phase B).",
  // phases is a pure literal — /workflows progress view only, no exec semantics.
  phases: [
    { title: "work", detail: "Drive each ticket through /ggx-work --auto" },
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

// ── Defensive guard: a uiTweak row must NOT reach the Phase A script. ──
// In Phase A the markdown caller runs design-bug tickets §5.0-inline and
// excludes them from the roster. If one slips through, do NOT drive it through
// runWork — that would route to /ui-tweak:ff, whose opus judge would then be a
// LEVEL-2 spawn (worker→judge), the exact failure the inline lane avoids.
// Reject it loudly so the operator sees the misconfiguration.
function rejectUiTweak(item) {
  log(`[work] ${item.ticketId} REJECTED — uiTweak row reached Phase A script`);
  return {
    ticketId: item.ticketId,
    outcome: "failed",
    prUrl: null,
    stage: null,
    error:
      "ui-tweak (design bug) ticket reached the Phase A workflow script; " +
      "it must run §5.0-inline in the dispatcher main session until Phase B. " +
      "Caller should have excluded uiTweak rows from the roster.",
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
  if (!res.workerDied) {
    // /ggx-work completed its own failure path and posted ggx-work-error. No-op.
    log(`[fallback] ${res.ticketId} failed (worker completed; ggx-work-error already posted) — no double-post`);
    return res;
  }
  log(`[fallback] ${res.ticketId} worker died -> post dispatch-fallback-error (distinct marker)`);
  await agent(
    [
      `Ticket ${res.ticketId}'s worker agent died before /ggx-work could finish,`,
      `so no <!-- ggx-work-error --> comment was posted.`,
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
const roster = Array.isArray(args) ? args : [];

if (roster.length === 0) {
  log("[ggx-dispatch] empty roster — nothing to fan out");
  return { counts: { done: 0, "port-paused": 0, failed: 0 }, rows: [] };
}

const results = await pipeline(
  roster,
  // stage 1: drive the ticket (or reject if a uiTweak row slipped through).
  (item) => (item.uiTweak ? rejectUiTweak(item) : runWork(item)),
  // stage 2: per-item fallback — immediacy comes from pipeline's per-item chaining.
  (res) => runFallback(res),
);

// pipeline drops a throwing item to null; filter so aggregation never NPEs.
const rows = results.filter(Boolean);

// ── Final aggregation (§6.4 feed). The returned object is what the calling
// session receives; §6.4 renders its table from it directly, with no per-ticket
// re-derivation via get_issue (WORK_SCHEMA already carries the outcome). ──
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
