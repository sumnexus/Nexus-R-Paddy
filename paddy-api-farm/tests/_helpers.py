"""Shared helpers for the tests: put backend/ on the import path, build engines at useful moments."""
from __future__ import annotations

import functools
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, 'backend')
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

os.environ['PADDY_NO_DOTENV'] = '1'      # tests never read .env, and never use a real key
os.environ.pop('API_KEY', None)
os.environ.pop('API_BASE_URL', None)
os.environ.pop('API_MODEL', None)


def engine_at(day, hour=6, seed=11, season='kharif', manager='builtin', **kw):
    """An engine run with the built-in manager up to the given day and hour."""
    from paddy.engine import Engine
    e = Engine(seed=seed, season=season, manager=manager, keep_states=False, **kw)
    target = day * 86400 + hour * 3600
    while e.tw.t < target and e.step_hour():
        pass
    return e


@functools.lru_cache(maxsize=None)
def season_snapshots(seed=11, season='kharif'):
    """Pickled copies of a built-in season at a few moments (land prep, puddled, young crop, flowering, ripe)."""
    import copy
    from paddy.engine import Engine
    e = Engine(seed=seed, season=season, manager='builtin', keep_states=False)
    marks = {}
    while e.step_hour():
        tw = e.tw
        f0 = tw.fields[0]
        prog = tw.field_progress(0)
        if 'land' not in marks and tw.day == 0 and tw.hour >= 7:
            marks['land'] = copy.deepcopy(tw)
        if 'puddled' not in marks and f0.puddled and f0.transplantT is None:
            marks['puddled'] = copy.deepcopy(tw)
        if 'young' not in marks and f0.transplantT is not None and tw.t - f0.transplantT > 3 * 86400:
            marks['young'] = copy.deepcopy(tw)
        if 'flowering' not in marks and 0.62 <= prog < 0.68 and 9 <= tw.hour <= 13:
            marks['flowering'] = copy.deepcopy(tw)
        if 'drain' not in marks and 0.92 <= prog < 0.95:
            marks['drain'] = copy.deepcopy(tw)
        if 'ripe' not in marks and prog >= 1.0 and f0.harvestT is None:
            marks['ripe'] = copy.deepcopy(tw)
    return marks


class FakeEngine:
    """What the guardrails need from an engine: the twin."""

    def __init__(self, tw):
        self.tw = tw
