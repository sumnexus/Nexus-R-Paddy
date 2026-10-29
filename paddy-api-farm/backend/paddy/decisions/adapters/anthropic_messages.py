"""The Anthropic Messages wire format (POST {base}/v1/messages, key in x-api-key)."""
from __future__ import annotations


class AnthropicMessagesAdapter:
    name = 'anthropic_messages'
    VERSION = '2023-06-01'

    def url(self, base_url):
        b = base_url.rstrip('/')
        if b.endswith('/messages'):
            return b
        return b + ('/messages' if b.endswith('/v1') else '/v1/messages')

    def headers(self, key):
        h = {'Content-Type': 'application/json', 'anthropic-version': self.VERSION}
        if key:
            h['x-api-key'] = key
        return h

    def body(self, messages, model, temperature, json_mode, max_tokens=1500, state=None):
        system = '\n\n'.join(m['content'] for m in messages if m['role'] == 'system')
        rest = [m for m in messages if m['role'] != 'system']
        return {'model': model, 'system': system, 'messages': rest, 'temperature': temperature, 'max_tokens': max_tokens}

    def parse(self, obj):
        try:
            text = ''.join(p.get('text', '') for p in obj['content'] if p.get('type') == 'text')
        except (KeyError, TypeError) as ex:
            raise ValueError(f'unexpected messages reply ({type(ex).__name__})') from None
        u = obj.get('usage') or {}
        return text, u.get('input_tokens'), u.get('output_tokens'), obj.get('model', '')
