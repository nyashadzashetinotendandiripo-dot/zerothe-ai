// Which build is actually running? The app version comes from the embedded
// app.json, but an OTA bundle can be newer than the APK — so the OTA id is the
// only honest answer to "am I on the latest code?". Settings shows both, and
// every API call sends them so the server log can confirm it.
import Constants from 'expo-constants';

let Updates = null;
try { Updates = require('expo-updates'); } catch (e) { /* Expo Go / web */ }

export const APP_VERSION = (Constants.expoConfig && Constants.expoConfig.version) || '1.0.0';
export const APP_CODE = (Constants.expoConfig && Constants.expoConfig.android && Constants.expoConfig.android.versionCode) || 0;

export const OTA_ID = (() => {
  try { return (Updates && Updates.updateId) || ''; } catch (e) { return ''; }
})();

export const OTA_CREATED = (() => {
  try {
    const d = Updates && Updates.createdAt;
    return d ? new Date(d).toISOString().slice(0, 16).replace('T', ' ') : '';
  } catch (e) { return ''; }
})();

export const BUILD_STAMP = APP_VERSION + (OTA_ID ? ' · ' + String(OTA_ID).slice(0, 8) : ' · built-in');
