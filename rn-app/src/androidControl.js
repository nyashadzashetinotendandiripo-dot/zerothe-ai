/**
 * Smart Android Control — ported into the ZerotheBot APK.
 *
 * Talks to the on-device AccessibilityService via a React Native native module.
 * Lets bots read the screen, tap, type, scroll, open apps, and wait for state
 * changes — all while ZerotheBot stays alive in the background.
 *
 * Falls back gracefully if the accessibility service is not enabled (user
 * must grant it once in Android Settings).
 */

import { NativeModules } from 'react-native';

const { ZerotheBotAccessibility } = NativeModules;

let isReady = false;
let readyListeners = [];

/**
 * Check if the accessibility service is available and enabled.
 */
export async function isAccessibilityAvailable() {
  if (!ZerotheBotAccessibility) return false;
  try {
    const status = await ZerotheBotAccessibility.isAccessibilityEnabled();
    return status && status.ourServiceEnabled === true;
  } catch {
    return false;
  }
}

/**
 * Open Android Settings > Accessibility so the user can enable ZerotheBot.
 */
export function openAccessibilitySettings() {
  if (!ZerotheBotAccessibility) {
    return Promise.reject(new Error('Native module not available'));
  }
  return ZerotheBotAccessibility.isAccessibilityEnabled().then(() => {
    return ZerotheBotAccessibility.openAccessibilitySettings();
  });
}

/**
 * Check if draw-overlays permission is granted (for the floating bubble).
 */
export async function isOverlayEnabled() {
  if (!ZerotheBotAccessibility) return false;
  try {
    return await ZerotheBotAccessibility.isOverlayEnabled();
  } catch {
    return false;
  }
}

/**
 * Request draw-overlays permission.
 */
export function requestOverlayPermission() {
  if (!ZerotheBotAccessibility) {
    return Promise.reject(new Error('Native module not available'));
  }
  return ZerotheBotAccessibility.requestOverlayPermission();
}

/**
 * Send a raw command to the accessibility service.
 */
async function sendCommand(command, args = {}) {
  if (!ZerotheBotAccessibility) {
    throw new Error('Accessibility native module not loaded. Rebuild the APK.');
  }
  if (!isReady) {
    await new Promise((resolve) => {
      readyListeners.push(resolve);
    });
  }
  return ZerotheBotAccessibility.sendCommand(command, args);
}

/**
 * When the native module signals it's ready, resolve pending calls.
 */
export function onReady(cb) {
  if (isReady) { cb(); return; }
  readyListeners.push(cb);
}

// Expose for the native module to call once attached
export function markReady() {
  isReady = true;
  readyListeners.forEach((cb) => cb());
  readyListeners = [];
}

/**
 * Dump the current screen accessibility tree as JSON text.
 */
export async function ui() {
  const result = await sendCommand('getRootNode', {});
  return typeof result === 'string' ? result : '';
}

/**
 * Find visible text on screen. Returns matching nodes with coordinates.
 */
export async function findText(text, exact = false) {
  const result = await sendCommand('findText', { text, exact });
  return Array.isArray(result) ? result : [];
}

/**
 * Tap an element by its accessibility resource id.
 */
export async function tapNode(id) {
  await sendCommand('tapNode', { id });
}

/**
 * Tap at screen coordinates.
 */
export async function tapAt(x, y) {
  await sendCommand('tapCoords', { x, y });
}

/**
 * Long press at screen coordinates.
 */
export async function longPress(x, y, durationMs = 800) {
  await sendCommand('longPress', { x, y, durationMs });
}

/**
 * Type text into the currently focused field.
 */
export async function typeText(text) {
  await sendCommand('setText', { text });
}

/**
 * Swipe from (x1,y1) to (x2,y2) over durationMs.
 */
export async function swipe(x1, y1, x2, y2, durationMs = 800) {
  await sendCommand('swipe', { x1, y1, x2, y2, durationMs });
}

/**
 * Press a hardware key: 'back', 'home', 'enter', 'menu'.
 */
export async function key(key) {
  await sendCommand('key', { key });
}

/**
 * Scroll the screen in a direction. direction: 'down' | 'up' | 'left' | 'right'.
 * amount: fraction of screen (0.05 - 1.0).
 */
export async function scroll(direction, amount = 0.6) {
  await sendCommand('scroll', { direction, amount });
}

/**
 * One scroll step. Returns { before, after, textMoved, pixelsMoved, moved }.
 * WebView text may stay stale, so pixelsMoved is the reliable signal there.
 */
export async function scrollStep(direction = 'down', amount = 0.6) {
  const before = await ui();
  await sendCommand('scroll', { direction, amount });
  // Give the screen time to settle
  await new Promise((r) => setTimeout(r, 800));
  const after = await ui();
  const beforeWords = before.split(/\s+/).filter(Boolean).sort();
  const afterWords = after.split(/\s+/).filter(Boolean).sort();
  const textMoved = beforeWords.join('|') !== afterWords.join('|');
  return {
    before,
    after,
    textMoved,
    pixelsMoved: textMoved,
    moved: textMoved,
  };
}

/**
 * Wait for specific text to appear on screen (polls).
 */
export async function waitForText(text, timeoutSec = 10) {
  const deadline = Date.now() + timeoutSec * 1000;
  while (Date.now() < deadline) {
    const found = await findText(text, false);
    if (found.length > 0) return true;
    await new Promise((r) => setTimeout(r, 600));
  }
  return false;
}

/**
 * Wait until screen text stops changing.
 */
export async function waitStable(timeoutSec = 10) {
  const deadline = Date.now() + timeoutSec * 1000;
  let last = await ui();
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 800));
    const current = await ui();
    if (current === last) return true;
    last = current;
  }
  return false;
}

/**
 * Open an app by package name or friendly name.
 */
export async function openApp(app) {
  return sendCommand('openApp', { app });
}

/**
 * Get the currently foreground package name.
 */
export async function currentApp() {
  const result = await sendCommand('currentApp', {});
  return typeof result === 'string' ? result : null;
}

/**
 * Wait until a specific app is in the foreground.
 */
export async function waitForApp(app, timeoutSec = 10) {
  const deadline = Date.now() + timeoutSec * 1000;
  while (Date.now() < deadline) {
    const current = await currentApp();
    if (current && (current === app || current.toLowerCase().includes(app.toLowerCase()))) {
      return true;
    }
    await new Promise((r) => setTimeout(r, 600));
  }
  return false;
}

export default {
  isAccessibilityAvailable,
  openAccessibilitySettings,
  isOverlayEnabled,
  requestOverlayPermission,
  ui,
  findText,
  tapNode,
  tapAt,
  longPress,
  typeText,
  swipe,
  key,
  scroll,
  scrollStep,
  waitForText,
  waitStable,
  openApp,
  currentApp,
  waitForApp,
  markReady,
  onReady,
};
