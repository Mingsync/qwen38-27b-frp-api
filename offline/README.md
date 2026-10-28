# 断网部署：镜像、内核、模型与运行缓存

只设置 Hub 离线变量不足以证明可断网运行。这里把固定 digest 的镜像构建、预编译内核准备、真实 GPU 断网生成、缓存导出和目标机复验串起来。工具需要 Linux、Docker Compose v2（支持 `!reset`）和 Python 3.11+；构建端与目标端 CPU 架构一致，GPU 验收端使用同样的双 RTX 5090、驱动及最终推理参数。

所有命令在 `/opt/qwen38` 执行。离线验收固定使用独立 Compose 项目 `qwen38-offline-check`，不发布端口，容器 `network_mode: none`。它仍需要两张 GPU，先按 [维护说明](../ops/README.md) 暂停 watchdog，并停止占用 GPU 的生产服务。配置包、模型目录、业务 Key、FRP 凭证分别安全转移；下列 bundle 不包含模型或业务凭证。

## 1. 联网端准备

先按 [主方案](../README.md) 初始化 `.env` / `secrets/api_key`，使用 `download-model.py` 下载固定 revision 的完整模型，核对 `.env` 的 `MODEL_ID`、`MODEL_REVISION` 和 `MODEL_DIR`。

```bash
cd /opt/qwen38
# output 必须是尚不存在的新目录；预留镜像、归档和缓存所需磁盘空间
python3 offline/prepare_image.py --output /srv/qwen38-bundle
```

构建仅发送 `offline/` 中的 Dockerfile，不发送仓库中的 Key。基础镜像取自 `image-lock.json` 的固定 digest，构建执行 `vllm download-kernels`。输出包含 `inference-image.tar` 与 `bundle.json`；后者记录不可变镜像 ID、平台和归档 SHA-256。构建成功仍不代表模型已在 GPU 上通过断网验收。内核必须与镜像中的 FlashInfer/CUDA 匹配，升级后重新构建。[vLLM 内核安装说明](https://docs.vllm.ai/en/v0.32.0/getting_started/installation/gpu/)

## 2. 同类 GPU 上断网验收并导出缓存

```bash
cd /opt/qwen38
docker compose stop qwen
IMAGE_ID=$(python3 -c 'import json; print(json.load(open("/srv/qwen38-bundle/bundle.json"))["image_id"])')
python3 offline/verify_offline.py --image "$IMAGE_ID" --bundle /srv/qwen38-bundle
python3 offline/export_caches.py --bundle /srv/qwen38-bundle
```

验收只操作独立项目：等待健康、在容器内读取 Key 并真实生成非空答案，最后删除测试容器并保留缓存卷。失败会将旧通过记录置为失败；缓存导出要求同一镜像已通过，并再次核对镜像归档 SHA-256。四类缓存为 vLLM、Triton、FlashInfer 和 Torch，导出为 `runtime-cache.tar.gz`，校验值写入 bundle。保留命令日志与最终 `.env` 参数作为验收记录；短生成通过后仍需业务长上下文和并发验收。

使用可信渠道传输整个 bundle、完整模型目录及匹配版本配置包。SHA-256 用于检查传输完整性，bundle 本身也必须来自可信来源。模型 `.cache` 下载元数据可省略，模型 manifest、config、tokenizer、模板及全部权重必须保留。

## 3. 目标机恢复并再次断网验收

在目标机初始化自己的配置与 Key，填写对应模型路径；先完成容器 GPU 注入检查，并停止其他 GPU 服务。

```bash
cd /opt/qwen38
python3 validate_model.py --model-dir /srv/models/Qwen3.8-27B-FP8 \
  --repo Qwen/Qwen3.8-27B-FP8 \
  --revision 017b9c7af6b5689d5dd426a76e0bc077eb5ca20a --full-hash
python3 offline/restore_bundle.py --bundle /srv/qwen38-bundle
IMAGE_ID=$(python3 -c 'import json; print(json.load(open("/srv/qwen38-bundle/bundle.json"))["image_id"])')
python3 offline/verify_offline.py --image "$IMAGE_ID" --bundle /srv/qwen38-bundle
python3 offline/export_caches.py --bundle /srv/qwen38-bundle
```

恢复工具先核对两个归档 SHA-256 与来源验收记录，再加载镜像及缓存。目标机必须重新验收，来源机器的通过记录不能代替目标机结果。若测试缓存卷已存在，工具拒绝覆盖；确认测试容器已停止并删除后，才显式加 `--replace-caches`。禁止用不同 GPU/镜像版本的缓存推断兼容性。

## 4. 将目标机已验证缓存恢复到生产项目

```bash
cd /opt/qwen38
# 确认 watchdog 已暂停；down 不加 -v，保留现有卷以供回退
docker compose down
python3 offline/restore_bundle.py --bundle /srv/qwen38-bundle --project qwen38
# 编辑 .env，将 VLLM_IMAGE 改为 bundle 中完整的 sha256:... 镜像 ID
nano .env
docker compose config --quiet
docker compose up -d --pull never
python3 verify.py --base-url http://127.0.0.1:8000/v1
```

如果生产缓存卷已存在，先备份需要保留的缓存，并在删除引用这些卷的已停止容器后，对恢复命令显式加 `--replace-caches`；此参数会删除并重建指定项目的四个缓存卷。自定义 `COMPOSE_PROJECT_NAME` 时，`--project` 必须与实际生产项目一致。

生产服务使用基础 `compose.yaml`，以保留本地 API 发布。`offline/compose.verify.yaml` 专用于断网验收，不能用于需要对外提供 API 的生产启动。断网环境不执行 `docker compose pull`；配置变更、镜像/模型/驱动升级后重复断网与业务验收，通过后再恢复 watchdog 和 FRP 对外入口。
