// linear-to-github-dispatch — Cloudflare Worker relay
//
// Receives Linear webhook on ticket state transition. When the transition
// matches the configured filter (assignee = ASSIGNEE_LINEAR_EMAIL, label =
// "Bug", state = "In Progress", team = "CAF"), POSTs a `repository_dispatch`
// event to the ggx-cloud-worker GitHub workflow with the ticket data baked
// into client_payload.
//
// Secrets (configured via `wrangler secret put`):
//   - LINEAR_WEBHOOK_SECRET — HMAC SHA-256 secret from Linear webhook config
//   - GH_PAT                — GitHub PAT with `repo` scope on GH_REPO
//
// Vars (in wrangler.toml [vars]):
//   - GH_REPO                  — e.g. "charlie-yang-gogox/gogox-claude"
//   - ASSIGNEE_LINEAR_EMAIL   — only fire when ticket assignee matches
//   - ALLOWED_TEAM_KEY         — e.g. "CAF"
//   - REQUIRED_LABEL           — e.g. "Bug"
//   - TRIGGER_STATE_NAME       — e.g. "In Progress"

export default {
  async fetch(request, env, ctx) {
    if (request.method !== 'POST') {
      return new Response('Method not allowed', { status: 405 });
    }

    const bodyText = await request.text();

    // 1. Verify Linear webhook HMAC signature
    const signature = request.headers.get('linear-signature');
    if (!signature) {
      return new Response('missing linear-signature header', { status: 401 });
    }
    const expected = await hmacSha256Hex(env.LINEAR_WEBHOOK_SECRET, bodyText);
    if (!timingSafeEqual(signature, expected)) {
      return new Response('invalid linear-signature', { status: 401 });
    }

    let payload;
    try {
      payload = JSON.parse(bodyText);
    } catch {
      return new Response('invalid JSON body', { status: 400 });
    }

    // 2. Filter: only act on Issue update events
    if (payload.type !== 'Issue' || payload.action !== 'update') {
      return new Response('ignored: not an Issue update event', { status: 200 });
    }

    const issue = payload.data || {};
    const reasons = [];

    // 2a. Status must match TRIGGER_STATE_NAME (default "In Progress")
    const targetState = env.TRIGGER_STATE_NAME || 'In Progress';
    if ((issue.state?.name || '') !== targetState) {
      reasons.push(`state "${issue.state?.name}" != "${targetState}"`);
    }

    // 2b. Must have REQUIRED_LABEL (default "Bug")
    const requiredLabel = env.REQUIRED_LABEL || 'Bug';
    const labels = (issue.labels?.nodes || issue.labels || []).map(l => l.name || l);
    if (!labels.includes(requiredLabel)) {
      reasons.push(`label "${requiredLabel}" not present (have: ${labels.join(',')})`);
    }

    // 2c. Team must match ALLOWED_TEAM_KEY (default "CAF")
    const teamKey = issue.team?.key || '';
    const allowedTeam = env.ALLOWED_TEAM_KEY || 'CAF';
    if (teamKey !== allowedTeam) {
      reasons.push(`team "${teamKey}" != "${allowedTeam}"`);
    }

    // 2d. Assignee email must match (the critical filter — prevents other
    //     team members' ticket moves from firing this worker).
    const expectedAssignee = (env.ASSIGNEE_LINEAR_EMAIL || '').toLowerCase();
    const actualAssignee = (issue.assignee?.email || '').toLowerCase();
    if (!expectedAssignee || actualAssignee !== expectedAssignee) {
      reasons.push(`assignee "${actualAssignee}" != "${expectedAssignee}"`);
    }

    // 2e. Only react to fresh transitions INTO target state.
    //     Linear webhook payload includes `updatedFrom`; if state field
    //     wasn't part of the update, this isn't a state-change event.
    const stateChanged =
      payload.updatedFrom &&
      Object.prototype.hasOwnProperty.call(payload.updatedFrom, 'stateId');
    if (!stateChanged) {
      reasons.push('not a state transition');
    }

    if (reasons.length > 0) {
      return new Response(`ignored: ${reasons.join('; ')}`, { status: 200 });
    }

    // 3. POST repository_dispatch to GitHub
    const dispatchBody = {
      event_type: 'linear-bug-fix',
      client_payload: {
        ticket_id: issue.identifier,
        ticket_title: issue.title || '',
        ticket_description: issue.description || '',
        // pass-through for debugging — workflow can ignore
        linear_url: issue.url || '',
        triggered_by: actualAssignee,
      },
    };

    const ghResp = await fetch(
      `https://api.github.com/repos/${env.GH_REPO}/dispatches`,
      {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${env.GH_PAT}`,
          'Accept': 'application/vnd.github+json',
          'X-GitHub-Api-Version': '2022-11-28',
          'User-Agent': 'ggx-linear-webhook-relay/1.0',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(dispatchBody),
      }
    );

    if (!ghResp.ok) {
      const err = await ghResp.text();
      console.error('GitHub dispatch failed', ghResp.status, err);
      return new Response(`gh dispatch failed: ${ghResp.status}`, { status: 502 });
    }

    return new Response(`triggered ggx-cloud-worker for ${issue.identifier}`, {
      status: 200,
    });
  },
};

// HMAC-SHA256 hex digest
async function hmacSha256Hex(secret, message) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    enc.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  );
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(message));
  return Array.from(new Uint8Array(sig))
    .map(b => b.toString(16).padStart(2, '0'))
    .join('');
}

// Timing-safe string compare (for signature verification)
function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}
