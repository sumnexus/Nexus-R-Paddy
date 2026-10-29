import { useRef, useState } from 'react';
import { Icon } from './Icons.jsx';

const SPEEDS = [[1, '1 h/s'], [6, '6 h/s'], [24, '1 day/s'], [96, '4 days/s'], [500, '3 weeks/s']];
const REPLAY_SPEEDS = [[1, '½ day/s'], [2, '1 day/s'], [6, '3 days/s'], [16, '8 days/s']];

function Dialog({ dref, title, children, onSubmit, submitLabel }) {
  return (
    <dialog ref={dref} aria-label={title} onClick={(e) => { if (e.target === dref.current) dref.current.close(); }}>
      <form method="dialog" onSubmit={(e) => { e.preventDefault(); onSubmit(new FormData(e.currentTarget)); dref.current.close(); }}>
        <h2>{title}</h2>
        {children}
        <menu>
          <button type="button" className="btn" onClick={() => dref.current.close()}>Cancel</button>
          <button type="submit" className="btn primary">{submitLabel}</button>
        </menu>
      </form>
    </dialog>
  );
}

export function RunControls({ src, status, config, onError, onDone }) {
  const resetRef = useRef(null);
  const eventRef = useRef(null);
  const noteRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const replay = src.kind === 'replay';
  const st = status?.status;
  const run = async (fn, okMsg) => {
    setBusy(true);
    try { await fn(); if (okMsg) onDone(okMsg); } catch (e) { onError(e.message); } finally { setBusy(false); }
  };
  const speeds = replay ? REPLAY_SPEEDS : SPEEDS;
  return (
    <div className="controls" role="toolbar" aria-label="Run controls">
      {st === 'running' ? (
        <button className="btn" onClick={() => run(() => src.pause())} disabled={busy}><Icon name="pause" /> Pause</button>
      ) : (
        <button className="btn primary" onClick={() => run(() => src.start())} disabled={busy || (!replay && st === 'finished')}>
          <Icon name="play" /> {replay && st === 'finished' ? 'Replay again' : 'Run'}
        </button>
      )}
      <button className="btn" onClick={() => run(() => src.step(1))} disabled={busy || st === 'finished'} title={replay ? 'Next decision point' : 'One simulated hour'}
              aria-label={replay ? 'Next decision point' : 'Step one simulated hour'}>
        <Icon name="step" /> <span className="label-wide">{replay ? 'Next' : '1 hour'}</span>
      </button>
      {!replay ? <button className="btn" onClick={() => run(() => src.step(24))} disabled={busy || st === 'finished'}>1 day</button> : null}
      <label className="sr" htmlFor="speed">Speed</label>
      <select id="speed" className="btn" value={status?.speed ?? 24} onChange={(e) => run(() => src.speed(Number(e.target.value)))} aria-label="Speed">
        {speeds.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        {!speeds.some(([v]) => v === status?.speed) && status?.speed ? <option value={status.speed}>{status.speed} h/s</option> : null}
      </select>
      {!replay ? (
        <div className="menu" onKeyDown={(e) => { if (e.key === 'Escape') setOpen(false); }}
             onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setOpen(false); }}>
          <button className="btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>More</button>
          {open ? (
            <div className="menu-list" role="menu">
              <button role="menuitem" className="btn" onClick={() => { setOpen(false); resetRef.current.showModal(); }}>New season…</button>
              <button role="menuitem" className="btn" onClick={() => { setOpen(false); eventRef.current.showModal(); }}>Add an event…</button>
              <button role="menuitem" className="btn" onClick={() => { setOpen(false); noteRef.current.showModal(); }}>Report something…</button>
            </div>
          ) : null}
        </div>
      ) : (
        <button className="btn" onClick={() => run(() => src.reset())}>Back to day 0</button>
      )}

      {!replay ? (
        <Dialog dref={resetRef} title="Start a new season" submitLabel="Start the season" onSubmit={(fd) => run(() => src.reset({
          seed: Number(fd.get('seed')), weatherYear: Number(fd.get('weatherYear')), season: fd.get('season'), manager: fd.get('manager'), mode: fd.get('mode'),
        }), 'New season ready: press Run.')}>
          <label>Season<select name="season" defaultValue={status?.season || 'kharif'}><option value="kharif">Kharif (from 1 June)</option><option value="rabi">Rabi (from 1 November)</option></select></label>
          <label>Decisions by<select name="manager" defaultValue={status?.manager || 'builtin'}>
            <option value="api">the decision API{config?.mock ? ' (stand-in mock)' : ''}</option>
            <option value="api-no-advice">the decision API, without the forests' advice</option>
            <option value="builtin">the built-in manager</option>
            <option value="conventional">conventional practice (calendar, continuous flooding)</option>
          </select></label>
          <label>Approval<select name="mode" defaultValue={status?.mode || 'auto'}><option value="auto">carry decisions out automatically</option><option value="approve">wait for my approval</option></select></label>
          <label>Field seed (soils and pests)<input name="seed" type="number" defaultValue={status?.seed ?? 20261024} /></label>
          <label>Weather year (0 = the default year)<input name="weatherYear" type="number" min="0" defaultValue={status?.weatherYear ?? 0} /></label>
        </Dialog>
      ) : null}
      {!replay ? (
        <Dialog dref={eventRef} title="Add an event" submitLabel="Add it" onSubmit={(fd) => run(() => src.event({
          kind: fd.get('kind'), field: fd.get('field') || undefined, pest: fd.get('pest') || undefined, day: fd.get('day') ? Number(fd.get('day')) : undefined,
        }), 'Event added.')}>
          <label>What<select name="kind" defaultValue="pest"><option value="pest">Pest outbreak</option><option value="storm">Heavy storm (about 95 mm)</option>
            <option value="dry">Dry spell (21 days)</option><option value="cloudy">Overcast week</option></select></label>
          <label>Field (outbreaks)<select name="field" defaultValue=""><option value="">either</option><option>A</option><option>B</option></select></label>
          <label>Pest (outbreaks)<select name="pest" defaultValue=""><option value="">any paddy pest</option><option value="yellow_stem_borer">yellow stem borer</option>
            <option value="brown_planthopper">brown planthopper</option><option value="leaf_folder">leaf folder</option></select></label>
          <label>On day of the season (empty = now)<input name="day" type="number" min="0" /></label>
        </Dialog>
      ) : null}
      {!replay ? (
        <Dialog dref={noteRef} title="Report something" submitLabel="Send it" onSubmit={(fd) => {
          const kind = fd.get('kind');
          const body = kind === 'pest'
            ? { kind, field: fd.get('field'), square: fd.get('square'), pest: fd.get('pest'), severity: Number(fd.get('severity')) / 100 }
            : kind === 'water' ? { kind, field: fd.get('field'), depth_mm: Number(fd.get('depth')) } : { kind, field: fd.get('field') || null, text: fd.get('text') };
          run(() => src.observe(body), 'Sent: the decision maker sees it at the next decision point.');
        }}>
          <label>Kind<select name="kind" defaultValue="note"><option value="note">A note for the decision maker</option><option value="pest">A pest I saw</option><option value="water">A water-tube reading</option></select></label>
          <label>Field<select name="field" defaultValue="A"><option>A</option><option>B</option></select></label>
          <label>Note<textarea name="text" rows="2" maxLength={300} placeholder="e.g. the bund on the east side of A is leaking" /></label>
          <label>Square (pest)<input name="square" placeholder="row,column, e.g. 3,4" /></label>
          <label>Pest<select name="pest" defaultValue="brown_planthopper"><option value="yellow_stem_borer">yellow stem borer</option><option value="brown_planthopper">brown planthopper</option><option value="leaf_folder">leaf folder</option></select></label>
          <label>Severity % (pest)<input name="severity" type="number" min="0" max="100" defaultValue="5" /></label>
          <label>Depth mm (water reading)<input name="depth" type="number" defaultValue="0" /></label>
        </Dialog>
      ) : null}
    </div>
  );
}
