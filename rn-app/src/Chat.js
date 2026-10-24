import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { View, Text, TextInput, Pressable, FlatList, StyleSheet, KeyboardAvoidingView, Platform, Image, ActivityIndicator, ScrollView } from 'react-native';
import Svg, { Defs, Pattern, Rect, Circle } from 'react-native-svg';
import { api, API_BASE } from './session';
import md from './md';
import { aiBody, uiPlain } from './widgets';
import { C } from './theme';

const IN_BG = '#202c33';     // incoming bubble
const OUT_BG = '#005c4b';    // outgoing bubble (WhatsApp dark green)
const WALL = '#0b141a';      // wallpaper base
const WA_GREEN = '#075E54';  // WhatsApp header green
const WA_GREEN_LIGHT = '#128C7E';
const FAINT = 'rgba(233,237,238,.55)';

function AnsRow({ onSend, placeholder }) {
  const [v, setV] = useState('');
  const go = () => { const t = v.trim(); if (t) { onSend(t); setV(''); } };
  return (
    <View style={{ flexDirection: 'row', gap: 8, marginTop: 6 }}>
      <TextInput
        value={v}
        onChangeText={setV}
        placeholder={placeholder}
        placeholderTextColor="#8696a0"
        style={{ flex: 1, color: '#e9edef', fontSize: 13, borderWidth: 1, borderColor: '#4d4122', borderRadius: 10, paddingHorizontal: 10, paddingVertical: 7 }}
        autoCapitalize="none"
        autoCorrect={false}
        returnKeyType="send"
        onSubmitEditing={go}
      />
      <Pressable onPress={go} style={({ pressed }) => [st.appBtn, pressed && { opacity: 0.85 }]}>
        <Text style={st.appOk}>Send</Text>
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

const TOOL_GLYPH = { running: '●', ok: '✓', error: '✕', denied: '⛔', blocked: '⛔' };

function Wallpaper() {
  return (
    <View pointerEvents="none" style={{ ...StyleSheet.absoluteFillObject, opacity: 0.06 }}>
      <Svg width="100%" height="100%">
        <Defs>
          <Pattern id="wa" width="120" height="120" patternUnits="userSpaceOnUse" patternTransform="rotate(12)">
            <Circle cx="20" cy="20" r="1.3" fill="#fff" />
            <Circle cx="60" cy="10" r="1" fill="#fff" />
            <Circle cx="90" cy="50" r="1.2" fill="#fff" />
            <Circle cx="40" cy="80" r="1" fill="#fff" />
            <Circle cx="10" cy="90" r="1.1" fill="#fff" />
          </Pattern>
        </Defs>
        <Rect width="100%" height="100%" fill="url(#wa)" />
      </Svg>
    </View>
  );
}

export default function Chat({ thread, threads, onSwitch, onBack, onEvent, onMenu }) {
  const ins = useSafeAreaInsets();
  const [items, setItems] = useState([]);
  const [apps, setApps] = useState([]);              // pending approvals on this thread
  const [streams, setStreams] = useState({});        // stream_id -> accumulated text
  const [busy, setBusy] = useState(null);            // {status, steps}
  const [replyTo, setReplyTo] = useState(null);
  const [pick, setPick] = useState(null);            // bubble long-pressed for react/reply
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(true);
  const [reasonOpen, setReasonOpen] = useState(false);
  const [liveFull, setLiveFull] = useState(false);
  const [histOpen, setHistOpen] = useState(false);
  const [histQ, setHistQ] = useState('');
  const listRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const d = await api('/api/threads/' + thread.id);
      setItems(d.items || []);
      setApps(d.approvals || []);
      setStreams({});
      setBusy(d.running && d.running.length ? { status: 'running' } : null);
    } catch (e) { /* offline */ }
    setLoading(false);
  }, [thread.id]);

  useEffect(() => { setItems([]); setLoading(true); setReplyTo(null); load(); }, [thread.id, load]);

  useEffect(() => {
    onEvent.current = (ev) => {
      if (ev.thread_id && ev.thread_id !== thread.id) return;
      if (ev.type === 'message' && Array.isArray(ev.items)) {
        setItems((prev) => {
          const known = new Set(prev.map((i) => i.id));
          return [...prev, ...ev.items.filter((i) => !known.has(i.id))];
        });
        setStreams({});
      } else if (ev.type === 'delta') {
        setStreams((s) => ({ ...s, [ev.stream_id]: (s[ev.stream_id] || '') + ev.text }));
      } else if (ev.type === 'delta_reset') {
        setStreams((s) => { const o = { ...s }; delete o[ev.stream_id]; return o; });
      } else if (ev.type === 'turn') {
        if (['done', 'stopped', 'error', 'limit', 'skipped'].includes(ev.status)) { setBusy(null); load(); }
        else setBusy({ status: ev.status, steps: ev.steps });
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
    setText(''); setReplyTo(null);
    try {
      await api('/api/threads/' + thread.id + '/messages', { method: 'POST', body: JSON.stringify({ text: snapshot, images: [], parent_id: pid }) });
    } catch (e) {
      // restore on failure so the user doesn't lose their message; guard against unmounted state
      setText(snapshot);
    }
  };
  const stop = async () => { try { await api('/api/threads/' + thread.id + '/stop', { method: 'POST', body: '{}' }); } catch (e) { /* already done */ } };
  const react = async (mid, emoji) => { try { await api('/api/messages/' + mid + '/reactions', { method: 'POST', body: JSON.stringify({ emoji, author: 'user' }) }); load(); } catch (e) { /* ignore */ } };
  const decide = async (a, approve, remember, answer) => {
    setApps((cur) => cur.filter((x) => x.id !== a.id));
    const body = { approve, remember };
    if (answer) body.answer = answer;
    try { await api('/api/approvals/' + a.id + '/decide', { method: 'POST', body: JSON.stringify(body) }); } catch (e) { /* ignore */ }
    load();
  };

  const renderItem = ({ item }) => {
    if (item.day) return <View style={st.dayWrap}><View style={st.dayChip}><Text style={st.dayT}>{item.label}</Text></View></View>;
    if (item.typing) return <View style={st.msg}><Text style={st.typingT}>● ● ●</Text></View>;
    if (item.type === 'user') return (
      <Pressable onLongPress={() => item.id && setPick(item)} delayLongPress={350} style={[st.msg, st.msgMe]}>
        <Text selectable style={st.msgTOut}>{String(item.text || '')}</Text>
        <MsgFoot it={item} time={fmtT(item)} outgoing />
        <View style={st.tailOut} />
      </Pressable>
    );
    if (item.type === 'assistant') return (
      <Pressable onLongPress={() => item.id && setPick(item)} delayLongPress={350} style={[st.msg]}>
        {!!item.name && !item.eq && <Text style={st.who}>{item.emoji || ''} {item.name}</Text>}
        {aiBody(String(item.text || ''), (t) => md(t, st.msgT), (r) => { setText(r); })}
        <MsgFoot it={item} time={fmtT(item)} />
        <View style={st.tailIn} />
      </Pressable>
    );
    if (item.type === 'tool') {
      const g = TOOL_GLYPH[item.status] || '•';
      const okk = item.status === 'ok' || item.status === 'running';
      return (
        <View style={st.toolWrap}>
          <Text style={[st.toolIc, { color: okk ? '#66d9a3' : '#f15c6d' }]}>{g}</Text>
          <Text style={st.toolT} numberOfLines={2}>{item.label || item.tool}</Text>
          {(item.images || []).map((n, i) => <Image key={i} style={st.shot}
            source={{ uri: API_BASE + '/api/files/shots/' + encodeURIComponent(n) }} />)}
        </View>
      );
    }
    if (item.type === 'notice') return item.level === 'cmd'
      ? <View style={st.cmdNote}>{md(item.text, st.noteT)}</View>
      : <Text style={st.noteT}>{item.text}</Text>;
    if (item.type === 'system_note') return <Text style={st.noteT}>{String(item.text || '').slice(0, 200)}</Text>;
    return null;
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
  const HEADER = (
    <View style={{ flex: 1 }}>
      <Text style={st.htitle} numberOfLines={1}>{(threads && threads.find(t=>t.id===thread.id)?.title) || 'ZerotheBot'}</Text>
      <Text style={st.hstatus} numberOfLines={1}>{!!busy ? (busy.status === 'waiting_approval' ? 'needs your approval • tap to respond' : 'typing…') : 'tap here for contact info'}</Text>
    </View>
  );
  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: WALL }} behavior={Platform.OS === 'ios' ? 'padding' : undefined} keyboardVerticalOffset={0}>
      {/* WhatsApp-style header */}
      <View style={[st.header, { paddingTop: (ins?.top || 0) + 6, backgroundColor: WA_GREEN }]}>
        <Pressable onPress={onBack} hitSlop={10} style={st.backBtn}><Text style={st.back}>‹</Text></Pressable>
        <View style={st.av}><Text style={{ fontSize: 16 }}>🤖</Text></View>
        {HEADER}
        <Pressable hitSlop={8} style={st.hIconBtn}><Text style={st.hIcon}>📞</Text></Pressable>
        <Pressable hitSlop={8} style={st.hIconBtn}><Text style={st.hIcon}>🎥</Text></Pressable>
        <Pressable onPress={() => setReasonOpen(!reasonOpen)} hitSlop={8}>
          <Text style={[st.hbtn, reasonOpen && st.hbtnOn]}>⋮</Text>
        </Pressable>
      </View>
      {/* header bottom strip like WhatsApp */}
      <View style={{ height: 1, backgroundColor: 'rgba(0,0,0,0.12)' }} />
      {reasonOpen && (
        <ScrollView style={st.reasonBody} nestedScrollEnabled>
          {acts.length ? [...acts].reverse().map((it, i) => (
            <View key={i} style={st.reasonRow}>
              <Text style={[st.reasonGlyph, { color: (TOOL_GLYPH[it.status] !== '✕' && it.status !== 'error' && it.status !== 'denied' && it.status !== 'blocked') ? C.ok : C.bad }]}>
                {TOOL_GLYPH[it.status] || '•'}</Text>
              <Text style={st.reasonTxt} numberOfLines={2}>{it.label || it.tool}</Text>
            </View>
          )) : <Text style={st.reasonEmpty}>No activity yet — the Bot's tool steps will stream here.</Text>}
        </ScrollView>
      )}
      {!!busy && busy.status !== 'waiting_approval' && lastAct && (
        <Pressable style={st.live} onPress={() => (shotUri ? setLiveFull(true) : null)}>
          <View style={st.liveHead}>
            <Text style={st.liveLbl} numberOfLines={1}>🖥 live on the screen{lastAct.label ? ' — ' + lastAct.label : ''}{shotUri ? '  ·  tap to watch' : ''}</Text>
          </View>
          {!!shotUri && <Image style={st.liveImgSm} source={{ uri: shotUri }} resizeMode="contain" />}
        </Pressable>
      )}
      <View style={{ flex: 1, backgroundColor: WALL }}>
        <Wallpaper />
      {loading ? <ActivityIndicator style={{ marginTop: 24 }} color={C.muted} /> : (
        <FlatList
          ref={listRef}
          data={[...disp, ...streamRows.map(([sid, t]) => ({ id: 's' + sid, stream: t })), ...((busy && busy.status !== 'waiting_approval' && !streamRows.length) ? [{ id: 'typing', typing: true }] : [])]}
          keyExtractor={(i) => String(i.id)}
          renderItem={renderStreamAware(renderItem)}
          contentContainerStyle={[st.list, { paddingBottom: 12 }]}
          keyboardShouldPersistTaps="handled"
          onContentSizeChange={() => listRef.current && listRef.current.scrollToEnd({ animated: false })}
          onLayout={() => listRef.current && listRef.current.scrollToEnd({ animated: false })}
        />
      )}
      </View>
      {!!busy && (
        <View style={st.busyRow}>
          <Text style={{ color: busy.status === 'waiting_approval' ? '#f0b232' : '#00a884' }}>{busy.status === 'waiting_approval' ? '⏸ waiting for your approval' : '● working…'}</Text>
          <Pressable onPress={stop} hitSlop={8}><Text style={st.stop}>Stop</Text></Pressable>
        </View>
      )}
      {!!apps.length && (
        <View style={st.appCard}>
          {apps.map((a, i) => {
            const isAsk = a.category === 'question' || a.tool === 'ask_code';
            return (
            <View key={a.id || i} style={st.appRow}>
              <Text style={st.appT}>{(a.category || '').toUpperCase()}{a.tool ? ' · ' + a.tool : ''}{a.summary ? '\n' + a.summary : ''}</Text>
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
          <Text numberOfLines={1} style={st.replyTxt}><Text style={{ fontWeight: '700' }}>{replyTo.type === 'user' ? 'You' : (replyTo.name || 'Bot')}</Text>{' '}{String(uiPlain(replyTo.text || '') || replyTo.text).slice(0, 80)}</Text>
          <Pressable onPress={() => setReplyTo(null)} hitSlop={8}><Text style={st.replyX}>✕</Text></Pressable>
        </View>
      )}
      {/* WhatsApp-style composer */}
      <View style={[st.inputRow, { paddingBottom: 8 + (ins?.bottom || 0), backgroundColor: '#111b21' }]}>
        <View style={st.inputWrapWA}>
          <Pressable hitSlop={8} style={st.inputIconBtn}><Text style={st.inputIcon}>😊</Text></Pressable>
          <TextInput
            style={st.inputWA}
            value={text}
            onChangeText={setText}
            placeholder="Message"
            placeholderTextColor="#8696a0"
            multiline
            returnKeyType="send"
            onSubmitEditing={send}
            blurOnSubmit={false}
          />
          <Pressable hitSlop={8} style={st.inputIconBtn}><Text style={st.inputIcon}>📎</Text></Pressable>
          {!!text.trim() ? null : <Pressable hitSlop={8} style={st.inputIconBtn}><Text style={st.inputIcon}>📷</Text></Pressable>}
        </View>
        <Pressable onPress={send} style={[st.sendBtn, !text.trim() && { backgroundColor: '#00a884', opacity: 0.95 }]} hitSlop={6}>
          <Text style={st.sendT}>{text.trim() ? '➤' : '🎤'}</Text>
        </Pressable>
      </View>

      {!!pick && (
        <View style={st.scrimM} onStartShouldSetResponder={() => true}>
          <View style={[st.sheet, { paddingBottom: 16 + (ins?.bottom || 0) }]}>
            <Text style={st.sheetT}>{pick.type === 'user' ? 'Your message' : (pick.name || 'Bot')}'s message</Text>
            <Text numberOfLines={3} style={st.sheetSub}>{String(pick.text || '').slice(0, 140)}</Text>
            <View style={{ flexDirection: 'row', gap: 12, marginTop: 10, flexWrap: 'wrap' }}>
              {REACTS.map((e) => (
                <Pressable key={e} onPress={() => { react(pick.id, e); setPick(null); }} hitSlop={4}>
                  <Text style={{ fontSize: 26 }}>{e}</Text>
                </Pressable>
              ))}
            </View>
            <Pressable onPress={() => { setReplyTo(pick); setPick(null); }} style={st.sheetBtn}><Text style={[st.sheetBtnT, { color: C.text }]}>↩  Reply to this</Text></Pressable>
            <Pressable onPress={() => setPick(null)} style={st.sheetBtn}><Text style={st.sheetBtnT}>Cancel</Text></Pressable>
          </View>
        </View>
      )}

      {histOpen && (
        <View style={st.scrimM}>
          <View style={[st.sheet, { paddingBottom: 16 + (ins?.bottom || 0) }]}>
            <TextInput style={st.histIn} value={histQ} onChangeText={setHistQ} placeholder="Search chats" placeholderTextColor="#8696a0" autoCorrect={false} />
            <ScrollView style={{ maxHeight: 320 }} nestedScrollEnabled>
              {(threads || []).filter((t) => !histQ.trim() || String(t.title || '').toLowerCase().includes(histQ.trim().toLowerCase())).map((t) => (
                <Pressable key={t.id} onPress={() => { onSwitch(t.id); setHistOpen(false); }} style={st.histRow}>
                  <Text style={{ fontSize: 15 }}>{t.main ? '🏠' : '💬'}</Text>
                  <Text style={st.histT} numberOfLines={1}>{t.main ? 'Main' : t.title || 'Chat'}</Text>
                </Pressable>
              ))}
              {!(threads || []).length && <Text style={st.noteT}>No other chats yet.</Text>}
            </ScrollView>
            <Pressable onPress={() => setHistOpen(false)} style={st.sheetBtn}><Text style={st.sheetBtnT}>Close</Text></Pressable>
          </View>
        </View>
      )}

      {liveFull && !!shotUri && (
        <View style={st.fullScr}>
          <View style={st.fullBar}>
            <Pressable onPress={() => setLiveFull(false)} hitSlop={8}><Text style={st.fullX}>✕ Close</Text></Pressable>
            <Pressable onPress={stop} hitSlop={8}><Text style={st.stop}>■ Stop bot</Text></Pressable>
          </View>
          <Image style={{ flex: 1 }} source={{ uri: shotUri }} resizeMode="contain" />
        </View>
      )}
    </KeyboardAvoidingView>
  );
}

function renderStreamAware(fn) {
  return ({ item }) => {
    if (item.stream != null) return (
      <View style={[st.msg, { opacity: .9 }]}>
        {md(uiStreamText(item.stream), st.msgT)}
      </View>
    );
    return fn({ item });
  };
}

function MsgFoot({ it, time, outgoing }) {
  const rx = it.reactions || [];
  const showTicks = !!outgoing;
  return (
    <View style={st.foot}>
      {rx.map((r, i) => <Text key={i} style={st.rx}>{r.emoji}{r.count > 1 ? ' ' + r.count : ''}</Text>)}
      <View style={st.gap} />
      {!!time && <Text style={st.time}>{time}</Text>}
      {!!showTicks && <Text style={st.ticks}>✓✓</Text>}
    </View>
  );
}

const st = StyleSheet.create({
  header: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 10, paddingTop: 8, paddingBottom: 8, borderBottomWidth: 1, borderColor: '#111b21' },
  back: { color: '#fff', fontSize: 28, fontWeight: '600', width: 26, textAlign: 'center' },
  backBtn: { width: 30, alignItems: 'center', justifyContent: 'center' },
  av: { width: 38, height: 38, borderRadius: 19, backgroundColor: '#2a3942', alignItems: 'center', justifyContent: 'center' },
  htitle: { color: '#fff', fontSize: 16, fontWeight: '700' },
  hstatus: { color: 'rgba(255,255,255,0.78)', fontSize: 12, marginTop: 1 },
  hIconBtn: { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center' },
  hIcon: { color: '#fff', fontSize: 16 },
  hbtn: { fontSize: 20, color: '#fff', opacity: 0.95, paddingHorizontal: 6 },
  hbtnOn: { opacity: 1 },
  chipRow: { backgroundColor: IN_BG, paddingBottom: 8, paddingTop: 2, borderBottomWidth: 1, borderColor: '#111b21' },
  chip: { color: '#8696a0', fontSize: 12.5, backgroundColor: '#2a3942', borderRadius: 999, paddingHorizontal: 12, paddingVertical: 5, overflow: 'hidden' },
  chipOn: { color: '#e9edef', backgroundColor: OUT_BG, fontWeight: '700' },
  list: { padding: 10, paddingBottom: 16, gap: 3 },
  msg: { alignSelf: 'flex-start', backgroundColor: IN_BG, borderRadius: 8, borderTopLeftRadius: 8, borderTopRightRadius: 8, borderBottomLeftRadius: 8, borderBottomRightRadius: 8, paddingVertical: 6, paddingHorizontal: 9, maxWidth: '78%', elevation: 1, overflow: 'visible' },
  msgMe: { alignSelf: 'flex-end', backgroundColor: OUT_BG },
  msgT: { color: C.text, fontSize: 15, lineHeight: 20 },
  msgTOut: { color: '#e9edef', fontSize: 15, lineHeight: 20 },
  who: { color: '#53bdeb', fontSize: 12.5, fontWeight: '700', marginBottom: 2 },
  foot: { flexDirection: 'row', alignItems: 'center', gap: 4, marginTop: 4 },
  gap: { flex: 1 },
  time: { color: FAINT, fontSize: 10.5 },
  ticks: { color: '#53bdeb', fontSize: 11, fontWeight: '700', marginLeft: 2 },
  tailIn: { position: 'absolute', left: -5, bottom: 0, width: 10, height: 10, backgroundColor: IN_BG, transform: [{ rotate: '45deg' }], borderBottomLeftRadius: 2 },
  tailOut: { position: 'absolute', right: -5, bottom: 0, width: 10, height: 10, backgroundColor: OUT_BG, transform: [{ rotate: '45deg' }], borderBottomRightRadius: 2 },
  rx: { color: '#8696a0', fontSize: 12 },
  rxBtn: { color: '#8696a0', fontSize: 13, paddingHorizontal: 2 },
  toolWrap: { alignSelf: 'center', flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: '#182229', borderRadius: 999, paddingHorizontal: 10, paddingVertical: 3, marginVertical: 1, flexWrap: 'wrap' },
  toolIc: { fontSize: 12, width: 12, textAlign: 'center' },
  toolT: { color: '#8696a0', fontSize: 11, maxWidth: 260 },
  shot: { width: 110, height: 70, borderRadius: 8, borderWidth: 1, borderColor: '#111b21' },
  cmdNote: { backgroundColor: IN_BG, borderRadius: 10, padding: 8, alignSelf: 'center' },
  noteT: { color: '#8696a0', fontSize: 12.5, lineHeight: 18, textAlign: 'center' },
  busyRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 14, paddingBottom: 4 },
  stop: { color: '#f15c6d', fontWeight: '700' },
  replyBar: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 14, paddingVertical: 5, backgroundColor: IN_BG, borderTopWidth: 1, borderColor: '#111b21' },
  replyTxt: { color: '#8696a0', fontSize: 12.5, flex: 1 },
  replyX: { color: '#8696a0', fontSize: 15 },
  inputRow: { flexDirection: 'row', alignItems: 'flex-end', gap: 8, padding: 6, paddingHorizontal: 8, backgroundColor: '#111b21' },
  inputWrap: { flex: 1, backgroundColor: IN_BG, borderRadius: 22, flexDirection: 'row', alignItems: 'flex-end' },
  inputWrapWA: { flex: 1, backgroundColor: '#202c33', borderRadius: 24, flexDirection: 'row', alignItems: 'flex-end', paddingHorizontal: 4, paddingVertical: 4, gap: 2 },
  input: { flex: 1, color: C.text, fontSize: 15.5, paddingHorizontal: 14, paddingTop: 10, paddingBottom: 8, maxHeight: 110 },
  inputWA: { flex: 1, color: '#e9edef', fontSize: 15.5, paddingHorizontal: 8, paddingTop: 8, paddingBottom: 8, maxHeight: 110, textAlignVertical: 'center' },
  inputIconBtn: { width: 34, height: 34, borderRadius: 17, alignItems: 'center', justifyContent: 'center' },
  inputIcon: { fontSize: 18, color: '#8696a0' },
  sendBtn: { width: 46, height: 46, borderRadius: 23, backgroundColor: '#00a884', alignItems: 'center', justifyContent: 'center', elevation: 2 },
  sendT: { color: '#fff', fontSize: 18, fontWeight: '700' },
  reasonBody: { maxHeight: 190, backgroundColor: WALL, paddingHorizontal: 14, paddingVertical: 6, borderBottomWidth: 1, borderColor: '#111b21' },
  reasonRow: { flexDirection: 'row', gap: 8, alignItems: 'center', paddingVertical: 2 },
  reasonGlyph: { fontSize: 12, width: 14, textAlign: 'center' },
  reasonTxt: { color: C.text, fontSize: 13, flex: 1 },
  reasonEmpty: { color: '#8696a0', fontSize: 12.5, paddingVertical: 4 },
  live: { backgroundColor: '#111b21', borderBottomWidth: 1, borderColor: '#111b21' },
  liveHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 14, paddingTop: 7, paddingBottom: 3 },
  liveLbl: { color: '#00a884', fontSize: 12.5, fontWeight: '600', flex: 1 },
  liveIcon: { color: '#8696a0', fontSize: 13, paddingLeft: 8 },
  liveImgSm: { width: '100%', height: 90 },
  dayWrap: { alignItems: 'center', paddingVertical: 5 },
  dayChip: { backgroundColor: '#182229', borderRadius: 999, paddingHorizontal: 10, paddingVertical: 3 },
  dayT: { color: '#8696a0', fontSize: 11, fontWeight: '700' },
  typingT: { color: '#8696a0', fontSize: 12, letterSpacing: 2 },
  appCard: { backgroundColor: '#2a2318', borderTopWidth: 1, borderColor: '#4d4122' },
  appRow: { paddingHorizontal: 14, paddingVertical: 8, gap: 8 },
  appT: { color: '#f0d999', fontSize: 13, lineHeight: 18 },
  appBtns: { flexDirection: 'row', gap: 8 },
  appBtn: { backgroundColor: '#3c3218', borderRadius: 999, paddingHorizontal: 12, paddingVertical: 7 },
  appOk: { color: '#7ee2a8', fontSize: 13, fontWeight: '800' },
  appNo: { color: '#f15c6d', fontSize: 13, fontWeight: '800' },
  scrimM: { position: 'absolute', inset: 0, backgroundColor: 'rgba(0,0,0,.6)', alignItems: 'center', justifyContent: 'flex-end' },
  sheet: { width: '100%', backgroundColor: '#233138', borderTopLeftRadius: 20, borderTopRightRadius: 20, padding: 16, gap: 6 },
  sheetT: { color: C.text, fontSize: 13, fontWeight: '700' },
  sheetSub: { color: '#8696a0', fontSize: 13, lineHeight: 18 },
  sheetBtn: { borderRadius: 12, padding: 12, alignItems: 'center', backgroundColor: '#2a3942', marginTop: 6 },
  sheetBtnT: { color: '#8696a0', fontSize: 14, fontWeight: '700' },
  histIn: { backgroundColor: '#2a3942', borderRadius: 999, color: C.text, paddingHorizontal: 14, paddingVertical: 9, fontSize: 14, marginBottom: 6 },
  histRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 10, borderBottomWidth: 1, borderColor: '#182229' },
  histT: { color: C.text, fontSize: 14.5, flex: 1 },
  fullScr: { position: 'absolute', inset: 0, backgroundColor: 'black' },
  fullBar: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 14, paddingTop: 40, paddingBottom: 8, backgroundColor: '#111b21' },
  fullX: { color: C.text, fontSize: 15, fontWeight: '700' },
});
