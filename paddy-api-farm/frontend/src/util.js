// Formatting shared by the views.
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

export function parseIso(iso) {
  if (!iso) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/.exec(iso);
  if (!m) return null;
  return { y: +m[1], mo: +m[2], d: +m[3], h: m[4] ? +m[4] : 0, mi: m[5] ? +m[5] : 0 };
}

export function fmtWhen(iso, withDay = true) {
  const p = parseIso(iso);
  if (!p) return '–';
  const dt = new Date(Date.UTC(p.y, p.mo - 1, p.d));
  const day = withDay ? `${DAYS[dt.getUTCDay()]} ` : '';
  return `${day}${p.d} ${MONTHS[p.mo - 1]} ${p.y}, ${String(p.h).padStart(2, '0')}:${String(p.mi).padStart(2, '0')}`;
}

export function fmtShort(iso) {
  const p = parseIso(iso);
  if (!p) return '–';
  return `${p.d} ${MONTHS[p.mo - 1]}, ${String(p.h).padStart(2, '0')}:${String(p.mi).padStart(2, '0')}`;
}

export function fmtDate(iso) {
  const p = parseIso(iso);
  if (!p) return '–';
  return `${p.d} ${MONTHS[p.mo - 1]}`;
}

export const pct = (x, nd = 0) => (x == null || Number.isNaN(x) ? '–' : `${(x * 100).toFixed(nd)}%`);
export const num = (x, nd = 0) => (x == null || Number.isNaN(x) ? '–' : Number(x).toLocaleString('en-IN', { minimumFractionDigits: nd, maximumFractionDigits: nd }));
export const mm = (x) => (x == null ? '–' : `${x > 0 ? '+' : x < 0 ? '−' : ''}${Math.abs(Math.round(x))} mm`);
export const pestName = (p) => (p || '').replaceAll('_', ' ');

export const ACTION_LABEL = {
  soil_check: 'Soil check', amend: 'Amend soil', puddle: 'Puddle', transplant: 'Transplant', irrigate: 'Irrigate', drain: 'Drain', hold: 'Hold',
  fertilise: 'Fertilise', scout: 'Scout', spray: 'Spray', harvest: 'Harvest', wait: 'Call me later',
};

export const STATUS_LABEL = {
  proposed: 'proposed', rejected: 'rejected by the guardrails', pending_approval: 'waiting for your approval', rejected_by_person: 'rejected by you',
  scheduled: 'scheduled', executed: 'carried out', failed: 'failed', skipped: 'not carried out', expired: 'expired',
};

export const PHASE_LABEL = { land_prep: 'land preparation', puddled: 'puddled, ready to transplant', growing: 'growing', drain_down: 'drained for harvest',
  harvested: 'harvested', abandoned: 'abandoned' };

export function sourceLabel(src) {
  if (!src) return { text: '–', cls: '' };
  if (src === 'api') return { text: 'decision API', cls: 'src-api' };
  if (src === 'mock') return { text: 'stand-in mock API', cls: 'src-mock' };
  if (src === 'builtin') return { text: 'built-in manager', cls: '' };
  if (src === 'conventional') return { text: 'conventional practice', cls: '' };
  if (src === 'replay') return { text: 'replayed reply', cls: '' };
  if (src.startsWith('fallback')) return { text: src === 'fallback-builtin' ? 'fallback: built-in manager' : 'fallback: hold', cls: 'src-fallback' };
  return { text: src, cls: '' };
}

export function describeDecision(d) {
  const p = d.params || {};
  const sq = d.squares ? (d.squares.length <= 4 ? d.squares.join(', ') : `${d.squares.length} squares`) : 'whole field';
  switch (d.action) {
    case 'irrigate': return `to ${p.target_depth_mm} mm`;
    case 'drain': return `to ${p.to_depth_mm} mm`;
    case 'fertilise': return `${p.kg_per_ha} kg/ha ${p.product}${p.split && p.split !== 'other' ? ` (${p.split.replaceAll('_', ' ')})` : ''}, ${sq}`;
    case 'amend': return `${p.product} ${p.dose} ${p.product === 'leach' ? 'mm' : 'g/m²'}, ${sq}`;
    case 'spray': return `${p.product}${p.target_pest ? ` on ${pestName(p.target_pest)}` : ''}, ${sq}`;
    case 'transplant': case 'soil_check': case 'scout': return sq;
    case 'harvest': return p.override_immature ? 'even though immature' : '';
    case 'wait': return `${p.hours ?? 12} h`;
    default: return '';
  }
}
