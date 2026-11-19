#!/bin/sh
set -e

mkdir -p /etc/nginx/conf.d/projects

calc_hash() {
    find /etc/nginx/conf.d -type f -name '*.conf' -exec sha256sum {} + 2>/dev/null | sort | sha256sum | awk '{print $1}'
}

nginx -g "daemon off;" &
NGINX_PID=$!

LAST_HASH=$(calc_hash)

while kill -0 "$NGINX_PID" 2>/dev/null; do
    sleep 3
    NEW_HASH=$(calc_hash)
    if [ "$NEW_HASH" != "$LAST_HASH" ]; then
        if nginx -t >/dev/null 2>&1; then
            nginx -s reload >/dev/null 2>&1 || true
            LAST_HASH="$NEW_HASH"
        fi
    fi
done

wait "$NGINX_PID"
