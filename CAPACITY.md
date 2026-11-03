# 上下文与并发容量实测

本文记录目标双 RTX 5090 服务器的容量测试与复现步骤。测试使用固定 revision 的官方 FP8 权重、TP2、BF16 KV、Eager、关闭 prefix caching 和思考，每卡物理显存 32607MiB。实际运行版本为 vLLM 0.32.0、PyTorch 2.13.0+cu130、Transformers 5.19.0。

## 统计口径

- 总上下文包含聊天模板、历史消息、当前输入和生成输出。模型固定配置的原生上限为 262144 tokens，长度按模型 tokenizer 统计。
- 发起并发为同时发送的 HTTP 请求数。`max_generation_overlap` 根据每条成功流的首个答案到完成时间计算重叠峰值，排队阶段不参与计算。
- `peak_running` 与 `peak_waiting` 来自 vLLM 指标采样。运行中可包含分块 prefill，运行峰值与答案生成重叠峰值分别报告。
- 合计输出 tokens/s 使用服务端 usage 总输出除以整轮耗时，包含输入处理、等待和生成。长输入测试的该指标受 prefill 明显影响。
- TTFT 为请求发起到首个非空答案片段的时间，忽略 SSE 保活和思考内容。每条流必须有非空答案并收到 `[DONE]`，才记为成功。
- 缓存占用、抢占计数和 GPU 显存/温度约每 0.5 秒采样一次。指标缺失记为 null，计数器重置时抢占增量记为 null。瞬时峰值可能发生在采样间隔内。
- 测试通过 `min_tokens=max_tokens`、`ignore_eos=true` 固定输出长度，观察持续生成时的资源压力。三个校验码分别放在合成材料的开头、中间和末尾，检查返回顺序。
- 合成重复材料与每档一轮突发用于识别容量边界。业务长文精度、持续负载和用户体验需要用实际任务另行验收。登录人数与同一时刻的生成请求数分别规划。

## 在同类服务器复现

先确认服务已健康、没有其他业务负载，并备份 `.env`。以下命令在 Linux GPU 服务器仓库目录执行，示例 IP 应替换为服务器实际地址。生成 prompt 需要容器里的本地 tokenizer，压测客户端只使用 Python 标准库。

```bash
cd /opt/qwen38
RESULT_DIR="$HOME/qwen-capacity-$(date +%Y%m%d-%H%M%S)"
install -d -m 700 "$RESULT_DIR"
cp .env "$RESULT_DIR/baseline.env"
chmod 600 "$RESULT_DIR/baseline.env"

docker compose exec -T qwen python3 - \
  --contexts 512 1024 8192 16384 32768 65536 131072 262144 \
  < generate_capacity_prompts.py > "$RESULT_DIR/prompts.jsonl"

python3 capacity_benchmark.py \
  --base-url http://192.168.10.20:8000/v1 \
  --model qwen3.8-27b --key-file secrets/api_key \
  --prompts "$RESULT_DIR/prompts.jsonl" \
  --case 131072 --concurrency 2 --max-tokens 512 --force-length \
  --profile-env .env --deadline 1800 \
  --output "$RESULT_DIR/128k-c2.json"
```

`--case` 选择生成数据中的长度标签。大于 8192 的标签预留 1024 输出 tokens，小标签尽量填满该数值的输入预算，准确输入量见 JSON 的 `prompt_tokens_expected` 和 `prompt_tokens_observed`。选择服务可接受的输入与输出组合。生成 262144 标签的数据本身不修改运行服务的长度上限。

`--concurrency` 支持 1 至 256，`--requests` 默认为同值，可设为更大值执行多轮。超过服务的运行加等待上限可能返回 503。脚本保存失败状态码并以非零状态退出。校验码通过数单独记录，容量成功与答案内容分别验收。

采样客户端应在目标 GPU 宿主机执行，通过模型直连 URL 访问 `/metrics`，并能执行 `nvidia-smi`。使用 Nginx 业务 URL 或在其他机器执行时，需另行核对指标可见性与 GPU 指标来源。

每次只测试一组负载。提高 `.env` 中 `MAX_MODEL_LEN`、`MAX_NUM_SEQS` 或显存预算后，用 `docker compose config --quiet` 检查，再重新创建容器并等待 healthy。保留启动日志与原始 JSON，核对 OOM、抢占增量、排队和 `RestartCount`。失败时恢复备份并重新创建容器。
