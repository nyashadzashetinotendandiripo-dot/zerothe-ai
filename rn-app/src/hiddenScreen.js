/**
 * Hidden Screen — the bot's invisible workspace.
 *
 * Runs apps on an invisible secondary display so bots can open an app and do
 * a task INSIDE it while the user's screen never changes. Uses only official,
 * phone-agnostic Android APIs (public virtual displays + multi-display
 * accessibility), so it behaves the same on Samsung, Google, Xiaomi, etc.
 *
 * Requires Android 11+. On older phones isSupported() returns false and bots
 * automatically fall back to Silent Mode (APIs) / the visible robot finger.
 */

import { NativeModules } from 'react-native';

const { ZerotheBotHidden } = NativeModules;

export async function status() {
  if (!ZerotheBotHidden) {
    return { available: false, supported: false, active: false, displayId: -1, service: false };
  }
  try {
    const s = await ZerotheBotHidden.isHiddenAvailable();
    return { available: true, supported: !!s.supported, active: !!s.active,
             displayId: s.displayId ?? -1, api: s.api, service: !!s.service };
  } catch {
    return { available: false, supported: false, active: false, displayId: -1, service: false };
  }
}

export const isSupported = async () => (await status()).supported;
export const isActive = async () => (await status()).active;

/** Create the invisible display (idempotent). Returns { displayId } or throws. */
export async function show(width = 720, height = 1280, dpi = 240) {
  if (!ZerotheBotHidden) throw new Error('Hidden module not loaded. Rebuild the APK.');
  return ZerotheBotHidden.create(width, height, dpi);
}

export function hide() {
  if (!ZerotheBotHidden) throw new Error('Hidden module not loaded. Rebuild the APK.');
  return ZerotheBotHidden.close();
}

/** Launch app on the hidden display — user sees nothing. */
export async function launchApp(pkg) {
  if (!ZerotheBotHidden) throw new Error('Hidden module not loaded. Rebuild the APK.');
  return ZerotheBotHidden.launchApp(pkg);
}

/** Convenience: ensure the display exists, then launch. */
export async function launchSilently(pkg) {
  const st = await status();
  if (!st.supported) throw new Error('Hidden screen needs Android 11 or newer.');
  if (!st.active || st.displayId < 0) {
    const r = await show();
    if (!r.displayId || r.displayId < 0) throw new Error('Could not create the hidden screen.');
  }
  return launchApp(pkg);
}

/** UI tree of whatever runs on the hidden display. */
export async function dump() {
  if (!ZerotheBotHidden) throw new Error('Hidden module not loaded. Rebuild the APK.');
  const raw = await ZerotheBotHidden.dump();
  if (typeof raw === 'string' && raw.length > 1) {
    try { return JSON.parse(raw); } catch { return null; }
  }
  return null;
}

export async function findText(text, exact = false) {
  if (!ZerotheBotHidden) throw new Error('Hidden module not loaded. Rebuild the APK.');
  const raw = await ZerotheBotHidden.findText(text, exact);
  if (typeof raw === 'string' && raw.length > 1) {
    try { return JSON.parse(raw); } catch { return []; }
  }
  return [];
}

export async function tapText(text) {
  if (!ZerotheBotHidden) throw new Error('Hidden module not loaded. Rebuild the APK.');
  return ZerotheBotHidden.tapText(text);
}

export async function setText(search, text) {
  if (!ZerotheBotHidden) throw new Error('Hidden module not loaded. Rebuild the APK.');
  return ZerotheBotHidden.setText(search, text);
}

export async function scroll(direction = 'down') {
  if (!ZerotheBotHidden) throw new Error('Hidden module not loaded. Rebuild the APK.');
  return ZerotheBotHidden.scroll(direction);
}

export default {
  status, isSupported, isActive, show, hide,
  launchApp, launchSilently, dump, findText, tapText, setText, scroll,
};
