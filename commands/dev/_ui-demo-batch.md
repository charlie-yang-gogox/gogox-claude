---
name: _ui-demo-batch
description: "Internal helper invoked by /ggx-dispatcher (--demo, after the §5.2 workflow join) and /ggx-on-duty (--demo, after the Leg-1 dispatch completion). Runs the ui-tweak demo capture for a batch of shipped `design bug` PRs as a SINGLE SERIAL pass — only one actor ever touches the simulator, so the device race that an inline per-ticket demo would create in the parallel fan-out is gone by construction (no lock, no TTL). Acquires the device ONCE (a running logged-in device, else boots the designated persistent sim), then per ticket: worktree-liveness check (+ fresh-checkout fallback so a concurrently-reset worktree never demos the wrong code) → install-not-rebuild → /ui-tweak:demo (NAVIGATE capture) → idempotent PR comment carrying a `<!-- ui-tweak-demo -->` marker (never a blind PR-body overwrite). Fail-soft per ticket and overall: NEVER blocks or fails the caller. Linear-only / flutter-only v1; design-bug demos only. Not user-invoked. (Inline-renderable PR images are GGC-30, separate.)"
Prerequisite: >
  - A flutter repo with a resolvable ui-tweak preview command (`ui_preview_cmd`);
    on non-flutter / build-only profiles this helper is a no-op.
  - The owner's persistent, already-logged-in simulator/emulator available to
    boot (login state must persist across reboots — otherwise the capture lands
    on a login screen; that is acceptable fail-soft, not an error).
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
and it never touches ship state (labels, PR open/closed, ticket status). It
edits only a PR comment (and, optionally, mirrors to a Linear comment).

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
   - **(a) a running logged-in device** — `$FLUTTER_BIN devices --machine`
     already lists a booted simulator / connected handset → use it. This is the
     common case (the owner keeps one running).
   - **(b) boot the designated persistent sim (the auto-boot fallback).** No
     running device → boot the owner's persistent simulator (the same named
     device they logged into once; its login + app data persist across reboots,
     so a boot restores a logged-in session). Use `$FLUTTER_BIN emulators
     --launch <id>` (or `xcrun simctl boot <udid>`), then poll
     `$FLUTTER_BIN devices --machine` with a bounded counter loop — **never
     `timeout`** (absent on macOS; see `preview.md`).
   - **(c) no device and none bootable** → print `ui-demo-batch: no device
     available — skipping all demos (fail-soft).` and exit 0. Do NOT cold-boot a
     throwaway/erased simulator: it would not be logged in, so it adds nothing
     (the documented `preview.md` Step 0b rationale).

   Pin the chosen device id `$DEV` for the whole pass — every ticket installs
   onto the SAME device. There is no lock because there is only one actor.

## Step 1 — serial loop (ONE ticket at a time on `$DEV`)

For each row, in order. A failure anywhere in a ticket's steps → WARN, consume,
move to the next ticket (never abort the batch):

1. **Worktree liveness + fallback (the wrong-code guard).** The demo must build
   the exact diff that shipped, but `/ggx-on-duty` Leg-2 `/ggx-pr-resolver`
   reuses and RESETS `../<ticketId>` to `origin/<headRefName>` and can run
   concurrently. So:
   - `BRANCH = gh pr view <prUrl> --json headRefName -q .headRefName`.
   - If `../<ticketId>` exists AND
     `git -C ../<ticketId> rev-parse --abbrev-ref HEAD == $BRANCH` AND the tree
     is clean → use it as `$WT`.
   - Otherwise (missing, wrong branch, or dirty) → `git fetch origin $BRANCH`
     then `git worktree add ../<ticketId>-demo origin/$BRANCH` and use that as
     `$WT` (a throwaway; removed in step 5). Never demo a worktree whose HEAD is
     not the PR branch.

2. **Install-not-rebuild.** Prefer installing the existing compile-gate artifact
   over a fresh build:
   - Try `$FLUTTER_BIN install -d $DEV` from `$WT` (installs the last build
     output, no recompile) and launch the app.
   - If no artifact survived (the compile gate quarantines build side-effects)
     or install fails → fall back to `ui_preview_cmd` (`flutter run`, which
     builds + installs + launches) onto `$DEV`. Rebuild is the acceptable
     fallback for a best-effort stage; install-first keeps the common path to
     seconds, not minutes.
   - Leave the app running on `$DEV` so `/ui-tweak:demo` Step 1 can discover it
     (demo.md does READ-ONLY device discovery — it never boots or launches).

3. **Capture via `/ui-tweak:demo` (reuse — do not re-implement navigation).**
   In `$WT`: write `.dev/ui-tweak/demo-requested` + `.dev/ui-tweak/auto-navigate`
   (NAVIGATE mode), export `UI_TWEAK_FF=1` (clears demo.md's Step 0a misdirect
   guard), and invoke `/ui-tweak:demo`. It fires the Tier-1 `ggv://` deep-link
   (or the Tier-2 codebase-planned nav-only tap-through), captures a screenshot
   + short recording into `.dev/ui-tweak/demo-files`, and is itself fail-silent.
   A capture failure here leaves `demo-files` empty → skip the embed for this
   ticket (WARN, continue). Running unattended (headless), demo.md takes its
   fail-silent path rather than the interactive nav-help prompt.

4. **Idempotent embed — PR COMMENT with a marker (NOT a body overwrite).** When
   `.dev/ui-tweak/demo-files` is non-empty:
   - Upload each artifact via the same path the `pr` stage uses
     (`prepare_attachment_upload` → PUT → `create_attachment_from_upload` on the
     ticket), and assemble a `## Demo` block (a `<!-- ui-tweak-demo -->` HTML
     marker line + the links + any `demo-note`). _(Inline-renderable images on
     the PR are GGC-30's job; v1 links the artifact.)_
   - `gh pr view <prUrl> --json comments` → if a comment containing
     `<!-- ui-tweak-demo -->` already exists (a re-run), **update that comment**
     (`gh api -X PATCH .../comments/<id>`); else `gh pr comment <prUrl> --body`
     a new one. NEVER `gh pr edit --body` — a blind body overwrite would clobber
     a reviewer's edits in the window since the PR opened.
   - Optional: mirror the same block as a Linear comment carrying the same
     marker (idempotent the same way). Best-effort.

5. **Per-ticket cleanup.** If a `../<ticketId>-demo` throwaway worktree was
   created in step 1, `git worktree remove --force` it. Consume
   `.dev/ui-tweak/demo-requested`. Leave `$DEV` running for the next ticket.

## Step 2 — summary

Print one grep-able line and exit 0 (always — this helper never fails):

```
ui-demo-batch: <C> captured, <S> skipped (<reasons>), device=<DEV|none>.
```

## §A — Non-goals (do not extend)

- Do NOT run concurrently with itself — the single-flight guard lives in the
  caller (`/ggx-on-duty`'s `demo.running` state; `/ggx-dispatcher`'s synchronous
  post-join invocation). One demo pass owns the sim at a time.
- Do NOT change ship state — no labels, no PR open/close/merge, no ticket
  status/assignee. Edits are confined to a PR comment (+ optional Linear
  comment).
- Do NOT block or fail the caller — every path exits 0; a total failure is one
  WARN line, never a non-zero exit.
- Do NOT solve inline PR-image rendering (GGC-30) or before/after framing
  (GGC-6) here — this helper closes only the "demo never captured in the
  unattended parallel path" gap.
- Do NOT touch the parallel compile gate (`preview.md` stays build-only) or the
  interactive `/ui-tweak` flow — this helper is the unattended-batch path only.
