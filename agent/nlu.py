"""Fast-path language understanding: pure-Python, sub-millisecond, no model calls.

Everything here is schema-driven or lexicon-driven — nothing keys off scenario ids,
timestamps or expected strings. The slow path (tools, ASR, vision) lives elsewhere.
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- lexicon
CITIES = {
    "boston": "Boston", "bos": "Boston", "new york": "New York", "nyc": "New York",
    "new york city": "New York", "chicago": "Chicago", "denver": "Denver",
    "seattle": "Seattle", "miami": "Miami", "austin": "Austin", "dallas": "Dallas",
    "houston": "Houston", "atlanta": "Atlanta", "phoenix": "Phoenix",
    "portland": "Portland", "san francisco": "San Francisco", "sf": "San Francisco",
    "los angeles": "Los Angeles", "la": "Los Angeles", "las vegas": "Las Vegas",
    "san diego": "San Diego", "washington": "Washington", "philadelphia": "Philadelphia",
    "orlando": "Orlando", "detroit": "Detroit", "minneapolis": "Minneapolis",
    "nashville": "Nashville", "london": "London", "paris": "Paris", "tokyo": "Tokyo",
    "seoul": "Seoul", "berlin": "Berlin", "toronto": "Toronto", "vancouver": "Vancouver",
    "bangalore": "Bangalore", "bengaluru": "Bangalore", "delhi": "Delhi",
    "mumbai": "Mumbai", "singapore": "Singapore", "sydney": "Sydney", "dubai": "Dubai",
}
_CITY_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, CITIES), key=len, reverse=True)) + r")\b", re.I)
_CAP_AFTER_PREP = re.compile(r"\b(?:to|in|at|from)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)")
# lowercase unknown place after a travel cue ("flights to kochi", "weather in pune")
_LOW_PLACE = re.compile(r"\b(?:flights?|fly|flying|trip|travel|going|weather|hotels?|forecast|rental)\b[^.?!]*?"
                        r"\b(?:to|in)\s+([a-z][a-z]{2,})\b", re.I)
# contextual place span after a directional preposition, lowercase or not, up to 3 words (R13)
_PREP_SPAN = re.compile(r"\b(?:to|from|in)\s+(?=([A-Za-z][A-Za-z]+(?:\s+[A-Za-z][A-Za-z]+){0,2}))", re.I)
_SPAN_STOP = re.compile(r"\b(?:for|on|at|by|tomorrow|today|tonight|next|this|and|please|under|with|around|"
                        r"departing|leaving|returning|in|to|from|monday|tuesday|wednesday|thursday|friday|saturday|"
                        r"sunday|morning|evening|afternoon|night|then|book|it|a|the)\b.*$", re.I)
_FROM_RE = re.compile(r"\bfrom\s*$", re.I)

# self-repair / correction markers — the value AFTER the last marker wins
REPAIR_MARKERS = re.compile(
    r"\b(?:no[, \-]+wait|wait[, \-]+no|no[, ]+sorry|sorry[, ]+i mean|i mean|actually|"
    r"rather|scratch that|make (?:it|that)|change (?:it|that) to|instead|not\s+\w+[, ]+but|no[,.]\s)",
    re.I)
RETRACTION = re.compile(
    r"\b(never ?mind|forget (?:it|about it|that)|cancel (?:that|it|everything)|don'?t bother|"
    r"stop(?: that)?|no need|skip it|call it off)\b", re.I)
INTENT_SWITCH = re.compile(r"\b(forget the \w+|different question|something else)\b", re.I)
GREETING = re.compile(r"\b(hi|hello|hey|what can you (?:do|help)|who are you|help me with)\b", re.I)
WEEKDAYS = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
DATE_RE = re.compile(
    r"\b(today|tonight|tomorrow|day after tomorrow|this weekend|next week|(?:next |this )?" + WEEKDAYS +
    r"|\d{4}-\d{2}-\d{2}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2}(?:st|nd|rd|th)?)\b", re.I)
TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)", re.I)
TIME24_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
NAME_RE = re.compile(r"\b(?:for|passenger|name is|named|i am|i'm|under)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)")
ID_RE = re.compile(r"\b([A-Za-z]{2,4}-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)\b")
ID_PREFIX = {"booking_id": "BK", "flight_id": "FL", "ticket_id": "TK"}
NEG_BOOK = re.compile(r"\b(?:do not|don'?t|dont|never|no need to|without|not)\s+(?:\w+\s+){0,2}?(?:book|booking|reserve)\b|"
                      r"\b(?:only|just)\s+(?:search|show|look|check|find)|\bshow (?:me )?(?:the )?options\b", re.I)
YES_RE = re.compile(r"^\W*(yes|yeah|yep|yup|correct|right|sure|that'?s right|exactly)\b", re.I)
NO_RE = re.compile(r"^\W*(no|nope|nah|wrong|incorrect)\b", re.I)
NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
STOP = set("""a an the to for of in on at and or is are be me my i you your it this that
please can could would will do does what whats what's how with like right now just some any
find get show tell want need there here up""".split())

# keyword priors for the public tool families; hidden tools fall back to schema overlap
TOOL_PRIORS = {
    "flight_search": {"flight", "flights", "fly", "flying", "plane", "airfare", "seats", "trip"},
    "book_flight": {"book", "reserve"},
    "cancel_booking": {"cancel", "refund"},
    "lookup_manual": {"port", "manual", "what", "blinking", "light", "error", "code",
                      "used", "connect", "cable", "led", "drum", "charging", "does"},
    "create_support_ticket": {"ticket", "broken", "support", "repair", "technician", "escalate",
                              "complaint", "report"},
}
DEVICE_ALIASES = {"QN90": ["qn90", "tv", "television", "neo qled"],
                  "S24": ["s24", "galaxy", "phone"],
                  "WF45": ["wf45", "washer", "washing machine"],
                  "GENERIC": ["laptop", "notebook", "pc", "computer"]}


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def tokens(s: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if w not in STOP]


def _stem(w: str) -> str:
    for suf in ("ings", "ing", "ies", "es", "s", "ed"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)]
    return w


# --------------------------------------------------------------------------- slots
_NOT_PLACE = set("""tomorrow today tonight morning evening afternoon night week weekend flight flights book
booking the a an my me him her them it this that there here help go get see buy check""".split())


def _travel_context(text: str) -> bool:
    return bool(re.search(r"\b(?:flights?|fly|flying|trip|travel|going|weather|hotels?|forecast|rental|book|"
                          r"from|to)\b", text, re.I))


def cities_in(text: str) -> List[Tuple[int, str]]:
    text = text or ""
    out = [(m.start(), CITIES[m.group(1).lower()]) for m in _CITY_RE.finditer(text)]
    if out and _travel_context(text):
        # combine gazetteer hits with contextual spans so "from Boston to Kochi" keeps both (R13)
        known = {p for p, _ in out}
        for m in _PREP_SPAN.finditer(text):
            st = m.start(1)
            if any(abs(st - k) < 2 for k in known):
                continue
            cand = _SPAN_STOP.sub("", m.group(1)).strip()
            if not cand or cand.lower() in STOP or cand.lower() in _NOT_PLACE or re.match(WEEKDAYS, cand, re.I):
                continue
            if _CITY_RE.fullmatch(cand):
                continue
            if cand[0].isupper() or re.search(r"\bfrom\s*$", text[:m.start(1)], re.I) or \
                    re.search(r"\b(?:flights?|fly|trip|travel)\b", text, re.I):
                out.append((st, " ".join(w[0].upper() + w[1:] for w in cand.split())))
        out.sort()
    if not out:
        for m in _CAP_AFTER_PREP.finditer(text):
            cand = m.group(1)
            if cand.lower() not in STOP and not re.match(WEEKDAYS, cand, re.I):
                out.append((m.start(1), cand))
    if not out:
        for m in _LOW_PLACE.finditer(text):
            cand = m.group(1)
            if cand.lower() not in STOP and cand.lower() not in _NOT_PLACE and not re.match(WEEKDAYS, cand, re.I):
                # keep a multiword lowercase place ("san jose") rather than truncating it (R13)
                rest = text[m.end(1):]
                nxt = re.match(r"\s+([a-z]{3,})\b", rest)
                if nxt and not _SPAN_STOP.fullmatch(nxt.group(1)) and nxt.group(1) not in STOP and \
                        nxt.group(1) not in _NOT_PLACE and not re.match(WEEKDAYS, nxt.group(1), re.I) and \
                        not DATE_RE.match(nxt.group(1)):
                    cand = cand + " " + nxt.group(1)
                out.append((m.start(1), " ".join(w[0].upper() + w[1:] for w in cand.split())))
    return out


def _is_origin(text: str, pos: int) -> bool:
    return bool(_FROM_RE.search(text[:pos]))


def _pick_after_repair(text: str, found: List[Tuple[int, str]]) -> Optional[str]:
    if not found:
        return None
    last_marker = max((m.end() for m in REPAIR_MARKERS.finditer(text)), default=-1)
    after = [c for pos, c in found if pos >= last_marker]
    return after[-1] if after else found[-1][1]


def extract_city(text: str) -> Optional[str]:
    """Destination-role city: the last city after the last self-repair marker, never an origin."""
    text = text or ""
    found = [(p, c) for p, c in cities_in(text) if not _is_origin(text, p)]
    return _pick_after_repair(text, found)


def extract_origin(text: str) -> Optional[str]:
    text = text or ""
    return _pick_after_repair(text, [(p, c) for p, c in cities_in(text) if _is_origin(text, p)])


def extract_date(text: str) -> Optional[str]:
    m = list(DATE_RE.finditer(text or ""))
    return m[-1].group(1) if m else None


def extract_time(text: str) -> Optional[str]:
    """Full time constraint as 'HH:MM' (minutes preserved), or None."""
    ms = list(TIME_RE.finditer(text or ""))
    if ms:
        m = ms[-1]
        h = int(m.group(1)) % 12
        if m.group(3).lower().startswith("p"):
            h += 12
        return f"{h:02d}:{int(m.group(2) or 0):02d}"
    ms = list(TIME24_RE.finditer(text or ""))
    if ms:
        return f"{int(ms[-1].group(1)):02d}:{ms[-1].group(2)}"
    return None


def _bad_name(cand: str) -> bool:
    first = cand.split()[0].lower()
    return (cand.lower() in CITIES or first in CITIES or re.match(WEEKDAYS, cand, re.I) is not None
            or first in STOP or first in _NOT_PLACE)


def extract_name(text: str) -> Optional[str]:
    """Passenger-role name; repair-aware ("for Alice, actually for Priya" -> Priya)."""
    text = text or ""
    found = []
    for m in NAME_RE.finditer(text):
        cand = m.group(1)
        parts = cand.split()
        if len(parts) == 2 and _bad_name(parts[1]):
            cand = parts[0]
        if _bad_name(cand):
            continue
        found.append((m.start(1), cand))
    return _pick_after_repair(text, found)


def parse_name_answer(text: str) -> Optional[str]:
    """Name given as a clarification answer: case-insensitive, full name kept."""
    t = re.sub(r"(?i)^\W*(?:(?:it'?s|it is|my name is|name is|the name is|under|for|book it under|passenger)\s+)+", "", text or "")
    if re.match(r"(?i)^\W*(?:i said|i mean|i meant|no[, ]|yes[, ]|actually)", t) or cities_in(t):
        return None
    t = re.sub(r"(?i)\b(please|thanks|thank you)\b", "", t)
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", t)]
    words = [w for w in words if w.lower() not in STOP][:3]
    if not words or _bad_name(words[0]):
        return None
    return " ".join(w[0].upper() + w[1:].lower() for w in words)


def extract_id(text: str, field: str = "") -> Optional[str]:
    ids = [m.group(1).upper() for m in ID_RE.finditer(text or "")]
    pre = ID_PREFIX.get(field)
    if pre:
        ids = [i for i in ids if i.startswith(pre + "-")]
    return ids[-1] if ids else None


NEG_WORD = re.compile(r"\b(?:do not|don'?t|dont|never|no need to|not)\b", re.I)


def negated_action(text: str, api: str) -> bool:
    """True when the user negates the action a (state-modifying) tool performs, e.g.
    "Do not open a support ticket" / "Please don't cancel my booking" / "Don't reserve a car".
    Schema-driven: the tool's own name tokens are matched within a short window after a negator."""
    low = (text or "").lower()
    toks = {t.rstrip("s") for t in re.split(r"[_\W]+", api.lower()) if len(t) > 2}
    for m in NEG_WORD.finditer(low):
        window = re.findall(r"[a-z']+", low[m.end():])[:4]
        if any(w.rstrip("s") in toks or w.rstrip("s").rstrip("ing") in toks for w in window):
            return True
    return False


