#!/bin/sh
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
/usr/bin/xray -config /etc/xray/config.json &
warp-svc  &
sleep 3 &&  echo y | warp-cli --accept-tos register
warp-cli --accept-tos set-license 1Op9F3g4-8Q9Yn0g6-743O9lrn
warp-cli --accept-tos set-mode proxy
# warp-cli set-custom-endpoint 104.20.38.141
warp-cli --accept-tos connect
warp-cli --accept-tos status
while true;
    do
        warp-cli --accept-tos status
        sleep 600;
    done
exec "$@"
