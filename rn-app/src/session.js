// ZerotheBot React-Native client — same REST + SSE API as the web app (service/server.py).
import { useEffect, useMemo, useRef, useState } from 'react';
import EventSource from 'react-native-sse';
import { APP_VERSION, OTA_ID } from './build';

// ------------------------------------------------------------ secure config
let SecureStore = null;
try { SecureStore = require('expo-secure-store'); } catch (e) { /* dev on web */ }

export async function cfgGet(key) {
  try { return SecureStore ? await SecureStore.getItemAsync(key) : localStorage.getItem(key); }
  catch (e) { return null; }
}
export async function cfgSet(key, value) {
  try { SecureStore ? await SecureStore.setItemAsync(key, value) : localStorage.setItem(key, value); } catch (e) {}
}
export async function cfgDel(key) {
  try { SecureStore ? await SecureStore.deleteItemAsync(key) : localStorage.removeItem(key); } catch (e) {}
}

export const K_SERVER = 'zt_server';
export const K_TOKEN = 'zt_token';
export const K_EMAIL = 'zt_email';
export const K_PASS = 'zt_pass';

// ------------------------------------------------------------ session
export let API_BASE = '';
let TOKEN = '';

export async function loadKey() { TOKEN = (await cfgGet(K_TOKEN)) || ''; }

// instabox-port --private requires ?_token= in each request URL (not just the Bearer header).
// API_BASE is kept clean; the token is appended per-request in api().
export async function saveConf(base, token, email, pass) {
  API_BASE = String(base || '').replace(/\/+$/, '');
  TOKEN = String(token || '').trim();
  await cfgSet(K_SERVER, API_BASE);
  await cfgSet(K_TOKEN, TOKEN);
  if (email !== undefined) await cfgSet(K_EMAIL, String(email || '').trim().toLowerCase());
  if (pass !== undefined) await cfgSet(K_PASS, String(pass || ''));
}
export async function clearConf() {
  API_BASE = ''; TOKEN = '';
  await cfgDel(K_SERVER); await cfgDel(K_TOKEN); await cfgDel(K_EMAIL); await cfgDel(K_PASS);
}

export function conf() { return { base: API_BASE, token: TOKEN }; }

