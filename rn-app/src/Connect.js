// Connectors & MCP servers: grant apps to your Bots from the phone.
// Authorization and setup pages open in an in-app overlay (WebView), closing
// automatically when the platform hands control back to the ZerotheBot server.
import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, TextInput, Pressable, ScrollView, Modal, StyleSheet, ActivityIndicator, Linking, Platform, AppState, Alert } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { WebView } from 'react-native-webview';
import { api, API_BASE } from './session';
import { C } from './theme';

const MCP_PRESETS_PUBLIC = [
  { name: 'learn', label: 'Microsoft Learn', url: 'https://learn.microsoft.com/api/mcp', transport: 'http' },
  { name: 'deepwiki', label: 'DeepWiki (repo docs)', url: 'https://mcp.deepwiki.com/mcp', transport: 'http' },
  { name: 'context7', label: 'Context7 (library docs)', url: 'https://mcp.context7.com/mcp', transport: 'http' },
];
const MCP_PRESETS_TOKEN = [
  { name: 'github', label: 'GitHub', url: 'https://api.githubcopilot.com/mcp/' },
  { name: 'linear', label: 'Linear', url: 'https://mcp.linear.app/sse', transport: 'sse' },
  { name: 'notion', label: 'Notion', url: 'https://mcp.notion.com/mcp' },
  { name: 'slack', label: 'Slack', url: 'https://mcp.slack.com/mcp' },
  { name: 'atlassian', label: 'Jira/Confluence', url: 'https://mcp.atlassian.com/mcp' },
  { name: 'stripe', label: 'Stripe', url: 'https://mcp.stripe.com' },
];

export default function Connect({ onBack, onMenu }) {
  const [tab, setTab] = useState('conn');
  const ins = useSafeAreaInsets();
  return (
    <View style={st.box}>
      <View style={[st.hdr, { paddingTop: 14 + ins.top }]}>
        <Pressable onPress={onBack} hitSlop={10}><Text style={st.back}>‹</Text></Pressable>
        <Text style={st.h1}>Connections</Text>
        <Pressable onPress={onMenu || onBack} hitSlop={10}><Text style={[st.back, { textAlign: 'right' }]}>☰</Text></Pressable>
      </View>
      <View style={st.tabs}>
        <Pressable onPress={() => setTab('conn')}><Text style={[st.tab, tab === 'conn' && st.tabOn]}>Connectors</Text></Pressable>
        <Pressable onPress={() => setTab('mcp')}><Text style={[st.tab, tab === 'mcp' && st.tabOn]}>MCP servers</Text></Pressable>
      </View>
      {tab === 'conn' ? <Connectors /> : <Mcp />}
    </View>
  );
}

