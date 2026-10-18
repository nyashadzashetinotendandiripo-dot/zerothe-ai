// Dark palette matching the phone web app (web/style.css dark tokens).
export const C = {
  bg: '#0f1115', panel: '#171a20', panel2: '#20242c', line: '#2a2f38',
  text: '#f2f3f5', muted: '#9aa0aa', soft: '#20242c',
  solid: '#f2f3f5', onSolid: '#0f1115',
  ok: '#3ecf7a', okSoft: '#12281c',
  warn: '#e0a53a', warnSoft: '#2a2213',
  bad: '#ff6b60', badSoft: '#2c1512',
};

export const baseApp = {
  flex: 1, backgroundColor: C.bg,
};

export const h1 = { color: C.text, fontSize: 22, fontWeight: '800' };
export const h2 = { color: C.text, fontSize: 16, fontWeight: '700' };
export const muted = { color: C.muted, fontSize: 13 };

export function col(el) { return el && el.color ? el.color : C.muted; }   // status -> color helper
