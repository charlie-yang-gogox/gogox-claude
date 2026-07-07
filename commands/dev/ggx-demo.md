---
name: ggx-demo
argument-hint: "<TICKET|PR> [--force] [--plan] [--max-scenarios:N] | --batch [--plan] [--draft] [--max-scenarios:N]"
description: >
  Post-hoc UI demo capture for already-shipped PRs with a recordable UI change.
  Operator skill with two modes — single (`<TICKET|PR>`) and self-discovering
  batch (`--batch`, which absorbed the former `/_ui-demo-batch`):
  `--batch` captures a demo for every open PR of yours that still lacks one
  (no JSON input, no class/title gate — a diff-first LLM recordability judge
  decides which PRs open a device), serially on one device. Batch discovery may
  be restricted to draft PRs with `--draft` (attach a demo before a PR leaves
  draft), and gated for human review with `--plan` (build the whole plan —
  discovery + recordability verdicts + navigation grounding + a data-prereq
  probe against the CURRENT PR heads — present it, STOP; on approval capture
  immediately in the SAME run, so the plan is never stale). The batch DEVICE
  phase runs strictly one ticket at a time (single shared device, never two
  actors), and each ticket's capture runs in its OWN serial sub-agent so the
  ~150–180k of build/adb output stays out of the orchestrator context
  (orchestrator holds only ≤2k summaries). Single mode:
  given a Linear
  ticket id OR a PR (number/URL), it resolves the PR's worktree, asserts the
  local HEAD matches the PR head (fail-loud — never demo a stale/unreviewed
  build), runs `/ui-tweak:preview --capture-only` (the SOLE capture point —
  navigate to the target screen on an already-running device, logging in via a
  staging QA account when the repo's `demo_auth` selector is set, and
  record a screenshot + short clip), asserts the capture actually shows the
  target (non-blank frames / not stuck on the login screen — a file-exists check
  is insufficient), then idempotently attaches the result to
  the Linear ticket and patches the PR body's `<!-- ui-tweak-demo -->` `## Demo`
  region (replace-between-markers, never append). REUSES the ui-tweak
  capture/upload machinery (does NOT reimplement it). Fails LOUD end-to-end
  (R13): no device / auto-login failed (login wall) / no route / head mismatch /
  screenrecord-ladder exhausted / content-assertion failed → non-zero exit +
  deterministic stderr. Edits
  NOTHING in the codebase and writes NO walker markers (it never enters
  `/ui-tweak:ff` / `infer_ui_stage`), so it cannot mis-route a later
  `/ui-tweak` resume. Linear-only / flutter-only v1. NOT designer-facing —
  `/ui-tweak` is the designer entry; this is the operator/pipeline action.
  The B1.5 capture plan carries resettable/reset/replayable lines and
  `preview --capture-only` does two-pass rehearse→record replay internally for
  Tier-2 / long-async / DRIVE=full driving flows (kills LLM-latency dead air);
  `--force` may reuse a persisted replay-script when sha + wm-size match.
  On a confirmed staging build `preview` resolves DRIVE=full, so the capture MAY
  type into fields and place an order to reach the demonstrated state (staging QA
  account only; fails closed to nav-only off-staging). `--plan` is a CORE
  single-`/ggx-demo` flag (not batch-bound): `/ggx-demo <PR> --plan` plans this one
  demo (recordability + navigation grounding + data-prereq probe + N-scenario
  enumeration, bounded by `--max-scenarios`, default 3), STOPs for approval, then
  captures in the SAME session — emitting one clip PER scenario, each keyed and
  attached without clobbering. `--batch --plan` reuses that core plan gate over the
  discovered set; `--draft` stays a batch-only discovery filter.
---

<!-- RULE: command content is English. -->

# `/ggx-demo <TICKET|PR>` | `/ggx-demo --batch`

> Productizes the manual post-hoc demo procedure that was first done by hand. `--auto`-shipped
> `design bug` PRs carry a demo only when one is captured post-ship: the parallel fan-out is device-free
> (build-only), so the demo must be recorded **after** the PR is open — on a device, logging in via the
> Step 2.4 gate when the repo's `demo_auth` selector is set.
>
> **Why a standalone skill, not a `--demo` flag on `/ui-tweak`** (decision): `/ui-tweak` is
> designer-facing (plain-language cards, fail-silent, rides the forward flow). A post-hoc demo is the
> *opposite* operation — no edit, fail-LOUD, runs against an already-shipped ticket — and an operator
> action (`/ggx-*`). It REUSES the same capture/upload machinery via `preview --capture-only`; only the
> ~15 lines of glue below differ.
>
> **Zero walker contamination**: `/ggx-demo` never enters `ff.md` / `infer_ui_stage`. It writes none of
> the forward markers, so it cannot leave a reused `../<ID>` worktree mis-routed for a later bare
> `/ui-tweak` resume.

## Usage

- `/ggx-demo <TICKET>` — e.g. `/ggx-demo <ticket-id>`. Resolves the ticket's open PR.
- `/ggx-demo <PR>` — a PR number (`#610` / `610`) or full URL.
- `/ggx-demo <TICKET|PR> --force` — **replace mode**: re-record and REPLACE an existing
  demo on the same commit. Without `--force`, the Step-4 dedup SKIPs any ticket that already carries a
  `ui-tweak-demo-<sha>` attachment for the current sha — correct for idempotent re-runs, wrong when the
  existing demo is bad (wrong flow / expired clip) and needs replacing. With `--force`, Step 4 runs the
  attach contract's REPLACE path (delete the old attachment → upload the new capture → rewrite the
  PR-body link; the `assetUrl` changes on re-upload). Single-ticket mode only; batch never forces.
  On a reused `../<ID>` worktree, `--force` also lets `preview` skip the rehearsal and replay a
  persisted `.dev/ui-tweak/replay-script` when its sha AND device `wm size` both still match (the
  replay fast-path); any mismatch rehearses fresh.
- `/ggx-demo <TICKET|PR> --plan` — **per-demo plan gate + multi-scenario** (a CORE single-mode flag).
  Runs the shared **Plan gate** (Step 1.9): confirm recordability, ground navigation, probe data
  prerequisites, and **enumerate N scenarios** for this PR (each = a typed-event intent + reset method +
  expected crux) against the current PR head, present the plan, and STOP for approval. On approval it
  captures in the SAME session (never stale) — **one clip per scenario**, each keyed and attached without
  clobbering. Without `--plan`, single mode captures exactly one clip (the pre-existing behaviour). Combinable with `--force`.
- `/ggx-demo <TICKET|PR> --plan --max-scenarios:N` — cap the enumerated scenarios at `N` (default `3`,
  bounding device time). Ignored (with a one-line warn) without `--plan`. LOUD-skips a per-scenario
  failure rather than emitting a silent empty clip.
