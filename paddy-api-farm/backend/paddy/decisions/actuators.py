"""Actuators: what carries a decision out.

``SimActuators`` applies decisions to the digital twin (this is what every run uses).

``HttpActuators`` is a stub for a real farm controller (pump relays, a drone or a spray robot). It is off by
default: it only describes what it would send. Wiring it to real hardware needs a controller that accepts the
JSON documented here, a safety review, and a person on site; nothing in this project does that for you.
"""
from __future__ import annotations

import json
import os
import urllib.request

from ..sim.twin import PEST_KEYS

FIELD_IDX = {'A': 0, 'B': 1}


class SimActuators:
    name = 'sim'

    def __init__(self, twin):
        self.tw = twin

    def execute(self, d):
        tw = self.tw
        act = d['action']
        p = d.get('params') or {}
        f = FIELD_IDX.get(d.get('field'))
        sq = d.get('squares')
        sq0 = [(r - 1, c - 1) for r, c in sq] if sq is not None else None
        if act == 'hold':
            return {'ok': True, 'msg': 'no change'}
        if act == 'wait':
            return {'ok': True, 'msg': f'call again in {p.get("hours", 12):g} h'}
        if act == 'soil_check':
            r = tw.act_soil_check(f, sq0)
        elif act == 'amend':
            r = tw.act_amend(f, sq0, p['product'], float(p['dose']))
        elif act == 'puddle':
            r = tw.act_puddle(f)
        elif act == 'transplant':
            r = tw.act_transplant(f, sq0)
        elif act == 'irrigate':
            r = tw.act_irrigate(f, float(p['target_depth_mm']))
        elif act == 'drain':
            r = tw.act_drain(f, float(p['to_depth_mm']))
        elif act == 'fertilise':
            r = tw.act_fertilise(f, sq0, p['product'], float(p['kg_per_ha']))
        elif act == 'scout':
            r = tw.act_scout(f, sq0)
        elif act == 'spray':
            r = tw.act_spray(f, sq0, p['product'], p.get('target_pest'))
        elif act == 'harvest':
            r = tw.act_harvest(f)
        else:
            return {'ok': False, 'msg': f'unknown action {act}'}
        out = {'ok': bool(r.get('ok', True)), 'msg': r.get('msg', '')}
        for k in ('squares', 'kg', 'lostNKgHa', 'found'):
            if r.get(k) is not None:
                out[k] = r[k]
        if r.get('kills') is not None:
            out['kills'] = [(int(i), float(e)) for i, e in r['kills']]
        if r.get('looks') is not None:
            out['looks'] = r['looks']
            out['effs'] = r['effs']
            out['conditions'] = dict(r['conditions'])
        return out


class HttpActuators:
    """Stub: POST each decision to ``{FARM_CONTROLLER_URL}/actions`` as JSON. Disabled unless both
    FARM_CONTROLLER_URL is set and ``enabled=True`` is passed explicitly."""
    name = 'http'

    def __init__(self, base_url=None, enabled=False, timeout_s=10):
        self.base_url = (base_url or os.environ.get('FARM_CONTROLLER_URL') or '').rstrip('/')
        self.enabled = bool(enabled and self.base_url)
        self.timeout_s = timeout_s

    def payload(self, d):
        return {'field': d.get('field'), 'action': d['action'], 'params': d.get('params') or {},
                'squares': [f'{r},{c}' for r, c in d['squares']] if d.get('squares') else None, 'reason': d.get('reason')}

    def execute(self, d):
        body = self.payload(d)
        if not self.enabled:
            return {'ok': False, 'msg': 'HttpActuators is a stub and is disabled; would POST ' + json.dumps(body)[:300]}
        req = urllib.request.Request(self.base_url + '/actions', data=json.dumps(body).encode(), method='POST',
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return {'ok': 200 <= resp.status < 300, 'msg': resp.read(2000).decode('utf-8', 'replace')}
        except Exception as ex:  # noqa: BLE001
            return {'ok': False, 'msg': f'controller error: {type(ex).__name__}'}


__all__ = ['SimActuators', 'HttpActuators', 'PEST_KEYS']
