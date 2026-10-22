import React, { useEffect, useRef, useState, useCallback } from 'react';
import { SafeAreaProvider, useSafeAreaInsets } from 'react-native-safe-area-context';
import { Animated, Modal, View, Text, TextInput, Pressable, FlatList, StyleSheet, Linking } from 'react-native';
import { StatusBar as ExpoStatus } from 'expo-status-bar';
import { api, connectEvents, loadKey, saveConf, tryConf, clearConf, conf, cfgGet, K_SERVER, K_TOKEN, K_EMAIL, K_PASS, loginEmail, loginGoogle } from './src/session';
import { C } from './src/theme';
import { checkUpdate, installUpdate, APP_VERSION } from './src/updates';
import Connect, { AuthModal } from './src/Connect';
import Chat from './src/Chat';
import { TopBar, DashboardScreen, MarketplaceScreen, ApprovalsScreen, McpScreen, RoutinesScreen, FilesScreen, WalletScreen } from './src/Screens';

function AnsRow({ onSend, placeholder }) {
  const [v, setV] = useState('');
  return (
    <View style={{ flexDirection: 'row', gap: 8, marginTop: 4 }}>
      <TextInput
        value={v}
        onChangeText={setV}
        placeholder={placeholder}
        placeholderTextColor={C.muted}
        style={{ flex: 1, color: C.text, fontSize: 13, borderWidth: 1, borderColor: C.warn, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 7 }}
        autoCapitalize="none"
        autoCorrect={false}
        returnKeyType="send"
        onSubmitEditing={() => { const t = v.trim(); if (t) { onSend(t); setV(''); } }}
      />
      <Pressable onPress={() => { const t = v.trim(); if (t) { onSend(t); setV(''); } }}
        style={({ pressed }) => [n.appB, pressed && { opacity: 0.85 }]}>
        <Text style={n.appOk}>Send</Text>
      </Pressable>
    </View>
  );
}

function asky(a) {
  return a.category === 'question' || a.tool === 'ask_code';
}

export default function App() {
  return (
    <SafeAreaProvider>
      <ZbRoot />
    </SafeAreaProvider>
  );
}

