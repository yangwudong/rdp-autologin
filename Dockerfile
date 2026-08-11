FROM debian:12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    freerdp2-x11 \
    xvfb \
    xdotool \
    python3 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY rdp-login.sh /app/rdp-login.sh
COPY webhook-server.py /app/webhook-server.py
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/rdp-login.sh /app/webhook-server.py /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
