#!/bin/sh
set -eu

export DISPLAY="${DISPLAY:-:99}"

Xvfb "$DISPLAY" -screen 0 1280x900x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
sleep 1
x11vnc -display "$DISPLAY" -localhost -forever -shared -rfbport 5900 -nopw >/tmp/x11vnc.log 2>&1 &
websockify --web=/usr/share/novnc 6080 localhost:5900 >/tmp/websockify.log 2>&1 &

exec "$@"
