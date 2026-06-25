// pr-resolver-batch.workflow.js
//
// GGC-92 — migrate `/ggx-pr-resolver --batch` fan-out from the Agent tool to the
// Workflow tool, mirroring the dispatcher's R5 pattern (GGC-55,
// workflows/dispatch-fanout.workflow.js). One fan-out mechanism across the
// platform instead of two, and `/ggx-on-duty` Leg 2 can own this as a top-level
// task (D22-style) instead of wrapping it in a headless `claude -p` child only
// to dodge the nested-spawn wall.
//
// Why Workflow fits the resolver with ZERO workarounds (the property the
// dispatcher does NOT have): the per-PR unit is a LEAF. `/resolve-conflict
// --callee` is inline (never spawns), tests run in the worker foreground, the
// thread judge runs in the worker. Workflow-spawned agents have no Agent tool
// (GGC-90) — they cannot spawn level-2 — but the resolver unit never needs to,
// so there is nothing to work around. The dispatcher's worker DOES need to spawn
// (verify-agent inside /dev:verify), which is why it still leans on R4 headless;
// the resolver does not. GGC-91 is the prerequisite that made this clean: the
// single-PR unit now owns its full procedure incl. its own named Push stage, so
// this script just pipelines N identical leaf units.
//
// Why this is safe (same invariant as dispatch-fanout): every agent() below is
// spawned BY THE SCRIPT, so it is level-1; the nested-spawn constraint does not
// apply between a workflow script and its agents. The heavy per-PR work
// (/resolve-conflict --callee, /check-test, /resolve-pr-comments) inlines INSIDE
// each unit agent — migration does not change the unit.
//
// FAIL-FAST / reversibility (AC6): the `Workflow`-tool-unavailable branch lives
// in the CALLER (commands/dev/ggx-pr-resolver.md "Batch mode") — a script cannot
// detect its own non-invocation. The caller mirrors the dispatcher's §5.2
// invocation-error branch: if the Workflow tool errors/unavailable, the batch
// FAILS FAST (no silent fallback to the old N×Agent path). Revert path: this
// change is a single commit; `git revert` restores the N×Agent fan-out.
//
// args (serialized by the markdown "Batch mode" top-level sweep AFTER it has
// applied the skip-set + the cheap MERGED/CLOSED + dirty pre-spawn filters):
//   {
//     roster: [ { pr:<number>, headRefName:<str>, baseRef:<str>, ticketId:<str|null> } ],
//     prefiltered: [ { pr, headRefName, reason:"merged"|"closed"|"worktree-dirty", files?:[...] } ],
//     platform: <str>,          // resolved repo platform — gates the flutter toolchain pre-flight
//     stashDirty: <bool>,       // --stash-dirty passthrough (default false)
//     waveSize: <number>        // optional concurrency cap (default 4)
//   }
//
// The roster carries ONLY spawn-worthy PRs: PRs already merged/closed at launch,
// or carrying non-residue dirty worktrees, are pre-filtered in the markdown and
// arrive in `prefiltered` (reported with ZERO agents spawned — P0-2 acceptance).
// The in-unit step-1 (MERGED/CLOSED) and step-4 (dirty guard) stay as the second
// line of defence for PRs that merge or go dirty mid-run.

export const meta = {
  name: "pr-resolver-batch",
  description:
    "Fan out one /ggx-pr-resolver single-PR unit (GGC-91) per candidate PR, in waves of 4-5 to hold the concurrency cap (P1-2). A single pre-flight agent resolves the fvm-aware FLUTTER_BIN once; the script injects it verbatim into every unit prompt (P1-3/P-B). Structured per-PR results feed the markdown run report.",
  // phases is a pure literal — /workflows progress view only, no exec semantics.
  phases: [
    { title: "preflight", detail: "Resolve the fvm-aware FLUTTER_BIN once (flutter repos); fail-fast if a pinned SDK can't run" },
    { title: "resolve", detail: "Run the single-PR unit (rebase -> comments -> push) per PR, in waves of 4-5" },
    { title: "aggregate", detail: "Collect structured per-PR results into the run summary" },
  ],
};

