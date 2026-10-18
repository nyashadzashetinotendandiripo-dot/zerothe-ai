// Self-update: the server publishes update.json next to the APK; the app compares
// versionCode, downloads the new APK in-app and hands it to Android's installer.
import { Linking } from 'react-native';
import Constants from 'expo-constants';

const UPD_URL = 'https://8888-agquljzsz7k42vgoenj7qr6fim.instabox.tech/update.json';

export const APP_CODE = (Constants.expoConfig && Constants.expoConfig.android && Constants.expoConfig.android.versionCode) || 60;
export const APP_VERSION = (Constants.expoConfig && Constants.expoConfig.version) || '1.7.0';

export async function checkUpdate() {
  try {
    const r = await fetch(UPD_URL, { headers: { Accept: 'application/json' } });
    if (!r.ok) return null;
    const d = await r.json();
    if (Number(d.code || 0) <= APP_CODE || !d.apk) return null;
    return d;
  } catch (e) { return null; }
}

export async function installUpdate(u) {
  // preferred: download inside the app, then share to the package installer
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
