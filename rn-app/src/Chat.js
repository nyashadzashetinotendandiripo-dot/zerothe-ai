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
  const v = it && it.ts;
  const n = v == null ? NaN : Number(v);
  if (!isFinite(n) || n <= 0) return '';
  const d = new Date(n > 1e12 ? n : n * 1000);
  if (isNaN(d.getTime())) return '';
  return d.toTimeString().slice(0, 5);
}

const REACTS = ['👍', '❤️', '🎉', '😄', '🚀', '👀'];

const TOOL_GLYPH = { running: '●', ok: '✓', error: '✕', denied: '⛔', blocked: '⛔' };

export default function Chat({ thread, threads, onSwitch, onBack, onEvent }) {
  const [items, setItems] = useState([]);
  const [streams, setStreams] = useState({});        // stream_id -> accumulated text
  const [busy, setBusy] = useState(null);            // {status, steps}
  const [replyTo, setReplyTo] = useState(null);
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(true);
  const [reasonOpen, setReasonOpen] = useState(false);
  const [liveBig, setLiveBig] = useState(false);
  const listRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const d = await api('/api/threads/' + thread.id);
      setItems(d.items || []);
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

  const renderItem = ({ item }) => {
    if (item.type === 'user') return (
      <View style={[st.msg, st.msgMe]}>
        <Text selectable style={st.msgTOut}>{item.text}</Text>
        <MsgFoot it={item} react={react} setReplyTo={setReplyTo} time={fmtT(item)} />
      </View>
    );
    if (item.type === 'assistant') return (
      <View style={[st.msg]}>
        {!!item.name && !item.eq && <Text style={st.who}>{item.emoji || ''} {item.name}</Text>}
        {aiBody(item.text, (t) => md(t, st.msgT), (r) => { setText(r); })}
        <MsgFoot it={item} react={react} setReplyTo={setReplyTo} time={fmtT(item)} />
      </View>
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
  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: WALL }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <View style={st.header}>
        <Pressable onPress={onBack} hitSlop={10}><Text style={st.back}>‹</Text></Pressable>
        <View style={st.av}><Text style={{ fontSize: 16 }}>🤖</Text></View>
        <View style={{ flex: 1 }}>
          <Text style={st.htitle}>ZerotheBot</Text>
          <Text style={st.hstatus}>{!!busy ? (busy.status === 'waiting_approval' ? 'needs your approval' : 'typing…') : 'online'}</Text>
        </View>
        <Pressable onPress={() => setReasonOpen(!reasonOpen)} hitSlop={8}>
          <Text style={[st.hbtn, reasonOpen && st.hbtnOn]}>⚡{acts.length ? ' ' + acts.length : ''}</Text>
        </Pressable>
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 6, paddingHorizontal: 10 }} style={st.chipRow}>
        {(threads || []).map((t) => (
          <Pressable key={t.id} onPress={() => onSwitch(t.id)}>
            <Text style={[st.chip, t.id === thread.id && st.chipOn]} numberOfLines={1}>{t.main ? 'Main' : t.title || 'Chat'}</Text>
          </Pressable>
        ))}
      </ScrollView>
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
      {!!busy && lastAct && (
        <Pressable style={st.live} onPress={() => (shotUri ? setLiveBig(!liveBig) : null)}>
          <View style={st.liveHead}>
            <Text style={st.liveLbl} numberOfLines={1}>
              🖥 {busy.status === 'waiting_approval' ? 'needs your approval' : 'live on the screen'}{lastAct.label ? ' — ' + lastAct.label : ''}
            </Text>
            {!!shotUri && <Text style={st.liveIcon}>{liveBig ? '▾' : '▸'}</Text>}
          </View>
          {!!shotUri && (
            <Image style={liveBig ? st.liveImg : st.liveImgSm} source={{ uri: shotUri }} resizeMode="contain" />
          )}
        </Pressable>
      )}
      {loading ? <ActivityIndicator style={{ marginTop: 24 }} color={C.muted} /> : (
        <FlatList
          ref={listRef}
          data={[...items, ...streamRows.map(([sid, t]) => ({ id: 's' + sid, stream: t }))]}
          keyExtractor={(i) => String(i.id)}
          renderItem={renderStreamAware(renderItem)}
          contentContainerStyle={st.list}
          onContentSizeChange={() => listRef.current && listRef.current.scrollToEnd({ animated: false })}
        />
      )}
      {!!busy && (
        <View style={st.busyRow}>
          <Text style={{ color: C.ok }}>{busy.status === 'waiting_approval' ? 'needs you' : 'working…'}</Text>
          <Pressable onPress={stop} hitSlop={8}><Text style={st.stop}>Stop</Text></Pressable>
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

function MsgFoot({ it, react, setReplyTo, time }) {
  if (!it.id) return null;
  const rx = it.reactions || [];
  return (
    <View style={st.foot}>
      {rx.map((r, i) => <Text key={i} style={st.rx}>{r.emoji}{r.count > 1 ? ' ' + r.count : ''}</Text>)}
      <View style={st.gap} />
      {!!time && <Text style={st.time}>{time}</Text>}
      {REACTS.map((e) => <Pressable key={e} onPress={() => react(it.id, e)} hitSlop={6}><Text style={st.rxBtn}>{e}</Text></Pressable>)}
      <Pressable onPress={() => setReplyTo(it)} hitSlop={6}><Text style={st.rxBtn}>↩</Text></Pressable>
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
  liveImg: { width: '100%', height: 320 },
  liveImgSm: { width: '100%', height: 180 },
});
