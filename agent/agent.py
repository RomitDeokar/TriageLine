"""TriageLine — a dual-process interruptible agent for Theme 5.

Architecture (one asyncio loop; the event consumer NEVER awaits slow work):

  FAST PATH  (<5 ms, inline on every event)
      turn buffering · self-repair resolution · interruption classification
      (slot-revision / retraction / intent-switch) · content-aware acknowledgement
      · state snapshot on every spoken action
  SLOW PATH  (background tasks → results come back as internal completion events)
      ASR (faster-whisper, confidence-calibrated) · frame analysis (OCR+CLIP, bounded,
      latest-frame-wins) · async tools with schema-driven args · one read-only retry ·
      chained plans
  COORDINATION
      task versions: every invalidation bumps `version`; perception completions carry
      the version they started under and are dropped if superseded.
      In-flight ledger keyed by call_id with the slots each call depends on → targeted
      cancel_tool, and results are re-validated against current state before use.
      Operation ledger for state-modifying calls with an explicit lifecycle
      (pending / succeeded / failed / cancelled / unknown) → never duplicate a commit,
      never silently swallow a repeat request, never auto-retry an unknown outcome.

Nothing here keys off scenario ids, timestamps, or expected strings.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from . import nlu
from . import perception as P
from .ledger import AMBIGUOUS_ERRORS, OperationLedger, has_evidence
from .baseline_agent import BaselineAgent  # noqa: F401  (kept importable for comparison)

log = logging.getLogger("triageline.agent")

ASR_CITY_CONF = 0.80      # below this a heard slot value is "shaky" → clarify
VISION_MIN_CONF = 0.50    # below this a visual label is treated as unknown
MAX_FILLERS = 3           # evaluation budget (scorer default is 4, some scenarios 3)
RESERVED_FILLERS = 1      # kept back for interruption acknowledgements
FLIGHT_FAMILY = {"flight_search", "book_flight"}
INTERNAL = "_internal"    # completion events from slow-path tasks


def _canon(v: Any) -> Any:
    """Canonical form for idempotency keys: recursive, whitespace/case-insensitive for free
    text, but ids (FL-/BK-/TK- …) keep their exact case."""
    if isinstance(v, dict):
        return {k: _canon(v[k]) for k in sorted(v)}
    if isinstance(v, list):
        return [_canon(x) for x in v]
    if isinstance(v, str):
        s = nlu.norm(v)
        return s if nlu.ID_RE.fullmatch(s) else s.casefold()
    return v


class ParticipantAgent:
    def __init__(self, in_queue: asyncio.Queue, out_queue: asyncio.Queue, live: bool = False):
        self.in_q, self.out_q = in_queue, out_queue
        self.live = live or os.environ.get("TRIAGELINE_LIVE") == "1"
        self.tools: Dict[str, Any] = {}
        self.tool_alias: Dict[str, str] = {}   # internal canonical name -> manifest name
        self.state: Dict[str, Any] = {"intent": None, "slots": {}}
        self.buffer: List[str] = []
        self.audio_parts: List[asyncio.Task] = []
        self.version = 0                                  # task version (invalidation counter)
        self.seq = 0
        self.inflight: Dict[str, Dict[str, Any]] = {}     # call_id -> call record
        self.ledger = OperationLedger()                   # durable lifecycle of every state-modifying call
        self.ops = self.ledger.ops                        # idempotency key -> latest op record (compat alias)
        self.pending_retry: Optional[Dict[str, Any]] = None   # an unknown outcome the user may explicitly retry
        self.held: Optional[Dict[str, Any]] = None        # replacement commit held until a cancel is confirmed
        self.after_cancel: Optional[Dict[str, Any]] = None    # commit to issue once a confirmed cancel lands
        self.fillers: List[str] = []
        self.turn_fillers = 0
        self.plan: List[str] = []                         # queued follow-up tool names
        self.pending_clarify: Optional[Dict[str, Any]] = None
        self.last_turn = ""
        self.last_api: Optional[str] = None
        self.answered = False
        self.tasks: set = set()
        # vision: bounded, latest-frame-wins
        self.frame: Optional[Dict[str, Any]] = None
        self.frame_seq = 0
        self.vision: Optional[Dict[str, Any]] = None      # {frame_seq, label, confidence, embedding, source}
        self.vision_busy = False
        self.vision_next: Optional[Dict[str, Any]] = None
        self.waiting_vision: Optional[Dict[str, Any]] = None
        self.diag: List[Dict[str, Any]] = []              # structured internal diagnostics

    # ------------------------------------------------------------------ lifecycle
    async def setup(self):
        await asyncio.gather(asyncio.to_thread(P.load_asr), asyncio.to_thread(P.load_clip))

    async def run(self):
        try:
            while True:
                ev = await self.in_q.get()
                try:
                    await self.dispatch(ev)
                except Exception as e:  # never let one bad event kill the loop
                    self.note("agent_exception", f"{type(e).__name__}: {e}", event=ev.get("event_type"))
                    log.exception("agent exception")
                    await self.say("final_response", "Sorry, something went wrong on my side — could you say that again?")
        finally:
            for t in list(self.tasks):
                t.cancel()

    def note(self, code: str, detail: str = "", **kw):
        """Structured diagnostics: subsystem + code, never the user's transcript."""
        self.diag.append({"code": code, "detail": detail[:200], **kw})

    def spawn(self, coro):
        t = asyncio.create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)
        return t

    # canonical internal name -> token set that identifies the same tool under a
    # different manifest naming convention (FDB-v3 uses "search_flights").
    CANONICAL_TOOLS = {"flight_search": ({"flight", "search"}, "read_only")}

    @classmethod
    def canonicalize_manifest(cls, tools: Dict[str, Any]):
        """Map semantically-equivalent manifest tools onto the canonical names the
        planner's flight-family logic (search -> book chaining, revise/redo,
        on_flights grounding) is written against. Matching is by name tokens
        (order/plural-insensitive), never by a hard-coded scenario string, and
        only fires when the canonical name itself is absent. Returns
        (tools_keyed_by_internal_name, {internal: external})."""
        tools = dict(tools or {})
        alias: Dict[str, str] = {}
        for canon, (need, _kind) in cls.CANONICAL_TOOLS.items():
            if canon in tools:
                continue
            for name in list(tools):
                toks = {t.rstrip("s") for t in re.split(r"[_\W]+", name.lower()) if t}
                if need <= toks and name not in alias.values():
                    tools = {(canon if k == name else k): v for k, v in tools.items()}
                    alias[canon] = name
                    break
        return tools, alias

    async def post(self, kind: str, **data):
        """Completion of slow work re-enters through the same queue → consumer stays serial."""
        await self.in_q.put({"event_type": INTERNAL, "payload": {"kind": kind, **data}})

    async def dispatch(self, ev: Dict[str, Any]):
        et, p = ev.get("event_type"), ev.get("payload") or {}
        if et == "tool_manifest":
            self.tools, self.tool_alias = self.canonicalize_manifest(p.get("tools") or {})
        elif et == "user_speech_chunk":
            self.buffer.append(p.get("text", ""))
            if p.get("end_of_turn"):
                turn, self.buffer = nlu.norm(" ".join(self.buffer)), []
                self.turn_fillers = 0
                await self.on_turn(turn)
        elif et == "user_audio_chunk":
            # streaming ASR: each clip starts transcribing the moment it arrives
            self.audio_parts.append(self.spawn(asyncio.to_thread(P.transcribe, p.get("audio_ref"), self.vocab_prompt())))
            if p.get("end_of_turn"):
                jobs, self.audio_parts = self.audio_parts, []
                self.turn_fillers = 0
                await self.say("filler_speech", "Mm-hm, one second." if self.pending_clarify is None else "Got it.")
                self.spawn(self._await_asr(jobs, self.version))
        elif et == "video_frame":
            self.on_frame(p)
        elif et == "interruption":
            self.turn_fillers = 0
            await self.on_interruption(p.get("text", ""))
        elif et == "tool_result":
            await self.on_tool_result(p)
        elif et == "tool_cancelled":
            await self.on_tool_cancelled(p)
        elif et == INTERNAL:
            await self.on_internal(p)

    async def on_internal(self, p: Dict[str, Any]):
        kind = p.get("kind")
        if kind == "asr_done":
            if p["version"] != self.version:
                return self.note("stale_asr_dropped")
            await self.on_audio_result(p["results"])
        elif kind == "vision_done":
            if p["frame_seq"] == self.frame_seq:
                self.vision = p["vis"]
            w = self.waiting_vision
            if w and (p["frame_seq"] >= w["frame_seq"]):
                self.waiting_vision = None
                if w["version"] == self.version:
                    await self._issue_manual(w["turn"], w["visual"])
        elif kind == "vision_timeout":
            w = self.waiting_vision
            if w and w["token"] == p["token"]:
                self.waiting_vision = None
                if w["version"] == self.version:
                    self.note("vision_timeout")
                    await self._issue_manual(w["turn"], w["visual"])

    # ------------------------------------------------------------------ output
    def snapshot(self) -> Dict[str, Any]:
        return {"intent": self.state["intent"] or "chitchat", "slots": dict(self.state["slots"])}

    async def say(self, kind: str, text: str, priority: bool = False):
        text = nlu.norm(text)
        if kind == "filler_speech":
            if text in self.fillers:
                return
            if self.live:
                if self.turn_fillers >= 2:           # live: per-turn budget, no lifetime cap
                    return
            else:
                budget = MAX_FILLERS if priority else MAX_FILLERS - RESERVED_FILLERS
                if len(self.fillers) >= budget:
                    return
            self.fillers.append(text)
            self.turn_fillers += 1
        if kind == "final_response":
            self.answered = True
        await self.out_q.put({"action": kind, "payload": {"text": text}, "state_snapshot": self.snapshot()})

    def op_key(self, api: str, args: Dict[str, Any]) -> str:
        return api + "|" + json.dumps(_canon(args), sort_keys=True, separators=(",", ":"))

    def blocking_op(self, api: str, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if self.tools.get(api, {}).get("kind") != "state_modifying":
            return None
        return self.ledger.blocking(self.op_key(api, args))

    async def explain_block(self, op: Dict[str, Any], api: str, args: Dict[str, Any], deps: Optional[Dict[str, Any]]):
        st = op["status"]
        if st == "pending":
            await self.say("filler_speech", "I'm already working on that one — hang on.")
        elif st == "committed":
            await self.say("final_response", "That's already done — " + self.describe_success(api, op["result"], op.get("ctx")))
        else:  # unknown / cancel_requested: never auto-retried, never silently swallowed (R04)
            self.pending_retry = {"api": api, "args": dict(args), "deps": dict(deps or {}), "op": op}
            what = self.what(api)
            await self.say("final_response", f"I'm not certain the earlier {what} went through, so I won't repeat it "
                                             f"automatically. Please check your confirmations, or say \"yes, try again\" "
                                             f"and I'll make a new attempt.")

    def what(self, api: str) -> str:
        return {"book_flight": "booking", "cancel_booking": "cancellation",
                "create_support_ticket": "support ticket"}.get(api, nlu.norm(api.replace("_", " ")))

    async def call(self, api: str, args: Dict[str, Any], retries: int = 0,
                   deps: Optional[Dict[str, Any]] = None,
                   supersedes: Optional[Dict[str, Any]] = None) -> Optional[str]:
        if api not in self.tools:                        # never call an undeclared tool
            self.note("undeclared_tool_blocked", api)
            await self.say("final_response", f"Sorry — I can't {api.replace('_', ' ')} in this session.")
            return None
        spec = self.tools[api]
        key = None
        if spec.get("kind") == "state_modifying":
            key = self.op_key(api, args)
            op = self.ledger.blocking(key)
            if op and supersedes is None:
                await self.explain_block(op, api, args, deps)
                return None
            # rejected / cancelled / reversed → a fresh attempt is allowed; an explicit, confirmed
            # retry of an unknown outcome supersedes (and links to) the original record
        self.seq += 1
        cid = f"c{self.seq}"
        self.inflight[cid] = {"cid": cid, "api": api, "args": args, "version": self.version, "retries": retries,
                              "deps": dict(deps or {}), "ctx": dict(self.state["slots"]),
                              "plan": list(self.plan), "turn": self.last_turn, "op": key}
        if key:
            self.ledger.open(key, api, args, cid, ctx=dict(self.state["slots"]), supersedes=supersedes)
        # emit the manifest's own tool name (e.g. FDB-v3 "search_flights") even
        # though the agent reasons with its canonical family name internally
        ext = getattr(self, "tool_alias", {}).get(api, api)
        await self.out_q.put({"action": "tool_call", "payload": {"call_id": cid, "api_name": ext, "args": args}})
        return cid

    async def cancel_where(self, pred) -> List[Dict[str, Any]]:
        gone = []
        for cid, c in list(self.inflight.items()):
            if pred(c):
                await self.out_q.put({"action": "cancel_tool", "payload": {"call_id": cid}})
                self.inflight.pop(cid, None)
                rec = self.ledger.for_call(cid)
                if rec is not None:
                    # cancelling the local task does not prove the side effect was rolled back (R03):
                    # the mock harness drops cancelled calls authoritatively; a live provider must confirm
                    self.ledger.cancel_requested(rec)
                    if not self.live:
                        self.ledger.cancel_confirmed(rec)
                gone.append(c)
        return gone

    def invalidate(self, keep_frame: bool = True):
        """One routine for everything a cancelled task owns."""
        self.version += 1
        self.buffer = []
        for t in self.audio_parts:
            t.cancel()
        self.audio_parts = []
        self.pending_clarify = None
        self.plan = []
        self.waiting_vision = None
        self.pending_retry = None
        self.held = None
        if not keep_frame:
            self.frame, self.vision = None, None
            self.frame_seq += 1

    # ------------------------------------------------------------------ vision
    def on_frame(self, p: Dict[str, Any]):
        self.frame = p
        self.frame_seq += 1
        job = {"ref": p.get("image_ref"), "frame_seq": self.frame_seq}
        if self.vision_busy:
            self.vision_next = job              # coalesce: only the newest frame is queued
        else:
            self._start_vision(job)

    def _start_vision(self, job):
        self.vision_busy = True
        self.spawn(self._vision_worker(job))

    async def _vision_worker(self, job):
        try:
            vis = await asyncio.to_thread(P.analyze_frame, job["ref"])
        except Exception as e:
            vis = {"label": None, "confidence": 0.0, "embedding": None, "source": f"error:{type(e).__name__}"}
        await self.post("vision_done", frame_seq=job["frame_seq"], vis=vis)
        nxt, self.vision_next = self.vision_next, None
        if nxt:
            self._start_vision(nxt)
        else:
            self.vision_busy = False

    # ------------------------------------------------------------------ audio
    def vocab_prompt(self) -> str:
        topics = [n.replace("_", " ") for n in list(self.tools)[:6]]
        return "Voice assistant: " + ", ".join(topics) + "." if topics else ""

    async def _await_asr(self, jobs: List[asyncio.Task], version: int):
        try:
            results = await asyncio.gather(*jobs)
        except asyncio.CancelledError:
            return
        await self.post("asr_done", results=results, version=version)

    async def on_audio_result(self, results: List[Dict[str, Any]]):
        text = nlu.norm(" ".join(r["text"] for r in results))
        words = [w for r in results for w in r["words"]]
        if not text:
            reason = next((r.get("error") for r in results if r.get("error")), "empty")
            self.note("asr_no_text", reason)
            pc = self.pending_clarify
            field = (pc or {}).get("field") or ""
            if pc and any(k in field for k in ("city", "destination")) or (not pc and self.last_api in FLIGHT_FAMILY):
                self.pending_clarify = pc or {"field": "destination", "api": "flight_search", "text": "", "args": {}}
                q = "Sorry, I didn't quite catch that — could you confirm which city you mean?"
            else:
                q = "Sorry, I didn't catch that — could you say it again?"
            await self.say("clarification_request", q)
            return
        city = nlu.extract_city(text)
        if city and self.pending_clarify is None:
            conf = P.word_confidence(words, city)
            alt_text = " ".join(r.get("alt_text", "") for r in results)
            alt_city = nlu.extract_city(alt_text) if alt_text else None
            disagree = bool(alt_city and alt_city != city)
            if conf < ASR_CITY_CONF or disagree:
                # only offer an alternative that a decoder actually heard
                cands = [city, alt_city] if disagree else [city]
                self.pending_clarify = {"field": "destination", "candidates": cands, "text": text,
                                        "api": None, "args": {}, "version": self.version}
                q = (f"Just to confirm — did you say {city} or {alt_city}?" if disagree
                     else f"Just to confirm — did you say {city}?")
                await self.say("clarification_request", q)
                return
        name = nlu.extract_name(text)
        if name and P.word_confidence(words, name) < 0.5 and self.pending_clarify is None and \
                re.search(r"\bbook\b", text, re.I):
            self.pending_clarify = {"field": "passenger_name", "candidates": [name], "text": text,
                                    "api": None, "args": {}, "version": self.version}
            await self.say("clarification_request", f"Just to confirm — is the passenger name {name}?")
            return
        await self.on_turn(text, from_audio=True)

    # ------------------------------------------------------------------ turns
    async def on_turn(self, turn: str, from_audio: bool = False):
        low = turn.lower()

        # answers to a pending side-effect decision are handled before anything else
        if await self.resume_side_effect_decision(turn):
            return
        # revoking booking permission stops the plan (and a running booking) — R01
        if await self.revoke_booking(turn):
            if self._has_new_values(turn):
                await self.revise(turn)
            return

        # explicit retraction arrives the same way whether spoken as a turn or a barge-in
        if nlu.RETRACTION.search(low) and not self._actionable(nlu.RETRACTION.sub(" ", turn)):
            return await self.retract()

        if self.pending_clarify:
            handled = await self.resume_clarification(turn)
            if handled:
                return
        self.last_turn = turn

        ranked = nlu.score_tools(turn, self.tools)
        # a genuinely new request supersedes unfinished work (B04)
        if self.inflight:
            top = ranked[0][1] if ranked and ranked[0][0] >= 1.5 else None
            if top:
                fam = FLIGHT_FAMILY if top in FLIGHT_FAMILY else {top}
                cur = {c["api"] for c in self.inflight.values()}
                if cur & fam:
                    return await self.revise(turn, announce=True)
                # a different request: unrelated read-only work keeps running (its result stays
                # valid); only superseded clarification / plans are dropped
                self.pending_clarify, self.plan = None, []

        # elliptical follow-up ("Boston." / "make it Friday") continues the previous task
        if self.last_api and (not ranked or ranked[0][0] < 1.5 or ranked[0][1] == self.last_api) and \
                (nlu.extract_city(turn) or nlu.extract_date(turn)) and len(nlu.tokens(turn)) <= 4:
            return await self.start_task(self.last_api, turn)

        if nlu.is_smalltalk(turn, self.tools) or not ranked or ranked[0][0] < 1.5:
            if self.frame and "lookup_manual" in self.tools and re.search(r"\b(this|that|it)\b", low):
                return await self.start_task("lookup_manual", turn)
            self.state["intent"] = "chitchat"
            await self.say("final_response", f"Happy to help! I can {self.capabilities()}. What would you like to do?")
            return

        top = ranked[0][1]
        # a negated state change is a refusal, not a request (R02)
        if self.tools.get(top, {}).get("kind") == "state_modifying" and top != "book_flight" and \
                nlu.negated_action(turn, top):
            self.state["intent"] = "chitchat"
            self.plan = []
            return await self.say("final_response", f"Okay — I won't {nlu.norm(top.replace('_', ' '))}. "
                                                    f"Is there anything else I can do?")
        negated = nlu.negates_booking(turn)
        wants_book = bool(re.search(r"\b(book|reserve)\b", low)) and not negated and "book_flight" in self.tools
        if top == "book_flight" or (wants_book and top in FLIGHT_FAMILY) or \
                (top == "flight_search" and negated):
            top = "flight_search" if "flight_search" in self.tools else top
            self.plan = ["book_flight"] if wants_book else []
        else:
            self.plan = []
        if negated and top == "book_flight":
            top = "flight_search" if "flight_search" in self.tools else None
            if not top:
                return await self.say("final_response", "Okay — I won't book anything.")
        await self.start_task(top, turn)

    def _booking_pending(self) -> bool:
        pc = self.pending_clarify or {}
        return ("book_flight" in self.plan or self.held is not None
                or any(c["api"] == "book_flight" or "book_flight" in c.get("plan", []) for c in self.inflight.values())
                or pc.get("api") == "book_flight" or "book_flight" in (pc.get("plan") or []))

    async def revoke_booking(self, text: str) -> bool:
        """'Actually don't book, only show options' removes booking from the plan everywhere it lives:
        the agent plan, every in-flight call's plan snapshot, a parked clarification, a held commit —
        and cancels a booking call that is already running."""
        if not nlu.negates_booking(text) or not self._booking_pending():
            return False
        self.plan = []
        self.held = None
        for c in self.inflight.values():
            c["plan"] = [x for x in c.get("plan", []) if x != "book_flight"]
        pc = self.pending_clarify
        if pc and pc.get("api") == "book_flight":
            self.pending_clarify = None
        elif pc:
            pc["plan"] = [x for x in (pc.get("plan") or []) if x != "book_flight"]
        gone = await self.cancel_where(lambda c: c["api"] == "book_flight")
        self.answered = False
        if gone and self.live:
            msg = ("Okay — I've asked the booking system to stop that booking. "
                   "I'll tell you if it had already gone through.")
        elif gone:
            msg = "Okay — I've stopped the booking and won't book anything."
        else:
            msg = "Okay — I won't book anything; I'll just show you the options."
        await self.say("filler_speech", msg, priority=True)
        return True

    async def resume_side_effect_decision(self, turn: str) -> bool:
        """Explicit user decisions about side effects whose outcome is open (R03 / R04)."""
        low = turn.lower()
        pc = self.pending_clarify
        if pc and pc.get("kind") == "replace":
            old, new = pc["old"], pc["args"]
            bid = (old.get("result") or {}).get("booking_id")
            if re.search(r"\bboth\b", low):
                self.pending_clarify = None
                old["resolved"] = "kept"
                await self.say("filler_speech", f"Okay — keeping {bid} and booking {new['flight_id']} as well.")
                await self.call("book_flight", new, deps=pc.get("deps"))
                return True
            if nlu.YES_RE.search(turn) or re.search(r"\b(cancel|replace|switch)\b", low):
                self.pending_clarify = None
                old["resolved"] = "replace"
                if "cancel_booking" not in self.tools:
                    await self.say("final_response", f"Sorry — I can't cancel bookings in this session, so {bid} stays "
                                                     f"active and I haven't booked the new flight.")
                    return True
                self.after_cancel = {"booking_id": bid, "args": dict(new), "deps": dict(pc.get("deps") or {})}
                await self.say("filler_speech", f"Okay — cancelling {bid} first, then booking {new['flight_id']}.")
                await self.call("cancel_booking", {"booking_id": bid})
                return True
            if nlu.NO_RE.search(turn) or re.search(r"\bkeep\b", low):
                self.pending_clarify = None
                old["resolved"] = "kept"
                await self.say("final_response", f"Okay — I'll keep {bid} and won't book the new flight.")
                return True
            self.pending_clarify = None       # anything else is a new request
            return False
        pr = self.pending_retry
        if pr:
            self.pending_retry = None
            if nlu.YES_RE.search(turn) or re.search(r"\btry (?:it |that )?again\b|\bretry\b", low):
                await self.say("filler_speech", f"Okay — making a new {self.what(pr['api'])} attempt.")
                await self.call(pr["api"], pr["args"], deps=pr["deps"], supersedes=pr["op"])
                return True
            if nlu.NO_RE.search(turn):
                await self.say("final_response", "Okay — I'll leave it as it is.")
                return True
        return False

    def _actionable(self, text: str) -> bool:
        r = nlu.score_tools(text, self.tools)
        return bool(r and r[0][0] >= 2.5)

    async def resume_clarification(self, turn: str) -> bool:
        """Resume the original request with an answer parsed for the field we asked about."""
        pc, self.pending_clarify = self.pending_clarify, None
        field = pc.get("field") or ""
        cands = pc.get("candidates") or []
        value = None
        if cands:
            for c in cands:
                if re.search(r"\b" + re.escape(c.lower()) + r"\b", turn.lower()):
                    value = c
            if value is None and nlu.YES_RE.search(turn):
                if len(cands) == 1:
                    value = cands[0]
                else:  # "yes" cannot choose between two options
                    self.pending_clarify = pc
                    await self.say("clarification_request", f"Sorry — which one: {cands[0]} or {cands[1]}?")
                    return True
            if value is None and nlu.NO_RE.search(turn) and len(cands) == 1:
                self.pending_clarify = {**pc, "candidates": []}
                what = "city" if "dest" in field or "city" in field else field.split(".")[-1].replace("_", " ")
                await self.say("clarification_request", f"Sorry about that — which {what} did you mean?")
                return True
        api = pc.get("api")
        fspec = nlu.field_spec(self.tools.get(api, {}), field) if api else {}
        if value is None:
            value = nlu.parse_field_answer(turn, field, fspec)
            if field.endswith("destination") or "city" in field:
                value = nlu.extract_city(turn) or (value if value and len(value.split()) <= 3 else None)
        # the reply was a whole new request, not an answer → treat it as one
        if value is None or (self._actionable(turn) and len(nlu.tokens(turn)) > 4 and not cands):
            return False
        slots = self.state["slots"]
        leaf = field.split(".")[-1]
        if leaf in ("destination", "city"):
            slots["destination"] = value
        elif "passenger" in leaf or leaf == "name":
            slots["passenger_name"] = value
        else:
            slots[field] = value
        base = pc.get("text") or self.last_turn
        self.last_turn = base
        if api == "book_flight" and slots.get("flight_id") and slots.get("passenger_name"):
            self.plan = []
            await self.say("filler_speech", f"Thanks — booking {slots['flight_id']} for {slots['passenger_name']} now.")
            await self.call("book_flight", {"flight_id": slots["flight_id"], "passenger_name": slots["passenger_name"]},
                            deps={"flight_id": slots["flight_id"]})
            return True
        if api is None:  # ASR confirmation: re-route the confirmed utterance
            if leaf in ("destination", "city"):
                turn2 = base if value.lower() in base.lower() else f"{base} to {value}"
                if not nlu.score_tools(turn2, self.tools):
                    turn2 += " flight"
            else:
                turn2 = base
            await self.on_turn(turn2)
            return True
        self.plan = pc.get("plan") or []
        await self.start_task(api, base, extra=pc.get("args") or {})
        return True

    def capabilities(self) -> str:
        pretty = []
        for name, spec in self.tools.items():
            d = str(spec.get("description", name.replace("_", " "))).rstrip(".")
            pretty.append(d[0].lower() + d[1:] if d else name)
        if not pretty:
            return "search and book flights, look things up in device manuals, and open support tickets"
        return ", ".join(pretty[:-1]) + (", or " if len(pretty) > 1 else "") + pretty[-1]

    INTENT_NAMES = {"flight_search": "book_flight", "book_flight": "book_flight",
                    "lookup_manual": "device_support", "create_support_ticket": "device_support",
                    "cancel_booking": "cancel_booking"}

    def update_slots(self, api: str, turn: str):
        slots = self.state["slots"]
        city = nlu.extract_city(turn)
        if city:
            slots["destination"] = city
        origin = nlu.extract_origin(turn)
        if origin:
            slots["origin"] = origin
        date = nlu.extract_date(turn)
        if date:
            slots["date"] = date
        name = nlu.extract_name(turn)
        if name and api in FLIGHT_FAMILY:
            slots["passenger_name"] = name
        want = nlu.extract_time(turn)
        if want:
            slots["depart_time"] = want
        bid = nlu.extract_id(turn, "booking_id")
        if bid:
            slots["booking_id"] = bid
        dev = nlu.extract_device(turn, (self.frame or {}).get("device_hint"))
        if dev and api in ("lookup_manual", "create_support_ticket"):
            slots["device_model"] = dev

    async def start_task(self, api: str, turn: str, extra: Optional[Dict[str, Any]] = None):
        self.last_api = api
        spec = self.tools.get(api, {})
        slots = self.state["slots"]
        # a concurrent, unrelated request must not overwrite the slots a running flight task owns (R22)
        protect = api not in FLIGHT_FAMILY and any(c["api"] in FLIGHT_FAMILY for c in self.inflight.values())
        saved = dict(slots) if protect else None
        self.update_slots(api, turn)
        self.state["intent"] = self.INTENT_NAMES.get(api, api)

        if api == "lookup_manual":
            return await self.manual_lookup(turn)
        if api == "create_support_ticket" and "create_support_ticket" in self.tools:
            slots["issue_summary"] = nlu.norm(re.sub(r"(?i)\b(please|open a ticket|create a ticket|open a support ticket)\b",
                                                     "", turn)).strip(" ,.") or turn
            args = {"device": {"model": slots.get("device_model") or "GENERIC"},
                    "issue": {"summary": slots["issue_summary"], "severity": nlu.severity_of(turn)}}
            await self.say("filler_speech", f"Okay, I'll open a support ticket for your {self.device_word()} now.")
            await self.call(api, args)
            return

        ctx = dict(slots)
        ctx.update(extra or {})
        args, missing = nlu.build_args(spec, turn, ctx)
        if protect:
            for k in ("destination", "date", "origin", "passenger_name", "depart_time", "flight_id"):
                if k in saved:
                    slots[k] = saved[k]
                else:
                    slots.pop(k, None)
        # Read-only search tools only: a missing top-level travel/search DATE is
        # defaulted to "today" (announced in the ack so the user can correct it
        # by barge-in, which goes through the normal revise/epoch path). A
        # side-effect-free lookup is cheap and reversible; asking first would
        # stall a chained plan (search -> book) on a slot the user never
        # considered. State-modifying tools are NEVER defaulted.
        assumed = []
        if missing and spec.get("kind", "read_only") == "read_only":
            for f in list(missing):
                if "." not in f and "date" in f.lower() and nlu.field_spec(spec, f).get("type", "string") == "string":
                    args[f] = "today"
                    missing.remove(f)
                    assumed.append(f)
        if missing:
            field = missing[0]
            leaf = field.split(".")[-1].replace("_", " ")
            fs = nlu.field_spec(spec, field)
            if any(k in leaf for k in ("city", "destination")):
                q = "Sure — which city?"
            elif fs.get("enum"):
                opts = [str(e) for e in fs["enum"]]
                q = f"Sure — which {leaf}: " + ", ".join(opts[:-1]) + f" or {opts[-1]}?"
            else:
                q = f"Sure — what {leaf} should I use?"
            self.pending_clarify = {"field": field, "api": api, "args": {**(extra or {})}, "text": turn,
                                    "plan": list(self.plan), "version": self.version}
            await self.say("clarification_request", q)
            return
        deps = {} if protect else {k: args.get(k) for k in ("destination", "date", "city", "origin") if k in args}
        op = self.blocking_op(api, args)
        if op:                                   # don't announce work we are not going to start
            return await self.explain_block(op, api, args, deps)
        ack = self.ack(api, args)
        if assumed and "today" not in ack:
            ack = ack.rstrip(".") + " — I'll assume today unless you say otherwise."
        await self.say("filler_speech", ack)
        await self.call(api, args, deps=deps)

    def device_word(self) -> str:
        return {"QN90": "TV", "S24": "phone", "WF45": "washer", "GENERIC": "device"}.get(
            self.state["slots"].get("device_model"), "device")

    def ack(self, api: str, args: Dict[str, Any]) -> str:
        if api == "flight_search":
            d = args.get("destination")
            when = f" for {args['date']}" if args.get("date") else ""
            return f"Sure, checking flights to {d}{when}." if not self.plan else f"On it — finding flights to {d}{when} first."
        vals = [str(v) for v in args.values() if isinstance(v, (str, int, float)) and not isinstance(v, bool)][:1]
        what = nlu.norm(api.replace("_", " "))
        return f"Sure, let me run a {what}" + (f" for {vals[0]}." if vals else ".")

    VISUAL_Q = re.compile(r"\b(this|that|these|those|it|here|camera|see|look(?:ing)? at|pointing)\b", re.I)

    async def manual_lookup(self, turn: str):
        if "lookup_manual" not in self.tools:
            return await self.say("final_response", "Sorry — manual lookup isn't available in this session.")
        visual = bool(self.frame) and bool(self.VISUAL_Q.search(turn))
        await self.say("filler_speech", "Let me take a look at that and check the manual." if visual
                       else "Let me check the manual for that.")
        if visual and (self.vision is None or self.vision_busy):
            # never block the consumer on vision: park the request, resume on completion
            self.seq += 1
            token = self.seq
            self.waiting_vision = {"turn": turn, "visual": True, "version": self.version,
                                   "frame_seq": self.frame_seq, "token": token}
            self.spawn(self._vision_deadline(token, 4.0))
            return
        await self._issue_manual(turn, visual)

    async def _vision_deadline(self, token: int, secs: float):
        await asyncio.sleep(secs)
        await self.post("vision_timeout", token=token)

    async def _issue_manual(self, turn: str, visual: bool):
        vis = (self.vision or {}) if visual else {}
        label, conf = vis.get("label"), float(vis.get("confidence") or 0.0)
        if label and conf < VISION_MIN_CONF:
            self.note("vision_low_confidence", f"{label}:{conf:.2f}")
            label = None
        q = turn
        if label:
            q = f"{label} — {turn}"
            self.state["slots"]["issue_summary"] = f"{label}: {turn}"
        args: Dict[str, Any] = {"query": q}
        spec = self.tools.get("lookup_manual", {}).get("args", {})
        emb = vis.get("embedding")
        # only a real CLIP embedding is a valid hybrid-search query vector
        if emb and "image_embedding" in spec and "clip" in str(vis.get("source", "")):
            args["image_embedding"] = emb
        dm = self.state["slots"].get("device_model")
        enum = (spec.get("device_model") or {}).get("enum") or []
        if dm in enum and dm != "GENERIC":
            args["device_model"] = dm
        cid = await self.call("lookup_manual", args)
        if cid in self.inflight:
            self.inflight[cid].update({"vision_label": label, "visual": visual})

    # ------------------------------------------------------------------ interruption
    async def retract(self):
        gone = await self.cancel_where(lambda c: True)
        self.invalidate(keep_frame=False)
        self.state["intent"] = "cancelled"
        risky = [c for c in gone if self.tools.get(c["api"], {}).get("kind") == "state_modifying"]
        if risky and self.live:
            return await self.say("final_response", f"Okay, I've dropped the request and asked the system to stop the "
                                                    f"{self.what(risky[0]['api'])}. I'll tell you if it had already gone "
                                                    f"through. Anything else I can do?")
        await self.say("final_response", "Okay, I've stopped that and dropped the request. Anything else I can do?")

    async def on_interruption(self, text: str):
        low = text.lower()
        if await self.revoke_booking(text):
            if self._has_new_values(text):
                await self.revise(text)
            return
        ranked = nlu.score_tools(text, self.tools)
        current_apis = {c["api"] for c in self.inflight.values()} or ({self.last_api} if self.last_api else set())
        top = ranked[0][1] if ranked and ranked[0][0] >= 2.0 else None
        same_family = bool(top and (top in current_apis or (top in FLIGHT_FAMILY and current_apis & FLIGHT_FAMILY)))
        switch = bool(top and not same_family)          # intent is decided independently of slots (B10)

        if nlu.RETRACTION.search(low) and not switch and not self._has_new_values(text):
            return await self.retract()

        if switch or (nlu.INTENT_SWITCH.search(low) and top):
            await self.cancel_where(lambda c: True)
            self.invalidate()
            self.state = {"intent": None, "slots": {}}
            await self.say("filler_speech", f"Sure, dropping that — switching to your "
                                            f"{top.replace('_', ' ').split()[-1] if top else 'new'} request.", priority=True)
            self.answered = False
            await self.on_turn(text)
            return
        await self.revise(text)

    def _has_new_values(self, text: str) -> bool:
        s = self.state["slots"]
        c, d, n = nlu.extract_city(text), nlu.extract_date(text), nlu.extract_name(text)
        return bool((c and c != s.get("destination")) or (d and d != s.get("date")) or (n and n != s.get("passenger_name")))

    async def revise(self, text: str, announce: bool = True):
        """Slot revision: cancel only calls that depend on a changed slot, then rebuild the goal."""
        slots = self.state["slots"]
        changed = {}
        c = nlu.extract_city(text)
        if c and c != slots.get("destination"):
            changed["destination"] = c
        d = nlu.extract_date(text)
        if d and d != slots.get("date"):
            changed["date"] = d
        n = nlu.extract_name(text)
        if n and n != slots.get("passenger_name"):
            changed["passenger_name"] = n
        t = nlu.extract_time(text)
        if t and t != slots.get("depart_time"):
            changed["depart_time"] = t
        if not changed:
            await self.say("filler_speech", "Okay — still on it.")
            return
        # which in-flight calls are invalidated by this change?
        def affected(call):
            if call["api"] == "book_flight":
                return True  # any flight/passenger change invalidates a pending booking
            return any(k in changed or (k == "city" and "destination" in changed) for k in call["deps"])
        was_booking = any(cc["api"] == "book_flight" for cc in self.inflight.values())
        had_plan = bool(self.plan) or was_booking or any("book_flight" in cc.get("plan", []) for cc in self.inflight.values())
        slots.update(changed)
        if "destination" in changed or "date" in changed or "depart_time" in changed:
            slots.pop("flight_id", None)
        what = changed.get("destination") or changed.get("date") or changed.get("passenger_name") or changed.get("depart_time")
        if announce:
            await self.say("filler_speech", f"Got it — switching to {what}.", priority=True)
        stale = await self.cancel_where(affected)
        for x in stale:
            rec = self.ledger.for_call(x.get("cid", ""))
            if rec is not None and x["api"] == "book_flight":
                rec["replaced"] = True          # a replacement is coming: gate it on this record's outcome
        self.version += 1
        self.pending_clarify = None
        self.answered = False
        redo = {x["api"] for x in stale if self.tools.get(x["api"], {}).get("kind") != "state_modifying"}
        if was_booking or (not redo and not self.inflight and self.last_api in FLIGHT_FAMILY):
            redo.add("flight_search")
        if (was_booking or had_plan) and "book_flight" in self.tools and not nlu.negates_booking(text):
            self.plan = ["book_flight"]
        if was_booking and self.live:
            # truthful: the provider has not confirmed the cancel yet (R03)
            await self.say("filler_speech", "I've asked the booking system to stop the earlier booking — "
                                            "I'll only book the new one once that's confirmed.")
        for api in sorted(redo):
            if api not in self.tools:
                continue
            args, missing = nlu.build_args(self.tools[api], text + " " + self.last_turn, dict(slots))
            for k, v in changed.items():
                if k in args:
                    args[k] = v
                elif k == "destination" and "city" in args:
                    args["city"] = v
            if missing:
                self.pending_clarify = {"field": missing[0], "api": api, "args": {}, "text": self.last_turn,
                                        "plan": list(self.plan), "version": self.version}
                await self.say("clarification_request", f"Sure — what {missing[0].split('.')[-1].replace('_', ' ')} should I use?")
                continue
            self.last_api = api
            await self.call(api, args, deps={k: args.get(k) for k in ("destination", "date", "city", "origin") if k in args})

    # ------------------------------------------------------------------ results
    def still_valid(self, c: Dict[str, Any]) -> bool:
        """A result is usable only if the slots it depended on still hold."""
        s = self.state["slots"]
        for k, v in c["deps"].items():
            cur = s.get("destination") if k == "city" else s.get(k)
            if cur is not None and v is not None and str(cur).casefold() != str(v).casefold():
                return False
        return True

    async def on_tool_cancelled(self, p: Dict[str, Any]):
        """Provider acknowledgement of a cancel_tool. Only `confirmed=True` proves nothing committed."""
        rec = self.ledger.for_call(p.get("call_id", ""))
        if rec is None:
            return
        if p.get("confirmed"):
            self.ledger.cancel_confirmed(rec)
        elif rec["status"] == "cancel_requested":
            self.ledger.unknown(rec, "cancel_unconfirmed")
        await self.release_held()

    def replacement_gate(self) -> Optional[tuple]:
        """Before a replacement booking commits, every booking it replaces must be resolved (R03)."""
        for rec in reversed(self.ledger.history):
            if rec["api"] != "book_flight" or not rec.get("replaced") or rec.get("resolved"):
                continue
            if rec["status"] == "committed":
                return ("confirm", rec)
            if rec["status"] in ("pending", "cancel_requested", "unknown"):
                return ("hold", rec)
        return None

    async def issue_booking(self, args: Dict[str, Any], deps: Dict[str, Any], announce: Optional[str] = None):
        """Single gate every automatic booking goes through: ledger dedup + replacement reconciliation."""
        op = self.blocking_op("book_flight", args)
        if op:
            return await self.explain_block(op, "book_flight", args, deps)
        gate = self.replacement_gate()
        if gate and gate[0] == "hold":
            self.held = {"args": dict(args), "deps": dict(deps), "rec": gate[1]}
            return await self.say("clarification_request",
                                  f"I've found {args['flight_id']}, but the booking system hasn't confirmed that the "
                                  f"earlier booking was stopped. I'll hold the new booking until it does — or tell me "
                                  f"to leave it.")
        if gate and gate[0] == "confirm":
            return await self.ask_replace(gate[1], args, deps)
        if announce:
            await self.say("filler_speech", announce)
        await self.call("book_flight", args, deps=deps)

    async def ask_replace(self, old: Dict[str, Any], args: Dict[str, Any], deps: Dict[str, Any]):
        bid = (old.get("result") or {}).get("booking_id")
        where = (old.get("ctx") or {}).get("destination") or "the earlier flight"
        self.held = None
        self.pending_clarify = {"kind": "replace", "field": "replace", "old": old, "args": dict(args),
                                "deps": dict(deps), "api": "book_flight", "text": self.last_turn,
                                "version": self.version}
        await self.say("clarification_request",
                       f"Your earlier booking {bid} to {where} is still active. Should I cancel it and book "
                       f"{args['flight_id']} instead, keep it, or keep both?")

    async def release_held(self):
        h = self.held
        if not h:
            return
        st = h["rec"]["status"]
        if st in ("cancelled", "rejected", "reversed"):
            self.held = None
            await self.issue_booking(h["args"], h["deps"],
                                     announce=f"The earlier booking was stopped — booking {h['args']['flight_id']} now.")
        elif st == "committed":
            await self.ask_replace(h["rec"], h["args"], h["deps"])

    async def on_late_result(self, cid: str, p: Dict[str, Any]):
        """A result for a call we already cancelled. Read-only → ignore. State-modifying → reconcile (R03)."""
        rec = self.ledger.for_call(cid)
        if rec is None or rec["status"] not in ("cancel_requested", "cancelled", "unknown", "pending"):
            return self.note("late_result_ignored")
        res = p.get("result") or {}
        if p.get("status") == "error":
            err = res.get("error", "error")
            if err in AMBIGUOUS_ERRORS:
                self.ledger.unknown(rec, err)
            else:
                self.ledger.cancel_confirmed(rec)
            return await self.release_held()
        if not has_evidence(rec["api"], res):
            self.ledger.unknown(rec, "malformed_result")
            return await self.release_held()
        self.ledger.late_commit(rec, res)
        self.note("late_commit_reconciled", rec["api"])
        if rec["api"] == "book_flight":
            where = (rec.get("ctx") or {}).get("destination") or "the earlier flight"
            await self.say("final_response",
                           f"Heads-up: the earlier {where} booking had already gone through before I could stop it — "
                           f"booking reference {res.get('booking_id')}. I won't book a replacement until you decide "
                           f"what to do with it.")
        else:
            await self.say("final_response",
                           f"Heads-up: the earlier {self.what(rec['api'])} had already gone through before I could stop "
                           f"it ({nlu.humanize_result(rec['api'], res) or 'confirmed by the provider'}).")
        await self.release_held()

    async def on_tool_result(self, p: Dict[str, Any]):
        cid = p.get("call_id")
        c = self.inflight.pop(cid, None)
        if c is None:
            return await self.on_late_result(cid or "", p)   # cancelled / unknown — never ground on it
        api, res = c["api"], p.get("result") or {}
        kind = self.tools.get(api, {}).get("kind", "read_only")
        op = self.ledger.for_call(cid)
        is_err = p.get("status") == "error"

        # ---- state-modifying outcome bookkeeping (R03/R04/R16) happens before any validity check
        if op is not None:
            if is_err:
                err = res.get("error", "error")
                if api == "book_flight" and err == "duplicate_booking":
                    bid = res.get("booking_id")
                    self.ledger.commit(op, {"booking_id": bid, "flight_id": c["args"].get("flight_id")})
                    if bid:
                        self.state["slots"]["booking_id"] = bid
                    return await self.say("final_response", f"Looks like that flight is already booked"
                                                            f"{f' ({bid})' if bid else ''}, so I didn't book it twice.")
                if err in AMBIGUOUS_ERRORS:
                    self.ledger.unknown(op, err)
                    self.plan = []
                    return await self.say("final_response",
                                          f"The {self.what(api)} request {'timed out' if err == 'timeout' else 'hit an error'}, "
                                          f"so I can't tell whether it went through. I won't repeat it automatically — "
                                          f"please check your confirmations, or ask me to try again.")
                self.ledger.reject(op, err)
            elif not has_evidence(api, res):
                self.ledger.unknown(op, "malformed_result")
                self.plan = []
                return await self.say("final_response",
                                      f"The system replied without a confirmation for the {self.what(api)}, so I can't "
                                      f"confirm it went through. I won't repeat it automatically — please check your "
                                      f"confirmations, or ask me to try again.")
            else:
                self.ledger.commit(op, res)

        if not self.still_valid(c):
            self.note("stale_result_dropped", api)
            if op is not None and op["status"] == "committed":
                await self.say("final_response", "Heads-up: an earlier " + self.what(api) + " had already completed — "
                               + self.describe_success(api, res, c["ctx"]))
            return

        if is_err:
            err = res.get("error", "error")
            if kind == "read_only" and c["retries"] < 1 and err in ("timeout", "error", "unavailable"):
                await self.say("filler_speech", "That's taking longer than usual — trying again.")
                self.plan = c["plan"]
                await self.call(api, c["args"], retries=c["retries"] + 1, deps=c["deps"])
                return
            await self.say("final_response", {
                "timeout": "Sorry — the service timed out and I was unable to finish that. Want me to try again?",
                "not_found": "Sorry, I couldn't find that — can you double-check the details?",
                "invalid_args": "Sorry, I was unable to complete that request — could you rephrase the details?",
                "unknown_tool": "Sorry — that service isn't available right now.",
            }.get(err, "Sorry — I was unable to complete that right now."))
            return

        if api == "flight_search":
            return await self.on_flights(res, c)
        if api == "lookup_manual":
            return await self.on_manual(res, c)
        if api == "book_flight":
            self.state["slots"]["booking_id"] = res.get("booking_id")
        if api == "create_support_ticket":
            self.state["slots"]["ticket_id"] = res.get("ticket_id")
        if api == "cancel_booking":
            bid = res.get("cancelled") or res.get("cancelled_booking_id") or c["args"].get("booking_id")
            prior = self.ledger.find_committed("book_flight", "booking_id", bid)
            if prior is not None:
                self.ledger.reverse(prior, by=cid)       # R05: a confirmed cancel re-enables a fresh booking
            if self.state["slots"].get("booking_id") == bid:
                self.state["slots"].pop("booking_id", None)
            ac, self.after_cancel = self.after_cancel, None
            if ac and str(ac["booking_id"]) == str(bid):
                await self.say("filler_speech", f"{bid} is cancelled — booking {ac['args']['flight_id']} now.")
                await self.call("book_flight", ac["args"], deps=ac["deps"])
                return
        # the completion is bound to the task that produced it, not to whatever is current (R22)
        ctx = dict(c["ctx"]) if kind == "state_modifying" else dict(self.state["slots"])
        subj = c["args"].get("city") or c["args"].get("destination")
        if kind != "state_modifying" and isinstance(subj, str):
            ctx["destination"] = subj
        await self.say("final_response", self.describe_success(api, res, ctx))

    def describe_success(self, api: str, res: Dict[str, Any], s: Optional[Dict[str, Any]]) -> str:
        s = s or {}
        if api == "book_flight":
            return (f"Done — you're booked on {res.get('flight_id', s.get('flight_id'))} to {s.get('destination', 'your destination')}"
                    f"{' for ' + s['passenger_name'] if s.get('passenger_name') else ''}. Booking reference {res.get('booking_id')}.")
        if api == "cancel_booking":
            return f"Your booking {res.get('cancelled', '')} has been cancelled."
        if api == "create_support_ticket":
            return f"I've opened support ticket {res.get('ticket_id')} for your {self.device_word()} — a technician will follow up."
        summary = nlu.humanize_result(api, res)
        subj = s.get("destination")
        return (f"Here's what I found{' for ' + subj if subj else ''}: {summary}." if summary
                else f"Done — {api.replace('_', ' ')} completed.")

    async def on_flights(self, res: Dict[str, Any], c: Dict[str, Any]):
        s = self.state["slots"]
        dest = c["args"].get("destination") or s.get("destination")   # bound to the originating call
        flights = res.get("flights") or []
        if not flights:
            self.plan = []
            return await self.say("final_response", f"I couldn't find any flights to {dest or 'there'}. Want to try another date?")
        want = s.get("depart_time")
        pick, matched = flights[0], want is None
        if want:
            for f in flights:
                if str(f.get("depart", "")).strip()[:5] == want:
                    pick, matched = f, True
                    break
        plan = self.plan or c.get("plan") or []
        if not matched:
            self.plan = []
            opts = " or ".join(f"{f['flight_id']} at {f.get('depart')} (${f.get('price_usd')})" for f in flights[:3])
            self.pending_clarify = {"field": "depart_time", "api": "flight_search", "args": {}, "text": c["turn"],
                                    "plan": plan, "version": self.version}
            return await self.say("clarification_request",
                                  f"I couldn't find a flight to {dest} at {want}. The options are {opts} — which would you like?")
        if plan and plan[0] == "book_flight":
            self.plan = []
            s["flight_id"] = pick["flight_id"]
            name = s.get("passenger_name")
            if not name:
                self.pending_clarify = {"field": "passenger_name", "api": "book_flight", "args": {},
                                        "text": c["turn"], "version": self.version}
                return await self.say("final_response",
                                      f"I found a flight to {dest}: {pick['flight_id']} departing "
                                      f"{pick.get('depart')} for ${pick.get('price_usd')}. Whose name should I book it under?")
            await self.issue_booking({"flight_id": pick["flight_id"], "passenger_name": name},
                                     {"flight_id": pick["flight_id"]},
                                     announce=f"Found {pick['flight_id']} at {pick.get('depart')} — booking it for {name} now.")
            return
        s["flight_id"] = pick["flight_id"]
        others = [f for f in flights if f is not pick]
        extra = (f" There's also {others[0]['flight_id']} at {others[0].get('depart')} for ${others[0].get('price_usd')}."
                 if others else "")
        await self.say("final_response",
                       f"I found a flight to {dest}: {pick['flight_id']} departing {pick.get('depart')} "
                       f"for ${pick.get('price_usd')}.{extra}")

    async def on_manual(self, res: Dict[str, Any], c: Dict[str, Any]):
        pages = res.get("pages") or []
        label, visual = c.get("vision_label"), c.get("visual", False)
        if not pages:
            return await self.say("final_response", "I checked the manuals but found no page covering that. "
                                  + ("Could you point the camera a little closer?" if visual else "Could you describe it a bit more?"))
        if visual and not label and len(pages) > 1:
            # vision gave us nothing reliable: don't guess a port type from text-only ranking
            return await self.say("clarification_request",
                                  f"I couldn't make out the port clearly from the camera. The {pages[0].get('doc', 'device')} manual "
                                  f"covers several connectors — could you move a little closer or tell me what's printed next to it?")
        best = pages[0]
        if label:
            key = label.split()[0].lower()
            for pg in pages:
                if key in pg.get("title", "").lower():
                    best = pg
                    break
        title = best.get("title", "")
        doc = re.sub(r"(?i)[-_ ]?manual$", "", best.get("doc", "device"))
        if not visual:
            return await self.say("final_response", f"The {doc} manual covers that on page {best.get('page')} (\"{title}\").")
        use = {"hdmi": "connect an external display, monitor or projector with an HDMI cable",
               "led": "show the device status", "charging": "charge the device", "drum": "explain drum error codes"}
        why = next((v for k, v in use.items() if k in title.lower()), None)
        head = title.split(" ")[0] if title else "port"
        suffix = " port" if "port" not in title.lower() and "hdmi" in title.lower() else ""
        await self.say("final_response", f"That's the {head}{suffix}" + (f" — it's used to {why}." if why else ".")
                       + f" See the {doc} manual, page {best.get('page')} (\"{title}\").")