function ZbRoot() {
  const [ready, setReady] = useState(false);
  const [signed, setSigned] = useState(false);
  const [screen, setScreen] = useState('home');            // dash home chat market approvals plugins mcp routines files wallet set
  const [menuOpen, setMenuOpen] = useState(false);         // side drawer
  const [bots, setBots] = useState([]);
  const [busy, setBusy] = useState({});                    // bot_id -> {status, thread_id}
  const [apps, setApps] = useState([]);                    // pending approvals, server-wide
  const [noteBadge, setNoteBadge] = useState(0);           // unread notifications
  const [noteOpen, setNoteOpen] = useState(false);
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
      setApps(d.approvals || []);
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
    if (ev.type === 'bots' || ev.type === 'groups' || ev.type === 'approval') refresh();
    if (ev.type === 'notification') { setNoteBadge((b) => b + 1); refresh(); }
    if (['message', 'delta', 'delta_reset', 'turn', 'approval'].includes(ev.type)) chatHandler.current && chatHandler.current(ev);
  }, [refresh, chatHandler]);

  // one live connection for the whole app
  useEffect(() => {
    if (!signed) return undefined;
    evtRef.current = connectEvents({ since: lastRef.current, onEvent, onConn: () => {}, onSince: () => {} });
    api('/api/notifications?unread_only=1&limit=200').then((rows) => setNoteBadge(Array.isArray(rows) ? rows.length : 0)).catch(() => {});
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

  const newBot = async (name, job, emoji) => {
    try { await api('/api/bots', { method: 'POST', body: JSON.stringify({ name, job, emoji }) }); await refresh(); }
    catch (e) { /* ignore */ }
  };

  const decideApp = async (a, approve, remember, answer) => {
    setApps((cur) => cur.filter((x) => x.id !== a.id));
    const body = { approve, remember };
    if (answer) body.answer = answer;
    try { await api('/api/approvals/' + a.id + '/decide', { method: 'POST', body: JSON.stringify(body) }); } catch (e) { /* ignore */ }
    refresh();
  };
  const [botModal, setBotModal] = useState(null);          // bot open in settings sheet

  // Chats tab: most recent chat, else the first Bot's main thread
  const openChatTab = useCallback(() => {
    if (chat) setScreen('chat');
    else if (bots.length) openBot(bots[0]);
    else setScreen('home');
  }, [chat, bots, openBot]);
  const openChatTabRef = useRef(null);
  useEffect(() => { openChatTabRef.current = openChatTab; }, [openChatTab]);
  const go = useCallback((k) => { setMenuOpen(false); if (k === 'chat') openChatTabRef.current && openChatTabRef.current(); else setScreen(k); }, []);

  const menuProps = {
    working: Object.keys(busy).length,
    approvals: apps.length,
    notes: noteBadge,
    tab: screen,
    onGo: go,
    onClose: () => setMenuOpen(false),
  };

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
        onMenu={() => setMenuOpen(true)}
      />
      <Drawer {...menuProps} />
    </Shell>
  );

  if (screen === 'plugins') return (
    <Shell upd={upd} onUpd={doUpdate} updState={updState}>
      <Connect onBack={() => setScreen('home')} onMenu={() => setMenuOpen(true)} />
      <Drawer {...menuProps} />
    </Shell>
  );

  const menuBlock = <Drawer {...menuProps} />;

  switch (screen) {
    case 'dash': return (
      <Shell upd={upd} onUpd={doUpdate} updState={updState}>
        <DashboardScreen onMenu={() => setMenuOpen(true)} bots={bots} approvals={apps} working={Object.keys(busy).length} />
        {menuBlock}
      </Shell>
    );
    case 'market': return (
      <Shell upd={upd} onUpd={doUpdate} updState={updState}>
        <MarketplaceScreen onMenu={() => setMenuOpen(true)} onRefresh={refresh} />
        {menuBlock}
      </Shell>
    );
    case 'approvals': return (
      <Shell upd={upd} onUpd={doUpdate} updState={updState}>
        <ApprovalsScreen onMenu={() => setMenuOpen(true)} apps={apps} bots={bots} decide={decideApp} />
        {menuBlock}
      </Shell>
    );
    case 'mcp': return (
      <Shell upd={upd} onUpd={doUpdate} updState={updState}>
        <McpScreen onMenu={() => setMenuOpen(true)} />
        {menuBlock}
      </Shell>
    );
    case 'routines': return (
      <Shell upd={upd} onUpd={doUpdate} updState={updState}>
        <RoutinesScreen onMenu={() => setMenuOpen(true)} />
        {menuBlock}
      </Shell>
    );
    case 'files': return (
      <Shell upd={upd} onUpd={doUpdate} updState={updState}>
        <FilesScreen onMenu={() => setMenuOpen(true)} />
        {menuBlock}
      </Shell>
    );
    case 'wallet': return (
      <Shell upd={upd} onUpd={doUpdate} updState={updState}>
        <WalletScreen onMenu={() => setMenuOpen(true)} bots={bots} />
        {menuBlock}
      </Shell>
    );
    case 'set': return (
      <Shell upd={upd} onUpd={doUpdate} updState={updState}>
        <Settings onOut={async () => { await clearConf(); setSigned(false); }} onCheck={runCheck} onPlug={() => setScreen('plugins')} />
        <DragMenu onMenu={() => setMenuOpen(true)} />
        {menuBlock}
      </Shell>
    );
    default: break;
  }

  return (
    <Shell upd={upd} onUpd={doUpdate} updState={updState}>
      <Home bots={bots} busy={busy} apps={apps} noteBadge={noteBadge} onOpen={openBot} onPlug={() => setScreen('plugins')} onBell={() => setNoteOpen(true)} onNew={newBot} onDecide={decideApp} onBotLong={setBotModal} onMenu={() => setMenuOpen(true)} />
      <DragMenu onMenu={() => setMenuOpen(true)} />
      {!!botModal && <BotModal bot={botModal} onClose={() => setBotModal(null)} onCloseSaved={() => { setBotModal(null); refresh(); }} />}
      {noteOpen && <NotifSheet onClose={() => setNoteOpen(false)} onReadAll={() => setNoteBadge(0)} />}
      {menuBlock}
    </Shell>
  );
}

