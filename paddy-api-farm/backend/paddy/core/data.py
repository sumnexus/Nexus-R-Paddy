# Copied from the Agri Robotics Playground's Python port (agriplay/data.py) for the Paddy API Farm; unchanged below.
"""Crop, pest and pesticide libraries, crop import checks and prices.

The libraries themselves live in ``library.py``; this module validates and normalises
crops (the importer uses the same rules), works out pesticide efficacy and holds the
prices the crop planner weighs.
"""
from __future__ import annotations

import math
import re

from . import library as _lib
from .util import Obj, to_obj, js_round

CROP_SCHEMA = dict(_lib.CROP_SCHEMA)
PESTS = {k: to_obj(v) for k, v in _lib.PESTS.items()}
PESTICIDES = {k: to_obj(v) for k, v in _lib.PESTICIDES.items()}
PEST_CATS = list(_lib.PEST_CATS)
PESTICIDE_KIND_RANK = dict(_lib.PESTICIDE_KIND_RANK)


def pesticide_efficacy(pesticide_id, pest_id) -> float:
    pc, pest = PESTICIDES.get(pesticide_id), PESTS.get(pest_id)
    if not pc or not pest:
        return 0.0
    ov = pest.get('effOverride')
    if ov and ov.get(pesticide_id) is not None:
        return ov[pesticide_id]
    return pc.eff.get(pest.cat) or 0.0


REQUIRED = ['id', 'name', 'form', 'durationDays', 'stages', 'harvest', 'kc', 'tBase', 'tOpt', 'tMax', 'ph', 'phTol',
            'sand', 'ecTol', 'npk', 'yieldTHa', 'monthFit', 'pests', 'visual']


def _num(v, lo, hi):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and lo <= v <= hi


def _pair(v, lo, hi):
    return isinstance(v, (list, tuple)) and len(v) == 2 and _num(v[0], lo, hi) and _num(v[1], lo, hi) and v[0] <= v[1]


