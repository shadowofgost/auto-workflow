FROM rust:1-bookworm AS builder

WORKDIR /build
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release --locked

FROM ubuntu:26.04

ARG COMMIT_SHA=unknown
ARG TARGETARCH
LABEL org.opencontainers.image.source="https://github.com/shadowofgost/auto-workflow" \
      org.opencontainers.image.revision="${COMMIT_SHA}"

RUN set -eux; \
    case "${TARGETARCH:-$(dpkg --print-architecture)}" in amd64|arm64) ;; *) exit 1 ;; esac; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl gpg; \
    curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | gpg --batch --yes --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg; \
    . /etc/os-release; \
    test "$ID" = ubuntu; \
    test -n "$UBUNTU_CODENAME"; \
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ ${UBUNTU_CODENAME} main" > /etc/apt/sources.list.d/cloudflare-client.list; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends cloudflare-warp; \
    printf 'distribution=%s\nsuite=%s\nrepository=%s\n' "$ID" "$UBUNTU_CODENAME" https://pkg.cloudflareclient.com/ > /etc/warp-package-source; \
    dpkg-query -W -f='${Version}\n' cloudflare-warp > /etc/warp-package-version; \
    dpkg-query -W -f='${Architecture}\n' cloudflare-warp > /etc/warp-package-architecture; \
    rm -rf /var/lib/apt/lists/*; \
    mkdir -p /root/.local/share/warp; printf yes > /root/.local/share/warp/accepted-tos.txt

COPY --from=builder /build/target/release/warp-supervisor /usr/local/bin/warp-supervisor

ENV WARP_SLEEP=2 WARP_PROXY=TRUE WARP_PORT=40000 WARP_LOG_LEVEL=info WARP_ENABLE_QLOG=false
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 CMD ["/usr/local/bin/warp-supervisor", "healthcheck"]
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/local/bin/warp-supervisor"]
