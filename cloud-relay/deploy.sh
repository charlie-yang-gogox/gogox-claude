#!/usr/bin/env bash
# One-shot Cloudflare Worker deploy for the Linear→GitHub dispatch relay.
# Run from cloud-relay/ directory after `wrangler login` succeeds.
#
# Usage: bash deploy.sh
#
# Requires:
#   - wrangler authenticated (run `wrangler login` first)
#   - gh CLI authenticated (already set up — uses your gh token as GH_PAT)
#   - LINEAR_WEBHOOK_SECRET via stdin (script will prompt — paste OR press Enter
#     to use a placeholder; you can rotate this after Linear creates its webhook)

set -e

cd "$(dirname "$0")"

echo "=== 1. Verify wrangler auth ==="
if ! wrangler whoami 2>&1 | grep -q "Account"; then
  echo "ERROR: wrangler not authenticated. Run: wrangler login"
  exit 1
fi
wrangler whoami 2>&1 | grep -E "(Account|Email|associated)" | head -5

echo
echo "=== 2. Push GH_PAT secret (using your local gh CLI token) ==="
gh auth token | wrangler secret put GH_PAT 2>&1 | tail -3

echo
echo "=== 3. Push LINEAR_WEBHOOK_SECRET ==="
echo "Linear will give you the real signing secret AFTER you create the webhook."
echo "For now we use a placeholder so the worker can deploy; rotate later:"
echo "  wrangler secret put LINEAR_WEBHOOK_SECRET"
echo "(For first deploy, the placeholder is OK — webhook verification will reject"
echo "requests with wrong signature until you rotate.)"
echo "Pushing placeholder..."
echo "placeholder-rotate-after-linear-webhook-created" | wrangler secret put LINEAR_WEBHOOK_SECRET 2>&1 | tail -3

echo
echo "=== 4. Deploy worker ==="
wrangler deploy 2>&1 | tail -15

echo
echo "=== 5. Quick smoke test (without signature — should return 401) ==="
WORKER_URL=$(wrangler deployments list 2>&1 | grep -oE 'https://[^ ]+\.workers\.dev' | head -1)
if [ -n "$WORKER_URL" ]; then
  echo "Worker URL: $WORKER_URL"
  echo
  echo "Test (expect 401 invalid signature, which proves auth check works):"
  curl -sS -X POST "$WORKER_URL" -H "Content-Type: application/json" -d '{"hello": "world"}' -w "\n  HTTP %{http_code}\n"
else
  echo "(Could not auto-detect worker URL — check 'wrangler deployments list')"
fi

echo
echo "=== Done! Next steps ==="
cat <<EOF

1. Open https://linear.app/gogox/settings/api/webhooks → New webhook
   - URL:            ${WORKER_URL:-<worker URL above>}
   - Resource types: Issues
   - Team:           CA Flutter Revamp
   - Save → Linear shows the SIGNING SECRET

2. Copy that signing secret, then rotate the placeholder:
     wrangler secret put LINEAR_WEBHOOK_SECRET
     # paste the Linear signing secret, Enter

3. Test by moving a CAF Bug ticket to "In Progress" (any ticket assigned
   to charlie.yang@gogox.com). Within 30 seconds you should see a new run
   on https://github.com/charlie-yang-gogox/gogox-claude/actions

   Tail worker logs in another terminal:
     wrangler tail
EOF
