import { useEffect, useRef } from 'react';
import { LAYERS, cellFill, describe } from '../layers.js';

export { LAYERS };

const CELL = 40;
const GAP = 3;
const BUND = 12;
const MID = 22;

// The two fields seen from above: field A, the bund between them, field B. Each square is coloured by the chosen
// layer; planted squares carry a rice tuft that grows with the crop; a magenta dot marks a pest sighting.
function Tuft({ x, y, progress, dead, harvested }) {
  if (harvested) {
    return <g stroke="var(--straw)" strokeWidth="1.6" strokeLinecap="round" opacity="0.9">
      {[-7, 0, 7].map((dx) => <path key={dx} d={`M${x + dx} ${y + 7} v-3`} />)}
    </g>;
  }
  if (dead) {
    return <path d={`M${x - 6} ${y - 6} l12 12 M${x + 6} ${y - 6} l-12 12`} stroke="var(--ink-3)" strokeWidth="1.6" strokeLinecap="round" />;
  }
  const h = 6 + 11 * Math.min(1, Math.max(0, progress ?? 0));
  const ripe = (progress ?? 0) >= 0.9;
  const col = ripe ? 'var(--straw)' : 'var(--rice)';
  return (
    <g stroke={col} strokeWidth="1.8" strokeLinecap="round" fill="none">
      <path d={`M${x} ${y + 8} q-1 ${-h / 2} -5 ${-h}`} />
      <path d={`M${x} ${y + 8} v${-h - 2}`} />
      <path d={`M${x} ${y + 8} q1 ${-h / 2} 5 ${-h}`} />
      {ripe ? <circle cx={x} cy={y + 6 - h - 2} r="1.8" fill="var(--straw)" stroke="none" /> : null}
    </g>
  );
}

function FieldBlock({ f, x0, y0, layer, selected, onSelect, focusRef }) {
  const rows = f.rows;
  const cols = f.cols;
  const w = cols * CELL + (cols - 1) * GAP + 2 * BUND;
  const h = rows * CELL + (rows - 1) * GAP + 2 * BUND;
  return (
    <g>
      <text x={x0 + w / 2} y={y0 - 10} textAnchor="middle" fontSize="15" fontWeight="600" fill="var(--ink)" style={{ fontFamily: 'var(--serif)' }}>Field {f.name}</text>
      <rect x={x0} y={y0} width={w} height={h} rx="9" fill="var(--bund)" />
      <rect x={x0 + BUND - 2} y={y0 + BUND - 2} width={w - 2 * BUND + 4} height={h - 2 * BUND + 4} rx="4" fill="var(--panel)" />
      {f.squares.flat().map((s) => {
        const cx = x0 + BUND + (s.col - 1) * (CELL + GAP);
        const cy = y0 + BUND + (s.row - 1) * (CELL + GAP);
        const [fill, op] = cellFill(layer, s);
        const isSel = selected === s.sq;
        return (
          <g key={s.sq} className={`sq${isSel ? ' selected' : ''}`} role="button" tabIndex={isSel ? 0 : -1} aria-label={describe(s, layer)}
             ref={isSel ? focusRef : null} onClick={() => onSelect(s.sq)}
             onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(s.sq); } }}>
            <rect x={cx} y={cy} width={CELL} height={CELL} rx="3" fill="var(--panel)" />
            <rect x={cx} y={cy} width={CELL} height={CELL} rx="3" fill={fill} fillOpacity={op} />
            {s.planted ? <Tuft x={cx + CELL / 2} y={cy + CELL / 2} progress={s.progress} dead={!s.alive && !s.harvested} harvested={s.harvested} /> : null}
            {s.pest ? <circle cx={cx + CELL - 7} cy={cy + 7} r={2.6 + 5 * Math.min(1, s.pest.severity / 0.2)} fill="var(--risk-4)" stroke="var(--panel)" strokeWidth="1.2" /> : null}
            {layer === 'establish' && s.establish != null && s.establish < 0.72 ? (
              <rect x={cx + 2} y={cy + 2} width={CELL - 4} height={CELL - 4} rx="2" fill="none" stroke="var(--rejected)" strokeDasharray="3 2" strokeWidth="1.2" />
            ) : null}
            <rect className="cell-outline" x={cx} y={cy} width={CELL} height={CELL} rx="3" fill="none" stroke="transparent" />
            <title>{describe(s, layer)}</title>
          </g>
        );
      })}
      {Array.from({ length: cols }, (_, c) => (
        <text key={`c${c}`} x={x0 + BUND + c * (CELL + GAP) + CELL / 2} y={y0 + h + 14} textAnchor="middle" fontSize="10" fill="var(--ink-3)">{c + 1}</text>
      ))}
    </g>
  );
}

