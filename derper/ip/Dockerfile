FROM golang:1.23-alpine AS builder
LABEL org.opencontainers.image.source="https://github.com/shadowofgost/auto-workflow"

WORKDIR /app


ADD tailscale /app/tailscale

# build modified derper
RUN cd /app/tailscale/cmd/derper && \
    sed -i '/if hi.ServerName != m.hostname {/,+2s/^/\/\//' ./cert.go && \
    CGO_ENABLED=0 /usr/local/go/bin/go build -buildvcs=false -ldflags "-s -w" -o /app/derper && \
    cd /app && \
    rm -rf /app/tailscale



FROM alpine:latest
WORKDIR /app
COPY --from=builder /app /app
# ========= CONFIG =========
# - derper args
ENV DERP_ADDR=:443
ENV DERP_HTTP_PORT=80
ENV DERP_HOST=127.0.0.1
ENV DERP_CERTS=/app/certs/
ENV DERP_STUN=true
ENV DERP_VERIFY_CLIENTS=false
# ==========================
# build self-signed certs && start derper
CMD /app/derper --hostname=$DERP_HOST \
    --certmode=manual \
    --certdir=$DERP_CERTS \
    --stun=$DERP_STUN  \
    --a=$DERP_ADDR \
    --http-port=$DERP_HTTP_PORT \
    --verify-clients=$DERP_VERIFY_CLIENTS
