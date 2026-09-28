"""ASGI mobile gateway. Run ONE worker: python -m uvicorn ui.api:app.

An invite-code gate provides pilot access, not customer identity/SSO. Random,
signed owner credentials isolate conversations. Native clients use Bearer;
browsers use HttpOnly cookies. All business action tools remain simulated.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from starlette.middleware.trustedhost import TrustedHostMiddleware

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "livekit_agent/.env.local")
from ui import live  # noqa: E402

log = logging.getLogger("triageline.api")
MAX_BODY = 8 * 1024 * 1024
TOKEN_TTL = 8 * 3600


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Body):
    access_code: str = Field(default="", max_length=256)


class Command(Body):
    text: str | None = Field(default=None, min_length=1, max_length=500)
    speaking: StrictBool = False
    image: str | None = None
    audio: str | None = None
    request_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{8,80}$")


class Start(Body):
    request_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{8,80}$")


class RateLimit:
    """Bounded single-process fixed windows. Edge limits still required in deployment."""
    def __init__(self):
        self.windows = OrderedDict()

    def check(self, key, limit):
        now = time.monotonic()
        began, count = self.windows.get(key, (now, 0))
        if now - began >= 60:
            began, count = now, 0
        if count >= limit:
            raise HTTPException(429, "Rate limit reached. Try again shortly.", headers={"Retry-After": "60"})
        self.windows[key] = (began, count + 1)
        self.windows.move_to_end(key)
        while len(self.windows) > 10000:
            self.windows.popitem(last=False)


def create_app() -> FastAPI:
    production = os.getenv("TRIAGELINE_ENV", "development") == "production"
    access_code = os.getenv("TRIAGELINE_ACCESS_CODE", "")
    secret = os.getenv("TRIAGELINE_SESSION_SECRET", "") or secrets.token_hex(32)
    origins = [x.strip().rstrip("/") for x in os.getenv("CORS_ORIGINS", "").split(",") if x.strip()]
    hosts = [x.strip() for x in os.getenv("ALLOWED_HOSTS", "").split(",") if x.strip()]
    if "*" in origins:
        raise RuntimeError("CORS_ORIGINS must list explicit origins, not *")
    if production and (len(access_code) < 16 or len(os.getenv("TRIAGELINE_SESSION_SECRET", "")) < 32 or not hosts or "*" in hosts):
        raise RuntimeError("Production requires ACCESS_CODE (16+), SESSION_SECRET (32+) and explicit ALLOWED_HOSTS")
    limiter = RateLimit()
    manager = live.Sessions()
    starts: OrderedDict = OrderedDict()
    operations = asyncio.Lock()

    async def reap():
        while True:
            await asyncio.sleep(30)
            with manager.lock:
                manager.reap()

    @asynccontextmanager
    async def lifespan(_app):
        reaper = asyncio.create_task(reap())
        try:
            yield
        finally:
            reaper.cancel()
            await asyncio.gather(reaper, return_exceptions=True)
            for sid in list(manager.by_id):
                s = manager.get(sid)
                manager.end(sid)
                if s:
                    await asyncio.to_thread(s.thread.join, 3)

    app = FastAPI(title="TriageLine Mobile API", version="1.0.0", lifespan=lifespan,
                  docs_url=None if production else "/docs", redoc_url=None,
                  openapi_url=None if production else "/openapi.json")
    app.state.sessions = manager
    app.state.limiter = limiter
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type", "Authorization", "Last-Event-ID"])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts or ["*"])

    @app.middleware("http")
    async def protections(request: Request, call_next):
        request_id = secrets.token_hex(8)
        try:
            if request.url.path.startswith("/api/"):
                origin = request.headers.get("origin")
                same_origin = f"{request.url.scheme}://{request.url.netloc}"
                if origin and origin != same_origin and origin not in origins:
                    raise HTTPException(403, "Origin not allowed")
                if request.method == "POST":
                    try:
                        size = int(request.headers.get("content-length", "0"))
                    except ValueError:
                        raise HTTPException(400, "Invalid Content-Length")
                    if size < 0 or size > MAX_BODY:
                        raise HTTPException(413, "Request too large")
                    if request.headers.get("content-type", "").split(";")[0] != "application/json":
                        raise HTTPException(415, "Use application/json")
                    async def read_bounded():
                        chunks, total = [], 0
                        async for chunk in request.stream():
                            total += len(chunk)
                            if total > MAX_BODY:
                                raise HTTPException(413, "Request too large")
                            chunks.append(chunk)
                        return b"".join(chunks)
                    request._body = await asyncio.wait_for(read_bounded(), 10)
            response = await call_next(request)
        except HTTPException as exc:
            response = JSONResponse({"error": exc.detail, "code": "request_rejected"}, exc.status_code, headers=exc.headers)
        except asyncio.TimeoutError:
            response = JSONResponse({"error": "Request body timed out", "code": "timeout"}, 408)
        except Exception as exc:
            log.error("request_failed id=%s type=%s", request_id, type(exc).__name__)
            response = JSONResponse({"error": "Server error", "code": "server_error"}, 500)
        response.headers.update({"X-Request-ID": request_id, "X-Content-Type-Options": "nosniff",
                                 "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
                                 "Permissions-Policy": "camera=(self), microphone=(self)",
                                 "Cache-Control": "no-store"})
        if production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc):
        code = "no_session" if exc.status_code == 404 else "unauthorized" if exc.status_code == 401 else "request_rejected"
        return JSONResponse({"error": exc.detail, "code": code}, exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid(_request, exc):
        # Do not echo the body (may contain recordings, access codes or personal information).
        fields = [".".join(str(x) for x in e["loc"]) for e in exc.errors()]
        return JSONResponse({"error": "Invalid request fields", "fields": fields, "code": "bad_request"}, 422)

    def issue(owner):
        payload = f"{owner}.{int(time.time()) + TOKEN_TTL}"
        sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return payload + "." + sig

    def authenticate(request: Request):
        bearer = request.headers.get("authorization", "")
        token = bearer[7:] if bearer.startswith("Bearer ") else request.cookies.get("tl_auth", "")
        try:
            owner, expires, sig = token.split(".")
            payload = f"{owner}.{expires}"
            expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
            if len(owner) != 32 or not hmac.compare_digest(sig, expected) or int(expires) <= time.time():
                raise ValueError()
        except (ValueError, TypeError):
            raise HTTPException(401, "Sign in to start or resume a session")
        request.state.auth_expiry = int(expires)
        request.state.owner = owner
        return owner

    def owned(sid, owner):
        s = manager.get(sid)
        if not s or getattr(s, "owner", None) != owner:
            raise HTTPException(404, "Session expired or unavailable")
        return s

    @app.get("/api/health")
    async def health():
        return {"ok": True, "service": "triageline"}

    @app.get("/api/auth/config")
    async def auth_config():
        return {"access_code_required": bool(access_code), "production": production}

    @app.post("/api/auth/login")
    async def login(body: Login, request: Request):
        limiter.check(("login", request.client.host if request.client else "unknown"), 10)
        if access_code and not hmac.compare_digest(body.access_code.encode(), access_code.encode()):
            raise HTTPException(401, "Invalid access code")
        token = issue(secrets.token_hex(16))
        response = JSONResponse({"access_token": token, "token_type": "Bearer", "expires_in": TOKEN_TTL})
        response.set_cookie("tl_auth", token, max_age=TOKEN_TTL, httponly=True,
                            secure=production or request.url.scheme == "https", samesite="strict")
        return response

    @app.get("/api/auth/me")
    async def me(owner=Depends(authenticate)):
        return {"authenticated": True}

    @app.get("/api/ready")
    async def ready():
        status = live.readiness()
        status["sessions"] = len(manager.by_id)
        status["rtc"] = {"configured": all(os.getenv(k) and "<" not in os.getenv(k, "") for k in
                                         ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")),
                         "worker_verified": False}
        status["production_ready"] = False
        status["limitations"] = ["Business tools are simulated", "Session memory is process-local",
                                 "Provider configuration does not prove connectivity or quota"]
        return status

    @app.post("/api/live/start")
    async def start(body: Start, owner=Depends(authenticate)):
        async with operations:
            key = (owner, body.request_id)
            if body.request_id and key in starts and manager.get(starts[key]):
                s = owned(starts[key], owner)
            else:
                limiter.check(("start", owner), 6)
                if sum(getattr(s, "owner", None) == owner for s in manager.by_id.values()) >= 2:
                    raise HTTPException(429, "End an existing session before starting another")
                try:
                    s = await asyncio.to_thread(manager.start)
                except OverflowError:
                    raise HTTPException(429, "Session capacity reached")
                s.owner = owner
                s.requests = {}
                s.command_lock = asyncio.Lock()
                if body.request_id:
                    starts[key] = s.sid
                    while len(starts) > 1000:
                        starts.popitem(last=False)
            return {"sid": s.sid, "mode": live.MODE, "audio": live.P.speech_config()}

    @app.post("/api/live/{sid}/{op}")
    async def command(sid: str, op: str, body: Command, owner=Depends(authenticate)):
        s = owned(sid, owner)
        limiter.check(("command", owner), 120)
        if op not in {"say", "audio", "frame", "interrupt", "log", "end"}:
            raise HTTPException(404, "Unknown operation")
        required = {"say": "text", "audio": "audio", "frame": "image"}.get(op)
        if required and not getattr(body, required):
            raise HTTPException(422, f"{required} is required")
        async with s.command_lock:
            signature = hashlib.sha256((op + body.model_dump_json()).encode()).hexdigest()
            if body.request_id and body.request_id in s.requests:
                previous, result = s.requests[body.request_id]
                if previous != signature:
                    raise HTTPException(409, "request_id was already used with different input")
                return result
            if len(s.requests) >= 500:
                raise HTTPException(429, "Session request limit reached; start a new session")
            try:
                if op == "say":
                    result = {"ok": True, "as": s.user_text(body.text, body.speaking)}
                elif op == "audio":
                    result = {"ok": True, "ref": await asyncio.to_thread(s.user_audio, body.audio, body.speaking)}
                elif op == "frame":
                    result = {"ok": True, "ref": await asyncio.to_thread(s.user_frame, body.image)}
                elif op == "interrupt":
                    s.interrupt()
                    result = {"ok": True}
                elif op == "end":
                    manager.end(sid)
                    result = {"ok": True}
                else:
                    s.touched = time.time()
                    result = {"log": s.events_after(0), "mode": live.MODE, "audio": live.P.speech_config()}
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            if body.request_id:
                s.requests[body.request_id] = (signature, result)
            return result

    @app.get("/api/live/{sid}/stream")
    async def stream(sid: str, request: Request, last: int = 0, owner=Depends(authenticate)):
        s = owned(sid, owner)
        limiter.check(("stream", owner), 30)
        try:
            cursor = max(0, int(request.headers.get("last-event-id", last)))
        except ValueError:
            raise HTTPException(400, "Invalid Last-Event-ID")
        if cursor > s.seq:
            raise HTTPException(409, "Event cursor is ahead of this session")
        generation = s.claim_stream()
        initial_seq = s.seq
        expiry = request.state.auth_expiry

        async def events():
            nonlocal cursor
            yield "retry: 1500\n\n"
            heartbeat = time.monotonic()
            while generation == s.stream_gen and time.time() < expiry:
                if await request.is_disconnected():
                    break
                batch = s.events_after(cursor)
                if batch and batch[0]["id"] > cursor + 1:
                    yield 'event: gap\ndata: {"message":"Older events have expired; showing available history"}\n\n'
                for ev in batch:
                    cursor = ev["id"]
                    yield f"id: {cursor}\ndata: {json.dumps({**ev, 'replay': cursor <= initial_seq})}\n\n"
                if s.closed:
                    break
                if time.monotonic() - heartbeat > 10:
                    yield ": ping\n\n"
                    heartbeat = time.monotonic()
                # Heartbeats do not extend idle lifetime: a forgotten tab must expire.
                await asyncio.sleep(0.1)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"})

    @app.post("/api/rtc/token")
    async def rtc_token(owner=Depends(authenticate)):
        limiter.check(("rtc", owner), 6)
        url, key, api_secret = (os.getenv(k, "") for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"))
        if not url.startswith("wss://") or not key or not api_secret or "<" in url:
            raise HTTPException(503, "Configure LiveKit URL, API key and secret, then start the voice worker")
        from livekit import api
        # Client cannot choose room/identity or gain admin grants. One room per owner.
        room = "triageline-" + owner
        grants = api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True,
                                 can_publish_data=True, can_publish_sources=["microphone"])
        token = (api.AccessToken(key, api_secret).with_identity("user-" + owner)
                 .with_ttl(timedelta(minutes=5)).with_grants(grants).to_jwt())
        return {"url": url, "token": token, "room": room, "expires_in": 300, "tools": "simulated"}

    @app.get("/")
    @app.get("/app")
    @app.get("/live")
    async def home():
        return FileResponse(ROOT / "ui/static/live.html")

    # No evaluation/diagnostic execution endpoint is exposed by the mobile gateway.
    app.mount("/", StaticFiles(directory=ROOT / "ui/static", html=True), name="static")
    return app


app = create_app()
