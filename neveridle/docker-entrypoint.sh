#!/bin/sh
#!/usr/bin/env bash
# -*- coding: utf-8 -*-
###
 # @Author           : Albert Wang
 # @Time             : 2026-11-14 19:34:56
 # @Description      :
 # @Email            : shadowofgost@outlook.com
 # @FilePath         : /auto-workflow/neveridle/docker-entrypoint.sh
 # @LastTime         : 2026-11-14 19:35:23
 # @LastAuthor       : Albert Wang
 # @Software         : Vscode
 #  Copyright Notice : Copyright (c) 2026 Albert Wang 王子睿, All Rights Reserved.
###
set -e

# 如果用户直接传入了命令行参数，则完全按传统方式执行，不经过环境变量转换
if [ $# -gt 0 ]; then
    exec /NeverIdle "$@"
fi

# 否则，从环境变量读取，并构造 NeverIdle 参数
ARGS=""

# CPU 定期浪费间隔，例如 "-c 2h" -> NEVERIDLE_CPU_INTERVAL=2h
if [ -n "${NEVERIDLE_CPU_INTERVAL}" ]; then
    ARGS="$ARGS -c ${NEVERIDLE_CPU_INTERVAL}"
fi

# CPU 粗粒度百分比，例如 "-cp 0.15" -> NEVERIDLE_CPU_PERCENT=0.15
if [ -n "${NEVERIDLE_CPU_PERCENT}" ]; then
    ARGS="$ARGS -cp ${NEVERIDLE_CPU_PERCENT}"
fi

# 内存浪费量（GiB），例如 "-m 2" -> NEVERIDLE_MEMORY=2
if [ -n "${NEVERIDLE_MEMORY}" ]; then
    ARGS="$ARGS -m ${NEVERIDLE_MEMORY}"
fi

# 网络测试间隔，例如 "-n 4h" -> NEVERIDLE_NETWORK_INTERVAL=4h
if [ -n "${NEVERIDLE_NETWORK_INTERVAL}" ]; then
    ARGS="$ARGS -n ${NEVERIDLE_NETWORK_INTERVAL}"
fi

# 测速并发连接数，例如 "-t 10" -> NEVERIDLE_THREADS=10
if [ -n "${NEVERIDLE_THREADS}" ]; then
    ARGS="$ARGS -t ${NEVERIDLE_THREADS}"
fi

# 进程优先级，例如 "-p 19" -> NEVERIDLE_PRIORITY=19
if [ -n "${NEVERIDLE_PRIORITY}" ]; then
    ARGS="$ARGS -p ${NEVERIDLE_PRIORITY}"
fi

# 如果完全没有设置任何环境变量，回退到合理的默认值
if [ -z "$ARGS" ]; then
    ARGS="-c 2h -m 2 -n 4h"
fi

exec /NeverIdle $ARGS
