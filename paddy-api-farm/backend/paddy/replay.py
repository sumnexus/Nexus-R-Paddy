"""Recorded runs → one JSON file the website can replay without a server (the published replay page).

    python -m paddy run --manager api --mock --out runs/demo     # record a season (frames are kept)
    python -m paddy export-replay runs/demo --out frontend/replay/replay.json

The file holds the run's configuration, summary, every decision and call (state sent and raw reply), the events,
the daily history and a square-by-square frame at every 06:00 and 18:00. If the decisions came from the stand-in
mock, the file says so and the website labels the page "stand-in".
"""
from __future__ import annotations

import json
import os

from . import __version__


def _jsonl(path):
    out = []
    if os.path.isfile(path):
        with open(path, encoding='utf-8') as fh:
            for line in fh:
                if line.strip():
                    out.append(json.loads(line))
    return out


def _read(path, default=None):
    if not os.path.isfile(path):
        return default
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def _latest(decisions):
    """decisions.jsonl appends a line at every change; keep the last version of each decision."""
    by = {}
    for d in decisions:
        by[d['id']] = d
    return [by[k] for k in sorted(by)]


def models_info(models=None):
    """The forests a recorded run used: the playground's (``used_here`` unless a paddy forest replaced it) and the paddy
    pack's (v2), for the replay page's Models view."""
    from .core import models as M
    from .engine import model_suite
    from . import modelround as MR
    m = M.ModelSuite.shipped()
    v = model_suite(models)
    pk = MR.pack_models_info(getattr(v, 'paddy', None)) if getattr(v, 'paddy', None) else []
    replaced = {x['replaces'] for x in pk if x.get('replaces')}
    out = [{'id': mid, 'title': met.get('title'), 'task': met.get('task'), 'trees': met.get('trees'), 'nodes': met.get('nodes'),
            'features': met.get('features'), 'metric': met.get('metric'),
            'used_here': mid in ('soil', 'pest', 'yield', 'establish', 'spray', 'harvest') and mid not in replaced}
           for mid, met in m.metrics.items()]
    for x in pk:
        out.append(dict(x, paddy=True, used_here=True))
    return out


def baseline_run(cfg):
    """Conventional practice on the same fields and weather, for the charts (as the live server does)."""
    from .engine import Engine
    be = Engine(seed=cfg['seed'], weatherYear=cfg['weatherYear'], season=cfg['season'], manager='conventional', keep_states=False)
    while be.step_hour():
        pass
    s = be.summary()
    return {'history': be.history,
            'summary': {k: s[k] for k in ('yield_t_ha', 'irrigation_mm', 'n_kg_ha', 'ai_g_ha', 'bio_kg_ha', 'sprayed_squares', 'ch4_kg_ha', 'pump_kwh')},
            'fields': {k: {kk: v[kk] for kk in ('yield_t_ha', 'irrigation_mm', 'n_kg_ha', 'sprays', 'sprayed_squares', 'ch4_kg_ha')} for k, v in s['fields'].items()}}


TABLE_KEYS = ('seasons', 'yield_t_ha', 'irrigation_mm', 'n_kg_ha', 'ai_g_ha', 'bio_kg_ha', 'sprayed_squares', 'ch4_kg_ha', 'calls', 'tokens_in')


def campaign_block(path, title=None, subtitle=None, note=None, base='conventional'):
    """A campaign or comparison file (python -m paddy campaign/compare --out x.json) → the Compare view's table."""
    from . import runs
    with open(path, encoding='utf-8') as fh:
        d = json.load(fh)
    res = d['results']
    S = runs.summarise(res)
    order = [m for m in ('api', 'api-no-advice', 'builtin', 'builtin-cf', 'conventional') if m in S] + [m for m in S if m not in ('api', 'api-no-advice', 'builtin', 'builtin-cf', 'conventional')]
    summary = {m: {k: S[m][k] for k in TABLE_KEYS if k in S[m] and S[m][k] is not None} for m in order}
    base = base if base in S else order[-1]
    paired = {m: {k: runs.paired(res, m, base, k) for k in ('yield_t_ha', 'irrigation_mm', 'n_kg_ha', 'ai_g_ha', 'ch4_kg_ha')} for m in order if m != base}
    n = max(S[m]['seasons'] for m in order)
    labels = {}
    if d.get('mock_identical') is not None:          # a comparison run against the stand-in (compare --mock)
        labels = {'api': 'Decision API (stand-in)', 'api-no-advice': 'Decision API without the advice (stand-in)'}
    return {'title': title or 'Campaign', 'subtitle': subtitle or f'{n} seasons per decision maker, same fields and weather for each',
            'managers': order, 'summary': summary, 'paired': paired, 'base': base, 'note': note, 'labels': labels}


