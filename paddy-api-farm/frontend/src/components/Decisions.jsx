import { useEffect, useState } from 'react';
import { Icon } from './Icons.jsx';
import { ACTION_LABEL, STATUS_LABEL, describeDecision, fmtShort, fmtWhen, sourceLabel } from '../util.js';

const ARM_TITLE = { planting: 'Planting arm', watering: 'Watering arm', pest: 'Pest arm', harvest: 'Harvest arm' };
const dur = (s) => (s == null ? '–' : s >= 3600 ? `${(s / 3600).toFixed(1)} h` : s >= 90 ? `${Math.round(s / 60)} min` : `${Math.round(s)} s`);

// one line about the arm that carried a decision out (or will, for a pending one): its plan's timing and checks
export function ArmPlanLine({ d }) {
  const p = d.arm_plan || d.arm_preview;
  if (!p || !p.arm) return null;
  const planned = !d.arm_plan;
  const done = (p.tasks || []).filter((t) => t[3] === 'done').length;
  return (
    <p className="armline small">
      <span className={`armdot arm-${p.arm}`} aria-hidden="true" />{ARM_TITLE[p.arm] || p.arm}{planned ? ' (planned)' : ''}:
      {' '}{p.stops ?? '–'} stop{p.stops === 1 ? '' : 's'}, {dur(planned ? (p.end_t != null && p.start_t != null ? p.end_t - p.start_t : null) : p.busy_s)}
      {p.travel_m != null ? `, ${p.travel_m} m` : ''}{p.energy_wh != null ? `, ${p.energy_wh} Wh` : ''}
      {p.min_clearance_m != null ? `, closest approach ${p.min_clearance_m} m` : ''}{p.collisions != null ? `, ${p.collisions} collisions` : ''}
      {!planned && p.tasks ? `, ${done} of ${p.tasks.length} done` : ''}
    </p>
  );
}

export function DecisionItem({ d, onOpen, onApprove, onReject, compact = false, onShow = null, active = false }) {
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const src = sourceLabel(d.source);
  const g = d.guardrail || {};
  const pending = d.status === 'pending_approval';
  const act = async (fn) => {
    setBusy(true);
    try { await fn(); } finally { setBusy(false); }
  };
  return (
    <article className={`dec ${d.status}${active ? ' active' : ''}`} aria-label={`${ACTION_LABEL[d.action] || d.action} for field ${d.field || 'both'}, ${STATUS_LABEL[d.status] || d.status}`}>
      <div className="dec-head">
        <Icon name={d.action} />
        <span className="act">{ACTION_LABEL[d.action] || d.action}{d.field ? ` ${d.field}` : ''}</span>
        <span className="muted small">{describeDecision(d)}</span>
        <span className="time num">{fmtShort(d.created)}</span>
      </div>
      {d.reason ? <p className="reason">{d.reason}</p> : null}
      <div className="chips">
        <span className={`chip ${src.cls}`}>{src.text}</span>
        <span className={`chip v-${g.verdict}`}>{g.verdict === 'accepted' ? 'guardrails: passed' : g.verdict === 'warning' ? 'guardrails: passed with a warning' : 'guardrails: rejected'}</span>
        <span className="chip">{STATUS_LABEL[d.status] || d.status}</span>
        {d.when && d.when !== 'now' && d.exec_at ? <span className="chip">for {fmtShort(d.exec_at)}</span> : null}
        {d.confidence != null ? <span className="chip">confidence {Math.round(d.confidence * 100)}%</span> : null}
      </div>
      {!compact && g.messages && g.messages.length ? <ul className="gmsgs">{g.messages.map((m, i) => <li key={i}>{m}</li>)}</ul> : null}
      {compact && g.verdict === 'rejected' && g.messages?.length ? <ul className="gmsgs"><li>{g.messages[0]}</li></ul> : null}
      <ArmPlanLine d={d} />
      {d.execution && !d.execution.ok ? <p className="outcome">Not carried out: {d.execution.msg}</p> : null}
      {d.rejection_reason ? <p className="outcome">Your reason: {d.rejection_reason}</p> : null}
      {d.outcome?.text ? <p className="outcome">Outcome: {d.outcome.text}</p> : null}
      {pending && onApprove ? (
        <div className="dec-actions">
          <button className="btn approve" disabled={busy} onClick={() => act(() => onApprove(d.id))}>Approve</button>
          <input aria-label="Reason for rejecting (optional)" placeholder="Reason (optional, sent back to the decision maker)" value={reason}
                 onChange={(e) => setReason(e.target.value)} maxLength={300} />
          <button className="btn reject" disabled={busy} onClick={() => act(() => onReject(d.id, reason))}>Reject</button>
        </div>
      ) : null}
      {onOpen || onShow ? (
        <div className="dec-links">
          {onShow && (d.arm_plan || d.arm_preview || d.squares) ? <button className="linkish" aria-pressed={active} onClick={() => onShow(d.id)}>{active ? 'Showing on the farm' : 'Show on the farm'}</button> : null}
          {onOpen ? <button className="linkish" onClick={() => onOpen(d.id)}>Details and the call behind it</button> : null}
        </div>
      ) : null}
    </article>
  );
}

