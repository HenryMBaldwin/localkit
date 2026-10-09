#!/bin/sh
set -eu

export LD_LIBRARY_PATH=/soc/usr/lib:/soc/lib:/app/bin:/app/lib:/alg

attempt=0
while [ ! -e /dev/shm/media_buffer_frame_buf ]; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 90 ] || exit 1
    sleep 1
done

exec /app/bin/tserver
