#!/usr/bin/env bash
# Regression tests for the GGC-93 binary-only short-circuit in
# workflows/dispatch-fanout.workflow.js (`isAllBinaryDiff`).
#
# Guards the CAF-780 fix: an all-binary `git diff` (PNG assets at multiple
# density buckets) has NO +/- text hunks, only `Binary files … differ` markers,
# and must be treated as CLEAR-by-construction so the dual-judge panel is skipped
# (a binary asset cannot carry logic). A diff that mixes a binary asset with ANY
# text edit must NOT short-circuit — it falls through to the panel for full
# scrutiny on its text hunks.
#
# The workflow module runs its orchestration body at top level (phase()/agent()/
# pipeline() are injected globals), so it cannot be `import`-ed in plain node.
# Instead this test EXTRACTS the real `isAllBinaryDiff` function source from the
# shipped file and evals it — so the assertions exercise the actual shipped logic
# and cannot drift from a hand-copied duplicate.
#
# No standalone test runner exists yet (GGC-27); scripts/prompt-lint.sh runs every
# lib/*.test.sh as part of the `prompt` platform's verify-stage test_cmd.
#
# Run directly:  bash lib/ui-tweak-binary-clear.test.sh   (exit 0 = pass, 1 = fail)

set -u
HERE=$(cd "$(dirname "$0")" && pwd)
WF="$HERE/../workflows/dispatch-fanout.workflow.js"

[ -f "$WF" ] || { echo "FAIL: cannot find $WF" >&2; exit 1; }

node - "$WF" <<'NODE'
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");

// Extract the `function isAllBinaryDiff(...) { ... }` block by brace-matching
// from its declaration, then eval it into this scope.
const start = src.indexOf("function isAllBinaryDiff");
if (start < 0) { console.error("FAIL: isAllBinaryDiff not found in workflow"); process.exit(1); }
let i = src.indexOf("{", start), depth = 0, end = -1;
for (; i < src.length; i++) {
  if (src[i] === "{") depth++;
  else if (src[i] === "}") { depth--; if (depth === 0) { end = i + 1; break; } }
}
const fnSrc = src.slice(start, end);
// eslint-disable-next-line no-eval
const isAllBinaryDiff = eval("(" + fnSrc + ")");

let pass = 0, fail = 0;
function check(name, got, want) {
  if (got === want) { console.log(`PASS ${name}: got=${got}`); pass++; }
  else { console.log(`FAIL ${name}: got=${got} want=${want}`); fail++; }
}

// CAF-780-shaped: three PNG density buckets, no text hunks → all-binary → CLEAR.
const allBinary = [
  "diff --git a/assets/1.5x/pick_up_code.png b/assets/1.5x/pick_up_code.png",
  "index 1111111..2222222 100644",
  "Binary files a/assets/1.5x/pick_up_code.png and b/assets/1.5x/pick_up_code.png differ",
  "diff --git a/assets/2.0x/pick_up_code.png b/assets/2.0x/pick_up_code.png",
  "new file mode 100644",
  "index 0000000..3333333",
  "Binary files /dev/null and b/assets/2.0x/pick_up_code.png differ",
].join("\n");
check("all-binary PNG set → true", isAllBinaryDiff(allBinary, false), true);

// Mixed: a PNG AND a .dart edit (real text hunk) → must NOT short-circuit.
const mixed = [
  "diff --git a/assets/icon.png b/assets/icon.png",
  "Binary files a/assets/icon.png and b/assets/icon.png differ",
  "diff --git a/lib/page.dart b/lib/page.dart",
  "@@ -1,3 +1,3 @@",
  "-  final x = 1;",
  "+  final x = 2;",
].join("\n");
check("mixed binary + dart → false", isAllBinaryDiff(mixed, false), false);

// Text-only diff (no binary marker) → false (the panel handles it).
const textOnly = [
  "diff --git a/lib/page.dart b/lib/page.dart",
  "@@ -1,2 +1,2 @@",
  "-  color: red",
  "+  color: blue",
].join("\n");
check("text-only diff → false", isAllBinaryDiff(textOnly, false), false);

// Truncated diff → false even if the visible head looks all-binary (we cannot
// prove the unseen remainder carries no text hunk).
check("truncated → false", isAllBinaryDiff(allBinary, true), false);

// Empty / null diff → false (the empty-diff no-op path owns that case).
check("empty diff → false", isAllBinaryDiff("", false), false);
check("null diff → false", isAllBinaryDiff(null, false), false);

console.log(`ui-tweak-binary-clear.test: ${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
NODE
