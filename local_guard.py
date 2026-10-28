"""Small pure ASGI allowlist for a text Chat Completions deployment.

Blocks vLLM management and alternate inference endpoints. Does not buffer SSE.
Configured key is still also enforced by vLLM on its protected API routes.
"""
import hmac
import json
import os


class LocalApiGuard:
    def __init__(self, app):
        self.app = app
        key = os.environ.get('VLLM_API_KEY', '')
        if len(key) < 24 or not key.isascii() or any(c.isspace() for c in key):
            raise RuntimeError('Invalid VLLM_API_KEY')
        self.expected = ('Bearer ' + key).encode('ascii')

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            return await self.app(scope, receive, send)
        if scope['type'] == 'websocket':
            return await send({'type': 'websocket.close', 'code': 1008})
        if scope['type'] != 'http':
            return
        method, path = scope['method'], scope.get('path', '')
        allowed = {
            ('GET', '/v1/models'),
            ('POST', '/v1/chat/completions'),
            ('GET', '/metrics'),
        }
        if (method, path) == ('GET', '/health'):
            return await self.app(scope, receive, send)
        if (method, path) not in allowed:
            return await self.reject(send, 404, 'Endpoint not enabled')
        auth = [v for k, v in scope.get('headers', []) if k.lower() == b'authorization']
        if len(auth) != 1 or not hmac.compare_digest(auth[0], self.expected):
            return await self.reject(send, 401, 'Invalid API key')
        return await self.app(scope, receive, send)

    @staticmethod
    async def reject(send, status, message):
        data = json.dumps({'error': {'message': message}}).encode()
        headers = [(b'content-type', b'application/json'),
                   (b'content-length', str(len(data)).encode())]
        if status == 401:
            headers.append((b'www-authenticate', b'Bearer'))
        await send({'type': 'http.response.start', 'status': status, 'headers': headers})
        await send({'type': 'http.response.body', 'body': data})
