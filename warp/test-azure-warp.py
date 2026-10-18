"""Run on Azure via SSH stdin; probe WARP and the existing Xray WebSocket inbound.

Uses existing images, removes temporary containers, and leaves service config intact.
Credentials are read in memory from Xray and are never printed or written to disk.
"""

import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import time
import uuid

TRACE_URL = "https://cloudflare.com/cdn-cgi/trace"


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=90 if args[:2] == ("docker", "run") else 30)
    if result.returncode:
        raise RuntimeError(f"Probe command failed (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout


def trace_fields(text):
    fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    if fields.get("warp") not in ("off", "on", "plus") or not fields.get("ip"):
        raise RuntimeError("Response did not contain a valid Cloudflare trace")
    return {key: fields.get(key) for key in ("ip", "warp", "gateway", "colo", "loc")}


def curl_probe(container=False, network="host", proxy=False):
    args = ["curl"]
    if container:
        args = ["docker", "run", "--rm", "--pull=never", "--memory=64m", "--cpus=0.25", "--pids-limit=32", "--network", network,
                "--entrypoint", "curl", "shadowofgost/warp:latest"]
    args += ["--noproxy", "" if proxy else "*", "-fsS", "--connect-timeout", "5", "--max-time", "15"]
    if proxy:
        args += ["--socks5-hostname", "127.0.0.1:40000"]
    args += [TRACE_URL]
    return trace_fields(command(*args))


def receive_exact(sock, length):
    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise RuntimeError("Xray closed the connection before completing the trace")
        data.extend(chunk)
    return bytes(data)


def ws_send(sock, payload, opcode=2):
    mask = os.urandom(4)
    length = len(payload)
    header = bytes([0x80 | opcode])
    if length < 126:
        header += bytes([0x80 | length])
    elif length < 65536:
        header += bytes([0xFE]) + struct.pack("!H", length)
    else:
        header += bytes([0xFF]) + struct.pack("!Q", length)
    sock.sendall(header + mask + bytes(value ^ mask[index % 4] for index, value in enumerate(payload)))


def ws_receive(sock):
    first, second = receive_exact(sock, 2)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", receive_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", receive_exact(sock, 8))[0]
    if length > 1024 * 1024:
        raise RuntimeError("Unexpectedly large WebSocket response")
    mask = receive_exact(sock, 4) if second & 0x80 else None
    payload = receive_exact(sock, length)
    if mask:
        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return first & 0x0F, payload


def xray_ws_probe(config):
    inbound = next(item for item in config["inbounds"]
                   if item.get("protocol") == "vless"
                   and item.get("streamSettings", {}).get("network") == "ws"
                   and item.get("streamSettings", {}).get("security", "none") == "none")
    client = inbound["settings"]["clients"][0]
    if client.get("flow"):
        raise RuntimeError("Probe requires a VLESS WebSocket client without Vision flow")
    path = inbound["streamSettings"]["wsSettings"]["path"]
    key = base64.b64encode(os.urandom(16)).decode()
    expected_accept = base64.b64encode(hashlib.sha1(
        (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
    socket_path = inbound["listen"].split(",", 1)[0]
    inspection = json.loads(command("docker", "inspect", "xray"))[0]
    for mount in inspection.get("Mounts", []):
        destination = mount["Destination"].rstrip("/")
        if socket_path.startswith(destination + "/"):
            socket_path = mount["Source"].rstrip("/") + socket_path[len(destination):]
            break
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(15)
        sock.connect(socket_path)
        sock.sendall((f"GET {path} HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                      f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                      "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        handshake = bytearray()
        while not handshake.endswith(b"\r\n\r\n"):
            handshake.extend(receive_exact(sock, 1))
            if len(handshake) > 16384:
                raise RuntimeError("Invalid WebSocket handshake")
        if not handshake.startswith(b"HTTP/1.1 101 ") or expected_accept not in handshake:
            raise RuntimeError("Xray WebSocket upgrade failed")
        host = b"cloudflare.com"
        # VLESS v0, existing client UUID, no addons, TCP, port 80, domain address.
        header = b"\x00" + uuid.UUID(client["id"]).bytes + b"\x00\x01\x00\x50\x02" + bytes([len(host)]) + host
        request = b"GET /cdn-cgi/trace HTTP/1.1\r\nHost: cloudflare.com\r\nConnection: close\r\n\r\n"
        ws_send(sock, header + request)
        response = bytearray()
        while len(response) < 65536:
            opcode, payload = ws_receive(sock)
            if opcode == 9:
                ws_send(sock, payload, opcode=10)
                continue
            if opcode == 8:
                break
            if opcode not in (0, 2):
                continue
            response.extend(payload)
            if len(response) < 2 or len(response) < 2 + response[1]:
                continue
            body = bytes(response[2 + response[1]:])
            if len(body) < 13:
                continue
            if not body.startswith(b"HTTP/1.1 200 "):
                raise RuntimeError("Xray trace did not return HTTP 200")
            if b"\nwarp=" in body and b"\n" in body.split(b"\nwarp=", 1)[1]:
                return trace_fields(body.decode(errors="replace"))
        raise RuntimeError("Xray response did not complete the Cloudflare trace")


def main():
    config = None
    if "--include-xray" in sys.argv or "--xray-only" in sys.argv:
        config = json.loads(command("docker", "exec", "xray", "cat", "/etc/xray/config.json"))
    results = {}
    probes = {
        "azure_direct": lambda: curl_probe(),
        "azure_warp_socks": lambda: curl_probe(proxy=True),
        "test_container_host_direct": lambda: curl_probe(container=True),
        "test_container_host_warp": lambda: curl_probe(container=True, proxy=True),
        "test_container_shared_warp": lambda: curl_probe(container=True, network="container:warp", proxy=True),
    }
    if config is not None:
        probes["live_xray_vless_ws"] = lambda: xray_ws_probe(config)
    if "--xray-only" in sys.argv:
        probes = {"live_xray_vless_ws": probes["live_xray_vless_ws"]}
    elif "--containers" not in sys.argv:
        probes = {name: probe for name, probe in probes.items() if not name.startswith("test_container_")}
    for name, probe in probes.items():
        start = time.monotonic()
        try:
            result = probe()
        except Exception as error:
            result = {"error": str(error)}
        result["seconds"] = round(time.monotonic() - start, 2)
        results[name] = result
        print(json.dumps({"probe": name, **result}), flush=True)
    if config is not None:
        default = config.get("outbounds", [{}])[0].get("tag")
        targets = [rule.get("outboundTag") for rule in config.get("routing", {}).get("rules", [])]
        print(json.dumps({"xray_default_outbound": default, "xray_rule_outbounds": targets}), flush=True)
    expected = {"azure_direct": {"off"}, "test_container_host_direct": {"off"},
                "azure_warp_socks": {"on", "plus"}, "test_container_host_warp": {"on", "plus"},
                "test_container_shared_warp": {"on", "plus"}, "live_xray_vless_ws": {"off", "on", "plus"}}
    return 0 if all(result.get("warp") in expected[name] for name, result in results.items()) else 1


if __name__ == "__main__":
    sys.exit(main())
