// Design tokens straight from the web app (web/style.css :root) — the same
// off-white surfaces, black pill primaries, hairline dividers and soft shadows.
export const C = {
  bg: '#ffffff', panel: '#f7f7f6', panel2: '#f0f0ef', line: '#e6e6e4',
  text: '#0b0b0c', muted: '#8a8a8e', soft: '#f0f0ef',
  solid: '#111111', onSolid: '#ffffff',
  ok: '#1f9d55', okSoft: '#e4f5ea',
  warn: '#b7791f', warnSoft: '#fdf3e0',
  bad: '#d92d20', badSoft: '#fdeceb',
  // web status tints
  workBg: '#e7f0ff', work: '#1d4ed8',
  radius: 16,
};

export const baseApp = {
  flex: 1, backgroundColor: C.bg,
};

export const h1 = { color: C.text, fontSize: 22, fontWeight: '800' };
export const h2 = { color: C.text, fontSize: 16, fontWeight: '700' };
export const muted = { color: C.muted, fontSize: 13 };

export function col(el) { return el && el.color ? el.color : C.muted; }   // status -> color helper
export function statusColor(s) {
  return (s === 'ok' || s === 'running') ? C.ok : (s === 'error') ? C.bad : C.warn;
}
