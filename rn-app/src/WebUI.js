// Web-app UI kit — elements rebuilt 1:1 from web/index.html + web/style.css +
// web/app.js, so the phone app looks and behaves like the site that runs at
// https://8765-agquljzsz7k42vgoenj7qr6fim.instabox.tech.
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator, Alert, FlatList, Image, Linking, Modal, Pressable,
  ScrollView, StyleSheet, Text, TextInput, View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { api, conf, pluginsList, pluginConnect, pluginDisconnect, pluginConfig, mcpList, mcpSave, mcpDelete, mcpRestart } from './session';
import { C } from './theme';
import Avatar from './Avatar';

// ---------------------------------------------------------------- helpers
export function initials(email) {
  const local = String(email || '').split('@')[0];
  const chars = String(local).replace(/[^a-z0-9]/gi, '');
  return (chars.slice(0, 2) || '··').toUpperCase();
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function hhmm(d) {
  const p = (n) => (n < 10 ? '0' + n : String(n));
  return p(d.getHours()) + ':' + p(d.getMinutes());
}
// web humanTime(): today -> time, yesterday -> "Yesterday", else "Mon d"
export function humanTime(ts) {
  const n = Number(ts);
  if (!n) return '';
  const secs = n < 1e12 ? n * 1000 : n;
  const d = new Date(secs);
  const now = new Date();
  const dayMs = 86400000;
  const day = (x) => Math.floor(x.getTime() / dayMs);
  if (day(d) === day(now)) return hhmm(d);
  if (day(now) - day(d) === 1) return 'Yesterday';
  return MONTHS[d.getMonth()] + ' ' + d.getDate();
}
// web fmtTime(): "Mon d, HH:mm"
export function fmtTime(ts) {
  const n = Number(ts);
  if (!n) return '';
  const secs = n < 1e12 ? n * 1000 : n;
  const d = new Date(secs);
  return MONTHS[d.getMonth()] + ' ' + d.getDate() + ', ' + hhmm(d);
}
export function agoTime(ts) {
  const n = Number(ts);
  if (!n) return '';
  const secs = n < 1e12 ? n : Math.floor(n / 1000);
  const d = Math.max(0, (Date.now() / 1000 - secs));
  if (d < 60) return 'just now';
  if (d < 3600) return Math.round(d / 60) + ' min ago';
  if (d < 86400) return Math.round(d / 3600) + ' h ago';
  return MONTHS[new Date(secs * 1000).getMonth()] + ' ' + new Date(secs * 1000).getDate();
}
export function humanSize(b) {
  const n = Number(b) || 0;
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1048576).toFixed(1) + ' MB';
}
// image URL with the per-request ?_token= the instabox port may require
export function imgUrl(path) {
  const { base, token } = conf();
  const sep = String(path).includes('?') ? '&' : '?';
  return base + path + sep + '_token=' + encodeURIComponent(token || '');
}
function dotColor(s) {
  if (s === 'ok' || s === 'running' || s === 'done') return C.ok;
  if (s === 'error') return C.bad;
  if (s === 'denied' || s === 'blocked' || s === 'waiting_approval') return C.warn;
  return C.muted;
}
export function stateOf(busy, id) {
  const st = busy && busy[id];
  if (!st) return { key: 'idle', label: '', chip: null };
  if (st.status === 'waiting_approval') return { key: 'wait', label: 'needs you', chip: [W.chipWait, C.warn] };
  return { key: 'work', label: st.status === 'retrying' ? 'retrying' : 'working', chip: [W.chipWork, C.work] };
}

// ---------------------------------------------------------------- sheet style
const W = StyleSheet.create({
  screen: { flex: 1, backgroundColor: C.bg },
  body: { flex: 1, backgroundColor: C.bg },
  // -- topbar
  tb: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 14, backgroundColor: C.bg },
  tbTitle: { flex: 1, color: C.text, fontSize: 20, fontWeight: '800', letterSpacing: -0.4 },
  tbChip: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 8 },
  tbChipBox: { flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: C.panel2, borderRadius: 999, paddingLeft: 3, paddingRight: 12, paddingVertical: 3 },
  tbChipT: { color: C.text, fontSize: 15, fontWeight: '700', flexShrink: 1 },
  dot: { width: 10, height: 10, borderRadius: 5, backgroundColor: C.ok },
  dotOff: { backgroundColor: C.bad },
  hbtn: {
    width: 40, height: 40, borderRadius: 999, backgroundColor: '#ffffff',
    alignItems: 'center', justifyContent: 'center', flex: 0,
    elevation: 4, shadowColor: '#111', shadowOpacity: 0.08, shadowRadius: 6, shadowOffset: { width: 0, height: 1 },
  },
  hbtnFill: { backgroundColor: C.solid },
  hbtnT: { fontSize: 19, color: C.text, marginTop: -2, fontWeight: '600' },
  hbtnTF: { color: '#ffffff' },
  avatarBtn: { width: 40, height: 40, borderRadius: 999, backgroundColor: C.panel2, alignItems: 'center', justifyContent: 'center' },
  avatarT: { fontSize: 13, fontWeight: '800', color: C.text },
  // -- shared bits
  section: { color: C.muted, fontSize: 13, fontWeight: '600', marginHorizontal: 16, marginTop: 14, marginBottom: 7 },
  card: { backgroundColor: C.bg, borderColor: C.line, borderWidth: 1, borderRadius: 16, padding: 14, marginHorizontal: 14, marginBottom: 10 },
  pillBtn: { backgroundColor: C.soft, borderRadius: 999, paddingHorizontal: 14, paddingVertical: 7, alignItems: 'center' },
  pillBtnT: { color: C.text, fontSize: 13, fontWeight: '600' },
  wideBlack: { backgroundColor: C.solid, borderRadius: 999, paddingVertical: 12, paddingHorizontal: 20, alignItems: 'center', justifyContent: 'center' },
  wideBlackT: { color: C.onSolid, fontWeight: '800', fontSize: 15 },
  input: { backgroundColor: C.panel2, borderRadius: 12, color: C.text, paddingHorizontal: 14, paddingVertical: 11, fontSize: 15 },
  rowInput: { backgroundColor: C.panel2, borderRadius: 12, color: C.text, paddingHorizontal: 11, paddingVertical: 9, fontSize: 14, flex: 1 },
  chip: { fontSize: 11, paddingVertical: 2, paddingHorizontal: 9, borderRadius: 999, backgroundColor: C.panel2, color: C.muted, fontWeight: '600', overflow: 'hidden' },
  chipWork: { backgroundColor: '#e7f0ff', color: C.work },
  chipWait: { backgroundColor: C.warnSoft, color: C.warn },
  muted: { color: C.muted, fontSize: 13 },
  mutedC: { color: C.muted, fontSize: 13, textAlign: 'center', padding: 24 },
  // -- list rows (bots / inbox / actions)
  grow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 16, paddingVertical: 11, borderBottomWidth: 1, borderColor: C.line, backgroundColor: C.bg },
  gName: { color: C.text, fontSize: 15, fontWeight: '600' },
  gSub: { color: C.muted, fontSize: 13, marginTop: 1 },
  gTime: { color: C.muted, fontSize: 12 },
  pillRow: { flexDirection: 'row', alignItems: 'center' },
  // -- tab sheet
  scrim: { flex: 1, backgroundColor: 'rgba(17,17,17,.32)' },
  sheet: { backgroundColor: C.bg, borderTopLeftRadius: 24, borderTopRightRadius: 24, paddingBottom: 6, maxHeight: '82%' },
  grip: { width: 40, height: 4, borderRadius: 2, backgroundColor: '#d8d8d6', alignSelf: 'center', marginTop: 6, marginBottom: 8 },
  tabRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomWidth: 1, borderColor: C.line, paddingHorizontal: 22, paddingVertical: 14 },
  tabLbl: { color: C.text, fontSize: 16, fontWeight: '600' },
  tabLblOn: { fontWeight: '800' },
  tabTick: { color: C.muted, fontSize: 15, fontWeight: '800' },
  badge: { backgroundColor: C.bad, color: '#ffffff', borderRadius: 999, overflow: 'hidden', paddingHorizontal: 8, paddingVertical: 1, fontSize: 12, fontWeight: '700' },
  dangerT: { color: C.bad },
  // -- banner / tiles (home)
  banner: { backgroundColor: C.panel, borderRadius: 16, padding: 14, marginHorizontal: 14, marginBottom: 10, flexDirection: 'row', gap: 10, alignItems: 'center' },
  bannerB: { color: '#FF9500', fontWeight: '800' },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginHorizontal: 14, marginBottom: 10 },
  tile: { flexGrow: 1, width: '47%', backgroundColor: C.bg, borderColor: C.line, borderWidth: 1, borderRadius: 18, padding: 14, minHeight: 108, justifyContent: 'space-between' },
  tileCap: { color: C.muted, fontSize: 12, fontWeight: '600', marginBottom: 4 },
  tileVal: { color: C.text, fontSize: 30, fontWeight: '800', letterSpacing: -1.2 },
  tileSub: { color: C.muted, fontSize: 12, marginTop: 4 },
  teamCard: { flexGrow: 1, width: '47%', backgroundColor: C.bg, borderColor: C.line, borderWidth: 1, borderRadius: 14, padding: 10, flexDirection: 'row', gap: 10, alignItems: 'center' },
  teamEmoji: { width: 36, height: 36, borderRadius: 12, backgroundColor: C.panel2, alignItems: 'center', justifyContent: 'center', fontSize: 20 },
  teamName: { color: C.text, fontWeight: '600', fontSize: 13.5, flexShrink: 1 },
  teamJob: { color: C.muted, fontSize: 11.5, flexShrink: 1 },
  appCard: { backgroundColor: '#f6f9ff', borderColor: '#d6e2ff', borderWidth: 1, borderRadius: 12, borderLeftWidth: 3, borderLeftColor: C.warn, padding: 12, marginHorizontal: 14, marginBottom: 8 },
  appSummary: { color: C.text, fontSize: 14, fontWeight: '600' },
  appMeta: { color: C.muted, fontSize: 12, marginTop: 2 },
  feedRow: { flexDirection: 'row', gap: 8, paddingHorizontal: 16, paddingVertical: 8, borderBottomWidth: 1, borderColor: C.line },
  feedDot: { width: 6, height: 6, borderRadius: 3, marginTop: 7 },
  feedWho: { color: C.muted, fontSize: 12, marginBottom: 1 },
  feedWhen: { color: C.muted, fontSize: 11, marginTop: 1 },
  // -- approval card buttons
  abtn: { backgroundColor: C.panel2, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 7, alignItems: 'center' },
  abtnP: { backgroundColor: C.solid },
  abtnT: { color: C.text, fontSize: 12.5, fontWeight: '700' },
  abtnPT: { color: C.onSolid },
  // -- inbox tabs
  ibTab: { flex: 1, paddingVertical: 8, alignItems: 'center', borderBottomWidth: 2, borderBottomColor: 'transparent' },
  ibTabOn: { borderBottomColor: C.solid },
  ibTabT: { color: C.muted, fontSize: 13, fontWeight: '600' },
  ibTabTOn: { color: C.text },
  // -- computer
  cpCard: { flexGrow: 1, width: '47%', backgroundColor: C.bg, borderColor: C.line, borderWidth: 1, borderRadius: 16, padding: 12 },
  cpThumb: { backgroundColor: C.panel, borderRadius: 12, height: 100, marginBottom: 8, alignItems: 'center', justifyContent: 'center', overflow: 'hidden' },
  cpNo: { color: C.muted, fontSize: 11, textAlign: 'center', paddingHorizontal: 6 },
  mono: { fontFamily: 'monospace', fontSize: 12, color: C.text },
  // -- screen takeover
  screenBar: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 10, paddingVertical: 8, backgroundColor: C.bg },
  screenWrap: { flex: 1, backgroundColor: '#000', alignItems: 'center' },
  pillInput: { flex: 1, minWidth: 0, backgroundColor: '#ffffff', borderRadius: 12, borderWidth: 1, borderColor: C.line, color: C.text, paddingHorizontal: 10, paddingVertical: 7, fontSize: 13 },
  wide: { alignSelf: 'stretch', marginHorizontal: 14, marginVertical: 10 },
  // ---------------------------------------------------------------- muse
  museHeader: { alignItems: 'center', paddingTop: 10, paddingHorizontal: 16, marginBottom: 14 },
  museAvatar: { width: 72, height: 72, borderRadius: 36, backgroundColor: C.panel2, alignItems: 'center', justifyContent: 'center', marginBottom: 10 },
  museAvatarT: { fontSize: 26 },
  museName: { color: C.text, fontSize: 22, fontWeight: '800', marginBottom: 2 },
  museStatus: { flexDirection: 'row', alignItems: 'center', gap: 6, color: C.ok, fontSize: 13, fontWeight: '700', marginBottom: 14 },
  museDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: C.ok },
  museActionRow: { flexDirection: 'row', gap: 10, marginBottom: 14 },
  museActionBtn: { flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, backgroundColor: C.panel2, borderRadius: 14, paddingVertical: 10, borderWidth: 1, borderColor: C.line },
  museActionIcon: { fontSize: 16 },
  museActionT: { color: C.muted, fontSize: 11, fontWeight: '600', marginTop: 2 },
  museSection: { color: C.muted, fontSize: 13, fontWeight: '700', marginHorizontal: 16, marginTop: 4, marginBottom: 10 },
  museCard: { backgroundColor: C.bg, borderColor: C.line, borderWidth: 1, borderRadius: 20, padding: 16, marginHorizontal: 16, marginBottom: 12 },
  museCardRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  museIconBox: { width: 48, height: 48, borderRadius: 12, backgroundColor: C.panel2, alignItems: 'center', justifyContent: 'center', marginRight: 2 },
  museIcon: { fontSize: 22 },
  museTitle: { color: C.text, fontSize: 15, fontWeight: '700', minWidth: 0 },
  museSub: { color: C.muted, fontSize: 12, marginTop: 2, lineHeight: 17 },
  museDetails: { color: C.ok, fontSize: 12, fontWeight: '700', marginTop: 10 },
  museBtnRow: { gap: 10, marginTop: 12 },
  museBtnPrimary: { flex: 1, backgroundColor: '#1f4ed8', borderRadius: 14, paddingVertical: 12, alignItems: 'center', justifyContent: 'center' },
  museBtnPrimaryT: { color: '#ffffff', fontWeight: '800', fontSize: 15 },
  museBtnGhost: { flex: 1, backgroundColor: '#f0f0ef', borderRadius: 14, paddingVertical: 12, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: C.line },
  museBtnGhostT: { color: C.text, fontWeight: '700', fontSize: 14 },
  museBtnRow2: { flexDirection: 'row', gap: 10, marginTop: 8 },
  museBtnGhost2: { flex: 1, backgroundColor: '#f0f0ef', borderRadius: 14, paddingVertical: 12, alignItems: 'center', justifyContent: 'center' },
  museBtnGhost2T: { color: C.text, fontWeight: '700', fontSize: 13 },
  museHistoryItem: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 10, borderBottomWidth: 1, borderColor: C.line },
  museHistoryIcon: { width: 40, height: 40, borderRadius: 10, backgroundColor: C.panel2, alignItems: 'center', justifyContent: 'center', fontSize: 18 },
  museHistoryText: { flex: 1, minWidth: 0 },
  museHistoryTitle: { color: C.text, fontSize: 14, fontWeight: '600', minWidth: 0 },
  museHistorySub: { color: C.muted, fontSize: 11, marginTop: 1 },
  museHistoryRight: { color: C.muted, fontSize: 11, fontWeight: '600' },
  museIdeaItem: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 10, borderBottomWidth: 1, borderColor: C.line },
  museGoalRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 10, borderBottomWidth: 1, borderColor: C.line },
  museGoalToggle: { width: 44, height: 24, borderRadius: 12, backgroundColor: C.panel2, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: C.line },
  museGoalToggleOn: { backgroundColor: C.ok, borderColor: C.ok },
  museShowMore: { color: C.ok, fontSize: 12, fontWeight: '700', marginTop: 6 },
  financeCard: { backgroundColor: '#1f4ed8', borderRadius: 20, padding: 16, marginHorizontal: 16, marginBottom: 12 },
  financeCardVal: { color: '#ffffff', fontSize: 28, fontWeight: '800' },
  financeCardCap: { color: 'rgba(255,255,255,.7)', fontSize: 12, fontWeight: '600' },
  financeCardSub: { color: 'rgba(255,255,255,.5)', fontSize: 11, marginTop: 2 },
});

// ---------------------------------------------------------------- top bar
export function WebTopBar({ title, showBack, onBack, conn, showSearch, onSearch, showNew, onNew, account, onAvatar, botForTitle }) {
  const ins = useSafeAreaInsets();
  return (
    <View style={[W.tb, { paddingTop: (ins.top || 0) + 8, paddingBottom: 8 }]}>
      {showBack ? (
        <Pressable onPress={onBack} hitSlop={6} style={W.hbtn}>
          <Text style={[W.hbtnT, { fontWeight: '800' }]}>‹</Text>
        </Pressable>
      ) : null}
      {botForTitle ? (
        <Pressable style={W.tbChip} onLongPress={onAvatar} delayLongPress={500}>
          <View style={W.tbChipBox}>
            <Avatar bot={botForTitle} size={26} />
            <Text style={W.tbChipT} numberOfLines={1}>{botForTitle.name}</Text>
          </View>
        </Pressable>
      ) : (
        <Text style={W.tbTitle} numberOfLines={1}>{title || ''}</Text>
      )}
      <View style={[W.dot, !conn && W.dotOff]} />
      {showSearch ? (
        <Pressable onPress={onSearch} hitSlop={6} style={({ pressed }) => [W.hbtn, pressed && { opacity: .8 }]}>
          <Text style={W.hbtnT}>⌕</Text>
        </Pressable>
      ) : null}
      {showNew ? (
        <Pressable onPress={onNew} hitSlop={6} style={({ pressed }) => [W.hbtn, W.hbtnFill, pressed && { opacity: .85 }]}>
          <Text style={[W.hbtnTF, { fontSize: 22, marginTop: -4, color: '#fff' }]}>＋</Text>
        </Pressable>
      ) : null}
      <Pressable onPress={onAvatar} hitSlop={6} style={({ pressed }) => [W.avatarBtn, pressed && { opacity: .85 }]}>
        <Text style={W.avatarT}>{initials(account)}</Text>
      </Pressable>
    </View>
  );
}