// floating handle that opens the side menu from every screen
function DragMenu({ onMenu }) {
  return (
    <Pressable onPress={onMenu} hitSlop={12}
      style={{ position: 'absolute', left: 0, top: '50%', marginTop: -34, width: 30, height: 68, borderTopRightRadius: 20, borderBottomRightRadius: 20, backgroundColor: C.panel2, borderWidth: 1, borderLeftWidth: 0, borderColor: C.line, alignItems: 'center', justifyContent: 'center', elevation: 8 }}>
      <Text style={{ color: C.muted, fontSize: 16 }}>»</Text>
    </Pressable>
  );
}

// ------------------------------------------------------------ 11-tab side drawer
const DR = StyleSheet.create({
  scrim: { opacity: 0 },
  panel: { position: 'absolute', left: 0, top: 0, bottom: 0, backgroundColor: C.bg, borderRightWidth: 1, borderColor: C.line, elevation: 16, paddingHorizontal: 10, paddingTop: 54 },
  brand: { color: C.text, fontSize: 19, fontWeight: '900', letterSpacing: -.4 },
  vs: { color: C.muted, fontSize: 11 },
  grp: { color: C.muted, fontSize: 10.5, fontWeight: '800', letterSpacing: 1.4, marginTop: 14, marginBottom: 4, paddingHorizontal: 8 },
  item: { flexDirection: 'row', alignItems: 'center', gap: 10, borderRadius: 12, paddingVertical: 10, paddingHorizontal: 10 },
  itemOn: { backgroundColor: C.panel2 },
  ic: { fontSize: 17, width: 22, textAlign: 'center' },
  lbl: { color: C.muted, fontSize: 14.5, fontWeight: '600', flex: 1 },
  lblOn: { color: C.text, fontWeight: '800' },
  cnt: { minWidth: 22, textAlign: 'center', color: C.onSolid, backgroundColor: C.bad, borderRadius: 999, overflow: 'hidden', fontSize: 11.5, fontWeight: '800', paddingHorizontal: 5, paddingVertical: 2 },
});

const TAB_GROUPS = [
  ['OVERVIEW', [
    ['dash', '📈', 'Dashboard'],
    ['home', '🤖', 'Team Status'],
  ]],
  ['COMMUNICATIONS', [
    ['chat', '💬', 'Chats Room'],
  ]],
  ['HIRE & EXTEND', [
    ['market', '🛍', 'Bot Marketplace'],
    ['approvals', '🛂', 'Approvals'],
    ['plugins', '🧩', 'Plugins Hub'],
    ['mcp', '🔌', 'MCP Tools'],
  ]],
  ['AUTOMATION', [
    ['routines', '⏱', 'Routines'],
    ['files', '🗄', 'File Vault'],
  ]],
  ['ADMIN', [
    ['wallet', '💳', 'Wallet'],
    ['set', '⚙️', 'Settings'],
  ]],
];

