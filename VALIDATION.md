# 交付检查记录

原部署包检查日期：2026-10-26；GitHub 首次发布检查日期：2026-10-28。方案编写环境为 macOS，本记录不代表目标 Linux 双 RTX 5090 已完成部署。

环境修订：用户已提供 Release 26.04、驱动 595.84、`nvidia-smi` CUDA 13.2、Container Toolkit CLI 1.20.1（commit `dffc40b4f820ce5c512633bac9e0418d0e05a2ee`）。已按 Ubuntu 26.04 调整说明，移除旧版驱动安装步骤，将 Toolkit 安装改为仅在缺失时执行。这些版本是用户报告的信息，未通过远程服务器检查。

用户后续提供 `sudo docker info` 输出：Runtimes 包含 nvidia，Default Runtime 为 runc。据此确认 NVIDIA runtime 已注册；无需修改默认 runtime 或重复注册、重启 Docker。已将该证据写入 README 与环境适配说明。容器 GPU 注入、实际 CUDA 运算与模型加载仍待验证。

已完成：

- 通过官方 Hugging Face API 核对 BF16/FP8 仓库存在、固定提交 SHA 与参数 dtype 元数据；检查官方模型配置、BF16 权重索引与 FP8 量化配置。
- 核对 vLLM 0.32.0 官方发行记录、版本化 CLI/环境变量/安全说明，以及 NVIDIA、Docker、Nginx 官方文档。
- 补查 Docker/Container Toolkit 对 Ubuntu 26.04 的平台支持及 Toolkit 1.20.1 发行说明。通过 Docker Registry 只读读取服务镜像 Linux amd64 配置，观察到 CUDA_VERSION=13.0.2；没有拉取镜像层或运行镜像。此项不是目标服务器 CUDA 执行验证。
- 所有 5 个交付 Python 文件通过语法解析，原有 3 个 Shell 文件及新增 FRP 目录中的 3 个 Shell 文件通过 `bash -n`。
- Compose YAML 解析和关键挂载/secret/只读模型配置检查通过。
- 使用本机独立 Docker Compose 5.2.0 执行 `config --quiet` 成功；没有启动 Docker 服务、拉取模型服务镜像或修改本机 Docker 配置。目标机建议使用官方 Compose 插件，并再次运行此校验。
- 在临时目录运行初始化脚本两次，确认随机 Key 格式、权限和不覆盖现有 Key 的行为。测试密钥及临时目录已删除，交付包内没有实际业务 Key。
- 对 ASGI 中间件实际运行本地异步检查：正确 Key、错误 Key、无 Key、路由白名单、替代推理与管理路由阻断、SSE 分块原样透传、lifespan 透传、WebSocket 关闭均符合设计。

FRP 公网扩展已完成的本地检查：

- 查询官方 v0.71.1 发行页及 GitHub Release API，固定版本并取得 Linux amd64/arm64 发行包 SHA-256，写入安装脚本及 `frp/RELEASE-SHA256SUMS`。
- 下载官方 macOS arm64 同版本二进制用于本地检查，SHA-256 与官方 release digest 一致；二进制只在工作目录中，未放入交付包，未安装系统服务。
- 在临时目录实际运行证书/Token 生成脚本，通过 OpenSSL 的证书链与 SAN/hostname 校验；测试私钥、Token 与临时目录已清除。
- 以实际 FRP v0.71.1 二进制执行 frps/frpc 严格配置验证，均通过。仅将生产路径、监听地址及端口改成本机临时值，配置字段保持一致。
- 启动真实 frps/frpc 进程，通过 127.0.0.1 上的 TLS 隧道访问模拟 HTTP 后端；验证 Authorization 原样转发、无业务 Key 返回 401、正确 Key 通过、SSE 首块在流结束前到达。
- 错误 TLS serverName 无法登录，错误 FRP Token 无法登录，合法 Token 请求 allowPorts 之外的 remotePort 被拒绝。
- 检查云端 Nginx 上游统一为 127.0.0.1:18000、FRP 业务端口回环绑定、TLS 强制与可信 CA 配置、接口路径白名单和连接上限的文件一致性。

上述隧道检查使用模拟后端，不包含真实 GPU 推理、公网线路或 Nginx 进程执行。验证完成后本地 FRP 测试进程均已停止。

GitHub 仓库首次发布检查：

