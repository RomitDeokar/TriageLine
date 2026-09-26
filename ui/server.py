#!/usr/bin/env python3
"""TriageLine server — Evaluation Console + Live Assistant (mobile PWA).

    python ui/server.py            # http://localhost:8080  (console)   /live.html (phone app)

Evaluation console (replays scenarios through the official harness + scorer):
  GET  /api/scenarios
  POST /api/run {path|scenario, agent, time_scale, tail_ms} -> {trace, score, config}
Live assistant (persistent session, streamed output):
  POST /api/live/start                      -> {sid, mode}
  POST /api/live/<sid>/say   {text, speaking}
  POST /api/live/<sid>/frame {image: dataURL}
  POST /api/live/<sid>/audio {audio: dataURL, speaking}   (server-side ASR fallback)
  GET  /api/live/<sid>/stream                (text/event-stream)
  POST /api/live/<sid>/end
  GET  /api/ready                            device/model/asset readiness
"""

from __future__ import annotations

import glob
import json
import os
import queue
import re
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "ui"))
os.chdir(ROOT)

from harness.runner import run_scenario  # noqa: E402
from harness.scorer import score_scenario  # noqa: E402
from run_local import load_agent_factory  # noqa: E402
import live  # noqa: E402

# The browser server and LiveKit workers share the same key file.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, "livekit_agent", ".env.local"))
except ImportError:
    pass  # key-free text mode has no dotenv dependency
