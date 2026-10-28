#!/usr/bin/env bash
set -euo pipefail
# No set -x: secrets must not enter startup logs or command arguments.
export VLLM_API_KEY
VLLM_API_KEY="$(cat /run/secrets/qwen_api_key)"
python3 - <<'PY'
import os
k = os.environ.get('VLLM_API_KEY', '')
if len(k) < 24 or any(c.isspace() for c in k) or not k.isascii():
    raise SystemExit('API key must contain at least 24 ASCII characters and no whitespace')
PY
[[ -s /model/config.json ]] || { echo 'Missing /model/config.json' >&2; exit 1; }
args=(
  /model
  --served-model-name "${SERVED_MODEL_NAME:-qwen3.8-27b}"
  --host 0.0.0.0 --port 8000
  --tensor-parallel-size "${TENSOR_PARALLEL_SIZE:-2}"
  --distributed-executor-backend mp
  --dtype bfloat16
  --kv-cache-dtype auto
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION:-0.90}"
  --max-model-len "${MAX_MODEL_LEN:-16384}"
  --max-num-seqs "${MAX_NUM_SEQS:-2}"
  --max-num-batched-tokens "${MAX_NUM_BATCHED_TOKENS:-2048}"
  --enable-chunked-prefill
  --no-enable-prefix-caching
  --generation-config vllm
  --reasoning-parser qwen3
  --middleware local_guard.LocalApiGuard
  --disable-fastapi-docs
)
[[ "${EAGER:-1}" == 1 ]] && args+=(--enforce-eager)
[[ "${TEXT_ONLY:-1}" == 1 ]] && args+=(--language-model-only)
[[ "${CPU_OFFLOAD_GB:-0}" != 0 ]] && args+=(--cpu-offload-gb "$CPU_OFFLOAD_GB")
# FP8 is inferred from the downloaded checkpoint's quantization_config.
# --dtype bfloat16 controls unquantized modules; it does not expand FP8 weights.
exec vllm serve "${args[@]}"
