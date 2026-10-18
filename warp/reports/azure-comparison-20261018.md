# Azure WARP 镜像对比检验报告

采集时间：2026-10-19T00:04:05+08:00（Asia/Shanghai）。
原生双架构构建与验证：[GitHub Actions](https://github.com/shadowofgost/auto-workflow/actions/runs/39141421668)。

## 结论

Azure 验收未通过，已恢复测试前镜像及冷备份数据；latest 未提升。原部署在测试前即无法提供代理，回滚不代表原有故障已修复。
本次双方出现 fwmark 规则安装权限错误。未增加权限进行反事实验证，因此只报告原参数下的实际失败，不声称已经验证加权限后的修复效果。

## 测试条件

Azure amd64，约 836MiB RAM、无 swap；每次仅运行一个 WARP 实例。保留原有 host 网络、
WARP_SLEEP=3、WARP_PROXY=TRUE、WARP_PORT=40000、许可证、数据路径、重启及健康检查参数。
许可证不写入报告；每阶段从同一冷备份恢复注册数据。双方统一使用 10MB×3 的 JSON 日志轮转。
对方适配入口使用同一监督器，只测原生 SOCKS5 代理，不启动 GOST、TUN 或 NAT，不增加网络管理权限。

## 实现差异

| 项目 | 升级后的自有镜像 | 对方当前镜像/源码 |
| --- | --- | --- |
| 基础系统 | Ubuntu 26.04 | Ubuntu 22.04 |
| 默认代理入口 | 官方原生 SOCKS5，40000 | GOST，1080；现有变量 WARP_PROXY/WARP_PORT 不由其入口解析 |
| 持久化启动 | 每次重设模式、端口、endpoint 并连接 | 已有注册时跳过首次注册和连接代码 |
| 进程与日志 | Python 监督、信号转发、精简过滤、qlog 默认关闭 | shell 启动 daemon，前台 GOST，保留 daemon 输出、默认关闭 qlog |
| WARP 版本参数 | 实际约束 APT 安装版本 | Dockerfile 的 WARP_VERSION 仅写入标签 |
| 架构 | 本次原生构建并检查 amd64/arm64 | Dockerfile 支持 amd64/arm64 |
| 用户 | root | warp 用户，具备免密码 sudo |

原始入口测试允许读取冷备份中已有的代理设置，因此端口存在与否不能单独证明其支持这些环境变量。
相同 WARP 版本号仍对应不同 Ubuntu 软件包和依赖，不把结果视为完全相同二进制的对照实验。

## 实测结果

| 阶段 | trace 成功 | 首字节 p50/p95（秒） | 总耗时 p50/p95（秒） | 下载成功 | 稳定性成功 | 阶段验收 |
| --- | --- | --- | --- | --- | --- | --- |
| own | 未执行 | —/— | —/— | 未执行 | 未执行 | 未通过 |
| upstream-unmodified | 未执行 | —/— | —/— | 未执行 | 未执行 | 未通过 |
| upstream-adapted | 未执行 | —/— | —/— | 未执行 | 未执行 | 未通过 |
| own-repeat | 未执行 | —/— | —/— | 未执行 | 未执行 | 未通过 |

## 镜像和诊断证据

### own

- 镜像：`shadowofgost/warp:sha-3233e6a838c9da5d2cd0aecd69384d33373740dd`；本地大小 871.0MiB。
- Digest：`shadowofgost/warp@sha256:e478131a25cff235266ed548d227a4e0f3bd3ee076f71931e0dcdeb348511db7`。
- WARP：`warp-cli 2026.8.2110.0; Build date: 2026-10-12 17:17:33.0 +00:00:00`；GOST：`未安装`。
- 源码：`3233e6a838c9da5d2cd0aecd69384d33373740dd`。
- 首次恢复健康：— 秒；自动重启次数：0。
- readiness_failed：`{"last_trace": {"ok": false, "proxy": true, "download": false, "returncode": 7, "ip": null, "warp": null, "http_code": 0, "ttfb": 0.0, "total": 0.000111, "bytes": 0, "bytes_per_second": 0, "error": "curl: (7) Failed to connect to cloudflare.com port 443 via 127.0.0.1 after 0 ms: Could not connect to server"}, "state": "running"}`。

启动诊断摘录：

```text
ERROR actor_connection::network: Reconnecting after failing to reinstall WARP fwmark policy rules err=Custom { kind: PermissionDenied, error: "IPv4 fwmark rule install: Operation not permitted (os error 1)" }
INFO Reusing existing WARP registration
INFO Configured mode=proxy port=40000 qlog=False
```

### upstream-unmodified

- 镜像：`caomingjun/warp:latest`；本地大小 983.0MiB。
- Digest：`caomingjun/warp@sha256:3bb9768a3cf0025aa93be7f83072771323afa9bbe8f3a8a95ffd0cb7ab9f1701`。
- WARP：`warp-cli 2026.8.2110.0; Build date: 2026-10-12 17:14:47.0 +00:00:00`；GOST：`2.12.0`。
- 源码：`bf774bae06b31fe50e7b8542558dc0ea20db679f`。
- 首次恢复健康：— 秒；自动重启次数：0。
- readiness_failed：`{"last_trace": {"ok": false, "proxy": true, "download": false, "returncode": 7, "ip": null, "warp": null, "http_code": 0, "ttfb": 0.0, "total": 0.000108, "bytes": 0, "bytes_per_second": 0, "error": "curl: (7) Failed to connect to cloudflare.com port 443 via 127.0.0.1 after 0 ms: Could not connect to server"}, "state": "running"}`。

启动诊断摘录：

```text
ERROR actor_connection::network: Reconnecting after failing to reinstall WARP fwmark policy rules err=Custom { kind: PermissionDenied, error: "IPv4 fwmark rule install: Operation not permitted (os error 1)" }
```

### upstream-adapted

- 镜像：`caomingjun/warp:latest`；本地大小 983.0MiB。
- Digest：`caomingjun/warp@sha256:3bb9768a3cf0025aa93be7f83072771323afa9bbe8f3a8a95ffd0cb7ab9f1701`。
- WARP：`warp-cli 2026.8.2110.0; Build date: 2026-10-12 17:14:47.0 +00:00:00`；GOST：`2.12.0`。
- 源码：`bf774bae06b31fe50e7b8542558dc0ea20db679f`。
- 首次恢复健康：— 秒；自动重启次数：0。
- readiness_failed：`{"last_trace": {"ok": false, "proxy": true, "download": false, "returncode": 7, "ip": null, "warp": null, "http_code": 0, "ttfb": 0.0, "total": 0.000113, "bytes": 0, "bytes_per_second": 0, "error": "curl: (7) Failed to connect to cloudflare.com port 443 via 127.0.0.1 after 0 ms: Could not connect to server"}, "state": "running"}`。

启动诊断摘录：

```text
INFO Starting WARP unknown
ERROR actor_connection::network: Reconnecting after failing to reinstall WARP fwmark policy rules err=Custom { kind: PermissionDenied, error: "IPv4 fwmark rule install: Operation not permitted (os error 1)" }
INFO Reusing existing WARP registration
INFO Configured mode=proxy port=40000 qlog=False
```

### own-repeat

- 镜像：`shadowofgost/warp:sha-3233e6a838c9da5d2cd0aecd69384d33373740dd`；本地大小 871.0MiB。
- Digest：`shadowofgost/warp@sha256:e478131a25cff235266ed548d227a4e0f3bd3ee076f71931e0dcdeb348511db7`。
- WARP：`2026.8.2110.0`；GOST：`未安装`。
- 源码：`3233e6a838c9da5d2cd0aecd69384d33373740dd`。
- 首次恢复健康：— 秒；自动重启次数：未取得。
- readiness_failed：`{"last_trace": {"ok": false, "proxy": true, "download": false, "returncode": 7, "ip": null, "warp": null, "http_code": 0, "ttfb": 0.0, "total": 0.000159, "bytes": 0, "bytes_per_second": 0, "error": "curl: (7) Failed to connect to cloudflare.com port 443 via 127.0.0.1 after 0 ms: Could not connect to server"}, "state": "running"}`。

## 最终部署及限制

- `2026-10-18T15:48:44.131835+00:00` baseline：`{"image": "sha256:0750627b07ba6dd8c5b7a119902bd42535af0f6ac8b24a7e5293f8b3b6535238", "state": "running", "health": "unhealthy", "restarts": 0, "log_config": {"Type": "json-file", "Config": {}}, "direct": {"ok": true, "proxy": false, "download": false, "returncode": 0, "ip": "20.25.137.229", "warp": "off", "http_code": 200, "ttfb": 0.049449, "total": 0.049489, "bytes": 217, "bytes_per_second": 4384, "error": ""}, "proxy": {"ok": false, "proxy": true, "download": false, "returncode": 7, "ip": null, "warp": null, "http_code": 0, "ttfb": 0.0, "total": 7.5e-05, "bytes": 0, "bytes_per_second": 0, "error": "curl: (7) Failed to connect to cloudflare.com port 443 via 127.0.0.1 after 0 ms: Could not connect to server"}}`
- `2026-10-18T15:58:20.664199+00:00` acceptance：`{"own": false, "upstream": false, "repeat": false}`
- `2026-10-18T15:58:34.352847+00:00` restored_original：`{"state": "running"}`

恢复核验：

- 原镜像一致：True；注册身份保留：True。
- Compose 已恢复：True；Watchtower 运行：True。
- 最终 WARP 健康状态：unhealthy；CAP_NET_ADMIN：False。

原始逐请求数据见同名 JSONL。计划在启动成功后执行 20 次请求、间隔 1 秒；稳定性窗口每 15 秒采样，共 40 次、持续 10 分钟。启动未通过时，后续性能、重启和稳定性项目不执行，不能据此得出性能排名。
下载为 3 次 1MiB 的同一 Cloudflare URL。失败样本保留，未用重试覆盖失败。
启动轮询在每阶段保留最后一次失败 trace，未逐次归档所有就绪轮询；表中 trace 指启动成功后才执行的性能批次。
Azure 结果只代表 amd64 和本次网络条件，不能推断 ARM 网络性能、GOST 性能或 OpenAI 等站点可用性。
Xray 路由未修改。两镜像客户端版本不同或适配后仍失败时，不把结果直接归因为代理实现的性能差异。