- 增加可重复执行的 `scripts/validate.py`，对发布清单文件执行 SHA-256、Python/Shell/TOML 语法、敏感文件/凭证模式和个人本地路径检查。本地通过。
- 增加 `tests/test_local_guard.py` 的 8 项行为测试，覆盖无/错 Key、重复 Authorization Header、有效 Key 的 SSE 透传、替代推理及管理路由阻断、健康探针、HTTP 方法、lifespan/WebSocket 和错误配置拒绝启动。本地全部通过。
- 在临时目录再次校验 Compose，确认 Key 初始化不覆盖现有值、权限为 600；临时密钥已清除。
- 增加 PR 模板、贡献说明与 GitHub Actions 工作流。工作流使用只读权限，checkout 固定官方 v7.0.1 的提交 SHA，不持久化 Git 凭据，不下载模型、不运行 GPU 或部署公网服务。远端检查结果以对应 PR 的 Checks 为准。

2026-10-28 优化修复检查（PR #2）：

- 固定官方 vLLM 0.32.0 镜像 index digest，记录 Linux amd64/arm64 子清单；核对版本化 CLI 的生成上限、运行加排队请求限制、默认聊天模板参数和 SSE 保活参数。镜像身份来自 Registry 读取，不是本机拉取或 GPU 执行结果。
- 本地 38 项标准库单元测试通过：原有鉴权/白名单/SSE 8 项、模型清单 6 项、下载续传身份 3 项、启动策略 4 项、watchdog 7 项、离线包防误操作 5 项及压测统计 5 项。离线测试使用模拟 Docker 调用，只证明错误路径拒绝操作，不代表完成真实镜像构建或断网 GPU 验收。
- 发布清单增至 57 个文件，Python/Shell/TOML 语法、SHA-256、凭证与个人路径扫描通过。真实 Nginx/FRP 验证脚本和新文件均已纳入清单。
- 在临时目录用本机 Compose 5.2.0 校验生产配置和断网覆盖配置：回环发布、两卡预约、输出/队列默认值、四个缓存卷、断网项目无端口且禁止 pull；初始化重复运行不覆盖 Key，权限为 600。未启动模型容器。
- 使用真实 macOS FRP v0.71.1 再次通过严格配置、证书链/SAN、TLS 隧道 HTTP/SSE、错误 Token/证书名和 allowPorts 拒绝测试；后端仍为模拟服务。
- GitHub Linux CI 已通过真实 Nginx 对四份 HTTP/HTTPS 配置的 `nginx -t`，并通过校验过发行包的 Linux FRP 二进制隧道测试。Nginx 检查使用临时证书、高位回环端口和临时日志目录，不替代目标主机的实际域名/证书检查。最终提交的 Checks 以 [PR #2](https://github.com/Mingsync/qwen38-27b-frp-api/pull/2) 为准。
- 新增标准库 `benchmark.py` 记录实际 API 成功率、答案 TTFT、端到端 P50/P95 及 usage 吞吐；单元测试确认不把保活/思考计为答案、不把截断或错误流计为成功。当前没有双卡实测吞吐或固定用户数结论。

尚未在目标服务器执行：

- 目标机的 GPU 枚举、容器 GPU 注入检查，以及按已锁定 digest 实际拉取镜像；现有驱动/Toolkit 正常时不重新安装。Docker runtime 注册情况已由用户输出确认。
- `preflight.sh` 的 CUDA 运算、实际安装版本/CLI 参数检查。
- 双卡 NCCL all-reduce、FP8 模型加载与混合注意力内核执行。
- `verify.py` 对真实模型的 HTTP 验收、curl/Python SSE、长上下文和并发压测。
- Nginx 本机 `nginx -t`、证书配置、DNS/防火墙与业务客户端端到端测试。
- 峰值显存、TTFT、吞吐、长文精度、Key 轮换、主机重启恢复。
- 内核镜像实际构建、断网 GPU 生成、缓存导出/恢复、目标机断网复验，以及 watchdog 的真实 systemd 安装和故障恢复演练。
- FRP 在真实两端 Linux 的安装/systemd 运行、云安全组与出口策略、公网 DNS/ACME 签证书、云端 `nginx -t`、真实公网 SSE 与隧道断线恢复、云流量与延迟。交付包内没有真实域名证书、CA 私钥或隧道 Token。

验收时按 README 的顺序完成上述项目，并补记：GPU 型号及实测显存、驱动版本、镜像 digest、模型 revision、最终 `.env`（不含密钥）、业务负载与指标。显存规划与候选并发档位不能替代实测结果。
