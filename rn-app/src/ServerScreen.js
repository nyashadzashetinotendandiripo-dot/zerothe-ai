import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Linking, Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { WebView } from 'react-native-webview';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { api, conf } from './session';
import { C } from './theme';
import { TopBar } from './Screens';

// Everything about the machine the Bots run on: health, backups, public ports,
// secrets, a shell and the template upgrade plan. The heavy lifting lives in
// core/host.py; this screen is the phone's window into it.

const S = StyleSheet.create({
  screen: { flex: 1, backgroundColor: C.bg },
  pad: { padding: 14, gap: 10, paddingBottom: 40 },
  tabs: { flexDirection: 'row', gap: 6, paddingHorizontal: 12, paddingTop: 10, flexWrap: 'wrap' },
  tab: { paddingHorizontal: 12, paddingVertical: 7, borderRadius: 999, borderWidth: 1, borderColor: C.line, backgroundColor: C.panel2 },
  tabOn: { borderColor: C.ok, backgroundColor: C.okSoft },
  tabT: { color: C.muted, fontSize: 12.5, fontWeight: '800' },
  tabTOn: { color: C.ok },
  card: { backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 16, padding: 13, gap: 8 },
  h: { color: C.text, fontSize: 15, fontWeight: '800' },
  s: { color: C.muted, fontSize: 12.5, lineHeight: 18 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  pill: { borderRadius: 999, borderWidth: 1, paddingHorizontal: 8, paddingVertical: 3 },
  pillT: { fontSize: 11, fontWeight: '800' },
  btn: { borderRadius: 999, borderWidth: 1, borderColor: C.line, backgroundColor: C.panel2, paddingHorizontal: 12, paddingVertical: 8 },
  btnT: { color: C.text, fontSize: 12.5, fontWeight: '800' },
  btnOk: { borderColor: C.ok, backgroundColor: C.okSoft },
  btnOkT: { color: C.ok },
  btnBad: { borderColor: C.bad, backgroundColor: C.badSoft },
  btnBadT: { color: C.bad },
  inp: { backgroundColor: C.bg, borderWidth: 1, borderColor: C.line, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, color: C.text, fontSize: 13.5 },
  mono: { backgroundColor: '#0a0c10', borderWidth: 1, borderColor: C.line, borderRadius: 12, padding: 10, color: '#c9e6d3', fontSize: 11.5, fontFamily: 'monospace' },
  barBg: { height: 5, borderRadius: 3, backgroundColor: C.panel2, overflow: 'hidden', marginTop: 4 },
  barFill: { height: 5 },
  kv: { color: C.text, fontSize: 12.5 },
  link: { color: C.ok, fontSize: 12.5, fontWeight: '700' },
});

function fmtB(n) {
  n = Number(n) || 0;
  if (n >= 1e9) return (n / 1e9).toFixed(1) + ' GB';
  if (n >= 1e6) return (n / 1e6).toFixed(0) + ' MB';
  if (n >= 1e3) return (n / 1e3).toFixed(0) + ' KB';
  return n + ' B';
}
function when(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  if (isNaN(d.getTime())) return String(ts);
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function Bar({ pct, tone }) {
  return (
    <View style={S.barBg}>
      <View style={[S.barFill, { width: Math.max(2, Math.min(100, Number(pct) || 0)) + '%', backgroundColor: tone || C.ok }]} />
    </View>
  );
}

function Btn({ label, onPress, tone, disabled, style }) {
  return (
    <Pressable onPress={disabled ? undefined : onPress} disabled={!!disabled}
      style={[S.btn, tone === 'ok' && S.btnOk, tone === 'bad' && S.btnBad, disabled && { opacity: .45 }, style]}>
      <Text style={[S.btnT, tone === 'ok' && S.btnOkT, tone === 'bad' && S.btnBadT]}>{label}</Text>
    </Pressable>
  );
}

// ------------------------------------------------------------------ health
function Health({ onLive }) {
  const [d, setD] = useState(null);
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState('');
  const load = useCallback(async (refresh) => {
    try { setD(await api('/api/host' + (refresh ? '?refresh=1' : ''))); setErr(''); }
    catch (e) { setErr(String(e.message || e)); }
  }, []);
  useEffect(() => { load(false); const t = setInterval(() => load(false), 20000); return () => clearInterval(t); }, [load]);
  const wake = async () => {
    setBusy('wake');
    try { await api('/api/host/wake', { method: 'POST', body: '{}' }); await load(true); }
    catch (e) { setErr(String(e.message || e)); }
    setBusy('');
  };
  if (!d && !err) return <View style={S.card}><ActivityIndicator color={C.ok} /></View>;
  if (d && !d.configured) return <View style={S.card}><Text style={S.h}>Instabox is not connected</Text><Text style={S.s}>{d.hint}</Text></View>;
  const m = (d && d.machine) || {};
  const st = m.state || 'unknown';
  const tone = st === 'running' ? C.ok : (st === 'suspended' || st === 'paused' ? C.warn : C.bad);
  const cpu = (d && d.cpu && d.cpu.pct) || 0;
  const mem = (d && d.memory) || {};
  const disk = (d && d.disk) || {};
  return (
    <View style={{ gap: 10 }}>
      <View style={S.card}>
        <View style={S.row}>
          <Text style={S.h}>{m.name || 'Server'}</Text>
          <View style={[S.pill, { borderColor: tone }]}><Text style={[S.pillT, { color: tone }]}>{st}</Text></View>
          <View style={{ flex: 1 }} />
          <Text style={S.s}>{m.template} {m.template_version}</Text>
        </View>
        {!!err && <Text style={[S.s, { color: C.bad }]}>{err}</Text>}
        <View style={{ gap: 2 }}>
          <Text style={S.s}>CPU {Math.round(cpu)}%</Text><Bar pct={cpu} />
        </View>
        <View style={{ gap: 2 }}>
          <Text style={S.s}>RAM {Math.round(mem.pct || 0)}% · {fmtB(mem.used)} / {fmtB(mem.total)}</Text>
          <Bar pct={mem.pct} tone={(mem.pct || 0) > 85 ? C.bad : C.ok} />
        </View>
        <View style={{ gap: 2 }}>
          <Text style={S.s}>Disk {Math.round(disk.pct || 0)}% · {fmtB(disk.used)} / {fmtB(disk.total)}</Text>
          <Bar pct={disk.pct} tone={(disk.pct || 0) > 85 ? C.bad : C.ok} />
        </View>
        <Text style={S.s}>
          {(d.processes ? d.processes.count + ' processes' : '')}
          {m.oom_kills ? ' · ' + m.oom_kills + ' OOM kills' : ' · no OOM kills'}
          {m.auto_suspend_seconds ? ' · pauses after ' + Math.round(m.auto_suspend_seconds / 60) + ' min idle' : ''}
        </Text>
        <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
          <Btn label="Refresh" onPress={() => load(true)} />
          {st !== 'running' && <Btn label={busy === 'wake' ? 'Waking…' : 'Wake'} tone="ok" onPress={wake} disabled={busy === 'wake'} />}
          <Btn label="Watch screen" tone="ok" onPress={onLive} />
        </View>
      </View>
      {!!(d.ports || []).length && (
        <View style={S.card}>
          <Text style={S.h}>Public ports</Text>
          {(d.ports || []).map((p) => (
            <Pressable key={p.port} onPress={() => Linking.openURL(p.url).catch(() => {})}>
              <Text style={S.link} numberOfLines={1}>:{p.port} {p.name || ''} · {p.access || 'public'} ↗</Text>
            </Pressable>
          ))}
        </View>
      )}
      <View style={S.card}>
        <Text style={S.h}>Last operations</Text>
        {(d.operations || []).slice().reverse().map((o, i) => (
          <Text key={'o' + i} style={S.s} numberOfLines={1}>{when(o.at)} · {o.type} · {o.state}{o.error ? ' · ' + o.error : ''}</Text>
        ))}
        {!(d.operations || []).length && <Text style={S.s}>No operations yet.</Text>}
      </View>
      <View style={S.card}>
        <Text style={S.h}>Console (tail)</Text>
        {(d.logs || []).slice(-8).map((l, i) => (
          <Text key={'l' + i} style={[S.s, { fontSize: 10.5 }]} numberOfLines={1}>{l}</Text>
        ))}
        {!(d.logs || []).length && <Text style={S.s}>No console output.</Text>}
      </View>
    </View>
  );
}

// ---------------------------------------------------------------- backups
function Backups() {
  const [rows, setRows] = useState(null);
  const [machine, setMachine] = useState('');
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState('');
  const [confirm, setConfirm] = useState('');
  const [target, setTarget] = useState(null);
  const load = useCallback(async () => {
    try {
      const d = await api('/api/host/snapshots');
      setRows(d.snapshots || []);
      const h = await api('/api/host').catch(() => null);
      setMachine(((h && h.machine) || {}).name || '');
      setErr('');
    } catch (e) { setErr(String(e.message || e)); setRows([]); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const save = async () => {
    setBusy('save'); setMsg('');
    try {
      const d = await api('/api/host/snapshots', { method: 'POST', body: JSON.stringify({}) });
      setMsg(d.status === 'ready' ? 'Snapshot saved.' : 'Snapshot is ' + (d.status || 'saving') + ' — it finishes in the background.');
      await load();
    } catch (e) { setErr(String(e.message || e)); }
    setBusy('');
  };
  const remove = async (s) => {
    setBusy('del' + s.id);
    try { await api('/api/host/snapshots/' + s.id, { method: 'DELETE' }); await load(); }
    catch (e) { setErr(String(e.message || e)); }
    setBusy('');
  };
  const doRestore = async () => {
    if (!target) return;
    setBusy('restore'); setMsg('');
    try {
      await api('/api/host/restore', { method: 'POST', body: JSON.stringify({ snapshotId: target.id, confirm }) });
      setMsg('Restore started. The server stops, swaps disks and boots again — give it a minute.');
      setTarget(null); setConfirm('');
      setTimeout(load, 4000);
    } catch (e) { setErr(String(e.message || e)); }
    setBusy('');
  };
  const fork = async () => {
    setBusy('fork'); setMsg('');
    try {
      const d = await api('/api/host/fork', { method: 'POST', body: JSON.stringify({ name: (machine || 'zb') + '-copy' }) });
      setMsg('Copy requested: ' + (d.machineId || d.id || d.name || 'new machine') + '. It counts as a second machine.');
    } catch (e) { setErr(String(e.message || e)); }
    setBusy('');
  };
  return (
    <View style={{ gap: 10 }}>
      <View style={S.card}>
        <Text style={S.h}>Backups</Text>
        <Text style={S.s}>A snapshot holds the installed software and everything under /data: repos, the venv, the thread database and OTA bundles. Restoring keeps the machine's ID and every public URL.</Text>
        <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
          <Btn label={busy === 'save' ? 'Saving…' : 'Save snapshot now'} tone="ok" onPress={save} disabled={busy === 'save'} />
          <Btn label={busy === 'fork' ? 'Copying…' : 'Copy this machine'} onPress={fork} disabled={busy === 'fork'} />
        </View>
        {!!msg && <Text style={[S.s, { color: C.ok }]}>{msg}</Text>}
        {!!err && <Text style={[S.s, { color: C.bad }]}>{err}</Text>}
      </View>
      {rows === null ? <View style={S.card}><ActivityIndicator color={C.ok} /></View> : rows.length === 0 ? (
        <View style={S.card}><Text style={S.s}>No snapshots yet. Save one before changing anything big.</Text></View>
      ) : rows.map((s) => (
        <View key={s.id} style={S.card}>
          <View style={S.row}>
            <Text style={S.h} numberOfLines={1}>{s.name}</Text>
            <View style={[S.pill, { borderColor: s.status === 'ready' ? C.ok : C.warn }]}>
              <Text style={[S.pillT, { color: s.status === 'ready' ? C.ok : C.warn }]}>{s.status}</Text>
            </View>
          </View>
          <Text style={S.s}>{when(s.created_at)} · {fmtB(s.size)}{s.root_only ? ' · software only' : ''}</Text>
          <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
            <Btn label="Restore" tone="bad" onPress={() => { setTarget(s); setConfirm(''); setErr(''); }} disabled={s.status !== 'ready'} />
            <Btn label={busy === 'del' + s.id ? 'Deleting…' : 'Delete'} onPress={() => remove(s)} disabled={busy === 'del' + s.id} />
          </View>
        </View>
      ))}
      <Modal visible={!!target} transparent animationType="fade" onRequestClose={() => setTarget(null)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,.7)', justifyContent: 'center', padding: 22 }}>
          <View style={S.card}>
            <Text style={S.h}>Restore “{target && target.name}”?</Text>
            <Text style={S.s}>Everything written since this snapshot is lost, including running programs. The machine keeps its ID and URLs.</Text>
            <Text style={S.s}>Type the machine name <Text style={{ color: C.text, fontWeight: '800' }}>{machine}</Text> to confirm:</Text>
            <TextInput style={S.inp} value={confirm} onChangeText={setConfirm} autoCapitalize="none" placeholder={machine} placeholderTextColor={C.muted} />
            {!!err && <Text style={[S.s, { color: C.bad }]}>{err}</Text>}
            <View style={{ flexDirection: 'row', gap: 8, justifyContent: 'flex-end' }}>
              <Btn label="Cancel" onPress={() => setTarget(null)} />
              <Btn label={busy === 'restore' ? 'Restoring…' : 'Restore'} tone="bad" disabled={busy === 'restore' || confirm.trim().toLowerCase() !== machine.toLowerCase()} onPress={doRestore} />
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

// ------------------------------------------------------------------ ports
function Ports() {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [port, setPort] = useState('');
  const [name, setName] = useState('');
  const [access, setAccess] = useState('public');
  const [busy, setBusy] = useState('');
  const load = useCallback(async () => {
    try { const d = await api('/api/host/ports'); setRows(d.ports || []); setErr(''); }
    catch (e) { setErr(String(e.message || e)); setRows([]); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const publish = async () => {
    const n = parseInt(port, 10);
    if (!n) { setErr('Enter a port number, e.g. 3000.'); return; }
    setBusy('pub'); setMsg('');
    try {
      const d = await api('/api/host/ports', { method: 'POST', body: JSON.stringify({ port: n, name, access }) });
      setMsg('Published: ' + (d.url || '') + (d.tokenUrl ? '\nPrivate link: ' + d.tokenUrl : ''));
      setPort(''); setName('');
      await load();
    } catch (e) { setErr(String(e.message || e)); }
    setBusy('');
  };
  const unpublish = async (p) => {
    setBusy('un' + p.port);
    try { await api('/api/host/ports/' + p.port, { method: 'DELETE' }); await load(); }
    catch (e) { setErr(String(e.message || e)); }
    setBusy('');
  };
  return (
    <View style={{ gap: 10 }}>
      <View style={S.card}>
        <Text style={S.h}>Publish a port</Text>
        <Text style={S.s}>Give a web server running on the machine an HTTPS URL. Private links only open with their token.</Text>
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <TextInput style={[S.inp, { width: 90 }]} value={port} onChangeText={setPort} keyboardType="number-pad" placeholder="3000" placeholderTextColor={C.muted} />
          <TextInput style={[S.inp, { flex: 1 }]} value={name} onChangeText={setName} placeholder="Name (optional)" placeholderTextColor={C.muted} />
        </View>
        <View style={{ flexDirection: 'row', gap: 8 }}>
          {['public', 'private'].map((a) => (
            <Pressable key={a} onPress={() => setAccess(a)} style={[S.tab, access === a && S.tabOn]}>
              <Text style={[S.tabT, access === a && S.tabTOn]}>{a}</Text>
            </Pressable>
          ))}
          <View style={{ flex: 1 }} />
          <Btn label={busy === 'pub' ? 'Publishing…' : 'Publish'} tone="ok" onPress={publish} disabled={busy === 'pub'} />
        </View>
        {!!msg && <Text style={[S.s, { color: C.ok }]}>{msg}</Text>}
        {!!err && <Text style={[S.s, { color: C.bad }]}>{err}</Text>}
      </View>
      {rows === null ? <View style={S.card}><ActivityIndicator color={C.ok} /></View> : rows.map((p) => (
        <View key={p.port} style={S.card}>
          <View style={S.row}>
            <Text style={S.h}>:{p.port}</Text>
            <Text style={S.s} numberOfLines={1}>{p.name || ''}</Text>
            <View style={{ flex: 1 }} />
            <View style={[S.pill, { borderColor: p.access === 'private' ? C.warn : C.ok }]}>
              <Text style={[S.pillT, { color: p.access === 'private' ? C.warn : C.ok }]}>{p.access || 'public'}</Text>
            </View>
          </View>
          <Pressable onPress={() => Linking.openURL(p.url).catch(() => {})}><Text style={S.link} numberOfLines={1}>{p.url} ↗</Text></Pressable>
          <Btn label={busy === 'un' + p.port ? 'Closing…' : 'Unpublish'} tone="bad" onPress={() => unpublish(p)} disabled={busy === 'un' + p.port} />
        </View>
      ))}
    </View>
  );
}

// ---------------------------------------------------------------- secrets
function Secrets() {
  const [d, setD] = useState(null);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState('');
  const [name, setName] = useState('');
  const [value, setValue] = useState('');
  const [hosts, setHosts] = useState('');
  const [delivery, setDelivery] = useState('proxy');
  const load = useCallback(async () => {
    try { setD(await api('/api/host/secrets')); setErr(''); }
    catch (e) { setErr(String(e.message || e)); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const save = async () => {
    if (!name.trim() || !value.trim()) { setErr('A name and a value are required.'); return; }
    setBusy('save'); setMsg(''); setErr('');
    try {
      await api('/api/host/secrets/' + encodeURIComponent(name.trim()), {
        method: 'PUT',
        body: JSON.stringify({ value, delivery, hosts: hosts.split(',').map((h) => h.trim()).filter(Boolean) }),
      });
      setMsg('Saved ' + name.trim() + '.');
      setName(''); setValue(''); setHosts('');
      await load();
    } catch (e) { setErr(String(e.message || e)); }
    setBusy('');
  };
  const saveLocal = async () => {
    if (!name.trim() || !value.trim()) { setErr('A name and a value are required.'); return; }
    setBusy('local'); setMsg(''); setErr('');
    try {
      await api('/api/host/vault/' + encodeURIComponent(name.trim()), {
        method: 'PUT', body: JSON.stringify({ value }),
      });
      setMsg('Saved ' + name.trim() + ' in this server’s encrypted vault.');
      setName(''); setValue('');
      await load();
    } catch (e) { setErr(String(e.message || e)); }
    setBusy('');
  };
  const apply = async () => {
    setBusy('apply'); setMsg(''); setErr('');
    try {
      await api('/api/host/secrets/apply', { method: 'POST', body: JSON.stringify({ all: true }) });
      setMsg('Every Instabox Secret is now selected for this machine.');
      await load();
    } catch (e) { setErr(String(e.message || e)); }
    setBusy('');
  };
  const del = async (n) => {
    setBusy('del' + n);
    try { await api('/api/host/secrets/' + encodeURIComponent(n), { method: 'DELETE' }); await load(); }
    catch (e) { setErr(String(e.message || e)); }
    setBusy('');
  };
  const delLocal = async (n) => {
    setBusy('dl' + n);
    try { await api('/api/host/vault/' + encodeURIComponent(n), { method: 'DELETE' }); await load(); }
    catch (e) { setErr(String(e.message || e)); }
    setBusy('');
  };
  const org = (d && d.instabox) || {};
  const items = org.items || [];
  return (
    <View style={{ gap: 10 }}>
      <View style={S.card}>
        <Text style={S.h}>Add a key</Text>
        <Text style={S.s}>Instabox Secrets are injected by its outbound proxy: the machine only ever sees a placeholder, never the key. ZerotheBot's vault is local and encrypted, for keys the Bots read directly.</Text>
        <TextInput style={S.inp} value={name} onChangeText={setName} autoCapitalize="characters" placeholder="NAME (e.g. GITHUB_TOKEN)" placeholderTextColor={C.muted} />
        <TextInput style={S.inp} value={value} onChangeText={setValue} secureTextEntry autoCapitalize="none" placeholder="value" placeholderTextColor={C.muted} />
        <TextInput style={S.inp} value={hosts} onChangeText={setHosts} autoCapitalize="none" placeholder="hosts for proxy delivery (api.github.com, github.com)" placeholderTextColor={C.muted} />
        <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          {['proxy', 'env'].map((x) => (
            <Pressable key={x} onPress={() => setDelivery(x)} style={[S.tab, delivery === x && S.tabOn]}>
              <Text style={[S.tabT, delivery === x && S.tabTOn]}>{x}</Text>
            </Pressable>
          ))}
          <View style={{ flex: 1 }} />
          <Btn label={busy === 'save' ? 'Saving…' : 'Save as Instabox Secret'} onPress={save} disabled={busy === 'save'} />
          <Btn label={busy === 'local' ? 'Saving…' : 'Save in local vault'} tone="ok" onPress={saveLocal} disabled={busy === 'local'} />
        </View>
        {!!msg && <Text style={[S.s, { color: C.ok }]}>{msg}</Text>}
        {!!err && <Text style={[S.s, { color: C.bad }]}>{err}</Text>}
      </View>
      <View style={S.card}>
        <Text style={S.h}>Instabox Secrets</Text>
        {!!org.error && <Text style={[S.s, { color: C.bad }]}>{org.error}</Text>}
        {items.length === 0 ? (
          <Text style={S.s}>None yet. Writing them needs a workspace-admin API key; this server's key belongs to a personal org, so use the local vault for now.</Text>
        ) : items.map((s) => (
          <View key={s.name} style={{ gap: 2 }}>
            <View style={S.row}>
              <Text style={S.kv} numberOfLines={1}>{s.name}</Text>
              <View style={{ flex: 1 }} />
              <Text style={S.s}>{s.delivery}</Text>
              <Btn label={busy === 'del' + s.name ? '…' : 'Delete'} tone="bad" onPress={() => del(s.name)} disabled={busy === 'del' + s.name} />
            </View>
            <Text style={S.s} numberOfLines={1}>{(s.hosts || []).join(', ') || 'every host'}</Text>
          </View>
        ))}
        {items.length > 0 && (
          <Btn label={busy === 'apply' ? 'Applying…' : 'Use all on this machine'} tone="ok" onPress={apply} disabled={busy === 'apply'} />
        )}
      </View>
      <View style={S.card}>
        <Text style={S.h}>ZerotheBot vault</Text>
        <Text style={S.s}>{d && d.vault_backend ? 'Encrypted file vault on the server.' : 'The OS credential store is in use.'}</Text>
        {(d && d.local && d.local.length) ? d.local.map((n) => (
          <View key={n} style={S.row}>
            <Text style={S.kv} numberOfLines={1}>{n}</Text>
            <View style={{ flex: 1 }} />
            <Btn label={busy === 'dl' + n ? '…' : 'Delete'} tone="bad" onPress={() => delLocal(n)} disabled={busy === 'dl' + n} />
          </View>
        )) : <Text style={S.s}>No keys stored here yet.</Text>}
        {!!(d && d.env && d.env.length) && (
          <Text style={[S.s, { fontSize: 11 }]}>From the environment (read-only): {d.env.join(', ')}</Text>
        )}
      </View>
    </View>
  );
}

// ------------------------------------------------------------------ shell
function streamExec(cmd, onEvent) {
  return new Promise((resolve, reject) => {
    const { base, token } = conf();
    const xhr = new XMLHttpRequest();
    xhr.open('POST', base + '/api/host/exec');
    xhr.setRequestHeader('Authorization', 'Bearer ' + token);
    xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.setRequestHeader('Accept', 'text/event-stream');
    let seen = 0, buf = '';
    const parse = () => {
      const txt = xhr.responseText || '';
      if (txt.length <= seen) return;
      buf += txt.slice(seen);
      seen = txt.length;
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, i);
        buf = buf.slice(i + 2);
        let ev = 'message', data = '';
        block.split('\n').forEach((ln) => {
          if (ln.startsWith('event:')) ev = ln.slice(6).trim();
          else if (ln.startsWith('data:')) data += ln.slice(5).trim();
        });
        if (!data) continue;
        try { onEvent(ev, JSON.parse(data)); } catch (e) { onEvent(ev, { data }); }
      }
    };
    xhr.onprogress = parse;
    xhr.onload = () => { parse(); resolve(); };
    xhr.onerror = () => reject(new Error('The server could not be reached.'));
    xhr.ontimeout = () => reject(new Error('The command timed out.'));
    xhr.timeout = 660000;
    xhr.send(JSON.stringify({ cmd, timeout: 600 }));
  });
}

function Shell() {
  const [cmd, setCmd] = useState('');
  const [out, setOut] = useState('');
  const [exit, setExit] = useState(null);
  const [busy, setBusy] = useState(false);
  const [hist, setHist] = useState([]);
  const scRef = useRef(null);
  const loadHist = useCallback(async () => {
    try { const d = await api('/api/host/exec/history?limit=15'); setHist(d.commands || []); } catch (e) { /* ignore */ }
  }, []);
  useEffect(() => { loadHist(); }, [loadHist]);
  const run = async (c) => {
    const command = (c || cmd).trim();
    if (!command || busy) return;
    setBusy(true); setOut(''); setExit(null);
    try {
      await streamExec(command, (ev, d) => {
        if (ev === 'stdout' || ev === 'stderr') {
          setOut((p) => p + (d.data || ''));
        } else if (ev === 'exit') {
          setExit(d);
          setOut((p) => p + '\n[exit ' + d.exitCode + (d.timedOut ? ', timed out' : '') + (d.oomKilled ? ', OOM' : '') + ']');
        } else if (ev === 'error') {
          setOut((p) => p + '\n' + (d.message || 'error'));
        }
      });
    } catch (e) {
      setOut((p) => p + '\n' + String(e.message || e));
    }
    setBusy(false);
    loadHist();
  };
  return (
    <View style={{ gap: 10 }}>
      <View style={S.card}>
        <Text style={S.h}>Run a command</Text>
        <Text style={S.s}>Runs as the machine's login user with bash, streaming output as it happens. Same history as the CLI's exec.</Text>
        <TextInput style={[S.inp, { minHeight: 44 }]} value={cmd} onChangeText={setCmd} autoCapitalize="none" autoCorrect={false}
          placeholder="systemctl --user status zerothebot" placeholderTextColor={C.muted} onSubmitEditing={() => run()} />
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <Btn label={busy ? 'Running…' : 'Run'} tone="ok" onPress={() => run()} disabled={busy || !cmd.trim()} />
          <Btn label="Clear" onPress={() => { setOut(''); setExit(null); }} />
          <View style={{ flex: 1 }} />
          {exit && <Text style={S.s}>exit {exit.exitCode}{exit.timedOut ? ' · timeout' : ''}{exit.oomKilled ? ' · OOM' : ''}</Text>}
        </View>
        <ScrollView ref={scRef} style={{ maxHeight: 300 }} onContentSizeChange={() => scRef.current && scRef.current.scrollToEnd({ animated: true })}>
          <Text style={S.mono} selectable>{out || (busy ? '…' : 'Output appears here.')}</Text>
        </ScrollView>
      </View>
      <View style={S.card}>
        <Text style={S.h}>Recent commands</Text>
        {hist.length === 0 ? <Text style={S.s}>Nothing yet.</Text> : hist.map((h) => (
          <Pressable key={h.id} onPress={() => { setCmd(h.command); run(h.command); }} style={{ gap: 2, paddingVertical: 4 }}>
            <Text style={S.kv} numberOfLines={2}>{h.command}</Text>
            <Text style={S.s}>{when(h.at)} · {h.status}{h.exit !== null && h.exit !== undefined ? ' · exit ' + h.exit : ''}</Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}

// ---------------------------------------------------------------- upgrade
function Upgrade() {
  const [d, setD] = useState(null);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { api('/api/host/template').then(setD).catch((e) => setErr(String(e.message || e))); }, []);
  const stage = async () => {
    setBusy(true); setErr(''); setMsg('');
    try {
      const r = await api('/api/host/template/refresh', { method: 'POST', body: JSON.stringify({ confirm }) });
      setMsg(r.up_to_date ? 'Already on the newest template.' : 'Staged. New machine: ' + (r.machine || '') + '. Finish the data copy, then repoint the app.');
      setConfirm('');
    } catch (e) { setErr(String(e.message || e)); }
    setBusy(false);
  };
  if (!d && !err) return <View style={S.card}><ActivityIndicator color={C.ok} /></View>;
  const cur = (d && d.current) || {};
  const latest = (d && d.latest) || {};
  return (
    <View style={{ gap: 10 }}>
      <View style={S.card}>
        <Text style={S.h}>Template</Text>
        <Text style={S.s}>This machine runs <Text style={{ color: C.text, fontWeight: '800' }}>{cur.id} {cur.version}</Text>.</Text>
        <Text style={S.s}>Newest available: <Text style={{ color: C.text, fontWeight: '800' }}>{latest.id} {latest.version}</Text>{d && d.up_to_date ? ' — you are current.' : ''}</Text>
        {!!(d && d.steps && d.steps.length) && (
          <View style={{ gap: 3 }}>
            <Text style={[S.s, { color: C.text, fontWeight: '800' }]}>What a refresh involves</Text>
            {d.steps.map((s, i) => <Text key={i} style={S.s}>{i + 1}. {s}</Text>)}
          </View>
        )}
        {!!(d && d.warnings && d.warnings.length) && d.warnings.map((w, i) => (
          <Text key={'w' + i} style={[S.s, { color: C.warn }]}>⚠ {w}</Text>
        ))}
        {!!err && <Text style={[S.s, { color: C.bad }]}>{err}</Text>}
        {!!msg && <Text style={[S.s, { color: C.ok }]}>{msg}</Text>}
      </View>
      {d && !d.up_to_date && (
        <View style={S.card}>
          <Text style={S.h}>Prepare the upgrade</Text>
          <Text style={S.s}>Saves a snapshot, then creates a stopped machine on {latest.id} {latest.version}. It does not touch this server or its URLs — you finish the copy and the cutover deliberately.</Text>
          <Text style={S.s}>Allowed on this server: <Text style={{ color: d.refresh_allowed ? C.ok : C.bad }}>{d.refresh_allowed ? 'yes' : 'no'}</Text>{d.refresh_allowed ? '' : ' — set ZEROTHEBOT_ALLOW_TEMPLATE_REFRESH=1 in /data/home/zb-secrets.env'}</Text>
          <TextInput style={S.inp} value={confirm} onChangeText={setConfirm} autoCapitalize="none" placeholder={'Type ' + ((d.machine && d.machine.name) || 'the machine name') + ' to confirm'} placeholderTextColor={C.muted} />
          <Btn label={busy ? 'Staging…' : 'Save snapshot + create new machine'} tone="ok" onPress={stage} disabled={busy || !d.refresh_allowed || confirm.trim().toLowerCase() !== ((d.machine && d.machine.name) || '').toLowerCase()} />
        </View>
      )}
    </View>
  );
}

// ------------------------------------------------------------------- screen
export default function ServerScreen({ onMenu }) {
  const ins = useSafeAreaInsets();
  const [tab, setTab] = useState('health');
  const [live, setLive] = useState(false);
  const [liveUrl, setLiveUrl] = useState('');
  const [liveErr, setLiveErr] = useState('');
  const [liveBusy, setLiveBusy] = useState(false);
  const openLive = async () => {
    setLive(true); setLiveErr(''); setLiveBusy(true);
    try {
      const d = await api('/api/host/desktop', { method: 'POST', body: JSON.stringify({ viewOnly: true, ttl: 3600 }) });
      if (!d || !d.url) throw new Error('The server did not return a link.');
      setLiveUrl(d.url);
    } catch (e) { setLiveErr(String(e.message || e)); }
    setLiveBusy(false);
  };
  const closeLive = () => {
    setLive(false); setLiveUrl('');
    api('/api/host/desktop', { method: 'DELETE' }).catch(() => {});
  };
  const TABS = [['health', 'Health'], ['backups', 'Backups'], ['ports', 'Ports'], ['secrets', 'Secrets'], ['shell', 'Shell'], ['upgrade', 'Upgrade']];
  return (
    <View style={S.screen}>
      <TopBar title="Server" subtitle="the machine your Bots live on" onMenu={onMenu} />
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={S.tabs}>
        {TABS.map(([k, l]) => (
          <Pressable key={k} onPress={() => setTab(k)} style={[S.tab, tab === k && S.tabOn]}>
            <Text style={[S.tabT, tab === k && S.tabTOn]}>{l}</Text>
          </Pressable>
        ))}
      </ScrollView>
      <ScrollView contentContainerStyle={S.pad} keyboardShouldPersistTaps="handled">
        {tab === 'health' && <Health onLive={openLive} />}
        {tab === 'backups' && <Backups />}
        {tab === 'ports' && <Ports />}
        {tab === 'secrets' && <Secrets />}
        {tab === 'shell' && <Shell />}
        {tab === 'upgrade' && <Upgrade />}
      </ScrollView>

      <Modal visible={live} animationType="slide" onRequestClose={closeLive}>
        <View style={{ flex: 1, backgroundColor: '#000' }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', paddingTop: (ins.top || 0) + 10, paddingHorizontal: 14, paddingBottom: 10, backgroundColor: '#111' }}>
            <Text style={{ color: '#fff', fontSize: 15, fontWeight: '800', flex: 1 }}>Live server screen</Text>
            <Pressable onPress={closeLive} hitSlop={8}><Text style={{ color: '#e57373', fontSize: 13, fontWeight: '800' }}>Close</Text></Pressable>
          </View>
          {!!liveErr ? (
            <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24, gap: 10 }}>
              <Text style={{ color: '#e57373', textAlign: 'center' }}>{liveErr}</Text>
              <Btn label="Try again" tone="ok" onPress={openLive} />
            </View>
          ) : liveUrl ? (
            <WebView source={{ uri: liveUrl }} style={{ flex: 1, backgroundColor: '#000' }} javaScriptEnabled domStorageEnabled
              allowsInlineMediaPlayback setSupportMultipleWindows={false}
              onError={() => setLiveErr('The live view could not connect. Close and try again.')} />
          ) : (
            <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8 }}>
              <ActivityIndicator color="#9ad3b0" />
              <Text style={{ color: '#9ad3b0' }}>{liveBusy ? 'Starting the desktop…' : 'Connecting…'}</Text>
            </View>
          )}
        </View>
      </Modal>
    </View>
  );
}
