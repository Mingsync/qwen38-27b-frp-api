#!/usr/bin/env python3
"""Measure an actual API workload; no synthetic throughput promises."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import time
import urllib.request


def consume_sse(response, start, clock=time.perf_counter):
    first=None
    usage=None
    content_seen=False
    for line in response:
        # Ignore keep-alive comments and blank lines; they are not first tokens.
        if not line.startswith(b'data:'):
            continue
        payload=line[5:].strip()
        if payload==b'[DONE]':
            break
        event=json.loads(payload)
        if event.get('usage'):
            usage=event['usage']
        for choice in event.get('choices',[]):
            if choice.get('delta',{}).get('content'):
                content_seen=True
                if first is None:
                    first=clock()-start
    if not content_seen:
        raise ValueError('No answer content received')
    return {'ttft_seconds':first,'elapsed_seconds':clock()-start,
            'completion_tokens':None if usage is None else usage.get('completion_tokens'),
            'prompt_tokens':None if usage is None else usage.get('prompt_tokens')}


def percentile(values, fraction):
    values=sorted(values)
    position=(len(values)-1)*fraction
    lower=int(position)
    upper=min(lower+1,len(values)-1)
    return values[lower]+(values[upper]-values[lower])*(position-lower)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--base-url',default=os.getenv('OPENAI_BASE_URL','http://127.0.0.1:8000/v1'))
    parser.add_argument('--key-file',default=str(Path(__file__).parent/'secrets/api_key'))
    parser.add_argument('--model',default='qwen3.8-27b')
    parser.add_argument('--prompt-file',help='Use a representative prompt; prompt text is not printed')
    parser.add_argument('--concurrency',type=int,default=2)
    parser.add_argument('--requests',type=int,default=20)
    parser.add_argument('--max-tokens',type=int,default=256)
    parser.add_argument('--timeout',type=int,default=300)
    args=parser.parse_args()
    if not 1<=args.concurrency<=128 or min(args.requests,args.max_tokens,args.timeout)<1:
        parser.error('Invalid concurrency, request count, output budget or timeout')
    key=os.getenv('OPENAI_API_KEY') or Path(args.key_file).read_text().strip()
    prompt=Path(args.prompt_file).read_text() if args.prompt_file else '请用三点说明本地大模型的用途。'
    body=json.dumps({'model':args.model,'messages':[{'role':'user','content':prompt}],
        'max_tokens':args.max_tokens,'stream':True,'stream_options':{'include_usage':True},
        'chat_template_kwargs':{'enable_thinking':False}}).encode()
    def request(_):
        try:
            start=time.perf_counter()
            req=urllib.request.Request(args.base_url.rstrip('/')+'/chat/completions',data=body,
                headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
            with urllib.request.urlopen(req,timeout=args.timeout) as response:
                return consume_sse(response,start)
        except Exception as error:
            # Never print the request, prompt or secret in a benchmark failure.
            return {'error':type(error).__name__,'status':getattr(error,'code',None)}
    start=time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results=list(pool.map(request,range(args.requests)))
    wall=time.perf_counter()-start
    passed=[r for r in results if 'error' not in r]
    errors=[r for r in results if 'error' in r]
    summary={'concurrency':args.concurrency,'requests':args.requests,'successful':len(passed),
             'failed':len(errors),'wall_seconds':wall,'success_rate':len(passed)/args.requests,
             'errors':errors}
    if passed:
        for field in ['ttft_seconds','elapsed_seconds']:
            summary[field]={'p50':percentile([r[field] for r in passed],.5),
                            'p95':percentile([r[field] for r in passed],.95)}
        tokens=[r['completion_tokens'] for r in passed]
        summary['aggregate_output_tokens_per_second']=None if None in tokens else sum(tokens)/wall
        summary['prompt_tokens_observed']=sorted(set(r['prompt_tokens'] for r in passed if r['prompt_tokens'] is not None))
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    raise SystemExit(1 if errors else 0)


if __name__=='__main__':
    main()
