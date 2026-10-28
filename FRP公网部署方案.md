# Qwen3.8 27B：通过云服务器与 FRP 提供公网 HTTPS API

本方案是在既有本地部署包上的公网访问扩展，日期 2026-10-26。保留内网服务器的 Ubuntu 26.04、595.84 驱动、Container Toolkit 1.20.1 和已注册的 nvidia runtime；不重新安装 GPU 环境。模型仍使用官方 FP8、vLLM 和现有 API Key。

**推荐结构：公网 HTTPS 在云端 Nginx 终止，FRP 将请求经校验证书的 TLS 隧道送回内网。云端 FRP 的业务转发端口和内网模型端口都只绑定回环地址。** 云服务器只做网络转发，不需要 GPU。

本包只生成配置和部署步骤，未登录或修改真实云服务器/内网服务器，未创建域名或申请真实证书。占位域名和 IP 必须替换。配置与本地隧道检查记录见 `VALIDATION.md`。

## 1. 工作原理与访问路径

```text
外部客户端
  │ HTTPS + Authorization: Bearer <模型 API Key>
  ▼
云服务器 Nginx：公网 TCP 443
  │ HTTP，仅本机回环
  ▼
云服务器 frps：127.0.0.1:18000
  ║ frpc 主动连云端 TCP 7000；TLS 加密 + 服务端证书验证 + FRP Token
  ║ 双向传输、TCP 多路复用
  ▼
内网 GPU 服务器 frpc：以 systemd 服务运行在宿主机
  │ HTTP，仅本机回环
  ▼
内网 Docker 发布端口：127.0.0.1:8000
  ▼
vLLM → 接口白名单 / 模型 API Key → 双 RTX 5090
```

