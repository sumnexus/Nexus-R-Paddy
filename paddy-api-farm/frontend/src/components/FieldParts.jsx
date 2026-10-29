import { useEffect, useState } from 'react';
import { ACTION_LABEL, PHASE_LABEL, mm, num, pct, pestName } from '../util.js';

const STAGE_ENDS = [0.14, 0.45, 0.6, 0.7, 0.9];

// The field's water tube as an instrument: −300 mm (tube bottom) to the 150 mm bund, with the soil surface and the
// safe-AWD line at −150 mm. Standing water shows blue above the surface; below it the water table is drawn inside
// the soil.
export function WaterTube({ name, level, target, pumping, draining, threshold }) {
  const H = 260;
  const top = 18;
  const bottom = H - 26;
  const lo = -300;
  const hi = 150;
  const y = (v) => top + (hi - Math.max(lo, Math.min(hi, v))) / (hi - lo) * (bottom - top);
  const x0 = 36;
  const w = 18;
  const lv = level ?? lo;
  const state = pumping ? 'filling' : draining ? 'draining' : '';
  return (
    <figure className="gauge" style={{ margin: 0 }} aria-label={`Field ${name} water tube: ${mm(level)}${state ? `, ${state}` : ''}`}>
      <svg viewBox={`0 0 80 ${H}`} preserveAspectRatio="xMidYMid meet">
        <text x={x0 + w / 2} y="11" textAnchor="middle" className="lbl" style={{ fontWeight: 600, fill: 'var(--ink)' }}>Tube {name}</text>
        <rect x={x0} y={y(0)} width={w} height={bottom - y(0)} fill="var(--mud-soft)" />
        {lv >= 0 ? (
          <>
            <rect x={x0} y={y(0)} width={w} height={bottom - y(0)} fill="var(--water)" opacity="0.28" />
            <rect x={x0} y={y(lv)} width={w} height={y(0) - y(lv)} fill="var(--water)" opacity="0.85" />
          </>
        ) : (
          <rect x={x0} y={y(lv)} width={w} height={bottom - y(lv)} fill="var(--water)" opacity="0.4" />
        )}
        <rect x={x0} y={top} width={w} height={bottom - top} rx="3" fill="none" stroke="var(--ink-3)" />
        <line x1={x0 - 6} x2={x0 + w + 6} y1={y(0)} y2={y(0)} stroke="var(--ink)" strokeWidth="1.4" />
        <line x1={x0 - 4} x2={x0 + w + 4} y1={y(150)} y2={y(150)} stroke="var(--bund)" strokeWidth="2" />
        <line x1={x0 - 4} x2={x0 + w + 4} y1={y(-150)} y2={y(-150)} stroke="var(--water)" strokeDasharray="3 2" />
        {threshold === 0 ? <line x1={x0 - 4} x2={x0 + w + 4} y1={y(0) + 2} y2={y(0) + 2} stroke="var(--water)" strokeDasharray="3 2" /> : null}
        {target != null ? <path d={`M${x0 + w + 2} ${y(target)} l6 -4 v8 z`} fill="var(--water)" /> : null}
        <text x={x0 - 8} y={y(150) + 4} textAnchor="end" className="lbl">150</text>
        <text x={x0 - 8} y={y(0) + 4} textAnchor="end" className="lbl">0</text>
        <text x={x0 - 8} y={y(-150) + 4} textAnchor="end" className="lbl">−150</text>
        <text x={x0 - 8} y={y(-300) + 4} textAnchor="end" className="lbl">−300</text>
        <text x={x0 + w / 2} y={H - 8} textAnchor="middle" className="val">{mm(level)}</text>
      </svg>
      {state ? <figcaption className="small muted" style={{ textAlign: 'center' }}>{state}</figcaption> : null}
    </figure>
  );
}