def negates_booking(text: str) -> bool:
    return bool(NEG_BOOK.search(text or ""))


def extract_device(text: str, hint: Optional[str] = None) -> Optional[str]:
    low = (text or "").lower()
    for model, aliases in DEVICE_ALIASES.items():
        if any(re.search(r"\b" + re.escape(a) + r"\b", low) for a in aliases):
            return model
    return hint


def severity_of(text: str) -> str:
    low = (text or "").lower()
    if re.search(r"\b(smoke|fire|spark|burn|shock|won'?t turn on|dead|urgent|broken)\b", low):
        return "high"
    if re.search(r"\b(blink|flash|error|noise|slow|intermittent)\w*", low):
        return "medium"
    return "low"


# --------------------------------------------------------------------------- routing
def score_tools(text: str, tools: Dict[str, Any]) -> List[Tuple[float, str]]:
    """Rank manifest tools against an utterance: lexical priors + schema overlap."""
    toks = {_stem(t) for t in tokens(text)}
    ranked = []
    for name, spec in tools.items():
        vocab = {_stem(t) for t in tokens(name.replace("_", " ") + " " + str(spec.get("description", "")))}
        for arg, aspec in (spec.get("args") or {}).items():
            vocab |= {_stem(t) for t in tokens(arg.replace("_", " "))}
        overlap = len(toks & vocab)
        prior = len(toks & {_stem(w) for w in TOOL_PRIORS.get(name, ())})
        s = overlap + 1.5 * prior
        if s > 0:  # bonus if every required arg is fillable from this utterance
            _, missing = build_args(spec, text, {})
            if not missing and (spec.get("args") or {}):
                s += 1.0
        if s > 0:
            ranked.append((s, name))
    ranked.sort(reverse=True)
    return ranked


