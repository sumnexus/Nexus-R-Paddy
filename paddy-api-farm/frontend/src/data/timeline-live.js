// The live 3D timeline: what the 3D farm shows when it is connected to the Python backend. The squares come from the
// latest /api/farm reports (kept with their times), the arms from the keyframes the server streams after each hour of
// arm work, the arm plans from the decisions. The server paces an hour with arm work so it takes (span ÷ work rate)
// seconds of wall time; the page plays that hour's keyframes at the same rate, starting when they arrive, and shows
// the squares as they were before that hour until the playback is over.
import { appendTrack, armJobs, armEvents } from '../three/view.js';

export function createLiveTimeline() {
  const tl = {
    kind: 'live', layout: null, frames: [], ft: new Float64Array(0), codes: [], tracks: {}, events: [], evT: new Float64Array(0),
    jobs: { 1: [], 2: [], 3: [], 4: [] }, weather: null, water: null, month: 6, cameraHours: [6, 8, 10, 12, 14, 16, 18], sweep: 120, tEnd: 0, energy: null,
  };
  const st = { serverT: 0, chunks: [], workRate: 30, version: 0 };
  const wall = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());

  tl.setConfig = (cfg) => {
    if (!cfg || !cfg.layout) return;
    tl.layout = cfg.layout;
    tl.codes = cfg.layout.codes || [];
    tl.cameraHours = cfg.camera_hours || tl.cameraHours;
    tl.sweep = cfg.scan_sweep_s || tl.sweep;
    tl.energy = cfg.energy_cfg || null;
  };
  tl.setWorkRate = (x) => { if (x > 0) st.workRate = x; };

  // a /api/farm report → one frame (the shape of the replay's frames)
  tl.feed = (farm) => {
    if (!farm || farm.t == null || !farm.fields) return;
    const fields = {};
    for (const [name, f] of Object.entries(farm.fields)) {
      if (!f.squares) continue;
      fields[name] = { phase: f.phase, tube: f.tube_mm ?? f.water_mm, lvl: f.lvl ?? f.tube_mm ?? f.water_mm, progress: f.progress, pumping: f.pumping, draining: f.draining,
        yield: f.yield_t_ha, sq: f.squares };
    }
    if (!Object.keys(fields).length) return;
    const fr = { t: farm.t, time: farm.time, day: farm.day, fields, power: farm.power, arms: farm.arms };
    const keep = tl.frames.filter((x) => x.t < farm.t - 1e-6);
    // a reset (time went backwards) starts over
    if (tl.frames.length && farm.t < tl.frames[tl.frames.length - 1].t - 1) { keep.length = 0; tl.tracks = {}; st.chunks = []; }
    tl.frames = keep.slice(-3).concat([fr]);
    tl.ft = Float64Array.from(tl.frames.map((x) => x.t));
    tl.weather = farm.weather_hours || tl.weather;
    tl.water = farm.water_hours || tl.water;
    tl.month = farm.season_month || tl.month;
    tl.tEnd = farm.t;
    if (farm.work_rate) st.workRate = farm.work_rate;
    st.serverT = farm.t;
    // parked arms with no keyframes yet: put them where the server says they are
    for (const a of farm.arms || []) {
      if (!tl.tracks[a.id] || !tl.tracks[a.id].n) tl.tracks[a.id] = appendTrack(null, [[farm.t, a.x, a.z, a.mast, a.heading, a.code, -1]]);
    }
    st.version++;
  };
  tl.onTick = (t) => { if (t != null && t > st.serverT) st.serverT = t; };

  // the server's 'arms' event: every keyframe of one hour of arm work
  tl.onArms = (msg) => {
    if (!msg || !msg.tracks) return;
    for (const [k, v] of Object.entries(msg.tracks)) {
      if (!v.length) continue;
      tl.tracks[+k] = appendTrack(tl.tracks[+k], v, msg.t0 - 2 * 3600);
    }
    const now = wall();
    // queue behind a chunk still playing
    st.chunks = st.chunks.filter((c) => c.end > now);
    const prev = st.chunks[st.chunks.length - 1];
    const start = prev ? prev.end : now;
    st.chunks.push({ first: msg.first, last: msg.last, start, end: start + Math.max(0, msg.last - msg.first) / st.workRate * 1000 });
    st.version++;
  };

  tl.setDecisions = (list) => {
    if (!tl.layout) return;
    tl.jobs = armJobs(list, tl.layout);
    tl.events = armEvents(list);
    tl.evT = Float64Array.from(tl.events.map((e) => e.t));
    st.version++;
  };

  tl.clock = {
    t: () => {
      const now = wall();
      while (st.chunks.length && st.chunks[0].end <= now) st.chunks.shift();
      const c = st.chunks[0];
      if (c) return now < c.start ? c.first : Math.min(c.last, c.first + (now - c.start) / 1000 * st.workRate);
      return st.serverT;
    },
  };
  tl.version = () => st.version;
  return tl;
}
