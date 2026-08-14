#!/usr/bin/env python3
"""
Lightweight, event-driven webhook receiver for triggering rdp-login.sh.

Idle cost is effectively zero: it blocks on the socket and only wakes when a
request arrives (no polling). On a matching event it runs a guarded flow:

  1. Guard polling   - every GUARD_INTERVAL seconds query the Beszel hub API:
                       proceed only when ONLINE system is up AND OFFLINE system
                       is still down. If OFFLINE system comes up meanwhile
                       (someone logged in), abort. Max GUARD_MAX_CHECKS tries.
  2. Login           - wait TRIGGER_DELAY, run the login script.
  3. Verify + retry  - poll until OFFLINE system is up (max VERIFY_WAIT), else
                       retry login up to LOGIN_MAX_ATTEMPTS times total.
  4. Failure alert   - when all attempts are exhausted, send the BARK_URL
                       push notification (if configured).

A single-flight lock ensures only one flow runs at a time; extra events while
a flow is running are skipped. Cooldown only starts counting after a real
login attempt (guard skips do not trigger cooldown).

Designed for Beszel's shoutrrr "generic" webhook, but generic enough for anything
that POSTs JSON or form/text containing a title/message string.

Environment (all optional, sensible defaults):
  WEBHOOK_PORT        listen port                                  [default 8080]
  WEBHOOK_PATH        only accept this path                        [default /trigger]
  WEBHOOK_TOKEN       if set, require ?token=... to match          [default empty = no auth]
  MATCH_RULES         rules separated by ';', keywords by ',';     [preferred]
                      triggers if ANY rule matches (all its keywords present).
                      e.g. "My Docker,down ; My Laptop,up"
                           -> (My Docker AND down) OR (My Laptop AND up)
  MATCH_KEYWORDS      legacy single-rule form (all keywords req'd) [default "up"]
                      used only when MATCH_RULES is unset. e.g. "My Host,up"
  MATCH_FIELD         json key to read text from, else whole body  [default "" = whole body]
  TRIGGER_DELAY       seconds to wait after guard passes           [default 30]
  LOGIN_SCRIPT        path to the login script                     [default /app/rdp-login.sh]
  TRIGGER_COOLDOWN    min seconds between two real logins          [default 120]
  GUARD_INTERVAL      seconds between guard status checks          [default 30]
  GUARD_MAX_CHECKS    max guard checks before giving up            [default 10]
  GUARD_ONLINE_NAME   system that must be UP (default: from MATCH_RULES ",up" rule)
  GUARD_OFFLINE_NAME  system that must be DOWN (default: from MATCH_RULES ",down" rule)
  VERIFY_WAIT         seconds to wait after login for OFFLINE to come up [default 150]
  VERIFY_POLL         seconds between verify checks                [default 30]
  LOGIN_MAX_ATTEMPTS  total login attempts (first + retries)       [default 3]
  BESZEL_URL          hub base URL                                 [default http://beszel:8090]
  BESZEL_EMAIL        bot account email for hub API auth
  BESZEL_PASS         bot account password for hub API auth
  BARK_URL            shoutrrr-style bark://:KEY@host?group=x push URL,
                      used ONLY for the all-attempts-failed alert
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("WEBHOOK_PORT", "8080"))
PATH = os.environ.get("WEBHOOK_PATH", "/trigger")
TOKEN = os.environ.get("WEBHOOK_TOKEN", "")
MATCH_FIELD = os.environ.get("MATCH_FIELD", "")
DELAY = int(os.environ.get("TRIGGER_DELAY", "30"))
LOGIN_SCRIPT = os.environ.get("LOGIN_SCRIPT", "/app/rdp-login.sh")
COOLDOWN = int(os.environ.get("TRIGGER_COOLDOWN", "120"))
GUARD_INTERVAL = int(os.environ.get("GUARD_INTERVAL", "30"))
GUARD_MAX_CHECKS = int(os.environ.get("GUARD_MAX_CHECKS", "10"))
VERIFY_WAIT = int(os.environ.get("VERIFY_WAIT", "150"))
VERIFY_POLL = int(os.environ.get("VERIFY_POLL", "30"))
LOGIN_MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", "3"))
BESZEL_URL = os.environ.get("BESZEL_URL", "http://beszel:8090").rstrip("/")
BESZEL_EMAIL = os.environ.get("BESZEL_EMAIL", "")
BESZEL_PASS = os.environ.get("BESZEL_PASS", "")
BARK_URL = os.environ.get("BARK_URL", "")


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


def _default_guard_names():
    """
    Derive guard system names from MATCH_RULES: the rule ending in ",up" names
    the system that must be UP; the one ending in ",down" the one that must be DOWN.
    e.g. "Perficient Windows 11 Docker,down ; Perficient Windows 11 Laptop,up"
         -> offline_name="Perficient Windows 11 Docker", online_name="Perficient Windows 11 Laptop"
    """
    online = offline = ""
    for rule in RULES:
        if len(rule) < 2:
            continue
        name, last = ", ".join(rule[:-1]), rule[-1].lower()
        if last == "up" and not online:
            online = name
        elif last == "down" and not offline:
            offline = name
    return online, offline


_default_online, _default_offline = _default_guard_names()
ONLINE_NAME = os.environ.get("GUARD_ONLINE_NAME", _default_online)
OFFLINE_NAME = os.environ.get("GUARD_OFFLINE_NAME", _default_offline)

_last_login = 0.0
_cd_lock = threading.Lock()
_flow_lock = threading.Lock()
_token_lock = threading.Lock()
_token = ""


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


# ---------------------------------------------------------------- Beszel API

def _get_token() -> str:
    """Authenticate to the hub PocketBase API; caches the token. Never logs secrets."""
    global _token
    with _token_lock:
        if _token:
            return _token
        body = json.dumps({"identity": BESZEL_EMAIL, "password": BESZEL_PASS}).encode()
        req = urllib.request.Request(
            f"{BESZEL_URL}/api/collections/users/auth-with-password",
            data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            _token = json.load(resp).get("token", "")
        log("hub auth ok")
        return _token


def _reset_token():
    global _token
    with _token_lock:
        _token = ""


def query_status(name: str):
    """Return the hub's status ('up'/'down'/...) for a system by exact name, or None on error."""
    filter_q = urllib.parse.quote(f"name = '{name}'")
    url = f"{BESZEL_URL}/api/collections/systems/records?filter={filter_q}&perPage=1"
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_get_token()}"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                items = json.load(resp).get("items", [])
            if not items:
                log(f"hub query: system '{name}' not found (check users permission)")
                return None
            return items[0].get("status")
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 1:
                _reset_token()
                continue
            log(f"hub query error for '{name}': HTTP {e.code}")
            return None
        except Exception as e:
            log(f"hub query error for '{name}': {e.__class__.__name__}")
            return None
    return None


