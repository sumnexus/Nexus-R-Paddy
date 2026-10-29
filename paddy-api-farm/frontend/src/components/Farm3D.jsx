import { useEffect, useMemo, useRef, useState } from 'react';
import { GX } from '../three/gl.js';
import { PaddyScene, resetCssCache } from '../three/scene.js';
import { viewAt, sceneState, fnv1a, parseSq, sqName, previewJob } from '../three/view.js';

// The 3D farm: the playground's renderer and camera showing the two paddies, their water and rice, and the four arms
// doing exactly what the recorded (or live) arm plans say, at the clock's time. Click a square or an arm to inspect
// it; drag to orbit, right-drag or Shift-drag to pan, wheel or pinch to zoom; arrows, WASD, +/− and PageUp/PageDown
// work when the view has focus.
export const PRESETS = [
  ['overview', 'Overview'], ['A', 'Field A'], ['B', 'Field B'], ['follow1', 'Planting arm'], ['follow2', 'Watering arm'], ['follow3', 'Pest arm'], ['follow4', 'Harvest arm'],
];

function hasWebGL2() {
  try { const c = document.createElement('canvas'); return !!c.getContext('webgl2'); } catch (e) { return false; }
}
export const WEBGL2 = typeof document !== 'undefined' ? hasWebGL2() : false;

function rendererName(gl) {
  try {
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    return ext ? String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) || '') : String(gl.getParameter(gl.RENDERER) || '');
  } catch (e) { return ''; }
}

// Choose a target and distance so every point lands inside the canvas for the given azimuth and elevation
// (the playground's fitView, without the side panels).
function fitView(cam, W, H, az, el, pts, margin = 0.08) {
  const M4 = GX.M4, asp = W / H, t = Math.tan(cam.fov / 2);
  let c = [0, 0, 0];
  for (const p of pts) { c[0] += p[0] / pts.length; c[2] += p[2] / pts.length; }
  let d = 40;
  const right = [Math.cos(az), 0, -Math.sin(az)], fwd = [-Math.sin(az), 0, -Math.cos(az)], sinEl = Math.max(0.2, Math.sin(el));
  for (let it = 0; it < 12; it++) {
    const ce = Math.cos(el);
    const eye = [c[0] + d * ce * Math.sin(az), c[1] + d * Math.sin(el), c[2] + d * ce * Math.cos(az)];
    const proj = M4.perspective(cam.fov, asp, 0.1, 600);
    const m = M4.mul(proj, M4.lookAt(eye, c, [0, 1, 0]));
    let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
    for (const p of pts) {
      const cw = Math.max(0.05, m[3] * p[0] + m[7] * p[1] + m[11] * p[2] + m[15]);
      const nx = (m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12]) / cw, ny = (m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13]) / cw;
      x0 = Math.min(x0, nx); x1 = Math.max(x1, nx); y0 = Math.min(y0, ny); y1 = Math.max(y1, ny);
    }
    const s = Math.max((x1 - x0) / 2, (y1 - y0) / 2) / (1 - margin);
    const wx = ((x0 + x1) / 2) * d * t * asp, wy = ((y0 + y1) / 2) * d * t / sinEl;
    c = [c[0] + right[0] * wx + fwd[0] * wy, 0, c[2] + right[2] * wx + fwd[2] * wy];
    d = Math.min(cam.maxDist, Math.max(cam.minDist, d * s));
  }
  return { target: c, dist: d, az, el };
}

