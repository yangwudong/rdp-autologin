# rdp-autologin

Headless, unattended RDP auto-login in a container. Connects to a Windows host over
RDP with NLA (which skips the Ctrl+Alt+Del secure-attention screen), dismisses the
Legal Notice / OK banner with a single Enter, holds briefly so the session fully
establishes and Startup-folder items launch (e.g. mail client, a VMware VM), then
disconnects. The server-side Windows session stays logged in after disconnect.

Built for the case where a managed Windows laptop is force-rebooted by Windows Update
and nobody is physically present to log in — so apps and VMs that require an
interactive session never come up. Native Windows ARSO is blocked on managed devices
by LSA Protection (RunAsPPL) policy, and VMware Workstation Pro can't power on VMs
from a non-interactive Session 0 — so a scripted interactive RDP login is the
working path.

## Image

Built and pushed by GitHub Actions on every push to `main`, as
`<DOCKERHUB_USERNAME>/<DOCKERHUB_REPO>:latest` (linux/amd64), where the name is
derived entirely from repository secrets (see below).

## Configuration — all via environment variables

### Run mode

| Var | Default | Meaning |
|-----|---------|---------|
| `MODE` | `once` | `once` = run one login and exit (manual / one-shot). `webhook` = run an event-driven listener that triggers a login when a matching webhook arrives. |

### RDP connection (both modes)

| Var | Required | Default | Meaning |
|-----|----------|---------|---------|
| `RDP_HOST` | yes | — | Target host |
| `RDP_USER` | yes | — | Username |
| `RDP_PASSWORD` | yes | — | Password |
| `RDP_DOMAIN` | no | (none) | Domain |
| `RENDER_WAIT` | no | `8` | Seconds to wait for the session to render before Enter |
| `HOLD_AFTER` | no | `20` | Seconds to hold the session open after Enter |

### Webhook mode (`MODE=webhook`)

Idle-cost is effectively zero: it blocks on the socket and only wakes on a request
(no polling). On a matching POST it runs a **guarded flow**:

1. **Guard polling** — every `GUARD_INTERVAL`s query the Beszel hub API: proceed
   only when the `,up`-rule system is up AND the `,down`-rule system is still
   down. If the `,down` system comes up meanwhile (someone logged in), abort.
   Gives up after `GUARD_MAX_CHECKS` tries (a later matched event can re-trigger).
2. **Login** — wait `TRIGGER_DELAY`, run the login script.
3. **Verify + retry** — poll until the `,down` system is up (max `VERIFY_WAIT`s
   per attempt), else retry login, up to `LOGIN_MAX_ATTEMPTS` total.
4. **Failure alert** — when all attempts fail, send one push via `BARK_URL`.

A single-flight lock skips events while a flow is running; cooldown counts only
real logins (guard skips don't). Designed for a shoutrrr **generic** webhook
(e.g. Beszel), but works with anything that POSTs JSON/text containing a
title/message string.

| Var | Default | Meaning |
|-----|---------|---------|
| `WEBHOOK_PORT` | `8080` | Listen port inside the container |
| `WEBHOOK_PATH` | `/trigger` | Only accept POSTs to this path |
| `WEBHOOK_TOKEN` | (none) | If set, require `?token=...` on the request |
| `MATCH_RULES` | (none) | Rules separated by `;`, keywords by `,`. Triggers if **any** rule matches (all its keywords present). Preferred. e.g. `My VM,down ; My Host,up` |
| `MATCH_KEYWORDS` | `up` | Legacy single-rule form (all required); used only if `MATCH_RULES` unset |
| `MATCH_FIELD` | (none) | JSON key to read text from; empty = match whole body |
| `TRIGGER_DELAY` | `30` | Seconds to wait after guard passes before logging in |
| `TRIGGER_COOLDOWN` | `120` | Min seconds between two **real** logins (debounce) |
| `GUARD_INTERVAL` | `30` | Seconds between guard status checks |
| `GUARD_MAX_CHECKS` | `10` | Max guard checks before giving up |
| `GUARD_ONLINE_NAME` / `GUARD_OFFLINE_NAME` | from `MATCH_RULES` | Override the two systems the guard queries |
| `VERIFY_WAIT` | `150` | Seconds to wait after each login for the offline system to come up |
| `VERIFY_POLL` | `30` | Seconds between verify checks |
| `LOGIN_MAX_ATTEMPTS` | `3` | Total login attempts (first + retries) |
| `BESZEL_URL` | `http://beszel:8090` | Hub base URL for status queries |
| `BESZEL_EMAIL` / `BESZEL_PASS` | (none) | Bot account for the hub API (must be in each system's users) |
| `BARK_URL` | (none) | shoutrrr-style `bark://:KEY@host?group=x` URL for the all-attempts-failed alert |

The image itself contains **no credentials**. Everything sensitive is injected at
run time via `--env-file`. See `.env.example` for the file format.

## Run

```bash
cp .env.example .env
chmod 600 .env
# edit .env ...

# one-shot login:
docker run --rm --env-file .env <your-image>

# or event-driven listener (set MODE=webhook in .env):
docker run -d --env-file .env -p 55679:8080 <your-image>
```

### Example: trigger from Beszel

Point a Beszel shoutrrr **generic** webhook (with `disabletls=yes` for a plain-HTTP
listener) at this service. Beszel sends titles like `Connection to <name> is up ✅`
or `Connection to <name> is down 🔴` only on state-change edges.

Belt-and-suspenders with two rules — fire when the VM goes offline **or** when the
host comes back online:

```
MATCH_RULES=<container/host name>,down ; <system agent name>,up
```

The `,down` system is the one that only runs after login (VM / Docker Desktop),
the `,up` one the system-level agent that is online whenever the machine is on.
The guard makes the pair safe: RDP is only attempted when the machine is
reachable (`,up` system online) but nobody has logged in yet (`,down` system
offline).

## GitHub Actions secrets required

Set these in the repo (Settings → Secrets and variables → Actions):

- `DOCKERHUB_USERNAME` — Docker Hub username
- `DOCKERHUB_TOKEN` — a Docker Hub access token with write scope
- `DOCKERHUB_REPO` — target repository name (image is pushed as `<USERNAME>/<REPO>`)

## Security notes

- The real `.env` is git-ignored and never committed.
- The image is credential-free; the repo/workflow contain no personal values.
- On disconnect the Windows session remains **logged in but locked/disconnected** —
  it is not signed off, so Startup items keep running.
