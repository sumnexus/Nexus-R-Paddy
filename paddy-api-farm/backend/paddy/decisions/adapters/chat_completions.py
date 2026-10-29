"""The OpenAI-style chat-completions wire format (POST {base}/chat/completions), which most model APIs accept,
including many hosted open-weight models. The default adapter."""
from __future__ import annotations


class ChatCompletionsAdapter:
    name = 'chat_completions'

    def url(self, base_url):
        b = base_url.rstrip('/')
        return b if b.endswith('/chat/completions') else b + '/chat/completions'

    def headers(self, key):
        h = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if key:
            h['Authorization'] = 'Bearer ' + key
        return h

    def body(self, messages, model, temperature, json_mode, max_tokens=1500, state=None):
        b = {'model': model, 'messages': messages, 'temperature': temperature, 'max_tokens': max_tokens}
        if json_mode:
            b['response_format'] = {'type': 'json_object'}
        return b

    def parse(self, obj):
        """→ (reply text, tokens in, tokens out, model). Raises ValueError on an unexpected shape."""
        try:
            msg = obj['choices'][0]['message']
            text = msg.get('content')
            if isinstance(text, list):     # some servers return content parts
                text = ''.join(p.get('text', '') for p in text if isinstance(p, dict))
        except (KeyError, IndexError, TypeError) as ex:
            raise ValueError(f'unexpected chat-completions reply ({type(ex).__name__})') from None
        u = obj.get('usage') or {}
        return text or '', u.get('prompt_tokens'), u.get('completion_tokens'), obj.get('model', '')