// ---------------------------------------------------------------- tab sheet
export const TABS = [
  ['profile', 'Muse'],
  ['bots', 'Bots'],
  ['home', 'Home'],
  ['inbox', 'Inbox'],
  ['marketplace', 'Marketplace'],
  ['computer', 'Computer'],
  ['files', 'Files'],
  ['skills', 'Skills'],
  ['routines', 'Routines'],
  ['plugins', 'Plugins'],
  ['usage', 'Usage'],
  ['actionlog', 'Action log'],
  ['plan', 'Plan'],
  ['admin', 'Admin'],
  ['approvals', 'Approvals'],
  ['connectors', 'Connectors'],
  ['ideas', 'Ideas'],
  ['goals', 'Goals'],
  ['finance', 'Spending'],
  ['email', 'Email'],
  ['search', 'Search'],
  ['image', 'Images'],
  ['code', 'Code'],
  ['collab', 'Multi-Agent'],
  ['android', 'Android Box'],
  ['set', 'Settings'],
];
export function TabSheet({ open, tab, inboxBadge, onGo, onClose, initial, tabs }) {
  const ins = useSafeAreaInsets();
  if (!initial && !open) return null;
  const TABS_LIST = tabs || TABS;
  return (
    <Modal transparent visible={open} animationType="slide" onRequestClose={onClose}>
      <View style={{ flex: 1 }}>
        <Pressable style={W.scrim} onPress={onClose} />
        <View style={[W.sheet, { paddingBottom: 6 + (ins.bottom || 0) }]}>
          <View style={W.grip} collapsable={false} />
          <ScrollView showsVerticalScrollIndicator={false} style={{ maxHeight: '82%' }}>
            {TABS_LIST.map(([k, lbl]) => {
              const on = tab === k || (k === 'bots' && tab === 'chat');
              return (
                <Pressable key={k} onPress={() => onGo(k)} style={({ pressed }) => [W.tabRow, pressed && { opacity: .85 }]}>
                  <Text style={[W.tabLbl, on && W.tabLblOn]}>{lbl}</Text>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
                    {k === 'inbox' && !!inboxBadge && <Text style={W.badge} collapsable={false}>{String(inboxBadge)}</Text>}
                    {on && <Text style={W.tabTick}>✓</Text>}
                  </View>
                </Pressable>
              );
            })}
            <Pressable onPress={() => onGo('signout')} style={({ pressed }) => [W.tabRow, { borderBottomWidth: 0 }, pressed && { opacity: .85 }]}>
              <Text style={[W.tabLbl, W.dangerT]}>Sign out</Text>
            </Pressable>
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

// ---------------------------------------------------------------- bots list
export function BotsList({ bots, groups, templates, busy, onOpen, onOpenGroup, onHire, onNew, onDecideHidden, onBotLong, conn, account, onAvatar }) {
  const [q, setQ] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const job = (b) => b.job || 'No job yet';
  const visible = (bots || []).filter((b) => !b.archived && !b.hidden);
  const hidden = (bots || []).filter((b) => b.hidden);
  const ql = q.trim().toLowerCase();
  const filtered = ql
    ? visible.filter((b) => String(b.name + ' ' + job(b)).toLowerCase().includes(ql))
    : visible;
  const sorted = filtered.slice().sort((a, b) => ((b.pinned ? 1 : 0) - (a.pinned ? 1 : 0)));
  const busyIds = Object.keys(busy || {});
  const busyBots = visible.filter((b) => busyIds.includes(b.id));
  const favs = (busyBots.length ? busyBots : visible.slice(0, 6)).slice(0, 6);
  const groupList = (groups || []).filter((g) => g && g.id);

  const favRow = (
    <View style={{ flexDirection: 'row', gap: 14, paddingHorizontal: 14, paddingTop: 6, paddingBottom: 12 }}>
      {favs.map((b) => (
        <Pressable key={b.id} onPress={() => onOpen(b)} style={{ width: 78, alignItems: 'center', gap: 6 }}>
          <Avatar bot={b} size={62} badge={busyIds.includes(b.id)} />
          <Text style={{ fontSize: 12, fontWeight: '600', color: C.text }} numberOfLines={1}>{b.name}</Text>
          {busyIds.includes(b.id) && <Text style={{ fontSize: 11, color: C.muted }}>{stateOf(busy, b.id).label}</Text>}
        </Pressable>
      ))}
    </View>
  );
  const empty = (!bots || !bots.length) ? (
    <View style={{ alignItems: 'center', padding: 40, gap: 6 }}>
      <Text style={{ color: C.text, fontSize: 20, fontWeight: '800' }}>Meet your first Bot</Text>
      <Text style={W.mutedC}>Bots you create show up here.</Text>
      <Pressable onPress={onNew} style={[W.wideBlack, { paddingHorizontal: 24 }]}>
        <Text style={W.wideBlackT}>Create a Bot</Text>
      </Pressable>
    </View>
  ) : !sorted.length && !!ql ? (
    <Text style={W.mutedC}>No Bots match "{q}".</Text>
  ) : null;
  const tpl = [
    <View key="sect-tpl" style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 16, marginTop: 14, marginBottom: 6 }}>
      <Text style={[W.section, { marginHorizontal: 0, marginTop: 0, marginBottom: 0 }]}>Ready-made Bots</Text>
    </View>,
  ];
  const tplRows = (templates || []).map((t) => (
    <Pressable key={t.id} onPress={() => onHire(t.id)} style={({ pressed }) => [W.grow, pressed && { backgroundColor: C.panel }]}>
      <View style={[W.teamEmoji, { width: 44, height: 44, borderRadius: 14 }]}>
        <Text style={{ fontSize: 20 }}>{t.emoji || '✨'}</Text>
      </View>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text style={W.gName} numberOfLines={1}>{t.name}</Text>
        <Text style={W.gSub} numberOfLines={1}>{t.blurb || t.job}</Text>
      </View>
      <View style={[W.pillBtn, { backgroundColor: C.soft }]}>
        <Text style={W.pillBtnT}>Add</Text>
      </View>
    </Pressable>
  ));
  const BotRow = ({ b }) => {
    const s = stateOf(busy, b.id);
    return (
      <Pressable onPress={() => onOpen(b)} onLongPress={() => onBotLong(b)} delayLongPress={400}
        style={({ pressed }) => [W.grow, pressed && { backgroundColor: C.panel }]}>
        <Avatar bot={b} size={44} badge={s.key === 'work' || s.key === 'wait'} />
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={W.gName} numberOfLines={1}>{b.pinned ? '📌 ' : ''}{b.name}</Text>
          <Text style={W.gSub} numberOfLines={1}>{job(b)}</Text>
        </View>
        <View style={{ alignItems: 'flex-end', gap: 3 }}>
          {s.chip ? <Text style={[W.chip, s.chip[0], { color: s.chip[1] }]}>{s.label}</Text> : null}
          <Text style={W.gTime}>{humanTime(b.updated_at)}</Text>
        </View>
      </Pressable>
    );
  };
  return (
    <View style={W.screen}>
      <WebTopBar
        title="Bots"
        conn={conn}
        showSearch
        onSearch={() => setSearchOpen((s) => { if (s) setQ(''); return !s; })}
        showNew
        onNew={onNew}
        account={account}
        onAvatar={onAvatar}
      />
      {searchOpen ? (
        <View style={{ flexDirection: 'row', gap: 8, paddingHorizontal: 14, paddingBottom: 10, alignItems: 'center' }}>
          <TextInput
            style={[W.input, { borderRadius: 999, flex: 1, paddingVertical: 9 }]}
            value={q}
            onChangeText={setQ}
            placeholder="Search Bots"
            placeholderTextColor={C.muted}
            autoCorrect={false}
          />
          <Pressable onPress={() => { setQ(''); setSearchOpen(false); }} hitSlop={6}>
            <Text style={{ color: C.muted, fontSize: 15,fontWeight:'700' }}>✕</Text>
          </Pressable>
        </View>
      ) : null}
      <FlatList
        data={[]}
        renderItem={null}
        ListEmptyComponent={
          <View>
            {!ql && favs.length > 0 ? favRow : null}
            {empty}
            {splitBots({ sorted, favs, favRow, BotRow, q })}
            {!!visible.length ? <View style={{ height: 12 }} /> : null}
            {(!!groupList.length || !!hidden.length || !templates) ? null : null}
            {groupList.length > 0 ? (
              <View>
                <Text style={W.section}>Group chats</Text>
                {groupList.map((g) => (
                  <Pressable key={g.id} onPress={() => onOpenGroup(g)} style={({ pressed }) => [W.grow, pressed && { backgroundColor: C.panel }]}>
                    <View style={[{ width: 44, height: 44, borderRadius: 14, backgroundColor: C.panel2, alignItems: 'center', justifyContent: 'center' }]}>
                      <Text style={{ fontSize: 20 }}>👥</Text>
                    </View>
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <Text style={W.gName} numberOfLines={1}>{g.name}</Text>
                      <Text style={W.gSub} numberOfLines={1}>{(g.members || g.member_names || []).join(', ')}</Text>
                    </View>
                  </Pressable>
                ))}
              </View>
            ) : null}
            {!ql ? tpl.concat(tplRows) : null}
            {hidden.length > 0 ? (
              <View>
                <Text style={W.section}>Hidden Bots ({hidden.length})</Text>
                {hidden.map((b) => (
                  <Pressable key={b.id} style={W.grow}>
                    <Avatar bot={b} size={44} />
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <Text style={W.gName}>{b.name}</Text>
                      <Text style={W.gSub}>Hidden from your team</Text>
                    </View>
                    <Pressable onPress={() => onDecideHidden && onDecideHidden(b)} style={W.pillBtn}>
                      <Text style={W.pillBtnT}>Unhide</Text>
                    </Pressable>
                  </Pressable>
                ))}
              </View>
            ) : null}
          </View>
        }
        keyExtractor={() => 'static'}
      />
    </View>
  );
}
// splitter keeps row & section order: pinned/grouped/segments of Bots list
function splitBots({ sorted, BotRow, q }) {
  if (!sorted.length) return null;
  return sorted.map((b) => <BotRow key={b.id} b={b} />);
}

