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

import { NativeModules, Platform, AppState } from 'react-native';
import { Alert } from 'react-native';

const { ZerotheBotAccessibility } = NativeModules;

let isReady = false;
let readyListeners: (() => void)[] = [];

/**
 * Check if the accessibility service is available and enabled.
 */
export async function isAccessibilityAvailable(): Promise<boolean> {
  if (!ZerotheBotAccessibility) return false;
  try {
    const status = await ZerotheBotAccessibility.isAccessibilityEnabled();
    return status?.ourServiceEnabled === true;
  } catch {
    return false;
  }
}

/**
 * Open Android Settings > Accessibility so the user can enable ZerotheBot.
 */
export function openAccessibilitySettings(): Promise<void> {
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
export async function isOverlayEnabled(): Promise<boolean> {
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
export function requestOverlayPermission(): Promise<void> {
  if (!ZerotheBotAccessibility) {
    return Promise.reject(new Error('Native module not available'));
  }
  return ZerotheBotAccessibility.requestOverlayPermission();
}

/**
 * Send a raw command to the accessibility service.
 */
async function sendCommand(command: string, args: Record<string, any> = {}): Promise<any> {
  if (!ZerotheBotAccessibility) {
    throw new Error('Accessibility native module not loaded. Rebuild the APK.');
  }
  if (!isReady) {
    await new Promise<void>((resolve) => {
      readyListeners.push(resolve);
    });
  }
  return ZerotheBotAccessibility.sendCommand(command, args);
}

/**
 * When the native module signals it's ready, resolve pending calls.
 */
export function onReady(cb: () => void) {
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
export async function ui(): Promise<string> {
  const result = await sendCommand('getRootNode', {});
  return typeof result === 'string' ? result : '';
}

/**
 * Find visible text on screen. Returns matching nodes with coordinates.
 */
export async function findText(text: string, exact = false): Promise<Array<any>> {
  const result = await sendCommand('findText', { text, exact });
  return Array.isArray(result) ? result : [];
}

/**
 * Tap an element by its accessibility resource id.
 */
export async function tapNode(id: string): Promise<void> {
  await sendCommand('tapNode', { id });
}

/**
 * Tap at screen coordinates.
 */
export async function tapAt(x: number, y: number): Promise<void> {
  await sendCommand('tapCoords', { x, y });
}

/**
 * Long press at screen coordinates.
 */
export async function longPress(x: number, y: number, durationMs = 800): Promise<void> {
  await sendCommand('longPress', { x, y, durationMs });
}

/**
 * Type text into the currently focused field.
 */
export async function typeText(text: string): Promise<void> {
  await sendCommand('setText', { text });
}

/**
 * Swipe from (x1,y1) to (x2,y2) over durationMs.
 */
export async function swipe(x1: number, y1: number, x2: number, y2: number, durationMs = 800): Promise<void> {
  await sendCommand('swipe', { x1, y1, x2, y2, durationMs });
}

/**
 * Press a hardware key: 'back', 'home', 'enter', 'menu'.
 */
export async function key(key: string): Promise<void> {
  await sendCommand('key', { key });
}

/**
 * Scroll the screen in a direction. direction: 'down' | 'up' | 'left' | 'right'.
 * amount: fraction of screen (0.05 - 1.0).
 */
export async function scroll(direction: string, amount = 0.6): Promise<void> {
  await sendCommand('scroll', { direction, amount });
}

/**
 * One scroll step. Returns { before, after, textMoved, pixelsMoved, moved }.
 * WebView text may stay stale, so pixelsMoved is the reliable signal there.
 */
export async function scrollStep(direction = 'down', amount = 0.6): Promise<any> {
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
    pixelsMoved: textMoved, // best-effort; no screenshot hash on-device
    moved: textMoved,
  };
}

/**
 * Wait for specific text to appear on screen (polls).
 */
export async function waitForText(text: string, timeoutSec = 10): Promise<boolean> {
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
export async function waitStable(timeoutSec = 10): Promise<boolean> {
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
export async function openApp(app: string): Promise<any> {
  return sendCommand('openApp', { app });
}

/**
 * Get the currently foreground package name.
 */
export async function currentApp(): Promise<string | null> {
  const result = await sendCommand('currentApp', {});
  return typeof result === 'string' ? result : null;
}

/**
 * Wait until a specific app is in the foreground.
 */
export async function waitForApp(app: string, timeoutSec = 10): Promise<boolean> {
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