def is_smalltalk(text: str, tools: Optional[Dict[str, Any]] = None) -> bool:
    """Greeting/capability talk ONLY when no actionable request remains in the turn."""
    if not GREETING.search(text or ""):
        return False
    rest = GREETING.sub(" ", text or "")
    if tools and (cities_in(rest) or extract_id(rest)):
        return False
    if tools:
        r = score_tools(rest, tools)
        if r and r[0][0] >= 2.5:
            return False
    return len(tokens(text)) <= 8


# --------------------------------------------------------------------------- args
def _arg_for(name: str, spec: Dict[str, Any], text: str, ctx: Dict[str, Any]) -> Any:
    lname = name.lower()
    typ = spec.get("type", "string")
    enum = spec.get("enum")
    if typ == "object":
        obj = {}
        for sub, sspec in (spec.get("properties") or {}).items():
            v = _arg_for(sub, sspec, text, ctx)
            if v is not None:
                obj[sub] = v
        return obj
    if enum:
        got = pick_enum(text, enum)
        if got is not None:
            return got
        if lname == "severity":
            return severity_of(text) if severity_of(text) in enum else enum[0]
        if lname in ("model", "device_model") and ctx.get("device_model") in enum:
            return ctx["device_model"]
        return None
    if typ in ("number", "integer"):
        return extract_number(text, name, spec, integer=(typ == "integer"))
    if typ == "boolean":
        return extract_bool(text, name)
    if typ == "array":
        items = spec.get("items")
        ienum = (items or {}).get("enum") if isinstance(items, dict) else None
        if ienum:
            low = text.lower()
            hits = [e for e in ienum if re.search(r"\b" + re.escape(str(e).lower()) + r"\b", low)]
            return hits or None
        return ctx.get(lname)
    # strings: match by semantic role of the arg name
    if "origin" in lname or lname.startswith("from") or "departure_city" in lname:
        return ctx.get("origin") or extract_origin(text)
    if any(k in lname for k in ("city", "destination", "location", "place", "town", "to_")):
        return ctx.get("destination") or extract_city(text)
    if "date" in lname or "day" in lname or "when" in lname:
        return ctx.get("date") or extract_date(text)
    if "passenger" in lname or lname in ("name", "customer", "guest", "full_name"):
        return ctx.get("passenger_name") or extract_name(text)
    if lname.endswith("_id") or lname == "id":
        got = extract_id(text, lname) or ctx.get(lname)
        if got:
            return got
        m = re.search(r"\b([A-Z]{2,}\d+|\d+[A-Z]{2,}\w*)\b", text)
        return m.group(1) if m else None
    if lname in ("model", "device", "device_model"):
        return ctx.get("device_model")
    if lname in ("query", "summary", "question", "text", "message", "description", "issue"):
        return norm(text)
    # generic fallback for typed string fields with no dedicated semantic role
    # above (e.g. currency codes, bare alphanumeric ids without a dash prefix,
    # free-form addresses/account/filter values). Added for FDB-v3 compatibility
    # (see livekit_agent/fdb_compat_check.py) — every earlier, already-tested
    # branch still wins first, so this only fires when nothing else matched.
    if "currency" in lname:
        m = re.search(r"\b([A-Za-z]{3})\b", text[text.lower().find(lname.split("_")[0]):] or text) \
            if lname.split("_")[0] in text.lower() else None
        # fall back to scanning the whole utterance for a bare 3-letter code
        codes = re.findall(r"\b([A-Za-z]{3})\b", text)
        codes = [c.upper() for c in codes if c.upper() not in ("THE", "FOR", "AND", "TO ")]
        if lname.startswith("from") and codes:
            return codes[0]
        if lname.startswith("to") and len(codes) > 1:
            return codes[1]
        return codes[0] if codes else None
    if lname.endswith("address"):
        m = re.search(r"\b(?:from|origin)\s+(.+?)\s+to\s+(.+?)(?:\s+(?:by|via|driving|walking|transit|cycling)\b|$)", text, re.I)
        if m:
            return norm(m.group(1) if "origin" in lname else m.group(2)).strip(" .,")
        return None
    words = lname.replace("_", " ")
    m = re.search(re.escape(words) + r"\s+(?:is|to|as|=|:)?\s*([\w][\w\-./]*(?:\s+[\w][\w\-./]*){0,2})", text, re.I)
    if m:
        return norm(m.group(1)).strip(" .,")
    return None


