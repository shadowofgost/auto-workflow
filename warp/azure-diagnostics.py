"""Collect only allowlisted, redacted startup evidence from the current trial."""
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import azure_network as network

work = Path(sys.argv[1]).resolve()
if not re.fullmatch(r"/var/tmp/warp-upgrade-[a-zA-Z0-9-]+", str(work)):
    raise SystemExit("Unexpected comparison directory")


def run(*args):
    result = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=15)
    return result.stdout + result.stderr


container = json.loads(run("docker", "inspect", "warp"))[0]
secrets = [item.split("=", 1)[1] for item in container["Config"]["Env"]
           if item.startswith("WARP_LICENSE_KEY=") and item.split("=", 1)[1]]
lines = []
daemon_log = run("docker", "logs", "--tail", "400", "warp")
for line in daemon_log.splitlines():
    line = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line)
    if re.search(r"fwmark.*Operation not permitted|mknod:|not permitted|INFO (Starting WARP|Configured mode|Reusing existing)|ERROR WARP command|Extra logging has not yet", line):
        for secret in secrets:
            line = line.replace(secret, "[redacted]")
        if line not in lines:
            lines.append(line)
pid = str(container["State"]["Pid"])
version = run("chroot", f"/proc/{pid}/root", "/usr/bin/warp-cli", "--version").strip() if pid != "0" else "not running"
last = json.loads((work / "results.jsonl").read_text().splitlines()[-1])
record = {"time": datetime.datetime.now(datetime.timezone.utc).isoformat(), "stage": last["stage"],
          "kind": "diagnostics", "image": container["Image"], "version": version,
          "health": container["State"].get("Health", {}).get("Status"),
          "restarts": container["RestartCount"], "lines": lines[-12:],
          "configured_cap_add": container["HostConfig"].get("CapAdd") or [],
          "log_config": container["HostConfig"]["LogConfig"],
          "license_leaked": any(secret in daemon_log or secret in (work / "results.jsonl").read_text() for secret in secrets),
          "log_tail_lines": len(daemon_log.splitlines()),
          "debug_lines_in_tail": sum(bool(re.search(r"\bDEBUG\b", line)) for line in daemon_log.splitlines())}
if pid != "0":
    rows = [{"pid": fields[0], "command": fields[1]} for line in run("docker", "top", "warp", "-eo", "pid,comm").splitlines()[1:]
            if len(fields := line.split(None, 1)) == 2]
    record["daemon_capabilities"] = network.daemon_capabilities(rows)
    record["cap_net_admin"] = all(row["net_admin"] for row in record["daemon_capabilities"]) if record["daemon_capabilities"] else None
if (work / "network-before.json").exists():
    before = json.loads((work / "network-before.json").read_text())
    state = json.loads((work / "control.json").read_text())
    record["network"] = network.summary(before, network.snapshot(state["args"].get("ssh_peer")))
if (work / "azure.yml.before").exists():
    import yaml
    compose = Path("/home/shadowofgost/Docker/azure.yml")
    record["other_services_unchanged"] = network.compose_other_services(compose.read_text()) == network.compose_other_services((work / "azure.yml.before").read_text())
    metadata = compose.stat()
    record["compose_metadata"] = {"uid": metadata.st_uid, "gid": metadata.st_gid, "mode": oct(metadata.st_mode & 0o777)}
    service = yaml.safe_load(compose.read_text())["services"]["warp"]
    record["compose_service"] = {key: service.get(key) for key in ("image", "network_mode", "cap_add", "logging")}
if last["stage"] == "rollback":
    original = json.loads((work / "original.json").read_text())
    record["original_image_restored"] = original["Image"] == container["Image"]
    data = json.loads(Path("/home/shadowofgost/Docker/warp/data/reg.json").read_text())
    identity = {key: value for key, value in data.items()
                if key.lower() in ("id", "device_id", "private_key", "public_key", "registration_id")}
    identity_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    events = [json.loads(line) for line in (work / "results.jsonl").read_text().splitlines()]
    snapshot = next(event for event in events if event["kind"] == "snapshot_complete")
    record["identity_preserved"] = identity_hash == snapshot["registration"]
    record["compose_restored"] = Path("/home/shadowofgost/Docker/azure.yml").read_bytes() == (work / "azure.yml.before").read_bytes()
    record["watchtower_running"] = json.loads(run("docker", "inspect", "watchtower"))[0]["State"]["Running"]
encoded = json.dumps(record)
with (work / "results.jsonl").open("a") as handle:
    handle.write(encoded + "\n")
print(encoded)
