FROM ubuntu:latest

WORKDIR /root
COPY docker-entrypoint.sh /root/docker-entrypoint.sh
COPY xray.sh /root/xray.sh
COPY config.json /etc/xray/config.json
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
    apt-get autoremove

RUN  mkdir -p /var/log/xray /usr/share/xray \
    && chmod +x /root/xray.sh \
    && chmod +x /root/docker-entrypoint.sh \
    && /root/xray.sh \
    && rm -fv /root/xray.sh \
    && wget -O /usr/share/xray/geosite.dat https://github.com/v2fly/domain-list-community/releases/latest/download/dlc.dat \
    && wget -O /usr/share/xray/geoip.dat https://github.com/v2fly/geoip/releases/latest/download/geoip.dat
VOLUME /etc/xray
ENV TZ=Asia/Shanghai
CMD /root/docker-entrypoint.sh
#CMD [ "/usr/bin/xray", "-config", "/etc/xray/config.json" ]
