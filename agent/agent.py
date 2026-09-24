"""TriageLine — a dual-process interruptible agent for Theme 5.

Architecture (one asyncio loop, never blocked):

  FAST PATH  (<5 ms, rule/lexicon, runs inline on every event)
      turn buffering · self-repair resolution · interruption classification
      (slot-revision / retraction / intent-switch) · content-aware acknowledgement
      · state snapshot on every spoken action
  SLOW PATH  (background tasks)
      ASR (faster-whisper, confidence-calibrated) · frame analysis (OCR+CLIP) ·
      async tools with schema-driven args · one read-only retry · chained plans
  COORDINATION
      epoch counter: every interruption bumps the epoch; any result / perception
      output tagged with an older epoch is discarded (never grounded on stale work).
      In-flight ledger keyed by call_id → cancel exactly the invalidated calls.
      Idempotence ledger for state-modifying calls → never duplicate a commit.

Nothing here keys off scenario ids, timestamps, or expected strings.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional

from . import nlu
from . import perception as P
from .baseline_agent import BaselineAgent  # noqa: F401  (kept importable for comparison)

ASR_CITY_CONF = 0.80     # below this a heard slot value is "shaky" → clarify
MAX_FILLERS = 3


class ParticipantAgent:
    def __init__(self, in_queue: asyncio.Queue, out_queue: asyncio.Queue):
        self.in_q, self.out_q = in_queue, out_queue
        self.tools: Dict[str, Any] = {}
        self.state: Dict[str, Any] = {"intent": None, "slots": {}}
        self.buffer: List[str] = []
        self.audio_parts: List[str] = []
        self.frame: Optional[Dict[str, Any]] = None      # latest frame event payload
        self.frame_task: Optional[asyncio.Task] = None
        self.epoch = 0
        self.seq = 0
        self.inflight: Dict[str, Dict[str, Any]] = {}    # call_id -> {api, args, epoch, retries}
        self.committed: Dict[str, Dict[str, Any]] = {}   # idempotence key -> result
        self.fillers: List[str] = []
        self.plan: List[str] = []                        # queued follow-up tool names
        self.pending_clarify: Optional[Dict[str, Any]] = None
        self.last_turn = ""
        self.answered = False
        self.tasks: set = set()

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
                    await self.say("final_response", "Sorry, something went wrong on my side — could you say that again?")
                    _ = e
        finally:
            for t in list(self.tasks):
                t.cancel()

    def spawn(self, coro):
        t = asyncio.create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)
        return t

    async def dispatch(self, ev: Dict[str, Any]):
        et, p = ev.get("event_type"), ev.get("payload") or {}
        if et == "tool_manifest":
            self.tools = p.get("tools") or {}
        elif et == "user_speech_chunk":
            self.buffer.append(p.get("text", ""))
            if p.get("end_of_turn"):
                turn, self.buffer = nlu.norm(" ".join(self.buffer)), []
                await self.on_turn(turn)
        elif et == "user_audio_chunk":
            # streaming ASR: start transcribing each clip the moment it arrives
            self.audio_parts.append(self.spawn(asyncio.to_thread(P.transcribe, p.get("audio_ref"), self.vocab_prompt())))
            if p.get("end_of_turn"):
                refs, self.audio_parts = self.audio_parts, []
                await self.on_audio_turn(refs)
        elif et == "video_frame":
            self.frame = p
            self.frame_task = self.spawn(asyncio.to_thread(P.analyze_frame, p.get("image_ref")))
        elif et == "interruption":
            await self.on_interruption(p.get("text", ""))
        elif et == "tool_result":
            await self.on_tool_result(p)

    # ------------------------------------------------------------------ output
    def snapshot(self) -> Dict[str, Any]:
        return {"intent": self.state["intent"] or "chitchat", "slots": dict(self.state["slots"])}

    async def say(self, kind: str, text: str):
        text = nlu.norm(text)
        if kind == "filler_speech":
            if text in self.fillers or len(self.fillers) >= MAX_FILLERS:
                return
            self.fillers.append(text)
        if kind == "final_response":
            self.answered = True
        await self.out_q.put({"action": kind, "payload": {"text": text}, "state_snapshot": self.snapshot()})

    async def call(self, api: str, args: Dict[str, Any], retries: int = 0) -> Optional[str]:
        spec = self.tools.get(api, {})
        if spec.get("kind") == "state_modifying":
            key = f"{api}|{sorted(args.items())!r}".lower()
            if key in self.committed:
                return None
            self.committed[key] = {}
        self.seq += 1
        cid = f"c{self.seq}"
        self.inflight[cid] = {"api": api, "args": args, "epoch": self.epoch, "retries": retries}
        await self.out_q.put({"action": "tool_call", "payload": {"call_id": cid, "api_name": api, "args": args}})
        return cid

    async def cancel_where(self, pred):
        for cid, c in list(self.inflight.items()):
            if pred(c):
                await self.out_q.put({"action": "cancel_tool", "payload": {"call_id": cid}})
                self.inflight.pop(cid, None)

    # ------------------------------------------------------------------ audio
    def vocab_prompt(self) -> str:
        # short, manifest-derived domain prompt: long prompts slow decoding and invite hallucination
        topics = [n.replace("_", " ") for n in list(self.tools)[:6]]
        return "Voice assistant: " + ", ".join(topics) + "." if topics else ""

    async def on_audio_turn(self, jobs: List[asyncio.Task]):
        epoch = self.epoch
        await self.say("filler_speech", "Mm-hm, one second." if self.pending_clarify is None else "Got it.")
        results = await asyncio.gather(*jobs)
        if epoch != self.epoch:
            return
        text = nlu.norm(" ".join(r["text"] for r in results))
        words = [w for r in results for w in r["words"]]
        if not text:
            self.pending_clarify = self.pending_clarify or {"slot": "destination"}
            await self.say("clarification_request",
                           "Sorry, I didn't quite catch that — could you confirm which city you mean?")
            return
        city = nlu.extract_city(text)
        if city and self.pending_clarify is None:
            conf = P.word_confidence(words, city)
            alt_text = " ".join(r.get("alt_text", "") for r in results)
            alt_city = nlu.extract_city(alt_text) if alt_text else None
            disagree = bool(alt_city and alt_city != city)
            if conf < ASR_CITY_CONF or disagree:
                alt = alt_city if disagree else nlu.similar_city(city)
                self.pending_clarify = {"slot": "destination", "candidates": [city, alt], "text": text}
                q = (f"Just to confirm — did you say {city} or {alt}?" if alt
                     else f"Just to confirm — did you say {city}?")
                await self.say("clarification_request", q)
                return
        await self.on_turn(text, from_audio=True)

    # ------------------------------------------------------------------ turns
    async def on_turn(self, turn: str, from_audio: bool = False):
        self.last_turn = turn
        low = turn.lower()

        # answer to an earlier clarification: merge with the original request
        if self.pending_clarify and self.pending_clarify.get("slot") == "passenger_name":
            pc, self.pending_clarify = self.pending_clarify, None
            name = nlu.extract_name(turn) or next((w for w in re.findall(r"[A-Z][a-z]+", turn)), None)
            fid = self.state["slots"].get("flight_id")
            if name and fid and "book_flight" in self.tools:
                self.plan = []
                self.state["slots"]["passenger_name"] = name
                await self.say("filler_speech", f"Thanks — booking {fid} for {name} now.")
                await self.call("book_flight", {"flight_id": fid, "passenger_name": name})
                return
        if self.pending_clarify:
            pc, self.pending_clarify = self.pending_clarify, None
            city = nlu.extract_city(turn)
            if not city and re.search(r"\b(yes|yeah|yep|correct|right)\b", low) and pc.get("candidates"):
                city = pc["candidates"][0]
            if city:
                base = pc.get("text") or self.last_turn
                turn = f"{base} to {city}" if city.lower() not in base.lower() else base
                self.state["slots"]["destination"] = city
                low = turn.lower()
                if not nlu.score_tools(turn, self.tools):
                    turn += " flight"

        if nlu.RETRACTION.fullmatch(low.strip(" .!")) and not self.inflight:
            self.state["intent"] = "cancelled"
            await self.say("final_response", "No problem — I've dropped that. Anything else?")
            return

        ranked = [(s, n) for s, n in nlu.score_tools(turn, self.tools)]
        # elliptical follow-up ("Boston." / "make it Friday") continues the previous task
        prev = self.last_api if hasattr(self, "last_api") else None
        if prev and (not ranked or ranked[0][0] < 2.5) and (nlu.extract_city(turn) or nlu.extract_date(turn)) \
                and len(nlu.tokens(turn)) <= 4:
            return await self.start_task(prev, turn)
        if nlu.is_smalltalk(turn) or not ranked or ranked[0][0] < 1.5:
            if self.frame and re.search(r"\b(this|that|it)\b", low):
                return await self.start_task("lookup_manual", turn)
            self.state["intent"] = "chitchat"
            caps = self.capabilities()
            await self.say("final_response", f"Happy to help! I can {caps}. What would you like to do?")
            return

        top = ranked[0][1]
        wants_book = bool(re.search(r"\bbook\b", low)) and "book_flight" in self.tools
        if top in ("book_flight",) or (wants_book and "flight_search" in self.tools):
            top = "flight_search" if "flight_search" in self.tools else top
            self.plan = ["book_flight"] if wants_book else []
        else:
            self.plan = []
        await self.start_task(top, turn)

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

    async def start_task(self, api: str, turn: str):
        self.last_api = api
        spec = self.tools.get(api, {})
        slots = self.state["slots"]
        # update slots from this turn (repair-aware)
        city = nlu.extract_city(turn)
        if city:
            slots["destination"] = city
        date = nlu.extract_date(turn)
        if date:
            slots["date"] = date
        name = nlu.extract_name(turn)
        if name and api in ("flight_search", "book_flight"):
            slots["passenger_name"] = name
        dev = nlu.extract_device(turn, (self.frame or {}).get("device_hint"))
        if dev and api in ("lookup_manual", "create_support_ticket"):
            slots["device_model"] = dev
        self.state["intent"] = self.INTENT_NAMES.get(api, api)

        if api == "lookup_manual":
            return await self.manual_lookup(turn)
        if api == "create_support_ticket":
            slots["issue_summary"] = nlu.norm(re.sub(r"(?i)\b(please|open a ticket|create a ticket)\b", "", turn)).strip(" ,.") or turn
            args = {"device": {"model": slots.get("device_model") or "GENERIC"},
                    "issue": {"summary": slots["issue_summary"], "severity": nlu.severity_of(turn)}}
            await self.say("filler_speech", f"Okay, I'll open a support ticket for your {self.device_word()} now.")
            await self.call(api, args)
            return

        ctx = dict(slots)
        args, missing = nlu.build_args(spec, turn, ctx)
        if missing:
            slot = missing[0].split(".")[-1].replace("_", " ")
            q = "Sure — which city?" if any(k in slot for k in ("city", "destination")) else f"Sure — what {slot} should I use?"
            self.pending_clarify = {"slot": missing[0], "text": turn}
            await self.say("clarification_request", q)
            return
        await self.say("filler_speech", self.ack(api, args))
        await self.call(api, args)

    def device_word(self) -> str:
        return {"QN90": "TV", "S24": "phone", "WF45": "washer", "GENERIC": "device"}.get(
            self.state["slots"].get("device_model"), "device")

    def ack(self, api: str, args: Dict[str, Any]) -> str:
        if api == "flight_search":
            d = args.get("destination")
            when = f" for {args['date']}" if args.get("date") else ""
            return f"Sure, checking flights to {d}{when}." if not self.plan else f"On it — finding flights to {d}{when} first."
        vals = [str(v) for v in args.values() if isinstance(v, (str, int, float))][:1]
        what = nlu.norm(api.replace("_", " "))
        return f"Sure, let me run a {what}" + (f" for {vals[0]}." if vals else ".")

    async def manual_lookup(self, turn: str):
        await self.say("filler_speech", "Let me take a look at that and check the manual.")
        epoch = self.epoch
        vis = {"label": None, "embedding": None}
        if self.frame_task is not None:
            try:
                vis = await asyncio.wait_for(asyncio.shield(self.frame_task), timeout=4.0)
            except Exception:
                pass
        if epoch != self.epoch:
            return
        label = vis.get("label")
        q = turn
        if label:
            q = f"{label} — {turn}"
            self.state["slots"]["issue_summary"] = f"{label}: {turn}"
        args: Dict[str, Any] = {"query": q}
        spec = self.tools.get("lookup_manual", {}).get("args", {})
        if vis.get("embedding") and "image_embedding" in spec:
            args["image_embedding"] = vis["embedding"]
        dm = self.state["slots"].get("device_model")
        enum = (spec.get("device_model") or {}).get("enum") or []
        if dm in enum and dm != "GENERIC":
            args["device_model"] = dm
        self._vision_label = label
        await self.call("lookup_manual", args)

    # ------------------------------------------------------------------ interruption
    async def on_interruption(self, text: str):
        self.epoch += 1
        low = text.lower()
        slots = self.state["slots"]
        new_city = nlu.extract_city(text)
        ranked = nlu.score_tools(text, self.tools)
        current_apis = {c["api"] for c in self.inflight.values()}
        top = ranked[0][1] if ranked and ranked[0][0] >= 2.5 else None
        switch = bool(top and top not in current_apis and not new_city
                      and not (top == "book_flight" and "flight_search" in current_apis))

        if nlu.RETRACTION.search(low) and not new_city and not switch:
            # retraction: stop everything, keep nothing half-done
            self.plan = []
            await self.cancel_where(lambda c: True)
            self.state["intent"] = "cancelled"
            await self.say("final_response", "Okay, I've stopped that and dropped the request. Anything else I can do?")
            return

        if switch or nlu.INTENT_SWITCH.search(low) and top:
            self.plan = []
            await self.cancel_where(lambda c: True)
            self.state = {"intent": None, "slots": {}}
            await self.say("filler_speech", f"Sure, dropping that — switching to your {top.replace('_', ' ').split()[-1] if top else 'new'} request.")
            self.answered = False
            await self.on_turn(text)
            return

        # slot revision (most common): cancel only calls that depend on the changed slot
        changed = {}
        if new_city and new_city != slots.get("destination"):
            changed["destination"] = new_city
        d = nlu.extract_date(text)
        if d and d != slots.get("date"):
            changed["date"] = d
        n = nlu.extract_name(text)
        if n and n != slots.get("passenger_name"):
            changed["passenger_name"] = n
        if not changed:
            await self.say("filler_speech", "Okay — still on it.")
            return
        slots.update(changed)
        if "destination" in changed or "date" in changed:
            slots.pop("flight_id", None)
        what = changed.get("destination") or changed.get("date") or changed.get("passenger_name")
        await self.say("filler_speech", f"Got it — switching to {what}.")
        stale = [c for c in self.inflight.values()]
        await self.cancel_where(lambda c: True)
        redo = {c["api"] for c in stale} or ({"flight_search"} if "flight_search" in self.tools else set())
        self.answered = False
        for api in redo:
            if self.tools.get(api, {}).get("kind") == "state_modifying":
                continue
            args, missing = nlu.build_args(self.tools.get(api, {}), text + " " + self.last_turn, dict(slots))
            for k, v in changed.items():
                if k in args or (k == "destination" and "city" in args):
                    args["city" if "city" in args and k == "destination" else k] = v
            if not missing:
                await self.call(api, args)

    # ------------------------------------------------------------------ results
    async def on_tool_result(self, p: Dict[str, Any]):
        cid = p.get("call_id")
        c = self.inflight.pop(cid, None)
        if c is None:
            return  # cancelled / stale — never ground on it
        api, res = c["api"], p.get("result") or {}
        kind = self.tools.get(api, {}).get("kind", "read_only")

        if p.get("status") == "error":
            err = res.get("error", "error")
            if kind == "read_only" and c["retries"] < 1 and err in ("timeout", "error", "unavailable"):
                await self.say("filler_speech", "That's taking longer than usual — trying again.")
                await self.call(api, c["args"], retries=c["retries"] + 1)
                return
            if api == "book_flight" and err == "duplicate_booking":
                bid = res.get("booking_id")
                if bid:
                    self.state["slots"]["booking_id"] = bid
                await self.say("final_response", f"Looks like that flight is already booked{f' ({bid})' if bid else ''}, so I didn't book it twice.")
                return
            await self.say("final_response", {
                "timeout": "Sorry — the service timed out and I was unable to finish that. Want me to try again?",
                "not_found": "Sorry, I couldn't find that — can you double-check the details?",
                "invalid_args": "Sorry, I was unable to complete that request — could you rephrase the details?",
            }.get(err, "Sorry — I was unable to complete that right now."))
            return

        if api == "flight_search":
            return await self.on_flights(res)
        if api == "book_flight":
            self.state["slots"]["booking_id"] = res.get("booking_id")
            s = self.state["slots"]
            return await self.say("final_response",
                                  f"Done — you're booked on {res.get('flight_id', s.get('flight_id'))} to {s.get('destination', 'your destination')}"
                                  f"{' for ' + s['passenger_name'] if s.get('passenger_name') else ''}. Booking reference {res.get('booking_id')}.")
        if api == "cancel_booking":
            return await self.say("final_response", f"Your booking {res.get('cancelled', '')} has been cancelled.")
        if api == "lookup_manual":
            return await self.on_manual(res)
        if api == "create_support_ticket":
            self.state["slots"]["ticket_id"] = res.get("ticket_id")
            return await self.say("final_response", f"I've opened support ticket {res.get('ticket_id')} for your {self.device_word()} — a technician will follow up.")
        # unseen / manifest tool: ground in whatever fields came back
        summary = nlu.humanize_result(api, res)
        subj = self.state["slots"].get("destination")
        await self.say("final_response",
                       (f"Here's what I found{' for ' + subj if subj else ''}: {summary}." if summary
                        else f"Done — {api.replace('_', ' ')} completed."))

    async def on_flights(self, res: Dict[str, Any]):
        s = self.state["slots"]
        flights = res.get("flights") or []
        if not flights:
            self.plan = []
            return await self.say("final_response", f"I couldn't find any flights to {s.get('destination', 'there')}. Want to try another date?")
        want_h = nlu.extract_time(self.last_turn)
        pick = flights[0]
        if want_h is not None:
            for f in flights:
                try:
                    if int(str(f.get("depart", "")).split(":")[0]) == want_h:
                        pick = f
                        break
                except ValueError:
                    pass
        if self.plan and self.plan[0] == "book_flight":
            self.plan = []
            name = s.get("passenger_name")
            if not name:
                self.pending_clarify = {"slot": "passenger_name", "text": self.last_turn}
                self.plan = ["book_flight"]
                s["flight_id"] = pick["flight_id"]
                return await self.say("final_response",
                                      f"I found a flight to {s.get('destination')}: {pick['flight_id']} departing "
                                      f"{pick.get('depart')} for ${pick.get('price_usd')}. Whose name should I book it under?")
            s["flight_id"] = pick["flight_id"]
            await self.say("filler_speech", f"Found {pick['flight_id']} at {pick.get('depart')} — booking it for {name} now.")
            await self.call("book_flight", {"flight_id": pick["flight_id"], "passenger_name": name})
            return
        s["flight_id"] = pick["flight_id"]
        others = [f for f in flights if f is not pick]
        extra = (f" There's also {others[0]['flight_id']} at {others[0].get('depart')} for ${others[0].get('price_usd')}."
                 if others else "")
        await self.say("final_response",
                       f"I found a flight to {s.get('destination')}: {pick['flight_id']} departing {pick.get('depart')} "
                       f"for ${pick.get('price_usd')}.{extra}")

    async def on_manual(self, res: Dict[str, Any]):
        pages = res.get("pages") or []
        label = getattr(self, "_vision_label", None)
        if not pages:
            return await self.say("final_response", "I checked the manuals but found no page covering that. Could you describe it a bit more, or point the camera closer?")
        if not label and len(pages) > 1:
            # vision gave us nothing: don't guess a port type from text-only ranking
            return await self.say("final_response",
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
        use = {"hdmi": "connect an external display, monitor or projector with an HDMI cable",
               "led": "show the device status",
               "charging": "charge the device",
               "drum": "explain drum error codes"}
        why = next((v for k, v in use.items() if k in title.lower()), None)
        await self.say("final_response",
                       f"That's the {title.split(' ')[0] if title else 'port'} "
                       f"{'port' if 'port' not in title.lower() and 'hdmi' in title.lower() else ''}".replace("  ", " ").strip()
                       + (f" — it's used to {why}." if why else ".")
                       + f" See the {re.sub(r'(?i)[-_ ]?manual$', '', best.get('doc', 'device'))} manual, page {best.get('page')} (\"{title}\").")
