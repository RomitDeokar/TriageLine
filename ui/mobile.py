"""Issue short-lived LiveKit participant credentials to a trusted application backend.

This endpoint must be called server-to-server. Never embed TRIAGELINE_API_KEY or
LIVEKIT_API_SECRET in a mobile binary. The caller authenticates its own user and
forwards the returned participant token to that user over TLS.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlparse


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).rstrip(b"=").decode()


def issue_token() -> dict:
    url = os.environ.get("LIVEKIT_URL", "")
    key = os.environ.get("LIVEKIT_API_KEY", "")
    secret = os.environ.get("LIVEKIT_API_SECRET", "")
    parsed = urlparse(url)
    if parsed.scheme not in ("wss", "ws") or not parsed.netloc or not key or not secret:
        raise RuntimeError("LiveKit credentials are not configured")
    if os.environ.get("TRIAGELINE_PRODUCTION") == "1" and parsed.scheme != "wss":
        raise RuntimeError("production requires a wss LiveKit URL")
    now = int(time.time())
    room = "triageline-" + secrets.token_urlsafe(18)
    identity = "participant-" + secrets.token_urlsafe(18)
    # LiveKit access-token grants: a unique room per call, with least-privilege
    # publish/subscribe permissions. JWT expiration limits JOIN, not room duration.
    claims = {"iss": key, "sub": identity, "iat": now, "nbf": now,
              "exp": now + 600, "video": {"roomJoin": True, "room": room,
                                         "canPublish": True, "canSubscribe": True,
                                         "canPublishData": True}}
    data = _b64({"alg": "HS256", "typ": "JWT"}) + "." + _b64(claims)
    signature = hmac.new(secret.encode(), data.encode(), hashlib.sha256).digest()
    token = data + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    return {"url": url, "token": token, "room": room, "identity": identity,
            "expires_at": claims["exp"], "mode": "LIVE AUDIO + MOCK TOOLS"}
