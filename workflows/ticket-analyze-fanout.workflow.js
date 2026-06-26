// ── ticket-analyze fan-out (GGC-97 / P2 of the analyzer evolution) ──
//
// Throughput substrate for /ticket-analyze: the serial single-session sweep
// (~2.5h) re-shaped as a Workflow fan-out so the analyzer keeps up with PM/QA
// inflow. Mirrors workflows/dispatch-fanout.workflow.js (the proven GGC-55
// Workflow-only fan-out pattern).
//
// THIS IS A THIN HARNESS, NOT A RE-IMPLEMENTATION. The per-ticket judgment
// (Step 2.7 auto-classify 2-vote, Step 3 completeness + logic-prediction,
// Step 8 label-only writes with the F5 fresh-label rebase) lives in
// commands/dev/ticket-analyze.md and stays the single source of truth — each
// agent below is told to APPLY those steps, so there is no logic to drift out
// of sync (the GGC-63 duplication trap). The script owns only: fan-out, the
// cross-item dependency-graph BARRIER (Step 5, pure JS — it needs every
// ticket's edges at once), and the run-level digest/metrics.
//
// args: the post-discovery roster — an array of { ticketId } (and optionally
// url/labels) produced by the caller's Step 1.5 discovery + re-analysis filter
// (the skill / cloud routine does discovery; the analyzer is label-only and
// idempotent, so unlike the dispatcher there is no race-lock to own here).
//
// Returns { counts, rows } for the caller's report (Step 9) + digest (Step 10).

export const meta = {
  name: "ticket-analyze-fanout",
  description:
    "Fan out /ticket-analyze per-ticket analysis: classify (2-vote, GGC-96) + completeness in parallel, BARRIER to build the dependency graph over the whole set (Step 5), then fan out label-only writes (Step 8, F5 fresh-label rebase). Thin harness — judgment lives in ticket-analyze.md; the script owns fan-out + the graph barrier + the run digest. Label-only: never assigns (pull model).",
  phases: [
    { title: "analyze", detail: "per-ticket: Step 2.7 classify (haiku→sonnet 2-vote) + Step 3 completeness (sonnet)" },
    { title: "graph", detail: "BARRIER: dependency graph + topo order + best-start (pure JS, Step 5)" },
    { title: "write", detail: "per-ticket: label-only writes + comment (Step 8, F5), never assign" },
    { title: "digest", detail: "run-level Slack digest + F2 unclaimed-ready / assign-latency" },
  ],
};

// Per-ticket analysis decision (judgment only — no writes happen in this stage).
const ANALYZE_SCHEMA = {
  type: "object",
  required: ["ticketId", "held", "lane", "completeness"],
  additionalProperties: false,
  properties: {
    ticketId: { type: "string" },
    // analyze-hold guard (GGC-60): a human-parked ticket is dropped — no write,
    // no comment, no verdict.
    held: { type: "boolean" },
    lane: { type: "string", enum: ["port", "feature", "bug", "ui-tweak", "unknown"] },
    // Step 2.7 auto-classification outcome (GGC-96). wroteClass=true ⇒ the write
    // stage must persist `classLabel` + the ta-class:v1 marker. classSource lets
    // the write stage honor the sticky-override gate (human ⇒ never re-flip).
    wroteClass: { type: "boolean" },
    classLabel: { type: ["string", "null"] },
    classSource: { type: ["string", "null"], enum: ["analyzer", "human", null] },
    completeness: { type: "string", enum: ["complete", "incomplete"] },
    reasons: { type: "array", items: { type: "string" } },
    // human-tail disambiguation (Q3): which need-revision flavor the comment must use.
    revisionKind: { type: ["string", "null"], enum: ["content-incomplete", "cannot-classify", null] },
    // blocking edges for the Step 5 graph barrier. `open` = target still open.
    blockingEdges: {
      type: "array",
      items: {
        type: "object",
        required: ["to", "open"],
        additionalProperties: true,
        properties: { to: { type: "string" }, open: { type: "boolean" }, source: { type: "string" } },
      },
    },
    error: { type: ["string", "null"] },
  },
};

const WRITE_SCHEMA = {
  type: "object",
  required: ["ticketId", "outcome"],
  additionalProperties: false,
  properties: {
    ticketId: { type: "string" },
    outcome: { type: "string", enum: ["analyzed", "skipped", "errored"] },
    targetLabel: { type: ["string", "null"] },
    detail: { type: ["string", "null"] },
  },
};

