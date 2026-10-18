FROM ubuntu:26.04

ARG WARP_VERSION
ARG COMMIT_SHA=unknown
ARG TARGETARCH
LABEL org.opencontainers.image.source="https://github.com/shadowofgost/auto-workflow" \
      org.opencontainers.image.revision="${COMMIT_SHA}" \
      org.opencontainers.image.version="${WARP_VERSION}"

RUN set -eux; \
    case "${TARGETARCH:-$(dpkg --print-architecture)}" in amd64|arm64) ;; *) exit 1 ;; esac; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl gpg python3-minimal; \
    curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | gpg --batch --yes --dearmor -o /usr/share/keyrings/cloudflare-warp.gpg; \
    . /etc/os-release; \
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp.gpg] https://pkg.cloudflareclient.com/ ${VERSION_CODENAME} main" > /etc/apt/sources.list.d/cloudflare-warp.list; \
    apt-get update; \
    if [ -n "${WARP_VERSION:-}" ]; then package="cloudflare-warp=$WARP_VERSION"; else package=cloudflare-warp; fi; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$package"; \
    dpkg-query -W -f='${Version}\n' cloudflare-warp > /etc/warp-package-version; \
    dpkg-query -W -f='${Architecture}\n' cloudflare-warp > /etc/warp-package-architecture; \
    rm -rf /var/lib/apt/lists/*; \
    mkdir -p /root/.local/share/warp; printf yes > /root/.local/share/warp/accepted-tos.txt

COPY docker-entrypoint.sh /docker-entrypoint.sh
COPY supervisor.py /usr/local/lib/warp/supervisor.py
COPY healthcheck.sh /usr/local/bin/warp-healthcheck
RUN chmod 755 /docker-entrypoint.sh /usr/local/bin/warp-healthcheck

ENV WARP_SLEEP=2 WARP_PROXY=TRUE WARP_PORT=40000 WARP_LOG_LEVEL=info WARP_ENABLE_QLOG=false
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 CMD ["/usr/local/bin/warp-healthcheck"]
STOPSIGNAL SIGTERM
ENTRYPOINT ["/docker-entrypoint.sh"]
