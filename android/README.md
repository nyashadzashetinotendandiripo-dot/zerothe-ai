# ZerotheBot — Native Android app

A real, installable Android APK (built with [Capacitor](https://capacitorjs.com/))
that wraps the ZerotheBot phone app in a native shell: own process, installable
icon, and ready for push notifications. The app connects to the cloud backend.

**Current APK:** `../dist/ZerotheBot-Android-1.6.0.apk` (package `ai.zerothebot.app`)

## What it is

- `ai.zerothebot.app` — the Android package id
- Loads the live backend at `https://8765-agquljzsz7k42vgoenj7qr6fim.instabox.tech`
- Everything the phone app does: bots, chats, threads, images, voice, approvals,
  browser takeover

## Install on a phone

1. Copy the APK to the phone (USB, email, or download link).
2. On the phone: allow "Install from unknown sources" for the app you used.
3. Open the APK → Install.

Or from a connected device:

```bash
adb install -r ZerotheBot-Android-1.6.0.apk
```

## Rebuild

```bash
# Linux / Mac / cloud VM — installs the SDK if missing, builds the APK
./build.sh
```

On Windows, the same steps work in WSL, or install JDK 17+ and the Android SDK,
then run `npm install`, `npx cap sync android`, and
`android\gradlew.bat assembleDebug` inside `android/`.

Point the app at a different server:

```bash
APP_URL=https://your-server.example.com ./build.sh
```

## Notes

- The build downloads ~500 MB of Android SDK + Gradle on first run.
- It is a **debug** build (unsigned) — installable on any device, but not
  publishable to Play Store. A release build needs a signing keystore.
