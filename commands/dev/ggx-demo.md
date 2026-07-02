---
name: ggx-demo
argument-hint: "<TICKET|PR> | --batch"
description: >
  Post-hoc UI demo capture for already-shipped PRs with a recordable UI change.
  Operator skill with two modes — single (`<TICKET|PR>`) and self-discovering
  batch (`--batch`, GGC-66, which absorbed the former `/_ui-demo-batch`):
  `--batch` captures a demo for every open PR of yours that still lacks one
  (no JSON input, no class/title gate — a diff-first LLM recordability judge
  decides which PRs open a device; GGC-69), serially on one device. Single mode:
  given a Linear
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
- `/ggx-demo <TICKET|PR> --force` — **replace mode (GGC-115 E14)**: re-record and REPLACE an existing
  demo on the same commit. Without `--force`, the Step-4 dedup SKIPs any ticket that already carries a
  `ui-tweak-demo-<sha>` attachment for the current sha — correct for idempotent re-runs, wrong when the
  existing demo is bad (wrong flow / expired clip) and needs replacing. With `--force`, Step 4 runs the
  attach contract's REPLACE path (delete the old attachment → upload the new capture → rewrite the
  PR-body link; the `assetUrl` changes on re-upload). Single-ticket mode only; batch never forces.
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
# GGC-115 E14: --force = replace mode (Step 4 takes the attach contract's REPLACE path on a same-title
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
> so a post-hoc run leaves no residue. The reused `../<ID>` worktree is left exactly as found (minus the
> Step 2.5 `ticket.json`, which Step 5 removes for a reused worktree) — and because `preview
> --capture-only` writes no walker markers, a later bare `/ui-tweak` resume there is never mis-routed.

## Step 2.5 — cache ticket context (for Step 2.4 region inference + Step 2.5 Tier-1 host)

`preview --capture-only` reads `.dev/ui-tweak/ticket.json` to (a) **infer the account region** for the
Step 2.4 login gate (GGC-65 auto-resolve — market token → `hk`/`sg`/… else `hk`) and (b) derive the
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
   **With `--force` (GGC-115 E14), same-title detection takes the contract's REPLACE path instead of
   SKIP**: `delete_attachment` the old one → upload the new capture → rewrite the PR-body link with the
   NEW `assetUrl` (re-upload always mints a fresh URL — the old link is dead once the attachment is
   deleted, so the PR-body rewrite is mandatory, not cosmetic).
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
if [ -n "$THROWAWAY" ]; then
  git worktree remove --force "$THROWAWAY" 2>/dev/null   # throwaway removed whole — no per-file cleanup
else
  rm -f "$WT/.dev/ui-tweak/ticket.json"                  # reused worktree: drop the Step 2.5 cache ("left as found")
