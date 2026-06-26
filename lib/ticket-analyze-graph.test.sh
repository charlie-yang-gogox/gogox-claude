#!/usr/bin/env bash
# Regression tests for the Step-5 dependency-graph barrier in
# workflows/ticket-analyze-fanout.workflow.js (`buildGraph`, GGC-97).
#
# Guards the pure-JS graph stage: blocked-detection (open vs satisfied edges),
# external blockers, cycle detection (no crash, members excluded from the
# order), and a topological order whose best-start is the first unblocked
# ticket. Extracts and evals the SHIPPED function so the assertions cannot drift
# from a hand-copied duplicate (same pattern as ui-tweak-binary-clear.test.sh).
#
# scripts/prompt-lint.sh runs every lib/*.test.sh as the prompt platform's
# verify-stage test_cmd. Run directly: bash lib/ticket-analyze-graph.test.sh

set -u
HERE=$(cd "$(dirname "$0")" && pwd)
WF="$HERE/../workflows/ticket-analyze-fanout.workflow.js"
[ -f "$WF" ] || { echo "FAIL: cannot find $WF" >&2; exit 1; }

node - "$WF" <<'NODE'
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const start = src.indexOf("function buildGraph");
if (start < 0) { console.error("FAIL: buildGraph not found"); process.exit(1); }
let i = src.indexOf("{", start), depth = 0, end = -1;
for (; i < src.length; i++) {
  if (src[i] === "{") depth++;
  else if (src[i] === "}") { depth--; if (depth === 0) { end = i + 1; break; } }
}
// eslint-disable-next-line no-eval
const buildGraph = eval("(" + src.slice(start, end) + ")");

let pass = 0, fail = 0;
const check = (name, got, want) => {
  const g = JSON.stringify(got), w = JSON.stringify(want);
  if (g === w) { console.log(`PASS ${name}: ${g}`); pass++; }
  else { console.log(`FAIL ${name}: got=${g} want=${w}`); fail++; }
};
const blk = (id, edges) => ({ ticketId: id, blockingEdges: edges || [] });

// 1. Linear chain A → B → C (each blocked by the prior, all open).
{
  const g = buildGraph([
    blk("A", []),
    blk("B", [{ to: "A", open: true }]),
    blk("C", [{ to: "B", open: true }]),
  ]);
  check("chain order", g.order, ["A", "B", "C"]);
  check("chain best-start", g.bestStart, "A");
  check("chain A unblocked", g.result.get("A").blocked, false);
  check("chain C blocked", g.result.get("C").blocked, true);
}

// 2. Satisfied edge (blocker Done) → unblocked.
{
  const g = buildGraph([blk("B", [{ to: "A", open: false }])]);
  check("satisfied → unblocked", g.result.get("B").blocked, false);
  check("satisfied best-start", g.bestStart, "B");
}

// 3. External open blocker (target not in queue) → blocked, excluded from order.
{
  const g = buildGraph([blk("A", [{ to: "EXT-9", open: true }])]);
  check("external blocker → blocked", g.result.get("A").blocked, true);
  check("external blocker → not in order", g.order, []);
}

// 4. Cycle A ↔ B → both flagged, no crash, excluded from order.
{
  const g = buildGraph([
    blk("A", [{ to: "B", open: true }]),
    blk("B", [{ to: "A", open: true }]),
  ]);
  check("cycle members", g.cycleMembers.sort(), ["A", "B"]);
  check("cycle A blocked", g.result.get("A").blocked, true);
  check("cycle → empty order", g.order, []);
}

console.log(`ticket-analyze-graph.test: ${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
NODE