// ── Pre-flight toolchain result (P1-3 / P-B). Resolved by ONE agent because the
// fvm probe is shell and the script cannot run shell. The script then injects
// `flutterBin` verbatim into every unit prompt — the hand-off is structural, so
// it cannot be dropped the way the 2026-06-08 retro's markdown→prompt chain did. ──
const PREFLIGHT_SCHEMA = {
  type: "object",
  required: ["flutterBin", "failFast"],
  additionalProperties: false,
  properties: {
    // The literal string every unit must use, e.g. "fvm flutter" or "flutter".
    // "" for non-flutter repos (or an unpinned repo with no flutter at all — the
    // units on such repos never call flutter).
    flutterBin: { type: "string" },
    // true ONLY when the repo PINS its SDK (.fvmrc / .fvm/fvm_config.json) but
    // fvm cannot run it — a per-worker fallback to system flutter would split the
    // toolchain across PRs (the 3.41.6-vs-3.38.7 split P1-3 exists to prevent),
    // so the WHOLE batch refuses to start.
    failFast: { type: "boolean" },
    reason: { type: ["string", "null"] },
  },
};

// ── Structured per-PR result — replaces the N×Agent text aggregation. Mirrors
// the step-8 exit shapes of the unit in commands/dev/ggx-pr-resolver.md. The
// schema forces a StructuredOutput tool call so agent() returns a validated
// object, not free text. Stable field names callers key off (up-to-date /
// judged-clean / pushedSha / assumptions / needs-human) are preserved. ──
const UNIT_SCHEMA = {
  type: "object",
  required: ["pr", "outcome"],
  additionalProperties: false,
  properties: {
    pr: { type: "number" },
    // resolved   = the unit ran the worktree stages (rebase and/or comments) and pushed.
    // skipped    = the two-stage gate found nothing to do (see skipReason).
    // needs-human = a guard stopped the unit (see needsHuman); nothing pushed.
    outcome: { type: "string", enum: ["resolved", "skipped", "needs-human"] },
    skipReason: { type: ["string", "null"] }, // "up-to-date" | "judged-clean" | null
    rebased: { type: "boolean" },
    pushedSha: { type: ["string", "null"] },
    // {fix, reply, stale, defer} when the comments stage ran; null otherwise.
    comments: { type: ["object", "null"] },
    held: { type: "array" }, // HOLD threads surfaced for the human (advisory)
    resolvedThreadIds: { type: "array" },
    // Tier-1 auto-assume ledger forwarded from the callee (GGC-15): each entry
    // {file, region, assumption, heuristic}. Empty when the rebase was clean.
    assumptions: { type: "array" },
    // conflict | tests-failed | worktree-dirty | comment-fix-failed-tests | push-failed | null
    needsHuman: { type: ["string", "null"] },
    needsHumanFiles: { type: "array" }, // non-residue file list for worktree-dirty
    error: { type: ["string", "null"] },
  },
};

