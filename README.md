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

| Var | Required | Default | Meaning |
|-----|----------|---------|---------|
| `RDP_HOST` | yes | — | Target host |
| `RDP_USER` | yes | — | Username |
| `RDP_PASSWORD` | yes | — | Password |
| `RDP_DOMAIN` | no | (none) | Domain |
| `RENDER_WAIT` | no | `8` | Seconds to wait for the session to render before Enter |
| `HOLD_AFTER` | no | `20` | Seconds to hold the session open after Enter |

The image itself contains **no credentials**. Everything sensitive is injected at
run time via `--env-file`. See `.env.example` for the file format.

## Run

```bash
# Create a local .env from the template and fill in real values:
cp .env.example .env
chmod 600 .env
# edit .env ...

docker run --rm --env-file .env <your-image>
```

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
