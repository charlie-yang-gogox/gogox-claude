---
name: start
description: "Stage 1 of the /ui-tweak pipeline — the up-front worktree split (R19), mirroring /dev:start and /port:start. Resolves the platform profile, fetches+caches the ticket (read-only), creates+enters the ../<ticket-id> worktree via /add-worktree, and writes the worktree-ready marker so the orchestrator never re-splits. On flutter repos it also writes the resolved-env block: the fvm-aware flutter binary marker (.dev/ui-tweak/flutter-bin), backed by a per-machine token cache (~/.cache/ui-tweak/<repo>/flutter-kind) so a fresh per-ticket worktree skips the fvm probe, + a non-blocking iOS-simulator pre-warm, so preview never rediscovers fvm or cold-boots on the critical path. Invoked by /ui-tweak:ff Step 0 before the first edit (every run splits up-front — there is no in-place path, B3). Designers never type it directly (a misdirect guard routes them back to /ui-tweak). Does NOT touch ticket status/assignee (no /_ticket-init)."
---

<!-- RULE: command content is English. Designer-facing CARD text may be Traditional Chinese. -->

# `/ui-tweak:start`

> **Single responsibility**: prepare the workspace (profile + ticket + worktree) and write the
> `worktree-ready` marker. Run **first**, by `/ui-tweak:ff` Step 0 (R19), before any edit — exactly
> like `/dev:start` / `/port:start` create their worktree up-front. Every `/ui-tweak` run splits a
> worktree here; there is no in-place path (B3).

## Inputs

`<source> [figma-url] [--auto] [--no-ticket-init]` — `<source>` MUST resolve to a ticket id (ID/URL);
free text cannot name a branch/PR. `/ui-tweak:ff` Step 0 only ever invokes this stage with an id
already parsed out of the designer's input (or supplied via card C-WT), so a free-text arrival here
is a misdirect. `--no-ticket-init` is **accepted and ignored** (never error on it): this stage does
no `/_ticket-init` anyway, so the flag is semantically a no-op — it exists only because `/ggx-work`'s
lane-agnostic spawn builder appends it uniformly and `/ui-tweak:ff` may forward it (belt-and-suspenders;
ff.md normally strips it before dispatching here).

## Step 0a — misdirect guard (R5/D11)

If `UI_TWEAK_FF` is not set, print **C-MISDIRECT** (see `/ui-tweak:apply` Step 0a) and STOP — a
designer never types `/ui-tweak:start`.

## Step 0 — precondition

- Parse `<source>`. Free text (no `[A-Z]+-[0-9]+` id and no Linear issue URL) → STOP:
  `FAIL: /ui-tweak:start requires a ticket source to name the worktree branch/PR.`
- Resolve the ticket prefix at runtime from `~/.claude/commands/profiles/org.yaml`
  (`linear.prefixes` / `jira.prefixes`) — never hardcode.

## Read / write

1. Resolve profile: `<repo>/.gogox-claude.yaml` → `platform`; fallback
   `~/.claude/commands/profiles/registry/<basename>.yaml`.
2. Fetch the ticket (`mcp__claude_ai_Linear__get_issue` or Jira per `_ticket-lib.md`), **read-only**
   (no status/assignee change, no comment). Determine branch type (`fix` for a bug-labelled ticket,
   else `feat`).
3. Create+enter the worktree: `/add-worktree <ticket-id> --type <feat|fix>` (the `../<ticket-id>`
   convention; off latest trunk). If it already exists, `/add-worktree` detects and asks (or enters it
   under `--auto`). After this returns the session is **inside** the worktree.
4. **Inside the worktree**, cache the ticket and write the up-front marker (both under the worktree's
   `.dev/ui-tweak/`, so the orchestrator's walker and `/ui-tweak:apply` find them locally — O1 avoids a
   re-fetch downstream):
   ```bash
   mkdir -p .dev/ui-tweak
   # ticket.json — read-only snapshot for apply/deliver (skip the re-fetch)
   printf '%s\n' "$TICKET_JSON" > .dev/ui-tweak/ticket.json
   # worktree-ready — Step-0 idempotency marker (ff.md skips re-split when present)
   printf 'ticket=%s\n' "$TICKET_ID" > .dev/ui-tweak/worktree-ready
   ```