# ---------------------------------------------------------------- Bark alert

def send_bark(title: str, body: str):
    """Send one push via a shoutrrr-style bark://:KEY@host?query URL. URL is never logged."""
    m = re.match(r"^bark://:([^@]+)@([^/?]+)(\?.*)?$", BARK_URL)
    if not m:
        log("bark: BARK_URL unset or unrecognized format, skip alert")
        return
    key, host, query = m.group(1), m.group(2), (m.group(3) or "")
    url = f"https://{host}/{key}/{urllib.parse.quote(title)}/{urllib.parse.quote(body)}{query}"
    try:
        urllib.request.urlopen(urllib.request.Request(url), timeout=10)
        log("bark: alert sent")
    except Exception as e:
        log(f"bark: send failed ({e.__class__.__name__})")


# ---------------------------------------------------------------- Main flow

class _GuardAbort(Exception):
    """Guard found OFFLINE system already up - abort the whole flow."""


def guard_passes() -> bool:
    """One guard check: ONLINE up AND OFFLINE down. Returns False to keep waiting,
    raises _GuardAbort when OFFLINE is already up (someone logged in - stop entirely)."""
    off = query_status(OFFLINE_NAME)
    on = query_status(ONLINE_NAME)
    if off == "up":
        raise _GuardAbort(f"{OFFLINE_NAME} already up (manual login?) - abort")
    return on == "up" and off == "down"


def run_login():
    if not _flow_lock.acquire(blocking=False):
        log("a flow is already running, skip event")
        return
    try:
        _run_flow()
    finally:
        _flow_lock.release()


def _run_flow():
    global _last_login
    # cooldown: only real logins count; guard-only skips never enter cooldown
    with _cd_lock:
        if time.time() - _last_login < COOLDOWN:
            log(f"cooldown active ({int(COOLDOWN - (time.time() - _last_login))}s left), skip")
            return

    # --- guard polling: wait until ONLINE is up while OFFLINE is still down ---
    passed = False
    for i in range(1, GUARD_MAX_CHECKS + 1):
        try:
            if guard_passes():
                log(f"guard passed (check {i}/{GUARD_MAX_CHECKS}): "
                    f"{ONLINE_NAME}=up, {OFFLINE_NAME}=down")
                passed = True
                break
        except Exception as abort:
            log(f"guard: {abort}")
            return
        log(f"guard check {i}/{GUARD_MAX_CHECKS} not ready, wait {GUARD_INTERVAL}s")
        time.sleep(GUARD_INTERVAL)
    if not passed:
        log("guard: gave up waiting (a later matched event can re-trigger)")
        return

    # --- login attempts ---
    if DELAY > 0:
        log(f"waiting {DELAY}s before login")
        time.sleep(DELAY)
    with _cd_lock:
        _last_login = time.time()
    for attempt in range(1, LOGIN_MAX_ATTEMPTS + 1):
        log(f"login attempt {attempt}/{LOGIN_MAX_ATTEMPTS}")
        try:
            r = subprocess.run(["/bin/bash", LOGIN_SCRIPT], timeout=300)
            log(f"login script exited code={r.returncode}")
        except Exception as e:
            log(f"login script error: {e}")

        # --- verify: OFFLINE system should come up ---
        waited = 0
        while waited < VERIFY_WAIT:
            time.sleep(min(VERIFY_POLL, VERIFY_WAIT - waited))
            waited += VERIFY_POLL
            if query_status(OFFLINE_NAME) == "up":
                log(f"verified: {OFFLINE_NAME} is up after attempt {attempt} - done")
                return
        log(f"{OFFLINE_NAME} still down after {VERIFY_WAIT}s")

    log(f"all {LOGIN_MAX_ATTEMPTS} attempts exhausted - giving up")
    send_bark("RDP auto-login failed",
              f"{OFFLINE_NAME} offline after {LOGIN_MAX_ATTEMPTS} attempts, manual login needed")


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
    log(f"guard: online='{ONLINE_NAME}' offline='{OFFLINE_NAME}' "
        f"interval={GUARD_INTERVAL}s max_checks={GUARD_MAX_CHECKS} "
        f"verify_wait={VERIFY_WAIT}s attempts={LOGIN_MAX_ATTEMPTS}")
    if not (BESZEL_EMAIL and BESZEL_PASS):
        log("WARNING: BESZEL_EMAIL/BESZEL_PASS unset - guard cannot query hub, flow disabled")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
