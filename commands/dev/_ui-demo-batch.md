---
name: _ui-demo-batch
description: "Internal helper invoked by /ggx-dispatcher (--demo, after the §5.2 workflow join) and /ggx-on-duty (--demo, after the Leg-1 dispatch completion). Runs the ui-tweak demo capture for a batch of shipped `design bug` PRs as a SINGLE SERIAL pass — only one actor ever touches the simulator, so the device race that an inline per-ticket demo would create in the parallel fan-out is gone by construction (no lock, no TTL). Acquires the device ONCE up front (a running device, else boots the designated persistent sim — it need not be pre-logged-in; preview's Step 2.4 gate logs in via a staging account when demo_auth is set, GGC-65) so the per-ticket leg always finds one, then loops `/ggx-demo <ticket>` per ticket — `/ggx-demo` (GGC-59) owns the worktree-liveness/head-guard, capture (preview --capture-only), and idempotent attach (deterministic Linear title + PR-body `<!-- ui-tweak-demo -->` marker-region). This helper owns only the device-once acquisition, the serial loop, and per-ticket pass/fail counting: it CATCHES `/ggx-demo`'s loud (non-zero) failures fail-soft. Fail-soft per ticket and overall: NEVER blocks or fails the caller. Linear-only / flutter-only v1; design-bug demos only. Not user-invoked. (Inline-renderable PR images are GGC-30, separate.)"
Prerequisite: >
  - A flutter repo with a resolvable ui-tweak preview command (`ui_preview_cmd`);
    on non-flutter / build-only profiles this helper is a no-op.
  - A running (or bootable persistent) simulator/emulator. It need NOT be
    pre-logged-in: the preview Step 2.4 login gate (GGC-65) logs in with a
    staging QA account when the repo declares a `demo_auth` selector. Without
    that selector, a logged-out device lands on a login wall → fail-soft skip
    (and the batch short-circuits the remaining tickets — shared login state).
  - gh authenticated; Linear MCP authenticated (for the optional Linear mirror).
---

# `/_ui-demo-batch <rows>`

Serial demo-capture pass over a batch of already-shipped `design bug` PRs.
Splits "ship the code" (parallel, device-free — done by the time this runs)
from "record the demo" (serial, device-bound, best-effort) so the single
shared simulator is never driven by two agents at once.

**This helper is fail-soft end to end** (the `/_slack-notify` contract): any
failure — no device, a dead worktree, a capture error, a Linear/gh hiccup —
degrades to one WARN line and continues; it NEVER blocks or fails the caller,
and it never touches ship state (labels, PR open/closed, ticket status). Per
ticket it edits only what `/ggx-demo` edits — the PR-body `## Demo` region
(`<!-- ui-tweak-demo -->` marker) + a Linear attachment — nothing else.

## Input

`<rows>` — the shipped design-bug tickets, as a JSON array (preferred) or a
TSV, one entry per ticket:

```json
[{ "ticketId": "CAF-569", "prUrl": "https://github.com/<org>/<repo>/pull/549" }, ...]
```

Callers filter to design-bug rows before invoking:

- `/ggx-dispatcher --demo`: the §5.2 `rows` where `uiTweak === true` and
  `outcome === "done"` and `prUrl` is non-null.
- `/ggx-on-duty --demo`: the same filter over the `{counts, rows}` it consumed
  on the dispatch-workflow completion.

Empty input → print `ui-demo-batch: no design-bug PRs to capture.` and exit 0.

## Step 0 — resolve env + acquire the device ONCE

1. **Resolve the project profile** (`ui_preview_cmd`, `ui_build_cmd`, the
   fvm-aware flutter binary) the same way the ui-tweak stages do — read
   `.dev/ui-tweak/flutter-bin` from the first row's worktree if present, else
   resolve from the repo profile. **No `ui_preview_cmd` (android/ios build-only
   profile, or non-flutter)** → print `ui-demo-batch: no device-preview command
   for this platform — skipping demos.` and exit 0 (no-op).

