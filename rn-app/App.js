import React, { useEffect, useRef, useState, useCallback } from 'react';
import { View, Text, TextInput, Pressable, FlatList, StyleSheet } from 'react-native';
import { StatusBar as ExpoStatus } from 'expo-status-bar';
import { api, connectEvents, loadKey, saveConf, tryConf, clearConf, conf, cfgGet, K_SERVER, K_TOKEN, K_EMAIL, K_PASS, loginEmail } from './src/session';
import { C } from './src/theme';
import { checkUpdate, installUpdate, APP_VERSION } from './src/updates';
import Connect from './src/Connect';
import Chat from './src/Chat';

export default function App() {
  const [ready, setReady] = useState(false);
  const [signed, setSigned] = useState(false);
  const [screen, setScreen] = useState('home');            // home | chat | conn | set
  const [bots, setBots] = useState([]);
  const [busy, setBusy] = useState({});                    // bot_id -> {status, thread_id}
  const [chat, setChat] = useState(null);                  // {bot, threadId, threads}
  const lastRef = useRef(0);
  const evtRef = useRef(null);
  const chatHandler = useRef(null);
  const [upd, setUpd] = useState(null);                    // available update
  const [updState, setUpdState] = useState('idle');        // idle | checking | installing | failed

  const runCheck = useCallback(async (quiet = true) => {
    if (!quiet) setUpdState('checking');
    const u = await checkUpdate();
    setUpd(u);
    if (!quiet) setUpdState(u ? 'idle' : 'idle');
    return u;
  }, []);

  useEffect(() => { if (ready) runCheck(true); }, [ready, runCheck]);

  const doUpdate = useCallback(async () => {
    const u = upd || await runCheck(true);
    if (!u) return;
    setUpdState('installing');
    const how = await installUpdate(u);
    setUpdState(how === 'installing' ? 'idle' : how === 'download' ? 'idle' : 'failed');
  }, [upd, runCheck]);

  useEffect(() => { (async () => {
    await loadKey();
    const saved = await cfgGet(K_SERVER), tok = await cfgGet(K_TOKEN);
    const mail = await cfgGet(K_EMAIL), pass = await cfgGet(K_PASS);
    if (saved && tok) { await saveConf(saved, tok); setSigned(true); await refresh(); }
    else if (saved && mail && pass) {
      // silent re-login with the stored web credentials (cookie session)
      try {
        await loginEmail(saved, mail, pass);
        await saveConf(saved, '', mail, pass);
        setSigned(true); await refresh();
      } catch (e) { /* stale password: stay on the login screen, keep values */ }
    }
    setReady(true);
  })(); }, []);

  const refresh = useCallback(async () => {
    try {
      const d = await api('/api/bootstrap');
      setBots(d.bots || []);
      if (d.last_event > lastRef.current) lastRef.current = d.last_event;
      const b = {};
      for (const [id, st] of Object.entries(d.status || {})) if (st && st.status && st.status !== 'idle') b[id] = st;
      setBusy(b);
    } catch (e) { /* keep whatever we had */ }
  }, []);

  const onEvent = useCallback((ev) => {
    if (ev.id && ev.id > lastRef.current) lastRef.current = ev.id;
    if (ev.type === 'turn') {
      setBusy((prev) => {
        const o = { ...prev };
        if (['done', 'stopped', 'error', 'limit', 'skipped'].includes(ev.status)) delete o[ev.bot_id];
        else o[ev.bot_id] = { status: ev.status, thread_id: ev.thread_id };
        return o;
      });
    }
    if (ev.type === 'bots' || ev.type === 'groups' || ev.type === 'notification') refresh();
    if (['message', 'delta', 'delta_reset', 'turn', 'approval'].includes(ev.type)) chatHandler.current && chatHandler.current(ev);
  }, [refresh, chatHandler]);

  // one live connection for the whole app
  useEffect(() => {
    if (!signed) return undefined;
    evtRef.current = connectEvents({ since: lastRef.current, onEvent, onConn: () => {}, onSince: () => {} });
    return evtRef.current;
  }, [signed, onEvent]);

  const openBot = async (bot) => {
    try {
      const rows = await api('/api/bots/' + bot.id + '/threads');
      const main = rows.find((r) => r.main) || rows[0];
      if (!main) return;
      setChat({ bot, threadId: main.id, threads: rows });
      setScreen('chat');
    } catch (e) { /* ignore */ }
  };

  const newBot = async (name, job) => {
    try { await api('/api/bots', { method: 'POST', body: JSON.stringify({ name, job }) }); await refresh(); }
    catch (e) { /* ignore */ }
  };

  // Chats tab: most recent chat, else the first Bot's main thread
  const openChatTab = useCallback(() => {
    if (chat) setScreen('chat');
    else if (bots.length) openBot(bots[0]);
    else setScreen('home');
  }, [chat, bots, openBot]);

  if (!ready) return null;
  if (!signed) return (
    <Shell upd={upd} onUpd={doUpdate} updState={updState}>
      <Login onDone={() => { setSigned(true); setScreen('home'); refresh(); }} />
    </Shell>
  );

  if (screen === 'chat' && chat) return (
    <Shell upd={upd} onUpd={doUpdate} updState={updState}>
      <Chat
        thread={{ id: chat.threadId }}
        threads={chat.threads}
        onSwitch={(tid) => setChat(c => ({ ...c, threadId: tid }))}
        onBack={() => setScreen('home')}
        onEvent={chatHandler}
      />
    </Shell>
  );

  if (screen === 'set') return (
    <Shell upd={upd} onUpd={doUpdate} updState={updState}>
      <Settings
        onOut={async () => { await clearConf(); setSigned(false); }}
        onCheck={runCheck}
        onPlug={() => setScreen('conn')}
      />
      <TabBar tab="set" onTab={setScreen} onChat={openChatTab} />
    </Shell>
  );

  if (screen === 'connect') return (
    <Shell upd={upd} onUpd={doUpdate} updState={updState}>
      <Connect onBack={() => setScreen('home')} />
      <TabBar tab="conn" onTab={setScreen} onChat={openChatTab} />
    </Shell>
  );

  return (
    <Shell upd={upd} onUpd={doUpdate} updState={updState}>
      <Home bots={bots} busy={busy} onOpen={openBot} onPlug={() => setScreen('conn')} onNew={newBot} />
      <TabBar tab="home" onTab={setScreen} onChat={openChatTab} />
    </Shell>
  );
}