// ------------------------------------------------------------ api
export async function api(path, opts = {}) {
  // A request that never answers must not leave a screen waiting forever: the
  // caller can pass { timeout: ms } and get a normal network error instead.
  const { timeout, ...rest } = opts;
  let ctl = null, timer = null;
  if (timeout && typeof AbortController !== 'undefined') {
    ctl = new AbortController();
    timer = setTimeout(() => { try { ctl.abort(); } catch (e) { /* ignore */ } }, timeout);
  }
  const isForm = typeof FormData !== 'undefined' && rest.body instanceof FormData;
  try {
    // instabox-port --private requires ?_token= in each request URL (not just the Bearer header).
    // API_BASE is kept clean; the token is appended per-request in api().
    const sep = API_BASE.includes('?') ? '&' : '?';
    const url = API_BASE + path + sep + '_token=' + encodeURIComponent(TOKEN);
    const r = await fetch(url, {
      ...rest,
      signal: ctl ? ctl.signal : undefined,
      headers: {
        'Authorization': 'Bearer ' + TOKEN,
        // FormData sets its own multipart Content-Type with the boundary.
        ...(isForm ? {} : { 'Content-Type': 'application/json' }),
        // Lets the server log which bundle the phone is really running, so
        // "I updated but nothing changed" is answerable from the server side.
        'X-ZB-App': APP_VERSION,
        'X-ZB-Ota': OTA_ID || 'embedded',
        ...(rest.headers || {}),
      },
    });
    if (r.status === 401) throw new Error('Not authorized');
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || data.detail || ('HTTP ' + r.status));
    return data;
  } catch (e) {
    if (e && (e.name === 'AbortError' || /abort/i.test(String(e.message || '')))) {
      throw new Error('The server took too long to answer.');
    }
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

// Authenticated fetch for wrappers that receive an explicit base URL (login
// flows pass the server the user just typed; everything else falls back to
// the saved API_BASE). Adds the Bearer header + ?_token= the private port needs.
async function authed(base, path, opts = {}) {
  const b = String(base || API_BASE || '').replace(/\/+$/, '');
  const url = b + path;
  const sep = url.includes('?') ? '&' : '?';
  const r = await fetch(url + sep + '_token=' + encodeURIComponent(TOKEN), {
    ...opts,
    headers: { 'Authorization': 'Bearer ' + TOKEN, ...(opts.headers || {}) },
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || data.detail || ('HTTP ' + r.status));
  return data;
}

export async function tryConf(base, token) {          // validate before saving
  const b = String(base || '').replace(/\/+$/, '');
  const r = await fetch(b + '/api/bots', { headers: { Authorization: 'Bearer ' + token } });
  if (r.status !== 200) throw new Error('That token was not accepted.');
  const d = await r.json().catch(() => null);
  if (!d) throw new Error('Server answered but not in the right language.');
  return { ok: true, email: null };
}

// Sign in with the same email + password as the web app (server sets zt_session cookie).
export async function loginEmail(base, email, pass) {
  const b = String(base || '').replace(/\/+$/, '');
  const r = await fetch(b + '/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: String(email || '').trim().toLowerCase(), password: String(pass || '') }) });
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || 'That email or password was not accepted.');
  return { ok: true, email: d.email || email, session: d.session || '' };
}

// Create a new account (first-run only -- the server allows one account per instance).
export async function signupEmail(base, email, pass) {
  const b = String(base || '').replace(/\/+$/, '');
  const r = await fetch(b + '/api/signup', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: String(email || '').trim().toLowerCase(), password: String(pass || '') }) });
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || 'Could not create account.');
  return { ok: true, email: d.email || email, session: d.session || '' };
}

// Check whether the server already has an account (first-run vs sign-in).
export async function authStatus(base) {
  const b = String(base || '').replace(/\/+$/, '');
  const r = await fetch(b + '/api/auth_status', { headers: { 'Content-Type': 'application/json' } });
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error('Could not reach the server.');
  return d;
}

// Current account: email, is_admin, plan, usage this week. Requires a signed session.
export async function me(base) {
  return authed(base, '/api/me');
}

// Admin: list every account with per-user token usage.
export async function adminUsers(base) {
  return authed(base, '/api/admin/users');
}

// Admin: detail for one account.
export async function adminAccount(base, email) {
  return authed(base, '/api/admin/account/' + encodeURIComponent(email));
}

// Admin: server settings (masked secrets).
export async function adminSettings(base) {
  return authed(base, '/api/admin/settings');
}

// Admin: set a user's plan tier.
export async function adminSetPlan(base, email, plan) {
  return authed(base, '/api/admin/account/' + encodeURIComponent(email) + '/plan', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ plan }),
  });
}

// Admin: grant or revoke admin rights.
export async function adminSetAdmin(base, email, isAdmin) {
  return authed(base, '/api/admin/account/' + encodeURIComponent(email) + '/admin', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ is_admin: isAdmin }),
  });
}

// Approval center: Muse-style pending actions with merchant/price/item details.
export async function approvalsCenter(base) {
  return authed(base, '/api/approvals/center');
}

// Decide an approval: approve or deny.
export async function approvalDecide(base, aid, approve, remember = false, note = '') {
  return authed(base, '/api/approvals/' + encodeURIComponent(aid) + '/decide', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ approve, remember, note }),
  });
}

// ---------------------------------------------------------------- plugins & connectors
// List every connector with connection status, fields and tools.
export async function pluginsList(base) {
  return authed(base, '/api/plugins');
}

