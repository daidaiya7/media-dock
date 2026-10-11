#!/bin/sh
set -eu
umask 077
exec uvicorn app:create_app --factory --host 0.0.0.0 --port 59189 \
  --workers 1 --no-access-log --proxy-headers \
  --forwarded-allow-ips "${MEDIADOCK_TRUSTED_PROXIES:-127.0.0.1}" "$@"
