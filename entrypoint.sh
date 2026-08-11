#!/bin/bash
# Dispatch by MODE:
#   MODE=once     (default) run one RDP auto-login and exit  -> for manual/one-shot use
#   MODE=webhook            run the webhook listener forever  -> event-driven auto-trigger
set -uo pipefail

MODE="${MODE:-once}"

case "$MODE" in
  once)
    exec /bin/bash /app/rdp-login.sh
    ;;
  webhook)
    exec python3 /app/webhook-server.py
    ;;
  *)
    echo "unknown MODE='$MODE' (expected 'once' or 'webhook')" >&2
    exit 2
    ;;
esac
