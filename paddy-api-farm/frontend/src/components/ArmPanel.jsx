import { fmtShort } from '../util.js';
import { ACTION_LABEL } from '../util.js';

// One arm, clicked in the 3D farm: what it is doing at the clock's time, the job it is on (from its decision's arm
// plan), what is queued behind it, and its totals for the season so far.
const ROLE_TEXT = {
  planter: 'transplants seedlings (and gap-fills dead squares) and probes the soil',
  waterer: 'opens and closes the inlets and outlets, spreads fertiliser and amendments',
  protector: 'scouts squares and sprays them',
  harvester: 'puddles the fields before transplanting and cuts the ripe crop',
};
const CODE_TEXT = {
  parked: 'parked at its dock', travel: 'travelling to its next stop', return: 'going back to its dock', tostation: 'going to its station', yield: 'making way for another arm',
  station: 'at its station', lower: 'lowering its mast', raise: 'raising its mast', reach: 'reaching for the next square', probe: 'probing the soil', dig: 'opening the mud',
  seed: 'setting a seedling hill', settle: 'firming the mud', valve: 'turning a gate', amend: 'applying an amendment', spread: 'spreading fertiliser', water: 'washing salts out',
  inspect: 'inspecting a square', spray: 'spraying', cut: 'cutting the crop', puddle: 'puddling', wait: 'waiting for a clear path', refill: 'refilling', unload: 'unloading grain',
};
const isoOf = (src, t) => {
  const fr = src.frames?.[0] || src.timeline?.frames?.[0];
  if (!fr) return null;
  const base = Date.parse(fr.time + ':00Z') - fr.t * 1000;
  return new Date(base + t * 1000).toISOString().slice(0, 16);
};
const dur = (s) => (s >= 3600 ? `${(s / 3600).toFixed(1)} h` : s >= 90 ? `${Math.round(s / 60)} min` : `${Math.round(s)} s`);

export function ArmPanel({ src, tl, armId, now, view, decisions, onClose, onShowDecision }) {
  if (!tl?.layout || armId == null) return null;
  const a = tl.layout.arms[armId - 1];
  const pose = view?.arms?.find((x) => x.id === armId);
  const jobs = tl.jobs?.[armId] || [];
  const cur = jobs.find((j) => j.t0 <= now && now <= j.t1 + 1) || null;
  const queued = jobs.filter((j) => j.t0 > now).slice(0, 3);
  const done = jobs.filter((j) => j.t1 < now);
  const sum = (k) => done.reduce((s, j) => s + (j.plan?.[k] || 0), 0);
  const clear = done.map((j) => j.plan?.min_clearance_m).filter((x) => x != null);
  const dec = cur ? decisions.find((d) => d.id === cur.decision) : null;
  return (
    <section className="panel square-panel arm-panel" aria-label={a.title}>
      <header>
        <h2><span className="dot" style={{ background: a.color }} aria-hidden="true" /> {a.title}</h2>
        <button className="btn" onClick={onClose} aria-label="Close">Close</button>
      </header>
      <div className="body">
        <p className="muted small">Arm {a.id}: {ROLE_TEXT[a.role]}.</p>
        <dl className="kv">
          <dt>Now</dt><dd>{pose ? CODE_TEXT[pose.code] || pose.code : '–'}{pose?.task && !pose.task.gate ? ` (square ${sqLabel(pose.task.key)})` : pose?.task?.gate ? ` (${String(pose.task.key).replace('_', ' ')})` : ''}</dd>
          {cur ? <><dt>Job</dt><dd>{ACTION_LABEL[cur.action] || cur.action} {cur.field || ''}, {cur.tasks.length} {cur.tasks.length === 1 ? 'target' : 'targets'}
            {dec ? <> · <button className="linkish" onClick={() => onShowDecision && onShowDecision(dec.id)}>decision {dec.id}</button></> : null}</dd>
            <dt>Started</dt><dd>{fmtShort(isoOf(src, cur.t0))}</dd>
            <dt>Busy until</dt><dd>{fmtShort(isoOf(src, cur.t1))} ({dur(cur.t1 - cur.t0)} in all)</dd>
            {cur.plan ? <><dt>This job</dt><dd>{cur.plan.travel_m} m travelled, {cur.plan.energy_wh} Wh, closest approach {cur.plan.min_clearance_m ?? '–'} m, {cur.plan.collisions} collisions</dd></> : null}</> : null}
          {queued.length ? <><dt>Queued</dt><dd>{queued.map((j) => `${ACTION_LABEL[j.action] || j.action} ${j.field || ''} at ${fmtShort(isoOf(src, j.t0))}`).join('; ')}</dd></> : null}
          <dt>Season so far</dt><dd>{done.length} jobs, {dur(sum('busy_s'))} busy, {Math.round(sum('travel_m'))} m travelled, {Math.round(sum('energy_wh'))} Wh{clear.length ? `, closest approach ${Math.min(...clear).toFixed(2)} m` : ''}</dd>
        </dl>
      </div>
    </section>
  );
}
function sqLabel(i) {
  const col = i % 13, r = Math.floor(i / 13);
  if (col === 6) return '?';
  return `${col < 6 ? 'A' : 'B'}${r + 1},${(col < 6 ? col : col - 7) + 1}`;
}
