#!/usr/bin/env bash
# Build the ZerotheBot React-Native (Expo) APK — the Grok-Bot-style real app.
#   ./build.sh          # Expo SDK 58, Expo/React-Native + Hermes, like Grok Bot
set -euo pipefail
cd "$(dirname "$0")"
log() { echo "[$(date +%H:%M:%S)] $*"; }
# Cap the JS bundler's heap — the build box has 8GB RAM and gradle spawns node.
export NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=3072}"

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

# 3b. Device Bridge — REMOVED.
# The AccessibilityService that let Bots drive the user's phone has been removed.
# Bots now drive Android on InstaBox Android Boxes (Redroid container, adb + desktop API),
# not on the user's phone. This eliminates the Play Store AccessibilityService
# restricted-permission declaration and simplifies the APK.
# The android-bridge/ directory and ZbDeviceService.kt are kept for reference
# but are no longer injected into the build.
export ZB_API_BASE="${ZB_API_BASE:-https://8765-agquljzsz7k42vgoenj7qr6fim.instabox.tech}"
# OOM guard part 2: prebuild regenerates gradle.properties, so re-disable the
# heavy release tasks every run and cap the bundler's JS heap.
cat >> android/gradle.properties <<'GEOF'

android.enableMinifyInReleaseBuilds=false
android.enableNativeBuild=false
GEOF

# 4. Signing.
#    Default (no ZB_KS_FILE): sign with the GLOBAL ~/.android/debug.keystore — the
#    same key the first shell APK used, so sideload installs keep upgrading.
#    Play mode (ZB_KS_FILE + ZB_KS_PASS env set): inject a real
#    signingConfigs.release from those env vars and let the template's release
#    buildType use the upload key. Never commit the keystore or its password.
if [ -n "${ZB_KS_FILE:-}" ] && [ -n "${ZB_KS_PASS:-}" ]; then
  log "release signing: ZB_KS_FILE present -> release buildType uses the upload key"
  python3 - <<'PY'
import os, re, sys

p = 'android/app/build.gradle'
s = open(p, encoding='utf-8').read()
ks = os.environ['ZB_KS_FILE']
pw = os.environ['ZB_KS_PASS']
al = os.environ.get('ZB_KS_ALIAS', 'zt-upload')

# 1. Add the release signing config object (idempotent).
if not re.search(r'signingConfigs\s*\{[^}]*release\s*\{', s):
    conf = ("    release {\n"
            "        storeFile file('" + ks + "')\n"
            "        storePassword '" + pw + "'\n"
            "        keyAlias '" + al + "'\n"
            "        keyPassword '" + pw + "'\n"
            "    }\n")
    m = re.search(r'signingConfigs\s*\{\n', s)
    if not m:
        sys.exit('signingConfigs block not found in app/build.gradle')
    s = s[:m.end()] + conf + s[m.end():]

# 2. Point the release buildType at signingConfigs.release (template may
#    still carry signingConfigs.debug there; leave the debug buildType alone).
def _fix_bt(m):
    def _rel(m2):
        blk = m2.group(0)
        blk = blk.replace('signingConfig signingConfigs.debug',
                          'signingConfig signingConfigs.release')
        if 'signingConfig signingConfigs.release' not in blk:
            blk = blk.replace('\n        release {',
                              '\n        release {\n            signingConfig signingConfigs.release', 1)
        return blk
    return re.sub(r'\n        release\s*\{.*?\n        \}', _rel, m.group(0),
                  count=1, flags=re.S)
s2, n = re.subn(r'buildTypes\s*\{.*?\n    \}', _fix_bt, s, count=1, flags=re.S)
if n == 0:
    sys.exit('buildTypes block not found in app/build.gradle')
open(p, 'w', encoding='utf-8').write(s2)
print('signing: release buildType -> signingConfigs.release (' + ks + ', alias ' + al + ')')
PY
else
  sed -i "s|storeFile file('debug.keystore')|storeFile file(System.getProperty(\"user.home\") + \"/.android/debug.keystore\")|" android/app/build.gradle
  sed -i 's/signingConfig signingConfigs.release/signingConfig signingConfigs.debug/' android/app/build.gradle || true
  grep -c signingConfig android/app/build.gradle || true
fi

# 4b. Memory guard: the build daemon was OOM-killed once reanimated/worklets landed.
# Keep gradle and kotlin compile workers inside the box's RAM budget.
cat >> android/gradle.properties <<'EOF'

# OOM guard for small build boxes
org.gradle.workers.max=2
org.gradle.jvmargs=-Xmx2048m -XX:MaxMetaspaceSize=512m
kotlin.daemon.jvmargs=-Xmx1024m
kotlin.compiler.execution.strategy=in-process
# The box only has JDK 21; Kotlin would otherwise emit 21 bytecode while the
# Android/Java tasks compile at 17 -> "Inconsistent JVM Target Compatibility".
kotlin.jvm.target.validation.mode=warning
EOF

# 4c. Align every Kotlin module with the Java 17 target that RN/Expo set.
#     Must run AFTER prebuild (which regenerates android/) or it is wiped.
cat >> android/build.gradle <<'EOF'

// ZerotheBot: force Kotlin bytecode to Java 17 on a JDK-21-only build box.
subprojects { sub ->
  def pinKotlinTarget = {
    sub.tasks.matching { it.class.name.contains('KotlinCompile') }.configureEach { t ->
      try {
        t.kotlinOptions.jvmTarget = '17'
      } catch (Throwable ignored) {
        try {
          t.compilerOptions.jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
        } catch (Throwable ignored2) { }
      }
    }
  }
  // Some subprojects (Expo modules) are already evaluated by the root plugins above.
  if (sub.state.executed) {
    pinKotlinTarget()
  } else {
    sub.afterEvaluate { pinKotlinTarget() }
  }
}
EOF

# 5. Build. PLAY_AAB=1 asks for the Play Bundle (.aab) AND a release-signed APK
#    (same signing, same content — the APK is what we can boot-test); default
#    keeps the sideload APK flow.
if [ "${PLAY_AAB:-0}" = "1" ]; then TASK="bundleRelease assembleRelease"; else TASK="assembleRelease"; fi
log "gradle $TASK (first run downloads a lot, be patient)"
( cd android && ./gradlew $TASK --no-daemon --console=plain ) > gradle.log 2>&1 || { tail -n 40 gradle.log; exit 1; }
tail -n 6 gradle.log

if [ "${PLAY_AAB:-0}" = "1" ]; then
  AAB="android/app/build/outputs/bundle/release/app-release.aab"
  [ -f "$AAB" ] || AAB="android/app/build/outputs/bundle/release/app-release-unsigned.aab"
  APK="android/app/build/outputs/apk/release/app-release.apk"
  [ -f "$APK" ] || APK="android/app/build/outputs/apk/release/app-release-unsigned.apk"
  log "AAB built: $AAB ($(du -h "$AAB" | cut -f1))"
  log "APK built (release-signed, for device checks): $APK ($(du -h "$APK" | cut -f1))"
else
  APK="android/app/build/outputs/apk/release/app-release.apk"
  [ -f "$APK" ] || APK="android/app/build/outputs/apk/release/app-release-unsigned.apk"
  log "APK built: $APK ($(du -h "$APK" | cut -f1))"
fi
log "done"
