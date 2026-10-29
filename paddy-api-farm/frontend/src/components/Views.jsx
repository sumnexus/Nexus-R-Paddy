import { useEffect, useState } from 'react';
import { num, pct } from '../util.js';

const MANAGER_LABEL = { builtin: 'Built-in manager', conventional: 'Conventional practice', api: 'Decision API', 'api-no-advice': 'Decision API without the advice',
  'builtin-cf': 'Built-in, continuous flooding' };

const METRICS = [
  ['yield_t_ha', 'Yield', 't/ha', 2],
  ['irrigation_mm', 'Irrigation', 'mm', 0],
  ['n_kg_ha', 'Nitrogen', 'kg N/ha', 0],
  ['ai_g_ha', 'Insecticide', 'g a.i./ha', 1],
  ['bio_kg_ha', 'Bt (microbial)', 'kg/ha', 2],
  ['sprayed_squares', 'Area sprayed', 'm²', 0],
  ['ch4_kg_ha', 'Methane', 'kg CH₄/ha', 0],
  ['calls', 'Decision calls', '', 0],
  ['tokens_in', 'Tokens sent', '', 0],
];

const mlabel = (m, labels) => (labels && labels[m]) || MANAGER_LABEL[m] || m;
const signed = (v, nd) => {           // "+0.28", "−366", and never "−0"
  const r = Number(v.toFixed(nd));
  if (r === 0) return num(0, nd);
  return `${r > 0 ? '+' : '−'}${num(Math.abs(r), nd)}`;
};

