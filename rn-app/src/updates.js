// Self-update, two layers:
//  1. OTA (expo-updates): a new JS bundle is downloaded and the app reloads into
//     it immediately — no APK, no "open it twice".
//  2. APK fallback: the server also publishes update.json next to the APK, for
//     changes that need new native code. The banner hands it to Android's
//     installer.
import { Linking } from 'react-native';
import { APP_CODE, APP_VERSION } from './build';
import { note } from './diag';

export { APP_CODE, APP_VERSION };

const UPD_URL = 'https://8888-agquljzsz7k42vgoenj7qr6fim.instabox.tech/update.json';

let Updates = null;
try { Updates = require('expo-updates'); } catch (e) { /* Expo Go / web */ }

// Pull the published JS bundle and restart into it. Returns 'updated',
// 'none' or 'error'. Safe to call on every launch: expo-updates only downloads
// when the server has a newer bundle for this runtime version.
export async function applyOta(onNote) {
  if (!Updates) return 'none';
  try {
    if (!Updates.isEnabled) return 'none';
    const chk = await Updates.checkForUpdateAsync();
    if (!chk || !chk.isAvailable) return 'none';
    if (onNote) onNote('Downloading the new version…');
    const dl = await Updates.fetchUpdateAsync();
    if (dl && dl.isNew) {
      if (onNote) onNote('Restarting with the new version…');
      setTimeout(() => { Updates.reloadAsync().catch(() => {}); }, 350);
      return 'updated';
    }
  } catch (e) {
    // An OTA that silently fails leaves the phone on old code forever; report it.
    note('ota', e, 'applyOta');
    return 'error';
  }
  return 'none';
}

export async function checkUpdate() {
  try {
    const r = await fetch(UPD_URL, { headers: { Accept: 'application/json' } });
    if (!r.ok) return null;
    const d = await r.json();
    if (Number(d.code || 0) <= APP_CODE || !d.apk) return null;
    return d;
  } catch (e) { return null; }
}

export async function installUpdate(u) {  // preferred: download inside the app, then share to the package installer
  try {
    const fs = require('expo-file-system/legacy');
    const Sharing = require('expo-sharing');
    const dir = fs.cacheDirectory || fs.documentDirectory;
    const { uri } = await fs.downloadAsync(u.apk, dir + 'ZerotheBot-update.apk');
    if (Sharing.isAvailableAsync) {
      const ok = await Sharing.isAvailableAsync();
      if (ok) {
        await Sharing.shareAsync(uri, { mimeType: 'application/vnd.android.package-archive', dialogTitle: 'Install ZerotheBot ' + (u.version || '') });
        return 'installing';
      }
    }
    throw new Error('no share');
  } catch (e) {
    // fallback: browser download (browser -> file -> tap to install)
    try { await Linking.openURL(u.apk); return 'download';
    } catch (e2) { return 'failed'; }
  }
}

// Everything expo-updates knows about this install. When the phone is stuck on
// old code, this is the difference between "OTA is off", "the runtime version
// does not match" and "the update server was never asked".
export function otaStatus() {
  if (!Updates) return { enabled: false, reason: 'expo-updates not loaded' };
  try {
    return {
      enabled: !!Updates.isEnabled,
      runtimeVersion: String(Updates.runtimeVersion || ''),
      channel: String(Updates.channel || ''),
      checkAutomatically: String(Updates.checkAutomatically || ''),
      updateId: String(Updates.updateId || ''),
      createdAt: Updates.createdAt ? new Date(Updates.createdAt).toISOString().slice(0, 19).replace('T', ' ') : '',
      embedded: String(Updates.manifest?.id || ''),
      reason: Updates.isEnabled ? '' : (Updates.isEmbeddedLaunch ? 'running the embedded bundle' : 'disabled'),
    };
  } catch (e) {
    return { enabled: false, reason: String((e && e.message) || e) };
  }
}