- `/ggx-demo --batch` — **no ticket argument**: self-discover every open PR of mine that still lacks a
  demo and capture them serially on one device. See **Batch mode** below. Accepts these optional modifiers:
  - `--draft` — **restrict discovery to DRAFT PRs** (B1). Use to attach a demo before a PR leaves draft.
    Without it, discovery is every open PR of mine (draft and ready alike). Batch-only.
  - `--max-scenarios:N` — cap per-PR scenario enumeration at `N` (default `3`). A `--plan` companion (it
    bounds B1.9's enumeration); ignored with a one-line warn when `--plan` is absent.
  - `--plan` — **HITL review gate** — the batch reuse of the CORE Plan gate (Step 1.9). Build the entire
    plan — discovery + per-PR recordability verdicts + navigation grounding + a data-prereq probe +
    per-PR scenario enumeration against the CURRENT PR heads — present it, and STOP for human review. On
    approval, capture immediately in the SAME run (the plan is generated fresh against the latest heads and
    consumed in the same session, so it is never stale — no persisted-plan re-consumption, no version-drift
    handling). The plan is also written to a receipt file for traceability, but that file is a record, NOT
    a re-consumed input. Without `--plan`, batch behaviour is unchanged (the agent captures directly after
    B1.5). Combinable with `--draft`. **STOP granularity**: the batch aggregates all PRs' plans into ONE
    STOP before any device opens (the B3 capture sub-agents are non-interactive and cannot host a per-PR
    HITL prompt) — this is the build-time settlement of the plan's open "per-call vs aggregate" sub-detail.

**Mode dispatch.** If the argument set contains `--batch`, run the **Batch mode** section (B1–B4) and
STOP; otherwise run the single-ticket flow (Steps 0–5, including Step 1.9's Plan gate when `--plan` is
set). `--plan` is valid in **both** modes (a CORE flag — single mode plans one demo at Step 1.9; batch
reuses the same gate over the discovered set). `--max-scenarios:N` is a `--plan` companion (ignored with a
one-line warn without `--plan`). `--draft` is **batch-only** (a discovery filter) — ignored with a
one-line warn in single mode. Both modes share Step 0's env gate and the per-ticket Steps 1–5 (batch runs
each ticket's Steps 1–5 in its own serial sub-agent — see B3).

## Step 0 — resolve env (flutter-only / Linear-only gate)

1. Resolve the project profile the same way the ui-tweak stages do (`ui_preview_cmd`, `ui_build_cmd`,
   the fvm-aware flutter binary). **No `ui_preview_cmd` (android/ios build-only profile, or non-flutter)**
   → print `ggx-demo: no device-preview command for this platform — nothing to capture.` and **exit 0**
   (no-op; batch mode prints the `ggx-demo --batch: …skipping demos.` variant). Demo capture needs
   `flutter run` (build+install+launch).
2. `gh` must be authenticated; the Linear MCP must be reachable. A hard failure of either is a LOUD
   abort (see Disposition).

## Step 1 — resolve the PR + ticket id

```bash
# --force = replace mode (Step 4 takes the attach contract's REPLACE path on a same-title
# match instead of SKIP). Parse + strip it before the TICKET|PR resolution below. Single mode only.
FORCE=0; printf '%s' "$ARGUMENTS" | grep -q -- '--force' && FORCE=1
ARG="<TICKET|PR, --force stripped>"
# PR form: a bare number, #NNN, or a github URL → resolve directly.
# TICKET form (CAF-/DAF-): find the open PR whose branch carries the ticket id.
if printf '%s' "$ARG" | grep -qE '^#?[0-9]+$|github\.com/.*/pull/[0-9]+'; then
  PR="$ARG"
else
  TICKET_ID=$(printf '%s' "$ARG" | grep -oE '[A-Z]+-[0-9]+' | head -1)
  # ui-tweak/dev branches are <type>/<ticket-id>; resolve the open PR by head branch
  # (NEVER `gh pr view <ticket>` — the branch is <prefix>/<id>, not the id).
  PR=$(gh pr list --search "$TICKET_ID" --state open --json number,headRefName \
        --jq '.[0].number' 2>/dev/null)
  [ -n "$PR" ] || { echo "GGX-DEMO FAIL: no open PR found for $TICKET_ID." >&2; exit 1; }
fi
PR_JSON=$(gh pr view "$PR" --json number,url,headRefName,headRefOid,body) \
  || { echo "GGX-DEMO FAIL: gh pr view $PR failed." >&2; exit 1; }
HEAD_REF=$(printf '%s' "$PR_JSON"  | jq -r .headRefName)
HEAD_OID=$(printf '%s' "$PR_JSON"  | jq -r .headRefOid)
PR_URL=$(printf '%s' "$PR_JSON"    | jq -r .url)
# Ticket id: from the arg, else extract from the branch name.
[ -n "$TICKET_ID" ] || TICKET_ID=$(printf '%s' "$HEAD_REF" | grep -oE '[A-Z]+-[0-9]+' | head -1)
TICKET_LC=$(printf '%s' "$TICKET_ID" | tr '[:upper:]' '[:lower:]')
```

## Step 1.9 — Plan gate (`--plan` only) — the CORE plan-then-capture flow

_Run this step ONLY when `--plan` was passed (single mode). When `--plan` is absent, skip Step 1.9
entirely and fall through to Step 2 (single-clip capture, the pre-existing behaviour). This is the CORE
plan gate — batch mode's B1.9 REUSES it over its discovered set (see Batch mode); it does NOT live in
batch (that was the prior batch-only binding, now corrected). It runs BEFORE the worktree/device (everything here is
cheap + read-only), so an unapproved plan never opens a device._

```bash
# --max-scenarios cap (companion to --plan; default 3). Ignored + warned without --plan.
MAX_SCENARIOS=3
printf '%s' "$ARGUMENTS" | grep -qE -- '--max-scenarios[:= ]' \
  && MAX_SCENARIOS=$(printf '%s' "$ARGUMENTS" | grep -oE -- '--max-scenarios[:= ][0-9]+' | grep -oE '[0-9]+' | head -1)
```

**1. Confirm recordability + ground navigation + probe data (read-only, no device)** — the same three
checks batch runs (B1.5 recordability, B1.9 nav grounding, B1.9 data-prereq probe), for this ONE PR:
fetch the diff (`gh pr diff "$PR" | head -c 60000`), confirm it produces a user-visible change reachable
on staging, mark the target `nav: grounded` / `nav: unresolved`, and record `data: ok|missing|unprobeable`.

**2. Enumerate N scenarios (the multi-clip plan).** From the PR's acceptance criteria (ticket + diff),
enumerate the **distinct demonstrable outcomes** — each its own clip. The canonical trigger is
multi-scenario behaviour verification: e.g. (a) blank field → inline error; (b) filled field → validation
passes / order proceeds. Each scenario is `{key, intent, reset-method, expected-crux}`:
- `key` — a short kebab slug (`blank-error`, `filled-pass`) used to key demo-files + attachments.
- `intent` — the typed-event drive to perform (what to type / tap / place, per the DRIVE=full policy).
- `reset-method` — how to return to the recording start point for the NEXT scenario (the B10 ladder rung:
  `in-flow undo` / `pop home + re-nav` / `force-stop + relaunch` / `n/a`).
- `expected-crux` — the post-fix state the clip must show (the A5/A7 assertion).

A PR with a single demonstrable outcome yields exactly ONE scenario (→ one clip, same as no-`--plan`).
**Bound N at `MAX_SCENARIOS`** (default 3): if the ACs imply more, keep the `MAX_SCENARIOS` highest-value
ones and note the rest as dropped in the plan (never silently truncate — surface what was cut).

**3. Present + STOP for approval** via `AskUserQuestion`, showing: recordability one-liner; `nav:` /
`data:` status; the enumerated scenarios (`key — intent — expected-crux`); and the capture order. Options:
- **Capture all now** — proceed to Step 2 → capture, for the full scenario set in the same session.
- **Capture a subset** — the human names scenarios/PR to drop; narrow the set, then proceed.
- **Abort** — exit 0 without a worktree/device.

Because approval leads straight into Step 2+ in the SAME session against the head just planned, there is
no re-fetch and no staleness window. Hold the approved scenario set in the session; Step 2.5 persists it
to `.dev/ui-tweak/scenarios` for `preview --capture-only` to consume.

## Step 2 — resolve the worktree + head-match guard (fail-LOUD)

The demo MUST build the exact diff that shipped. Reuse `../<ID>` only when it is genuinely on the PR
head; otherwise check out a throwaway worktree at the PR head. Then assert HEAD equals the PR head
— a mismatch means the worktree drifted (a sibling reset, an unpushed local commit)
and we must NOT capture a stale/unreviewed build.

```bash
git fetch origin "$HEAD_REF" 2>/dev/null \
  || { echo "GGX-DEMO FAIL: git fetch origin $HEAD_REF failed (ticket $TICKET_ID)." >&2; exit 1; }

THROWAWAY=""
if [ -d "../$TICKET_LC" ] \
   && [ "$(git -C "../$TICKET_LC" rev-parse --abbrev-ref HEAD)" = "$HEAD_REF" ] \
   && [ -z "$(git -C "../$TICKET_LC" status --porcelain)" ]; then
  WT=$(cd "../$TICKET_LC" && pwd)
else
  WT=$(cd .. && pwd)/"${TICKET_LC}-demo"
  git worktree add "$WT" "origin/$HEAD_REF" 2>/dev/null \
    || { echo "GGX-DEMO FAIL: could not create demo worktree at $WT (ticket $TICKET_ID)." >&2; exit 1; }
  THROWAWAY="$WT"
fi

# Head-match guard — the local HEAD must equal the PR head OID.
ACTUAL=$(git -C "$WT" rev-parse HEAD)
if [ "$ACTUAL" != "$HEAD_OID" ]; then
  echo "GGX-DEMO FAIL: worktree HEAD ($ACTUAL) != PR #$PR head ($HEAD_OID) — refusing to demo a stale/unreviewed build (ticket $TICKET_ID)." >&2
  [ -n "$THROWAWAY" ] && git worktree remove --force "$THROWAWAY" 2>/dev/null
  exit 1
fi
```

> A throwaway `../<ID>-demo` worktree is removed at the end (Step 5) on every exit path (trap-delete),
> so a post-hoc run leaves no residue. The reused `../<ID>` worktree is left exactly as found (minus the
> Step 2.5 `ticket.json`, which Step 5 removes for a reused worktree) — and because `preview
> --capture-only` writes no walker markers, a later bare `/ui-tweak` resume there is never mis-routed.

## Step 2.5 — cache ticket context (for Step 2.4 region inference + Step 2.5 Tier-1 host)

`preview --capture-only` reads `.dev/ui-tweak/ticket.json` to (a) **infer the account region** for the
Step 2.4 login gate (auto-resolve — market token → `hk`/`sg`/… else `hk`) and (b) derive the
Tier-1 `ggv://` deep-link host. A dev/port/bug worktree (the common `/ggx-demo` target) has none — the
forward ui-tweak pipeline writes it, this post-hoc path does not — so cache a minimal one now. We already
hold `$TICKET_ID`; fetch the ticket via the Linear MCP `get_issue` and write the fields region inference
+ host derivation actually read:

```bash
mkdir -p "$WT/.dev/ui-tweak"
# Fetch <ticket> (Linear get_issue) → write {title, description, labels[], market/region if present}.
# `market`/`region` is whatever the ticket exposes (a field, a label like "SG", or a phrase in the body);
# leave it absent if the ticket says nothing and Step 2.4 will fall back to hk. Keep it SMALL — only the
# fields the two consumers read, not the whole issue payload.
cat > "$WT/.dev/ui-tweak/ticket.json" <<JSON
{"id":"$TICKET_ID","title":"<title>","description":"<short description>","labels":[<labels>],"market":"<market-or-empty>"}
JSON
```

This file is NOT a walker marker (the walker keys on `build-pass` / `preview-shown` / audit markers, not
`ticket.json`), so it never mis-routes a later `/ui-tweak` resume; Step 5 still deletes it from a **reused**
worktree to honor "left exactly as found" (a throwaway worktree is removed whole, so no per-file cleanup).

**Persist the approved scenarios (`--plan` only).** When Step 1.9 ran and the human approved a scenario
set, write it to `.dev/ui-tweak/scenarios` — one scenario per line, TAB-separated, in capture order —
which `preview --capture-only`'s per-scenario loop reads:

```bash
# Only when --plan produced an approved set. Absent file → preview captures a single `main` clip.
: > "$WT/.dev/ui-tweak/scenarios"
# for each approved scenario: printf '%s\t%s\t%s\t%s\n' "$key" "$intent" "$reset_method" "$expected_crux" >> "$WT/.dev/ui-tweak/scenarios"
```

Like `ticket.json`, `scenarios` is NOT a walker marker; Step 5 removes it from a reused worktree (a
throwaway worktree is removed whole). Without `--plan` this file is never written, so `preview` falls back
to the single-clip `main` capture.

## Step 3 — capture (reuse `preview --capture-only`, the SOLE capture point)

From inside `$WT`, set `UI_TWEAK_FF=1` (so `/ui-tweak:preview`'s Step-0a misdirect guard passes — this
is an orchestrated call, not a stray designer invocation) and invoke:

```
UI_TWEAK_FF=1  /ui-tweak:preview --capture-only
```

`preview --capture-only` (Step 0c) acquires an **already-running** device (path (a) only, **no
cold-boot**), launches the existing build, runs the **Step 2.4 login gate** (logs in with a
staging QA account when the repo declares a `demo_auth` selector and the app is not already logged in —
otherwise a no-op), then Step 2.5 navigate + capture (Tier-1 `ggv://` deep-link → Tier-2 nav-only
tap-through → screenshot + short recording → `.dev/ui-tweak/demo-files`), and writes **no** walker
markers. The 3 device fixes (package-targeted deep-link, `screenrecord --size`
ladder, scaled taps) are baked into Step 2.5. **In `--capture-only` mode the capture fails LOUD** — a
non-zero exit propagates here. Treat any non-zero exit, OR an empty `.dev/ui-tweak/demo-files`, as a
capture failure and abort LOUD (Step 5 still removes the throwaway worktree):

```bash
if [ ! -s "$WT/.dev/ui-tweak/demo-files" ]; then
  echo "GGX-DEMO FAIL: no demo captured for $TICKET_ID (no device / auto-login failed (login wall) / unreachable screen / screenrecord ladder exhausted)." >&2
  [ -n "$THROWAWAY" ] && git worktree remove --force "$THROWAWAY" 2>/dev/null
  exit 1
fi
```

## Step 3.5 — post-capture content assertion (gate the attach)

A non-empty `.dev/ui-tweak/demo-files` proves a file was WRITTEN, not that it shows the target. A black
frame, an all-white frame, or a screenshot **stuck on the login screen** all pass a file-exists /
non-empty check yet are useless demos — and the login-screen case is the most common false pass (the
Step 2.4 gate silently failed, capture ran anyway on the login wall). So before attaching, assert the
capture actually shows the target screen:

```bash
# Content assertion over the captured artifacts listed in demo-files. This is a CONTENT check, distinct
# from Step 3's existence check. demo-files is keyed: `<scenario-key>\t<path>` (a bare line = key `main`).
# Judge PER SCENARIO KEY — each clip is its own demonstrable outcome and must be asserted independently.
# For each unique key, its screenshot is the .png line for that key:
#   SHOT=$(awk -F'\t' -v k="$key" '{ p=(NF>1?$2:$1); ky=(NF>1?$1:"main"); if (ky==k && p ~ /\.(png|jpg|jpeg)$/){print p; exit} }' "$WT/.dev/ui-tweak/demo-files")
KEYS=$(awk -F'\t' '{ print (NF>1?$1:"main") }' "$WT/.dev/ui-tweak/demo-files" | sort -u)
```

Then **you (the LLM executing this skill)** judge **each scenario key's** primary captured screenshot
(`$SHOT` for that key; if the scenario produced only a clip, sample its first/mid/last frame) with two gates:

1. **Non-blank** — the frame is not a solid black / solid white / single-flat-colour surface (a crashed
   launch, a not-yet-rendered frame, or a `screenrecord` that captured nothing). Read the image and
   reject if it carries no rendered UI.
2. **Not the login wall** — the frame is not the app's login / OTP / sign-in screen. Judge this the same
   way Step 2.4 decides whether a login is needed: a **visual read of the screenshot** (Step 2.4.1's
   LLM screenshot read), NOT a config selector — `demo_auth` carries no login-screen field (its fields
   are `notion_page` / `app` / `region` / `account_label` / `login_probe_host`). Compare what the frame
   shows against the target screen described by the cached `ticket.json` (its `title` / `description`):
   a capture that shows the login / OTP screen means
   the Step 2.4 gate did not actually land on the target (a silent login-wall), NOT a real demo of the
   change.

A key whose screenshot passes BOTH gates is **kept** for attach (Step 4). A key that fails EITHER gate is
**dropped** with a LOUD per-scenario note (never attach a blank / login-wall clip) — the remaining passing
keys still attach:

```bash
echo "GGX-DEMO: scenario <key> for $TICKET_ID did not show the target (blank frame or stuck on the login screen) — dropping that clip." >&2
```

**Whole-run disposition.** If AT LEAST ONE key passes → proceed to Step 4 with the passing keys (single
mode reports the dropped ones; each passing clip is attached under its key). If **EVERY** key fails →
fail-LOUD the whole run:

```bash
echo "GGX-DEMO FAIL: no scenario for $TICKET_ID showed the target (all clips blank or stuck on the login screen) — refusing to attach a useless demo." >&2
[ -n "$THROWAWAY" ] && git worktree remove --force "$THROWAWAY" 2>/dev/null
exit 1
```

This exit is a fail-LOUD non-zero (single mode). In batch, the B3 loop treats it exactly like any other
per-ticket loud failure — counted as a capture-skip with its reason — and an all-`login screen` content
failure is classified as a `login wall` for the B3 short-circuit (a silent login-wall recurs identically
on the shared device). The assertion runs on EVERY key on EVERY path that would otherwise attach,
including `--force` replace (never replace a good demo with a blank one). For a single-clip capture there
is exactly one key (`main`), so this collapses to the pre-existing single-gate behaviour.

## Step 4 — idempotent attach (the shared `ff.md` contract — do NOT re-derive)

Run the **Idempotent attach** contract documented in `commands/design/ui-tweak/ff.md` ("Deliver PR body"
→ "Idempotent attach (shared contract …)") against `$TICKET_ID` / PR `$PR`, using the files listed in
`$WT/.dev/ui-tweak/demo-files` — which are **keyed by scenario** (`<scenario-key>\t<path>`; a bare line =
key `main`). The contract is the single source of truth for the keyed-set behaviour; do NOT re-derive it
here. In brief:

1. **Linear attachment dedupe — PER KEY.** Title each scenario's attachment `ui-tweak-demo-<sha>-<key>`
   (`<sha> = git -C "$WT" rev-parse --short HEAD`), except the single/`main` key stays the bare
   `ui-tweak-demo-<sha>` (backward-compatible with pre-existing demos). List the ticket's existing
   attachments; per key, SKIP the upload (reuse the existing `assetUrl`) on a title match, so re-running
   never adds a duplicate. **With `--force`, same-title detection takes the REPLACE path per key**
   (delete → re-upload → rewrite that key's PR-body link with the new `assetUrl`).
2. **PR body — ONE `## Demo` region, N labeled links.** The region stays a single
   `<!-- ui-tweak-demo -->` … `<!-- /ui-tweak-demo -->` block (replace-between-markers, never append), but
   it lists **one labeled link per scenario key** (e.g. `- **blank-error**: <link>`). Re-running produces
   exactly one marked block with the current key set.
3. **PR link form**: the Linear `assetUrl` is a **plain link** in the PR body (deterministic 401 to
   GitHub on this private repo); inline render lives on the Linear ticket only.

This is the ONLY write `/ggx-demo` performs: the Linear attachment + the PR-body region. It changes NO
ship state — no labels, no PR open/close/merge, no ticket status/assignee.

## Step 5 — cleanup + summary

```bash
if [ -n "$THROWAWAY" ]; then
  git worktree remove --force "$THROWAWAY" 2>/dev/null   # throwaway removed whole — no per-file cleanup
else
  rm -f "$WT/.dev/ui-tweak/ticket.json" "$WT/.dev/ui-tweak/scenarios"   # reused worktree: drop the Step 2.5 caches ("left as found")
fi
echo "ggx-demo: captured + attached demo for $TICKET_ID (PR #$PR, sha $(git -C "$WT" rev-parse --short HEAD 2>/dev/null))."
exit 0
```

> Cleanup runs on the **success** path above; the fail-LOUD exits in Steps 2–3 already remove a throwaway
> worktree. For a reused worktree those early exits may leave `ticket.json` behind — harmless (not a
> walker marker), and the next run overwrites it — but if strict "left as found" matters on a failure
> path too, delete it in those branches as well.

## Batch mode (`--batch`) — absorbed `/_ui-demo-batch`

`/ggx-demo --batch` captures demos for **every open PR of mine that still lacks one** — no JSON input
(it self-discovers). It is the serial, device-bound counterpart of the single-ticket flow: "ship the
code" is parallel + device-free (the fan-out), "record the demo" is serial here, so the one shared
simulator is never driven by two actors at once (no lock, no TTL — true by construction). Invoked by
`/ggx-dispatcher --demo` (after the §5.2 join) and `/ggx-on-duty --demo` (after the Leg-1 dispatch).

**Fail-soft end to end** (the `/_slack-notify` contract): any per-ticket failure degrades to one WARN
line and continues; the batch never blocks/fails its caller and never touches ship state (labels, PR
open/closed, ticket status). Per ticket it edits only what the single-ticket flow edits — the Linear
attachment + the PR-body `<!-- ui-tweak-demo -->` region.

### B1 — discover candidate PRs (no input) — attachment-truth dedup

The "already demoed" dedup keys on the **Linear `ui-tweak-demo-<sha>` attachment**, NOT the PR-body
`<!-- ui-tweak-demo -->` marker. The marker is written at PR-open *regardless of capture success*:
forward `/ui-tweak:ff`'s "Deliver PR body" emits a marker-wrapped `## Demo` block even when
`preview`'s navigate+capture fail-silents (the block just carries a "No screenshot" line). So
**marker-present ≠ demo-captured** — keying discovery on the marker permanently skipped PRs that never
actually recorded (observed on PRs whose demo silently never captured). The Linear attachment is the
authoritative signal: both forward `/ui-tweak:ff` and Step 4 here create `ui-tweak-demo-<sha>` **only
after a real capture** (`demo-files` non-empty); a fail-silent run produces no such attachment. This is
the **same signal source** as Step 4's idempotent-attach dedup, so discovery and re-attach agree.

```bash
# Batch modifiers (parse once, at the top of Batch mode). `--draft` is batch-only; `--plan` is the
# CORE flag reused here (both default OFF). `--max-scenarios` is a `--plan` companion in BOTH modes —
# parse it the same way as Step 1.9 so `--batch --plan --max-scenarios:N` bounds each PR's enumeration.
PLAN=0;  printf '%s' "$ARGUMENTS" | grep -q -- '--plan'  && PLAN=1
DRAFT=0; printf '%s' "$ARGUMENTS" | grep -q -- '--draft' && DRAFT=1
MAX_SCENARIOS=3
printf '%s' "$ARGUMENTS" | grep -qE -- '--max-scenarios[:= ]' \
  && MAX_SCENARIOS=$(printf '%s' "$ARGUMENTS" | grep -oE -- '--max-scenarios[:= ][0-9]+' | grep -oE '[0-9]+' | head -1)

# Every open PR of MINE. author=@me is a HARD limit (never touch others' PRs). NO class/title
# gate (the old `^## UI Tweak` body filter was DROPPED; the dispatch finisher emits that title
# unreliably). Recordability is judged per-PR in B1.5 (diff-first), before any device opens.
# `isDraft` is always fetched so `--draft` can restrict the set without a second API call.
PRS=$(gh pr list --author "@me" --state open --json number,headRefName,url,body,title,isDraft)

# --draft: restrict discovery to DRAFT PRs (attach a demo before the PR leaves draft). Without the flag,
# take every open PR (draft and ready alike). Filtering client-side keeps the one `gh pr list` call.
if [ "$DRAFT" = 1 ]; then
  PRS=$(printf '%s' "$PRS" | jq '[ .[] | select(.isDraft == true) ]')
  [ "$(printf '%s' "$PRS" | jq 'length')" -gt 0 ] || { echo "ggx-demo --batch --draft: no open DRAFT PRs of mine."; exit 0; }
else
  [ "$(printf '%s' "$PRS" | jq 'length')" -gt 0 ] || { echo "ggx-demo --batch: no open PRs of mine."; exit 0; }
fi
```

Then **you (the LLM executing this skill) build `CANDIDATES`** by excluding only PRs that already carry a
real demo attachment. For each PR in `$PRS`:

1. **Extract the ticket id** — `[A-Z]+-[0-9]+` from `headRefName` (fallback: `title`, then `body`),
   uppercased. If none parses → the PR is a **CANDIDATE** (cannot verify a demo without a ticket; safer
   to re-offer than to silently skip).
2. **Check the ticket's attachments** — call `mcp__claude_ai_Linear__get_issue --id <ticket-id>` and
   inspect `.attachments[]`. If any attachment's `.title` starts with `ui-tweak-demo-` → a real demo was
   recorded → **EXCLUDE** the PR (idempotent: a successfully-demoed PR is never re-recorded). Otherwise
   (no such attachment — *even if the PR body has the `<!-- ui-tweak-demo -->` marker*) → **CANDIDATE**.
   A `get_issue` failure (network / not found) → treat as **CANDIDATE** and log one WARN line (fail-soft:
   re-offering a demo is cheap; the per-ticket flow is idempotent on the attachment title anyway).
3. Assemble `CANDIDATES` as the JSON array B1.5 consumes — same shape as `$PRS` items
   (`number,headRefName,url,body,title`).

```bash
[ "$(printf '%s' "$CANDIDATES" | jq 'length')" -gt 0 ] || { echo "ggx-demo --batch: no open PRs of mine without a recorded demo."; exit 0; }
```

This makes the regression cases (marker present, no `ui-tweak-demo-*` attachment) candidates
again, while PRs with a real attachment stay excluded.

(A caller that already holds the freshly-shipped rows — `/ggx-dispatcher --demo`, `/ggx-on-duty --demo`
— MAY narrow to these PRs, but the default is self-discovery so no caller has to hand-build a JSON array.)

### B1.5 — recordability judge (diff-first, LLM, before any device)

Dropping the title gate (B1) means `CANDIDATES` now includes non-UI PRs (pure logic / backend / config /
test / analytics). Opening a device + `flutter run` for each of 16–18 PRs would blow the device budget,
so judge recordability from the **diff first** (cheap) and only let recordable PRs reach B2/B3.

For each PR in `CANDIDATES`, fetch the diff (size-capped so a huge PR doesn't blow context); its
title/body are already in `CANDIDATES`:

```bash
# Cheap signal — read the diff, not the device. Cap to keep context bounded; the judgment only needs
# the SHAPE of the change, not every line.
DIFF=$(gh pr diff "$PR" 2>/dev/null | head -c 60000)
```

Then **you (the LLM executing this skill) classify** each PR as RECORDABLE or SKIP, from the diff +
title + body:

- **RECORDABLE** — the diff plausibly produces a **user-visible change** AND the app can be **driven to
  the state that shows it**. Two shapes qualify (the rubric was loosened after an early batch
  recorded 1/12 and skipped obviously-recordable PRs):
  1. **Static UI change** — widget / layout / style / copy / asset / screen changes in app UI code,
     with the affected screen navigable (a known route, a `ggv://` deep-link, or tap-through).
  2. **Behavioural / error / state change** — the diff alters what the user SEES when a flow runs
     (an error message, a pre-fill, a redirect, a validation) and the flow can be DRIVEN to that
     state on staging. "No pixel diff in a static shot" / "nav-only taps can't select it" do NOT
     make a PR unrecordable — recording the flow is the demo.
     **"Requires typing" / "requires placing an order" is NO LONGER an auto-SKIP.** On a staging build
     `preview` resolves `DRIVE=full` and MAY type addresses / form fields and place an order to reach the
     crux, so order-flow states — post-placement price breakdown, ASAP-reset on a new order, a
     duplicated-order toast — are RECORDABLE (drive the order flow on the staging QA account). A **staging
     order placement is NOT a "real payment"** — it is the sanctioned staging drive, not the unreachable
     real-money case.
     **Auth-error demos are explicitly RECORDABLE**: staging accepts the SMS request,
     reaches the code screen, and a deliberately wrong code (e.g. `1234`) returns a real 401 → the
     specific message renders. Wrong-code / wrong-password / verification-failure PRs need no real
     OTP and belong in `RECORDABLE`.
- **SKIP (record the reason, no device)** — when the diff shows none of the above:
  1. No user-visible surface at all — pure backend / config / build / test / analytics /
     dependency-only diff whose effect cannot be seen on any screen.
  2. Visible change present but the state is **genuinely unreachable** on the capture device — NOT merely
     "needs typing / needs placement" (those are now driveable under `DRIVE=full`). Keep SKIP only for:
     - **backend-only conditions** — a state that only fires on a region/backend the capture device is not
       on (e.g. a `duplicated_order` 429 that fires on one staging region but not another);
     - **platform-only** — behaviour visible only on a platform the capture device is not (e.g. an iOS-26
       tap-arbitration change, invisible on an Android capture device);
     - **access-gated** — a screen behind an account type / feature flag we cannot provision on staging
       (e.g. a B2B business account + a Split flag we can't toggle);
     - **real-money / irreversible external side-effect** — a state needing an actual payment or a real
       external mutation (a staging order placement is NOT this — it is the sanctioned staging drive).
  Append the PR number + a one-phrase reason to a `DIFF_SKIPPED` list; do NOT open a device for it.

For each RECORDABLE PR, also emit a one-line **capture plan** (consumed by B3 / Step 2.5's scoping):

- **`trigger:`** the control the recording must START on, for reuse / re-order / button-triggered
  behaviours — or `none` for static screens.
- **`source-data:`** what complete state to pick/seed first — e.g. "a fully-populated past
  order" — or `none`.
- **`auth-mutating: yes|no`** — does the demo log out, sign up, or switch accounts? These
  contaminate the shared device's login state and are ORDERED LAST in B3.
- **`resettable: yes|no`** — can the flow be reset to the recording start point (in-flow
  undo → pop-home + re-nav → force-stop + relaunch)? `no` for one-shot / consumable-fixture flows
  (force-stop clears process state only; persisted client state survives).
- **`reset:`** the reset method to use — `in-flow undo` / `pop home + re-nav` / `force-stop + relaunch`
  / `n/a`.
- **`replayable: yes|no`** — eligible for the two-pass rehearse→record replay: `yes`
  iff the flow is **Tier-2 / long-async** (the replay trigger) AND **resettable** AND **not** one of the
  four single-pass classes (non-resettable / consumable crux / transient crux / auth-mutating). Tier-1
  single-action and pixel-verify colour tickets are always `no` (never rehearse — AC7). `preview
  --capture-only` re-derives this itself; the plan line just makes the batch's intent legible.

Build `RECORDABLE` (the subset that advances to B2/B3) and `DIFF_SKIPPED` (PR# + reason, surfaced in B4).
This judgment is intentionally LLM-driven — no regex reliably separates "visible UI change" from "a
refactor that happens to touch a widget file". **Bias toward SKIP only when an ambiguous diff is purely
structural** (a refactor touching widget files with no visible effect); do NOT skip merely because the
change is behavioural rather than pixel-static — that was the C9 over-conservatism this rubric replaces.

If `RECORDABLE` is empty → print the B4 summary form (all candidates diff-skipped) and **exit 0 without
acquiring a device** (the whole point of judging before B2).

### B1.9 — plan gate (`--plan` only) — HITL review before any device opens

_Run this step ONLY when `PLAN == 1`. When `--plan` is absent, skip B1.9 entirely and fall straight
through to B2 (batch behaviour is unchanged — the agent captures directly)._

**This is the batch reuse of the CORE Plan gate (single-mode Step 1.9)** — the same checks
(recordability, navigation grounding, data-prereq probe, **N-scenario enumeration bounded by
`--max-scenarios`**), applied to each PR in `RECORDABLE` instead of one. `--plan` turns the batch into a
review-then-capture flow. Everything cheap and reversible runs FIRST against the **current PR heads**, the
whole plan is presented, and the run STOPS ONCE for human review. On approval, capture proceeds in the
SAME session against those same heads — so the plan is generated fresh and consumed immediately, never
persisted-and-re-consumed, which is why no staleness / version-drift handling is needed.

**One aggregate STOP, not per-PR.** Unlike single mode (one PR, one STOP), the batch aggregates every PR's
plan into a SINGLE STOP before any device opens. This is deliberate: the B3 capture sub-agents are
non-interactive (a spawned sub-agent cannot host an `AskUserQuestion`), so a per-inner-call HITL prompt is
impossible — aggregating the plan into one orchestrator-level STOP is the build-time settlement of the
plan's open "per-call vs aggregate" sub-detail. Each approved PR then captures its scenarios in its own
B3 sub-agent (the scenario set for each PR is written to that worktree's `.dev/ui-tweak/scenarios`).

**1. Navigation grounding (per RECORDABLE PR, read-only, no device).** For each PR's capture plan
(B1.5), confirm the target screen is plausibly reachable BEFORE committing a device to it: a known route
/ `ggv://` deep-link host (from the cached target), or a described tap-through. Mark each PR
`nav: grounded` or `nav: unresolved (<reason>)`. `nav: unresolved` PRs stay in the plan but are flagged —
the human decides whether to keep or drop them.

**2. Data-prerequisite probe (per RECORDABLE PR, launch-free, no device).** Some demos need specific
staging state to exist (e.g. a recent delivered order with an assigned courier for a favourite-driver
demo, or a Personal-type account for an FPS demo). Probe those prerequisites at plan time via a
**launch-free backend/API query** where one is available (Tier-0), and record `data: ok` /
`data: missing (<what>)` / `data: unprobeable (<why>)` per PR. A `data: missing` PR is surfaced so the
human can seed the fixture or drop the PR rather than discover the gap only after a build+launch fails.

> **Tiering (forward note, non-blocking):** the probe is Tier-0 (launch-free query at plan time). Facts
> that go stale between plan and capture — a sliding "last 7 days" window, a consumable fixture — should
> be RE-probed at capture start / post-login pre-record (Tier-1). Tier-1 re-probing lives with the
> per-ticket capture (Step 2.5 / B3 re-confirmation) and is not required for this plan gate; record the
> Tier-0 result here.

**2.5. Scenario enumeration (per RECORDABLE PR).** Run the core Step 1.9 scenario enumeration for each PR:
from its ACs, list the distinct demonstrable outcomes as `{key, intent, reset-method, expected-crux}`,
bounded by `--max-scenarios` (default 3; surface any dropped). A single-outcome PR yields one scenario
(→ one clip). Carry each PR's scenario set into B3 (each B3 sub-agent writes its PR's set to that
worktree's `.dev/ui-tweak/scenarios`).

**3. Write the plan receipt (record only — NOT re-consumed).** Write the plan to a receipt file for
traceability. It is never read back as input (the same session captures directly on approval):

```bash
mkdir -p "$(cd .. && pwd)/.ggx-demo"
PLAN_FILE="$(cd .. && pwd)/.ggx-demo/plan-$(git rev-parse --short HEAD 2>/dev/null || echo batch).md"
# Write: the discovery filter (draft-only?), the RECORDABLE set with each PR's sha (current head),
# recordability verdict, capture plan (trigger/source-data/auth-mutating/resettable/reset/replayable),
# nav grounding, and the Tier-0 data-prereq result; plus the DIFF_SKIPPED list with reasons.
# Keep it a human-readable record — do NOT design it to be parsed back in.
```

**4. Present + STOP for review.** Show the human, in the session:
- the candidate set and the discovery filter (`--draft` on/off);
- per RECORDABLE PR: `#<num> sha=<short> — <recordability one-liner> | nav: <grounded|unresolved> | data: <ok|missing|unprobeable> | scenarios: <N> (<key,key,…>)`;
- the `DIFF_SKIPPED` non-recordable list with reasons;
- the capture ORDER (auth-mutating PRs last, per B3).

Then **STOP** and ask for approval via `AskUserQuestion`:
- **Capture all now** — proceed to B2 → B3 for the full `RECORDABLE` set in the same run.
- **Capture a subset** — the human names PRs to drop (typically `nav: unresolved` / `data: missing`);
  narrow `RECORDABLE` to the kept set, then proceed to B2.
- **Abort** — exit 0 without acquiring a device; the receipt file remains as a record.

Because approval leads straight into B2/B3 in the SAME session against the heads just planned, there is
no re-fetch and no plan-staleness window. A later `/ggx-demo --batch --plan` run re-plans fresh.

### B2 — acquire the device ONCE

Reached only when B1.5 produced a non-empty `RECORDABLE`. Resolve the profile (Step 0). No `ui_preview_cmd` → `ggx-demo --batch: no device-preview command for
this platform — skipping demos.` exit 0. Acquire a device once, in order — stop at the first that yields
one, and pin it as `$DEV` for the whole pass (no lock because there is only one actor):

- **(a)** a running device — `$FLUTTER_BIN devices --machine` lists a booted sim / connected handset.
- **(b)** boot the designated persistent sim — `$FLUTTER_BIN emulators --launch <id>` (or `xcrun simctl
  boot <udid>`), then poll `$FLUTTER_BIN devices --machine` with a bounded counter loop — **never
  `timeout`** (absent on macOS; see `preview.md`). The device need not be pre-logged-in — the Step 2.4
  login gate logs in per-pass when `demo_auth` is configured.
- **(c)** none available → `ggx-demo --batch: no device available — skipping all demos (fail-soft).`
  exit 0.

### B3 — serial loop: ONE sub-agent per ticket on `$DEV` (context isolation)

**Execution model — serial, single-tenant, one sub-agent per ticket.** The device phase runs strictly
one ticket at a time (`$DEV` is a single shared device — NEVER two actors at once; this is true by
construction, no lock, no TTL). Each ticket's capture runs in its OWN sub-agent, so the ~150–180k tokens
of build logs + screenshots + adb output that a single capture generates stay OUT of the orchestrator's
context. The orchestrator holds only a ≤2k structured summary per ticket (≈30k for a 15-PR batch,
versus ≈1M if every capture ran inline in one session). This is **context isolation, NOT parallel
fan-out** — the sub-agents run one after another, each awaited before the next opens the device.

**Spawn level (nested-spawn constraint).** The per-ticket sub-agent is the ONLY nested leg `/ggx-demo`
introduces. Run the batch from an orchestrator context that can spawn a level-1 sub-agent: an operator
session (top-level) or the `/ggx-dispatcher --demo` / `/ggx-on-duty --demo` **post-join** main session
(the §5.2 join has completed — the demo batch is not itself inside the dispatcher's Workflow leg). Do
NOT invoke `/ggx-demo --batch` from inside an already-nested worker leg, where a further spawn would be
level-2 (unsupported — see `ARCHITECTURE.md` "Nested-spawn constraint"). The sub-agent itself performs
NO further spawning: it runs Steps 1–5 directly (`preview --capture-only`'s own internal two-pass replay
is not a sub-agent spawn).

**Ordering — every `auth-mutating: yes` PR LAST.** An auth-mutating demo (logout → signup, account
switch) leaves the shared device in a different login state, and the login-wall short-circuit below never
fires for it (that guard catches *failures*, not a self-inflicted logout) — so a logged-in demo scheduled
after it would silently capture the wrong state. Sort `RECORDABLE`: `auth-mutating: no` first, `yes`
last. Login-state restoration after an auth-mutating demo is NOT the orchestrator's job (driving a
re-login from the orchestrator would pull device/adb output back into its context — the very isolation
this model exists to preserve). It rides the NEXT sub-agent's live state re-confirmation below: a fresh
sub-agent reads the device's login state live, sees the logged-out/switched state left by the prior
auth-mutating demo, and re-runs the Step 2.4 gate itself before recording. The LAST ordering is the
primary guard (at most the batch tail is auth-mutating); the per-sub-agent live re-confirmation is the
belt-and-braces that also covers a batch with more than one auth-mutating demo.

**Each sub-agent reads device + login state LIVE off the device — it does NOT inherit it.** Because state
lives on the shared device (not in orchestrator memory), a fresh sub-agent must OBSERVE it rather than
trust a hand-off value. At the START of its ticket (after build+install, before capture) the sub-agent
takes one read-only screenshot and confirms the logged-in account / fixtures match what its capture plan
needs; mismatch → it runs the Step 2.4 gate (or re-seeds source data per the plan) before recording.
Login/session across a reinstall is unpredictable — a reinstall can come back logged into a *different*
stored account (stale fixtures, old orders). The per-ticket rebuild itself is correct and required (each
diff must be in the binary) — do NOT "optimize" it away by reusing a prior ticket's build.

**Two-pass replay is internal to `preview` (inside the sub-agent).** For a `replayable: yes` PR,
`preview --capture-only` rehearses (no recording) → resets → records ONE smooth scripted replay, killing
the LLM-latency dead air that once inflated a clip to over two minutes. Neither the batch nor the
orchestrator orchestrates the two passes — they only carry the `replayable` / `reset` plan lines (B1.5)
so the intent is legible. The live state re-confirmation above still holds, and `preview` repeats it
**after its reset, before the replay** (the reset is one more state transition since the last verify).
Auth-mutating demos are a non-replayable class, so they stay single-pass AND keep their LAST ordering +
re-login here.

**Dispatch loop.** For each `RECORDABLE` PR in the sorted order, spawn ONE sub-agent, await it, then move
on. The sub-agent's brief: "Run `/ggx-demo <PR-number>` (single-ticket Steps 1–5, including Step 3.5's
content assertion) for exactly this one PR on the already-running device `$DEV`. Read device + login
state live off the device; re-login only if logged out. Return ONLY a ≤2k structured summary — do NOT
dump build logs, adb output, or screenshots into your reply." **When the batch ran with `--plan`**, the
orchestrator also passes this PR's APPROVED scenario set (from B1.9) in the brief; the sub-agent writes it
verbatim to `$WT/.dev/ui-tweak/scenarios` at Step 2.5 and does NOT re-run the Step 1.9 STOP (already
approved) — so it captures one clip per approved scenario. The summary MUST carry: `outcome` =
`captured | skipped`, and on skip a one-phrase `reason` plus a `login_wall: yes|no` flag (a Step 3.5
`login screen` content failure or a Step 2.4 login-wall counts as `login_wall: yes`). The orchestrator
parses that summary — it never re-reads the sub-agent's device output.

```
CAPTURED=0; SKIPPED=0; REASONS=""; LOGIN_WALL=0
for PR in (RECORDABLE sorted: auth-mutating:no first, auth-mutating:yes last):
    summary = spawn a serial sub-agent for THIS PR only, then await it   # single device, single actor
    if summary.outcome == "captured":
        CAPTURED += 1
        # No orchestrator re-login: if this PR was auth-mutating, the NEXT sub-agent's live state
        # re-confirmation detects the logged-out/switched device and re-runs the Step 2.4 gate itself
        # (device I/O stays inside the sub-agent). Auth-mutating-LAST keeps this to the batch tail.
    else:  # skipped (the sub-agent's Steps 1–5 fail-LOUD; it reports the reason in its ≤2k summary)
        SKIPPED += 1; REASONS += " #PR"
        WARN "ggx-demo --batch: PR #PR demo failed (<summary.reason>); continuing." (fail-soft)
        if summary.login_wall == "yes":
            LOGIN_WALL = 1
            WARN "ggx-demo --batch: login wall — short-circuiting remaining demos (shared device, same login state); configure demo_auth + a staging account on the Notion page."
            break
```

The loop is fail-soft end to end (the `/_slack-notify` contract): a per-ticket loud failure inside the
sub-agent degrades to one WARN line and continues; the batch never blocks/fails its caller. A `login
wall` short-circuits the rest (one shared device = one shared login state, so it recurs identically).

### B4 — summary (always exit 0)

```
ggx-demo --batch: <CAPTURED> captured, <SKIPPED> capture-skipped (<REASONS>), <DIFF_SKIPPED_COUNT> diff-skipped non-recordable (<DIFF_SKIPPED PR#+reasons>), device=<DEV|none>.
```

`DIFF_SKIPPED_COUNT` / its reasons come from B1.5 (PRs judged non-recordable from the diff, never opened a
device); `SKIPPED` / `REASONS` are the B3 per-ticket capture failures. Append ` — SHORT-CIRCUITED at login
wall (configure demo_auth + a staging account on the Notion page)` when `LOGIN_WALL=1`. Per-ticket idempotency (deterministic Linear title + PR-body marker-region replace)
makes re-running safe — a re-run after fixing login picks up exactly the still-undemoed PRs. STOP.

## Disposition — fail-LOUD (R13), the inverse of `/ui-tweak`'s designer-facing fail-silent

Every failure path above emits ONE deterministic `GGX-DEMO FAIL: …` line to stderr and exits non-zero:
unresolvable PR, `gh`/Linear unreachable, head mismatch, no device, auto-login failed (login wall), unreachable target screen,
`screenrecord` ladder exhausted, or the Step 3.5 content assertion failing (blank frame / stuck on the
login screen). The throwaway worktree (if any) is removed on every exit
path. **Batch mode (B3) catches this loud failure fail-soft** — the per-ticket sub-agent reports the
reason in its ≤2k summary, the orchestrator counts it as a per-ticket skip and continues (or
short-circuits on a `login wall`); the batch as a whole never fails its caller.

The flutter-only / Linear-only no-op (Step 0) is the ONE exit-0 non-success case — there is simply
nothing to capture on a build-only platform.

## Constraints

- Linear-only, flutter-only v1. Batch mode demos any open PR of mine with a recordable UI change
  (not bound to `design bug` / ui-tweak; a diff-first judge filters non-UI PRs before any
  device opens); single mode demos an explicit ticket/PR. (Inline-renderable PR images are a separate concern.)
- Edits NO source, writes NO walker markers, never enters `/ui-tweak:ff`.
- Reuses `preview --capture-only` (capture) + the `ff.md` Idempotent-attach contract (upload/embed) —
  it does NOT reimplement either. The only logic owned here is PR/worktree resolution, the head guard,
  the CORE `--plan` gate (Step 1.9 — recordability + nav grounding + data-prereq + N-scenario enumeration
  + HITL STOP), the per-key post-capture content assertion, the scenario-file handoff to `preview`, the
  fail-loud disposition, and (batch) device-once + self-discovery + the `--draft` filter + the B1.9 reuse
  of the core plan gate + the serial one-sub-agent-per-ticket loop.
- Batch device phase is serial + single-tenant: one ticket at a time on one shared device, each in its
  own sub-agent for context isolation (≤2k summary per ticket to the orchestrator). NOT parallel fan-out.
- `--plan` = HITL review gate + multi-scenario, a **CORE flag valid in both modes** (single: Step 1.9
  plans one demo; batch: B1.9 reuses the same gate over the discovered set, ONE aggregate STOP). Plan
  built fresh against current heads, presented, STOP; on approval capture in the SAME run (never stale).
  Enumerates N scenarios (`--max-scenarios`, default 3) → one clip per scenario, keyed. `--draft` is
  batch-only (a discovery filter). Both default OFF.
- Multi-clip output: on `--plan`, `demo-files` is keyed (`<scenario-key>\t<path>`; bare line = `main`),
  each clip attached under `ui-tweak-demo-<sha>-<key>` (bare `ui-tweak-demo-<sha>` for the single `main`
  key), one `## Demo` PR-body region with N labeled links, `--force` REPLACE per key. Bound N; a
  per-scenario failure LOUD-skips that clip (never a silent empty) — the whole run fails LOUD only when
  EVERY scenario fails.
- Driving is gated on the build (`preview` Step 2.4.4 `DRIVE=full|nav-only`): on a confirmed staging
  build the capture MAY type + place orders to reach the crux (staging QA account only); off-staging it
  fails closed to nav-only. The safety condition is the build, not this skill.
- Post-capture content assertion (Step 3.5) gates every attach, PER scenario key: a non-blank frame that
  is not the login screen. A file-exists / non-empty check is explicitly insufficient. Runs on `--force`
  replace too.
- Batch mode (`--batch`) absorbed the former `/_ui-demo-batch` — there is now ONE post-hoc demo
  skill. Self-discovers open PRs lacking a demo, filtered by a diff-first recordability judge; no JSON input.
- Regression case: `/ggx-demo <ticket-id>` reproduces the equivalent manual post-hoc demo.
