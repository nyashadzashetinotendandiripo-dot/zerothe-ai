// Intelligent-UI widgets (React-Native port of the phone ```ui protocol, core/uiblocks.py).
import React, { useMemo, useState, useEffect } from 'react';
import { Text, View, TextInput, Pressable, ScrollView, StyleSheet, Image, Linking } from 'react-native';
import Svg, { Rect, Circle, Line } from 'react-native-svg';
import { C } from './theme';

const TYPES = ['buttons', 'form', 'table', 'chart', 'steps', 'stats', 'calculator', 'cards'];
const MAX = 4;
const FENCE = /```ui[ \t]*\r?\n([\s\S]*?)```/g;

export function uiSplit(text) {
  const segs = []; const s = String(text == null ? '' : text);
  let pos = 0, count = 0, m;
  FENCE.lastIndex = 0;
  while ((m = FENCE.exec(s)) != null) {
    if (m.index > pos) segs.push(['text', s.slice(pos, m.index)]);
    if (count < MAX) {
      let d = null;
      try { d = JSON.parse(m[1]); } catch (_e) { d = null; }
      segs.push(['ui', d]); count++; pos = m.index + m[0].length;
    } else { pos = m.index; break; }
  }
  if (pos < s.length) segs.push(['text', s.slice(pos)]);
  return segs.filter(([k, v]) => k === 'ui' || (typeof v === 'string' && v.trim()));
}

export function uiPlain(text) {
  const j = uiSplit(text).filter(([k]) => k === 'text').map(([, v]) => v).join('\n\n');
  return j.replace(/\n{3,}/g, '\n\n').trim();
}

export function uiStreamText(text) {
  const s = String(text == null ? '' : text);
  const open = s.lastIndexOf('```ui');
  if (open < 0) return s;
  if (s.indexOf('```', open + 5) < 0) return uiPlain(s.slice(0, open));
  return uiPlain(s);
}

function num(v) {
  const f = parseFloat(v);
  if (!isFinite(f)) return '—';
  const a = Math.abs(f);
  if (a >= 1e6) return (f / 1e6).toFixed(2) + 'M';
  if (a >= 1000) return (f / 1000).toFixed(1) + 'k';
  return String(parseFloat(f.toPrecision(6)));
}

function safeEval(src) {
  const s = String(src == null ? '' : src).trim().replace(/×/g, '*').replace(/÷/g, '/').replace(/,/g, '');
  let i = 0;
  const ws = () => { while (i < s.length && /\s/.test(s[i])) i++; };
  const numT = () => { ws(); const a = i; while (i < s.length && /[0-9.]/.test(s[i])) i++; if (i === a) throw new Error('n'); const v = parseFloat(s.slice(a, i)); if (!isFinite(v)) throw new Error('n'); return v; };
  const primary = () => { ws(); if (s[i] === '(') { i++; const v = expr(); ws(); if (s[i] !== ')') throw new Error(')'); i++; return v; } return numT(); };
  const power = () => { const b = primary(); ws(); if (s.slice(i, i + 2) === '**') { i += 2; return Math.pow(b, unary()); } return b; };
  const unary = () => { ws(); if (s[i] === '+') { i++; return unary(); } if (s[i] === '-') { i++; return -unary(); } return power(); };
  const term = () => { let v = unary(); for (;;) { ws(); const c = s[i]; if (c === '*') { i++; v *= unary(); } else if (c === '/' && s[i + 1] === '/') { i += 2; v = Math.floor(v / unary()); } else if (c === '/') { i++; v /= unary(); } else if (c === '%') { i++; v %= unary(); } else return v; } };
  function expr() { let v = term(); for (;;) { ws(); const c = s[i]; if (c === '+') { i++; v += term(); } else if (c === '-') { i++; v -= term(); } else return v; } }
  const out = expr(); ws(); if (i !== s.length) throw new Error('t');
  if (!isFinite(out)) throw new Error('m');
  return out;
}

const COLORS = ['#6ea8ff', '#3ecf7a', '#e0a53a'];

function Btn({ label, onPress, primary }) {
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [st.btn, primary && st.btnP, pressed && { opacity: .85 }]}>
      <Text style={[st.btnT, primary && { color: C.onSolid }]}>{label}</Text>
    </Pressable>
  );
}