// ── Pre-flight: resolve the flutter toolchain ONCE (P1-3). flutter repos only;
// android/ios/prompt repos skip the probe (their units never call flutter). ──
async function resolveToolchain(platform) {
  if (platform !== "flutter") {
    log(`[preflight] platform=${platform} — no flutter toolchain to resolve (flutterBin="")`);
    return { flutterBin: "", failFast: false, reason: `non-flutter platform (${platform})` };
  }
  log("[preflight] resolving fvm-aware FLUTTER_BIN once for the whole batch");
  const pf = await agent(
    [
      `Resolve the flutter toolchain for this batch ONCE and report it. Run exactly`,
      `this probe in the main worktree (the same block as commands/dev/ggx-pr-resolver.md`,
      `Batch-mode pre-flight and ui-tweak/start.md's resolved-env block):`,
      "",
      "```bash",
      `probe() { eval "$1 --version" >/dev/null 2>&1; }`,
      `FVM_BIN=$(command -v fvm 2>/dev/null || true)`,
      `[ -z "$FVM_BIN" ] && [ -x "$HOME/.pub-cache/bin/fvm" ] && FVM_BIN="$HOME/.pub-cache/bin/fvm"`,
      `PINNED=0; { [ -f .fvmrc ] || [ -f .fvm/fvm_config.json ]; } && PINNED=1`,
      `FLUTTER_BIN=""`,
      `if [ "$PINNED" = 1 ] && [ -n "$FVM_BIN" ] && probe "$FVM_BIN flutter"; then`,
      `  FLUTTER_BIN="$FVM_BIN flutter"            # pinned repo + working fvm`,
      `elif [ "$PINNED" = 1 ]; then`,
      `  echo "FAILFAST: repo pins its SDK via fvm but fvm could not run it"; `,
      `elif probe flutter; then`,
      `  FLUTTER_BIN="flutter"                     # unpinned repo -> system flutter`,
      `elif [ -n "$FVM_BIN" ] && probe "$FVM_BIN flutter"; then`,
      `  FLUTTER_BIN="$FVM_BIN flutter"`,
      `fi`,
      `echo "FLUTTER_BIN=$FLUTTER_BIN"`,
      "```",
      "",
      `Rules for your structured return:`,
      `- If the probe printed FAILFAST (pinned SDK but fvm cannot run it): return`,
      `  failFast=true, flutterBin="", reason naming the problem. The batch will`,
      `  refuse to start — a per-worker fallback to system flutter would split the`,
      `  toolchain across PRs (P1-3). Do NOT attempt any other fallback.`,
      `- If FLUTTER_BIN resolved to a non-empty string: return failFast=false,`,
      `  flutterBin=<that exact string> (e.g. "fvm flutter" or "flutter"),`,
      `  reason=null.`,
      `- If FLUTTER_BIN is empty AND the repo is not pinned (no working flutter`,
      `  found at all): return failFast=true, flutterBin="", reason="no working`,
      `  flutter found (tried fvm + bare flutter)". A flutter batch with no SDK`,
      `  cannot run tests.`,
      `Report ONLY the literal resolved string — do not paraphrase or add a path.`,
    ].join("\n"),
    {
      label: "preflight:toolchain",
      phase: "preflight",
      agentType: "general-purpose",
      schema: PREFLIGHT_SCHEMA,
    },
  );
  if (!pf) {
    // Agent died before it could report — treat as fail-fast (we cannot prove a
    // consistent toolchain, and an inconsistent one is exactly P1-3's failure).
    return {
      flutterBin: "",
      failFast: true,
      reason: "pre-flight toolchain agent returned null (skipped or terminal API error) — refusing to start the batch with an unknown SDK",
    };
  }
  return pf; // validated against PREFLIGHT_SCHEMA
}

