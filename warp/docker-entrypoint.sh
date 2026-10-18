#!/bin/sh
set -eu
if [ "$#" -gt 0 ]; then
    exec "$@"
fi
exec python3 /usr/local/lib/warp/supervisor.py
