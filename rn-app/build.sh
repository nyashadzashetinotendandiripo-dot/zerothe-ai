#!/usr/bin/env bash
# Build the ZerotheBot React-Native (Expo) APK — the Grok-Bot-style real app.
#   ./build.sh          # Expo SDK 58, Expo/React-Native + Hermes, like Grok Bot
set -euo pipefail
cd "$(dirname "$0")"
log() { echo "[$(date +%H:%M:%S)] $*"; }

echo "node $(node -v)  npm $(npm -v)"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/android-sdk}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
if [ -z "${JAVA_HOME:-}" ]; then
  JAVA_HOME="$(dirname "$(dirname "$(readlink -f "$(command -v javac)")")")"
  export JAVA_HOME
fi
log "JAVA_HOME=$JAVA_HOME ANDROID_HOME=$ANDROID_HOME"

# 1. JDK/SDK pieces RN needs (platform 35 + 36 templates, new build-tools)
if [ ! -d "$ANDROID_HOME/platforms/android-36" ]; then
  log "installing Android SDK pieces (platforms 35/36, build-tools 36)"
  yes | "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" --licenses >/dev/null 2>&1 || true
  "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" --install "platform-tools" "platforms;android-36" "build-tools;36.0.0" >/dev/null
fi
[ -d "$ANDROID_HOME/platforms/android-35" ] || "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" --install "platforms;android-35" >/dev/null || true

# 2. JS deps
[ -d node_modules ] || { log "npm install"; npm install --no-audit --no-fund --loglevel=error; }

# 3. Native android project from the Expo template
log "expo prebuild"
npx expo prebuild -p android --no-install --clean

# 4. Sign everything with the GLOBAL ~/.android/debug.keystore — the same key the
#    first shell APK was signed with, so this APK upgrade-installs over it.
sed -i "s|storeFile file('debug.keystore')|storeFile file(System.getProperty(\"user.home\") + \"/.android/debug.keystore\")|" android/app/build.gradle
sed -i 's/signingConfig signingConfigs.release/signingConfig signingConfigs.debug/' android/app/build.gradle || true
grep -c signingConfig android/app/build.gradle || true

# 5. Build
log "gradle assembleRelease (first run downloads a lot, be patient)"
( cd android && ./gradlew assembleRelease --no-daemon --console=plain ) > gradle.log 2>&1 || { tail -n 40 gradle.log; exit 1; }
tail -n 6 gradle.log

APK="android/app/build/outputs/apk/release/app-release.apk"
[ -f "$APK" ] || APK="android/app/build/outputs/apk/release/app-release-unsigned.apk"
log "APK built: $APK ($(du -h "$APK" | cut -f1))"
log "done"
