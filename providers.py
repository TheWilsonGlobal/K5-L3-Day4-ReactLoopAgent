"""Bốn provider cùng một interface. Không chứa agent loop."""
import json
import os
import time
import urllib.error
import urllib.request

CONFIG = {
    'openai': ('OPENAI_API_KEY', 'https://api.openai.com/v1/chat/completions'),
    'gemini': ('GEMINI_API_KEY', 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'),
    'openrouter': ('OPENROUTER_API_KEY', 'https://openrouter.ai/api/v1/chat/completions'),
    'anthropic': ('ANTHROPIC_API_KEY', 'https://api.anthropic.com/v1/messages'),
}


RATE_LIMIT_RETRIES = 8  # Groq giới hạn 8.000 token/phút; một request lớn có thể cần vài lần chờ
MAX_RETRY_WAIT = 120  # giây


def _retry_wait(exc, attempt):
    """Chờ theo retry-after nếu có, nếu không dùng backoff tăng dần."""
    raw = exc.headers.get('retry-after') if getattr(exc, 'headers', None) else None
    try:
        wait = float(raw)
    except (TypeError, ValueError):
        wait = 15.0 * attempt
    return min(max(wait, 1.0), MAX_RETRY_WAIT)


def post_json(url, headers, payload):
    body = json.dumps(payload).encode()
    # Một số endpoint (ví dụ Groq qua Cloudflare) chặn User-Agent mặc định của urllib bằng HTTP 403.
    base_headers = {'Content-Type': 'application/json', 'User-Agent': 'StatusAgentLab/1.0 (educational classroom)', **headers}
    for attempt in range(1, RATE_LIMIT_RETRIES + 2):
        req = urllib.request.Request(url, data=body, headers=base_headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # 429: giới hạn tốc độ tạm thời; chờ rồi thử lại trong giới hạn số lần.
            if exc.code == 429 and attempt <= RATE_LIMIT_RETRIES:
                time.sleep(_retry_wait(exc, attempt))
                continue
            # Do not print response bodies: they may contain account or request data.
            raise RuntimeError(f'Provider trả HTTP {exc.code}; kiểm tra model, key và quota.') from exc
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            # Lỗi mạng tạm thời (ví dụ connection reset): thử lại trong giới hạn số lần.
            if attempt <= RATE_LIMIT_RETRIES:
                time.sleep(_retry_wait(None, attempt))
                continue
            reason = getattr(exc, 'reason', exc)
            raise RuntimeError(f'Không kết nối được provider: {reason}') from exc


class Provider:
    """complete(messages, schemas) -> {message, calls, finish, usage}.

    messages: system/user/assistant/tool theo format Chat Completions. message trả về
    cần được append nguyên vẹn; tool result có role, tool_call_id, content (JSON string).
    """
    def __init__(self, name, model):
        if name not in CONFIG:
            raise ValueError(f'Provider không hợp lệ: {name}')
        if not model:
            raise ValueError('Cần đặt MODEL là model có hỗ trợ tool calling.')
        self.name, self.model = name, model
        key_name, self.url = CONFIG[name]
        self.key = os.getenv(key_name)
        if not self.key:
            raise ValueError(f'Thiếu {key_name}; xem README.md.')
        # OPENAI_BASE_URL cho phép trỏ provider openai tới endpoint tương thích (ví dụ Groq).
        base_url = os.getenv('OPENAI_BASE_URL') if name == 'openai' else None
        if base_url:
            self.url = base_url.rstrip('/') + '/chat/completions'

    def complete(self, messages, schemas):
        if self.name == 'anthropic':
            return self._anthropic(messages, schemas)
        payload = {'model': self.model, 'messages': messages}
        if schemas:
            payload['tools'] = schemas
        data = post_json(self.url, {'Authorization': f'Bearer {self.key}'}, payload)
        choice = data['choices'][0]
        message = choice['message']
        calls = [{'id': c['id'], 'name': c['function']['name'], 'arguments': c['function']['arguments']} for c in message.get('tool_calls', [])]
        return {'message': message, 'calls': calls, 'finish': choice.get('finish_reason'), 'usage': data.get('usage', {})}

    def _anthropic(self, messages, schemas):
        system = '\n'.join(m['content'] for m in messages if m['role'] == 'system')
        native = []
        for m in messages:
            if m['role'] == 'system':
                continue
            if m['role'] == 'assistant':
                blocks = []
                if m.get('content'):
                    blocks.append({'type': 'text', 'text': m['content']})
                for call in m.get('tool_calls', []):
                    try:
                        call_input = json.loads(call['function']['arguments'])
                    except (json.JSONDecodeError, TypeError):
                        # Arguments lỗi đã được trả INVALID_ARGUMENTS ở tool result; không để adapter crash.
                        call_input = {}
                    if not isinstance(call_input, dict):
                        call_input = {}
                    blocks.append({'type': 'tool_use', 'id': call['id'], 'name': call['function']['name'], 'input': call_input})
                native.append({'role': 'assistant', 'content': blocks})
            elif m['role'] == 'tool':
                block = {'type': 'tool_result', 'tool_use_id': m['tool_call_id'], 'content': m['content']}
                if native and native[-1]['role'] == 'user' and isinstance(native[-1]['content'], list):
                    native[-1]['content'].append(block)
                else:
                    native.append({'role': 'user', 'content': [block]})
            else:
                native.append({'role': 'user', 'content': m['content']})
        tools = [{'name': x['function']['name'], 'description': x['function']['description'], 'input_schema': x['function']['parameters']} for x in schemas]
        payload = {'model': self.model, 'max_tokens': 1024, 'system': system, 'messages': native}
        if tools:
            payload['tools'] = tools
        data = post_json(self.url, {'x-api-key': self.key, 'anthropic-version': '2023-06-01'}, payload)
        blocks = data['content']
        calls = [{'id': b['id'], 'name': b['name'], 'arguments': json.dumps(b['input'], ensure_ascii=False)} for b in blocks if b['type'] == 'tool_use']
        text = '\n'.join(b['text'] for b in blocks if b['type'] == 'text')
        message = {'role': 'assistant', 'content': text or None}
        if calls:
            message['tool_calls'] = [{'id': c['id'], 'type': 'function', 'function': {'name': c['name'], 'arguments': c['arguments']}} for c in calls]
        reason = {'end_turn': 'stop', 'tool_use': 'tool_calls', 'max_tokens': 'length'}.get(data.get('stop_reason'), data.get('stop_reason'))
        return {'message': message, 'calls': calls, 'finish': reason, 'usage': data.get('usage', {})}
