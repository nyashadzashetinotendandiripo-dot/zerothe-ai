/**
 * Silent Mode — bots operate Android through official APIs so nothing on the
 * user's screen ever changes while they stay inside ZerotheBot.
 *
 *   - readNotifications()   read every active notification (no app opens)
 *   - replyTo(key, text)    hidden direct reply (no app opens)
 *   - dismiss(key)          clear a notification (no app opens)
 *   - setAlarm/setTimer     Clock app never opens (SKIP_UI)
 *   - addCalendarEvent      Calendar app never opens
 *   - addContact/findContact Contacts app never opens
 *   - setVolume / dnd       audio and zen modes (no app opens)
 *
 * Screen-tapping tasks (openApp/tapText) stay in androidControl.js — they are
 * the fallback for things that truly need the robot finger.
 */

import { NativeModules, PermissionsAndroid } from 'react-native';

const { ZerotheBotSilent } = NativeModules;

export async function isSilentReady() {
  if (!ZerotheBotSilent) return { available: false, listener: false, dndAccess: false };
  try {
    const s = await ZerotheBotSilent.isSilentReady();
    return { available: true, listener: !!s.listener, dndAccess: !!s.dndAccess };
  } catch {
    return { available: false, listener: false, dndAccess: false };
  }
}

export function openNotificationAccessSettings() {
  if (!ZerotheBotSilent) return Promise.reject(new Error('Native module not available'));
  return ZerotheBotSilent.openNotificationAccessSettings();
}

export function openDndSettings() {
  if (!ZerotheBotSilent) return Promise.reject(new Error('Native module not available'));
  return ZerotheBotSilent.openDndSettings();
}

function parseJsonArray(v) {
  if (typeof v !== 'string') return [];
  try { return JSON.parse(v); } catch { return []; }
}

/** Every active notification: [{ key, pkg, app, title, text, replyable, when }] */
export async function readNotifications() {
  if (!ZerotheBotSilent) throw new Error('Silent module not loaded. Rebuild the APK.');
  const raw = await ZerotheBotSilent.listNotifications();
  return parseJsonArray(raw);
}

/** Hidden direct reply into a notification — the target app never opens. */
export async function replyTo(key, text) {
  if (!ZerotheBotSilent) throw new Error('Silent module not loaded. Rebuild the APK.');
  return ZerotheBotSilent.replyToNotification(key, text);
}

/** Reply to the first replyable notification (convenience for bots). */
export async function replyToLatest(text) {
  const notifs = await readNotifications();
  const target = notifs.find((n) => n.replyable) || notifs[0];
  if (!target) throw new Error('No notifications to reply to.');
  const res = await replyTo(target.key, text);
  return { ...res, target: target.app || target.pkg };
}

export async function dismiss(key) {
  if (!ZerotheBotSilent) throw new Error('Silent module not loaded. Rebuild the APK.');
  return ZerotheBotSilent.dismissNotification(key);
}

/** Dismiss the newest notification. */
export async function dismissLatest() {
  const notifs = await readNotifications();
  if (!notifs.length) return false;
  return dismiss(notifs[0].key);
}

/** Set an alarm silently — hour 0-23, minute 0-59. Clock app never opens. */
export async function setAlarm(hour, minute, label = 'ZerotheBot') {
  if (!ZerotheBotSilent) throw new Error('Silent module not loaded. Rebuild the APK.');
  return ZerotheBotSilent.setAlarm(hour, minute, label);
}

/** Start a countdown silently, in seconds. Clock app never opens. */
export async function setTimer(seconds, label = 'ZerotheBot') {
  if (!ZerotheBotSilent) throw new Error('Silent module not loaded. Rebuild the APK.');
  return ZerotheBotSilent.setTimer(seconds, label);
}

/** Volume percent (0-100). stream: 'music' | 'ring' | 'alarm' | 'notification'. */
export async function setVolume(percent, stream = 'music') {
  if (!ZerotheBotSilent) throw new Error('Silent module not loaded. Rebuild the APK.');
  return ZerotheBotSilent.setVolume(percent, stream);
}

/** Do Not Disturb on for N minutes (0 = until switched off). */
export async function dnd(minutes = 30) {
  if (!ZerotheBotSilent) throw new Error('Silent module not loaded. Rebuild the APK.');
  return ZerotheBotSilent.dnd(minutes);
}

export async function dndOff() {
  if (!ZerotheBotSilent) throw new Error('Silent module not loaded. Rebuild the APK.');
  return ZerotheBotSilent.dndOff();
}

const CONTACT_PERMS = ['android.permission.READ_CONTACTS', 'android.permission.WRITE_CONTACTS'];
const CALENDAR_PERMS = ['android.permission.READ_CALENDAR', 'android.permission.WRITE_CALENDAR'];

async function ensurePerms(perms) {
  const results = await PermissionsAndroid.requestMultiple(perms);
  return perms.every((p) => results[p] === PermissionsAndroid.RESULTS.GRANTED);
}

/** Add a contact silently — Contacts app never opens. */
export async function addContact(name, phone) {
  if (!ZerotheBotSilent) throw new Error('Silent module not loaded. Rebuild the APK.');
  await ensurePerms(CONTACT_PERMS);
  return ZerotheBotSilent.addContact(name, phone);
}

/** Find contacts by name: [{ name, number }] — Contacts app never opens. */
export async function findContact(name) {
  if (!ZerotheBotSilent) throw new Error('Silent module not loaded. Rebuild the APK.');
  await ensurePerms(CONTACT_PERMS);
  const raw = await ZerotheBotSilent.findContact(name);
  return parseJsonArray(raw);
}

/** Add a calendar event silently — Calendar app never opens. */
export async function addCalendarEvent(title, startMs = Date.now(), minutes = 30) {
  if (!ZerotheBotSilent) throw new Error('Silent module not loaded. Rebuild the APK.');
  await ensurePerms(CALENDAR_PERMS);
  return ZerotheBotSilent.addCalendarEvent(title, startMs, minutes);
}

export default {
  isSilentReady,
  openNotificationAccessSettings,
  openDndSettings,
  readNotifications,
  replyTo,
  replyToLatest,
  dismiss,
  dismissLatest,
  setAlarm,
  setTimer,
  setVolume,
  dnd,
  dndOff,
  addContact,
  findContact,
  addCalendarEvent,
};
