#!/bin/sh
set -eu
case "${WARP_PROXY:-TRUE}" in
    TRUE|true|1|yes) set -- --socks5-hostname "127.0.0.1:${WARP_PORT:-40000}" ;;
    FALSE|false|0|no) set -- ;;
    *) exit 1 ;;
esac
curl --noproxy '' "$@" --connect-timeout 2 --max-time 4 -fsS \
    https://cloudflare.com/cdn-cgi/trace | grep -qE '^warp=(on|plus)$'
