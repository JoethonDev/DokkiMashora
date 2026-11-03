// DokkiMashora supervised demo gateway. No secrets belong in this source.
// Activation is an explicit POST guarded by a secret; GET never starts billing.
const PUBLIC_HOST = 'demo.joethon.dev';
const ORIGIN_HOST = 'dokkimashora-origin.joethon.dev';
const REPOSITORY = 'JoethonDev/DokkiMashora';
const START_PATH = '/__demo/activate';
const STATUS_PATH = '/__demo/status';
const OFFLINE_STATUSES = new Set([502, 503, 504, 521, 522, 523, 530]);

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' }
  });
}

function equalSecret(provided, actual) {
  if (typeof provided !== 'string' || typeof actual !== 'string') return false;
  const a = new TextEncoder().encode(provided);
  const b = new TextEncoder().encode(actual);
  let mismatch = a.length ^ b.length;
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    mismatch |= (a[i] || 0) ^ (b[i] || 0);
  }
  return mismatch === 0 && a.length >= 20;
}

function offlinePage() {
  const html = [
    '<!doctype html><html lang="en"><head><meta charset="utf-8">',
    '<meta name="viewport" content="width=device-width,initial-scale=1">',
    '<meta name="robots" content="noindex,nofollow"><title>DokkiMashora Demo · Offline</title>',
    '<style>body{margin:0;background:#f4f6fa;color:#17243b;font:16px system-ui,-apple-system,sans-serif}',
    'main{max-width:540px;margin:8vh auto;background:#fff;border:1px solid #e4e8f0;border-radius:18px;padding:34px;',
    'box-shadow:0 12px 38px #152b4b0a}h1{font-size:27px;line-height:1.2}p{line-height:1.6;color:#556074}',
    'label{display:block;font-size:14px;font-weight:650;margin:24px 0 7px}input{box-sizing:border-box;width:100%;',
    'padding:13px;border:1px solid #b9c4d5;border-radius:9px;font:inherit}button{background:#1f5379;color:#fff;',
    'border:0;border-radius:9px;padding:13px 22px;font:inherit;font-weight:650;margin-top:13px;cursor:pointer}',
    'button:disabled{opacity:.6;cursor:wait}small{color:#707b89}#message{min-height:1.4em;margin-top:16px}',
    '</style></head><body><main><small>DokkiMashora · Temporary development demo</small>',
    '<h1>The demo is currently stopped</h1>',
    '<p>الديمو متوقف حالياً. يمكن للمسؤول تشغيله باستخدام رمز التفعيل.</p>',
    '<p>This is a supervised demonstration, not an always-on website. Only an authorized operator can start the Codespace.</p>',
    '<form id="activate"><label for="key">Activation passphrase</label>',
    '<input id="key" type="password" minlength="20" autocomplete="off" required>',
    '<button id="start" type="submit">Activate demo</button></form>',
    '<p id="message" role="status" aria-live="polite">Checking availability…</p>',
    '<small>The page checks for availability but never starts the environment without an authorized click.</small>',
    '</main><script>',
    'const form=document.getElementById("activate"),msg=document.getElementById("message"),btn=document.getElementById("start");',
    'async function check(){try{const r=await fetch("/__demo/status",{cache:"no-store"});const x=await r.json();',
    'if(x.ready){location.replace("/");return;}if(!btn.disabled)msg.textContent="Offline · Activation required";',
    '}catch(e){if(!btn.disabled)msg.textContent="Unable to check demo status";}}',
    'form.addEventListener("submit",async(e)=>{e.preventDefault();btn.disabled=true;msg.textContent="Requesting activation…";',
    'const key=document.getElementById("key").value;document.getElementById("key").value="";',
    'try{const r=await fetch("/__demo/activate",{method:"POST",headers:{"content-type":"application/json"},',
    'body:JSON.stringify({key})});const x=await r.json();msg.textContent=x.message||"Activation unavailable";',
    '}catch(e){msg.textContent="Unable to contact activation service";}finally{btn.disabled=false;}});',
    'setInterval(check,5000);check();',
    '</script></body></html>'
  ].join('');
  return new Response(html, { status: 503, headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store', 'x-robots-tag': 'noindex,nofollow', 'retry-after': '10' } });
}

async function originReady() {
  try {
    const response = await fetch('https://' + ORIGIN_HOST + '/healthz', {
      headers: { 'cache-control': 'no-cache' },
      redirect: 'manual',
      cf: { cacheTtl: 0 }
    });
    return response.status === 200;
  } catch {
    return false;
  }
}

function githubHeaders(token) {
  return {
    accept: 'application/vnd.github+json',
    authorization: 'Bearer ' + token,
    'x-github-api-version': '2022-11-28',
    'user-agent': 'dokkimashora-supervised-demo-gateway'
  };
}

async function codespaceName(env) {
  if (env.CODESPACE_NAME) return { name: env.CODESPACE_NAME };
  const response = await fetch('https://api.github.com/user/codespaces?per_page=100', {
    headers: githubHeaders(env.GITHUB_TOKEN)
  });
  if (!response.ok) return { error: 'GitHub Codespaces listing failed. Check token permissions.', status: 502 };
  const payload = await response.json();
  const matches = (payload.codespaces || []).filter(c => c.repository && c.repository.full_name === REPOSITORY);
  if (matches.length !== 1) {
    return { error: 'Expected exactly one Codespace for this repository. Set CODESPACE_NAME on the Worker when multiple Codespaces exist.', status: 409 };
  }
  return { name: matches[0].name };
}

async function startCodespace(request, env) {
  if (request.headers.get('origin') && request.headers.get('origin') !== 'https://' + PUBLIC_HOST) {
    return json({ message: 'Unauthorized origin.' }, 403);
  }
  if (!env.GITHUB_TOKEN || !env.ACTIVATION_KEY) return json({ message: 'Activation has not been configured by the demo operator.' }, 503);
  if (Number(request.headers.get('content-length') || 0) > 2048) return json({ message: 'Request too large.' }, 413);
  let body;
  try { body = await request.json(); } catch { return json({ message: 'Invalid activation request.' }, 400); }
  if (!equalSecret(body.key, env.ACTIVATION_KEY)) return json({ message: 'Incorrect activation passphrase.' }, 403);
  const target = await codespaceName(env);
  if (target.error) return json({ message: target.error }, target.status);
  const response = await fetch('https://api.github.com/user/codespaces/' + encodeURIComponent(target.name) + '/start', {
    method: 'POST',
    headers: githubHeaders(env.GITHUB_TOKEN)
  });
  if (response.ok || response.status === 409 || response.status === 304) {
    return json({ message: 'Codespace start requested. The page will open the demo when it becomes ready.' }, 202);
  }
  if (response.status === 401 || response.status === 403) return json({ message: 'GitHub refused activation. Review the Codespaces lifecycle permission.' }, 502);
  return json({ message: 'GitHub could not start the Codespace (HTTP ' + response.status + ').' }, 502);
}

async function proxy(request) {
  const target = new URL(request.url);
  target.hostname = ORIGIN_HOST;
  target.protocol = 'https:';
  const headers = new Headers(request.headers);
  headers.delete('host');
  headers.delete('x-forwarded-host');
  const upstream = new Request(target, { method: request.method, headers, body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.body, redirect: 'manual' });
  let response;
  try {
    response = await fetch(upstream, { cf: { cacheTtl: 0 } });
  } catch {
    return offlinePage();
  }
  if (OFFLINE_STATUSES.has(response.status)) {
    const accepts = request.headers.get('accept') || '';
    return request.method === 'GET' && (accepts.includes('text/html') || new URL(request.url).pathname === '/')
      ? offlinePage()
      : new Response('Demo temporarily unavailable', { status: 503, headers: { 'cache-control': 'no-store' } });
  }
  const resultHeaders = new Headers(response.headers);
  const location = resultHeaders.get('location');
  if (location) {
    try {
      const redirect = new URL(location, 'https://' + ORIGIN_HOST);
      if ([PUBLIC_HOST, ORIGIN_HOST].includes(redirect.hostname)) {
        redirect.hostname = PUBLIC_HOST;
        redirect.protocol = 'https:';
        resultHeaders.set('location', redirect.href);
      }
    } catch {}
  }
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers: resultHeaders });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.hostname !== PUBLIC_HOST) return new Response('Not found', { status: 404 });
    if (url.pathname === STATUS_PATH) {
      if (request.method !== 'GET') return json({ message: 'Method not allowed' }, 405);
      return json({ ready: await originReady() });
    }
    if (url.pathname === START_PATH) {
      if (request.method !== 'POST') return json({ message: 'Method not allowed' }, 405);
      return startCodespace(request, env);
    }
    return proxy(request);
  }
};
