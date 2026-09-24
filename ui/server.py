#!/usr/bin/env python3
"""TriageLine console — zero-dependency web UI over the official harness + scorer.

    python ui/server.py            # http://localhost:8080

GET  /api/scenarios            list scenarios (public + extra)
POST /api/run {path|scenario, agent, time_scale}  -> {trace, score}
"""

from __future__ import annotations

import glob
import json
import os
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from harness.runner import run_scenario  # noqa: E402
from harness.scorer import score_scenario  # noqa: E402
from run_local import load_agent_factory  # noqa: E402

AGENTS = {"triageline": "agent.agent:ParticipantAgent", "baseline": "agent.agent:BaselineAgent"}
_LOCK = threading.Lock()  # one scenario at a time: keeps virtual timing honest


def list_scenarios():
    out = []
    for p in sorted(glob.glob("scenarios/*.json")) + sorted(glob.glob("scenarios_extra/*.json")):
        try:
            d = json.load(open(p))
        except Exception:
            continue
        md = d.get("metadata", {})
        out.append({"path": p, "id": d.get("scenario_id"), "modality": md.get("modality"),
                    "difficulty": md.get("difficulty"), "description": md.get("description", ""),
                    "events": [{"t": e.get("timestamp_ms"), "type": e.get("event_type"),
                                "text": (e.get("payload") or {}).get("text")
                                or (e.get("payload") or {}).get("audio_ref")
                                or (e.get("payload") or {}).get("image_ref")} for e in d.get("events", [])]})
    return out


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=os.path.join(ROOT, "ui", "static"), **k)

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/api/scenarios"):
            return self._json(list_scenarios())
        return super().do_GET()

    def do_POST(self):
        if not self.path.startswith("/api/run"):
            return self._json({"error": "not found"}, 404)
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if req.get("scenario"):
                sc = req["scenario"]
            else:
                path = os.path.normpath(req.get("path", ""))
                if not (path.startswith("scenarios") and path.endswith(".json")):
                    return self._json({"error": "bad path"}, 400)
                sc = json.load(open(path))
            factory = load_agent_factory(AGENTS.get(req.get("agent"), AGENTS["triageline"]))
            ts = min(max(float(req.get("time_scale", 2)), 1.0), 8.0)
            with _LOCK:
                trace = run_scenario(sc, factory, time_scale=ts, verbose=False, tail_ms=4000)
            score = score_scenario(sc, trace) if sc.get("ground_truth") else None
            return self._json({"trace": trace, "score": score, "scenario_id": sc.get("scenario_id")})
        except Exception as e:
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print(f"TriageLine console on http://0.0.0.0:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