// ── Step 5 dependency graph (pure JS — the BARRIER stage). Exported-shape
// helper kept side-effect free so lib/ticket-analyze-graph.test.sh can extract
// and exercise it (same extract-and-eval pattern as the ui-tweak binary test).
// Input: analyzed rows (each with ticketId + blockingEdges). Output: per-ticket
// { blocked, blockers, cycle } + a topological order over the unblocked subgraph.
function buildGraph(rows) {
  const ids = new Set(rows.map((r) => r.ticketId));
  // inbound blocking edges from an OPEN target (in-queue or external).
  const inbound = new Map(rows.map((r) => [r.ticketId, []]));
  for (const r of rows) {
    for (const e of r.blockingEdges || []) {
      if (!e.open) continue; // satisfied (Done/canceled target) — not blocking
      inbound.get(r.ticketId).push(e.to);
    }
  }
  // cycle detection over in-queue edges (external targets can't form a cycle here).
  const color = new Map(); // 0=unvisited,1=visiting,2=done
  const inCycle = new Set();
  const adj = new Map(rows.map((r) => [r.ticketId, []]));
  for (const r of rows) for (const t of inbound.get(r.ticketId)) if (ids.has(t)) adj.get(t).push(r.ticketId);
  const stack = [];
  const dfs = (n) => {
    color.set(n, 1);
    stack.push(n);
    for (const m of adj.get(n) || []) {
      if (color.get(m) === 1) {
        // back-edge → mark the whole current stack segment as in a cycle
        const i = stack.indexOf(m);
        for (let k = i; k < stack.length; k++) inCycle.add(stack[k]);
      } else if (!color.get(m)) dfs(m);
    }
    stack.pop();
    color.set(n, 2);
  };
  for (const r of rows) if (!color.get(r.ticketId)) dfs(r.ticketId);

  const blockedOf = (id) =>
    inCycle.has(id) || inbound.get(id).length > 0;
  const result = new Map();
  for (const r of rows) {
    result.set(r.ticketId, {
      blocked: blockedOf(r.ticketId),
      blockers: inbound.get(r.ticketId).slice(),
      cycle: inCycle.has(r.ticketId),
    });
  }
  // topological order over the unblocked-reachable subgraph (cycle members excluded).
  const order = [];
  const placed = new Set();
  const ready = rows
    .filter((r) => !result.get(r.ticketId).blocked)
    .map((r) => r.ticketId);
  // simple Kahn-ish: unblocked tickets first; downstream tickets follow once all
  // their in-queue blockers are placed.
  let changed = true;
  ready.forEach((id) => (order.push(id), placed.add(id)));
  while (changed) {
    changed = false;
    for (const r of rows) {
      if (placed.has(r.ticketId) || result.get(r.ticketId).cycle) continue;
      const blk = inbound.get(r.ticketId).filter((t) => ids.has(t));
      if (blk.length && blk.every((t) => placed.has(t))) {
        order.push(r.ticketId);
        placed.add(r.ticketId);
        changed = true;
      }
    }
  }
  const bestStart = order[0] || null;
  return { result, order, bestStart, cycleMembers: [...inCycle] };
}

