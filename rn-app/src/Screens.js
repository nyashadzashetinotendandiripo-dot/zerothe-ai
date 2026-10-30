import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Alert, FlatList, Pressable, Text, TextInput, View, Share, Linking } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { api, conf } from './session';
import { C } from './theme';

const s0 = {
  screen: { flex: 1, backgroundColor: C.bg },
  body: { flex: 1 },
  pad: { padding: 14, gap: 10, paddingBottom: 40 },
  hdr: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 14, paddingTop: 14, paddingBottom: 10, borderBottomWidth: 1, borderColor: C.line },
  hbtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: C.panel2, borderWidth: 1, borderColor: C.line, alignItems: 'center', justifyContent: 'center' },
  title: { color: C.text, fontSize: 19, fontWeight: '900', letterSpacing: -.3 },
  sub: { color: C.muted, fontSize: 12, marginTop: 1 },
  card: { backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 16, padding: 13, gap: 4 },
  statCard: { flex: 1, backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 16, padding: 12, gap: 2 },
  statNum: { color: C.text, fontSize: 21, fontWeight: '900' },
  statLbl: { color: C.muted, fontSize: 11, fontWeight: '700', textTransform: 'uppercase', letterSpacing: .6 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 16, padding: 13 },
  rowIc: { width: 38, height: 38, borderRadius: 12, backgroundColor: C.panel2, borderWidth: 1, borderColor: C.line, alignItems: 'center', justifyContent: 'center' },
  rowL: { color: C.text, fontSize: 14.5, fontWeight: '700' },
  rowS: { color: C.muted, fontSize: 12, marginTop: 1 },
  pill: { backgroundColor: C.panel2, borderWidth: 1, borderColor: C.line, borderRadius: 999, paddingHorizontal: 9, paddingVertical: 4 },
  chip: { color: C.muted, fontSize: 11, fontWeight: '800', textTransform: 'uppercase', letterSpacing: .8 },
  group: { color: C.muted, fontSize: 11.5, fontWeight: '800', letterSpacing: 1.2, marginTop: 8, marginBottom: 2 },
  btn: { backgroundColor: C.panel2, borderWidth: 1, borderColor: C.line, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 7 },
  btnT: { color: C.text, fontSize: 12.5, fontWeight: '800' },
  ok: { color: C.ok }, warn: { color: C.warn }, bad: { color: C.bad },
  barBg: { height: 9, borderRadius: 5, backgroundColor: C.panel2, overflow: 'hidden' },
  barFill: { height: 9, borderRadius: 5, backgroundColor: C.ok },
  chevS: { color: C.muted, fontSize: 12, fontWeight: '700' },
};

export function TopBar({ title, subtitle, onMenu, right }) {
  const ins = useSafeAreaInsets();
  return (
    <View style={[s0.hdr, { paddingTop: 14 + ins.top }]}>
      <Pressable onPress={onMenu} hitSlop={8}>
        <View style={s0.hbtn}><Text style={{ fontSize: 16, color: C.text }}>☰</Text></View>
      </Pressable>
      <View style={{ flex: 1 }}>
        <Text style={s0.title}>{title}</Text>
        {subtitle ? <Text style={s0.sub} numberOfLines={1}>{subtitle}</Text> : null}
      </View>
      {right || null}
    </View>
  );
}

function Card({ children, style }) {
  return <View style={[s0.card, style]}>{children}</View>;
}

