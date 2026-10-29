"""The OpenAPI 3.1 description of the REST API (served at /api/openapi.json)."""
from __future__ import annotations

from . import __version__
from . import config as CFG
from .decisions.schema import response_schema

FIELD = {'name': 'name', 'in': 'path', 'required': True, 'schema': {'type': 'string', 'enum': list(CFG.FIELDS)}}
DID = {'name': 'id', 'in': 'path', 'required': True, 'schema': {'type': 'string', 'pattern': '^d[0-9]+$'}, 'example': 'd00012'}
RID = {'name': 'id', 'in': 'path', 'required': True, 'schema': {'type': 'string'}}


def _ok(desc, ref=None, content='application/json'):
    c = {content: {'schema': {'$ref': f'#/components/schemas/{ref}'}}} if ref else {content: {'schema': {'type': 'object'}}}
    return {'200': {'description': desc, 'content': c}, '4XX': {'$ref': '#/components/responses/Error'}}


def _body(props, required=()):
    return {'required': bool(required), 'content': {'application/json': {'schema': {'type': 'object', 'properties': props, 'required': list(required)}}}}


def openapi():
    status_schema = {'type': 'object', 'properties': {
        'status': {'type': 'string', 'enum': ['running', 'paused', 'awaiting_approval', 'finished']}, 'run_id': {'type': 'string'},
        'time': {'type': 'string', 'example': '2026-06-01T06:00'}, 'day': {'type': 'integer'}, 'season': {'type': 'string', 'enum': list(CFG.SEASONS)},
        'seed': {'type': 'integer'}, 'weatherYear': {'type': 'integer'}, 'manager': {'type': 'string', 'enum': ['builtin', 'conventional', 'api', 'api-no-advice']},
        'mode': {'type': 'string', 'enum': ['auto', 'approve']}, 'speed': {'type': 'number'}, 'pending_approvals': {'type': 'integer'},
        'scheduled': {'type': 'integer'}, 'calls': {'type': 'integer'}}}
    decision = {'type': 'object', 'properties': {
        'id': {'type': 'string'}, 'call_id': {'type': 'string'}, 'created': {'type': 'string'}, 'source': {'type': 'string',
                                                                                                         'description': 'api | mock | builtin | conventional | fallback-hold | fallback-builtin'},
        'field': {'type': ['string', 'null']}, 'action': {'type': 'string'}, 'params': {'type': 'object'}, 'squares': {'type': ['array', 'null'], 'items': {'type': 'string'}},
        'when': {'type': 'string'}, 'exec_at': {'type': 'string'}, 'reason': {'type': 'string'}, 'confidence': {'type': ['number', 'null']},
        'guardrail': {'type': 'object', 'properties': {'verdict': {'type': 'string', 'enum': ['accepted', 'warning', 'rejected']},
                                                        'messages': {'type': 'array', 'items': {'type': 'string'}}}},
        'status': {'type': 'string', 'enum': ['proposed', 'rejected', 'pending_approval', 'rejected_by_person', 'scheduled', 'executed', 'failed', 'skipped', 'expired']},
        'execution': {'type': ['object', 'null']}, 'outcome': {'type': ['object', 'null']}, 'planned_kill': {'type': ['number', 'null']}}}
    paths = {
        '/api/health': {'get': {'summary': 'Server and simulation status', 'responses': _ok('status', 'Status')}},
        '/api/config': {'get': {'summary': 'Farm layout, managers, limits, actions, the label list and the decision API settings (never the key)',
                                'responses': _ok('configuration')}},
        '/api/farm': {'get': {'summary': 'Both fields at a glance, the weather and the last events', 'responses': _ok('farm')}},
        '/api/fields/{name}': {'get': {'summary': 'One field square by square (water depth, stage, soil, pests, advice)', 'parameters': [FIELD],
                                       'responses': _ok('field')}},
        '/api/fields/{name}/squares/{rc}': {'get': {'summary': "One square's history at every 06:00 and 18:00 (water over it, progress, health, pest, risk)",
                                                  'parameters': [FIELD, {'name': 'rc', 'in': 'path', 'required': True, 'schema': {'type': 'string', 'pattern': '^[0-9]+,[0-9]+$'},
                                                                         'example': '3,4'}],
                                                  'responses': _ok('square history')}},
        '/api/weather': {'get': {'summary': 'Station reading, 3-day forecast, daily rain so far, events', 'responses': _ok('weather')}},
        '/api/arms': {'get': {'summary': 'The four arms: where each is, what it is doing, its jobs and when it should be done, traffic and '
                                         'collision totals, and the rate arm work is slowed to so it can be watched', 'responses': _ok('arms')}},
        '/api/decisions': {'get': {'summary': 'Decision records, newest last', 'parameters': [
            {'name': 'field', 'in': 'query', 'schema': {'type': 'string', 'enum': list(CFG.FIELDS)}},
            {'name': 'status', 'in': 'query', 'schema': {'type': 'string'}},
            {'name': 'action', 'in': 'query', 'schema': {'type': 'string'}},
            {'name': 'since', 'in': 'query', 'schema': {'type': 'string'}, 'description': 'a decision id (newer than) or a simulated time "YYYY-MM-DDTHH:MM"'},
            {'name': 'limit', 'in': 'query', 'schema': {'type': 'integer', 'default': 500}}],
            'responses': _ok('decisions')}},
        '/api/decisions/{id}': {'get': {'summary': 'One decision with the call that produced it (state sent, raw reply, validation)', 'parameters': [DID],
                                        'responses': _ok('decision and call')}},
        '/api/decisions/{id}/approve': {'post': {'summary': 'Approve a decision waiting for approval (approve mode)', 'parameters': [DID],
                                                 'responses': _ok('approved')}},
        '/api/decisions/{id}/reject': {'post': {'summary': 'Reject a decision waiting for approval; the reason goes back to the decision maker',
                                                'parameters': [DID], 'requestBody': _body({'reason': {'type': 'string', 'maxLength': 300}}),
                                                'responses': _ok('rejected')}},
        '/api/sim/start': {'post': {'summary': 'Run the clock', 'responses': _ok('status', 'Status')}},
        '/api/sim/pause': {'post': {'summary': 'Stop the clock', 'responses': _ok('status', 'Status')}},
        '/api/sim/step': {'post': {'summary': 'Advance a number of simulated hours now', 'requestBody': _body({'hours': {'type': 'integer', 'minimum': 1, 'maximum': 1440}}),
                                   'responses': _ok('status', 'Status')}},
        '/api/sim/reset': {'post': {'summary': 'Start a new season', 'requestBody': _body({
            'seed': {'type': 'integer'}, 'weatherYear': {'type': 'integer'}, 'season': {'type': 'string', 'enum': list(CFG.SEASONS)},
            'manager': {'type': 'string', 'enum': ['builtin', 'conventional', 'api', 'api-no-advice']}, 'mode': {'type': 'string', 'enum': ['auto', 'approve']}}),
            'responses': _ok('status', 'Status')}},
        '/api/sim/speed': {'post': {'summary': 'Simulated hours per real second; and (v2) work: simulated seconds per real second while an arm works, '
                                               'so the live 3D view can show it (default 30; 3600 is as good as no slow-down)',
                                    'requestBody': _body({'x': {'type': 'number', 'minimum': 0.1, 'maximum': 5000},
                                                          'work': {'type': 'number', 'minimum': 1, 'maximum': 3600}}, ['x']),
                                    'responses': _ok('status', 'Status')}},
        '/api/events': {'post': {'summary': 'Inject an event now, or schedule it for a later day of the season', 'requestBody': _body({
            'kind': {'type': 'string', 'enum': ['pest', 'dry', 'storm', 'cloudy']}, 'day': {'type': 'integer'},
            'field': {'type': 'string', 'enum': list(CFG.FIELDS)}, 'pest': {'type': 'string', 'enum': ['yellow_stem_borer', 'brown_planthopper', 'leaf_folder']}}, ['kind']),
            'responses': _ok('event')}},
        '/api/observations': {'post': {'summary': "Readings from outside the twin (real-farm mode): a pest seen on a square, a water reading (replaces the "
                                                  "tube for 6 h), a soil reading for a square (replaces the probe), or a note for the decision maker",
                                       'requestBody': _body({'kind': {'type': 'string', 'enum': ['pest', 'water', 'soil', 'note']}, 'field': {'type': 'string'},
                                                             'ph': {'type': 'number'}, 'ec': {'type': 'number'}, 'n': {'type': 'number'}, 'p': {'type': 'number'},
                                                             'k': {'type': 'number'}, 'oc': {'type': 'number'},
                                                             'square': {'type': 'string', 'example': '3,4'}, 'pest': {'type': 'string'},
                                                             'severity': {'type': 'number', 'minimum': 0, 'maximum': 1}, 'depth_mm': {'type': 'number'},
                                                             'text': {'type': 'string', 'maxLength': 300}}, ['kind']),
                                       'responses': _ok('observation')}},
        '/api/compare': {
            'get': {'summary': 'The last comparison run: progress, then means and paired differences', 'responses': _ok('comparison')},
            'post': {'summary': "Run the current season's case (and the following ones) under several managers in the background",
                     'requestBody': _body({'managers': {'type': 'array', 'items': {'type': 'string', 'enum': ['builtin', 'conventional', 'api', 'api-no-advice']}},
                                           'seasons': {'type': 'integer', 'minimum': 1, 'maximum': 20},
                                           'confirm_real_api': {'type': 'boolean', 'description': 'required when the real (paid) API would be called'}}),
                     'responses': _ok('comparison started')}},
        '/api/models': {'get': {'summary': "The forests and this run's checks of their advice", 'responses': _ok('models')}},
        '/api/audit': {'get': {'summary': 'Checks of the advice against what happened (fixed definitions), with samples', 'responses': _ok('audit')}},
        '/api/runs': {'get': {'summary': 'Runs kept in memory (the current one and up to 12 earlier ones)', 'responses': _ok('runs')}},
        '/api/runs/{id}/export': {'get': {'summary': 'Download a run: everything as JSON, or the decision table as CSV', 'parameters': [
            RID, {'name': 'format', 'in': 'query', 'schema': {'type': 'string', 'enum': ['json', 'csv'], 'default': 'json'}}],
            'responses': {'200': {'description': 'the export', 'content': {'application/json': {}, 'text/csv': {}}}}}},
        '/api/stream': {'get': {'summary': 'Server-sent events: {"type": "status" | "tick" | "call" | "decision" | "event" | "finished", "data": {...}}',
                                'responses': {'200': {'description': 'text/event-stream', 'content': {'text/event-stream': {}}}}}},
        '/api/openapi.json': {'get': {'summary': 'This document', 'responses': _ok('OpenAPI')}},
    }
    return {
        'openapi': '3.1.0',
        'info': {'title': 'Paddy API Farm', 'version': __version__,
                 'description': 'Two 6 × 6 paddy fields simulated hour by hour; decisions come from an external decision API (or the built-in '
                                'manager), are checked by guardrails and carried out on the digital twin. Times are simulated local times.'},
        'servers': [{'url': '/'}],
        'paths': paths,
        'components': {
            'schemas': {'Status': status_schema, 'Decision': decision, 'DecisionAPIReply': response_schema()},
            'responses': {'Error': {'description': 'error', 'content': {'application/json': {'schema': {'type': 'object', 'properties': {'error': {'type': 'string'}}}}}}},
        },
    }
