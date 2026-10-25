"""Root-only, sequential Azure comparison with a cold snapshot and automatic rollback.

Run from a private work directory on Azure after both images have been built.
The original container is retained (stopped/renamed) until acceptance completes.
NET_ADMIN is opt-in; host-network deltas are monitored and recovered independently.
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
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import azure_network as network

DATA = Path("/home/shadowofgost/Docker/warp/data")
TRACE = "https://cloudflare.com/cdn-cgi/trace"
METRICS = "\n__WARP_METRICS__"
COMPOSE = Path("/home/shadowofgost/Docker/azure.yml")


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
        self.net_admin = getattr(args, "net_admin", False)
        self.backup_name = "warp-before-" + re.sub(r"[^a-zA-Z0-9-]", "-", self.work.name)[-60:]
        self.host_baseline = None
        self.services_baseline = None
        self.identity_before = None
        self.completed = False
        self.heartbeat_stop = threading.Event()

    def checkpoint(self):
        state = {"pid": os.getpid(), "args": vars(self.args), "backup_name": self.backup_name,
                 "rollback_needed": self.rollback_needed, "snapshot_ready": self.snapshot_ready,
                 "identity_before": self.identity_before, "completed": self.completed}
        temporary = self.work / "control.json.tmp"
        temporary.write_text(json.dumps(state))
        temporary.replace(self.work / "control.json")

    def heartbeat(self):
        while not self.heartbeat_stop.is_set():
            (self.work / "heartbeat").touch()
            self.heartbeat_stop.wait(5)

    def start_watchdog(self):
        self.checkpoint()
        threading.Thread(target=self.heartbeat, daemon=True).start()
        self.command("systemd-run", "--quiet", "--unit", "warp-watch-" + self.work.name[-45:],
                     "--property=RuntimeMaxSec=5700", sys.executable, str(Path(__file__).resolve()),
                     "--watchdog", "--work", str(self.work), timeout=20)
        deadline = time.monotonic() + 10
        while not (self.work / "watchdog-ready").exists():
            if time.monotonic() > deadline:
                raise RuntimeError("Independent watchdog did not become ready")
            time.sleep(0.2)
        self.event("watchdog_armed", heartbeat_timeout=90, maximum_seconds=5400)

    def host_snapshot(self):
        return network.snapshot(getattr(self.args, "ssh_peer", None))

    def service_probes(self):
        import socket
        try:
            with socket.create_connection(("127.0.0.1", 22), timeout=3) as connection:
                ssh = connection.recv(255).startswith(b"SSH-")
        except OSError:
            ssh = False
        nginx = self.command("curl", "--noproxy", "*", "-ksS", "--connect-timeout", "3",
                             "--max-time", "5", "-o", "/dev/null", "-w", "%{http_code}",
                             "https://127.0.0.1", check=False, timeout=8)
        xray = self.command(sys.executable, str(Path(__file__).with_name("test-azure-warp.py")),
                            "--xray-only", check=False, timeout=25)
        rows = [json.loads(line) for line in xray.stdout.splitlines() if line.startswith("{")]
        return {"ssh": ssh, "nginx": nginx.stdout.strip() if nginx.returncode == 0 else None,
                "xray": next((row for row in rows if row.get("probe") == "live_xray_vless_ws"), {})}

    def host_check(self, services=True):
        if self.phase != "rollback" and (self.work / "abort-requested").exists():
            raise RuntimeError("Independent watchdog requested rollback")
        if self.host_baseline is None:
            return
        current = self.host_snapshot()
        changes = network.summary(self.host_baseline, current)
        if services:
            probes = self.service_probes()
            direct = self.curl(proxy=False)
            changes.update(services=probes, direct=direct)
            if not direct["ok"]:
                changes["problems"].append("Host direct trace failed or uses WARP")
            for key in ("ssh", "nginx"):
                if self.services_baseline[key] and probes[key] != self.services_baseline[key]:
                    changes["problems"].append(f"Existing {key} service regressed")
            baseline_xray = self.services_baseline["xray"].get("warp")
            if baseline_xray and probes["xray"].get("warp") != baseline_xray:
                changes["problems"].append("Existing Xray trace regressed or changed route")
        self.event("host_network", **changes)
        if changes["problems"]:
            raise RuntimeError("Host regression: " + "; ".join(changes["problems"]))

    def cleanup_network(self):
        if self.host_baseline is not None:
            actions = network.restore(self.host_baseline)
            self.event("network_recovery", actions=actions)
            self.host_check(services=False)

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
        return json.loads(self.command("docker", "inspect", name, timeout=60).stdout)[0]

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
            if (self.work / "abort-requested").exists():
                raise RuntimeError("Host watchdog requested rollback")
            state = self.inspect()["State"]
            latest = self.curl(max_time=4)
            self.event("readiness_probe", health=state.get("Health", {}).get("Status"), **latest)
            if state.get("Health", {}).get("Status") == "healthy" and latest["ok"] and time.monotonic() - start <= limit:
                self.event("ready", seconds=round(time.monotonic() - start, 3), trace=latest,
                           auto_restarts=self.inspect()["RestartCount"])
                return True
            time.sleep(2)
        self.event("readiness_failed", last_trace=latest, state=self.inspect()["State"]["Status"])
        self.diagnostics()
        return False

    def diagnostics(self):
        container = self.inspect()
        result = self.command("docker", "logs", "--tail", "400", "warp", check=False, timeout=20)
        lines = []
        for raw in (result.stdout + result.stderr).splitlines():
            line = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw)
            if re.search(r"fwmark.*Operation not permitted|mknod:|INFO Configured mode|ERROR WARP command", line):
                lines.append(self.safe(line))
        pid = container["State"]["Pid"]
        version = "not running"
        daemons = []
        if pid:
            version = self.command("chroot", f"/proc/{pid}/root", "/usr/bin/warp-cli", "--version", check=False, timeout=10).stdout.strip()
            top = self.command("docker", "top", "warp", "-eo", "pid,comm", check=False, timeout=10)
            rows = [{"pid": fields[0], "command": fields[1]} for line in top.stdout.splitlines()[1:]
                    if len(fields := line.split(None, 1)) == 2]
            daemons = network.daemon_capabilities(rows)
        self.event("diagnostics", image=container["Image"], version=version,
                   configured_cap_add=container.get("HostConfig", {}).get("CapAdd") or [],
                   daemon_capabilities=daemons,
                   cap_net_admin=all(row["net_admin"] for row in daemons) if daemons else None,
                   restarts=container["RestartCount"], lines=lines[-12:])

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
            if self.original and json.loads(result.stdout)[0]["Id"] == self.original["Id"]:
                return
            self.command("docker", "stop", "--time", "20", "warp", timeout=40, check=False)
            self.command("docker", "rm", "warp", timeout=30)
            self.cleanup_network()

    def launch(self, image, adapted=False, production=False, net_admin=None, stock=False):
        old = self.original
        health = old["Config"]["Healthcheck"]
        args = ["docker", "run", "-d", "--name", "warp", "--network", "host",
                "--restart", "unless-stopped", "--env-file", str(self.work / "environment"),
                "--expose", "40000", "--volume", f"{DATA}:/var/lib/cloudflare-warp",
                "--log-driver", "json-file", "--log-opt", "max-size=10m", "--log-opt", "max-file=3",
                "--health-cmd", health["Test"][1], "--health-interval", "15s", "--health-timeout", "5s",
                "--health-start-period", "60s", "--health-retries", "3"]
        grant = self.net_admin if net_admin is None else net_admin
        if grant:
            args += ["--cap-add", "NET_ADMIN"]
        differences = {}
        if stock:
            differences = {"GOST_ARGS": "-L 127.0.0.1:1080", "WARP_ENABLE_NAT": ""}
            for key, value in differences.items():
                args += ["--env", f"{key}={value}"]
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
                   size=image_info["Size"], adapted=adapted, net_admin=grant,
                   entrypoint=inspected["Config"].get("Entrypoint"), parameter_differences=differences)

    def snapshot(self):
        self.original = self.inspect()
        if self.command("docker", "inspect", self.backup_name, check=False).returncode == 0:
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
        shutil.copyfile(COMPOSE, self.work / "azure.yml.before")
        (self.work / "azure.yml.before").chmod(0o600)
        compose_stat = COMPOSE.stat()
        (self.work / "compose-metadata.json").write_text(json.dumps({"uid": compose_stat.st_uid,
             "gid": compose_stat.st_gid, "mode": compose_stat.st_mode & 0o777}))
        self.command("docker", "compose", "-f", str(COMPOSE), "config", "--quiet")
        self.event("baseline", image=self.original["Image"],
                   state=self.original["State"]["Status"], health=self.original["State"].get("Health", {}).get("Status"),
                   restarts=self.original["RestartCount"], log_config=self.original["HostConfig"]["LogConfig"],
                   direct=self.curl(proxy=False), proxy=self.curl())
        self.command("docker", "pull", self.args.image, timeout=600)
        self.command("docker", "pull", self.args.upstream, timeout=600)
        for attribute in ("image", "upstream"):
            reference = getattr(self.args, attribute)
            info = json.loads(self.command("docker", "image", "inspect", reference).stdout)[0]
            pinned = reference if "@sha256:" in reference else info["RepoDigests"][0]
            setattr(self.args, attribute, pinned)
        self.event("images_pulled")
        data_size = sum(path.stat().st_size for path in DATA.rglob("*") if path.is_file())
        log = self.original.get("LogPath")
        log_size = Path(log).stat().st_size if log and Path(log).exists() else 0
        if shutil.disk_usage(self.work).free < log_size + data_size * 3 + 2 * 1024 ** 3:
            raise RuntimeError("Insufficient space for private cold backup and log archive")
        self.host_baseline = self.host_snapshot()
        (self.work / "network-before.json").write_text(json.dumps(self.host_baseline))
        self.services_baseline = self.service_probes()
        (self.work / "services-before.json").write_text(json.dumps(self.services_baseline))
        self.event("services_baseline", **self.services_baseline)
        self.start_watchdog()
        watchtower = self.inspect("watchtower")
        (self.work / "watchtower-running").write_text(str(watchtower["State"]["Running"]))
        self.rollback_needed = True
        self.checkpoint()
        if watchtower["State"]["Running"]:
            self.command("docker", "stop", "--time", "20", "watchtower", timeout=90)
        self.command("docker", "stop", "--time", "20", "warp", timeout=45)
        self.command("docker", "rename", "warp", self.backup_name)
        self.command("tar", "-cpf", str(self.work / "data.tar"), "-C", str(DATA), ".", timeout=120)
        self.snapshot_ready = True
        self.identity_before = self.registration_identity()
        self.checkpoint()
        if log and Path(log).exists():
            shutil.copyfile(log, self.work / "original-docker.log")
            (self.work / "original-docker.log").chmod(0o600)
        (self.work / "adapter.sh").write_text("#!/bin/sh\nset -eu\nexec sudo -E python3 /warp-benchmark/supervisor.py\n")
        (self.work / "adapter.sh").chmod(0o755)
        self.event("snapshot_complete", registration=self.identity_before, archived_log_bytes=log_size,
                   backup_container=self.backup_name)

    def runtime_info(self):
        pid = str(self.inspect()["State"]["Pid"])
        result = self.command("chroot", f"/proc/{pid}/root", "/usr/bin/warp-cli", "--version", timeout=10, check=False)
        self.event("runtime", version=result.stdout.strip(), registration=self.registration_identity())
        self.diagnostics()

    def phase_test(self, name, image, adapted=False, stability=True, restarts=True):
        self.phase = name
        self.remove_trial()
        self.restore_data()
        self.launch(image, adapted=adapted)
        self.host_check(services=False)
        if not self.wait_healthy():
            self.event("phase_result", passed=False)
            return False
        self.runtime_info()
        self.host_check()
        identity = self.registration_identity()
        passed = self.inspect()["RestartCount"] == 0
        for index in range(20):
            if (self.work / "abort-requested").exists():
                raise RuntimeError("Host watchdog requested rollback")
            result = self.curl()
            passed = passed and result["ok"]
            self.event("trace", index=index, **result)
            time.sleep(1)
        for index in range(3):
            result = self.curl(download=True, max_time=20)
            passed = passed and result["ok"] and result["bytes"] == 1048576
            self.event("download", index=index, **result)
        self.host_check()
        if restarts:
            for index in range(2):
                self.command("docker", "restart", "--time", "20", "warp", timeout=45)
                ready = self.wait_healthy()
                same_identity = self.registration_identity() == identity
                passed = passed and ready and same_identity and self.inspect()["RestartCount"] == 0
                self.event("restart", index=index, ready=ready, identity_preserved=same_identity)
                self.host_check()
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
                self.host_check(services=index % 4 == 0)
            time.sleep(max(0, started + 600 - time.monotonic()))
            state = self.inspect()
            growth = Path(state["LogPath"]).stat().st_size - start_log
            passed = passed and state["RestartCount"] == initial_restarts
            self.event("log_growth", bytes=growth, seconds=600)
        self.host_check()
        self.event("phase_result", passed=passed)
        return passed

    def rollback(self):
        self.phase = "rollback"
        self.remove_trial()
        if self.snapshot_ready:
            self.restore_data()
        self.cleanup_network()
        if self.command("docker", "inspect", self.backup_name, check=False).returncode == 0:
            self.command("docker", "rename", self.backup_name, "warp")
        if self.original["State"]["Running"]:
            self.command("docker", "start", "warp", timeout=45)
        if (self.work / "azure.yml.before").exists():
            shutil.copyfile(self.work / "azure.yml.before", COMPOSE)
            if (self.work / "compose-metadata.json").exists():
                metadata = json.loads((self.work / "compose-metadata.json").read_text())
                os.chown(COMPOSE, metadata["uid"], metadata["gid"])
                COMPOSE.chmod(metadata["mode"])
        self.rollback_needed = False
        self.event("restored_original", state=self.inspect()["State"]["Status"])
        self.checkpoint()

    def persist_compose(self, image):
        original = COMPOSE.read_text()
        revised = network.update_compose(original, image, self.net_admin)
        temporary = COMPOSE.with_name(".azure-warp-candidate.yml")
        metadata = COMPOSE.stat()
        try:
            temporary.write_text(revised)
            os.chown(temporary, metadata.st_uid, metadata.st_gid)
            temporary.chmod(metadata.st_mode & 0o777)
            self.command("docker", "compose", "--project-directory", str(COMPOSE.parent),
                         "-f", str(temporary), "config", "--quiet")
            temporary.replace(COMPOSE)
            self.command("docker", "compose", "-f", str(COMPOSE), "config", "--quiet")
        finally:
            if temporary.exists():
                temporary.unlink()

    def finish(self, commit=False):
        marker = self.work / "watchtower-running"
        if marker.exists() and marker.read_text() == "True":
            self.command("docker", "start", "watchtower", timeout=45)
        if self.original and (self.work / "azure.yml.before").exists():
            current = self.inspect()
            self.event("verification", original_image_restored=current["Image"] == self.original["Image"],
                       identity_preserved=self.identity_before == self.registration_identity() if self.identity_before else None,
                       compose_restored=COMPOSE.read_bytes() == (self.work / "azure.yml.before").read_bytes(),
                       health=current["State"].get("Health", {}).get("Status"),
                       watchtower_running=self.inspect("watchtower")["State"]["Running"])
        if commit:
            self.rollback_needed = False
        self.completed = True
        self.checkpoint()
        self.heartbeat_stop.set()

    def run(self):
        if self.output.exists() or (self.work / "control.json").exists():
            raise RuntimeError("Run directory already contains evidence; choose a new --work")
        if os.geteuid() != 0:
            raise RuntimeError("Run with sudo to preserve data ownership and protect the snapshot")
        import fcntl
        self.lock = open("/var/lock/warp-azure-compare.lock", "w")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            self.snapshot()
            if self.net_admin:
                self.phase = "own-without-net-admin"
                self.restore_data()
                self.launch(self.args.image, net_admin=False)
                control = self.wait_healthy()
                self.event("compatibility", passed=control, purpose="same-image permission control")
                self.remove_trial()
            own = self.phase_test("own", self.args.image)
            self.phase = "upstream-stock-constrained"
            self.remove_trial()
            self.restore_data()
            occupied = self.command("ss", "-H", "-lnt", "sport = :1080").stdout.strip()
            if occupied:
                self.event("skipped", reason="Loopback GOST port 1080 already occupied")
            else:
                self.launch(self.args.upstream, stock=True)
                compatible = self.wait_healthy()
                self.host_check()
                self.event("compatibility", passed=compatible, purpose="constrained stock entrypoint; not GOST performance")
                self.runtime_info()
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
                    self.host_check(services=False)
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
            self.host_check()
            self.persist_compose(self.args.production_image or self.args.image)
            self.event("deployed", image=self.inspect()["Image"], trace=self.curl())
            self.deployment_ready = True
            return 0
        except BaseException as error:
            self.event("fatal", error=self.safe(error))
            if self.rollback_needed:
                self.rollback()
            raise
        finally:
            if getattr(self, "deployment_ready", False):
                try:
                    self.finish(commit=True)
                except BaseException:
                    self.rollback()
                    self.finish()
                    raise
            elif not self.rollback_needed:
                self.finish()
            self.lock.close()


def recover(work):
    from types import SimpleNamespace
    state = json.loads((work / "control.json").read_text())
    if state["completed"]:
        return
    import fcntl
    with (work / "recovery.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads((work / "control.json").read_text())
        if state["completed"]:
            return
        trial = Comparison(SimpleNamespace(**state["args"]))
        trial.original = json.loads((work / "original.json").read_text())
        trial.secret = next((item.split("=", 1)[1] for item in trial.original["Config"]["Env"]
                             if item.startswith("WARP_LICENSE_KEY=")), "")
        trial.backup_name = state["backup_name"]
        trial.rollback_needed = state["rollback_needed"]
        trial.snapshot_ready = state["snapshot_ready"]
        trial.identity_before = state["identity_before"]
        trial.host_baseline = json.loads((work / "network-before.json").read_text())
        trial.services_baseline = json.loads((work / "services-before.json").read_text())
        if trial.rollback_needed:
            trial.rollback()
        trial.finish()


def watchdog(work):
    (work / "watchdog-ready").touch()
    started = time.monotonic()
    while True:
        state = json.loads((work / "control.json").read_text())
        if state["completed"]:
            return 0
        try:
            os.kill(state["pid"], 0)
            alive = True
        except ProcessLookupError:
            alive = False
        heartbeat = work / "heartbeat"
        stale = not heartbeat.exists() or time.time() - heartbeat.stat().st_mtime > 90
        problems = []
        try:
            before = json.loads((work / "network-before.json").read_text())
            problems = network.regressions(before, network.snapshot(state["args"].get("ssh_peer")))
        except Exception as error:
            problems = [str(error)]
        if not alive or stale or problems or time.monotonic() - started > 5400:
            reason = {"controller_alive": alive, "heartbeat_stale": stale, "problems": problems,
                      "deadline": time.monotonic() - started > 5400}
            (work / "abort-requested").write_text(json.dumps(reason))
            with (work / "results.jsonl").open("a") as handle:
                handle.write(json.dumps({"time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                         "stage": "watchdog", "kind": "watchdog_abort", **reason}) + "\n")
            if alive:
                unit = state["args"].get("controller_unit")
                if unit:
                    subprocess.run(["systemctl", "kill", "--signal=SIGTERM", "--kill-whom=all", unit],
                                   capture_output=True, timeout=10)
                else:
                    os.kill(state["pid"], signal.SIGTERM)
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    if json.loads((work / "control.json").read_text())["completed"]:
                        return 0
                    try:
                        os.kill(state["pid"], 0)
                    except ProcessLookupError:
                        break
                    time.sleep(1)
                if unit:
                    subprocess.run(["systemctl", "kill", "--signal=SIGKILL", "--kill-whom=all", unit],
                                   capture_output=True, timeout=10)
                else:
                    try:
                        os.kill(state["pid"], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            recover(work)
            return 1
        time.sleep(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image")
    parser.add_argument("--upstream", default="caomingjun/warp:latest")
    parser.add_argument("--work", default="/var/tmp/warp-upgrade-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--supervisor", default="/tmp/warp-supervisor-20261018.py")
    parser.add_argument("--production-image", help="Require a promotion-approved gate before deploying this tag")
    parser.add_argument("--net-admin", action="store_true", help="Grant NET_ADMIN and include a same-image no-capability control")
    parser.add_argument("--ssh-peer", default=os.environ.get("SSH_CONNECTION", "").split(" ")[0] or None)
    parser.add_argument("--controller-unit", help="systemd unit used to terminate the entire controller process tree")
    parser.add_argument("--watchdog", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--recover", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.watchdog or args.recover:
        work = Path(args.work).resolve()
        if not re.fullmatch(r"/var/tmp/warp-upgrade-[a-zA-Z0-9-]+", str(work)):
            parser.error("Unexpected private recovery directory")
        return watchdog(work) if args.watchdog else recover(work)
    if not args.image:
        parser.error("--image is required")
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(InterruptedError("Comparison terminated")))
    return Comparison(args).run()


if __name__ == "__main__":
    sys.exit(main())
