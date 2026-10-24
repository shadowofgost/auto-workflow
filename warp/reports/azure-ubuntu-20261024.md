# Azure Ubuntu WARP 安装与代理复测

测试日期：2026-10-24，启动与恢复样本时间为 15:10–15:16 UTC（北京时间 23:10–23:16）。

## 结论

Ubuntu 软件包安装、amd64/arm64 原生构建均通过；Azure 在原部署权限下的代理验收未通过。
候选镜像等待 120 秒后，SOCKS5 `127.0.0.1:40000` 仍无法连接；客户端反复报告
`IPv4 fwmark rule install: Operation not permitted (os error 1)`。
本次没有增加权限验证修复效果，因此不能声称添加权限后一定可以运行。

已恢复原容器、原镜像和冷备份数据，注册身份与 Compose 内容核验一致，Watchtower 已恢复运行。
原部署测试前即为 unhealthy 且代理不可用；恢复原部署不代表原有故障已解决。latest 未提升。

## Ubuntu 安装来源

Cloudflare [官方 Ubuntu 安装说明](https://pkg.cloudflareclient.com/#ubuntu)提供 Ubuntu 26.04 的
`resolute` 软件源。APT 源里的 `deb` 是 Ubuntu 也使用的软件源格式，不能据此判断使用 Debian 发行版。
现有旧镜像与修改前的 Dockerfile 已选取 `resolute`，没有使用 `bookworm`、`trixie` 等 Debian 发行版源。

本次修改显式检查 `ID=ubuntu`，使用 `UBUNTU_CODENAME`，统一官方密钥环及源文件名称，并记录安装来源。
候选镜像内实际检查结果：

```text
distribution=ubuntu
suite=resolute
repository=https://pkg.cloudflareclient.com/
```

```text
deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ resolute main
```

记录位于 `/etc/warp-package-source`；实际版本和架构分别位于
`/etc/warp-package-version`、`/etc/warp-package-architecture`。
软件包通过 APT 直接安装，没有恢复共同版本解析或版本固定逻辑。

## 镜像和构建证据

- 源码提交：`e9f4d7da3bb8677ad775c6c9b44b98bcbd11567a`。
- [GitHub Actions 构建](https://github.com/shadowofgost/auto-workflow/actions/runs/40105332657)：amd64、arm64 原生构建和 manifest 合并均成功。
- 两个架构实际执行 `warp-cli --version` 均为 `2026.8.2115.0`，Ubuntu 来源检查、单元测试及日志轮转检查通过。
- 候选镜像：`shadowofgost/warp:sha-e9f4d7da3bb8677ad775c6c9b44b98bcbd11567a`。
- 拉取的镜像 digest：`sha256:52ac93c7de15f214377eb27fd5170100b489520a113ba33b62328d39c3e6454e`。
- Azure amd64 镜像 ID：`sha256:45d6addd4ef5984f7446a1de882be7cd7441b7a7d39187ec7a89196d182beef4`。
- Azure 客户端：`warp-cli 2026.8.2115.0`，Build date `2026-10-19 22:58:00.0 +00:00:00`。
- 原镜像 ID：`sha256:0750627b07ba6dd8c5b7a119902bd42535af0f6ac8b24a7e5293f8b3b6535238`；原客户端为 `2026.8.2110.0`。

## 测试条件和方法

Azure 为 amd64，内存约 836MiB，无 swap。镜像在 GitHub runner 构建，Azure 只拉取并运行。
保留 `container_name=warp`、host 网络、`WARP_SLEEP=3`、`WARP_PROXY=TRUE`、`WARP_PORT=40000`、
现有许可证、数据挂载路径、`unless-stopped` 及原健康检查参数（15s/5s/60s/3 次）。许可证不写入报告。
候选容器统一使用 `json-file`、`max-size=10m`、`max-file=3`；未添加 NET_ADMIN、privileged、TUN 或宿主路由修改。

复用 `azure-compare.py` 的冷备份、候选阶段测试与回滚逻辑，本次只测试自有 Ubuntu 候选镜像，测试结束后恢复原部署。
先预拉取镜像，暂停 Watchtower，停止并保留原容器，冷备份注册数据并归档原 Docker 日志。
每次只运行一个 WARP daemon。独立验证通过 `curl --socks5-hostname` 请求 Cloudflare trace，避免使用本地 DNS 替代代理 DNS。

## 实测结果

| 项目 | 结果 |
| --- | --- |
| 原部署状态 | running / unhealthy；原生代理连接失败 |
| 宿主直连基线 | HTTP 200，`warp=off`，出口 `20.25.137.229` |
| Ubuntu 来源与实际安装版本 | 通过，resolute / amd64 / 2026.8.2115.0 |
| 候选容器启动 | 进程 running，但未建立可用代理 |
| 120 秒代理就绪检查 | 未通过，curl 返回码 7，HTTP 0 |
| CAP_NET_ADMIN | False |
| 候选自动重启次数 | 0；进程仍存活不能证明代理有效 |
| 20 次请求、下载、重启、10 分钟稳定性 | 启动门槛未通过，未执行，不作成功或性能结论 |
| 停止和恢复 | 候选已删除，原容器及原数据已恢复 |

诊断摘录：

```text
ERROR actor_connection::network: Reconnecting after failing to reinstall WARP fwmark policy rules
err=Custom { kind: PermissionDenied, error: "IPv4 fwmark rule install: Operation not permitted (os error 1)" }
```

该错误明确表明客户端安装 fwmark 规则时被拒绝；Ubuntu 安装来源已验证，当前结果不能归因于误装 Debian 包。
尚未进行增加网络管理权限的对照测试。本次没有测试其他发行版、其他协议或 ARM 的实际网络代理能力。

## 回滚、日志与原始样本

2026-10-24 15:16:33 UTC 的恢复核验：原镜像一致、注册身份一致、Compose 内容一致、Watchtower 运行。
原容器恢复后初始健康状态为 starting，当时再次验证直连成功、SOCKS5 仍连接失败。
15:17:29 UTC 再次核验 Watchtower 为 healthy，原 WARP 仍处于 starting，代理未有恢复成功证据。
15:19:27 UTC 最终核验：原镜像 ID 一致，WARP 为 running / unhealthy，Watchtower 为 running / healthy；测试进程已退出。

原 Docker 日志归档大小为 10,188,040,172 字节（约 9.49GiB），未截断 Docker 管理的原始日志。
日志归档、原始配置和数据快照保留在服务器的私有目录 `/var/tmp/warp-upgrade-ubuntu-20261024`。
原部署恢复时保留原日志配置，因此候选镜像的轮转设置未永久应用到旧容器。

脱敏逐事件样本见 [azure-ubuntu-20261024.jsonl](azure-ubuntu-20261024.jsonl)。
SSH 读取状态期间有间歇性连接超时；测试在远端后台运行，代理失败样本没有被重试覆盖成成功。
Xray、Nginx 和宿主路由未修改。
