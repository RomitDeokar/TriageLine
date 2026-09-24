/**
 * Builds the WebSocket URL for one of the two channels api/app.py exposes
 * (see docs/UI_SPEC.md section 3):
 *   - "call"   -> /ws/call/{id}
 *   - "replay" -> /ws/replay/{id}
 *
 * Always same-origin: in `npm run dev` the Vite dev server proxies /ws to
 * the FastAPI backend (see vite.config.js), and a production deployment is
 * expected to serve both behind one origin too. No backend host is
 * hardcoded into application code.
 */
export function buildWsUrl(kind, id) {
  if (kind !== "call" && kind !== "replay") {
    throw new Error(`Unknown channel kind: ${kind}`);
  }
  const trimmedId = (id || "").trim();
  if (!trimmedId) return null;

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/${kind}/${encodeURIComponent(trimmedId)}`;
}

/** All event types core/events.py can currently emit (kept for reference /
 * validation in the debug panel — the UI must not crash on any of these,
 * per Phase 7B-1 step 4, and must not silently swallow an event type this
 * list doesn't yet know about either). */
export const KNOWN_EVENT_TYPES = [
  "TurnStarted",
  "PartialTranscript",
  "FinalTranscript",
  "BargeIn",
  "BackchannelSent",
  "DeliberationStarted",
  "DeliberationResolved",
  "SelfCritiqueFailed",
  "ReDeliberationTriggered",
  "ActionProposed",
  "ActionPending",
  "ActionFinalized",
  "ActionAborted",
  "MetricsTick",
];