function Shell({ children, upd, onUpd, updState }) {
  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>{children}
      {!!upd && (
        <Pressable onPress={onUpd} style={sh.banner} disabled={updState === 'installing'}>
          <Text style={sh.bannerT}>
            {updState === 'installing' ? `Downloading ZerotheBot ${upd.version}…` : `Update available: ZerotheBot ${upd.version} — tap to install`}
          </Text>
        </Pressable>
      )}
      <ExpoStatus style="light" />
    </View>
  );
}

// ----------------------------------------------------------------- login
function Login({ onDone }) {
  const [base, setBase] = useState('https://8765-agquljzsz7k42vgoenj7qr6fim.instabox.tech');
  const [mode, setMode] = useState('advance');             // advance (email+password) | token
  const [mail, setMail] = useState('');
  const [pass, setPass] = useState('');
  const [token, setToken] = useState('');
  const [err, setErr] = useState('');
  const [go, setGo] = useState(false);
  const save = async () => {
    setErr(''); setGo(true);
    try {
      if (mode === 'advance') {
        await loginEmail(base, mail, pass);               // cookie session
        await saveConf(base, '', mail, pass);              // remember for silent re-login
      } else {
        await tryConf(base, token);
        await saveConf(base, token);                      // clears stale creds? keep old email if any
      }
      onDone();
    }
    catch (e) { setErr(e.message || 'Could not connect.'); }
    setGo(false);
  };
  const ready = mode === 'advance' ? (!!mail.trim() && !!pass) : !!token.trim();
  return (
    <View style={[l.box, { justifyContent: 'flex-start', paddingTop: 80 }]}>
      <Text style={l.h1}>ZerotheBot</Text>
      <Text style={l.cap}>Point the app at your server and sign in.</Text>
      <View style={l.seg}>
        <Pressable onPress={() => setMode('advance')} style={[l.segB, l.segOn]}><Text style={[l.segT, { color: C.onSolid }]}>Email &amp; password</Text></Pressable>
      </View>
      <Text style={l.lbl}>Server</Text>
      <TextInput style={l.inl} value={base} autoCapitalize="none" autoCorrect={false} keyboardType="url" onChangeText={setBase} />
      {mode === 'advance' ? (
        <>
          <Text style={l.lbl}>Email (the one you use on the web app)</Text>
          <TextInput style={l.inl} value={mail} autoCapitalize="none" autoCorrect={false} keyboardType="email-address" onChangeText={setMail} />
          <Text style={l.lbl}>Password</Text>
          <TextInput style={l.inl} value={pass} secureTextEntry onChangeText={setPass} />
        </>
      ) : (
        <>
          <Text style={l.lbl}>Service token</Text>
          <TextInput style={l.inl} value={token} autoCapitalize="none" autoCorrect={false} secureTextEntry onChangeText={setToken} />
        </>
      )}
      {!!err && <Text style={l.err}>{err}</Text>}
      <Pressable onPress={save} disabled={!ready || go} style={({ pressed }) => [l.btn, !ready && { opacity: .5 }, pressed && { opacity: .9 }]}>
        <Text style={l.btnT}>{go ? 'Checking…' : 'Sign in'}</Text>
      </Pressable>
      <Text style={l.cap}>
        Same sign-in as the web app — one time per install. Your phone stays signed in across app
        updates from here on. Google sign-in is coming next.
      </Text>
      {mode === 'advance'
        ? <Pressable onPress={() => setMode('token')} hitSlop={8}><Text style={l.tiny}>Sign in with a service token instead</Text></Pressable>
        : <Pressable onPress={() => setMode('advance')} hitSlop={8}><Text style={l.tiny}>Sign in with email & password instead</Text></Pressable>}
    </View>
  );
}

