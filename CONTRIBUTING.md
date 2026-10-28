# 更新部署方案

代码和配置通过功能分支提交，由 Pull Request 合并到 `main`。首次发布分支为 `qwen38-deploy`。

## 本地检查

Python 校验需要 3.11+，只使用标准库；不下载模型，不要求 GPU。

```bash
python3 scripts/validate.py
python3 -m unittest discover -s tests -v
bash init-config.sh
docker compose config --quiet
```

`init-config.sh` 只在本机创建被忽略的 `.env` 和测试/部署 Key，不覆盖已有配置。校验脚本扫描版本清单中的文件，不能代替人工核对待提交内容。

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
