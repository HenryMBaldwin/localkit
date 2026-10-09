#!/bin/sh
set -eu

base=/opt/localkit-axera

ip route replace prohibit 0.0.0.0/1
ip route replace prohibit 128.0.0.0/1
if [ -e /proc/net/if_inet6 ]; then
    ip -6 route replace prohibit ::/1
    ip -6 route replace prohibit 8000::/1
fi

if mount --bind "$base/hosts" /etc/hosts && mount --bind "$base/ca.crt" /app/bin/ca.crt; then
    ("$base/camera.sh" > /tmp/localkit-camera.log 2>&1) &
fi
exec /app/script/app_init.sh
