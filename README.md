# Qwen3.8 27B 本地部署配置方案

公开仓库：[Mingsync/qwen38-27b-frp-api](https://github.com/Mingsync/qwen38-27b-frp-api)。

本仓库包含双 RTX 5090 上的 vLLM API 配置、官方模型下载脚本、API Key 鉴权、FRP TLS 隧道、Nginx HTTPS 和 systemd 服务。先阅读本文本地部署步骤，再按 [FRP 公网部署方案](FRP公网部署方案.md) 接入云服务器；[验证边界](VALIDATION.md) 与 [贡献及检查流程](CONTRIBUTING.md) 单独记录。

```bash
git clone https://github.com/Mingsync/qwen38-27b-frp-api.git
cd qwen38-27b-frp-api
```

克隆操作不启动服务。后续按文档把配置放到 `/opt/qwen38`，替换示例域名并初始化自己的 Key；仓库不包含实际业务密钥、证书私钥或模型权重。

适用目标：Linux x86_64 单机、2 × NVIDIA GeForce RTX 5090 32GB，提供可自定义 API Key、IP、端口、域名及 URL 前缀的 OpenAI 兼容文本推理服务。

首启用 16K 上下文、2 个活跃序列、Eager 模式，验收后再提高性能参数。域名访问可加宿主机 Nginx；也可直接通过内网 IP 访问。

本目录是交付配置包，未连接或修改目标 Linux 服务器，未下载几十 GB 权重，也未在双卡 5090 上实测性能。已核对官方仓库和文档；脚本的静态与本地逻辑检查记录见 `VALIDATION.md`。文中的显存余量和参数档位是工程估算，最终以目标服务器的加载日志、NCCL 检查与压测为准。

**公网访问采用新增的 [FRP 公网部署方案](FRP公网部署方案.md)**：云端 Nginx HTTPS → 云端 frps 127.0.0.1:18000 → 校验证书的 TLS 隧道 → 内网 frpc → 本机 vLLM 127.0.0.1:8000。两端配置、固定版本安装、独立隧道 Token、证书、systemd 和公网验收均已包含在 `frp/` 及该文档中。

## 1. 模型与版本确认

官方模型名称为 `Qwen/Qwen3.8-27B`。它是带视觉编码器的稠密混合注意力模型，配置架构名称仍为 `Qwen3_5ForConditionalGeneration`、`model_type=qwen3_5`。[官方模型卡](https://huggingface.co/Qwen/Qwen3.8-27B)、[官方 config.json](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/config.json)

本方案默认使用官方预量化 FP8 权重，保留模型原始聊天模板，通过 checkpoint 自带的量化配置自动识别。不要把 BF16 文件改名冒充 FP8，也不要给 FP8 checkpoint 强加 AWQ/GPTQ 参数。[官方 FP8 模型卡](https://huggingface.co/Qwen/Qwen3.8-27B-FP8)

已核对并固定以下仓库提交：

- FP8：`Qwen/Qwen3.8-27B-FP8`，revision=`017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`。
- BF16：`Qwen/Qwen3.8-27B`，revision=`1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`。
- 推理镜像：官方 `vllm/vllm-openai:v0.32.0`，默认配置已固定为 `image-lock.json` 记录的多平台 index digest `sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34`，同时记录 Linux amd64/arm64 子清单。更新版本时重新核对官方 digest 并一起更新锁文件与配置。[vLLM 0.32.0 官方发行页](https://github.com/vllm-project/vllm/releases/tag/v0.32.0)

FP8 服务在客户端统一使用别名 `qwen3.8-27b`。API 请求的 `model` 必须与该别名一致，不能直接假定它等于下载仓库名。

## 2. 硬件与显存评估

### 2.1 双卡不是一张自动共享的 64GB 显卡

RTX 5090 的参考规格为每卡 32GB GDDR7、计算能力 12.0、无 NVLink，参考总显卡功耗 575W。两卡间通信走 PCIe/可用的 NCCL 传输路径；PCIe 插槽布局、主板 ACS/IOMMU、跨 CPU 插槽连接及 P2P 可用性都会影响 TP=2 的吞吐与延迟。[NVIDIA RTX 5090 规格](https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/)

必须显式使用张量并行，将权重分布到两卡。不能在两张卡上分别启动同一个 BF16 模型，就指望它们共享显存。每卡都要容纳自己的分片、通信缓冲区、缓存及运行时开销，单卡不足仍会 OOM。

### 2.2 权重容量

纯参数下限估算：27B × 2 字节约 54GB，即 50.29GiB；27B × 1 字节约 27GB，即 25.15GiB。GB 为十进制，GiB 为 2^30 字节，不能混用。

本次核对原始 BF16 权重索引，实际 tensor 总字节数为 55,562,855,904，约 **51.75GiB**，TP=2 理想均分约 **25.87GiB/卡**，尚未计入运行时。[官方权重索引](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/model.safetensors.index.json)

官方 FP8 Hub 元数据包含约 24.699B 个 FP8 元素及 3.082B 个 BF16 元素，因此数据载荷约 30.864GB，即 **28.74GiB**，还应加 scale、文件头等开销。它不是所有参数都只占 1 字节。TP=2 理想均分约 **14.37GiB/卡**；实际以下载脚本生成的 manifest、权重加载日志为准。[官方 FP8 仓库元数据](https://huggingface.co/api/models/Qwen/Qwen3.8-27B-FP8)

规划结论：

- **主推 FP8 + TP2**：给激活、SSM 状态和上下文缓存留出明显余量，适合逐步增加上下文和并发。初期按每卡约 15–18GiB 权重及量化相关占用做预算，这是规划区间而非实测。
- **BF16 + TP2**：可以尝试低并发短上下文，首启 8K、1 序列、Eager、显存比例 0.92；每卡预算约 29.44GiB，权重后只余约 3.6GiB 用于框架和缓存，较紧张。不能保证 32K/高并发。
- **4bit/NVFP4**：权重还可下降，但实际大小、支持内核和精度要看具体 checkpoint。本包不依赖第三方量化仓库即可落地，因此不把它作为首选。

### 2.3 混合注意力缓存估算

配置中 64 层只有 16 层为全注意力，其余 48 层使用线性注意力。仅对全注意力部分、BF16 KV、TP=2 且 KV heads 可均分时：

```text
每 token 每卡 KV 字节
= 2(K,V) × 16层 × (4 KV heads / 2卡) × 256 head_dim × 2字节
= 32,768字节 = 32KiB

8,192 tokens  × 1序列：约0.25GiB/卡
16,384 tokens × 2序列：约1GiB/卡
32,768 tokens × 4序列：约4GiB/卡
```

以上不含线性注意力 recurrent state、状态检查点、分页/对齐浪费、prefill 激活、临时 GEMM 工作区、CUDA Graph 和通信内存。不能把该公式当作总显存公式，也不能按 64 层全注意力模型来估算。[模型结构配置](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/config.json)

本包默认 KV cache 为 `auto`，未量化模块使用 BF16。FP8 权重量化不等于 KV cache 也是 FP8。模型原生上下文上限也不等于当前机器能在该长度下承载任意并发。


## 3. 推理框架选型

**选择 vLLM。** 本需求核心是标准 HTTP API、自定义 Key、双卡并行和可维护服务配置，vLLM 的本地模型加载、连续批处理、OpenAI 兼容接口与容器交付能覆盖这些需求。vLLM 官方配方也包含此模型和双 RTX 5090 场景，但其配方的硬件实测版本与本包版本不同，不能直接照搬配方吞吐数字。[vLLM 官方模型配方](https://recipes.vllm.ai/Qwen/Qwen3.8-27B)

SGLang 也是合理备选，尤其已有该框架运维经验时；不建议首轮同时安装两套复杂 CUDA 环境。Transformers 适合离线验证与实验，直接拿示例 `generate()` 充当生产 API 还需要自行实现调度、鉴权和服务管理。llama.cpp/Ollama 更适合 GGUF/个人客户端体验；本项目优先使用官方 Transformers 格式 FP8 权重，降低格式切换带来的变量。

本包只承诺文本 Chat Completions 范围。默认关闭图像/视频输入与工具自动调用；如业务需要这些能力，再单独打开并验收。

## 4. 交付文件与服务器目录

```text
Qwen3.8模型配置方案/
  README.md                 本方案
  VALIDATION.md             本地检查记录与待服务器验收项
  .env.example              IP、端口、模型、性能参数示例
  compose.yaml              双卡容器服务
  init-config.sh            初始化 .env 和随机 API Key，不覆盖现有值
  download-model.py         固定 revision 下载、校验分片并生成完整性清单
  launch.sh                 容器启动入口，加载 Key 并构造启动参数
  local_guard.py            接口白名单与 Key 校验中间件
  preflight.sh              GPU/CUDA/CLI 预检查
  nccl-check.py             两卡 NCCL 实际通信测试
  verify.py                 模型、鉴权、推理、旁路接口验收
  client.py                 Python 流式调用示例
  validate_model.py         启动校验与完整权重 SHA-256 验收
  image-lock.json           官方基础镜像 digest 与平台清单
  benchmark.py              真实 API 流式延迟及吞吐压测
  offline/                  内核镜像、断网验收与缓存导入导出
  ops/                      有重启预算的健康恢复与维护说明
  nginx/                    HTTP、HTTPS 与 SSE 反向代理配置
  FRP公网部署方案.md         云服务器中转的公网 HTTPS 完整部署步骤
  frp/                      两端 FRP、systemd、证书和云端 Nginx 配置
```

建议复制配置包到服务器 `/opt/qwen38`，权重放 `/srv/models`。下文命令在目标 Linux 上用 Bash 执行，默认有 sudo 权限。它们不是在编写方案的当前电脑上执行。

```bash
sudo mkdir -p /opt/qwen38 /srv/models
sudo chown "$USER":"$(id -gn)" /opt/qwen38 /srv/models
# 将本目录全部内容（含 .env.example）复制到 /opt/qwen38
cd /opt/qwen38
bash init-config.sh
```

只从可信交付包复制脚本。`secrets/api_key` 由初始化脚本生成，目录权限 700、文件权限 600；不写入 Git、不放入 URL、不写入 Nginx access log，也不通过 `set -x` 打印。

## 5. 环境依赖安装

### 5.1 操作系统与驱动

当前环境按 Ubuntu 26.04 LTS x86_64、现有 595.84 驱动配置，无需为本方案降级到 580。Docker 和 NVIDIA Container Toolkit 的官方支持列表均包含 Ubuntu 26.04。[Docker 系统要求](https://docs.docker.com/engine/install/ubuntu/)、[Container Toolkit 平台支持](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/supported-platforms.html)

RTX 5090 是 Blackwell，应使用支持该卡的 **open GPU kernel modules**。先验证当前驱动和双卡，不重新安装正常工作的驱动：[NVIDIA 驱动模块说明](https://docs.nvidia.com/datacenter/tesla/driver-installation-guide/615/kernel-modules.html)

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg python3 python3-venv \
  python3-pip pciutils numactl
cat /etc/os-release
uname -r
nvidia-smi
nvidia-smi -L
modinfo -F license nvidia
```

应看到两张 RTX 5090；open 模块许可证通常显示 `Dual MIT/GPL`。只有发现驱动未加载、模块类型或当前内核不匹配等具体问题时，才按 Ubuntu/NVIDIA 官方流程修复；不要混用 `.run` 安装器与 APT 管理的驱动。Secure Boot 开启时注意 MOK 签名/注册。[Ubuntu Server 驱动安装说明](https://ubuntu.com/server/docs/nvidia-drivers-installation/)

容器方案不要求宿主机另装完整 CUDA Toolkit 或 cuDNN。`nvidia-smi` 的 CUDA 13.2 是驱动能力信息，不是已安装 CUDA Toolkit 13.2 的证明，也不要求镜像 CUDA 必须等于 13.2。本次读取 Docker Registry 的 `vllm/vllm-openai:v0.32.0` Linux amd64 镜像元数据，基础环境标注 `CUDA_VERSION=13.0.2`、`NVIDIA_REQUIRE_CUDA=cuda>=13.0 ...`；595.84 驱动在 CUDA 版本方向上满足该环境。具体 PyTorch 构建版本仍由 preflight 打印并实际运算验证。[NVIDIA CUDA 向后兼容说明](https://docs.nvidia.com/deploy/cuda-compatibility/why-cuda-compatibility.html)

若未来换成更高 CUDA 的镜像，要重新核对驱动、PTX/JIT 与镜像要求；同一 CUDA 大版本的小版本兼容存在功能限制，不代表所有新内核均可在旧驱动运行。[CUDA 小版本兼容限制](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html)

### 5.2 安装 Docker Engine 与 Compose v2

已有正常 Docker/Compose v2 可跳过。全新 Ubuntu 可使用 Docker 官方 APT 源：

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
cat <<EOF | sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker version
sudo docker compose version
```

若已有发行版 `docker.io` 等冲突包，按官方迁移步骤处理，先保护现有容器和数据。后续示例默认当前管理员有 Docker 权限；否则给 `docker` 命令加 `sudo`。Docker 组近似 root 权限，仅给授权运维账号。[Docker Ubuntu 安装说明](https://docs.docker.com/engine/install/ubuntu/)

### 5.3 安装 NVIDIA Container Toolkit

**当前服务器已报告 CLI 1.20.1，且 `docker info` 已确认注册 nvidia runtime，跳过本节安装和注册命令。** Default Runtime 保持 runc 即可，先按环境适配说明用显式 `--runtime=nvidia --gpus all` 验证容器 GPU。无需为本方案修改默认 runtime 或重启正常工作的 Docker。只有之后发现具体配置故障时才诊断修复，不重复安装软件包。[1.20.1 官方发行说明](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/release-notes.html)

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

重启 Docker 会影响该主机上的现有容器，应安排维护窗口。安装后用下文 preflight 在实际服务镜像内检查 GPU，无需额外拉取一个随意版本的 CUDA 镜像。[NVIDIA Container Toolkit 官方安装指南](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)

## 6. 模型下载、固定版本及离线部署

### 6.1 Hugging Face 主路径

```bash
cd /opt/qwen38
python3 -m venv .download-venv
.download-venv/bin/pip install --upgrade pip huggingface_hub
.download-venv/bin/pip freeze > download-requirements.lock.txt
.download-venv/bin/python download-model.py \
  --repo Qwen/Qwen3.8-27B-FP8 \
  --revision 017b9c7af6b5689d5dd426a76e0bc077eb5ca20a \
  --output /srv/models/Qwen3.8-27B-FP8
```

下载脚本固定提交并下载完整仓库，检查每个权重分片及远端大小，对全部仓库文件计算 SHA-256，并在上游提供 LFS SHA-256 时比对，原子写入 schema v2 的 `deployment-manifest.json`。中断后可向同一目录、同一 repo/revision 重跑；更换版本必须使用新目录。启动时核对清单中的模型身份、config/索引/模板/tokenizer 哈希以及所有文件大小，完整权重哈希在传输或升级验收时使用 `validate_model.py --full-hash` 检查。它不执行模型仓库的 Python 代码，也不会把 HF Token 当作推理 API Key。公共仓库一般无需 Token；需要时在下载环境单独配置 `HF_TOKEN`。

官方 CLI 可用于独立下载，但不会生成本项目的模型清单，不能直接满足本项目的启动校验。部署时使用上述脚本；已有 schema v1 清单的同版本目录可重跑脚本升级，未带下载身份或清单的非空目录应换新目录下载。必须保留 tokenizer、chat template、processor 和 config 文件，不能只复制 safetensors。[Hugging Face CLI 下载说明](https://huggingface.co/docs/huggingface_hub/guides/cli)

### 6.2 网络受限或离线服务器

最稳妥做法是在能访问官方仓库的联网 Linux 机器运行同一个固定提交下载脚本，再传输完整目录。国内也可从 Qwen 官方 ModelScope 页面选择相应模型；Hugging Face 与 ModelScope 的 revision 不保证通用，不能把此处的 HF commit 直接当作 ModelScope commit。[Qwen 官方下载渠道说明](https://github.com/QwenLM/Qwen3.8/blob/main/README.md)

联网端生成校验清单，完成后再打包/传输：

```bash
cd /srv/models/Qwen3.8-27B-FP8
find . -type f ! -path './.cache/*' ! -name SHA256SUMS -print0 \
  | sort -z | xargs -0 sha256sum > SHA256SUMS
# 用 rsync/scp 将整个模型目录送往目标机相同路径
# 目标机：
cd /srv/models/Qwen3.8-27B-FP8
sha256sum -c SHA256SUMS
```

镜像与内核采用 [离线部署操作手册](offline/README.md)：固定 digest 构建并运行 `vllm download-kernels`，在同类双卡环境用 `network_mode: none` 真实加载与生成，导出四类运行缓存；目标机核对归档和模型哈希后再次断网验收。仅 `docker save/load` 或设置 Hub 离线变量，不能证明首次启动不需要网络。通过前不把隔离环境部署标记为完成。[vLLM 内核缓存说明](https://docs.vllm.ai/en/v0.32.0/getting_started/installation/gpu/)

## 7. 自定义 API Key、监听 IP、端口及访问 URL

### 7.1 API Key

```bash
cd /opt/qwen38
bash init-config.sh
# 如需使用自己的 Key，在编辑器中把这一文件改为仅包含一行 Key：
nano secrets/api_key
chmod 600 secrets/api_key
```

本包约定至少 24 个 ASCII 字符且无空白，建议使用初始化脚本生成的随机值。API 请求头为 `Authorization: Bearer <你的Key>`。Key 是本地服务自定义的共享密钥，不依赖任何云厂商账号。

`launch.sh` 从 `/run/secrets/qwen_api_key` 读取 Key，导出 `VLLM_API_KEY`，等效于 vLLM 的 `--api-key` 配置，不把密钥放进进程启动参数。[vLLM 环境变量](https://docs.vllm.ai/en/v0.32.0/configuration/env_vars/)

Compose 本地 secrets 是文件挂载，不是加密的集中密钥管理。root/Docker 管理员仍能访问密钥与进程环境。更换 Key 后执行 `docker compose up -d --force-recreate qwen`，并更新客户端。

### 7.2 本机调试：默认值

`.env` 设置：

```dotenv
BIND_HOST=127.0.0.1
API_PORT=8000
API_BASE_URL=http://127.0.0.1:8000/v1
SERVED_MODEL_NAME=qwen3.8-27b
```

Docker 内部始终监听 `0.0.0.0:8000`，宿主只发布 `127.0.0.1:8000`。其他电脑不能直接访问，这是默认访问范围。不能把容器内监听改成 `127.0.0.1` 后还期待宿主端口映射正常。

### 7.3 内网直接访问：无需 Nginx

例如服务器真实内网 IP 为 `192.168.10.20`，希望用端口 18000：

```dotenv
BIND_HOST=192.168.10.20
API_PORT=18000
API_BASE_URL=http://192.168.10.20:18000/v1
```

这里的地址是示例，替换为服务器网卡实际拥有的地址，否则 Docker 会报 bind 错误。也可设 `BIND_HOST=0.0.0.0` 监听所有 IPv4 接口，但应配合网络访问控制。客户端不能使用 `0.0.0.0` 作为访问地址。

服务 URL 由协议、可达 IP/域名、宿主端口及路径组成：`http://真实IP:端口/v1`。修改 `API_BASE_URL` 只用于记录/客户端配置，**不会**改变监听、创建 DNS、签发证书或自动配置路由。修改 `.env` 后执行 `docker compose up -d --force-recreate qwen`。

仅在可信内网使用明文 HTTP；跨不可信网络使用 HTTPS 或已有 VPN。Docker 发布端口可能绕过 UFW 的常规 INPUT 规则，不能只看到 `ufw deny` 就认定已经隔离；优先绑定指定内网地址，并通过交换网络/边界防火墙及 Docker 转发规则限制来源。[Docker 防火墙说明](https://docs.docker.com/engine/install/ubuntu/)

### 7.4 域名与自定义路径

有宿主机 Nginx 时，保持后端 `BIND_HOST=127.0.0.1`、`API_PORT=8000`：

- 标准 Base URL：`https://llm.example.com/v1`。
- 带业务前缀 Base URL：`https://llm.example.com/qwen/v1`。
- 完整对话接口：Base URL 再拼 `/chat/completions`。

普通 OpenAI SDK 的 `base_url` 填到 `/v1`，不要直接填完整 `/chat/completions`。个别业务系统自动拼 `/v1`，按其文档填根 URL，避免 `/v1/v1`。`server_name` 仅匹配域名，不负责 DNS；须在 DNS 或企业内网解析系统指向服务器。

### 7.5 默认接口范围与鉴权边界

当前 vLLM 的原生 API Key 保护不覆盖所有路由，官方特别说明 `/invocations` 等非 `/v1` 入口可能不受 Key 保护。因此本包额外配置 `local_guard.LocalApiGuard`：[vLLM 官方安全说明](https://docs.vllm.ai/en/v0.32.0/usage/security/)

- `GET /v1/models`、`POST /v1/chat/completions`：必须持有效 Key。
- `GET /metrics`：必须持有效 Key，仅供后端监控；Nginx 默认不转发。
- `GET /health`：不带 Key，供容器内部探针；通过直接端口可见健康状态。
- 其余端点统一返回 404，包括替代推理、管理接口、文档、Responses、Embeddings、工具相关辅助端点和 Web UI；WebSocket 关闭。

这是面向当前业务范围的白名单，使用纯 ASGI 不缓存响应体，支持 SSE。它不限制请求频率、token 配额或每人用量；共享 Key 不等于多租户计费系统。有更多接口需求时，逐项扩展白名单并补验收，不能直接删掉防护。

## 8. 启动与预检查

确保 `.env` 的 `MODEL_DIR` 已存在且下载完整，再执行：

```bash
cd /opt/qwen38
docker compose config --quiet
docker compose pull
# 在线路径已使用 .env 中固定的镜像 digest；离线路径按 offline/README.md 操作
bash preflight.sh
docker compose run --rm --no-deps --entrypoint torchrun qwen \
  --standalone --nproc_per_node=2 /opt/qwen/nccl-check.py
docker compose up -d
docker compose logs -f --tail=100 qwen
```

preflight 会实际在两卡执行 BF16 矩阵运算，并打印 vLLM/PyTorch/Transformers/CUDA 版本；仅能验证基础 CUDA，FP8 模型内核仍须实际加载验证。NCCL 检查必须在模型占用 GPU 之前执行，以验证双卡 all-reduce 结果和通信路径。

默认性能参数在 `.env`：FP8 权重、TP=2、显存比例 0.90、上下文 16384、最多 2 个活跃序列、每调度轮最多 2048 tokens、EAGER=1、TEXT_ONLY=1、CPU_OFFLOAD_GB=0、PREFIX_CACHING=0。服务端通过 `MAX_OUTPUT_TOKENS=1024` 限制每个生成序列的输出预算，运行加等待最多 `MAX_NUM_QUEUED_REQS=8` 个 HTTP 请求，超出返回 503。默认 `ENABLE_THINKING=0`，SSE 空闲时每 `SSE_KEEP_ALIVE_INTERVAL=15` 秒发保活注释。所有启动参数见 `launch.sh`，参数定义见 [vLLM 0.32.0 CLI](https://docs.vllm.ai/en/v0.32.0/cli/serve/)。

其中 `--dtype bfloat16` 控制非量化模块，不会把 FP8 权重自动展开成 BF16；FP8 由 checkpoint 内 `quantization_config` 识别，不需要额外 `--quantization fp8`。`max_model_len` 包含输入、聊天模板、历史消息与输出；`max_num_batched_tokens` 是调度预算，不是单个请求上下文上限。

启动成功应看到两卡权重加载、KV cache 容量与 HTTP 就绪日志。首次内核编译可能较慢，不要只凭一分钟没有返回就反复重启。镜像探针有 30 分钟启动宽限；容器显示 `running` 不代表已完成模型加载。

```bash
docker compose ps
curl --fail http://127.0.0.1:8000/health
python3 verify.py --base-url http://127.0.0.1:8000/v1
```

若改成直连内网 IP/18000，以上 URL 一并替换。未手动重启或删除容器时，`restart: unless-stopped` 随 Docker 启动恢复服务；Docker 本身已设开机启动。

## 9. Nginx 反向代理（可选）

通过云服务器 FRP 公网访问时，请采用 `FRP公网部署方案.md` 中的云端配置，后端指向云端 127.0.0.1:18000；无需在 GPU 主机再安装这一节的 Nginx。本节仅适用于 Nginx 与模型处于同一宿主机的访问方式，两个方案不可混淆上游端口。

本包配置假设 Nginx 安装在同一台 Linux 宿主机。若 Nginx 也运行在容器中，`127.0.0.1` 指向那个容器自身，应改用共享 Docker 网络中的服务名并重新设计发布方式，不能直接照抄。

### 9.1 HTTP：内网域名或证书准备阶段

先保证后端使用宿主 `127.0.0.1:8000`：

```bash
sudo apt-get install -y nginx
sudo mkdir -p /etc/nginx/snippets
sudo install -m 644 nginx/qwen38-proxy.conf /etc/nginx/snippets/qwen38-proxy.conf
sudo install -m 644 nginx/qwen38-http.conf /etc/nginx/conf.d/qwen38.conf
sudo nano /etc/nginx/conf.d/qwen38.conf
# 将所有 llm.example.com 改成实际域名；后端端口有改动也要同步
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

`proxy_pass` 在标准 `/v1/...` 路径下保留原 URI；在 `/qwen/v1/...` 精确位置中显式去除 `/qwen`，后端收到的仍是 `/v1/...`。因此无需再给 vLLM 加 `/qwen` 路由。不要随意给 `proxy_pass` 加尾部 `/`，这会改变 URI 替换规则。

共享 snippet 关闭代理缓冲和缓存，保留 Bearer Header，设置 600 秒读写空闲超时并关闭自动换上游重试。`proxy_read_timeout` 是相邻读取间的空闲时间，不是总请求时长。[Nginx 代理模块说明](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)

### 9.2 HTTPS：公网或企业网络正式入口

准备实际域名解析、80/443 的网络入口及有效证书。若使用 ACME，域名和端口可满足校验时，可在 HTTP 配置通过检查后用发行版支持的 Certbot/Nginx 流程申请；企业内部域名可使用内部 CA。证书签发方式取决于网络和 DNS，本包不假设示例域名可签发。

证书已放到配置要求的位置后：

```bash
sudo install -m 644 nginx/qwen38-https.conf /etc/nginx/conf.d/qwen38.conf
sudo nano /etc/nginx/conf.d/qwen38.conf
# 替换域名和 fullchain.pem / privkey.pem 路径
sudo nginx -t
sudo systemctl reload nginx
```

同一位置用 HTTPS 配置替换 HTTP 配置，避免重复 server。证书不存在时先不要启用引用它的 443 配置。启用续期机制并检查续期后 reload。客户端使用受信证书，不以 `curl -k` 作为生产验收方式。

例如标准入口调用：

```bash
python3 verify.py --base-url https://llm.example.com/v1
# 带前缀入口：
python3 verify.py --base-url https://llm.example.com/qwen/v1
```

Nginx 默认只转发 models 和 chat completions，不公开 health、metrics、管理接口。请求 Key 仍由后端验证，Nginx 配置中没有硬编码业务 Key。

## 10. 验证调用示例

### 10.1 设置客户端配置

在服务器本机验收：

```bash
cd /opt/qwen38
export OPENAI_BASE_URL=http://127.0.0.1:8000/v1
export OPENAI_API_KEY="$(cat secrets/api_key)"
```

远程客户端设置为可达的内网或 HTTPS Base URL，并通过自己的密钥管理方式设置相同 Key。以下环境变量只在当前 shell 生效；业务应用须在自身运行环境配置，不能假定服务器上的 export 会传到另一台电脑。

### 10.2 查询模型与非流式对话

```bash
curl --fail-with-body "$OPENAI_BASE_URL/models" \
  -H "Authorization: Bearer $OPENAI_API_KEY"

curl --fail-with-body "$OPENAI_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary @- <<'JSON'
{
  "model": "qwen3.8-27b",
  "messages": [{"role": "user", "content": "请用一句中文介绍你自己。"}],
  "max_tokens": 128,
  "temperature": 0.7,
  "top_p": 0.8,
  "top_k": 20,
  "presence_penalty": 1.5,
  "chat_template_kwargs": {"enable_thinking": false},
  "stream": false
}
JSON
```

初次验收关闭思考，避免有限的输出预算全用于 reasoning 而 `content` 为空。服务默认关闭思考，请求仍可通过 `enable_thinking=true` 覆盖默认值。需要更长思考输出时，运维先调整 `MAX_OUTPUT_TOKENS` 并重新验收容量，再增大请求输出预算；客户端同时处理 `reasoning`/`reasoning_content` 与最终 `content`；不要将 reasoning 文本当作最终答案。本包保留官方模板，未加入自行编写的角色拼接模板。[Qwen 官方 API 与思考控制说明](https://huggingface.co/Qwen/Qwen3.8-27B)

### 10.3 流式 SSE

```bash
curl --no-buffer --fail-with-body "$OPENAI_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary @- <<'JSON'
{
  "model": "qwen3.8-27b",
  "messages": [{"role": "user", "content": "用三点说明本地模型的用途。"}],
  "max_tokens": 256,
  "chat_template_kwargs": {"enable_thinking": false},
  "stream": true
}
JSON
```

应持续收到 `data: ...` 事件并正常结束。分别通过直连和 Nginx 验证，不能只测一次非流式成功就认定代理正确。

### 10.4 Python SDK

```bash
python3 -m venv .client-venv
.client-venv/bin/pip install openai
.client-venv/bin/python client.py
```

示例 SDK 只作为兼容客户端使用，调用目标是本地服务，不会自动转到云端。OpenAI 兼容不代表实现该厂商所有 API；本包明确只开放 models 和 chat completions。

### 10.5 验收要求

`verify.py` 无第三方依赖，覆盖：无 Key/错误 Key 401、正确 Key 能列出模型、模型别名匹配、无 Key 无法推理、有 Key 返回非空中文内容，以及 `/invocations`、`/pause`、`/collective_rpc` 均被阻断。

服务器验收还应包含：两卡均参与加载、NCCL 正确、直连和代理 SSE 成功、目标长度与并发负载成功、Key 轮换生效、重启后服务恢复。验收 URL 必须从实际业务客户端所在网络再测一次。

## 11. 系统调优与容量扩展

### 11.1 调整顺序

每次只改变一类参数并记录结果，避免同时切换镜像、量化方式和并发，导致故障难以定位。

1. **起步档（本包默认）**：官方 FP8、TP2、16K、2 序列、batched tokens 2048、显存比例 0.90、Eager=1、prefix caching 关闭。
2. **性能档候选**：稳定后先单独设 EAGER=0 启用编译/CUDA Graph，重测峰值显存。再单独比较 PREFIX_CACHING=1；混合注意力状态缓存需验证当前模型/内核兼容性及冷热命中结果。图捕获可能额外占用显存，若 OOM 先恢复 Eager。
3. **业务档候选**：先尝试 32K + 4 序列；batch tokens 可从 2048 到 4096，比较首 token 延迟与吞吐，再考虑 64K。不能只提高 `max_num_seqs` 就认为总吞吐一定增加。
4. **长上下文**：128K/256K 和更多并发先做独立容量测试。必要时研究 FP8 KV，但它与模型 FP8 权重是两项独立变更，要确认 hybrid attention/SSM 状态的内核支持与长文质量；本包不默认启用。

修改 `.env` 后 `docker compose up -d --force-recreate qwen`。不要对运行中的 vLLM 直接换模型目录里的文件。

### 11.2 主机与容器

- Compose 已配置 16GB `/dev/shm` 上限、memlock 与 65535 nofile，避免默认 64MB 共享内存瓶颈。shm 不是预留显存，也不是一启动就锁住 16GB RAM。
- NVMe 加载，保留编译缓存卷；不把全部 CPU 线程分给 tokenizer/OpenMP。双路 CPU 先测 NUMA 和两卡位置，只有日志或基准说明问题时再做进程亲和性绑定。
- 驱动支持时可 `sudo nvidia-smi -pm 1` 开启持久模式；不做超频。限功耗要先检查卡支持的范围，再用实际业务比较速度、温度和功耗。
- 保留正常系统 swap 策略，但推理工作集不应依赖频繁 swap；CPU offload 与 swap 不是一回事。不要为“优化”盲目关闭整机安全/内存机制。
- 服务日志已轮转，每份 20MB，5 份。勿长期启用打印用户请求/响应正文的调试选项。

### 11.3 监控与压力测试

```bash
watch -n 1 nvidia-smi
docker compose stats
docker compose logs --tail=200 qwen
curl --fail http://127.0.0.1:8000/metrics \
  -H "Authorization: Bearer $OPENAI_API_KEY"
```

建议业务验收矩阵：输入约 1K/8K/接近设定上限，输出 128/512/1024 tokens，并发 1/2/4（后者需已提高活跃序列参数）；每组重复多次，覆盖真实中文、长文、JSON 输出等负载。记录请求成功率、TTFT、端到端延迟 P50/P95、输出 tokens/s、显存峰值、排队和抢占情况。输入加输出必须在上限内。

默认最多 2 路同时生成，运行加等待最多 8 个请求；Nginx 入口超限返回 429，vLLM 容量阀超限返回 503。客户端做有限次数、带抖动的退避，避免自动无限重试。共享 Key 不区分用户，`max_tokens` 上限按每个生成序列计算；多候选 `n>1` 会额外消耗算力。对外多人业务应在网关限制 `n=1`、按用户分配 Key、记录配额并实施总时长预算。当前包未提供用户管理/计费网关。SSE 保活不计入答案 token，也不限制请求总耗时。

“支持多少用户”取决于用户请求频率与可接受等待。2 路活跃不等于只能注册 2 个用户，也不等于可承诺 8 人同时快速回答；先按目标输入/输出和业务频率测出延迟与吞吐，再决定用户规模。

用标准库压测工具记录真实 API 数据，先跑一轮预热，再分别测试本地与 FRP 公网 URL：

```bash
python3 benchmark.py --concurrency 1 --requests 1 --max-tokens 128
python3 benchmark.py --concurrency 1 --requests 20 --max-tokens 256
python3 benchmark.py --concurrency 2 --requests 20 --max-tokens 256
python3 benchmark.py --concurrency 4 --requests 20 --max-tokens 256
python3 benchmark.py --base-url https://llm.example.com/v1 \
  --concurrency 2 --requests 20 --max-tokens 256
# 长文场景另外加 --prompt-file /可信目录/代表性提示词.txt
```

并发 4 而活跃序列仍为 2 时，测到的是含排队的延迟；提高活跃序列应另建一组记录。结果包含成功率、TTFT 与端到端 P50/P95、聚合输出 tokens/s 和服务端报告的输入 token 数。保活/角色/思考片段不算首个答案，缺少 `[DONE]` 或空答案计为失败；服务端未返回 usage 时不估算 tokens/s。`--timeout` 是网络读超时，保活可能延长总请求耗时，严格总时长由业务网关/客户端管理。默认测试复用同一提示词，prefix caching 打开后须另测真实提示词分布，避免高命中率误导容量判断。

本方案不提供未经实测的“每秒多少 token”承诺。PCIe 跨卡通信、输入长度、思考模式、并发与编译状态均会显著影响结果。

## 12. BF16 替代配置

如果业务要求原始 BF16 权重，单独下载到新目录：

```bash
cd /opt/qwen38
.download-venv/bin/python download-model.py \
  --repo Qwen/Qwen3.8-27B \
  --revision 1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0 \
  --output /srv/models/Qwen3.8-27B
```

在 `.env` 调整：

```dotenv
MODEL_ID=Qwen/Qwen3.8-27B
MODEL_REVISION=1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0
MODEL_DIR=/srv/models/Qwen3.8-27B
TENSOR_PARALLEL_SIZE=2
GPU_MEMORY_UTILIZATION=0.92
MAX_MODEL_LEN=8192
MAX_NUM_SEQS=1
MAX_NUM_BATCHED_TOKENS=1024
EAGER=1
TEXT_ONLY=1
CPU_OFFLOAD_GB=0
```

别名可保持 `qwen3.8-27b`，客户端不必改。若仍 OOM，先缩上下文与序列预算，确保其他进程释放 GPU；必要时可尝试 `CPU_OFFLOAD_GB=4`（每 GPU），但 PCIe 取权重会拖慢推理、增加 RAM 需求，属兜底方案。若 FP8 已满足精度需求，通常优先恢复 FP8，避免长期 offload。[vLLM offload 参数定义](https://docs.vllm.ai/en/v0.32.0/cli/serve/)


## 13. 运维、升级与回滚

```bash
cd /opt/qwen38
docker compose ps
docker compose logs --tail=200 qwen
docker compose stop
docker compose start
# 修改 .env / Key / 挂载脚本后：
docker compose up -d --force-recreate qwen
```

可选的 [有限健康恢复](ops/README.md) 通过宿主机 timer 处理连续 unhealthy；需显式安装启用。Docker restart policy 本身不因 unhealthy 自动重启。恢复有冷却和每小时预算，维护前应暂停 timer，不能以无限重启替代诊断。

生产升级前记录 `.env`（不包含 Key）、镜像 digest、模型 revision、硬件/驱动版本、权重 SHA256SUMS、业务验收结果和当前时延基线。Key 独立保管。新镜像先做 preflight、NCCL、鉴权、SSE 和真实业务测试，通过后再替换。

回滚时恢复旧 `.env`、旧镜像 digest、旧完整模型目录与匹配脚本，然后重新创建容器。CPU offload、不同量化和 chat template 变更都应纳入回归。日常停止不需要删除 named volumes；不要为排错直接运行 `docker compose down -v` 清空缓存。

最终上线以这些结果为准：目标网络可达、自定义 Key 生效、未授权推理被阻断、模型回答正确、代理流式正常、目标上下文/并发下无 OOM、重启恢复成功。完成后把实测容量、响应延迟和最终 digest 写回本目录的运维记录。

## 14. 公网访问扩展：FRP 自建隧道

已按“内网主动连接云服务器”的方式补齐公网部署，详见 [FRP 公网部署方案](FRP公网部署方案.md)。默认只在云端开放 HTTPS 443、证书校验 80 和隧道入口 TCP 7000；云端业务映射口 18000 与内网模型 8000 均为回环监听。FRP v0.71.1 两端使用独立隧道 Token、TLS 服务端证书校验和 systemd 自动恢复，公网业务继续使用原模型 API Key。

配置包未部署到真实公网环境。新增 Nginx 全局入口上限为 8 个对话 HTTP 请求，超过返回 429；该限制包含排队请求，不改变内网模型默认 2 路同时生成和每请求 16K 上下文。

## 15. 已部署旧版本的迁移

`init-config.sh` 保留现有 `.env` 与 Key，升级代码不会自动覆盖旧镜像设置。维护前保留旧源码提交、`.env`、旧模型目录和缓存备份，暂停 watchdog 与业务入口，再执行迁移：

1. 对照 `.env.example`，将旧浮动镜像 tag 改为本次固定 digest；补齐输出、排队、默认思考、SSE 保活及性能开关。采用新默认值时为 `MAX_OUTPUT_TOKENS=1024`、`MAX_NUM_QUEUED_REQS=8`、`ENABLE_THINKING=0`、`SSE_KEEP_ALIVE_INTERVAL=15`、`PREFIX_CACHING=0`、`EAGER=1`。离线部署使用通过验收的本地不可变镜像 ID。
2. 旧 schema v1 模型清单需要在联网下载环境用同 repo/revision 重跑新版下载脚本升级并重新验收；不同版本使用新目录。离线目标机转移升级后的完整清单与模型，不能手改 revision 绕过校验。
3. 用 `validate_model.py --full-hash` 完成权重传输验收，再跑 Compose 配置、preflight、NCCL、鉴权与短生成。启动会检查模型身份和关键文件，失败时停止服务，修复完整模型后再启动。
4. 重新创建容器，再验收本地与公网 SSE、长文及并发。请求 16K 上下文包含输出；默认输出上限为 1024，超额请求的实际行为与 finish_reason 一并检查。需要更长回答时先评估资源并调整服务端上限。
5. 验收通过再恢复入口与 watchdog。若失败，回滚到完整旧版本配置/镜像/模型；不要只回退 `.env` 而保留不匹配的启动脚本或模型清单。
