import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { FarmPlan, LAYERS, LayerLegend } from './components/FarmPlan.jsx';
import { Farm3D, WEBGL2 } from './components/Farm3D.jsx';
import { ArmPanel } from './components/ArmPanel.jsx';
import { previewJob, ROLE_ID, viewAt } from './three/view.js';
import { createLiveTimeline } from './data/timeline-live.js';
import { FieldCard, SquarePanel, WaterTube } from './components/FieldParts.jsx';
import { DecisionDetail, DecisionItem, DecisionsView } from './components/Decisions.jsx';
import { ChartsView } from './components/Charts.jsx';
import { CompareView, ModelsView } from './components/Views.jsx';
import { RunControls } from './components/RunControls.jsx';
import { Icon } from './components/Icons.jsx';
import { fmtWhen } from './util.js';

const TABS = [['fields', 'Fields'], ['decisions', 'Decisions'], ['charts', 'Charts'], ['compare', 'Compare'], ['models', 'Models']];
const MANAGER = { builtin: 'built-in manager', conventional: 'conventional practice', api: 'decision API', 'api-no-advice': 'decision API without advice' };
const ACTION_ROLE = { transplant: 'planting', soil_check: 'planting', irrigate: 'watering', drain: 'watering', amend: 'watering', fertilise: 'watering',
  scout: 'pest', spray: 'pest', harvest: 'harvest', puddle: 'harvest' };
const ARM_RATES = [[10, 'arms at 10×'], [30, 'arms at 30×'], [60, 'arms at 60×'], [240, 'arms at 240×'], [0, "arms: don't slow down"]];

// the squares a decision acts on (its own list, or the whole field for field-wide jobs)
function decisionSquares(d) {
  if (!d || !d.field) return [];
  if (d.squares) return d.squares.map(([r, c]) => ({ f: d.field, r: r - 1, c: c - 1 }));
  if (['irrigate', 'drain', 'hold', 'wait', 'scout', 'spray'].includes(d.action)) return [];
  const out = [];
  for (let r = 0; r < 6; r++) for (let c = 0; c < 6; c++) out.push({ f: d.field, r, c });
  return out;
}

function NaturalLegend() {
  return (
    <div className="legend">
      <span><i className="sw" style={{ background: '#3f6a72' }} />water, drawn at its true depth</span>
      <span><i className="sw" style={{ background: '#4e3c2e' }} />saturated mud</span>
      <span><i className="sw" style={{ background: '#9e785a' }} />drying soil, cracks below the AWD line</span>
      <span><i className="sw" style={{ background: '#e5392b', borderRadius: '50%' }} />pest seen (amber below the 8% threshold)</span>
      <span className="muted">tube boards: blue column = reading, red line = AWD (−150 mm)</span>
    </div>
  );
}

function readHash() {
  const h = (typeof location !== 'undefined' ? location.hash : '').replace('#', '');
  if (/^d\d+$/.test(h)) return { tab: 'decisions', detail: h };
  return { tab: TABS.some(([k]) => k === h) ? h : 'fields', detail: null };
}

function useMediaQuery(q) {
  const get = () => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(q).matches : false);
  const [m, setM] = useState(get);
  useEffect(() => {
    if (!window.matchMedia) return undefined;
    const mq = window.matchMedia(q);
    const on = () => setM(mq.matches);
    mq.addEventListener ? mq.addEventListener('change', on) : mq.addListener(on);
    return () => (mq.removeEventListener ? mq.removeEventListener('change', on) : mq.removeListener(on));
  }, [q]);
  return m;
}

// A host page (such as an artifact viewer) may already have set data-theme from the viewer's own choice;
// "automatic" keeps whatever the host set.
const HOST_THEME = typeof document !== 'undefined' ? document.documentElement.dataset.theme : undefined;

function useTheme() {
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem('paddy-theme') || 'system'; } catch (e) { return 'system'; }
  });
  useEffect(() => {
    const el = document.documentElement;
    if (theme === 'system') {
      if (HOST_THEME) el.dataset.theme = HOST_THEME; else delete el.dataset.theme;
    } else el.dataset.theme = theme;
    try { localStorage.setItem('paddy-theme', theme); } catch (e) { /* private window */ }
  }, [theme]);
  const next = () => setTheme((t) => (t === 'system' ? 'light' : t === 'light' ? 'dark' : 'system'));
  return [theme, next];
}

