"""Collect only allowlisted, redacted startup evidence from the current trial."""
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

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
for line in run("docker", "logs", "--tail", "400", "warp").splitlines():
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
          "restarts": container["RestartCount"], "lines": lines[-12:]}
if pid != "0":
    status = Path(f"/proc/{pid}/status").read_text()
    capability = re.search(r"^CapEff:\s*([0-9a-f]+)", status, re.MULTILINE)
    record["cap_net_admin"] = bool(int(capability.group(1), 16) & (1 << 12)) if capability else None
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
