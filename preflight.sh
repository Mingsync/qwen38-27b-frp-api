#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
printf '%s\n' 'Host GPU inventory'
nvidia-smi --query-gpu=index,name,driver_version,memory.total,memory.free --format=csv
nvidia-smi topo -m
docker compose version
docker compose config --quiet
printf '%s\n' 'Container CUDA execution check'
docker compose run --rm --no-deps --entrypoint python3 qwen -c '
import importlib.metadata as m
import torch
print({p:m.version(p) for p in ("vllm", "torch", "transformers")})
print("torch CUDA:", torch.version.cuda, "compiled arch list:", torch.cuda.get_arch_list())
assert torch.cuda.is_available(), "CUDA unavailable inside container"
assert torch.cuda.device_count() == 2, "Expected exactly two visible GPUs"
for i in range(2):
    print(i, torch.cuda.get_device_name(i), torch.cuda.get_device_capability(i))
    assert torch.cuda.get_device_capability(i) == (12, 0), "Expected RTX 5090 sm_120"
    a=torch.randn(512,512,device=f"cuda:{i}",dtype=torch.bfloat16)
    b=a@a
    torch.cuda.synchronize(i)
    assert torch.isfinite(b).all().item()
print("CUDA BF16 execution passed on both cards; model/FP8/NCCL validation is still required.")
'
printf '%s\n' 'Installed CLI parameters'
docker compose run --rm --no-deps --entrypoint vllm qwen serve --help=all > /tmp/qwen38-vllm-help.txt
for flag in --language-model-only --middleware --reasoning-parser --no-enable-prefix-caching \
  --override-generation-config --max-num-queued-reqs --default-chat-template-kwargs --sse-keep-alive-interval; do
  if ! grep -q -- "$flag" /tmp/qwen38-vllm-help.txt; then
    printf 'Missing required CLI flag: %s\n' "$flag" >&2
    exit 1
  fi
done
printf '%s\n' 'Preflight passed. It is now safe to attempt model startup.'