const API_BADGE = {
  connected: ['decision API connected', ''], configured: ['decision API set up, no call yet', ''], 'no key': ['decision API: no key', 'bad'],
  'not configured': ['decision API not set up', 'bad'], failing: ['decision API failing', 'bad'], recorded: ['recorded decision API replies', ''],
};

function ApiBadge({ status, standIn }) {
  if (!status) return null;
  const budget = status.max_calls_per_day ? `${status.calls_today ?? 0} of ${status.max_calls_per_day} calls today, ${status.calls} of ${status.max_calls_per_season} this season` : null;
  if (standIn || status.api_status === 'mock') {
    return <span className="standin" title={`Decisions come from the mock decision API, which answers with the built-in manager's choices.${budget ? ' ' + budget : ''}`}>stand-in API</span>;
  }
  const b = API_BADGE[status.api_status];
  if (!b) return budget ? <span className="muted small">{status.calls_today ?? 0}/{status.max_calls_per_day} calls today</span> : null;
  return <span className={`apibadge ${b[1]}`} title={budget || ''}>{b[0]}{budget ? <span className="muted">, {status.calls_today ?? 0}/{status.max_calls_per_day} calls today</span> : null}</span>;
}

export default function App({ src }) {
  const [route, setRoute] = useState(readHash);
  const [status, setStatus] = useState(null);
  const [config, setConfig] = useState(null);
  const [farm, setFarm] = useState(null);
  const [fields, setFields] = useState({});
  const [decisions, setDecisions] = useState([]);
  const [weather, setWeather] = useState(null);
  const [offline, setOffline] = useState(false);
  const [layer, setLayer] = useState('water');
  const [selected, setSelected] = useState(null);
  const [toast, setToast] = useState(null);
  const [tick, setTick] = useState(0);
  const [theme, nextTheme] = useTheme();
  const narrow = useMediaQuery('(max-width: 760px)');
  const prefersDark = useMediaQuery('(prefers-color-scheme: dark)');
  const timers = useRef({});
  // the 3D farm: its timeline (the recorded run, or the live server's reports), view and selection
  const tl = useMemo(() => (src.kind === 'replay' ? src.timeline : createLiveTimeline()), [src]);
  const [farmView, setFarmView] = useState(() => {
    try { return localStorage.getItem('paddy-farm-view') || (WEBGL2 ? '3d' : 'plan'); } catch (e) { return WEBGL2 ? '3d' : 'plan'; }
  });
  const [no3d, setNo3d] = useState(WEBGL2 ? null : 'WebGL 2 is not available');
  const [look, setLook] = useState('natural');
  const [selArm, setSelArm] = useState(null);
  const [hlId, setHlId] = useState(null);
  const [previewId, setPreviewId] = useState(null);
  const [armRate, setArmRate] = useState(() => (src.kind === 'replay' && src.armRate != null ? src.armRate : src.kind === 'live' ? 30 : 60));
  const [, setClockTick] = useState(0);

  const say = useCallback((msg) => {
    setToast(msg);
    clearTimeout(timers.current.toast);
    timers.current.toast = setTimeout(() => setToast(null), 4200);
  }, []);

  const refreshFarm = useCallback(async () => {
    try {
      const f = await src.farm();
      setFarm(f);
      setStatus(f);
      const names = Object.keys(f.fields || {});
      const detail = await Promise.all(names.map((n) => src.field(n)));
      const map = {};
      names.forEach((n, i) => { map[n] = detail[i]; });
      setFields(map);
      setOffline(false);
      if (route.tab === 'charts') setWeather(await src.weather());
    } catch (e) {
      if (e.offline) setOffline(true);
    }
  }, [src, route.tab]);

  const refreshDecisions = useCallback(async () => {
    try {
      setDecisions(await src.decisions({ limit: 2000 }));
    } catch (e) {
      if (e.offline) setOffline(true);
    }
  }, [src]);

  const schedule = useCallback((key, fn, ms) => {
    if (timers.current[key]) return;
    timers.current[key] = setTimeout(() => { timers.current[key] = null; fn(); }, ms);
  }, []);

  useEffect(() => {
    src.config().then(setConfig).catch((e) => e.offline && setOffline(true));
    refreshFarm();
    refreshDecisions();
    const off = src.subscribe((msg) => {
      if (msg.type === 'status') setStatus((s) => ({ ...(s || {}), ...msg.data }));
      if (msg.type === 'clock') { setStatus((s) => ({ ...(s || {}), time: msg.data.time, day: msg.data.day })); setClockTick((k) => k + 1); }
      if (msg.type === 'arms' && tl && tl.onArms) tl.onArms(msg.data);
      if (msg.type === 'tick' && tl && tl.onTick) tl.onTick(msg.data?.t);
      if (msg.type === 'tick' || msg.type === 'status' || msg.type === 'finished') schedule('farm', refreshFarm, src.kind === 'replay' ? 30 : 500);
      if (msg.type === 'decision' || msg.type === 'call' || msg.type === 'tick') schedule('dec', refreshDecisions, src.kind === 'replay' ? 30 : 400);
      if (msg.type === 'finished') { say('The season is over: see Compare for the results.'); setTick((t) => t + 1); }
    }, (ok) => { if (!ok && src.kind === 'live') schedule('retry', refreshFarm, 3000); });
    const poll = setInterval(() => { if (src.kind === 'live') { refreshFarm(); refreshDecisions(); } }, 6000);
    return () => { off(); clearInterval(poll); };
  }, [src, refreshFarm, refreshDecisions, schedule, say, tl]);

  // the live timeline follows the server's reports
  useEffect(() => { if (src.kind === 'live' && tl && config) tl.setConfig(config); }, [src, tl, config]);
  useEffect(() => { if (src.kind === 'live' && tl && farm) { tl.feed(farm); if (status?.work_rate) tl.setWorkRate(status.work_rate); } }, [src, tl, farm, status?.work_rate]);
  // live: the server's arm slow-down is the one shown (3600 = as good as no slow-down)
  useEffect(() => { if (src.kind === 'live' && status?.work_rate) setArmRate(status.work_rate >= 3600 ? 0 : status.work_rate); }, [src, status?.work_rate]);
  useEffect(() => { if (src.kind === 'live' && tl) tl.setDecisions(decisions); }, [src, tl, decisions]);

  useEffect(() => {
    const onHash = () => setRoute(readHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  useEffect(() => {
    if (route.tab === 'charts') src.weather().then(setWeather).catch(() => {});
  }, [route.tab, src, tick, farm?.day]);

  const go = (tab, detail = null) => {
    const h = detail || tab;
    if (location.hash !== `#${h}`) history.replaceState(null, '', `#${h}`);
    setRoute({ tab, detail });
  };
  const approve = async (id) => { try { await src.approve(id); say('Approved.'); refreshDecisions(); refreshFarm(); } catch (e) { say(e.message); } };
  const reject = async (id, reason) => { try { await src.reject(id, reason); say('Rejected: the reason goes back to the decision maker.'); refreshDecisions(); } catch (e) { say(e.message); } };

  const pending = decisions.filter((d) => d.status === 'pending_approval');
  const can3d = !!(tl && tl.layout) && !no3d;
  const show3d = can3d && farmView === '3d';
  const chooseView = (v) => { setFarmView(v); try { localStorage.setItem('paddy-farm-view', v); } catch (e) { /* private window */ } };
  const themeKey = theme === 'system' ? (HOST_THEME || (prefersDark ? 'dark' : 'light')) : theme;
  const hlDecision = hlId ? decisions.find((d) => d.id === hlId) : null;
  const highlight = useMemo(() => {
    if (!hlDecision || !tl?.layout) return null;
    const role = hlDecision.arm_plan?.arm || hlDecision.arm_preview?.arm || ACTION_ROLE[hlDecision.action];
    const arm = ROLE_ID[role] ?? null;
    const job = arm ? (tl.jobs?.[arm] || []).find((j) => j.decision === hlDecision.id) : null;
    return { arm, squares: decisionSquares(hlDecision), job: job ? { ...job, arm } : null, id: hlDecision.id };
  }, [hlDecision, tl, tl?.jobs]);  // eslint-disable-line react-hooks/exhaustive-deps
  const previewDecision = pending.find((d) => d.id === previewId) || pending.find((d) => d.arm_preview);
  const preview = useMemo(() => (previewDecision && tl?.layout ? previewJob(previewDecision, tl.layout) : null), [previewDecision, tl]);
  const select3d = (sel) => {
    if (!sel) { setSelected(null); setSelArm(null); return; }
    if (sel.type === 'arm') { setSelArm(sel.id); setSelected(null); } else { setSelected(sel.sq); setSelArm(null); }
  };
  const showDecision = (id) => { setHlId((h) => (h === id ? null : id)); if (route.tab !== 'fields') go('fields'); };
  const clockT = tl && tl.clock ? tl.clock.t() : src.clock ? src.clock.t() : 0;
  const armView = selArm != null && tl?.layout && tl.frames?.length ? viewAt(tl, src.kind === 'replay' ? src.clock.t() : tl.clock.t()) : null;
  const st = offline ? 'offline' : status?.status;
  const stLabel = { running: 'Running', paused: 'Paused', awaiting_approval: 'Waiting for your approval', finished: 'Season over', offline: 'Server not answering' }[st] || '…';
  const sq = selected ? fields[selected[0]]?.squares?.flat().find((s) => s.sq === selected) : null;
  const standIn = config?.mock || farm?.mock || (src.kind === 'replay' && src.data?.stand_in);
  const recent = decisions.slice(-30).reverse();

  return (
    <div className="app">
      <header className="top">
        <div className="top-inner">
          <div className="brand"><h1>Paddy API Farm</h1><span className="kn">two 6 × 6 paddies, Bengaluru</span></div>
          <div className="clock">
            <span className="when num">{status?.time ? fmtWhen(status.time) : '–'}</span>
            {(() => {
              const sub = status ? `Day ${status.day} of the ${status.season} season, decisions by the ${MANAGER[status.manager] || status.manager}${status.mode === 'approve' ? ' with your approval' : ''}` : '';
              return <span className="sub" title={sub}>{sub}</span>;
            })()}
          </div>
          <span className={`status ${st || ''}`} role="status"><i />{stLabel}</span>
          <ApiBadge status={status} standIn={standIn} />
          <span className="spacer" />
          <RunControls src={src} status={status} config={config} onError={say} onDone={say} />
          <button className="btn theme-btn" onClick={nextTheme} aria-label={`Theme: ${theme}. Change theme`} title={`Theme: ${theme}`}>
            <Icon name={theme === 'dark' ? 'moon' : 'sun'} /><span className="sr">{theme === 'system' ? 'Automatic theme' : theme === 'light' ? 'Light theme' : 'Dark theme'}</span>
          </button>
        </div>
        <nav className="nav" role="tablist" aria-label="Views">
          {TABS.map(([k, label]) => (
            <button key={k} role="tab" aria-selected={route.tab === k} onClick={() => go(k)}>
              {label}{k === 'decisions' && pending.length ? <span className="count" aria-label={`${pending.length} waiting`}>{pending.length}</span> : null}
            </button>
          ))}
        </nav>
      </header>
      {offline ? (
        <div className="banner" role="alert">
          <strong>The farm server is not answering.</strong>
          <span>Start it from the backend folder with <code>python -m paddy serve</code>; this page reconnects by itself.</span>
          <button className="btn" onClick={() => { refreshFarm(); refreshDecisions(); }}>Try again</button>
        </div>
      ) : null}
      {src.kind === 'replay' && src.data?.label ? <div className="banner note" role="note"><strong>{src.data.stand_in ? 'Stand-in replay.' : 'Recorded run.'}</strong><span>{src.data.label}</span></div> : null}
      <main>
        {route.tab === 'fields' ? (
          <div className="fields-layout">
            <div className="stack">
              <section className="panel farm-plan" aria-label="The farm">
                <div className="farm-head">
                  {tl && tl.layout ? (
                    <div className="seg" role="group" aria-label="Show the farm as">
                      <button aria-pressed={show3d} disabled={!can3d} onClick={() => chooseView('3d')}>3D</button>
                      <button aria-pressed={!show3d} onClick={() => chooseView('plan')}>Plan</button>
                    </div>
                  ) : null}
                  <div className="layers" role="group" aria-label="Colour the squares by">
                    {show3d ? <button aria-pressed={look === 'natural'} onClick={() => setLook('natural')}>As it looks</button> : null}
                    {LAYERS.map((l) => <button key={l.id} aria-pressed={show3d ? look === l.id : layer === l.id} onClick={() => (show3d ? setLook(l.id) : setLayer(l.id))}>{l.label}</button>)}
                  </div>
                  {show3d && src.setArmRate ? (
                    <select className="btn arm-rate" aria-label="How fast arm work plays" value={armRate}
                            onChange={async (e) => { const v = Number(e.target.value); setArmRate(v); try { await src.setArmRate(v, status?.speed); } catch (err) { say(err.message); } }}>
                      {ARM_RATES.some(([v]) => v === armRate) ? null : <option value={armRate}>arms at {armRate}×</option>}
                      {ARM_RATES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </select>
                  ) : null}
                </div>
                {tl && tl.layout && no3d ? <p className="note small" role="note">The 3D farm needs WebGL 2, which this browser doesn't provide, so the farm is shown as a plan.</p> : null}
                {show3d ? (
                  <>
                    <Farm3D tl={tl} clock={src.kind === 'replay' ? src.clock : tl.clock} overlay={look === 'natural' ? null : look} themeKey={themeKey}
                            selected={selArm != null ? { type: 'arm', id: selArm } : selected ? { sq: selected } : null} onSelect={select3d}
                            highlight={highlight} preview={preview} compact={narrow}
                            onFallback={(why) => { setNo3d(why || 'no WebGL 2'); }}
                            onWatchArms={(on) => { if (src.watchArms) src.watchArms(on); }} />
                    {look === 'natural' ? <NaturalLegend /> : <LayerLegend layer={look} />}
                    {preview ? <p className="note small" role="note">Planned path for decision {preview.decision} ({preview.action} {preview.field || ''}): dashed line and ghost arm, before you approve it.</p> : null}
                  </>
                ) : null}
                {show3d ? null : Object.keys(fields).length ? (narrow ? (
                  Object.keys(fields).map((n) => (
                    <div className="field-row" key={n}>
                      <FarmPlan fields={{ [n]: fields[n] }} layer={layer} selected={selected} onSelect={setSelected} />
                      <WaterTube name={n} level={farm?.fields?.[n]?.water_mm} pumping={farm?.fields?.[n]?.pumping} draining={farm?.fields?.[n]?.draining} />
                    </div>
                  ))
                ) : (
                  <div className="plan-row">
                    <WaterTube name="A" level={farm?.fields?.A?.water_mm} pumping={farm?.fields?.A?.pumping} draining={farm?.fields?.A?.draining} />
                    <FarmPlan fields={fields} layer={layer} selected={selected} onSelect={setSelected} />
                    <WaterTube name="B" level={farm?.fields?.B?.water_mm} pumping={farm?.fields?.B?.pumping} draining={farm?.fields?.B?.draining} />
                  </div>
                )) : <p className="empty">{offline ? 'Waiting for the server…' : 'Loading the fields…'}</p>}
                {show3d ? null : <LayerLegend layer={layer} />}
              </section>
              <div className="field-cards">
                <FieldCard f={farm?.fields?.A ? { ...farm.fields.A, name: 'A' } : null} />
                <FieldCard f={farm?.fields?.B ? { ...farm.fields.B, name: 'B' } : null} />
              </div>
              {selArm != null ? (
                <ArmPanel src={src} tl={tl} armId={selArm} now={clockT} view={armView} decisions={decisions} onClose={() => setSelArm(null)} onShowDecision={showDecision} />
              ) : <SquarePanel sq={sq} src={src} day={farm?.day} onClose={() => setSelected(null)} />}
            </div>
            <aside className="feed feed-rail" aria-label="Latest decisions">
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                <h2>Latest decisions</h2><span className="spacer" />
                <button className="linkish" onClick={() => go('decisions')}>All decisions</button>
              </div>
              {pending.map((d) => <DecisionItem key={d.id} d={d} compact onApprove={approve} onReject={reject} onOpen={(id) => go('decisions', id)}
                                                 onShow={show3d ? (id) => { setPreviewId(id); showDecision(id); } : null} active={hlId === d.id} />)}
              {recent.filter((d) => d.status !== 'pending_approval').slice(0, narrow ? 5 : 25).map((d) => (
                <DecisionItem key={d.id} d={d} compact onOpen={(id) => go('decisions', id)} onShow={show3d ? showDecision : null} active={hlId === d.id} />
              ))}
              {!decisions.length ? <p className="empty">No decisions yet. Press Run: the first decision point is 06:00 on day 0.</p> : null}
            </aside>
          </div>
        ) : null}
        {route.tab === 'decisions' ? (
          route.detail ? <DecisionDetail src={src} id={route.detail} onBack={() => go('decisions')} />
            : <DecisionsView src={src} decisions={decisions} onOpen={(id) => go('decisions', id)} onApprove={approve} onReject={reject} />
        ) : null}
        {route.tab === 'charts' ? <ChartsView farm={farm} weather={weather} /> : null}
        {route.tab === 'compare' ? <CompareView src={src} status={status} config={config} tick={tick + (status?.status === 'finished' ? 1 : 0)} onError={say} /> : null}
        {route.tab === 'models' ? <ModelsView src={src} tick={Math.floor((farm?.day || 0) / 5)} /> : null}
      </main>
      {toast ? <div className="toast" role="status">{toast}</div> : null}
    </div>
  );
}