export function Farm3D({ tl, clock, overlay, selected, onSelect, highlight, preview, onFallback, onWatchArms, themeKey, compact }) {
  const wrap = useRef(null), canvasRef = useRef(null), labelsRef = useRef(null);
  const st = useRef({});
  const [err, setErr] = useState(null);
  const [preset, setPreset] = useState('overview');
  const [stats, setStats] = useState({ fps: 0, soft: false, renderer: '' });
  const [hover, setHover] = useState(null);
  const reduced = useMemo(() => typeof window !== 'undefined' && window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches, []);
  // phones and small or slow devices get the lighter render (fewer hills per square, no shadows, a smaller soil
  // texture); localStorage 'paddy-3d-quality' = 'high' | 'low' overrides the guess
  const low = useMemo(() => {
    if (typeof window === 'undefined') return false;
    let q = null;
    try { q = localStorage.getItem('paddy-3d-quality'); } catch (e) { q = null; }
    if (q === 'high') return false;
    if (q === 'low') return true;
    return (window.matchMedia && window.matchMedia('(pointer: coarse)').matches) || window.innerWidth < 700 || (navigator.hardwareConcurrency || 8) <= 2;
  }, []);

  // props the frame loop reads without restarting
  st.current.props = { tl, clock, overlay, selected, highlight, preview, themeKey };

  useEffect(() => {
    const canvas = canvasRef.current;
    let engine;
    try { engine = new GX.Engine(canvas, { shadows: !low, shadowSize: low ? 1024 : 2048, preserve: true }); }
    catch (e) { setErr(e.message); onFallback && onFallback(e.message); return undefined; }
    const cam = new GX.OrbitCamera(canvas);
    cam.instant = reduced;
    const scene = new PaddyScene(engine, { low, reduced });
    scene.attachLabels(labelsRef.current);
    scene.build(tl.layout, { pvKwp: tl.energy?.pv_kwp });
    const S = st.current;
    Object.assign(S, { engine, cam, scene, follow: null, renderScale: 1, slowT: 0, fastT: 0, ft: 0, n: 0, acc: 0, last: performance.now() });
    const renderer = rendererName(engine.gl);
    const soft = /swiftshader|llvmpipe|software|softpipe|mesa offscreen/i.test(renderer);
    S.soft = soft;
    cam.onClick = (e) => {
      const view = S.view;
      if (!view) return;
      const hit = scene.pick(cam.ray(e.clientX, e.clientY), view);
      if (!hit) { onSelect && onSelect(null); return; }
      if (hit.type === 'arm') onSelect && onSelect({ type: 'arm', id: hit.id });
      else onSelect && onSelect({ type: 'square', sq: sqName(hit.f, hit.r, hit.c) });
    };
    const onMove = (e) => {
      if (!S.view || e.buttons) return;
      const hit = scene.pick(cam.ray(e.clientX, e.clientY), S.view);
      const key = hit ? (hit.type === 'arm' ? `arm:${hit.id}` : sqName(hit.f, hit.r, hit.c)) : null;
      if (key !== S.hoverKey) { S.hoverKey = key; setHover(key); }
    };
    canvas.addEventListener('pointermove', onMove);
    const resize = () => {
      const dpr = Math.max(0.5, Math.min(window.devicePixelRatio || 1, low ? 1.25 : 2) * S.renderScale);
      const w = Math.max(1, Math.floor(canvas.clientWidth * dpr)), h = Math.max(1, Math.floor(canvas.clientHeight * dpr));
      if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    };
    S.applyPreset = (name, instant) => {
      const L = tl.layout, fr = L.frame;
      const W = canvas.clientWidth || 800, H = canvas.clientHeight || 500;
      S.follow = null;
      if (name.startsWith('follow')) {
        // from the fields' side (east-south-east), a little above the shoulder height, so the followed arm is in front
        S.follow = +name.slice(6);
        const a = S.view && S.view.arms.find((x) => x.id === S.follow);
        cam.set({ target: a ? [a.x, 2.0, a.z] : cam.goal.target, dist: low ? 8.5 : 7.5, az: 1.15, el: 0.42 }, instant);
        return;
      }
      if (name === 'overview') {
        const pts = [];
        for (const x of [fr.x0, fr.x1]) for (const z of [fr.z0, fr.z1]) for (const y of [0, fr.top]) pts.push([x, y, z]);
        // a tall (phone) view looks along the frame's long side so the farm fills it
        cam.set(W / H < 0.95 ? fitView(cam, W, H, 1.2, 0.72, pts, 0.03) : fitView(cam, W, H, -0.42, 0.62, pts, 0.04), instant);
      } else if (name === 'A' || name === 'B') {
        const F = L.fields[name], o = L.outer;
        const pts = [];
        for (const x of F.x) for (const z of [o.z0, o.z1]) for (const y of [0, 1.1]) pts.push([x, y, z]);
        // low enough (about 30°) that the line of sight passes under the frame's rails into the field
        cam.set(fitView(cam, W, H, name === 'A' ? -0.3 : 0.3, 0.52, pts, 0.04), instant);
      }
    };
    resize();
    S.applyPreset('overview', true);
    let raf = 0, alive = true;
    const frame = (now) => {
      if (!alive) return;
      raf = requestAnimationFrame(frame);
      if (document.hidden) { S.last = now; return; }
      const raw = Math.max(0.001, (now - S.last) / 1000), dt = Math.min(0.25, raw);
      S.last = now;
      try {
        resize();
        const P = S.props;
        const t = P.clock.t();
        const view = viewAt(P.tl, t);
        S.view = view;
        const fa = S.follow ? view.arms.find((a) => a.id === S.follow) : null;
        cam.update(dt, canvas.width / canvas.height, fa ? [fa.x, 2.0, fa.z] : null);
        const sel = P.selected;
        const ui = {
          overlay: P.overlay, themeKey: P.themeKey,
          selected: sel?.type === 'arm' ? sel : sel?.sq ? { type: 'square', ...parseSq(sel.sq) } : null,
          highlight: P.highlight, preview: P.preview, previewT: now / 1000,
        };
        scene.update(dt, now / 1000, cam, view, ui);
        engine.render(cam, scene.env(view, now / 1000));
        // frame-rate bookkeeping and adaptive resolution (the playground's rule)
        S.n++; S.acc += Math.min(raw, 2);
        S.ft = S.ft ? S.ft * 0.94 + Math.min(raw, 0.5) * 0.06 : raw;
        S.slowT = S.ft > 0.045 ? S.slowT + raw : 0;
        S.fastT = S.ft < 0.022 ? S.fastT + raw : 0;
        if (S.slowT > 2 && S.renderScale > 0.5) { S.renderScale = Math.max(0.5, S.renderScale * 0.85); S.slowT = 0; }
        else if (S.fastT > 5 && S.renderScale < 1) { S.renderScale = Math.min(1, S.renderScale / 0.85); S.fastT = 0; }
        if (S.acc >= 1) {
          const fps = S.n / S.acc;
          S.n = 0; S.acc = 0;
          setStats({ fps, soft, renderer, scale: S.renderScale });
        }
        // for the browser tests: the state the 3D view shows, as a short hash, and where things are on screen
        if (!S.hook || now - S.hookT > 250) {
          S.hookT = now;
          const proj = (p) => { const q = cam.project(p); if (!q) return null; const r = canvas.getBoundingClientRect(); return { x: r.left + q.x, y: r.top + q.y }; };
          window.__PADDY_3D__ = S.hook = { t, fi: view.fi, hash: fnv1a(sceneState(view, true)), state: sceneState(view, true), draws: engine.stats.draws,
            tris: engine.stats.tris, renderer, soft, low, reduced, highlight: P.highlight ? P.highlight.id : null, preview: P.preview ? P.preview.decision : null,
            arms: view.arms.map((a) => ({ id: a.id, x: a.x, z: a.z, code: a.code, shoulderY: a.shoulderY, target: a.target })),
            square: (name) => { const q = parseSq(name); if (!q) return null; const c = q.f === 'A' ? q.c : q.c + 7; return proj([c + 0.5 - 6.5, 0.05, q.r + 0.5 - 3]); },
            arm: (id) => { const a = view.arms.find((x) => x.id === id); return a ? proj([a.x, (a.shoulderY + tl.layout.frame.rail) / 2, a.z]) : null; } };
        }
      } catch (e) {
        console.error(e);
        alive = false;
        setErr(e.message);
      }
    };
    raf = requestAnimationFrame(frame);
    onWatchArms && onWatchArms(true);
    return () => {
      alive = false;
      cancelAnimationFrame(raf);
      canvas.removeEventListener('pointermove', onMove);
      scene.dispose();
      onWatchArms && onWatchArms(false);
      const lose = engine.gl.getExtension('WEBGL_lose_context');
      if (lose) lose.loseContext();
    };
  }, [tl.layout]);   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { resetCssCache(); }, [themeKey]);

  const choose = (name) => { setPreset(name); st.current.applyPreset && st.current.applyPreset(name, reduced); };

  if (err) {
    return (
      <div className="farm3d-fallback" role="note">
        <strong>The 3D farm can't run in this browser</strong>
        <span>{/webgl ?2/i.test(err) ? 'It needs WebGL 2, which this browser or device does not provide.' : err} The plan view below shows the same squares.</span>
      </div>
    );
  }
  return (
    <div className={`farm3d${compact ? ' compact' : ''}`} ref={wrap}>
      <div className="farm3d-bar" role="group" aria-label="Camera">
        {PRESETS.map(([k, l]) => (
          <button key={k} className="btn small" aria-pressed={preset === k} onClick={() => choose(k)}>{l}</button>
        ))}
      </div>
      <div className="farm3d-stage">
        <canvas ref={canvasRef} className="farm3d-canvas" tabIndex={0} role="img"
                aria-label="3D view of the two paddy fields and the four arms. Use the arrow keys to orbit, WASD or Shift+arrows to pan, plus and minus to zoom; the plan view lists every square." />
        <div className="labels" ref={labelsRef} />
        {hover ? <div className="farm3d-hover" aria-hidden="true">{hover.startsWith('arm:') ? tl.layout.arms[+hover.slice(4) - 1]?.title : `Square ${hover}`}</div> : null}
        <div className="farm3d-stats" aria-live="off" title={stats.renderer}>
          {stats.fps ? `${Math.round(stats.fps)} fps` : '…'}{stats.soft ? ' · software rendering' : ''}{low ? ' · light mode' : ''}{reduced ? ' · reduced motion' : ''}
        </div>
      </div>
    </div>
  );
}

export { previewJob };
