import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { View, Text, TextInput, Pressable, FlatList, StyleSheet, KeyboardAvoidingView, Platform, Image, ActivityIndicator, ScrollView, Clipboard, Alert, Linking, Animated, Easing } from 'react-native';
import { WebView } from 'react-native-webview';
import { api, API_BASE, conf, agentMessages, threadExportText } from './session';
import md from './md';
import { aiBody, uiPlain, uiStreamText } from './widgets';
import { breadcrumb, note, noteOnce } from './diag';
import * as ImagePicker from 'expo-image-picker';
import * as DocumentPicker from 'expo-document-picker';
import * as FileSystem from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';

// Chat theme: light default, dark toggle (feature 9).
const C = {
  light: { BG: '#ffffff', PANEL: '#f2f2f2', BUBBLE: '#e7e7e7', TEXT: '#111111', MUTED: '#666666', LINE: '#cccccc', ACCENT: '#000000' },
  dark:  { BG: '#0a0a0a', PANEL: '#121212', BUBBLE: '#2b2b2b', TEXT: '#ececec', MUTED: '#8f8f8f', LINE: '#1f1f1f', ACCENT: '#ffffff' },
};
// Module constants must not reference the component's `theme` state from any
// place inline (module scope) — use the light palette here. Otherwise the
// bundle crashes at require time: ReferenceError: Property 'theme' doesn't exist.
const themeKey = 'light';
const t = (key) => C[themeKey][key];
const BG = t('BG');
const PANEL = t('PANEL');
const BUBBLE = t('BUBBLE');
const TEXT = t('TEXT');
const MUTED = t('MUTED');
const LINE = t('LINE');
const ACCENT = t('ACCENT');

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

