# syntax=docker/dockerfile:1
# Two targets from one image base:
#   docker build --target api    -t triageline-api .     # FastAPI gateway (PWA, auth, LiveKit tokens)
#   docker build --target worker -t triageline-worker .  # LiveKit voice worker (cascaded_agent.py)
# Secrets come from the environment (docker compose env_file / your orchestrator), never the image.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    TRIAGELINE_UPLOAD_DIR=/tmp/triageline_uploads \
    TRIAGELINE_HEARTBEAT_LOG=/tmp/agent_heartbeat.log TRIAGELINE_TOOL_LOG=/tmp/agent_tool_calls.log
RUN useradd --create-home --uid 10001 app
WORKDIR /app

FROM base AS api
COPY requirements-app.txt .
RUN pip install -r requirements-app.txt
COPY --chown=app:app agent agent
COPY --chown=app:app harness harness
COPY --chown=app:app livekit_agent livekit_agent
COPY --chown=app:app ui ui
COPY --chown=app:app frames frames
COPY --chown=app:app audio audio
COPY --chown=app:app scenarios scenarios
COPY --chown=app:app run_local.py ./
USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=2).status==200 else 1)"
# One process/replica: sessions are process-local (see README "Deployment limits").
CMD ["python", "-m", "uvicorn", "ui.api:app", "--host", "0.0.0.0", "--port", "8080", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--workers", "1"]

FROM base AS worker
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements-fdb.txt .
RUN pip install -r requirements-fdb.txt
COPY --chown=app:app agent agent
COPY --chown=app:app harness harness
COPY --chown=app:app livekit_agent livekit_agent
USER app
RUN python livekit_agent/cascaded_agent.py download-files || true
# TRIAGELINE_MODE=assistant for phone users; benchmark for the FDB-v3 runner (default).
CMD ["python", "livekit_agent/cascaded_agent.py", "start"]