// ---------------------------------------------------------------- workspace 1: dashboard
export function DashboardScreen({ onMenu, bots, approvals, working }) {
  const [d, setD] = useState(null);
  const load = useCallback(async () => {
    try {
      const [u, st] = await Promise.all([api('/api/usage'), api('/api/status').catch(() => null)]);
      setD({ u, st });
    } catch (e) { setD({ err: String(e.message || e) }); }
  }, []);
  useEffect(() => { load(); }, [load, bots, approvals]);
  const u = d && d.u || {};
  const cost = u.cost || {};
  const pct = u.limit > 0 ? Math.min(100, Math.round((cost.total || 0) / u.limit * 100)) : null;
  return (
    <View style={s0.screen}>
      <TopBar title="Dashboard" subtitle="your command tower" onMenu={onMenu} />
      <FlatList
        data={[0]}
        keyExtractor={() => 'd'}
        contentContainerStyle={s0.pad}
        renderItem={() => (
          <View style={{ gap: 12 }}>
            <View style={{ flexDirection: 'row', gap: 10 }}>
              <View style={s0.statCard}><Text style={[s0.statNum, { color: C.text }]}>{bots.length}</Text><Text style={s0.statLbl}>Bots</Text></View>
              <View style={s0.statCard}><Text style={[s0.statNum, { color: working ? C.ok : C.text }]}>{working}</Text><Text style={s0.statLbl}>Working now</Text></View>
            </View>
            <View style={{ flexDirection: 'row', gap: 10 }}>
              <View style={s0.statCard}><Text style={[s0.statNum, { color: approvals.length ? C.warn : C.text }]}>{approvals.length}</Text><Text style={s0.statLbl}>Waiting for you</Text></View>
              <View style={s0.statCard}><Text style={[s0.statNum, { color: C.text }]}>{"$" + (cost.today || 0).toFixed(2)}</Text><Text style={s0.statLbl}>Spent today</Text></View>
            </View>
            {pct !== null && (
              <Card>
                <Text style={s0.chip}>Token budget</Text>
                <View style={{ height: 8 }} />
                <View style={s0.barBg}><View style={[s0.barFill, { width: pct + '%', backgroundColor: pct > 80 ? C.warn : C.ok }]} /></View>
                <Text style={s0.rowS}>{pct}% of this period's cap · {(cost.currency || '$') + (cost.total || 0).toFixed(2)} used</Text>
              </Card>
            )}
            <Card>
              <Text style={s0.chip}>Tokens</Text>
              <Text style={s0.rowS}>in {fmtN(u.input_tokens)} · out {fmtN(u.output_tokens)} · {(cost.currency || '$') + (cost.total || 0).toFixed(2)} total</Text>
              {(cost.per_bot && Object.keys(cost.per_bot).length) ? (
                Object.entries(cost.per_bot).slice(0, 6).map(([k, v]) => (
                  <View key={k} style={{ flexDirection: 'row', gap: 8 }}>
                    <Text style={[s0.rowS, { flex: 1 }]} numberOfLines={1}>{shortBot(k, bots)}</Text>
                    <Text style={[s0.rowS, { color: C.text }]}>{(cost.currency || '$') + Number(v).toFixed(2)}</Text>
                  </View>
                ))
              ) : null}
            </Card>
            <Card>
              <Text style={s0.chip}>Server</Text>
              <Text style={s0.rowS}>
                {d && d.st ? ('computer: ' + (d.st.computer ? 'ready' : 'off') + ' · engine v' + (d.st.version || '')) : 'loading…'}
              </Text>
            </Card>
            {!d && <ActivityIndicator color={C.ok} />}
            {!!d && d.err ? <Text style={s0.bad}>Could not load: {d.err}</Text> : null}
          </View>
        )}
      />
    </View>
  );
}

function fmtN(n) {
  const x = Number(n) || 0;
  if (x > 1e6) return (x / 1e6).toFixed(1) + 'M';
  if (x > 1e3) return (x / 1e3).toFixed(1) + 'k';
  return String(x);
}
function shortBot(id, bots) {
  const b = bots.find((x) => x.id === id);
  return b ? (b.emoji || '🤖') + ' ' + b.name : (id || '').slice(0, 10);
}

