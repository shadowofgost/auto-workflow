FROM ubuntu:22.04

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y curl gpg lsb-release tzdata ca-certificates wget && \
    # lsb-core && \
    curl https://pkg.cloudflareclient.com/pubkey.gpg | gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" | tee /etc/apt/sources.list.d/cloudflare-client.list && \
    apt-get update && \
    apt-get install -y cloudflare-warp && \
    apt-get clean all && \
    apt-get autoclean && \
    apt-get autoremove && \
    rm -rf /var/lib/apt/lists/*

# Accept Cloudflare WARP TOS
RUN mkdir -p /root/.local/share/warp && \
    echo -n 'yes' > /root/.local/share/warp/accepted-tos.txt

# ENV GOST_ARGS="-L :1080"
ENV WARP_SLEEP=2
ENV WARP_PROXY=TRUE

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS "https://cloudflare.com/cdn-cgi/trace" | grep -qE "warp=(plus|on)" || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
#CMD [ "/usr/bin/xray", "-config", "/etc/xray/config.json" ]