def pick_enum(text: str, enum: List[Any]) -> Any:
    """Authoritative enum mention (R14): positions matter, not schema order. The last mention after the
    last self-repair marker wins; a mention directly negated ("not compact") is skipped."""
    low = (text or "").lower()
    hits = []
    for e in enum:
        for m in re.finditer(r"\b" + re.escape(str(e).lower()) + r"\b", low):
            if re.search(r"\b(?:not|no|don'?t want)\s+(?:a\s+|the\s+)?$", low[max(0, m.start() - 16):m.start()]):
                continue
            hits.append((m.start(), e))
    if not hits:
        return None
    hits.sort(key=lambda h: h[0])
    last_marker = max((m.end() for m in REPAIR_MARKERS.finditer(low)), default=-1)
    after = [e for p, e in hits if p >= last_marker]
    return after[-1] if after else hits[-1][1]


def validate_value(v: Any, spec: Dict[str, Any]) -> bool:
    """Pre-dispatch schema check for one leaf value (R15)."""
    typ = spec.get("type", "string")
    if typ == "integer":
        if isinstance(v, bool) or not isinstance(v, (int, float)) or float(v) != int(v):
            return False
    elif typ == "number":
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return False
    elif typ == "boolean":
        if not isinstance(v, bool):
            return False
    elif typ == "string":
        if not isinstance(v, str):
            return False
        if "minLength" in spec and len(v) < spec["minLength"]:
            return False
        if "maxLength" in spec and len(v) > spec["maxLength"]:
            return False
        if spec.get("pattern") and not re.search(spec["pattern"], v):
            return False
    elif typ == "array":
        if not isinstance(v, list):
            return False
        items = spec.get("items") if isinstance(spec.get("items"), dict) else None
        if items and not all(validate_value(x, items) for x in v):
            return False
        if "minItems" in spec and len(v) < spec["minItems"]:
            return False
    if typ in ("integer", "number"):
        if "minimum" in spec and v < spec["minimum"]:
            return False
        if "maximum" in spec and v > spec["maximum"]:
            return False
        if "exclusiveMinimum" in spec and v <= spec["exclusiveMinimum"]:
            return False
    if spec.get("enum") and v not in spec["enum"]:
        return False
    return True