// ---------------------------------------------------------------- workspace 4: marketplace
export function MarketplaceScreen({ onMenu, onRefresh, onHired }) {
  const [rows, setRows] = useState(null);
  const [q, setQ] = useState('');
  const [busyId, setBusyId] = useState('');
  const [msg, setMsg] = useState('');
  const load = () => { api('/api/templates').then((r) => setRows(r || [])).catch((e) => setRows([])); };
  useEffect(() => { load(); }, []);
  const add = async (t) => {
    setBusyId(t.id); setMsg('');
    try {
      const b = await api('/api/bots/from_template', { method: 'POST', body: JSON.stringify({ template: t.id }) });
      setMsg((b && b.name ? b.name : 'Bot') + ' joined your team — opening its chat…');
      onRefresh && onRefresh();
      // Land in the new Bot's chat so hiring means you can start working with it.
      if (b && b.id && onHired) onHired(b);
    } catch (e) { setMsg('Could not hire: ' + (e.message || e)); }
    setBusyId('');
  };
  const ql = q.trim().toLowerCase();
  const seen = {};
  const groups = [];
  for (const t of (rows || [])) {
    if (ql && !((t.name || '') + ' ' + (t.blurb || '') + ' ' + (t.category || '')).toLowerCase().includes(ql)) continue;
    if (!seen[t.category]) { seen[t.category] = []; groups.push([t.category, seen[t.category]]); }
    seen[t.category].push(t);
  }
  return (
    <View style={s0.screen}>
      <TopBar title="Bot Marketplace" subtitle={rows && rows.length ? rows.length + ' ready-made specialists' : 'hiring hall'} onMenu={onMenu} />
      <View style={{ paddingHorizontal: 14, paddingTop: 10 }}>
        <TextInput style={sIn} value={q} onChangeText={setQ} placeholder="Search by skill — inbox, devops, procurement…" placeholderTextColor={C.muted} autoCapitalize="none" />
      </View>
      <FlatList
        data={groups}
        keyExtractor={([k]) => k}
        contentContainerStyle={s0.pad}
        renderItem={({ item: [cat, list] }) => (
          <View>
            <Text style={s0.group}>{cat}</Text>
            {list.map((t) => (
              <View key={t.id} style={s0.row}>
                <View style={s0.rowIc}><Text style={{ fontSize: 18 }}>{t.emoji || '🤖'}</Text></View>
                <View style={{ flex: 1, gap: 1 }}>
                  <Text style={s0.rowL} numberOfLines={1}>{t.name}</Text>
                  <Text style={s0.rowS} numberOfLines={2}>{t.blurb || t.job}</Text>
                </View>
                <Pressable onPress={() => add(t)} disabled={busyId === t.id} style={({ pressed }) => [s0.btn, { backgroundColor: C.solid, borderColor: C.solid }, pressed && { opacity: .9 }]}>
                  <Text style={[s0.btnT, { color: C.onSolid }]}>{busyId === t.id ? '…' : '＋ Hire'}</Text>
                </Pressable>
              </View>
            ))}
          </View>
        )}
        ListFooterComponent={<Text style={s0.rowS}>{msg || (rows === null ? 'loading…' : '')}</Text>}
      />
    </View>
  );
}
const sIn = { backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 12, color: C.text, padding: 11, fontSize: 14 };

// ---------------------------------------------------------------- workspace 5: approvals
export function ApprovalsScreen({ onMenu, apps, decide, bots }) {
  return (
    <View style={s0.screen}>
      <TopBar title="Approvals" subtitle={apps.length ? apps.length + ' waiting for your OK' : 'security gate'} onMenu={onMenu} />
      <FlatList
        data={apps}
        keyExtractor={(a) => a.id}
        contentContainerStyle={s0.pad}
        ListEmptyComponent={
          <View style={{ alignItems: 'center', gap: 6, padding: 40 }}>
            <Text style={{ fontSize: 40 }}>🛂</Text>
            <Text style={s0.rowL}>Nothing waiting</Text>
            <Text style={s0.rowS}>High-risk jobs stop here before they happen. Approve, deny, or answer right from this desk.</Text>
          </View>
        }
        renderItem={({ item: a }) => {
          const b = (bots || []).find((x) => x.id === a.bot_id) || {};
          const isAsk = asky2(a);
          const det = a.details && typeof a.details === 'object' ? JSON.stringify(a.details, null, 1) : (a.details || '');
          return (
            <View style={[s0.card, { borderColor: isAsk ? C.ok : C.warn }]}>
              <Text style={[s0.chip, { color: isAsk ? C.ok : C.warn }]}>{(a.category || 'approval').toUpperCase()}{a.tool ? ' · ' + a.tool : ''}</Text>
              {!!det && det.length < 500 && <Text style={s0.rowS}>{det}</Text>}
              {isAsk ? <Ans onSend={(t) => decide(a, true, false, t)} placeholder={a.tool === 'ask_code' ? 'Paste the code' : 'Type an answer'} /> : null}
              <View style={{ flexDirection: 'row', gap: 8, marginTop: 4, flexWrap: 'wrap' }}>
                {!isAsk && (
                  <>
                    <Pressable onPress={() => decide(a, true, true)} style={s0.btn}><Text style={[s0.btnT, { color: C.ok }]}>✅ Always</Text></Pressable>
                    <Pressable onPress={() => decide(a, true, false)} style={s0.btn}><Text style={[s0.btnT, { color: C.ok }]}>Approve</Text></Pressable>
                  </>
                )}
                <Pressable onPress={() => decide(a, false, false)} style={s0.btn}><Text style={[s0.btnT, s0.bad]}>{isAsk ? "Can't do it" : 'Deny'}</Text></Pressable>
              </View>
              <Text style={s0.rowS}>{b ? (b.emoji || '🤖') + ' ' + b.name : ''}</Text>
            </View>
          );
        }}
      />
    </View>
  );
}
function asky2(a) { return a.category === 'question' || a.tool === 'ask_code'; }
function Ans({ onSend, placeholder }) {
  const [v, setV] = useState('');
  return (
    <View style={{ flexDirection: 'row', gap: 8, marginTop: 6 }}>
      <TextInput value={v} onChangeText={setV} placeholder={placeholder} placeholderTextColor={C.muted}
        style={{ flex: 1, color: C.text, fontSize: 13, borderWidth: 1, borderColor: C.warn, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 7 }}
        autoCapitalize="none" autoCorrect={false} returnKeyType="send" onSubmitEditing={() => { const t = v.trim(); if (t) { onSend(t); setV(''); } }} />
      <Pressable onPress={() => { const t = v.trim(); if (t) { onSend(t); setV(''); } }} style={({ pressed }) => [s0.btn, { backgroundColor: C.solid, borderColor: C.solid }, pressed && { opacity: .85 }]}>
        <Text style={[s0.btnT, { color: C.onSolid }]}>Send</Text>
      </Pressable>
    </View>
  );
}