// ------------------------------------------------------------ connectors
function Connectors() {
  const [rows, setRows] = useState(null);
  const [catalog, setCatalog] = useState([]);      // installable connectors (bundled + catalog URLs)
  const [canInstall, setCanInstall] = useState(false);
  const [gitUrl, setGitUrl] = useState('');
  const [msg, setMsg] = useState('');
  const [busyId, setBusyId] = useState('');
  const [overlay, setOverlay] = useState(null);   // {url, ptit, auto}
  // remember the last connect URL attempt for retry / debugging + external open
  const [externalUrl, setExternalUrl] = useState('');
  const load = useCallback(async () => {
    try {
      const d = await api('/api/plugins');
      setRows(d.plugins || []);
      setCatalog(d.catalog || []);
      setCanInstall(!!d.install_allowed);
    } catch (e) { setMsg(String(e.message || e)); }
  }, []);
  useEffect(() => { load(); }, [load]);
  // Coming back from the system browser must refresh the list even when the deep
  // link is swallowed by Android — otherwise Connect looks like it did nothing.
  useEffect(() => {
    const sub = AppState.addEventListener('change', (s) => { if (s === 'active') load(); });
    return () => sub && sub.remove && sub.remove();
  }, [load]);
  useEffect(() => {
    const onUrl = ({ url }) => {
      const m = String(url || '').match(/plugins\/go\?pid=([^&]+)/);
      if (!m) return;
      setMsg(`${m[1]} connected ✓`);
      load();
    };
    let sub = { remove: () => {} };
    try { sub = Linking.addEventListener('url', onUrl); } catch (e) { /* older RN */ }
    Linking.getInitialURL().then((u) => u && onUrl({ url: u })).catch(() => {});
    return () => sub.remove && sub.remove();
  }, [load]);
  const done = async (id) => {
    setOverlay(null); setMsg('');
    await load();
    if (id) setMsg((rows || []).find((r) => r.id === id)?.name + ' connected ✓');
  };
  const act = async (p, what) => {
    // `p` may be an object or (legacy) an id string for 'test'
    const obj = (typeof p === 'string') ? (rows || []).find(r => r.id === p) || { id: p } : p;
    const pid = obj.id || p;
    setBusyId(pid + what); setMsg('');
    try {
      if (what === 'connect') {
        if (Platform.OS !== 'web') {
          const d = await api('/api/plugins/' + pid + '/connect?mobile=1', { method: 'POST', body: '{}' });
          if (/^https?:/.test(d.url || '')) {
            setExternalUrl(d.url);
            setMsg('Finish signing in to ' + (obj.name || pid) + ' in your browser…');
            await Linking.openURL(d.url).catch((ee) => setMsg('Could not open browser: ' + String(ee.message || ee)));
          } else setMsg(pid + ': could not start the sign-in.');
        } else {
          const d = await api('/api/plugins/' + pid + '/connect', { method: 'POST', body: '{}' });
          if (/^https?:/.test(d.url || '')) setOverlay({ url: d.url, name: obj.name || pid, auto: true, pid: pid });
          else setMsg(pid + ': could not start the sign-in.');
        }
      } else if (what === 'setup' && obj.setup_url) {
        // Some vendor setup / console pages block embedded webviews (Google):
        // open those externally so you don't get "this browser may not be secure".
        const u = String(obj.setup_url || '');
        const blocked = /accounts\.google\.com|console\.cloud\.google\.com|google\.com/i.test(u);
        if (blocked && Platform.OS !== 'web') {
          setExternalUrl(u);
          await Linking.openURL(u).catch(() => setOverlay({ url: u, name: obj.name || pid, auto: false, pid: '' }));
        } else {
          setOverlay({ url: u, name: obj.name || pid, auto: false, pid: '' });
        }
      } else if (what === 'test') {
        const d = await api('/api/plugins/' + pid + '/test', { method: 'POST', body: '{}' });
        setMsg(pid + ': ' + (d.message || 'ok'));
      }
      await load();
    } catch (e) { setMsg(pid + ': ' + (e.message || 'failed')); }
    setBusyId('');
  };
  const installOne = async (c) => {
    setBusyId('i:' + c.id); setMsg('');
    try {
      await api('/api/plugins/install', { method: 'POST', body: JSON.stringify({ source: c.source || ('bundled:plugins/' + c.id) }) });
      setMsg((c.name || c.id) + ' installed ✓');
      await load();
    } catch (e) { setMsg((c.name || c.id) + ': ' + (e.message || 'could not install')); }
    setBusyId('');
  };
  const installAll = async () => {
    const list = (catalog || []).filter((c) => !c.installed);
    if (!list.length) return;
    setBusyId('i:all'); setMsg('Installing ' + list.length + ' connectors…');
    let ok = 0;
    for (const c of list) {
      try {
        await api('/api/plugins/install', { method: 'POST', body: JSON.stringify({ source: c.source || ('bundled:plugins/' + c.id) }) });
        ok += 1;
      } catch (e) { /* keep going: one bad manifest must not stop the rest */ }
    }
    setBusyId('');
    setMsg(ok + ' of ' + list.length + ' connectors installed ✓');
    await load();
  };
  const installGit = async () => {
    const src = gitUrl.trim();
    if (!src) return;
    setBusyId('i:git'); setMsg('');
    try {
      const r = await api('/api/plugins/install', { method: 'POST', body: JSON.stringify({ source: src }) });
      setMsg((r && r.name ? r.name : 'Plugin') + ' installed ✓');
      setGitUrl('');
      await load();
    } catch (e) { setMsg('Install failed: ' + (e.message || e)); }
    setBusyId('');
  };
  const removeOne = (p) => {
    Alert.alert('Remove ' + p.name + '?', 'Its keys are deleted from this server. You can install it again any time.', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Remove', style: 'destructive', onPress: async () => {
          setBusyId('r:' + p.id);
          try { await api('/api/plugins/' + p.id, { method: 'DELETE' }); setMsg(p.name + ' removed'); await load(); }
          catch (e) { setMsg(p.name + ': ' + (e.message || 'could not remove')); }
          setBusyId('');
        },
      },
    ]);
  };
  const avail = (catalog || []).filter((c) => !c.installed);
  if (!rows) return <Text style={l.cap}>Loading connectors…</Text>;
  return (
    <View style={{ flex: 1 }}>
      {!!msg && (
        <View style={st.statusBar}>
          <Text style={st.statusT} numberOfLines={3}>{msg}</Text>
          <Pressable onPress={() => setMsg('')} hitSlop={8}><Text style={st.statusX}>✕</Text></Pressable>
        </View>
      )}
      <ScrollView style={{ flex: 1 }} contentContainerStyle={st.list}>
        {rows.map((p) => <Row key={p.id} p={p} act={act} busyId={busyId} onMsg={setMsg} onRemove={removeOne} reload={load} />)}

        {!!avail.length && (
          <View style={st.card}>
            <Text style={st.cardName}>Available connectors — {avail.length} not installed</Text>
            <Text style={l.cap}>The full set that ships with ZerotheBot. Install one, tap Connect, then grant it to a Bot so it can use it.</Text>
            <View style={{ gap: 8, marginTop: 8 }}>
              {avail.map((c) => (
                <View key={c.id} style={st.availRow}>
                  <View style={{ flex: 1 }}>
                    <Text style={st.availName} numberOfLines={1}>{c.name || c.id}</Text>
                    <Text style={l.cap} numberOfLines={2}>{c.description}</Text>
                  </View>
                  <PBtn label={busyId === 'i:' + c.id ? '…' : 'Install'} onPress={() => installOne(c)} />
                </View>
              ))}
            </View>
            <Pressable onPress={installAll} disabled={busyId === 'i:all'} style={[l.btn, { backgroundColor: C.solid, marginTop: 10 }]}>
              <Text style={l.btnT}>{busyId === 'i:all' ? 'Installing…' : '＋ Install all ' + avail.length}</Text>
            </Pressable>
          </View>
        )}

        {canInstall && (
          <View style={st.card}>
            <Text style={st.cardName}>Install from a git URL or folder</Text>
            <Text style={l.cap}>Paste a repository URL (or a path on the server) whose top folder has a plugin.json.</Text>
            <TextInput style={[l.inl, { marginTop: 8 }]} value={gitUrl} onChangeText={setGitUrl}
              placeholder="https://github.com/you/zerothebot-plugin" placeholderTextColor={C.muted} autoCapitalize="none" autoCorrect={false} />
            <View style={{ flexDirection: 'row', gap: 8, marginTop: 8 }}>
              <PBtn label={busyId === 'i:git' ? 'installing…' : 'Install'} onPress={installGit} />
            </View>
          </View>
        )}
        <Pressable onPress={load} style={[l.btn, { backgroundColor: C.panel2, borderWidth: 1, borderColor: C.line }]}><Text style={[l.btnT, { color: C.text }]}>↻ Refresh</Text></Pressable>
        {!!externalUrl && (
          <Pressable onPress={() => Linking.openURL(externalUrl).catch(()=>{})} style={[l.btn, { backgroundColor: '#111b21', borderWidth: 1, borderColor: C.line }]}>
            <Text style={[l.btnT, { color: '#53bdeb' }]}>Open last link in browser ↗</Text>
          </Pressable>
        )}
        <Text style={st.foot}>
          Sign-in opens in your browser and comes back to the app by itself. Google setup/console
          pages also open in your browser (they block in-app views). Bots see a connector's tools
          only after you grant it in the Bot's settings.
        </Text>
      </ScrollView>
      {!!overlay && <AuthModal o={overlay} onClose={() => done(overlay.auto ? overlay.pid : '')} />}
    </View>
  );
}

