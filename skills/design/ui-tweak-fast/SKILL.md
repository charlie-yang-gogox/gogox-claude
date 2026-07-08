---
name: ui-tweak-fast
description: "Self-contained, portable variant of /ui-tweak for UI-Designer-safe codebase edits. ONE file runs the whole flow (parse → worktree → triage → apply → iterate → preview → audit → ship) with every step's instructions inlined — an agent never has to read ff/start/detect/apply/preview/audit separately. Given a UI change as free text, a Linear/Jira ticket (ID/URL), and/or a Figma link, it edits ONLY the UI (visual values, layout, structure), confirms it compiles, and is blocked from touching logic by a deferred two-judge audit (kept model-agnostic so it runs under any host's own model). Three things make it 'fast': (1) one read instead of six; (2) device previews build+run from a PERSISTENT per-repo clone (changed files are synced in from the worktree) so .dart_tool/build caches survive across tickets instead of recompiling cold in every fresh worktree, and after the first preview a 'more changes' round HOT-RESTARTS the still-running app (~seconds) instead of cold-rebuilding; (3) the designer can pick a 'Show me on iPhone + Android' card answer to preview on an iOS simulator AND an Android emulator at once (no flag to type). Portable across hosts (no harness-specific tools or pinned models). Use when a designer says 'make the order-page button 5dp bigger', 'change this color', passes a ticket like <ticket-id> describing a UI tweak, or gives a Figma frame to match — and wants the fast, single-file, portable flow."
---

<!--
  RULE: all skill PROSE — including every designer-facing CARD — is written in English: no Chinese /
  CJK / non-English text. The card emoji (📍 📦 👉 ⚠) and typographic symbols (— … → ≤ Δ) are fine, as
  in /ui-tweak. But NEVER put a non-ASCII char inside a runnable shell line (e.g. a literal "…" in a
  command is a glob/parse error) — use a real variable instead.

  This is a SELF-CONTAINED peer of /ui-tweak. It deliberately INLINES the per-step logic that
  /ui-tweak splits across commands/design/ui-tweak/{ff,start,apply,preview,audit}.md, so an agent
  reads ONE file. It is a NEW skill — /ui-tweak and its 6 files are untouched, and this skill is NOT
  wired into /route / /ggx-work / /ggx-dispatcher (those keep using /ui-tweak:ff for the `design bug`
  lane). Run it directly: `/ui-tweak-fast …`.
-->

# `/ui-tweak-fast`

> **One-line summary**: a UI Designer describes a UI change in plain language; this skill edits only
> the UI (visual / layout / structure), confirms it still compiles, is blocked from touching logic,
> and guides the designer with plain-language cards — they never need to know git / commit / PR /
> build exist. It is the **fast, single-file, portable** cousin of `/ui-tweak`.

```
/ui-tweak-fast <source> [figma-url] [--auto]
```

`--auto` → unattended, no cards. **There is no device flag** — when the designer asks to see the
change, the "show me" card offers the device choice (one phone, or **iOS + Android at once**) as an
answer; designers never type a flag (Step 5).

## What these instructions mean operationally — read once, applies everywhere below

This file is written **harness-agnostic**: it never names a host or a host-specific tool. Map each phrase
below to whatever your host environment provides.

| This skill says | What to do (any host) |
|---|---|
| "spawn a subagent / a judge" | Use your host's subagent mechanism with a **generic** read-only type and **no model override** (inherits the session model). Never spawn a preset/named subagent type whose definition pins a specific model (see rule 1). |
| "ask the designer (a card)" | Use your host's question/card UI; if it has none, ask the designer in plain chat. |
| worktree / preview-clone paths | If your host moves the session into the worktree on `cd`, later relative paths work. If it does NOT, resolve **absolute** paths and pass them to every Read/Write/Edit and `working_directory`, and after creating the worktree tell the designer its path (best UX: open that folder). |
| fan-out / parallel orchestration | **never used here** — this skill has no fan-out. |
| an MCP tool (Linear / Notion / Figma) | Use your host's MCP for that service (resolve the ticket MCP via `_ticket-lib.md`). If a referenced MCP is **absent**, take the step's documented degrade (ticket fetch → ask in chat; Figma → DEGRADED; Notion login → fail-silent login wall) — never hard-error. |
| reasoning model for judges | **never pin a model** — decorrelation is by *lens*, not tier (see Step 7). |

**Three execution rules that make this file portable:**

1. **No model is ever pinned.** Everywhere a model would be chosen, do not choose one — let the host
   use its default. The two judges are decorrelated by their **prompts/lenses** (UI-completeness vs
   program-behavior). This is a deliberate trade: it is portable but **weaker than tier-decorrelation** —
   under one host model, two prompts can share blind spots that two different model tiers would not. If
   your host lets you choose models you MAY *optionally* run the two judges on different models for extra
   decorrelation (opt-in only — never required). **Do NOT spawn a preset/named subagent type whose
   definition pins a specific model** — that re-introduces a host-specific model pin this skill promises
   to avoid. Spawn **generic** read-only subagents and paste the lens prompts inline (Step 7c / 4b already
   contain them).
2. **Shell state does NOT persist between separate command/bash invocations** (true on every host). So
   **every bash block re-derives the vars it needs at its top** — do not assume
   `$WT` / `$BASE` / `$PLATFORM` / `$FLUTTER_BIN` / `$FRESH_CLONE` survived from an earlier block. The
   canonical re-derivations (run them at the start of any block that needs them):
   ```bash
   [ -n "${ZSH_VERSION:-}" ] && setopt sh_word_split   # see rule 3 — MUST come before any unquoted $FLUTTER_BIN use
   WT=$(git rev-parse --show-toplevel)                                   # session cwd is inside the worktree
   PLATFORM=$(sed -n 's/^platform:[[:space:]]*//p' "$WT/.gogox-claude.yaml" 2>/dev/null | head -1)
   [ -f "$WT/.dev/ui-tweak-fast/base_ref" ]   && BASE=$(cat "$WT/.dev/ui-tweak-fast/base_ref")
   [ -f "$WT/.dev/ui-tweak-fast/flutter-bin" ] && FLUTTER_BIN=$(cat "$WT/.dev/ui-tweak-fast/flutter-bin")
   ```
3. **Word-splitting: the skill invokes flutter UNQUOTED on purpose.** `$FLUTTER_BIN` may be **two tokens**
   (`fvm flutter`) and `$FLAVOR_ARG` is `--flavor stag`, so callers write `$FLUTTER_BIN run …` /
   `$FLAVOR_ARG` **unquoted**, relying on `sh` word-splitting. **zsh does NOT word-split unquoted
   expansions** — under zsh `$FLUTTER_BIN run` tries to exec a binary literally named `fvm flutter` and
   dies instantly. So **every bash block that runs flutter MUST begin with**
   `[ -n "${ZSH_VERSION:-}" ] && setopt sh_word_split` (a no-op on bash/sh; makes zsh split like sh). It
   is already baked into the canonical re-derivation above and re-stated at the top of each flutter block.
   Paths stay quoted (`"$WT"`), so enabling sh-splitting is safe — only the intentionally-unquoted command
   strings split.

## The guarantee (read this first)

Accepts **any UI-form change** and is built so a designer **cannot ship broken logic**. There is **no
edit-time hook** — a cheap **upfront triage** catches the obvious mis-routes, and hard enforcement is
deferred to two checks at different times:

- **Upfront triage (Step 3).** Before any edit, a read-only visual-vs-logic check reads the target
  widget; an *obvious* needs-logic ask (a tap target / navigation / state change dressed as a design
  bug) is stopped here with **nothing changed**, so it never burns an apply + build + repair cycle. It
  leans pure-visual when ambiguous — an early catch, **not** the enforcement; the panel below is.
- **Build (Phase 1, Step 6).** When the designer asks to see it, the change is built; anything that
  won't compile is reverted and auto-repaired (max 3) before they ever see an "ask an engineer" card.
- **Model-agnostic 2-judge panel (Phase 2, Step 7).** When the designer ships, a deterministic
  structural pre-pass runs, then **two independent read-only judges** audit the final cumulative diff
  on **different lenses** — one asks "does this change any program behavior?", the other asks "is every
  change purely visual/layout/structure and does it cover all the targets?". **Both must return
  CLEAR.** Any logic/behavior change — a non-UI file touched, or logic edited inside a UI file —
  reverts the whole run **before anything is committed or a PR is opened**.

Iteration stays free (apply only, no build). The terminal is always a **draft PR** — never
draft→ready, never merge.

## Designer-facing language rules (apply to EVERY card)

Use the right column, never the left:

| BANNED (dev jargon) | Plain wording |
|---|---|
| branch / commit / merge | (don't surface; handled internally) |
| build / compile | confirm it still works |
| emulator / simulator / run | see it on a phone screen |
| marker / base_ref / SHA / diff / worktree | (don't surface) |
| ticket | work-item number |
| revert | take the change back / put it back |
| judge / logic / behavior | check / the part about how the program runs |

"**PR**" / "**draft PR**" is allowed — designers know what a PR is; say it directly. Translate a
judge's verdict into ONE plain sentence; never paste raw judge text to a designer. The `📦` narrative
carries only `component + visual property + old→new` — never file paths, SHAs, or raw build errors.

---

# The flow

State lives in **filesystem markers** under the worktree's `.dev/ui-tweak-fast/` (no `state.json`).
Re-running with no new argument **resumes** from the markers; re-running with a new requirement is a
**correction** (see "Correction & repair loops"). Markers used:

```
.dev/ui-tweak-fast/worktree-ready     # Step 1 done (idempotency; never re-split)
.dev/ui-tweak-fast/ticket.json        # read-only ticket snapshot (no re-fetch)
.dev/ui-tweak-fast/comments.json      # read-only comment-THREAD snapshot (union'd into the requirement, no re-fetch)
.dev/ui-tweak-fast/flutter-bin        # resolved flutter binary (flutter platform only)
.dev/ui-tweak-fast/flavor             # line1=flavor, line2=detected|missing
.dev/ui-tweak-fast/triage-pass        # Step 3 verdict: pure-visual (widget + rationale) — resume/correction skips re-triage
.dev/ui-tweak-fast/base_ref           # pre-edit SHA (cumulative-diff baseline)
.dev/ui-tweak-fast/figma-context.md   # structured target checklist (grounding receipt)
.dev/ui-tweak-fast/.not-deliverable   # written iff any target is NOT-FOUND (quality bar)
.dev/ui-tweak-fast/preview-requested  # designer picked "show me" / "show me on both" (route to Step 6)
.dev/ui-tweak-fast/dual-device        # Step-5 "Show me on iPhone + Android" choice (preview on both)
.dev/ui-tweak-fast/audit-files        # frozen base→HEAD name list that bounds what Step 7 judges
.dev/ui-tweak-fast/build-pass         # "Status: PASS" after a clean build (Step 6)
.dev/ui-tweak-fast/preview-shown      # preview launched + (maybe) captured → show C1 looks-good
.dev/ui-tweak-fast/demo-files         # captured/handed-in screenshot+recording paths
.dev/ui-tweak-fast/run-<tag>.pid      # live `flutter run` pid (tag=single|ios|android) — enables hot-restart re-preview
.dev/ui-tweak-fast/run-<tag>.fifo     # control FIFO: write `R` to hot-restart the live run
.dev/ui-tweak-fast/run-<tag>.holder   # pid of the FIFO-open holder (kept so the run never sees stdin EOF)
.dev/ui-tweak-fast/run-<tag>.dir      # the clone dir the live run builds from (where corrections are synced)
.dev/ui-tweak-fast/run-<tag>.log      # that run's combined output (polled for "Restarted application" / compile errors)
.dev/ui-tweak-fast/deliver            # designer picked "Ship it" (Phase 2)
.dev/ui-tweak-fast/direct-ship        # shipped without a device preview (build-only gate)
.dev/ui-tweak-fast/repair-context     # a build/audit failure for the agent to fix UI-only
.dev/ui-tweak-fast/repair-count       # repair attempts (cap 3 → engineer card)
.dev/ui-verify-pass.md                # UI-completeness-lens judge verdict (Status: CLEAR|BLOCKED)
.dev/dev-reviewer-pass.md             # behavior-lens judge verdict (Status: CLEAR|BLOCKED)
```

`--auto` (unattended): render no cards; after the single apply, auto-take the direct-ship path
(build-only gate → audit → commit → draft PR). On any blocked/exhausted state, print ONE deterministic
stderr line and exit non-zero. (This skill is not wired into the dispatcher; `--auto` exists for
direct headless invocation and parity.)

## Step 0 — first contact / parse

- **Empty / `help` / `?` / garbled `<source>`** → print card **C0** and STOP (do not edit):
  ```
  📍 Hi! I can change how the App looks — sizes, colors, spacing, layout.
  📦 Just describe it in plain words. It helps to say "which screen + what to change".
  👉 e.g.  /ui-tweak-fast "make the order-page primary button a bit taller"  <ticket-id>
           /ui-tweak-fast <ticket-id>   (a Figma link can go at the end)
  ```
- **Otherwise** → strip the `--auto` flag from `<source>` (⇒ unattended), then continue to Step 1.
  (There is no device flag — dual-device is a Step-5 card choice, recorded as the `dual-device` marker.)

## Step 1 — workspace: resolve profile, fetch ticket (read-only), split the worktree

A work-item number is **required** (every change is tracked under one and handed to an engineer that
way) — exactly like `/dev:ff` / `/port:ff`. There is **no in-place edit path**.

```bash
echo "{\"skill\":\"ui-tweak-fast\",\"ts\":\"$(date -u +%FT%TZ)\"}" >> ~/.gogox-claude-usage.jsonl 2>/dev/null || true
REPO_ROOT=$(git rev-parse --show-toplevel)
[ -f "$REPO_ROOT/.dev/ui-tweak-fast/worktree-ready" ] && SPLIT_DONE=1 || SPLIT_DONE=0
```

- `SPLIT_DONE=1` (resume / correction — already inside the worktree) → **infer the stage from markers**
  (do NOT blindly re-apply). With a NEW requirement in `<source>` this is a **correction** (go to the
  Correction loop). With no new argument, jump by the first marker that matches, in priority order:
  ```bash
  WT=$(git rev-parse --show-toplevel); M="$WT/.dev/ui-tweak-fast"
  if   [ -f "$M/repair-context" ];                                       then GOTO="Step 4 (repair mode)"
  elif [ -f "$M/deliver" ] && grep -q '^Status: CLEAR' "$WT/.dev/ui-verify-pass.md" 2>/dev/null; then GOTO="Step 8 (commit/PR — audit already CLEAR)"
  elif [ -f "$M/deliver" ];                                              then GOTO="Step 7 (audit)"
  elif [ -f "$M/preview-shown" ];                                        then GOTO="Step 6 tail — render C1 (looks-good)"
  elif [ -f "$M/preview-requested" ];                                    then GOTO="Step 6 (build the preview)"
  elif [ -f "$M/base_ref" ];                                             then GOTO="Step 5 — render C1 (show-me)"
  elif [ -f "$M/triage-pass" ];                                          then GOTO="Step 4 (apply — triage already pure-visual)"
  else                                                                         GOTO="Step 3 (triage)"; fi
  ```
  Still run Step 2 (resolve tooling) before any step that needs `$FLUTTER_BIN`. Then go to `$GOTO`.
  (A needs-logic STOP writes no `triage-pass` and no `base_ref`, so a bare resume re-enters Step 3 and
  re-triages — idempotent and cheap.)
- `SPLIT_DONE=0` → parse a work-item id from `<source>` (`[A-Z]+-[0-9]+`, or a
  `linear.app/<org>/issue/<ID>/...` URL; first match).
  - **No id (pure free text)**:
    - interactive → render **card C-WT** (header `Work-item no.`):
      > Before I start, what's the work-item number for this (like <ticket-id>)? I use it to keep your
      > change in its own space and to hand it over later. Pick **Other** and paste the number to begin.

      A number (via Other) → continue with it. "I don't have one yet" → STOP, change nothing, and say:
      *"Every change needs a work-item number so it can be tracked and handed to an engineer. Create one
      (or ask your PM/engineer), then run `/ui-tweak-fast` again with that number."*
    - `--auto` → print `FAIL: /ui-tweak-fast --auto needs a work-item id in <source>.` to stderr, STOP.

**Resolve the platform profile** (note the friendly repo/screen name for cards). Bind it to a shell
var — `PLATFORM=$(sed -n 's/^platform:[[:space:]]*//p' "$REPO_ROOT/.gogox-claude.yaml" | head -1)`;
fallback to the profile registry under your install root
(`~/.claude/commands/profiles/registry/<basename>.yaml` or `~/.cursor/commands/profiles/registry/<basename>.yaml`).
The platform decides build/preview commands:
- `flutter` → `ui_build_cmd: flutter build apk --debug --flavor stag` (or iOS),
  `ui_preview_cmd: flutter run -d {device} --debug --flavor stag` (covers Android emulators **and** iOS
  simulators). A repo override in `<repo>/.gogox-claude.yaml` wins.
- `android` → `ui_build_cmd` is gradlew (build-only; no flutter tooling).
- `ios` → `ui_build_cmd` is xcodebuild (build-only).

**Fetch the ticket, read-only** (never change status/assignee, never comment) via your host's Linear MCP
`get_issue` (or ask in chat if absent — read-only either way) or Jira per `_ticket-lib.md`; derive the
requirement from
title + description + comments. **Capture the fetched issue JSON into `TICKET_JSON`** (it is written to
`ticket.json` below). Free-text runs skip the fetch and write `TICKET_JSON='{}'`.

**Also fetch the comment THREAD, not just the description.** The `get_issue` snapshot carries
the description + attachments but **NOT** the Linear comment thread — so a follow-up comment that
refines or reverses the spec is invisible to a description-only read, and an earned no-op grounded off
it then validates against a *stale* spec. Fetch the thread read-only via your host's Linear MCP
`list_comments` ordered by `createdAt` (or Jira per `_ticket-lib.md` — Jira embeds comments in the
issue snapshot, so derive them from `TICKET_JSON`). **Capture it into `COMMENTS_JSON`** (cached to
`comments.json` below; normalize to `{"comments":[…]}`). This is **fail-soft and never blocks the
split**: if the Linear MCP is absent or the fetch fails, leave `COMMENTS_JSON` unset — the block below
caches an empty array + a `note`, and Step 3a re-fetches/degrades. Free-text runs skip it.

**Create + enter the worktree** (mirrors `/add-worktree`; off latest trunk). Inlined so this file is
self-contained:

```bash
source "$HOME/.claude/lib/dev-mode.sh" 2>/dev/null || source "$HOME/.cursor/lib/dev-mode.sh" 2>/dev/null || true
TRUNK_DIR="$REPO_ROOT"                                  # the main checkout we are splitting from
TICKET_ID="<parsed id>"
TYPE=fix                                                # design bug → fix; else feat
DEFAULT_BRANCH=$( (command -v default_branch >/dev/null 2>&1 && default_branch) \
  || git -C "$TRUNK_DIR" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@' \
  || echo main )
git -C "$TRUNK_DIR" fetch --quiet origin "$DEFAULT_BRANCH" 2>/dev/null || true
WT="$(cd "$TRUNK_DIR/.." && pwd)/$TICKET_ID"
BRANCH="$TYPE/$TICKET_ID"
if ! git -C "$TRUNK_DIR" worktree list --porcelain | grep -qxF "worktree $WT"; then
  git -C "$TRUNK_DIR" worktree add -b "$BRANCH" "$WT" "origin/$DEFAULT_BRANCH" \
    || git -C "$TRUNK_DIR" worktree add "$WT" "$BRANCH"   # branch already exists → check it out
fi
cd "$WT"   # If your host moves the session into the worktree on `cd`, later relative paths work. If it
           # does NOT, treat "$WT" as the absolute root for ALL later Read/Write/Edit + git/shell working_directory.
mkdir -p "$WT/.dev/ui-tweak-fast"
printf '%s\n' "${TICKET_JSON:-{\}}" > "$WT/.dev/ui-tweak-fast/ticket.json"  # read-only snapshot (skip refetch)
# comments.json — the comment THREAD. Fail-soft (mirrors the ticket fetch): on any miss, cache
# an empty array + a note so Step 3a knows to re-fetch — and NEVER block the split. (Jira: COMMENTS_JSON
# was normalized from TICKET_JSON above; Linear: from the host list_comments call.)
printf '%s\n' "${COMMENTS_JSON:-{\"comments\":[],\"note\":\"comment fetch failed at split — apply may re-fetch\"\}}" \
  > "$WT/.dev/ui-tweak-fast/comments.json"
printf 'ticket=%s\n' "$TICKET_ID" > "$WT/.dev/ui-tweak-fast/worktree-ready"
```
(Deps are installed in Step 2 once `$FLUTTER_BIN` is resolved — not here, since the binary is not known
yet.)

Do **not** call `/_ticket-init` — ticket reads stay read-only (the PR-open transition in Step 8 is the
only lifecycle write).

## Step 2 — resolve build tooling once (flutter only; skip on android/ios)

Resolve the flutter binary (fvm-aware) and the flavor ONCE so no later step rediscovers them. Define a
reusable resolver — it is called for the **worktree** here and for the **preview clone** in Step 6.
(Re-derive `WT`/`PLATFORM` at the top of this block if it is a separate invocation — see the Tooling
map's execution rule 2.)

```bash
[ -n "${ZSH_VERSION:-}" ] && setopt sh_word_split   # rule 3: zsh must split unquoted $FLUTTER_BIN like sh
# resolve_flutter <dir> → echoes a working flutter invocation for that dir (handles fvm + bare).
# Probe each candidate with `--version`; never guess from config alone (machines vary: some have only
# fvm; some only bare flutter). Cache the winning KIND per machine so the next ticket skips the probe.
# CAVEAT: the result may be two tokens ("fvm flutter"), so callers use it UNQUOTED — which (a) assumes the
# resolved path has no spaces (true unless $HOME itself contains a space; that rare case falls back to bare
# `flutter`), and (b) REQUIRES sh word-splitting: under zsh you MUST `setopt sh_word_split` first (rule 3)
# or `$FLUTTER_BIN run` execs a binary literally named "fvm flutter". Do not "$FLUTTER_BIN"-quote it.
resolve_flutter() {
  d="$1"; CACHE_DIR="$HOME/.cache/ui-tweak-fast/$(basename "$(dirname "$(git -C "$d" rev-parse --git-common-dir)")")"
  probe() { eval "$1 --version" >/dev/null 2>&1; }
  FVM_BIN=$(command -v fvm 2>/dev/null || true); [ -z "$FVM_BIN" ] && [ -x "$HOME/.pub-cache/bin/fvm" ] && FVM_BIN="$HOME/.pub-cache/bin/fvm"
  PINNED=0; { [ -f "$d/.fvmrc" ] || [ -f "$d/.fvm/fvm_config.json" ]; } && PINNED=1
  SDK="$d/.fvm/flutter_sdk/bin/flutter"
  if   [ "$PINNED" = 1 ] && [ -x "$SDK" ] && probe "$SDK"; then printf '%s' "$SDK"
  elif [ "$PINNED" = 1 ] && [ -n "$FVM_BIN" ] && probe "$FVM_BIN flutter"; then printf '%s flutter' "$FVM_BIN"
  elif probe flutter; then printf 'flutter'
  elif [ -n "$FVM_BIN" ] && probe "$FVM_BIN flutter"; then printf '%s flutter' "$FVM_BIN"
  else return 1; fi
}
if [ "$PLATFORM" = flutter ]; then
  if [ -f "$WT/.dev/ui-tweak-fast/flutter-bin" ]; then FLUTTER_BIN=$(cat "$WT/.dev/ui-tweak-fast/flutter-bin")
  else FLUTTER_BIN=$(resolve_flutter "$WT") || { echo "FAIL: no working flutter found (tried fvm + bare)." >&2; exit 1; }
       printf '%s\n' "$FLUTTER_BIN" > "$WT/.dev/ui-tweak-fast/flutter-bin"; fi
  ( cd "$WT" && $FLUTTER_BIN pub get >/dev/null 2>&1 || true )   # deps for the worktree (build-only mode)
  # flavor: <repo>/.gogox-claude.yaml `flavor:` > platform default `stag`; PROBE it exists, else strip later.
  FLAVOR=$(sed -n 's/^flavor:[[:space:]]*//p' "$WT/.gogox-claude.yaml" 2>/dev/null | head -1); FLAVOR=${FLAVOR:-stag}
  FLAVOR_DETECTED=missing
  G=""; for g in "$WT/android/app/build.gradle" "$WT/android/app/build.gradle.kts"; do [ -f "$g" ] && G="$g" && break; done
  if [ -n "$G" ] && grep -q productFlavors "$G" 2>/dev/null && { grep -qE "(^|[^A-Za-z0-9_])${FLAVOR}[[:space:]]*\{" "$G" || grep -qE "create\([\"']${FLAVOR}[\"']\)" "$G"; }; then FLAVOR_DETECTED=detected; fi
  if [ "$FLAVOR_DETECTED" = missing ] && ls "$WT"/ios/*.xcodeproj/xcshareddata/xcschemes/"$FLAVOR".xcscheme >/dev/null 2>&1; then FLAVOR_DETECTED=detected; fi
  printf '%s\n%s\n' "$FLAVOR" "$FLAVOR_DETECTED" > "$WT/.dev/ui-tweak-fast/flavor"
  # iOS simulator pre-warm (macOS) — NON-BLOCKING, fail-silent: overlaps boot with apply.
  if [ "$(uname)" = Darwin ] && ! xcrun simctl list devices booted 2>/dev/null | grep -q '(Booted)'; then
    udid=$(xcrun simctl list devices available 2>/dev/null | grep iPhone | grep -m1 -oiE '[0-9a-f-]{36}')
    [ -n "$udid" ] && ( xcrun simctl boot "$udid" >/dev/null 2>&1 & )
  fi
fi
```

## Step 3 — triage: pure-visual vs needs-logic (read-only, BEFORE any edit)

> The upfront, read-only visual-vs-logic gate (mirrors `/ui-tweak:detect`). It runs BEFORE any
> edit, grounding, or build, so a misrouted `design bug` that really needs logic/behaviour changes is
> caught **here** — not after a whole apply + build + 3× repair cycle ends at the late Step-7 dual-judge
> BLOCK. Middle tier of a 3-tier cascade, cheap → expensive: an upstream text-only ticket gate → **this**
> per-ticket read-of-one-widget → the Step-7 dual-judge post-apply backstop (unchanged). It NEVER edits
> code and NEVER reclassifies the work item itself (`design bug → bug` stays human-owned).
>
> Re-derive `WT`/`PLATFORM` at the top if this is a separate invocation (Tooling-map rule 2). If
> `.dev/ui-tweak-fast/triage-pass` already exists (a prior round triaged pure-visual) or
> `.dev/ui-tweak-fast/repair-context` exists (repair mode), this step is already done — go to Step 4.

**3a — derive the requirement (union title + description + the FULL comment thread).** Free
text → use it verbatim. Ticket → derive from the **union** of the cached `ticket.json` title +
description **and the full comment thread** in `comments.json`. When a later comment refines or
contradicts the description, **the most-recent comment is authoritative** (it is the live intent; the
description is the stale baseline). A requirement can be **state-dependent** (e.g. a *completed* state
needs the opposite section order from an *en-route* state) — keep **every** state's requirement
distinct; never collapse them into one.
- **Read `comments.json` (don't trust the comment-less `ticket.json` alone).** If it is absent, or its
  `comments` array is empty with a `note` (a fetch failure at split time), **re-fetch the thread
  yourself, read-only** — via your host's Linear MCP `list_comments` ordered by `createdAt`, or from
  `ticket.json` for Jira (per `_ticket-lib.md`) — and refresh the cache.
- **If comments still cannot be read** (MCP absent / re-fetch failed) → proceed on the **description
  alone** and stamp the grounding provenance `⚠ comments-unavailable` on the receipt
  (`figma-context.md`), so the no-op verdict in 4d and the audit downstream are not trusted as
  comment-aware.

**3b — locate + read the primary target widget (read-only, first widget only).** From the requirement
(+ any Figma node refs, incl. a trailing `[figma-url]`), grep the target screen/component and resolve
the single primary target widget `<file>[:line]` — the same locate Step 4c does, but read-only and
stopping at the FIRST widget rather than enumerating every `Ti`. **Read** it (and the immediately-
relevant collaborators it wires up — its build method, the state/controller it reads, the
gesture/callback it fires) — enough to judge whether satisfying the request needs behaviour, not a full
investigation. If no plausible code site is found, do **not** verdict here — let Step 4c's locate gate
handle the not-found case (it renders card **C6**); record nothing and proceed to Step 4.

**3c — classify, then stop or proceed.** Decide whether the **requested change** (not the widget in
general) can be satisfied by **look-and-feel alone**:
- **pure-visual** — token / colour / typography / spacing / sizing / layout / structure. A value, a
  style, a constraint, a widget arrangement changes; **nothing the screen DOES changes**.
- **needs-logic** — satisfiable **only** by changing gesture / state / control-flow / data / interaction
  wiring: a different tap target or where a tap navigates, a new/altered callback, a state transition,
  conditional rendering, a changed data binding. (Motivating case: a routing bug dressed as a design bug
  — e.g. a "reuse this order" control that silently redirects to the wrong flow.)

**Bias: when genuinely ambiguous, LEAN pure-visual and proceed** — the Step-7 dual-judge panel is the
backstop and reverts the whole run on any logic finding, so a false pure-visual is caught later, while a
false needs-logic wrongly blocks a real design bug from the cheap path. **Only** emit needs-logic when
the logic dependency is CLEAR from the requirement + the widget you just read.

- **pure-visual** → record the verdict (so a resume/correction does not re-triage) and continue to
  **Step 4 (apply)**, which reuses the widget located in 3b:
  ```bash
  WT=$(git rev-parse --show-toplevel); mkdir -p "$WT/.dev/ui-tweak-fast"
  { echo "Verdict: pure-visual"; echo "Widget: <relative/path>[:line]"; echo "Rationale: <one line>"; } \
    > "$WT/.dev/ui-tweak-fast/triage-pass"
  ```
- **needs-logic** → **STOP before any edit** — nothing has been changed (no worktree edit, no
  `base_ref`, no `triage-pass`), which is the whole point of triaging upfront. Render card **C6**
  (header `What next`):
  > I can't find that place in "<screen>", or it's really about the part of how the program runs (like
  > what a tap does or which screen it opens) — not just the look. Nothing has changed. I'd suggest
  > re-filing this as a normal bug so an engineer can pick it up. Or describe it a different way and I'll
  > take another look? (Or pick **Other**.)

  The recommended choice is to re-file it for an engineer; "Describe it differently" / **Other** re-runs
  **this triage** on the new wording (it may now read pure-visual). The recommendation is **human-owned**
  — this skill NEVER changes the work-item's type itself. Under `--auto`, print
  `UI-TWEAK-FAST NEEDS-ENGINEER: <one-line plain reason> — needs an engineer; recommend re-filing as a normal bug; no change made.`
  to stderr and exit non-zero (no label is flipped). **C6 is defined here and reused by Step 4c** (its
  forbidden-file / not-found stop).

## Step 4 — apply: produce ONE UI diff (no build, no audit)

> If `.dev/ui-tweak-fast/repair-context` exists, this is **repair mode** — see "Correction & repair
> loops" below; fix the edit UI-only and skip the parse/ground re-do.

**4a — reuse the requirement + the located widget from Step 3.** The requirement (Step 3a) and the
primary target widget (Step 3b) were already resolved by the upfront triage — reuse them; do not
re-derive or re-locate from scratch. On a fresh-session **resume** that landed here via the
`triage-pass` marker (Step 3 ran in an earlier session), re-derive the requirement per Step 3a (re-read
`ticket.json` / `comments.json`, applying the same most-recent-comment-authoritative + state-distinct
rules) before grounding.

**4b — ground into a structured target checklist** (`figma-context.md`). Figma is **optional**:
- Figma URL present (trailing `[figma-url]` wins, else one extracted from the ticket) → fetch the design
  context with a **generic** read-only subagent (no model override; do NOT spawn a preset/named subagent
  type that pins a specific model or a host-specific Figma MCP) and enumerate every visual property
  the frame pins, one codeable row each. If the Figma MCP is unavailable in your host → DEGRADED
  fallback below (never blocks). Persist:
  ```
  Fetched: <ISO> <node-ids>
  ## Target values (checklist)
  - [ ] T1  property=button.height  target=48dp     node=123:45
  - [ ] T2  property=button.corner  target=8dp      node=123:45
  - [ ] T3  property=button.bg      target=#0A7CFF  node=123:46
  ```
- No Figma → derive rows from the requirement; first line `Fetched: SKIPPED — derived from requirement`.
- Figma fetch failed → first line `Fetched: DEGRADED — <err>`, fall back to requirement-derived rows,
  warn, and **continue** (never blocks).
- **Ticket reference image is the spec.** If `ticket.json` carries an attachment or the description has
  an inline `![](https://uploads.linear.app/...)`, `curl -fsSL` it to `.dev/ui-tweak-fast/ref-<n>.png`,
  **Read it as an image**, and build/refine the checklist from what the PICTURE shows (which regions
  change and which do NOT) — a screenshot often pins zoned/partial scope a sentence loses. Figma pins
  exact values; the screenshot pins scope. Stamp `ref-image: <file>` on the receipt.

**4c — locate + map (+ shared-token blast radius).** For each row `Ti`: grep the target
screen/component, record `{Ti, file, current, target}`, classify the file UI-eligible vs forbidden.
- Shared-token check: grep the reference count of each edited token/resource key across the UI surface;
  `>1` → mark the row `SHARED (N refs)` and list the other affected screens (cheapest over-scope
  defense — one `dimens` entry restyling five screens).
- A `Ti` with no code site → `NOT-FOUND`.
- A value living **only** in a forbidden file (ViewModel / Repository / build config / a referenced
  `@+id`/function name) → this is **not** a pure-UI change → STOP and render card **C6** (defined in
  Step 3 — the "can't find that place / it's really about how the program runs" card). Remove any
  `base_ref` you wrote. Under `--auto`, print the question to stderr and stop. (A *clear needs-logic*
  verdict is already caught upfront in Step 3 before any worktree edit; this 4c stop is the locate-time
  forbidden-file / not-found backstop for what slips through to apply.)

**4d — coverage gate (bidirectional) + quality bar.**
- Forward: every `Ti` resolves to a planned edit, `ALREADY-MATCHES` (with the matched site), or
  `NOT-FOUND`. No `Ti` silently dropped.
- Reverse: every planned edit cites a backing `Ti` (free-text runs cite the requirement). An edit with
  no backing target is flagged.
- If **any `Ti` is NOT-FOUND** → write `.dev/ui-tweak-fast/.not-deliverable` listing the unmet targets;
  else ensure that file does not exist. (`Fetched: SKIPPED/DEGRADED` does NOT trip the bar.)
- **Earned-no-op guard.** When **every** `Ti` resolves to `ALREADY-MATCHES` (no planned edit —
  an earned no-op), that no-op is trustworthy ONLY if the checklist was grounded against the **current**
  spec, i.e. description **+ the full comment thread** (3a) AND every state named across
  description+comments is satisfied. Refuse the no-op when either fails:
  - grounding provenance is `⚠ comments-unavailable` → the spec may have evolved in a comment the run
    never saw; or
  - a comment introduced a **state** the reference image/description did not cover (an `ALREADY-MATCHES`
    against one state, e.g. en-route, says nothing about a later-comment state, e.g. completed).
  On a refused no-op, do **not** earn it — change nothing, and surface it for a human (render card **C6**
  — defined in Step 3; under `--auto` print the reason to stderr and stop).
- **Present the plan** (table: `Ti | property | target | code site | current | new | shared? | status`).
  Default mode takes ONE plan confirmation — **except** skip it when exactly ONE target resolved AND its
  source is the ticket (not `⚠ estimated`): the next card's Other field is already the correction escape,
  so a separate confirm is one prompt too many. Keep the confirm for ≥2 targets, any `⚠ estimated`, or
  free-text. `--auto` skips the interactive confirm but records the plan.

**4e — record `base_ref` (clean-trunk-anchored) — guard against a poisoned baseline.** (Full rationale —
the cross-worktree contamination case — is in `commands/design/ui-tweak/apply.md` Step 5.)

```bash
WT=$(git rev-parse --show-toplevel)                                                  # re-derive (rule 2)
PLATFORM=$(sed -n 's/^platform:[[:space:]]*//p' "$WT/.gogox-claude.yaml" 2>/dev/null | head -1)
DEFAULT_BRANCH=$(git -C "$WT" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@'); DEFAULT_BRANCH=${DEFAULT_BRANCH:-main}
if [ "$PLATFORM" = flutter ]; then git -C "$WT" checkout -- pubspec.lock 2>/dev/null || true; fi  # drop pub-get noise
if [ ! -f "$WT/.dev/ui-tweak-fast/base_ref" ]; then
  EXPECTED_TIP=$(git -C "$WT" rev-parse "origin/$DEFAULT_BRANCH" 2>/dev/null || true)
  ACTUAL_HEAD=$(git -C "$WT" rev-parse HEAD)
  if [ -n "$EXPECTED_TIP" ] && [ "$ACTUAL_HEAD" != "$EXPECTED_TIP" ]; then
    echo "FAIL: refusing to record base_ref — HEAD ($ACTUAL_HEAD) != fresh origin/$DEFAULT_BRANCH ($EXPECTED_TIP)." >&2
    echo "A no-op/diff computed against a non-trunk base is invalid. Recreate the worktree off clean trunk and re-run." >&2
    exit 1
  fi
  git -C "$WT" rev-parse HEAD > "$WT/.dev/ui-tweak-fast/base_ref"
fi
```
(A correction/repair re-run keeps the original `base_ref` — never overwrite it, so preview/audit always
diff the **cumulative** change.)

**4f — edit, value/UI-only.** Make the change with `Edit`/`Write` (inspect with `Read`/`Grep`/`Glob`).
Keep the diff to **pure-visual values, layout, and structure — no logic, no build config, no source
rewrites**. There is no edit-time hook; the Step-7 panel reverts the whole run on any logic finding
(max 3 agent repairs). Prefer the narrowest change that satisfies the targets. Never route an edit
through Bash.

Then STOP and render the iteration card (Step 5). (`--auto`: skip the card — go to "Auto path" below.)

## Step 5 — iteration card C1 (show-me)

Render **C1 (show-me)** (header `Next step`). The build has NOT run — do not claim
it compiles:
> I made the change (look-and-feel values only).
> What changed: order-page primary button — height 44→48dp, corner 4→8dp.
> [source: Figma-confirmed / from the work-item / ⚠ estimated]
> Want to see it on a phone, ship it as-is (if you've already seen it), or make more changes first?
> (Or pick **Other** and tell me what to change.)

- options (this is the place the device choice is offered — designers never type a flag):
  - **`I'm done — show me`** *(recommended)* — "I build it onto a phone, go to that screen myself, and
    show you a screenshot + short recording." (single device — the fast cascade)
  - **`Show me on iPhone + Android`** — "Same, but on an iPhone **and** an Android phone at the same
    time, so you can check both — takes a little longer." (flutter only; falls back to one if only one
    kind is available)
  - **`It already looks right — ship it`** — "You've already seen it — I skip the phone preview,
    confirm it still works, run the full check, and open a draft PR."
  - **`I want more changes`** — "Tell me what to adjust."
- A card allows up to 4 options; on `android`/`ios` native profiles (no `flutter run`) OMIT the
  `Show me on iPhone + Android` option (there is no dual-platform preview there).
- When `.not-deliverable` exists, OMIT all three preview/ship options (you can neither preview nor ship
  a partial), leaving only `I want more changes`, and append: *"⚠ N spot(s) weren't changed — I couldn't
  find them; adjust the wording?"*
- routing (all writes go under `"$WT/.dev/ui-tweak-fast/"`):
  - `I'm done — show me` → `: > preview-requested`, then Step 6 (single-device cascade).
  - `Show me on iPhone + Android` → `: > preview-requested` AND `: > dual-device`, then Step 6 (it reads
    `dual-device` and runs the two-clone dual path).
  - `It already looks right — ship it` → `: > deliver` AND `: > direct-ship`, then Step 6 in **build-only**
    mode (the hand-build may predate the latest tweak — the compile gate never relaxes), then Step 7.
  - `I want more changes` / **Other** → Correction loop.

## Step 6 — preview (Phase 1): build from the PERSISTENT clone, on one or both devices

> Single responsibility: build + launch the change onto a device from a **persistent preview clone**
> (so caches stay warm across tickets), then navigate to the target screen and capture it FOR the
> designer. The build gate keys on **exit code / successful install+launch**, never on log text.
> `flutter run` = build + install + launch in one. Skip the device cascade in **direct-ship build-only**
> mode (no device, just compile). On `android`/`ios` platforms there is no `flutter run` — run the
> build-only `ui_build_cmd` and skip all flutter device steps.

**6a — freeze the audited file set** (the build mutates the tree — codegen, registrants; those
side-effects must never widen what Step 7 judges):

```bash
BASE=$(cat "$WT/.dev/ui-tweak-fast/base_ref")
git -C "$WT" diff "$BASE" --name-only > "$WT/.dev/ui-tweak-fast/audit-files"
DIRECT_SHIP=0;  [ -f "$WT/.dev/ui-tweak-fast/direct-ship" ] && DIRECT_SHIP=1
DUAL_DEVICE=0;  [ -f "$WT/.dev/ui-tweak-fast/dual-device" ] && DUAL_DEVICE=1   # set by the Step-5 card choice
```

**6b — build a warm preview clone per target** (THE cache-warming core — flutter device path only;
skipped in direct-ship build-only mode and on android/ios). Re-derive `WT`/`BASE`/`PLATFORM`/`FLUTTER_BIN`
first (separate invocation — Tooling-map rule 2). `ensure_clone <path>` creates the persistent worktree
once (NEVER deleted, so its gitignored `.dart_tool/`+`build/` survive across tickets), realigns it to
`base_ref`, drops stray prior-ticket source, overlays ONLY this ticket's changed files (**NUL-safe** —
paths may contain spaces), applies its deletions, then **verifies** the overlay byte-matches the
worktree (a silent sync miss would build STALE source that still passes the exit-code gate and shows the
designer the OLD UI — so a mismatch returns non-zero and the caller falls back to `$WT`):

```bash
[ -n "${ZSH_VERSION:-}" ] && setopt sh_word_split   # rule 3: zsh must split unquoted $FLUTTER_BIN/$cf like sh
PREVIEW_BASE="$HOME/.cache/ui-tweak-fast/$(basename "$(dirname "$(git -C "$WT" rev-parse --git-common-dir)")")"

ensure_clone() {                                   # $1 = clone path. non-zero on create/sync/verify failure.
  CLONE="$1"; TRUNK=$(dirname "$(git -C "$WT" rev-parse --git-common-dir)")
  if ! git -C "$TRUNK" worktree list --porcelain 2>/dev/null | grep -qxF "worktree $CLONE"; then
    mkdir -p "$(dirname "$CLONE")"
    git -C "$TRUNK" worktree add --detach "$CLONE" "$BASE" 2>/dev/null \
      || git -C "$TRUNK" worktree add --detach "$CLONE" HEAD || return 1
  fi
  # stop any prior-ticket live `flutter run` still attached to THIS clone before realigning (reset --hard
  # would otherwise thrash its build). pid+fifo are parked in .dart_tool (gitignored → survive clean -fd);
  # send flutter `q` so the fvm→flutter_tools→frontend_server tree tears down (killing the wrapper orphans it).
  rp="$CLONE/.dart_tool/ui-tweak-fast-run.pid"; rf="$CLONE/.dart_tool/ui-tweak-fast-run.fifo"
  if [ -f "$rp" ]; then
    [ -f "$rf" ] && [ -p "$(cat "$rf" 2>/dev/null)" ] && { printf 'q\n' > "$(cat "$rf")" 2>/dev/null; sleep 2; }
    p=$(cat "$rp" 2>/dev/null); [ -n "$p" ] && { pkill -P "$p" 2>/dev/null; kill "$p" 2>/dev/null; }
    rm -f "$rp" "$rf"
  fi
  # realign tracked files to base_ref + drop stray source; `clean -fd` has NO -x → caches survive.
  git -C "$CLONE" reset --hard "$BASE" >/dev/null 2>&1 \
    || { git -C "$CLONE" fetch --quiet "$TRUNK" 2>/dev/null; git -C "$CLONE" reset --hard "$BASE" >/dev/null 2>&1; } || return 1
  git -C "$CLONE" clean -fd >/dev/null 2>&1        # NEVER -x (that would wipe the warm caches)
  # overlay changed files (added/modified/renamed-to), NUL-delimited so spaced paths are safe:
  git -C "$WT" diff "$BASE" --no-renames --diff-filter=ACMR -z --name-only \
    | while IFS= read -r -d '' f; do mkdir -p "$CLONE/$(dirname "$f")" && cp "$WT/$f" "$CLONE/$f" || exit 1; done || return 1
  git -C "$WT" diff "$BASE" --no-renames --diff-filter=D -z --name-only \
    | while IFS= read -r -d '' f; do rm -f "$CLONE/$f"; done
  # VERIFY: every changed file in the clone must byte-match the worktree, else the preview is stale.
  git -C "$WT" diff "$BASE" --no-renames --diff-filter=ACMR -z --name-only \
    | while IFS= read -r -d '' f; do cmp -s "$WT/$f" "$CLONE/$f" || exit 1; done || return 1
  return 0
}
clone_flutter() {                                  # resolve a clone's flutter + warm its deps (cheap when warm)
  cf=$(resolve_flutter "$1" 2>/dev/null) || cf="$FLUTTER_BIN"
  ( cd "$1" && $cf pub get >/dev/null 2>&1 || true ); printf '%s' "$cf"; }

# Build the flavor arg from the DURABLE marker (Step-2 shell vars don't survive into this block):
FLAVOR=$(sed -n 1p "$WT/.dev/ui-tweak-fast/flavor" 2>/dev/null); FLAVOR_DETECTED=$(sed -n 2p "$WT/.dev/ui-tweak-fast/flavor" 2>/dev/null)
FLAVOR_ARG=""; [ -n "$FLAVOR" ] && [ "$FLAVOR_DETECTED" = detected ] && FLAVOR_ARG="--flavor $FLAVOR"
```

In **direct-ship build-only** mode (`DIRECT_SHIP=1`, no navigate need) or on `android`/`ios`: skip the
clone, `BUILD_DIR="$WT"`, `PREVIEW_FLUTTER="$FLUTTER_BIN"` — a build-only compile gate needs no warm
clone or live device.

**6c — acquire device(s) and build+launch.** Device LISTING uses `$FLUTTER_BIN` (devices are global, not
per-clone). `flutter run` runs from each target's clone dir.

> ⛔ macOS has **no `timeout`** — never use it. Use the counter-bounded poll below.

```bash
[ -n "${ZSH_VERSION:-}" ] && setopt sh_word_split   # rule 3: zsh must split unquoted $FLUTTER_BIN/$f/$F like sh
poll_for_device() { MAX=$1; i=0; while [ "$i" -lt "$MAX" ]; do          # no `timeout` (absent on macOS)
    DEV=$($FLUTTER_BIN devices --machine 2>/dev/null | jq -r '[.[]|select(.isSupported!=false)|.id][0]//empty' 2>/dev/null)
    [ -n "$DEV" ] && { printf '%s\n' "$DEV"; return 0; }; i=$((i+1)); sleep 1; done; return 1; }
ids_of_kind() { K=$1; $FLUTTER_BIN devices --machine 2>/dev/null \
  | jq -r --arg k "$K" '.[]|select((.targetPlatform//"")|test($k))|.id' 2>/dev/null; }

# --- live-run control: launch ONCE per ticket, HOT-RESTART on later rounds ----------------------
# The first preview launches a long-lived `flutter run` whose stdin is a FIFO, so a "more changes"
# round re-renders by writing `R` to that FIFO (~2-5s) instead of cold-rebuilding (~30s+). `R` (hot
# RESTART, not `r` reload) is used because UI tweaks routinely change `const` values, which only a
# restart re-evaluates. `nohup` detaches each process so it survives across separate bash calls; the
# whole mechanism is best-effort — any failure falls back to the full build below (never worse).
M="$WT/.dev/ui-tweak-fast"
launch_run() {              # $1=clone-dir $2=flutter $3=device-id $4=tag(single|ios|android)
  d="$1"; f="$2"; dev="$3"; tag="$4"
  rm -f "$M/run-$tag.fifo"; mkfifo "$M/run-$tag.fifo" 2>/dev/null || return 1
  nohup sh -c 'exec 9<>"$1"; exec sleep 2147483647' _ "$M/run-$tag.fifo" >/dev/null 2>&1 & echo $! > "$M/run-$tag.holder"
  ( cd "$d" && nohup $f run -d "$dev" --debug --no-pub $FLAVOR_ARG < "$M/run-$tag.fifo" > "$M/run-$tag.log" 2>&1 & echo $! > "$M/run-$tag.pid" )
  printf '%s\n' "$d" > "$M/run-$tag.dir"
  mkdir -p "$d/.dart_tool" 2>/dev/null                                                            # clone-local pid+fifo (cross-ticket teardown)
  cp "$M/run-$tag.pid" "$d/.dart_tool/ui-tweak-fast-run.pid" 2>/dev/null
  printf '%s\n' "$M/run-$tag.fifo" > "$d/.dart_tool/ui-tweak-fast-run.fifo" 2>/dev/null
}
# NOTE on log scanning: `flutter run` interleaves the TOOL's messages with the APP's own stdout in one
# stream. App lines are prefixed `flutter: ` (often containing words like "error"/"Exception" from
# normal runtime logging); TOOL messages are NOT. So error detection ALWAYS drops `^flutter: ` lines and
# matches the distinctive Dart compile-error shape `file.dart:line:col: Error:` — never a bare "error".
gate_launch() {             # $1=tag — poll the run log: 0=launched, 2=compile/build FAIL → 6f, 1=timeout
  tag="$1"; i=0; while [ $i -lt 300 ]; do
    grep -qE "Flutter run key commands|Dart VM Service|Syncing files to|is available at:" "$M/run-$tag.log" 2>/dev/null && return 0
    grep -v '^flutter: ' "$M/run-$tag.log" 2>/dev/null | grep -qE "\.dart:[0-9]+:[0-9]+: Error:|Compiler message:|Build failed|FAILURE:|Could not build" && return 2
    kill -0 "$(cat "$M/run-$tag.pid" 2>/dev/null)" 2>/dev/null || return 2
    i=$((i+1)); sleep 1; done; return 1; }
live_tags() { for t in single ios android; do p=$(cat "$M/run-$t.pid" 2>/dev/null); [ -n "$p" ] && kill -0 "$p" 2>/dev/null && printf '%s\n' "$t"; done; }
kill_run() {                # $1=tag — quit a live run GRACEFULLY, then drop holder + markers + clone-local files
  tag="$1"; d=$(cat "$M/run-$tag.dir" 2>/dev/null); [ -n "$d" ] && rm -f "$d/.dart_tool/ui-tweak-fast-run.pid" "$d/.dart_tool/ui-tweak-fast-run.fifo"
  # `fvm` SPAWNS (doesn't exec) flutter, so the wrapper pid is the parent of flutter_tools→frontend_server.
  # Killing the pid alone orphans the children — instead send flutter `q` (it tears down app + frontend_server
  # + itself), then SIGTERM the pid + its direct child as a fallback.
  [ -p "$M/run-$tag.fifo" ] && { printf 'q\n' > "$M/run-$tag.fifo" 2>/dev/null; sleep 2; }
  for k in pid holder; do p=$(cat "$M/run-$tag.$k" 2>/dev/null); [ -n "$p" ] && { pkill -P "$p" 2>/dev/null; kill "$p" 2>/dev/null; }; done
  rm -f "$M/run-$tag.pid" "$M/run-$tag.fifo" "$M/run-$tag.holder" "$M/run-$tag.dir" "$M/run-$tag.log"; }
hot_repreview() {           # $1=tag — sync cumulative diff into the live clone + hot-restart. 0=ok 2=compile-fail 1=fall-back
  tag="$1"; d=$(cat "$M/run-$tag.dir" 2>/dev/null); BASE=$(cat "$M/base_ref"); [ -d "$d" ] || return 1
  git -C "$WT" diff "$BASE" --no-renames --diff-filter=ACMR -z --name-only \
    | while IFS= read -r -d '' f; do mkdir -p "$d/$(dirname "$f")" && cp "$WT/$f" "$d/$f"; done   # NO realign — don't thrash the live build
  git -C "$WT" diff "$BASE" --no-renames --diff-filter=D -z --name-only \
    | while IFS= read -r -d '' f; do rm -f "$d/$f"; done
  b=$(wc -l < "$M/run-$tag.log" 2>/dev/null); b=${b:-0}                                            # baseline: ignore the build phase + all prior app logs
  printf 'R\n' > "$M/run-$tag.fifo"                                                                # hot RESTART
  i=0; while [ $i -lt 60 ]; do
    sed -n "$((b+1)),\$p" "$M/run-$tag.log" 2>/dev/null | grep -q "Restarted application" && return 0
    sed -n "$((b+1)),\$p" "$M/run-$tag.log" 2>/dev/null | grep -v '^flutter: ' | grep -qE "\.dart:[0-9]+:[0-9]+: Error:|Try again after fixing" && return 2
    kill -0 "$(cat "$M/run-$tag.pid" 2>/dev/null)" 2>/dev/null || return 1
    i=$((i+1)); sleep 1; done; return 1; }
```

- **6c.0 — warm re-preview shortcut (hot restart).** In interactive mode (`DIRECT_SHIP=0`), BEFORE
  acquiring a device, check for a live run left by an earlier round of **this same ticket**:
  `TAGS=$(live_tags)`. If non-empty, `hot_repreview "$TAG"` each tag instead of rebuilding:
  - all live tags return `0` → the new edit is on the device(s) in ~2-5s. **Skip the rest of 6b/6c**
    (no `ensure_clone`, no device acquisition); go straight to 6e (navigate + capture) then 6f.
  - any tag returns `2` → a **compile error** in the new edit → treat exactly as a build FAIL (6f
    build-fail path: NUL-safe whole-edit revert + `repair-context` `kind: build`).
  - any tag returns `1` → that live run is gone/unusable → `kill_run` **every** tag and fall through to
    the full build+launch below (so a stale run never wedges the round).
  Direct-ship/build-only mode and `android`/`ios` native profiles have no live run → this is a no-op.

- **Single-device default** — cascade, stop at the first hit, then build from ONE warm clone:
  1. **already-running / connected** (incl. a physical phone): if `$FLUTTER_BIN devices --machine` lists
     one, use it. On macOS, if `xcrun simctl list devices booted` shows a `(Booted)` sim flutter has not
     surfaced yet, grace-poll ~10s: `DEVICE=$(poll_for_device 10)`.
  2. **boot one**: `$FLUTTER_BIN emulators` → `$FLUTTER_BIN emulators --launch <id>`, then
     `DEVICE=$(poll_for_device 60)`.
  3. **no device → honest build-only fallback**: run the build-only `ui_build_cmd` in `$WT`, set a
     `no_device` flag, and let C1 say so. Do NOT fail.
  ```bash
  [ -n "${ZSH_VERSION:-}" ] && setopt sh_word_split   # rule 3 (also: requires the Step-6b/6c helpers in scope)
  if [ -z "$DEVICE" ]; then                                    # case 3 — no device → build-only gate in $WT
    ( cd "$WT" && $FLUTTER_BIN build apk --debug --no-pub $FLAVOR_ARG ) ; NO_DEVICE=1   # exit code is the gate (--no-pub: deps already resolved)
  else
    kill_run single 2>/dev/null                                # drop a stale run before this clone is realigned
    PREVIEW_DIR="$PREVIEW_BASE/preview"
    if ensure_clone "$PREVIEW_DIR"; then BUILD_DIR="$PREVIEW_DIR"; PREVIEW_FLUTTER=$(clone_flutter "$PREVIEW_DIR")
    else echo "WARN: preview clone unavailable/stale — building from the worktree (cold cache)." >&2; BUILD_DIR="$WT"; PREVIEW_FLUTTER="$FLUTTER_BIN"; fi
    launch_run "$BUILD_DIR" "$PREVIEW_FLUTTER" "$DEVICE" single  # long-lived run (FIFO stdin) → next round hot-restarts
    gate_launch single; GATE=$?                                 # 0=launched, 2=build FAIL → 6f build-fail path, 1=timeout (proceed; capture may no-op)
  fi
  ```
  (`build apk` is the Android build-only line; use the profile `ui_build_cmd` for the real platform/iOS.)

- **Dual-device (`DUAL_DEVICE=1` — designer picked "Show me on iPhone + Android")** — preview on an iOS
  sim **and** an Android emulator at once. Each platform gets its **own** warm clone, so the two
  `flutter run` never contend on a shared `.dart_tool/`/build lock (the single-dir approach races
  precisely on the cold first build):
  ```bash
  [ -n "${ZSH_VERSION:-}" ] && setopt sh_word_split   # rule 3 (also: requires the Step-6b/6c helpers in scope)
  IOS_ID=$(ids_of_kind ios | head -1); AND_ID=$(ids_of_kind android | head -1)
  [ -z "$IOS_ID" ] && [ "$(uname)" = Darwin ] && { u=$(xcrun simctl list devices available 2>/dev/null | grep iPhone | grep -m1 -oiE '[0-9a-f-]{36}'); [ -n "$u" ] && xcrun simctl boot "$u" >/dev/null 2>&1; }
  if [ -z "$AND_ID" ]; then
    avd=$($FLUTTER_BIN emulators --machine 2>/dev/null | jq -r '[.[]|select((.platformType//"")=="android")|.id][0]//empty' 2>/dev/null)
    [ -z "$avd" ] && avd=$($FLUTTER_BIN emulators 2>/dev/null | grep -iE 'android' | head -1 | awk '{print $1}')
    [ -n "$avd" ] && $FLUTTER_BIN emulators --launch "$avd" >/dev/null 2>&1
  fi
  poll_for_device 90 >/dev/null                                  # let the boots surface, then re-read each kind
  IOS_ID=$(ids_of_kind ios | head -1); AND_ID=$(ids_of_kind android | head -1)
  DEVICES=""
  if [ -n "$IOS_ID" ]; then
    kill_run ios 2>/dev/null
    ensure_clone "$PREVIEW_BASE/preview-ios"     && D="$PREVIEW_BASE/preview-ios"     && F=$(clone_flutter "$D") || { D="$WT"; F="$FLUTTER_BIN"; }
    launch_run "$D" "$F" "$IOS_ID" ios; DEVICES="$DEVICES $IOS_ID"; fi
  if [ -n "$AND_ID" ]; then
    kill_run android 2>/dev/null
    ensure_clone "$PREVIEW_BASE/preview-android" && D="$PREVIEW_BASE/preview-android" && F=$(clone_flutter "$D") || { D="$WT"; F="$FLUTTER_BIN"; }
    launch_run "$D" "$F" "$AND_ID" android; DEVICES="$DEVICES $AND_ID"; fi
  ```
  If only one kind is available, this previews on that one and notes the other was unavailable (never
  fails). Capture (6e) runs per launched device in `$DEVICES`.

> ### Build gate = exit code / successful launch (never a screenshot)
> The moment the app is installed + launched, the gate has **passed**. Some flavored flutter builds
> print a false `Gradle build failed to produce an .apk file` tail yet exit 0 — confirm via the
> installed app, not log text. A crash-on-launch or a compile failure is the **build-fail path** (6f).
> Because the live run is **detached** (so it survives for hot restart), the first launch is gated by
> `gate_launch` polling the run log for a launch marker (`GATE=2` → 6f build-fail). The hot-restart
> shortcut (6c.0) has **no exit code** — it MUST read `Restarted application` / a compile error from the
> run log. This log-reading is a deliberate, narrowly-scoped exception to the "never a screenshot / never
> log text" rule above: it gates only the *reload result of an already-launched* run, not the build.

**6d — login gate (only if needed) + navigation policy.** Navigation is **navigation-only**: ONE
deep-link fire, or a capped sequence of nav taps (tabs, menu, list rows, back). **Never** tap
confirm/submit/pay/delete, grant permissions, or type — **except** the one sanctioned login gate: if a
target screen sits behind a login wall and the app is logged out, you MAY type a dedicated **staging QA
automation** account (auto-resolved from Notion page `443eb970733e452690cfa0a299eab6f2` by app+region
inferred from the ticket; a repo `demo_auth:` block only overrides which account) and submit — nothing
else. No OTP/2FA handling (treat as a login wall → fail-silent). Probe login state first by firing a
logged-in-only deep-link and screenshotting; skip login if already in. **If the Notion MCP is
unavailable in your host, treat the gate as an unpassable login wall and fail-silent —
never error.**

  Resolve each field as `demo_auth.<field>` (from the repo profile) when present, else the derived
  default — **no `demo_auth` block at all is fine**: `notion_page` → `443eb970733e452690cfa0a299eab6f2`;
  `app` from the profile `product` (`ca*` → customer app, `da*`/`driver*` → driver app); `region`
  inferred from the ticket (below); `account_label` → `automation` (prefer the entry tagged *for
  automation usage*); `login_probe_host` → `profile` (a logged-in-only `ggv://` host). The build is
  the **staging flavor**, so the **Staging** accounts on the page are the right ones. Fetch the page
  via your host's Notion MCP and select the row by the resolved `app` + `region`.
  **Region inference** (from `ticket.json` market/title/description): Singapore/SG → `sg`, Hong Kong/HK
  → `hk`, Vietnam/VN → `vn`, Taiwan/TW → `tw`, Korea/KR → `kr`, India/IN → `in`; **multiple markets,
  no match, or no `ticket.json` → fallback `hk`**. An explicit `demo_auth.region` always wins over
  inference.

**6e — navigate to the target + capture** (best-effort, fail-silent; per launched device):
- **Tier 1 (preferred): deep-link.** Derive the `ggv://<host>` target from `ticket.json` + the change
  summary (known CAF hosts: `news, promotions, payment, profile, service-delivery, rate-us, login,
  voucher, order-detail, rate-driver`; repo `deeplink_hosts:` overrides). Fire ONE:
  - iOS: `xcrun simctl openurl "$id" "ggv://<host>"`.
  - Android: `adb -s "$id" shell am start -a android.intent.action.VIEW -d "ggv://<host>" -p <PKG>`
    where `<PKG>` is the launched app's package (`dumpsys activity activities` → resumed package, else
    the lone installed `gogovan|gogox` package) — the explicit `-p` avoids the multi-app chooser stall.
  Settle ~4s with the counter-bounded poll, then capture.
- **Tier 2 (no deep-link route): codebase-planned, nav-only tap-through**, capped at 6 taps. Plan the
  path from widget keys / route names. **Tap coordinates must be in DEVICE-DISPLAY space**: `screencap`
  returns the native framebuffer while `input tap` expects display coords — read `wm size` and the PNG
  dims, scale `x=sx*wm_w/shot_w, y=sy*wm_h/shot_h`. iOS taps need `idb ui tap` (`xcrun simctl` cannot
  tap); if `idb` is absent, Tier-2 is unavailable on iOS → fail-silent.
- **Capture mechanics:** screenshot + a recording that spans the CRUX into
  `.dev/ui-tweak-fast/demo`. Scope before recording: start on the TRIGGER control for
  reuse/button-triggered behaviours (B6), keep recording until the crux UI has RENDERED — async loads
  can show a placeholder 10s+ (A3) — and screenshot-verify the post-fix state before finalizing (B8).
  - Recording is a backgrounded SHELL job with a generous safety cap, stopped by SIGINT — never a
    fixed short window (the clip must contain the crux, whatever its length), and on Android NEVER
    the MCP `adb_shell` tool (its schema has no `run_in_background`; the file is never produced — A1):
    `adb -s "$id" shell screenrecord --size 720x1280 --time-limit 180 /sdcard/uitw.mp4 &` … act …
    `adb -s "$id" shell pkill -INT screenrecord` (SIGINT flushes a valid mp4 — A2), then pull.
  - iOS: `xcrun simctl io "$id" screenshot after-<id>.png`; record `xcrun simctl io "$id" recordVideo
    --codec h264 after-<id>.mp4` backgrounded, SIGINT-stopped after the crux renders.
  - Android screenshot: `adb -s "$id" exec-out screencap -p > after-<id>.png`; the `--size` ladder
    still applies (`--size 720x1280` → `--size 540x1140` → device-native) — a sizeless `screenrecord`
    throws codec error -22 on large native resolutions and produces a 0-byte file.
  - Post-process: normalize VFR→CFR before any trim (`ffmpeg -i in.mp4 [-t N] -r 15 -vsync cfr -c:v
    libx264 -pix_fmt yuv420p -movflags +faststart out.mp4` — A4) and verify the final clip's last
    frame is the crux (`ffmpeg -sseof -1 -i out.mp4 -frames:v 1 last.png` — A5); no `ffmpeg` → skip
    with a one-line note and upload the raw SIGINT-flushed clip.
  Append each output path to `.dev/ui-tweak-fast/demo-files`.
- **Pixel-verify subtle colours (stale-build guard).** Only when the checklist names a colour
  (`grep -qiE '#[0-9A-Fa-f]{6}|target=.*(colou?r|bg|background|shade|fill)' figma-context.md`): sample the
  rendered pixel at a flat point INSIDE the changed region (away from text/icons) and compare to the
  target hex (per-channel |Δ|≤4). First available sampler:
  - `python3 -c "from PIL import Image;print('#%02X%02X%02X'%Image.open('after-<id>.png').convert('RGB').getpixel((X,Y)))"`
  - else ImageMagick: `magick after-<id>.png -format '%[hex:p{X,Y}]' info:` (or `convert …`).
  - Neither installed → skip with a one-line note (best-effort, like the `screenrecord` ladder).
  If it equals the OLD value (or is outside tolerance), the build is stale → kill + relaunch a fresh
  `flutter run` once, re-capture, re-sample. Still stale → fail-silent (no capture; honest no-image
  note). This NEVER relaxes the build gate (Step 2 is exit-code-only); it guards only what gets shown.
- **Could not reach the target** (no route, tap-through stuck, `idb` absent, unpassable login wall) →
  **fail-silent**: capture nothing, leave `demo-files` empty, continue. Never ask the designer to drive,
  never fail the gate.

**6f — record success / failure.** Builds run in the clone, so `$WT` normally stays clean; in
build-only/direct-ship mode the build ran in `$WT`, so still quarantine its side-effects. The freeze in
6a is what actually bounds Step 7's judged set. Restore anything in `$WT` outside the frozen audit set —
**NUL-safe** (a spaced path must still be restored), and never `xargs -r` (not portable to older macOS):
```bash
# restore worktree files touched outside the frozen audit set (NUL-safe; no -r dependency)
git -C "$WT" diff "$BASE" -z --name-only \
  | { while IFS= read -r -d '' f; do grep -qxF "$f" "$WT/.dev/ui-tweak-fast/audit-files" || printf '%s\0' "$f"; done; } \
  | xargs -0 -I{} git -C "$WT" checkout -- {} 2>/dev/null || true
printf 'Status: PASS\n' > "$WT/.dev/ui-tweak-fast/build-pass"
[ "$DIRECT_SHIP" = 1 ] || : > "$WT/.dev/ui-tweak-fast/preview-shown"   # direct-ship has no "looks good?" stop
rm -f "$WT/.dev/ui-tweak-fast/repair-count"                           # clean build resets the repair budget
```
- **build FAIL** (`gate_launch` returned `GATE=2`, a non-zero `ui_build_cmd`/no-device build, or the
  6c.0 `hot_repreview` returned `2`) → agent repair (NOT a designer error). The whole-edit revert is
  **NUL-safe** so a spaced path is always reverted (this backs the "broken edit is dropped" guarantee).
  A live run may stay up (it still shows the last good frame); the repair round's 6c.0 re-syncs the
  fixed files and hot-restarts it:
  ```bash
  git -C "$WT" diff "$BASE" -z --name-only | xargs -0 git -C "$WT" checkout --
  n=$(cat "$WT/.dev/ui-tweak-fast/repair-count" 2>/dev/null || echo 0); echo $((n+1)) > "$WT/.dev/ui-tweak-fast/repair-count"
  { echo "kind: build"; echo "error:"; echo "<one-line compile error>"; } > "$WT/.dev/ui-tweak-fast/repair-context"
  rm -f "$WT/.dev/ui-tweak-fast/build-pass" "$WT/.dev/ui-tweak-fast/preview-shown"
  ```
  Go to the repair loop (max 3, then engineer card Ce). Under `--auto`, print
  `UI-TWEAK-FAST BUILD-FAIL: <reason> — repair attempt <n>/3.` to stderr.

After a successful **interactive** preview (`DIRECT_SHIP=0`) → render **C1 (looks-good)**
(header `Next step`): show the captured screenshot(s) (one per device when the
dual-device choice was picked) if any, else an honest *"I confirmed it compiles but couldn't auto-reach
<screen> to grab a shot"* note:
> Here's <screen> with your change, on <device(s)>:  [screenshot]
> (What changed: <plain summary>.)  Does it look right?

- options: **`Ship it`** *(recommended)* — "Run the full check + open a draft PR with a link on the
  work item." / **`I want more changes`** — "Tell me what to adjust; I'll redo and re-show it."
- routing: `Ship it` → write `deliver` → Step 7. `I want more changes` / Other → Correction loop. Other
  text that is ONLY an existing local image/video path (dragged in) → append it to `demo-files`, reply
  "Got it — I'll include it.", re-render.

In **direct-ship** mode there is no card — go straight to Step 7.

## Step 7 — audit (Phase 2): the deferred, model-agnostic logic gate

> Reached only on the deliver path (`deliver` exists). Phase 1 already proved it compiles; this proves
> it changes **no logic**, ONCE, on the final cumulative diff, right before commit. Both judges always
> run **unless the 7b deterministic pre-pass already BLOCKs** — for a diff with ANY text file the pre-pass
> can only short-circuit to BLOCKED, never to CLEAR, so a text CLEAR always required both judges to actually
> run and agree (nothing upstream proves the diff is value-only). The ONE exception is **7a.5** below: a
> binary-only changeset is CLEAR **by construction** (it cannot carry logic) and is the only path to a CLEAR
> verdict without both judges. **No model is pinned** — decorrelation is by lens (so
> it runs under any host's own model).

```bash
WT=$(git rev-parse --show-toplevel)
[ -f "$WT/.dev/ui-tweak-fast/deliver"  ] || { echo "FAIL: audit is deliver-path only." >&2; exit 1; }
grep -q '^Status: PASS' "$WT/.dev/ui-tweak-fast/build-pass" 2>/dev/null || { echo "FAIL: build did not pass — run preview first." >&2; exit 1; }
BASE=$(cat "$WT/.dev/ui-tweak-fast/base_ref")
```

**7a — format first**, then compute the diff ONCE. Run the profile `format_cmd` (e.g.
`/format --skip-commit`, or inline `dart format .`) so the judges see exactly what will ship. Then:
```bash
CHANGED_FILES=$(git -C "$WT" diff "$BASE" --name-only)
DIFF_TEXT=$(git -C "$WT" diff "$BASE")          # the exact text that ships; fed inline to both judges
```

**7a.5 — binary-only changeset → CLEAR by construction.** A changeset whose **every** file is binary
(image assets across density buckets, fonts, …) **cannot carry logic**, so the panel — whose only
question is *UI-only vs behavior* — is trivially CLEAR. Detect via `--numstat` (binary rows report
their add/remove columns as `-`):
```bash
NUMSTAT=$(git -C "$WT" diff "$BASE" --numstat)
NON_BINARY=$(printf '%s\n' "$NUMSTAT" | grep -cvE '^-\t-\t')   # rows that are NOT binary
```
- `NUMSTAT` non-empty **and** `NON_BINARY == 0` → **CLEAR by construction**: skip the structural
  pre-pass (7b) **AND** the two judges (7c) entirely, write `.dev/ui-verify-pass.md` first line
  `Status: CLEAR`, then go straight to Step 8 (commit). This is **not** a no-op — the binary assets ARE
  the change (e.g. a swapped `pick_up_code.png` shipped at 1x/2x/3x to fix a blurry retina image). It
  closes a false BLOCK: a binary diff has no `+`/`-` hunks, which a judge misreads as "diff incomplete".
- Any text file in the set (`NON_BINARY > 0`) → fall through to 7b + the panel as usual, so a mixed
  binary+`.dart` diff still gets full scrutiny on its text hunks.

**7b — deterministic structural pre-pass** (grep only, no model call). Short-circuits to BLOCKED ONLY
on added-line signals that are unambiguously runtime behavior. Allow-by-default, deny only unambiguous
behavior (a false BLOCK reverts a legit UI change; a miss is caught by the panel below). Imports, pure
layout/structure widgets (`LayoutBuilder`, `ConstrainedBox`, `Row`/`Column`/`Stack`/`Padding`/…), style
tokens (`App*` accessors), and generic shapes (`=>`, `return`, `if (`, `for (`) are **not** signals.

```bash
ADDED=$(printf '%s\n' "$DIFF_TEXT" | grep -E '^\+' | grep -vE '^\+\+\+')
# Unambiguous runtime-behavior signals only (macOS/BSD grep -E safe; no \b).
# KEEP IN SYNC with commands/design/ui-tweak/audit.md's BEHAVIOR_RE (the canonical copy; there is no
# automated byte-equal lint covering THIS file — the prompt-lint gate only binds audit.md ↔ the
# dispatcher workflow). If you tune one, tune both.
BEHAVIOR_RE='(initState|dispose|didChangeDependencies|didUpdateWidget|deactivate|setState|notifyListeners|addListener|removeListener)\(|GestureRecognizer|await |async[ ({]|\.then\(|ref\.(read|watch|listen)\(|Navigator\.|GoRouter|context\.(go|push|pop)|\.pushNamed\(|\.pushReplacement|StreamSubscription|StreamController'
STRUCTURAL_HIT=$(printf '%s\n' "$ADDED" | grep -cE "$BEHAVIOR_RE")
```
- `STRUCTURAL_HIT > 0` → **BLOCKED** (record the matched signal as the reason), skip the judges, go to
  7d. (This never produces an early CLEAR — only an earlier BLOCK.)
- `STRUCTURAL_HIT == 0` → 7c. (A non-hit is NOT a pass — the panel still runs in full.)

**7c — two independent judges, decorrelated by lens (NOT by model).** Spawn TWO read-only **generic**
subagents, in parallel, each fed the precomputed `$DIFF_TEXT` +
`$CHANGED_FILES` inline (so git/file IO is paid once for the panel). **Do not pass a model override and
do NOT spawn a preset/named subagent type whose definition pins a specific model** — a spawn inherits that
pin, which re-introduces the host-specific model pin this skill avoids (prose cannot strip it once
inherited). Paste the two lens prompts inline instead. The prompts are different lenses so their
misses are not correlated (the `.dev/*-pass.md` filenames below are just persistence sinks, not agent
bindings):

- **Judge A — behavior lens**: *"Here
  is a diff that is supposed to be UI-only (visual values, layout, structure). Decide whether it changes
  any PROGRAM BEHAVIOR — control flow, data/state, side effects, evaluation order, contracts, defaults,
  interaction wiring, navigation. Default to BLOCKED if uncertain. Return first line `Status: CLEAR` or
  `Status: BLOCKED` + one sentence."*
- **Judge B — UI-completeness lens**: *"Here
  is a diff. Confirm every change is purely visual/layout/structure, AND read
  `.dev/ui-tweak-fast/figma-context.md` and assert the diff covers every WILL-EDIT target row (a miss →
  BLOCKED; when `Fetched: DEGRADED/SKIPPED`, judge on UI-vs-logic merits only). Return first line
  `Status: CLEAR` or `Status: BLOCKED` + one sentence."*

Persist each judge's returned text: Judge A → `.dev/dev-reviewer-pass.md`, Judge B →
`.dev/ui-verify-pass.md` (first line `Status: CLEAR|BLOCKED`).

**7d — adjudicate (unanimous CLEAR).**
- Any `BLOCKED` (or a missing file / agent error / the 7b short-circuit) → whole run BLOCKED → 7e.
- All CLEAR → `rm -f "$WT/.dev/ui-tweak-fast/.not-deliverable"`; ensure `.dev/ui-verify-pass.md` first
  line is `Status: CLEAR`. Go to Step 8 (commit).

**7e — BLOCKED → agent repair (max 3; NOT a designer card).** The revert is **NUL-safe** so a logic-
tainted edit in a spaced path is always dropped (this backs the whole-run-revert guarantee):
```bash
git -C "$WT" diff "$BASE" -z --name-only | xargs -0 git -C "$WT" checkout --   # drop the flagged edit + format pass
n=$(cat "$WT/.dev/ui-tweak-fast/repair-count" 2>/dev/null || echo 0); echo $((n+1)) > "$WT/.dev/ui-tweak-fast/repair-count"
{ echo "kind: audit"; echo "finding:"; echo "<one-line plain summary of what touched logic>"; } > "$WT/.dev/ui-tweak-fast/repair-context"
```
Go to the repair loop. Under `--auto`, audit BLOCKED is **loud-fail with NO repair loop** (a logic
finding is not a mechanical fix): still run the revert, then print
`UI-TWEAK-FAST BLOCKED (<judge>): <reason> — reverted, no changes kept.` to stderr and exit non-zero.

## Step 8 — ship: commit, draft PR, ticket transition (inline; no `/commit` / `/pull-request` read)

All inlined so this file stays self-contained.

**8a — commit only the covered files** (the Step-4d coverage table; formatter-touched extras go in the
PR body's `### Formatter-only changes`). No extra confirm — "Ship it" already authorized the handoff.

> **Externally-visible labelling — tag `ui-tweak`, NEVER `ui-tweak-fast`.** Every artifact an engineer
> or observer can see — the **commit-message prefix** (8a) and the **PR title** (8b) — is tagged
> `[ui-tweak]`, so this skill presents itself outwardly as plain `/ui-tweak`; the "fast" variant is
> never surfaced on the commit, PR, or work item. This is deliberate — **do NOT "correct" the prefix
> back to `[ui-tweak-fast]`.** (Internal-only references keep the real name on purpose because they are
> never published: the `.dev/ui-tweak-fast/` markers, the `~/.cache/ui-tweak-fast/` clone, the Step-1
> usage-telemetry line, and the `--auto` stderr diagnostics.) The PR body header, the ticket PR-link
> comment, and the `ui-tweak-demo` markers below already say "UI tweak" / "ui-tweak-demo" — keep them.

```bash
git -C "$WT" add <covered files>
git -C "$WT" commit -m "[ui-tweak] <plain component + visual property + old→new> (<TICKET-ID>)"
git -C "$WT" push -u origin "$(git -C "$WT" branch --show-current)"
```

**8b — open a DRAFT PR** with a pre-built, designer-verifiable body. Title prefixed `[ui-tweak]` (NOT
`[ui-tweak-fast]` — see the labelling note in 8a).
Body sections: `## UI Tweak — designer-verifiable summary` (Source / Grounding-provenance / Audit
verdict / Coverage table with `shared?`), then a marker-wrapped `## Demo`:
```
<!-- ui-tweak-demo -->
## Demo
<links / images>
<!-- /ui-tweak-demo -->
```
Demo content, in priority order:
1. **Captured demo** (`demo-files`, preferred — the actual result): a Linear `assetUrl` is a
   **deterministic 401 to GitHub** on a private repo, so in the PR body it is **always a plain link**,
   never `![](…)`. Publish via the **`/ggx-attach` Attach core** (`commands/dev/ggx-attach.md` — the
   single source of truth; do NOT re-derive) with `namespace: "ui-tweak-demo"`,
   `sha: $(git -C "$WT" rev-parse --short HEAD)`, and `pr` absent (the PR doesn't exist yet): the core
   writes ONE marked inline-rendered Linear comment (`<!-- ggx-attach:ui-tweak-demo -->`, idempotent by
   marker + sha; never a download-card attachment) and returns the `assetUrl`(s) to reference here.
2. **Ticket visuals**: image attachments from `ticket.json` embedded as `![]()` only if publicly
   fetchable (`curl -fsI <url>` → 200); the Figma node URL is a page → always a plain
   `Target design (Figma): <url>` link, never `![]()`.
3. **Fallback** (only if 1–2 yield nothing): "No screenshot — eyeball before→after against the Figma
   node or ticket".
```bash
gh pr create --draft --title "[ui-tweak] <plain summary>" --body-file <body>
```

**8c — ticket transition (Linear-only, idempotent, best-effort — never fails the run).** After the
draft PR is open and a `🎨 UI tweak ready for engineer review` PR-link comment is posted: set status →
`In Review` (skip if already there/later) and remove the `ready-to-dev` label, keeping all others (e.g.
`design bug`) — read current labels first, `save_issue` replaces the whole set. `assignee` is never
touched. A failure here logs ONE WARN and continues (the PR is the deliverable). Then stop any live
preview run(s) — `for t in single ios android; do kill_run "$t"; done` — the ticket is shipped, so free
the device and the detached `flutter run` processes (best-effort; `kill_run` is defined in Step 6c).

**Done card C5** (plain text, no choice):
```
📍 Done! I've opened a draft PR for engineer review and left a link on the work item.
📦 Link: <url>  (draft — an engineer reviews it; it won't go live automatically.)
👉 Your part is finished. Want more changes? Just tell me.
```
Under `--auto`, instead print `Ticket <id>: ui-tweak-fast shipped — draft PR open.` and exit 0.

## Correction & repair loops

Both keep the original `base_ref` (the diff is always cumulative).

- **Designer correction** ("I want more changes" / any Other free-text at a C1 card): clear all
  downstream markers so the walker drops back to iteration, reset the repair budget + `direct-ship`,
  then re-run **Step 4 (apply)** with the new requirement — **unless no `triage-pass` exists yet** (the
  prior run stopped at a needs-logic triage), in which case re-enter at **Step 3 (triage)** so the
  re-worded request is re-classified before any edit. `triage-pass` is **kept** (a value tweak stays
  pure-visual; the Step-7 panel still backstops any logic that sneaks in):
  ```bash
  rm -f "$WT"/.dev/ui-tweak-fast/{build-pass,preview-shown,deliver,direct-ship,dual-device,demo-files,repair-context,repair-count} \
        "$WT"/.dev/ui-verify-pass.md "$WT"/.dev/dev-reviewer-pass.md
  ```
  **Keep the `run-*` markers** — the live preview is deliberately reused: when the designer next picks
  "show me", 6c.0 hot-restarts it (~2-5s) instead of cold-rebuilding. (`base_ref` is unchanged, so the
  files synced into the live clone stay correct.)
  Post-deliver guard: if a commit already exists beyond `base_ref` (`git merge-base --is-ancestor
  $base_ref HEAD` true AND `HEAD != base_ref`), the previous change was already handed off — say *"that
  was already wrapped up; I'll start a fresh round"*, **kill the live runs first**
  (`for t in single ios android; do kill_run "$t"; done` — the diff base is about to change) and
  re-baseline (`base_ref` = current HEAD) before Step 4.
- **Agent repair** (a `repair-context` written by 6f or 7e): in Step 4 repair mode, read
  `repair-context` (`kind: build` → fix the value that broke compile; `kind: audit` → redo as pure UI
  without the logic touch), keep `base_ref`, clear
  `build-pass`/`preview-shown`/`deliver`/`direct-ship`/`dual-device` + both judge files, **keep** the
  designer's intent to preview and `repair-count`. After 3
  attempts (`repair-count >= 3`) render the **engineer card Ce** instead of re-applying:
  > I tried a few times but couldn't make this work as a pure look-and-feel change — this part may need
  > an engineer. Everything's back to how it was. (Or pick Other to describe it differently and I'll
  > start fresh, which resets my attempts.)

  Under `--auto`, at `repair-count >= 3` print
  `FAIL: /ui-tweak-fast --auto — repair budget exhausted (3); needs an engineer.` to stderr, exit
  non-zero.

## Auto path (no cards)

Under `--auto`: Step 0 (parse) → Step 1 (worktree; id required) → Step 2 → Step 3 (triage; a clear
needs-logic verdict loud-fails with `UI-TWEAK-FAST NEEDS-ENGINEER: …` and exits non-zero **before any
edit**) → Step 4 (single apply, no
plan confirm) → write `deliver` + `direct-ship` (skip the device preview; if `.not-deliverable` exists,
fail with `… change is partial …` to stderr) → Step 6 build-only compile gate → Step 7 (both judges,
loud-fail on BLOCKED, no repair loop) → Step 8 (commit → draft PR → transition). The draft PR an
engineer reviews is the human gate that replaces the cards. Every failure prints ONE deterministic
stderr line and exits non-zero.

## Constraints

- Terminal is always a **draft PR** — never draft→ready, never merge. Allowed ticket writes: the
  PR-link comment, attaching a captured/handed-in demo, and the PR-open transition (status → In Review +
  drop `ready-to-dev`, Linear-only). `assignee` is never touched.
- The change set is **UI-only** (visual values / layout / structure). Logic, build config
  (`build.gradle`, `pubspec.yaml`, `Info.plist`, `AndroidManifest.xml`), referenced `@+id`/function
  renames → blocked by the Step-7 panel.
- The persistent preview clone is an **optimization, not a requirement** — if it can't be created, fall
  back to building from the worktree with a WARN. Never run `git clean -x` in it (that would wipe the
  warm caches that are the whole point). It is created off trunk and re-aligned to `base_ref` each run;
  it never holds uncommitted ticket state that matters (the worktree is the source of truth).
- No fan-out/orchestration tooling, no pinned models — runs identically on any host.

## Install / availability

This skill is picked up automatically by the repo's install scripts (`./install.sh` and
`./install-cursor.sh`) the next time either is run, since both symlink every `skills/<category>/<name>/`
directory. No new install-script wiring is needed. If your host maps tools via a rules file, ensure it
maps the generic operations used here (spawn a subagent, ask the designer, the worktree absolute-path
rule) to the host's own tool names.
