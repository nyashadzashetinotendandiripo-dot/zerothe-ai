import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, TextInput, Pressable, FlatList, StyleSheet, KeyboardAvoidingView, Platform, Image, ActivityIndicator } from 'react-native';
import { api, API_BASE } from './session';
import md from './md';
import { aiBody, uiPlain } from './widgets';
import { C } from './theme';

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
    if (item.type === 'user') return <Msg me><Text selectable style={[st.msgT]}>{item.text}</Text><MsgFoot it={item} react={react} setReplyTo={setReplyTo} /></Msg>;
    if (item.type === 'assistant') return (
      <Msg>
        {!!item.name && !item.eq && <Text style={st.who}>{item.emoji || ''} {item.name}</Text>}
        {aiBody(item.text, (t) => md(t, st.msgT), (r) => { setText(r); })}
        <MsgFoot it={item} react={react} setReplyTo={setReplyTo} />
      </Msg>
    );
    if (item.type === 'tool') {
      const g = TOOL_GLYPH[item.status] || '•';
      const colT = item.status === 'ok' || item.status === 'running' ? C.ok : C.bad;
      return (
        <View style={st.tool}>
          <Text style={[st.toolIc, { color: colT }]}>{g}</Text>
          <Text style={st.toolT} numberOfLines={2}>{item.label || item.tool}</Text>
          {(item.images || []).map((n, i) => <Image key={i} style={st.shot}
            source={{ uri: API_BASE + '/api/files/shots/' + encodeURIComponent(n) }} />)}
        </View>
      );
    }
    if (item.type === 'notice') return item.level === 'cmd'
      ? <View style={st.cmdNote}>{md(item.text, st.noteT)}</View>
      : <Text style={[st.noteT, { textAlign: 'center' }]}>{item.text}</Text>;
    if (item.type === 'system_note') return <Text style={[st.noteT, { textAlign: 'center' }]}>{String(item.text || '').slice(0, 200)}</Text>;
    return null;
  };

  const streamRows = Object.entries(streams).filter(([, t]) => t);
  const acts = items.filter((i) => i.type === 'tool');
  const lastAct = acts[acts.length - 1] || null;
  const shotItems = acts.filter((i) => (i.images || []).length);
  const lastShot = shotItems.length ? String(shotItems[shotItems.length - 1].images.slice(-1)[0]) : null;
  const shotUri = lastShot ? API_BASE + '/api/files/shots/' + encodeURIComponent(lastShot) : null;
  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: C.bg }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <View style={st.header}>
        <Pressable onPress={onBack} hitSlop={10}><Text style={st.back}>‹</Text></Pressable>
        <View style={{ flex: 1, flexDirection: 'row', gap: 8 }}>
          {(threads || []).map((t) => (
            <Pressable key={t.id} onPress={() => onSwitch(t.id)}>
              <Text style={[st.chip, t.id === thread.id && st.chipOn]} numberOfLines={1}>{t.main ? 'Main' : t.title || 'Chat'}</Text>
            </Pressable>
          ))}
        </View>
      </View>
      <Pressable style={st.reasonHead} onPress={() => setReasonOpen(!reasonOpen)}>
        <Text style={st.reasonT}>⚡ Reasoning <Text style={st.reasonN}>{acts.length}</Text></Text>
        <Text style={st.reasonIcon}>{reasonOpen ? '▾' : '▸'}</Text>
      </Pressable>
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
        <TextInput style={st.input} value={text} onChangeText={setText} placeholder="Message…" placeholderTextColor={C.muted}
          multiline />
        <Pressable onPress={send} hitSlop={6}><Text style={st.send}>➤</Text></Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}

// wrap renderItem: stream rows get a live bubble instead
function renderStreamAware(fn) {
  return ({ item }) => {
    if (item.stream != null) return (
      <View style={[st.msg, { alignSelf: 'flex-start', backgroundColor: C.panel, opacity: .85 }]}>
        {md(uiStreamText(item.stream), st.msgT)}
      </View>
    );
    return fn({ item });
  };
}

function Msg({ me, children }) {
  return <View style={[st.msg, me ? st.msgMe : st.msgBot]}>{children}</View>;
}

