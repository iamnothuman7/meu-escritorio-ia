#!/bin/sh
set -eu
if [ "${RENEWED_LINEAGE:-}" = "/etc/letsencrypt/live/office.nabio.pro" ]; then
    /usr/sbin/nginx -t
    /bin/systemctl reload nginx
fi
