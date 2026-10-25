# NeverIdle

基于 [layou233/NeverIdle](https://github.com/layou233/NeverIdle) 的 Release 二进制构建镜像，支持 `linux/amd64` 和 `linux/arm64`。Dockerfile 沿用上游的 Alpine 下载阶段和 scratch 运行阶段，保留 HTTPS 请求所需的 CA 证书。

## 镜像与首次发布

| 镜像仓库 | 镜像地址 |
| --- | --- |
| Docker Hub | `shadowofgost/neveridle:latest` |
| GHCR | `ghcr.io/shadowofgost/neveridle:latest` |

部署前，需要先将这些文件提交到本仓库，并成功运行一次 **neveridle build** 工作流。首次发布 GHCR 包后，若需要匿名拉取，请在 GitHub 包设置中将其可见性设为 Public；私有包需要先执行 `docker login ghcr.io`。

工作流支持手动触发、每月 15 日北京时间 08:00 定时触发，以及 `main` 分支中本目录或对应工作流变更触发。相关 PR 只验证，不登录或发布镜像。两个架构都会先执行 `--help` 冒烟测试，通过后再发布；发布后检查两个镜像仓库的清单均包含 AMD64 和 ARM64。

每次运行先解析一次上游最新 Release，两个架构使用同一版本。镜像发布 `latest` 和北京时间 `YYYY-MM-DD` 标签，例如 `shadowofgost/neveridle:2026-10-24`；同一天再次发布会覆盖当天日期标签。日期标签代表构建日期，程序版本记录在镜像的 `org.opencontainers.image.version` 标签中。

仓库 Actions Secrets 需要配置：

| Secret | 用途 |
| --- | --- |
| `DOCKER_USERNAME` | 有权向 `shadowofgost/neveridle` 推送镜像的 Docker Hub 用户 |
| `DOCKER_PASSWORD` | 对应 Docker Hub 访问令牌 |

GHCR 使用自动提供的 `GITHUB_TOKEN`，工作流已声明 `packages: write` 权限。

## Docker Compose 部署

安装 Docker Engine 与 Docker Compose，将本目录的 `compose.yml` 和 `.env.example` 保存到服务器的同一个部署目录后，在该目录执行：

```bash
cp .env.example .env
# 编辑 .env，设置所需的 CPU、内存和网络参数。
docker compose config
docker compose pull
docker compose up -d
docker compose logs -f --tail=100
```

Compose 自动读取同目录的 `.env`，将参数传给 NeverIdle，无需重新构建镜像。不创建 `.env` 时也可启动，使用与上游一致的默认参数。此处的变量由 Compose 转换为程序的命令行参数，不是 NeverIdle 自带的环境变量接口。参见 [Docker Compose 变量说明](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/)。

默认使用 Docker Hub 镜像。如需使用 GHCR，将 `.env` 中的 `NEVERIDLE_IMAGE` 改为 `ghcr.io/shadowofgost/neveridle:latest`。两者均自动匹配 AMD64 或 ARM64 主机，无需手动设置 `platform`。

服务无需端口映射或持久化数据卷，使用 Docker 默认网络访问外网进行测速。`restart: unless-stopped` 会在容器退出或 Docker 重启后自动启动服务，手动停止的容器除外。

更新镜像并重建容器：

```bash
docker compose pull
docker compose up -d
```

停止并删除容器：

```bash
docker compose down
```

如需使用某次已发布的构建，将 `.env` 中的 `NEVERIDLE_IMAGE` 改为对应日期镜像，例如 `shadowofgost/neveridle:2026-10-24`，再执行上述更新命令。

## 运行参数

直接修改 `.env` 即可调整部署参数：

```dotenv
NEVERIDLE_IMAGE=shadowofgost/neveridle:latest
NEVERIDLE_CPU_MODE=c
NEVERIDLE_CPU_VALUE=2h
NEVERIDLE_MEMORY_GIB=2
NEVERIDLE_NETWORK_INTERVAL=4h
NEVERIDLE_NETWORK_CONNECTIONS=10
```

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `NEVERIDLE_IMAGE` | `shadowofgost/neveridle:latest` | 镜像地址和标签 |
| `NEVERIDLE_CPU_MODE` | `c` | `c` 表示定时负载，`cp` 表示比例负载；只传入一种 CPU 参数 |
| `NEVERIDLE_CPU_VALUE` | `2h` | `c` 模式填写间隔，如 `2h`、`30m`；`cp` 模式填写 0 到 1 的比例，如 `0.15`；`0` 关闭 CPU 负载 |
| `NEVERIDLE_MEMORY_GIB` | `2` | 持续占用的内存，单位 GiB，填写非负整数；`0` 关闭 |
| `NEVERIDLE_NETWORK_INTERVAL` | `4h` | Ookla 网络测速间隔，如 `4h`、`12h30m`；`0` 关闭 |
| `NEVERIDLE_NETWORK_CONNECTIONS` | `10` | 网络测速并发连接数，填写正整数 |

程序启动后会立即执行已启用的任务，并非等到第一个间隔后才开始。默认内存占用为 2 GiB，还需为程序和其他服务预留内存；测速流量取决于带宽与测速过程。按主机资源调整参数。

例如，只启用 15% CPU 比例负载，并关闭额外内存占用和测速，在 `.env` 中修改：

```dotenv
NEVERIDLE_CPU_MODE=cp
NEVERIDLE_CPU_VALUE=0.15
NEVERIDLE_MEMORY_GIB=0
NEVERIDLE_NETWORK_INTERVAL=0
```

切换 CPU 模式时同时修改 `NEVERIDLE_CPU_MODE` 和 `NEVERIDLE_CPU_VALUE`，避免将时间值用于比例模式。Compose 不会同时传入 `-c` 和 `-cp`。

修改 `.env` 后，检查最终参数并重新创建容器：

```bash
docker compose config
docker compose up -d
```

仅执行 `docker compose restart` 不会应用新参数。如果配置文件不在默认位置，可显式指定：

```bash
docker compose --env-file /path/to/neveridle.env -f /path/to/compose.yml up -d
```

可用 `docker compose run --rm neveridle --help` 查看完整程序参数；如需传入其他参数，也可直接编辑 `compose.yml` 的 `command` 列表。

## 本地构建

在本目录执行以下命令，自动使用本机架构构建并下载上游最新 Release：

```bash
docker build --pull --no-cache -t neveridle:local .
docker run --rm neveridle:local --help
```

默认 `NEVERIDLE_VERSION=latest`。由于下载地址不随版本变化，跟进最新 Release 时使用 `--no-cache`，避免复用旧下载层。

指定上游 Release 标签构建，例如 `0.2.4`：

```bash
docker build --build-arg NEVERIDLE_VERSION=0.2.4 -t neveridle:0.2.4 .
docker run --rm neveridle:0.2.4 --help
```

如需通过 Compose 使用本地镜像，将 `.env` 中的 `NEVERIDLE_IMAGE` 改为 `neveridle:local` 后执行 `docker compose up -d`，不需要执行 `pull`。

构建并推送两个架构时，使用支持多架构构建的 Buildx builder，将下方镜像名换为有推送权限的地址：

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  --build-arg NEVERIDLE_VERSION=0.2.4 \
  -t YOUR_NAMESPACE/neveridle:0.2.4 --push .
```

## 上游参考

- [Dockerfile](https://github.com/layou233/NeverIdle/blob/master/Dockerfile)
- [运行参数说明](https://github.com/layou233/NeverIdle#usage)
- [Releases](https://github.com/layou233/NeverIdle/releases)