// ------------------------------------------------------------ in-app auth overlay
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36';

export function AuthModal({ o, onClose, onUrl }) {
  const [loaded, setLoaded] = useState(false);
  const ins = useSafeAreaInsets();
  const nav = (s) => {
    const u = String(s && s.url || '');
    if (onUrl) onUrl(u);
    if (o.auto && u.includes('/oauth/callback')) { setTimeout(onClose, 900); return true; }
    return false;
  };
  return (
    <Modal animationType="slide" transparent={false} visible onRequestClose={onClose}>
      <View style={{ flex: 1, backgroundColor: C.bg }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, padding: 12, paddingTop: 12 + (ins.top || 0), backgroundColor: C.panel, borderBottomWidth: 1, borderBottomColor: C.line }}>
          <Pressable onPress={onClose} hitSlop={8}><Text style={st.back}>✕</Text></Pressable>
          <Text style={{ color: C.text, fontSize: 15, fontWeight: '800', flex: 1 }} numberOfLines={1}>{o.title || (o.auto ? 'Sign in to ' + o.name : o.name + ' setup')}</Text>
          <Pressable onPress={onClose} hitSlop={8}>
            <Text style={{ color: o.auto ? C.ok : C.text, fontWeight: '800', fontSize: 14 }}>{o.auto ? 'Done' : 'Close'}</Text>
          </Pressable>
        </View>
        {!loaded && (
          <View style={{ padding: 8 }}><ActivityIndicator color={C.ok} /></View>
        )}
        <WebView
          source={{ uri: o.url }}
          userAgent={UA}
          style={{ flex: 1, backgroundColor: '#fff' }}
          onLoadEnd={() => setLoaded(true)}
          onNavigationStateChange={(s) => { nav(s); }}
          onShouldStartLoadWithRequest={(r) => {
            const u = String(r.url || '');
            if (r.isTopFrame !== false && onUrl && u.includes('/auth/google-done?tok=')) { onUrl(u); return false; }
            if (r.isTopFrame !== false && o.auto && u.includes('/oauth/callback')) { setTimeout(onClose, 900); return false; }
            return true;
          }}
          sharedCookiesEnabled
          thirdPartyCookiesEnabled
          setSupportMultipleWindows={false}
        />
        {o.auto && (
          <Text style={{ color: C.muted, fontSize: 11.5, textAlign: 'center', padding: 8 }}>
            When you approve, this closes itself. If a site refuses sign-in here, open it outside the app.
          </Text>
        )}
      </View>
    </Modal>
  );
}