// ── The per-PR unit (GGC-91 self-contained leaf). One script-spawned level-1
// agent per PR. It runs the FULL "Per-PR procedure" (steps 1-8) from
// commands/dev/ggx-pr-resolver.md against this PR's worktree and ends by owning
// its push (step 7). FLUTTER_BIN is injected verbatim and re-probing is
// FORBIDDEN (P-B): the commander decides the SDK, the worker obeys. ──
async function runUnit(item, flutterBin, stashDirty) {
  log(`[resolve] PR #${item.pr} (${item.headRefName})`);
  const result = await agent(
    [
      `Run the /ggx-pr-resolver SINGLE-PR UNIT for PR #${item.pr}.`,
      ``,
      `Read commands/dev/ggx-pr-resolver.md "Per-PR procedure (the unit both`,
      `modes run)" and execute steps 1-8 verbatim against THIS PR. This is the`,
      `batch path, so behave exactly as a batch worker: --auto is in effect`,
      `(the /resolve-pr-comments strategy table is auto-approved; never prompt —`,
      `AskUserQuestion deadlocks a background agent).`,
      ``,
      `PR context (already resolved by the top-level sweep — trust it, but step 1`,
      `still re-checks MERGED/CLOSED and step 4 still runs the dirty guard, as the`,
      `second line of defence for a PR that changed since the sweep):`,
      `  pr           = ${item.pr}`,
      `  headRefName  = ${item.headRefName}`,
      `  baseRef      = ${item.baseRef}`,
      `  ticketId     = ${item.ticketId == null ? "(none — grep headRefName for [A-Z]+-[0-9]+; PR-comment fallback if none)" : item.ticketId}`,
      ``,
      // P-B / P1-3 — the exact worker-spec wording ggx-pr-resolver.md requires.
      flutterBin
        ? [
            `TOOLCHAIN (P1-3 / P-B): use the FLUTTER_BIN handed to you —`,
            `  FLUTTER_BIN="${flutterBin}"`,
            `— for every flutter / dart / /check-test invocation. Do NOT run`,
            `\`fvm\`, \`command -v\`, or any probe to find your own: the orchestrator`,
            `already decided the toolchain for the whole batch. Re-probing is the`,
            `P-B defect this contract exists to prevent.`,
          ].join("\n")
        : `TOOLCHAIN: this is not a flutter repo — no flutter binary is involved.`,
      ``,
      stashDirty
        ? `--stash-dirty IS set: step 4's dirty guard may stash non-residue tracked changes (labeled, git-stash-pop-recoverable) instead of reporting needs-human:worktree-dirty.`
        : `--stash-dirty is NOT set: step 4's dirty guard is inviolable — any non-residue dirty file => needs-human:worktree-dirty, touch nothing.`,
      ``,
      `Step 7 (Push) is YOURS — it is the unit's named terminal stage and MUST`,
      `complete before you return (GGC-91). A rebase-only result WITHOUT a`,
      `pushedSha is a defect, not an accepted variance. There is no`,
      `orchestrator-owned push; this script only aggregates your returned result.`,
      ``,
      `Return the structured object mapping your step-8 report:`,
      `  pr=${item.pr}`,
      `  outcome: "resolved" if you ran worktree stages and pushed; "skipped" if`,
      `    the two-stage gate found nothing to do (set skipReason to "up-to-date"`,
      `    or "judged-clean"); "needs-human" if a guard stopped you (set needsHuman`,
      `    to one of conflict | tests-failed | worktree-dirty |`,
      `    comment-fix-failed-tests | push-failed, and for worktree-dirty set`,
      `    needsHumanFiles to the non-residue file list).`,
      `  rebased (bool), pushedSha (string or null), comments ({fix,reply,stale,`,
      `    defer} or null), held (array), resolvedThreadIds (array), assumptions`,
      `    (the Tier-1 auto-assume ledger, [] when clean), error (one-line reason`,
      `    when needs-human, else null).`,
    ].join("\n"),
    {
      label: `resolve:#${item.pr}`,
      phase: "resolve",
      agentType: "general-purpose",
      schema: UNIT_SCHEMA,
    },
  );
  if (!result) {
    // agent() returns null on user-skip or a terminal API error after retries —
    // the worker DIED. Map to a synthetic needs-human row so aggregation never
    // NPEs and the human sees the PR rather than it silently vanishing.
    log(`[resolve] PR #${item.pr} agent returned null (skip / terminal API error)`);
    return {
      pr: item.pr,
      outcome: "needs-human",
      rebased: false,
      pushedSha: null,
      comments: null,
      held: [],
      resolvedThreadIds: [],
      assumptions: [],
      needsHuman: "worker-died",
      needsHumanFiles: [],
      error: "unit agent returned null (skipped or terminal API error); the unit did not complete",
    };
  }
  return result; // validated against UNIT_SCHEMA
}

// ── Orchestration ───────────────────────────────────────────────────────────

