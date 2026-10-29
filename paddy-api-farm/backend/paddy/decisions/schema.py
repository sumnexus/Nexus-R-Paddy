"""The action menu and the JSON the decision API must return.

One source of truth: ``ACTION_PARAMS`` and ``DECISION_BASE`` are written in a small subset of JSON Schema (type,
enum, required, properties, additionalProperties, items, minimum, maximum, minLength, maxLength, minItems,
maxItems). ``validate`` checks a response against them with a tiny validator (no third-party library), and
``response_schema()`` publishes the same rules as a standard JSON Schema document (for the prompt, the README and
the OpenAPI document).

Structure is checked here; whether a decision is safe and sensible is the guardrails' job.
"""
from __future__ import annotations

import copy
import json
import math
import re

from .. import config as CFG
from ..core import data as D

PADDY = next(c for c in D.CROPS if c.id == 'paddy')
PRODUCTS = sorted(D.PESTICIDES.keys())
FERT_PRODUCTS = ['urea', 'dap', 'mop', 'ssp', 'complex_10_26_26']
AMEND_PRODUCTS = ['lime', 'gypsum', 'sulphur', 'compost', 'leach']
SPLITS = ['basal', 'tillering', 'panicle_initiation', 'other']

ACTION_PARAMS = {
    'soil_check': {'type': 'object', 'properties': {}, 'additionalProperties': False,
                   'description': 'Probe squares precisely (all squares of the field when "squares" is null).'},
    'amend': {'type': 'object', 'required': ['product', 'dose'], 'additionalProperties': False,
              'description': 'Soil amendment on the listed squares. dose: g/m² for lime, gypsum, sulphur and compost; mm of water for leach.',
              'properties': {'product': {'type': 'string', 'enum': AMEND_PRODUCTS}, 'dose': {'type': 'number', 'minimum': 0, 'maximum': 10000}}},
    'puddle': {'type': 'object', 'properties': {}, 'additionalProperties': False,
               'description': 'Puddle the flooded field (needs at least 20 mm of standing water) before transplanting.'},
    'transplant': {'type': 'object', 'properties': {}, 'additionalProperties': False,
                   'description': 'Transplant nursery seedlings into the puddled field (all squares not yet planted when "squares" is null; '
                                  'squares whose seedlings died can be gap-filled within 15 days). The seedlings\' age is set by the nursery.'},
    'irrigate': {'type': 'object', 'required': ['target_depth_mm'], 'additionalProperties': False,
                 'description': 'Pump water in until the field water level reaches target_depth_mm above the soil surface.',
                 'properties': {'target_depth_mm': {'type': 'number', 'minimum': -1000, 'maximum': 1000}}},
    'drain': {'type': 'object', 'required': ['to_depth_mm'], 'additionalProperties': False,
              'description': 'Open the outlet until standing water is down to to_depth_mm (0 = no standing water).',
              'properties': {'to_depth_mm': {'type': 'number', 'minimum': -1000, 'maximum': 1000}}},
    'hold': {'type': 'object', 'properties': {}, 'additionalProperties': False,
             'description': 'Keep the bunds closed and change nothing in this field.'},
    'fertilise': {'type': 'object', 'required': ['product', 'kg_per_ha'], 'additionalProperties': False,
                  'description': 'Broadcast fertiliser (all squares when "squares" is null). kg_per_ha is product, not nutrient.',
                  'properties': {'product': {'type': 'string', 'enum': FERT_PRODUCTS}, 'kg_per_ha': {'type': 'number', 'minimum': 0, 'maximum': 100000},
                                 'split': {'type': 'string', 'enum': SPLITS}}},
    'scout': {'type': 'object', 'properties': {}, 'additionalProperties': False,
              'description': 'Close-up inspection of the listed squares (exact pest reading).'},
    'spray': {'type': 'object', 'required': ['product'], 'additionalProperties': False,
              'description': 'Spot-spray the listed squares at the label dose.',
              'properties': {'product': {'type': 'string', 'enum': PRODUCTS}, 'target_pest': {'type': 'string', 'enum': list(PADDY.pests)}}},
    'harvest': {'type': 'object', 'additionalProperties': False,
                'description': 'Harvest the whole field.', 'properties': {'override_immature': {'type': 'boolean'}}},
    'wait': {'type': 'object', 'additionalProperties': False,
             'description': 'Do nothing now and ask to be called again after "hours" (field may be null).',
             'properties': {'hours': {'type': 'number', 'minimum': 0, 'maximum': 10000}}},
}
ACTIONS = list(ACTION_PARAMS.keys())
SQUARE_ACTIONS_REQUIRED = {'amend', 'scout', 'spray'}
SQUARE_ACTIONS_OPTIONAL = {'soil_check', 'transplant', 'fertilise'}

