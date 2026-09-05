/**
 * FPL Picker — Claude proxy.
 *
 * The site is public, so the Anthropic key cannot live in the browser. This
 * Worker holds it, checks that the caller is the signed-in owner, and streams
 * Claude's answer back. It exists so the app can ask follow-up questions in
 * seconds; the weekly briefing still runs in GitHub Actions, where its prompt
 * is built.
 *
 * Secrets (wrangler secret put NAME):
 *   ANTHROPIC_API_KEY   the API key
 * Vars (wrangler.toml):
 *   FIREBASE_API_KEY    public Firebase web key, used to verify the ID token
 *   ALLOWED_UIDS        comma-separated Firebase uids permitted to spend
 *   ALLOWED_ORIGINS     comma-separated site origins allowed to call this
 */

const MODEL = 'claude-opus-5';
const MAX_TOKENS = 4000;          // fixed here: a compromised page cannot raise the bill
const MAX_BODY = 400_000;         // characters of context accepted per request

function cors(origin, env) {
  const allowed = (env.ALLOWED_ORIGINS || '').split(',').map((s) => s.trim()).filter(Boolean);
  const ok = allowed.includes(origin);
  return {
    'Access-Control-Allow-Origin': ok ? origin : allowed[0] || '',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Max-Age': '86400',
    Vary: 'Origin',
  };
}

/** Confirm the Firebase ID token is real and belongs to someone allowed to spend. */
async function verify(idToken, env) {
  if (!idToken) return { ok: false, why: 'no sign-in token' };
  const r = await fetch(
    `https://identitytoolkit.googleapis.com/v1/accounts:lookup?key=${env.FIREBASE_API_KEY}`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ idToken }) },
  );
  if (!r.ok) return { ok: false, why: 'sign-in token rejected' };
  const data = await r.json();
  const uid = data.users?.[0]?.localId;
  if (!uid) return { ok: false, why: 'sign-in token carried no user' };
  const allowed = (env.ALLOWED_UIDS || '').split(',').map((s) => s.trim()).filter(Boolean);
  if (allowed.length && !allowed.includes(uid)) return { ok: false, why: 'this account is not allowed to use the assistant' };
  return { ok: true, uid };
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get('Origin') || '';
    const headers = cors(origin, env);
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers });
    if (request.method !== 'POST') return new Response('POST only', { status: 405, headers });

    let body;
    try {
      const text = await request.text();
      if (text.length > MAX_BODY) return json({ error: 'request too large' }, 413, headers);
      body = JSON.parse(text);
    } catch {
      return json({ error: 'body was not JSON' }, 400, headers);
    }

    const auth = await verify(body.idToken, env);
    if (!auth.ok) return json({ error: auth.why }, 401, headers);

    const messages = Array.isArray(body.messages) ? body.messages : null;
    if (!messages || !messages.length) return json({ error: 'no messages' }, 400, headers);

    const upstream = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'x-api-key': env.ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: MODEL,
        max_tokens: MAX_TOKENS,
        stream: true,
        thinking: { type: 'adaptive' },
        output_config: { effort: 'medium' },
        system: typeof body.system === 'string' ? body.system : undefined,
        messages,
      }),
    });

    if (!upstream.ok || !upstream.body) {
      const detail = await upstream.text().catch(() => '');
      return json({ error: `Claude returned ${upstream.status}`, detail: detail.slice(0, 500) }, 502, headers);
    }
    // Stream the event source straight through; the page reads the text deltas.
    return new Response(upstream.body, {
      headers: { ...headers, 'Content-Type': 'text/event-stream; charset=utf-8', 'Cache-Control': 'no-store' },
    });
  },
};

function json(obj, status, headers) {
  return new Response(JSON.stringify(obj), { status, headers: { ...headers, 'Content-Type': 'application/json' } });
}