function Drawer({ open, tab, onGo, onClose, approvals, working, notes }) {
  const dIns = useSafeAreaInsets();
  const [shown, setShown] = useState(false);
  const dx = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (open) {
      setShown(true);
      Animated.timing(dx, { toValue: 1, duration: 200, useNativeDriver: true }).start();
    } else if (shown) {
      Animated.timing(dx, { toValue: 0, duration: 180, useNativeDriver: true }).start(() => setShown(false));
    }
  }, [open]);
  if (!shown) return null;
  const W = 268;
  const tx = dx.interpolate({ inputRange: [0, 1], outputRange: [-W, 0] });
  const sc = dx.interpolate({ inputRange: [0, 1], outputRange: [0, 0.55] });
  const counts = { approvals, work: working, notes };
  return (
    <Modal transparent visible onRequestClose={onClose}>
      <View style={{ flex: 1, backgroundColor: 'transparent' }}>
        <Animated.View style={[{ flex: 1, backgroundColor: '#000' }, { opacity: sc }]}>
          <Pressable style={{ flex: 1 }} onPress={onClose} />
        </Animated.View>
        <Animated.View style={[DR.panel, { width: W, transform: [{ translateX: tx }] }]}>
          <View style={{ paddingHorizontal: 8 }}>
            <Text style={DR.brand}>🤖 ZerotheBot</Text>
            <Text style={DR.vs}>v{APP_VERSION}{working ? ' · ' + working + ' working' : ''}</Text>
          </View>
          <FlatList
            data={TAB_GROUPS}
            keyExtractor={([g]) => g}
            showsVerticalScrollIndicator={false}
            renderItem={({ item: [g, rows] }) => (
              <View>
                <Text style={DR.grp}>{g}</Text>
                {rows.map(([k, ic, lbl]) => {
                  const on = tab === k || (k === 'home' && !tabSPECIAL.includes(tab));
                  const badge = k === 'approvals' ? approvals : k === 'chat' ? notes : 0;
                  return (
                    <Pressable key={k} onPress={() => onGo(k)}
                      style={({ pressed }) => [DR.item, on && DR.itemOn, pressed && { opacity: .88 }]}>
                      <Text style={DR.ic}>{ic}</Text>
                      <Text style={[DR.lbl, on && DR.lblOn]} numberOfLines={1}>{lbl}</Text>
                      {badge > 0 && <Text style={DR.cnt}>{badge > 99 ? '99+' : badge}</Text>}
                    </Pressable>
                  );
                })}
              </View>
            )}
          />
        </Animated.View>
      </View>
    </Modal>
  );
}
const tabSPECIAL = ['chat', 'dash', 'market', 'approvals', 'plugins', 'mcp', 'routines', 'files', 'wallet', 'set'];

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
  const [mail, setMail] = useState('');
  const [pass, setPass] = useState('');
  const [err, setErr] = useState('');
  const [go, setGo] = useState(false);
  const [ov, setOv] = useState(null);         // Google sign-in overlay (unused now; kept for AuthModal webview fallback)
  const gDone = useRef(false);
  const baseRef = useRef(base);
  baseRef.current = base;
  const save = async () => {
    setErr(''); setGo(true);
    try {
      await loginEmail(base, mail, pass);                // cookie session
      await saveConf(base, '', mail, pass);              // remember for silent re-login
      onDone();
    }
    catch (e) { setErr(e.message || 'Could not connect.'); }
    setGo(false);
  };
  const google = () => {
    // System-browser flow: Google refuses embedded webviews, so sign in in real Chrome.
    // The server bounces back to the app via the zerothebot:// deep link.
    setErr('');
    const b = String(base || '').replace(/\/+$/, '');
    if (!/^https?:\/\/.+/.test(b)) { setErr('Enter the server address first.'); return; }
    gDone.current = false;
    Linking.openURL(b + '/auth/google-app/start').catch((e) => setErr('Could not open the browser: ' + (e.message || e)));
  };
  const finishGoogle = async (tok) => {
    setOv(null);
    const b = String(baseRef.current || '').replace(/\/+$/, '');
    try {
      if (String(tok).includes('.')) {
        // google-app flow: the server verified + bound the owner; tok IS the session
        await saveConf(b, tok, '', '');
        onDone();
        return;
      }
      const d = await loginGoogle(b, tok);
      await saveConf(b, d.session, d.email, '');         // session token, no password
      onDone();
    } catch (e) { setErr(e.message || 'Google sign-in failed.'); }
  };
  useEffect(() => {
    const handle = ({ url }) => {
      const m = String(url || '').match(/[?&]tok=([^&]+)/);
      if (m && !gDone.current) { gDone.current = true; finishGoogle(decodeURIComponent(m[1])); }
    };
    let sub = { remove: () => {} };
    try { sub = Linking.addEventListener('url', handle); } catch (e) { /* older RN */ }
    Linking.getInitialURL().then((u) => handle({ url: u })).catch(() => {});
    return () => sub.remove && sub.remove();
  }, []);
  const ready = !!mail.trim() && !!pass;
  return (
    <View style={[l.box, { justifyContent: 'flex-start', paddingTop: 80 }]}>
      <Text style={l.h1}>ZerotheBot</Text>
      <Text style={l.cap}>Sign in with the same email and password as the web app.</Text>
      <Text style={l.lbl}>Server</Text>
      <TextInput style={l.inl} value={base} autoCapitalize="none" autoCorrect={false} keyboardType="url" onChangeText={setBase} />
      <Text style={l.lbl}>Email</Text>
      <TextInput style={l.inl} value={mail} autoCapitalize="none" autoCorrect={false} keyboardType="email-address" onChangeText={setMail} />
      <Text style={l.lbl}>Password</Text>
      <TextInput style={l.inl} value={pass} secureTextEntry onChangeText={setPass} />
      {!!err && <Text style={l.err}>{err}</Text>}
      <Pressable onPress={save} disabled={!ready || go} style={({ pressed }) => [l.btn, !ready && { opacity: .5 }, pressed && { opacity: .9 }]}>
        <Text style={l.btnT}>{go ? 'Checking…' : 'Sign in'}</Text>
      </Pressable>
      <Pressable onPress={google} disabled={go} style={({ pressed }) => [l.btnG, pressed && { opacity: .9 }]}>
        <Text style={l.btnGT}>Continue with Google</Text>
      </Pressable>
      <Text style={l.cap}>One time per install — from then on the app keeps you signed in across updates.</Text>
      {ov && (
        <AuthModal
          o={{ url: ov, auto: false, name: 'Google', title: 'Sign in with Google' }}
          onClose={() => setOv(null)}
        />
      )}
    </View>
  );
}

