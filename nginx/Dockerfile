FROM nginx:mainline

WORKDIR /root
COPY xray.sh /root/xray.sh
COPY config.json /etc/xray/config.json
RUN apt-get update && apt-get install -y wget tzdata iproute2 ca-certificates \
	&& mkdir -p /var/log/xray /usr/share/xray \
	&& chmod +x /root/xray.sh \
	&& /root/xray.sh \
	&& rm -fv /root/xray.sh \
	&& wget -O /usr/share/xray/geosite.dat https://github.com/v2fly/domain-list-community/releases/latest/download/dlc.dat \
	&& wget -O /usr/share/xray/geoip.dat https://github.com/v2fly/geoip/releases/latest/download/geoip.dat

VOLUME /etc/xray
ENV TZ=Asia/Shanghai
CMD nginx ; /usr/bin/xray -config /etc/xray/config.json
