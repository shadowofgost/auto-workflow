# Cloudflare WARP container

The default mode is the official client's loopback SOCKS5 proxy. With host
networking, applications on the host (including Xray using host networking)
can connect to `socks5h://127.0.0.1:40000`. A bridge container's own localhost
does not point at that listener. This image does not contain GOST.

**Azure acceptance (2026-10-18): not passed under the supplied permissions.**
Both this candidate and the upstream image (WARP 2026.8.2110.0) failed to
install fwmark policy rules without network administration capability, even
in proxy mode. The original deployment was restored; latest was not promoted.
See [the comparison report](reports/azure-comparison-20261018.md). Host
networking alone is not evidence that this client version will work with the
existing permissions. No broader capability or host-route change was made.

## Configuration

| Variable | Default | Behavior |
| --- | --- | --- |
| `WARP_SLEEP` | `2` | Minimum startup delay, 0–60 seconds, followed by up to 60 seconds of daemon readiness checks |
| `WARP_PROXY` | `TRUE` | `TRUE` selects proxy mode; `FALSE` selects full WARP mode, which requires network administration/TUN permissions and can change routing |
| `WARP_PORT` | `40000` | Loopback proxy port, 1–65535 |
| `WARP_LICENSE_KEY` | empty | Compare with the CLI's `registration show` License field; update only when different |
| `WARP_CUSTOM_ENDPOINT` | empty | Apply an endpoint override; empty removes an old override |
| `WARP_LOG_LEVEL` | `info` | `info`: lifecycle/state changes plus daemon warnings/errors; `warn`: warnings/errors; `debug`: complete daemon output |
| `WARP_ENABLE_QLOG` | `false` | Explicit opt-in to diagnostic qlogs; independent of console verbosity |

Boolean variables accept true/false (case insensitive), 1/0 and yes/no.
Existing registration is preserved. A failed CLI read with a persisted
`reg.json` fails startup rather than registering a replacement identity.
Every start reapplies mode, port, endpoint and connection settings.

The daemon is supervised directly and its exit status is propagated. Docker
SIGTERM is forwarded and children are reaped. Failed periodic status queries
are warnings; they do not by themselves restart the daemon. A disconnected
daemon remains visible through the trace-based Docker health check.

`registration license` is a setter; the readable License field is obtained
using `registration show`. No license is written to container logs.

## Bounded logs on Azure

Apply the logging override together with the existing deployment file, using
the same Compose project and working directory:

```sh
docker compose -f azure.yml -f /path/to/compose.azure.override.yml up -d --no-deps warp
```

The override bounds Docker JSON logs to three approximately 10 MB files.
The Dockerfile cannot configure the host logging driver. Archive old logs
before recreating a container; do not truncate Docker-managed live files.
The client may retain its own diagnostic files independently; console
filtering does not claim to disable all internal diagnostic writes.

The container accepts command overrides for diagnostics, for example
`docker run --rm shadowofgost/warp:latest warp-cli --version`.

## Native amd64 and arm64 release

The `warp build` workflow resolves a common package version from the signed
APT repository metadata's architecture indexes; installation itself uses
APT's signature validation. `WARP_VERSION` pins the actual package installed.
Both architectures build and execute on native GitHub runners. Unit tests,
CLI/package checks and Docker log rotation must pass before publishing the
combined manifests to Docker Hub and GHCR.

Manual dispatch defaults to a candidate: `sha-<full commit SHA>` and UTC date
tags, with no `latest` change. After Azure acceptance, dispatch the workflow
with `promote_sha=<tested full SHA>` to copy that existing manifest to
`latest` without rebuilding. Scheduled runs retain the monthly publication
behavior and update latest only after both native checks pass. PR runs do
not log in or publish. ARM means arm64/aarch64, not arm/v7.

## Tests and Azure comparison

```sh
python3 -m unittest discover -s warp/tests -v
```

`test-azure-warp.py` can be piped over SSH to Python on Azure. Its default
checks only direct and WARP egress. `--containers` adds temporary curl
containers; `--include-xray` or `--xray-only` explicitly opts into inspection
and a real request through the existing Xray WebSocket socket.

`azure-compare.py` is a root-only maintenance tool for the agreed Azure
deployment. It pre-pulls images, pauses Watchtower, stops and retains the
original container under `warp-before-upgrade-20261018`, takes a cold data
snapshot and archives old Docker logs. It restores the same snapshot before
each test phase and never changes Xray or host routing. It must not be run
concurrently or against another deployment.

The upstream stock-entrypoint test preserves all supplied parameters. The
adapted test mounts this image's supervisor through a separate `sudo -E
python3` entrypoint, using only the upstream image's WARP client; GOST,
TUN creation and NAT are not started. It compares native proxies at port
40000, not GOST throughput or upstream's default tunnel architecture.

The full test requires two ten-minute observation windows, plus sequential
requests, restart checks and a repeat of the candidate. Raw samples go to
`results.jsonl` in the private remote work directory. Credentials, original
container inspection, snapshots and archived logs stay on the server with
restricted permissions. Public reports contain only sanitized evidence.

When `--production-image` is supplied, the tool waits for a root-created
`promotion-approved` file in its work directory after acceptance. It pulls
the promoted tag and verifies that its image ID matches the tested candidate
before deployment. Failures restore the original container and cold data;
the old deployment may already have been unhealthy before the test.

Readiness failures capture an allowlisted startup diagnostic before rollback.
`azure-diagnostics.py` can additionally capture the same type of redacted
evidence during a running trial; its output appends to that trial's JSONL.