// ── Stage 1: per-ticket analyze (classify 2-vote + completeness). Run inside a
// parallel() barrier so the graph stage sees every ticket's edges at once.
async function analyzeTicket(item) {
  const id = item.ticketId;
  const r = await agent(
    [
      `Analyze Linear ticket ${id} by applying commands/dev/ticket-analyze.md`,
      `to THIS ONE ticket (single-ticket semantics), steps 2 → 2.7 → 3 → 4,`,
      `then RETURN the decision (do NOT write anything — the write stage owns all`,
      `mutations). Specifically:`,
      `- Step 2: fetch title/description/labels/relations/comments; derive lane.`,
      `  If labels contain analyze-hold → held:true and stop (no further work).`,
      `- Step 2.7 (GGC-96): if lane is unknown, run the auto-classification`,
      `  decision EXACTLY as written — Gate 0 sticky-override (read the newest`,
      `  ta-class:v1 marker; if current classification differs from it, the class`,
      `  is human-owned: set classSource="human", do NOT propose a new one), then`,
      `  the decorrelated 2-vote. IMPORTANT: you are ONE agent, so emulate the`,
      `  2-vote by reasoning the haiku-proposer judgment AND an independent`,
      `  second-pass confirmer judgment, and only set wroteClass:true for a`,
      `  STRONG bug/design-bug consensus.`,
      `- Step 2.7 Gate 1b (GGC-98, port-vs-feature grounding — LOCAL ONLY,`,
      `  fail-closed): for a port/feature lean, resolve the origin from`,
      "  `<repo-root>/.claude/port-settings.json` (jq -r .originalProjectPath,",
      `  expand ~/$ENV). If missing/empty/not a directory on disk, OR the current`,
      `  repo has no working tree (cloud run) → do NOT guess: wroteClass:false,`,
      `  revisionKind:"cannot-classify" (human tail). Otherwise scan READ-ONLY`,
      `  (Grep/Glob/Read, never write): present in origin + absent in current →`,
      `  port; net-new → feature; inconclusive/partial → human tail. Confident`,
      `  port/feature → wroteClass:true + classLabel. Else → wroteClass:false,`,
      `  revisionKind:"cannot-classify".`,
      `- Step 3: completeness against the resolved lane's checklist (+ the GGC-58`,
      `  logic-prediction sub-judgment for design bug). reasons[] = the missing`,
      `  items. If incomplete for content reasons set revisionKind:"content-incomplete".`,
      `- Step 4: capture blocking edges (explicit relations of kind blocks/`,
      `  blocked-by); for each, resolve the target's live status → open:true unless`,
      `  the target is Done/canceled. Inferred edges are report-only — never set`,
      `  open:true on an unconfirmed inferred edge in --non-interactive fan-out.`,
      `Return the ANALYZE_SCHEMA object. The analyzer NEVER assigns — assignee is`,
      `out of scope here and in the write stage.`,
    ].join("\n"),
    {
      label: `analyze:${id}`,
      phase: "analyze",
      agentType: "general-purpose",
      // Tiering (GGC-97): per-ticket classify+completeness is judgment over text
      // — sonnet, not opus (no implementation happens here). The internal 2-vote
      // decorrelation is emulated within the sonnet pass; a true cross-tier split
      // is a refinement (would be a haiku pre-pass) tracked under measurement.
      model: "sonnet",
      schema: ANALYZE_SCHEMA,
    },
  );
  // agent() returns null on user-skip / terminal API error — synthesize an
  // errored row so the barrier never NPEs and the batch is not aborted (AC1).
  if (!r) {
    return {
      ticketId: id,
      held: false,
      lane: "unknown",
      wroteClass: false,
      classLabel: null,
      classSource: null,
      completeness: "incomplete",
      reasons: ["analyze agent died (null result)"],
      revisionKind: null,
      blockingEdges: [],
      error: "analyze-agent-null",
    };
  }
  return r;
}

// ── Stage 3: per-ticket write (label-only). Computes the target analyzer label
// from completeness + the graph verdict, then persists label + comment with the
// F5 fresh-label rebase. NEVER assigns.
async function writeTicket(row, graphInfo) {
  const id = row.ticketId;
  const g = graphInfo.get(id) || { blocked: false, blockers: [], cycle: false };
  // Step 6 decision matrix → analyzer target label.
  let target;
  if (row.completeness === "incomplete") target = "need-revision";
  else if (g.blocked) target = "need-dependency";
  else target = row.lane === "port" ? "ready-to-port" : "ready-to-dev";
  // (the design-bug ready-to-dev gate / GGC-37+58 is applied inside the agent
  // per Step 6/8.2b — pass the lane + reasons so it can hold when required.)

  const res = await agent(
    [
      `Persist the /ticket-analyze verdict for ${id} by applying ticket-analyze.md`,
      `Step 8 (writes are read-before-write, LABEL-ONLY — never set assignee).`,
      `Inputs from the analysis + graph stages:`,
      `  lane=${row.lane} completeness=${row.completeness} blocked=${g.blocked}`,
      `  blockers=${(g.blockers || []).join(",") || "none"} cycle=${g.cycle}`,
      `  target_analyzer_label=${target}`,
      `  wroteClass=${row.wroteClass} classLabel=${row.classLabel || "—"} classSource=${row.classSource || "—"}`,
      `  revisionKind=${row.revisionKind || "—"} reasons=${JSON.stringify(row.reasons || [])}`,
      `Do, in order:`,
      `1. Step 8.2 pre-write check INCLUDING F5: re-fetch comments+labels; the`,
      `   FRESH label set is the authoritative rewrite base. Hard conflict`,
      `   (foreign ticket-analysis:v1 newer than now, dispatcher-*-in-flight,`,
      `   analyze-hold added, or the classification changed since analysis) →`,
      `   outcome="skipped". Benign label drift → keep, rebase on the fresh set.`,
      `2. If wroteClass and classSource!="human": write the classification label`,
      `   (full-set rebase on fresh labels) + post the ta-class:v1 marker (§A2).`,
      `   Honor Gate 0: if a human changed it, skip (never re-flip).`,
      `3. Apply the Design-bug ready-to-dev gate (GGC-37 marker + GGC-58 logic`,
      `   prediction) before any ready-to-dev: hold to need-revision when it fires.`,
      `4. Write the analyzer label = fresh_labels − {ready-to-port,ready-to-dev,`,
      `   need-revision,need-dependency} + ${target} (preserve everything else).`,
      `   Already at target → skip the label write.`,
      `5. Post the ticket-analysis:v1 comment (§A). For need-revision, use the`,
      `   revisionKind to pick the wording: "content-incomplete" lists what to`,
      `   add; "cannot-classify" says the lane couldn't be auto-classified and`,
      `   asks the human to set one (+ suggested lane). NEVER write an assignee.`,
      `Return WRITE_SCHEMA: outcome analyzed|skipped|errored, targetLabel, detail.`,
    ].join("\n"),
    {
      label: `write:${id}`,
      phase: "write",
      agentType: "general-purpose",
      // Mechanical MCP read-before-write + comment — the cheap tier.
      model: "haiku",
      schema: WRITE_SCHEMA,
    },
  );
  if (!res) return { ticketId: id, outcome: "errored", targetLabel: target, detail: "write-agent-null" };
  return res;
}

