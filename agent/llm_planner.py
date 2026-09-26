"""agent/llm_planner.py — optional hosted-LLM slow-path planner (C6).

Hybrid design: the rule-based parser (agent/nlu.py) stays the fast path, the validator and the
fallback. This planner is consulted only when the rules cannot produce a complete call (no tool
ranked, or required args missing). Its output is JSON validated against the tool manifest
before anything is issued, so it can never call an undeclared tool or invent argument names.

    TRIAGELINE_LLM_PLANNER = 1              enable (default 0 = rules only)
    TRIAGELINE_LLM_PROVIDER = openai|groq   (default openai; uses OPENAI_API_KEY[/OPENAI_BASE_URL]
                                             or GROQ_API_KEY)
    TRIAGELINE_LLM_MODEL                    default gpt-4o-mini (openai) / llama-3.3-70b-versatile (groq)
    TRIAGELINE_LLM_TIMEOUT_S                default 4.0

Deterministic settings: temperature 0, fixed seed 7, JSON response format.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

log = logging.getLogger("triageline.llm_planner")

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODELS = {"openai": "gpt-4o-mini", "groq": "llama-3.3-70b-versatile"}
SEED = 7

SYSTEM = ("You convert one spoken user request (an ASR transcript that may contain disfluencies, "
          "self-corrections and misrecognised words) into tool calls. Use ONLY the tools given. "
          "When the user corrects themselves, use the corrected value. Spoken ids are written without "
          "spaces or dashes (\"P five two\" -> \"P52\"). Do not ask questions: call with the arguments "
          "that are known and omit unknown optional ones. If no tool applies return an empty list. "
          'Reply with JSON only: {"calls": [{"name": "<tool>", "args": {...}}]}')


def enabled() -> bool:
    return os.environ.get("TRIAGELINE_LLM_PLANNER", "0") == "1"


def config() -> Dict[str, Any]:
    prov = os.environ.get("TRIAGELINE_LLM_PROVIDER", "openai").lower()
    return {"provider": prov, "model": os.environ.get("TRIAGELINE_LLM_MODEL", DEFAULT_MODELS.get(prov, "gpt-4o-mini")),
            "timeout": float(os.environ.get("TRIAGELINE_LLM_TIMEOUT_S", "4.0"))}


def _client(cfg):
    from openai import OpenAI
    if cfg["provider"] == "groq":
        return OpenAI(api_key=os.environ["GROQ_API_KEY"], base_url=GROQ_BASE_URL, timeout=cfg["timeout"])
    return OpenAI(timeout=cfg["timeout"])


def _schema(tools: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for name, spec in tools.items():
        out.append({"name": name, "description": spec.get("description", ""),
                    "args": {k: {"type": v.get("type", "string"), "required": bool(v.get("required")),
                                 "description": v.get("description", "")}
                             for k, v in (spec.get("args") or {}).items()}})
    return out


def validate(calls: Any, tools: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Keep only calls to declared tools with declared arg names, coerced to the schema types."""
    good = []
    for c in calls if isinstance(calls, list) else []:
        if not isinstance(c, dict) or c.get("name") not in tools:
            continue
        props = tools[c["name"]].get("args") or {}
        args = {}
        for k, v in (c.get("args") or {}).items():
            if k not in props or v in (None, ""):
                continue
            t = props[k].get("type")
            try:
                if t == "integer":
                    v = int(float(v))
                elif t == "number":
                    v = float(v)
                elif t == "boolean" and isinstance(v, str):
                    v = v.strip().lower() in ("true", "yes", "1")
            except (TypeError, ValueError):
                continue
            args[k] = v
        good.append({"name": c["name"], "args": args})
    return good


def plan(text: str, tools: Dict[str, Any], history: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Blocking call (run it in a thread). Returns validated calls, [] on any failure."""
    cfg = config()
    try:
        cli = _client(cfg)
        msgs = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps({"tools": _schema(tools),
                                                        "previous_user_turns": (history or [])[-3:],
                                                        "transcript": text})}]
        r = cli.chat.completions.create(model=cfg["model"], messages=msgs, temperature=0, seed=SEED,
                                        response_format={"type": "json_object"}, max_tokens=300)
        data = json.loads(r.choices[0].message.content or "{}")
        return validate(data.get("calls"), tools)
    except Exception as e:  # noqa: BLE001 - the planner is advisory; rules remain the fallback
        log.warning("llm planner unavailable: %s", e)
        return []