_UNSET = (None, "")


def _fill(props: Dict[str, Any], text: str, ctx: Dict[str, Any], prefix: str,
          parent_required: bool) -> Tuple[Dict[str, Any], List[str]]:
    """Recursive schema fill. False / 0 / [] are valid values, only None/'' are unresolved."""
    out, missing = {}, []
    for name, aspec in (props or {}).items():
        path = f"{prefix}{name}"
        req = bool(aspec.get("required")) and parent_required
        if aspec.get("type") == "object":
            sub, gaps = _fill(aspec.get("properties") or {}, text, ctx, path + ".", True)
            if gaps:
                if req:
                    missing += gaps
                continue
            if sub or req:
                out[name] = sub
            continue
        v = ctx.get(path) if ctx.get(path) not in _UNSET else _arg_for(name, aspec, text, ctx)
        if v not in _UNSET and not validate_value(v, aspec):
            v = None                               # invalid values never reach the tool (R15)
        if v not in _UNSET:
            out[name] = v
        elif req:
            missing.append(path)
    return out, missing


def build_args(spec: Dict[str, Any], text: str, ctx: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Fill a tool call from its schema. Returns (args, missing_required)."""
    return _fill(spec.get("args") or {}, text, ctx, "", True)


def field_spec(spec: Dict[str, Any], path: str) -> Dict[str, Any]:
    node: Dict[str, Any] = {"properties": spec.get("args") or {}}
    for part in path.split("."):
        node = (node.get("properties") or {}).get(part) or {}
    return node


def parse_field_answer(answer: str, name: str, fspec: Dict[str, Any]) -> Any:
    """Parse a clarification answer specifically for the field that was asked about."""
    lname = name.split(".")[-1].lower()
    typ = fspec.get("type", "string")
    enum = fspec.get("enum")
    low = (answer or "").lower()
    if enum:
        for e in enum:
            if re.search(r"\b" + re.escape(str(e).lower()) + r"\b", low):
                return e
        close = difflib.get_close_matches(low.strip(" .!"), [str(e).lower() for e in enum], n=1, cutoff=0.7)
        return next((e for e in enum if str(e).lower() == close[0]), None) if close else None
    if typ in ("number", "integer"):
        return extract_number(answer, "", fspec, integer=(typ == "integer"))
    if typ == "boolean":
        if YES_RE.search(answer or ""):
            return True
        if NO_RE.search(answer or ""):
            return False
        return extract_bool(answer, lname)
    if typ == "array":
        return [x.strip() for x in re.split(r",|\band\b", answer or "") if x.strip()] or None
    if lname.endswith("_id") or lname == "id":
        return extract_id(answer, lname) or (norm(answer).strip(" .") or None)
    if "passenger" in lname or "name" in lname or lname in ("customer", "guest"):
        return parse_name_answer(answer)
    if "date" in lname or "day" in lname or "when" in lname:
        return extract_date(answer) or norm(answer).strip(" .") or None
    if "origin" in lname:
        return extract_origin("from " + answer) or extract_city("to " + answer)
    if any(k in lname for k in ("city", "destination", "location", "place", "town")):
        return extract_city(answer) or extract_city("to " + answer.strip())
    v = norm(re.sub(r"(?i)^\W*(?:it'?s|it is|use|make it|the|a|an)\s+", "", answer or "")).strip(" .!")
    return v or None


BUDGET_FIELD_HINTS = ("price", "budget", "rent", "cost", "limit", "amount")
BUDGET_RE = re.compile(r"\b(?:under|below|less than|no more than|up to|at most|max(?:imum)?(?: of)?|"
                       r"budget(?: of| is)?|cheaper than|within)\s*\$?\s*(\d[\d,]*(?:\.\d+)?)", re.I)

WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
            "nine": 9, "ten": 10, "a couple": 2, "a single": 1}


def extract_number(text: str, name: str, spec: Dict[str, Any], integer: bool = False) -> Optional[float]:
    """Prefer the number next to a word from the field's name/description ("3 nights" for nights)."""
    t = text or ""
    for w, n in WORD_NUM.items():
        t = re.sub(r"\b" + w + r"\b", str(n), t, flags=re.I)
    t = TIME_RE.sub(" ", t)
    t = ID_RE.sub(" ", t)
    nums = [(m.start(), m.end(), m.group()) for m in NUMBER_RE.finditer(t)]
    if not nums:
        return None
    # upper-bound / budget fields (max_price, budget, max_rent, ...): the value is
    # the number introduced by a ceiling phrase ("under 3000", "below $50",
    # "up to 2k", "budget of 900"), not whichever number happens to come first.
    lname = (name or "").lower()
    if lname and (lname.startswith("max") or any(k in lname for k in BUDGET_FIELD_HINTS)):
        m = BUDGET_RE.search(t)
        if m:
            v = float(m.group(1).replace(",", ""))
            return int(v) if integer or v.is_integer() else v
    cues = {_stem(w) for w in tokens(name.replace("_", " "))} or \
        {_stem(w) for w in tokens(str(spec.get("description", "")))}
    words = [(m.start(), _stem(m.group().lower())) for m in re.finditer(r"[A-Za-z]+", t)]
    best, bd = None, 10 ** 9
    for a, b, g in nums:
        for pos, w in words:
            if w in cues:
                d = (pos - b) if pos >= b else (a - pos) + 5   # unit after the number preferred
                if d < bd and d <= 25:
                    best, bd = g, d
    if best is None:
        if len(nums) > 1 and cues:
            return None  # several numbers and none tied to this field: ask rather than guess
        best = nums[0][2]
    v = float(best)
    if integer and not v.is_integer():
        return v                                    # keep the fraction so validation rejects it (R15)
    return int(v) if integer or v.is_integer() else v


def extract_bool(text: str, name: str) -> Optional[bool]:
    low = (text or "").lower()
    words = [w for w in tokens(name.replace("_", " ")) if len(w) > 2 and w not in ("include", "has", "with", "is")]
    for w in words:
        sw = _stem(w)
        if re.search(r"\b(?:no|without|not|don'?t want|exclude)\s+(?:\w+\s+)?" + re.escape(sw), low):
            return False
        if re.search(r"\b" + re.escape(sw), low):
            return True
    return None


def similar_city(heard: str) -> Optional[str]:
    """Nearest confusable city — used to phrase a two-way clarification."""
    pool = sorted(set(CITIES.values()) - {heard})
    best = max(pool, key=lambda c: difflib.SequenceMatcher(None, heard.lower(), c.lower()).ratio())
    r = difflib.SequenceMatcher(None, heard.lower(), best.lower()).ratio()
    return best if r >= 0.45 else None


def humanize_result(tool: str, result: Dict[str, Any], _depth: int = 0) -> str:
    """Ground an answer in an arbitrary (unseen-tool) result object, incl. lists of records."""
    bits = []
    for k, v in result.items():
        if k == "status":
            continue
        if isinstance(v, list) and v and isinstance(v[0], dict) and _depth < 2:
            first = humanize_result(tool, v[0], _depth + 1)
            more = f" (+{len(v) - 1} more)" if len(v) > 1 else ""
            bits.append(first + more)
            continue
        if isinstance(v, dict) and _depth < 2:
            bits.append(humanize_result(tool, v, _depth + 1))
            continue
        if isinstance(v, list):
            bits.append(", ".join(map(str, v[:3])))
            continue
        if k.endswith("_f"):
            bits.append(f"{v}°F")
        elif k.endswith("_c"):
            bits.append(f"{v}°C")
        elif k.endswith("_usd") or "price" in k or "cost" in k:
            bits.append(f"${v}")
        elif k.endswith("_id") or k == "id":
            bits.append(f"ref {v}")
        elif isinstance(v, str):
            bits.append(v)
        else:
            bits.append(f"{k.replace('_', ' ')} {v}")
    return ", ".join(b for b in bits if b)