// ---------------------------------------------------------------- home
export function HomeView({ bots, busy, apps, onOpen, onOpenThread, conn, account, onAvatar, onBack, onSearch }) {
  const [us, setUs] = useState(null);
  const [acts, setActs] = useState([]);
  const [headline, setHeadline] = useState('');
  const ins = useSafeAreaInsets();
  const load = useCallback(async () => {
    try {
      setUs(await api('/api/usage'));
    } catch (e) { /* ignore */ }
    try { const d = await api('/api/actions?limit=8'); setActs(Array.isArray(d) ? d : (d.actions || [])); } catch (e) { /* ignore */ }
    try { const dg = await api('/api/digest?spec=today'); setHeadline((dg && dg.headline) || ''); } catch (e) { /* ignore */ }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);
  const workingIds = Object.keys(busy || {});
  const workingBots = (bots || []).filter((b) => workingIds.includes(b.id));
  const tok = us && (us.total_tokens || us.total);
  const limit = us && us.limit;
  const teamCard = (b) => {
    const s = stateOf(busy, b.id);
    return (
      <Pressable key={b.id} onPress={() => onOpen(b)} style={({ pressed }) => [W.teamCard, pressed && { opacity: .9 }]}>
        <View style={W.teamEmoji}><Text style={{ fontSize: 18 }}>{b.emoji || '🤖'}</Text></View>
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={W.teamName} numberOfLines={1}>{b.name}{b.paused ? ' ⏸' : ''}</Text>
          <Text style={W.teamJob} numberOfLines={1}>{b.job || 'No job yet'}</Text>
        </View>
        {s.chip ? <Text style={[W.chip, { color: s.chip[1], backgroundColor: s.chip[0].backgroundColor, fontSize: 10 }]}>{s.label}</Text> : null}
      </Pressable>
    );
  };
  return (
    <View style={W.screen}>
      <WebTopBar title="Home" showBack onBack={onBack} conn={conn} showSearch onSearch={onSearch}
        account={account} onAvatar={onAvatar} />
      <ScrollView contentContainerStyle={{ paddingBottom: 30 + (ins.bottom || 0) }} showsVerticalScrollIndicator={false}>
        <Text style={W.section}>Today</Text>
        <View style={W.banner}>
          <Text style={W.bannerB}>●</Text>
          <Text style={{ color: C.text, fontSize: 14, flex: 1 }} numberOfLines={2}>{headline || 'All quiet — no approvals waiting.'}</Text>
        </View>
        <View style={W.grid}>
          <View style={W.tile}>
            <Text style={W.tileCap}>Needs you</Text>
            <Text style={W.tileVal}>{(apps || []).length ? String((apps || []).length) : '0'}</Text>
            <Text style={W.tileSub}>open the inbox</Text>
          </View>
          <View style={W.tile}>
            <Text style={W.tileCap}>Working now</Text>
            <Text style={W.tileVal}>{String(workingBots.length)}</Text>
            <Text style={W.tileSub} numberOfLines={1}>{workingBots.length ? workingBots.map((b) => b.name).join(', ') : 'nobody is busy'}</Text>
          </View>
          <View style={[W.tile, { width: '100%' }]}>
            <Text style={W.tileCap}>Tokens this week</Text>
            <Text style={W.tileVal}>{tok ? Number(tok).toLocaleString() : '–'}</Text>
            <Text style={W.tileSub}>{limit ? Math.min(999, Math.round((Number(tok) || 0) / (Number(limit) || 1) * 100)) + '% of ' + Number(limit).toLocaleString() + ' limit' : 'no weekly limit'}</Text>
          </View>
        </View>
        <Text style={W.section}>Your team</Text>
        <View style={W.grid}>{(bots || []).filter((b) => !b.archived).map(teamCard)}</View>
        <Text style={W.section}>Needs you</Text>
        {(apps || []).filter((a) => a.status !== 'resolved').slice(0, 4).map((a) => (
          <Pressable key={a.id} onPress={() => onOpenThread(a.thread_id, a.title || (a.bot_name || 'Approval'), a.bot_id)} style={({ pressed }) => [W.appCard, pressed && { opacity: .9 }]}>
            <Text style={W.appSummary}>{a.summary || a.title}</Text>
            <Text style={W.appMeta}>{(a.bot_name || (bots || []).find((b) => b.id === a.bot_id)?.name || 'Bot')} · {a.category || a.tool || 'approval'}</Text>
          </Pressable>
        ))}
        {(apps || []).filter((a) => a.status !== 'resolved').length === 0 ? (
          <Text style={W.mutedC}>Nothing needs your attention. ✓</Text>
        ) : null}
        <Text style={W.section}>Recent activity</Text>
        {!acts.length ? <Text style={W.mutedC}>Nothing yet. Bot actions show up here as they happen.</Text> : (
          acts.map((a, i) => {
            const b2 = (bots || []).find((x) => x.id === a.bot_id) || {};
            const dst = dotColor(a.status);
            return (
              <View key={a.id || i} style={W.feedRow}>
                <View style={[W.feedDot, { backgroundColor: dst }]} />
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Text style={W.feedWho} numberOfLines={1}>{(b2.name || a.bot_name || 'Bot')} · {a.tool || a.category}</Text>
                  <Text style={W.feedWhen} numberOfLines={1}>{fmtTime(a.ts)}{a.url ? ' · ' + a.url : ''}{a.path ? ' · ' + a.path : ''}{a.summary ? ' · ' + a.summary : ''}</Text>
                </View>
              </View>
            );
          })
        )}
      </ScrollView>
    </View>
  );
}

// ---------------------------------------------------------------- inbox
export function InboxView({ bots, apps: appsProp, decide, onScreen, conn, account, onAvatar }) {
  const [tab, setTab] = useState('pend');
  const [rows, setRows] = useState(appsProp ? appsProp.slice() : null);
  const [handoffs, setHandoffs] = useState(null);
  const [appsAll, setAppsAll] = useState(null);
  const load = useCallback(async () => {
    try {
      const d = await api('/api/approvals?status=all&limit=200');
      setAppsAll(Array.isArray(d) ? d : (d.items || d.approvals || []));
    } catch (e) { setAppsAll([]); }
    try { const d = await api('/api/handoffs?status=all'); setHandoffs(Array.isArray(d) ? d : (d.handoffs || [])); } catch (e) { setHandoffs([]); }
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (appsProp) setRows(appsProp.slice()); }, [appsProp]);
  const botName = (id) => ((bots || []).find((b) => b.id === id) || {}).name || 'Bot';
  const pend = (appsAll || []).filter((a) => a.status === 'waiting_approval');
  const hist = (appsAll || []).slice(0, 60);
  return (
    <View style={W.screen}>
      <WebTopBar title="Inbox" showBack conn={conn} showSearch showNew={false}
        account={account} onAvatar={onAvatar} />
      <View style={{ flexDirection: 'row', borderBottomWidth: 1, borderColor: C.line }}>
        {[['pend', 'Needs you'], ['hist', 'History'], ['handoff', 'Handoffs']].map(([k, lbl]) => (
          <Pressable key={k} onPress={() => setTab(k)} style={[W.ibTab, tab === k && W.ibTabOn]}>
            <Text style={[W.ibTabT, tab === k && W.ibTabTOn]}>{lbl}</Text>
          </Pressable>
        ))}
      </View>
      <ScrollView contentContainerStyle={{ paddingBottom: 30 }} showsVerticalScrollIndicator={false}>
        {tab === 'pend' ? (
          pend === null ? <Text style={W.mutedC}>Loading…</Text> : !pend.length ? (
            <Text style={W.mutedC}>Nothing waiting — all clear.</Text>
          ) : pend.map((a) => (
            <View key={a.id} style={W.appCard}>
              <Text style={W.appSummary}>{a.summary || a.title}</Text>
              <Text style={W.appMeta}>{botName(a.bot_id)} · {a.category || a.tool || 'approval'}</Text>
              <View style={{ flexDirection: 'row', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
                <Pressable onPress={() => decide(a, true)} style={[W.abtn, W.abtnP]}>
                  <Text style={[W.abtnT, W.abtnPT]}>Approve</Text>
                </Pressable>
                <Pressable onPress={() => decide(a, false)} style={W.abtn}>
                  <Text style={W.abtnT}>Deny</Text>
                </Pressable>
                {!!a.bot_id && (
                  <Pressable onPress={() => onScreen(a.bot_id)} style={W.abtn}>
                    <Text style={W.abtnT}>Browser</Text>
                  </Pressable>
                )}
              </View>
            </View>
          ))
        ) : null}
        {tab === 'hist' ? (
          !hist.length ? <Text style={W.mutedC}>No history yet.</Text> : hist.map((a, i) => (
            <View key={a.id || i} style={[W.feedRow, { alignItems: 'flex-start' }]}>
              <View style={[W.feedDot, { backgroundColor: dotColor(a.status), marginTop: 9 }]} />
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={{ color: C.text, fontSize: 12, fontWeight: '600' }} numberOfLines={1}>{botName(a.bot_id)}</Text>
                <Text style={W.feedWhen} numberOfLines={2}>{(a.category || '').toUpperCase()}{a.tool ? ' · ' + a.tool : ''}{a.summary ? ' · ' + a.summary : ''}</Text>
              </View>
            </View>
          ))
        ) : null}
        {tab === 'handoff' ? (
          handoffs === null ? <Text style={W.mutedC}>Loading…</Text> : !handoffs.length ? (
            <Text style={W.mutedC}>No handoffs yet.</Text>
          ) : handoffs.slice(0, 40).map((h, i) => (
            <View key={h.id || i} style={[W.feedRow, { alignItems: 'flex-start' }]}>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={{ color: C.text, fontSize: 12, fontWeight: '600' }} numberOfLines={1}>{h.from_name || h.from} → {h.to_name || h.to}</Text>
                <Text style={W.feedWhen} numberOfLines={2}>{h.title || ''}{h.brief ? ' · ' + h.brief : ''}</Text>
              </View>
            </View>
          ))
        ) : null}
      </ScrollView>
    </View>
  );
}

// ---------------------------------------------------------------- computer
export function ComputerView({ bots, busy, conn, account, onAvatar, onScreen }) {
  const [st, setSt] = useState(null);
  const [cmd, setCmd] = useState('');
  const [busyT, setBusyT] = useState(false);
  const load = useCallback(async () => {
    try { setSt(await api('/api/computer')); } catch (e) { setSt({ error: String(e.message || e) }); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const recover = async () => {
    try { await api('/api/computer/restart', { method: 'POST', body: '{}' }); Alert.alert('Recovered', 'Browser restarted.'); load(); }
    catch (e) { Alert.alert('Could not recover', String(e.message || e)); }
  };
  const reset = () => {
    Alert.alert('Reset computer', 'Erase the workspace and restart?', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Reset', style: 'destructive', onPress: async () => {
        try { await api('/api/computer/reset', { method: 'POST', body: '{}' }); Alert.alert('Reset done.'); load(); }
        catch (e) { Alert.alert('Could not reset', String(e.message || e)); }
      } },
    ]);
  };
  const runCmd = async () => {
    const c = (cmd || '').trim();
    if (!c) return;
    setBusyT(true);
    try {
      const d = await api('/api/computer/terminal', { method: 'POST', body: JSON.stringify({ command: c, timeout: 60 }) });
      Alert.alert('Exit ' + (d && d.exit != null ? d.exit : '?') + (d && d.sandbox ? ' · sandboxed' : ''), String((d && d.output) || '').slice(0, 300));
      setCmd('');
      load();
    } catch (e) { Alert.alert('Could not run', String(e.message || e)); }
    setBusyT(false);
  };
  const term = (st && Array.isArray(st.terminal) ? st.terminal : []).slice(-8).reverse();
  return (
    <View style={W.screen}>
      <WebTopBar title="Computer" showBack conn={conn} account={account} onAvatar={onAvatar} />
      <ScrollView contentContainerStyle={{ paddingBottom: 30 }} showsVerticalScrollIndicator={false}>
        <View style={{ flexDirection: 'row', gap: 8, paddingHorizontal: 14, paddingTop: 10 }}>
          <Pressable onPress={recover} style={W.pillBtn}><Text style={W.pillBtnT}>Recover browser</Text></Pressable>
          <Pressable onPress={reset} style={[W.pillBtn, { backgroundColor: C.badSoft }]}>
            <Text style={[W.pillBtnT, { color: C.bad }]}>Reset computer</Text>
          </Pressable>
        </View>
        {!!(st && st.error) && <Text style={[W.mutedC, { color: C.bad }]}>{st.error}</Text>}
        <Text style={W.section}>Bot screens</Text>
        <View style={[W.grid, { marginBottom: 4 }]}>
          {(bots || []).filter((b) => !b.archived).map((b) => {
            const info = st && st.browser && st.browser[b.id];
            const open = !!(info && info.open);
            const s = stateOf(busy, b.id);
            const chipLabel = s.key === 'work' ? 'working' : open ? 'no screen yet' : 'no screen yet';
            return (
              <View key={b.id} style={W.cpCard}>
                <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center', marginBottom: 8 }}>
                  <Text style={{ fontSize: 16 }}>{b.emoji || '🤖'}</Text>
                  <Text style={{ color: C.text, fontWeight: '600', fontSize: 13, flex: 1 }} numberOfLines={1}>{b.name}</Text>
                  {s.key === 'work'
                    ? <Text style={[W.chip, { color: C.work, backgroundColor: '#e7f0ff' }]}>working</Text>
                    : <Text style={W.chip}>no screen yet</Text>}
                </View>
                <View style={W.cpThumb}>
                  {open ? (
                    <Image source={{ uri: imgUrl('/api/bots/' + b.id + '/screen.jpg?q=40'), width: '100%', height: 100 }} style={{ width: '100%', height: 100 }} resizeMode="cover" />
                  ) : <Text style={W.cpNo}>No browser tab open yet</Text>}
                </View>
                <View style={{ flexDirection: 'row', gap: 8 }}>
                  <Pressable onPress={() => onScreen(b.id, true)} style={[W.wideBlack, { flex: 1, paddingVertical: 9 }]}>
                    <Text style={{ color: '#fff', fontWeight: '700', fontSize: 12.5 }}>Take over</Text>
                  </Pressable>
                  <Pressable onPress={() => onScreen(b.id, false)} style={[W.pillBtn, { flex: 1 }]}>
                    <Text style={W.pillBtnT}>Follow</Text>
                  </Pressable>
                </View>
              </View>
            );
          })}
        </View>
        {(bots || []).length === 0 ? <Text style={W.mutedC}>Create a Bot to give it a screen.</Text> : null}
        <Text style={W.section}>Terminal</Text>
        <View style={{ flexDirection: 'row', gap: 8, paddingHorizontal: 14, alignItems: 'center' }}>
          <TextInput
            style={[W.input, { flex: 1, fontFamily: 'monospace', paddingVertical: 9 }]}
            value={cmd}
            onChangeText={setCmd}
            placeholder="Run a command in the workspace…"
            placeholderTextColor={C.muted}
            autoCapitalize="none"
            autoCorrect={false}
            onSubmitEditing={runCmd}
          />
          <Pressable onPress={runCmd} disabled={busyT} style={[W.wideBlack, { paddingVertical: 10, opacity: busyT ? .5 : 1 }]}>
            <Text style={{ color: '#fff', fontWeight: '800', fontSize: 13 }}>{busyT ? '…' : 'Run'}</Text>
          </Pressable>
        </View>
        <View style={{ paddingHorizontal: 14, marginTop: 10 }}>
          {!term.length ? <Text style={W.mutedC}>No terminal history.</Text> : term.map((t, i) => (
            <View key={i} style={{ paddingVertical: 8, borderBottomWidth: 1, borderColor: C.line }}>
              <Text style={W.mono} numberOfLines={1}>[{t.who || 'bot'}] {t.cwd || '~'}&gt; {t.command}</Text>
              {!!t.output && <Text style={[W.mono, { color: C.muted, marginTop: 2 }]}>{String(t.output).slice(0, 400)}</Text>}
              <Text style={{ color: C.muted, fontSize: 11, marginTop: 2 }}>exit {t.exit}{t.sandbox === false ? ' · no sandbox' : ' · sandboxed'}</Text>
            </View>
          ))}
        </View>
      </ScrollView>
    </View>
  );
}

// ---------------------------------------------------------------- screen takeover
export function ScreenView({ bot, onClose, onHandback }) {
  const id = bot.id;
  const [url, setUrl] = useState('');
  const [typeText, setTypeText] = useState('');
  const [frame, setFrame] = useState(Date.now());
  const [failed, setFailed] = useState(0);
  const [dims, setDims] = useState({ w: 0, h: 0 });
  const timer = useRef(null);
  const post = async (body) => {
    try { await api('/api/bots/' + id + '/screen/input', { method: 'POST', body: JSON.stringify(body) }); }
    catch (e) { /* ignore */ }
  };
  useEffect(() => {
    api('/api/bots/' + id + '/takeover', { method: 'POST', body: JSON.stringify({ action: 'start' }) }).catch(() => {});
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [id]);
  const nextFrame = (ms) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setFrame(Date.now()), ms);
  };
  const tap = (evt) => {
    const { w, h } = dims || {};
    if (!w || !h) return;
    const { locationX, locationY } = evt.nativeEvent;
    const x = Math.round((locationX / w) * 1280);
    const y = Math.round((locationY / h) * 800);
    post({ type: 'click', x, y });
    nextFrame(350);
  };
  return (
    <View style={W.screen}>
      <View style={W.screenBar}>
        <Pressable onPress={onClose} style={W.pillBtn}><Text style={W.pillBtnT}>‹ Back</Text></Pressable>
        <Pressable onPress={() => { post({ type: 'reload' }); nextFrame(600); }} style={W.pillBtn}><Text style={W.pillBtnT}>⟳</Text></Pressable>
        <TextInput
          style={W.pillInput}
          value={url}
          onChangeText={setUrl}
          placeholder="Go to URL"
          placeholderTextColor={C.muted}
          autoCapitalize="none"
          autoCorrect={false}
          onSubmitEditing={() => { post({ type: 'navigate', url }); nextFrame(800); }}
        />
        <Pressable onPress={() => { post({ type: 'navigate', url }); nextFrame(800); }} style={W.pillBtn}><Text style={W.pillBtnT}>Go</Text></Pressable>
      </View>
      <View style={W.screenWrap}>
        <Pressable onPress={tap} onLayout={(e) => {
          const { width, height } = e.nativeEvent.layout;
          setDims({ w: width, h: height });
        }} style={{ width: '100%', aspectRatio: 1.6 }}>
          <Image
            source={{ uri: imgUrl('/api/bots/' + id + '/screen.jpg?q=55&t=' + frame), cache: 'reload' }}
            style={{ width: '100%', height: '100%', aspectRatio: 1.6 }}
            resizeMode="contain"
            onLoad={() => nextFrame(700)}
            onError={() => { setFailed((f) => f + 1); nextFrame(2500); }}
          />
        </Pressable>
        {failed > 2 ? null : null}
      </View>
      <View style={W.screenBar}>
        <TextInput
          style={W.pillInput}
          value={typeText}
          onChangeText={setTypeText}
          placeholder="Type into the page…"
          placeholderTextColor={C.muted}
          autoCapitalize="none"
          autoCorrect={false}
        />
        <Pressable onPress={() => { post({ type: 'text', text: typeText }); setTypeText(''); nextFrame(400); }} style={W.pillBtn}><Text style={W.pillBtnT}>Type</Text></Pressable>
        <Pressable onPress={() => { post({ type: 'key', key: 'Enter' }); nextFrame(400); }} style={W.pillBtn}><Text style={W.pillBtnT}>⏎</Text></Pressable>
        <Pressable onPress={() => { post({ type: 'scroll', dy: -500 }); nextFrame(400); }} style={W.pillBtn}><Text style={W.pillBtnT}>▲</Text></Pressable>
        <Pressable onPress={() => { post({ type: 'scroll', dy: 500 }); nextFrame(400); }} style={W.pillBtn}><Text style={W.pillBtnT}>▼</Text></Pressable>
      </View>
      <Pressable onPress={async () => {
        try { await api('/api/bots/' + id + '/takeover', { method: 'POST', body: JSON.stringify({ action: 'handback' }) }); } catch (e) { /* ignore */ }
        Alert.alert('Handed back', 'The Bot will continue.');
        onHandback && onHandback();
      }} style={[W.wideBlack, W.wide]}>
        <Text style={W.wideBlackT}>Hand back to the Bot</Text>
      </Pressable>
    </View>
  );
}

// ---------------------------------------------------------------- skills
export function SkillsView({ bots, conn, account, onAvatar, onOpenThread }) {
  const [rows, setRows] = useState(null);
  const [sel, setSel] = useState(null);            // {name, raw, status}
  const [showRecs, setShowRecs] = useState(false);
  const [recs, setRecs] = useState(null);
  const [newOpen, setNewOpen] = useState(false);
  const [nm, setNm] = useState(''); const [desc, setDesc] = useState(''); const [steps, setSteps] = useState('');
  const load = useCallback(async () => {
    try { const d = await api('/api/skills'); setRows(Array.isArray(d) ? d : (d.skills || d.items || [])); }
    catch (e) { setRows([]); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const openSkill = async (s) => {
    try {
      const d = await api('/api/skills/' + encodeURIComponent(s.name));
      setSel({ name: s.name, raw: (d && d.raw) || '', status: (d && d.status) || s.status || '' });
    } catch (e) { setSel({ name: s.name, raw: '', status: s.status || '' }); }
  };
  const save = async () => {
    try { await api('/api/skills/' + encodeURIComponent(sel.name), { method: 'PUT', body: JSON.stringify({ raw: sel.raw }) }); Alert.alert('Saved'); load(); }
    catch (e) { Alert.alert('Could not save', String(e.message || e)); }
  };
  const setStatus = async () => {
    const next = sel.status === 'active' ? 'draft' : 'active';
    try {
      await api('/api/skills/' + encodeURIComponent(sel.name) + '/status', { method: 'POST', body: JSON.stringify({ status: next }) });
      setSel({ ...sel, status: next }); load();
    } catch (e) { Alert.alert('Could not set status', String(e.message || e)); }
  };
  const testSkill = async () => {
    const b = (bots || [])[0];
    if (!b) { Alert.alert('Create a Bot first'); return; }
    try {
      const d = await api('/api/skills/' + encodeURIComponent(sel.name) + '/test', { method: 'POST', body: JSON.stringify({ bot_id: b.id, dry_run: true }) });
      const th = d && d.thread;
      if (th && th.id) { setSel(null); onOpenThread && onOpenThread(th.id, 'Skill test', th.bot_id || b.id); }
      else Alert.alert('Test started');
    } catch (e) { Alert.alert('Test failed', String(e.message || e)); }
  };
  const del = () => {
    Alert.alert('Delete skill?', sel.name + ' will be removed.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete', style: 'destructive', onPress: async () => {
        try { await api('/api/skills/' + encodeURIComponent(sel.name), { method: 'DELETE' }); setSel(null); load(); }
        catch (e) { Alert.alert('Could not delete', String(e.message || e)); }
      } },
    ]);
  };
  const loadRecs = async () => {
    setShowRecs(true);
    try { const d = await api('/api/recordings'); setRecs(Array.isArray(d) ? d : (d.recordings || d.items || [])); } catch (e) { setRecs([]); }
  };
  const create = async () => {
    const name = nm.trim().toLowerCase().replace(/[^a-z0-9-_]/g, '-').replace(/^-+|-+$/g, '');
    if (!name) { Alert.alert('Name is required'); return; }
    const raw = '---\nname: ' + name + '\ndescription: ' + (desc.trim() || name) + '\nstatus: draft\n---\n\n' + (steps.trim() || '') + '\n';
    try { await api('/api/skills/' + name, { method: 'PUT', body: JSON.stringify({ raw }) }); setNewOpen(false); setNm(''); setDesc(''); setSteps(''); load(); }
    catch (e) { Alert.alert('Could not create', String(e.message || e)); }
  };
  if (sel) {
    return (
      <View style={W.screen}>
        <WebTopBar title={sel.name} showBack onBack={() => setSel(null)} conn={conn} account={account} onAvatar={onAvatar} />
        <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: 14, gap: 10 }} showsVerticalScrollIndicator={false}>
          <TextInput
            style={[W.input, { fontFamily: 'monospace', minHeight: 320, textAlignVertical: 'top' }]}
            value={sel.raw}
            onChangeText={(t) => setSel({ ...sel, raw: t })}
            multiline
            autoCapitalize="none"
            autoCorrect={false}
          />
          <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
            <Pressable onPress={save} style={[W.wideBlack, { flex: 1 }]}><Text style={{ color: '#fff', fontWeight: '800' }}>Save</Text></Pressable>
            <Pressable onPress={setStatus} style={W.pillBtn}><Text style={W.pillBtnT}>{sel.status === 'active' ? 'Set draft' : 'Activate'}</Text></Pressable>
            <Pressable onPress={testSkill} style={W.pillBtn}><Text style={W.pillBtnT}>Test</Text></Pressable>
            <Pressable onPress={del} style={[W.pillBtn, { backgroundColor: C.badSoft }]}><Text style={[W.pillBtnT, { color: C.bad }]}>Delete</Text></Pressable>
            <Pressable onPress={() => setSel(null)} style={W.pillBtn}><Text style={W.pillBtnT}>Close</Text></Pressable>
          </View>
        </ScrollView>
      </View>
    );
  }
  return (
    <View style={W.screen}>
      <WebTopBar title="Skills" showBack conn={conn} account={account} onAvatar={onAvatar} />
      <View style={{ flexDirection: 'row', gap: 8, paddingHorizontal: 14, paddingTop: 10, paddingBottom: 4 }}>
        <Pressable onPress={() => setNewOpen(true)} style={W.pillBtn}><Text style={W.pillBtnT}>＋ New skill</Text></Pressable>
        <Pressable onPress={loadRecs} style={W.pillBtn}><Text style={W.pillBtnT}>Recordings</Text></Pressable>
      </View>
      {!showRecs ? (
        <FlatList
          data={rows || []}
          keyExtractor={(s, i) => s.name || String(i)}
          contentContainerStyle={{ paddingBottom: 30 }}
          renderItem={({ item: s }) => (
            <Pressable onPress={() => openSkill(s)} style={({ pressed }) => [W.grow, pressed && { backgroundColor: C.panel }]}>
              <View style={[W.feedDot, { backgroundColor: s.status === 'draft' ? C.warn : C.ok, marginTop: 8 }]} />
              <Text style={[W.gName, { flex: 1 }]} numberOfLines={1}>{s.name}</Text>
              <Text style={{ color: C.muted, fontSize: 11 }}>{[s.bot, s.status].filter(Boolean).join(' · ')}</Text>
            </Pressable>
          )}
          ListEmptyComponent={<Text style={W.mutedC}>No skills yet — tap "New skill".</Text>}
        />
      ) : (
        <FlatList
          data={recs === null ? [] : recs}
          keyExtractor={(s, i) => (s.file || s.name || 'r') + i}
          contentContainerStyle={{ paddingBottom: 30 }}
          renderItem={({ item: s }) => (
            <View style={W.grow}>
              <Text style={[W.gName, { flex: 1 }]} numberOfLines={1}>{s.name || s.file || 'Recording'}</Text>
              <Text style={{ color: C.muted, fontSize: 11 }}>{s.description || s.bot || ''}</Text>
            </View>
          )}
          ListHeaderComponent={recs === null ? <Text style={W.mutedC}>Loading…</Text> : null}
          ListEmptyComponent={<Text style={W.mutedC}>No recordings yet.</Text>}
        />
      )}
      {newOpen && (
        <Modal transparent visible animationType="slide" onRequestClose={() => setNewOpen(false)}>
          <View style={{ flex: 1, backgroundColor: 'rgba(17,17,17,.32)', justifyContent: 'flex-end' }}>
            <View style={[W.sheet, { paddingBottom: 30 }]}>
              <View style={W.grip} />
              <View style={{ padding: 16, gap: 10 }}>
                <Text style={{ color: C.text, fontSize: 16, fontWeight: '800' }}>New skill</Text>
                <TextInput style={W.input} placeholder="Name (letters, dashes, numbers)" placeholderTextColor={C.muted} autoCapitalize="none" value={nm} onChangeText={setNm} />
                <TextInput style={W.input} placeholder="What it does" placeholderTextColor={C.muted} value={desc} onChangeText={setDesc} />
                <TextInput style={[W.input, { minHeight: 120, textAlignVertical: 'top' }]} placeholder="Steps…" placeholderTextColor={C.muted} value={steps} onChangeText={setSteps} multiline />
                <View style={{ flexDirection: 'row', gap: 10 }}>
                  <Pressable onPress={() => setNewOpen(false)} style={[W.pillBtn, { flex: 1 }]}><Text style={W.pillBtnT}>Cancel</Text></Pressable>
                  <Pressable onPress={create} style={[W.wideBlack, { flex: 1 }]}><Text style={{ color: '#fff', fontWeight: '800' }}>Create</Text></Pressable>
                </View>
              </View>
            </View>
          </View>
        </Modal>
      )}
    </View>
  );
}

// ---------------------------------------------------------------- usage
export function UsageView({ conn, account, onAvatar }) {
  const [us, setUs] = useState(null);
  useEffect(() => { (async () => {
    try { setUs(await api('/api/usage')); } catch (e) { setUs({}); }
  })(); }, []);
  const bm = (us && us.by_model) || (us && us.models) || {};
  const bmList = Array.isArray(bm) ? bm : Object.entries(bm).map(([model, v]) => ({ model, cost: v.cost, tokens: v.tokens }));
  const Tile = ({ cap, val }) => (
    <View style={W.card}>
      <Text style={W.tileCap}>{cap}</Text>
      <Text style={{ color: C.text, fontSize: 24, fontWeight: '800' }}>{val}</Text>
    </View>
  );
  return (
    <View style={W.screen}>
      <WebTopBar title="Usage" showBack conn={conn} account={account} onAvatar={onAvatar} />
      <ScrollView contentContainerStyle={{ paddingBottom: 30, paddingTop: 10 }} showsVerticalScrollIndicator={false}>
        {!us ? <Text style={W.mutedC}>Loading…</Text> : (
          <>
            <Tile cap="Total cost" val={us.total_cost != null ? '$' + Number(us.total_cost).toFixed(2) : '–'} />
            <Tile cap="Tokens" val={(us.total_tokens || us.total) ? Number(us.total_tokens || us.total).toLocaleString() : '–'} />
            <Tile cap="Requests" val={us.total_requests != null ? Number(us.total_requests).toLocaleString() : '–'} />
            <Tile cap="Models" val={String(Object.keys(bm).length || 0)} />
            <Text style={W.section}>By model</Text>
            {!bmList.length ? <Text style={W.mutedC}>No usage yet.</Text> : bmList.map((m, i) => (
              <View key={m.model || i} style={[W.feedRow, { alignItems: 'center' }]}>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Text style={{ color: C.text, fontSize: 13, fontWeight: '600' }} numberOfLines={1}>{m.model || '—'}</Text>
                </View>
                <Text style={{ color: C.muted, fontSize: 12 }}>{m.cost != null ? '$' + Number(m.cost).toFixed(2) : ''}{m.tokens ? ' · ' + Number(m.tokens).toLocaleString() + ' tok' : ''}</Text>
              </View>
            ))}
          </>
        )}
      </ScrollView>
    </View>
  );
}

// ---------------------------------------------------------------- action log
export function ActionLogView({ bots, conn, account, onAvatar }) {
  const [rows, setRows] = useState(null);
  useEffect(() => { (async () => {
    try {
      const d = await api('/api/actions?limit=120');
      setRows(Array.isArray(d) ? d : (d.actions || d.items || []));
    } catch (e) { setRows([]); }
  })(); }, []);
  const botName = (id) => ((bots || []).find((b) => b.id === id) || {}).name || '';
  return (
    <View style={W.screen}>
      <WebTopBar title="Action log" showBack conn={conn} account={account} onAvatar={onAvatar} />
      <FlatList
        data={rows === null ? [] : rows}
        keyExtractor={(a, i) => (a.id || 'a') + i}
        contentContainerStyle={{ paddingBottom: 30 }}
        renderItem={({ item: a }) => (
          <View style={[W.feedRow, { alignItems: 'flex-start' }]}>
            <View style={[W.feedDot, { backgroundColor: dotColor(a.status), marginTop: 9 }]} />
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={{ color: C.text, fontSize: 12, fontWeight: '600' }} numberOfLines={1}>{(botName(a.bot_id) || a.bot_name || 'Bot').toUpperCase()} · {(a.tool || 'ACTION').toUpperCase()}</Text>
              <Text style={W.feedWhen} numberOfLines={2}>{a.url || a.path || a.summary || a.title || ''}</Text>
              <Text style={W.feedWhen}>{fmtTime(a.ts)}</Text>
            </View>
          </View>
        )}
        ListEmptyComponent={<Text style={W.mutedC}>{rows === null ? 'Loading…' : 'Nothing yet. Bot actions show up here as they happen.'}</Text>}
      />
    </View>
  );
}

// ---------------------------------------------------------------- search palette
export function SearchPalette({ onClose, onOpenBot, onOpenThread, onGoView }) {
  const [q, setQ] = useState('');
  const [res, setRes] = useState(null);
  const ins = useSafeAreaInsets();
  useEffect(() => {
    const t = setTimeout(async () => {
      const s = q.trim();
      if (!s) { setRes(null); return; }
      try {
        const d = await api('/api/palette?q=' + encodeURIComponent(s) + '&limit=8');
        setRes(d);
      } catch (e) { setRes({ error: String(e.message || e) }); }
    }, 180);
    return () => clearTimeout(t);
  }, [q]);
  const botRow = (b) => (
    <Pressable key={'b' + b.id} onPress={() => { onClose(); onOpenBot(b); }} style={[W.tabRow, { paddingVertical: 9, paddingHorizontal: 16 }]}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, flex: 1, minWidth: 0 }}>
        <Avatar bot={b} size={34} />
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={{ color: C.text, fontSize: 14, fontWeight: '600' }} numberOfLines={1}>{b.name}</Text>
          <Text style={W.gSub} numberOfLines={1}>{b.job || 'Bot'}</Text>
        </View>
      </View>
    </Pressable>
  );
  const sec = (label, rows) => (!rows || !rows.length) ? null : (
    <View key={label}>
      <Text style={{ color: C.muted, fontSize: 12, fontWeight: '600', paddingHorizontal: 16, paddingTop: 12, paddingBottom: 4 }}>{label}</Text>
      {rows}
    </View>
  );
  return (
    <Modal transparent visible onRequestClose={onClose} animationType="fade">
      <View style={{ flex: 1, backgroundColor: 'rgba(17,17,17,.32)' }}>
        <Pressable style={{ flex: 1 }} onPress={onClose} />
        <View style={{ backgroundColor: C.bg, marginHorizontal: 10, marginBottom: 200, borderRadius: 20, paddingTop: (ins.top || 0) + 8, overflow: 'hidden', maxHeight: '70%' }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 14, paddingBottom: 10 }}>
            <Text style={{ fontSize: 18, color: C.muted }}>⌕</Text>
            <TextInput
              style={[W.input, { flex: 1, borderRadius: 999, paddingVertical: 9 }]}
              placeholder="Search Bots, chats, files, routines…"
              placeholderTextColor={C.muted}
              autoFocus
              value={q}
              onChangeText={setQ}
            />
            <Pressable onPress={onClose} hitSlop={6}><Text style={{ color: C.muted, fontSize: 14, fontWeight: '700' }}>✕</Text></Pressable>
          </View>
          <ScrollView showsVerticalScrollIndicator={false} style={{ maxHeight: '100%' }} contentContainerStyle={{ paddingBottom: 12 }}>
            {!q.trim() ? <Text style={W.mutedC}>Type to search everything.</Text> : res === null ? (
              <View style={{ padding: 16 }}><ActivityIndicator /></View>
            ) : (
              <>
                {sec('Bots', (res.bots || []).map(botRow))}
                {sec('Group chats', (res.groups || []).map((g) => (
                  <Pressable key={'g' + g.id} onPress={() => { onClose(); onOpenThread(g.thread_id || g.id, g.name, null); }} style={[W.tabRow, { paddingVertical: 9, paddingHorizontal: 16 }]}>
                    <Text style={{ fontSize: 18, width: 34, textAlign: 'center' }}>👥</Text>
                    <Text style={{ color: C.text, flex: 1, fontWeight: '600' }} numberOfLines={1}>{g.name}</Text>
                  </Pressable>
                )))}
                {sec('Messages', (res.messages || []).map((m) => (
                  <Pressable key={'m' + (m.thread_id || m.id)} onPress={() => { onClose(); onOpenThread(m.thread_id, m.where || m.title || 'Chat', m.bot_id); }} style={[W.tabRow, { paddingVertical: 9, paddingHorizontal: 16 }]}>
                    <Text style={{ fontSize: 16, width: 34, textAlign: 'center' }}>💬</Text>
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <Text style={{ color: C.text, fontWeight: '600', fontSize: 13.5 }} numberOfLines={1}>{m.where || m.title}</Text>
                      <Text style={W.gSub} numberOfLines={1}>{m.payload || m.snippet}</Text>
                    </View>
                  </Pressable>
                )))}
                {sec('Files', (res.files || []).map((f) => (
                  <Pressable key={'f' + f.path} onPress={() => { onClose(); onGoView('files'); }} style={[W.tabRow, { paddingVertical: 9, paddingHorizontal: 16 }]}>
                    <Text style={{ fontSize: 16, width: 34, textAlign: 'center' }}>{f.dir ? '📁' : '📄'}</Text>
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <Text style={{ color: C.text, fontWeight: '600', fontSize: 13.5 }} numberOfLines={1}>{f.name}</Text>
                      <Text style={W.gSub} numberOfLines={1}>{f.path}</Text>
                    </View>
                  </Pressable>
                )))}
                {sec('Routines', (res.routines || []).map((r) => (
                  <Pressable key={'r' + r.id} onPress={() => { onClose(); onGoView('routines'); }} style={[W.tabRow, { paddingVertical: 9, paddingHorizontal: 16 }]}>
                    <Text style={{ fontSize: 16, width: 34, textAlign: 'center' }}>🔁</Text>
                    <Text style={{ color: C.text, flex: 1, fontWeight: '600' }} numberOfLines={1}>{r.name}</Text>
                  </Pressable>
                )))}
                {!res.error && !res.bots && !res.groups && !res.messages && !res.files && !res.routines ? (
                  <Text style={W.mutedC}>Nothing matches "{q}".</Text>
                ) : null}
              </>
            )}
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

