# Deploying TriageLine

Two ways, in order of permanence. Both use the same code and `.env` keys; nothing in the image
contains secrets.

---

## A. Temporary public demo (no account, 1 minute)

The gateway runs locally and is exposed through a Cloudflare quick tunnel. The URL is random and
dies when the tunnel process stops — perfect for a demo or a video shoot.

```bash
# 1. gateway (allow the tunnel host through the Host-header allowlist)
ALLOWED_HOSTS="localhost,127.0.0.1,<your-tunnel-host>" \
  .venv-win/Scripts/python.exe -m uvicorn ui.api:app --host 0.0.0.0 --port 8080 \
  --proxy-headers --forwarded-allow-ips="*"

# 2. voice workers (one per call flow)
TRIAGELINE_AGENT_NAME=triageline-assistant .venv-win/Scripts/python.exe livekit_agent/cascaded_agent.py dev
TRIAGELINE_AGENT_NAME=triageline-triage    .venv-win/Scripts/python.exe livekit_agent/triage_livekit_agent.py dev

# 3. public URL
cloudflared tunnel --url http://127.0.0.1:8080
```

Open the printed `https://…trycloudflare.com` URL, sign in with the access code from `.env`
(`TRIAGELINE_ACCESS_CODE`), and use `/rtc.html?flow=triage` for the Triage Line call.

Notes
- Set `ALLOWED_HOSTS` to include the tunnel hostname, or the gateway answers `400 Invalid host header`.
- Quick tunnels are rate-limited and unsuitable for real users. For a stable temporary URL use a
  **named** Cloudflare tunnel (free account + a domain): `cloudflared tunnel create triageline`,
  then `cloudflared tunnel run --url http://127.0.0.1:8080 triageline`.

---

## B. Permanent deployment

### B1. Docker Compose (this machine or any VM)

The image builds both targets; compose runs the gateway + both voice workers with
`restart: unless-stopped`. Secrets come from `.env` (never baked into the image).

```bash
cp .env.example .env      # fill LiveKit + Deepgram + Gemini keys and the access code
docker compose up -d --build
docker compose logs -f api
```

What compose starts
| service | what it is |
|---|---|
| `api` | FastAPI gateway on :8080 — PWA, access-code auth, LiveKit token API |
| `worker` | assistant call flow (`cascaded_agent.py`, LiveKit automatic dispatch) |
| `triage-worker` | Triage Line extension (`triage_livekit_agent.py`) |

Put a TLS reverse proxy (Caddy/nginx/Cloud LB) in front of :8080 and set
`ALLOWED_HOSTS` to the public hostname; keep :8080 off the public internet.

**LiveKit Cloud is already the media/transport layer** — the workers dial out to
`wss://<project>.livekit.cloud`, so the containers need outbound HTTPS only, no inbound ports.

### B2. A real host (recommended for "permanent")

Any container host works because the workers are outbound-only:

1. **VM + compose** (most control): create a small VM (2 vCPU / 2 GB), install Docker,
   `git clone https://github.com/RomitDeokar/TriageLine`, copy `.env`, `docker compose up -d --build`,
   add Caddy for TLS on your domain.
2. **Managed container hosts** (Render / Railway / Fly.io): point them at the repo, use
   `Dockerfile` target `api` for the web service and `worker` / `triage-worker` as background
   workers; set the same env vars in their dashboards.
3. **One process per replica**: sessions are process-local (see README "Deployment limits"), so keep
   `--workers 1` and scale horizontally only after moving session state to a shared store.

### Permanent checklist
- [ ] `TRIAGELINE_ENV=production`, `TRIAGELINE_ACCESS_CODE` (16+ chars), `TRIAGELINE_SESSION_SECRET` (32+)
- [ ] `ALLOWED_HOSTS` = your public hostname only (no `*`)
- [ ] TLS terminated by the proxy; :8080 not publicly reachable
- [ ] `.env` never committed (it is gitignored)
- [ ] Health check: `GET /api/health` returns `{"ok": true}`
- [ ] Rotate the LiveKit / Deepgram / Gemini keys before making the URL public
