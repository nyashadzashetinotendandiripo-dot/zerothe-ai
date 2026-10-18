// Tiny markdown renderer: bold, italic, inline code, code fences, bullets, numbers, headings, links.
import React from 'react';
import { Text, Linking, View } from 'react-native';
import { C } from './theme';

function inline(text, styles) {
  const out = [];
  const pat = /`([^`\n]+)`|\*\*([^*\n]+)\*\*|(^|[\s(])\*([^*\n]+)\*|\[([^\]]+)\]\((https?:[^)\s]+)\)/g;
  let last = 0, m, i = 0;
  while ((m = pat.exec(text))) {
    if (m.index > last) out.push(<Text key={i++} style={styles}>{text.slice(last, m.index)}</Text>);
    if (m[1]) out.push(<Text key={i++} style={[styles, { color: C.muted, fontFamily: 'monospace' }]}>{m[1]}</Text>);
    else if (m[2]) out.push(<Text key={i++} style={[styles, { fontWeight: '800' }]}>{m[2]}</Text>);
    else if (m[4]) out.push(<Text key={i++} style={styles}>{m[3]}<Text style={{ fontStyle: 'italic' }}>{m[4]}</Text></Text>);
    else if (m[5]) out.push(
      <Text key={i++} style={[styles, { color: '#a9c2ff', textDecorationLine: 'underline' }]}
        onPress={() => Linking.openURL(m[6])}>{m[5]}</Text>
    );
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(<Text key={i++} style={styles}>{text.slice(last)}</Text>);
  return out;
}

export default function md(src, styles = {}) {
  const text = String(src == null ? '' : src);
  const blocks = [];
  const s = text.replace(/```(\w*)\n([\s\S]*?)```/g, (_, _l, code) => {
    blocks.push(code.replace(/\n$/, '')); return '\u0000' + (blocks.length - 1) + '\u0000';
  });
  const lines = s.split('\n');
  const out = [];
  let num = 0;
  const pushCode = (placeholder) => {
    const code = blocks[+placeholder[1]];
    out.push(
      <View key={num++} style={{ backgroundColor: C.panel2, borderRadius: 10, padding: 10, marginVertical: 4 }}>
        <Text selectable style={{ color: C.text, fontFamily: 'monospace', fontSize: 12.5 }}>{code}</Text>
      </View>
    );
  };
  let para = [];
  const flushPara = () => {
    if (!para.length) return;
    const joined = para.join('\n');
    out.push(<Text key={num++} style={[{ color: C.text, fontSize: 15, lineHeight: 21 }, styles]} selectable>
      {inline(joined, [{ color: C.text, fontSize: 15, lineHeight: 21 }])}
    </Text>);
    para = [];
  };
  for (const line of lines) {
    const t = line.trim();
    let m;
    if ((m = line.match(/^\u0000(\d+)\u0000$/))) { flushPara(); pushCode(m); }
    else if (!t) flushPara();
    else if ((m = t.match(/^#{1,4}\s+(.*)/))) { flushPara(); out.push(<Text key={num++} style={[hStyle, styles]}>{m[1]}</Text>); }
    else if ((m = t.match(/^[-*]\s+(.*)/))) {
      flushPara();
      out.push(<Text key={num++} style={{ color: C.text, fontSize: 15, lineHeight: 21 }} selectable>
        {'•  '}{inline(m[1], [{ color: C.text, fontSize: 15, lineHeight: 21 }])}
      </Text>);
    }
    else if ((m = t.match(/^\d+[.)]\s+(.*)/))) {
      flushPara();
      out.push(<Text key={num++} style={{ color: C.text, fontSize: 15, lineHeight: 21 }} selectable>
        {t.match(/^(\d+)/)[1] + '.  '}{inline(m[1], [{ color: C.text, fontSize: 15, lineHeight: 21 }])}
      </Text>);
    }
    else para.push(line);
  }
  flushPara();
  return <View>{out}</View>;
}
const hStyle = { color: C.text, fontSize: 16, fontWeight: '800', marginVertical: 2 };