// ---------------------------------------------------------------- plan (billing)
const PLAN_LABEL = { free: 'Free', starter: 'Starter $2/mo', pro: 'Pro $5/mo', team: 'Team $10/mo', business: 'Business $20/mo' };
const PLAN_DESC = {
  free: '3 bots · 50K tokens/week · community support',
  starter: '10 bots · 200K tokens/week · email support · Google sign-in',
  pro: '50 bots · 1M tokens/week · priority email · API access · routines',
  team: '100 bots · 5M tokens/week · 5 team members · Discord support',
  business: 'Unlimited bots · 20M tokens/week · unlimited team · phone support',
};

export function PlanScreen({ me: meProp, onUpgrade }) {
  const [plan, setPlan] = useState(meProp.plan || 'free');
  const limits = auth_mod_plan_limits(plan);
  const saving = useRef(false);
  const currentPrice = auth_mod_plan_limits(meProp.plan || 'free').price_usd || 0;
  const canUpgradeCurrent = (auth_mod_plan_limits(plan).price_usd || 0) > currentPrice;
  const doUpgrade = async () => {
    if (saving.current) return;
    saving.current = true;
    try {
      await onUpgrade(plan);
      setPlan(plan);
      Alert.alert('Plan updated', 'Your plan is now ' + PLAN_LABEL[plan] + '.');
    } catch (e) {
      Alert.alert('Upgrade failed', String(e.message || e));
    } finally { saving.current = false; }
  };
  return (
    <View style={W.screen}>
      <WebTopBar title="Plan & billing" showBack conn={false} account={meProp.email} onAvatar={() => {}} />
      <ScrollView contentContainerStyle={{ paddingBottom: 30, paddingTop: 10 }} showsVerticalScrollIndicator={false}>
        <View style={{ paddingHorizontal: 16 }}>
          <Text style={{ color: C.muted, fontSize: 12, fontWeight: '600', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10 }}>Current plan</Text>
          <View style={[W.card, { flexDirection: 'row', alignItems: 'center', gap: 12, padding: 14, marginBottom: 16, borderLeftWidth: 4, borderColor: C.ok }]}>
            <Text style={{ color: C.text, fontSize: 17, fontWeight: '800' }}>{PLAN_LABEL[plan]}</Text>
            <Text style={{ color: C.muted, fontSize: 12 }}>{limits.price_usd === 0 ? 'Free forever' : '$' + limits.price_usd + '/' + limits.interval}</Text>
          </View>
          <Text style={{ color: C.muted, fontSize: 12, fontWeight: '600', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10 }}>Upgrade to</Text>
          {Object.entries(auth_mod_all_plans()).map(([key, p]) => {
            const isCurrent = key === plan;
            const canUpgrade = p.price_usd > (limits.price_usd || 0);
            return (
              <Pressable key={key} onPress={isCurrent ? undefined : () => setPlan(key)} disabled={isCurrent || !canUpgrade}
                style={[W.card, { flexDirection: 'row', alignItems: 'center', gap: 12, padding: 14, marginBottom: 10, opacity: (!canUpgrade && !isCurrent) ? 0.55 : 1, borderWidth: isCurrent ? 2 : 0, borderColor: isCurrent ? C.ok : 'transparent' }]}>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Text style={{ color: C.text, fontSize: 15, fontWeight: '700' }}>{PLAN_LABEL[key]}</Text>
                  <Text style={W.gSub} numberOfLines={2}>{PLAN_DESC[key]}</Text>
                </View>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                  {isCurrent ? <Text style={{ color: C.ok, fontWeight: '800' }}>Current</Text> :
                   !canUpgrade ? <Text style={{ color: C.muted, fontSize: 11 }}>Included</Text> :
                   <Text style={{ color: C.ok, fontWeight: '700' }}>${'$' + p.price_usd + '/mo'}</Text>}
                </View>
                {!isCurrent && canUpgrade ? <Text style={{ color: C.muted, fontSize: 11 }}>▸</Text> : null}
              </Pressable>
            );
          })}
          <View style={{ marginTop: 14, paddingHorizontal: 4 }}>
            <Pressable onPress={doUpgrade} disabled={saving.current || !canUpgradeCurrent()}
              style={[W.wideBlack, { marginTop: 8, opacity: (saving.current || !canUpgradeCurrent()) ? 0.5 : 1 }]}>
              <Text style={{ color: '#fff', fontWeight: '800', fontSize: 15 }}>
                {saving.current ? 'Updating…' : 'Switch to ' + PLAN_LABEL[plan]}
              </Text>
            </Pressable>
          </View>
          <View style={{ marginTop: 12, alignItems: 'center' }}>
            <Pressable onPress={() => Alert.alert('Billing', 'Upgrades are handled by the server admin. Pick a plan above and tap Switch — your server owner will activate it (Stripe checkout comes next).')}>
              <Text style={{ color: C.muted, fontSize: 12, fontWeight: '600' }}>How paid plans work</Text>
            </Pressable>
          </View>
        </View>
      </ScrollView>
    </View>
  );
}

