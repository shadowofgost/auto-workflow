# Azure WARP 镜像对比检验报告

采集时间：2026-10-18T23:48:24+08:00（Asia/Shanghai）。
原生双架构构建与验证：[GitHub Actions](https://github.com/shadowofgost/auto-workflow/actions/runs/39140362915)。

## 测试条件

Azure amd64，约 836MiB RAM、无 swap；每次仅运行一个 WARP 实例。保留原有 host 网络、
WARP_SLEEP=3、WARP_PROXY=TRUE、WARP_PORT=40000、许可证、数据路径、重启及健康检查参数。
许可证不写入报告；每阶段从同一冷备份恢复注册数据。双方统一使用 10MB×3 的 JSON 日志轮转。
对方适配入口使用同一监督器，只测原生 SOCKS5 代理，不启动 GOST、TUN 或 NAT，不增加网络管理权限。

## 实测结果

| 阶段 | trace 成功 | 首字节 p50/p95（秒） | 总耗时 p50/p95（秒） | 下载成功 | 稳定性成功 | 阶段验收 |
| --- | --- | --- | --- | --- | --- | --- |
| own | 0/0 | —/— | —/— | 0/0 | 0/0 | 未完成 |

## 镜像和诊断证据

### own

- 镜像：`shadowofgost/warp:sha-6faebbc339d29aeadc4482da65772a008ce2173f`；本地大小 871.0MiB。
- Digest：`shadowofgost/warp@sha256:8ac4e37273bb72ac4b4fbe50b29c9c607267200ecac269b9fe6b6c15588be530`。
- WARP：`2026.8.2110.0`；GOST：`未安装`。
- 源码：`6faebbc339d29aeadc4482da65772a008ce2173f`。
- 首次恢复健康：— 秒；自动重启次数：未取得。

## 最终部署及限制

- `2026-10-18T15:31:56.395447+00:00` baseline：`{"image": "sha256:0750627b07ba6dd8c5b7a119902bd42535af0f6ac8b24a7e5293f8b3b6535238", "state": "running", "health": "unhealthy", "restarts": 6, "log_config": {"Type": "json-file", "Config": {}}, "direct": {"ok": true, "proxy": false, "download": false, "returncode": 0, "ip": "20.25.137.229", "warp": "off", "http_code": 200, "ttfb": 0.205403, "total": 0.205537, "bytes": 212, "bytes_per_second": 1031, "error": ""}, "proxy": {"ok": false, "proxy": true, "download": false, "returncode": 7, "ip": null, "warp": null, "http_code": 0, "ttfb": 0.0, "total": 0.000113, "bytes": 0, "bytes_per_second": 0, "error": "curl: (7) Failed to connect to cloudflare.com port 443 via 127.0.0.1 after 0 ms: Could not connect to server"}}`
- `2026-10-18T15:36:25.456712+00:00` fatal：`{"error": "Comparison terminated"}`
- `2026-10-18T15:36:28.262741+00:00` restored_original：`{"state": "running"}`

原始逐请求数据见同名 JSONL。20 次请求间隔 1 秒，稳定性窗口每 15 秒采样，共 40 次、持续 10 分钟；
下载为 3 次 1MiB 的同一 Cloudflare URL。失败样本保留，未用重试覆盖失败。
Azure 结果只代表 amd64 和本次网络条件，不能推断 ARM 网络性能、GOST 性能或 OpenAI 等站点可用性。
Xray 路由未修改。两镜像客户端版本不同或适配后仍失败时，不把结果直接归因为代理实现的性能差异。
