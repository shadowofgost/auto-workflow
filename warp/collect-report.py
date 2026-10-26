"""Collect sanitized comparison events over SSH and produce a Chinese report."""
import argparse
import datetime
import json
from pathlib import Path
import re
import subprocess


def percentile(values, p):
    values = sorted(value for value in values if isinstance(value, (int, float)))
    if not values:
        return None
    index = (len(values) - 1) * p
    low = int(index)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (index - low)


def number(value):
    return "—" if value is None else f"{value:.3f}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True)
    parser.add_argument("--output", default="warp/reports/azure-netadmin-" + datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y%m%d"))
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"/var/tmp/warp-upgrade-[a-zA-Z0-9-]+", args.work):
        parser.error("Unexpected remote work directory")
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ConnectionAttempts=2", "azure",
                             "sudo", "-n", "cat", args.work + "/results.jsonl"],
                            capture_output=True, check=True, timeout=60)
    events = [json.loads(line) for line in result.stdout.decode().splitlines() if line.strip()]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".jsonl").write_text(result.stdout.decode(), encoding="utf-8")
    phases = list(dict.fromkeys(event["stage"] for event in events if event["kind"] == "image"))
    deployed = any(e["kind"] == "deployed" for e in events)
    restored = any(e["kind"] == "restored_original" for e in events)
    lines = ["# Azure WARP NET_ADMIN 双镜像检验报告", "",
             f"采集时间：{datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec='seconds')}（Asia/Shanghai）。",
             f"原候选双架构构建与验证：[GitHub Actions]({args.run})；本轮复用镜像。", "",
             "## 结论", "",
             ("新镜像已通过 Azure 验收并部署。" if deployed and not restored
              else "Azure 验收未通过，已恢复测试前镜像及冷备份数据。原部署可能本已存在故障，回滚不代表原有故障已修复。" if restored
              else "测试未完成或在部署前停止，不能宣称验收通过。"),
             "权限、网络影响和代理可用性均按本次样本报告；不预先保证添加 NET_ADMIN 一定成功。", "",
             "## 测试条件", "",
             "Azure amd64，约 836MiB RAM、无 swap；每次仅运行一个 WARP 实例。保留原有 host 网络、",
             "WARP_SLEEP=3、WARP_PROXY=TRUE、WARP_PORT=40000、许可证、数据路径、重启及健康检查参数。",
             "许可证不写入报告；每阶段从同一冷备份恢复注册数据。双方统一使用 10MB×3 的 JSON 日志轮转。",
             "各阶段实际 NET_ADMIN 配置参见表格及 daemon CapEff；无权限对照单独标注，不使用 privileged 或额外 TUN 挂载。",
             "对方适配入口使用同一监督器，只测原生 SOCKS5 代理，不启动 GOST 或 NAT。",
             "对方原始入口限制 GOST 到 127.0.0.1:1080 并关闭 NAT，属于受约束原始入口检查，不能称为完全未经参数调整。", "",
             "## 实现差异", "",
             "| 项目 | 升级后的自有镜像 | 对方当前镜像/源码 |",
             "| --- | --- | --- |",
             "| 基础系统 | Ubuntu 26.04 | Ubuntu 22.04 |",
             "| 默认代理入口 | 官方原生 SOCKS5，40000 | GOST，1080；现有变量 WARP_PROXY/WARP_PORT 不由其入口解析 |",
             "| 持久化启动 | 每次重设模式、端口、endpoint 并连接 | 已有注册时跳过首次注册和连接代码 |",
             "| 进程与日志 | 内置监督器、信号转发、精简过滤、qlog 默认关闭 | shell 启动 daemon，前台 GOST，保留 daemon 输出、默认关闭 qlog |",
             "| 架构 | 原候选已原生检查 amd64/arm64，本轮复用 | Dockerfile 支持 amd64/arm64 |",
             "| 用户 | root | warp 用户，具备免密码 sudo |", "",
             "原始入口允许读取冷备份中已有的代理设置，因此端口存在与否不能单独证明其支持这些环境变量。",
             "相同 WARP 版本号仍对应不同 Ubuntu 软件包和依赖，不把结果视为完全相同二进制的对照实验。", "",
             "## 实测结果", "",
             "| 阶段 | NET_ADMIN | trace 成功 | 首字节 p50/p95（秒） | 总耗时 p50/p95（秒） | 下载成功 | 稳定性成功 | 阶段验收 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for phase in phases:
        selected = [e for e in events if e["stage"] == phase]
        traces = [e for e in selected if e["kind"] == "trace"]
        successful = [e for e in traces if e["ok"]]
        image = next(e for e in selected if e["kind"] == "image")
        downloads = [e for e in selected if e["kind"] == "download"]
        stability = [e for e in selected if e["kind"] == "stability"]
        conclusion = next((e.get("passed") for e in reversed(selected) if e["kind"] in ("phase_result", "compatibility")), None)
        if phase == 'production' and any(e['kind'] == 'deployed' for e in selected):
            conclusion = True
        counts = lambda rows: f"{sum(e['ok'] for e in rows)}/{len(rows)}" if rows else "未执行"
        verdict = '通过' if conclusion is True else '未通过' if conclusion is False else '未完成'
        if phase == 'own-without-net-admin' and conclusion is False:
            verdict = '无权限对照失败'
        lines.append(f"| {phase} | {image.get('net_admin', '未记录')} | {counts(traces)} | "
                     f"{number(percentile([e['ttfb'] for e in successful], .5))}/{number(percentile([e['ttfb'] for e in successful], .95))} | "
                     f"{number(percentile([e['total'] for e in successful], .5))}/{number(percentile([e['total'] for e in successful], .95))} | "
                     f"{counts(downloads)} | {counts(stability)} | "
                     f"{verdict} |")
    lines += ["", "## 镜像和诊断证据", ""]
    for phase in phases:
        selected = [e for e in events if e["stage"] == phase]
        image = next(e for e in selected if e["kind"] == "image")
        runtime = next((e for e in selected if e["kind"] == "runtime"), {})
        diagnostics = [e for e in selected if e["kind"] == "diagnostics"]
        if not runtime and diagnostics:
            runtime = diagnostics[-1]
        ready = next((e for e in selected if e["kind"] == "ready"), {})
        labels = image.get("labels", {})
        version = (runtime.get('version') or labels.get('WARP_VERSION') or '未取得').replace('\n', '; ')
        lines += [f"### {phase}", "", f"- 镜像：`{image['reference']}`；本地大小 {image['size'] / 1048576:.1f}MiB。",
                  f"- Digest：`{', '.join(image.get('digests') or [])}`。",
                  f"- WARP：`{version}`；"
                  f"GOST：`{labels.get('GOST_VERSION', '未安装')}`。",
                  f"- 源码：`{labels.get('COMMIT_SHA') or labels.get('org.opencontainers.image.revision', '未标注')}`。",
                  f"- 入口：`{json.dumps(image.get('entrypoint'), ensure_ascii=False)}`；适配：`{image.get('adapted')}`；参数差异：`{json.dumps(image.get('parameter_differences', {}), ensure_ascii=False)}`。",
                  f"- Docker CapAdd：`{diagnostics[-1].get('configured_cap_add', []) if diagnostics else []}`；daemon CapEff：`{json.dumps(diagnostics[-1].get('daemon_capabilities', []) if diagnostics else [])}`。",
                  f"- 首次恢复健康：{number(ready.get('seconds'))} 秒；自动重启次数：{ready.get('auto_restarts', diagnostics[-1].get('restarts', '未取得') if diagnostics else '未取得')}。"]
        for event in selected:
            if event["kind"] in ("restart", "log_growth", "readiness_failed", "stopped_proxy"):
                detail = {k: v for k, v in event.items() if k not in ("time", "stage", "kind")}
                lines.append(f"- {event['kind']}：`{json.dumps(detail, ensure_ascii=False)}`。")
        if diagnostics:
            final = diagnostics[-1]
            if 'compose_service' in final:
                lines.append(f"生产 Compose：`{json.dumps(final['compose_service'], ensure_ascii=False)}`。")
                lines.append(f"其他服务原文保持：`{final.get('other_services_unchanged')}`；配置属主及权限：`{json.dumps(final.get('compose_metadata'))}`。")
                lines.append(f"实际日志配置：`{json.dumps(final.get('log_config'))}`；最终网络回归：`{final.get('network', {}).get('problems')}`。")
                if 'license_leaked' in final:
                    lines.append(f"许可证泄露检查：`{final['license_leaked']}`；日志尾部行数：`{final.get('log_tail_lines')}`；DEBUG 行数：`{final.get('debug_lines_in_tail')}`。")
            lines += ["", "启动诊断摘录：", "", "```text"]
            lines.extend(dict.fromkeys(re.sub(r"^\S+\s+", "", line) for line in diagnostics[-1].get("lines", [])))
            lines += ["```"]
        stats = [e['stats'] for e in selected if e['kind'] == 'stability' and e.get('stats')]
        if stats:
            lines.append(f"最后一次 CPU/内存采样：`{stats[-1]}`；完整时间序列见 JSONL。")
        lines.append("")
    host = [e for e in events if e['kind'] == 'host_network']
    lines += ["## 宿主网络检查", "", f"记录 {len(host)} 次，其中回归记录 {sum(bool(e.get('problems')) for e in host)} 次。"]
    rules = dict.fromkeys(json.dumps(e.get('added_rules', {}), ensure_ascii=False, sort_keys=True) for e in host)
    lines.extend(f"- 新增策略规则差异：`{rule}`" for rule in rules)
    for event in host:
        if event.get('problems'):
            lines.append(f"- {event['stage']}：`{json.dumps(event, ensure_ascii=False)}`")
    lines += ["## 最终部署及限制", ""]
    for event in events:
        if event["kind"] in ("baseline", "services_baseline", "acceptance", "deployed", "restored_original", "fatal", "watchdog_abort", "network_recovery", "verification", "skipped", "snapshot_complete"):
            lines.append(f"- `{event['time']}` {event['kind']}：`{json.dumps({k:v for k,v in event.items() if k not in ('time','kind','stage')}, ensure_ascii=False)}`")
    verification = next((e for e in reversed(events) if e['kind'] == 'verification'), None)
    if verification:
        lines += ["", "部署/恢复核验：", "", f"- 原镜像一致：{verification['original_image_restored']}；注册身份保留：{verification['identity_preserved']}。",
                  f"- Compose 已恢复：{verification['compose_restored']}；Watchtower 运行：{verification['watchtower_running']}。",
                  f"- 最终 WARP 健康状态：{verification['health']}；CAP_NET_ADMIN：{verification.get('cap_net_admin')}。"]
    lines += ["", "原始逐请求数据见同名 JSONL。计划在启动成功后执行 20 次请求、间隔 1 秒；稳定性窗口每 15 秒采样，共 40 次、持续 10 分钟。启动未通过时，后续性能、重启和稳定性项目不执行，不能据此得出性能排名。",
              "下载为 3 次 1MiB 的同一 Cloudflare URL。失败样本保留，未用重试覆盖失败。",
              "启动轮询逐次保留 readiness_probe；表中 trace 指启动成功后执行的性能批次。耗时分位数仅统计成功请求，失败样本保留。",
              "Azure 结果只代表 amd64 和本次网络条件，不能推断 ARM 网络性能、GOST 性能或 OpenAI 等站点可用性。",
              "Xray 路由未修改。两镜像客户端版本不同或适配后仍失败时，不把结果直接归因为代理实现的性能差异。", ""]
    output.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    print(output.with_suffix(".md"))


if __name__ == "__main__":
    main()
