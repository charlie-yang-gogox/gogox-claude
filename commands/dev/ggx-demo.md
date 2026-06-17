---
name: ggx-demo
description: >
  Post-hoc UI demo capture for already-shipped `design bug` PRs. Operator skill
  with two modes — single (`<TICKET|PR>`) and self-discovering batch (`--batch`,
  GGC-66, which absorbed the former `/_ui-demo-batch`): `--batch` captures a demo
  for every open PR of yours that still lacks one (no JSON input), serially on
  one device. Single mode: given a Linear
  ticket id OR a PR (number/URL), it resolves the PR's worktree, asserts the
  local HEAD matches the PR head (fail-loud — never demo a stale/unreviewed
  build), runs `/ui-tweak:preview --capture-only` (the SOLE capture point —
  navigate to the target screen on an already-running device, logging in via a
  staging QA account when the repo's `demo_auth` selector is set (GGC-65), and
  record a screenshot + short clip), then idempotently attaches the result to
  the Linear ticket and patches the PR body's `<!-- ui-tweak-demo -->` `## Demo`
  region (replace-between-markers, never append). REUSES the ui-tweak
  capture/upload machinery (does NOT reimplement it). Fails LOUD end-to-end
  (R13): no device / auto-login failed (login wall) / no route / head mismatch /
  screenrecord-ladder exhausted → non-zero exit + deterministic stderr. Edits
  NOTHING in the codebase and writes NO walker markers (it never enters
  `/ui-tweak:ff` / `infer_ui_stage`), so it cannot mis-route a later
  `/ui-tweak` resume. Linear-only / flutter-only v1. NOT designer-facing —
  `/ui-tweak` is the designer entry; this is the operator/pipeline action.
---

<!-- RULE: command content is English. -->

# `/ggx-demo <TICKET|PR>` | `/ggx-demo --batch`

