"""Optional schema-validated slow-path planner. Gemini uses native function calling.

TRIAGELINE_LLM_PLANNER=auto (default) enables Gemini when its key is configured;
0 forces key-free rules, 1 enables the selected provider. The agent runs planning
in background tasks, never on the serial event consumer.
"""
from __future__ import annotations

import json
import logging
import math
import os
import urllib.request
from typing import Any, Dict, List, Optional

log = logging.getLogger("triageline.llm_planner")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODELS = {"gemini": "gemini-2.5-flash", "openai": "gpt-4o-mini", "groq": "llama-3.3-70b-versatile"}
SEED = 7
SYSTEM = (
    "Convert the spoken user request into the next executable tool call. Use ONLY declared tools. "
    "Resolve hesitations and self-corrections using the latest value. Never execute a negated or "
    "withdrawn action. Do not invent required arguments or result IDs. Use actual previous tool "
    "results for dependent steps. Return at most ONE call; subsequent steps run after its result. "
    "Spoken IDs have no spaces (P five two -> P52). Omit unknown optional arguments. "
    'If no complete tool call is possible, return no calls. JSON format: {"calls": [{"name": "tool", "args": {}}]}.')


def gemini_key() -> str:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""


def enabled() -> bool:
    setting = os.environ.get("TRIAGELINE_LLM_PLANNER", "auto").strip().lower()
    return setting == "1" or (setting == "auto" and bool(gemini_key()))


def config() -> Dict[str, Any]:
    prov = os.environ.get("TRIAGELINE_LLM_PROVIDER", "gemini").strip().lower()
    if prov not in DEFAULT_MODELS:
        raise ValueError("TRIAGELINE_LLM_PROVIDER must be gemini, groq, or openai")
    return {"provider": prov, "model": os.environ.get("TRIAGELINE_LLM_MODEL") or DEFAULT_MODELS[prov],
            "timeout": max(0.1, float(os.environ.get("TRIAGELINE_LLM_TIMEOUT_S", "8")))}


def _client(cfg):
    from openai import OpenAI
    if cfg["provider"] == "groq":
        return OpenAI(api_key=os.environ["GROQ_API_KEY"], base_url=GROQ_BASE_URL,
                      timeout=cfg["timeout"], max_retries=0)
    return OpenAI(timeout=cfg["timeout"], max_retries=0)


def _schema(tools: Dict[str, Any]) -> List[Dict[str, Any]]:
    def schema(spec):
        out = {k: v for k, v in spec.items() if k in ("type", "description", "enum", "items")}
        if spec.get("properties"):
            out["properties"] = {k: schema(v) for k, v in spec["properties"].items()}
            out["required"] = [k for k, v in spec["properties"].items() if v.get("required")]
        return out
    return [{"name": name, "description": spec.get("description", ""),
             "parameters": {"type": "object", "properties": {k: schema(v) for k, v in (spec.get("args") or {}).items()},
                            "required": [k for k, v in (spec.get("args") or {}).items() if v.get("required")]}}
            for name, spec in tools.items()]


def _value(value, spec):
    typ = spec.get("type", "string")
    if typ in ("integer", "number"):
        if isinstance(value, bool):
            raise ValueError("boolean is not a number")
        value = float(value)
        if not math.isfinite(value) or (typ == "integer" and not value.is_integer()):
            raise ValueError("invalid number")
        if typ == "integer":
            value = int(value)
    elif typ == "boolean":
        if isinstance(value, str) and value.lower() in ("true", "false"):
            value = value.lower() == "true"
        if not isinstance(value, bool):
            raise ValueError("invalid boolean")
    elif typ == "string" and not isinstance(value, str):
        raise ValueError("invalid string")
    elif typ == "object":
        value = _args(value, spec.get("properties", {}))
    elif typ == "array":
        if not isinstance(value, list):
            raise ValueError("invalid array")
        value = [_value(v, spec.get("items", {})) for v in value]
    if "enum" in spec and value not in spec["enum"]:
        raise ValueError("invalid enum")
    return value


def _args(values, props):
    if not isinstance(values, dict) or set(values) - set(props):
        raise ValueError("undeclared arguments")
    if any(v.get("required") and (k not in values or values[k] in (None, "")) for k, v in props.items()):
        raise ValueError("missing required arguments")
    return {k: _value(v, props[k]) for k, v in values.items() if v is not None}


def validate(calls: Any, tools: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Reject malformed, incomplete, unknown, nonfinite, or mistyped calls before execution."""
    good = []
    for call in calls if isinstance(calls, list) else []:
        if not isinstance(call, dict) or not isinstance(call.get("name"), str) or call["name"] not in tools:
            continue
        try:
            args = _args(call.get("args", {}), tools[call["name"]].get("args") or {})
        except (ValueError, TypeError, OverflowError):
            continue
        good.append({"name": call["name"], "args": args})
    return good


def _gemini_plan(cfg, payload, tools):
    key = gemini_key()
    if not key:
        raise ValueError("set GEMINI_API_KEY (or GOOGLE_API_KEY)")
    body = {"systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": json.dumps(payload)}]}],
            "tools": [{"functionDeclarations": _schema(tools)}],
            "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
            "generationConfig": {"temperature": 0, "maxOutputTokens": 2048,
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    from urllib.parse import quote
    req = urllib.request.Request(f"{GEMINI_BASE_URL}/models/{quote(cfg['model'], safe='')}:generateContent",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=cfg["timeout"]) as response:
        data = json.load(response)
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    return [{"name": p["functionCall"].get("name"), "args": p["functionCall"].get("args", {})}
            for p in parts if "functionCall" in p]


def plan(text: str, tools: Dict[str, Any], history: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Blocking provider request; caller MUST use a background task/thread. No automatic retries."""
    try:
        cfg = config()
        payload = {"previous_context": (history or [])[-3:], "transcript": text}
        if cfg["provider"] == "gemini":
            calls = _gemini_plan(cfg, payload, tools)
        else:
            payload["tools"] = _schema(tools)
            cli = _client(cfg)
            try:
                r = cli.chat.completions.create(model=cfg["model"], messages=[
                    {"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(payload)}],
                    temperature=0, seed=SEED, response_format={"type": "json_object"}, max_tokens=1000)
                calls = json.loads(r.choices[0].message.content or "{}").get("calls")
            finally:
                cli.close()
        return validate(calls, tools)[:1]
    except Exception as exc:
        # Provider exceptions may contain request headers: never log secrets or payloads.
        log.warning("LLM planner unavailable (%s); falling back to rules", type(exc).__name__)
        return []