// ---------------------------------------------------------------- workspace 7: MCP
export function McpScreen({ onMenu }) {
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState('');
  const load = useCallback(() => { api('/api/mcp').then((d) => setRows(Array.isArray(d) ? d : (d.servers || d.items || []))).catch(() => setRows([])); }, []);
  useEffect(() => { load(); }, [load]);
  const restart = async (r) => {
    setBusy(r.id || r.name || '');
    try { await api('/api/mcp/' + (r.id || r.name) + '/restart', { method: 'POST', body: '{}' }); } catch (e) { /* ignore */ }
    setBusy(''); load();
  };
  return (
    <View style={s0.screen}>
      <TopBar title="MCP Tools" subtitle={rows.length ? rows.length + ' tool packs installed' : 'modular skill packs'} onMenu={onMenu} />
      <FlatList
        data={rows}
        keyExtractor={(r) => r.id || r.name}
        contentContainerStyle={s0.pad}
        ListEmptyComponent={<Text style={s0.rowS}>No MCP servers yet. Add them on the web app (Settings → MCP) with a JSON import; they run on your server.</Text>}
        renderItem={({ item: r }) => (
          <View style={s0.row}>
            <View style={s0.rowIc}><Text style={{ fontSize: 16 }}>🔌</Text></View>
            <View style={{ flex: 1, gap: 1 }}>
              <Text style={s0.rowL} numberOfLines={1}>{r.name}</Text>
              <Text style={s0.rowS} numberOfLines={1}>{r.transport || r.url || (r.command || '')}</Text>
            </View>
            <Pressable onPress={() => restart(r)} disabled={busy === (r.id || r.name)} style={s0.btn}>
              <Text style={s0.btnT}>{busy === (r.id || r.name) ? '…' : '🔄 Restart'}</Text>
            </Pressable>
          </View>
        )}
      />
    </View>
  );
}

