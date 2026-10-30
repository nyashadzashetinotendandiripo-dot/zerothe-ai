import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { View, Text, TextInput, Pressable, FlatList, StyleSheet, KeyboardAvoidingView, Platform, Image, ActivityIndicator, ScrollView } from 'react-native';
import { WebView } from 'react-native-webview';
import { api, API_BASE } from './session';
import md from './md';
import { aiBody, uiPlain, uiStreamText } from './widgets';
import { breadcrumb, note } from './diag';

// Grok-style chat: near-black canvas, assistant answers as plain full-width text,
// user messages as compact right-aligned bubbles, floating pill composer.
const BG = '#0a0a0a';
const PANEL = '#121212';
const BUBBLE = '#2b2b2b';
const TEXT = '#ececec';
const MUTED = '#8f8f8f';
const LINE = '#1f1f1f';
const ACCENT = '#ffffff';

function AnsRow({ onSend, placeholder }) {
  const [v, setV] = useState('');
  const go = () => { const t = v.trim(); if (t) { onSend(t); setV(''); } };
  return (
    <View style={{ flexDirection: 'row', gap: 8, marginTop: 8 }}>
      <TextInput
        value={v}
        onChangeText={setV}
        placeholder={placeholder}
        placeholderTextColor={MUTED}
        style={st.ansIn}
        autoCapitalize="none"
        autoCorrect={false}
        returnKeyType="send"
        onSubmitEditing={go}
      />
      <Pressable onPress={go} style={({ pressed }) => [st.ansBtn, pressed && { opacity: 0.85 }]}>
        <Text style={st.ansBtnT}>Send</Text>
      </Pressable>
    </View>
  );
}

function fmtT(it) {
  const d2 = parseDate(it);
  return d2 ? d2.toTimeString().slice(0, 5) : '';
}

function parseDate(it) {
  const v = it && it.ts;
  if (v == null) return null;
  if (typeof v === 'number' || (typeof v === 'string' && /^\d+(\.\d+)?$/.test(v))) {
    const n = Number(v);
    if (!isFinite(n) || n <= 0) return null;
    const d = new Date(n > 1e12 ? n : n * 1000);
    return isNaN(d.getTime()) ? null : d;
  }
  const d = new Date(String(v));
  return isNaN(d.getTime()) ? null : d;
}

const REACTS = ['👍', '❤️', '🎉', '😄', '🚀', '👀'];

const TOOL_GLYPH = { running: '•', ok: '✓', error: '✕', denied: '⛔', blocked: '⛔' };

// One assistant message can carry several rows (the text plus each tool call), and
// they share the message id — so identity needs the type and the tool call id.
// Never index-based: keys must stay stable across polls or the list re-mounts.
const asText = (v) => (v == null ? '' : (typeof v === 'string' ? v : (typeof v === 'number' ? String(v) : '')));
const asList = (v) => (Array.isArray(v) ? v : []);
const keyOf = (i) => asText(i && i.id) + '|' + asText(i && i.type) + '|' + asText(i && i.call_id);

export default function Chat(props) {
  return <ChatBoundary><ChatScreen {...props} /></ChatBoundary>;
}

// A chat that cannot draw one message must not take the whole app with it: show
// a reload button and stay signed in.
class ChatBoundary extends React.Component {
  constructor(props) { super(props); this.state = { err: null }; }
  static getDerivedStateFromError(err) { return { err }; }
  componentDidCatch(err, info) { note('render', err, 'chat screen'); }
  render() {
    if (this.state.err) {
      return (
        <View style={{ flex: 1, backgroundColor: BG, alignItems: 'center', justifyContent: 'center', padding: 26, gap: 10 }}>
          <Text style={{ fontSize: 34 }}>🛠</Text>
          <Text style={{ color: TEXT, fontSize: 17, fontWeight: '800' }}>This chat hit a snag</Text>
          <Text style={{ color: MUTED, fontSize: 13, textAlign: 'center' }}>{String((this.state.err && this.state.err.message) || 'Unknown error')}</Text>
          <Pressable onPress={() => this.setState({ err: null })} style={{ backgroundColor: '#00a884', borderRadius: 999, paddingHorizontal: 22, paddingVertical: 11, marginTop: 6 }}>
            <Text style={{ color: '#fff', fontWeight: '800' }}>Reload the chat</Text>
          </Pressable>
          <Text style={{ color: MUTED, fontSize: 11.5, textAlign: 'center' }}>Your Bots keep running on the server — nothing was lost.</Text>
        </View>
      );
    }
    return this.props.children;
  }
}

