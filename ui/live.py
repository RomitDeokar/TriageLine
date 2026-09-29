"""Live Assistant sessions: one persistent agent per phone conversation.

Transport: HTTP POST for user events + Server-Sent Events for agent output (works through
any proxy, no extra dependencies). Each session owns its own asyncio loop thread, a
ParticipantAgent in live mode, and a tool adapter.

Execution mode is explicit and shown to the user:
    LIVE INPUT + MOCK TOOLS   (default: bookings/tickets are simulated, nothing real happens)
No real-effect provider is wired in; the adapter boundary is `ToolAdapter.execute`.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import os
import queue
import re
import secrets
import shutil
import threading
import time
from typing import Any, Dict, Optional

from agent.agent import ParticipantAgent
from agent import perception as P
from harness.mock_env import MockEnvironment
from livekit_agent.fdb_tools import FDB_TOOLS
from livekit_agent.fdb_v3_offline_replay import load_registry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOADS = os.environ.get("TRIAGELINE_UPLOAD_DIR") or os.path.join(ROOT, "live_uploads")
MAX_SESSIONS = int(os.environ.get("TRIAGELINE_MAX_SESSIONS", "8"))
SESSION_IDLE_S = int(os.environ.get("TRIAGELINE_SESSION_IDLE_S", str(15 * 60)))
INIT_TIMEOUT_S = float(os.environ.get("TRIAGELINE_SESSION_INIT_S", "20"))
MAX_UPLOAD = 6 * 1024 * 1024
MAX_TEXT = 500
MODE = "LIVE INPUT + SIMULATED TOOLS"


class ToolAdapter:
    """Mock provider (clearly labelled). Swap for a real provider behind the same method."""
    mode = "mock"

    def __init__(self, sid: str):
        self.env = MockEnvironment(scenario_id=f"live-{sid}", time_scale=1.0)
        self.fdb = load_registry()
        # Keep the practice flight backend as a unit (search/book/cancel share
        # its booking store), while exposing all the other FDB domains.
        self.fdb_tools = {k: v for k, v in FDB_TOOLS.items() if k not in ("search_flights", "book_flight")}
        self.registry = {**self.env.registry, **self.fdb_tools}

    async def execute(self, api: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if api in self.fdb_tools:
            try:
                return await asyncio.to_thread(self.fdb.call, api, **args)
            except TypeError as exc:
                return {"status": "error", "error": "invalid_args", "message": str(exc)}
        return await self.env.execute(api, args)


class LiveSession:
    def __init__(self, sid: str):
        self.sid = sid
        self.upload_lock = threading.RLock()
        self.upload_bytes = 0
        self.input_count = 0
        self.init_error = None
        self.created = self.touched = time.time()
        self.out: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=500)
        self.log: list = []
        self.seq = 0                      # monotonically increasing SSE event id (replay on reconnect)
        self.stream_gen = 0               # newest SSE consumer wins; stale streams exit
        self.lock = threading.Lock()
        self.cancelled: set = set()          # call ids cancelled by the agent: late results are dropped
        self._waiters: list = []             # (loop, asyncio.Event) of SSE consumers to wake on emit
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.closed = False
        self.frame_n = 0
        self.dir = os.path.join(UPLOADS, sid)
        os.makedirs(self.dir, exist_ok=True)
        self.thread = threading.Thread(target=self._main, daemon=True)
        self.thread.start()
        if not self.ready.wait(INIT_TIMEOUT_S) or self.init_error:
            self.close()
            raise RuntimeError("session initialization failed")

    # ------------------------------------------------------------ loop thread
    def _main(self):
        asyncio.set_event_loop(self.loop)
        self.in_q: asyncio.Queue = asyncio.Queue()
        self.out_q: asyncio.Queue = asyncio.Queue()
        self.t0 = time.monotonic()
        try:
            self.agent = ParticipantAgent(self.in_q, self.out_q, live=True)
            self.agent.on_transcript = lambda text, err: self.emit(
                "asr", text=text, error=("Speech not recognized" if err and not text else None))
            self.tools = ToolAdapter(self.sid)
        except Exception as exc:
            self.init_error = type(exc).__name__
            self.ready.set()
            self.loop.close()
            return
        self.pending: Dict[str, asyncio.Task] = {}
        self.t0 = time.monotonic()
        self.loop.create_task(self.agent.run())
        self.loop.create_task(self._pump())
        self.loop.call_soon(self.in_q.put_nowait, {"event_type": "tool_manifest",
                                                   "payload": {"tools": dict(self.tools.registry)}})
        self.ready.set()
        try:
            self.loop.run_forever()
        finally:
            # let cancelled tasks unwind, then release the loop (no "Task was destroyed" leaks)
            pending = [t for t in asyncio.all_tasks(self.loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.close()

    def ms(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)

    def emit(self, kind: str, **data):
        with self.lock:
            self.seq += 1
            ev = {"id": self.seq, "kind": kind, "t_ms": self.ms(), **data}
            self.log.append(ev)
            del self.log[:-400]
            waiters = list(self._waiters)
        for loop, event in waiters:          # wake SSE consumers immediately (no busy polling)
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                pass
        try:
            self.out.put_nowait(ev)
        except queue.Full:                # slow/absent consumer: drop oldest, keep newest
            try:
                self.out.get_nowait()
                self.out.put_nowait(ev)
            except (queue.Empty, queue.Full):
                pass

    def events_after(self, last_id: int) -> list:
        """Events a reconnecting client missed (bounded by the 400-event log)."""
        with self.lock:
            return [e for e in self.log if e["id"] > last_id]

    def subscribe(self, loop, event) -> None:
        with self.lock:
            self._waiters.append((loop, event))

    def unsubscribe(self, loop, event) -> None:
        with self.lock:
            try:
                self._waiters.remove((loop, event))
            except ValueError:
                pass

    def claim_stream(self) -> int:
        """Register a new SSE consumer; any older consumer stops reading the queue."""
        with self.lock:
            self.stream_gen += 1
            # drain the live queue: the new consumer replays from the log instead
            while True:
                try:
                    self.out.get_nowait()
                except queue.Empty:
                    break
            return self.stream_gen

    async def _pump(self):
        while True:
            a = await self.out_q.get()
            kind = a.get("action")
            p = a.get("payload") or {}
            if kind == "tool_call":
                cid, api, args = p.get("call_id"), p.get("api_name"), p.get("args") or {}
                self.emit("task", call_id=cid, api=api, status="running", args=_short(args))
                self.pending[cid] = self.loop.create_task(self._exec(cid, api, args))
                self.pending[cid].tool_api = api
            elif kind == "cancel_tool":
                cid = p.get("call_id")
                t = self.pending.pop(cid, None)
                if t and not t.done():
                    api = getattr(t, "tool_api", "")
                    kind = self.agent.tools.get(api, {}).get("kind", "read_only") if hasattr(self.agent, "tools") else "read_only"
                    if api in self.tools.fdb_tools and kind == "state_modifying":
                        # A running synchronous state change cannot be recalled by cancelling its awaiter:
                        # keep its result so the ledger can reconcile the real outcome (never guess).
                        self.emit("task", call_id=cid, api=api, status="cancel_requested")
                        self.pending[cid] = t
                    else:
                        # Read-only work (or a cancellable async mock): stop it, and drop any late result.
                        self.cancelled.add(cid)
                        t.cancel()
                        await self.in_q.put({"event_type": "tool_cancelled", "payload": {"call_id": cid, "confirmed": True}})
                        self.emit("task", call_id=cid, api=api, status="cancelled")
                else:
                    self.emit("task", call_id=cid, status="cancel_noop")
            else:
                self.emit("say", action=kind, text=p.get("text", ""), state=a.get("state_snapshot"))

    async def _exec(self, cid, api, args):
        try:
            res = await self.tools.execute(api, args)
        except asyncio.CancelledError:
            self.pending.pop(cid, None)
            getattr(self, "cancelled", set()).discard(cid)   # cancel already reached the agent
            return
        except Exception as e:  # provider exception → structured, ambiguous terminal event (R20)
            log_detail = type(e).__name__          # never echo provider internals / secrets to the user
            res = {"status": "error", "error": "provider_exception", "detail": log_detail}
        finally:
            self.pending.pop(cid, None)
        cancelled = getattr(self, "cancelled", set())
        if cid in cancelled:
            # the agent already received tool_cancelled for this call: a late result must never surface
            cancelled.discard(cid)
            return
        if not isinstance(res, dict):
            res = {"status": "error", "error": "malformed_result"}
        st = res.get("status", "success")
        self.emit("task", call_id=cid, api=api, status="done" if st == "success" else "error",
                  result=_short(res), mode=self.tools.mode)
        await self.in_q.put({"event_type": "tool_result",
                             "payload": {"call_id": cid, "api_name": api, "status": st, "result": res}})

    # ------------------------------------------------------------ API (HTTP threads)
    def push(self, ev: Dict[str, Any]):
        if self.closed:
            raise ValueError("session is closed")
        if self.input_count >= 500:
            raise ValueError("session input limit reached; start a new session")
        self.input_count += 1
        self.touched = time.time()
        self.loop.call_soon_threadsafe(self.in_q.put_nowait, ev)

    def busy(self) -> bool:
        return (bool(self.agent.inflight) or bool(self.agent.waiting_vision)
                or bool(self.agent.planner_pending) or bool(self.pending))

    def user_text(self, text: str, speaking: bool) -> str:
        text = re.sub(r"\s+", " ", text or "").strip()[:MAX_TEXT]
        if not text:
            raise ValueError("empty text")
        # speaking over the assistant, or over running work, is a barge-in
        et = "interruption" if (speaking or self.busy()) else "user_speech_chunk"
        self.emit("user", text=text, event_type=et)
        payload = {"text": text} if et == "interruption" else {"text": text, "end_of_turn": True}
        self.push({"event_type": et, "payload": payload})
        return et

    def interrupt(self):
        self.push({"event_type": "speech_started", "payload": {}})
        self.emit("interrupted")

    def _save(self, b64: str, ext: str) -> str:
        with self.upload_lock:
            if self.closed:
                raise ValueError("session is closed")
            return self._save_locked(b64, ext)

    def _save_locked(self, b64: str, ext: str) -> str:
        """Decode, validate and store one upload. Content is checked, not the client's claim (audit §7):
        images must decode with Pillow and are re-encoded as JPEG; audio must carry a WebM/Ogg/WAV/MP4
        container signature."""
        if len(b64) > MAX_UPLOAD * 4 // 3 + 128:
            raise ValueError("upload too large")
        try:
            raw = base64.b64decode(b64.split(",", 1)[-1], validate=True)
        except (ValueError, binascii.Error) as e:
            raise ValueError("upload is not valid base64") from e
        if len(raw) > MAX_UPLOAD:
            raise ValueError("upload too large")
        if not raw:
            raise ValueError("empty upload")
        if ext == "jpg":
            raw = _validated_jpeg(raw)
        elif not _looks_like_audio(raw):
            raise ValueError("unsupported audio format (expected webm/ogg/wav/mp4)")
        if self.frame_n >= 100 or self.upload_bytes + len(raw) > 30 * 1024 * 1024:
            raise ValueError("session upload limit reached; start a new session")
        self.upload_bytes += len(raw)
        if ext == "webm":
            ext = "ogg" if raw.startswith(b"OggS") else "wav" if raw.startswith(b"RIFF") else "mp4" if raw[4:8] == b"ftyp" else "webm"
        self.frame_n += 1
        name = f"{ext}_{self.frame_n:04d}.{ext}"
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(raw)
        return os.path.relpath(path, ROOT)

    def user_frame(self, b64: str) -> str:
        ref = self._save(b64, "jpg")
        self.emit("user", text="[camera frame]", event_type="video_frame")
        self.push({"event_type": "video_frame", "payload": {"frame_id": f"live_{self.frame_n}", "image_ref": ref}})
        return ref

    def user_audio(self, b64: str, speaking: bool) -> str:
        """Server-side ASR path (for browsers without on-device speech recognition)."""
        ref = self._save(b64, "webm")
        self.touched = time.time()

        # One perception contract for live and harness (R18): the clip enters the agent as a
        # user_audio_chunk, so word confidences, alternative decodes, utterance ordering (version) and
        # the clarification gate before side effects all apply exactly as in the evaluated path.
        self.emit("user", text="[voice clip]", event_type="user_audio_chunk")
        self.push({"event_type": "user_audio_chunk", "payload": {"audio_ref": ref, "end_of_turn": True, "interrupted": speaking or self.busy()}})
        return ref

    def close(self):
        if self.closed:
            return
        self.emit("closed")
        self.closed = True
        # uploads (camera frames / voice clips) never outlive the session (audit §7)
        with self.upload_lock:
            shutil.rmtree(self.dir, ignore_errors=True)

        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
        except RuntimeError:              # loop already closed
            pass


_AUDIO_MAGIC = (b"\x1a\x45\xdf\xa3",  # WebM / Matroska (MediaRecorder default)
                b"OggS", b"RIFF")         # Ogg/Opus (Firefox), WAV


def _looks_like_audio(raw: bytes) -> bool:
    return raw.startswith(_AUDIO_MAGIC[:2]) or (raw.startswith(b"RIFF") and raw[8:12] == b"WAVE") or raw[4:8] == b"ftyp"   # MP4/M4A (Safari)


def _validated_jpeg(raw: bytes) -> bytes:
    """Decode with Pillow (rejects non-images and decompression bombs), re-encode as JPEG."""
    try:
        from PIL import Image
    except ImportError as e:  # pillow is in requirements.txt; refuse rather than store unchecked bytes
        raise ValueError("image validation unavailable (pip install pillow)") from e
    Image.MAX_IMAGE_PIXELS = 40_000_000
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.width * im.height > 16_000_000:
                raise ValueError("image dimensions exceed limit")
            im.verify()
        with Image.open(io.BytesIO(raw)) as im:
            buf = io.BytesIO()
            im.thumbnail((1920, 1920))
            im.convert("RGB").save(buf, "JPEG", quality=88)
            return buf.getvalue()
    except Exception as e:  # noqa: BLE001 - any decoder error means "not an image we accept"
        raise ValueError("upload is not a valid image") from e


def _short(o: Any, n: int = 240) -> Any:
    if isinstance(o, dict):
        return {k: (f"[{len(v)}-d vector]" if isinstance(v, list) and len(v) > 8 and isinstance(v[0], (int, float))
                    else _short(v, n)) for k, v in o.items()}
    if isinstance(o, str) and len(o) > n:
        return o[:n] + "…"
    return o


class Sessions:
    def __init__(self):
        self.by_id: Dict[str, LiveSession] = {}
        self.lock = threading.Lock()

    def reap(self):
        now = time.time()
        for sid, s in list(self.by_id.items()):
            if s.closed or now - s.touched > SESSION_IDLE_S:
                s.close()
                self.by_id.pop(sid, None)

    def start(self) -> LiveSession:
        with self.lock:
            self.reap()
            if len(self.by_id) >= MAX_SESSIONS:
                raise OverflowError("too many live sessions — try again shortly")
            sid = secrets.token_urlsafe(12)
            s = LiveSession(sid)
            self.by_id[sid] = s
            return s

    def start_reaper(self, every_s: float = 60.0):
        def run():
            while True:
                time.sleep(every_s)
                with self.lock:
                    self.reap()
        threading.Thread(target=run, daemon=True, name="session-reaper").start()

    def get(self, sid: str) -> Optional[LiveSession]:
        s = self.by_id.get(sid)
        return s if s and not s.closed else None

    def end(self, sid: str):
        with self.lock:
            s = self.by_id.pop(sid, None)
        if s:
            s.close()


SESSIONS = Sessions()


def readiness() -> Dict[str, Any]:
    import importlib.util as u
    import shutil
    need = ["audio/pub_05_turn1.mp3", "audio/pub_05_turn2.mp3", "audio/pub_06_turn1_part1.mp3",
            "audio/pub_06_turn1_part2.mp3", "frames/pub_07_f017.png"]
    return {
        "mode": MODE,
        "offline": os.environ.get("TRIAGELINE_OFFLINE") == "1",
        "planner": _planner_status(),
        "audio": P.speech_config(),
        "speech": _speech_summary(),
        "tools": sorted(set(MockEnvironment("readiness").registry) | (set(FDB_TOOLS) - {"search_flights"})),
        "packages": {m: bool(u.find_spec(m)) for m in ("faster_whisper", "onnxruntime", "huggingface_hub", "tokenizers", "PIL", "numpy")},
        "tesseract": bool(shutil.which("tesseract")),
        "asr_loaded": P._ASR not in (None, False),
        "clip_loaded": P._CLIP not in (None, False),
        "assets": {p: os.path.exists(os.path.join(ROOT, p)) for p in need},
        "sessions": len(SESSIONS.by_id),
    }


def _planner_status():
    """Secret-free LLM status: effective chain, per-provider health, and whether planning is on."""
    from agent import llm_planner
    st = llm_planner.status()
    if not st["enabled"]:
        return {"provider": "local rules", "configured": True, "chain": st["chain"], "offline": st["offline"],
                "error": st["error"]}
    try:
        cfg = llm_planner.config()
    except ValueError as exc:
        return {"provider": "invalid configuration", "configured": False, "error": str(exc)}
    return {"provider": cfg["provider"], "model": cfg["model"], "configured": st["configured"],
            "chain": st["chain"], "error": st["error"]}


def _speech_summary() -> Dict[str, Any]:
    """Truthful speech pipeline per client path (browser PWA vs LiveKit voice worker)."""
    cfg = P.speech_config()
    out: Dict[str, Any] = {"pwa": {"stt": f"{cfg['provider']}:{cfg['model']} (server) or browser SpeechRecognition",
                                   "tts": "browser speechSynthesis"}}
    try:
        from livekit_agent import speech_providers as sp
        c = sp.selected()
        out["livekit_worker"] = {"stt": f"{c['stt_provider']}:{c['stt_model']}",
                                 "tts": f"{c['tts_provider']}:{c['tts_model']}", "missing_keys": c["missing_keys"]}
    except Exception as exc:  # noqa: BLE001 - worker deps optional in the gateway image
        out["livekit_worker"] = {"error": type(exc).__name__}
    return out
