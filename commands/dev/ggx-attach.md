---
name: ggx-attach
argument-hint: "<TICKET|PR> [--files <spec...>] [--from <dir>] [--force] [--no-pr] [--no-caption] [--ns <namespace>]"
description: >
  Publish USER-SUPPLIED screenshots/videos to a Linear ticket as ONE marked,
  inline-rendered (autoplay) comment — never a download-card attachment — and
  mirror them into the PR body's demo region ONLY (the <!-- ui-tweak-demo -->
  block; adds one if absent; touches nothing else in the body). The reusable
  demo-publish engine: /ggx-demo delegates its whole upload+embed back-half here.
  Idempotent via the comment marker + the PR-body marker region (replace-between,
  never append/EOF). Does NOT capture (no device) and does NOT change ship state.
  PR links are plain links (Linear assets 401 on GitHub). Fail-LOUD. Linear + GitHub.
---

<!-- RULE: command content is English. -->

# `/ggx-attach <TICKET|PR>` — the asset-publish atom (demo-publish engine)

> **One sentence**: idempotently publish a keyed set of EXISTING asset files as the ticket's
> Linear inline demo comment, and mirror them into its open PR's demo region; return the assetUrls.
>
> Two write surfaces, with the PR one strictly narrowed:
> - **Linear** — the canonical host. ONE marked inline comment per namespace (see Attach core).
> - **PR body** — the demo region ONLY (the `<!-- ui-tweak-demo -->` marker block wrapping `## Demo`);
>   if the PR has none, add one. **Everything else in the PR body is untouchable.**
>
> **Write order = Linear first (canonical), then the PR mirror.** All uploads must succeed before the
> Linear comment is written (all-or-nothing); the PR region is written only after Linear succeeded. A PR
> write failure is one LOUD line — Linear is already durable, and an idempotent re-run fills in the PR.
>
> **Out of scope (explicitly NOT done here)**: capture / device work, any part of the PR body outside
> the demo region, ship state (labels / status / assignee / PR open-close-merge), source edits, and
> deciding WHICH files to publish (the caller or the `--files`/scan input decides).
>
> **Presentation decision (inline comment, not attachment card).** Assets are published as `![](assetUrl)`
> inside a comment — images render inline and videos autoplay in the Linear web UI (verified on DAF-465).
> The legacy `create_attachment_from_upload` path produces a download CARD (no autoplay) and is NOT used
> here — this command never calls it.

## Usage

