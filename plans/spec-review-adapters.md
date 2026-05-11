# `/spec-review` — Adapter TODOs (port + dev)

> **Status**: Not yet started. Do **after** `commands/dev/spec-review.md` ships
> and is proven on real tickets. Spec-review's legacy fallback (see the
> "Marker contract" section of `commands/dev/spec-review.md`) keeps it working
> in the meantime.

This file tracks the changes needed in the **adjacent** skills so that the
end-to-end marker contract (defined in `commands/dev/spec-review.md`) is fully
wired.

## Why these are separate

- `/spec-review` is a self-contained skill. It can ship and provide value
  without these adapters (lite/legacy mode handles old tickets).
- The adapters touch other skill families' surface area — different blast radius,
  different review/test cadence.
- Decoupled deployment lets us validate the spec-review UX in isolation before
  perturbing the port and dev pipelines.

---

## TODO-1 — `/port:*` writes v1 auto-accept marker

**Owner skills**: `commands/dev/port/revise.md`, `commands/dev/port/ship.md`

**What to change**

Today both files emit auto-accept lines in the format:
```
- **[AUTO-ACCEPTED] medium** — Risk R-1 (server-side `bookmarked_order` ...)
```

Change to:
```
- <!-- ac:v1 sev=medium -->**[AUTO-ACCEPTED]** medium — Risk R-1 (server-side `bookmarked_order` ...)
```

**Why HTML comment**
- Linear renders HTML comments invisibly but preserves them in the raw markdown,
  so spec-review's regex anchors on a stable token immune to:
  - smart-quote / em-dash auto-replacement
  - markdown list-indent normalization
  - severity casing drift
  - future visible-text format changes
- Carries an explicit version (`v1`) so future format upgrades can be detected.

**Acceptance**
- All new port-summary comments contain at least one `<!-- ac:v1 sev=X -->`
  marker per auto-accept line.
- The visible text format is unchanged (humans still see `[AUTO-ACCEPTED]`).
- Old port comments (without the HTML marker) remain readable — spec-review's
  legacy regex handles them.

**Files to grep before editing**
```
grep -n "AUTO-ACCEPTED\|AUTO_ACCEPTED" commands/dev/port/revise.md commands/dev/port/ship.md
```

---

## TODO-2 — `/dev:apply` reads spec-review override + verifies source_hash

**Owner skill**: `commands/dev/dev/apply.md`

**What to add (high-level)**

Insert a new pre-apply step before the existing artifact-reading logic:

1. `list_comments` on the current ticket.
2. Find the **latest** comment whose body contains `<!-- spec-review:v1 ticket=<id> -->`.
3. If none found:
   - If the ticket has the `ready-to-dev` label, this is allowed (lite-mode
     review with no revisions). Proceed normally.
   - If `ready-to-dev` is missing, abort: `/spec-review` was not run.
4. Parse the comment for `[CONFIRMED]` / `[REVISED]` / `[DEFERRED]` blocks.
5. For each block, re-hash the matching `[AUTO-ACCEPTED]` body in the original
   port summary comment. Compare against `source_hash`:
   - **Match** → continue.
   - **Mismatch** → abort with: `Auto-accept content changed since review.
     Re-run /spec-review to refresh decisions.`
6. Build a directive overlay structure:
   ```
   {
     confirmed: [...],
     revised: [{label, original, directive, hash}, ...],
     deferred: [{label, note, hash}, ...]
   }
   ```
7. When invoking `/opsx:apply` (or the dev-agent in --auto), inject the overlay
   into the prompt with explicit precedence wording: **"`[REVISED]` directives
   are the final decision and override conflicting guidance in OpenSpec
   artifacts. Cite the directive label in commit messages."**

**Why this is load-bearing**

Without this change, `/dev:apply` reads artifacts and ignores the spec-review
comment. Even if the agent happens to fetch comments naturally, it will not
know that `[REVISED]` outranks `proposal.md` / `design.md`. The whole
"post-only, no artifact edits" simplification of `/spec-review` rests on this
adapter landing.

**Acceptance**
- `/dev:apply <ticket>` on a ticket with a `[REVISED]` directive produces code
  consistent with the directive even when the original `design.md` says
  otherwise.
- Commit message and PR body reference the directive label.
- `source_hash` mismatch aborts cleanly with re-review instruction.
- Backwards compatibility: tickets with no `<!-- spec-review:v1 -->` comment
  but with `ready-to-dev` label run unchanged (lite-mode review honored).

---

## TODO-3 — Migration window strategy (no code change, just a note)

For ~2 weeks after TODO-1 ships:
- New tickets carry v1 markers → spec-review uses fast path.
- Older `need-spec-review` tickets carry legacy markers → spec-review uses
  fallback regex (see "Marker contract" in `commands/dev/spec-review.md`),
  prints a one-line warning in the output
  comment.

After the window, audit `need-spec-review` backlog. If any legacy tickets
remain, either re-run `/port:ff` on them or hand-review without the skill.

Once backlog is drained, optionally remove the legacy fallback regex from
spec-review (keep the v1 path only). This is **not urgent** — fallback costs
~10 lines of skill prompt.

---

## Deployment order (do not invert)

```
1. /spec-review.md ships          ← lands first (legacy fallback handles existing tickets)
2. TODO-1: port writes v1 marker  ← new tickets benefit from fast-path
3. TODO-2: dev:apply reads override ← end-to-end contract closed
4. (later) TODO-3: drain legacy backlog
```

Why this order:
- Steps 1-2 reversed would mean spec-review depends on a marker format that
  doesn't exist yet → useless skill.
- Steps 2-3 reversed would mean dev:apply parses a comment format that no port
  yet emits → no behavioral change yet, but the parser sits unused (acceptable
  but wasted).
- Steps 1-3 reversed would mean spec-review writes override comments that nobody
  reads → silent ineffectiveness (this is the current "no /dev:apply parser"
  failure mode that ai-expert flagged).

The chosen order means **at every step, all skills produce useful behavior**.

---

## Open questions for adapter implementation

- TODO-2: how exactly to inject the directive overlay into `/opsx:apply`'s
  prompt? Probably via a `--directives` flag or a temporary file the dev-agent
  reads. Decide when implementing.
- TODO-1: do we also want to add `<!-- ac:v1 -->` to **defer / risk** sections
  of the port summary, or only `[AUTO-ACCEPTED]`? Likely only `[AUTO-ACCEPTED]`
  since that's the only block spec-review consumes.
- Versioning: when does `v1` → `v2`? Reserve this for a marker-format-breaking
  change (e.g. moving severity from HTML attribute to nested JSON). Until then,
  keep extending v1 with optional fields.