// ---------------------------------------------------------------- workspace 8: routines
export function RoutinesScreen({ onMenu }) {
  const [rows, setRows] = useState(null);
  const [busy, setBusy] = useState('');
  const load = useCallback(() => { api('/api/routines').then((d) => setRows(Array.isArray(d) ? d : [])).catch(() => setRows([])); }, []);
  useEffect(() => { load(); }, [load]);
  const runNow = async (r) => {
    setBusy(r.id);
    try { await api('/api/routines/' + r.id + '/run', { method: 'POST', body: '{}' }); } catch (e) { /* ignore */ }
    setBusy('');
  };
  const toggle = async (r) => {
    try { await api('/api/routines/' + r.id, { method: 'PUT', body: JSON.stringify({ enabled: !r.enabled }) }); } catch (e) { /* ignore */ }
    load();
  };
  return (
    <View style={s0.screen}>
      <TopBar title="Routines" subtitle={rows && rows.length ? rows.length + ' scheduled jobs' : 'background autocron'} onMenu={onMenu} />
      <FlatList
        data={rows || []}
        keyExtractor={(r) => r.id}
        contentContainerStyle={s0.pad}
        ListEmptyComponent={<Text style={s0.rowS}>{rows === null ? 'loading…' : 'No routines yet. Create them from chat — a Bot can schedule its own background jobs, or add one in the web app.'}</Text>}
        renderItem={({ item: r }) => (
          <View style={s0.row}>
            <View style={s0.rowIc}><Text style={{ fontSize: 16 }}>⏱</Text></View>
            <View style={{ flex: 1, gap: 1 }}>
              <Text style={s0.rowL} numberOfLines={1}>{r.name}</Text>
              <Text style={s0.rowS} numberOfLines={1}>{r.cron || r.schedule || ''}{r.bot_name ? ' · ' + r.bot_name : ''}</Text>
            </View>
            <Pressable onPress={() => toggle(r)} style={[s0.btn, r.enabled && { borderColor: C.ok }]}><Text style={[s0.btnT, { color: r.enabled ? C.ok : C.muted }]}>{r.enabled ? 'On' : 'Off'}</Text></Pressable>
            <Pressable onPress={() => runNow(r)} disabled={busy === r.id} style={s0.btn}><Text style={s0.btnT}>{busy === r.id ? '…' : '⚡ Now'}</Text></Pressable>
          </View>
        )}
      />
    </View>
  );
}

// ---------------------------------------------------------------- workspace 9: files
export function FilesScreen({ onMenu }) {
  const [mode, setMode] = useState('workspace');   // workspace (Bots' files) | server (whole machine)
  const [path, setPath] = useState('.');
  const [items, setItems] = useState(null);
  const [err, setErr] = useState('');
  const load = useCallback((p, m) => {
    const kind = m || mode;
    const target = p === undefined || p === null ? (kind === 'server' ? '/data/home' : '.') : p;
    setErr('');
    const req = kind === 'server'
      ? api('/api/host/files?path=' + encodeURIComponent(target))
      : api('/api/computer/files?path=' + encodeURIComponent(target));
    req.then((d) => {
      const rows = kind === 'server'
        ? (d.entries || []).map((e) => ({ name: e.name, dir: e.type === 'dir', size: e.size, mode: e.mode, ts: e.modTime }))
        : (d.items || []);
      setPath(d.path || target);
      setItems(rows);
    }).catch((e) => { setErr(e.message || 'Could not open folder.'); setItems([]); });
  }, [mode]);
  useEffect(() => { load(mode === 'server' ? '/data/home' : '.', mode); }, [mode]);
  const join = (name) => {
    const base = path === '.' ? '' : path.replace(/\/+$/, '');
    if (mode === 'server') return (base || '') + '/' + name;
    return base ? base + '/' + name : name;
  };
  const open = (it) => {
    if (it.dir) { load(join(it.name)); return; }
    const { base, token } = conf();
    const p = join(it.name);
    const url = base + (mode === 'server' ? '/api/host/file?path=' : '/api/computer/file?path=') + encodeURIComponent(p)
      + '&download=1&token=' + encodeURIComponent(token);
    // open in the browser so Android downloads it; share is the fallback
    Linking.openURL(url).catch(() => Share.share({ message: url }));
  };
  const up = () => {
    if (mode === 'server') {
      const p = path.replace(/\/+$/, '');
      if (!p || p === '/') return;
      const parts = p.split('/').filter(Boolean);
      parts.pop();
      load('/' + parts.join('/'));
      return;
    }
    if (path && path !== '.') { const parts = path.split('/').filter(Boolean); parts.pop(); load(parts.join('/') || '.'); }
  };
  const seg = (id, label) => (
    <Pressable onPress={() => setMode(id)} style={[s0.btn, mode === id && { borderColor: C.ok, backgroundColor: C.panel2 }]}>
      <Text style={[s0.btnT, { color: mode === id ? C.ok : C.muted }]}>{label}</Text>
    </Pressable>
  );
  const atRoot = mode === 'server' ? (path === '/' || path === '/data/home') : (path === '.');
  return (
    <View style={s0.screen}>
      <TopBar title="Files" subtitle={(mode === 'server' ? path : (path === '.' ? 'workspace root' : path)) + (items ? ' · ' + items.length + ' items' : '')}
        onMenu={onMenu} right={<Pressable onPress={up} disabled={atRoot} hitSlop={8}><Text style={[s0.btnT, atRoot && { opacity: .35 }]}>↑</Text></Pressable>} />
      <View style={{ flexDirection: 'row', gap: 8, paddingHorizontal: 14, paddingTop: 10 }}>
        {seg('workspace', 'Workspace')}
        {seg('server', 'Whole server')}
      </View>
      <FlatList
        data={items || []}
        keyExtractor={(it) => it.name}
        contentContainerStyle={s0.pad}
        ListEmptyComponent={
          <Text style={s0.rowS}>{err || (items === null ? 'loading…'
            : mode === 'server'
              ? 'Empty folder. "Whole server" browses every path on the machine — logs, builds, /data.'
              : 'Empty folder. Files Bots create and download (PDFs, reports, exports) appear here.')}</Text>
        }
        renderItem={({ item: it }) => (
          <Pressable onPress={() => open(it)} style={({ pressed }) => [s0.row, pressed && { opacity: .9 }]}>
            <View style={s0.rowIc}><Text style={{ fontSize: 16 }}>{it.dir ? '📁' : '📄'}</Text></View>
            <View style={{ flex: 1 }}>
              <Text style={s0.rowL} numberOfLines={1}>{it.name}</Text>
              <Text style={s0.rowS}>{it.dir ? 'folder' : fmtSize(it.size)}{it.mode ? ' · ' + it.mode : ''}</Text>
            </View>
            {!it.dir && <Text style={s0.chevS}>share ↗</Text>}
          </Pressable>
        )}
      />
    </View>
  );
}