// ----------------------------------------------------------------- home
function Home({ bots, busy, onOpen, onPlug, onNew }) {
  const [modal, setModal] = useState(null);
  const working = Object.keys(busy).length;
  return (
    <View style={n.screen}>
      <View style={n.hdr}>
        <View style={{ flex: 1 }}>
          <Text style={n.brand}>ZerotheBot</Text>
          <Text style={n.sub}>
            {bots.length === 0
              ? 'your 24/7 AI teammates live on your server'
              : bots.length + (bots.length === 1 ? ' Bot' : ' Bots') + (working ? '  ·  ' + working + ' working' : '  ·  all idle')}
          </Text>
        </View>
        <Pressable onPress={onPlug} hitSlop={8}><View style={n.hbtn}><Text style={{ fontSize: 15 }}>🧩</Text></View></Pressable>
      </View>
      <FlatList
        data={bots}
        keyExtractor={(x) => x.id}
        ListEmptyComponent={
          <View style={n.empty}>
            <Text style={{ fontSize: 44 }}>🤖</Text>
            <Text style={n.emptyT}>No Bots yet</Text>
            <Text style={n.emptyS}>A Bot is an AI teammate that keeps working while your phone is locked — give it a job and chat with it from anywhere.</Text>
          </View>
        }
        contentContainerStyle={{ padding: 16, gap: 10, paddingBottom: 100 }}
        renderItem={({ item }) => {
          const s = busy[item.id];
          const pill = s ? (s.status === 'waiting_approval' ? ['needs you', C.warn] : ['working', C.ok]) : null;
          return (
            <Pressable onPress={() => onOpen(item)} style={({ pressed }) => [n.card, pressed && { opacity: .92 }]}>
              <View style={n.av}><Text style={{ fontSize: 22 }}>{item.emoji || '🤖'}</Text></View>
              <View style={{ flex: 1, gap: 2 }}>
                <Text style={n.name} numberOfLines={1}>{item.name}</Text>
                <Text style={n.job} numberOfLines={1}>{'job' in item && item.job ? item.job : 'Tap to open the conversation'}</Text>
              </View>
              {pill
                ? <View style={n.pill}><Text style={{ color: pill[1], fontSize: 11, fontWeight: '800' }}>● {pill[0]}</Text></View>
                : <Text style={n.chev}>›</Text>}
            </Pressable>
          );
        }}
      />
      <Pressable onPress={() => setModal({})} style={({ pressed }) => [n.fab, pressed && { opacity: .9 }]}>
        <Text style={n.fabT}>＋  New Bot</Text>
      </Pressable>
      {!!modal && <NewBotModal onDone={(nm, j) => { setModal(null); nm && onNew(nm, j); }} />}
    </View>
  );
}

function TabBar({ tab, onTab, onChat }) {
  const tabs = [['home', '🏠', 'Home'], ['chat', '💬', 'Chats'], ['conn', '🧩', 'Connect'], ['set', '⚙️', 'You']];
  return (
    <View style={n.bar}>
      {tabs.map(([k, ic, lbl]) => (
        <Pressable key={k} style={{ flex: 1 }} onPress={() => (k === 'chat' ? onChat() : onTab(k))} hitSlop={4}>
          <View style={n.tabItem} collapsable={false}>
            <Text style={[n.tabIc, tab === k && st2.on]}>{ic}</Text>
            <Text style={[n.tabL, tab === k && st2.on]}>{lbl}</Text>
          </View>
        </Pressable>
      ))}
    </View>
  );
}

