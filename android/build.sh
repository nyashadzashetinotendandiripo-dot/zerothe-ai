#!/usr/bin/env bash
# Build the ZerotheBot native Android APK.
#
#   ./build.sh            # assumes JDK 17+ and Android SDK are already installed
#   APP_URL=... ./build.sh  # point the app at a different server
#
# Needs: JDK 17+, Android SDK (platform 34+, build-tools 34+), Node 18+, curl.
set -euo pipefail

cd "$(dirname "$0")"

APP_URL="${APP_URL:-https://8765-agquljzsz7k42vgoenj7qr6fim.instabox.tech}"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/android-sdk}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"

if [ -z "${JAVA_HOME:-}" ]; then
  JAVA_HOME="$(dirname "$(dirname "$(readlink -f "$(command -v javac)")")")"
  export JAVA_HOME
fi
echo "JAVA_HOME=$JAVA_HOME  ANDROID_HOME=$ANDROID_HOME"

# 1. Android SDK (only if missing)
if [ ! -d "$ANDROID_HOME/cmdline-tools/latest" ]; then
  echo "Installing Android command-line tools..."
  mkdir -p "$ANDROID_HOME/cmdline-tools"
  curl -sL -o /tmp/cmdtools.zip "https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
  unzip -q -o /tmp/cmdtools.zip -d "$ANDROID_HOME/cmdline-tools/"
  mv "$ANDROID_HOME/cmdline-tools/cmdline-tools" "$ANDROID_HOME/cmdline-tools/latest"
  yes | "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" --licenses >/dev/null 2>&1 || true
  "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" --install "platform-tools" "platforms;android-34" "build-tools;34.0.0" >/dev/null
fi

# 2. Fresh copy of the web app as the Capacitor payload
rm -rf www && mkdir -p www
cp -r ../web/* www/

# 3. Capacitor project
cat > capacitor.config.json <<JSON
{
  "appId": "ai.zerothebot.app",
  "appName": "ZerotheBot",
  "webDir": "www",
  "server": { "url": "$APP_URL", "androidScheme": "https" },
  "android": { "allowMixedContent": false }
}
JSON

npm install
npx cap add android >/dev/null 2>&1 || true
npx cap sync android

# 4. Build the APK
( cd android && ./gradlew assembleDebug --no-daemon )

APK="android/app/build/outputs/apk/debug/app-debug.apk"
echo
echo "APK built: $APK ($(du -h "$APK" | cut -f1))"
echo "Install with:  adb install -r \"$APK\""
