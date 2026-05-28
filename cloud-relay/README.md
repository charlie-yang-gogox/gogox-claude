# Linear → GitHub Dispatch Relay (Cloudflare Worker)

A 100-line Cloudflare Worker that:

1. Receives Linear webhook on ticket update
2. Verifies the HMAC signature
3. Filters: team = CAF, label = Bug, state = "In Progress", assignee = configured user
4. POSTs a `repository_dispatch` event to the `ggx-cloud-worker` GitHub workflow
5. The workflow then runs the cloud bug-fix flow and opens a PR

This is the final piece of the **Linear ticket move → cloud bug worker → PR** pipeline. Without this relay, the workflow can only be triggered manually via `workflow_dispatch`.

## Architecture

```
Linear ticket → moves to "In Progress"
  ↓ (Linear webhook POST)
Cloudflare Worker (this directory)
  ↓ verify signature, filter, transform
  ↓ POST repository_dispatch
GitHub Actions workflow (ggx-cloud-worker.yml)
  ↓ claude-code-base-action with ticket data
PR opened on gogovan/gogox-client-flutter
```

## One-time deployment (≈ 5 minutes)

### 1. Install wrangler

```bash
npm install -g wrangler
wrangler --version    # should print 4.x or 3.x
```

### 2. Sign in to Cloudflare

```bash
wrangler login
# opens browser → sign up (free tier, no credit card required) → authorize
```

### 3. Configure secrets

```bash
cd cloud-relay

# 3a. Linear webhook secret (will be generated in step 4 — placeholder for now)
wrangler secret put LINEAR_WEBHOOK_SECRET
# paste any random string for now; we'll regenerate after Linear webhook is created

# 3b. GitHub PAT (re-use the one already in GitHub secrets, or copy local gh token)
gh auth token | wrangler secret put GH_PAT
```

### 4. Deploy

```bash
wrangler deploy
```

Output will show a URL like `https://ggx-linear-to-github-dispatch.<your-subdomain>.workers.dev`. **Copy this URL** — you'll need it in Linear.

### 5. Configure Linear webhook

1. Open https://linear.app/gogox/settings/api/webhooks
2. **New webhook**:
   - **URL**: paste the Worker URL from step 4
   - **Resource types**: tick `Issues`
   - **Team**: select `CA Flutter Revamp`
   - **Label** (if available): `Bug`
3. Save → Linear shows the webhook's **signing secret**
4. Copy that secret, then:
   ```bash
   wrangler secret put LINEAR_WEBHOOK_SECRET
   # paste the Linear signing secret
   ```
   This replaces the placeholder from step 3a.

### 6. Test

Move a CAF Bug ticket to "In Progress" in Linear. Within 30 seconds:
- Worker logs (`wrangler tail`) should show `triggered ggx-cloud-worker for CAF-XXX`
- GitHub Actions tab on `charlie-yang-gogox/gogox-claude` should show a new `linear-bug-fix` run
- After ~10 minutes a PR should appear on `gogovan/gogox-client-flutter`

## Trouble-shooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Worker returns `401 invalid linear-signature` | wrong `LINEAR_WEBHOOK_SECRET` | re-run `wrangler secret put LINEAR_WEBHOOK_SECRET` with the value Linear shows |
| Worker returns `200 ignored: ...` | filter mismatch (wrong team / no Bug label / wrong assignee) | check the body of the 200 response — it lists which filter blocked |
| Worker returns `502 gh dispatch failed: 401` | `GH_PAT` is missing or revoked | re-run `gh auth token \| wrangler secret put GH_PAT` |
| Worker returns `502 gh dispatch failed: 404` | wrong `GH_REPO` or PAT lacks access | edit `wrangler.toml` GH_REPO, redeploy |
| Worker fires but no workflow run | GitHub repo has no `ggx-cloud-worker.yml` on default branch | confirm the workflow file is on `main` |
| Workflow runs but Claude opens wrong PR | ticket description didn't reach payload | tail Worker logs, check the dispatch JSON body |

## Updating the filter

Edit `[vars]` in `wrangler.toml`, then `wrangler deploy`. No secret changes needed.

## Cost

Cloudflare Workers free tier: 100,000 requests / day. Even if Linear sends 10 webhooks / hour, we're using < 1% of the free tier.
