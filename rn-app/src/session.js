// ZerotheBot React-Native client ? same REST + SSE API as the web app (service/server.py).
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
        'Content-Type': 'application/json',
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
  const b = String(base || '').replace(/\/+$/, '');
  const r = await fetch(b + '/api/me', { headers: { 'Content-Type': 'application/json' } });
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || d.detail || 'Could not load account.');
  return d;
}

// Admin: list every account with per-user token usage.
export async function adminUsers(base) {
  const b = String(base || '').replace(/\/+$/, '');
  const r = await fetch(b + '/api/admin/users', { headers: { 'Content-Type': 'application/json' } });
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || d.detail || 'Could not load users.');
  return d;
}

// Admin: detail for one account.
export async function adminAccount(base, email) {
  const b = String(base || '').replace(/\/+$/, '');
  const r = await fetch(b + '/api/admin/account/' + encodeURIComponent(email), { headers: { 'Content-Type': 'application/json' } });
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || d.detail || 'Could not load account.');
  return d;
}

// Admin: server settings (masked secrets).
export async function adminSettings(base) {
  const b = String(base || '').replace(/\/+$/, '');
  const r = await fetch(b + '/api/admin/settings', { headers: { 'Content-Type': 'application/json' } });
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || d.detail || 'Could not load settings.');
  return d;
}

// Admin: set a user's plan tier.
export async function adminSetPlan(base, email, plan) {
  const b = String(base || '').replace(/\/+$/, '');
  const r = await fetch(b + '/api/admin/account/' + encodeURIComponent(email) + '/plan', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plan }),
  });
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || d.detail || 'Could not set plan.');
  return d;
}

// Admin: grant or revoke admin rights.
export async function adminSetAdmin(base, email, isAdmin) {
  const b = String(base || '').replace(/\/+$/, '');
  const r = await fetch(b + '/api/admin/account/' + encodeURIComponent(email) + '/admin', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_admin: isAdmin }),
  });
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || d.detail || 'Could not set admin.');
  return d;
}

// Approval center: Muse-style pending actions with merchant/price/item details.
export async function approvalsCenter(base) {
  const b = String(base || '').replace(/\/+$/, '');
  const r = await fetch(b + '/api/approvals/center', { headers: { 'Content-Type': 'application/json' } });
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || d.detail || 'Could not load approvals.');
  return d;
}

// Decide an approval: approve or deny.
export async function approvalDecide(base, aid, approve, remember = false, note = '') {
  const b = String(base || '').replace(/\/+$/, '');
  const r = await fetch(b + '/api/approvals/' + encodeURIComponent(aid) + '/decide', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ approve, remember, note }),
  });
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || d.detail || 'Could not decide approval.');
  return d;
}

// ---------------------------------------------------------------- email (IMAP) connector
export async function emailConfigure(base, host, port, username, password, useSsl = true) {
  const b = String(base || '').replace(/\/+$/, '');
  const r = await fetch(b + '/api/email/imap/configure', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ host, port, username, password, use_ssl: useSsl }),
  });
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || d.detail || 'Could not configure IMAP.');
  return d;
}

export async function emailPoll(base) {
  const b = String(base || '').replace(/\/+$/, '');
  const r = await fetch(b + '/api/email/imap/poll', { headers: { 'Content-Type': 'application/json' } });
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || d.detail || 'Could not poll email.');
  return d;
}

export async function emailCreateApprovals(base) {
  const b = String(base || '').replace(/\/+$/, '');
  const r = await fetch(b + '/api/email/imap/create-approvals', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
  });
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || d.detail || 'Could not create approvals.');
  return d;
}

// ---------------------------------------------------------------- document upload & parsing
export async function documentUpload(base, fileUri, fileName) {
  const b = String(base || '').replace(/\/+$/, '');
  const form = new FormData();
  form.append('file', {
    uri: fileUri,
    name: fileName,
    type: 'application/pdf',
  } as any);
  const r = await fetch(b + '/api/document/upload', {
    method: 'POST',
    body: form,
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || d.detail || 'Could not upload document.');
  return d;
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
export async function browserNavigate(base, url) {
  const b = String(base || '').replace(/\/+$/, '');
  const r = await fetch(b + '/api/browser/navigate', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  });
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || d.detail || 'Browser navigate failed.');
  return d;
}

export async function browserClick(base, selector) {
  const b = String(base || '').replace(/\/+$/, '');
  const r = await fetch(b + '/api/browser/click', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ selector }),
  });
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || d.detail || 'Browser click failed.');
  return d;
}

export async function browserType(base, selector, text) {
  const b = String(base || '').replace(/\/+$/, '');
  const r = await fetch(b + '/api/browser/type', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ selector, text }),
  });
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || d.detail || 'Browser type failed.');
  return d;
}

export async function browserScreenshot(base) {
  const b = String(base || '').replace(/\/+$/, '');
  const r = await fetch(b + '/api/browser/screenshot', { headers: { 'Content-Type': 'application/json' } });
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || d.detail || 'Browser screenshot failed.');
  return d;
}

// ---------------------------------------------------------------- agent-initiated proactive messages
export async function agentMessage(base, text, threadId = '') {
  const b = String(base || '').replace(/\/+$/, '');
  const r = await fetch(b + '/api/agent/message', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, thread_id: threadId }),
  });
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || d.detail || 'Agent message failed.');
  return d;
}

export async function agentMessages(base) {
  const b = String(base || '').replace(/\/+$/, '');
  const r = await fetch(b + '/api/agent/messages', { headers: { 'Content-Type': 'application/json' } });
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || d.detail || 'Could not load agent messages.');
  return d;
}
