"""Wire formats for the external decision API. Each adapter turns (messages, settings) into an HTTP request and
pulls the reply text and token counts out of the HTTP response. Pick one with API_ADAPTER."""
from .anthropic_messages import AnthropicMessagesAdapter
from .chat_completions import ChatCompletionsAdapter
from .generic_json import GenericJSONAdapter

ADAPTERS = {
    'chat_completions': ChatCompletionsAdapter,
    'anthropic_messages': AnthropicMessagesAdapter,
    'generic_json': GenericJSONAdapter,
}

__all__ = ['ADAPTERS', 'ChatCompletionsAdapter', 'AnthropicMessagesAdapter', 'GenericJSONAdapter']