// ---------------------------------------------------------------- admin: stats
export function AdminStatsScreen({ stats, onRefresh }) {
  const [d, setD] = useState(stats || null);
  const [loading, setLoading] = useState(!d);
  useEffect(() => { (async () => {
    if (loading) {
      try { setD(await api('/api/admin/stats')); } catch (e) { setD({}); }
      finally { setLoading(false); }
    } else if (onRefresh) { onRefresh(); }
  })(); }, []);
  const big = (cap, val, sub) => (
    <View style={[W.card, { flex: 1, minWidth: 0, padding: 14, gap: 6 }]}>
      <Text style={W.tileCap}>{cap}</Text>
      <Text style={{ color: C.text, fontSize: 26, fontWeight: '800' }}>{Number(val || 0).toLocaleString()}</Text>
      {sub && <Text style={W.gSub}>{sub}</Text>}
    </View>
  );
  return (
    <View style={W.screen}>
      <WebTopBar title="Admin · stats" showBack conn={false} account="admin" onAvatar={() => {}} />
      <ScrollView contentContainerStyle={{ paddingBottom: 30, paddingTop: 10 }} showsVerticalScrollIndicator={false}>
        <View style={{ paddingHorizontal: 16 }}>
          {loading ? <View style={{ padding: 20 }}><ActivityIndicator /></View> : (
            <>
              <View style={{ flexDirection: 'row', gap: 10, marginBottom: 16 }}>
                {big('Accounts', d.accounts, 'signed up')}
                {big('Bots', d.bots, 'across all users')}
                {big('Threads', d.threads, 'conversations')}
                {big('Tokens this week', d.week_tokens, d.today_tokens != null ? ('· ' + Number(d.today_tokens).toLocaleString() + ' today') : '')}
              </View>
              <Text style={W.section}>Server</Text>
              <View style={[W.card, { padding: 14, marginBottom: 10 }]}>
                <Text style={{ color: C.text, fontSize: 13, fontWeight: '600' }}>Version</Text>
                <Text style={{ color: C.muted, fontSize: 12 }}>{d.version || '—'}</Text>
              </View>
              <Pressable onPress={runCheck} style={[W.card, { padding: 14, marginBottom: 10 }]}>
                <Text style={{ color: C.ok, fontWeight: '700' }}>Check for app update</Text>
              </Pressable>
            </>
          )}
        </View>
      </ScrollView>
    </View>
  );
  function runCheck() { onRefresh && onRefresh(); }
}

// ---------------------------------------------------------------- admin: users list
export function AdminUsersScreen({ users: usersProp, onSetPlan, onSetAdmin, onOpenUser }) {
  const [d, setD] = useState(usersProp || null);
  const [loading, setLoading] = useState(!d);
  useEffect(() => { (async () => {
    if (loading) {
      try { setD(await api('/api/admin/users')); } catch (e) { setD({ users: [] }); }
      finally { setLoading(false); }
    } else if (usersProp) { setD(usersProp); }
  })(); }, []);
  const us = (d && d.users) || [];
  return (
    <View style={W.screen}>
      <WebTopBar title="Admin · users" showBack conn={false} account="admin" onAvatar={() => {}} />
      <ScrollView contentContainerStyle={{ paddingBottom: 30, paddingTop: 10 }} showsVerticalScrollIndicator={false}>
        <View style={{ paddingHorizontal: 16 }}>
          <View style={{ flexDirection: 'row', gap: 8, marginBottom: 10, paddingBottom: 8, borderBottomWidth: 1, borderColor: C.line }}>
            <View style={{ flex: 1, minWidth: 0 }}><Text style={W.tileCap}>Account</Text></View>
            <View style={{ minWidth: 70 }}><Text style={W.tileCap}>Plan</Text></View>
            <View style={{ minWidth: 60 }}><Text style={W.tileCap}>Admin</Text></View>
            <View style={{ minWidth: 70 }}><Text style={W.tileCap}>Tokens wk</Text></View>
            <View style={{ minWidth: 70 }}><Text style={W.tileCap}>Bots</Text></View>
          </View>
          {loading ? <View style={{ padding: 20 }}><ActivityIndicator /></View> : (
            us.length === 0 ? <Text style={W.mutedC} style={{ paddingHorizontal: 16 }}>No users.</Text> :
            us.map((u) => {
              const isMe = u.email === (usersProp && usersProp.current_admin);
              const isSelfAdmin = u.is_admin && isMe;
              return (
                <Pressable key={u.email} onPress={() => onOpenUser(u)}
                  style={[W.tabRow, { paddingVertical: 10, paddingHorizontal: 14, borderBottomWidth: 1, borderColor: C.line, alignItems: 'center' }]}>
                  <View style={{ flex: 1, minWidth: 0, flexDirection: 'row', alignItems: 'center', gap: 10 }}>
                    <Text style={{ color: C.text, fontWeight: '700', fontSize: 13, minWidth: 0 }}>{u.email}</Text>
                    {u.is_admin ? <Text style={{ color: C.ok, fontSize: 10, fontWeight: '800' }}>ADMIN</Text> : null}
                  </View>
                  <View style={{ minWidth: 70 }}>
                    <Text style={{ color: C.muted, fontSize: 11, fontWeight: '600' }}>{(auth_mod_plan_label(u.plan || 'free'))}</Text>
                  </View>
                  <View style={{ minWidth: 60, alignItems: 'center' }}>
                    {u.is_admin ? <Text style={{ color: C.ok, fontSize: 11 }}>●</Text> : <Text style={{ color: C.muted, fontSize: 11 }}>○</Text>}
                  </View>
                  <View style={{ minWidth: 70, alignItems: 'flex-end' }}>
                    <Text style={{ color: C.text, fontSize: 11, fontWeight: '600' }}>{Number(u.week_tokens || 0).toLocaleString()}</Text>
                  </View>
                  <View style={{ minWidth: 70, alignItems: 'flex-end' }}>
                    <Text style={{ color: C.text, fontSize: 11, fontWeight: '600' }}>{u.bots_count}</Text>
                  </View>
                  <Pressable onPress={() => onOpenUser(u)} hitSlop={20}
                    style={{ padding: 6 }}><Text style={{ color: C.ok, fontWeight: '700' }}>▸</Text></Pressable>
                </Pressable>
              );
            })
          )}
        </View>
      </ScrollView>
    </View>
  );
}

// ---------------------------------------------------------------- admin: user detail
export function AdminUserDetailScreen({ user: userProp, onSetPlan, onSetAdmin }) {
  const [d, setD] = useState(userProp || null);
  const [saving, setSaving] = useState(false);
  useEffect(() => { if (userProp && (!d || d.email !== userProp.email)) setD(userProp); }, [userProp]);
  const pl = d ? auth_mod_plan_limits(d.plan || 'free') : null;
  const doSetPlan = async (p) => {
    if (!d || saving) return;
    setSaving(true);
    try { await onSetPlan(d.email, p); setD({ ...d, plan: p }); Alert.alert('Plan set', d.email + ' → ' + pl.label); }
    catch (e) { Alert.alert('Failed', String(e.message || e)); }
    finally { setSaving(false); }
  };
  const doToggleAdmin = async () => {
    if (!d || saving) return;
    setSaving(true);
    try { await onSetAdmin(d.email, !d.is_admin); setD({ ...d, is_admin: !d.is_admin }); Alert.alert('Admin', d.is_admin ? 'revoked' : 'granted'); }
    catch (e) { Alert.alert('Failed', String(e.message || e)); }
    finally { setSaving(false); }
  };
  return (
    <View style={W.screen}>
      <WebTopBar title={"Admin · " + (d ? d.email : 'user') + " account"} showBack conn={false} account="admin" onAvatar={() => {}} />
      <ScrollView contentContainerStyle={{ paddingBottom: 30, paddingTop: 10 }} showsVerticalScrollIndicator={false}>
        <View style={{ paddingHorizontal: 16 }}>
          {!d ? <View style={{ padding: 20 }}><ActivityIndicator /></View> : (
            <>
              <View style={[W.card, { flexDirection: 'row', alignItems: 'center', gap: 12, padding: 14, marginBottom: 16 }]}>
                <Avatar bot={{ name: d.email, emoji: '👤' }} size={44} />
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Text style={{ color: C.text, fontSize: 18, fontWeight: '800' }}>{d.email}</Text>
                  <Text style={W.gSub} numberOfLines={2}>{d.is_admin ? 'Server administrator' : 'Standard user'} · {d.google_only ? 'Google sign-in only' : 'Email + password'}</Text>
                </View>
                <Pressable onPress={doToggleAdmin} disabled={saving}
                  style={[W.pillBtn, { backgroundColor: d.is_admin ? '#b00020' : C.solid, paddingHorizontal: 14 }]}>
                  <Text style={{ color: '#fff', fontWeight: '800' }}>{d.is_admin ? 'Revoke admin' : 'Make admin'}</Text>
                </Pressable>
              </View>
              <Text style={{ color: C.muted, fontSize: 12, fontWeight: '600', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10 }}>Plan</Text>
              <View style={{ paddingHorizontal: 2 }}>
                {Object.entries(PLANS).map(([key, p]) => (
                  <Pressable key={key} onPress={() => doSetPlan(key)} disabled={saving || key === d.plan}
                    style={[W.card, { flexDirection: 'row', alignItems: 'center', gap: 10, padding: 12, marginBottom: 8, opacity: key === d.plan ? 1 : 0.9, borderWidth: key === d.plan ? 2 : 0, borderColor: C.ok }]}>
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <Text style={{ color: C.text, fontWeight: '700', fontSize: 14 }}>{p.label}</Text>
                      <Text style={W.gSub} fontSize={11}>{p.price_usd === 0 ? 'Free' : '$' + p.price_usd + '/mo'}</Text>
                    </View>
                    <View style={{ minWidth: 56, alignItems: 'flex-end' }}>
                      <Text style={{ color: key === d.plan ? C.ok : C.muted, fontWeight: '800' }}>{key === d.plan ? 'Current' : p.price_usd === 0 ? 'Free' : '$' + p.price_usd}</Text>
                    </View>
                  </Pressable>
                ))}
              </View>
              <View style={[W.card, { padding: 14, marginTop: 14 }]}>
                <Text style={W.tileCap}>Usage</Text>
                <View style={{ flexDirection: 'row', gap: 12, marginTop: 8 }}>
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <Text style={W.tileCap}>Today</Text>
                    <Text style={{ color: C.text, fontSize: 20, fontWeight: '800' }}>{Number(d.today_tokens || 0).toLocaleString()}</Text>
                  </View>
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <Text style={W.tileCap}>This week</Text>
                    <Text style={{ color: C.text, fontSize: 20, fontWeight: '800' }}>{Number(d.week_tokens || 0).toLocaleString()}</Text>
                  </View>
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <Text style={W.tileCap}>Bots</Text>
                    <Text style={{ color: C.text, fontSize: 20, fontWeight: '800' }}>{d.bots_count}</Text>
                  </View>
                </View>
              </View>
              <Text style={W.section}>Bots owned by this user</Text>
              {!d.bots || d.bots.length === 0 ? <Text style={W.mutedC} style={{ paddingHorizontal: 4, marginTop: 6 }}>No bots yet.</Text> :
                d.bots.map((b) => (
                  <Pressable key={b.id} style={[W.tabRow, { paddingVertical: 8, paddingHorizontal: 4, borderBottomWidth: 1, borderColor: C.line }]}>
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <Text style={{ color: C.text, fontWeight: '600', fontSize: 13 }}>{b.name}</Text>
                      <Text style={W.gSub} fontSize={11}>{(b.job || 'Bot') + ' · ' + (b.approval_mode || 'ask')}</Text>
                    </View>
                    <View style={{ minWidth: 56, alignItems: 'flex-end' }}>
                      <Text style={{ color: C.muted, fontSize: 11 }}>{b.owner || '—'}</Text>
                    </View>
                  </Pressable>
                ))}
              </>
            )}
          </View>
        </ScrollView>
    </View>
  );
}

// ---------------------------------------------------------------- admin: settings
export function AdminSettingsScreen({ settings: settingsProp, onRefresh }) {
  const [d, setD] = useState(settingsProp || null);
  const [loading, setLoading] = useState(!d);
  useEffect(() => { (async () => {
    if (loading) {
      try { setD(await api('/api/admin/settings')); } catch (e) { setD({}); }
      finally { setLoading(false); }
    } else if (settingsProp) { setD(settingsProp); }
  })(); }, []);
  const s = (d && d.settings) || {};
  const kv = (k, v) => (
    <Pressable key={k} style={[W.tabRow, { paddingVertical: 8, paddingHorizontal: 4, borderBottomWidth: 1, borderColor: C.line }]}>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text style={{ color: C.text, fontWeight: '600', fontSize: 12 }}>{k}</Text>
      </View>
      <View style={{ minWidth: 120, alignItems: 'flex-end' }}>
        <Text style={{ color: C.muted, fontSize: 11 }}>{v === undefined || v === null || v === '' ? '—' : String(v).length > 40 ? String(v).slice(0, 37) + '…' : String(v)}</Text>
      </View>
    </Pressable>
  );
  return (
    <View style={W.screen}>
      <WebTopBar title="Admin · settings" showBack conn={false} account="admin" onAvatar={() => {}} />
      <ScrollView contentContainerStyle={{ paddingBottom: 30, paddingTop: 10 }} showsVerticalScrollIndicator={false}>
        <View style={{ paddingHorizontal: 16 }}>
          <View style={[W.card, { padding: 14, marginBottom: 10 }]}>
            <Text style={{ color: C.text, fontWeight: '700', fontSize: 13 }}>{d.api_base || '—'}</Text>
            <Text style={W.gSub} fontSize={11}>Server API base</Text>
          </View>
          {Object.entries(s).map(([section, val]) => {
            if (typeof val !== 'object' || val === null || Array.isArray(val)) return null;
            return (
              <View key={section} style={{ marginBottom: 10 }}>
                <Text style={{ color: C.muted, fontSize: 12, fontWeight: '600', textTransform: 'uppercase', letterSpacing: 1 }}>{section}</Text>
                {Object.entries(val).map(([k, v]) => kv(section + '.' + k, typeof v === 'object' ? JSON.stringify(v) : v))}
              </View>
            );
          })}
        </View>
      </ScrollView>
    </View>
  );
}

