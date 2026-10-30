// Crash evidence, kept on the phone and sent to the server.
//
// A release build has no console: when the app dies, the stack trace dies with
// it and "it crashes when I send" is all anyone can say. This module keeps a
// small ring of breadcrumbs, catches every uncaught JS error, and posts the
// report to /api/client-log, where it can actually be read and fixed.
import { APP_VERSION, APP_CODE, OTA_ID } from './build';
import { conf, cfgGet, cfgSet } from './session';

const RING = [];
const K_LAST = 'zt_lasterr';
let LAST = null;

function clip(v, n) {
  try {
    const s = typeof v === 'string' ? v : JSON.stringify(v);
    return String(s).length > n ? String(s).slice(0, n) + '…' : String(s);
  } catch (e) {
    try { return String(v); } catch (e2) { return '?'; }
  }
}

export function errText(e) {
  if (e == null) return 'Unknown error';
  if (typeof e === 'string') return e;
  try { return String(e.message || e); } catch (e2) { return 'Unknown error'; }
}

function stackOf(e) {
  try { return e && e.stack ? String(e.stack) : ''; } catch (e2) { return ''; }
}

// One line per thing that happened, newest last. Cheap: a push and a trim.
export function breadcrumb(name, data) {
  try {
    RING.push({ t: Math.round(Date.now() / 1000), n: String(name).slice(0, 40), d: data === undefined ? '' : clip(data, 240) });
    while (RING.length > 60) RING.shift();
  } catch (e) { /* never throw from logging */ }
}

export function crumbs() { return RING.slice(-40); }

export function lastError() { return LAST; }

export function clearLastError() { LAST = null; }

// POST one report. Raw fetch, not api(): this must work even when the session
// is the thing that is broken, and it must never throw into a crash handler.
export async function send(kind, e, extra) {
  try {
    const base = String(conf().base || '').replace(/\/+$/, '');
    const tok = conf().token || '';
    if (!base) return false;
    const body = JSON.stringify({
      kind: String(kind || 'error').slice(0, 40),
      app: APP_VERSION,
      code: APP_CODE,
      ota: OTA_ID || 'embedded',
      message: clip(errText(e), 900),
      stack: clip(stackOf(e), 7000),
      extra: clip(extra || '', 600),
      crumbs: crumbs(),
    });
    const ctl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timer = ctl ? setTimeout(() => { try { ctl.abort(); } catch (e2) {} }, 8000) : null;
    const r = await fetch(base + '/api/client-log', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + tok,
        'X-ZB-App': APP_VERSION,
        'X-ZB-Ota': OTA_ID || 'embedded',
      },
      body,
      signal: ctl ? ctl.signal : undefined,
    });
    if (timer) clearTimeout(timer);
    return !!r.ok;
  } catch (e2) {
    return false;
  }
}

// Remember it locally (Settings → Diagnostics) and ship it.
export function note(kind, e, extra) {
  LAST = {
    t: new Date().toISOString().replace('T', ' ').slice(0, 19),
    kind: String(kind || 'error'),
    message: clip(errText(e), 900),
    stack: clip(stackOf(e), 4000),
    extra: clip(extra || '', 600),
  };
  breadcrumb('error: ' + LAST.kind, LAST.message);
  // Survives a process death: the next launch can still show (and send) it.
  try { cfgSet(K_LAST, JSON.stringify(LAST)).catch(() => {}); } catch (e2) { /* ignore */ }
  send(kind, e, extra);
  return LAST;
}

// Load the last error from a previous launch, so "it crashed yesterday" is still
// on screen today.
export async function hydrate() {
  try {
    const raw = await cfgGet(K_LAST);
    if (!raw) return null;
    const d = JSON.parse(raw);
    if (d && d.message) LAST = d;
  } catch (e) { /* ignore */ }
  return LAST;
}

// Post the stored record again (Settings → Diagnostics).
export async function resend() {
  if (!LAST) return false;
  return send(LAST.kind, { message: LAST.message, stack: LAST.stack }, 'stored ' + LAST.t + ' ' + (LAST.extra || ''));
}

// Report at most once a minute per key: a polling loop must not flood the log
// (or the battery) while the server is unreachable.
const ONCE = {};
export function noteOnce(key, kind, e, extra) {
  const now = Date.now();
  if (ONCE[key] && now - ONCE[key] < 60000) return null;
  ONCE[key] = now;
  return note(kind, e, extra);
}

// Swallow uncaught errors instead of letting Android close the app. A bad row,
// a dead socket or a widget that chokes must cost one bubble, not the session.
// If errors keep firing (a render loop), stop swallowing: a crash the user can
// restart from beats a phone burning battery on a broken screen.
export function installCrashGuard() {
  try {
    const EU = global.ErrorUtils;
    if (!EU || typeof EU.setGlobalHandler !== 'function') return;
    const prev = typeof EU.getGlobalHandler === 'function' ? EU.getGlobalHandler() : null;
    let hits = [];
    EU.setGlobalHandler((e, isFatal) => {
      const now = Date.now();
      hits = hits.filter((t) => now - t < 10000);
      hits.push(now);
      try { note(isFatal ? 'fatal' : 'error', e, (isFatal ? 'uncaught fatal' : 'uncaught') + ' x' + hits.length); } catch (e2) { /* ignore */ }
      if (hits.length > 25 && typeof prev === 'function') {
        try { prev(e, isFatal); } catch (e3) { /* ignore */ }
      }
    });
  } catch (e) { /* ignore */ }
}
