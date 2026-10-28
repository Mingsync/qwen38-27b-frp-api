# 健康检查、告警与有限恢复

Docker 的 restart policy 不会仅因 unhealthy 自动重启。本目录提供宿主机 systemd timer，模型容器不挂载 Docker socket。默认 CLI 是 dry-run，安装并启用下列服务后才允许恢复。

```bash
cd /opt/qwen38
sudo python3 ops/watchdog.py --project-dir /opt/qwen38
sudo install -m 644 ops/qwen38-watchdog.service /etc/systemd/system/
sudo install -m 644 ops/qwen38-watchdog.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now qwen38-watchdog.timer
```

默认每分钟检查一次，只处理运行中且 Docker 已标为 unhealthy 的容器：连续 3 次观察后才重启，两次尝试至少相隔 30 分钟，每小时最多 2 次。starting、healthy、已手动停止、缺失服务均不会被启动/重启；达到预算时以退出码 2 告警，不无限重启。状态保存在 `/var/lib/qwen38-watchdog`，跨检查保留预算。此探针不等于真实生成质量检查。

维护前暂停，维护结束恢复：

```bash
sudo install -d -m 700 /var/lib/qwen38-watchdog
sudo touch /var/lib/qwen38-watchdog/disabled
sudo systemctl stop qwen38-watchdog.timer
# 完成维护、确认模型可用后：
sudo rm /var/lib/qwen38-watchdog/disabled
sudo systemctl start qwen38-watchdog.timer
sudo journalctl -u qwen38-watchdog.service -n 80 --no-pager
```

把 service failed、`alert-budget-exhausted`、连续 5xx、队列与 GPU/Xid 纳入已有监控告警。可定期在可信客户端跑短生成 canary，但不要用频繁的大段生成挤占业务并发。服务不发送邮件或调用外部通知，通知渠道由运维系统配置。