// ---------------------------------------------------------------- approval center (Muse-style Allow/Deny)
export function ApprovalCenterView({ conn, account, onAvatar, onOpenThread }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [deciding, setDeciding] = useState({});

  useEffect(() => { (async () => {
    try {
      const d = await api('/api/approvals/center');
      setItems(d.approvals || []);
    } catch (e) { setItems([]); }
    finally { setLoading(false); }
  })(); }, []);

  const decide = async (item, approve) => {
    setDeciding((prev) => ({ ...prev, [item.id]: true }));
    try {
      await approvalDecide(item.id, approve);
      setItems(items.filter((i) => i.id !== item.id));
      if (onOpenThread && item.thread_id) {
        // handled by parent navigation
      }
    } catch (e) {
      Alert.alert('Decision failed', String(e.message || e));
    }
    finally { setDeciding((prev) => { const n = { ...prev }; delete n[item.id]; return n; }); }
  };

  const priceText = (item) => {
    const c = item.currency || '$';
    const p = item.price || '';
    return p ? c + ' ' + p : (c === '$' ? 'Price not shown' : '');
  };

  return (
    <View style={W.screen}>
      <WebTopBar title="Approvals" showBack conn={conn} account={account} onAvatar={onAvatar} />
      <ScrollView contentContainerStyle={{ paddingBottom: 30, paddingTop: 10 }} showsVerticalScrollIndicator={false}>
        <View style={{ paddingHorizontal: 16 }}>
          {loading ? <View style={{ padding: 20 }}><ActivityIndicator /></View> :
            items.length === 0 ? (
              <View style={[W.card, { alignItems: 'center', padding: 30 }]}>
                <Text style={{ fontSize: 40 }}>✅</Text>
                <Text style={{ color: C.text, fontWeight: '700', marginTop: 10 }}>All caught up</Text>
                <Text style={W.gSub} textAlign='center'>No actions waiting for your approval.</Text>
              </View>
            ) : (
              items.map((item) => (
                <Pressable key={item.id} style={[W.card, { flexDirection: 'row', gap: 12, padding: 14, marginBottom: 12 }]}>
                  {item.image_url ? (
                    <Image source={{ uri: item.image_url }} style={{ width: 70, height: 70, borderRadius: 10 }} />
                  ) : (
                    <View style={{ width: 70, height: 70, borderRadius: 10, backgroundColor: C.panel2, alignItems: 'center', justifyContent: 'center' }}>
                      <Text style={{ fontSize: 28 }}>🤖</Text>
                    </View>
                  )}
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <Text style={{ color: C.text, fontWeight: '700', fontSize: 15, minWidth: 0 }} numberOfLines={2}>
                      {item.item || item.summary || item.title}
                    </Text>
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 4 }}>
                      {item.merchant ? <Text style={{ color: C.muted, fontSize: 11, fontWeight: '600' }}>{item.merchant}</Text> : null}
                      <Text style={{ color: C.muted, fontSize: 11 }}>{item.action || item.category}</Text>
                    </View>
                    <Text style={{ color: priceText(item).includes('not') ? C.muted : C.ok, fontWeight: '800', fontSize: 16, marginTop: 6 }}>
                      {priceText(item)}
                    </Text>
                  </View>
                  <View style={{ gap: 8 }}>
                    <Pressable onPress={() => decide(item, false)} disabled={deciding[item.id]}
                      style={[W.pillBtn, { backgroundColor: '#b00020', paddingHorizontal: 14, paddingVertical: 8, minWidth: 60 }]}>
                      <Text style={{ color: '#fff', fontWeight: '800', fontSize: 13 }}>
                        {deciding[item.id] ? '…' : 'Deny'}
                      </Text>
                    </Pressable>
                    <Pressable onPress={() => decide(item, true)} disabled={deciding[item.id]}
                      style={[W.wideBlack, { paddingHorizontal: 16, paddingVertical: 8, minWidth: 70 }]}>
                      <Text style={{ color: '#fff', fontWeight: '800', fontSize: 13 }}>
                        {deciding[item.id] ? '…' : 'Allow'}
                      </Text>
                    </Pressable>
                  </View>
                </Pressable>
              ))
            )}
        </View>
      </ScrollView>
    </View>
  );
}

// ---------------------------------------------------------------- muse: agent profile / home
export function MuseProfileView({ conn, account, onAvatar, onGoView }) {
  return (
    <View style={W.screen}>
      <View style={W.museHeader}>
        <View style={W.museAvatar}>
          <Text style={W.museAvatarT}>🤖</Text>
        </View>
        <Text style={W.museName}>Muse</Text>
        <View style={W.museStatus}>
          <View style={W.museDot} />
          <Text>Connected</Text>
        </View>
        <View style={W.museActionRow}>
          <Pressable style={W.museActionBtn}><Text style={W.museActionIcon}>☰</Text><Text style={W.museActionT}>All</Text></Pressable>
          <Pressable style={W.museActionBtn}><Text style={W.museActionIcon}>🛡</Text><Text style={W.museActionT}>Protect</Text></Pressable>
          <Pressable style={W.museActionBtn}><Text style={W.museActionIcon}>⏱</Text><Text style={W.museActionT}>History</Text></Pressable>
          <Pressable style={W.museActionBtn}><Text style={W.museActionIcon}>👆</Text><Text style={W.museActionT}>ID</Text></Pressable>
        </View>
      </View>
      <ScrollView contentContainerStyle={{ paddingBottom: 30, paddingTop: 2 }} showsVerticalScrollIndicator={false}>
        <View style={{ paddingHorizontal: 16 }}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
            <Text style={W.museSection}>Needs review</Text>
            <Text style={{ color: C.muted, fontSize: 11, fontWeight: '600' }}>…</Text>
          </View>
          <View style={[W.museCard, { flexDirection: 'row', gap: 12, padding: 14 }]}>
            <View style={{ width: 48, height: 48, borderRadius: 12, backgroundColor: '#f6f9ff', alignItems: 'center', justifyContent: 'center' }}>
              <Text style={{ fontSize: 22 }}>📧</Text>
            </View>
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={W.museTitle}>Allow Muse to send emails in Gmail?</Text>
              <Text style={W.museSub}>Hatch wants to send a follow up email to your golf group to revise the course for Thursday.</Text>
              <Text style={W.museDetails}>Details ▸</Text>
            </View>
          </View>
          <View style={W.museBtnRow}>
            <Pressable style={W.museBtnPrimary}><Text style={W.museBtnPrimaryT}>Allow once</Text></Pressable>
          </View>
          <View style={W.museBtnRow2}>
            <Pressable style={W.museBtnGhost2}><Text style={W.museBtnGhost2T}>Allow for this task</Text></Pressable>
            <Pressable style={W.museBtnGhost2}><Text style={W.museBtnGhost2T}>Deny</Text></Pressable>
          </View>
          <View style={{ marginTop: 18 }}>
            <Text style={W.museSection}>Approvals history</Text>
            <View style={[W.museCard, { padding: 0 }]}>
              <View style={[W.museHistoryItem, { borderBottomWidth: 0 }]}>
                <View style={W.museHistoryIcon}>🛒</View>
                <View style={W.museHistoryText}>
                  <Text style={W.museHistoryTitle}>Create a spend request</Text>
                  <Text style={W.museHistorySub}>To buy new headcovers for $56.00 at Dor…</Text>
                </View>
                <Text style={W.museHistoryRight}>Allowed for this task · 3:34pm</Text>
              </View>
              <View style={W.museHistoryItem}>
                <View style={W.museHistoryIcon}>📸</View>
                <View style={W.museHistoryText}>
                  <Text style={W.museHistoryTitle}>Post to Instagram</Text>
                  <Text style={W.museHistorySub}>Share 3 photos as a post to your account</Text>
                </View>
                <Text style={W.museHistoryRight}>Allowed for this task · 11:44am</Text>
              </View>
            </View>
          </View>
        </View>
      </ScrollView>
    </View>
  );
}

// ---------------------------------------------------------------- muse: connectors (live)
// Wired to the real /api/plugins + /api/mcp endpoints. OAuth connectors open
// the vendor's sign-in in the system browser; the server deep-links back to
// zerothebot://plugins/... once the grant is done.
const CONNECTOR_ICONS = {
  gmail: '📧', gcal: '📅', google_calendar: '📅', calendar: '📅',
  healthex: '💊', opentable: '🍽', facebook: '👥', instagram: '📸',
  peloton: '🚲', plaid: '🏦', function: '🩺',
};
function connectorIcon(p) {
  return CONNECTOR_ICONS[String(p.id || '').toLowerCase()] || (p.oauth ? '🔗' : '🧩');
}
function mcpDot(status) {
  if (status === 'connected') return C.ok;
  if (status === 'error') return C.bad;
  if (status === 'disabled') return C.muted;
  return C.warn;
}

export function ConnectorsView({ conn, account, onAvatar }) {
  const [plugins, setPlugins] = useState([]);
  const [mcps, setMcps] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState('');            // plugin id mid-connect/disconnect
  const [q, setQ] = useState('');
  const [cfg, setCfg] = useState(null);            // plugin whose fields are being edited
  const [cfgVals, setCfgVals] = useState({});
  const [cfgBusy, setCfgBusy] = useState(false);
  const [mcpAdd, setMcpAdd] = useState(false);
  const [mcpJson, setMcpJson] = useState('');
  const [mcpBusy, setMcpBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const pl = await pluginsList();
      setPlugins((pl && pl.plugins) || []);
      setErr('');
    } catch (e) {
      setErr(String(e.message || e));
    }
    try {
      const mc = await mcpList();
      setMcps(Array.isArray(mc) ? mc : []);
    } catch (e) { /* a failed MCP list must not blank the connectors */ }
    setLoading(false);
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // Back from the OAuth browser: the server deep-links zerothebot://plugins/go?pid=…
  useEffect(() => {
    const handle = (u) => { if (String(u || '').startsWith('zerothebot://plugins')) refresh(); };
    let sub = null;
    try { sub = Linking.addEventListener('url', handle); } catch (e) { /* older RN */ }
    Linking.getInitialURL().then((u) => { if (u) handle(u); }).catch(() => {});
    return () => { try { if (sub) sub.remove(); } catch (e) { /* ignore */ } };
  }, [refresh]);

  const needle = q.trim().toLowerCase();
  const shown = needle
    ? plugins.filter((p) => String(p.name || '').toLowerCase().includes(needle) || String(p.description || '').toLowerCase().includes(needle))
    : plugins;
  const connected = shown.filter((p) => p.connected);
  const available = shown.filter((p) => !p.connected);

  const openCfg = (p) => {
    const vals = {};
    (p.fields || []).forEach((f) => { vals[f.key] = f.value || ''; });
    setCfgVals(vals);
    setCfg(p);
  };

  const doConnect = async (p) => {
    if (busy) return;
    if (!p.oauth) { openCfg(p); return; }   // field-based connector: ask for keys first
    setBusy(p.id);
    try {
      const d = await pluginConnect(undefined, p.id, true);
      if (d && d.url) await Linking.openURL(d.url);
      else await refresh();
    } catch (e) {
      Alert.alert('Could not connect', String(e.message || e));
    } finally {
      setBusy('');
    }
  };

  const doDisconnect = async (p) => {
    if (busy) return;
    setBusy(p.id);
    try {
      await pluginDisconnect(undefined, p.id);
      await refresh();
    } catch (e) {
      Alert.alert('Could not disconnect', String(e.message || e));
    } finally {
      setBusy('');
    }
  };

  const saveCfg = async () => {
    if (cfgBusy || !cfg) return;
    setCfgBusy(true);
    try {
      await pluginConfig(undefined, cfg.id, cfgVals);
      setCfg(null);
      await refresh();
    } catch (e) {
      Alert.alert('Could not save', String(e.message || e));
    } finally {
      setCfgBusy(false);
    }
  };

  const addMcp = async () => {
    if (mcpBusy) return;
    setMcpBusy(true);
    try {
      await mcpSave(undefined, { import_json: mcpJson });
      setMcpJson('');
      setMcpAdd(false);
      await refresh();
    } catch (e) {
      Alert.alert('Could not add server', String(e.message || e));
    } finally {
      setMcpBusy(false);
    }
  };

  const mcpRestartOne = async (s) => {
    try { await mcpRestart(undefined, s.id); await refresh(); }
    catch (e) { Alert.alert('MCP server', String(e.message || e)); }
  };
  const mcpDeleteOne = async (s) => {
    try { await mcpDelete(undefined, s.id); await refresh(); }
    catch (e) { Alert.alert('MCP server', String(e.message || e)); }
  };

  const pill = (label, color, onPress, disabled) => (
    <Pressable disabled={disabled} onPress={onPress}
      style={{ backgroundColor: C.panel2, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 6 }}>
      <Text style={{ color, fontWeight: '800', fontSize: 12 }}>{label}</Text>
    </Pressable>
  );

  return (
    <View style={W.screen}>
      <WebTopBar title="Connectors" showBack conn={conn} account={account} onAvatar={onAvatar} />
      <ScrollView contentContainerStyle={{ paddingBottom: 30, paddingTop: 10 }} showsVerticalScrollIndicator={false}>
        <View style={{ paddingHorizontal: 16 }}>
          <View style={[W.card, { padding: 12, marginBottom: 14 }]}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
              <View style={{ width: 36, height: 36, borderRadius: 10, backgroundColor: C.panel2, alignItems: 'center', justifyContent: 'center' }}>
                <Text style={{ fontSize: 16 }}>🔍</Text>
              </View>
              <TextInput style={{ flex: 1, color: C.text, fontSize: 14 }} placeholder="Search connectors"
                placeholderTextColor={C.muted} value={q} onChangeText={setQ} />
            </View>
          </View>

          {loading ? (
            <ActivityIndicator style={{ marginTop: 30 }} color={C.muted} />
          ) : (
            <View>
              {!!err && <Text style={{ color: C.bad, fontSize: 13, marginBottom: 10 }}>{err}</Text>}

              {!!connected.length && <Text style={W.museSection}>Connected</Text>}
              {connected.map((p) => (
                <View key={p.id} style={[W.museCard, { padding: 12 }]}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
                    <View style={{ width: 36, height: 36, borderRadius: 10, backgroundColor: C.panel2, alignItems: 'center', justifyContent: 'center' }}>
                      <Text style={{ fontSize: 18 }}>{connectorIcon(p)}</Text>
                    </View>
                    <View style={{ flex: 1 }}>
                      <Text style={{ color: C.text, fontWeight: '600', fontSize: 14 }} numberOfLines={1}>{p.name || p.id}</Text>
                      {!!p.description && <Text style={{ color: C.muted, fontSize: 12, marginTop: 2 }} numberOfLines={2}>{p.description}</Text>}
                    </View>
                    <View style={{ flexDirection: 'row', gap: 8 }}>
                      {!!(p.fields || []).length && pill('Configure', C.text, () => openCfg(p), false)}
                      {pill(busy === p.id ? '…' : 'Disconnect', C.bad, () => doDisconnect(p), !!busy)}
                    </View>
                  </View>
                </View>
              ))}

              <Text style={W.museSection}>Available</Text>
              {available.length ? available.map((p) => (
                <View key={p.id} style={[W.museCard, { padding: 12 }]}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
                    <View style={{ width: 36, height: 36, borderRadius: 10, backgroundColor: C.panel2, alignItems: 'center', justifyContent: 'center' }}>
                      <Text style={{ fontSize: 18 }}>{connectorIcon(p)}</Text>
                    </View>
                    <View style={{ flex: 1 }}>
                      <Text style={{ color: C.text, fontWeight: '600', fontSize: 14 }} numberOfLines={1}>{p.name || p.id}</Text>
                      {!!p.description && <Text style={{ color: C.muted, fontSize: 12, marginTop: 2 }} numberOfLines={2}>{p.description}</Text>}
                    </View>
                    {pill(busy === p.id ? '…' : (p.oauth ? 'Sign in' : 'Connect'), C.ok, () => doConnect(p), !!busy)}
                  </View>
                </View>
              )) : (
                <Text style={{ color: C.muted, fontSize: 13, marginHorizontal: 16 }}>No other connectors yet.</Text>
              )}

              <Text style={[W.museSection, { marginTop: 18 }]}>MCP servers</Text>
              {mcps.map((s) => (
                <View key={s.id} style={[W.museCard, { padding: 12 }]}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
                    <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: mcpDot(s.status) }} />
                    <View style={{ flex: 1 }}>
                      <Text style={{ color: C.text, fontWeight: '600', fontSize: 14 }} numberOfLines={1}>{s.name}</Text>
                      <Text style={{ color: C.muted, fontSize: 12, marginTop: 2 }} numberOfLines={1}>
                        {s.transport} · {s.status}{s.tool_count ? ' · ' + s.tool_count + ' tools' : ''}{s.error ? ' · ' + s.error : ''}
                      </Text>
                    </View>
                    <View style={{ flexDirection: 'row', gap: 8 }}>
                      {pill('Restart', C.text, () => mcpRestartOne(s), false)}
                      {pill('Delete', C.bad, () => mcpDeleteOne(s), false)}
                    </View>
                  </View>
                </View>
              ))}
              <Pressable onPress={() => setMcpAdd(true)} style={[W.museCard, { padding: 14, alignItems: 'center' }]}>
                <Text style={{ color: C.text, fontWeight: '800', fontSize: 13 }}>+ Add MCP server</Text>
              </Pressable>
            </View>
          )}
        </View>
      </ScrollView>

      {/* connector fields: API keys, regions, custom OAuth clients */}
      <Modal visible={!!cfg} transparent animationType="fade" onRequestClose={() => setCfg(null)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.35)', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
          <View style={{ backgroundColor: C.bg, borderRadius: 18, padding: 18, width: '100%' }}>
            <Text style={{ color: C.text, fontWeight: '800', fontSize: 16 }}>{cfg ? cfg.name : ''} settings</Text>
            {((cfg && cfg.fields) || []).map((f) => (
              <View key={f.key} style={{ marginTop: 12 }}>
                <Text style={{ color: C.muted, fontSize: 12, marginBottom: 4 }}>{f.label || f.key}{f.required ? ' *' : ''}</Text>
                <TextInput
                  style={{ borderWidth: 1, borderColor: C.line, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: C.text, fontSize: 14 }}
                  placeholder={f.placeholder || ''} placeholderTextColor={C.muted}
                  value={cfgVals[f.key] || ''} secureTextEntry={!!f.secret}
                  autoCapitalize="none" autoCorrect={false}
                  onChangeText={(v) => setCfgVals((prev) => ({ ...prev, [f.key]: v }))}
                />
              </View>
            ))}
            <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: 10, marginTop: 16 }}>
              <Pressable onPress={() => setCfg(null)} style={{ backgroundColor: C.panel2, borderRadius: 10, paddingHorizontal: 14, paddingVertical: 8 }}>
                <Text style={{ color: C.text, fontWeight: '700', fontSize: 13 }}>Cancel</Text>
              </Pressable>
              <Pressable disabled={cfgBusy} onPress={saveCfg} style={{ backgroundColor: C.solid, borderRadius: 10, paddingHorizontal: 14, paddingVertical: 8 }}>
                <Text style={{ color: C.onSolid, fontWeight: '700', fontSize: 13 }}>{cfgBusy ? 'Saving…' : 'Save'}</Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>

      {/* add an MCP server by pasting the standard config JSON */}
      <Modal visible={mcpAdd} transparent animationType="fade" onRequestClose={() => setMcpAdd(false)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.35)', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
          <View style={{ backgroundColor: C.bg, borderRadius: 18, padding: 18, width: '100%' }}>
            <Text style={{ color: C.text, fontWeight: '800', fontSize: 16 }}>Add MCP server</Text>
            <Text style={{ color: C.muted, fontSize: 12, marginTop: 4 }}>Paste the standard config JSON (mcpServers).</Text>
            <TextInput
              style={{ borderWidth: 1, borderColor: C.line, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: C.text, fontSize: 13, marginTop: 12, minHeight: 110, textAlignVertical: 'top' }}
              placeholder="mcpServers JSON" placeholderTextColor={C.muted} value={mcpJson}
              multiline autoCapitalize="none" autoCorrect={false}
              onChangeText={setMcpJson}
            />
            <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: 10, marginTop: 16 }}>
              <Pressable onPress={() => setMcpAdd(false)} style={{ backgroundColor: C.panel2, borderRadius: 10, paddingHorizontal: 14, paddingVertical: 8 }}>
                <Text style={{ color: C.text, fontWeight: '700', fontSize: 13 }}>Cancel</Text>
              </Pressable>
              <Pressable disabled={mcpBusy || !mcpJson.trim()} onPress={addMcp} style={{ backgroundColor: C.solid, borderRadius: 10, paddingHorizontal: 14, paddingVertical: 8 }}>
                <Text style={{ color: C.onSolid, fontWeight: '700', fontSize: 13 }}>{mcpBusy ? 'Adding…' : 'Add'}</Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

