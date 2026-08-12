#!/bin/bash
# Unattended RDP auto-login.
# Reads ALL connection params from environment (pass via --env-file .env):
#   RDP_HOST        target host (e.g. host.example.com)          [required]
#   RDP_USER        username    (e.g. your-username)                [required]
#   RDP_PASSWORD    password                                     [required]
#   RDP_DOMAIN      domain      (e.g. YOUR_DOMAIN)                [optional]
#   RENDER_WAIT     seconds to wait for session to render        [default 8]
#   HOLD_AFTER      seconds to keep session open after Enter     [default 20]
#
# Logic: NLA-authenticated RDP connect (skips Ctrl+Alt+Del) -> wait for render ->
#        send Enter (dismiss Legal Notice / OK) -> hold -> disconnect.
# On disconnect the server-side session stays logged in (Startup items keep running).

set -uo pipefail

: "${RDP_HOST:?RDP_HOST required}"
: "${RDP_USER:?RDP_USER required}"
: "${RDP_PASSWORD:?RDP_PASSWORD required}"
RDP_DOMAIN="${RDP_DOMAIN:-}"
RENDER_WAIT="${RENDER_WAIT:-8}"
HOLD_AFTER="${HOLD_AFTER:-20}"

export DISPLAY=:99

echo "== [1] Xvfb =="
Xvfb :99 -screen 0 1280x800x24 -nolisten tcp &
XVFB_PID=$!
sleep 2

echo "== [2] xfreerdp -> $RDP_HOST (user=$RDP_USER domain=${RDP_DOMAIN:-<none>}, sec:tls) =="
DOMAIN_ARG=()
[ -n "$RDP_DOMAIN" ] && DOMAIN_ARG=(/d:"$RDP_DOMAIN")
xfreerdp /v:"$RDP_HOST" /u:"$RDP_USER" "${DOMAIN_ARG[@]}" /p:"$RDP_PASSWORD" \
    /cert:ignore /sec:tls /w:1280 /h:800 /log-level:WARN \
    > /tmp/xfreerdp.log 2>&1 &
XFREERDP_PID=$!

echo "== [3] wait ${RENDER_WAIT}s for render =="
sleep "$RENDER_WAIT"

echo "== [4] send Enter (dismiss Legal Notice / OK) =="
xdotool key --delay 200 Return

echo "== [5] hold ${HOLD_AFTER}s so the session establishes + Startup items launch =="
sleep "$HOLD_AFTER"

echo "== [6] disconnect (session stays logged in server-side) =="
kill "$XFREERDP_PID" 2>/dev/null || true
sleep 1
kill "$XVFB_PID" 2>/dev/null || true

echo "== done. xfreerdp log tail: =="
tail -5 /tmp/xfreerdp.log 2>/dev/null || true
