#!/usr/bin/env python3
"""HTTP acceptance test, including missing/wrong keys and blocked bypass routes."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default=os.getenv('OPENAI_BASE_URL', 'http://127.0.0.1:8000/v1'))
    parser.add_argument('--model', default='qwen3.8-27b')
    parser.add_argument('--key-file', default=str(Path(__file__).parent / 'secrets/api_key'))
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    if not base.endswith('/v1'):
        parser.error('--base-url must end in /v1')
    key = os.getenv('OPENAI_API_KEY') or Path(args.key_file).read_text().strip()

    def request(url, token=None, body=None):
        headers = {'Content-Type': 'application/json'}
        if token is not None:
            headers['Authorization'] = 'Bearer ' + token
        req = urllib.request.Request(url, headers=headers,
            data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(req, timeout=300) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def check(condition, message):
        if not condition:
            raise RuntimeError(message)
        print('PASS:', message)

    status, data = request(base + '/models')
    check(status == 401, 'missing key returns 401')
    status, data = request(base + '/models', 'wrong-key')
    check(status == 401, 'wrong key returns 401')
    status, data = request(base + '/models', key)
    check(status == 200, 'valid key can list models')
    ids = [item['id'] for item in json.loads(data)['data']]
    check(args.model in ids, 'served model alias matches')
    payload = {'model': args.model, 'messages': [
        {'role': 'user', 'content': '请用一句中文介绍你自己。'}],
        'max_tokens': 128, 'temperature': 0.7, 'top_p': 0.8,
        'presence_penalty': 1.5, 'top_k': 20,
        'chat_template_kwargs': {'enable_thinking': False}, 'stream': False}
    status, _ = request(base + '/chat/completions', body=payload)
    check(status == 401, 'missing key cannot run inference')
    start = time.monotonic()
    status, data = request(base + '/chat/completions', key, payload)
    check(status == 200, 'authenticated text inference succeeds')
    response = json.loads(data)
    content = response['choices'][0]['message'].get('content')
    check(bool(content and content.strip()), 'non-thinking response contains an answer')
    print(content)
    print('elapsed_seconds:', round(time.monotonic() - start, 2), 'usage:', response.get('usage'))
    # Both direct service and Nginx prefix paths must block alternate entry points.
    root = base[:-3].rstrip('/')
    for route in ['/invocations', '/pause', '/collective_rpc']:
        status, _ = request(root + route, body={})
        check(status == 404, route + ' is not publicly enabled')
    print('HTTP acceptance passed. Run the documented SSE and load tests separately.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('FAIL:', str(exc), file=sys.stderr)
        sys.exit(1)
