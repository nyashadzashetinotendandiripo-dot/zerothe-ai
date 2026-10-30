#!/usr/bin/env bash
# Publish a JS-only over-the-air update (expo-updates, self-hosted).
# Users get it on next app launch — no APK download. Native changes still need build.sh.
set -euo pipefail
cd "$(dirname "$0")"
UPD="${ZB_UPDATES_DIR:-/data/home/.local/share/OpenGrokBot/updates}"
BASE="${ZB_PUBLIC_BASE:-https://8765-agquljzsz7k42vgoenj7qr6fim.instabox.tech}"
mkdir -p "$UPD/assets"
rm -rf dist-ota
npx expo export --platform android --output-dir dist-ota --clear > /tmp/zb-ota-export.log 2>&1 || { tail -20 /tmp/zb-ota-export.log; exit 1; }
node - "$PWD/dist-ota" "$UPD" "$BASE" <<'NODE'
const fs = require('fs'), path = require('path'), crypto = require('crypto');
const [dir, out, base] = process.argv.slice(2);
const md = JSON.parse(fs.readFileSync(path.join(dir, 'metadata.json'), 'utf8'));
const app = JSON.parse(fs.readFileSync(path.join(process.cwd(), 'app.json'), 'utf8'));
const fm = (md.fileMetadata && md.fileMetadata.android) || {};
if (!fm.bundle) throw new Error('metadata.json has no android bundle');
const sha = (p) => crypto.createHash('sha256').update(fs.readFileSync(p)).digest('base64url');
const MIME = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp',
  svg: 'image/svg+xml', ttf: 'font/ttf', otf: 'font/otf', json: 'application/json' };
const copy = (rel) => {
  const src = path.join(dir, rel);
  const hash = sha(src);
  const ext = path.extname(rel) || '.bin';
  const name = hash + ext;
  fs.copyFileSync(src, path.join(out, 'assets', name));
  return { hash, name, ext: ext.replace('.', '') };
};
const bundle = copy(fm.bundle);
const assets = (fm.assets || []).map((a) => {
  const rel = a.path || a;
  const r = copy(rel);
  return { hash: r.hash, key: r.hash, contentType: MIME[r.ext] || 'application/octet-stream',
    fileExtension: '.' + r.ext, url: `${base}/updates/assets/${r.name}` };
});
const manifest = {
  id: crypto.randomUUID(), createdAt: new Date().toISOString(), runtimeVersion: app.expo.runtimeVersion || '1',
  launchAsset: { key: 'bundle', contentType: 'application/javascript', fileExtension: '.' + bundle.ext,
    url: `${base}/updates/assets/${bundle.name}` },
  assets, metadata: {},
  // expo-constants reads the app config from here; without it Constants.expoConfig
  // is null after an OTA update (version/code would fall back to stale defaults).
  extra: { expoClient: JSON.parse(fs.readFileSync(path.join(process.cwd(), 'app.json'), 'utf8')).expo || {} },
};
fs.writeFileSync(path.join(out, 'manifest.json'), JSON.stringify(manifest, null, 2));
// Housekeeping: bundles are content-addressed, so an old file is never needed
// once no recent manifest points at it. Keep the current bundle and anything
// touched in the last week; drop the rest so the disk does not fill up.
const keep = new Set([bundle.name, ...assets.map((a) => a.name)]);
const cutoff = Date.now() - 7 * 24 * 3600 * 1000;
let pruned = 0, freed = 0;
for (const f of fs.readdirSync(path.join(out, 'assets'))) {
  const p = path.join(out, 'assets', f);
  const st = fs.statSync(p);
  if (keep.has(f) || st.mtimeMs > cutoff) continue;
  freed += st.size;
  fs.unlinkSync(p);
  pruned++;
}
console.log(`OTA ${manifest.id.slice(0, 8)} bundle=${bundle.name} assets=${assets.length} pruned=${pruned} freed=${(freed / 1048576).toFixed(0)}MB`);
NODE
echo "manifest: $BASE/updates/manifest"
curl -s -o /dev/null -w 'manifest http %{http_code}\n' "$BASE/updates/manifest" -H 'expo-platform: android' -H "expo-runtime-version: $(node -p "require('./app.json').expo.runtimeVersion || '1'")" -H 'expo-protocol-version: 1'
