# 更新部署方案

代码和配置通过功能分支提交，由 Pull Request 合并到 `main`。首次发布分支为 `qwen38-deploy`。

## 提交与 PR 规范

按功能拆成细粒度提交，一个 commit 只处理一件事，遵循 Conventional Commits：`fix(api): bound generation output`、`feat(ops): add recovery timer`、`test(model): cover manifest mismatch`、`docs(deploy): explain migration`。功能、测试和独立文档变更分别提交；与该变更对应的 `SHA256SUMS` 同步更新，不单独留下失效清单。

开始前读取远端分支，保留他人的修改；不通过 force push 覆盖共享历史。已公开的问题用后续修复提交更正。使用 PR 模板说明最终实现、迁移与验证边界，功能开发和测试进展可在 PR 的 Conversation 使用 Add a comment 同步。合并使用 merge commit，保留细粒度提交历史。

## 本地检查

Python 校验需要 3.11+，只使用标准库；不下载模型，不要求 GPU。

```bash
python3 scripts/validate.py
python3 -m unittest discover -s tests -v
bash init-config.sh
docker compose config --quiet
docker compose -p qwen38-offline-check -f compose.yaml -f offline/compose.verify.yaml config --quiet
```

`init-config.sh` 只在本机创建被忽略的 `.env` 和测试/部署 Key，不覆盖已有配置。校验脚本扫描版本清单中的文件，不能代替人工核对待提交内容。

Linux CI 还会安装真实 Nginx，并下载官方 SHA-256 校验后的 FRP v0.71.1 二进制。可在准备好相应依赖后手动复现：

```bash
python3 scripts/check_nginx.py
python3 tests/integration/frp_tunnel.py --bin-dir /可信目录/frp_0.71.1_linux_amd64
```

Nginx 测试使用临时证书、日志目录及高位回环端口，检查仓库中四份配置；FRP 测试验证实际 TLS 隧道、鉴权透传、SSE、错误 Token/证书名及不允许的远端端口。两者都不要求 GPU，不修改生产服务。macOS 复现时使用同版本 Darwin 二进制和支持 SAN 的 OpenSSL 3，可通过 `OPENSSL_BIN` 指定路径。

## 更新校验清单

先检查 `git diff` 和 `git status`，确认只包含应该公开的源文件，再暂存并更新 SHA256SUMS：

```bash
git add README.md compose.yaml
# 将上面的文件名替换/补充为本次实际变更；不要暂存实际密钥。
python3 - <<'PY'
import hashlib
from pathlib import Path
import subprocess
paths = subprocess.check_output(['git', 'ls-files', '-z']).decode().split('\0')
records = []
for name in sorted(filter(None, paths)):
    if name == 'SHA256SUMS':
        continue
    records.append(hashlib.sha256(Path(name).read_bytes()).hexdigest() + '  ' + name + '\n')
Path('SHA256SUMS').write_text(''.join(records))
PY
git add SHA256SUMS
python3 scripts/validate.py
```

使用仓库提供的 PR 模板填写背景、变更、实际验证、部署影响、风险和回滚方式。GitHub Actions 校验通过后再合并。

模型加载、NCCL、显存、吞吐、公网 DNS/证书和真实云端链路仍须在目标服务器验收。更新镜像/模型/FRP 版本时同时核对官方来源与校验值，不把本地模拟测试描述为双卡推理实测。