// ---------------------------------------------------------------- muse: ideas
export function IdeasView({ conn, account, onAvatar }) {
  const ideas = [
    { icon: '✈️', title: 'I can follow up on your airline refund', sub: 'I found an email from the airline confirming your cancelled flight on October 14, but no refund has been posted. Want me to draft a follow-up and track it until it lands?' },
    { icon: '🛒', title: 'I can find a bigger stroller for Luca', sub: 'Based on Luca’s age he’ll outgrow his stroller in the next month. I’m watching Marketplace for a bigger option under $300.' },
    { icon: '🏃', title: 'Build a daily training plan for your half marathon', sub: "Your VO2 max is up 4% and your average pace dropped to 8:12. I'm building a 10-week plan synced to your calendar that adjusts based on your health connector data." },
    { icon: '🍽', title: 'I can book your anniversary dinner', sub: 'You saved a restaurant on Instagram last month and there’s an opening Saturday at 8 PM. Want me to reserve a table for two?' },
    { icon: '😴', title: 'I can help dial in your sleep based on the data', sub: 'Your sleep connector shows inconsistent bedtimes. Want me to set a wind-down routine and block late-night notifications?' },
  ];
  return (
    <View style={W.screen}>
      <WebTopBar title="Ideas" showBack conn={conn} account={account} onAvatar={onAvatar} />
      <ScrollView contentContainerStyle={{ paddingBottom: 30, paddingTop: 10 }} showsVerticalScrollIndicator={false}>
        <View style={{ paddingHorizontal: 16 }}>
          <Text style={W.museSection}>Ideas</Text>
          {ideas.map((idea, i) => (
            <Pressable key={i} style={[W.museCard, { flexDirection: 'row', gap: 10, padding: 12 }]}>
              <View style={{ width: 40, height: 40, borderRadius: 10, backgroundColor: C.panel2, alignItems: 'center', justifyContent: 'center' }}>
                <Text style={{ fontSize: 18 }}>{idea.icon}</Text>
              </View>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={W.museTitle}>{idea.title}</Text>
                <Text style={W.museSub}>{idea.sub}</Text>
              </View>
              <Text style={{ color: C.muted, fontSize: 11 }}>▸</Text>
            </Pressable>
          ))}
        </View>
      </ScrollView>
    </View>
  );
}

// ---------------------------------------------------------------- muse: goals
export function GoalsView({ conn, account, onAvatar }) {
  const goals = [
    { id: 'dining', title: 'Dinner reservations', sub: 'Sushi restaurants downtown', on: false },
    { id: 'marathon', title: 'Marathon prep', sub: 'Build endurance, hit your pace goals, and cross that finish line strong.', on: true },
    { id: 'car', title: 'Save for new car', sub: 'On track to hit your goals if you save $210 each month towards your new car fund.', on: false },
  ];
  return (
    <View style={W.screen}>
      <WebTopBar title="Goals" showBack conn={conn} account={account} onAvatar={onAvatar} />
      <ScrollView contentContainerStyle={{ paddingBottom: 30, paddingTop: 10 }} showsVerticalScrollIndicator={false}>
        <View style={{ paddingHorizontal: 16 }}>
          <Text style={W.museSection}>Goals</Text>
          {goals.map((g) => (
            <Pressable key={g.id} style={[W.museCard, { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: 12 }]}>
              <View style={{ flex: 1, minWidth: 0 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <Pressable style={[W.museGoalToggle, g.on && W.museGoalToggleOn]}>
                    <Text style={{ fontSize: 10 }}>{g.on ? '✓' : ''}</Text>
                  </Pressable>
                  <Text style={{ color: C.text, fontWeight: '700', fontSize: 14 }}>{g.title}</Text>
                  {g.on ? <Text style={{ color: C.ok, fontSize: 11, fontWeight: '700' }}>Tracking</Text> : null}
                </View>
                <Text style={W.museSub}>{g.sub}</Text>
              </View>
              <Text style={{ color: C.muted, fontSize: 11 }}>▸</Text>
            </Pressable>
          ))}
          <View style={{ marginTop: 10 }}>
            <Pressable style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
              <Text style={{ color: C.ok, fontWeight: '800', fontSize: 12 }}>Show 2 more</Text>
            </Pressable>
          </View>
          <View style={{ marginTop: 18, borderTopWidth: 1, borderColor: C.line, paddingTop: 10 }}>
            <Text style={W.museSection}>Create a goal</Text>
            <Pressable style={[W.museCard, { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: 12 }]}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
                <View style={{ width: 36, height: 36, borderRadius: 10, backgroundColor: C.panel2, alignItems: 'center', justifyContent: 'center' }}>
                  <Text style={{ fontSize: 18 }}>❤️</Text>
                </View>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Text style={{ color: C.text, fontWeight: '600', fontSize: 14 }}>Health</Text>
                </View>
              </View>
              <Pressable style={{ backgroundColor: C.panel2, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 6 }}>
                <Text style={{ color: C.ok, fontWeight: '800', fontSize: 12 }}>+</Text>
              </Pressable>
            </Pressable>
            <Pressable style={[W.museCard, { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: 12, marginTop: 8 }]}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
                <View style={{ width: 36, height: 36, borderRadius: 10, backgroundColor: C.panel2, alignItems: 'center', justifyContent: 'center' }}>
                  <Text style={{ fontSize: 18 }}>🤝</Text>
                </View>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Text style={{ color: C.text, fontWeight: '600', fontSize: 14 }}>Relationships</Text>
                </View>
              </View>
              <Pressable style={{ backgroundColor: C.panel2, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 6 }}>
                <Text style={{ color: C.ok, fontWeight: '800', fontSize: 12 }}>+</Text>
              </Pressable>
            </Pressable>
          </View>
        </View>
      </ScrollView>
    </View>
  );
}

// ---------------------------------------------------------------- muse: finance tracker
export function FinanceView({ conn, account, onAvatar }) {
  return (
    <View style={W.screen}>
      <WebTopBar title="Spending" showBack conn={conn} account={account} onAvatar={onAvatar} />
      <ScrollView contentContainerStyle={{ paddingBottom: 30, paddingTop: 10 }} showsVerticalScrollIndicator={false}>
        <View style={{ paddingHorizontal: 16 }}>
          <View style={[W.financeCard, { flexDirection: 'row', gap: 12 }]}>
            <View style={{ flex: 1 }}>
              <Text style={W.financeCardCap}>Spending (last 3 months)</Text>
              <Text style={W.financeCardVal}>$4,320/mo</Text>
              <Text style={W.financeCardSub}>Down 35% to $410/mo. You're averaging $430/day.</Text>
            </View>
            <View style={{ width: 64, height: 64, borderRadius: 16, backgroundColor: 'rgba(255,255,255,.15)', alignItems: 'center', justifyContent: 'center' }}>
              <Text style={{ fontSize: 24 }}>📉</Text>
            </View>
          </View>
          <View style={{ marginTop: 16 }}>
            <Text style={{ color: C.text, fontWeight: '700', fontSize: 14 }}>Things standing out:</Text>
            <View style={[W.museCard, { padding: 12, marginTop: 8 }]}>
              <View style={{ flexDirection: 'row', gap: 8, alignItems: 'flex-start' }}>
                <Text style={{ color: C.muted, fontSize: 12, marginTop: 2 }}>•</Text>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Text style={{ color: C.text, fontSize: 13, fontWeight: '600' }}>Dining is up 35% to $410/mo, and you have three subscriptions you haven't opened in 90 days.</Text>
                  <Text style={{ color: C.muted, fontSize: 11, marginTop: 4 }}>$47/mo you won't miss. Want me to cancel them?</Text>
                </View>
              </View>
            </View>
          </View>
          <View style={{ marginTop: 18 }}>
            <Text style={{ color: C.text, fontWeight: '700', fontSize: 14 }}>Finance tracker</Text>
            <Text style={W.gSub} marginTop={2}>Spending, savings, subscriptions, and more.</Text>
            <View style={W.financeCard}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 12 }}>
                <View>
                  <Text style={W.financeCardCap}>Felix Card ····0589</Text>
                  <Text style={W.financeCardVal}>$4,850</Text>
                </View>
                <View>
                  <Text style={W.financeCardCap}>Peloton Card</Text>
                  <Text style={W.financeCardVal}>$4,320</Text>
                </View>
                <View>
                  <Text style={W.financeCardCap}>Quicksilver</Text>
                  <Text style={W.financeCardVal}>$530</Text>
                </View>
              </View>
            </View>
          </View>
        </View>
      </ScrollView>
    </View>
  );
}

// ---------------------------------------------------------------- Tier 1: Email config UI
export function EmailConfigScreen({ conn, account, onAvatar }) {
  const [host, setHost] = useState('imap.gmail.com');
  const [port, setPort] = useState('993');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [useSsl, setUseSsl] = useState(true);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState('');

  const doSave = async () => {
    if (saving) return;
    setSaving(true);
    try {
      await emailConfigure(
        undefined,
        host,
        parseInt(port) || 993,
        username,
        password,
        useSsl
      );
      setStatus('IMAP configured successfully.');
    } catch (e) {
      setStatus('Failed: ' + String(e.message || e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <View style={W.screen}>
      <WebTopBar title="Email (IMAP)" showBack conn={conn} account={account} onAvatar={onAvatar} />
      <ScrollView contentContainerStyle={{ paddingBottom: 30, paddingTop: 10 }} showsVerticalScrollIndicator={false}>
        <View style={{ paddingHorizontal: 16 }}>
          <Text style={W.museSection}>Connect your email</Text>
          <Text style={W.gSub} marginBottom={12}>ZerotheBot reads your inbox and proposes actions — like filling out forms or sending follow-ups — so you only approve what gets sent.</Text>
          <View style={[W.museCard, { padding: 14, marginBottom: 12 }]}>
            <View style={{ flexDirection: 'row', gap: 10, marginBottom: 10 }}>
              <View style={{ flex: 1 }}>
                <Text style={W.tileCap}>IMAP Host</Text>
                <TextInput style={[W.input, { flex: 1, borderRadius: 999, paddingVertical: 9 }]} value={host} onChangeText={setHost} placeholder="imap.gmail.com" />
              </View>
              <View style={{ minWidth: 70 }}>
                <Text style={W.tileCap}>Port</Text>
                <TextInput style={[W.input, { flex: 1, borderRadius: 999, paddingVertical: 9, textAlign: 'right' }]} value={port} onChangeText={setPort} placeholder="993" keyboardType="numeric" />
              </View>
            </View>
            <View style={{ flexDirection: 'row', gap: 10, marginBottom: 10 }}>
              <View style={{ flex: 1 }}>
                <Text style={W.tileCap}>Username (email)</Text>
                <TextInput style={[W.input, { flex: 1, borderRadius: 999, paddingVertical: 9 }]} value={username} onChangeText={setUsername} placeholder="you@gmail.com" autoCapitalize="none" />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={W.tileCap}>Password / App Password</Text>
                <TextInput style={[W.input, { flex: 1, borderRadius: 999, paddingVertical: 9 }]} value={password} onChangeText={setPassword} secureTextEntry placeholder="app password" autoCapitalize="none" />
              </View>
            </View>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 12 }}>
              <Pressable style={[W.museGoalToggle, useSsl && W.museGoalToggleOn]} onPress={() => setUseSsl(!useSsl)}>
                <Text style={{ fontSize: 10 }}>{useSsl ? '✓' : ''}</Text>
              </Pressable>
              <Text style={W.museSub}>Use SSL/TLS (recommended)</Text>
            </View>
          </View>
          <Pressable onPress={doSave} disabled={saving}
            style={[W.wideBlack, { marginTop: 8, opacity: saving ? 0.5 : 1 }]}>
            <Text style={{ color: '#fff', fontWeight: '800', fontSize: 15 }}>
              {saving ? 'Saving…' : 'Save IMAP Configuration'}
            </Text>
          </Pressable>
          {status && (
            <View style={{ marginTop: 12, padding: 12, backgroundColor: status.startsWith('Failed') ? '#fdeceb' : '#e4f5ea', borderRadius: 12, borderLeftWidth: 4, borderLeftColor: status.startsWith('Failed') ? C.bad : C.ok }}>
              <Text style={{ color: status.startsWith('Failed') ? C.bad : C.ok, fontSize: 13, fontWeight: '600' }}>{status}</Text>
            </View>
          )}
        </View>
      </ScrollView>
    </View>
  );
}

// ---------------------------------------------------------------- Tier 2: Web search
export function SearchScreen({ conn, account, onAvatar }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);

  const doSearch = async () => {
    if (!query.trim() || loading) return;
    setLoading(true);
    try {
      const d = await webSearch(undefined, query);
      setResults(d.results || []);
    } catch (e) {
      Alert.alert('Search failed', String(e.message || e));
    } finally { setLoading(false); }
  };

  return (
    <View style={W.screen}>
      <WebTopBar title="Search" showBack conn={conn} account={account} onAvatar={onAvatar} />
      <ScrollView contentContainerStyle={{ paddingBottom: 30, paddingTop: 10 }} showsVerticalScrollIndicator={false}>
        <View style={{ paddingHorizontal: 16 }}>
          <View style={[W.museCard, { flexDirection: 'row', gap: 8, marginBottom: 14, padding: 12 }]}>
            <TextInput style={{ flex: 1, color: C.text, fontSize: 14 }} placeholder="Search the web…" value={query} onChangeText={setQuery} onSubmitEditing={doSearch} />
            <Pressable onPress={doSearch} disabled={loading} style={[W.pillBtn, { backgroundColor: C.solid, paddingHorizontal: 14 }]}>
              <Text style={{ color: '#fff', fontWeight: '800' }}>Search</Text>
            </Pressable>
          </View>
          {loading && <View style={{ padding: 20 }}><ActivityIndicator /></View>}
          {results.length > 0 && (
            <>
              <Text style={W.museSection} marginTop={8}>Results</Text>
              {results.map((r, i) => (
                <Pressable key={i} style={[W.museCard, { padding: 12, marginBottom: 8 }]} onPress={() => Linking.openURL(r.url)}>
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <Text style={{ color: C.text, fontWeight: '700', fontSize: 14 }} numberOfLines={2}>{r.title}</Text>
                    <Text style={{ color: C.ok, fontSize: 11, marginTop: 2 }}>{r.url}</Text>
                    <Text style={W.museSub} marginTop={4}>{r.snippet}</Text>
                  </View>
                  <Text style={{ color: C.muted, fontSize: 11 }}>▸</Text>
                </Pressable>
              ))}
            </>
          )}
          {results.length === 0 && !loading && (
            <Text style={W.mutedC} style={{ paddingHorizontal: 4, marginTop: 6 }}>No results yet. Enter a query and tap Search.</Text>
          )}
        </View>
      </ScrollView>
    </View>
  );
}

