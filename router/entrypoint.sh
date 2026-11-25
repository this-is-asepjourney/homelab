#!/bin/sh
set -e

mkdir -p /etc/nginx/conf.d/projects /etc/nginx/conf.d/maintenance 2>/dev/null || true

calc_signature() {
    find /etc/nginx/conf.d -type f \( -name '*.conf' -o -name '*.html' \) -exec stat -c '%n:%Y:%s' {} + 2>/dev/null | sort
}

nginx -g "daemon off;" &
NGINX_PID=$!

LAST_SIG=$(calc_signature)

while kill -0 "$NGINX_PID" 2>/dev/null; do
    sleep 5
    NEW_SIG=$(calc_signature)
    if [ "$NEW_SIG" != "$LAST_SIG" ]; then
        if nginx -t >/dev/null 2>&1; then
            nginx -s reload >/dev/null 2>&1 || true
            LAST_SIG="$NEW_SIG"
        fi
    fi
done

wait "$NGINX_PID"
