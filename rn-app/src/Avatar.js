// Bot avatars exactly like the web app (web/app.js avSVG): a deterministic
// shape + color per Bot id (FNV-1a seed, same 10 shapes / 11 colors), the two
// tilted white "eyes", and the optional ★ busy badge — so a Bot looks the
// same on the web app and in this app.
import React from 'react';
import { View, Text } from 'react-native';
import Svg, { Circle, Ellipse, Rect, Path, G } from 'react-native-svg';

export const AV_SHAPES = ['circle', 'oval', 'squircle', 'pill', 'triangle', 'hexagon', 'cloud', 'drop', 'star', 'blob'];
export const AV_COLORS = ['#111111', '#8A5A3B', '#FF3B30', '#FF9500', '#FFB300', '#22C55E', '#12B5A5', '#2E7CF6', '#A855F7', '#FF2D78', '#9AA0A6'];
export const AV_PICK = ['#FFFFFF'].concat(AV_COLORS);

export function avHash(s) {
  let h = 2166136261;
  const t = String(s == null ? 'bot' : s);
  for (let i = 0; i < t.length; i++) { h ^= t.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}

function ShapeEl({ shape }) {
  switch (shape) {
    case 'circle': return <Circle cx={50} cy={50} r={46} />;
    case 'oval': return <Ellipse cx={50} cy={50} rx={47} ry={38} />;
    case 'squircle': return <Rect x={6} y={6} width={88} height={88} rx={30} />;
    case 'pill': return <Rect x={4} y={29} width={92} height={42} rx={21} />;
    case 'triangle': return <Path d="M50 8c2 0 4 3 6 7l34 63c4 8 2 14-7 14H17c-9 0-11-6-7-14L44 15c2-4 4-7 6-7z" />;
    case 'hexagon': return <Path d="M50 4l38 22c4 2 6 6 6 11v26c0 5-2 9-6 11L50 96 12 74c-4-2-6-6-6-11V37c0-5 2-9 6-11z" />;
    case 'cloud': return <Path d="M30 88c-16 0-24-10-22-22 2-10 8-14 16-16-2-12 8-22 20-20 6-10 22-12 30-2 14-2 22 10 18 22 6 8 2 20-8 24-4 12-14 16-24 14z" />;
    case 'drop': return <Path d="M50 6s38 42 38 60c0 18-17 30-38 30S12 84 12 66C12 48 50 6 50 6z" />;
    case 'star': return <Path d="M50 4c4 22 24 42 46 46-22 4-42 24-46 46-4-22-24-42-46-46C26 46 46 26 50 4z" />;
    default: return <Path d="M54 6c22-2 40 14 40 36 0 16-10 24-18 30-8 12-22 24-36 20C24 88 8 78 8 58 8 40 20 28 30 22 38 16 44 8 54 6z" />;
  }
}

const EYES = [
  <Rect key="e1" x={37} y={43} width={7} height={18} rx={3.5} fill="#fff" transform="rotate(-9 40.5 52)" />,
  <Rect key="e2" x={56} y={43} width={7} height={18} rx={3.5} fill="#fff" transform="rotate(9 59.5 52)" />,
];

export function avParse(bot) {
  const parts = String((bot && bot.avatar) || '').split(':');
  const sh = parts[0], colr = parts[1];
  return {
    shape: AV_SHAPES.includes(sh) ? sh : '',
    color: /^#[0-9a-f]{6}$/i.test(colr || '') ? colr : '',
  };
}

export function avSpec(bot) {
  const p = avParse(bot);
  const seed = avHash((bot && (bot.id || bot.name)) || 'bot');
  return {
    shape: p.shape || AV_SHAPES[seed % AV_SHAPES.length],
    color: p.color || AV_COLORS[(seed >>> 4) % AV_COLORS.length],
  };
}

export function AvatarSvg({ bot, size }) {
  const { shape, color } = avSpec(bot);
  const s = size || 40;
  return (
    <Svg viewBox="0 0 100 100" width={s} height={s}>
      <G fill={color}>
        <ShapeEl shape={shape} />
        {EYES}
      </G>
    </Svg>
  );
}

export default function Avatar({ bot, size, badge }) {
  const s = size || 40;
  const badgeSize = Math.max(18, size * 0.32);
  return (
    <View style={{ width: s, height: s, alignItems: 'center', justifyContent: 'center' }}>
      <AvatarSvg bot={bot} size={s} />
      {!!badge && (
        <View
          collapsable={false}
          style={{
            position: 'absolute', right: -2, bottom: -2,
            width: badgeSize, height: badgeSize, borderRadius: badgeSize / 2,
            backgroundColor: '#FF9500', alignItems: 'center', justifyContent: 'center',
            borderWidth: 2, borderColor: '#ffffff',
          }}
        >
          <Text style={{ color: '#fff', fontSize: Math.max(10, s * 0.19), fontWeight: '800' }}>★</Text>
        </View>
      )}
    </View>
  );
}
