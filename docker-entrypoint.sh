#!/bin/sh
set -eu
umask 077

mkdir -p /app/data
chmod 700 /app/data
export DISPLAY="${DISPLAY:-:99}"

Xvfb "$DISPLAY" -screen 0 1280x900x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
sleep 1

if [ "${ENABLE_NOVNC:-0}" = "1" ]; then
  password_file="${NOVNC_PASSWORD_FILE:-/app/data/.novnc-password}"
  if [ ! -s "$password_file" ]; then
    echo "ENABLE_NOVNC=1 requires a non-empty NOVNC_PASSWORD_FILE" >&2
    exit 1
  fi
  x11vnc -storepasswd "$(head -n 1 "$password_file")" /tmp/x11vnc.pass >/dev/null
  chmod 600 /tmp/x11vnc.pass
  x11vnc -display "$DISPLAY" -localhost -forever -shared -rfbport 5900 \
    -rfbauth /tmp/x11vnc.pass >/tmp/x11vnc.log 2>&1 &
  websockify --web=/usr/share/novnc 6080 localhost:5900 >/tmp/websockify.log 2>&1 &
fi

exec "$@"