const STATUSES = ['', 'pending_approval', 'scheduled', 'executed', 'rejected', 'rejected_by_person', 'skipped', 'failed', 'expired'];
const ACTIONS = ['', 'soil_check', 'amend', 'puddle', 'transplant', 'irrigate', 'drain', 'fertilise', 'scout', 'spray', 'harvest', 'hold', 'wait'];

export function DecisionsView({ src, decisions, onOpen, onApprove, onReject }) {
  const [field, setField] = useState('');
  const [status, setStatus] = useState('');
  const [action, setAction] = useState('');
  const [showAll, setShowAll] = useState(false);
  const list = decisions.filter((d) => (!field || d.field === field) && (!status || d.status === status) && (!action || d.action === action));
  const pending = list.filter((d) => d.status === 'pending_approval');
  const rest = list.filter((d) => d.status !== 'pending_approval').slice().reverse();
  const shown = showAll ? rest : rest.slice(0, 80);
  return (
    <div>
      <div className="filters" role="group" aria-label="Filter decisions">
        <label>Field <select className="btn" value={field} onChange={(e) => setField(e.target.value)}><option value="">both</option><option>A</option><option>B</option></select></label>
        <label>Status <select className="btn" value={status} onChange={(e) => setStatus(e.target.value)}>{STATUSES.map((s) => <option key={s} value={s}>{s ? STATUS_LABEL[s] : 'any'}</option>)}</select></label>
        <label>Action <select className="btn" value={action} onChange={(e) => setAction(e.target.value)}>{ACTIONS.map((s) => <option key={s} value={s}>{s ? ACTION_LABEL[s] : 'any'}</option>)}</select></label>
        <span className="muted small">{list.length} decision{list.length === 1 ? '' : 's'}</span>
        {src.kind === 'live' ? <a className="small" href={src.exportUrl('current', 'csv')} onClick={(e) => e.preventDefault()} hidden>CSV</a> : null}
      </div>
      {pending.length ? (
        <section style={{ marginBottom: 16 }}>
          <h2 style={{ marginBottom: 8 }}>Waiting for you</h2>
          <div className="feed">{pending.map((d) => <DecisionItem key={d.id} d={d} onOpen={onOpen} onApprove={onApprove} onReject={onReject} />)}</div>
        </section>
      ) : null}
      {shown.length ? (
        <div className="feed">{byDay(shown).map(([day, items]) => (
          <section key={day} className="feed" aria-label={fmtDay(day)}>
            <h3 className="day-head">{fmtDay(day)}</h3>
            {items.map((d) => <DecisionItem key={d.id} d={d} onOpen={onOpen} />)}
          </section>
        ))}</div>
      ) : (
        <p className="empty">No decisions yet. Start the clock: the first decision point is 06:00 on the first day.</p>
      )}
      {!showAll && rest.length > shown.length ? <p style={{ marginTop: 12 }}><button className="btn" onClick={() => setShowAll(true)}>Show all {rest.length}</button></p> : null}
    </div>
  );
}

export function DecisionDetail({ src, id, onBack }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let live = true;
    setData(null);
    src.decision(id).then((r) => live && setData(r)).catch((e) => live && setErr(e.message));
    return () => { live = false; };
  }, [src, id]);
  if (err) return <div className="panel"><div className="body"><p>{err}</p><button className="btn" onClick={onBack}>Back to the decisions</button></div></div>;
  if (!data) return <p className="muted">Loading…</p>;
  const { decision: d, call: c } = data;
  return (
    <div className="stack">
      <div><button className="btn" onClick={onBack}>Back to the decisions</button></div>
      <DecisionItem d={d} />
      <div className="detail-grid">
        <section className="panel">
          <header><h2>The call</h2>{c ? <span className="muted small">{c.id} at {fmtWhen(c.time)}</span> : null}</header>
          <div className="body">
            {c ? (
              <dl className="kv">
                <dt>Why the call was made</dt><dd>{(c.triggers || []).join(', ').replaceAll('_', ' ')}</dd>
                <dt>Answered by</dt><dd>{sourceLabel(c.source).text}{c.model ? ` (${c.model})` : ''}</dd>
                <dt>Result</dt><dd>{c.ok ? 'usable reply' : `failed: ${c.error}`}</dd>
                {c.fallback ? <><dt>Fallback</dt><dd>{c.fallback}</dd></> : null}
                <dt>Latency</dt><dd>{c.latency_ms ? `${Math.round(c.latency_ms)} ms` : '–'}</dd>
                <dt>Attempts</dt><dd>{c.attempts}{c.repaired ? ' (one repair request)' : ''}</dd>
                <dt>Tokens</dt><dd>{c.tokens_in || 0} in, {c.tokens_out || 0} out</dd>
                <dt>Prompt version</dt><dd>{c.prompt_version || '–'}</dd>
                <dt>Decisions in the reply</dt><dd>{c.n_decisions}</dd>
              </dl>
            ) : <p className="muted">The call record is not available.</p>}
            {c && c.schema_errors && c.schema_errors.length ? (
              <><h3 style={{ marginTop: 12 }}>Problems in the reply</h3><ul className="gmsgs">{c.schema_errors.map((e, i) => <li key={i}>{e}</li>)}</ul></>
            ) : null}
            {c && c.notes ? <p className="reason" style={{ marginTop: 10 }}>{c.notes}</p> : null}
          </div>
        </section>
        <section className="panel">
          <header><h2>The reply, as received</h2></header>
          <div className="body"><pre className="json">{c && c.raw_response ? pretty(c.raw_response) : '–'}</pre></div>
        </section>
      </div>
      <ArmPlanPanel d={d} />
      <section className="panel">
        <header><h2>The state that was sent</h2><span className="muted small">everything the decision maker saw</span></header>
        <div className="body"><pre className="json">{c && c.state ? JSON.stringify(c.state, null, 1) : 'Not kept for this run.'}</pre></div>
      </section>
    </div>
  );
}

