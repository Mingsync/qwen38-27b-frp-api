# 上下文与并发容量实测

本文记录目标双 RTX 5090 服务器的容量测试与复现步骤。测试使用固定 revision 的官方 FP8 权重、TP2、BF16 KV、Eager、关闭 prefix caching 和思考，每卡物理显存 32607MiB。实际运行版本为 vLLM 0.32.0、PyTorch 2.13.0+cu130、Transformers 5.19.0。

## 当前常驻配置与复验

目标服务器已设置以下性能参数并重新创建容器。启动器同时启用 `qwen3_coder` 自动工具解析。`.env.example` 保留首次安装的 16K、2 序列、0.90 显存预算起点，完成对应机器验收后再采用容量升级参数。

```dotenv
MAX_MODEL_LEN=262144
MAX_NUM_SEQS=64
MAX_NUM_QUEUED_REQS=128
GPU_MEMORY_UTILIZATION=0.95
MAX_NUM_BATCHED_TOKENS=2048
MAX_OUTPUT_TOKENS=1024
EAGER=1
PREFIX_CACHING=0
ENABLE_THINKING=0
```

64 是调度活跃序列上限，128 是运行加等待请求总上限。长输入占用更多缓存，实际活跃数还受到缓存准入限制。首次把运行加等待上限推到 129 个突发请求时，128 个请求成功、1 个返回 503，随后服务继续 healthy，抢占增量为 0。

选择常驻 64 序列，是依据下文相同输入与输出预算的曲线，高于 64 路后的吞吐增益较小，等待明显增加。显存预算从 0.90 调为 0.95，由部署测试作出这一选择。最终配置加载日志的可用 KV 池为 15.42GiB。三组最终复验共 68/68 成功，三处校验码全部通过，抢占增量为 0，容器未自动重启。[最终配置及逐请求记录](capacity-results.json)

- 256K 满窗验证实际输入 261108 tokens、固定输出 1024，合计 262132，单请求成功。TTFT 为 156.94 秒，完成为 244.11 秒。
- 128K 三路验证每请求输入 130047 tokens、固定输出 512，3/3 成功，同时生成峰值 3。TTFT P95 为 202.39 秒，完成 P95 为 246.08 秒。
- 短输入 64 路验证每请求输入 498 tokens、固定输出 1024，64/64 成功，同时生成峰值 64。合计输出 928.29 tokens/s，TTFT P95 为 17.17 秒，完成 P95 为 70.55 秒。

最终复验采样显存最高 31568MiB，按报告物理容量计算余量约 1039MiB。测试期间也有其他内网客户端请求，`peak_running` 可高于本次测试的生成重叠数。吞吐使用本次测试的输出与整轮耗时，资源采样包含当时整台推理服务的负载。

另外验证超出 262144 的输入返回 400，正确/缺失/错误 Key 和替代路由验收通过。工具调用的非流式 auto、流式函数参数拼接、模拟工具执行结果回传三项均通过。整机重启恢复、持续业务负载与业务长文精度仍需后续验收。

需要复制该性能档位时，先将当前 `.env` 备份并设为 600 权限，将上述参数合并到现有 `.env`，保留机器实际地址、镜像与模型路径，再执行以下命令。回滚时恢复原备份并重新创建容器。

```bash
docker compose config --quiet
docker compose up -d --force-recreate --pull never qwen
docker compose ps
python3 verify.py --base-url http://192.168.10.20:8000/v1
```

## 2026-11-03 容量边界探测

探索配置为 `MAX_MODEL_LEN=262144`、`MAX_NUM_SEQS=256`、`MAX_NUM_QUEUED_REQS=512`、`GPU_MEMORY_UTILIZATION=0.95`、`MAX_NUM_BATCHED_TOKENS=2048`，每请求输出上限 1024。启动日志报告每卡权重约 13.99GiB、可用 KV 池 15.14GiB，按满长请求计算的并发估计为 1.87x。

以下各档均为固定输出的一轮突发，请求数等于发起并发。列出的同时生成数采用答案流重叠峰值。当前精度与配置下，增加输入长度会降低可同时生成的数量。[逐请求原始汇总](capacity-results.json)

- 261108 输入 tokens、128 输出 tokens，发起 2 个请求，2/2 成功，同时生成峰值 1。第二个请求排队，该轮 TTFT P95 为 345.97 秒、端到端 P95 为 353.14 秒。
- 130047 输入 tokens、512 输出 tokens，发起 4 个请求，4/4 成功，同时生成峰值 3。TTFT P95 为 283.26 秒、端到端 P95 为 315.14 秒。
- 64506 输入 tokens、512 输出 tokens，发起 8 个请求，8/8 成功，同时生成峰值 7。TTFT P95 为 250.23 秒、端到端 P95 为 282.90 秒。
- 31725 输入 tokens、512 输出 tokens，发起 16 个请求，16/16 成功，同时生成峰值 14。TTFT P95 为 239.63 秒、端到端 P95 为 272.39 秒。
- 8184 输入 tokens、256 输出 tokens，发起 64 个请求，64/64 成功，同时生成峰值 45。TTFT P95 为 231.43 秒、端到端 P95 为 254.04 秒。输入加输出为 8440 tokens，采用服务的 262144 总上下文上限。
- 1023 输入 tokens、256 输出 tokens，发起 160 个请求，160/160 成功，同时生成峰值 126。TTFT P95 为 93.11 秒、端到端 P95 为 108.53 秒。
- 498 输入 tokens、128 输出 tokens，发起 256 个请求，256/256 成功，同时生成峰值 157。其余请求等待资源，TTFT P95 为 67.20 秒、端到端 P95 为 78.58 秒。

短输入另外逐档测试 1、2、4、8、16、32、64、128 路，全部成功。64 路的合计输出为 396.55 tokens/s、TTFT P95 为 13.70 秒，128 路为 414.11 tokens/s、28.23 秒，256 路为 415.64 tokens/s、67.20 秒。增加高并发主要延长等待，吞吐提升逐渐收窄。[完整测试曲线数据](capacity-results.json)

上述 15 组探索测试共 765 个请求，均收到完整答案流，三处校验码全部通过，抢占增量为 0，容器未发生自动重启。采样显存最高为 32104MiB，物理余量约 503MiB。另有 0.90 显存预算下的 128K 双路基线，2/2 成功，同样记录在汇总文件中。

探索配置的长输入组在启用自动工具调用之前执行，498-token 组在补齐 `qwen3_coder` 参数并重启后执行。短输入测试时观察到少量其他内网业务请求，环境未完全隔离，延迟与资源采样会受当时负载影响。本轮结果描述合成请求的观察边界，业务输入与输出增长后需重新测量。

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
