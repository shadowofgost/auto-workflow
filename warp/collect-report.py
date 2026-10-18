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
    parser.add_argument("--output", default="warp/reports/azure-comparison-20261018")
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"/var/tmp/warp-upgrade-[a-zA-Z0-9-]+", args.work):
        parser.error("Unexpected remote work directory")
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "azure",
                             "sudo", "-n", "cat", args.work + "/results.jsonl"],
                            capture_output=True, check=True, timeout=60)
    events = [json.loads(line) for line in result.stdout.decode().splitlines() if line.strip()]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".jsonl").write_text(result.stdout.decode(), encoding="utf-8")
    phases = list(dict.fromkeys(event["stage"] for event in events if event["kind"] == "image"))
    lines = ["# Azure WARP 镜像对比检验报告", "",
             f"采集时间：{datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec='seconds')}（Asia/Shanghai）。",
             f"原生双架构构建与验证：[GitHub Actions]({args.run})。", "",
             "## 测试条件", "",
             "Azure amd64，约 836MiB RAM、无 swap；每次仅运行一个 WARP 实例。保留原有 host 网络、",
             "WARP_SLEEP=3、WARP_PROXY=TRUE、WARP_PORT=40000、许可证、数据路径、重启及健康检查参数。",
             "许可证不写入报告；每阶段从同一冷备份恢复注册数据。双方统一使用 10MB×3 的 JSON 日志轮转。",
             "对方适配入口使用同一监督器，只测原生 SOCKS5 代理，不启动 GOST、TUN 或 NAT，不增加网络管理权限。", "",
             "## 实测结果", "",
             "| 阶段 | trace 成功 | 首字节 p50/p95（秒） | 总耗时 p50/p95（秒） | 下载成功 | 稳定性成功 | 阶段验收 |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for phase in phases:
        selected = [e for e in events if e["stage"] == phase]
        traces = [e for e in selected if e["kind"] == "trace"]
        downloads = [e for e in selected if e["kind"] == "download"]
        stability = [e for e in selected if e["kind"] == "stability"]
        conclusion = next((e.get("passed") for e in reversed(selected) if e["kind"] in ("phase_result", "compatibility")), None)
        lines.append(f"| {phase} | {sum(e['ok'] for e in traces)}/{len(traces)} | "
                     f"{number(percentile([e['ttfb'] for e in traces], .5))}/{number(percentile([e['ttfb'] for e in traces], .95))} | "
                     f"{number(percentile([e['total'] for e in traces], .5))}/{number(percentile([e['total'] for e in traces], .95))} | "
                     f"{sum(e['ok'] for e in downloads)}/{len(downloads)} | {sum(e['ok'] for e in stability)}/{len(stability)} | "
                     f"{'通过' if conclusion is True else '未通过' if conclusion is False else '未完成'} |")
    lines += ["", "## 镜像和诊断证据", ""]
    for phase in phases:
        selected = [e for e in events if e["stage"] == phase]
        image = next(e for e in selected if e["kind"] == "image")
        runtime = next((e for e in selected if e["kind"] == "runtime"), {})
        ready = next((e for e in selected if e["kind"] == "ready"), {})
        labels = image.get("labels", {})
        lines += [f"### {phase}", "", f"- 镜像：`{image['reference']}`；本地大小 {image['size'] / 1048576:.1f}MiB。",
                  f"- Digest：`{', '.join(image.get('digests') or [])}`。",
                  f"- WARP：`{runtime.get('version') or labels.get('WARP_VERSION') or labels.get('org.opencontainers.image.version', '未取得')}`；"
                  f"GOST：`{labels.get('GOST_VERSION', '未安装')}`。",
                  f"- 源码：`{labels.get('COMMIT_SHA') or labels.get('org.opencontainers.image.revision', '未标注')}`。",
                  f"- 首次恢复健康：{number(ready.get('seconds'))} 秒；自动重启次数：{ready.get('auto_restarts', '未取得')}。"]
        for event in selected:
            if event["kind"] in ("restart", "log_growth", "readiness_failed", "stopped_proxy"):
                detail = {k: v for k, v in event.items() if k not in ("time", "stage", "kind")}
                lines.append(f"- {event['kind']}：`{json.dumps(detail, ensure_ascii=False)}`。")
        lines.append("")
    lines += ["## 最终部署及限制", ""]
    for event in events:
        if event["kind"] in ("baseline", "acceptance", "deployed", "restored_original", "fatal"):
            lines.append(f"- `{event['time']}` {event['kind']}：`{json.dumps({k:v for k,v in event.items() if k not in ('time','kind','stage')}, ensure_ascii=False)}`")
    lines += ["", "原始逐请求数据见同名 JSONL。20 次请求间隔 1 秒，稳定性窗口每 15 秒采样，共 40 次、持续 10 分钟；",
              "下载为 3 次 1MiB 的同一 Cloudflare URL。失败样本保留，未用重试覆盖失败。",
              "Azure 结果只代表 amd64 和本次网络条件，不能推断 ARM 网络性能、GOST 性能或 OpenAI 等站点可用性。",
              "Xray 路由未修改。两镜像客户端版本不同或适配后仍失败时，不把结果直接归因为代理实现的性能差异。", ""]
    output.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    print(output.with_suffix(".md"))


if __name__ == "__main__":
    main()