> Productizes the manual post-hoc demo procedure (prototyped on CAF-541 → PR #610). `--auto`-shipped
> `design bug` PRs carry a demo only when one is captured post-ship: the parallel fan-out is device-free
> (build-only), so the demo must be recorded **after** the PR is open — on a device, logging in via the
> Step 2.4 gate (GGC-65) when the repo's `demo_auth` selector is set.
>
> **Why a standalone skill, not a `--demo` flag on `/ui-tweak`** (decision, GGC-59): `/ui-tweak` is
> designer-facing (plain-language cards, fail-silent, rides the forward flow). A post-hoc demo is the
> *opposite* operation — no edit, fail-LOUD, runs against an already-shipped ticket — and an operator
> action (`/ggx-*`). It REUSES the same capture/upload machinery via `preview --capture-only`; only the
> ~15 lines of glue below differ.
>
> **Zero walker contamination**: `/ggx-demo` never enters `ff.md` / `infer_ui_stage`. It writes none of
> the forward markers, so it cannot leave a reused `../<ID>` worktree mis-routed for a later bare
> `/ui-tweak` resume.

## Usage

- `/ggx-demo <TICKET>` — e.g. `/ggx-demo CAF-541`. Resolves the ticket's open PR.
- `/ggx-demo <PR>` — a PR number (`#610` / `610`) or full URL.
- `/ggx-demo --batch` — **no argument**: self-discover every open PR of mine that still lacks a demo and
  capture them serially on one device. See **Batch mode** below.

**Mode dispatch.** If the argument is `--batch`, run the **Batch mode** section (B1–B4) and STOP;
otherwise run the single-ticket flow (Steps 0–5). Both share Step 0's env gate and the per-ticket
Steps 1–5 (batch loops them).

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
ARG="<TICKET|PR>"
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

## Step 2 — resolve the worktree + head-match guard (fail-LOUD)

The demo MUST build the exact diff that shipped. Reuse `../<ID>` only when it is genuinely on the PR
head; otherwise check out a throwaway worktree at the PR head. Then assert HEAD equals the PR head
(GGC-59 must-fix #4) — a mismatch means the worktree drifted (a sibling reset, an unpushed local commit)
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
> so a post-hoc run leaves no residue. The reused `../<ID>` worktree is left exactly as found — and
> because `preview --capture-only` writes no walker markers, a later bare `/ui-tweak` resume there is
> never mis-routed.

## Step 3 — capture (reuse `preview --capture-only`, the SOLE capture point)

From inside `$WT`, set `UI_TWEAK_FF=1` (so `/ui-tweak:preview`'s Step-0a misdirect guard passes — this
is an orchestrated call, not a stray designer invocation) and invoke:

```
UI_TWEAK_FF=1  /ui-tweak:preview --capture-only
```

`preview --capture-only` (Step 0c) acquires an **already-running** device (path (a) only, **no
cold-boot**), launches the existing build, runs the **Step 2.4 login gate** (GGC-65: logs in with a
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

## Step 4 — idempotent attach (the shared `ff.md` contract — do NOT re-derive)

Run the **Idempotent attach** contract documented in `commands/design/ui-tweak/ff.md` ("Deliver PR body"
→ "Idempotent attach (shared contract …)") against `$TICKET_ID` / PR `$PR`, using the files listed in
`$WT/.dev/ui-tweak/demo-files`:

1. **Linear attachment dedupe** by deterministic title `ui-tweak-demo-<sha>` (`<sha> = git -C "$WT"
   rev-parse --short HEAD`). List the ticket's existing attachments; skip the upload (reuse the existing
   `assetUrl`) when that title already exists, so re-running `/ggx-demo` never adds a second inline video.
2. **PR body `## Demo` region** wrapped in `<!-- ui-tweak-demo -->` / `<!-- /ui-tweak-demo -->`:
   read the current body, **replace between the markers** (or replace an existing unmarked `## Demo`
   section, else append a marked block), then `gh pr edit "$PR" --body <new>`. Never blind-append.
   Re-running produces exactly one marked block.
3. **PR link form**: the Linear `assetUrl` is a **plain link** in the PR body (deterministic 401 to
   GitHub on this private repo); inline render lives on the Linear ticket only.

This is the ONLY write `/ggx-demo` performs: the Linear attachment + the PR-body region. It changes NO
ship state — no labels, no PR open/close/merge, no ticket status/assignee.

## Step 5 — cleanup + summary

```bash
[ -n "$THROWAWAY" ] && git worktree remove --force "$THROWAWAY" 2>/dev/null
echo "ggx-demo: captured + attached demo for $TICKET_ID (PR #$PR, sha $(git -C "$WT" rev-parse --short HEAD 2>/dev/null))."
exit 0
```

## Batch mode (`--batch`) — GGC-66 (absorbed `/_ui-demo-batch`)

`/ggx-demo --batch` captures demos for **every open PR of mine that still lacks one** — no JSON input
(it self-discovers). It is the serial, device-bound counterpart of the single-ticket flow: "ship the
code" is parallel + device-free (the fan-out), "record the demo" is serial here, so the one shared
simulator is never driven by two actors at once (no lock, no TTL — true by construction). Invoked by
`/ggx-dispatcher --demo` (after the §5.2 join) and `/ggx-on-duty --demo` (after the Leg-1 dispatch).

**Fail-soft end to end** (the `/_slack-notify` contract): any per-ticket failure degrades to one WARN
line and continues; the batch never blocks/fails its caller and never touches ship state (labels, PR
open/closed, ticket status). Per ticket it edits only what the single-ticket flow edits — the Linear
attachment + the PR-body `<!-- ui-tweak-demo -->` region.

### B1 — discover the target PRs (no input)

```bash
# My open PRs that are ui-tweak/design-bug PRs AND have no demo yet. ui-tweak PRs carry a `## UI Tweak`
# summary section (written by ff.md's pr stage); a demoed PR carries the `<!-- ui-tweak-demo -->` marker
# block. Want: ui-tweak ∧ ¬demoed.
ROWS=$(gh pr list --author "@me" --state open --json number,headRefName,url,body \
  --jq '[.[] | select(.body | test("(?m)^## UI Tweak")) | select(.body | test("<!-- ui-tweak-demo -->") | not)]')
COUNT=$(printf '%s' "$ROWS" | jq 'length')
[ "$COUNT" -gt 0 ] || { echo "ggx-demo --batch: no open PRs of mine without a demo."; exit 0; }
```

(A caller that already holds the freshly-shipped rows — `/ggx-dispatcher --demo`, `/ggx-on-duty --demo`
— MAY narrow to those PRs, but the default is self-discovery so no caller has to hand-build a JSON array.)

### B2 — acquire the device ONCE

Resolve the profile (Step 0). No `ui_preview_cmd` → `ggx-demo --batch: no device-preview command for
this platform — skipping demos.` exit 0. Acquire a device once, in order — stop at the first that yields
one, and pin it as `$DEV` for the whole pass (no lock because there is only one actor):

- **(a)** a running device — `$FLUTTER_BIN devices --machine` lists a booted sim / connected handset.
- **(b)** boot the designated persistent sim — `$FLUTTER_BIN emulators --launch <id>` (or `xcrun simctl
  boot <udid>`), then poll `$FLUTTER_BIN devices --machine` with a bounded counter loop — **never
  `timeout`** (absent on macOS; see `preview.md`). The device need not be pre-logged-in — the Step 2.4
  login gate logs in per-pass when `demo_auth` is configured.
- **(c)** none available → `ggx-demo --batch: no device available — skipping all demos (fail-soft).`
  exit 0.

### B3 — serial loop (one ticket at a time on `$DEV`)

Run the per-ticket procedure (Steps 1–5 above) for each discovered PR, in order. Catch each loud failure
fail-soft and count it; on a `login wall` failure **short-circuit** the rest (one shared device = one
shared login state, so it recurs identically):

```bash
CAPTURED=0; SKIPPED=0; REASONS=""; LOGIN_WALL=0
for PR in $(printf '%s' "$ROWS" | jq -r '.[].number'); do
  # Run Steps 1–5 for "$PR" (PR-number form). It is fail-LOUD; capture stderr to classify the reason.
  ERRLINE=$( run_steps_1_to_5 "$PR" 2>&1 1>/dev/null ) && RC=0 || RC=$?
  if [ "$RC" = 0 ]; then
    CAPTURED=$((CAPTURED+1))
  else
    SKIPPED=$((SKIPPED+1)); REASONS="$REASONS #$PR"
    echo "ggx-demo --batch: WARN — PR #$PR demo failed (see its GGX-DEMO FAIL line); continuing." >&2
    if printf '%s' "$ERRLINE" | grep -qi 'login wall'; then
      LOGIN_WALL=1
      echo "ggx-demo --batch: login wall — short-circuiting remaining demos (shared device, same login state); configure demo_auth + a staging account on the Notion page." >&2
      break
    fi
  fi
done
```

### B4 — summary (always exit 0)

```
ggx-demo --batch: <CAPTURED> captured, <SKIPPED> skipped (<REASONS>), device=<DEV|none>.
```

Append ` — SHORT-CIRCUITED at login wall (configure demo_auth + a staging account on the Notion page)`
when `LOGIN_WALL=1`. Per-ticket idempotency (deterministic Linear title + PR-body marker-region replace)
makes re-running safe — a re-run after fixing login picks up exactly the still-undemoed PRs. STOP.

## Disposition — fail-LOUD (R13), the inverse of `/ui-tweak`'s designer-facing fail-silent

Every failure path above emits ONE deterministic `GGX-DEMO FAIL: …` line to stderr and exits non-zero:
unresolvable PR, `gh`/Linear unreachable, head mismatch, no device, auto-login failed (login wall), unreachable target screen,
or `screenrecord` ladder exhausted. The throwaway worktree (if any) is removed on every exit
path. **Batch mode (B3) catches this loud failure fail-soft** — counting it as a per-ticket skip and
continuing (or short-circuiting on a `login wall`); the batch as a whole never fails its caller.

The flutter-only / Linear-only no-op (Step 0) is the ONE exit-0 non-success case — there is simply
nothing to capture on a build-only platform.

## Constraints

- Linear-only, flutter-only v1; `design bug` demos. (Inline-renderable PR images are GGC-30, separate.)
- Edits NO source, writes NO walker markers, never enters `/ui-tweak:ff`.
- Reuses `preview --capture-only` (capture) + the `ff.md` Idempotent-attach contract (upload/embed) —
  it does NOT reimplement either. The only logic owned here is PR/worktree resolution, the head guard,
  the fail-loud disposition, and (batch) device-once + self-discovery + the serial loop.
- Batch mode (`--batch`) absorbed the former `/_ui-demo-batch` (GGC-66) — there is now ONE post-hoc demo
  skill. Self-discovers open PRs lacking a demo; no JSON input.
- Regression case (GGC-59): `/ggx-demo CAF-541` reproduces the manual PR #610 demo.
