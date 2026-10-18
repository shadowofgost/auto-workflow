"""Root-only, sequential Azure comparison with a cold snapshot and automatic rollback.

Run from a private work directory on Azure after both images have been built.
The original container is retained (stopped/renamed) until acceptance completes.
No Xray configuration or host route is changed.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

DATA = Path("/home/shadowofgost/Docker/warp/data")
TRACE = "https://cloudflare.com/cdn-cgi/trace"
METRICS = "\n__WARP_METRICS__"


class Comparison:
    def __init__(self, args):
        self.args = args
        os.umask(0o077)
        self.work = Path(args.work).resolve()
        self.work.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.work.chmod(0o700)
        self.output = self.work / "results.jsonl"
        self.original = None
        self.secret = ""
        self.phase = "preflight"
        self.rollback_needed = False
        self.snapshot_ready = False

    def safe(self, value):
        text = str(value)
        return text.replace(self.secret, "[redacted]") if self.secret else text

    def event(self, kind, **fields):
        record = {"time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  "stage": self.phase, "kind": kind, **fields}
        line = self.safe(json.dumps(record, ensure_ascii=False))
        with self.output.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        print(line, flush=True)

    def command(self, *args, timeout=45, check=True):
        result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError(self.safe(f"Command failed ({args[0]} {args[1:3]}): {result.stderr[-800:]}"))
        return result

    def inspect(self, name="warp"):
        return json.loads(self.command("docker", "inspect", name, timeout=20).stdout)[0]

    def curl(self, proxy=True, download=False, max_time=15):
        args = ["curl", "--noproxy", "" if proxy else "*", "-fsS", "--connect-timeout", "3", "--max-time", str(max_time)]
        if proxy:
            args += ["--socks5-hostname", "127.0.0.1:40000"]
        if download:
            args += ["-o", "/dev/null"]
        args += ["--write-out", METRICS + "%{json}",
                 "https://speed.cloudflare.com/__down?bytes=1048576" if download else TRACE]
        result = self.command(*args, timeout=max_time + 5, check=False)
        body, _, encoded = result.stdout.rpartition(METRICS)
        metrics = json.loads(encoded) if encoded else {}
        trace = dict(line.split("=", 1) for line in body.splitlines() if "=" in line)
        expected_route = download or (trace.get("warp") in ("on", "plus") if proxy else trace.get("warp") == "off")
        valid = metrics.get("http_code") == 200 and result.returncode == 0 and expected_route
        return {"ok": bool(valid), "proxy": proxy, "download": download,
                "returncode": result.returncode, "ip": trace.get("ip"), "warp": trace.get("warp"),
                "http_code": metrics.get("http_code"), "ttfb": metrics.get("time_starttransfer"),
                "total": metrics.get("time_total"), "bytes": metrics.get("size_download"),
                "bytes_per_second": metrics.get("speed_download"), "error": self.safe(result.stderr.strip())[:300]}

    def wait_healthy(self, limit=120):
        start = time.monotonic()
        latest = None
        while time.monotonic() - start < limit:
            state = self.inspect()["State"]
            latest = self.curl(max_time=4)
            if state.get("Health", {}).get("Status") == "healthy" and latest["ok"]:
                self.event("ready", seconds=round(time.monotonic() - start, 3), trace=latest,
                           auto_restarts=self.inspect()["RestartCount"])
                return True
            time.sleep(2)
        self.event("readiness_failed", last_trace=latest, state=self.inspect()["State"]["Status"])
        return False

    def registration_identity(self):
        data = json.loads((DATA / "reg.json").read_text())
        # Persisted key and identity fields only; account/license metadata may change.
        identity = {key: value for key, value in data.items()
                    if key.lower() in ("id", "device_id", "private_key", "public_key", "registration_id")}
        if not identity:
            raise RuntimeError("Unknown registration schema: cannot assert persisted identity")
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def restore_data(self):
        if DATA.resolve() != DATA or DATA.parent != Path("/home/shadowofgost/Docker/warp"):
            raise RuntimeError("Unexpected production data path")
        if DATA.exists():
            archive = self.work / f"used-data-{self.phase}-{time.time_ns()}"
            shutil.move(str(DATA), str(archive))
            archive.chmod(0o700)
        DATA.mkdir(mode=0o700)
        self.command("tar", "--same-owner", "-xpf", str(self.work / "data.tar"), "-C", str(DATA), timeout=120)

    def remove_trial(self):
        result = self.command("docker", "inspect", "warp", timeout=20, check=False)
        if result.returncode == 0:
            self.command("docker", "stop", "--time", "20", "warp", timeout=40, check=False)
            self.command("docker", "rm", "warp", timeout=30)

    def launch(self, image, adapted=False, production=False):
        old = self.original
        health = old["Config"]["Healthcheck"]
        args = ["docker", "run", "-d", "--name", "warp", "--network", "host",
                "--restart", "unless-stopped", "--env-file", str(self.work / "environment"),
                "--expose", "40000", "--volume", f"{DATA}:/var/lib/cloudflare-warp",
                "--log-driver", "json-file", "--log-opt", "max-size=10m", "--log-opt", "max-file=3",
                "--health-cmd", health["Test"][1], "--health-interval", "15s", "--health-timeout", "5s",
                "--health-start-period", "60s", "--health-retries", "3"]
        if production:
            for key, value in old["Config"].get("Labels", {}).items():
                args += ["--label", f"{key}={value}"]
        if adapted:
            args += ["--volume", f"{self.args.supervisor}:/warp-benchmark/supervisor.py:ro",
                     "--volume", f"{self.work / 'adapter.sh'}:/warp-benchmark/adapter.sh:ro",
                     "--entrypoint", "/bin/sh"]
        args += [image]
        if adapted:
            args += ["/warp-benchmark/adapter.sh"]
        self.command(*args, timeout=90)
        inspected = self.inspect()
        image_info = json.loads(self.command("docker", "image", "inspect", inspected["Image"]).stdout)[0]
        self.event("image", reference=image, id=inspected["Image"],
                   digests=image_info.get("RepoDigests"), labels=image_info["Config"].get("Labels", {}),
                   size=image_info["Size"], adapted=adapted)

    def snapshot(self):
        self.original = self.inspect()
        if self.command("docker", "inspect", "warp-before-upgrade-20261018", check=False).returncode == 0:
            raise RuntimeError("An earlier rollback container exists; refusing to overwrite recovery state")
        (self.work / "original.json").write_text(json.dumps(self.original))
        (self.work / "original.json").chmod(0o600)
        environment = self.original["Config"]["Env"]
        for item in environment:
            if item.startswith("WARP_LICENSE_KEY="):
                self.secret = item.split("=", 1)[1]
        required = {"WARP_SLEEP": "3", "WARP_PROXY": "TRUE", "WARP_PORT": "40000"}
        actual = dict(item.split("=", 1) for item in environment)
        if any(actual.get(key) != value for key, value in required.items()):
            raise RuntimeError("Live WARP environment differs from the agreed benchmark")
        if self.original["HostConfig"]["NetworkMode"] != "host":
            raise RuntimeError("Live WARP does not use host networking")
        (self.work / "environment").write_text("\n".join(environment) + "\n")
        (self.work / "environment").chmod(0o600)
        self.event("baseline", image=self.original["Image"],
                   state=self.original["State"]["Status"], health=self.original["State"].get("Health", {}).get("Status"),
                   restarts=self.original["RestartCount"], log_config=self.original["HostConfig"]["LogConfig"],
                   direct=self.curl(proxy=False), proxy=self.curl())
        self.command("docker", "pull", self.args.image, timeout=600)
        self.command("docker", "pull", self.args.upstream, timeout=600)
        self.event("images_pulled")
        watchtower = self.inspect("watchtower")
        (self.work / "watchtower-running").write_text(str(watchtower["State"]["Running"]))
        if watchtower["State"]["Running"]:
            self.command("docker", "stop", "--time", "20", "watchtower", timeout=45)
        self.command("docker", "stop", "--time", "20", "warp", timeout=45)
        self.command("docker", "rename", "warp", "warp-before-upgrade-20261018")
        self.rollback_needed = True
        self.command("tar", "-cpf", str(self.work / "data.tar"), "-C", str(DATA), ".", timeout=120)
        self.snapshot_ready = True
        log = self.original.get("LogPath")
        if log and Path(log).exists():
            shutil.copy2(log, self.work / "original-docker.log")
        compose = Path("/home/shadowofgost/Docker/azure.yml")
        shutil.copy2(compose, self.work / "azure.yml.before")
        (self.work / "adapter.sh").write_text("#!/bin/sh\nset -eu\nexec sudo -E python3 /warp-benchmark/supervisor.py\n")
        (self.work / "adapter.sh").chmod(0o755)
        # Traverse-only access to the adapter for the upstream USER warp; secrets stay 0600.
        self.work.chmod(0o711)
        self.event("snapshot_complete", registration=self.registration_identity())

    def runtime_info(self):
        pid = str(self.inspect()["State"]["Pid"])
        result = self.command("chroot", f"/proc/{pid}/root", "/usr/bin/warp-cli", "--version", timeout=10, check=False)
        self.event("runtime", version=result.stdout.strip(), registration=self.registration_identity())

    def phase_test(self, name, image, adapted=False, stability=True, restarts=True):
        self.phase = name
        self.remove_trial()
        self.restore_data()
        self.launch(image, adapted=adapted)
        if not self.wait_healthy():
            self.event("phase_result", passed=False)
            return False
        self.runtime_info()
        identity = self.registration_identity()
        passed = self.inspect()["RestartCount"] == 0
        for index in range(20):
            result = self.curl()
            passed = passed and result["ok"]
            self.event("trace", index=index, **result)
            time.sleep(1)
        for index in range(3):
            result = self.curl(download=True, max_time=20)
            passed = passed and result["ok"] and result["bytes"] == 1048576
            self.event("download", index=index, **result)
        if restarts:
            for index in range(2):
                self.command("docker", "restart", "--time", "20", "warp", timeout=45)
                ready = self.wait_healthy()
                same_identity = self.registration_identity() == identity
                passed = passed and ready and same_identity and self.inspect()["RestartCount"] == 0
                self.event("restart", index=index, ready=ready, identity_preserved=same_identity)
            self.command("docker", "stop", "--time", "20", "warp", timeout=45)
            negative = self.curl(max_time=4)
            self.event("stopped_proxy", **negative)
            passed = passed and not negative["ok"] and negative["returncode"] != 0
            self.command("docker", "start", "warp", timeout=45)
            passed = self.wait_healthy() and passed
        if stability:
            initial = self.inspect()
            initial_restarts = initial["RestartCount"]
            start_log = Path(initial["LogPath"]).stat().st_size
            started = time.monotonic()
            for index in range(40):
                target = started + index * 15
                time.sleep(max(0, target - time.monotonic()))
                result = self.curl()
                state = self.inspect()
                stats = self.command("docker", "stats", "--no-stream", "--format", "{{json .}}", "warp", timeout=15, check=False)
                passed = passed and result["ok"] and state["RestartCount"] == initial_restarts
                self.event("stability", index=index, restarts=state["RestartCount"], stats=stats.stdout.strip(), **result)
            time.sleep(max(0, started + 600 - time.monotonic()))
            state = self.inspect()
            growth = Path(state["LogPath"]).stat().st_size - start_log
            passed = passed and state["RestartCount"] == initial_restarts
            self.event("log_growth", bytes=growth, seconds=600)
        self.event("phase_result", passed=passed)
        return passed

    def rollback(self):
        self.phase = "rollback"
        self.remove_trial()
        if self.snapshot_ready:
            self.restore_data()
        self.command("docker", "rename", "warp-before-upgrade-20261018", "warp")
        self.command("docker", "start", "warp", timeout=45)
        if (self.work / "azure.yml.before").exists():
            shutil.copy2(self.work / "azure.yml.before", "/home/shadowofgost/Docker/azure.yml")
        self.rollback_needed = False
        self.event("restored_original", state=self.inspect()["State"]["Status"])

    def run(self):
        if os.geteuid() != 0:
            raise RuntimeError("Run with sudo to preserve data ownership and protect the snapshot")
        try:
            self.snapshot()
            own = self.phase_test("own", self.args.image)
            # Exact compatibility check uses the original upstream entrypoint and same parameters.
            self.phase = "upstream-unmodified"
            self.remove_trial()
            self.restore_data()
            self.launch(self.args.upstream)
            compatible = self.wait_healthy(limit=60)
            self.event("compatibility", passed=compatible)
            upstream = self.phase_test("upstream-adapted", self.args.upstream, adapted=True)
            repeat = self.phase_test("own-repeat", self.args.image, stability=False, restarts=False)
            self.event("acceptance", own=own, upstream=upstream, repeat=repeat)
            if not own or not repeat:
                self.rollback()
                return 1
            if self.args.production_image:
                self.event("awaiting_promotion", source=self.args.image)
                deadline = time.monotonic() + 1200
                while not (self.work / "promotion-approved").exists():
                    if time.monotonic() > deadline:
                        raise RuntimeError("Timed out waiting for promotion of the tested digest")
                    time.sleep(5)
                self.command("docker", "pull", self.args.production_image, timeout=600)
                candidate_id = json.loads(self.command("docker", "image", "inspect", self.args.image).stdout)[0]["Id"]
                production_id = json.loads(self.command("docker", "image", "inspect", self.args.production_image).stdout)[0]["Id"]
                if candidate_id != production_id:
                    raise RuntimeError("Production tag does not match the tested image")
            self.phase = "production"
            self.remove_trial()
            self.restore_data()
            self.launch(self.args.production_image or self.args.image, production=True)
            if not self.wait_healthy():
                self.rollback()
                return 1
            # Make log rotation durable in the existing deployment source without changing other services.
            compose = Path("/home/shadowofgost/Docker/azure.yml")
            text = compose.read_text()
            marker = "    warp:\n"
            if text.count(marker) != 1:
                raise RuntimeError("Cannot locate the exact WARP service for durable log rotation")
            logging = '        logging:\n            driver: json-file\n            options:\n                max-size: "10m"\n                max-file: "3"\n'
            text = text.replace(marker, marker + logging, 1)
            compose.write_text(text)
            self.rollback_needed = False
            self.event("deployed", image=self.inspect()["Image"], trace=self.curl())
            return 0
        except BaseException as error:
            self.event("fatal", error=self.safe(error))
            if self.rollback_needed:
                self.rollback()
            raise
        finally:
            marker = self.work / "watchtower-running"
            if marker.exists() and marker.read_text() == "True":
                self.command("docker", "start", "watchtower", timeout=45, check=False)
            self.work.chmod(0o700)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--upstream", default="caomingjun/warp:latest")
    parser.add_argument("--work", default="/var/tmp/warp-upgrade-20261018")
    parser.add_argument("--supervisor", default="/tmp/warp-supervisor-20261018.py")
    parser.add_argument("--production-image", help="Require a promotion-approved gate before deploying this tag")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(InterruptedError("Comparison terminated")))
    return Comparison(args).run()


if __name__ == "__main__":
    sys.exit(main())