function Buttons({ d, reply }) {
  const items = (d.items || []).slice(0, 6).filter((it) => it && it.label);
  return (
    <View style={st.col}>
      {items.map((it, idx) => {
        const url = typeof it.open === 'string' ? it.open : '';
        if (url) return <Btn key={idx} label={it.label} primary={false} onPress={() => { if (/^https?:/i.test(url)) Linking.openURL(url); }} />;
        return <Btn key={idx} label={it.label} primary={idx === 0} onPress={() => reply(String(it.reply || it.label))} />;
      })}
    </View>
  );
}

function Form({ d, reply }) {
  const fields = (d.fields || []).slice(0, 8).filter((f) => f && f.key && f.label);
  const [vals, setVals] = useState({});
  const set = (k, v) => setVals((o) => ({ ...o, [k]: v }));
  const tmpl = String((d.submit || {}).message || '') || fields.map((f) => f.key + ': {' + f.key + '}').join('\n');
  const send = () => {
    const msg = tmpl.replace(/\{(\w+)\}/g, (m, k) => (k in vals ? vals[k] : m)).trim();
    if (msg) reply(msg);
  };
  return (
    <View style={st.col}>
      {!!d.title && <Text style={st.h2}>{d.title}</Text>}
      {fields.map((f, i) => {
        if (f.kind === 'long') return <View key={i} style={st.col}><Text style={st.lbl}>{f.label}</Text>
          <TextInput style={st.in} multiline placeholder={f.placeholder || ''} placeholderTextColor={C.muted}
            value={vals[f.key] || ''} onChangeText={(t) => set(f.key, t)} /></View>;
        if (f.kind === 'toggle') return (
          <Pressable key={i} style={st.row} onPress={() => set(f.key, vals[f.key] === 'yes' ? 'no' : 'yes')}>
            <Text style={st.rowGlyph}>{vals[f.key] === 'yes' ? '☑' : '☐'}</Text><Text style={st.txt}>{f.label}</Text>
          </Pressable>);
        return <View key={i} style={st.col}><Text style={st.lbl}>{f.label}</Text>
          <TextInput style={st.in} placeholder={f.placeholder || ''} placeholderTextColor={C.muted}
            value={vals[f.key] || ''} onChangeText={(t) => set(f.key, t)} /></View>;
      })}
      <Btn label={(d.submit || {}).label || 'Send'} primary onPress={send} />
    </View>
  );
}

function Table({ d }) {
  const cols = (d.columns || []).slice(0, 8).map(String);
  const rows = (d.rows || []).slice(0, 30);
  return (
    <View style={st.col}>
      {!!d.caption && <Text style={st.cap}>{d.caption}</Text>}
      <ScrollView horizontal showsHorizontalScrollIndicator={false}>
        <View>
          <View style={[st.tr, { backgroundColor: C.panel2 }]}>
            {cols.map((c, i) => <Text key={i} style={[st.th, { width: Math.max(64, Math.min(160, String(c).length * 9 + 34)) }]}>{c}</Text>)}
          </View>
          {rows.map((r, ri) => (
            <View key={ri} style={[st.tr, ri % 2 && { backgroundColor: C.panel }]}>
              {cols.map((c, ci) => <Text key={ci} style={[st.td, { width: Math.max(64, Math.min(160, String(cols[ci]).length * 9 + 34)) }]}>{Array.isArray(r) && ci < r.length ? String(r[ci]) : ''}</Text>)}
            </View>
          ))}
        </View>
      </ScrollView>
    </View>
  );
}