function _fmtSize(n) {
  if (n == null) return '';
  for (const unit of ['B', 'KB', 'MB', 'GB']) {
    if (n < 1024) return unit === 'B' ? n + ' ' + unit : n.toFixed(2) + ' ' + unit;
    n /= 1024;
  }
  return n.toFixed(2) + ' TB';
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

// Replies should read as raw text: strip decorative rules, heading hashes and
// runaway blank lines that models add on their own. Keeps lists, links and code.
function plainText(src) {
  const clean = (part) => {
    let t = part;
    t = t.replace(/^[ \t]*([-*_])\1{2,}[ \t]*$/gm, '');        // ---, ***, ___ rules
    t = t.replace(/^[ \t]{0,3}#{1,6}[ \t]*/gm, '');            // ### heading hashes
    t = t.replace(/^[ \t]*>[ \t]?/gm, '');                     // block quotes
    t = t.replace(/[ \t]+$/gm, '');                            // trailing spaces
    t = t.replace(/\n{3,}/g, '\n\n');                          // extra blank lines
    return t;
  };
  const parts = String(src == null ? '' : src).split(/```/);
  // Odd segments are fenced code — leave them byte-for-byte alone.
  return parts.map((p, i) => (i % 2 ? '```' + p + '```' : clean(p))).join('').trim();
}

// Living backdrop: two slow aurora glows over near-black. Cheap (native driver),
// non-interactive, and sits behind the message list so the chat feels alive.
function ChatBackdrop() {
  const a = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    const loop = Animated.loop(Animated.sequence([
      Animated.timing(a, { toValue: 1, duration: 11000, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
      Animated.timing(a, { toValue: 0, duration: 11000, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
    ]));
    loop.start();
    return () => loop.stop();
  }, [a]);
  const teal = a.interpolate({ inputRange: [0, 1], outputRange: [0.10, 0.22] });
  const indigo = a.interpolate({ inputRange: [0, 1], outputRange: [0.16, 0.07] });
  return (
    <View pointerEvents="none" style={StyleSheet.absoluteFill}>
      <Animated.View style={{
        position: 'absolute', top: -140, left: -120, width: 380, height: 380, borderRadius: 190,
        backgroundColor: '#00a884', opacity: teal,
        transform: [{ scale: a.interpolate({ inputRange: [0, 1], outputRange: [0.9, 1.15] }) }],
      }} />
      <Animated.View style={{
        position: 'absolute', bottom: 40, right: -150, width: 420, height: 420, borderRadius: 210,
        backgroundColor: '#5b7cfa', opacity: indigo,
        transform: [{ scale: a.interpolate({ inputRange: [0, 1], outputRange: [1.1, 0.92] }) }],
      }} />
      <View style={{ position: 'absolute', top: '34%', right: -80, width: 220, height: 220, borderRadius: 110, backgroundColor: '#00d4a0', opacity: 0.045 }} />
    </View>
  );
}

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

function ChatScreen({ thread, threads, botName: propsBotName, onSwitch, onBack, onEvent, onMenu, modalOpenRef, onNewChat, onThreadsRefresh }) {
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
  const [liveInteractive, setLiveInteractive] = useState(false);   // interactive takeover mode
  const [typeText, setTypeText] = useState('');                    // text posted to the live desktop
  const [typeBusy, setTypeBusy] = useState(false);
  const [zoom, setZoom] = useState(1);                             // live desktop fit scale
  // Real bot-browser takeover: screen.jpg polling + /screen/input taps.
  // The phone streams the machine's X desktop (VNC) by default; this instead shows
  // the Bot's own browser tab and lets the user tap/click inside it.
  const [browserOpen, setBrowserOpen] = useState(false);           // full-screen browser view visible
  const [controlling, setControlling] = useState(false);           // user is driving (takeover started)
  const [browserShot, setBrowserShot] = useState(null);            // screen.jpg uri
  const [browserW, setBrowserW] = useState(1280);                  // real screenshot width
  const [browserH, setBrowserH] = useState(800);                   // real screenshot height
  const [browserErr, setBrowserErr] = useState('');
  const browserRef = useRef(null);                                 // the <Image> layout ref
  const browserPoll = useRef(null);                                // interval id
  const [attachOpen, setAttachOpen] = useState(false);              // photo/file chooser sheet
  const [filePrev, setFilePrev] = useState(null);                   // {name, path, mime, url} for side preview
  const [histOpen, setHistOpen] = useState(false);
  const [histQ, setHistQ] = useState('');
  const [renId, setRenId] = useState(null);          // thread being renamed in the drawer
  const [renVal, setRenVal] = useState('');
  const [expBusy, setExpBusy] = useState(false);     // export/share in flight
  const [err, setErr] = useState('');
  const [attachments, setAttachments] = useState([]);
  const listRef = useRef(null);
  const liveRef = useRef(null);
  const fastRef = useRef(0);            // keep fast polling for a while after sending
  const busyRef = useRef(null);
  busyRef.current = busy;
  const atBottom = useRef(true);        // only auto-scroll when the user is already at the bottom
  // ---- new frontend feature state ----
  const [theme, setTheme] = useState('light');   // 'light' | 'dark'
  const [online, setOnline] = useState(true);    // SSE / poll connection state
  const [notifCount, setNotifCount] = useState(0); // unread notification count
  const [chatSearch, setChatSearch] = useState(''); // filter messages in this thread
  const [searchOpen, setSearchOpen] = useState(false); // shows/hides the search bar
  const [schedTime, setSchedTime] = useState(null); // scheduled send epoch ms
  const [proactiveMsgs, setProactiveMsgs] = useState([]); // agent-initiated messages
  const [proactiveLoaded, setProactiveLoaded] = useState(false);
  // Tell the hardware back button to close open sheets/modals instead of quitting.
  useEffect(() => {
    const open = !!histOpen || !!pick || !!attachOpen || liveFull || browserOpen;
    if (modalOpenRef) modalOpenRef.current = open;
  }, [histOpen, pick, attachOpen, liveFull, browserOpen, modalOpenRef]);
  useEffect(() => {
    return () => {
      if (browserPoll.current) { clearInterval(browserPoll.current); browserPoll.current = null; }
    };
  }, []);
  // scrollToEnd can throw when the list has no rows yet (or is mid-measure), and
  // that throw happens in a native callback where React cannot catch it.
  const scrollEnd = useCallback(() => {
    if (atBottom.current) {
      try { if (listRef.current) listRef.current.scrollToEnd({ animated: false }); } catch (e) { /* not laid out yet */ }
    }
  }, []);
  const onScroll = useCallback((e) => {
    // Decide whether the user is still at the bottom: if the visible window
    // reaches within 40 px of the content end, new messages can autoscroll.
    const layout = e.nativeEvent.layoutMeasurement;
    const content = e.nativeEvent.contentSize;
    const offset = e.nativeEvent.contentOffset.y;
    const bottom = content.height - layout.height - offset;
    atBottom.current = bottom <= 40;
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
      const d = await api('/api/threads/' + thread.id, { timeout: 15000 });
      apply(d, !!quiet);
    } catch (e) {
      // Keep the screen usable even if the first load is slow — polling will
      // retry automatically, so "stuck on loading" never happens.
      noteOnce('load', 'net', e, 'thread ' + thread.id);
      if (!quiet) setErr(String(e.message || e));
    }
    setLoading(false);
  }, [thread.id, apply]);

  // Show cached items immediately if available (from SSE delta), then load fresh.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      // First, try a fast load with a short timeout so the screen isn't blank.
      try {
        const d = await api('/api/threads/' + thread.id, { timeout: 8000 });
        if (!cancelled) apply(d, true);
      } catch (e) {
        // Fall through to the normal load below.
      }
      if (!cancelled) loadWithConn(false);
    })();
    return () => { cancelled = true; };
  }, [thread.id, load, apply]);

  // Load agent-initiated proactive messages for this chat.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (proactiveLoaded) return;
      try {
        const d = await agentMessages();
        if (!cancelled) {
          setProactiveMsgs(d.messages || []);
          setProactiveLoaded(true);
        }
      } catch (e) {
        // Proactive messages are optional; don't block the chat.
      }
    })();
    return () => { cancelled = true; };
  }, [proactiveLoaded]);

  // Merge agent-initiated proactive messages into the chat items for display.
  useEffect(() => {
    if (!proactiveLoaded || !proactiveMsgs.length) return;
    setItems((prev) => {
      const existing = new Set(prev.map((i) => i.id));
      const newRows = proactiveMsgs
        .filter((m) => !existing.has(m.id))
        .map((m) => ({
          id: m.id,
          author: 'agent',
          role: 'agent',
          kind: 'text',
          content: [ { type: 'text', text: m.content || m.text || '' } ],
          created_at: m.created_at || m.ts,
          thread_id: m.thread_id,
          bot_id: m.bot_id,
          pending: false,
        }));
      return [...prev, ...newRows];
    });
  }, [proactiveLoaded, proactiveMsgs]);

  // Safety net: the phone's SSE can be blocked by a proxy or killed by the OS, and
  // then the answer never appeared while the screen kept saying "working". Poll the
  // thread as well — every 1.5 s while a turn runs (or just after you send), then
  // back off to 15 s when idle: SSE already pushes live updates, so a fast idle
  // poll only burned battery and server time.
  useEffect(() => {
    let alive = true;
    let t = null;
    const tick = async () => {
      await loadWithConn(true);
      if (!alive) return;
      const fast = busyRef.current || Date.now() < fastRef.current;
      t = setTimeout(tick, fast ? 1500 : 15000);
    };
    t = setTimeout(tick, ((busyRef.current || Date.now() < fastRef.current) ? 1500 : 15000));
    return () => { alive = false; if (t) clearTimeout(t); };
  }, [load]);

  // Poll for unread notifications (approvals, errors, etc.) so the user sees a badge
  // even when the SSE stream is silent.
  useEffect(() => {
    let alive = true;
    let t = null;
    const tick = async () => {
      try {
        const d = await api('/api/notifications?unread_only=1', { timeout: 8000 });
        if (!alive) return;
        const n = Array.isArray(d) ? d.length : 0;
        setNotifCount(n);
      } catch (e) { /* ignore - notifications are a nice-to-have */ }
      if (!alive) return;
      t = setTimeout(tick, 10000);
    };
    t = setTimeout(tick, 10000);
    return () => { alive = false; if (t) clearTimeout(t); };
  }, []);

  // Track connection: if a poll fails, mark offline; on success, back online.
  const _origLoad = load;
  const loadWithConn = async (quiet) => {
    try {
      await _origLoad(quiet);
      if (!online) setOnline(true);
    } catch (e) {
      if (online) setOnline(false);
      throw e;
    }
  };

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
          if (['done', 'stopped', 'error', 'limit', 'skipped'].includes(ev.status)) { setBusy(null); loadWithConn(); }
          else setBusy({ status: ev.status, steps: ev.steps });
        }
      } catch (e) {
        note('event', e, 'chat event ' + asText(ev && ev.type));
      }
    };
    return () => { onEvent.current = null; };
  }, [thread.id, load]);

  const pickImage = async () => {
    try {
      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ['images', 'videos'],
        allowsMultipleSelection: true,
        maxResults: 5,
        quality: 0.7,
      });
      if (!result.canceled && result.assets) {
        const next = [];
        let failed = 0;
        for (const a of result.assets) {
          try {
            const b64 = await FileSystem.readAsStringAsync(a.uri, { encoding: FileSystem.EncodingType.Base64 });
            next.push({ data: b64, type: a.mimeType || 'image/jpeg', name: a.fileName || 'file' });
          } catch (e) { failed += 1; note('upload', e, 'read attachment'); }
        }
        if (next.length) setAttachments((prev) => [...prev, ...next]);
        if (failed) Alert.alert('Could not add file', failed + ' file(s) could not be read. Try a different file.');
      }
    } catch (e) {
      note('upload', e, 'image picker');
      Alert.alert('Could not open files', (e && e.message) || 'The file picker failed to open.');
    }
  };

  const pickFile = async () => {
    try {
      const result = await DocumentPicker.getDocumentAsync({
        multiple: true,
        copyToCacheDirectory: true,
        type: '*/*',
      });
      if (result.canceled || !result.assets) return;
      const next = [];
      let failed = 0;
      for (const a of result.assets) {
        try {
          const b64 = await FileSystem.readAsStringAsync(a.uri, { encoding: FileSystem.EncodingType.Base64 });
          next.push({ data: b64, type: a.mimeType || 'application/octet-stream', name: a.name || 'file' });
        } catch (e) { failed += 1; note('upload', e, 'read file'); }
      }
      if (next.length) setAttachments((prev) => [...prev, ...next]);
      if (failed) Alert.alert('Could not add file', failed + ' file(s) could not be read. Try a different file.');
    } catch (e) {
      note('upload', e, 'document picker');
      Alert.alert('Could not open files', (e && e.message) || 'The file picker failed to open.');
    }
  };

  const send = async () => {
    const t = text.trim();
    if (!t && !attachments.length) return;
    if (!thread?.id) return;
    // Feature 12: scheduled send — if a time is set and we haven't reached it,
    // stash the message and fire again at the scheduled time.
    if (schedTime && Date.now() < schedTime) {
      const tmp = 'tmp' + Date.now() + Math.random().toString(36).slice(2, 6);
      setItems((prev) => [...prev, { id: tmp, type: 'user', text: t, ts: Date.now() / 1000, pending: true, scheduled: true }]);
      const delay = schedTime - Date.now();
      setTimeout(() => {
        setItems((prev) => prev.filter((i) => i.id !== tmp));
        send();
      }, delay);
      setText(''); setAttachments([]); setSchedTime(null);
      return;
    }
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
      await api('/api/threads/' + thread.id + '/messages', { method: 'POST', timeout: 60000, body: JSON.stringify({ text: snapshot, images: attachments.map((a) => ({ data: a.data, type: a.type, name: a.name })), parent_id: pid }) });
      breadcrumb('send:ok', snapshot.slice(0, 40));
      await loadWithConn();
      setAttachments([]);                            // show it immediately even if SSE lags
    } catch (e) {
      breadcrumb('send:fail', String(e && e.message || e));
      note('send', e, 'thread ' + thread.id);
      setText(snapshot);                       // never lose the message
      setBusy(null);
      setErr(String(e.message || e));
      setItems((prev) => prev.filter((i) => i.id !== tmp));
    }
  };

  // Download URL for a workspace file (uses the same auth token as the API).
  const fileUrl = (p) => {
    const { base, token } = conf();
    return base + '/api/computer/file?path=' + encodeURIComponent(p) + '&download=1&token=' + encodeURIComponent(token || '');
  };

  // A file attachment row with an icon and a download button.
  const FileRow = ({ f }) => {
    const mime = (f && f.mime) || 'application/octet-stream';
    const isImage = mime.startsWith('image/');
    const kind = isImage ? 'image'
      : /\.pdf$/.test((f && f.name || '').toLowerCase()) ? 'pdf'
      : /\.docx$/.test((f && f.name || '').toLowerCase()) ? 'docx'
      : /\.(txt|md|csv|json|log|py|xml|html|yaml|yml)$/.test((f && f.name || '').toLowerCase()) || mime.startsWith('text/') ? 'text'
      : 'binary';
    const icon = kind === 'image' ? '🖼' : kind === 'pdf' ? '📕' : kind === 'docx' ? '📘' : kind === 'text' ? '📄' : '📦';
    return (
      <Pressable onPress={() => {
        const { base, token } = conf();
        setFilePrev({
          name: f && f.name,
          path: f && f.path,
          mime,
          kind,
          url: base + '/api/computer/file?path=' + encodeURIComponent(f && f.path) + '&download=1&token=' + encodeURIComponent(token || ''),
        });
        setAttachOpen(false);
      }} style={st.fileRow}>
        <View style={st.fileIc}>{icon}</View>
        <View style={{ flex: 1 }}>
          <Text style={st.fileName} numberOfLines={1}>{asText(f && f.name) || 'file'}</Text>
          <Text style={st.fileSize}>{f && f.size ? _fmtSize(f.size) : ''}</Text>
        </View>
        <Pressable onPress={(e) => { e.stopPropagation(); Linking.openURL(fileUrl(f && f.path)).catch(() => {}) }} style={st.fileBtn}>
          <Text style={st.fileBtnT}>↓</Text>
        </Pressable>
      </Pressable>
    );
  };

  const stop = async () => {
    try { await api('/api/threads/' + thread.id + '/stop', { method: 'POST', body: '{}' }); } catch (e) { /* already done */ }
    setTimeout(() => loadWithConn(), 600);
  };

  // ---- history drawer: rename + export + new chat -------------------------
  const startRename = (t) => { setRenId(t.id); setRenVal(asText(t.title) || ''); };
  const saveRename = async () => {
    const title = renVal.trim();
    if (renId && title) {
      try {
        await api('/api/threads/' + renId, { method: 'PUT', body: JSON.stringify({ title }) });
        if (onThreadsRefresh) await onThreadsRefresh();
      } catch (e) { Alert.alert('Could not rename', String(e.message || e)); }
    }
    setRenId(null); setRenVal('');
  };
  // Export the whole conversation as a markdown file and hand it to the
  // Android share sheet (save to Drive, mail it, and so on).
  const exportChat = async () => {
    if (expBusy) return;
    setExpBusy(true);
    try {
      const mdText = await threadExportText(undefined, thread.id);
      const safe = (asText(thread.title) || 'chat').replace(/[^\w\-]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40) || 'chat';
      const file = (FileSystem.cacheDirectory || '') + safe + '.md';
      await FileSystem.writeAsStringAsync(file, mdText, { encoding: FileSystem.EncodingType.UTF8 });
      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(file, { mimeType: 'text/markdown', dialogTitle: 'Share chat export' });
      } else {
        Alert.alert('Exported', 'Saved as ' + safe + '.md in the app cache.');
      }
    } catch (e) { Alert.alert('Could not export', String(e.message || e)); }
    setExpBusy(false);
  };

  // A real live view of the machine's desktop (Instabox VNC link in a WebView).
  // `control` opens it with keyboard/mouse control so the user can log in, solve
  // a CAPTCHA or finish a step themselves.
  const startLive = async (control) => {
    setLiveFull(true); setLiveErr(''); setLiveBusy(true);
    if (control) setLiveInteractive(true);
    try {
      if (liveUrl) { try { await api('/api/host/desktop', { method: 'DELETE' }); } catch (e) { /* already expired */ } }
      setLiveUrl('');
      const d = await api('/api/host/desktop', { method: 'POST', body: JSON.stringify({ viewOnly: !control, ttl: 3600 }) });
      if (!d || !d.url) throw new Error('The server did not return a live link.');
      setLiveUrl(d.url);
      setLiveInteractive(!!control);
    } catch (e) {
      setLiveErr(String(e.message || e));
    }
    setLiveBusy(false);
  };
  const openLive = () => startLive(false);
  const openInteractive = () => startLive(true);
  const closeLive = () => {
    setLiveFull(false); setLiveUrl(''); setLiveInteractive(false); setZoom(1);
    api('/api/host/desktop', { method: 'DELETE' }).catch(() => {});
  };
  // ---------------------------------------------------------------- real bot-browser takeover
  // Uses the proven /screen.jpg + /screen/input + /takeover pipeline (same as the desktop app)
  // instead of the machine's X-desktop VNC stream, so taps land inside the Bot's browser tab.
  const browserUrl = () => {
    const { base, token } = conf();
    return base + '/api/bots/' + encodeURIComponent(thread && (thread.botId || thread.bot_id || '') || '') + '/screen.jpg?q=55&token=' + encodeURIComponent(token || '') + '&t=' + Date.now();
  };
  const fetchBrowserShot = async () => {
    try {
      const u = browserUrl();
      setBrowserShot(u);
      const { width, height } = await new Promise((res, rej) =>
        Image.getSize(u, (w, h) => res({ width: w, height: h }), rej));
      setBrowserW(width || 1280);
      setBrowserH(height || 800);
      setBrowserErr('');
    } catch (e) { setBrowserErr(String(e.message || e)); }
  };
  const startBrowserScreen = async () => {
    setBrowserErr('');
    setControlling(true);
    try {
      await api('/api/bots/' + encodeURIComponent(thread && (thread.botId || thread.bot_id || '')) + '/takeover', {
        method: 'POST', body: JSON.stringify({ action: 'start' })
      });
    } catch (e) {
      setControlling(false); setBrowserErr(String(e.message || e)); return;
    }
    await fetchBrowserShot();
    setBrowserOpen(true);
    browserPoll.current = setInterval(fetchBrowserShot, 700);
  };
  const stopBrowserScreen = async () => {
    setBrowserOpen(false);
    if (browserPoll.current) { clearInterval(browserPoll.current); browserPoll.current = null; }
    try {
      await api('/api/bots/' + encodeURIComponent(thread && (thread.botId || thread.bot_id || '')) + '/takeover', {
        method: 'POST', body: JSON.stringify({ action: 'handback' })
      });
    } catch (e) { /* best effort */ }
    setControlling(false); setBrowserShot(null); setBrowserErr('');
  };
  const onBrowserTap = (e) => {
    if (!browserRef.current || !browserShot) return;
    const l = browserRef.current;
    const sc = Math.min(l.width / browserW, l.height / browserH);
    const offX = (l.width - browserW * sc) / 2, offY = (l.height - browserH * sc) / 2;
    const x = Math.round((e.nativeEvent.locationX - offX) / sc);
    const y = Math.round((e.nativeEvent.locationY - offY) / sc);
    if (x < 0 || y < 0 || x >= browserW || y >= browserH) return;
    api('/api/bots/' + encodeURIComponent(thread && (thread.botId || thread.bot_id || '')) + '/screen/input', {
      method: 'POST', body: JSON.stringify({ type: 'click', x, y, button: 'left', count: 1 })
    }).catch(() => {});
  };
  const onBrowserScroll = (g) => {
    api('/api/bots/' + encodeURIComponent(thread && (thread.botId || thread.bot_id || '')) + '/screen/input', {
      method: 'POST', body: JSON.stringify({ type: 'scroll', x: 640, y: 400, dx: 0, dy: g && (g.translationY || 0) * 2 })
    }).catch(() => {});
  };

  // The VNC canvas swallows the phone keyboard, so text and special keys are
  // posted to the server and typed with xdotool into the focused window.
  const desktopType = async (enter) => {
    const t = typeText;
    if (!t && !enter) return;
    setTypeBusy(true);
    try {
      await api('/api/host/type', { method: 'POST', body: JSON.stringify({ text: t, enter: !!enter }) });
      setTypeText('');
    } catch (e) { setLiveErr(String(e.message || e)); }
    setTypeBusy(false);
  };
  const desktopKey = async (key) => {
    try { await api('/api/host/key', { method: 'POST', body: JSON.stringify({ key }) }); }
    catch (e) { setLiveErr(String(e.message || e)); }
  };
  // Fit the whole desktop on the phone screen (the machine's display is 1280x800).
  const applyZoom = (z) => {
    setZoom(z);
    try {
      if (liveRef.current) {
        // CSS zoom (not transform) so the VNC client's own click/pointer mapping
        // stays valid at reduced zoom — transform:scale() makes taps land wrong.
        liveRef.current.injectJavaScript(
          "(function(){var b=document.body;if(!b)return;b.style.transform='';b.style.transformOrigin='';"
          + "b.style.width='';b.style.height='';b.style.zoom=" + z + ";})();true;");
      }
    } catch (e) { /* view not ready yet */ }
  };
  const react = async (mid, emoji) => { try { await api('/api/messages/' + mid + '/reactions', { method: 'POST', body: JSON.stringify({ emoji, author: 'user' }) }); loadWithConn(); } catch (e) { /* ignore */ } };
  const decide = async (a, approve, remember, answer) => {
    setApps((cur) => cur.filter((x) => x.id !== a.id));
    const body = { approve, remember };
    if (answer) body.answer = answer;
    try { await api('/api/approvals/' + a.id + '/decide', { method: 'POST', body: JSON.stringify(body) }); } catch (e) { /* ignore */ }
    loadWithConn();
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
        <View style={[st.answer, { backgroundColor: BUBBLE, borderRadius: 14, borderTopLeftRadius: 14, borderTopRightRadius: 4, borderBottomLeftRadius: 14, paddingHorizontal: 12, paddingVertical: 10, maxWidth: '92%' }]}>
          {aiBody(asText(item.text), (t) => md(plainText(t), st.answerT), (r) => { setText(r); })}
          {(item.files || []).length ? (
            <View style={{ marginTop: 6 }}>
              {(item.files || []).map((f, i) => <FileRow key={i} f={f} />)}
            </View>
          ) : null}
        </View>
        <MsgFoot it={item} time={fmtT(item)} />
      </Pressable>
    );
    if (item.type === 'tool') {
      // Compact tool row so the phone chat actually shows "working" per bot: what the bot
      // is doing right now, its status, and how long it took — Claude-style tool evidence.
      const dur = item.duration_ms ? ` · ${(item.duration_ms / 1000).toFixed(1)}s` : '';
      return (
        <View style={st.toolRow}>
          <Text style={st.toolName}>{asText(item.tool || item.label || 'tool')}</Text>
          <Text style={st.toolStatus}>{asText(item.status || item.summary || '')}{dur}</Text>
        </View>
      );
    }
    if (item.type === 'notice') return item.level === 'cmd'
      ? null
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
  const { base, token } = conf();
  const shotUri = lastShot ? API_BASE + '/api/files/shots/' + encodeURIComponent(lastShot) + '?token=' + encodeURIComponent(token || '') : null;
  const today = new Date().toDateString();
  const cs = chatSearch.trim().toLowerCase();
  const disp = [];
  { let lastDay = '';
    items.forEach((it, idx) => {
      // feature 7: search within chat — hide items that don't match
      if (cs && it.type !== 'day') {
        const haystack = (asText(it.text || '') + ' ' + (it.tool || '') + ' ' + (it.label || '')).toLowerCase();
        if (!haystack.includes(cs)) return;
      }
      const d2 = parseDate(it);
      const k = d2 ? d2.toDateString() : '';
      if (k && k !== lastDay) {
        disp.push({ id: 'day' + idx, day: true, label: k === today ? 'Today' : (k === new Date(Date.now() - 864e5).toDateString() ? 'Yesterday' : k.slice(0, 10)) });
        lastDay = k;
      }
      disp.push(it);
    }); }

  // The header shows the Bot the user opened, never the conversation title
  // (a thread can be called "hi", which is not a name).
  const botName = propsBotName || 'ZerotheBot';
  const curThread = (threads || []).find((t) => t.id === thread.id);
  const threadLabel = curThread && !curThread.main ? asText(curThread.title) : '';
  const isBusy = !!busy;
  const statusText = busy
    ? (busy.status === 'waiting_approval' ? 'needs your approval' : 'typing…')
    : (err ? 'offline — tap to retry' : (threadLabel ? 'online · ' + threadLabel : 'online'));
  const statusColor = busy
    ? (busy.status === 'waiting_approval' ? '#e0a53a' : '#0066cc')
    : (err ? '#b00020' : '#008000');

  // Offline banner (feature 4)
  const offlineBanner = !online && (
    <View style={st.offlineBanner}>
      <Text style={st.offlineText}>You're offline — messages will send when reconnected</Text>
    </View>
  );

  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: BG }} behavior="padding" keyboardVerticalOffset={10}>
      <ChatBackdrop />
      {offlineBanner}
      {/* WhatsApp-style top bar: menu · name/status (tap for chat history) */}
      <View style={[st.header, { paddingTop: (ins?.top || 0) + 6 }]}>
        <Pressable onPress={onMenu} hitSlop={10} style={st.hBtn}><Text style={st.hIcon}>☰</Text></Pressable>
        <Pressable onPress={() => Alert.alert('Delete conversation?', 'This permanently deletes this chat and all its messages.', [
          { text: 'Cancel', style: 'cancel' },
          { text: 'Delete', style: 'destructive', onPress: async () => {
            try { 
              await api('/api/threads/' + thread.id, { method: 'DELETE' }); 
              // Stay on the same bot: switch to its main thread instead of going back
              const mainThread = (threads || []).find((t) => t.main);
              if (mainThread) onSwitch(mainThread.id);
              else onBack();
            } catch (e) { Alert.alert('Could not delete', String(e.message || e)); }
          } },
        ])} hitSlop={10} style={st.hBtn}><Text style={st.hIcon}>🗑</Text></Pressable>
        <View style={st.headerCenter}>
          <Pressable style={{ flex: 1 }} onPress={() => { setHistQ(''); setHistOpen(true); }}>
            <Text style={st.htitle} numberOfLines={1}>{botName}</Text>
            <View style={[st.statusWrap, { backgroundColor: statusColor }]}>
              <Text style={st.statusText}>{isBusy ? '● ' : ''}{statusText}</Text>
            </View>
          </Pressable>
          {threads && threads.length > 1 && (
            <Pressable onPress={() => { setHistQ(''); setHistOpen(true); }} hitSlop={8} style={st.switchBtn}>
              <Text style={st.switchBtnT}>▾</Text>
            </Pressable>
          )}
          <Pressable onPress={() => setTheme(theme === 'light' ? 'dark' : 'light')} hitSlop={8} style={[st.themeBtn, theme === 'dark' && { backgroundColor: '#e7e7e7' }]}>
            <Text style={st.themeBtnT}>{theme === 'light' ? '🌙' : '☀️'}</Text>
          </Pressable>
          {notifCount > 0 && (
            <View style={st.notifBadge}>
              <Text style={st.notifBadgeT}>{notifCount > 9 ? '9+' : notifCount}</Text>
            </View>
          )}
        </View>
      </View>

      {/* Feature 7: search within chat */}
      {searchOpen ? (
        <View style={st.searchBar}>
          <TextInput
            style={st.searchInput}
            value={chatSearch}
            onChangeText={setChatSearch}
            placeholder="Search messages…"
            placeholderTextColor={MUTED}
            onSubmitEditing={() => {}}
            autoFocus
          />
          <Pressable onPress={() => { setSearchOpen(false); setChatSearch(''); }} style={st.searchClear}>
            <Text style={st.searchClearT}>✕</Text>
          </Pressable>
        </View>
      ) : (
        <Pressable onPress={() => { setSearchOpen(!searchOpen); if (searchOpen) setChatSearch(''); }} hitSlop={8} style={st.searchBtn}>
          <Text style={st.searchBtnT}>🔍</Text>
        </Pressable>
      )}

      {!!err && (
        <Pressable style={st.errBar} onPress={() => loadWithConn()}>
          <Text style={st.errT} numberOfLines={2}>{err} · tap to retry</Text>
        </Pressable>
      )}

      {!!busy && busy.status !== 'waiting_approval' && lastAct && (
        <Pressable style={st.live} onPress={openLive}>
          <Text style={st.liveLbl} numberOfLines={1}>🖥 {lastAct.label || 'Working on the screen'}  ·  tap to watch live</Text>
          {!!shotUri && <Image style={st.liveImgSm} source={{ uri: shotUri }} resizeMode="contain" />}
        </Pressable>
      )}

      <View style={{ flex: 1, backgroundColor: 'transparent' }}>
        {loading ? <ActivityIndicator style={{ marginTop: 28 }} color={MUTED} /> : (
          <FlatList
            ref={listRef}
            data={[...disp, ...streamRows.map(([sid, t]) => ({ id: 's' + sid, stream: t }))]}
            keyExtractor={keyOf}
            renderItem={renderStreamAware(renderItem)}
            contentContainerStyle={[st.list, { paddingBottom: 16 }]}
            keyboardShouldPersistTaps="handled"
            initialNumToRender={14}
            maxToRenderPerBatch={10}
            windowSize={9}
            removeClippedSubviews={Platform.OS === 'android'}
            onContentSizeChange={scrollEnd}
            onLayout={scrollEnd}
            onScroll={onScroll}
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
                      {(a.category === 'takeover' || a.category === 'login') && (
                        <Pressable onPress={() => { decide(a, true, false); startBrowserScreen(); }} style={[st.appBtn, { backgroundColor: '#005a9e', borderColor: '#007acc' }]}>
                          <Text style={st.appOk}>🌐 Open browser</Text>
                        </Pressable>
                      )}
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

      {/* WhatsApp-style composer: attach · input · round send */}
      <View style={[st.composerWrap, { paddingBottom: 8 + (ins?.bottom || 0) }]}>
        {!!attachments.length && (
          <View style={st.attachRow}>
            {attachments.map((a, i) => {
              const isImg = (a.type || '').startsWith('image/');
              return (
                <Pressable key={i} onPress={() => setAttachments((prev) => prev.filter((_x, j) => j !== i))} style={[st.attachChip, isImg && { paddingHorizontal: 0, paddingVertical: 0 }]}>
                  {isImg ? (
                    <Image source={{ uri: 'data:' + a.type + ';base64,' + a.data }} style={st.attachThumb} />
                  ) : null}
                  <Text numberOfLines={1} style={st.attachChipT}>📎 {asText(a.name).slice(0, 18)} ✕</Text>
                </Pressable>
              );
            })}
          </View>
        )}
        {/* Feature 12: scheduled send chip */}
        {schedTime && (
          <View style={st.schedChip}>
            <Text style={st.schedChipT}>⏰ Sends at {new Date(schedTime).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</Text>
            <Pressable onPress={() => setSchedTime(null)} style={st.schedClear}><Text style={st.schedClearT}>✕</Text></Pressable>
          </View>
        )}
        <View style={st.composer}>
          <Pressable onPress={() => setAttachOpen(true)} hitSlop={6} style={[st.uploadBtn, attachments.length > 0 && { backgroundColor: '#00a884' }]}>
            <Text style={st.uploadT}>📎{attachments.length > 0 ? ' ' + attachments.length : ''}</Text>
          </Pressable>
          <Pressable onPress={() => {
            const mins = prompt('Send in how many minutes? (e.g. 60)');
            const m = parseInt(mins || '0', 10);
            if (m && m > 0) setSchedTime(Date.now() + m * 60000);
          }} hitSlop={6} style={[st.uploadBtn, { backgroundColor: '#fff3cd', borderColor: '#e0a53a' }]}>
            <Text style={st.uploadT}>⏰</Text>
          </Pressable>
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
          <Pressable onPress={send} disabled={!text.trim() && !attachments.length} hitSlop={6}
            style={[st.sendBtn, !text.trim() && !attachments.length && st.sendBtnOff]}>
            <Text style={[st.sendT, !text.trim() && !attachments.length && { color: MUTED }]}>➤</Text>
          </Pressable>
        </View>
      </View>

      {attachOpen && (
        <View style={st.scrimM} onStartShouldSetResponder={() => true}>
          <View style={[st.sheet, { paddingBottom: 16 + (ins?.bottom || 0) }]}>
            <View style={st.grabber} />
            <Text style={st.sheetT}>Add to this chat</Text>
            <Pressable onPress={() => { setAttachOpen(false); pickImage(); }} style={st.sheetBtn}>
              <Text style={st.sheetBtnT}>🖼  Photos & videos</Text>
            </Pressable>
            <Pressable onPress={() => { setAttachOpen(false); pickFile(); }} style={st.sheetBtn}>
              <Text style={st.sheetBtnT}>📄  Files (PDF, docs, zip…)</Text>
            </Pressable>
            <Pressable onPress={() => setAttachOpen(false)} style={[st.sheetBtn, { backgroundColor: 'transparent' }]}>
              <Text style={[st.sheetBtnT, { color: MUTED }]}>Cancel</Text>
            </Pressable>
          </View>
        </View>
      )}

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
            <View style={{ marginTop: 10 }}>
              <Pressable onPress={() => { setReplyTo(pick); setPick(null); }} style={st.sheetBtn}><Text style={st.sheetBtnT}>↩  Reply</Text></Pressable>
              {pick.type === 'user' && (
                <>
                  <Pressable onPress={() => { setText(asText(pick.text)); setPick(null); }} style={st.sheetBtn}><Text style={st.sheetBtnT}>✏  Edit & resend</Text></Pressable>
                  <Pressable onPress={() => { Clipboard.setString(asText(pick.text)); setPick(null); }} style={st.sheetBtn}><Text style={st.sheetBtnT}>📋 Copy text</Text></Pressable>
                </>
              )}
            </View>
            <Pressable onPress={() => setPick(null)} style={[st.sheetBtn, { backgroundColor: 'transparent' }]}><Text style={[st.sheetBtnT, { color: MUTED }]}>Cancel</Text></Pressable>
          </View>
        </View>
      )}

      {histOpen && (
        <View style={st.scrimM}>
          <View style={[st.sheet, { paddingBottom: 16 + (ins?.bottom || 0) }]}>
            <View style={st.grabber} />
            <Pressable onPress={() => { setHistOpen(false); if (onNewChat) onNewChat(); }} style={st.sheetBtn}>
              <Text style={st.sheetBtnT}>＋ New chat</Text>
            </Pressable>
            <TextInput style={st.histIn} value={histQ} onChangeText={setHistQ} placeholder="Search chats" placeholderTextColor={MUTED} autoCorrect={false} />
            <ScrollView style={{ maxHeight: 320 }} nestedScrollEnabled>
              {(threads || []).filter((t) => !histQ.trim() || String(t.title || '').toLowerCase().includes(histQ.trim().toLowerCase())).map((t) => (
                <Pressable key={t.id} onPress={() => { onSwitch(t.id); setHistOpen(false); }} style={[st.histRow, t.id === thread.id && { backgroundColor: PANEL }]}>
                  <Text style={{ fontSize: 15 }}>{t.main ? '🏠' : '💬'}</Text>
                  <Text style={st.histT} numberOfLines={1}>{t.main ? 'Main' : (asText(t.title) || 'Chat')}</Text>
                  {!t.main && (
                    <Pressable hitSlop={8} onPress={() => startRename(t)}>
                      <Text style={{ color: MUTED, fontSize: 14 }}>✎</Text>
                    </Pressable>
                  )}
                </Pressable>
              ))}
              {!(threads || []).length && <Text style={st.noteT}>No other chats yet.</Text>}
            </ScrollView>
            {renId ? (
              <View style={{ flexDirection: 'row', gap: 8, marginTop: 8 }}>
                <TextInput style={[st.histIn, { flex: 1 }]} value={renVal} onChangeText={setRenVal} placeholder="Chat name"
                  placeholderTextColor={MUTED} autoFocus onSubmitEditing={saveRename} />
                <Pressable onPress={saveRename} style={st.sheetBtn}><Text style={st.sheetBtnT}>Save</Text></Pressable>
                <Pressable onPress={() => { setRenId(null); setRenVal(''); }} style={st.sheetBtn}><Text style={st.sheetBtnT}>✕</Text></Pressable>
              </View>
            ) : null}
            <Pressable onPress={exportChat} disabled={expBusy} style={st.sheetBtn}>
              <Text style={st.sheetBtnT}>{expBusy ? 'Exporting…' : '⇩ Export chat (.md)'}</Text>
            </Pressable>
            <Pressable onPress={() => setHistOpen(false)} style={st.sheetBtn}><Text style={st.sheetBtnT}>Close</Text></Pressable>
          </View>
        </View>
      )}

      {liveFull && (
        <View style={st.fullScr}>
          <View style={[st.fullBar, { paddingTop: (ins?.top || 0) + 8 }]}>
            <Text style={st.fullT}>{liveInteractive ? 'Desktop · in control' : 'Live desktop'}</Text>
            <View style={{ flex: 1 }} />
            <Pressable onPress={() => applyZoom(zoom === 1 ? 0.7 : zoom === 0.7 ? 0.5 : 1)} hitSlop={8} style={st.fullBtn}>
              <Text style={st.fullBtnT}>{zoom === 1 ? 'Fit' : Math.round(zoom * 100) + '%'}</Text>
            </Pressable>
            {!!liveInteractive ? (
              <Pressable onPress={closeLive} hitSlop={8} style={st.fullBtn}><Text style={st.fullBtnT}>✕ End</Text></Pressable>
            ) : (
              <Pressable onPress={openInteractive} hitSlop={8} disabled={liveBusy}
                style={[st.fullBtn, { backgroundColor: '#00a884', borderColor: '#00a884' }]}>
                <Text style={[st.fullBtnT, { color: '#fff' }]}>{liveBusy ? '…' : 'Control'}</Text>
              </Pressable>
            )}
            <Pressable onPress={stop} hitSlop={8}><Text style={st.stop}>■ Stop</Text></Pressable>
            <Pressable onPress={closeLive} hitSlop={8}><Text style={st.fullX}>✕</Text></Pressable>
          </View>
          {!!liveErr ? (
            <View style={st.fullErr}>
              <Text style={st.fullErrT}>{liveErr}</Text>
              <Pressable onPress={() => startLive(liveInteractive)} style={st.sheetBtn}><Text style={st.sheetBtnT}>Try again</Text></Pressable>
              {!!shotUri && <Image style={{ flex: 1, width: '100%' }} source={{ uri: shotUri }} resizeMode="contain" />}
            </View>
          ) : liveUrl ? (
            <WebView
              ref={liveRef}
              source={{ uri: liveUrl }}
              style={{ flex: 1, backgroundColor: '#000' }}
              javaScriptEnabled
              domStorageEnabled
              allowsInlineMediaPlayback
              mediaPlaybackRequiresUserAction={false}
              setSupportMultipleWindows={false}
              mixedContentMode="always"
              useWideViewPort
              loadWithOverviewMode
              scalesPageToFit
              originWhitelist={['*']}
              onLoadEnd={() => { if (zoom !== 1) applyZoom(zoom); }}
              onError={() => setLiveErr('The live view could not connect. Tap Try again.')}
            />
          ) : (
            <View style={st.fullErr}>
              <ActivityIndicator color={MUTED} />
              <Text style={st.fullErrT}>{liveBusy ? 'Starting the desktop…' : 'Connecting…'}</Text>
              <Text style={st.fullErrS}>{liveInteractive ? 'Interactive control starting…' : 'First start can take a few seconds.'}</Text>
            </View>
          )}
          {!!liveInteractive && (
            <View style={[st.typeBar, { paddingBottom: 8 + (ins?.bottom || 0) }]}>
              <View style={st.typeKeys}>
                {[['Enter', 'enter'], ['Tab', 'tab'], ['Esc', 'escape'], ['⌫', 'backspace']].map(([label, key]) => (
                  <Pressable key={key} onPress={() => desktopKey(key)} style={st.typeKey}>
                    <Text style={st.typeKeyT}>{label}</Text>
                  </Pressable>
                ))}
              </View>
              <View style={st.typeRow}>
                <TextInput
                  style={st.typeIn}
                  value={typeText}
                  onChangeText={setTypeText}
                  placeholder="Type here, then Send — it lands on the desktop"
                  placeholderTextColor={MUTED}
                  autoCapitalize="none"
                  autoCorrect={false}
                  onSubmitEditing={() => desktopType(false)}
                  returnKeyType="done"
                />
                <Pressable onPress={() => desktopType(false)} disabled={typeBusy || !typeText}
                  style={[st.typeSend, (typeBusy || !typeText) && { backgroundColor: '#e7e7e7' }]}>
                  <Text style={st.typeSendT}>{typeBusy ? '…' : 'Send'}</Text>
                </Pressable>
              </View>
            </View>
          )}
        </View>
      )}
      {browserOpen && (
        <View style={st.fullScr} onStartShouldSetResponder={() => true}>
          <View style={[st.fullBar, { paddingTop: (ins?.top || 0) + 8 }]}>
            <Text style={st.fullT}>{controlling ? 'Browser · you are driving' : 'Browser · follow along'}</Text>
            <View style={{ flex: 1 }} />
            <Pressable onPress={stopBrowserScreen} hitSlop={8} style={[st.fullBtn, { backgroundColor: '#00a884', borderColor: '#00a884' }]}>
              <Text style={[st.fullBtnT, { color: '#fff' }]}>Hand back to Bot</Text>
            </Pressable>
            <Pressable onPress={stopBrowserScreen} hitSlop={8}><Text style={st.fullX}>✕</Text></Pressable>
          </View>
          <View style={{ flex: 1, backgroundColor: '#000' }}>
            {!browserShot ? (
              <View style={st.fullErr}>
                <ActivityIndicator color={MUTED} />
                <Text style={st.fullErrT}>{browserErr || 'Fetching the Bot’s browser screen…'}</Text>
              </View>
            ) : (
              <Image
                ref={browserRef}
                style={{ flex: 1, width: '100%', height: '100%', backgroundColor: '#000' }}
                source={{ uri: browserShot }}
                resizeMode="contain"
                onStartShouldSetResponder={() => true}
                onTouchStart={onBrowserTap}
              />
            )}
          </View>
          <View style={[st.typeBar, { paddingBottom: 8 + (ins?.bottom || 0) }]}>
            <Text style={st.fullErrS}>Tap to click inside the browser · ✕ to hand back to the Bot</Text>
          </View>
        </View>
      )}
      
      {filePrev && (
        <View style={st.scrimM} onStartShouldSetResponder={() => true}>
          <View style={[st.sheet, { paddingBottom: 16 + (ins?.bottom || 0) }]}>
            <View style={st.grabber} />
            <Text style={st.sheetT}>{filePrev.name ? String(filePrev.name) : 'File Preview'}</Text>
            <View style={{ marginTop: 10 }}>
              <FilePreviewBody f={filePrev} />
            </View>
            <View style={{ flexDirection: 'row', gap: 8 }}>
              <Pressable onPress={() => Linking.openURL(filePrev.url).catch(() => {})} style={[st.fileBtn, { flex: 1 }]}>
                <Text style={st.fileBtnT}>Download</Text>
              </Pressable>
              <Pressable onPress={() => setFilePrev(null)} style={[st.fileBtn, { backgroundColor: '#f2f2f2', flex: 1 }]}>
                <Text style={st.fileBtnT}>Close</Text>
              </Pressable>
            </View>
          </View>
        </View>
      )}
      
    </KeyboardAvoidingView>
  );
}

// Claude-style document preview: first PDF page inline with a page switch,
// extracted text for DOCX/TXT, image for images. Pages come from /api/computer/preview.
function FilePreviewBody({ f }) {
  const kind = (f && f.kind) || 'binary';
  const path = f && f.path;
  const [pages, setPages] = useState(null);
  const [page, setPage] = useState(1);
  const [text, setText] = useState('');
  const [err, setErr] = useState('');
  useEffect(() => {
    let live = true;
    (async () => {
      if (kind === 'image') return;
      try {
        const info = await api('/api/computer/preview?path=' + encodeURIComponent(path));
        if (!live) return;
        if (info && info.pages) setPages(info.pages);
        if (info && typeof info.text === 'string') setText(info.text.slice(0, 20000));
        if (info && info.kind && info.kind !== kind && info.kind !== 'binary') setErr('');
      } catch (e) { if (live) setErr(''); }
    })();
    return () => { live = false; };
  }, [path, kind]);
  const { base, token } = conf();
  const pageUri = (p) => base + '/api/computer/preview/page?path=' + encodeURIComponent(path)
    + '&page=' + p + '&token=' + encodeURIComponent(token || '');
  if (kind === 'pdf') {
    if (!pages) {
      return (
        <View style={st.fpLoading}><ActivityIndicator color={MUTED} /><Text style={st.fpNote}>loading preview…</Text></View>
      );
    }
    return (
      <View>
        <Image key={'pg' + page} style={st.fpPage} source={{ uri: pageUri(page) }} resizeMode="contain" />
        {pages > 1 ? (
          <View style={st.fpPager}>
            <Pressable disabled={page <= 1} onPress={() => setPage(page - 1)} style={[st.fpBtn, page <= 1 && { opacity: 0.3 }]}>
              <Text style={st.fpBtnT}>‹</Text>
            </Pressable>
            <Text style={st.fpPageLbl}>{page} / {pages}</Text>
            <Pressable disabled={page >= pages} onPress={() => setPage(page + 1)} style={[st.fpBtn, page >= pages && { opacity: 0.3 }]}>
              <Text style={st.fpBtnT}>›</Text>
            </Pressable>
          </View>
        ) : null}
      </View>
    );
  }
  if (kind === 'docx' || kind === 'text') {
    if (!text && !err) {
      return (
        <View style={st.fpLoading}><ActivityIndicator color={MUTED} /><Text style={st.fpNote}>loading preview…</Text></View>
      );
    }
    return (
      <ScrollView style={st.fpTextWrap} contentContainerStyle={st.fpTextPad} showsVerticalScrollIndicator>
        <Text selectable style={st.fpText}>{text || '(no text extracted)'}</Text>
      </ScrollView>
    );
  }
  if (kind === 'image') {
    return <Image style={st.filePrevImg} source={{ uri: f.url }} />;
  }
  return (
    <View style={st.filePrevDoc}>
      <Text style={{ fontSize: 24 }}>📦</Text>
      <Text style={st.filePrevName}>{f.name}</Text>
      <Text style={st.filePrevMime}>{f.mime}</Text>
    </View>
  );
}

function renderStreamAware(fn) {
  return ({ item }) => {
    if (item && item.stream != null) {
      try {
        return (
          <View style={st.rowIn}>
            <View style={st.answer}>
              {md(plainText(uiStreamText(item.stream)) || '…', st.answerT)}
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
  header: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 10, paddingBottom: 8, backgroundColor: '#ffffff' },
  hBtn: { width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center' },
  hIcon: { color: TEXT, fontSize: 19 },
  hIconSm: { color: MUTED, fontSize: 17 },
  htitle: { color: TEXT, fontSize: 16, fontWeight: '700', textAlign: 'center' },
  hstatus: { color: MUTED, fontSize: 11.5, marginTop: 1, textAlign: 'center' },

  errBar: { backgroundColor: '#fdeceb', borderTopWidth: 1, borderBottomWidth: 1, borderColor: '#d92d20', paddingHorizontal: 14, paddingVertical: 8 },
  errT: { color: '#b00020', fontSize: 12.5 },

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
  stop: { color: '#b00020', fontWeight: '700', fontSize: 13 },

  replyBar: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 14, paddingVertical: 7, backgroundColor: PANEL, borderTopWidth: 1, borderColor: LINE },
  replyWho: { color: ACCENT, fontSize: 12, fontWeight: '700' },
  replyTxt: { color: MUTED, fontSize: 12.5 },
  replyX: { color: MUTED, fontSize: 15 },

  composerWrap: { paddingHorizontal: 10, paddingTop: 6, backgroundColor: '#ffffff' },
  composer: { flexDirection: 'row', alignItems: 'flex-end', gap: 6, backgroundColor: '#f2f2f2', borderWidth: 1, borderColor: '#e7e7e7', borderRadius: 24, paddingLeft: 14, paddingRight: 5, paddingVertical: 5 },
  uploadBtn: { width: 34, height: 34, borderRadius: 17, backgroundColor: '#e7e7e7', alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: '#cccccc' },
  uploadT: { color: TEXT, fontSize: 14, lineHeight: 16 },
  input: { flex: 1, color: TEXT, fontSize: 15.5, lineHeight: 21, paddingVertical: 9, maxHeight: 120 },
  sendBtn: { width: 38, height: 38, borderRadius: 19, backgroundColor: '#00a884', alignItems: 'center', justifyContent: 'center' },
  sendBtnOff: { backgroundColor: '#e7e7e7' },
  sendT: { color: '#ffffff', fontSize: 17, fontWeight: '800', lineHeight: 19, marginLeft: 1 },
  attachRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, paddingHorizontal: 4, paddingBottom: 6 },
  attachChip: { backgroundColor: '#e7e7e7', borderRadius: 999, paddingHorizontal: 10, paddingVertical: 5, borderWidth: 1, borderColor: '#999999' },
  attachChipT: { color: TEXT, fontSize: 12 },
  attachThumb: { width: 56, height: 56, borderRadius: 10 },

  emojiPanel: { flexDirection: 'row', flexWrap: 'wrap', gap: 4, backgroundColor: BG, paddingHorizontal: 10, paddingTop: 8 },
  emojiB: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center', borderRadius: 10 },

  reasonBody: { maxHeight: 190, backgroundColor: PANEL, paddingHorizontal: 14, paddingVertical: 6, borderTopWidth: 1, borderBottomWidth: 1, borderColor: LINE },
  reasonRow: { flexDirection: 'row', gap: 8, alignItems: 'center', paddingVertical: 3 },
  reasonGlyph: { fontSize: 12, width: 14, textAlign: 'center' },
  reasonTxt: { color: TEXT, fontSize: 13, flex: 1 },
  reasonEmpty: { color: MUTED, fontSize: 12.5, paddingVertical: 4 },

  live: { backgroundColor: PANEL, borderBottomWidth: 1, borderColor: LINE },
  liveLbl: { color: '#0066cc', fontSize: 12.5, fontWeight: '600', paddingHorizontal: 14, paddingTop: 7, paddingBottom: 4 },
  liveImgSm: { width: '100%', height: 90 },

  appCard: { backgroundColor: '#f2f2f2', borderTopWidth: 1, borderColor: '#cccccc' },
  appRow: { paddingHorizontal: 14, paddingVertical: 10, gap: 8 },
  appT: { color: '#000000', fontSize: 13, lineHeight: 18 },
  appBtns: { flexDirection: 'row', gap: 8 },
  appBtn: { backgroundColor: '#e7e7e7', borderRadius: 999, paddingHorizontal: 14, paddingVertical: 8 },
  appOk: { color: '#006400', fontSize: 13, fontWeight: '800' },
  appNo: { color: '#b00020', fontSize: 13, fontWeight: '800' },
  ansIn: { flex: 1, color: TEXT, fontSize: 14, borderWidth: 1, borderColor: '#cccccc', borderRadius: 12, paddingHorizontal: 10, paddingVertical: 8 },
  ansBtn: { backgroundColor: '#e7e7e7', borderRadius: 12, paddingHorizontal: 14, justifyContent: 'center' },
  ansBtnT: { color: '#000000', fontSize: 13, fontWeight: '800' },

  scrimM: { position: 'absolute', inset: 0, backgroundColor: 'rgba(0,0,0,.66)', alignItems: 'center', justifyContent: 'flex-end' },
  sheet: { width: '100%', backgroundColor: '#f2f2f2', borderTopLeftRadius: 22, borderTopRightRadius: 22, padding: 16, gap: 6, borderTopWidth: 1, borderColor: '#e7e7e7' },
  grabber: { alignSelf: 'center', width: 38, height: 4, borderRadius: 2, backgroundColor: '#999999', marginBottom: 8 },
  sheetT: { color: TEXT, fontSize: 13, fontWeight: '700' },
  sheetSub: { color: MUTED, fontSize: 13, lineHeight: 18 },
  sheetBtn: { borderRadius: 14, padding: 13, alignItems: 'center', backgroundColor: '#e7e7e7', marginTop: 6 },
  sheetBtnT: { color: TEXT, fontSize: 14, fontWeight: '700' },
  histIn: { backgroundColor: '#e7e7e7', borderRadius: 999, color: TEXT, paddingHorizontal: 14, paddingVertical: 10, fontSize: 14, marginBottom: 8 },
  histRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 12, paddingHorizontal: 8, borderRadius: 10 },
  histT: { color: TEXT, fontSize: 14.5, flex: 1 },

  fullScr: { position: 'absolute', inset: 0, backgroundColor: '#000' },
  fullBar: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 14, paddingTop: 40, paddingBottom: 8, backgroundColor: BG, gap: 12 },
  fullT: { color: TEXT, fontSize: 15, fontWeight: '800' },
  fullBtn: { width: 34, height: 34, borderRadius: 17, backgroundColor: '#e7e7e7', alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: '#cccccc' },
  fullBtnT: { color: TEXT, fontSize: 13, fontWeight: '600', lineHeight: 15 },
  fullX: { color: TEXT, fontSize: 15, fontWeight: '700' },
  fullErr: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 20, gap: 8 },
  fullErrT: { color: '#b00020', fontSize: 13.5, textAlign: 'center' },
  fullErrS: { color: MUTED, fontSize: 12 },
  typeBar: { backgroundColor: BG, borderTopWidth: 1, borderColor: LINE, paddingHorizontal: 10, paddingTop: 6, gap: 6 },
  typeKeys: { flexDirection: 'row', gap: 6 },
  typeKey: { backgroundColor: '#e7e7e7', borderRadius: 8, paddingHorizontal: 12, paddingVertical: 6, borderWidth: 1, borderColor: '#999999' },
  typeKeyT: { color: TEXT, fontSize: 12.5, fontWeight: '600' },
  typeRow: { flexDirection: 'row', gap: 8, alignItems: 'center' },
  typeIn: { flex: 1, backgroundColor: '#f2f2f2', borderRadius: 20, borderWidth: 1, borderColor: '#e7e7e7', color: TEXT, paddingHorizontal: 14, paddingVertical: 9, fontSize: 14 },
  typeSend: { backgroundColor: '#00a884', borderRadius: 999, paddingHorizontal: 16, paddingVertical: 10 },
  typeSendT: { color: '#fff', fontSize: 13, fontWeight: '800' },
  fileRow: { flexDirection: 'row', alignItems: 'center', gap: 8, backgroundColor: '#f2f2f2', borderWidth: 1, borderColor: '#e7e7e7', borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, marginVertical: 4 },
  fileIc: { fontSize: 20, width: 32, textAlign: 'center' },
  fileName: { color: TEXT, fontSize: 13, fontWeight: '600', numberOfLines: 1 },
  fileSize: { color: MUTED, fontSize: 11 },
  fileBtn: { backgroundColor: '#e7e7e7', borderRadius: 8, borderWidth: 1, borderColor: '#999999', alignItems: 'center', justifyContent: 'center', paddingHorizontal: 10, paddingVertical: 6 },
  fileBtnT: { color: '#0066cc', fontSize: 14, fontWeight: '700' },
  filePrevImg: { width: '100%', height: 300, borderRadius: 12, backgroundColor: '#ffffff' },
  filePrevDoc: { alignItems: 'center', padding: 24, backgroundColor: '#ffffff', borderRadius: 12 },
  fpPage: { width: '100%', height: 420, borderRadius: 8, backgroundColor: '#f2f2f2' },
  fpPager: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 14, paddingVertical: 8 },
  fpBtn: { width: 40, height: 32, borderRadius: 8, backgroundColor: '#f2f2f2', alignItems: 'center', justifyContent: 'center' },
  fpBtnT: { color: TEXT, fontSize: 18, fontWeight: '700' },
  fpPageLbl: { color: MUTED, fontSize: 13 },
  fpTextWrap: { maxHeight: 380, backgroundColor: '#f2f2f2', borderRadius: 12 },
  fpTextPad: { padding: 14 },
  fpText: { color: '#000000', fontSize: 13.5, lineHeight: 20 },
  fpLoading: { padding: 22, alignItems: 'center', gap: 8 },
  fpNote: { color: MUTED, fontSize: 12.5 },
  toolRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', backgroundColor: '#f2f2f2', borderWidth: 1, borderColor: '#e7e7e7', borderRadius: 8, paddingHorizontal: 10, paddingVertical: 6, marginVertical: 5 },
  toolName: { color: ACCENT, fontSize: 12, fontWeight: '700' },
  toolStatus: { color: MUTED, fontSize: 11.5 },
  filePrevName: { color: TEXT, fontSize: 16, fontWeight: '600', marginTop: 10 },
  filePrevMime: { color: MUTED, fontSize: 12, marginTop: 4 },
  // ---- new feature styles ----
  headerCenter: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 4 },
  statusWrap: { flexDirection: 'row', alignItems: 'center', gap: 4, borderRadius: 10, paddingHorizontal: 8, paddingVertical: 2, marginTop: 2 },
  statusText: { color: '#ffffff', fontSize: 11, fontWeight: '700' },
  switchBtn: { width: 32, height: 32, borderRadius: 16, backgroundColor: '#e7e7e7', alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: '#cccccc' },
  switchBtnT: { color: TEXT, fontSize: 14 },
  themeBtn: { width: 32, height: 32, borderRadius: 16, backgroundColor: '#e7e7e7', alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: '#cccccc' },
  themeBtnT: { fontSize: 16 },
  notifBadge: { position: 'absolute', top: 2, right: 2, width: 20, height: 20, borderRadius: 10, backgroundColor: '#b00020', alignItems: 'center', justifyContent: 'center', borderWidth: 2, borderColor: '#ffffff' },
  notifBadgeT: { color: '#ffffff', fontSize: 10, fontWeight: '800' },
  // search within chat
  searchBar: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 14, paddingVertical: 8, backgroundColor: PANEL, borderBottomWidth: 1, borderColor: LINE },
  searchInput: { flex: 1, color: TEXT, fontSize: 14, backgroundColor: '#ffffff', borderWidth: 1, borderColor: LINE, borderRadius: 20, paddingHorizontal: 12, paddingVertical: 7 },
  searchClear: { width: 28, height: 28, borderRadius: 14, backgroundColor: '#e7e7e7', alignItems: 'center', justifyContent: 'center' },
  searchClearT: { color: TEXT, fontSize: 14 },
  searchBtn: { width: 34, height: 34, borderRadius: 17, backgroundColor: '#e7e7e7', alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: '#cccccc' },
  searchBtnT: { fontSize: 16 },
  offlineBanner: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 14, paddingVertical: 6, backgroundColor: '#fff3cd', borderBottomWidth: 1, borderColor: '#e0a53a' },
  offlineText: { color: '#856404', fontSize: 12, fontWeight: '600' },
  // scheduled send chip
  schedChip: { flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: '#e7f0ff', borderRadius: 10, paddingHorizontal: 10, paddingVertical: 5, marginBottom: 6 },
  schedChipT: { color: '#1d4ed8', fontSize: 12, fontWeight: '700' },
  schedClear: { color: '#b00020', fontSize: 11, fontWeight: '700' },
});
