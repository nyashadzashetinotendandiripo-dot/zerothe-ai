import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, TextInput, Pressable, FlatList, StyleSheet, KeyboardAvoidingView, Platform, Image, ActivityIndicator, ScrollView } from 'react-native';
import { api, API_BASE } from './session';
import md from './md';
import { aiBody, uiPlain } from './widgets';
import { C } from './theme';

const IN_BG = '#202c33';     // incoming bubble / chrome (WhatsApp dark)
const OUT_BG = '#005c4b';    // outgoing bubble (WhatsApp dark green)
const WALL = '#0b141a';      // wallpaper
const FAINT = 'rgba(233,237,238,.55)';

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

export default function Chat({ thread, threads, onSwitch, onBack, onEvent }) {
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
    setText(''); setReplyTo(null);
    try { await api('/api/threads/' + thread.id + '/messages', { method: 'POST', body: JSON.stringify({ text: t, images: [], parent_id: replyTo ? replyTo.id : 0 }) }); }
    catch (e) { setText(t); }
  };
  const stop = async () => { try { await api('/api/threads/' + thread.id + '/stop', { method: 'POST', body: '{}' }); } catch (e) { /* already done */ } };
  const react = async (mid, emoji) => { try { await api('/api/messages/' + mid + '/reactions', { method: 'POST', body: JSON.stringify({ emoji, author: 'user' }) }); load(); } catch (e) { /* ignore */ } };
  const decide = async (a, approve, remember) => {
    setApps((cur) => cur.filter((x) => x.id !== a.id));
    try { await api('/api/approvals/' + a.id + '/decide', { method: 'POST', body: JSON.stringify({ approve, remember }) }); } catch (e) { /* ignore */ }
    load();
  };

  const renderItem = ({ item }) => {
    if (item.day) return <View style={st.dayWrap}><View style={st.dayChip}><Text style={st.dayT}>{item.label}</Text></View></View>;
    if (item.typing) return <View style={st.msg}><Text style={st.typingT}>● ● ●</Text></View>;
    if (item.type === 'user') return (
      <Pressable onLongPress={() => item.id && setPick(item)} delayLongPress={350} style={[st.msg, st.msgMe]}>
        <Text selectable style={st.msgTOut}>{item.text}</Text>
        <MsgFoot it={item} time={fmtT(item)} />
      </Pressable>
    );
    if (item.type === 'assistant') return (
      <Pressable onLongPress={() => item.id && setPick(item)} delayLongPress={350} style={[st.msg]}>
        {!!item.name && !item.eq && <Text style={st.who}>{item.emoji || ''} {item.name}</Text>}
        {aiBody(item.text, (t) => md(t, st.msgT), (r) => { setText(r); })}
        <MsgFoot it={item} time={fmtT(item)} />
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
      <Text style={st.htitle}>ZerotheBot</Text>
      <Text style={st.hstatus}>{!!busy ? (busy.status === 'waiting_approval' ? 'needs your approval' : 'typing…') : 'online'}</Text>
    </View>
  );
  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: WALL }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <View style={st.header}>
        <Pressable onPress={onBack} hitSlop={10}><Text style={st.back}>‹</Text></Pressable>
        <View style={st.av}><Text style={{ fontSize: 16 }}>🤖</Text></View>
        {HEADER}
        <Pressable onPress={() => setReasonOpen(!reasonOpen)} hitSlop={8}>
          <Text style={[st.hbtn, reasonOpen && st.hbtnOn]}>⚡{acts.length ? ' ' + acts.length : ''}</Text>
        </Pressable>
        <Pressable onPress={() => { setHistQ(''); setHistOpen(true); }} hitSlop={8}>
          <Text style={st.hbtn}>🗂</Text>
        </Pressable>
      </View>
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
      {loading ? <ActivityIndicator style={{ marginTop: 24 }} color={C.muted} /> : (
        <FlatList
          ref={listRef}
          data={[...disp, ...streamRows.map(([sid, t]) => ({ id: 's' + sid, stream: t })), ...((busy && busy.status !== 'waiting_approval' && !streamRows.length) ? [{ id: 'typing', typing: true }] : [])]}
          keyExtractor={(i) => String(i.id)}
          renderItem={renderStreamAware(renderItem)}
          contentContainerStyle={st.list}
          onContentSizeChange={() => listRef.current && listRef.current.scrollToEnd({ animated: false })}
        />
      )}
      {!!busy && (
        <View style={st.busyRow}>
          <Text style={{ color: busy.status === 'waiting_approval' ? '#f0b232' : '#00a884' }}>{busy.status === 'waiting_approval' ? '⏸ waiting for your approval' : '● working…'}</Text>
          <Pressable onPress={stop} hitSlop={8}><Text style={st.stop}>Stop</Text></Pressable>
        </View>
      )}
      {!!apps.length && (
        <View style={st.appCard}>
          {apps.map((a, i) => (
            <View key={a.id || i} style={st.appRow}>
              <Text style={st.appT}>{(a.category || '').toUpperCase()}{a.tool ? ' · ' + a.tool : ''}{a.summary ? '\n' + a.summary : ''}</Text>
              <View style={st.appBtns}>
                <Pressable onPress={() => decide(a, true, true)} style={st.appBtn}><Text style={st.appOk}>✅ Always</Text></Pressable>
                <Pressable onPress={() => decide(a, true, false)} style={st.appBtn}><Text style={st.appOk}>Approve</Text></Pressable>
                <Pressable onPress={() => decide(a, false, false)} style={st.appBtn}><Text style={st.appNo}>Deny</Text></Pressable>
              </View>
            </View>
          ))}
        </View>
      )}
      {!!replyTo && (
        <View style={st.replyBar}>
          <Text numberOfLines={1} style={st.replyTxt}><Text style={{ fontWeight: '700' }}>{replyTo.type === 'user' ? 'You' : (replyTo.name || 'Bot')}</Text>{' '}{String(uiPlain(replyTo.text || '') || replyTo.text).slice(0, 80)}</Text>
          <Pressable onPress={() => setReplyTo(null)} hitSlop={8}><Text style={st.replyX}>✕</Text></Pressable>
        </View>
      )}
      <View style={st.inputRow}>
        <View style={st.inputWrap}>
          <TextInput style={st.input} value={text} onChangeText={setText} placeholder="Message" placeholderTextColor="#8696a0"
            multiline />
        </View>
        <Pressable onPress={send} style={st.sendBtn} hitSlop={6}><Text style={st.sendT}>➤</Text></Pressable>
      </View>

      {!!pick && (
        <View style={st.scrimM} onStartShouldSetResponder={() => true}>
          <View style={st.sheet}>
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
          <View style={st.sheet}>
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

function MsgFoot({ it, time }) {
  const rx = it.reactions || [];
  if (!rx.length && !time) return null;
  return (
    <View style={st.foot}>
      {rx.map((r, i) => <Text key={i} style={st.rx}>{r.emoji}{r.count > 1 ? ' ' + r.count : ''}</Text>)}
      <View style={st.gap} />
      {!!time && <Text style={st.time}>{time}</Text>}
    </View>
  );
}

const st = StyleSheet.create({
  header: { flexDirection: 'row', alignItems: 'center', gap: 10, backgroundColor: IN_BG, paddingHorizontal: 10, paddingTop: 8, paddingBottom: 8, borderBottomWidth: 1, borderColor: '#111b21' },
  back: { color: C.text, fontSize: 28, fontWeight: '600', width: 26 },
  av: { width: 38, height: 38, borderRadius: 19, backgroundColor: '#2a3942', alignItems: 'center', justifyContent: 'center' },
  htitle: { color: C.text, fontSize: 16, fontWeight: '700' },
  hstatus: { color: '#8696a0', fontSize: 12, marginTop: 1 },
  hbtn: { fontSize: 16, opacity: .7 },
  hbtnOn: { opacity: 1 },
  chipRow: { backgroundColor: IN_BG, paddingBottom: 8, paddingTop: 2, borderBottomWidth: 1, borderColor: '#111b21' },
  chip: { color: '#8696a0', fontSize: 12.5, backgroundColor: '#2a3942', borderRadius: 999, paddingHorizontal: 12, paddingVertical: 5, overflow: 'hidden' },
  chipOn: { color: '#e9edef', backgroundColor: OUT_BG, fontWeight: '700' },
  list: { padding: 10, paddingBottom: 16, gap: 4 },
  msg: { alignSelf: 'flex-start', backgroundColor: IN_BG, borderTopLeftRadius: 12, borderBottomLeftRadius: 12, borderTopRightRadius: 12, borderBottomRightRadius: 4, paddingVertical: 7, paddingHorizontal: 10, maxWidth: '84%', elevation: 1 },
  msgMe: { alignSelf: 'flex-end', backgroundColor: OUT_BG, borderTopLeftRadius: 12, borderBottomLeftRadius: 12, borderTopRightRadius: 12, borderBottomRightRadius: 4 },
  msgT: { color: C.text, fontSize: 15, lineHeight: 20 },
  msgTOut: { color: '#e9edef', fontSize: 15, lineHeight: 20 },
  who: { color: '#53bdeb', fontSize: 12.5, fontWeight: '700', marginBottom: 2 },
  foot: { flexDirection: 'row', alignItems: 'center', gap: 4, marginTop: 4 },
  gap: { flex: 1 },
  time: { color: FAINT, fontSize: 10 },
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
  inputRow: { flexDirection: 'row', alignItems: 'flex-end', gap: 8, padding: 8, backgroundColor: WALL },
  inputWrap: { flex: 1, backgroundColor: IN_BG, borderRadius: 22, flexDirection: 'row', alignItems: 'flex-end' },
  input: { flex: 1, color: C.text, fontSize: 15.5, paddingHorizontal: 14, paddingTop: 10, paddingBottom: 8, maxHeight: 110 },
  sendBtn: { width: 44, height: 44, borderRadius: 22, backgroundColor: '#00a884', alignItems: 'center', justifyContent: 'center' },
  sendT: { color: '#fff', fontSize: 17, fontWeight: '700' },
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
