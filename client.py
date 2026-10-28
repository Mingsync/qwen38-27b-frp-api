#!/usr/bin/env python3
"""pip install openai; credentials are read from environment, never hardcoded."""
import os
from openai import OpenAI

client = OpenAI(base_url=os.environ['OPENAI_BASE_URL'],
                api_key=os.environ['OPENAI_API_KEY'], timeout=300.0)
stream = client.chat.completions.create(
    model=os.getenv('QWEN_MODEL', 'qwen3.8-27b'),
    messages=[{'role': 'user', 'content': '请用三点说明本地大模型的用途。'}],
    max_tokens=512, temperature=0.7, top_p=0.8, presence_penalty=1.5,
    stream=True, extra_body={
        'top_k': 20, 'chat_template_kwargs': {'enable_thinking': False}},
)
for chunk in stream:
    if chunk.choices:
        delta = chunk.choices[0].delta
        if delta.content:
            print(delta.content, end='', flush=True)
print()
