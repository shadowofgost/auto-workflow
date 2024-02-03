#!/bin/sh
# -*- coding: utf-8 -*-
###
 # @Author           : Albert Wang
 # @Time             : 2023-09-22 18:30:01
 # @Description      :
 # @Email            : shadowofgost@outlook.com
 # @FilePath         : /okteto-deploy/warp-xray/docker-entrypoint.sh
 # @LastTime         : 2023-09-22 19:19:14
 # @LastAuthor       : Albert Wang
 # @Software         : Vscode
 #  Copyright Notice : Copyright (c) 2023 Albert Wang 王子睿, All Rights Reserved.
###

set -e
#start the daemon
warp-svc &

# sleep to wait for the daemon to start, default 2 seconds
sleep "$WARP_SLEEP"
warp-cli register && echo "Warp client registered!"
# if a license key is provided, register the license
if [ -n "$WARP_LICENSE_KEY" ]; then
    echo "License key found, registering license..."
    warp-cli --accept-tos set-license "$WARP_LICENSE_KEY" && echo "Warp license registered!"
fi
 # Assuming $WARP_LICENSE_KEY contains the value "true" or "false"
if [[ "$WARP_PROXY" == "TRUE" ]]; then
    echo "WARP_PROXY is true."
    warp-cli --accept-tos set-mode proxy
else
    echo "WARP_PROXY is false."
fi
# connect to the warp server
warp-cli --accept-tos connect

warp-cli --accept-tos enable-always-on