def bundle(cfg, summary, decisions, calls, events, history, frames, api=None, baseline=None, campaign=None, arms=None, weather=None,
           water=None, power=None):
    sources = sorted({c.get('source') for c in calls if c.get('source')})
    stand_in = 'mock' in sources or any('stand-in' in str(c.get('model', '')) for c in calls)
    from . import config as CFG
    from .sim.twin import PEST_KEYS
    cfg = dict(cfg or {})
    cfg.setdefault('rows', CFG.ROWS)
    cfg.setdefault('cols', CFG.COLS)
    return {
        'format': 'paddy-replay/1', 'version': __version__, 'pest_keys': PEST_KEYS,
        'stand_in': stand_in,
        'label': ('Every decision in this season came from the stand-in mock decision API (the built-in manager\'s choices), '
                  'not a real model. It opens on day 60; "Back to day 0" plays the season from the start.'
                  if stand_in else 'A recorded season. It opens on day 60; "Back to day 0" plays it from the start.'),
        'config': cfg, 'api': api, 'sources': sources, 'summary': summary,
        'decisions': decisions, 'calls': calls, 'events': events, 'history': history, 'frames': frames,
        'models': models_info(cfg.get('models', 'v1')), 'baseline': baseline, 'campaign': campaign,
        # v2: the 3D view replays the arms from these keyframes (and each decision's arm_plan), on this layout
        'arms': arms, 'layout': _layout(), 'lat': CFG.LAT, 'camera_hours': list(CFG.CAMERA_HOURS), 'scan_sweep_s': 120,
        'weather_hourly': weather, 'water_hourly': water, 'power_hourly': power, 'season_month': CFG.SEASONS.get(cfg.get('season'), 6), 'energy_cfg': _energy_cfg(),
        # the page opens here: the first 06:00 frame after 60 days, when the crop is growing and the decisions have a story
        'start_index': next((i for i, fr in enumerate(frames or []) if fr.get('day', 0) >= 60), 0),
    }


def _energy_cfg():
    from .engine import energy_config
    c = energy_config()
    return {'pv_kwp': c.get('pvKwp'), 'battery_kwh': c.get('battKwh'), 'biogas': bool(c.get('biogas'))}


def _layout():
    from .robots.layout import layout_info
    return layout_info()


def export_replay(run_dir, out=None, baseline=False, campaigns=()):
    """campaigns: (path, title, subtitle, note) tuples shown in the replay page's Compare view."""
    cfg = (_read(os.path.join(run_dir, 'config.json')) or {})
    frames = _read(os.path.join(run_dir, 'frames.json'), [])
    if not frames:
        raise SystemExit(f'{run_dir} has no frames.json: record the run with frames (python -m paddy run … records them)')
    data = bundle(cfg.get('cfg') or cfg, _read(os.path.join(run_dir, 'summary.json')), _latest(_jsonl(os.path.join(run_dir, 'decisions.jsonl'))),
                  _jsonl(os.path.join(run_dir, 'calls.jsonl')), _jsonl(os.path.join(run_dir, 'events.jsonl')),
                  _read(os.path.join(run_dir, 'history.json'), []), frames, cfg.get('api'),
                  baseline=baseline_run(cfg.get('cfg') or cfg) if baseline else None,
                  campaign=[campaign_block(*c) for c in campaigns] or None, arms=_read(os.path.join(run_dir, 'arm_tracks.json')),
                  weather=_read(os.path.join(run_dir, 'weather_hourly.json')), water=_read(os.path.join(run_dir, 'water_hourly.json')),
                  power=_read(os.path.join(run_dir, 'power_hourly.json')))
    out = out or os.path.join(run_dir, 'replay.json')
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, separators=(',', ':'), default=str)
    return out


def export_engine(e, out, api=None):
    """The same file straight from a finished Engine (used by the tests and the build script)."""
    data = bundle(e.cfg, e.summary_cache or e.summary(), [e._public(d) for d in e.store.decisions], e.store.calls, e.store.events,
                  e.history, e.frames, api, arms=e.tracks_export() if e.arms is not None and e.record_tracks else None,
                  weather=e.weather_hours(0, (e.tw.day + 2) * 24), water=e.water_hours(),
                  power=e.power_log if e.record_frames else None)
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, separators=(',', ':'), default=str)
    return out
