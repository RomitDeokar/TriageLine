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
_CAP_AFTER_PREP = re.compile(r"\b(?:to|in|for|at|from)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)")

# self-repair / correction markers — the value AFTER the last marker wins
REPAIR_MARKERS = re.compile(
    r"\b(?:no[, \-]+wait|wait[, \-]+no|no[, ]+sorry|sorry[, ]+i mean|i mean|actually|"
    r"rather|scratch that|make (?:it|that)|change (?:it|that) to|instead|not\s+\w+[, ]+but|no[,.]\s)",
    re.I)
RETRACTION = re.compile(
    r"\b(never ?mind|forget (?:it|about it|that)|cancel (?:that|it|everything)|don'?t bother|"
    r"stop(?: that)?|no need|skip it|call it off)\b", re.I)
INTENT_SWITCH = re.compile(r"\b(forget the \w+|instead|different question|something else)\b", re.I)
GREETING = re.compile(r"\b(hi|hello|hey|what can you (?:do|help)|who are you|help me with)\b", re.I)
WEEKDAYS = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
DATE_RE = re.compile(
    r"\b(today|tonight|tomorrow|day after tomorrow|this weekend|next week|(?:next |this )?" + WEEKDAYS +
    r"|\d{4}-\d{2}-\d{2}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2}(?:st|nd|rd|th)?)\b", re.I)
TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)", re.I)
NAME_RE = re.compile(r"\b(?:for|passenger|name is|named|i am|i'm|under)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)")
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
def cities_in(text: str) -> List[Tuple[int, str]]:
    out = [(m.start(), CITIES[m.group(1).lower()]) for m in _CITY_RE.finditer(text or "")]
    if not out:
        for m in _CAP_AFTER_PREP.finditer(text or ""):
            cand = m.group(1)
            if cand.lower() not in STOP and not re.match(WEEKDAYS, cand, re.I):
                out.append((m.start(1), cand))
    return out


def extract_city(text: str) -> Optional[str]:
    found = cities_in(text)
    if not found:
        return None
    last_marker = max((m.end() for m in REPAIR_MARKERS.finditer(text)), default=-1)
    after = [c for pos, c in found if pos >= last_marker]
    return after[-1] if after else found[-1][1]


def extract_date(text: str) -> Optional[str]:
    m = list(DATE_RE.finditer(text or ""))
    return m[-1].group(1) if m else None


def extract_time(text: str) -> Optional[int]:
    m = TIME_RE.search(text or "")
    if not m:
        return None
    h = int(m.group(1)) % 12
    if m.group(3).lower().startswith("p"):
        h += 12
    return h


def extract_name(text: str) -> Optional[str]:
    for m in NAME_RE.finditer(text or ""):
        cand = m.group(1)
        if cand.lower() in CITIES or re.match(WEEKDAYS, cand, re.I) or cand.lower() in STOP:
            continue
        return cand
    return None


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


def is_smalltalk(text: str) -> bool:
    return bool(GREETING.search(text or "")) and len(tokens(text)) <= 6


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
        low = text.lower()
        for e in enum:
            if re.search(r"\b" + re.escape(str(e).lower()) + r"\b", low):
                return e
        if lname == "severity":
            return severity_of(text) if severity_of(text) in enum else enum[0]
        if lname in ("model", "device_model") and ctx.get("device_model") in enum:
            return ctx["device_model"]
        return None
    if typ == "number":
        m = NUMBER_RE.search(text)
        return float(m.group()) if m else None
    if typ == "boolean":
        return None
    if typ == "array":
        return None
    # strings: match by semantic role of the arg name
    if any(k in lname for k in ("city", "destination", "location", "place", "origin", "town")):
        return ctx.get("destination") or extract_city(text)
    if "date" in lname or "day" in lname or "when" in lname:
        return ctx.get("date") or extract_date(text)
    if "passenger" in lname or lname in ("name", "customer", "guest", "full_name"):
        return ctx.get("passenger_name") or extract_name(text)
    if lname.endswith("_id") or lname == "id":
        return ctx.get(lname)
    if lname in ("model", "device", "device_model"):
        return ctx.get("device_model")
    if lname in ("query", "summary", "question", "text", "message", "description", "issue"):
        return norm(text)
    return None


def build_args(spec: Dict[str, Any], text: str, ctx: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Fill a tool call from its schema. Returns (args, missing_required)."""
    args, missing = {}, []
    for name, aspec in (spec.get("args") or {}).items():
        v = _arg_for(name, aspec, text, ctx)
        if aspec.get("type") == "object":
            req = [s for s, ss in (aspec.get("properties") or {}).items() if ss.get("required")]
            gaps = [s for s in req if v.get(s) in (None, "")]
            if gaps and aspec.get("required"):
                missing += [f"{name}.{g}" for g in gaps]
            if v and not gaps:
                args[name] = v
            continue
        if v not in (None, ""):
            args[name] = v
        elif aspec.get("required"):
            missing.append(name)
    return args, missing


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
