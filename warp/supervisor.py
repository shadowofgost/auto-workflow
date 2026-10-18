"""Supervise the official WARP daemon without systemd or daemon logging flags."""

import datetime
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SEVERITY = re.compile(r"\b(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL|FATAL)\b")


def boolean(value):
    if value.lower() in ("true", "1", "yes"):
        return True
    if value.lower() in ("false", "0", "no"):
        return False
    raise ValueError("Boolean values must be true/false, 1/0 or yes/no")


class Supervisor:
    def __init__(self, env=None):
        self.env = dict(os.environ if env is None else env)
        self.level = self.env.get("WARP_LOG_LEVEL", "info").lower()
        if self.level not in ("info", "warn", "debug"):
            raise ValueError("WARP_LOG_LEVEL must be info, warn or debug")
        self.proxy = boolean(self.env.get("WARP_PROXY", "TRUE"))
        self.qlog = boolean(self.env.get("WARP_ENABLE_QLOG", "false"))
        self.port = int(self.env.get("WARP_PORT", "40000"))
        self.initial_wait = float(self.env.get("WARP_SLEEP", "2"))
        if not 1 <= self.port <= 65535 or not 0 <= self.initial_wait <= 60:
            raise ValueError("WARP_PORT must be 1..65535 and WARP_SLEEP must be 0..60")
        self.license = self.env.get("WARP_LICENSE_KEY", "")
        self.stopping = threading.Event()
        self.daemon = None

    def redact(self, text):
        text = ANSI.sub("", text)
        return text.replace(self.license, "[redacted]") if self.license else text

    def log(self, level, message):
        if self.level == "warn" and level == "INFO":
            return
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        print(f"{timestamp} {level} {self.redact(message)}", flush=True)

    def cli(self, *args, check=True, allow_errors=()):
        try:
            result = subprocess.run(
                ["warp-cli", "--accept-tos", "--no-ansi", *args], env=self.env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=8,
            )
        except subprocess.TimeoutExpired:
            if not check:
                return None
            raise RuntimeError(f"WARP command timed out: {' '.join(args[:2])}") from None
        allowed = any(message in result.stdout for message in allow_errors)
        if check and result.returncode and not allowed:
            raise RuntimeError(self.redact(f"WARP command failed: {' '.join(args[:2])}: {result.stdout.strip()}"))
        return result if result.returncode == 0 or allowed else None

    def daemon_logs(self):
        keep = True
        try:
            for raw in self.daemon.stdout:
                line = self.redact(raw.rstrip("\n"))
                match = SEVERITY.search(line)
                if match:
                    keep = self.level == "debug" or match.group(1) in ("WARN", "WARNING", "ERROR", "CRITICAL", "FATAL")
                if keep:
                    print(line, flush=True)
        except (ValueError, OSError):
            return

    def wait_ready(self):
        if self.stopping.wait(self.initial_wait):
            return False
        deadline = time.monotonic() + 60
        while not self.stopping.is_set() and time.monotonic() < deadline:
            if self.daemon.poll() is not None:
                raise RuntimeError("WARP daemon exited before becoming ready")
            if self.cli("status", check=False) is not None:
                return True
            self.stopping.wait(1)
        if self.stopping.is_set():
            return False
        raise RuntimeError("WARP daemon did not become ready within 60 seconds")

    def configure(self):
        registration = self.cli("registration", "show", check=False)
        if registration is None:
            if Path("/var/lib/cloudflare-warp/reg.json").exists():
                raise RuntimeError("Cannot read existing registration; preserving persisted identity")
            self.cli("registration", "new")
            registration = self.cli("registration", "show")
            self.log("INFO", "Registered WARP client")
        else:
            self.log("INFO", "Reusing existing WARP registration")
        if self.license:
            existing = re.search(r"^\s*License:\s*(.*?)\s*$", registration.stdout, re.MULTILINE | re.IGNORECASE)
            if existing is None:
                raise RuntimeError("Registration output has no License field; refusing to guess license state")
            if existing.group(1) != self.license:
                self.cli("registration", "license", self.license)
                self.log("INFO", "Updated WARP account license")
        self.cli("mode", "proxy" if self.proxy else "warp")
        if self.proxy:
            self.cli("proxy", "port", str(self.port))
        endpoint = self.env.get("WARP_CUSTOM_ENDPOINT", "")
        self.cli("tunnel", "endpoint", "set", endpoint) if endpoint else self.cli("tunnel", "endpoint", "reset")
        self.cli("debug", "extra", "stop", allow_errors=("Extra logging has not yet been started",))
        self.cli("debug", "qlog", "enable" if self.qlog else "disable")
        self.cli("connect")
        self.log("INFO", f"Configured mode={'proxy' if self.proxy else 'warp'} port={self.port} qlog={self.qlog}")

    def stop(self, *_):
        self.stopping.set()
        if self.daemon is not None and self.daemon.poll() is None:
            self.daemon.terminate()

    def run(self):
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        version = Path("/etc/warp-package-version")
        self.log("INFO", f"Starting WARP {version.read_text().strip() if version.exists() else 'unknown'}")
        self.daemon = subprocess.Popen(
            ["warp-svc", "--accept-tos"], env=self.env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1,
        )
        reader = threading.Thread(target=self.daemon_logs, daemon=True)
        reader.start()
        exit_code = 0
        try:
            if not self.wait_ready():
                return 0
            self.configure()
            previous = None
            next_status = 0
            while not self.stopping.wait(0.5):
                code = self.daemon.poll()
                if code is not None:
                    self.log("ERROR", f"WARP daemon exited with status {code}")
                    return code if code >= 0 else 128 - code
                if time.monotonic() >= next_status:
                    result = self.cli("status", check=False)
                    if result is None:
                        self.log("WARN", "Status query failed; daemon is still running")
                    else:
                        status = self.redact(result.stdout.strip())
                        if status != previous:
                            self.log("INFO", f"Connection state: {status}")
                            previous = status
                    next_status = time.monotonic() + 30
        except (RuntimeError, OSError) as error:
            self.log("ERROR", str(error))
            code = self.daemon.poll()
            exit_code = 0 if self.stopping.is_set() else (code if code is not None and code > 0 else 1)
        finally:
            self.stop()
            try:
                self.daemon.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.daemon.kill()
                self.daemon.wait()
            reader.join(timeout=2)
        return exit_code


def main():
    try:
        return Supervisor().run()
    except (ValueError, OSError) as error:
        print(f"ERROR Invalid WARP configuration: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
