import { useEffect, useRef, useState } from 'react';
import { line, scaleBand, scaleLinear } from 'd3';
import { fmtDate, num } from '../util.js';

const M = { top: 14, right: 46, bottom: 26, left: 46 };
const H = 220;

// The drawing is as wide as its box in CSS pixels, so the 11 px labels stay 11 px on a phone.
function useBoxWidth(fallback = 560) {
  const [el, setEl] = useState(null);
  const [w, setW] = useState(fallback);
  useEffect(() => {
    if (!el) return undefined;
    const measure = (cw) => { if (cw > 0) setW(Math.max(280, Math.min(900, Math.round(cw)))); };
    measure(el.clientWidth);
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver((entries) => measure(entries[0].contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return [w, setEl];
}

// One measure, one axis. Series are told apart by colour, a legend and a label at the end of each line.
export function LineChart({ title, rows, series, yLabel, refs = [], yDomain, minHi = 1, fmt = (v) => num(v), note }) {
  const [hover, setHover] = useState(null);
  const ref = useRef(null);
  const [W, boxRef] = useBoxWidth();
  const days = rows.map((r) => r.day);
  const x = scaleLinear().domain([Math.min(...days, 0), Math.max(...days, 1)]).range([M.left, W - M.right]);
  const vals = rows.flatMap((r) => series.map((s) => r[s.key])).filter((v) => v != null);
  const lo = yDomain ? yDomain[0] : Math.min(0, ...vals, ...refs.map((r) => r.y));
  const hi = yDomain ? yDomain[1] : Math.max(minHi, ...vals.map((v) => v * 1.1), ...refs.map((r) => r.y));
  const y = scaleLinear().domain([lo, hi]).nice().range([H - M.bottom, M.top]);
  const ticks = y.ticks(5);
  const xt = x.ticks(W < 440 ? 4 : 6);
  const onMove = (e) => {
    const box = ref.current.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    const d = x.invert(px);
    let best = null;
    for (const r of rows) if (best === null || Math.abs(r.day - d) < Math.abs(best.day - d)) best = r;
    setHover(best);
  };
  if (!rows.length) {
    return <section className="panel chart"><header><h2>{title}</h2></header><div className="body" ref={boxRef}><p className="empty">Nothing yet: the lines start on the first day.</p></div></section>;
  }
  const last = rows[rows.length - 1];
  return (
    <section className="panel chart">
      <header>
        <h2>{title}</h2>
        <div className="chart-legend">{series.map((s) => <span key={s.key}><i style={s.dash ? { background: 'none', borderTop: `2px dashed ${s.color}`, height: 0 } : { background: s.color }} />{s.label}</span>)}</div>
      </header>
      <div className="body" style={{ position: 'relative' }} ref={boxRef}>
        <svg ref={ref} viewBox={`0 0 ${W} ${H}`} onMouseMove={onMove} onMouseLeave={() => setHover(null)} role="img"
             aria-label={`${title}: ${series.map((s) => `${s.label} ${fmt(last[s.key])}`).join(', ')} on day ${last.day}`}>
          <g className="grid">{ticks.map((t) => <line key={t} x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} />)}</g>
          <g className="axis">
            {ticks.map((t) => <text key={t} x={M.left - 6} y={y(t) + 4} textAnchor="end">{fmt(t)}</text>)}
            {xt.map((t) => <text key={t} x={x(t)} y={H - 8} textAnchor="middle">day {t}</text>)}
            {yLabel ? <text x={M.left} y={M.top - 4} textAnchor="start">{yLabel}</text> : null}
          </g>
          {refs.map((r) => <line key={r.label} className="ref" x1={M.left} x2={W - M.right} y1={y(r.y)} y2={y(r.y)} stroke={r.color || 'var(--ink-3)'} />)}
          {series.map((s) => {
            const pts = rows.filter((r) => r[s.key] != null);
            const d = line().x((r) => x(r.day)).y((r) => y(r[s.key]))(pts);
            return <path key={s.key} d={d || ''} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" strokeDasharray={s.dash || undefined} />;
          })}
          {/* labels on top of the lines, with a halo in the panel colour so a crossing line never hides them */}
          {refs.map((r) => <text key={r.label} className="ref-label halo" x={M.left + 4} y={y(r.y) - 4} fill={r.color || 'var(--ink-3)'}>{r.label}</text>)}
          {endLabels(series, rows, x, y).map((e) => (
            <text key={e.key} className="halo" x={e.x} y={e.y} fontSize="11" fill="var(--ink-2)" fontWeight="600">{e.label}</text>
          ))}
          {hover ? (
            <g className="tip">
              <line x1={x(hover.day)} x2={x(hover.day)} y1={M.top} y2={H - M.bottom} stroke="var(--ink-3)" />
              {series.map((s) => hover[s.key] != null ? <circle key={s.key} cx={x(hover.day)} cy={y(hover[s.key])} r="4" fill={s.color} stroke="var(--panel)" strokeWidth="2" /> : null)}
            </g>
          ) : null}
        </svg>
        {hover ? (
          <div className="tooltip" style={{ left: `${Math.min(70, (x(hover.day) / W) * 100)}%`, top: 8 }}>
            <strong>day {hover.day}</strong> {hover.date ? fmtDate(hover.date) : ''}
            {series.map((s) => <div key={s.key}><i style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 2, background: s.color, marginRight: 6 }} />{s.label}: {fmt(hover[s.key])}</div>)}
          </div>
        ) : null}
        {note ? <p className="small muted" style={{ marginTop: 4 }}>{note}</p> : null}
      </div>
    </section>
  );
}

// Direct labels at the end of each line, nudged apart so that lines ending close together keep readable labels.
function endLabels(series, rows, x, y) {
  const out = [];
  for (const s of series) {
    const pts = rows.filter((r) => r[s.key] != null);
    const end = pts[pts.length - 1];
    if (end) out.push({ key: s.key, label: s.short || s.label, x: x(end.day) + 5, y: y(end[s.key]) + 4 });
  }
  out.sort((a, b) => a.y - b.y);
  for (let i = 1; i < out.length; i++) if (out[i].y - out[i - 1].y < 12) out[i].y = out[i - 1].y + 12;
  return out;
}

export function BarChart({ title, rows, valueKey, label, color = 'var(--water)', fmt = (v) => num(v, 1), note }) {
  const [hover, setHover] = useState(null);
  const [W, boxRef] = useBoxWidth();
  if (!rows.length) return null;
  const x = scaleBand().domain(rows.map((r) => r.day)).range([M.left, W - M.right]).paddingInner(0.15);
  const hi = Math.max(5, ...rows.map((r) => r[valueKey] || 0));
  const y = scaleLinear().domain([0, hi]).nice().range([H - M.bottom, M.top]);
  const ticks = y.ticks(4);
  const xt = rows.filter((r, i) => i % Math.ceil(rows.length / (W < 440 ? 4 : 6)) === 0);
  return (
    <section className="panel chart">
      <header><h2>{title}</h2></header>
      <div className="body" style={{ position: 'relative' }} ref={boxRef}>
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${title}, ${rows.length} days`} onMouseLeave={() => setHover(null)}>
          <g className="grid">{ticks.map((t) => <line key={t} x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} />)}</g>
          <g className="axis">
            {ticks.map((t) => <text key={t} x={M.left - 6} y={y(t) + 4} textAnchor="end">{fmt(t)}</text>)}
            {xt.map((r) => <text key={r.day} x={x(r.day) + x.bandwidth() / 2} y={H - 8} textAnchor="middle">day {r.day}</text>)}
            <text x={M.left} y={M.top - 4}>{label}</text>
          </g>
          {rows.map((r) => (r[valueKey] > 0 ? (
            <rect key={r.day} x={x(r.day)} width={Math.max(1, x.bandwidth())} y={y(r[valueKey])} height={y(0) - y(r[valueKey])} rx="1.5"
                  fill={color} opacity={hover && hover.day === r.day ? 1 : 0.8} onMouseEnter={() => setHover(r)} />
          ) : null))}
          {rows.map((r) => <rect key={`h${r.day}`} x={x(r.day)} width={x.step()} y={M.top} height={H - M.top - M.bottom} fill="transparent" onMouseEnter={() => setHover(r)} />)}
        </svg>
        {hover ? <div className="tooltip" style={{ left: `${Math.min(75, ((x(hover.day) || 0) / W) * 100)}%`, top: 8 }}><strong>day {hover.day}</strong> {fmtDate(hover.date)}: {fmt(hover[valueKey])} mm</div> : null}
        {note ? <p className="small muted" style={{ marginTop: 4 }}>{note}</p> : null}
      </div>
    </section>
  );
}

function RunVsBaseline({ farm, rows }) {
  const base = farm?.baseline;
  const last = rows[rows.length - 1];
  const bh = base && base.status === 'done' ? base.history : null;
  const bAt = bh && last ? bh.filter((h) => h.day <= last.day).pop() : null;
  const avg = (r, k) => (r ? ((r[`A_${k}`] ?? 0) + (r[`B_${k}`] ?? 0)) / 2 : null);
  const fA = farm?.fields?.A || {};
  const fB = farm?.fields?.B || {};
  const yieldNow = fA.yield_t_ha != null && fB.yield_t_ha != null ? (fA.yield_t_ha + fB.yield_t_ha) / 2 : null;
  const forecast = fA.yield_forecast_t_ha != null && fB.yield_forecast_t_ha != null ? (fA.yield_forecast_t_ha + fB.yield_forecast_t_ha) / 2 : null;
  const sprayed = (fA.sprayed_squares || 0) + (fB.sprayed_squares || 0);
  return (
    <section className="panel chart">
      <header><h2>This season against conventional practice</h2><span className="muted small">same fields, same weather, run alongside</span></header>
      <div className="body tbl-wrap">
        {!base ? <p className="muted">No comparison run for this season.</p> : base.status !== 'done' ? <p className="muted">The conventional run for comparison is still going…</p> : null}
        <table className="tbl">
          <thead><tr><th>Mean of both fields</th><th>This season</th><th>Conventional</th></tr></thead>
          <tbody>
            <tr><td>Yield</td><td>{yieldNow != null ? `${num(yieldNow, 2)} t/ha` : forecast != null ? `forecast ${num(forecast, 1)} t/ha` : '–'}</td>
              <td>{base?.summary ? `${num(base.summary.yield_t_ha, 2)} t/ha (season)` : '–'}</td></tr>
            <tr><td>Irrigation so far</td><td>{last ? `${num(avg(last, 'irr'))} mm` : '–'}</td><td>{bAt ? `${num(avg(bAt, 'irr'))} mm` : '–'}</td></tr>
            <tr><td>Nitrogen so far</td><td>{last ? `${num(avg(last, 'n'))} kg N/ha` : '–'}</td><td>{bAt ? `${num(avg(bAt, 'n'))} kg N/ha` : '–'}</td></tr>
            <tr><td>Area sprayed</td><td>{num(sprayed)} m² so far</td><td>{base?.summary ? `${num(base.summary.sprayed_squares)} m² (season)` : '–'}</td></tr>
            <tr><td>Methane</td><td>{fA.ch4_kg_ha != null ? `${num(((fA.ch4_kg_ha || 0) + (fB.ch4_kg_ha || 0)) / 2)} kg/ha so far` : '–'}</td>
              <td>{base?.summary ? `${num(base.summary.ch4_kg_ha)} kg/ha (season)` : '–'}</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function ChartsView({ farm, weather }) {
  const A = farm?.fields?.A?.history || [];
  const B = farm?.fields?.B?.history || [];
  const byDay = new Map();
  for (const r of A) byDay.set(r.day, { ...r });
  for (const r of B) byDay.set(r.day, { ...(byDay.get(r.day) || {}), ...r });
  const bh = farm?.baseline?.status === 'done' ? farm.baseline.history : [];
  const lastDay = Math.max(-1, ...[...byDay.keys()]);
  for (const h of bh) {
    if (h.day > lastDay) continue;
    const r = byDay.get(h.day) || { day: h.day, date: h.date };
    r.C_irr = ((h.A_irr ?? 0) + (h.B_irr ?? 0)) / 2;
    r.C_n = ((h.A_n ?? 0) + (h.B_n ?? 0)) / 2;
    byDay.set(h.day, r);
  }
  const rows = [...byDay.values()].sort((a, b) => a.day - b.day);
  const rain = (weather?.daily || []).map((r) => ({ ...r, rain: r.rain_mm }));
  const sA = 'var(--series-a)';
  const sB = 'var(--series-b)';
  return (
    <div className="charts">
      <LineChart title="Water level" rows={rows} yLabel="mm" yDomain={[-300, 160]}
                 series={[{ key: 'A_h', label: 'Field A', short: 'A', color: sA, dy: -6 }, { key: 'B_h', label: 'Field B', short: 'B', color: sB, dy: 8 }]}
                 refs={[{ y: 150, label: 'bund', color: 'var(--bund)' }, { y: 0, label: 'surface', color: 'var(--ink-2)' }, { y: -150, label: 'AWD', color: 'var(--water)' }]}
                 fmt={(v) => num(v)} note="Positive: standing water. Negative: the water table below the surface. Safe AWD re-floods at −150 mm." />
      <BarChart title="Rain" rows={rain} valueKey="rain" label="mm per day" note="The rain gauge, day by day." />
      <LineChart title="Irrigation so far" rows={rows} yLabel="mm"
                 series={[{ key: 'A_irr', label: 'Field A', short: 'A', color: sA, dy: -6 }, { key: 'B_irr', label: 'Field B', short: 'B', color: sB, dy: 8 },
                          ...(bh.length ? [{ key: 'C_irr', label: 'Conventional (same weather)', short: 'conv.', color: 'var(--ink-3)', dash: '5 4' }] : [])]}
                 note="Pumped water per field; the grey dashed line is continuous flooding on the same fields and weather." />
      <LineChart title="Crop progress" rows={rows} yDomain={[0, 1.1]} fmt={(v) => (v == null ? '–' : `${Math.round(v * 100)}%`)}
                 series={[{ key: 'A_prog', label: 'Field A', short: 'A', color: sA, dy: -6 }, { key: 'B_prog', label: 'Field B', short: 'B', color: sB, dy: 8 }]}
                 refs={[{ y: 0.45, label: 'PI', color: 'var(--ink-3)' }, { y: 0.6, label: 'flower', color: 'var(--ink-3)' }, { y: 0.9, label: 'drain', color: 'var(--ink-3)' }]}
                 note="Thermal time from transplanting (100% = maturity)." />
      <LineChart title="Pests seen" rows={rows} yLabel="mean severity" minHi={0.02} fmt={(v) => (v == null ? '–' : `${(v * 100).toFixed(1)}%`)}
                 series={[{ key: 'A_pest', label: 'Field A', short: 'A', color: sA, dy: -6 }, { key: 'B_pest', label: 'Field B', short: 'B', color: sB, dy: 8 }]}
                 note="Average camera severity over all squares." />
      <LineChart title="Nitrogen applied" rows={rows} yLabel="kg N/ha"
                 series={[{ key: 'A_n', label: 'Field A', short: 'A', color: sA, dy: -6 }, { key: 'B_n', label: 'Field B', short: 'B', color: sB, dy: 8 },
                          ...(bh.length ? [{ key: 'C_n', label: 'Conventional', short: 'conv.', color: 'var(--ink-3)', dash: '5 4', dy: 14 }] : [])]}
                 refs={[{ y: 150, label: 'cap', color: 'var(--rejected)' }]} />
      <RunVsBaseline farm={farm} rows={rows} />
    </div>
  );
}
