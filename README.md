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
(no polling). On a matching POST it waits `TRIGGER_DELAY` then runs the login once.
Designed for a shoutrrr **generic** webhook (e.g. Beszel), but works with anything
that POSTs JSON/text containing a title/message string.

| Var | Default | Meaning |
|-----|---------|---------|
| `WEBHOOK_PORT` | `8080` | Listen port inside the container |
| `WEBHOOK_PATH` | `/trigger` | Only accept POSTs to this path |
| `WEBHOOK_TOKEN` | (none) | If set, require `?token=...` on the request |
| `MATCH_KEYWORDS` | `up` | Comma-separated; **all** must appear in the payload to trigger |
| `MATCH_FIELD` | (none) | JSON key to read text from; empty = match whole body |
| `TRIGGER_DELAY` | `40` | Seconds to wait after event before logging in |
| `TRIGGER_COOLDOWN` | `120` | Min seconds between two triggers (debounce) |

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

### Example: trigger from Beszel when a host comes back online

Point a Beszel shoutrrr **generic** webhook at this listener and set
`MATCH_KEYWORDS=<system name>,up`. Beszel sends a title like
`Connection to <system name> is up ✅` only on the offline→online edge, so the
login fires exactly when the target host reboots and nobody has logged in yet.

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
