#!/usr/bin/env bash
# Publish the built RN APK as the canonical ZerotheBot.apk + write the self-update manifest.
# Run after build.sh: bash publish.sh
set -e
cd "$(dirname "$0")"
APK=android/app/build/outputs/apk/release/app-release.apk
BT=/data/home/android-sdk/build-tools/34.0.0
BADGE=$("$BT/aapt" dump badging "$APK" 2>/dev/null | grep '^package:' | head -1)
VNAME=$(echo "$BADGE" | sed -E "s/.*versionName='([^']+)'.*/\1/")
VCODE=$(echo "$BADGE" | sed -E "s/.*versionCode='([0-9]+)'.*/\1/")
SHA=$(sha256sum "$APK" | cut -d' ' -f1)
[ -n "$VNAME" ] || { echo "could not read version from APK"; exit 1; }

cp -f "$APK" /tmp/apkserve/ZerotheBot.apk
cp -f "$APK" /data/home/zerothebot/dist/ZerotheBot-Android-"$VNAME".apk
rm -f /data/home/zerothebot/dist/ZerotheBot-Android-1.6.0.apk /data/home/zerothebot/dist/ZerotheBot-react-native-1.0.0.apk /tmp/apkserve/ZerotheBot-rn.apk

python3 - "$VNAME" "$VCODE" "$SHA" <<'PY'
import json, sys
vname, vcode, sha = sys.argv[1], int(sys.argv[2]), sys.argv[3]
m = {"code": vcode, "version": vname, "apk": "https://8888-agquljzsz7k42vgoenj7qr6fim.instabox.tech/ZerotheBot.apk",
     "sha256": sha, "notes": ""}
open("/tmp/apkserve/update.json", "w").write(json.dumps(m))
print("manifest:", json.dumps(m))
PY

curl -s -o /dev/null -w 'apk -> http %{http_code}  %{size_download} bytes\n' https://8888-agquljzsz7k42vgoenj7qr6fim.instabox.tech/ZerotheBot.apk
curl -s -o /dev/null -w 'update.json -> http %{http_code}\n' https://8888-agquljzsz7k42vgoenj7qr6fim.instabox.tech/update.json
echo "published $VNAME (code $VCODE) sha=$SHA"