function NewBotModal({ onDone }) {
  const [name, setName] = useState('');
  const [job, setJob] = useState('');
  return (
    <View style={m.scrim}>
      <View style={m.modal}>
        <Text style={l.h2}>New Bot</Text>
        <Text style={l.lbl}>Name</Text>
        <TextInput style={l.inl} value={name} onChangeText={setName} placeholder="Inbox" placeholderTextColor={C.muted} />
        <Text style={l.lbl}>What it does</Text>
        <TextInput style={[l.inl, { minHeight: 60 }]} value={job} multiline onChangeText={setJob} placeholder="Triage email every morning" placeholderTextColor={C.muted} />
        <View style={{ flexDirection: 'row', gap: 10, marginTop: 6 }}>
          <Pressable onPress={() => onDone(null)} style={[l.btn, { flex: 1, backgroundColor: C.panel2, borderWidth: 1, borderColor: C.line }]}><Text style={[l.btnT, { color: C.text }]}>Cancel</Text></Pressable>
          <Pressable onPress={() => onDone(name.trim(), job.trim())} disabled={!name.trim()} style={[l.btn, { flex: 1, opacity: name.trim() ? 1 : .5 }]}><Text style={l.btnT}>Create</Text></Pressable>
        </View>
      </View>
    </View>
  );
}

// ----------------------------------------------------------------- settings
function Settings({ onOut, onCheck, onPlug }) {
  const c2 = conf();
  const mail2 = String(cfgGet(K_EMAIL) || '');
  const [updMsg, setUpdMsg] = useState('');
  const [checking, setChecking] = useState(false);
  const check = async () => {
    setChecking(true); setUpdMsg('');
    try { const u = await onCheck(true); setUpdMsg(u ? ('New version ' + u.version + ' is available — tap the banner to install it.') : 'You are up to date.'); }
    catch (e) { setUpdMsg('Could not check: ' + (e.message || 'network')); }
    setChecking(false);
  };
  let host = '';
  try { host = new URL(c2.base).host; } catch (e) { host = c2.base || ''; }
  return (
    <View style={n.screen}>
      <View style={n.hdr}>
        <View style={{ flex: 1 }}>
          <Text style={n.brand}>Settings</Text>
          <Text style={n.sub}>app v{APP_VERSION}</Text>
        </View>
      </View>
      <FlatList
        data={null}
        ListHeaderComponent={
          <View style={{ padding: 16, gap: 10 }}>
            <View style={[n.card, { paddingVertical: 15 }]}>
              <View style={[n.av, { backgroundColor: C.solid, borderColor: C.solid }]}>
                <Text style={{ fontSize: 20 }}>{mail2 ? '👤' : '🤖'}</Text>
              </View>
              <View style={{ flex: 1 }}>
                <Text style={n.name} numberOfLines={1}>{mail2 || 'Signed in'}</Text>
                <Text style={n.job} numberOfLines={1}>on {host || 'your server'}</Text>
              </View>
              <View style={n.pill}><Text style={{ color: C.ok, fontSize: 11, fontWeight: '800' }}>● online</Text></View>
            </View>
            <Row icon="🧩" label="Connectors & MCP" sub="Gmail, Slack, GitHub, Notion, MCP servers…" onPress={onPlug} />
            <Row icon="⬇️" label="Check for updates" sub={checking ? 'Checking…' : (updMsg || 'Install new versions over the air')} onPress={check} />
            <Row icon="🖥" label="Server" sub={host || c2.base} />
            <Pressable onPress={onOut} style={({ pressed }) => [n.row, { borderColor: '#5c2723' }, pressed && { opacity: .9 }]}>
              <View style={n.rowIc}><Text style={{ fontSize: 15 }}>🚪</Text></View>
              <View style={{ flex: 1 }}><Text style={[n.rowL, { color: C.bad }]}>Sign out</Text><Text style={n.rowS}>Clears this phone's saved sign-in</Text></View>
            </Pressable>
            <Text style={l.cap}>Your Bots keep running on the server either way — sign-out only affects this phone. Google sign-in is coming next.</Text>
          </View>
        }
      />
    </View>
  );
}

function Row({ icon, label, sub, onPress }) {
  const body = (
    <>
      <View style={n.rowIc}><Text style={{ fontSize: 15 }}>{icon}</Text></View>
      <View style={{ flex: 1 }}><Text style={n.rowL}>{label}</Text>{!!sub && <Text style={n.rowS} numberOfLines={2}>{sub}</Text>}</View>
      {onPress ? <Text style={n.chev}>›</Text> : null}
    </>
  );
  return onPress
    ? <Pressable onPress={onPress} style={({ pressed }) => [n.row, pressed && { opacity: .9 }]}>{body}</Pressable>
    : <View style={n.row}>{body}</View>;
}