def _fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def check_crop(c: dict) -> list:
    """Range and consistency checks for an imported crop; returns a list of problems."""
    p = []
    g = c.get
    if not isinstance(g('id'), str) or not re.fullmatch(r'[a-z0-9_-]+', g('id') or ''):
        p.append(f'id "{g("id")}" must be lowercase letters, digits, - or _')
    if g('form') not in ('grass', 'tallgrass', 'bush', 'creeper'):
        p.append('form must be grass, tallgrass, bush or creeper')
    if not _num(g('durationDays'), 20, 1500):
        p.append('durationDays must be 20–1500')
    st = g('stages')
    if not isinstance(st, list) or len(st) < 2:
        p.append('stages must list at least two stages')
    else:
        bad = False
        for i, s in enumerate(st):
            if not isinstance(s, dict) or not s.get('name') or not _num(_fnum(s.get('end')), 0.01, 1):
                bad = True
                break
            if i and _fnum(s.get('end')) <= _fnum(st[i - 1].get('end')):
                bad = True
                break
        if bad:
            p.append('stages need names and rising `end` fractions up to 1')
    hv = g('harvest')
    if not isinstance(hv, dict) or hv.get('type') not in ('single', 'multi'):
        p.append("harvest.type must be 'single' or 'multi'")
    elif hv.get('type') == 'multi' and ((hv.get('start') is not None and not _num(hv.get('start'), 0.05, 0.95)) or
                                         (hv.get('interval') is not None and not _num(hv.get('interval'), 1, 60))):
        p.append('multi harvest needs start 0.05–0.95 and interval 1–60 days')
    kc = g('kc')
    if not isinstance(kc, dict) or not all(_num(kc.get(k), 0.1, 1.6) for k in ('ini', 'mid', 'end')):
        p.append('kc needs ini, mid and end between 0.1 and 1.6')
    if g('ky') is not None and not _num(g('ky'), 0.1, 2):
        p.append('ky must be 0.1–2')
    if g('depletionP') is not None and not _num(g('depletionP'), 0.05, 0.95):
        p.append('depletionP must be 0.05–0.95')
    t_ok = _num(g('tBase'), -5, 25) and _pair(g('tOpt'), 0, 45) and _num(g('tMax'), 15, 50)
    if not t_ok or not (g('tBase') < g('tOpt')[0] and g('tOpt')[1] < g('tMax')):
        p.append('temperatures need tBase < tOpt[0] ≤ tOpt[1] < tMax')
    if (not _pair(g('ph'), 3, 10) or not _pair(g('phTol'), 3, 10) or g('phTol')[0] > g('ph')[0] or g('phTol')[1] < g('ph')[1]):
        p.append('ph needs [low, high] inside phTol [low, high] (3–10)')
    if not _pair(g('sand'), 0, 100):
        p.append('sand needs [min, max] between 0 and 100')
    if not _num(g('ecTol'), 0.1, 20):
        p.append('ecTol must be 0.1–20 dS/m')
    if g('wlTol') is not None and not _num(g('wlTol'), 0, 1):
        p.append('wlTol must be 0–1')
    npk = g('npk')
    if not isinstance(npk, list) or len(npk) != 3 or not all(_num(v, 0, 1000) for v in npk):
        p.append('npk needs three numbers (kg/ha)')
    if not _num(g('yieldTHa'), 0.05, 500):
        p.append('yieldTHa must be above 0')
    if g('waterNeedMm') is not None and not _num(g('waterNeedMm'), 50, 4000):
        p.append('waterNeedMm must be 50–4000')
    mf = g('monthFit')
    if not isinstance(mf, list) or len(mf) != 12 or not all(_num(v, 0, 1) for v in mf):
        p.append('monthFit needs 12 numbers between 0 and 1')
    if not isinstance(g('name'), str) or not g('name').strip():
        p.append('name must be text')
    if g('short') is not None and not isinstance(g('short'), str):
        p.append('short must be text')
    if g('family') is not None and not isinstance(g('family'), str):
        p.append('family must be text')
    texts = [('name', g('name')), ('short', g('short')), ('family', g('family')), ('local', g('local')),
             ('scientific', g('scientific')), ('seedPerSite.unit', (g('seedPerSite') or {}).get('unit') if isinstance(g('seedPerSite'), dict) else None)]
    if isinstance(st, list):
        texts += [(f'stage {i + 1} name', s.get('name') if isinstance(s, dict) else None) for i, s in enumerate(st)]
    marked = [k for k, v in texts if isinstance(v, str) and re.search(r'[<>]', v)]
    if marked:
        p.append(f"{', '.join(marked)} must be plain text (no < or >)")
    if g('establishment') is not None and g('establishment') not in ('direct', 'transplant'):
        p.append("establishment must be 'direct' or 'transplant'")
    if g('ponded') is not None and not isinstance(g('ponded'), bool):
        p.append('ponded must be true or false')
    if g('ecSlope') is not None and not _num(g('ecSlope'), 0, 1):
        p.append('ecSlope must be 0–1 (yield loss per dS/m)')
    if g('susceptibility') is not None and not _num(g('susceptibility'), 0, 5):
        p.append('susceptibility must be a number 0–5')
    if g('priceRsKg') is not None and not _num(g('priceRsKg'), 0.1, 5000):
        p.append('priceRsKg must be a number 0.1–5000 (₹ per kg)')
    if g('convSeedFactor') is not None and not _num(g('convSeedFactor'), 1, 20):
        p.append('convSeedFactor must be 1–20')
    sps = g('seedPerSite')
    if sps is not None and (not isinstance(sps, dict) or not _num(sps.get('amount'), 0.01, 1000) or not isinstance(sps.get('unit'), str)):
        p.append('seedPerSite needs {amount: number, unit: text}')
    pests = g('pests')
    if not isinstance(pests, list):
        p.append('pests must be a list of pest ids')
    else:
        unknown = [x for x in pests if x not in PESTS]
        if unknown:
            p.append(f"unknown pests {', '.join(map(str, unknown))} (known: {', '.join(PESTS)})")
    v = g('visual')
    hexok = lambda x: isinstance(x, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', x)
    if not isinstance(v, dict):
        p.append('visual must be an object')
    else:
        if not _num(v.get('height'), 0.05, 8) or not _num(v.get('spread'), 0.05, 5):
            p.append('visual.height (0.05–8 m) and visual.spread (0.05–5 m) must be numbers')
        if v.get('perSite') is not None and not _num(v.get('perSite'), 1, 9):
            p.append('visual.perSite must be 1–9')
        bad_col = [k for k in ('leaf', 'leafDry', 'fruit', 'fruitUnripe', 'flower') if v.get(k) is not None and not hexok(v.get(k))]
        if bad_col:
            p.append(f"visual colours must be #rrggbb ({', '.join(bad_col)})")
    return p


class CropError(ValueError):
    pass


def normalise_crop(c: dict) -> Obj:
    """Validate a crop and fill in defaults (the importer uses this)."""
    missing = [k for k in REQUIRED if c.get(k) is None]
    if missing:
        raise CropError(f'Crop "{c.get("id") or c.get("name") or "?"}" is missing: {", ".join(missing)}')
    problems = check_crop(c)
    if problems:
        more = f' (+{len(problems) - 3} more)' if len(problems) > 3 else ''
        raise CropError(f'Crop "{c.get("id")}": {"; ".join(problems[:3])}{more}')
    out = to_obj({
        'short': c['name'], 'local': '', 'scientific': '', 'family': 'Other', 'establishment': 'direct', 'ky': 1.0,
        'depletionP': 0.5, 'ponded': False, 'ecSlope': 0.1, 'wlTol': 0.3, 'waterNeedMm': 500,
        'seedPerSite': {'amount': 5, 'unit': 'seeds'}, 'convSeedFactor': 1.3, 'susceptibility': 1,
    })
    out.update(to_obj(dict(c)))
    out.stages = [Obj(name=str(s['name']), end=float(s['end'])) for s in c['stages']]
    out.stages[-1].end = 1.0
    out.pests = [p for p in (c.get('pests') or []) if p in PESTS]
    vis = to_obj({'height': 0.8, 'spread': 0.5, 'perSite': 4, 'leaf': '#4a8a3a', 'leafDry': '#a8975a', 'fruit': '#c0392b',
                  'fruitUnripe': '#6aa84f', 'flower': '#f1d54b'})
    vis.update(to_obj(dict(c['visual'])))
    vis.perSite = max(1, min(9, js_round(vis.perSite)))
    out.visual = vis
    if out.harvest.type == 'multi':
        h = Obj(start=0.5, interval=6)
        h.update(out.harvest)
        out.harvest = h
    return out


CROPS = [normalise_crop(c) for c in _lib.CROPS_RAW]

# Typical prices the farm gets (₹/kg, 2026): grains and groundnut at the kharif 2026-27 minimum support prices
# (paddy ₹2,441, ragi ₹5,205, maize ₹2,410, groundnut ₹7,517 per quintal); vegetables at typical Bengaluru-area
# APMC modal wholesale prices (tomato ₹13–24, brinjal ₹22–32, beans ₹43, green chilli ≈ ₹30–34).
# Vegetable prices swing several-fold within a year; these are planning figures, not forecasts.
PRICES_RS_KG = {'ragi': 52, 'paddy': 24.4, 'maize': 24.1, 'groundnut': 75, 'tomato': 14, 'beans': 40, 'brinjal': 25, 'chilli': 32}
_pv = sorted(PRICES_RS_KG.values())
PRICE_MEDIAN = (_pv[(len(_pv) - 1) >> 1] + _pv[len(_pv) >> 1]) / 2


def crop_price(c) -> float:
    if c and (c.get('priceRsKg') or 0) > 0:
        return float(c['priceRsKg'])
    if c and PRICES_RS_KG.get(c.get('id')) is not None:
        return PRICES_RS_KG[c['id']]
    return PRICE_MEDIAN


# weight of the harvest's value in crop choice (Agent.plan_crops); 0.1 chosen by A/B over 8 farm-years in the
# web version: +20 % kg and +13 % value at the same share of attainable yield, 3 % less water, the same failed plantings
PLAN_VALUE_WEIGHT = 0.1