// ---------------------------------------------------------------- Tier 2: Image understanding
export function ImageUnderstandScreen({ conn, account, onAvatar }) {
  const [imageData, setImageData] = useState('');
  const [prompt, setPrompt] = useState('Describe this image in detail.');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  const doAnalyze = async () => {
    if (!imageData || loading) return;
    setLoading(true);
    try {
      const d = await imageUnderstand(undefined, imageData, prompt);
      setResult(d);
    } catch (e) {
      Alert.alert('Analysis failed', String(e.message || e));
    } finally { setLoading(false); }
  };

  return (
    <View style={W.screen}>
      <WebTopBar title="Image Understanding" showBack conn={conn} account={account} onAvatar={onAvatar} />
      <ScrollView contentContainerStyle={{ paddingBottom: 30, paddingTop: 10 }} showsVerticalScrollIndicator={false}>
        <View style={{ paddingHorizontal: 16 }}>
          <Text style={W.museSection}>Upload Image</Text>
          <View style={[W.museCard, { alignItems: 'center', padding: 20, marginBottom: 12 }]}>
            {imageData ? (
              <Image source={{ uri: imageData }} style={{ width: 160, height: 160, borderRadius: 16 }} />
            ) : (
              <View style={{ width: 160, height: 160, borderRadius: 16, backgroundColor: C.panel2, alignItems: 'center', justifyContent: 'center' }}>
                <Text style={{ fontSize: 40 }}>📷</Text>
              </View>
            )}
          </View>
          <Pressable style={[W.wideBlack, { marginBottom: 10 }]} onPress={() => Alert.alert('Image picker', 'Image upload via camera/photo library coming in next build. For now, paste a base64 image data URI below.')}>
            <Text style={{ color: '#fff', fontWeight: '800' }}>Choose Image (simulated)</Text>
          </Pressable>
          <TextInput style={[W.input, { marginBottom: 10 }]} value={prompt} onChangeText={setPrompt} placeholder="Describe this image…" />
          <Pressable onPress={doAnalyze} disabled={loading || !imageData}
            style={[W.wideBlack, { marginTop: 8, opacity: (loading || !imageData) ? 0.5 : 1 }]}>
            <Text style={{ color: '#fff', fontWeight: '800', fontSize: 15 }}>
              {loading ? 'Analyzing…' : 'Analyze Image'}
            </Text>
          </Pressable>
          {result && (
            <View style={[W.museCard, { marginTop: 14, padding: 14 }]}>
              <Text style={{ color: C.muted, fontSize: 12, fontWeight: '600', textTransform: 'uppercase' }}>Result</Text>
              <Text style={{ color: C.text, fontSize: 13, marginTop: 6 }}>{result.description || result.error}</Text>
              {result.provider && <Text style={{ color: C.muted, fontSize: 11, marginTop: 4 }}>Provider: {result.provider}</Text>}
            </View>
          )}
        </View>
      </ScrollView>
    </View>
  );
}

// ---------------------------------------------------------------- Tier 3: Image generation
export function ImageGenerateScreen({ conn, account, onAvatar }) {
  const [prompt, setPrompt] = useState('');
  const [imageUrl, setImageUrl] = useState(null);
  const [loading, setLoading] = useState(false);

  const doGenerate = async () => {
    if (!prompt.trim() || loading) return;
    setLoading(true);
    try {
      const d = await imageGenerate(undefined, prompt);
      setImageUrl(d.image_url);
    } catch (e) {
      Alert.alert('Generation failed', String(e.message || e));
    } finally { setLoading(false); }
  };

  return (
    <View style={W.screen}>
      <WebTopBar title="Image Generation" showBack conn={conn} account={account} onAvatar={onAvatar} />
      <ScrollView contentContainerStyle={{ paddingBottom: 30, paddingTop: 10 }} showsVerticalScrollIndicator={false}>
        <View style={{ paddingHorizontal: 16 }}>
          <Text style={W.museSection}>Generate Image</Text>
          <TextInput style={[W.input, { marginBottom: 10 }]} value={prompt} onChangeText={setPrompt} placeholder="A futuristic city with flying cars…" />
          <Pressable onPress={doGenerate} disabled={loading || !prompt.trim()}
            style={[W.wideBlack, { marginBottom: 14, opacity: (loading || !prompt.trim()) ? 0.5 : 1 }]}>
            <Text style={{ color: '#fff', fontWeight: '800', fontSize: 15 }}>
              {loading ? 'Generating…' : 'Generate'}
            </Text>
          </Pressable>
          {imageUrl && (
            <View style={[W.museCard, { alignItems: 'center', padding: 16, marginBottom: 12 }]}>
              <Image source={{ uri: imageUrl }} style={{ width: 260, height: 260, borderRadius: 20 }} />
              <Text style={{ color: C.text, fontWeight: '700', marginTop: 10 }}>{prompt}</Text>
            </View>
          )}
          {imageUrl && (
            <Pressable style={[W.wideBlack, { marginBottom: 14 }]} onPress={() => Linking.openURL(imageUrl)}>
              <Text style={{ color: '#fff', fontWeight: '800' }}>Open Full Image</Text>
            </Pressable>
          )}
          <Text style={W.mutedC} style={{ paddingHorizontal: 4, marginTop: 6 }}>Powered by DALL·E 3 via OpenAI API key configured in Admin → Settings.</Text>
        </View>
      </ScrollView>
    </View>
  );
}

// ---------------------------------------------------------------- Tier 3: Code execution sandbox
export function CodeExecuteScreen({ conn, account, onAvatar }) {
  const [code, setCode] = useState('# Write Python code here\\nprint("Hello from ZerotheBot")');
  const [output, setOutput] = useState(null);
  const [loading, setLoading] = useState(false);

  const doExecute = async () => {
    if (!code.trim() || loading) return;
    setLoading(true);
    try {
      const d = await codeExecute(undefined, code);
      setOutput(d);
    } catch (e) {
      Alert.alert('Execution failed', String(e.message || e));
    } finally { setLoading(false); }
  };

  return (
    <View style={W.screen}>
      <WebTopBar title="Code Sandbox" showBack conn={conn} account={account} onAvatar={onAvatar} />
      <ScrollView contentContainerStyle={{ paddingBottom: 30, paddingTop: 10 }} showsVerticalScrollIndicator={false}>
        <View style={{ paddingHorizontal: 16 }}>
          <Text style={W.museSection}>Python Code</Text>
          <Text style={W.gSub} marginBottom={8}>Safe sandbox — no network, filesystem, or system access.</Text>
          <View style={[W.museCard, { marginBottom: 12 }]}>
            <TextInput style={{ ...W.input, height: 200, textAlignVertical: 'top', paddingTop: 12 }} value={code} onChangeText={setCode} multiline numberOfLines={12} />
          </View>
          <Pressable onPress={doExecute} disabled={loading || !code.trim()}
            style={[W.wideBlack, { marginBottom: 14, opacity: (loading || !code.trim()) ? 0.5 : 1 }]}>
            <Text style={{ color: '#fff', fontWeight: '800', fontSize: 15 }}>
              {loading ? 'Running…' : 'Run Code'}
            </Text>
          </Pressable>
          {output && (
            <View style={[W.museCard, { padding: 14 }]}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 8 }}>
                <Text style={{ color: C.muted, fontSize: 12, fontWeight: '600', textTransform: 'uppercase' }}>Output</Text>
                <Text style={{ color: output.ok ? C.ok : C.bad, fontSize: 11, fontWeight: '700' }}>{output.ok ? 'OK' : 'ERROR'}</Text>
              </View>
              <Text style={{ color: C.text, fontFamily: 'monospace', fontSize: 11, lineHeight: 16 }}>{output.output || output.error}</Text>
            </View>
          )}
        </View>
      </ScrollView>
    </View>
  );
}

// ---------------------------------------------------------------- Tier 3: Multi-agent collaboration
export function AgentCollaborateScreen({ conn, account, onAvatar }) {
  const [task, setTask] = useState('');
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);

  const doCollaborate = async () => {
    if (!task.trim() || loading) return;
    setLoading(true);
    try {
      const d = await agentCollaborate(undefined, task);
      setResults(d);
    } catch (e) {
      Alert.alert('Collaboration failed', String(e.message || e));
    } finally { setLoading(false); }
  };

  return (
    <View style={W.screen}>
      <WebTopBar title="Multi-Agent" showBack conn={conn} account={account} onAvatar={onAvatar} />
      <ScrollView contentContainerStyle={{ paddingBottom: 30, paddingTop: 10 }} showsVerticalScrollIndicator={false}>
        <View style={{ paddingHorizontal: 16 }}>
          <Text style={W.museSection}>Agent Collaboration</Text>
          <Text style={W.gSub} marginBottom={12}>Multiple agents work together: Planner → Executor → Reviewer.</Text>
          <TextInput style={[W.input, { marginBottom: 10 }]} value={task} onChangeText={setTask} placeholder="Plan a product launch for ZerotheBot…" />
          <Pressable onPress={doCollaborate} disabled={loading || !task.trim()}
            style={[W.wideBlack, { marginBottom: 14, opacity: (loading || !task.trim()) ? 0.5 : 1 }]}>
            <Text style={{ color: '#fff', fontWeight: '800', fontSize: 15 }}>
              {loading ? 'Collaborating…' : 'Start Collaboration'}
            </Text>
          </Pressable>
          {results && (
            <View style={[W.museCard, { padding: 14 }]}>
              <Text style={{ color: C.muted, fontSize: 12, fontWeight: '600', textTransform: 'uppercase' }}>Task: {results.task}</Text>
              <View style={{ marginTop: 10 }}>
                {results.agents.map((a, i) => (
                  <View key={i} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <View style={{ width: 32, height: 32, borderRadius: 16, backgroundColor: C.panel2, alignItems: 'center', justifyContent: 'center' }}>
                      <Text style={{ fontSize: 14 }}>🤖</Text>
                    </View>
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <Text style={{ color: C.text, fontWeight: '600', fontSize: 13 }}>{a.bot}</Text>
                      <Text style={{ color: C.muted, fontSize: 11 }}>{a.role}</Text>
                    </View>
                    <Text style={{ color: C.ok, fontSize: 11, fontWeight: '700' }}>{a.status}</Text>
                  </View>
                ))}
              </View>
            </View>
          )}
        </View>
      </ScrollView>
    </View>
  );
}

// ---------------------------------------------------------------- InstaBox Android Box screen
// Bots drive Android on InstaBox machines instead of the user's phone.
// No AccessibilityService needed on the phone.
export function AndroidBoxScreen({ conn, account, onAvatar }) {
  const [machines, setMachines] = useState([]);
  const [selectedMid, setSelectedMid] = useState(null);
  const [streamUrl, setStreamUrl] = useState(null);
  const [loading, setLoading] = useState(false);
  const [adbCmd, setAdbCmd] = useState('');
  const [adbResult, setAdbResult] = useState(null);

  useEffect(() => { (async () => {
    try { const d = await ibListMachines(); setMachines(d.machines || []); } catch (e) { setMachines([]); }
  })(); }, []);

  const createMachine = async () => {
    setLoading(true);
    try {
      const d = await ibCreateMachine(undefined, 'zerothebot-android');
      setMachines((prev) => [...prev, d]);
      setSelectedMid(d.id || d.machineId);
    } catch (e) { Alert.alert('Create failed', String(e.message || e)); }
    finally { setLoading(false); }
  };

  const startDesktop = async () => {
    if (!selectedMid) return;
    setLoading(true);
    try {
      await ibDesktopStart(undefined, selectedMid);
      const d = await ibDesktopStream(undefined, selectedMid, true, 3600);
      setStreamUrl(d.url);
    } catch (e) { Alert.alert('Desktop start failed', String(e.message || e)); }
    finally { setLoading(false); }
  };

  const runAdb = async () => {
    if (!selectedMid || !adbCmd.trim()) return;
    setLoading(true);
    try {
      const d = await ibAdb(undefined, selectedMid, adbCmd);
      setAdbResult(JSON.stringify(d, null, 2));
    } catch (e) { Alert.alert('adb failed', String(e.message || e)); }
    finally { setLoading(false); }
  };

  const takeScreenshot = async () => {
    if (!selectedMid) return;
    setLoading(true);
    try {
      const d = await ibDesktopScreenshot(undefined, selectedMid);
      // screenshot returns binary; we'd need to display it as base64 data URI
      Alert.alert('Screenshot', 'Screenshot captured (binary response). Check server logs.');
    } catch (e) { Alert.alert('Screenshot failed', String(e.message || e)); }
    finally { setLoading(false); }
  };

  return (
    <View style={W.screen}>
      <WebTopBar title="Android Box" showBack conn={conn} account={account} onAvatar={onAvatar} />
      <ScrollView contentContainerStyle={{ paddingBottom: 30, paddingTop: 10 }} showsVerticalScrollIndicator={false}>
        <View style={{ paddingHorizontal: 16 }}>
          <Text style={W.museSection}>InstaBox Android Boxes</Text>
          <Text style={W.gSub} marginBottom={12}>Bots drive Android on InstaBox machines (Redroid, no emulator), not on your phone. No AccessibilityService needed.</Text>

          <View style={[W.museCard, { marginBottom: 12 }]}>
            <View style={{ flexDirection: 'row', gap: 8, marginBottom: 10 }}>
              <Pressable onPress={createMachine} disabled={loading}
                style={[W.pillBtn, { backgroundColor: C.solid, paddingHorizontal: 14 }]}>
                <Text style={{ color: '#fff', fontWeight: '800' }}>Create Android Box</Text>
              </Pressable>
              <Pressable onPress={() => setLoading(true) || ibListMachines(undefined).then(d => setMachines(d.machines || [])).finally(() => setLoading(false))}
                style={[W.pillBtn, { backgroundColor: C.panel2, paddingHorizontal: 14 }]}>
                <Text style={{ color: C.text, fontWeight: '700' }}>Refresh</Text>
              </Pressable>
            </View>
            {loading ? <View style={{ padding: 10 }}><ActivityIndicator /></View> :
              machines.length === 0 ? (
                <Text style={W.mutedC} textAlign='center'>No Android boxes yet. Create one to start.</Text>
              ) : (
                machines.map((m) => (
                  <Pressable key={m.id} onPress={() => setSelectedMid(m.id)}
                    style={[W.museCard, { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: 12, marginBottom: 8, borderWidth: selectedMid === m.id ? 2 : 0, borderColor: selectedMid === m.id ? C.ok : 'transparent' }]}>
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <Text style={{ color: C.text, fontWeight: '700', fontSize: 14 }}>{m.name || m.id}</Text>
                      <Text style={W.museSub}>{m.state || 'unknown'} · {m.state === 'running' ? 'ready' : 'starting…'}</Text>
                    </View>
                    <Text style={{ color: m.state === 'running' ? C.ok : C.muted, fontSize: 11, fontWeight: '700' }}>{m.state}</Text>
                  </Pressable>
                ))
              )}
          </View>

          {selectedMid && (
            <>
              <Text style={W.museSection} marginTop={10}>Desktop Control</Text>
              <View style={[W.museCard, { marginBottom: 12 }]}>
                {streamUrl ? (
                  <View style={{ alignItems: 'center' }}>
                    <Text style={{ color: C.muted, fontSize: 11, marginBottom: 6 }}>Android desktop stream</Text>
                    <Image source={{ uri: streamUrl }} style={{ width: 260, height: 460, borderRadius: 16, backgroundColor: '#000' }} />
                  </View>
                ) : (
                  <Pressable onPress={startDesktop} disabled={loading}
                    style={[W.wideBlack, { paddingVertical: 14 }]}>
                    <Text style={{ color: '#fff', fontWeight: '800', fontSize: 15 }}>
                      {loading ? 'Starting desktop…' : 'Start Android Desktop'}
                    </Text>
                  </Pressable>
                )}
              </View>

              <View style={[W.museCard, { marginBottom: 12 }]}>
                <Text style={W.museSection} marginBottom={8}>adb Commands</Text>
                <View style={{ flexDirection: 'row', gap: 6, marginBottom: 8 }}>
                  <TextInput style={[W.rowInput, { flex: 1 }]} value={adbCmd} onChangeText={setAdbCmd} placeholder="adb devices" />
                  <Pressable onPress={runAdb} disabled={loading || !selectedMid}
                    style={[W.pillBtn, { backgroundColor: C.solid, paddingHorizontal: 14 }]}>
                    <Text style={{ color: '#fff', fontWeight: '800' }}>{loading ? '…' : 'Run'}</Text>
                  </Pressable>
                </View>
                {adbResult && (
                  <View style={{ backgroundColor: C.panel2, borderRadius: 10, padding: 10 }}>
                    <Text style={{ color: C.text, fontFamily: 'monospace', fontSize: 11 }}>{adbResult}</Text>
                  </View>
                )}
                <View style={{ flexDirection: 'row', gap: 6, marginTop: 8 }}>
                  <Pressable onPress={takeScreenshot} disabled={loading || !selectedMid}
                    style={[W.pillBtn, { backgroundColor: C.panel2, paddingHorizontal: 12 }]}>
                    <Text style={{ color: C.text, fontWeight: '700', fontSize: 12 }}>Screenshot</Text>
                  </Pressable>
                </View>
              </View>
            </>
          )}
        </View>
      </ScrollView>
    </View>
  );
}

// ---------------------------------------------------------------- plan helpers (module-local)
function auth_mod_plan_limits(plan) {
  // mirror of server PLANS; kept in sync with core/auth.py PLANS
  const P = {
    free: { label: 'Free', price_usd: 0, max_bots: 3, weekly_tokens: 50000 },
    starter: { label: 'Starter', price_usd: 2, max_bots: 10, weekly_tokens: 200000 },
    pro: { label: 'Pro', price_usd: 5, max_bots: 50, weekly_tokens: 1000000 },
    team: { label: 'Team', price_usd: 10, max_bots: 100, weekly_tokens: 5000000 },
    business: { label: 'Business', price_usd: 20, max_bots: 0, weekly_tokens: 20000000 },
  };
  return P[plan] || P.free;
}
function auth_mod_plan_label(plan) {
  const L = { free: 'Free', starter: 'Starter', pro: 'Pro', team: 'Team', business: 'Business' };
  return L[plan] || 'Free';
}
function auth_mod_all_plans() {
  return PLANS;
}

