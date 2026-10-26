# Cloudflare WARP container

The default mode is the official client's loopback SOCKS5 proxy. With host
networking, applications on the host (including Xray using host networking)
can connect to `socks5h://127.0.0.1:40000`. A bridge container's own localhost
does not point at that listener. This image does not contain GOST.

The production supervisor is a single Rust executable. It handles startup,
registration/license updates, log filtering, shutdown and health checks. A
multi-stage build uses `rust:1-bookworm` to produce a GNU/glibc executable;
the final Ubuntu image
does not install Python or a Rust compiler. No separate startup or healthcheck
scripts are required. Logs continue to go to Docker stdout; lifecycle
timestamps are Unix seconds in UTC. Client-internal diagnostic files remain
independent of Docker's log rotation.

**Previous Python-based image: Azure acceptance (2026-10-25) passed with host networking + NET_ADMIN.**
Those historical results do not validate the Rust rewrite; a new candidate
must pass the same native builds and Azure acceptance before promotion.
The Ubuntu 26.04/resolute image (WARP 2026.8.2115.0) failed the same-image
control without NET_ADMIN and passed the proxy, restart and ten-minute
stability checks with it. The constrained upstream stock entrypoint and
adapted native proxy also passed. The tested image was promoted to latest
without rebuilding and retained on Azure, with capabilities and bounded logs
persisted in Compose. Host direct traffic and Xray still returned warp=off;
the native SOCKS5 proxy returned warp=on. Host-network effects were measured
for this run and client version, not guaranteed for future releases.
See [the NET_ADMIN comparison](reports/azure-netadmin-20261025.md), the
[earlier Ubuntu retest](reports/azure-ubuntu-20261024.md) and the
[original comparison](reports/azure-comparison-20261018.md).

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

## Azure permissions and bounded logs

The supplied override grants `NET_ADMIN` for the client's fwmark policy-rule
installation and bounds Docker logs. With `network_mode: host`, that capability
applies to the host network namespace. Proxy mode must therefore be verified
against host routes, DNS, firewall and existing services; it is not a guarantee
of isolation. No privileged mode or additional TUN device mount is configured.

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
The built-in health check is `warp-supervisor healthcheck`; it uses curl with
SOCKS5 remote DNS and requires an exact `warp=on` or `warp=plus` trace line.
`warp-supervisor --version` prints the supervisor version without starting WARP.

## Native amd64 and arm64 release

The `warp build` workflow builds and executes both architectures on native
GitHub runners. Each build installs the current `cloudflare-warp` package
directly from Cloudflare's APT repository using APT's signature validation,
without a separate version resolver or version pin. Builds pull the base
image and run without layer caching so APT package installation is refreshed.
The Dockerfile checks that the base distribution is Ubuntu and selects its
`UBUNTU_CODENAME` suite (`resolute` for Ubuntu 26.04), following the official
[Ubuntu installation instructions](https://pkg.cloudflareclient.com/#ubuntu).
The APT `deb` format is used by Ubuntu as well as Debian; the suite selects
the Ubuntu package, not a Debian release such as `bookworm` or `trixie`.
The selected distribution, suite and repository are saved in
`/etc/warp-package-source` and checked during CI.
The installed version and architecture are recorded in the image and printed
during CI. Unit tests, CLI/architecture checks and Docker log rotation must
pass before publishing the combined manifests to Docker Hub and GHCR.

Manual dispatch defaults to a candidate: `sha-<full commit SHA>` and UTC date
tags, with no `latest` change. After Azure acceptance, dispatch the workflow
with `promote_sha=<tested full SHA>` to copy that existing manifest to
`latest` without rebuilding. Scheduled runs retain the monthly publication
behavior and update latest only after both native checks pass. PR runs do
not log in or publish. ARM means arm64/aarch64, not arm/v7.

## Tests and Azure comparison

```sh
cargo test --manifest-path warp/Cargo.toml --locked
python3 -m unittest discover -s warp/tests -v
```

Rust tests cover registration preservation, license updates, log redaction,
CLI deadlines, health checks and real process shutdown. Unix process tests
run on both native CI architectures. Python files are host-side maintenance
tools and their tests, excluded from the Docker build context; they require
PyYAML. The WARP container has no Python runtime requirement.

`test-azure-warp.py` can be piped over SSH to Python on Azure. Its default
checks only direct and WARP egress. `--containers` adds temporary curl
containers; `--include-xray` or `--xray-only` explicitly opts into inspection
and a real request through the existing Xray WebSocket socket.

`azure-compare.py` is a root-only maintenance tool for the agreed Azure
deployment. It pre-pulls images, pauses Watchtower, stops and retains the
original container under a name derived from the run directory, takes a cold
data snapshot and archives old Docker logs. A host lock rejects concurrent
runs. Private run directories default to a UTC timestamp. It restores the
same snapshot before each phase and does not change Xray configuration.
Do not run it against another deployment.

`--net-admin` explicitly grants the capability to both images and production
deployment; it is off by default. This option first runs a short control using
the same candidate digest without the capability. The formal phases are the
candidate, constrained upstream stock entrypoint, adapted upstream native
proxy and a repeat of the candidate. Image references are pinned after pulling.

The upstream stock-entrypoint test adds `GOST_ARGS=-L 127.0.0.1:1080` and an
empty `WARP_ENABLE_NAT` to prevent public GOST listening or NAT configuration;
it is skipped if port 1080 is occupied. These differences are reported. The
adapted test mounts the Rust supervisor and runs it as root,
using only the upstream image's WARP client; GOST,
TUN creation and NAT are not started. It compares native proxies at port
40000, not GOST throughput or upstream's default tunnel architecture.

Before an adapted trial, copy `/usr/local/bin/warp-supervisor` out of the
candidate image built for the Azure host architecture and supply its absolute
host path with `--supervisor` (default `/tmp/warp-supervisor`). Keep its execute
permission. The GNU/glibc binary must also be compatible with the host and
upstream image. Before stopping any services, the comparison tool checks that
the binary executes in the pinned upstream image; an incompatible libc fails
the preflight instead of proceeding with the trial.

The full test requires two ten-minute observation windows, plus sequential
requests, restart checks and a repeat of the candidate. Raw samples go to
`results.jsonl` in the private remote work directory. Credentials, original
container inspection, snapshots and archived logs stay on the server with
restricted permissions. Public reports contain only sanitized evidence.

An independent systemd watchdog checks controller liveness, a 90-second
heartbeat, a 90-minute deadline and host-network invariants. Main routes,
unmarked traffic decisions, DNS and firewall rules are compared with the
baseline; new tunnel interfaces and unmarked policy rules fail the trial.
New fwmark rules are recorded. Recovery stops the trial and restores the
measured network delta, original container, cold data and Compose configuration.
It does not globally flush routes or firewall rules. Pass `--controller-unit`
when launching the controller through systemd so recovery can stop its full
process tree. `azure_network.py --check` performs a read-only baseline check.

When `--production-image` is supplied, the tool waits for a root-created
`promotion-approved` file in its work directory after acceptance. It pulls
the promoted tag and verifies that its image ID matches the tested candidate
before deployment. The production WARP service is updated atomically and
idempotently through PyYAML, with Compose validation before and after replacement;
other services retain their original text. NET_ADMIN, image and log rotation
are persisted together. Failures restore the original container and cold data;
the old deployment may already have been unhealthy before the test.

Readiness failures capture an allowlisted startup diagnostic before rollback.
`azure-diagnostics.py` can additionally capture the same type of redacted
evidence during a running trial; its output appends to that trial's JSONL.