function Row({ p, act, busyId, onMsg, onRemove, reload }) {
  const [open, setOpen] = useState(false);
  const [vals, setVals] = useState({});
  const [saved, setSaved] = useState(false);
  const [busyLocal, setBusyLocal] = useState(false);
  const save = async () => {
    const body = {}; for (const f of (p.fields || [])) if (String(vals[f.key] || '').trim()) body[f.key] = vals[f.key];
    if (!Object.keys(body).length) { setSaved(false); if (onMsg) onMsg(p.name + ': nothing to save yet.'); return; }
    try {
      await api('/api/plugins/' + p.id + '/config', { method: 'PUT', body: JSON.stringify(body) });
      setSaved(true);
      if (onMsg) onMsg(p.name + ': keys saved ✓');
    } catch (e) {
      // Never fail silently: a rejected save used to look like a dead Connect button.
      setSaved(false);
      if (onMsg) onMsg(p.name + ': ' + (e.message || 'could not save the keys'));
    }
  };
  const chip = p.oauth && !p.connected ? ['not connected', C.warn] : p.configured ? ['ready', C.ok] : ['needs setup', C.warn];
  const hasFields = (p.fields || []).length > 0;
  // One-field connectors (Notion, Linear, GitHub, Asana PAT, HubSpot private app,
  // X bearer, Mouser, Onshape keys) should not need a hidden form: paste the token
  // right on the card, save, and test it in one tap.
  const tokenField = (p.fields || []).find((f) => ['token', 'api_key', 'bot_token'].includes(f.key));
  const quick = tokenField && !p.oauth ? tokenField : null;
  const quickSave = async () => {
    const v = String(vals[quick.key] || '').trim();
    if (!v) { if (onMsg) onMsg(p.name + ': paste the ' + quick.label.toLowerCase() + ' first.'); return; }
    setBusyLocal(true);
    try {
      await api('/api/plugins/' + p.id + '/config', { method: 'PUT', body: JSON.stringify({ [quick.key]: v }) });
      setVals((o) => ({ ...o, [quick.key]: '' }));
      setSaved(true);
      let t = '';
      try { const r = await api('/api/plugins/' + p.id + '/test', { method: 'POST' }); t = r.message || ''; } catch (e) { t = e.message || ''; }
      if (onMsg) onMsg(p.name + ': connected ✓' + (t ? ' — ' + t : ''));
      if (reload) await reload();
    } catch (e) {
      if (onMsg) onMsg(p.name + ': ' + (e.message || 'could not save the token'));
    }
    setBusyLocal(false);
  };
  return (
    <View style={st.card}>
      <View style={st.cardTop}>
        <Text style={st.cardName} numberOfLines={1}>{p.name}</Text>
        <Text style={[st.chip, { color: chip[1], borderColor: chip[1] }]}>{chip[0]}</Text>
      </View>
      <Text style={l.cap} numberOfLines={2}>{p.description}</Text>
      {!!p.managed && (
        <Text style={st.managed}>Uses this server’s Google app — just tap Connect, nothing to paste.</Text>
      )}
      {quick && !p.configured && (
        <View style={{ gap: 6, marginTop: 4 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <TextInput style={[l.inl, { flex: 1 }]} value={vals[quick.key] || ''}
              onChangeText={(t) => setVals((o) => ({ ...o, [quick.key]: t }))}
              placeholder={quick.label} placeholderTextColor={C.muted}
              secureTextEntry={!!quick.secret} autoCapitalize="none" autoCorrect={false} />
            <PBtn label={busyLocal ? '…' : 'Connect'} onPress={quickSave} />
          </View>
          {!!p.setup_url && (
            <Pressable onPress={() => act(p, 'setup')} hitSlop={6}>
              <Text style={[l.cap, { color: C.ok, marginBottom: 0 }]}>Get the {quick.label.toLowerCase()} ↗</Text>
            </Pressable>
          )}
        </View>
      )}
      <View style={{ flexDirection: 'row', gap: 8, marginTop: 6, flexWrap: 'wrap' }}>
        {p.oauth ? (
          <PBtn label={busyId === p.id + 'connect' ? 'opening…' : 'Connect'} onPress={() => act(p, 'connect')} />
        ) : null}
        {!!p.setup_url && <PBtn label={p.setup_label || 'Open setup'} ghost onPress={() => act(p, 'setup')} />}
        <PBtn label={busyId === p.id + 'test' ? 'testing…' : 'Test'} ghost onPress={() => act(p.id, 'test')} />
        {hasFields ? <PBtn label={open ? 'Hide setup' : 'Key / details'} ghost onPress={() => setOpen(!open)} /> : null}
        {p.removable ? <PBtn label="Remove" ghost onPress={() => onRemove && onRemove(p)} /> : null}
      </View>
      {open && hasFields && (
        <View style={st.form}>
          {(p.fields || []).map((f) => (
            <View key={f.key} style={{ gap: 4, marginBottom: 6 }}>
              <Text style={l.lbl}>{f.label}{f.secret ? ' (kept secret)' : ''}</Text>
              <TextInput style={l.inl} value={vals[f.key] !== undefined ? vals[f.key] : String(f.value || '')}
                secureTextEntry={!!f.secret} autoCapitalize="none" autoCorrect={false}
                placeholder={f.set ? '•••• set — type to replace' : ''} placeholderTextColor={C.muted}
                onChangeText={(t) => setVals((o) => ({ ...o, [f.key]: t }))} />
            </View>
          ))}
          <PBtn label={saved ? 'Saved ✓' : 'Save keys'} onPress={save} />
          {!!p.help && <Text style={l.help}>{p.help}</Text>}
        </View>
      )}
      {!open && !!p.help && !!p.oauth ? null : null}
    </View>
  );
}

function PBtn({ label, onPress, ghost, busy }) {
  return (
    <Pressable onPress={onPress} disabled={busy} style={({ pressed }) => [st.pb, !ghost && st.pbOn, pressed && { opacity: .85 }]}>
      <Text style={[st.pbT, !ghost && { color: C.onSolid }]}>{label}</Text>
    </Pressable>
  );
}

// ------------------------------------------------------------ MCP servers
function Mcp() {
  const [rows, setRows] = useState(null);
  const [msg, setMsg] = useState('');
  const [importJson, setImportJson] = useState('');
  const load = useCallback(async () => {
    try { setRows(await api('/api/mcp')); } catch (e) { setMsg(String(e.message || e)); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const addPreset = async (preset) => {
    setMsg('');
    try {
      await api('/api/mcp', { method: 'POST', body: JSON.stringify({ ...preset, enabled: true, trust: 'ask' }) });
      setMsg(preset.label + ' added and connecting…');
      await load();
    } catch (e) { setMsg(preset.label + ': ' + (e.message || 'failed')); }
  };
  const addImport = async () => {
    setMsg('');
    try {
      const d = await api('/api/mcp', { method: 'POST', body: JSON.stringify({ import_json: importJson.trim() }) });
      setMsg('Added ' + (d.servers || []).length + ' server(s).');
      setImportJson('');
      await load();
    } catch (e) { setMsg((e.message || 'failed')); }
  };
  const restart = async (sid) => {
    try { await api('/api/mcp/' + sid + '/restart', { method: 'POST', body: '{}' }); await load(); setMsg('Restarted.'); }
    catch (e) { setMsg('Restart failed: ' + (e.message || '')); }
  };
  if (!rows) return <Text style={l.cap}>Loading MCP servers…</Text>;
  return (
    <ScrollView style={{ flex: 1 }} contentContainerStyle={st.list}>
      <Text style={l.lbl}>One-tap add (free, no login)</Text>
      <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
        {MCP_PRESETS_PUBLIC.map((p) => <PBtn key={p.name} label={p.label} ghost onPress={() => addPreset({ ...p, name: p.name })} />)}
      </View>
      <Text style={l.lbl}>Popular (needs your access token)</Text>
      <Text style={l.help}>Tap to add as "stopped" until you paste its token via Import below (header {"{"}"Authorization": "Bearer TOKEN"{"}"}).</Text>
      <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
        {MCP_PRESETS_TOKEN.map((p) => <PBtn key={p.name} label={p.label} ghost onPress={() => setMsg('Import ' + p.name + " with import JSON below:\n{\"mcpServers\":{\"" + p.name + "\":{\"transport\":\"http\",\"url\":\"" + p.url + "\",\"headers\":{\"Authorization\":\"Bearer YOUR_TOKEN\"}}}}")} />)}
      </View>
      <Text style={l.lbl}>Your servers</Text>
      {!rows.length && <Text style={l.cap}>None yet — add one above.</Text>}
      {rows.map((r) => (
        <View key={r.id} style={st.card}>
          <View style={st.cardTop}>
            <Text style={st.cardName}>mcp:{r.name}</Text>
            <Text style={[st.chip, { color: r.status === 'connected' ? C.ok : C.warn, borderColor: r.status === 'connected' ? C.ok : C.warn }]}>{r.status}</Text>
          </View>
          <Text style={l.cap}>tools: {r.tool_count || 0}{r.error ? ' · ' + String(r.error).slice(0, 80) : ''}{r.enabled ? '' : ' · disabled'}</Text>
          <View style={{ flexDirection: 'row', gap: 8, marginTop: 6 }}>
            <PBtn label="↻ Restart" ghost onPress={() => restart(r.id)} />
          </View>
        </View>
      ))}
      <Text style={l.lbl}>Import servers (paste JSON)</Text>
      <TextInput style={[l.inl, { minHeight: 90 }]} multiline value={importJson} onChangeText={setImportJson}
        placeholder={'{"mcpServers":{"fs":{"transport":"http","url":"https://…"}}}'} placeholderTextColor={C.muted}
        autoCapitalize="none" autoCorrect={false} />
      <PBtn label="Add servers" onPress={addImport} />
      {!!msg && <Text style={l.msg}>{msg}</Text>}
      <Text style={st.foot}>MCP tools land as mcp:&lt;server&gt; on the Bots you grant them — request_access inside a Bot also works.</Text>
    </ScrollView>
  );
}

const l = {
  cap: { color: C.muted, fontSize: 12.5, lineHeight: 17, marginTop: 2 },
  lbl: { color: C.muted, fontSize: 12, textTransform: 'uppercase', letterSpacing: 1, marginTop: 10 },
  inl: { backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 12, color: C.text, padding: 10, fontSize: 14 },
  btn: { borderRadius: 12, padding: 12, alignItems: 'center', marginTop: 12 },
  btnT: { color: C.onSolid, fontWeight: '800', fontSize: 14 },
  msg: { color: C.accentText || C.text, fontSize: 12.5, marginTop: 10 },
  help: { color: C.muted, fontSize: 12, lineHeight: 16, marginTop: 6 },
};

const st = StyleSheet.create({
  box: { flex: 1, backgroundColor: C.bg },
  hdr: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', padding: 14, paddingBottom: 6 },
  back: { color: C.text, fontSize: 28, fontWeight: '600', width: 30 },
  h1: { color: C.text, fontSize: 22, fontWeight: '900' },
  tabs: { flexDirection: 'row', gap: 10, paddingHorizontal: 14, paddingBottom: 8 },
  tab: { color: C.muted, fontSize: 14, fontWeight: '700', paddingVertical: 6, paddingHorizontal: 12, borderRadius: 999, backgroundColor: C.panel2, overflow: 'hidden' },
  tabOn: { color: C.onSolid, backgroundColor: C.solid },
  list: { padding: 14, gap: 12, paddingBottom: 60 },
  card: { backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 16, padding: 12 },
  cardTop: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 2 },
  cardName: { color: C.text, fontSize: 15.5, fontWeight: '700', flex: 1, marginRight: 8 },
  chip: { fontSize: 11.5, fontWeight: '700', borderWidth: 1, borderRadius: 999, paddingHorizontal: 8, paddingVertical: 3, overflow: 'hidden' },
  managed: { color: C.ok, fontSize: 12, lineHeight: 16, marginTop: 4 },
  availRow: { flexDirection: 'row', alignItems: 'center', gap: 10, borderTopWidth: 1, borderColor: C.line, paddingTop: 8 },
  availName: { color: C.text, fontSize: 14, fontWeight: '700', marginBottom: 1 },
  pb: { backgroundColor: C.panel2, borderRadius: 10, borderWidth: 1, borderColor: C.line, paddingVertical: 8, paddingHorizontal: 12 },
  pbOn: { backgroundColor: C.solid, borderColor: C.solid },
  pbT: { color: C.text, fontSize: 13.5, fontWeight: '600' },
  form: { marginTop: 10, borderTopWidth: 1, borderColor: C.line, paddingTop: 8 },
  foot: { color: C.muted, fontSize: 12, lineHeight: 17, marginTop: 4 },
  statusBar: { flexDirection: 'row', alignItems: 'center', gap: 8, backgroundColor: '#132a24', borderBottomWidth: 1, borderColor: '#1f4438', paddingHorizontal: 14, paddingVertical: 9 },
  statusT: { color: '#7ee2a8', fontSize: 12.5, lineHeight: 17, flex: 1 },
  statusX: { color: '#7ee2a8', fontSize: 14, fontWeight: '800' },
});
