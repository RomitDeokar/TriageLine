"""One LiveKit participant-token issuer for every client path.

Used by:
  * POST /api/rtc/token     — browser / mobile app user signed in to this gateway
  * POST /api/mobile/token  — trusted backend-to-backend (Bearer TRIAGELINE_API_KEY)

Every call gets a fresh, unguessable room (so a rejoin always gets a fresh agent job and two
devices never share a conversation) and a least-privilege grant: join that one room, publish the
microphone only, subscribe, publish data. When TRIAGELINE_AGENT_NAME is set the token carries an
explicit agent dispatch (RoomConfiguration), so only the named worker joins the room.
Never embed TRIAGELINE_API_KEY or LIVEKIT_API_SECRET in a mobile binary.
"""
from __future__ import annotations

import os
import secrets
import time
from datetime import timedelta
from urllib.parse import urlsplit

TOKEN_TTL_S = 600  # default; override with TRIAGELINE_RTC_TOKEN_TTL_S (60..3600)
MODE = "LIVE AUDIO + SIMULATED TOOLS"


class NotConfigured(RuntimeError):
    """LiveKit URL/key/secret missing or malformed (message is safe to show)."""


def livekit_config() -> tuple[str, str, str]:
    url, key, secret = (os.environ.get(k, "") for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"))
    try:
        parsed = urlsplit(url)
        _ = parsed.port  # raises ValueError on a malformed/non-numeric port
        allowed = ("wss",) if (os.environ.get("TRIAGELINE_ENV") == "production"
                               or os.environ.get("TRIAGELINE_PRODUCTION") == "1") else ("wss", "ws")
        valid = (parsed.scheme in allowed and parsed.hostname and not parsed.username
                 and not parsed.password and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid or not key or not secret or any("<" in v for v in (url, key, secret)):
        raise NotConfigured("Configure LIVEKIT_URL (wss://…), LIVEKIT_API_KEY and LIVEKIT_API_SECRET")
    return url, key, secret


def configured() -> bool:
    try:
        livekit_config()
        return True
    except NotConfigured:
        return False


def agent_name() -> str:
    return os.environ.get("TRIAGELINE_AGENT_NAME", "").strip()


def issue_token(owner: str | None = None, ttl_s: int | None = None) -> dict:
    """Short-lived participant token for a brand-new room. ``owner`` tags the identity only."""
    url, key, secret = livekit_config()
    from livekit import api

    ttl = int(ttl_s or os.environ.get("TRIAGELINE_RTC_TOKEN_TTL_S") or TOKEN_TTL_S)
    ttl = min(max(ttl, 60), 3600)
    tag = (owner or secrets.token_hex(8))[:8]
    room = f"triageline-{tag}-{secrets.token_urlsafe(9)}"
    identity = f"user-{tag}-{secrets.token_urlsafe(6)}"
    grants = api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True,
                             can_publish_data=True, can_publish_sources=["microphone"])
    token = api.AccessToken(key, secret).with_identity(identity).with_ttl(timedelta(seconds=ttl)).with_grants(grants)
    name = agent_name()
    if name:
        token = token.with_room_config(api.RoomConfiguration(agents=[api.RoomAgentDispatch(agent_name=name)]))
    now = int(time.time())
    return {"url": url, "token": token.to_jwt(), "room": room, "identity": identity,
            "expires_in": ttl, "expires_at": now + ttl, "agent_name": name or None,
            "mode": MODE, "tools": "simulated"}