// ---------------------------------------------------------------- workspace 10: wallet
export function WalletScreen({ onMenu, bots }) {
  const [u, setU] = useState(null);
  useEffect(() => { api('/api/usage').then(setU).catch(() => setU({ error: 1 })); }, []);
  const cost = (u && u.cost) || {};
  const cur = cost.currency || '$';
  const pct = (u && u.limit > 0) ? Math.min(100, Math.round((cost.total || 0) / u.limit * 100)) : null;
  const names = id => { const b = (bots || []).find(x => x.id === id); return b ? (b.emoji || '') + ' ' + b.name : id; };
  return (
    <View style={s0.screen}>
      <TopBar title="Wallet" subtitle="token & money spend" onMenu={onMenu} />
      <FlatList
        data={[0]}
        keyExtractor={() => 'w'}
        contentContainerStyle={s0.pad}
        renderItem={() => (
          <View style={{ gap: 12 }}>
            <Card>
              <Text style={s0.chip}>Total spend</Text>
              <Text style={[s0.statNum, { color: C.text }]}>{cur}{(cost.total || 0).toFixed(2)}</Text>
              <Text style={s0.rowS}>today {cur}{(cost.today || 0).toFixed(2)} · tokens in {fmtN(u && u.input_tokens)} / out {fmtN(u && u.output_tokens)}</Text>
              {pct !== null && (
                <View style={{ marginTop: 8 }}>
                  <View style={s0.barBg}><View style={[s0.barFill, { width: pct + '%', backgroundColor: pct > 80 ? C.warn : C.ok }]} /></View>
                  <Text style={s0.rowS}>{pct}% of the spend cap</Text>
                </View>
              )}
            </Card>
            <View>
              <Text style={s0.group}>By bot</Text>
              {(cost.per_bot && Object.keys(cost.per_bot).length)
                ? Object.entries(cost.per_bot).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
                  <View key={k} style={[s0.row, { paddingVertical: 10 }]}>
                    <Text style={[s0.rowL, { flex: 1 }]} numberOfLines={1}>{names(k)}</Text>
                    <Text style={[s0.rowL, s0.ok]}>{cur}{Number(v).toFixed(2)}</Text>
                  </View>
                ))
                : <Text style={s0.rowS}>No spend yet — wallets warm.</Text>}
            </View>
            {(u && u.unpriced && u.unpriced.length) ? <Text style={s0.rowS}>Unpriced models: {u.unpriced.join(', ')} — set prices in web settings.</Text> : null}
            {!u && <ActivityIndicator color={C.ok} />}
          </View>
        )}
      />
    </View>
  );
}