// ----------------------------------------------------------------- home
function Home({ bots, busy, apps, noteBadge, onOpen, onPlug, onBell, onNew, onDecide, onBotLong, onMenu }) {
  const ins = useSafeAreaInsets();
  const [modal, setModal] = useState(null);
  const working = Object.keys(busy).length;
  return (
    <View style={n.screen}>
      <View style={[n.hdr, { paddingTop: 14 + ins.top }]}>
        <Pressable onPress={onMenu} hitSlop={8}><View style={n.hbtn}><Text style={{ fontSize: 16, color: C.text }}>☰</Text></View></Pressable>
        <View style={{ flex: 1 }}>
          <Text style={n.brand}>ZerotheBot</Text>
          <Text style={n.sub}>
            {bots.length === 0
              ? 'your 24/7 AI teammates live on your server'
              : bots.length + (bots.length === 1 ? ' Bot' : ' Bots') + (working ? '  ·  ' + working + ' working' : '  ·  all idle')}
          </Text>
        </View>
        <Pressable onPress={onBell} hitSlop={8}>
          <View style={n.hbtn}>
            <Text style={{ fontSize: 15 }}>🔔</Text>
            {noteBadge > 0 && <View style={n.badgeDot} collapsable={false} />}
          </View>
        </Pressable>
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
        ListHeaderComponent={
          apps.length ? (
            <View style={{ gap: 8, marginBottom: 4 }}>
              {apps.slice(0, 5).map((a) => {
                const b2 = bots.find((x) => x.id === a.bot_id) || {};
                const isAsk = asky(a);
                return (
                  <View key={a.id} style={n.appCard}>
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                      <Text style={{ fontSize: 18 }}>{b2.emoji || '🤖'}</Text>
                      <Text style={[n.rowL, { flex: 1, fontSize: 13.5 }]} numberOfLines={1}>{b2.name || 'Bot'} {isAsk ? 'needs your answer' : 'needs your OK'}</Text>
                    </View>
                    <Text style={n.job} numberOfLines={3}>{(a.category || '').toUpperCase()}{a.tool ? ' · ' + a.tool : ''}{a.summary ? ' — ' + a.summary : ''}</Text>
                    {isAsk ? (
                      <AnsRow onSend={(t) => onDecide(a, true, false, t)} placeholder={a.tool === 'ask_code' ? 'Paste the code' : 'Type an answer'} />
                    ) : null}
                    <View style={{ flexDirection: 'row', gap: 8, marginTop: 2 }}>
                      {!isAsk && (
                        <>
                          <Pressable onPress={() => onDecide(a, true, true)} style={n.appB}><Text style={n.appOk}>✅ Always</Text></Pressable>
                          <Pressable onPress={() => onDecide(a, true, false)} style={n.appB}><Text style={n.appOk}>Approve</Text></Pressable>
                        </>
                      )}
                      <Pressable onPress={() => onDecide(a, false, false)} style={n.appB}><Text style={n.appNo}>{isAsk ? "Can't do it" : 'Deny'}</Text></Pressable>
                    </View>
                  </View>
                );
              })}
              {apps.length > 5 ? <Text style={n.job}>{apps.length - 5} more waiting — open the Bot to see all.</Text> : null}
            </View>
          ) : null
        }
        renderItem={({ item }) => {
          const s = busy[item.id];
          const pill = s ? (s.status === 'waiting_approval' ? ['needs you', C.warn] : ['working', C.ok]) : null;
          return (
            <Pressable onPress={() => onOpen(item)} onLongPress={() => onBotLong(item)} delayLongPress={400} style={({ pressed }) => [n.card, pressed && { opacity: .92 }]}>
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
      {!!modal && <NewBotModal onDone={(nm, j, em) => { setModal(null); nm && onNew(nm, j, em); }} />}
    </View>
  );
}

function NewBotModal({ onDone }) {
  const [name, setName] = useState('');
  const [job, setJob] = useState('');
  const [em, setEm] = useState('🤖');
  const EMOJIS = ['🤖', '🧠', '📬', '🛍', '📊', '🧾', '🔍', '✍️', '📅', '🌐', '💼', '🧹'];
  return (
    <View style={m.scrim}>
      <View style={m.modal}>
        <Text style={l.h2}>New Bot</Text>
        <Text style={l.lbl}>Name</Text>
        <TextInput style={l.inl} value={name} onChangeText={setName} placeholder="Inbox" placeholderTextColor={C.muted} />
        <Text style={l.lbl}>What it does</Text>
        <TextInput style={[l.inl, { minHeight: 60 }]} value={job} multiline onChangeText={setJob} placeholder="Triage email every morning" placeholderTextColor={C.muted} />
        <Text style={[l.lbl, { marginTop: 4 }]}>Avatar</Text>
        <View style={m.emojiRow}>
          {EMOJIS.map((e) => (
            <Pressable key={e} onPress={() => setEm(e)} style={[m.emojiB, e === em && m.emojiOn]}><Text style={{ fontSize: 20 }}>{e}</Text></Pressable>
          ))}
        </View>
        <View style={{ flexDirection: 'row', gap: 10, marginTop: 6 }}>
          <Pressable onPress={() => onDone(null)} style={[l.btn, { flex: 1, backgroundColor: C.panel2, borderWidth: 1, borderColor: C.line }]}><Text style={[l.btnT, { color: C.text }]}>Cancel</Text></Pressable>
          <Pressable onPress={() => onDone(name.trim(), job.trim(), em)} disabled={!name.trim()} style={[l.btn, { flex: 1, opacity: name.trim() ? 1 : .5 }]}><Text style={l.btnT}>Create</Text></Pressable>
        </View>
      </View>
    </View>
  );
}

// ----------------------------------------------------------------- bot settings
function BotModal({ bot, onClose, onCloseSaved }) {
  const [em, setEm] = useState(bot.emoji || '🤖');
  const [mems, setMems] = useState(null);
  const [memAdd, setMemAdd] = useState('');
  const [saving, setSaving] = useState(false);
  const loadMem = useCallback(async () => {
    try { const d = await api('/api/bots/' + bot.id + '/memory'); setMems(Array.isArray(d) ? d : (d.items || [])); }
    catch (e) { setMems([]); }
  }, [bot.id]);
  useEffect(() => { loadMem(); }, [loadMem]);
  const EMOJIS = ['🤖', '🧠', '📬', '🛍', '📊', '🧾', '🔍', '✍️', '📅', '🌐', '💼', '🧹'];
  const save = async () => {
    setSaving(true);
    try { await api('/api/bots/' + bot.id, { method: 'PUT', body: JSON.stringify({ emoji: em }) }); onCloseSaved(); }
    catch (e) { /* ignore */ }
    setSaving(false);
  };
  const addMem = async () => {
    const t2 = memAdd.trim();
    if (!t2) return;
    setMemAdd('');
    try { await api('/api/bots/' + bot.id + '/memory', { method: 'POST', body: JSON.stringify({ kind: 'preference', text: t2 }) }); refreshMems(); }
    catch (e) { /* ignore */ }
  };
  const delMem = async (mid) => {
    try { await api('/api/bots/' + bot.id + '/memory/' + mid, { method: 'DELETE' }); refreshMems(); }
    catch (e) { /* ignore */ }
  };
  const refreshMems = () => { setMems(null); loadMem(); };
  return (
    <View style={m.scrim}>
      <View style={m.modal}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
          <View style={[n.av, { width: 40, height: 40, borderRadius: 20 }]}><Text style={{ fontSize: 19 }}>{em}</Text></View>
          <Text style={l.h2} numberOfLines={1}>{bot.name}</Text>
          <View style={st3.gap} />
          <Pressable onPress={onClose} hitSlop={8}><Text style={l.cap}>✕</Text></Pressable>
        </View>
        <Text style={[l.lbl, { marginTop: 4 }]}>Avatar</Text>
        <View style={m.emojiRow}>
          {EMOJIS.map((e) => (
            <Pressable key={e} onPress={() => setEm(e)} style={[m.emojiB, e === em && m.emojiOn]}><Text style={{ fontSize: 20 }}>{e}</Text></Pressable>
          ))}
        </View>
        <Pressable onPress={save} disabled={saving} style={[l.btn, { opacity: saving ? .5 : 1 }]}><Text style={l.btnT}>Save avatar</Text></Pressable>
        <Text style={[l.lbl, { marginTop: 6 }]}>Memory — what it knows about you</Text>
        <View style={m.memList}>
          {mems === null ? <Text style={l.cap}>Loading…</Text> : (mems.length
            ? mems.slice(0, 30).map((mm) => (
              <View key={mm.id} style={m.memRow}>
                <Text style={[l.cap, { flex: 1, marginBottom: 0 }]} numberOfLines={2}>{mm.kind ? '[' + mm.kind + '] ' : ''}{mm.text}</Text>
                <Pressable onPress={() => delMem(mm.id)} hitSlop={6}><Text style={{ color: C.bad, fontSize: 14 }}>✕</Text></Pressable>
              </View>
            ))
            : <Text style={l.cap}>Nothing yet — it will remember as you work with it.</Text>)}
        </View>
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <TextInput style={[l.inl, { flex: 1 }]} value={memAdd} onChangeText={setMemAdd} placeholder="Teach it something…"
            placeholderTextColor={C.muted} onSubmitEditing={addMem} />
          <Pressable onPress={addMem} style={[n.hbtn, { backgroundColor: C.solid }]}><Text style={{ fontSize: 18, color: C.onSolid }}>＋</Text></Pressable>
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
  btnG: { backgroundColor: '#fff', borderRadius: 12, borderWidth: 1, borderColor: C.line, padding: 13, alignItems: 'center', marginTop: 6 },
  btnGT: { color: '#1f1f1f', fontWeight: '800', fontSize: 15 },
  err: { color: C.bad, fontSize: 13 },
});

const n = StyleSheet.create({
  screen: { flex: 1, backgroundColor: C.bg },
  hdr: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 16, paddingTop: 14, paddingBottom: 10, borderBottomWidth: 1, borderColor: C.line },
  brand: { color: C.text, fontSize: 24, fontWeight: '900', letterSpacing: -.5 },
  sub: { color: C.muted, fontSize: 12.5, marginTop: 2 },
  hbtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: C.panel2, borderWidth: 1, borderColor: C.line, alignItems: 'center', justifyContent: 'center' },
  badgeDot: { position: 'absolute', top: 5, right: 6, width: 9, height: 9, borderRadius: 4.5, backgroundColor: C.bad, borderWidth: 1.5, borderColor: C.panel2 },
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
  appCard: { backgroundColor: C.panel, borderWidth: 1, borderColor: C.warn, borderRadius: 16, padding: 12, gap: 4 },
  appB: { backgroundColor: C.panel2, borderWidth: 1, borderColor: C.line, borderRadius: 999, paddingHorizontal: 11, paddingVertical: 6 },
  appOk: { color: C.ok, fontSize: 12.5, fontWeight: '800' },
  appNo: { color: C.bad, fontSize: 12.5, fontWeight: '800' },
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
  modal: { width: '100%', backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 18, padding: 16, gap: 8, maxHeight: '88%' },
  emojiRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 4 },
  emojiB: { width: 40, height: 40, borderRadius: 12, borderWidth: 1, borderColor: C.line, backgroundColor: C.panel2, alignItems: 'center', justifyContent: 'center' },
  emojiOn: { borderColor: C.solid, backgroundColor: C.solid, opacity: .95 },
  memList: { gap: 4, maxWidth: '100%' },
  memRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
});
const st3 = { gap: { flex: 1 } };