你的理解正确：内网主动向具有公网地址的云服务器建立并维持连接，因此无需在内网路由器映射 8000 端口，也不要求内网有公网 IPv4。FRP 有控制会话、工作连接/复用流等机制，不能简单理解为所有 HTTP 请求在一个连接里串行执行。本包启用 TCP multiplexing；多个请求可并行通过隧道，但底层 TCP 链路丢包或断开仍会影响这些流。[FRP 原理](https://gofrp.org/en/docs/overview/)、[连接优化说明](https://gofrp.org/en/docs/features/common/network/network/)

云端是可信转发节点：Nginx 会解密用户 HTTPS，因此云端管理员可以接触请求和响应明文。本方案是“两段独立加密”，不是客户端到 GPU 进程的单一端到端 TLS。隧道断开时已进行的 SSE 不会自动从中断位置续传；客户端应明确处理失败。

## 2. 地址、端口与凭证约定

以下默认值在两端保持一致：

- API 域名：`llm.example.com`，A 记录指向云服务器公网 IPv4。
- FRP 域名：`frp.example.com`，A 记录也指向同一云服务器，用于连接地址和隧道证书名称。
- 云端 Nginx：443/TCP 对外提供 API；80/TCP 用于证书校验与跳转。
- 云端 frps：7000/TCP 接受内网 frpc 主动连接。
- 云端业务转发口：**127.0.0.1:18000**，不在公网监听。
- 内网模型服务：**127.0.0.1:8000**，frpc 与 Docker 在同一宿主机。
- 标准 Base URL：`https://llm.example.com/v1`。
- 可选业务前缀 Base URL：`https://llm.example.com/qwen/v1`。

如果服务器未配置公网 IPv6，先不要添加 AAAA 记录；本包公网监听配置按 IPv4 编写。内网出口地址可变也能工作，变化后由 frpc 重连，但云端 7000 的来源限制需相应调整。

两套凭证必须分开：

1. **FRP Token**：只给 frpc/frps，允许内网机器建立隧道，存于两端 `/etc/frp/frp_token`。
2. **模型 API Key**：只用于 API 访问，仍由内网 `/opt/qwen38/secrets/api_key` 配置；外部用户在 Authorization Header 携带。FRP Token 不能代替它。

FRP 采用 file tokenSource，不把 Token 写进 TOML 或启动参数。两端 auth scope 同时覆盖心跳与新工作连接。[FRP 鉴权配置](https://gofrp.org/en/docs/features/common/authentication/)

## 3. 防火墙与云端选型

云端安全组和主机防火墙：

- 443/TCP：允许业务客户端访问，能限定企业来源时优先限定。
- 80/TCP：使用 HTTP-01 自动签证书时开放；不转发明文推理。
- 7000/TCP：内网出口公网 IP 固定时，仅允许该 IP；出口不固定时可放宽来源，但保留强随机 Token、TLS 和证书校验。
- SSH 管理端口：仅允许运维来源。
- **不开放 18000、8000 或 FRP Dashboard 端口；本包不启用 Dashboard。无需开放 UDP/KCP/QUIC 端口。**

内网只需允许向云端 7000/TCP 出站及 DNS 解析，不设置入站端口映射。若内网必须通过企业 HTTP 代理上网，须另行配置支持长连接的出口路径；本包默认内网能直接访问云端 TCP 7000。

云主机优先选择线路稳定、与内网实际 RTT 较低且带宽足够的实例，无需 GPU。FRP 不提高模型计算并发；云带宽、云流量额度、内网上行、丢包和延迟都要纳入验收。纯文本通常不需要很大的带宽，但长提示词上传与多路 SSE 仍需实际测量，不能只看云服务器标称带宽。

## 4. 文件与固定版本

新增配置位于 `frp/`：

```text
frps.toml                       云服务器配置
frpc.toml                       内网 GPU 宿主机配置
frps.service / frpc.service      两端 systemd 服务
install-frp.sh                   固定版本安装，校验官方 SHA-256
make-frp-pki.sh                  生成隧道私有 CA、服务端证书及随机 Token
cloud-nginx-bootstrap.conf       只开放 ACME 校验的 HTTP 引导配置
cloud-nginx-https.conf           云端 HTTPS、路径白名单、入口连接上限
reload-nginx.sh                  公网证书续期成功后的 reload hook
```

两端固定 FRP **v0.71.1**，使用 TOML；不要混用旧 INI 示例。安装脚本支持 Linux amd64/arm64，内置对应官方发行包 SHA-256；云服务器可用不同于 GPU 主机的 CPU 架构。[FRP 官方 v0.71.1 发行页](https://github.com/fatedier/frp/releases/tag/v0.71.1)

## 5. 前置：先确认内网模型可用

在 GPU 主机 `/opt/qwen38/.env` 中保持：

```dotenv
BIND_HOST=127.0.0.1
API_PORT=8000
API_BASE_URL=https://llm.example.com/v1
```

API_BASE_URL 只是客户端配置记录，不会改变 Docker 绑定或建立 DNS。使用实际域名替换示例。应用 `.env` 修改后，先在 GPU 主机验收：

```bash
cd /opt/qwen38
docker compose up -d --force-recreate qwen
curl --fail http://127.0.0.1:8000/health
python3 verify.py --base-url http://127.0.0.1:8000/v1
```

**frpc 直接运行在 GPU Linux 宿主机**，其 localIP=127.0.0.1 才能访问这个发布端口。若把 frpc 放到独立容器，它的 127.0.0.1 会指向该容器自身，不能直接照抄本配置。无需在 GPU 主机再叠一层 Nginx；本公网方案的 Nginx 部署在云端，与 README 第 9 节的同机方案二选一。

## 6. 生成隧道证书与 Token

在受信任的管理员工作目录执行，输出目录放在交付包之外，且必须尚不存在：

```bash
# 工作目录中已经有解压后的配置包
bash /路径/Qwen3.8模型配置方案/frp/make-frp-pki.sh \
  frp.example.com ./frp-pki-private
```

脚本需要支持 `req -addext` 的 OpenSSL（Ubuntu 26.04 的 OpenSSL 可用）。它创建私有 CA、带 `DNS:frp.example.com` SAN 的服务端证书及随机 FRP Token，不打印私钥和 Token。

通过受信任 SSH/SCP 分发到权限受限的临时目录，例如两台服务器的 `/opt/frp-bootstrap`：

- 云端接收：`server.crt`、`server.key`、`frp_token`。
- 内网 GPU 主机接收：`ca.crt`、同一个 `frp_token`。
- **`ca.key` 不上传云端或 GPU 服务器，离线保管**；公开根证书 `ca.crt` 应经可信渠道核对/分发。

如果 `/opt/frp-bootstrap` 需要预先建立，在每台服务器执行：

```bash
sudo install -d -m 700 -o "$USER" -g "$(id -gn)" /opt/frp-bootstrap
```

在管理员机器上将对应文件用 SCP 发送到该目录。不要通过公开 HTTP 下载 Token 或把它写进工单/截图。公网客户端不需要安装这个私有 CA；它只验证 Nginx 的公开 HTTPS 证书。

本包明确给 frpc 配置 trustedCaFile 和 serverName；仅设置 TLS enable 并不自动保证验证服务端身份。frps 强制 TLS，另外用 Token 验证客户端，本方案不额外要求客户端证书。[FRP TLS 证书校验说明](https://gofrp.org/en/docs/features/common/network/network-tls/)

## 7. 两台主机安装 FRP 与服务账号

将配置包复制到两台主机的 `/opt/qwen38`（云端只复制配置，不需要 GPU 权重）。若已有旧 FRP 服务，先备份旧二进制链接、配置和服务定义，再安排维护窗口切换。

云服务器执行：

```bash
cd /opt/qwen38
bash frp/install-frp.sh frps
```

内网 GPU 主机执行：

```bash
cd /opt/qwen38
bash frp/install-frp.sh frpc
```

脚本只安装，不启动服务。GitHub 无法访问时，可从可信联网机器下载相同架构的官方发行包，按脚本中的 SHA-256 核验后离线传入；不要使用不明重打包二进制。

以下账号与目录准备步骤在**两台主机分别执行**：

```bash
getent group frp >/dev/null || sudo groupadd --system frp
id -u frp >/dev/null 2>&1 || sudo useradd --system --gid frp \
  --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin frp
sudo install -d -o root -g frp -m 750 /etc/frp /etc/frp/pki
```

systemd 使用独立低权限账号，日志进入 journal，失败后自动重启，随系统开机启动。[FRP systemd 说明](https://gofrp.org/en/docs/setup/systemd/)

## 8. 云端配置并启动 frps

在云服务器执行，假设分发文件位于 `/opt/frp-bootstrap`：

```bash
cd /opt/qwen38
sudo install -o root -g frp -m 640 frp/frps.toml /etc/frp/frps.toml
sudo install -o frp -g frp -m 600 /opt/frp-bootstrap/frp_token /etc/frp/frp_token
sudo install -o root -g frp -m 640 /opt/frp-bootstrap/server.crt /etc/frp/pki/server.crt
sudo install -o root -g frp -m 640 /opt/frp-bootstrap/server.key /etc/frp/pki/server.key
sudo -u frp /usr/local/bin/frps verify -c /etc/frp/frps.toml
sudo install -m 644 frp/frps.service /etc/systemd/system/frps.service
sudo systemctl daemon-reload
sudo systemctl enable --now frps
sudo systemctl status frps --no-pager
sudo journalctl -u frps -n 80 --no-pager
```

默认控制入口监听 0.0.0.0:7000，必须有云端安全组/防火墙规则配合；业务转发口由 `proxyBindAddr=127.0.0.1` 固定在回环地址，并限制允许的 remotePort 仅 18000。即使客户端请求其他端口，服务端也应拒绝。[FRP 服务端配置](https://gofrp.org/en/docs/reference/server-configures/)

业务端口 18000 通常在 frpc 登录并注册代理成功后才开始监听，不能在 frpc 启动前仅因没有 18000 就判定 frps 失败。

## 9. 内网配置并启动 frpc

在 GPU 主机执行：

```bash
cd /opt/qwen38
sudo install -o root -g frp -m 640 frp/frpc.toml /etc/frp/frpc.toml
sudo install -o frp -g frp -m 600 /opt/frp-bootstrap/frp_token /etc/frp/frp_token
sudo install -o root -g frp -m 640 /opt/frp-bootstrap/ca.crt /etc/frp/pki/ca.crt
sudo nano /etc/frp/frpc.toml
# 将 serverAddr 和 transport.tls.serverName 中的 frp.example.com 改成实际域名
sudo -u frp /usr/local/bin/frpc verify -c /etc/frp/frpc.toml
sudo install -m 644 frp/frpc.service /etc/systemd/system/frpc.service
sudo systemctl daemon-reload
sudo systemctl enable --now frpc
sudo systemctl status frpc --no-pager
sudo journalctl -u frpc -n 80 --no-pager
```

服务端证书 SAN、client 的 serverName 必须一致。serverAddr 也可以填云端固定 IP，但 serverName 仍填写证书中的 DNS 名称；使用受信私有 CA 时不要求该证书由公网 CA 签发。

若本地模型发布端口不是 8000，修改 frpc 的 localPort；若云端转发端口不是 18000，必须同步修改 frps allowPorts、frpc remotePort、云端 Nginx upstream。检查日志应看到登录成功和 `qwen38-api` 代理启动成功。[FRP 客户端配置](https://gofrp.org/en/docs/reference/client-configures/)

在云服务器检查：

```bash
sudo ss -lntp | grep -E ':7000|:18000'
# 不带模型 Key 应返回401，说明已穿过隧道到达鉴权边界：
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18000/v1/models
```

预期 7000 是公网监听，18000 **只显示 127.0.0.1:18000**；models 返回 401。若它返回 200，先停止对外启用，排查内网模型 Key 和白名单配置。若返回 502/连接失败，先检查 frpc 到本地 8000，暂不排查证书签发。

## 10. 云端 Nginx HTTPS

### 10.1 HTTP 引导与签发证书

确认 llm.example.com 已解析到云端且 80/TCP 可用于 ACME 校验，在云服务器执行：

```bash
sudo apt-get update
sudo apt-get install -y nginx certbot
sudo install -d -m 755 /var/www/certbot /etc/nginx/snippets
cd /opt/qwen38
sudo install -m 644 nginx/qwen38-proxy.conf /etc/nginx/snippets/qwen38-proxy.conf
sudo install -m 644 frp/cloud-nginx-bootstrap.conf /etc/nginx/conf.d/qwen38-frp.conf
sudo nano /etc/nginx/conf.d/qwen38-frp.conf
# 将 llm.example.com 换成实际 API 域名
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
sudo certbot certonly --webroot -w /var/www/certbot \
  -d llm.example.com --email admin@example.com --agree-tos --non-interactive
```

替换实际域名和联系邮箱后再执行证书命令。若是已有证书，可跳过签发并使用实际路径；若 80 端口无法开放，可选 DNS-01 方式，但应采用可自动续期的 DNS 集成。HTTP 引导配置只服务 ACME，不在无 TLS 时转发模型请求。[Certbot webroot 与续期说明](https://eff-certbot.readthedocs.io/en/stable/using.html)

### 10.2 安装正式 HTTPS 配置

证书文件存在后，在云服务器执行：

```bash
cd /opt/qwen38
sudo install -m 644 frp/cloud-nginx-https.conf /etc/nginx/conf.d/qwen38-frp.conf
sudo nano /etc/nginx/conf.d/qwen38-frp.conf
# 替换域名、证书路径，核对后端始终为127.0.0.1:18000
sudo nginx -t
sudo systemctl reload nginx
sudo install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
sudo install -m 750 frp/reload-nginx.sh \
  /etc/letsencrypt/renewal-hooks/deploy/reload-qwen-nginx.sh
sudo systemctl enable --now certbot.timer
sudo certbot renew --dry-run
```

该配置应替换同名引导文件；不要同时启用旧版同域名的配置，避免重复 server_name。保留 ACME 路径便于后续续期，其余 HTTP 请求跳转到 HTTPS。不要用 `curl -k` 绕过正式客户端证书验证。

云端 Nginx 配置特点：

- 仅允许 models、chat completions 两个 API 路径及其 `/qwen` 前缀形式，其余 404；不公开管理接口、health、metrics。
- 保留 Authorization；模型 Key 仍由内网服务校验，云端配置无需保存同一业务 Key。
- 关闭代理缓冲和缓存，保持 SSE 逐块返回，读写空闲超时 600 秒；关闭自动重试，避免长生成被透明重发。[Nginx 代理配置](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)
- 同一 API 域名在两个 chat 路径上合计最多 **8 个正在处理的 HTTP 请求**，超限返回 429；这是公网入口保护，**不是 GPU 同时生成 8 路**。当前 GPU MAX_NUM_SEQS=2，另外的请求可能排队；后端 MAX_NUM_QUEUED_REQS=8 还限制运行加等待请求，后端超限返回 503。没有按客户端 IP 限制，以免同一企业 NAT 下的多人被误伤。[Nginx 连接限制](https://nginx.org/en/docs/http/ngx_http_limit_conn_module.html)
- 请求体默认上限 2MB；超长中文历史可能更大，遇到合法 413 时按实际 token 上限与 JSON 字节数调整，不盲目取消限制。

需要更严格的用户级限额、审计或独立 Key 时，在此入口增加支持对应能力的 API 网关。共享业务 Key、FRP Token 和 Nginx 全局连接限制均不是按用户计费体系。

## 11. 公网调用与验收

在能访问公网域名的可信客户端设置模型 Key，再验证：

```bash
export OPENAI_BASE_URL=https://llm.example.com/v1
read -r -s -p 'Model API key: ' OPENAI_API_KEY; printf '\n'
export OPENAI_API_KEY

curl --fail-with-body "$OPENAI_BASE_URL/models" \
  -H "Authorization: Bearer $OPENAI_API_KEY"

curl --no-buffer --fail-with-body "$OPENAI_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary @- <<'JSON'
{
  "model": "qwen3.8-27b",
  "messages": [{"role":"user","content":"请分三点介绍本地大模型的用途。"}],
  "max_tokens": 256,
  "chat_template_kwargs": {"enable_thinking": false},
  "stream": true
}
JSON

python3 verify.py --base-url "$OPENAI_BASE_URL"
```

这里 read 的交互命令适用于 Bash；不要把模型 Key 作为 URL 查询参数。前缀模式只需把 Base URL 改为 `https://llm.example.com/qwen/v1`，后端 model 别名保持不变。

必须从实际外部业务网络完成：正确 Key 成功、无/错 Key 401、旁路/管理接口 404、连续 SSE 正常，以及并发与长提示压测。云端 18000 从公网不应可连接，内网也无 8000 的路由映射。

Nginx 在转发域名请求时会记录真实客户端连接地址；frpc 到 vLLM 的 TCP 来源是本机回环，因此不要用 vLLM 看到的 TCP IP 统计用户。若启用可信代理 IP 解析，只信任受控的云端链路，不直接相信客户端自带的 X-Forwarded-For。

## 12. 开机恢复、轮换与常见故障

两端状态：

```bash
# 云端
sudo systemctl status frps nginx --no-pager
sudo journalctl -u frps -n 100 --no-pager
# GPU主机
sudo systemctl status frpc --no-pager
sudo journalctl -u frpc -n 100 --no-pager
cd /opt/qwen38 && docker compose ps
```

云端启动 frps/Nginx，内网启动 Docker/frpc；模型加载完成前可能短暂不可用，frpc 的长连接重试不等于模型已就绪。本包 loginFailExit=false 允许启动时云端不可达后继续重试，systemd 负责进程异常恢复。

常见问题：

- **7000 连接超时**：核对 DNS、云安全组、云防火墙、内网出站策略及 `frps` 是否监听；无需改内网路由器端口映射。
- **token 不匹配/权限拒绝**：两端必须分发同一个 FRP Token；用 `sudo -u frp ... verify` 检查文件可读。不要把模型 API Key 填进 FRP 配置。
- **x509 unknown authority/名称错误**：检查 GPU 主机 ca.crt 是否匹配服务端签发 CA、serverName 是否出现在证书 SAN、两端时钟是否正确。不要删除 trustedCaFile 来掩盖问题。
- **proxy name/port already used**：检查云端是否已有旧 frpc 注册相同 `qwen38-api` 或端口冲突，清理经确认的旧服务后再启动。
- **remote port not allowed**：frpc remotePort 必须与 frps allowPorts 一致；本包只允许 18000。
- **本地 8000 正常，云端 18000 失败**：先看 frpc 代理日志，确认 frpc 在 GPU 宿主机而不是独立容器中，localPort 对应 Docker 的宿主发布端口。
- **云端 18000 正常，公网 502/504**：核对云端 Nginx 代理端口、隧道连接状态、GPU 队列和超时。先定位内网模型耗时，不能靠不断放大超时解决过载。
- **公网 401**：模型 Key 或 Authorization Header 有问题；FRP 登录成功只表示隧道凭证正确。
- **公网 429**：Nginx 入口已有 8 个处理中的对话请求，按业务退避/排队；增加此值不提高 GPU 并发。
- **SSE 成批返回/中途断开**：检查云 Nginx、额外 CDN/WAF 的缓冲和流式超时、内网上行丢包、连接空闲限制。先直连云源站验证，再叠加 CDN；客户端不自动重复提交可能产生副作用的长请求。

公网 HTTPS 证书由 Certbot 续期；FRP 私有证书是另一套生命周期。本包隧道叶子证书 397 天，提前检查：

```bash
openssl x509 -in /etc/frp/pki/server.crt -noout -dates
openssl x509 -checkend 2592000 -noout -in /etc/frp/pki/server.crt
```

隧道证书将到期时，由离线 CA 签发同 SAN 的新叶子证书，保留现有 ca.crt 和 FRP Token，替换云端 server.crt/server.key 后重启 frps。不要简单重跑生成脚本替换 CA，否则客户端信任也要更新。FRP 重启会中断现有流，应安排维护窗口。

模型 API Key 轮换只涉及本地模型和业务客户端；FRP Token 轮换需要两端同步文件并重启服务，均不能视为无中断热更新。部署完成、备份确认后清理两台服务器上的 bootstrap 私钥/Token 副本，正式文件只留在 `/etc/frp` 的受限目录，离线 CA 私钥继续离线保管。

最终公网验收通过后记录真实 API URL、FRP 版本、两端服务状态、公网证书到期时间、隧道证书到期时间、验证日期与压测结果。默认模型上下文与并发仍为 **每请求 16K（含输入与输出）、同时生成 2 路、运行加等待最多 8 个请求**，每序列输出上限 1024，默认关闭思考，空闲 SSE 每 15 秒保活；共享 Key 不能作为每用户配额，保活也不等于请求总时长预算。需要 4 路/更长上下文时按主方案逐档压测。
