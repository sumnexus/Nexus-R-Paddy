// The replay data source: a recorded run bundled into the page (window.__PADDY_REPLAY__). It answers the same
// questions as the live API, as of the replay's clock. The squares come from the frames (one at every 06:00 and
// 18:00); the clock itself runs continuously so the 3D farm can show the arms at work: while the 3D view is open the
// clock slows to the chosen arm-watching rate whenever an arm is busy, and skips quickly through quiet hours.
import { makeTimeline, frameIndexAt, armsBusy, nextArmStart } from '../three/view.js';
const PEST_KEYS_FALLBACK = ['pink_stem_borer', 'earhead_caterpillar', 'yellow_stem_borer', 'brown_planthopper', 'leaf_folder', 'fall_armyworm', 'fruit_borer', 'tuta',
  'whitefly', 'pod_borer', 'aphid', 'shoot_fruit_borer', 'jassid', 'thrips', 'yellow_mite', 'leaf_miner', 'spodoptera'];

const STAGES = [['Establishment', 0.14], ['Tillering', 0.45], ['Panicle initiation', 0.6], ['Flowering', 0.7], ['Grain filling', 0.9], ['Maturity', 99]];
function stageName(p) {
  for (const [n, end] of STAGES) if (p < end) return n;
  return 'Maturity';
}