export function Sparkline({ values, zero = true, label }) {
  const pts = values.filter((v) => v != null);
  if (pts.length < 2) return <p className="small muted">The water level line appears after a few days.</p>;
  const W = 300;
  const H = 44;
  const lo = Math.min(-160, ...pts);
  const hi = Math.max(60, ...pts);
  const x = (i) => (i / (values.length - 1)) * W;
  const y = (v) => H - 4 - ((v - lo) / (hi - lo)) * (H - 8);
  let d = '';
  values.forEach((v, i) => { if (v != null) d += `${d ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`; });
  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={label}>
      {zero ? <line x1="0" x2={W} y1={y(0)} y2={y(0)} stroke="var(--ink-3)" strokeWidth="0.8" /> : null}
      <line x1="0" x2={W} y1={y(-150)} y2={y(-150)} stroke="var(--water)" strokeWidth="0.8" strokeDasharray="3 3" />
      <path d={d} fill="none" stroke="var(--water)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function MiniLine({ values, color, lo, hi, label }) {
  const pts = values.map((v, i) => [i, v]).filter(([, v]) => v != null);
  if (pts.length < 2) return null;
  const W = 300;
  const H = 36;
  const x = (i) => (i / Math.max(1, values.length - 1)) * W;
  const y = (v) => H - 3 - ((Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo || 1)) * (H - 6);
  const d = pts.map(([i, v], k) => `${k ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join('');
  return <svg className="spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={label}><path d={d} fill="none" stroke={color} strokeWidth="2" vectorEffect="non-scaling-stroke" /></svg>;
}

export function FieldCard({ f }) {
  if (!f) return null;
  const hist = (f.history || []).slice(-45).map((h) => h[`${f.name}_h`]);
  const growing = f.phase === 'growing' || f.phase === 'drain_down';
  return (
    <section className="panel fcard" aria-label={`Field ${f.name}`}>
      <h3>Field {f.name} <span className="phase">{PHASE_LABEL[f.phase] || f.phase}</span></h3>
      {growing ? (
        <>
          <div className="prog" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((f.progress || 0) * 100)} aria-label="Crop progress">
            <span style={{ width: `${Math.min(100, (f.progress || 0) * 100)}%` }} />
            {STAGE_ENDS.map((e) => <i key={e} style={{ left: `${e * 100}%` }} />)}
          </div>
          <p className="small"><strong>{f.stage}</strong> <span className="muted">{pct(f.progress)} of the way to maturity</span></p>
        </>
      ) : null}
      <dl className="kv">
        <dt>Water tube</dt><dd>{mm(f.water_mm)}</dd>
        {f.transplanted ? <><dt>Transplanted</dt><dd>{f.transplanted}</dd></> : null}
        {f.days_to_harvest != null ? <><dt>Days to harvest</dt><dd>about {f.days_to_harvest}</dd></> : null}
        {f.yield_forecast_t_ha != null && f.yield_t_ha == null ? <><dt>Yield forecast</dt><dd>{num(f.yield_forecast_t_ha, 1)} t/ha</dd></> : null}
        {f.plants?.planted ? <><dt>Plants alive</dt><dd>{f.plants.alive} of {f.plants.planted}</dd></> : null}
        <dt>Irrigation</dt><dd>{num(f.irrigation_mm)} mm</dd>
        <dt>Rain</dt><dd>{num(f.rain_mm)} mm</dd>
        <dt>Nitrogen</dt><dd>{num(f.n_kg_ha)} kg/ha</dd>
        <dt>Sprays</dt><dd>{num(f.sprays)}{f.sprayed_squares ? ` (${f.sprayed_squares} m²)` : ''}</dd>
        <dt>Methane so far</dt><dd>{num(f.ch4_kg_ha, 1)} kg/ha</dd>
        {f.yield_t_ha != null ? <><dt>Yield</dt><dd><strong>{num(f.yield_t_ha, 2)} t/ha</strong></dd></> : null}
      </dl>
      <Sparkline values={hist} label={`Field ${f.name} water level over the last ${hist.length} days`} />
      <p className="small muted">Water level, last {hist.length} days; dashed line −150 mm</p>
    </section>
  );
}

export function SquarePanel({ sq, src, day, onClose }) {
  const [hist, setHist] = useState(null);
  const key = sq ? sq.sq : null;
  useEffect(() => {
    let live = true;
    if (!key || !src || !src.squareHistory) { setHist(null); return undefined; }
    const m = /^([A-Z])(\d+),(\d+)$/.exec(key);
    src.squareHistory(m[1], +m[2], +m[3]).then((h) => live && setHist(h.history)).catch(() => live && setHist(null));
    return () => { live = false; };
  }, [key, src, day]);
  if (!sq) {
    return (
      <section className="panel square-panel">
        <h2>A square</h2>
        <p className="muted small" style={{ marginTop: 6 }}>Select a square in the plan (or use the arrow keys) to see its readings and the advice for it.</p>
      </section>
    );
  }
  const soil = sq.soil || {};
  return (
    <section className="panel square-panel" aria-live="polite">
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <h2>Square {sq.sq}</h2>
        <span className="spacer" />
        <button className="linkish" onClick={onClose}>Close</button>
      </div>
      <div className="grid2" style={{ marginTop: 8 }}>
        <div><span>Water over it</span><span>{mm(sq.depth_mm)}</span></div>
        <div><span>Crop</span><span>{sq.planted ? (sq.alive ? sq.stage : sq.harvested ? 'harvested' : 'died') : 'not planted'}</span></div>
        <div><span>Health</span><span>{sq.health != null ? pct(sq.health) : '–'}</span></div>
        <div><span>Soil (forest)</span><span>{{ S: 'suitable', A: 'needs amendment', U: 'unsuitable' }[soil.code] || '–'}</span></div>
        <div><span>pH</span><span>{soil.ph ?? '–'}</span></div>
        {soil.ec != null ? <div><span>EC</span><span>{soil.ec} dS/m</span></div> : null}
        {soil.n != null ? <div><span>Available N</span><span>{soil.n} kg/ha</span></div> : null}
        {soil.oc != null ? <div><span>Organic C</span><span>{soil.oc}%</span></div> : null}
        {soil.sand != null ? <div><span>Sand / clay</span><span>{soil.sand}% / {soil.clay}%</span></div> : null}
        {soil.probed != null ? <div><span>Reading from</span><span>{soil.probed ? 'probe' : 'daily scan'}</span></div> : null}
        <div><span>Pest seen</span><span>{sq.pest ? `${pestName(sq.pest.pest)}, ${pct(sq.pest.severity, 1)}` : 'none'}</span></div>
        <div><span>Pest risk (7 d)</span><span>{sq.risk != null ? pct(sq.risk) : '–'}</span></div>
        <div><span>Establishment</span><span>{sq.establish != null ? pct(sq.establish) : '–'}</span></div>
        {sq.n_applied != null ? <div><span>N applied</span><span>{sq.n_applied} kg/ha</span></div> : null}
        {sq.sprays != null ? <div><span>Sprays</span><span>{sq.sprays}</span></div> : null}
        {sq.yield_kg != null ? <div><span>Harvested</span><span>{(sq.yield_kg * 1000).toFixed(0)} g</span></div> : null}
      </div>
      {hist && hist.length > 1 ? (
        <div style={{ marginTop: 10 }}>
          <p className="small muted">Water over the square, at every 06:00 and 18:00 so far</p>
          <MiniLine values={hist.map((h) => h.depth_mm)} color="var(--water)" lo={-300} hi={150} label="Water over the square" />
          {hist.some((h) => h.pest > 0) ? (
            <><p className="small muted">Pest severity seen</p>
              <MiniLine values={hist.map((h) => h.pest)} color="var(--risk-4)" lo={0} hi={Math.max(0.05, ...hist.map((h) => h.pest || 0))} label="Pest severity on the square" /></>
          ) : null}
        </div>
      ) : null}
      {soil.doses && soil.doses.length ? (
        <p className="small" style={{ marginTop: 8 }}>Suggested amendment: {soil.doses.map((d) => `${d.product} ${d.dose} ${d.unit}`).join(', ')}</p>
      ) : null}
    </section>
  );
}

export { ACTION_LABEL };
