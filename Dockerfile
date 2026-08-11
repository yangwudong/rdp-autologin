FROM debian:12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    freerdp2-x11 \
    xvfb \
    xdotool \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY rdp-login.sh /app/rdp-login.sh
RUN chmod +x /app/rdp-login.sh

ENTRYPOINT ["/app/rdp-login.sh"]