function MsgFoot({ it, react, setReplyTo }) {
  if (!it.id) return null;
  const rx = it.reactions || [];
  return (
    <View style={st.foot}>
      {rx.map((r, i) => <Text key={i} style={st.rx}>{r.emoji}{r.count > 1 ? ' ' + r.count : ''}</Text>)}
      <View style={st.gap} />
      {REACTS.map((e) => <Pressable key={e} onPress={() => react(it.id, e)} hitSlop={6}><Text style={st.rxBtn}>{e}</Text></Pressable>)}
      <Pressable onPress={() => setReplyTo(it)} hitSlop={6}><Text style={st.rxBtn}>↩</Text></Pressable>
    </View>
  );
}

const st = StyleSheet.create({
  header: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 12, paddingTop: 12, paddingBottom: 8, minHeight: 48 },
  back: { color: C.text, fontSize: 30, fontWeight: '600', width: 30 },
  chip: { color: C.muted, fontSize: 13, backgroundColor: C.panel2, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 6, maxWidth: 140, overflow: 'hidden' },
  chipOn: { color: C.onSolid, backgroundColor: C.solid, fontWeight: '700' },
  list: { padding: 12, paddingBottom: 24, gap: 8 },
  msg: { alignSelf: 'flex-start', backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 16, padding: 12, maxWidth: '88%' },
  msgMe: { alignSelf: 'flex-end', backgroundColor: '#16203a', borderColor: '#26365e' },
  msgT: { color: C.text, fontSize: 15, lineHeight: 21 },
  who: { color: C.muted, fontSize: 12, marginBottom: 4 },
  foot: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 6 },
  gap: { flex: 1 },
  rx: { color: C.muted, fontSize: 13 },
  rxBtn: { color: C.muted, fontSize: 14, paddingHorizontal: 2 },
  tool: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 8, paddingVertical: 4 },
  toolIc: { fontSize: 14, width: 16, textAlign: 'center' },
  toolT: { color: C.muted, fontSize: 13, flex: 1 },
  shot: { width: 110, height: 70, borderRadius: 8, borderWidth: 1, borderColor: C.line },
  cmdNote: { backgroundColor: C.panel2, borderRadius: 10, padding: 8, alignSelf: 'center' },
  noteT: { color: C.muted, fontSize: 13, lineHeight: 18 },
  busyRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 14, paddingBottom: 4 },
  stop: { color: C.bad, fontWeight: '700' },
  replyBar: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 14, paddingVertical: 4, backgroundColor: C.panel2 },
  replyTxt: { color: C.muted, fontSize: 12.5, flex: 1 },
  replyX: { color: C.muted, fontSize: 15 },
  inputRow: { flexDirection: 'row', alignItems: 'flex-end', gap: 8, padding: 10 },
  input: { flex: 1, backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 14, color: C.text, fontSize: 15, paddingHorizontal: 12, paddingTop: 10, paddingBottom: 6, maxHeight: 110 },
  send: { color: C.solid, fontSize: 22, paddingBottom: 6, paddingHorizontal: 6 },
  reasonHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 14, paddingVertical: 6, backgroundColor: C.panel2, borderTopWidth: 1, borderBottomWidth: 1, borderColor: C.line },
  reasonT: { color: C.text, fontSize: 13, fontWeight: '700' },
  reasonN: { color: C.muted, fontWeight: '400' },
  reasonIcon: { color: C.muted, fontSize: 14 },
  reasonBody: { maxHeight: 190, backgroundColor: C.bg, paddingHorizontal: 14, paddingVertical: 6, borderBottomWidth: 1, borderColor: C.line },
  reasonRow: { flexDirection: 'row', gap: 8, alignItems: 'center', paddingVertical: 2 },
  reasonGlyph: { fontSize: 12, width: 14, textAlign: 'center' },
  reasonTxt: { color: C.text, fontSize: 13, flex: 1 },
  reasonEmpty: { color: C.muted, fontSize: 12.5, paddingVertical: 4 },
  live: { backgroundColor: '#0b1120', borderBottomWidth: 1, borderColor: C.line },
  liveHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 14, paddingTop: 7, paddingBottom: 3 },
  liveLbl: { color: C.ok, fontSize: 12.5, fontWeight: '600', flex: 1 },
  liveIcon: { color: C.muted, fontSize: 13, paddingLeft: 8 },
  liveImg: { width: '100%', height: 320 },
  liveImgSm: { width: '100%', height: 180 },
});