DECISION_BASE = {
    'type': 'object', 'required': ['action', 'reason'], 'additionalProperties': False,
    'properties': {
        'field': {'type': ['string', 'null'], 'enum': list(CFG.FIELDS) + ['a', 'b', 'both', None]},
        'action': {'type': 'string', 'enum': ACTIONS},
        'params': {'type': 'object'},
        'squares': {'type': ['array', 'null'], 'maxItems': 72, 'items': {'type': ['string', 'array']}},
        'when': {'type': 'string', 'maxLength': 40},
        'reason': {'type': 'string', 'minLength': 1, 'maxLength': 600},
        'confidence': {'type': 'number', 'minimum': 0, 'maximum': 1},
    },
}
RESPONSE_BASE = {
    'type': 'object', 'required': ['decisions'], 'additionalProperties': False,
    'properties': {'decisions': {'type': 'array', 'maxItems': 40, 'items': {'type': 'object'}}, 'notes': {'type': 'string', 'maxLength': 4000}},
}


def response_schema():
    """The full JSON Schema (draft 2020-12) of a decision API response."""
    item = copy.deepcopy(DECISION_BASE)
    item['allOf'] = [{'if': {'properties': {'action': {'const': a}}, 'required': ['action']},
                      'then': {'properties': {'params': ACTION_PARAMS[a]}}} for a in ACTIONS]
    out = copy.deepcopy(RESPONSE_BASE)
    out['properties']['decisions']['items'] = item
    out['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
    out['title'] = 'Paddy API Farm decision response'
    return out


# ── a tiny JSON Schema subset validator ──
_TYPES = {'object': dict, 'array': list, 'string': str, 'boolean': bool, 'null': type(None)}


def _is_type(v, t):
    if t == 'number':
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    if t == 'integer':
        return (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and math.isfinite(v) and v.is_integer())
    return isinstance(v, _TYPES[t])


def check(v, sch, path='$'):
    errs = []
    ts = sch.get('type')
    if ts is not None:
        tl = ts if isinstance(ts, list) else [ts]
        if not any(_is_type(v, t) for t in tl):
            return [f'{path}: expected {" or ".join(tl)}, got {type(v).__name__}']
    if 'enum' in sch and v not in sch['enum']:
        errs.append(f'{path}: {v!r} is not one of {[x for x in sch["enum"] if x is not None]}')
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 'minimum' in sch and v < sch['minimum']:
            errs.append(f'{path}: {v} is below {sch["minimum"]}')
        if 'maximum' in sch and v > sch['maximum']:
            errs.append(f'{path}: {v} is above {sch["maximum"]}')
    if isinstance(v, str):
        if 'minLength' in sch and len(v.strip()) < sch['minLength']:
            errs.append(f'{path}: empty')
        if 'maxLength' in sch and len(v) > sch['maxLength']:
            errs.append(f'{path}: longer than {sch["maxLength"]} characters')
    if isinstance(v, list):
        if 'maxItems' in sch and len(v) > sch['maxItems']:
            errs.append(f'{path}: more than {sch["maxItems"]} items')
        if 'minItems' in sch and len(v) < sch['minItems']:
            errs.append(f'{path}: fewer than {sch["minItems"]} items')
        if 'items' in sch:
            for k, x in enumerate(v):
                errs += check(x, sch['items'], f'{path}[{k}]')
    if isinstance(v, dict):
        for k in sch.get('required', []):
            if k not in v:
                errs.append(f'{path}: missing "{k}"')
        props = sch.get('properties', {})
        if sch.get('additionalProperties') is False:
            for k in v:
                if k not in props:
                    errs.append(f'{path}: unknown key "{k}"')
        for k, s2 in props.items():
            if k in v:
                errs += check(v[k], s2, f'{path}.{k}')
    return errs


_SQ = re.compile(r'^\s*([AaBb])?\s*(\d+)\s*[,:x ]\s*(\d+)\s*$')


def parse_square(s, field=None):
    """'3,4', 'A3,4', [3, 4] → (row, col) 1-based; raises ValueError."""
    if isinstance(s, (list, tuple)) and len(s) == 2 and all(_is_type(x, 'integer') for x in s):
        r, c = int(s[0]), int(s[1])
    elif isinstance(s, str):
        m = _SQ.match(s)
        if not m:
            raise ValueError(f'square {s!r} is not "row,col"')
        if m.group(1) and field and m.group(1).upper() != field:
            raise ValueError(f'square {s!r} is not in field {field}')
        r, c = int(m.group(2)), int(m.group(3))
    else:
        raise ValueError(f'square {s!r} is not "row,col"')
    if not (1 <= r <= CFG.ROWS and 1 <= c <= CFG.COLS):
        raise ValueError(f'square {s!r} is outside the {CFG.ROWS} × {CFG.COLS} field')
    return r, c


def extract_json(text):
    """The JSON object in a model reply (tolerates code fences and text around it)."""
    if isinstance(text, dict):
        return text
    s = (text or '').strip()
    m = re.search(r'```(?:json)?\s*(\{.*\})\s*```', s, re.S)
    if m:
        s = m.group(1)
    else:
        a, b = s.find('{'), s.rfind('}')
        if a >= 0 and b > a:
            s = s[a:b + 1]
    return json.loads(s)


def validate(obj):
    """Check a parsed response. Returns (decisions, errors): decisions normalised (field 'A'/'B'/None, squares as
    1-based (row, col) tuples or None, params with defaults), errors as human-readable strings. A decision with an
    error is left out; the others are kept."""
    errors = check(obj, RESPONSE_BASE)
    if errors and not (isinstance(obj, dict) and isinstance(obj.get('decisions'), list)):
        return [], errors
    out = []
    for k, d in enumerate(obj.get('decisions') or []):
        path = f'$.decisions[{k}]'
        e = check(d, DECISION_BASE, path)
        if e:
            errors += e
            continue
        act = d['action']
        params = d.get('params') or {}
        e = check(params, ACTION_PARAMS[act], f'{path}.params')
        if e:
            errors += e
            continue
        field = d.get('field')
        field = None if field in (None, 'both') else str(field).upper()
        if act != 'wait' and field is None:
            errors.append(f'{path}: "{act}" needs a field (A or B)')
            continue
        squares = d.get('squares')
        sq = None
        if squares is not None:
            try:
                sq = list(dict.fromkeys(parse_square(s, field) for s in squares))    # each square once
            except ValueError as ex:
                errors.append(f'{path}.squares: {ex}')
                continue
        if act in SQUARE_ACTIONS_REQUIRED and not sq:
            errors.append(f'{path}: "{act}" needs a list of squares')
            continue
        if sq is not None and not sq:
            errors.append(f'{path}.squares: empty list (use null for the whole field)')
            continue
        p = dict(params)
        if act == 'fertilise':
            p.setdefault('split', 'other')
        if act == 'wait':
            p.setdefault('hours', 12)
        out.append({'field': field, 'action': act, 'params': p, 'squares': sq, 'when': (d.get('when') or 'now').strip(),
                    'reason': d['reason'].strip(), 'confidence': d.get('confidence')})
    return out, errors