export function FarmPlan({ fields, layer, selected, onSelect }) {
  const focusRef = useRef(null);
  const names = Object.keys(fields);
  const first = fields[names[0]];
  const rows = first?.rows || 6;
  const cols = first?.cols || 6;
  const w = cols * CELL + (cols - 1) * GAP + 2 * BUND;
  const h = rows * CELL + (rows - 1) * GAP + 2 * BUND;
  const total = 18 + w * names.length + MID * (names.length - 1) + 4;
  const blocks = names.map((n, k) => ({ f: fields[n], x0: 18 + k * (w + MID) }));

  useEffect(() => {
    if (focusRef.current && document.activeElement && document.activeElement.closest && document.activeElement.closest('.plan-svg')) {
      focusRef.current.focus();
    }
  }, [selected]);

  const onKey = (e) => {
    if (!selected) return;
    const m = /^([A-Z])(\d+),(\d+)$/.exec(selected);
    if (!m) return;
    let [, fname, r, c] = m;
    r = +r; c = +c;
    let fi = names.indexOf(fname);
    if (e.key === 'ArrowUp') r = Math.max(1, r - 1);
    else if (e.key === 'ArrowDown') r = Math.min(rows, r + 1);
    else if (e.key === 'ArrowLeft') { if (c > 1) c -= 1; else if (fi > 0) { fi -= 1; c = cols; } }
    else if (e.key === 'ArrowRight') { if (c < cols) c += 1; else if (fi < names.length - 1) { fi += 1; c = 1; } }
    else return;
    e.preventDefault();
    onSelect(`${names[fi]}${r},${c}`);
  };

  return (
    <svg className="plan-svg" viewBox={`0 0 ${total} ${h + 50}`} onKeyDown={onKey} role="group" aria-label="The two paddy fields, square by square">
      <g transform="translate(0, 26)">
        {Array.from({ length: rows }, (_, r) => (
          <text key={`r${r}`} x="8" y={BUND + r * (CELL + GAP) + CELL / 2 + 4} textAnchor="middle" fontSize="10" fill="var(--ink-3)">{r + 1}</text>
        ))}
        {blocks.map(({ f, x0 }) => (
          <FieldBlock key={f.name} f={f} x0={x0} y0={0} layer={layer} selected={selected} onSelect={onSelect} focusRef={focusRef} />
        ))}
      </g>
    </svg>
  );
}

export function LayerLegend({ layer }) {
  if (layer === 'water') {
    return (
      <div className="legend">
        <span>below the surface</span>
        <span className="ramp">{[0.8, 0.55, 0.3, 0.12].map((o) => <span key={o} style={{ background: 'var(--mud)', opacity: o }} />)}
          {[0.18, 0.4, 0.65, 0.9].map((o) => <span key={`w${o}`} style={{ background: 'var(--water)', opacity: o }} />)}</span>
        <span>standing water (to 100 mm)</span>
      </div>
    );
  }
  if (layer === 'soil') {
    return (
      <div className="legend">
        <span><i className="sw" style={{ background: 'var(--rice)', opacity: 0.55 }} />suitable</span>
        <span><i className="sw" style={{ background: 'var(--straw)', opacity: 0.65 }} />needs amendment</span>
        <span><i className="sw" style={{ background: 'var(--mud)', opacity: 0.85 }} />unsuitable</span>
        <span className="muted">soil-suitability forest, from the probe or the daily scan</span>
      </div>
    );
  }
  if (layer === 'risk') {
    return (
      <div className="legend">
        <span>chance of passing the 8% threshold within 7 days</span>
        <span className="ramp">{[0, 1, 2, 3, 4].map((k) => <span key={k} style={{ background: `var(--risk-${k})` }} />)}</span>
        <span>0 → 100%</span>
        <span><i className="sw" style={{ background: 'var(--risk-4)', borderRadius: '50%' }} />pest seen (bigger = more severe)</span>
      </div>
    );
  }
  if (layer === 'establish') {
    return (
      <div className="legend">
        <span>chance a seedling planted now establishes</span>
        <span className="ramp">{[0.15, 0.35, 0.55, 0.75, 0.95].map((o) => <span key={o} style={{ background: 'var(--rice)', opacity: o }} />)}</span>
        <span><i className="sw" style={{ border: '1.5px dashed var(--rejected)', background: 'transparent' }} />below the 72% planting rule</span>
      </div>
    );
  }
  if (layer === 'health') {
    return <div className="legend"><span>plant health seen by the cameras</span><span className="ramp">{[0.15, 0.35, 0.55, 0.75, 0.95].map((o) => <span key={o} style={{ background: 'var(--rice)', opacity: o }} />)}</span><span>poor → good</span></div>;
  }
  return (
    <div className="legend">
      <span><i className="sw" style={{ background: 'var(--mud-soft)', opacity: 0.55 }} />not planted</span>
      <span className="ramp">{[0.2, 0.4, 0.6, 0.8].map((o) => <span key={o} style={{ background: 'var(--rice)', opacity: o }} />)}</span>
      <span>transplanting → grain filling</span>
      <span><i className="sw" style={{ background: 'var(--straw)', opacity: 0.85 }} />ripening</span>
      <span><i className="sw" style={{ background: 'var(--mud)', opacity: 0.5 }} />seedling died</span>
    </div>
  );
}