// Start an OAuth sign-in. Returns { url } — open it in the system browser.
// mobile=1 makes the server's callback deep-link back into the app.
export async function pluginConnect(base, pid, mobile = true) {
  return authed(base, '/api/plugins/' + encodeURIComponent(pid) + '/connect?mobile=' + (mobile ? 1 : 0), { method: 'POST' });
}

// Forget a connector's stored credentials.
export async function pluginDisconnect(base, pid) {
  return authed(base, '/api/plugins/' + encodeURIComponent(pid) + '/disconnect', { method: 'POST' });
}

// Save config fields (client ids, tokens, region overrides...).
export async function pluginConfig(base, pid, values) {
  return authed(base, '/api/plugins/' + encodeURIComponent(pid) + '/config', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values || {}),
  });
}

// Run the plugin's connection test.
export async function pluginTest(base, pid) {
  return authed(base, '/api/plugins/' + encodeURIComponent(pid) + '/test', { method: 'POST' });
}

// ---------------------------------------------------------------- MCP servers
// List configured MCP servers with live status and tool counts.
export async function mcpList(base) {
  return authed(base, '/api/mcp');
}

// Add or update one server, or import many: { import_json: '{"mcpServers": {...}}' }.
export async function mcpSave(base, cfg) {
  return authed(base, '/api/mcp', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cfg),
  });
}

export async function mcpDelete(base, sid) {
  return authed(base, '/api/mcp/' + encodeURIComponent(sid), { method: 'DELETE' });
}

export async function mcpRestart(base, sid) {
  return authed(base, '/api/mcp/' + encodeURIComponent(sid) + '/restart', { method: 'POST' });
}

// ---------------------------------------------------------------- email (IMAP) connector
export async function emailConfigure(base, host, port, username, password, useSsl = true) {
  return authed(base, '/api/email/imap/configure', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ host, port, username, password, use_ssl: useSsl }),
  });
}

export async function emailPoll(base) {
  return authed(base, '/api/email/imap/poll');
}

export async function emailCreateApprovals(base) {
  return authed(base, '/api/email/imap/create-approvals', { method: 'POST' });
}

// ---------------------------------------------------------------- document upload & parsing
export async function documentUpload(base, fileUri, fileName) {
  const form = new FormData();
  form.append('file', {
    uri: fileUri,
    name: fileName,
    type: 'application/pdf',
  });
  return authed(base, '/api/document/upload', { method: 'POST', body: form });
}

// Sign in with Google: the reader app hands the verified Google ID token here
// and the server returns { ok, email, session } usable as the Bearer token.
export async function loginGoogle(base, idToken) {
  const b = String(base || '').replace(/\/+$/, '');
  const r = await fetch(b + '/api/google', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id_token: String(idToken || '') }) });
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || d.detail || 'Google sign-in was not accepted.');
  return { ok: true, email: d.email || '', session: d.session || '' };
}

// ------------------------------------------------------------ live events (SSE)
export function connectEvents({ since, onEvent, onConn, onSince }) {
  let es = null, last = since || 0, stop = false, timer = null;
  const open = () => {
    if (stop) return;
    // instabox-port --private requires ?_token= in each request URL
    const sep = API_BASE.includes('?') ? '&' : '?';
    es = new EventSource(`${API_BASE}/api/events?kind=mobile&since=${last}${sep}_token=${encodeURIComponent(TOKEN)}`, {
      headers: { Authorization: 'Bearer ' + TOKEN },
    });
    es.addEventListener('open', () => onConn(true));
    es.addEventListener('error', () => { onConn(false); es && es.close(); es = null; if (!stop) timer = setTimeout(open, 3000); });
    es.addEventListener('message', (m) => {
      let ev; try { ev = JSON.parse(m.data); } catch (e) { return; }
      if (ev.id && ev.id > last) { last = ev.id; onSince(ev.id); }
      onEvent(ev);
    });
  };
  open();
  return () => { stop = true; clearTimeout(timer); es && es.close(); es = null; };
}

