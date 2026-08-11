#!/usr/bin/env python3
"""
Lightweight, event-driven webhook receiver for triggering rdp-login.sh.

Idle cost is effectively zero: it blocks on the socket and only wakes when a
request arrives (no polling). On a matching event it waits TRIGGER_DELAY seconds
then runs the login script once.

Designed for Beszel's shoutrrr "generic" webhook, but generic enough for anything
that POSTs JSON or form/text containing a title/message string.

Environment (all optional, sensible defaults):
  WEBHOOK_PORT        listen port                                  [default 8080]
  WEBHOOK_PATH        only accept this path                        [default /trigger]
  WEBHOOK_TOKEN       if set, require ?token=... to match          [default empty = no auth]
  MATCH_RULES         rules separated by ';', keywords by ',';     [preferred]
                      triggers if ANY rule matches (all its keywords present).
                      e.g. "My VM,down ; My Host,up"
                           -> (My VM AND down) OR (My Host AND up)
  MATCH_KEYWORDS      legacy single-rule form (all keywords req'd) [default "up"]
                      used only when MATCH_RULES is unset. e.g. "My Host,up"
  MATCH_FIELD         json key to read text from, else whole body  [default "" = whole body]
  TRIGGER_DELAY       seconds to wait before running login         [default 40]
  LOGIN_SCRIPT        path to the login script                     [default /app/rdp-login.sh]
  TRIGGER_COOLDOWN    min seconds between two triggers (debounce)  [default 120]
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("WEBHOOK_PORT", "8080"))
PATH = os.environ.get("WEBHOOK_PATH", "/trigger")
TOKEN = os.environ.get("WEBHOOK_TOKEN", "")
MATCH_FIELD = os.environ.get("MATCH_FIELD", "")
DELAY = int(os.environ.get("TRIGGER_DELAY", "40"))
LOGIN_SCRIPT = os.environ.get("LOGIN_SCRIPT", "/app/rdp-login.sh")
COOLDOWN = int(os.environ.get("TRIGGER_COOLDOWN", "120"))


def _parse_rules():
    """
    Build a list of rules; the payload triggers if ANY rule matches.
    Each rule is a list of keywords that must ALL be present (case-insensitive).

    MATCH_RULES (preferred): rules separated by ';', keywords within a rule by ','.
      e.g. "Host A,down ; Host B,up"  ->  (Host A AND down) OR (Host B AND up)
    MATCH_KEYWORDS (legacy, single rule): comma-separated keywords, all required.
      e.g. "Host A,up"  ->  (Host A AND up)
    """
    raw_rules = os.environ.get("MATCH_RULES", "").strip()
    if raw_rules:
        rules = []
        for group in raw_rules.split(";"):
            kws = [k.strip() for k in group.split(",") if k.strip()]
            if kws:
                rules.append(kws)
        return rules
    legacy = [k.strip() for k in os.environ.get("MATCH_KEYWORDS", "up").split(",") if k.strip()]
    return [legacy] if legacy else []


RULES = _parse_rules()

_last_trigger = 0.0
_lock = threading.Lock()


def log(msg):
    print(f"[webhook] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def extract_text(raw: bytes) -> str:
    """Return the text to match against. If MATCH_FIELD set and body is JSON, use that field; else whole body."""
    body = raw.decode("utf-8", errors="replace")
    if MATCH_FIELD:
        try:
            data = json.loads(body)
            # support nested-ish: exact key match anywhere at top level
            if isinstance(data, dict) and MATCH_FIELD in data:
                return str(data[MATCH_FIELD])
        except Exception:
            pass
    return body


def matches(text: str) -> bool:
    """Case-insensitive: triggers if ANY rule matches (all keywords in that rule present)."""
    low = text.lower()
    for rule in RULES:
        if all(k.lower() in low for k in rule):
            return True
    return False


def run_login():
    global _last_trigger
    with _lock:
        now = time.time()
        if now - _last_trigger < COOLDOWN:
            log(f"cooldown active ({int(COOLDOWN - (now - _last_trigger))}s left), skip")
            return
        _last_trigger = now
    log(f"match! waiting {DELAY}s before login")
    time.sleep(DELAY)
    log("running login script")
    try:
        r = subprocess.run(["/bin/bash", LOGIN_SCRIPT], timeout=300)
        log(f"login script exited code={r.returncode}")
    except Exception as e:
        log(f"login script error: {e}")


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, msg):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(msg.encode())

    def do_POST(self):
        # path check
        path = self.path.split("?", 1)[0]
        if path != PATH:
            self._reply(404, "not found")
            return
        # token check
        if TOKEN:
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            if f"token={TOKEN}" not in q:
                log("rejected: bad/missing token")
                self._reply(403, "forbidden")
                return
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        text = extract_text(raw)
        snippet = text.replace("\n", " ")[:200]
        if matches(text):
            log(f"payload MATCHED rules={RULES}: {snippet}")
            threading.Thread(target=run_login, daemon=True).start()
            self._reply(200, "triggered")
        else:
            log(f"payload ignored (no match): {snippet}")
            self._reply(200, "ignored")

    def do_GET(self):
        # simple health endpoint
        if self.path.split("?", 1)[0] in ("/health", "/"):
            self._reply(200, "ok")
        else:
            self._reply(404, "not found")

    def log_message(self, *args):
        pass  # silence default access logging; we log our own


def main():
    log(f"listening on :{PORT}{PATH}  rules={RULES}  delay={DELAY}s  cooldown={COOLDOWN}s  auth={'yes' if TOKEN else 'no'}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