// ════════════════════════════════ orchestration ════════════════════════════

const roster = Array.isArray(args) ? args.filter((x) => x && x.ticketId) : [];
if (!roster.length) {
  log("[analyze-fanout] empty roster — nothing to analyze");
  return { counts: { analyzed: 0, skipped: 0, errored: 0 }, rows: [] };
}
log(`[analyze-fanout] roster: ${roster.length} ticket(s)`);

// Phase 1 — analyze, BARRIER. The graph stage needs every ticket's edges at
// once (Step 5), so this is one of the rare legitimate barriers: collect all
// analyses before building the graph. A thrown thunk resolves to null → filtered.
phase("analyze");
const analyzedRaw = await parallel(roster.map((item) => () => analyzeTicket(item)));
const analyzed = analyzedRaw.filter(Boolean);
// analyze-hold tickets are dropped here (GGC-60) — no write, no comment.
const held = analyzed.filter((r) => r.held);
const live = analyzed.filter((r) => !r.held);
if (held.length) log(`[analyze-fanout] ${held.length} skipped — human-parked (analyze-hold)`);

// Phase 2 — dependency graph (pure JS, no agents). The BARRIER's payoff.
phase("graph");
const { result: graphInfo, order, bestStart, cycleMembers } = buildGraph(live);
if (cycleMembers.length) log(`[analyze-fanout] ⚠ CYCLE among: ${cycleMembers.join(" ↔ ")}`);
log(`[analyze-fanout] order: ${order.join(" → ") || "(none unblocked)"}${bestStart ? ` · start=${bestStart}` : ""}`);

// Phase 3 — writes, fan out. Independent per ticket (label-only; F5 makes the
// full-set rewrite safe against concurrent human label edits).
phase("write");
const written = (await parallel(live.map((row) => () => writeTicket(row, graphInfo)))).filter(Boolean);

// Phase 4 — digest + F2 metrics. unclaimedReady = tickets we just made
// ready-to-* that carry no assignee (the "looks done but does nothing" risk the
// review flagged): surface them so a human pulls. assign-latency is computed by
// the caller from Linear history; the digest just names the candidates.
phase("digest");
const rows = written;
const counts = rows.reduce(
  (a, r) => ((a[r.outcome] = (a[r.outcome] || 0) + 1), a),
  { analyzed: 0, skipped: 0, errored: 0 },
);
const readyRows = rows.filter((r) => r.targetLabel === "ready-to-dev" || r.targetLabel === "ready-to-port");
log(
  `[analyze-fanout] analyzed=${counts.analyzed} skipped=${counts.skipped + held.length} errored=${counts.errored} · ready=${readyRows.length}`,
);
log(`[analyze-fanout] F2 unclaimed-ready (need a human to pull): ${readyRows.map((r) => r.ticketId).join(", ") || "none"}`);

return {
  counts,
  rows,
  graph: { order, bestStart, cycleMembers },
  held: held.map((r) => r.ticketId),
  readyUnclaimed: readyRows.map((r) => r.ticketId),
};
