# Azure WARP NET_ADMIN 双镜像检验报告

采集时间：2026-10-25T19:33:05+08:00（Asia/Shanghai）。
原生双架构构建与验证：[GitHub Actions](https://github.com/shadowofgost/auto-workflow/actions/runs/40105332657)。

## 结论

测试未完成或在部署前停止，不能宣称验收通过。
该轮在读取 Watchtower 状态时发生 Docker 管理命令超时，尚未停止或替换容器。独立看护未记录宿主网络回归；后续完整重测结果见 [最终报告](azure-netadmin-20261025.md)。本文件保留首次失败证据。
权限、网络影响和代理可用性均按本次样本报告；不预先保证添加 NET_ADMIN 一定成功。

## 测试条件

Azure amd64，约 836MiB RAM、无 swap；每次仅运行一个 WARP 实例。保留原有 host 网络、
WARP_SLEEP=3、WARP_PROXY=TRUE、WARP_PORT=40000、许可证、数据路径、重启及健康检查参数。
许可证不写入报告；每阶段从同一冷备份恢复注册数据。双方统一使用 10MB×3 的 JSON 日志轮转。
本轮预检未进入正式阶段，没有成功的权限对照或代理验收结果；下面部署条件是原定测试条件。
对方适配入口使用同一监督器，只测原生 SOCKS5 代理，不启动 GOST 或 NAT。
对方原始入口限制 GOST 到 127.0.0.1:1080 并关闭 NAT，属于受约束原始入口检查，不能称为完全未经参数调整。

## 实现差异

| 项目 | 升级后的自有镜像 | 对方当前镜像/源码 |
| --- | --- | --- |
| 基础系统 | Ubuntu 26.04 | Ubuntu 22.04 |
| 默认代理入口 | 官方原生 SOCKS5，40000 | GOST，1080；现有变量 WARP_PROXY/WARP_PORT 不由其入口解析 |
| 持久化启动 | 每次重设模式、端口、endpoint 并连接 | 已有注册时跳过首次注册和连接代码 |
| 进程与日志 | Python 监督、信号转发、精简过滤、qlog 默认关闭 | shell 启动 daemon，前台 GOST，保留 daemon 输出、默认关闭 qlog |
| 架构 | 本次原生构建并检查 amd64/arm64 | Dockerfile 支持 amd64/arm64 |
| 用户 | root | warp 用户，具备免密码 sudo |

原始入口允许读取冷备份中已有的代理设置，因此端口存在与否不能单独证明其支持这些环境变量。
相同 WARP 版本号仍对应不同 Ubuntu 软件包和依赖，不把结果视为完全相同二进制的对照实验。

## 实测结果

| 阶段 | NET_ADMIN | trace 成功 | 首字节 p50/p95（秒） | 总耗时 p50/p95（秒） | 下载成功 | 稳定性成功 | 阶段验收 |
| --- | --- | --- | --- | --- | --- | --- | --- |

## 镜像和诊断证据

## 宿主网络检查

记录 0 次，其中回归记录 0 次。
## 最终部署及限制

- `2026-10-25T10:46:18.382676+00:00` baseline：`{"image": "sha256:0750627b07ba6dd8c5b7a119902bd42535af0f6ac8b24a7e5293f8b3b6535238", "state": "running", "health": "unhealthy", "restarts": 1, "log_config": {"Type": "json-file", "Config": {}}, "direct": {"ok": true, "proxy": false, "download": false, "returncode": 0, "ip": "20.25.137.229", "warp": "off", "http_code": 200, "ttfb": 0.035727, "total": 0.035767, "bytes": 216, "bytes_per_second": 6039, "error": ""}, "proxy": {"ok": false, "proxy": true, "download": false, "returncode": 7, "ip": null, "warp": null, "http_code": 0, "ttfb": 0.0, "total": 0.000424, "bytes": 0, "bytes_per_second": 0, "error": "curl: (7) Failed to connect to cloudflare.com port 443 via 127.0.0.1 after 0 ms: Could not connect to server"}}`
- `2026-10-25T10:47:17.350340+00:00` services_baseline：`{"ssh": true, "nginx": "200", "xray": {"probe": "live_xray_vless_ws", "ip": "20.25.137.229", "warp": "off", "gateway": "off", "colo": "LAX", "loc": "US", "seconds": 2.92}}`
- `2026-10-25T10:47:38.570635+00:00` fatal：`{"error": "Command '('docker', 'inspect', 'watchtower')' timed out after 20 seconds"}`
- `2026-10-25T10:50:06.727382+00:00` watchdog_abort：`{"controller_alive": false, "heartbeat_stale": false, "problems": [], "deadline": false}`

原始逐请求数据见同名 JSONL。计划在启动成功后执行 20 次请求、间隔 1 秒；稳定性窗口每 15 秒采样，共 40 次、持续 10 分钟。启动未通过时，后续性能、重启和稳定性项目不执行，不能据此得出性能排名。
下载为 3 次 1MiB 的同一 Cloudflare URL。失败样本保留，未用重试覆盖失败。
启动轮询逐次保留 readiness_probe；表中 trace 指启动成功后执行的性能批次。耗时分位数仅统计成功请求，失败样本保留。
Azure 结果只代表 amd64 和本次网络条件，不能推断 ARM 网络性能、GOST 性能或 OpenAI 等站点可用性。
Xray 路由未修改。两镜像客户端版本不同或适配后仍失败时，不把结果直接归因为代理实现的性能差异。