5. **Resolved-env block (flutter platform only — skip for android/ios)** — resolve the build tooling
   ONCE here so no later stage rediscovers it per run:
   ```bash
   # (a) flutter binary resolution — resolve ONCE PER MACHINE, cache a RELATIVE token (never a
   #     $WT-absolute path), reuse across every ticket's worktree. Real machines break config-only
   #     detection in BOTH directions: an engineer may have only bare `flutter` (fvm hidden in
   #     ~/.pub-cache/bin); a designer may have ONLY fvm (bare `flutter` = command-not-found). Probe
   #     each candidate ONCE (`--version`); persist the winner as a token in a per-machine shared
   #     cache so the NEXT ticket's fresh worktree skips the probe. Every later flutter/dart call
   #     reads the expanded worktree-local marker — nothing downstream guesses again.
   WT=$(git rev-parse --show-toplevel)
   TRUNK=$(dirname "$(git rev-parse --git-common-dir)")     # main checkout; stable across all worktrees
   CACHE_DIR="$HOME/.cache/ui-tweak/$(basename "$TRUNK")"   # basename collision = known debt (deferred)
   CACHE_FMT=v1                                             # bump to self-invalidate every cached token if the grammar changes
   probe() { eval "$1 --version" >/dev/null 2>&1; }
   # cache token grammar (file: line1=CACHE_FMT, line2=token):
   #   sdk-rel|<relpath>  → $WT/<relpath>      (fvm SDK symlink; worktree-RELATIVE → re-expanded per ticket)
   #   fvm-abs|<fvmpath>  → <fvmpath> flutter  (fvm binary is machine-stable → absolute is safe to cache)
   #   bare               → flutter
   expand_token() { case "$1" in
       "sdk-rel|"*) printf '%s' "$WT/${1#sdk-rel|}";;
       "fvm-abs|"*) printf '%s flutter' "${1#fvm-abs|}";;
       bare)        printf 'flutter';;
     esac; }
   bin_exists() { h=${1% flutter}; case "$h" in /*) [ -x "$h" ];; *) command -v "$h" >/dev/null 2>&1;; esac; }  # space-safe head check
   FLUTTER_BIN=""
   # (a.1) explicit override in <repo>/.gogox-claude.yaml — RELATIVE paths only (an absolute path would
   #       leak a per-machine path into a committed, shared file). Resolve against $WT.
   OV=$(sed -n 's/^flutter_bin:[[:space:]]*//p' "$WT/.gogox-claude.yaml" 2>/dev/null | head -1)
   if [ -n "$OV" ]; then case "$OV" in
       /*) echo "WARN: .gogox-claude.yaml flutter_bin is absolute ('$OV') — ignored (commit only relative paths)." >&2;;
       *)  [ -x "$WT/$OV" ] && FLUTTER_BIN="$WT/$OV";;
     esac; fi
   # (a.2) per-machine shared cache hit → 0 probes (validate cheaply; never run --version on a hit)
   if [ -z "$FLUTTER_BIN" ] && [ -f "$CACHE_DIR/flutter-kind" ] \
      && [ "$(sed -n 1p "$CACHE_DIR/flutter-kind")" = "$CACHE_FMT" ]; then
     cand=$(expand_token "$(sed -n 2p "$CACHE_DIR/flutter-kind")")
     [ -n "$cand" ] && bin_exists "$cand" && FLUTTER_BIN="$cand"
   fi
   # (a.3) cache miss → probe by priority, set KIND alongside; direct SDK binary beats the fvm wrapper
   if [ -z "$FLUTTER_BIN" ]; then
     FVM_BIN=$(command -v fvm 2>/dev/null || true)
     [ -z "$FVM_BIN" ] && [ -x "$HOME/.pub-cache/bin/fvm" ] && FVM_BIN="$HOME/.pub-cache/bin/fvm"
     PINNED=0; { [ -f "$WT/.fvmrc" ] || [ -f "$WT/.fvm/fvm_config.json" ]; } && PINNED=1
     SDK_REL=".fvm/flutter_sdk/bin/flutter"; KIND=""
     if   [ "$PINNED" = 1 ] && [ -x "$WT/$SDK_REL" ] && probe "$WT/$SDK_REL"; then FLUTTER_BIN="$WT/$SDK_REL"; KIND="sdk-rel|$SDK_REL"
     elif [ "$PINNED" = 1 ] && [ -n "$FVM_BIN" ] && probe "$FVM_BIN flutter";  then FLUTTER_BIN="$FVM_BIN flutter"; KIND="fvm-abs|$FVM_BIN"
     elif probe flutter; then FLUTTER_BIN="flutter"; KIND="bare"
       [ "$PINNED" = 1 ] && echo "WARN: repo pins its SDK via fvm but fvm did not run — using system flutter (may drift from CI)." >&2
     elif [ -n "$FVM_BIN" ] && probe "$FVM_BIN flutter"; then FLUTTER_BIN="$FVM_BIN flutter"; KIND="fvm-abs|$FVM_BIN"
     fi
     # guard empty BEFORE writing — never persist an empty token to the shared cache
     [ -z "$FLUTTER_BIN" ] && { echo "FAIL: no working flutter found (tried fvm + bare flutter). Install flutter or fvm, then re-run." >&2; exit 1; }
     [ -n "$KIND" ] && printf '%s\n%s\n' "$CACHE_FMT" "$KIND" > "$CACHE_DIR/flutter-kind"   # KIND empty only for an override (not cacheable)
   fi
   [ -z "$FLUTTER_BIN" ] && { echo "FAIL: no working flutter found (tried fvm + bare flutter). Install flutter or fvm, then re-run." >&2; exit 1; }
   printf '%s\n' "$FLUTTER_BIN" > .dev/ui-tweak/flutter-bin   # worktree-local EXPANDED value; downstream read unchanged
   # (b) iOS simulator pre-warm (macOS only) — NON-BLOCKING + fail-silent: kick the boot off in the
   #     background so the cold boot overlaps ticket analysis + the first apply, and a later "show me"
   #     finds a warm simulator. NEVER wait on it, NEVER fail or warn because of it (a designer may
   #     preview on Android or a physical phone instead — the warm sim is a bonus, not a requirement).
   if [ "$(uname)" = "Darwin" ] && ! xcrun simctl list devices booted 2>/dev/null | grep -q '(Booted)'; then
     udid=$(xcrun simctl list devices available 2>/dev/null | grep iPhone | grep -m1 -oE '[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}')
     [ -n "$udid" ] && { (xcrun simctl boot "$udid" >/dev/null 2>&1 &) ; }
   fi
   # (c) flavor resolution + detection (GGC-7) — resolve the effective flavor by precedence, PROBE
   #     whether the repo actually has it, and cache the result worktree-local so /ui-tweak:preview
   #     never re-probes. Android flavor = a gradle `productFlavors` entry; iOS flavor = an Xcode
   #     scheme of that name — DIFFERENT mechanisms (they only coincide as `stag` on gogox repos), so
   #     probe BOTH surfaces that exist in the repo and treat "found in either" as detected. A repo
   #     without the matching flavor must degrade to a no-flavor build downstream, not a cryptic error.
   #     Precedence: <repo>/.gogox-claude.yaml `flavor:` (relative, plain token) > platform default.
   #     The platform-default flavor is read from the LAST token of ui_build_cmd in the resolved
   #     flutter.yaml (kept at the END on purpose, see flutter.yaml), so this stays a mechanical tail-read.
   #     Marker grammar (.dev/ui-tweak/flavor): line1 = flavor name (empty ⇒ no flavor configured at all),
   #                                            line2 = detected|missing (missing ⇒ preview strips --flavor).
   PLATFORM_YAML="$HOME/.claude/commands/profiles/platform/flutter.yaml"
   FLAVOR_OV=$(sed -n 's/^flavor:[[:space:]]*//p' "$WT/.gogox-claude.yaml" 2>/dev/null | head -1)
   if [ -n "$FLAVOR_OV" ]; then
     FLAVOR="$FLAVOR_OV"
   else
     # platform default = last whitespace token of ui_build_cmd's `--flavor <x>` (no override ⇒ empty)
     FLAVOR=$(sed -n 's/^ui_build_cmd:[[:space:]]*//p' "$PLATFORM_YAML" 2>/dev/null \
       | grep -oE -- '--flavor[[:space:]]+[A-Za-z0-9_]+' | head -1 | awk '{print $2}')
   fi
   FLAVOR_DETECTED=missing
   if [ -z "$FLAVOR" ]; then
     # no flavor configured anywhere — nothing to probe; preview will build with no --flavor.
     FLAVOR_DETECTED=missing
   else
     # Android: a `productFlavors` block declaring this flavor name. Match the flavor token inside the
     # block in either Groovy (build.gradle) or Kotlin DSL (build.gradle.kts: `create("stag")` / `stag {`).
     ANDROID_GRADLE=""
     for g in "$WT/android/app/build.gradle" "$WT/android/app/build.gradle.kts"; do
       [ -f "$g" ] && ANDROID_GRADLE="$g" && break
     done
     if [ -n "$ANDROID_GRADLE" ] && grep -q 'productFlavors' "$ANDROID_GRADLE" 2>/dev/null; then
       # tolerate `stag {`, `stag{`, `create("stag")`, `create('stag')`
       if grep -qE "(^|[^A-Za-z0-9_])${FLAVOR}[[:space:]]*\{" "$ANDROID_GRADLE" 2>/dev/null \
          || grep -qE "create\([\"']${FLAVOR}[\"']\)" "$ANDROID_GRADLE" 2>/dev/null; then
         FLAVOR_DETECTED=detected
       fi
     fi
     # iOS: an Xcode scheme file named <flavor>.xcscheme under the project or workspace shared data.
     if [ "$FLAVOR_DETECTED" = missing ]; then
       if ls "$WT"/ios/*.xcodeproj/xcshareddata/xcschemes/"$FLAVOR".xcscheme >/dev/null 2>&1 \
          || ls "$WT"/ios/*.xcworkspace/xcshareddata/xcschemes/"$FLAVOR".xcscheme >/dev/null 2>&1 \
          || ls "$WT"/ios/Runner.xcodeproj/xcshareddata/xcschemes/"$FLAVOR".xcscheme >/dev/null 2>&1; then
         FLAVOR_DETECTED=detected
       fi
     fi
     [ "$FLAVOR_DETECTED" = missing ] && \
       echo "WARN: flavor '$FLAVOR' not found in this repo (no matching Android productFlavor or iOS scheme) — /ui-tweak:preview will build WITHOUT --flavor." >&2
   fi
   printf '%s\n%s\n' "$FLAVOR" "$FLAVOR_DETECTED" > .dev/ui-tweak/flavor   # worktree-local; preview reads this
   ```

## Not the ticket lifecycle

Explicitly do **NOT** call `/_ticket-init` — that is `/dev:start`'s ticket lifecycle. `/ui-tweak:*`
keeps ticket reads read-only.

## Failure / HITL / `--auto`

No destructive action; failure → STOP, leave no `worktree-ready` marker (only write it after
`/add-worktree` succeeds, so a failed split never looks done to `/ui-tweak:ff` Step 0). `/add-worktree`
owns its own dirty-tree warning and existing-branch / existing-worktree prompts (default); `--auto`
passes through to `/add-worktree` (enter the existing worktree instead of asking).

## Stop

Print: `Worktree ready for <ticket-id>. Next: /ui-tweak:apply.`
