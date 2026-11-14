# syntax=docker/dockerfile:1

FROM --platform=$BUILDPLATFORM alpine:latest AS builder

ARG TARGETARCH
ARG NEVERIDLE_VERSION=latest

RUN apk add --no-cache ca-certificates
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64|arm64) ;; \
      *) echo "Unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    if [ "$NEVERIDLE_VERSION" = "latest" ]; then \
      release_url="https://github.com/layou233/NeverIdle/releases/latest/download"; \
    else \
      release_url="https://github.com/layou233/NeverIdle/releases/download/$NEVERIDLE_VERSION"; \
    fi; \
    wget -O /NeverIdle "$release_url/NeverIdle-linux-$TARGETARCH"; \
    test -s /NeverIdle; \
    chmod +x /NeverIdle

FROM scratch

# 1. 标注版本：使用 OCI 标准 Labels
ARG NEVERIDLE_VERSION=latest
LABEL org.opencontainers.image.title="NeverIdle" \
      org.opencontainers.image.version="${NEVERIDLE_VERSION}" 

# 2. 复制二进制、CA 证书和入口脚本
COPY --from=builder /NeverIdle /NeverIdle
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
RUN chmod +x /NeverIdle

ENTRYPOINT ["/docker-entrypoint.sh"]
