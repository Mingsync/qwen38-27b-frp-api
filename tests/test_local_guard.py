"""Behavior checks for the externally reachable authentication boundary."""
import os
import unittest
from unittest.mock import patch

from local_guard import LocalApiGuard


class LocalApiGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.key = 'test-only-key-for-local-guard-checks'
        self.messages = []
        self.app_called = False

        async def app(scope, receive, send):
            self.app_called = True
            if scope['type'] == 'lifespan':
                return
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'data: first\n\n', 'more_body': True})
            await send({'type': 'http.response.body', 'body': b'data: [DONE]\n\n'})

        with patch.dict(os.environ, VLLM_API_KEY=self.key):
            self.guard = LocalApiGuard(app)

    async def call(self, path, method='GET', headers=None, kind='http'):
        self.messages = []
        self.app_called = False

        async def send(message):
            self.messages.append(message)

        async def receive():
            self.fail('Guard must not read or buffer the request body')

        await self.guard({'type': kind, 'method': method, 'path': path,
                          'headers': headers or []}, receive, send)
        return self.messages

    def authorization(self):
        return [(b'authorization', ('Bearer ' + self.key).encode())]

    async def test_missing_and_wrong_keys_cannot_reach_backend(self):
        for path, method in [('/v1/models', 'GET'), ('/v1/chat/completions', 'POST'), ('/metrics', 'GET')]:
            for headers in [[], [(b'authorization', b'Bearer wrong')]]:
                with self.subTest(path=path, headers=bool(headers)):
                    result = await self.call(path, method, headers)
                    self.assertEqual(result[0]['status'], 401)
                    self.assertFalse(self.app_called)

    async def test_duplicate_authorization_is_rejected(self):
        result = await self.call('/v1/models', headers=self.authorization() * 2)
        self.assertEqual(result[0]['status'], 401)
        self.assertFalse(self.app_called)

    async def test_valid_key_keeps_stream_chunks_intact(self):
        result = await self.call('/v1/chat/completions', 'POST', self.authorization())
        self.assertTrue(self.app_called)
        self.assertEqual(result[0]['status'], 200)
        self.assertEqual(result[1]['body'], b'data: first\n\n')
        self.assertTrue(result[1]['more_body'])
        self.assertEqual(result[2]['body'], b'data: [DONE]\n\n')

    async def test_alternate_inference_and_admin_routes_are_blocked(self):
        for path in ['/invocations', '/pause', '/collective_rpc', '/v1/responses', '/docs']:
            for headers in [[], self.authorization()]:
                with self.subTest(path=path, authorized=bool(headers)):
                    result = await self.call(path, 'POST', headers)
                    self.assertEqual(result[0]['status'], 404)
                    self.assertFalse(self.app_called)

    async def test_health_probe_does_not_need_a_key(self):
        result = await self.call('/health')
        self.assertEqual(result[0]['status'], 200)

    async def test_wrong_http_method_is_blocked(self):
        result = await self.call('/v1/chat/completions', 'GET', self.authorization())
        self.assertEqual(result[0]['status'], 404)

    async def test_lifespan_and_websocket_handling(self):
        await self.call('/', kind='lifespan')
        self.assertTrue(self.app_called)
        result = await self.call('/', kind='websocket')
        self.assertEqual(result, [{'type': 'websocket.close', 'code': 1008}])
        self.assertFalse(self.app_called)

    def test_invalid_configuration_fails_closed(self):
        for key in ['', 'short', 'contains whitespace in a long key', '非ASCII字符' * 8]:
            with self.subTest(key_type='invalid'), patch.dict(os.environ, VLLM_API_KEY=key):
                with self.assertRaises(RuntimeError):
                    LocalApiGuard(None)


if __name__ == '__main__':
    unittest.main()