if "--offline" in sys.argv:
    os.environ["TRIAGELINE_LLM_PLANNER"] = "0"
    os.environ["TRIAGELINE_OFFLINE"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ.setdefault("PRELOAD", "0")

AGENTS = {"triageline": "agent.agent:ParticipantAgent", "baseline": "agent.agent:BaselineAgent"}
OFFICIAL_TAIL_MS = 6000.0          # identical to run_local.py / runner default
_RUN_SEM = threading.BoundedSemaphore(1)   # one scored run at a time: keeps virtual timing honest
_QUEUE = threading.BoundedSemaphore(4)     # bounded wait queue; beyond this → 429
MAX_BODY = 8 * 1024 * 1024
MAX_EVENTS = 60
MAX_SCENARIO_MS = 60_000


def list_scenarios():
    out = []
    for p in sorted(glob.glob("scenarios/*.json")) + sorted(glob.glob("scenarios_extra/*.json")):
        try:
            d = json.load(open(p))
        except Exception:
            continue
        md = d.get("metadata", {})
        refs = [(e.get("payload") or {}).get("audio_ref") or (e.get("payload") or {}).get("image_ref")
                for e in d.get("events", [])]
        missing = [r for r in refs if r and not os.path.exists(os.path.join(ROOT, r))]
        out.append({"path": p, "id": d.get("scenario_id"), "modality": md.get("modality"),
                    "difficulty": md.get("difficulty"), "description": md.get("description", ""),
                    "missing_media": missing,
                    "events": [{"t": e.get("timestamp_ms"), "type": e.get("event_type"),
                                "text": (e.get("payload") or {}).get("text")
                                or (e.get("payload") or {}).get("audio_ref")
                                or (e.get("payload") or {}).get("image_ref")} for e in d.get("events", [])]})
    return out


class BadRequest(Exception):
    pass


def _sse(ev) -> bytes:
    return f"id: {ev['id']}\ndata: {json.dumps(ev)}\n\n".encode()


def _qs(path):
    from urllib.parse import parse_qs, urlsplit
    return {k: v[-1] for k, v in parse_qs(urlsplit(path).query).items()}


def _num(v, name, default):
    try:
        f = float(default if v is None else v)
    except (TypeError, ValueError):
        raise BadRequest(f"{name} must be a number")
    if f != f or f in (float("inf"), float("-inf")):
        raise BadRequest(f"{name} must be finite")
    return f


def validate_scenario(sc):
    if not isinstance(sc, dict) or not isinstance(sc.get("events"), list):
        raise BadRequest("scenario must be an object with an events list")
    if len(sc["events"]) > MAX_EVENTS:
        raise BadRequest(f"at most {MAX_EVENTS} events")
    for e in sc["events"]:
        if not isinstance(e, dict) or e.get("event_type") not in ("user_speech_chunk", "interruption", "user_audio_chunk", "video_frame"):
            raise BadRequest("unsupported event type")
        if not (0 <= _num(e.get("timestamp_ms"), "timestamp_ms", 0) <= MAX_SCENARIO_MS):
            raise BadRequest("timestamp out of range")
        p = e.get("payload") or {}
        for k in ("audio_ref", "image_ref"):
            if p.get(k) and not re.fullmatch(r"(audio|frames)/[\w.\-]+", str(p[k])):
                raise BadRequest(f"{k} must reference a bundled asset")
        if len(str(p.get("text", ""))) > 500:
            raise BadRequest("text too long")


class H(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *a, **k):
        super().__init__(*a, directory=os.path.join(ROOT, "ui", "static"), **k)

    def log_message(self, *a):
        pass

    def end_headers(self):
        if self.path.endswith((".js", ".html", ".css", ".json", "/")) or self.path.startswith("/api"):
            self.send_header("Cache-Control", "no-store")
        if self.path.startswith("/api"):
            # mobile / native / cross-origin clients (React Native, Flutter, Capacitor webviews)
            self.send_header("Access-Control-Allow-Origin", os.environ.get("CORS_ORIGIN", "*"))
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            raise BadRequest("invalid Content-Length")
        if n < 0:
            raise BadRequest("invalid Content-Length")
        if n > MAX_BODY:
            raise BadRequest("request too large")
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise BadRequest("invalid JSON")
        if not isinstance(body, dict):
            raise BadRequest("JSON body must be an object")
        return body

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        if self.path.startswith("/api/scenarios"):
            return self._json(list_scenarios())
        if self.path.startswith("/api/ready"):
            return self._json(live.readiness())
        if self.path.startswith("/api/health"):
            return self._json({"ok": True, "service": "triageline", "sessions": len(live.SESSIONS.by_id)})
        m = re.fullmatch(r"/api/live/([\w\-]+)/stream", self.path.split("?")[0])
        if m:
            return self._stream(m.group(1))
        if self.path.split("?")[0] in ("/live", "/app"):
            self.path = "/live.html"
        return super().do_GET()

    def _stream(self, sid):
        s = live.SESSIONS.get(sid)
        if not s:
            return self._json({"error": "no such session", "code": "no_session"}, 404)
        try:
            last = int(self.headers.get("Last-Event-ID") or _qs(self.path).get("last", "0") or 0)
        except ValueError:
            last = 0
        gen = s.claim_stream()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(b"retry: 1500\n\n")
            sent = last
            for ev in s.events_after(last):          # replay what a reconnecting client missed
                self.wfile.write(_sse({**ev, "replay": True}))
                sent = ev["id"]
            self.wfile.flush()
            while s.stream_gen == gen:
                try:
                    ev = s.out.get(timeout=15)
                except queue.Empty:
                    if s.closed:
                        break
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                if ev["id"] <= sent:
                    continue
                self.wfile.write(_sse(ev))
                self.wfile.flush()
                sent = ev["id"]
                if ev.get("kind") == "closed":
                    break
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    # ------------------------------------------------------------------ POST
    def do_POST(self):
        try:
            if self.path.startswith("/api/run"):
                return self._run(self._body())
            if self.path.startswith("/api/live/"):
                return self._live(self._body())
            return self._json({"error": "not found"}, 404)
        except BadRequest as e:
            return self._json({"error": str(e), "code": "bad_request"}, 400)
        except OverflowError as e:
            return self._json({"error": str(e), "code": "busy"}, 429)
        except Exception as e:
            return self._json({"error": f"{type(e).__name__}: {e}", "code": "server_error"}, 500)

    def _live(self, req):
        parts = self.path.split("?")[0].strip("/").split("/")   # api/live/<sid>/<op>
        if parts[2:] == ["start"]:
            s = live.SESSIONS.start()
            return self._json({"sid": s.sid, "mode": live.MODE, "audio": live.P.speech_config()})
        if len(parts) != 4:
            return self._json({"error": "not found"}, 404)
        sid, op = parts[2], parts[3]
        if op == "end":
            live.SESSIONS.end(sid)
            return self._json({"ok": True})
        s = live.SESSIONS.get(sid)
        if not s:
            return self._json({"error": "session expired — start a new one", "code": "no_session"}, 404)
        try:
            if op == "say":
                return self._json({"ok": True, "as": s.user_text(str(req.get("text", "")), bool(req.get("speaking")))})
            if op == "frame":
                return self._json({"ok": True, "ref": s.user_frame(str(req.get("image", "")))})
            if op == "audio":
                return self._json({"ok": True, "ref": s.user_audio(str(req.get("audio", "")), bool(req.get("speaking")))})
            if op == "log":
                return self._json({"log": s.log, "diag": s.agent.diag[-50:]})
        except ValueError as e:
            raise BadRequest(str(e))
        return self._json({"error": "not found"}, 404)

    def _run(self, req):
        if req.get("scenario"):
            sc = req["scenario"]
            validate_scenario(sc)
        else:
            path = os.path.normpath(str(req.get("path", "")))
            if not (re.fullmatch(r"scenarios(_extra)?/[\w\-]+\.json", path) and os.path.exists(path)):
                raise BadRequest("bad path")
            sc = json.load(open(path))
        agent = req.get("agent") if req.get("agent") in AGENTS else "triageline"
        factory = load_agent_factory(AGENTS[agent])
        ts = min(max(_num(req.get("time_scale"), "time_scale", 1), 1.0), 8.0)
        tail = OFFICIAL_TAIL_MS
        if not _QUEUE.acquire(blocking=False):
            raise OverflowError("run queue full — try again in a moment")
        try:
            with _RUN_SEM:
                trace = run_scenario(sc, factory, time_scale=ts, verbose=False, tail_ms=tail)
        finally:
            _QUEUE.release()
        score = score_scenario(sc, trace) if sc.get("ground_truth") else None
        abandoned = [e["call_id"] for e in trace if e.get("kind") == "tool_abandoned"]
        crashed = [e.get("error") for e in trace if e.get("kind") == "agent_crash"]
        refs = [(e.get("payload") or {}).get("audio_ref") or (e.get("payload") or {}).get("image_ref")
                for e in sc.get("events", [])]
        return self._json({"trace": trace, "score": score, "scenario_id": sc.get("scenario_id"),
                           "config": {"agent": agent, "time_scale": ts, "tail_ms": tail,
                                      "official": ts == 1.0 and tail == OFFICIAL_TAIL_MS},
                           "status": {"runner_stopped_with_pending_calls": abandoned, "agent_crash": crashed,
                                      "missing_media": [r for r in refs if r and not os.path.exists(os.path.join(ROOT, r))]}})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    if os.environ.get("PRELOAD", "0") == "1":
        threading.Thread(target=lambda: (live.P.load_asr(), live.P.load_clip()), daemon=True).start()
    live.SESSIONS.start_reaper()
    print(f"TriageLine on http://0.0.0.0:{port}   (console /  ·  live assistant /live.html)")
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