// ---------------------------------------------------------------- plugin items: Gmail emails
export function PluginItemsScreen({ onMenu, pluginId, title }) {
  const [items, setItems] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [q, setQ] = useState('');
  const [delId, setDelId] = useState(null);
  const load = useCallback(async () => {
    setBusy(true); setMsg('');
    try {
      const url = '/api/plugins/' + pluginId + '/items' + (q ? '?query=' + encodeURIComponent(q) : '');
      const d = await api(url);
      setItems(d && d.items || []);
    } catch (e) { setMsg('Could not load: ' + (e.message || e)); setItems([]); }
    setBusy(false);
  }, [pluginId, q]);
  useEffect(() => { load(); }, [load]);
  const del = async (id) => {
    setDelId(id);
    Alert.alert(
      'Delete email?',
      'This permanently deletes the message from Gmail and cannot be undone.',
      [
        { text: 'Cancel', style: 'cancel', onPress: () => setDelId(null) },
        { text: 'Delete', style: 'destructive', onPress: async () => {
          setBusy(id);
          try {
            await api('/api/plugins/' + pluginId + '/items/' + encodeURIComponent(id), { method: 'DELETE' });
            setMsg('Email deleted.');
            setDelId(null);
            load();
          } catch (e) { setMsg('Delete failed: ' + (e.message || e)); }
          setBusy(false);
        } },
      ]
    );
  };
  return (
    <View style={s0.screen}>
      <TopBar title={title || 'Plugin items'} subtitle={items === null ? 'loading…' : items.length + ' items'} onMenu={onMenu}
        right={<Pressable onPress={load} hitSlop={8} disabled={busy}><Text style={[s0.chevS, { fontSize: 14 }]}>↻</Text></Pressable>} />
      <View style={{ paddingHorizontal: 14, paddingTop: 10, gap: 8 }}>
        <TextInput style={sIn} value={q} onChangeText={setQ} placeholder="Search (Gmail query, e.g. is:unread)" placeholderTextColor={C.muted} autoCapitalize="none" onSubmitEditing={load} />
        {!!msg && <Text style={s0.rowS}>{msg.startsWith('Could not') || msg.startsWith('Delete failed') ? '' : msg}</Text>}
        {!!msg && (msg.startsWith('Could not') || msg.startsWith('Delete failed')) && <Text style={[s0.rowS, { color: C.bad }]}>{msg}</Text>}
      </View>
      <FlatList
        data={items || []}
        keyExtractor={(it) => it.id}
        contentContainerStyle={s0.pad}
        ListEmptyComponent={
          <View style={{ alignItems: 'center', gap: 6, padding: 40 }}>
            <Text style={{ fontSize: 40 }}>📭</Text>
            <Text style={s0.rowL}>{items === null ? 'loading…' : 'No emails found.'}</Text>
            <Text style={s0.rowS}>Try a Gmail query like "is:unread" or "from:example.com".</Text>
          </View>
        }
        renderItem={({ item: it }) => (
          <Pressable onPress={() => del(it.id)} disabled={busy} style={({ pressed }) => [s0.row, pressed && { opacity: .9 }, busy && { opacity: .5 }]}>
            <View style={s0.rowIc}><Text style={{ fontSize: 16 }}>✉️</Text></View>
            <View style={{ flex: 1, gap: 2 }}>
              <Text style={s0.rowL} numberOfLines={1}>{it.subject || '(no subject)'}</Text>
              <Text style={s0.rowS} numberOfLines={2}>{(it.from || '')} · {it.date || ''}{it.snippet ? ' — ' + it.snippet : ''}</Text>
              {it.labels && it.labels.length && <Text style={[s0.rowS, { color: C.muted }]}>{it.labels.join(', ')}</Text>}
            </View>
            <Text style={s0.chevS}>🗑</Text>
          </Pressable>
        )}
      />
      {!!delId && <View style={{ position: 'absolute', bottom: 20, left: 0, right: 0, alignItems: 'center' }}>
        <Pressable onPress={() => setDelId(null)} style={[s0.btn, { backgroundColor: C.bad, borderColor: C.bad }]}><Text style={[s0.btnT, { color: C.onSolid }]}>Cancel delete</Text></Pressable>
      </View>}
    </View>
  );
}

function fmtSize(n) {
  const x = Number(n) || 0;
  if (x > 1e6) return (x / 1e6).toFixed(1) + ' MB';
  if (x > 1e3) return (x / 1e3).toFixed(1) + ' KB';
  return x + ' B';
}