function PairedTable({ paired, base, labels }) {
  const ms = Object.keys(paired || {});
  if (!ms.length) return null;
  const rows = [['yield_t_ha', 'Yield', 't/ha', 2], ['irrigation_mm', 'Irrigation', 'mm', 0], ['n_kg_ha', 'Nitrogen', 'kg N/ha', 0],
                ['ai_g_ha', 'Insecticide', 'g a.i./ha', 1], ['ch4_kg_ha', 'Methane', 'kg CH₄/ha', 0]];
  return (
    <div className="tbl-wrap" style={{ marginTop: 12 }}>
      <table className="tbl">
        <thead><tr><th>Difference from {mlabel(base, labels)}, same seasons</th>{ms.map((m) => <th key={m}>{mlabel(m, labels)}</th>)}</tr></thead>
        <tbody>
          {rows.map(([k, label, unit, nd]) => (
            <tr key={k}>
              <td>{label} <span className="muted">({unit})</span></td>
              {ms.map((m) => {
                const x = paired[m]?.[k];
                if (!x || x.mean == null) return <td key={m}>–</td>;
                const ci = x.lo != null ? ` (${signed(x.lo, nd)} to ${signed(x.hi, nd)})` : '';
                return <td key={m} className="num">{signed(x.mean, nd)}{x.relative != null ? ` = ${signed(x.relative * 100, 0)}%` : ''}<span className="muted small">{ci}</span></td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CampaignTable({ campaign }) {
  const managers = campaign.managers;
  const S = campaign.summary;
  return (
    <section className="panel">
      <header><h2>{campaign.title || 'Campaign'}</h2><span className="muted small">{campaign.subtitle}</span></header>
      <div className="body">
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr><th>Mean per season (± 95% interval)</th>{managers.map((m) => <th key={m}>{mlabel(m, campaign.labels)}</th>)}</tr></thead>
            <tbody>
              <tr><td>Seasons</td>{managers.map((m) => <td key={m}>{S[m].seasons}</td>)}</tr>
              {METRICS.map(([k, label, unit, nd]) => (managers.some((m) => S[m]?.[k] && S[m][k].mean) ? (
                <tr key={k}>
                  <td>{label}{unit ? <span className="muted"> ({unit})</span> : null}</td>
                  {managers.map((m) => {
                    const x = S[m][k];
                    return <td key={m} className="num">{x ? `${num(x.mean, nd)} ± ${num((x.hi ?? x.mean) - x.mean, nd)}` : '–'}</td>;
                  })}
                </tr>
              ) : null))}
            </tbody>
          </table>
        </div>
        <PairedTable paired={campaign.paired} base={campaign.base} labels={campaign.labels} />
        {campaign.note ? <p className="small muted" style={{ marginTop: 8, maxWidth: '80ch' }}>{campaign.note}</p> : null}
      </div>
    </section>
  );
}

const ALL_MANAGERS = ['api', 'api-no-advice', 'builtin', 'conventional'];

function CompareRunner({ src, status, config, onError }) {
  const [job, setJob] = useState(null);
  const [managers, setManagers] = useState(ALL_MANAGERS);
  const [seasons, setSeasons] = useState(1);
  const [confirm, setConfirm] = useState(false);
  const mock = !!config?.mock;
  const real = managers.some((m) => m.startsWith('api')) && !mock;
  useEffect(() => {
    let live = true;
    let timer = null;
    const poll = () => src.compare().then((j) => {
      if (!live) return;
      setJob(j);
      if (j.status === 'running') timer = setTimeout(poll, 1500);
    }).catch(() => {});
    poll();
    return () => { live = false; clearTimeout(timer); };
  }, [src, job?.status === 'running' ? job.started : null]);
  const start = async () => {
    try {
      const j = await src.compareStart({ managers, seasons, confirm_real_api: confirm });
      setJob(j);
    } catch (e) { onError(e.message); }
  };
  const toggle = (m) => setManagers((ms) => (ms.includes(m) ? ms.filter((x) => x !== m) : [...ms, m]).sort((a, b) => ALL_MANAGERS.indexOf(a) - ALL_MANAGERS.indexOf(b)));
  return (
    <section className="panel">
      <header><h2>Compare decision makers on this season's weather</h2>
        <span className="muted small">{status ? `${status.season}, field seed ${status.seed}, weather year ${status.weatherYear}` : ''}</span></header>
      <div className="body">
        <div className="filters" role="group" aria-label="Who decides">
          {ALL_MANAGERS.map((m) => (
            <label key={m}><input type="checkbox" checked={managers.includes(m)} onChange={() => toggle(m)} />{MANAGER_LABEL[m]}{m.startsWith('api') && mock ? ' (stand-in)' : ''}</label>
          ))}
          <label>Seasons <select className="btn" value={seasons} onChange={(e) => setSeasons(Number(e.target.value))}>{[1, 3, 5, 10].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
          <button className="btn primary" disabled={managers.length < 2 || job?.status === 'running' || (real && !confirm)} onClick={start}>Run the comparison</button>
        </div>
        {real ? (() => {
          const nApi = managers.filter((m) => m.startsWith('api')).length;
          const c = config?.cost_per_api_season;
          const calls = (c?.calls || 300) * seasons * nApi;
          const usd = c ? c.usd * seasons * nApi : null;
          return (
            <label className="small" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} />
              <span>This calls the real decision API about {num(calls)} times
                {usd != null ? <>, roughly US${num(usd, 2)} at US${c.price_in_per_m} / US${c.price_out_per_m} per million input / output tokens
                  {c.prices_from_env ? '' : ' (example prices: set API_PRICE_IN_PER_M and API_PRICE_OUT_PER_M)'}</> : null}. Tick to spend that.</span>
            </label>
          );
        })() : null}
        {job?.status === 'running' ? (
          <div style={{ marginTop: 10 }}>
            <div className="prog" role="progressbar" aria-valuemin={0} aria-valuemax={job.total} aria-valuenow={job.done}><span style={{ width: `${(100 * job.done) / Math.max(1, job.total)}%`, background: 'var(--water)' }} /></div>
            <p className="small muted">{job.done} of {job.total} seasons simulated…</p>
          </div>
        ) : null}
        {job?.status === 'error' ? <p className="fail" style={{ marginTop: 10 }}>{job.error}</p> : null}
        {job?.status === 'done' ? <CampaignTable campaign={{ title: 'Result', subtitle: `${job.seasons} season${job.seasons > 1 ? 's' : ''} per decision maker`, managers: job.managers,
          summary: job.summary, paired: job.paired, base: job.base,
          note: mock ? 'Decision API rows come from the stand-in mock (the built-in manager\'s choices), not a real model.' : null }} /> : null}
      </div>
    </section>
  );
}

function RunsTable({ runs, src }) {
  if (!runs.length) return null;
  return (
    <section className="panel">
      {src.kind === 'replay'
        ? <header><h2>The recorded season</h2><span className="muted small">the one this page replays</span></header>
        : <header><h2>Seasons on this server</h2><span className="muted small">the current one and up to 12 earlier ones, kept in memory</span></header>}
      <div className="body tbl-wrap">
        <table className="tbl">
          <thead><tr><th>Run</th><th style={{ textAlign: 'left' }}>Decided by</th><th style={{ textAlign: 'left' }}>Season</th><th>Yield t/ha</th><th>Irrigation mm</th><th>N kg/ha</th><th>Sprayed m²</th><th>CH₄ kg/ha</th><th>Calls</th><th>Export</th></tr></thead>
          <tbody>
            {runs.slice().reverse().map((r) => {
              const s = r.summary;
              return (
                <tr key={r.run_id}>
                  <td>{r.current ? <strong>current</strong> : r.run_id.split('-').slice(-1)[0]}</td>
                  <td style={{ textAlign: 'left' }}>{MANAGER_LABEL[r.config.manager] || r.config.manager}{r.config.mode === 'approve' ? ', with approval' : ''}</td>
                  <td style={{ textAlign: 'left' }}>{r.config.season} {r.config.seed}</td>
                  <td>{s ? num(s.yield_t_ha, 2) : r.finished ? '–' : 'running'}</td>
                  <td>{s ? num(s.irrigation_mm) : '–'}</td>
                  <td>{s ? num(s.n_kg_ha) : '–'}</td>
                  <td>{s ? num(s.sprayed_squares) : '–'}</td>
                  <td>{s ? num(s.ch4_kg_ha) : '–'}</td>
                  <td>{r.calls}</td>
                  <td>{src.exportUrl(r.run_id, 'json') ? <><a href={src.exportUrl(r.run_id, 'json')} download>JSON</a> <a href={src.exportUrl(r.run_id, 'csv')} download>CSV</a></> : '–'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function CompareView({ src, status, config, tick, onError }) {
  const [runs, setRuns] = useState([]);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let live = true;
    src.runs().then((r) => live && setRuns(r || [])).catch((e) => live && setErr(e.message));
    return () => { live = false; };
  }, [src, tick]);
  const campaigns = src.campaign ? (Array.isArray(src.campaign) ? src.campaign : [src.campaign]) : [];
  return (
    <div className="stack">
      {src.kind === 'live' ? <CompareRunner src={src} status={status} config={config} onError={onError || (() => {})} /> : null}
      {campaigns.map((c, i) => <CampaignTable key={i} campaign={c} />)}
      {err ? <p className="muted">{err}</p> : <RunsTable runs={runs} src={src} />}
      <section className="panel">
        <header><h2>Many seasons at once</h2></header>
        <div className="body">
          <p style={{ maxWidth: '72ch' }}>Comparisons are paired: every manager gets the same fields, soils and weather, so differences come from the decisions alone.
            Run them from the backend folder:</p>
          <pre className="json" style={{ marginTop: 8 }}>python -m paddy compare --managers api,builtin,conventional --seasons 10 --mock</pre>
          <p className="small muted" style={{ marginTop: 6 }}>Drop --mock to use the real decision API: it asks before spending, and prints the token count and a cost estimate.</p>
        </div>
      </section>
    </div>
  );
}

export function ModelsView({ src, tick }) {
  const [m, setM] = useState(null);
  useEffect(() => {
    let live = true;
    src.models().then((r) => live && setM(r)).catch(() => {});
    return () => { live = false; };
  }, [src, tick]);
  if (!m) return <p className="muted">Loading…</p>;
  const live = m.live || {};
  const rows = [
    ['soil', 'Soil suitability', 'every probed square: class from the probe vs the rules on the true soil'],
    ['establish', 'Transplanting day', 'every square at every planting check: chance within ±5 points of the true chance'],
    ['establishDecision', 'Transplant / wait call', 'per field and check: the 72% rule on the advice vs on the true chances'],
    ['pest', 'Pest risk (7 days)', 'every live plant in a field with a pest seen, daily at 08:00'],
    ['yield', 'Yield forecast', 'every field-season: forecast at transplanting within ±10 points'],
    ['spray', 'Spray efficacy', 'every sprayed square at the minute the arm sprays it: predicted kill within ±5 points'],
    ['sprayPlan', 'Spray efficacy, planned', 'every sprayed square: the kill predicted when the spray was decided, within ±5 points (v2 target)'],
    ['harvest', 'Harvest readiness', 'every plant assessed: ready / not ready'],
    ['awd', 'Time to AWD', 'each transplanted field at 06:00 and 18:00 while not pumping: hours to the AWD line within ±6 h or ±20 % (v2 target; scored when under 72 h)'],
  ];
  const pk = m.paddy?.models || [];
  return (
    <div className="stack">
      <section className="panel">
        <header><h2>How the advice has done</h2><span className="muted small">{m.note}</span></header>
        <div className="body tbl-wrap">
          <table className="tbl">
            <thead><tr><th>Check</th><th>Cases</th><th>Right</th><th>95% interval</th><th>Target 95%</th><th style={{ textAlign: 'left' }}>Who is checked</th></tr></thead>
            <tbody>
              {rows.map(([k, label, who]) => {
                const x = live[k];
                const ok = x && x.acc != null ? x.acc >= 0.95 : null;
                return (
                  <tr key={k}>
                    <td>{label}</td>
                    <td>{x ? num(x.n) : 0}</td>
                    <td>{x && x.acc != null ? pct(x.acc, 1) : '–'}{k === 'pest' && x?.balanced != null ? <span className="muted"> (balanced {pct(x.balanced, 1)})</span> : null}</td>
                    <td className="muted">{x && x.ci ? `${pct(x.ci[0], 1)}–${pct(x.ci[1], 1)}` : '–'}</td>
                    <td>{ok === null ? '–' : x.n < 30 ? <span className="muted">too few cases</span> : ok ? <span className="pass">met</span> : <span className="fail">not met</span>}</td>
                    <td style={{ textAlign: 'left', whiteSpace: 'normal', minWidth: 260 }} className="small muted">{who}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="small muted" style={{ marginTop: 8 }}>A check with few cases has a wide interval; the README reports the same checks over hundreds of seasons.</p>
        </div>
      </section>
      {pk.length > 0 && (
        <section className="panel">
          <header><h2>The paddy forests (v2)</h2><span className="muted small">trained on simulated paddy seasons of this farm (set G); pack {m.paddy.version} {m.paddy.built}</span></header>
          <div className="body tbl-wrap">
            <table className="tbl">
              <thead><tr><th>Forest</th><th style={{ textAlign: 'left' }}>Answers</th><th>Trees</th><th>Nodes</th><th>Inputs</th><th style={{ textAlign: 'left' }}>Replaces</th></tr></thead>
              <tbody>
                {pk.map((x) => (
                  <tr key={x.id}><td>{x.title}</td>
                    <td style={{ textAlign: 'left', whiteSpace: 'normal' }}>{x.task}</td>
                    <td>{x.trees}</td><td>{num(x.nodes)}</td><td>{x.features}</td>
                    <td style={{ textAlign: 'left' }} className="muted">{x.replaces ? `the playground's ${x.replaces} forest` : 'new'}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      <section className="panel">
        <header><h2>{pk.length ? "The playground's forests" : 'The forests'}</h2><span className="muted small">trained on simulated farm-years by the Agri Robotics Playground; used here unchanged{pk.length ? ' (greyed: replaced by a paddy forest, or not used here)' : ''}</span></header>
        <div className="body tbl-wrap">
          <table className="tbl">
            <thead><tr><th>Forest</th><th style={{ textAlign: 'left' }}>Answers</th><th>Trees</th><th>Nodes</th><th>Inputs</th></tr></thead>
            <tbody>
              {(m.models || []).filter((x) => !x.paddy).map((x) => (
                <tr key={x.id} className={x.id === 'stress' || x.used_here === false ? 'muted' : ''}><td>{x.title}</td>
                  <td style={{ textAlign: 'left', whiteSpace: 'normal' }}>{x.task}{x.id === 'stress' ? (pk.some((p) => p.id === 'awd') ? ' (its paddy job is the time-to-AWD forest above)' : ' (not used here: paddy water follows the water tube and the AWD rules)') : ''}</td>
                  <td>{x.trees}</td><td>{num(x.nodes)}</td><td>{x.features}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