2. **Acquire the simulator (once, up front), in this order — stop at the first
   that yields a device:**
   - **(a) a running device** — `$FLUTTER_BIN devices --machine`
     already lists a booted simulator / connected handset → use it. This is the
     common case (the owner keeps one running).
   - **(b) boot the designated persistent sim (the auto-boot fallback).** No
     running device → boot the owner's persistent simulator. Use `$FLUTTER_BIN
     emulators --launch <id>` (or `xcrun simctl boot <udid>`), then poll
     `$FLUTTER_BIN devices --machine` with a bounded counter loop — **never
     `timeout`** (absent on macOS; see `preview.md`). Login state need not
     persist across reboots — the preview Step 2.4 gate logs in per-pass when a
     `demo_auth` selector is configured.
   - **(c) no device and none bootable** → print `ui-demo-batch: no device
     available — skipping all demos (fail-soft).` and exit 0.

   Pin the chosen device id `$DEV` for the whole pass — every ticket installs
   onto the SAME device. There is no lock because there is only one actor.

## Step 1 — serial loop over `/ggx-demo` (ONE ticket at a time on `$DEV`)

The per-ticket mechanics (worktree-liveness + head-guard, install/launch, the Tier-1/Tier-2
navigate+capture, and the idempotent attach) are **owned by `/ggx-demo`** (GGC-59) — the single-ticket
sibling of this batch. This helper does NOT re-implement them; it loops `/ggx-demo` and counts. Step 0
already put a device up, so each `/ggx-demo`'s path-(a) acquisition finds it (it deliberately
never cold-boots); preview's Step 2.4 gate logs in per-pass when `demo_auth` is configured.

For each row, in order:

```bash
CAPTURED=0; SKIPPED=0; REASONS=""; LOGIN_WALL=0
for row in <rows>; do
  TICKET=<row.ticketId>
  # /ggx-demo is fail-LOUD (non-zero on any failure). Capture its stderr so we can classify the reason;
  # catch the failure fail-soft here and count.
  ERRLINE=$(/ggx-demo "$TICKET" 2>&1 1>/dev/null) && RC=0 || RC=$?
  if [ "$RC" = 0 ]; then
    CAPTURED=$((CAPTURED+1))
  else
    SKIPPED=$((SKIPPED+1)); REASONS="$REASONS $TICKET"
    echo "ui-demo-batch: WARN — /ggx-demo $TICKET failed (see its GGX-DEMO FAIL line); continuing." >&2
    # GGC-65 short-circuit: the whole batch shares ONE device with ONE login state. A `login wall`
    # failure (auto-login failed, or no `demo_auth` selector configured) will recur identically for
    # every remaining ticket — stop grinding the serial loop, surface it once, and bail.
    if printf '%s' "$ERRLINE" | grep -qi 'login wall'; then
      LOGIN_WALL=1
      echo "ui-demo-batch: login wall — short-circuiting the remaining demos (shared device, same login state); configure demo_auth + a staging account on the Notion page." >&2
      break
    fi
  fi
done
```

- Pass the **ticket id** (preferred; `/ggx-demo` resolves the open PR by branch) or the `prUrl`.
- `/ggx-demo` self-cleans any throwaway `../<ticketId>-demo` worktree on every exit path, so the batch
  leaves no residue.
- A `/ggx-demo` loud failure is a per-ticket **skip**, never a batch abort — the batch is fail-soft end
  to end (the `/_slack-notify` contract): it never blocks or fails its caller and never touches ship
  state (labels, PR open/closed, ticket status). Each `/ggx-demo` edits only the Linear attachment + the
  PR-body `## Demo` region.

## Step 2 — summary

Print one grep-able line and exit 0 (always — this helper never fails):

```
ui-demo-batch: <CAPTURED> captured, <SKIPPED> skipped (<REASONS>), device=<DEV|none>.
```

When `LOGIN_WALL=1` the loop short-circuited (auto-login could not pass the login wall — see GGC-65);
append ` — SHORT-CIRCUITED at login wall (configure demo_auth + a staging account on the Notion page)`
to the line so the cause is visible at a glance.

**Idempotency / sweep note.** `/ggx-demo` is idempotent (deterministic Linear attachment title +
PR-body marker-region replace), so re-running over an already-demoed PR is safe. When a caller wants to
sweep rather than take an explicit row list, the target set is: `design bug` + `In Review` + PR open +
the PR **body** has no `<!-- ui-tweak-demo -->` block (already-demoed PRs carry the marker).

## §A — Non-goals (do not extend)

- Do NOT run concurrently with itself — the single-flight guard lives in the
  caller (`/ggx-on-duty`'s `demo.running` state; `/ggx-dispatcher`'s synchronous
  post-join invocation). One demo pass owns the sim at a time.
- Do NOT change ship state — no labels, no PR open/close/merge, no ticket
  status/assignee. Per ticket, edits are confined to what `/ggx-demo` writes:
  the PR-body `## Demo` region (the `<!-- ui-tweak-demo -->` marker block) + a
  Linear attachment.
- Do NOT block or fail the caller — every path exits 0; a total failure is one
  WARN line, never a non-zero exit.
- Do NOT solve inline PR-image rendering (GGC-30) or before/after framing
  (GGC-6) here — this helper closes only the "demo never captured in the
  unattended parallel path" gap.
- Do NOT touch the parallel compile gate (`preview.md` stays build-only) or the
  interactive `/ui-tweak` flow — this helper is the unattended-batch path only.
