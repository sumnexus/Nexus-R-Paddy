"""A plain JSON decision service: POST {base} with {"state", "prompt_version", "system", "schema"} and get the decision
object ({"decisions": [...]}) straight back — for a decision service of your own rather than a language model."""
from __future__ import annotations

import json


class GenericJSONAdapter:
    name = 'generic_json'

    def url(self, base_url):
        return base_url.rstrip('/')

    def headers(self, key):
        h = {'Content-Type': 'application/json'}
        if key:
            h['Authorization'] = 'Bearer ' + key
        return h

    def body(self, messages, model, temperature, json_mode, max_tokens=1500, state=None):
        from ..prompt import PROMPT_VERSION
        from ..schema import response_schema
        return {'state': state, 'model': model, 'prompt_version': PROMPT_VERSION, 'schema': response_schema(),
                'system': next((m['content'] for m in messages if m['role'] == 'system'), ''),
                'repair': [m['content'] for m in messages[2:]] or None}

    def parse(self, obj):
        if isinstance(obj, dict) and 'decisions' in obj:
            return json.dumps(obj), None, None, obj.get('model', '')
        if isinstance(obj, dict) and isinstance(obj.get('content'), str):
            return obj['content'], None, None, obj.get('model', '')
        raise ValueError('unexpected generic-json reply')