function Chart({ d }) {
  const kind = d.kind === 'line' ? 'line' : 'bar';
  const labels = (d.labels || []).slice(0, 12).map(String);
  const series = (d.series || []).slice(0, 3).map((s) => ({
    name: (s && s.name) || '', values: ((s && s.values) || []).slice(0, 12).map((v) => (isFinite(parseFloat(v)) ? parseFloat(v) : 0)),
  }));
  const n = Math.max(labels.length, ...series.map((s) => s.values.length), 1);
  const mx = Math.max(1, ...series.flatMap((s) => s.values.map((v) => Math.abs(v))));
  const W = 320, H = 150, L = 44, B = 24, T = 12;
  const pw = Math.max(10, W - L - 8), ph = Math.max(10, H - T - B), step = pw / n;
  const grid = [];
  for (let k = 0; k < 4; k++) { const y = T + (ph * k) / 3; grid.push(<Line key={k} x1={L} y1={y} x2={W - 8} y2={y} stroke={C.line} strokeWidth={1} />); }
  const marks = [];
  series.forEach((s, si) => {
    for (let i = 0; i < s.values.length; i++) {
      const x = L + i * step, h = (ph * Math.abs(s.values[i])) / mx, y0 = T + ph - h, col = COLORS[si % 3];
      if (kind === 'bar') { const bw = step / (series.length + 1); marks.push(<Rect key={si + '-' + i} x={x + 2 + si * bw} y={y0} width={Math.max(2, bw - 2)} height={Math.max(2, h)} rx={3} fill={col} />); }
      else { if (i + 1 < s.values.length) { const h2 = (ph * Math.abs(s.values[i + 1])) / mx; marks.push(<Line key={si + '-' + i} x1={x + step / 2} y1={y0} x2={x + step * 1.5} y2={T + ph - h2} stroke={col} strokeWidth={2} />); } marks.push(<Circle key={'c' + si + '-' + i} cx={x + step / 2} cy={y0} r={3} fill={col} />); }
    }
  });
  const leg = series.filter((s) => s.name).map((s, i) => (
    <View key={i} style={st.row}>
      <View style={[st.dot, { backgroundColor: COLORS[i % 3] }]} />
      <Text style={st.cap}>{s.name}</Text>
    </View>
  ));
  return (
    <View style={[st.card, st.col]}>
      {!!d.title && <Text style={[st.h2, { color: C.text }]}>{d.title}</Text>}
      <Svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`}>{grid}{marks}</Svg>
      {labels.length ? (
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 2 }}>
          {labels.map((l, i) => <Text key={i} style={[st.cap, { flex: 1, textAlign: 'center' }]} numberOfLines={1}>{l}</Text>)}
        </View>
      ) : null}
      {leg.length ? <View style={st.colG}>{leg}</View> : null}
    </View>
  );
}

function Steps({ d }) {
  const glyph = { done: ['✓', C.ok], running: ['▶', '#6ea8ff'], error: ['✗', C.bad] };
  const items = (d.items || []).slice(0, 12).filter((it) => it && it.label);
  return (
    <View style={st.col}>
      {items.map((it, i) => {
        const g = glyph[it.status] || ['○', C.muted];
        return (
          <View key={i} style={st.row}>
            <Text style={[st.rowGlyph, { color: g[1], width: 20 }]}>{g[0]}</Text>
            <Text style={st.txt}>{it.label}</Text>
          </View>
        );
      })}
    </View>
  );
}

function Stats({ d }) {
  const items = (d.items || []).filter((it) => it && it.value).slice(0, 4);
  const list = items.length ? items : [{ value: '—' }];
  return (
    <View style={{ flexDirection: 'row', gap: 8 }}>
      {list.map((it, i) => (
        <View key={i} style={st.stat}>
          <Text style={st.statV}>{String(it.value)}</Text>
          {!!it.label && <Text style={st.cap}>{it.label}</Text>}
        </View>
      ))}
    </View>
  );
}

function Calc({ d, reply }) {
  const [expr, setExpr] = useState(String(d.expression || ''));
  const [out, setOut] = useState('—');
  useEffect(() => { try { setOut(num(safeEval(expr))); } catch (e) { setOut('—'); } }, [expr]);
  const send = () => { if (out !== '—' && expr.trim()) reply(expr.trim() + ' = ' + out); };
  return (
    <View style={[st.card, st.col]}>
      {!!d.title && <Text style={[st.h2, { color: C.text }]}>{d.title}</Text>}
      <TextInput style={st.in} value={expr} onChangeText={setExpr} placeholder="e.g. 1200*1.2/12" placeholderTextColor={C.muted} />
      <Text style={{ color: C.text, fontSize: 22, fontWeight: '800' }}>{out}</Text>
      <Btn label="Send result" primary onPress={send} />
    </View>
  );
}

function Cards({ d, reply }) {
  const cards = (d.cards || []).slice(0, 5).filter((c) => c && c.title);
  const acts = (d.actions || []).slice(0, 4).filter((it) => it && it.label);
  return (
    <View style={st.col}>
      {!!d.title && <Text style={st.h2}>{d.title}</Text>}
      {cards.map((c, i) => {
        const row = (
          <View style={st.cardRow}>
            {(typeof c.image === 'string' && /^https?:/i.test(c.image)) ? (
              <Image source={{ uri: c.image }} style={st.cardImg} resizeMode="cover" />
            ) : null}
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={st.cardT}>{String(c.title)}</Text>
              {!!c.price && <Text style={st.cardP}>{String(c.price)}</Text>}
              {!!c.why && <Text style={st.cardWhy}>{String(c.why)}</Text>}
              {!!c.specs && <Text style={st.cardSpecs}>{String(c.specs)}</Text>}
            </View>
          </View>
        );
        const url = typeof c.url === 'string' && /^https:\/\//i.test(c.url) ? c.url : '';
        return (
          <Pressable
            key={i}
            onPress={() => { if (url) Linking.openURL(url); }}
            disabled={!url}
            style={({ pressed }) => [st.cardRowWrap, url && pressed && { opacity: 0.8 }]}
          >
            {row}
            {!!url && (
              <View style={st.cardLinkRow}>
                <Text style={st.cardLink}>Product page ↗</Text>
              </View>
            )}
          </Pressable>
        );
      })}
      {!cards.length && <Text style={st.cap}>No cards.</Text>}
      {acts.length ? <View style={st.col}>{acts.map((it, idx) => (
        <Btn key={idx} label={it.label} primary={idx === 0} onPress={() => {
          const url = typeof it.open === 'string' ? it.open : '';
          if (/^https?:/i.test(url)) Linking.openURL(url); else reply(String(it.reply || it.label));
        }} />
      ))}</View> : null}
    </View>
  );
}

const RENDER = { buttons: Buttons, form: Form, table: Table, chart: Chart, steps: Steps, stats: Stats, calculator: Calc, cards: Cards };

export function uiBlock(d, reply) {
  if (d && typeof d === 'object' && TYPES.indexOf(d.type) >= 0) {
    try { const W = RENDER[d.type]; return <W d={d} reply={reply} />; }
    catch (e) { /* fall through */ }
  }
  return <Text style={st.note}>That block didn't render — ask the Bot to try again.</Text>;
}

export function aiBody(text, renderText, reply) {
  const t = String(text || '');
  const segs = uiSplit(t);
  if (!segs.some(([k]) => k === 'ui')) return renderText(t);
  return segs.map(([k, v], i) => (k === 'text'
    ? <View key={i}>{renderText(v)}</View>
    : v == null
      ? <Text key={i} style={st.note}>That interactive block didn't parse — ask the Bot to try again.</Text>
      : <View key={i}>{uiBlock(v, reply)}</View>));
}

const st = StyleSheet.create({
  col: { gap: 6, marginVertical: 4 },
  colG: { flexDirection: 'row', gap: 12, marginTop: 6 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  btn: { backgroundColor: C.panel, borderRadius: 12, borderWidth: 1, borderColor: C.line, paddingVertical: 10, paddingHorizontal: 12 },
  btnP: { backgroundColor: C.solid, borderColor: C.solid },
  btnT: { color: C.text, fontSize: 14.5, fontWeight: '600' },
  h2: { color: C.text, fontSize: 15, fontWeight: '700' },
  lbl: { color: C.muted, fontSize: 12 },
  in: { backgroundColor: C.bg, borderWidth: 1, borderColor: C.line, borderRadius: 10, padding: 9, color: C.text, fontSize: 14 },
  card: { backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 12, padding: 10 },
  tr: { flexDirection: 'row' },
  th: { color: C.text, fontSize: 12.5, fontWeight: '700', padding: 8 },
  td: { color: C.text, fontSize: 13, padding: 8 },
  cap: { color: C.muted, fontSize: 12 },
  stat: { flex: 1, backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 12, padding: 10 },
  statV: { color: C.text, fontSize: 20, fontWeight: '800' },
  rowGlyph: { color: C.text, fontSize: 15 },
  txt: { color: C.text, fontSize: 14.5, flex: 1 },
  dot: { width: 10, height: 10, borderRadius: 3 },
  cardRow: { flexDirection: 'row', gap: 10, backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 12, padding: 10, alignItems: 'flex-start' },
  cardRowWrap: { marginBottom: 8, borderRadius: 12 },
  cardLinkRow: { alignItems: 'flex-end', marginTop: 2 },
  cardLink: { color: C.ok, fontSize: 12, fontWeight: '800', opacity: 0.9 },
  cardImg: { width: 72, height: 72, borderRadius: 10, backgroundColor: C.panel2 },
  cardT: { color: C.text, fontSize: 14.5, fontWeight: '800' },
  cardP: { color: C.ok, fontSize: 14, fontWeight: '800', marginTop: 1 },
  cardWhy: { color: C.text, fontSize: 12.5, marginTop: 4, opacity: .92 },
  cardSpecs: { color: C.muted, fontSize: 12, marginTop: 3 },
  note: { color: C.muted, fontStyle: 'italic', marginVertical: 4 },
});