fi
echo "ggx-demo: captured + attached demo for $TICKET_ID (PR #$PR, sha $(git -C "$WT" rev-parse --short HEAD 2>/dev/null))."
exit 0
```

> Cleanup runs on the **success** path above; the fail-LOUD exits in Steps 2–3 already remove a throwaway
> worktree. For a reused worktree those early exits may leave `ticket.json` behind — harmless (not a
> walker marker), and the next run overwrites it — but if strict "left as found" matters on a failure
> path too, delete it in those branches as well.

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

### B1 — discover candidate PRs (no input) — GGC-75 (attachment-truth dedup)

The "already demoed" dedup keys on the **Linear `ui-tweak-demo-<sha>` attachment**, NOT the PR-body
`<!-- ui-tweak-demo -->` marker. The marker is written at PR-open *regardless of capture success*:
forward `/ui-tweak:ff`'s "Deliver PR body" emits a marker-wrapped `## Demo` block even when
`preview`'s navigate+capture fail-silents (the block just carries a "No screenshot" line). So
**marker-present ≠ demo-captured** — keying discovery on the marker permanently skipped PRs that never
actually recorded (CAF-613 #644 / CAF-519 #645, both flagged 2026-06-23). The Linear attachment is the
authoritative signal: both forward `/ui-tweak:ff` and Step 4 here create `ui-tweak-demo-<sha>` **only
after a real capture** (`demo-files` non-empty); a fail-silent run produces no such attachment. This is
the **same signal source** as Step 4's idempotent-attach dedup, so discovery and re-attach agree.

```bash
# Every open PR of MINE. author=@me is a HARD limit (GGC-69 — never touch others' PRs). NO class/title
# gate (the old `^## UI Tweak` body filter was DROPPED in GGC-69; the dispatch finisher emits that title
# unreliably — GGC-67). Recordability is judged per-PR in B1.5 (diff-first), before any device opens.
PRS=$(gh pr list --author "@me" --state open --json number,headRefName,url,body,title)
[ "$(printf '%s' "$PRS" | jq 'length')" -gt 0 ] || { echo "ggx-demo --batch: no open PRs of mine."; exit 0; }
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

This makes the regression cases (#644 / #645 — marker present, no `ui-tweak-demo-*` attachment) candidates
again, while PRs with a real attachment stay excluded.

(A caller that already holds the freshly-shipped rows — `/ggx-dispatcher --demo`, `/ggx-on-duty --demo`
— MAY narrow to these PRs, but the default is self-discovery so no caller has to hand-build a JSON array.)

### B1.5 — recordability judge (diff-first, LLM, before any device) — GGC-69

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
  the state that shows it**. Two shapes qualify (GGC-115 C9 loosened the rubric — the first batch
  recorded 1/12 and skipped obviously-recordable PRs):
  1. **Static UI change** — widget / layout / style / copy / asset / screen changes in app UI code,
     with the affected screen navigable (a known route, a `ggv://` deep-link, or tap-through).
  2. **Behavioural / error / state change** — the diff alters what the user SEES when a flow runs
     (an error message, a pre-fill, a redirect, a validation) and the flow can be DRIVEN to that
     state on staging. "No pixel diff in a static shot" / "nav-only taps can't select it" do NOT
     make a PR unrecordable — recording the flow is the demo.
     **Auth-error demos are explicitly RECORDABLE (GGC-115 C10)**: staging accepts the SMS request,
     reaches the code screen, and a deliberately wrong code (e.g. `1234`) returns a real 401 → the
     specific message renders. Wrong-code / wrong-password / verification-failure PRs need no real
     OTP and belong in `RECORDABLE`.
- **SKIP (record the reason, no device)** — when the diff shows none of the above:
  1. No user-visible surface at all — pure backend / config / build / test / analytics /
     dependency-only diff whose effect cannot be seen on any screen.
  2. Visible change present but the state is not reachable (no route AND no plausible tap-through,
     or the flow requires state we cannot create on staging — e.g. a real payment).
  Append the PR number + a one-phrase reason to a `DIFF_SKIPPED` list; do NOT open a device for it.

For each RECORDABLE PR, also emit a one-line **capture plan** (consumed by B3 / Step 2.5's scoping —
GGC-115 B6/B7/D11):

- **`trigger:`** the control the recording must START on, for reuse / re-order / button-triggered
  behaviours (B6) — or `none` for static screens.
- **`source-data:`** what complete state to pick/seed first (B7) — e.g. "a fully-populated past
  order" — or `none`.
- **`auth-mutating: yes|no`** (D11) — does the demo log out, sign up, or switch accounts? These
  contaminate the shared device's login state and are ORDERED LAST in B3.

Build `RECORDABLE` (the subset that advances to B2/B3) and `DIFF_SKIPPED` (PR# + reason, surfaced in B4).
This judgment is intentionally LLM-driven — no regex reliably separates "visible UI change" from "a
refactor that happens to touch a widget file". **Bias toward SKIP only when an ambiguous diff is purely
structural** (a refactor touching widget files with no visible effect); do NOT skip merely because the
change is behavioural rather than pixel-static — that was the C9 over-conservatism this rubric replaces.

If `RECORDABLE` is empty → print the B4 summary form (all candidates diff-skipped) and **exit 0 without
acquiring a device** (the whole point of judging before B2).

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

### B3 — serial loop (one ticket at a time on `$DEV`)

Run the per-ticket procedure (Steps 1–5 above) for each `RECORDABLE` PR (B1.5) — **ordered with every
`auth-mutating: yes` PR LAST (GGC-115 D11)**. An auth-mutating demo (logout → signup, account switch)
leaves the shared device in a different login state, and the login-wall short-circuit below never fires
for it (that guard catches *failures*, not a self-inflicted logout) — so a logged-in demo scheduled
after it silently captures the wrong state. Sort `RECORDABLE`: `auth-mutating: no` first, `yes` last.
**After each auth-mutating demo, restore the login state** by re-running the Step 2.4 login gate
(auto-resolve + login) before the next ticket — belt-and-braces even with the LAST ordering, since a
batch can contain more than one auth-mutating demo.

**Per-ticket state re-confirmation (GGC-115 D13).** Login/session across a reinstall is unpredictable —
a reinstall can come back logged into a *different* stored account (stale fixtures, old orders). At the
START of each ticket (after its build+install, before capture), take one read-only screenshot and
confirm the logged-in account / fixtures match what the capture plan needs; mismatch → run the Step 2.4
gate (or re-seed source data per the plan) before recording. The per-ticket rebuild itself is correct
and required (D12 confirmed: each diff must be in the binary) — do NOT "optimize" it away by reusing a
prior ticket's build.

Catch each loud failure fail-soft and count it; on a `login wall` failure **short-circuit** the rest
(one shared device = one shared login state, so it recurs identically):

```bash
CAPTURED=0; SKIPPED=0; REASONS=""; LOGIN_WALL=0
for PR in $(printf '%s' "$RECORDABLE" | jq -r '.[].number'); do
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
ggx-demo --batch: <CAPTURED> captured, <SKIPPED> capture-skipped (<REASONS>), <DIFF_SKIPPED_COUNT> diff-skipped non-recordable (<DIFF_SKIPPED PR#+reasons>), device=<DEV|none>.
```

`DIFF_SKIPPED_COUNT` / its reasons come from B1.5 (PRs judged non-recordable from the diff, never opened a
device); `SKIPPED` / `REASONS` are the B3 per-ticket capture failures. Append ` — SHORT-CIRCUITED at login
wall (configure demo_auth + a staging account on the Notion page)` when `LOGIN_WALL=1`. Per-ticket idempotency (deterministic Linear title + PR-body marker-region replace)
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

- Linear-only, flutter-only v1. Batch mode demos any open PR of mine with a recordable UI change
  (GGC-69 — not bound to `design bug` / ui-tweak; a diff-first judge filters non-UI PRs before any
  device opens); single mode demos an explicit ticket/PR. (Inline-renderable PR images are GGC-30, separate.)
- Edits NO source, writes NO walker markers, never enters `/ui-tweak:ff`.
- Reuses `preview --capture-only` (capture) + the `ff.md` Idempotent-attach contract (upload/embed) —
  it does NOT reimplement either. The only logic owned here is PR/worktree resolution, the head guard,
  the fail-loud disposition, and (batch) device-once + self-discovery + the serial loop.
- Batch mode (`--batch`) absorbed the former `/_ui-demo-batch` (GGC-66) — there is now ONE post-hoc demo
  skill. Self-discovers open PRs lacking a demo, filtered by a diff-first recordability judge (GGC-69); no JSON input.
- Regression case (GGC-59): `/ggx-demo CAF-541` reproduces the manual PR #610 demo.
