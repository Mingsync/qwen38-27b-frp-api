#!/usr/bin/env python3
"""Generate synthetic retrieval prompts inside the inference container.

Pipe stdout to a JSONL file on the host. Requires the local model tokenizer.
"""
import argparse
import json

FILLER = '材料记录：本地服务器提供内网模型服务，部署验收需要记录配置和运行结果。\n'
MARKERS = ['ALPHA7319', 'BETA4826', 'GAMMA9561']


def messages_for(repetitions):
    left = repetitions // 2
    text = '请阅读测试材料，最后按起始、中段、末尾顺序只输出三个校验码。\n起始校验码：ALPHA7319\n'
    text += FILLER * left + '中段校验码：BETA4826\n' + FILLER * (repetitions - left)
    text += '末尾校验码：GAMMA9561\n请按起始、中段、末尾的顺序输出三个校验码。'
    return [{'role': 'user', 'content': text}]


def main():
    from transformers import AutoTokenizer
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-dir', default='/model')
    parser.add_argument('--contexts', nargs='+', type=int,
                        default=[1024, 8192, 16384, 32768, 65536, 131072, 262144])
    parser.add_argument('--reserve-output', type=int, default=1024)
    args = parser.parse_args()
    if min(args.contexts) < 512 or args.reserve_output < 1:
        parser.error('Use contexts >=512 and a positive output reserve')
    tokenizer = AutoTokenizer.from_pretrained(args.model_dir, local_files_only=True,
                                             trust_remote_code=False)
    filler_tokens = tokenizer(FILLER, add_special_tokens=False,
                              return_tensors='pt')['input_ids'].numel()

    def count(messages):
        result = tokenizer.apply_chat_template(messages, tokenize=True,
            add_generation_prompt=True, enable_thinking=False, return_dict=True,
            return_tensors='pt')
        return result['input_ids'].shape[-1]

    overhead = count(messages_for(0))
    for context in args.contexts:
        target = context if context <= 8192 else context - args.reserve_output
        if target < overhead:
            parser.error('Output reserve leaves no room for the test prompt')
        low, high = 0, (target - overhead) // filler_tokens + 4
        while low < high:
            middle = (low + high + 1) // 2
            if count(messages_for(middle)) <= target:
                low = middle
            else:
                high = middle - 1
        messages = messages_for(low)
        actual = count(messages)
        if not target - 128 <= actual <= target:
            raise ValueError('Tokenizer boundaries prevented the requested prompt size')
        print(json.dumps({'context': context, 'target_prompt_tokens': target,
            'prompt_tokens': actual, 'messages': messages, 'expected_markers': MARKERS},
            ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
