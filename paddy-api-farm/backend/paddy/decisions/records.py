"""Decision and call records (the audit trail) and their storage.

A call record keeps the state that was sent, the raw reply, how it was validated, latency and token counts.
A decision record keeps one decision from start to finish: proposed → guardrail verdict → approved or not →
executed (or skipped) → outcome. Nothing written here may contain the API key: every string goes through
``redact`` before it is stored or exported.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re

_SECRET_HINT = re.compile(r'(?i)(authorization|api[-_]?key|bearer)\s*[:=]\s*\S+')


def _secrets():
    out = []
    for k in ('API_KEY',):
        v = os.environ.get(k)
        if v and len(v) >= 6:
            out.append(v)
    return out


def redact(obj):
    """Return a copy of obj with the API key (and anything that looks like an auth header) removed."""
    secrets = _secrets()

    def fix(s):
        for v in secrets:
            if v in s:
                s = s.replace(v, '[redacted]')
        return _SECRET_HINT.sub(lambda m: m.group(1) + ': [redacted]', s)

    def walk(x):
        if isinstance(x, str):
            return fix(x)
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items() if str(k).lower() not in ('authorization', 'api_key', 'apikey', 'x-api-key')}
        if isinstance(x, (list, tuple)):
            return [walk(v) for v in x]
        return x
    return walk(obj)


class RunStore:
    """In-memory store of one run, optionally mirrored to JSONL files under ``directory``."""

    def __init__(self, run_id, directory=None, keep_states=True):
        self.run_id = run_id
        self.dir = directory
        self.keep_states = keep_states
        self.calls = []
        self.decisions = []
        self.events = []
        self.by_id = {}
        if directory:
            os.makedirs(directory, exist_ok=True)

    def _append(self, name, obj):
        if not self.dir:
            return
        with open(os.path.join(self.dir, name), 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(redact(obj), ensure_ascii=False) + '\n')

    def add_call(self, rec):
        if not self.keep_states:
            rec = dict(rec)
            rec.pop('state', None)
        rec = redact(rec)
        self.calls.append(rec)
        self._append('calls.jsonl', rec)
        return rec

    def add_decision(self, rec):
        self.decisions.append(rec)
        self.by_id[rec['id']] = rec
        return rec

    def flush_decision(self, rec):
        self._append('decisions.jsonl', redact(rec))

    def add_event(self, ev):
        ev = redact(ev)
        self.events.append(ev)
        self._append('events.jsonl', ev)

    def write_json(self, name, obj):
        if not self.dir:
            return
        with open(os.path.join(self.dir, name), 'w', encoding='utf-8') as fh:
            json.dump(redact(obj), fh, ensure_ascii=False, indent=1)


DECISION_COLUMNS = ['id', 'call_id', 'created', 'source', 'field', 'action', 'params', 'squares', 'when', 'exec_at', 'status',
                    'verdict', 'guardrail_messages', 'executed_at', 'execution', 'outcome', 'reason', 'confidence']


def decisions_csv(decisions):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(DECISION_COLUMNS)
    for d in decisions:
        g = d.get('guardrail') or {}
        ex = d.get('execution') or {}
        w.writerow([d.get('id'), d.get('call_id'), d.get('created'), d.get('source'), d.get('field'), d.get('action'),
                    json.dumps(d.get('params') or {}, ensure_ascii=False), ' '.join(d.get('squares') or []) if d.get('squares') else 'all',
                    d.get('when'), d.get('exec_at'), d.get('status'), g.get('verdict'), ' | '.join(g.get('messages') or []),
                    ex.get('at'), ex.get('msg'), json.dumps(d.get('outcome'), ensure_ascii=False) if d.get('outcome') else '',
                    d.get('reason'), d.get('confidence')])
    return redact(buf.getvalue())