// Normalize args (the markdown serializes an object; tolerate a bare-array
// roster for hand-runs / older callers).
const roster = Array.isArray(args) ? args : (args && Array.isArray(args.roster) ? args.roster : []);
const prefiltered = (args && Array.isArray(args.prefiltered)) ? args.prefiltered : [];
const platform = (args && typeof args.platform === "string") ? args.platform : "unknown";
const stashDirty = !!(args && args.stashDirty);
const waveSize = (args && Number.isInteger(args.waveSize) && args.waveSize > 0) ? args.waveSize : 4;

if (roster.length === 0) {
  // Smoke guard (mirrors dispatch-fanout's P2 guard). Distinguish a GENUINELY
  // empty roster (no actionable PRs — a legitimate no-op) from `args` that DID
  // carry work but parsed to zero rows (a serialization/parse mismatch that
  // spawned 0 agents and looks identical to "no work").
  const argsCarriedWork =
    (typeof args === "string" && args.trim() !== "" && args.trim() !== "[]") ||
    (Array.isArray(args) && args.length > 0) ||
    (args && Array.isArray(args.roster) && args.roster.length > 0);
  if (argsCarriedWork) {
    log("[pr-resolver-batch] ERROR: args carried a roster but parsed to 0 rows — serialization/parse mismatch, NOT a no-op. Spawned 0 agents.");
    return { error: "roster-parse-failed", counts: { resolved: 0, skipped: 0, "needs-human": 0 }, rows: [], prefiltered };
  }
  log("[pr-resolver-batch] empty roster — nothing to fan out");
  return { counts: { resolved: 0, skipped: 0, "needs-human": 0 }, rows: [], prefiltered };
}

// Phase 1 — resolve the toolchain once.
phase("preflight");
const tc = await resolveToolchain(platform);
if (tc.failFast) {
  // P1-3: a pinned-but-unrunnable SDK (or no SDK on a flutter repo) aborts the
  // WHOLE batch before any unit spawns — a per-worker fallback is forbidden.
  log(`[preflight] FAIL-FAST — ${tc.reason}`);
  return {
    error: "toolchain-failfast",
    reason: tc.reason,
    counts: { resolved: 0, skipped: 0, "needs-human": 0 },
    rows: [],
    prefiltered,
  };
}
log(`[preflight] FLUTTER_BIN="${tc.flutterBin}" — injecting into every unit`);

// Phase 2 — run the units in WAVES of `waveSize` (P1-2). The Workflow default
// cap is min(16, cores-2) — too high; N full suites in parallel thrash the CPU.
// We chunk into waves and parallel() each wave so at most `waveSize` units run
// at once, exactly as the old sliding-window fan-out did. NOT one pipeline()
// over the whole set (which would inherit the high default cap).
phase("resolve");
const rows = [];
for (let i = 0; i < roster.length; i += waveSize) {
  const wave = roster.slice(i, i + waveSize);
  log(`[resolve] wave ${Math.floor(i / waveSize) + 1}: PRs ${wave.map((w) => "#" + w.pr).join(", ")}`);
  const waveResults = await parallel(
    wave.map((item) => () => runUnit(item, tc.flutterBin, stashDirty)),
  );
  // parallel() resolves a throwing thunk to null — filter so aggregation never NPEs.
  rows.push(...waveResults.filter(Boolean));
}

// Phase 3 — aggregate. The returned object is what the calling markdown session
// receives; its "Batch mode" aggregation renders the run report from it (lists
// needs-human prominently, renders the assumptions blocks, writes the cross-run
// report log + needs-human-state.json dedup) exactly as before — only the
// fan-out mechanism changed.
phase("aggregate");
const counts = rows.reduce(
  (a, r) => ((a[r.outcome] = (a[r.outcome] || 0) + 1), a),
  { resolved: 0, skipped: 0, "needs-human": 0 },
);
log(
  `[aggregate] resolved=${counts.resolved} skipped=${counts.skipped} needs-human=${counts["needs-human"]}` +
    (prefiltered.length ? ` (pre-filtered ${prefiltered.length} with zero agents)` : "") +
    (rows.length !== roster.length ? ` (dropped ${roster.length - rows.length} to null)` : ""),
);

return { counts, rows, prefiltered, flutterBin: tc.flutterBin };
