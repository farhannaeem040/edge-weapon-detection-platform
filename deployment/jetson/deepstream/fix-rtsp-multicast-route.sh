#!/usr/bin/env bash
#
# DeepStream RTSP output — multicast loopback route (IP-06 follow-up).
#
# Root cause: deepstream-app's built-in RTSP sink (sink type=4) hardcodes its UDP relay
# destination to multicast 224.224.255.255 (deepstream_sink_bin.c, create_rtsp_sink_bin),
# while the RTSP media factory's client-facing pipeline is a plain
# "udpsrc port=<udp-port>" with no multicast group/address set. With no route directing
# that multicast destination back into this host, the kernel sends it out through the
# default-route interface and it is never delivered to any local socket — every RTSP
# client's DESCRIBE hangs until gst-rtsp-server gives up and returns 503 Service Unavailable.
#
# Fix: route the RTSP sink's multicast destination through loopback so the traffic is
# delivered locally. This does not touch DeepStream, its config, or the Agent — it is a
# host-network prerequisite for DeepStream's RTSP output, installed the same way as the
# 'video'/'render' group membership DeepStream also needs (install.sh).
#
# Idempotent and safe to re-run.

set -Eeuo pipefail

readonly MCAST_ADDR="224.224.255.255/32"

if ip route show | grep -qE "^${MCAST_ADDR%/32}(/32)? dev lo"; then
    echo "[fix-rtsp-multicast-route] route already present"
else
    ip route add "${MCAST_ADDR}" dev lo
    echo "[fix-rtsp-multicast-route] added: ${MCAST_ADDR} dev lo"
fi
