# Azure WARP NET_ADMIN 双镜像检验报告

采集时间：2026-10-25T20:33:15+08:00（Asia/Shanghai）。
原候选双架构构建与验证：[GitHub Actions](https://github.com/shadowofgost/auto-workflow/actions/runs/40105332657)；本轮复用镜像。

## 结论

新镜像已通过 Azure 验收并部署。
同一自有镜像的无 NET_ADMIN 对照失败，添加 NET_ADMIN 后通过全部验收；两个阶段的实际 daemon CapEff 只相差 NET_ADMIN 对应的位。
权限、网络影响和代理可用性均按本次样本报告；不预先保证添加 NET_ADMIN 一定成功。

## 测试条件

Azure amd64，约 836MiB RAM、无 swap；每次仅运行一个 WARP 实例。保留原有 host 网络、
WARP_SLEEP=3、WARP_PROXY=TRUE、WARP_PORT=40000、许可证、数据路径、重启及健康检查参数。
许可证不写入报告；每阶段从同一冷备份恢复注册数据。双方统一使用 10MB×3 的 JSON 日志轮转。
各阶段实际 NET_ADMIN 配置参见表格及 daemon CapEff；无权限对照单独标注，不使用 privileged 或额外 TUN 挂载。
对方适配入口使用同一监督器，只测原生 SOCKS5 代理，不启动 GOST 或 NAT。
对方原始入口限制 GOST 到 127.0.0.1:1080 并关闭 NAT，属于受约束原始入口检查，不能称为完全未经参数调整。

## 实现差异

| 项目 | 升级后的自有镜像 | 对方当前镜像/源码 |
| --- | --- | --- |
| 基础系统 | Ubuntu 26.04 | Ubuntu 22.04 |
| 默认代理入口 | 官方原生 SOCKS5，40000 | GOST，1080；现有变量 WARP_PROXY/WARP_PORT 不由其入口解析 |
| 持久化启动 | 每次重设模式、端口、endpoint 并连接 | 已有注册时跳过首次注册和连接代码 |
| 进程与日志 | Python 监督、信号转发、精简过滤、qlog 默认关闭 | shell 启动 daemon，前台 GOST，保留 daemon 输出、默认关闭 qlog |
| 架构 | 原候选已原生检查 amd64/arm64，本轮复用 | Dockerfile 支持 amd64/arm64 |
| 用户 | root | warp 用户，具备免密码 sudo |

原始入口允许读取冷备份中已有的代理设置，因此端口存在与否不能单独证明其支持这些环境变量。
相同 WARP 版本号仍对应不同 Ubuntu 软件包和依赖，不把结果视为完全相同二进制的对照实验。

## 实测结果

| 阶段 | NET_ADMIN | trace 成功 | 首字节 p50/p95（秒） | 总耗时 p50/p95（秒） | 下载成功 | 稳定性成功 | 阶段验收 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| own-without-net-admin | False | 未执行 | —/— | —/— | 未执行 | 未执行 | 无权限对照失败 |
| own | True | 20/20 | 0.037/0.160 | 0.037/0.160 | 3/3 | 40/40 | 通过 |
| upstream-stock-constrained | True | 未执行 | —/— | —/— | 未执行 | 未执行 | 通过 |
| upstream-adapted | True | 20/20 | 0.041/0.049 | 0.041/0.049 | 3/3 | 40/40 | 通过 |
| own-repeat | True | 20/20 | 0.042/0.057 | 0.042/0.057 | 3/3 | 未执行 | 通过 |
| production | True | 未执行 | —/— | —/— | 未执行 | 未执行 | 通过 |

稳定性窗口内每 15 秒采样的最大值（不是连续监测峰值）：

| 镜像 | CPU 采样最大值 | 内存采样最大值 | 600 秒 Docker 日志增长 |
| --- | --- | --- | --- |
| 自有 | 4.86% | 118.90MiB | 66,770 字节 |
| 对方适配入口 | 8.45% | 91.86MiB | 67,365 字节 |

## 镜像和诊断证据

### own-without-net-admin

- 镜像：`shadowofgost/warp@sha256:52ac93c7de15f214377eb27fd5170100b489520a113ba33b62328d39c3e6454e`；本地大小 872.5MiB。
- Digest：`shadowofgost/warp@sha256:52ac93c7de15f214377eb27fd5170100b489520a113ba33b62328d39c3e6454e`。
- WARP：`warp-cli 2026.8.2115.0; Build date: 2026-10-19 22:58:00.0 +00:00:00`；GOST：`未安装`。
- 源码：`e9f4d7da3bb8677ad775c6c9b44b98bcbd11567a`。
- 入口：`["/docker-entrypoint.sh"]`；适配：`False`；参数差异：`{}`。
- Docker CapAdd：`[]`；daemon CapEff：`[{"pid": 2551969, "cap_eff": "00000000a80425fb", "net_admin": false}]`。
- 首次恢复健康：— 秒；自动重启次数：0。
- readiness_failed：`{"last_trace": {"ok": false, "proxy": true, "download": false, "returncode": 7, "ip": null, "warp": null, "http_code": 0, "ttfb": 0.0, "total": 0.000108, "bytes": 0, "bytes_per_second": 0, "error": "curl: (7) Failed to connect to cloudflare.com port 443 via 127.0.0.1 after 0 ms: Could not connect to server"}, "state": "running"}`。

启动诊断摘录：

```text
ERROR actor_connection::network: Reconnecting after failing to reinstall WARP fwmark policy rules err=Custom { kind: PermissionDenied, error: "IPv4 fwmark rule install: Operation not permitted (os error 1)" }
```

### own

- 镜像：`shadowofgost/warp@sha256:52ac93c7de15f214377eb27fd5170100b489520a113ba33b62328d39c3e6454e`；本地大小 872.5MiB。
- Digest：`shadowofgost/warp@sha256:52ac93c7de15f214377eb27fd5170100b489520a113ba33b62328d39c3e6454e`。
- WARP：`warp-cli 2026.8.2115.0; Build date: 2026-10-19 22:58:00.0 +00:00:00`；GOST：`未安装`。
- 源码：`e9f4d7da3bb8677ad775c6c9b44b98bcbd11567a`。
- 入口：`["/docker-entrypoint.sh"]`；适配：`False`；参数差异：`{}`。
- Docker CapAdd：`['CAP_NET_ADMIN']`；daemon CapEff：`[{"pid": 2553419, "cap_eff": "00000000a80435fb", "net_admin": true}]`。
- 首次恢复健康：7.225 秒；自动重启次数：0。
- restart：`{"index": 0, "ready": true, "identity_preserved": true}`。
- restart：`{"index": 1, "ready": true, "identity_preserved": true}`。
- stopped_proxy：`{"ok": false, "proxy": true, "download": false, "returncode": 7, "ip": null, "warp": null, "http_code": 0, "ttfb": 0.0, "total": 0.000126, "bytes": 0, "bytes_per_second": 0, "error": "curl: (7) Failed to connect to cloudflare.com port 443 via 127.0.0.1 after 0 ms: Could not connect to server"}`。
- log_growth：`{"bytes": 66770, "seconds": 600}`。

启动诊断摘录：

```text
INFO Configured mode=proxy port=40000 qlog=False
```
最后一次 CPU/内存采样：`{"BlockIO":"3.62GB / 77.8kB","CPUPerc":"0.23%","Container":"warp","ID":"1373bf5a6bc0","MemPerc":"8.47%","MemUsage":"70.85MiB / 836.1MiB","Name":"warp","NetIO":"0B / 0B","PIDs":"30"}`；完整时间序列见 JSONL。

### upstream-stock-constrained

- 镜像：`caomingjun/warp@sha256:2891b92420a193a49c74cbd4387bee91f5c35fbc487fca8e8f739baed52c1d2b`；本地大小 978.7MiB。
- Digest：`caomingjun/warp@sha256:2891b92420a193a49c74cbd4387bee91f5c35fbc487fca8e8f739baed52c1d2b`。
- WARP：`warp-cli 2026.8.2115.0; Build date: 2026-10-19 22:57:46.0 +00:00:00`；GOST：`2.12.0`。
- 源码：`bf774bae06b31fe50e7b8542558dc0ea20db679f`。
- 入口：`["/entrypoint.sh"]`；适配：`False`；参数差异：`{"GOST_ARGS": "-L 127.0.0.1:1080", "WARP_ENABLE_NAT": ""}`。
- Docker CapAdd：`['CAP_NET_ADMIN']`；daemon CapEff：`[{"pid": 2559642, "cap_eff": "00000000a80435fb", "net_admin": true}]`。
- 首次恢复健康：7.095 秒；自动重启次数：0。

启动诊断摘录：

```text
```

### upstream-adapted

- 镜像：`caomingjun/warp@sha256:2891b92420a193a49c74cbd4387bee91f5c35fbc487fca8e8f739baed52c1d2b`；本地大小 978.7MiB。
- Digest：`caomingjun/warp@sha256:2891b92420a193a49c74cbd4387bee91f5c35fbc487fca8e8f739baed52c1d2b`。
- WARP：`warp-cli 2026.8.2115.0; Build date: 2026-10-19 22:57:46.0 +00:00:00`；GOST：`2.12.0`。
- 源码：`bf774bae06b31fe50e7b8542558dc0ea20db679f`。
- 入口：`["/bin/sh"]`；适配：`True`；参数差异：`{}`。
- Docker CapAdd：`['CAP_NET_ADMIN']`；daemon CapEff：`[{"pid": 2560231, "cap_eff": "00000000a80435fb", "net_admin": true}]`。
- 首次恢复健康：7.610 秒；自动重启次数：0。
- restart：`{"index": 0, "ready": true, "identity_preserved": true}`。
- restart：`{"index": 1, "ready": true, "identity_preserved": true}`。
- stopped_proxy：`{"ok": false, "proxy": true, "download": false, "returncode": 7, "ip": null, "warp": null, "http_code": 0, "ttfb": 0.0, "total": 0.000139, "bytes": 0, "bytes_per_second": 0, "error": "curl: (7) Failed to connect to cloudflare.com port 443 via 127.0.0.1 after 0 ms: Could not connect to server"}`。
- log_growth：`{"bytes": 67365, "seconds": 600}`。

启动诊断摘录：

```text
INFO Configured mode=proxy port=40000 qlog=False
```
最后一次 CPU/内存采样：`{"BlockIO":"3.74GB / 73.7kB","CPUPerc":"0.26%","Container":"warp","ID":"8595dee29338","MemPerc":"9.15%","MemUsage":"76.49MiB / 836.1MiB","Name":"warp","NetIO":"0B / 0B","PIDs":"32"}`；完整时间序列见 JSONL。

### own-repeat

- 镜像：`shadowofgost/warp@sha256:52ac93c7de15f214377eb27fd5170100b489520a113ba33b62328d39c3e6454e`；本地大小 872.5MiB。
- Digest：`shadowofgost/warp@sha256:52ac93c7de15f214377eb27fd5170100b489520a113ba33b62328d39c3e6454e`。
- WARP：`warp-cli 2026.8.2115.0; Build date: 2026-10-19 22:58:00.0 +00:00:00`；GOST：`未安装`。
- 源码：`e9f4d7da3bb8677ad775c6c9b44b98bcbd11567a`。
- 入口：`["/docker-entrypoint.sh"]`；适配：`False`；参数差异：`{}`。
- Docker CapAdd：`['CAP_NET_ADMIN']`；daemon CapEff：`[{"pid": 2566518, "cap_eff": "00000000a80435fb", "net_admin": true}]`。
- 首次恢复健康：7.450 秒；自动重启次数：0。

启动诊断摘录：

```text
INFO Configured mode=proxy port=40000 qlog=False
```

### production

- 镜像：`shadowofgost/warp:latest`；本地大小 872.5MiB。
- Digest：`shadowofgost/warp@sha256:52ac93c7de15f214377eb27fd5170100b489520a113ba33b62328d39c3e6454e`。
- WARP：`warp-cli 2026.8.2115.0; Build date: 2026-10-19 22:58:00.0 +00:00:00`；GOST：`未安装`。
- 源码：`e9f4d7da3bb8677ad775c6c9b44b98bcbd11567a`。
- 入口：`["/docker-entrypoint.sh"]`；适配：`False`；参数差异：`{}`。
- Docker CapAdd：`['CAP_NET_ADMIN']`；daemon CapEff：`[{"pid": 2573522, "cap_eff": "00000000a80435fb", "net_admin": true}]`。
- 首次恢复健康：7.411 秒；自动重启次数：0。
生产 Compose：`{"image": "shadowofgost/warp:latest", "network_mode": "host", "cap_add": ["NET_ADMIN"], "logging": {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}}`。
其他服务原文保持：`True`；配置属主及权限：`{"uid": 1000, "gid": 1000, "mode": "0o775"}`。
实际日志配置：`{"Type": "json-file", "Config": {"max-file": "3", "max-size": "10m"}}`；最终网络回归：`[]`。
许可证泄露检查：`False`；日志尾部行数：`350`；DEBUG 行数：`0`。

启动诊断摘录：

```text
INFO Starting WARP 2026.8.2115.0
INFO Reusing existing WARP registration
INFO Configured mode=proxy port=40000 qlog=False
```

## 宿主网络检查

记录 257 次，其中回归记录 0 次。
- 新增策略规则差异：`{"4": [], "6": []}`
- 新增策略规则差异：`{"4": [{"fwmark": "0x100cf", "not": null, "priority": 32765, "src": "all", "table": "65743"}], "6": [{"fwmark": "0x100cf", "not": null, "priority": 32765, "src": "all", "table": "65743"}]}`
## 最终部署及限制

- `2026-10-25T11:33:00.787660+00:00` baseline：`{"image": "sha256:0750627b07ba6dd8c5b7a119902bd42535af0f6ac8b24a7e5293f8b3b6535238", "state": "running", "health": "unhealthy", "restarts": 2, "log_config": {"Type": "json-file", "Config": {}}, "direct": {"ok": true, "proxy": false, "download": false, "returncode": 0, "ip": "20.25.137.229", "warp": "off", "http_code": 200, "ttfb": 0.147867, "total": 0.14793, "bytes": 212, "bytes_per_second": 1433, "error": ""}, "proxy": {"ok": false, "proxy": true, "download": false, "returncode": 7, "ip": null, "warp": null, "http_code": 0, "ttfb": 0.0, "total": 0.000116, "bytes": 0, "bytes_per_second": 0, "error": "curl: (7) Failed to connect to cloudflare.com port 443 via 127.0.0.1 after 0 ms: Could not connect to server"}}`
- `2026-10-25T11:33:05.206112+00:00` services_baseline：`{"ssh": true, "nginx": "200", "xray": {"probe": "live_xray_vless_ws", "ip": "20.25.137.229", "warp": "off", "gateway": "off", "colo": "LAX", "loc": "US", "seconds": 0.92}}`
- `2026-10-25T11:36:24.217896+00:00` snapshot_complete：`{"registration": "589fc32bb4c1fc00fe3d9ebe75a2c608485191cf6a24ab46014ed1fb3d317c21", "archived_log_bytes": 11221140037, "backup_container": "warp-before-warp-upgrade-netadmin-20261025T113300Z"}`
- `2026-10-25T11:38:31.222507+00:00` network_recovery：`{"actions": []}`
- `2026-10-25T11:49:42.250119+00:00` network_recovery：`{"actions": ["IPv4 removed policy rule ('priority', '32765', 'not', 'from', 'all', 'fwmark', '0x100cf', 'lookup', '65743')", "IPv6 removed policy rule ('priority', '32765', 'not', 'from', 'all', 'fwmark', '0x100cf', 'lookup', '65743')"]}`
- `2026-10-25T11:50:15.527828+00:00` network_recovery：`{"actions": ["IPv4 removed policy rule ('priority', '32765', 'not', 'from', 'all', 'fwmark', '0x100cf', 'lookup', '65743')", "IPv6 removed policy rule ('priority', '32765', 'not', 'from', 'all', 'fwmark', '0x100cf', 'lookup', '65743')"]}`
- `2026-10-25T12:01:20.884592+00:00` network_recovery：`{"actions": ["IPv4 removed policy rule ('priority', '32765', 'not', 'from', 'all', 'fwmark', '0x100cf', 'lookup', '65743')", "IPv6 removed policy rule ('priority', '32765', 'not', 'from', 'all', 'fwmark', '0x100cf', 'lookup', '65743')"]}`
- `2026-10-25T12:01:56.628590+00:00` acceptance：`{"own": true, "upstream": true, "repeat": true}`
- `2026-10-25T12:14:53.886074+00:00` network_recovery：`{"actions": ["IPv4 removed policy rule ('priority', '32765', 'not', 'from', 'all', 'fwmark', '0x100cf', 'lookup', '65743')", "IPv6 removed policy rule ('priority', '32765', 'not', 'from', 'all', 'fwmark', '0x100cf', 'lookup', '65743')"]}`
- `2026-10-25T12:15:06.270367+00:00` deployed：`{"image": "sha256:45d6addd4ef5984f7446a1de882be7cd7441b7a7d39187ec7a89196d182beef4", "trace": {"ok": true, "proxy": true, "download": false, "returncode": 0, "ip": "2a09:bac1:76a0:8e0::1bd:11d", "warp": "on", "http_code": 200, "ttfb": 0.187733, "total": 0.187791, "bytes": 230, "bytes_per_second": 1224, "error": ""}}`
- `2026-10-25T12:15:08.293970+00:00` verification：`{"original_image_restored": false, "identity_preserved": true, "compose_restored": false, "health": "healthy", "watchtower_running": true}`

部署/恢复核验：

- 原镜像一致：False；注册身份保留：True。
- Compose 已恢复：False；Watchtower 运行：True。
- 最终 WARP 健康状态：healthy；CAP_NET_ADMIN：None。

原始逐请求数据见同名 JSONL。计划在启动成功后执行 20 次请求、间隔 1 秒；稳定性窗口每 15 秒采样，共 40 次、持续 10 分钟。启动未通过时，后续性能、重启和稳定性项目不执行，不能据此得出性能排名。
下载为 3 次 1MiB 的同一 Cloudflare URL。失败样本保留，未用重试覆盖失败。
启动轮询逐次保留 readiness_probe；表中 trace 指启动成功后执行的性能批次。耗时分位数仅统计成功请求，失败样本保留。
Azure 结果只代表 amd64 和本次网络条件，不能推断 ARM 网络性能、GOST 性能或 OpenAI 等站点可用性。
Xray 路由未修改。两镜像客户端版本不同或适配后仍失败时，不把结果直接归因为代理实现的性能差异。

## 发布和补充核验

- [同 digest 提升记录](https://github.com/shadowofgost/auto-workflow/actions/runs/40191838229)：promote 成功，build 与 manifest 构建任务均跳过。Docker Hub 和 GHCR 的 latest 已提升，未重新构建。
- Azure 实际拉取的 latest digest 为 `sha256:52ac93c7de15f214377eb27fd5170100b489520a113ba33b62328d39c3e6454e`，与本轮测试候选一致。
- 正式部署于 2026-10-25 12:15 UTC（北京时间 20:15）恢复健康；12:31 UTC 再次核验仍 healthy、自动重启次数 0，实际 daemon 具备 NET_ADMIN。
- 最终日志配置为 json-file、10m×3；公开 JSONL 与当前容器日志未发现配置中的许可证，日志尾部 DEBUG 行数为 0。
- 生产 Compose 已持久化 host、NET_ADMIN、latest 和日志轮转；其他服务原文不变，配置属主保持 uid/gid 1000:1000、权限 0775。
- 最终独立请求：宿主直连 warp=off，原生 SOCKS5 warp=on，现有 Xray warp=off；Nginx HTTPS 返回 200。代理仅监听 127.0.0.1:40000，1080 未保留监听。
- 对方额外执行 `gost -V` 核验为 `gost 2.12.0 (go1.22.8 linux/amd64)`；本轮未测试 GOST 转发性能。
- [首轮预检失败记录](azure-netadmin-preflight-20261025.md)单独保留：Docker 管理命令超时，未进入容器变更；主机恢复响应后在新目录进行了本轮完整测试。失败证据未被重测覆盖。
- 两个代理各 20 个性能请求，结果会受网络波动影响，本轮不据此作稳定的性能优劣排名。
- 本轮原容器、冷备份和日志归档保留在服务器私有目录 `/var/tmp/warp-upgrade-netadmin-20261025T113300Z`，没有删除此前备份或截断 Docker 管理的日志。