function ChatScreen({ thread, threads, onSwitch, onBack, onEvent, onMenu }) {
  const ins = useSafeAreaInsets();
  const [items, setItems] = useState([]);
  const [apps, setApps] = useState([]);              // pending approvals on this thread
  const [streams, setStreams] = useState({});        // stream_id -> accumulated text
  const [busy, setBusy] = useState(null);            // {status, steps}
  const [replyTo, setReplyTo] = useState(null);
  const [pick, setPick] = useState(null);            // bubble long-pressed for react/reply
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(true);
  const [liveFull, setLiveFull] = useState(false);
  const [liveUrl, setLiveUrl] = useState('');
  const [liveBusy, setLiveBusy] = useState(false);
  const [liveErr, setLiveErr] = useState('');
  const [histOpen, setHistOpen] = useState(false);
  const [histQ, setHistQ] = useState('');
  const [err, setErr] = useState('');
  const listRef = useRef(null);
  const fastRef = useRef(0);            // keep fast polling for a while after sending
  const busyRef = useRef(null);
  busyRef.current = busy;
  // scrollToEnd can throw when the list has no rows yet (or is mid-measure), and
  // that throw happens in a native callback where React cannot catch it.
  const scrollEnd = useCallback(() => {
    try { if (listRef.current) listRef.current.scrollToEnd({ animated: false }); } catch (e) { /* not laid out yet */ }
  }, []);

  const apply = useCallback((d, quiet) => {
    const rows = asList(d && d.items);
    setItems((prev) => {
      if (!quiet) return rows;
      // Merge by row identity instead of only appending: a streaming answer and
      // a tool step keep the same id while their text/status change, so appending
      // alone left the screen stuck on the first, empty version of the reply.
      const prevByKey = new Map(prev.map((i) => [keyOf(i), i]));
      const merged = rows.map((i) => prevByKey.get(keyOf(i)) || i);
      const seen = new Set(rows.map(keyOf));
      // Optimistic bubbles are only a placeholder for the server's copy.
      const keep = prev.filter((i) => !i.pending && !seen.has(keyOf(i)));
      return [...merged, ...keep];
    });
    setApps(asList(d && d.approvals));
    const running = !!(d && d.running && d.running.length);
    if (!running) setStreams({});
    const next = running ? 'running' : ((asList(d && d.approvals).length) ? 'waiting_approval' : null);
    setBusy((prev) => ((prev && prev.status === next) ? prev : (next ? { status: next } : null)));
    setErr('');
  }, []);

  const load = useCallback(async (quiet) => {
    try {
      const d = await api('/api/threads/' + thread.id, { timeout: 25000 });
      apply(d, !!quiet);
    } catch (e) {
      if (!quiet) setErr(String(e.message || e));
    }
    setLoading(false);
  }, [thread.id, apply]);

  useEffect(() => { setItems([]); setLoading(true); setReplyTo(null); setErr(''); load(); }, [thread.id, load]);

  // Safety net: the phone's SSE can be blocked by a proxy or killed by the OS, and
  // then the answer never appeared while the screen kept saying "working". Poll the
  // thread as well — every 1.5 s while a turn runs, every 3 s otherwise, so an
  // answer shows up on its own even if the live stream is dead.
  useEffect(() => {
    let alive = true;
    let t = null;
    const tick = async () => {
      await load(true);
      if (!alive) return;
      const fast = busyRef.current || Date.now() < fastRef.current;
      t = setTimeout(tick, fast ? 1500 : 3000);
    };
    t = setTimeout(tick, ((busyRef.current || Date.now() < fastRef.current) ? 1500 : 3000));
    return () => { alive = false; if (t) clearTimeout(t); };
  }, [load]);

  useEffect(() => {
    onEvent.current = (ev) => {
      // This runs from a socket callback, outside React: an exception here is
      // uncaught and used to close the app. Keep it contained.
      try {
        if (!ev || typeof ev !== 'object') return;
        if (ev.thread_id && ev.thread_id !== thread.id) return;
        if (ev.type === 'message' && Array.isArray(ev.items)) {
          setItems((prev) => {
            const known = new Set(prev.map(keyOf));
            const add = ev.items.filter((i) => !known.has(keyOf(i)));
            return add.length ? [...prev, ...add] : prev;
          });
          setStreams({});
        } else if (ev.type === 'delta') {
          setStreams((s) => ({ ...s, [ev.stream_id]: asText(ev.text) }));
        } else if (ev.type === 'delta_reset') {
          setStreams((s) => { const o = { ...s }; delete o[ev.stream_id]; return o; });
        } else if (ev.type === 'turn') {
          if (['done', 'stopped', 'error', 'limit', 'skipped'].includes(ev.status)) { setBusy(null); load(); }
          else setBusy({ status: ev.status, steps: ev.steps });
        }
      } catch (e) {
        note('event', e, 'chat event ' + asText(ev && ev.type));
      }
    };
    return () => { onEvent.current = null; };
  }, [thread.id, load]);

  const send = async () => {
    const t = text.trim();
    if (!t) return;
    if (!thread?.id) return;
    const pid = replyTo ? replyTo.id : 0;
    const snapshot = t;
    breadcrumb('send', snapshot.slice(0, 80));
    setText(''); setReplyTo(null); setErr('');
    fastRef.current = Date.now() + 120000;     // poll fast until the answer lands
    setBusy({ status: 'running' });            // show "working" at once, start fast polling
    // Show the message immediately (WhatsApp does), then replace it with the
    // server's row as soon as the POST returns or a poll sees it.
    const tmp = 'tmp' + Date.now() + Math.random().toString(36).slice(2, 6);
    setItems((prev) => [...prev, { id: tmp, type: 'user', text: snapshot, ts: Date.now() / 1000, pending: true }]);
    try {
      // The POST is the one call that must not hang: a stuck request used to
      // leave the bubble saying "sending" with no way out.
      await api('/api/threads/' + thread.id + '/messages', { method: 'POST', timeout: 30000, body: JSON.stringify({ text: snapshot, images: [], parent_id: pid }) });
      breadcrumb('send:ok', snapshot.slice(0, 40));
      await load();                            // show it immediately even if SSE lags
    } catch (e) {
      breadcrumb('send:fail', String(e && e.message || e));
      note('send', e, 'thread ' + thread.id);
      setText(snapshot);                       // never lose the message
      setBusy(null);
      setErr(String(e.message || e));
      setItems((prev) => prev.filter((i) => i.id !== tmp));
    }
  };
  const stop = async () => {
    try { await api('/api/threads/' + thread.id + '/stop', { method: 'POST', body: '{}' }); } catch (e) { /* already done */ }
    setTimeout(() => load(), 600);
  };
  // A real live view of the machine's desktop (Instabox VNC link in a WebView)
  // instead of a still screenshot. View-only, short-lived, revoked on close.
  const openLive = async () => {
    setLiveFull(true); setLiveErr('');
    if (liveUrl) return;
    setLiveBusy(true);
    try {
      const d = await api('/api/host/desktop', { method: 'POST', body: JSON.stringify({ viewOnly: true, ttl: 3600 }) });
      if (!d || !d.url) throw new Error('The server did not return a live link.');
      setLiveUrl(d.url);
    } catch (e) {
      setLiveErr(String(e.message || e));
    }
    setLiveBusy(false);
  };
  const closeLive = () => {
    setLiveFull(false); setLiveUrl('');
    api('/api/host/desktop', { method: 'DELETE' }).catch(() => {});
  };
  const react = async (mid, emoji) => { try { await api('/api/messages/' + mid + '/reactions', { method: 'POST', body: JSON.stringify({ emoji, author: 'user' }) }); load(); } catch (e) { /* ignore */ } };
  const decide = async (a, approve, remember, answer) => {
    setApps((cur) => cur.filter((x) => x.id !== a.id));
    const body = { approve, remember };
    if (answer) body.answer = answer;
    try { await api('/api/approvals/' + a.id + '/decide', { method: 'POST', body: JSON.stringify(body) }); } catch (e) { /* ignore */ }
    load();
  };

  const renderRow = ({ item }) => {
    if (!item || typeof item !== 'object') return null;
    if (item.day) return <View style={st.dayWrap}><Text style={st.dayT}>{asText(item.label)}</Text></View>;
    if (item.typing) return (
      <View style={st.typingWrap}><Text style={st.typingT}>●  ●  ●</Text></View>
    );
    if (item.type === 'user') return (
      <View style={st.rowOut}>
        <Pressable onLongPress={() => item.id && setPick(item)} delayLongPress={350} style={st.bubbleOut}>
          <Text selectable style={st.bubbleOutT}>{asText(item.text)}</Text>
          <MsgFoot it={item} time={fmtT(item)} outgoing />
        </Pressable>
      </View>
    );
    if (item.type === 'assistant') return (
      <Pressable onLongPress={() => item.id && setPick(item)} delayLongPress={350} style={st.rowIn}>
        <View style={st.answer}>
          {aiBody(asText(item.text), (t) => md(t, st.answerT), (r) => { setText(r); })}
        </View>
        <MsgFoot it={item} time={fmtT(item)} />
      </Pressable>
    );
    if (item.type === 'tool') {
      const g = TOOL_GLYPH[item.status] || '•';
      const okk = item.status === 'ok' || item.status === 'running';
      return (
        <View style={st.toolWrap}>
          <Text style={[st.toolIc, { color: okk ? '#6fcf97' : '#e57373' }]}>{g}</Text>
          <Text style={st.toolT} numberOfLines={2}>{asText(item.label) || asText(item.tool)}</Text>
          {asList(item.images).map((n, i) => <Image key={i} style={st.shot}
            source={{ uri: API_BASE + '/api/files/shots/' + encodeURIComponent(asText(n)) }} />)}
        </View>
      );
    }
    if (item.type === 'notice') return item.level === 'cmd'
      ? <View style={st.cmdNote}>{md(asText(item.text), st.noteT)}</View>
      : <Text style={st.noteT}>{asText(item.text)}</Text>;
    if (item.type === 'system_note') return <Text style={st.noteT}>{asText(item.text).slice(0, 200)}</Text>;
    return null;
  };

  // A single unrenderable row (bad widget, odd markup) must not take the screen
  // down with it — report it and draw a placeholder instead.
  const renderItem = (args) => {
    try {
      return renderRow(args);
    } catch (e) {
      note('render', e, 'chat row type=' + asText(args && args.item && args.item.type));
      return (
        <View style={st.rowIn}>
          <Text style={st.noteT}>This message could not be drawn. Pull the chat again to reload it.</Text>
        </View>
      );
    }
  };

  const streamRows = Object.entries(streams).filter(([, t]) => t);
  const acts = items.filter((i) => i.type === 'tool');
  const lastAct = acts[acts.length - 1] || null;
  const shotItems = acts.filter((i) => (i.images || []).length);
  const lastShot = shotItems.length ? String(shotItems[shotItems.length - 1].images.slice(-1)[0]) : null;
  const shotUri = lastShot ? API_BASE + '/api/files/shots/' + encodeURIComponent(lastShot) : null;
  const today = new Date().toDateString();
  const disp = [];
  { let lastDay = '';
    items.forEach((it, idx) => {
      const d2 = parseDate(it);
      const k = d2 ? d2.toDateString() : '';
      if (k && k !== lastDay) {
        disp.push({ id: 'day' + idx, day: true, label: k === today ? 'Today' : (k === new Date(Date.now() - 864e5).toDateString() ? 'Yesterday' : k.slice(0, 10)) });
        lastDay = k;
      }
      disp.push(it);
    }); }

  const botName = (threads && threads.find((t) => t.id === thread.id)?.title) || 'ZerotheBot';
  const status = busy
    ? (busy.status === 'waiting_approval' ? 'needs your approval' : 'typing…')
    : (err ? 'offline — tap to retry' : 'online');

  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: BG }} behavior={Platform.OS === 'ios' ? 'padding' : undefined} keyboardVerticalOffset={0}>
      {/* WhatsApp-style top bar: menu · name/status (tap for chat history) */}
      <View style={[st.header, { paddingTop: (ins?.top || 0) + 6 }]}>
        <Pressable onPress={onMenu} hitSlop={10} style={st.hBtn}><Text style={st.hIcon}>☰</Text></Pressable>
        <Pressable style={{ flex: 1 }} onPress={() => { setHistQ(''); setHistOpen(true); }}>
          <Text style={st.htitle} numberOfLines={1}>{botName}</Text>
          <Text style={[st.hstatus, err && { color: '#e57373' }]} numberOfLines={1}>{status}</Text>
        </Pressable>
      </View>

      {!!err && (
        <Pressable style={st.errBar} onPress={() => load()}>
          <Text style={st.errT} numberOfLines={2}>{err} · tap to retry</Text>
        </Pressable>
      )}

      {!!busy && busy.status !== 'waiting_approval' && lastAct && (
        <Pressable style={st.live} onPress={openLive}>
          <Text style={st.liveLbl} numberOfLines={1}>🖥 {lastAct.label || 'Working on the screen'}  ·  tap to watch live</Text>
          {!!shotUri && <Image style={st.liveImgSm} source={{ uri: shotUri }} resizeMode="contain" />}
        </Pressable>
      )}

      <View style={{ flex: 1, backgroundColor: BG }}>
        {loading ? <ActivityIndicator style={{ marginTop: 28 }} color={MUTED} /> : (
          <FlatList
            ref={listRef}
            data={[...disp, ...streamRows.map(([sid, t]) => ({ id: 's' + sid, stream: t }))]}
            keyExtractor={keyOf}
            renderItem={renderStreamAware(renderItem)}
            contentContainerStyle={[st.list, { paddingBottom: 16 }]}
            keyboardShouldPersistTaps="handled"
            onContentSizeChange={scrollEnd}
            onLayout={scrollEnd}
            ListEmptyComponent={!busy ? <Text style={st.empty}>Ask {botName} anything.</Text> : null}
          />
        )}
      </View>

      {!!busy && (
        <View style={st.busyRow}>
          <Text style={{ color: busy.status === 'waiting_approval' ? '#e0a53a' : MUTED, fontSize: 12.5 }}>
            {busy.status === 'waiting_approval' ? 'waiting for your approval' : ''}
          </Text>
          <View style={{ flex: 1 }} />
          <Pressable onPress={stop} hitSlop={8}><Text style={st.stop}>Stop</Text></Pressable>
        </View>
      )}

      {!!apps.length && (
        <View style={st.appCard}>
          {apps.map((a, i) => {
            const isAsk = a.category === 'question' || a.tool === 'ask_code';
            return (
              <View key={a.id || i} style={st.appRow}>
                <Text style={st.appT}>{asText(a.category).toUpperCase()}{a.tool ? ' · ' + asText(a.tool) : ''}{a.summary ? '\n' + asText(a.summary) : ''}</Text>
                {isAsk ? <AnsRow onSend={(t) => decide(a, true, false, t)} placeholder={a.tool === 'ask_code' ? 'Paste the code' : 'Type an answer'} /> : null}
                <View style={st.appBtns}>
                  {!isAsk && (
                    <>
                      <Pressable onPress={() => decide(a, true, true)} style={st.appBtn}><Text style={st.appOk}>✅ Always</Text></Pressable>
                      <Pressable onPress={() => decide(a, true, false)} style={st.appBtn}><Text style={st.appOk}>Approve</Text></Pressable>
                    </>
                  )}
                  <Pressable onPress={() => decide(a, false, false)} style={st.appBtn}><Text style={st.appNo}>{isAsk ? "Can't do it" : 'Deny'}</Text></Pressable>
                </View>
              </View>
            );
          })}
        </View>
      )}

      {!!replyTo && (
        <View style={st.replyBar}>
          <View style={{ flex: 1 }}>
            <Text style={st.replyWho}>{replyTo.type === 'user' ? 'You' : (asText(replyTo.name) || 'Bot')}</Text>
            <Text numberOfLines={1} style={st.replyTxt}>{asText(uiPlain(replyTo.text || '') || replyTo.text).slice(0, 80)}</Text>
          </View>
          <Pressable onPress={() => setReplyTo(null)} hitSlop={8}><Text style={st.replyX}>✕</Text></Pressable>
        </View>
      )}

      {/* WhatsApp-style composer: input · round send */}
      <View style={[st.composerWrap, { paddingBottom: 8 + (ins?.bottom || 0) }]}>
        <View style={st.composer}>
          <TextInput
            style={st.input}
            value={text}
            onChangeText={setText}
            placeholder="Message"
            placeholderTextColor={MUTED}
            multiline
            returnKeyType="send"
            onSubmitEditing={send}
            blurOnSubmit={false}
          />
          <Pressable onPress={send} disabled={!text.trim()} hitSlop={6}
            style={[st.sendBtn, !text.trim() && st.sendBtnOff]}>
            <Text style={[st.sendT, !text.trim() && { color: MUTED }]}>➤</Text>
          </Pressable>
        </View>
      </View>

      {!!pick && (
        <View style={st.scrimM} onStartShouldSetResponder={() => true}>
          <View style={[st.sheet, { paddingBottom: 16 + (ins?.bottom || 0) }]}>
            <View style={st.grabber} />
            <Text style={st.sheetT}>{pick.type === 'user' ? 'Your message' : (asText(pick.name) || 'Bot')}</Text>
            <Text numberOfLines={3} style={st.sheetSub}>{asText(pick.text).slice(0, 140)}</Text>
            <View style={{ flexDirection: 'row', gap: 14, marginTop: 12, flexWrap: 'wrap' }}>
              {REACTS.map((e) => (
                <Pressable key={e} onPress={() => { react(pick.id, e); setPick(null); }} hitSlop={4}>
                  <Text style={{ fontSize: 26 }}>{e}</Text>
                </Pressable>
              ))}
            </View>
            <Pressable onPress={() => { setReplyTo(pick); setPick(null); }} style={st.sheetBtn}><Text style={st.sheetBtnT}>↩  Reply</Text></Pressable>
            <Pressable onPress={() => setPick(null)} style={[st.sheetBtn, { backgroundColor: 'transparent' }]}><Text style={[st.sheetBtnT, { color: MUTED }]}>Cancel</Text></Pressable>
          </View>
        </View>
      )}

      {histOpen && (
        <View style={st.scrimM}>
          <View style={[st.sheet, { paddingBottom: 16 + (ins?.bottom || 0) }]}>
            <View style={st.grabber} />
            <TextInput style={st.histIn} value={histQ} onChangeText={setHistQ} placeholder="Search chats" placeholderTextColor={MUTED} autoCorrect={false} />
            <ScrollView style={{ maxHeight: 320 }} nestedScrollEnabled>
              {(threads || []).filter((t) => !histQ.trim() || String(t.title || '').toLowerCase().includes(histQ.trim().toLowerCase())).map((t) => (
                <Pressable key={t.id} onPress={() => { onSwitch(t.id); setHistOpen(false); }} style={[st.histRow, t.id === thread.id && { backgroundColor: PANEL }]}>
                  <Text style={{ fontSize: 15 }}>{t.main ? '🏠' : '💬'}</Text>
                  <Text style={st.histT} numberOfLines={1}>{t.main ? 'Main' : (asText(t.title) || 'Chat')}</Text>
                </Pressable>
              ))}
              {!(threads || []).length && <Text style={st.noteT}>No other chats yet.</Text>}
            </ScrollView>
            <Pressable onPress={() => setHistOpen(false)} style={st.sheetBtn}><Text style={st.sheetBtnT}>Close</Text></Pressable>
          </View>
        </View>
      )}

      {liveFull && (
        <View style={st.fullScr}>
          <View style={[st.fullBar, { paddingTop: (ins?.top || 0) + 8 }]}>
            <Text style={st.fullT}>Live screen</Text>
            <View style={{ flex: 1 }} />
            <Pressable onPress={stop} hitSlop={8}><Text style={st.stop}>■ Stop bot</Text></Pressable>
            <Pressable onPress={closeLive} hitSlop={8}><Text style={st.fullX}>✕ Close</Text></Pressable>
          </View>
          {!!liveErr ? (
            <View style={st.fullErr}>
              <Text style={st.fullErrT}>{liveErr}</Text>
              {!!shotUri && <Image style={{ flex: 1, width: '100%' }} source={{ uri: shotUri }} resizeMode="contain" />}
            </View>
          ) : liveUrl ? (
            <WebView
              source={{ uri: liveUrl }}
              style={{ flex: 1, backgroundColor: '#000' }}
              javaScriptEnabled
              domStorageEnabled
              allowsInlineMediaPlayback
              setSupportMultipleWindows={false}
              onError={() => setLiveErr('The live view could not connect. Close and try again.')}
            />
          ) : (
            <View style={st.fullErr}>
              <ActivityIndicator color={MUTED} />
              <Text style={st.fullErrT}>{liveBusy ? 'Starting the desktop…' : 'Connecting…'}</Text>
              <Text style={st.fullErrS}>First start can take a few seconds.</Text>
            </View>
          )}
        </View>
      )}
    </KeyboardAvoidingView>
  );
}