export function createReplaySource(data) {
  const frames = data.frames || [];
  const pestKeys = data.pest_keys || PEST_KEYS_FALLBACK;
  const listeners = new Set();
  // open on a growing crop (the bundle names the frame) rather than on bare fields; "Back to day 0" starts over
  const st = { i: Math.max(0, Math.min(frames.length - 1, data.start_index || 0)), playing: false, speed: 2, timer: null, t: 0, watch: false, armRate: 60,
    lastWall: 0, lastClock: 0 };
  const rows = data.config?.rows || 6;
  const cols = data.config?.cols || 6;
  const tOf = (iso) => (iso ? Date.parse(iso + ':00Z') : 0);
  // the 3D timeline (v2 bundles carry the layout and the arms' keyframes)
  const tl = data.layout && frames.length ? makeTimeline(data) : null;
  if (tl) tl.energy = data.energy_cfg || null;
  const base = frames.length ? tOf(frames[0].time) - frames[0].t * 1000 : 0;
  const isoAt = (t) => new Date(base + t * 1000).toISOString().slice(0, 16);
  const tEnd = tl ? tl.tEnd : frames.length ? frames[frames.length - 1].t : 0;
  // a v2 page opens at 10:00 on the opening day (06:00 is barely after sunrise in the 3D view)
  st.t = frames[st.i] ? frames[st.i].t + (tl && frames[st.i + 1] ? Math.min(4 * 3600, frames[st.i + 1].t - frames[st.i].t - 1) : 0) : 0;

  const frame = () => frames[Math.min(st.i, frames.length - 1)] || { fields: {}, time: '', day: 0 };
  const nowIso = () => (frames.length ? isoAt(st.t) : '');
  const finished = () => st.i >= frames.length - 1 && st.t >= tEnd - 1e-6;
  const emit = (msg) => listeners.forEach((fn) => fn(msg));
  const status = () => ({
    status: finished() ? 'finished' : st.playing ? 'running' : 'paused', run_id: data.summary?.run_id || 'replay', time: nowIso(), day: Math.floor(st.t / 86400),
    season: data.config?.season, seed: data.config?.seed, weatherYear: data.config?.weatherYear, manager: data.config?.manager,
    mode: data.config?.mode || 'auto', speed: st.speed, pending_approvals: 0, scheduled: 0, calls: (data.calls || []).filter((c) => tOf(c.time) <= tOf(nowIso())).length,
    api_status: data.stand_in ? 'mock' : (data.config?.manager || '').startsWith('api') ? 'recorded' : 'not used',
  });

  // the clock: `speed` decision points (12 h) per second; with the 3D view open, arm work plays at `armRate` × real time
  function setT(t) {
    st.t = Math.max(frames.length ? frames[0].t : 0, Math.min(tEnd, t));
    const fi = frameIndexAt(tl || { ft: Float64Array.from(frames.map((f) => f.t)) }, st.t);
    if (fi !== st.i) { st.i = fi; emit({ type: 'tick', data: status() }); }
  }
  function tick() {
    const now = typeof performance !== 'undefined' ? performance.now() : Date.now();
    const dt = Math.min(0.25, Math.max(0, (now - st.lastWall) / 1000));
    st.lastWall = now;
    const quiet = st.speed * 43200;
    let t1 = st.t + quiet * dt;
    if (tl && st.watch && st.armRate > 0) {
      if (armsBusy(tl, st.t)) t1 = st.t + st.armRate * dt;
      else {
        const next = nextArmStart(tl, st.t);
        if (next < t1) t1 = next + 1e-3;
      }
    }
    setT(t1);
    if (now - st.lastClock > 200) { st.lastClock = now; emit({ type: 'clock', data: { time: nowIso(), day: Math.floor(st.t / 86400), t: st.t } }); }
    if (finished()) {
      stop();
      emit({ type: 'status', data: status() });
    }
  }
  function stop() {
    st.playing = false;
    clearInterval(st.timer);
    st.timer = null;
  }
  function play() {
    stop();
    if (finished()) { st.i = 0; st.t = frames[0].t; }
    st.playing = true;
    st.lastWall = typeof performance !== 'undefined' ? performance.now() : Date.now();
    st.timer = setInterval(tick, 40);
  }

  const visibleDecision = (d) => {
    const now = tOf(nowIso());
    const out = { ...d };
    const execAt = d.execution?.at ? tOf(d.execution.at) : null;
    if (execAt !== null && execAt > now) {
      out.status = 'scheduled';
      out.execution = null;
    }
    if (d.outcome && tOf(d.outcome.time) > now) out.outcome = null;
    return out;
  };

  const history = () => (data.history || []).filter((h) => h.day <= frame().day);

  function farmBrief() {
    const f = frame();
    const out = status();
    out.fields = {};
    for (const [name, x] of Object.entries(f.fields || {})) {
      out.fields[name] = {
        phase: x.phase, water_mm: x.tube, progress: x.progress, stage: x.phase === 'growing' || x.phase === 'drain_down' ? stageName(x.progress) : null,
        yield_t_ha: x.yield, pumping: x.pumping, draining: x.draining,
      };
    }
    return out;
  }

  function farm() {
    const f = frame();
    const out = farmBrief();
    out.weather = { time: f.time, ...(f.weather || {}), forecast: (f.weather?.forecast || []).map((x, k) => ({ ...x, date: '' + k })) };
    for (const [name, x] of Object.entries(f.fields || {})) {
      const state = x.sq?.state || '';
      Object.assign(out.fields[name], {
        puddled: x.phase !== 'land_prep', transplanted: data.summary?.fields?.[name]?.transplanted && tOf(data.summary.fields[name].transplanted + 'T06:00') <= tOf(f.time) ? data.summary.fields[name].transplanted : null,
        harvested: x.phase === 'harvested' ? data.summary?.fields?.[name]?.harvested : null, abandoned: x.phase === 'abandoned',
        plants: { planted: [...state].filter((c) => c !== '.').length, alive: [...state].filter((c) => c === 'L').length },
        irrigation_mm: x.irr_mm, rain_mm: x.rain_mm, n_kg_ha: x.n_kg_ha, sprays: x.sprays, ch4_kg_ha: x.ch4,
        squares: x.sq, days_to_harvest: x.phase === 'growing' || x.phase === 'drain_down' ? Math.max(0, Math.round((1 - x.progress) * 125)) : null,
        pests_seen: (x.sq?.pid || []).filter((p) => p >= 0).length,
        history: history().map((h) => ({ day: h.day, date: h.date, [`${name}_h`]: h[`${name}_h`], [`${name}_prog`]: h[`${name}_prog`], [`${name}_health`]: h[`${name}_health`],
          [`${name}_irr`]: h[`${name}_irr`], [`${name}_pest`]: h[`${name}_pest`], [`${name}_n`]: h[`${name}_n`] })),
      });
    }
    out.summary = finished() ? data.summary : null;
    out.baseline = data.baseline ? { status: 'done', manager: 'conventional', history: data.baseline.history, summary: data.baseline.summary } : null;
    out.events = (data.events || []).filter((e) => tOf(e.time) <= tOf(f.time)).slice(-20);
    out.api = data.api || null;
    out.mock = !!data.stand_in;
    return out;
  }

  function field(name) {
    const f = frame();
    const x = f.fields?.[name];
    if (!x) throw new Error(`no field ${name}`);
    const squares = [];
    for (let r = 0; r < rows; r++) {
      const row = [];
      for (let c = 0; c < cols; c++) {
        const k = r * cols + c;
        const s = x.sq.state[k];
        const pid = x.sq.pid[k];
        row.push({
          sq: `${name}${r + 1},${c + 1}`, row: r + 1, col: c + 1, depth_mm: x.sq.depth[k], planted: s !== '.', alive: s === 'L', harvested: s === 'H',
          progress: x.sq.prog[k], stage: s === 'L' && x.sq.prog[k] != null ? stageName(Math.min(1, x.sq.prog[k])) : null, health: x.sq.health[k],
          soil: { code: x.sq.soil[k], ph: x.sq.ph ? x.sq.ph[k] : null },
          pest: pid >= 0 && x.sq.pest[k] > 0 ? { pest: pestKeys[pid] || `pest ${pid}`, severity: x.sq.pest[k] } : null,
          risk: x.sq.risk[k], establish: x.sq.est[k],
        });
      }
      squares.push(row);
    }
    return { ...farm().fields[name], name, rows, cols, squares };
  }

  return {
    kind: 'replay',
    data,
    frames,
    get index() { return st.i; },
    seek(i) { st.i = Math.max(0, Math.min(frames.length - 1, i)); st.t = frames[st.i]?.t ?? 0; emit({ type: 'tick', data: status() }); },
    timeline: tl,
    clock: { t: () => st.t },
    seekTime(t) { stop(); setT(t); emit({ type: 'tick', data: status() }); emit({ type: 'clock', data: { time: nowIso(), day: Math.floor(st.t / 86400), t: st.t } }); },
    watchArms(on) { st.watch = !!on; },
    get armRate() { return st.armRate; },
    setArmRate(x) { st.armRate = Math.max(0, Number(x) || 0); emit({ type: 'clock', data: { time: nowIso(), day: Math.floor(st.t / 86400), t: st.t } }); },
    health: async () => ({ ok: true, ...status(), mock: !!data.stand_in }),
    config: async () => ({ fields: Object.keys(frames[0]?.fields || { A: 1, B: 1 }), rows, cols, managers: [data.config?.manager], modes: ['auto'], api: data.api,
      mock: !!data.stand_in, event_kinds: [], replay: true }),
    farm: async () => farm(),
    field: async (name) => field(name),
    weather: async () => ({ ...farm().weather, daily: history().map((h) => ({ day: h.day, date: h.date, rain_mm: h.rain })) }),
    squareHistory: async (name, r, c) => {
      const k = (r - 1) * cols + (c - 1);
      return { sq: `${name}${r},${c}`, history: frames.slice(0, st.i + 1).map((fr) => {
        const x = fr.fields[name].sq;
        return { time: fr.time, day: fr.day, depth_mm: x.depth[k], progress: x.prog[k], health: x.health[k], pest: x.pest[k], risk: x.risk[k], state: x.state[k] };
      }) };
    },
    compareStart: async () => { throw new Error('Comparisons run on a live farm; this page shows the recorded campaign instead.'); },
    compare: async () => ({ status: 'none' }),
    decisions: async (params = {}) => {
      const now = tOf(nowIso());
      return (data.decisions || []).filter((d) => tOf(d.created) <= now)
        .filter((d) => !params.field || d.field === params.field)
        .filter((d) => !params.action || d.action === params.action)
        .map(visibleDecision)
        .filter((d) => !params.status || d.status === params.status)
        .slice(-(params.limit || 500));
    },
    decision: async (id) => {
      const d = (data.decisions || []).find((x) => x.id === id);
      if (!d) throw new Error(`no decision ${id}`);
      return { decision: visibleDecision(d), call: (data.calls || []).find((c) => c.id === d.call_id) || null };
    },
    approve: async () => { throw new Error('This is a recorded run: nothing to approve.'); },
    reject: async () => { throw new Error('This is a recorded run: nothing to reject.'); },
    start: async () => { play(); emit({ type: 'status', data: status() }); return status(); },
    pause: async () => { stop(); emit({ type: 'status', data: status() }); return status(); },
    step: async (hours) => {
      stop();
      st.i = Math.min(frames.length - 1, st.i + Math.max(1, Math.round(hours / 12)));
      st.t = frames[st.i].t;
      emit({ type: 'tick', data: status() }); return status();
    },
    reset: async () => { stop(); st.i = 0; st.t = frames[0]?.t ?? 0; emit({ type: 'status', data: status() }); return status(); },
    speed: async (x) => { st.speed = Math.max(0.5, Math.min(30, x)); if (st.playing) play(); return status(); },
    event: async () => { throw new Error('Events can only be added on a live farm.'); },
    observe: async () => { throw new Error('Observations can only be added on a live farm.'); },
    models: async () => {
      const all = data.models || [];
      const pk = all.filter((x) => x.paddy);
      return { version: '', models: all, live: data.summary?.audit || {}, note: 'Checks over the whole recorded season.',
        paddy: pk.length ? { version: data.config?.models || '', built: data.config?.pack_sha256 ? `sha ${data.config.pack_sha256}` : '', models: pk } : null };
    },
    audit: async () => ({ summary: data.summary?.audit || {} }),
    runs: async () => [{ run_id: data.summary?.run_id, config: data.config, finished: true, summary: data.summary, calls: data.summary?.calls, current: true }],
    campaign: data.campaign || null,
    exportUrl: () => null,
    subscribe(onMessage, onConnection) {
      listeners.add(onMessage);
      onConnection && onConnection(true);
      return () => listeners.delete(onMessage);
    },
  };
}