// ----------------------------------------------------------------- notification inbox
const KIND_ICON = { approval: '🛂', question: '❓', finished: '✅', error: '⚠️', routine: '🗓', digest: '📰', test: '🔔', takeover: '🖥', login: '🔐' };

function NotifSheet({ onClose, onReadAll }) {
  const [rows, setRows] = useState(null);
  const load = useCallback(async () => {
    try { const d = await api('/api/notifications?limit=60'); setRows(Array.isArray(d) ? d : (d.items || [])); }
    catch (e) { setRows([]); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const readAll = async () => {
    try { await api('/api/notifications/read', { method: 'POST', body: '{}' }); } catch (e) { /* ignore */ }
    onReadAll(); load();
  };
  return (
    <View style={m.scrim}>
      <View style={[m.modal, { gap: 6 }]}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Text style={l.h2}>Notifications</Text>
          <View style={st3.gap} />
          <Pressable onPress={readAll}><Text style={[l.cap, { color: C.ok, fontSize: 13 }]}>Mark all read</Text></Pressable>
          <Pressable onPress={onClose} hitSlop={8}><Text style={l.cap}>✕</Text></Pressable>
        </View>
        <View style={{ maxHeight: 380 }}>
          {rows === null ? <Text style={l.cap}>Loading…</Text> : (rows.length ? rows.map((r) => (
            <View key={r.id} style={[m.memRow, { alignItems: 'flex-start', opacity: r.read ? .65 : 1 }]}>
              <Text style={{ fontSize: 16, width: 22 }}>{KIND_ICON[r.kind] || '🤖'}</Text>
              <View style={{ flex: 1 }}>
                <Text style={[n.rowL, { fontSize: 13.5 }]} numberOfLines={1}>{r.title}</Text>
                {!!r.body && <Text style={n.rowS} numberOfLines={3}>{r.body}</Text>}
                <Text style={[n.rowS, { fontSize: 10.5, marginTop: 2 }]}>{r.bot_name || ''}{r.bot_name ? ' · ' : ''}{timeAgo(r.ts)}</Text>
              </View>
            </View>
          )) : <Text style={l.cap}>Nothing yet. Bots will tap you here when they finish jobs, need approval, or hit an error.</Text>)}
        </View>
      </View>
    </View>
  );
}

function timeAgo(ts) {
  const n = Number(ts);
  if (!isFinite(n) || n <= 0) return '';
  const d = Math.max(0, (Date.now() - n * 1000) / 1000);
  if (d < 60) return 'just now';
  if (d < 3600) return Math.round(d / 60) + 'm ago';
  if (d < 86400) return Math.round(d / 3600) + 'h ago';
  return Math.round(d / 86400) + 'd ago';
}