function byDay(list) {
  const out = [];
  for (const d of list) {
    const day = (d.created || '').slice(0, 10);
    if (!out.length || out[out.length - 1][0] !== day) out.push([day, []]);
    out[out.length - 1][1].push(d);
  }
  return out;
}

function fmtDay(day) {
  return fmtWhen(`${day}T00:00`).replace(/, 00:00$/, '');
}

function pretty(raw) {
  try { return JSON.stringify(JSON.parse(raw), null, 1); } catch (e) { return raw; }
}

const sqName13 = (k) => {
  if (typeof k !== 'number') return String(k).replace('_', ' ');
  const col = k % 13, r = Math.floor(k / 13);
  return `${col < 6 ? 'A' : 'B'}${r + 1},${(col < 6 ? col : col - 7) + 1}`;
};

// the decision's arm plan as stored in its record: the route, each target's time, energy and the collision checks
function ArmPlanPanel({ d }) {
  const p = d.arm_plan || d.arm_preview;
  if (!p || !p.arm) return null;
  const t0 = p.start_t;
  const rel = (t) => (t == null || t0 == null ? '–' : `+${Math.round(t - t0)} s`);
  const stops = (p.route || []).filter((s) => s[0] === 'stop');
  return (
    <section className="panel">
      <header><h2>The arm plan</h2><span className="muted small">{ARM_TITLE[p.arm] || p.arm}{d.arm_plan ? ', as carried out' : ', planned (dry run)'}</span></header>
      <div className="body">
        <dl className="kv">
          <dt>Route</dt><dd>{stops.length} stop{stops.length === 1 ? '' : 's'}{(p.route || []).some((s) => s[0] === 'station') ? ', with a visit to its station' : ''}</dd>
          <dt>Time</dt><dd>{p.start ? `from ${fmtShort(p.start)}, ` : ''}{d.arm_plan ? `busy ${dur(p.busy_s)} (planned ${dur(p.planned_s)})` : `about ${dur(p.end_t != null && t0 != null ? p.end_t - t0 : null)}`}</dd>
          <dt>Travel and energy</dt><dd>{p.travel_m ?? '–'} m, {p.energy_wh ?? '–'} Wh{p.refills ? `, ${p.refills} refill${p.refills === 1 ? '' : 's'}` : ''}</dd>
          <dt>Collision checks</dt><dd>closest approach to another arm {p.min_clearance_m ?? '–'} m, {p.collisions ?? 0} collisions, {p.near_misses ?? 0} near misses{p.traffic_wait_s ? `, waited ${p.traffic_wait_s} s for traffic` : ''}</dd>
        </dl>
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr><th scope="col">Stop</th><th scope="col">Where (m)</th><th scope="col">Targets</th><th scope="col">Shoulder (m)</th></tr></thead>
            <tbody>{stops.slice(0, 12).map((s, i) => (
              <tr key={i}><td>{i + 1}</td><td className="num">{s[1].toFixed(2)}, {s[2].toFixed(2)}</td><td>{(s[3] || []).map(sqName13).join(' ')}</td><td className="num">{s[4] != null ? s[4].toFixed(2) : '–'}</td></tr>
            ))}</tbody>
          </table>
          {stops.length > 12 ? <p className="muted small">…and {stops.length - 12} more stops.</p> : null}
        </div>
        {p.tasks && p.tasks.length ? (
          <div className="tbl-wrap">
            <table className="tbl">
              <thead><tr><th scope="col">Target</th><th scope="col">Started</th><th scope="col">Finished</th><th scope="col">Tool height (m)</th><th scope="col">Status</th></tr></thead>
              <tbody>{p.tasks.slice(0, 40).map((t, i) => (
                <tr key={i}><td>{sqName13(t[0])}</td><td className="num">{rel(t[1])}</td><td className="num">{rel(t[2])}</td><td className="num">{t[4] != null ? t[4].toFixed(2) : '–'}</td><td>{t[3]}</td></tr>
              ))}</tbody>
            </table>
            {p.tasks.length > 40 ? <p className="muted small">…and {p.tasks.length - 40} more targets.</p> : null}
          </div>
        ) : null}
      </div>
    </section>
  );
}