// ---------------------------------------------------------------- browser-in-chat (agent computer use)
// These drive a Bot's embedded browser through /api/browser/*; bot_id selects
// the Bot whose tab is acted on. click takes { element } (snapshot id) or { x, y }.
export async function browserNavigate(base, botId, url) {
  return authed(base, '/api/browser/navigate', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ bot_id: botId, url }),
  });
}

export async function browserClick(base, botId, opts = {}) {
  return authed(base, '/api/browser/click', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ bot_id: botId, ...opts }),
  });
}

export async function browserType(base, botId, text, element = null) {
  return authed(base, '/api/browser/type', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ bot_id: botId, text, element }),
  });
}

export async function browserScreenshot(base, botId) {
  return authed(base, '/api/browser/screenshot?bot_id=' + encodeURIComponent(botId || ''));
}

// ---------------------------------------------------------------- agent-initiated proactive messages
export async function agentMessage(base, text, threadId = '') {
  return authed(base, '/api/agent/message', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, thread_id: threadId }),
  });
}

export async function agentMessages(base) {
  return authed(base, '/api/agent/messages');
}

// ---------------------------------------------------------------- Tier 2: Web search
export async function webSearch(base, query) {
  return authed(base, '/api/search', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ q: query }),
  });
}

// ---------------------------------------------------------------- Tier 2: Image understanding
export async function imageUnderstand(base, imageData, prompt = 'Describe this image in detail.') {
  return authed(base, '/api/image/understand', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image_data: imageData, prompt }),
  });
}

// ---------------------------------------------------------------- Tier 3: Image generation
export async function imageGenerate(base, prompt) {
  return authed(base, '/api/image/generate', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ prompt }),
  });
}

// ---------------------------------------------------------------- Tier 3: Code execution sandbox
export async function codeExecute(base, code) {
  return authed(base, '/api/code/execute', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code }),
  });
}

// ---------------------------------------------------------------- Tier 3: Multi-agent collaboration
export async function agentCollaborate(base, task) {
  return authed(base, '/api/agent/collaborate', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ task }),
  });
}

// ---------------------------------------------------------------- InstaBox Android Boxes
// Bots drive Android on InstaBox machines instead of the user's phone.
// No AccessibilityService needed on the phone. The server authenticates the
// user and attaches its own InstaBox API key — the phone never holds one.
export async function ibCreateMachine(base, name) {
  return authed(base, '/api/instabox/machines', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
  });
}

export async function ibListMachines(base) {
  return authed(base, '/api/instabox/machines');
}

export async function ibGetMachine(base, mid) {
  return authed(base, '/api/instabox/machines/' + encodeURIComponent(mid));
}

export async function ibAdb(base, mid, cmd) {
  return authed(base, '/api/instabox/machines/' + encodeURIComponent(mid) + '/adb', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cmd }),
  });
}

export async function ibDesktopStart(base, mid, width = 1280, height = 800) {
  return authed(base, '/api/instabox/machines/' + encodeURIComponent(mid) + '/desktop/start', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ width, height }),
  });
}

export async function ibDesktopScreenshot(base, mid) {
  return authed(base, '/api/instabox/machines/' + encodeURIComponent(mid) + '/desktop/screenshot');
}

export async function ibDesktopStream(base, mid, viewOnly = true, ttlSeconds = 3600) {
  return authed(base, '/api/instabox/machines/' + encodeURIComponent(mid) + '/desktop/stream', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ viewOnly, ttlSeconds }),
  });
}

export async function ibDesktopClick(base, mid, x, y) {
  return authed(base, '/api/instabox/machines/' + encodeURIComponent(mid) + '/desktop/click', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ x, y }),
  });
}

export async function ibDesktopType(base, mid, text) {
  return authed(base, '/api/instabox/machines/' + encodeURIComponent(mid) + '/desktop/type', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }),
  });
}
