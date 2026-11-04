// Device push registration: best-effort. Without a Firebase project
// (google-services.json in the native build) token acquisition fails and this
// stays silent — the in-app Inbox still shows every notification. Once the
// Firebase config lands, this lights up with no further app changes.
import { Platform } from 'react-native';
import { pushRegister } from './session';
import { note } from './diag';

let done = false;

export async function registerPush() {
  if (done) return;
  done = true;
  try {
    if (Platform.OS !== 'android') return;
    const Notifs = require('expo-notifications');
    const settings = await Notifs.getPermissionsAsync();
    let granted = !!settings.granted;
    if (!granted && settings.canAskAgain) {
      const r = await Notifs.requestPermissionsAsync();
      granted = !!r.granted;
    }
    if (!granted) return;
    const tok = await Notifs.getDevicePushTokenAsync();
    const token = tok && (tok.data || tok.token);
    if (!token) return;
    await pushRegister(undefined, String(token), 'android');
  } catch (e) {
    // Expected until Firebase is configured; the note makes it diagnosable.
    note('push', e, 'registerPush');
  }
}