function renderStreamAware(fn) {
  return ({ item }) => {
    if (item && item.stream != null) {
      try {
        return (
          <View style={st.rowIn}>
            <View style={st.answer}>
              {md(uiStreamText(item.stream) || '…', st.answerT)}
            </View>
          </View>
        );
      } catch (e) {
        note('render', e, 'stream');
        return (
          <View style={st.rowIn}>
            <Text style={st.noteT}>{asText(item.stream)}</Text>
          </View>
        );
      }
    }
    return fn({ item });
  };
}

function MsgFoot({ it, time, outgoing }) {
  const rx = asList(it && it.reactions);
  return (
    <View style={[st.foot, outgoing && { justifyContent: 'flex-end' }]}>
      {rx.map((r, i) => <Text key={i} style={st.rx}>{asText(r && r.emoji)}{r && r.count > 1 ? ' ' + r.count : ''}</Text>)}
      {!!time && <Text style={st.time}>{asText(time)}</Text>}
    </View>
  );
}

const st = StyleSheet.create({
  header: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 10, paddingBottom: 8, backgroundColor: BG },
  hBtn: { width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center' },
  hIcon: { color: TEXT, fontSize: 19 },
  hIconSm: { color: MUTED, fontSize: 17 },
  htitle: { color: TEXT, fontSize: 16, fontWeight: '700', textAlign: 'center' },
  hstatus: { color: MUTED, fontSize: 11.5, marginTop: 1, textAlign: 'center' },

  errBar: { backgroundColor: '#2a1414', borderTopWidth: 1, borderBottomWidth: 1, borderColor: '#3a1a1a', paddingHorizontal: 14, paddingVertical: 8 },
  errT: { color: '#f0a0a0', fontSize: 12.5 },

  list: { paddingHorizontal: 14, paddingTop: 8, gap: 2 },
  empty: { color: MUTED, fontSize: 15, textAlign: 'center', marginTop: 60 },

  rowOut: { alignItems: 'flex-end', marginTop: 10 },
  bubbleOut: { backgroundColor: '#005c4b', borderRadius: 14, borderTopRightRadius: 14, borderBottomRightRadius: 4, paddingHorizontal: 14, paddingVertical: 9, maxWidth: '84%' },
  bubbleOutT: { color: TEXT, fontSize: 15.5, lineHeight: 22 },

  rowIn: { marginTop: 14, paddingHorizontal: 2 },
  who: { color: MUTED, fontSize: 12, fontWeight: '700', marginBottom: 4, letterSpacing: 0.2 },
  answer: { marginTop: 1 },
  answerT: { color: TEXT, fontSize: 15.5, lineHeight: 23 },

  foot: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 4 },
  time: { color: MUTED, fontSize: 10.5 },
  rx: { color: MUTED, fontSize: 12 },

  typingWrap: { marginTop: 14, paddingHorizontal: 2 },
  typingT: { color: MUTED, fontSize: 11, letterSpacing: 3 },

  toolWrap: { alignSelf: 'center', flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: PANEL, borderWidth: 1, borderColor: LINE, borderRadius: 999, paddingHorizontal: 11, paddingVertical: 4, marginVertical: 4, flexWrap: 'wrap' },
  toolIc: { fontSize: 11, width: 12, textAlign: 'center' },
  toolT: { color: MUTED, fontSize: 11.5, maxWidth: 270 },
  shot: { width: 110, height: 70, borderRadius: 8, borderWidth: 1, borderColor: LINE },

  cmdNote: { backgroundColor: PANEL, borderRadius: 12, padding: 10, alignSelf: 'center', marginVertical: 4 },
  noteT: { color: MUTED, fontSize: 12.5, lineHeight: 18, textAlign: 'center', marginVertical: 6 },

  dayWrap: { alignItems: 'center', paddingVertical: 10 },
  dayT: { color: MUTED, fontSize: 11, fontWeight: '600' },

  busyRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 16, paddingBottom: 2 },
  stop: { color: '#e57373', fontWeight: '700', fontSize: 13 },

  replyBar: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 14, paddingVertical: 7, backgroundColor: PANEL, borderTopWidth: 1, borderColor: LINE },
  replyWho: { color: ACCENT, fontSize: 12, fontWeight: '700' },
  replyTxt: { color: MUTED, fontSize: 12.5 },
  replyX: { color: MUTED, fontSize: 15 },

  composerWrap: { paddingHorizontal: 10, paddingTop: 6, backgroundColor: BG },
  composer: { flexDirection: 'row', alignItems: 'flex-end', gap: 6, backgroundColor: '#1c1c1c', borderWidth: 1, borderColor: '#2a2a2a', borderRadius: 24, paddingLeft: 14, paddingRight: 5, paddingVertical: 5 },
  input: { flex: 1, color: TEXT, fontSize: 15.5, lineHeight: 21, paddingVertical: 9, maxHeight: 120 },
  sendBtn: { width: 38, height: 38, borderRadius: 19, backgroundColor: '#00a884', alignItems: 'center', justifyContent: 'center' },
  sendBtnOff: { backgroundColor: '#2a2a2a' },
  sendT: { color: '#ffffff', fontSize: 17, fontWeight: '800', lineHeight: 19, marginLeft: 1 },

  emojiPanel: { flexDirection: 'row', flexWrap: 'wrap', gap: 4, backgroundColor: BG, paddingHorizontal: 10, paddingTop: 8 },
  emojiB: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center', borderRadius: 10 },

  reasonBody: { maxHeight: 190, backgroundColor: PANEL, paddingHorizontal: 14, paddingVertical: 6, borderTopWidth: 1, borderBottomWidth: 1, borderColor: LINE },
  reasonRow: { flexDirection: 'row', gap: 8, alignItems: 'center', paddingVertical: 3 },
  reasonGlyph: { fontSize: 12, width: 14, textAlign: 'center' },
  reasonTxt: { color: TEXT, fontSize: 13, flex: 1 },
  reasonEmpty: { color: MUTED, fontSize: 12.5, paddingVertical: 4 },

  live: { backgroundColor: PANEL, borderBottomWidth: 1, borderColor: LINE },
  liveLbl: { color: '#9ad3b0', fontSize: 12.5, fontWeight: '600', paddingHorizontal: 14, paddingTop: 7, paddingBottom: 4 },
  liveImgSm: { width: '100%', height: 90 },

  appCard: { backgroundColor: '#1a1710', borderTopWidth: 1, borderColor: '#332a16' },
  appRow: { paddingHorizontal: 14, paddingVertical: 10, gap: 8 },
  appT: { color: '#e6d3a3', fontSize: 13, lineHeight: 18 },
  appBtns: { flexDirection: 'row', gap: 8 },
  appBtn: { backgroundColor: '#2a2415', borderRadius: 999, paddingHorizontal: 14, paddingVertical: 8 },
  appOk: { color: '#9ad3b0', fontSize: 13, fontWeight: '800' },
  appNo: { color: '#e57373', fontSize: 13, fontWeight: '800' },
  ansIn: { flex: 1, color: TEXT, fontSize: 14, borderWidth: 1, borderColor: '#3a3120', borderRadius: 12, paddingHorizontal: 10, paddingVertical: 8 },
  ansBtn: { backgroundColor: '#2a2415', borderRadius: 12, paddingHorizontal: 14, justifyContent: 'center' },
  ansBtnT: { color: '#e6d3a3', fontSize: 13, fontWeight: '800' },

  scrimM: { position: 'absolute', inset: 0, backgroundColor: 'rgba(0,0,0,.66)', alignItems: 'center', justifyContent: 'flex-end' },
  sheet: { width: '100%', backgroundColor: '#161616', borderTopLeftRadius: 22, borderTopRightRadius: 22, padding: 16, gap: 6, borderTopWidth: 1, borderColor: '#2a2a2a' },
  grabber: { alignSelf: 'center', width: 38, height: 4, borderRadius: 2, backgroundColor: '#333', marginBottom: 8 },
  sheetT: { color: TEXT, fontSize: 13, fontWeight: '700' },
  sheetSub: { color: MUTED, fontSize: 13, lineHeight: 18 },
  sheetBtn: { borderRadius: 14, padding: 13, alignItems: 'center', backgroundColor: '#222', marginTop: 6 },
  sheetBtnT: { color: TEXT, fontSize: 14, fontWeight: '700' },
  histIn: { backgroundColor: '#222', borderRadius: 999, color: TEXT, paddingHorizontal: 14, paddingVertical: 10, fontSize: 14, marginBottom: 8 },
  histRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 12, paddingHorizontal: 8, borderRadius: 10 },
  histT: { color: TEXT, fontSize: 14.5, flex: 1 },

  fullScr: { position: 'absolute', inset: 0, backgroundColor: '#000' },
  fullBar: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 14, paddingTop: 40, paddingBottom: 8, backgroundColor: BG, gap: 12 },
  fullT: { color: TEXT, fontSize: 15, fontWeight: '800' },
  fullX: { color: TEXT, fontSize: 15, fontWeight: '700' },
  fullErr: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 20, gap: 8 },
  fullErrT: { color: '#e57373', fontSize: 13.5, textAlign: 'center' },
  fullErrS: { color: MUTED, fontSize: 12 },
});