const l = StyleSheet.create({
  box: { flex: 1, backgroundColor: C.bg, justifyContent: 'center', padding: 22, gap: 8 },
  seg: { flexDirection: 'row', gap: 6, marginTop: 8, marginBottom: 2 },
  segB: { flex: 1, paddingVertical: 9, borderRadius: 999, backgroundColor: C.panel2, borderWidth: 1, borderColor: C.line, alignItems: 'center' },
  segOn: { backgroundColor: C.solid, borderColor: C.solid },
  segT: { color: C.muted, fontSize: 13.5, fontWeight: '700' },
  tiny: { color: C.muted, fontSize: 12, textDecorationLine: 'underline', marginTop: 10 },
  h1: { color: C.text, fontSize: 26, fontWeight: '900', marginBottom: 2 },
  h2: { color: C.text, fontSize: 18, fontWeight: '800' },
  cap: { color: C.muted, fontSize: 13, marginBottom: 8 },
  lbl: { color: C.muted, fontSize: 12, textTransform: 'uppercase', letterSpacing: 1 },
  inl: { backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 12, color: C.text, padding: 11, fontSize: 15 },
  btn: { backgroundColor: C.solid, borderRadius: 12, padding: 13, alignItems: 'center', marginTop: 6 },
  btnT: { color: C.onSolid, fontWeight: '800', fontSize: 15 },
  err: { color: C.bad, fontSize: 13 },
});

const n = StyleSheet.create({
  screen: { flex: 1, backgroundColor: C.bg },
  hdr: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 16, paddingTop: 14, paddingBottom: 10, borderBottomWidth: 1, borderColor: C.line },
  brand: { color: C.text, fontSize: 24, fontWeight: '900', letterSpacing: -.5 },
  sub: { color: C.muted, fontSize: 12.5, marginTop: 2 },
  hbtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: C.panel2, borderWidth: 1, borderColor: C.line, alignItems: 'center', justifyContent: 'center' },
  card: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 18, padding: 14 },
  av: { width: 46, height: 46, borderRadius: 23, backgroundColor: C.panel2, borderWidth: 1, borderColor: C.line, alignItems: 'center', justifyContent: 'center' },
  name: { color: C.text, fontSize: 15.5, fontWeight: '800' },
  job: { color: C.muted, fontSize: 12.5 },
  pill: { backgroundColor: C.okSoft, borderWidth: 1, borderColor: C.line, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 5 },
  chev: { color: C.muted, fontSize: 22, fontWeight: '600' },
  empty: { alignItems: 'center', gap: 6, padding: 30, paddingTop: 60 },
  emptyT: { color: C.text, fontSize: 19, fontWeight: '800' },
  emptyS: { color: C.muted, fontSize: 13.5, textAlign: 'center', lineHeight: 19 },
  fab: { position: 'absolute', right: 16, bottom: 72, backgroundColor: C.solid, borderRadius: 999, paddingHorizontal: 18, paddingVertical: 13, elevation: 6 },
  fabT: { color: C.onSolid, fontWeight: '800', fontSize: 15 },
  bar: { flexDirection: 'row', borderTopWidth: 1, borderColor: C.line, backgroundColor: C.panel, paddingHorizontal: 6, paddingVertical: 5, elevation: 10 },
  tabItem: { flex: 1, alignItems: 'center', gap: 1, paddingVertical: 3, borderRadius: 12 },
  tabIc: { fontSize: 19, opacity: .55 },
  tabL: { color: C.muted, fontSize: 10.5, fontWeight: '700' },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 16, padding: 13 },
  rowIc: { width: 38, height: 38, borderRadius: 12, backgroundColor: C.panel2, borderWidth: 1, borderColor: C.line, alignItems: 'center', justifyContent: 'center' },
  rowL: { color: C.text, fontSize: 15, fontWeight: '700' },
  rowS: { color: C.muted, fontSize: 12, marginTop: 1 },
});
const st2 = { on: { opacity: 1, color: C.text } };
const sh = StyleSheet.create({
  banner: { backgroundColor: C.okSoft, borderTopWidth: 1, borderColor: C.line, paddingVertical: 12, paddingHorizontal: 14, height: 44, justifyContent: 'center' },
  bannerT: { color: C.ok, fontWeight: '700', fontSize: 13 },
});
const m = StyleSheet.create({
  scrim: { position: 'absolute', inset: 0, backgroundColor: 'rgba(0,0,0,.65)', alignItems: 'center', justifyContent: 'center', padding: 20 },
  modal: { width: '100%', backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 18, padding: 16, gap: 8 },
});
