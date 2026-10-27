// ZerotheBot React-Native client — same REST + SSE API as the web app (service/server.py).
import { useEffect, useMemo, useRef, useState } from 'react';
import EventSource from 'react-native-sse';

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
  const r = await fetch(API_BASE + path, {
    ...opts,
    headers: {
      'Authorization': 'Bearer ' + TOKEN,
      'Content-Type': 'application/json',
      ...(opts.headers || {}),
    },
  });
  if (r.status === 401) throw new Error('Not authorized');
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
    es = new EventSource(`${API_BASE}/api/events?kind=mobile&since=${last}`, {
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