- `/ggx-attach <TICKET>` — no `--files`: auto-scan `~/Desktop/<TICKET>-*.{png,jpg,jpeg,gif,mp4,mov}`
  (the user's filename convention); resolve the ticket's open PR and mirror there too.
- `/ggx-attach <TICKET> --files a.png b.mp4 …` — an explicit file list.
- `/ggx-attach <PR#|URL> --files …` — given a PR, reverse-derive the ticket id.
- `/ggx-attach <TICKET> --from <dir>` — scan `<dir>` instead of the Desktop.
- `--force` — **replace mode**: re-upload same-key assets and replace them (the `assetUrl` changes;
  the comment AND the PR region are rewritten in step). Without the flag, an existing key is SKIPped.
- `--no-pr` — write Linear only; never touch the PR.
- `--no-caption` — disable the agent auto-caption layer (layer 2 below); use only explicit / filename /
  key captions. For mechanical batches that should not spend tokens reading images.
- `--ns <namespace>` — the comment-marker / key namespace (default `manual`; `/ggx-demo` and the
  ui-tweak `pr` stage pass `ui-tweak-demo`).
- Explicit captions: `--files "<caption>::<key>=<path>"`, or a sidecar manifest `<TICKET>-demos.md`
  (see Caption resolution below).

## Files → keyed set (with captions)

The input resolves to `[{key, caption, description?, path}]`:

- **key** — a stable, sortable kebab slug (dedupe / file pairing; not human-facing). Derived by default
  from the `<NN>-<name>` filename segment: `DAF-1234-03-OrderCard.png` → `03-order-card`; with no `NN`
  prefix, slugify the whole basename.
- **caption** — the human-visible "what this demo is" title. Layered resolution below.
- **description** (optional) — one supplementary sentence under the caption.
- **Ordering** — ascending by the key's `NN` prefix; non-prefixed keys lexicographic after them.
- Images and videos are treated identically; the only difference is the PUT `content-type`.
- Extension whitelist: `png jpg jpeg gif mp4 mov`.

### Caption resolution (first hit wins)

1. **Explicit (highest priority)**:
   - inline: `--files "Order card — empty state::03=03.png"` (`caption::key=path`);
   - sidecar manifest `~/Desktop/<TICKET>-demos.md` (or `<from-dir>/<TICKET>-demos.md`), one line per
     file, `<key-or-filename> = <caption>[：<description>]`, e.g.:
     ```
     03-order-card = Order card — empty state：無單時顯示 retry CTA
     05 = Empty state illustration
     ```
     Match by key or filename; a line matching no file is ignored with one WARN line.
2. **Agent auto-caption (the default)**: for every file with no explicit caption, **you (the LLM
   executing this skill) Read the screenshot** (for a video, sample its first/mid/last frame) plus the
   `.dev` context / ticket title+description, and write one clear "what screen / flow this shows"
   sentence as the caption (add a one-line description when it helps). This is the default so that zero
   extra input still yields a readable demo. Print `auto-captioned` per such file in the summary so the
   user can proofread afterwards.
3. **Filename humanize**: `<name>` humanized (`OrderCard` → `Order Card`).
4. **Bare key**: when all else fails, the key itself.

`--no-caption` disables layer 2 only (layers 1/3/4 still apply). Captions are written in Traditional
Chinese or English following the ticket's language context.

## Steps

```
Step 0 — env gate: the Linear MCP must be reachable; gh must be authenticated unless --no-pr.
         A hard failure of either = fail-LOUD, non-zero exit.
Step 1 — resolve target:
   - PR form (#NNN / URL / bare number) → use it directly; reverse-derive TICKET_ID from
     headRefName ([A-Z]+-[0-9]+ grep).
   - TICKET form (^[A-Z]+-[0-9]+) → find the open PR by head branch:
     `gh pr list --search <TICKET> --state open --json number,headRefName`
     (NEVER `gh pr view <ticket-id>` — the branch is <prefix>/<id>, not the id).
   - --no-pr, or no open PR found → skip the PR leg (Linear only); NOT a failure (one note line).
Step 2 — collect + validate assets: build the keyed set per the section above; every file must
         exist, be non-empty, and match the extension whitelist; an empty set = fail-LOUD.
Step 3 — run the Attach core (below) with { ticket_id, pr?, keyed_set, namespace (default
         `manual`), force, sha? } (sha stays empty for manual use; demo callers pass it).
Step 4 — summary: uploaded/skipped/replaced counts + the Linear comment link + PR # (if any)
         + the assetUrls map (+ `auto-captioned` marks per layer-2 caption).
```

Writes are limited to: the marked Linear comment + the PR-body demo region. **No ship state changes.**

## Attach core (single source of truth — shared demo-publish engine)

> Shared verbatim by `/ggx-attach` itself, `/ggx-demo` Step 4, and the ui-tweak `pr` stage
> (`commands/design/ui-tweak/ff.md` "Deliver PR body"). This section REPLACED the former `ff.md`
> "Idempotent attach" contract — do NOT re-derive any of it in a caller.

### Inputs / outputs

- **Input**: `{ ticket_id, pr?, keyed_set:[{key,caption,description?,path}], namespace, force, sha? }`
- **Output (structured, for the caller to parse)**:
  ```
  { uploaded:[{key,assetUrl}], skipped:[key], replaced:[key], failed:[{key,reason}],
    comment_url, pr_number?, assetUrls:{key->assetUrl} }
  ```
- `pr` absent → the PR leg is skipped entirely (the ui-tweak `pr` stage calls the core BEFORE its PR
  exists and embeds the returned `assetUrls` in the body it pre-builds; `/ggx-attach --no-pr` is the
  same shape).

### Upload mechanics (agent-inline, F19 — same as the proven `/ggx-demo` path)

Per file, serially:

1. `prepare_attachment_upload(issue=ticket_id, filename, contentType, size)` → a clean base `assetUrl`
   (`https://uploads.linear.app/<team>/<a>/<b>`, no query) + a signed `uploadRequest`.
2. **Immediately** `curl -X PUT --data-binary @<path>` against `uploadRequest.url`, sending EVERY
   `uploadRequest.headers` entry **verbatim** (`content-type`, `cache-control`,
   `x-goog-content-length-range: N,N`, `Content-Disposition`) — one missing or case-mangled header is a
   signed-URL signature mismatch (403). The signed URL expires in ~60s: prepare and PUT in the same
   breath, never prepare early.
3. **Do NOT call `create_attachment_from_upload`** — skip the finalize. That call is what creates the
   download card; skipping it is what keeps the asset inline-only.
4. Keep the clean base `assetUrl` (no `?signature=…`).

Batching: one file at a time (prepare → PUT → next), at most 3 signed PUTs per burst.

> **Why the asset survives without finalize**: Linear stores only the asset reference and re-signs a
> ~5-minute GCS URL on every fetch, so the web UI inlines it on every load. The `?signature=…&exp=…`
> seen when reading the comment back via `list_comments` is a per-fetch representation, not a stored
> value (two consecutive reads show an advancing `iat`). Verified on DAF-465.

### Linear inline comment (canonical presentation)

ONE marked comment per namespace:

```
<!-- ggx-attach:<namespace> -->
<!-- ggx-attach-keys: 03-order-card,05-empty-state -->
<!-- ggx-attach-sha: <sha-or-empty> -->
### Order card — empty state
無單時顯示 retry CTA。
![Order card — empty state](<assetUrl-1>)

### Empty state illustration
![Empty state illustration](<assetUrl-2>)
<!-- /ggx-attach:<namespace> -->
```

- Per key: one `### <caption>` heading + (optional) one description line + `![caption](assetUrl)`.
- `ggx-attach-keys` / `ggx-attach-sha` are machine-readable meta (dedupe + batch discovery).
  `sha` is passed by a demo caller (`/ggx-demo`, ui-tweak `pr`); manual use leaves it empty.
- Videos use the same `![caption](assetUrl)` markdown — with the PUT's `content-type: video/mp4` the
  Linear web UI renders an inline player (first-run verification item 2 below).

### Linear idempotency (marker-based)

1. `list_comments(ticket_id)` and look for `<!-- ggx-attach:<namespace> -->`.
2. Absent → `save_comment` a new comment with the full block.
3. Present → merge per key:
   - existing key, **no `--force`, and the passed `sha` matches the stored `ggx-attach-sha`** (or both
     are empty) → **SKIP** (reuse the existing assetUrl);
   - existing key with `--force`, **or with a passed non-empty `sha` that differs from the stored
     `ggx-attach-sha`** → **REPLACE** (re-upload; the assetUrl changes) — a stale-sha demo is a demo of
     the WRONG commit, so a sha mismatch takes the replace path even without `--force` (this keeps the
     semantics of the retired title-embeds-sha scheme, where a new sha never title-matched);
   - new key → merge in.
   Rewrite the two meta lines (`ggx-attach-keys` = the merged key set, `ggx-attach-sha` = the passed
   sha) and update the comment in place.
4. **In-place update**: prefer `save_comment` with the existing comment's id to update its body
   (first-run verification item 1 below); if the MCP does not support update-by-id, fall back to
   `delete_comment` the old one → `save_comment` the merged full content.

### All-or-nothing + failure disposition

- ALL uploads must succeed before the Linear comment is written; any file failing → fail-LOUD, never a
  half-written comment.
- The PR region is written only AFTER the Linear write succeeded; a PR write failure = one LOUD line,
  but Linear is already durable — an idempotent re-run completes the PR side.
- **Standalone invocation** fails LOUD (R13): one deterministic stderr line + non-zero exit.
  **Called from a batch**, the core reports success/failure truthfully in its structured output and
  never swallows errors itself — fail-soft wrapping (the `/_slack-notify` contract) is the CALLER's job.

### PR body demo region (strictly surgical — the ff.md marker-region contract, verbatim)

Touch ONLY the demo region. The marker stays the existing **`<!-- ui-tweak-demo -->`** pair (NEVER
rename it — open PRs already carry this marker; a renamed marker would append a second region):

```
<!-- ui-tweak-demo -->
## Demo
- **Order card — empty state**: <assetUrl-1>
- **Empty state illustration**: <assetUrl-2>
<!-- /ui-tweak-demo -->
```

(Bullets are titled by caption; a description, when present, follows the link as ` — <description>`.
A SINGLE key renders as one unlabeled plain link — backward-compatible with pre-existing demos.)

Algorithm (the ff.md PR-body contract, carried over verbatim):

1. `gh pr view <pr> --json body -q .body` to read the current body.
2. **Marker pair present** → replace ONLY the text between the markers (the closing
   `<!-- /ui-tweak-demo -->` is the hard boundary).
3. **Else an UNMARKED `## Demo` exists** (the `/pull-request` template one, placeholder included) →
   replace that section AND wrap it in the markers, **bounded at the next `## ` heading or end-of-body
   — NEVER replace-to-EOF (E15)**: a mid-body `## Demo` with sections after it would otherwise have its
   siblings silently eaten.
4. **Neither** → append one marked block.
5. `gh pr edit <pr> --body-file <abs-path>`, run **from inside the repo directory with an absolute
   `--body-file` path (F17)** — the Bash tool's cwd resets between calls.

- **PR links are ALWAYS plain links** (a Linear `assetUrl` is a deterministic 401 to GitHub's image
  proxy on this private repo — never `![]()` in the PR body); the inline render lives on Linear only.
- This is the ONLY PR-body write: a read-modify-write of ONE region. It preserves reviewer edits
  elsewhere in the body, and re-runs can never stack a second `## Demo`.

## First-run verification (report a mismatch, do not silently improvise)

These three facts were specified from the proven `/ggx-demo` path + DAF-465 but must be confirmed the
first time this command runs against live Linear MCP; on a mismatch, STOP and report rather than
improvising a workaround:

1. **`save_comment` update-by-id** — can it update an existing comment's body? Decides in-place update
   vs the delete-and-repost fallback (Linear idempotency step 4).
2. **Video inline render** — after a PUT with `content-type: video/mp4`, does `![](assetUrl)` in a
   comment render an inline player? If a different syntax is needed, branch the comment template for
   videos.
3. **`prepare_attachment_upload` response shape** — clean base `assetUrl` + the `uploadRequest.headers`
   key set (expected to match the current `/ggx-demo`-verified shape).

## Non-goals / constraints

- No capture, no ship-state changes, no source edits.
- PR body: ONLY the demo region (`<!-- ui-tweak-demo -->`); add one if absent; touch nothing else.
- Presentation is the inline comment — never a download-card attachment; PR side is always plain links.
- Idempotent: a re-run adds no duplicate comment and never stacks a second `## Demo`; per-key replace
  only on `--force` or a sha mismatch (a passed sha differing from the stored one).
- Upload = agent-inline (same as `/ggx-demo`), no new backing script; F19 verbatim headers + the ~60s
  signed-URL window.
- Linear first (canonical), PR is the mirror; a PR failure is LOUD but never rolls back Linear
  (a re-run completes it).
- Linear + GitHub only.
