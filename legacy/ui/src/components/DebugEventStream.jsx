import { KNOWN_EVENT_TYPES } from "../lib/config.js";

/**
 * Proves the FastAPI -> WebSocket -> React path works end to end (Phase
 * 7B-1 step 4/5). Accepts every event type in core/events.py without
 * crashing -- and any event type it doesn't recognize too, since the
 * contract is "don't crash," not "reject unknown types." No specialized
 * per-event UI here on purpose; that's Phase 7B-2 (Conversation /
 * Deliberation / Dispatch panels).
 */
export default function DebugEventStream({ status, latestEvent, events }) {
  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/60 p-4 font-mono text-xs">
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-slate-400">
        Debug event stream (Phase 7B-1)
      </h2>

      <div className="mb-2">
        Connection: <span className="font-semibold">{status.toUpperCase()}</span>
      </div>

      <div className="mb-3">
        Latest Event:{" "}
        <span className="font-semibold">
          {latestEvent ? renderEventTypeLabel(latestEvent) : "(none yet)"}
        </span>
      </div>

      <div>
        <div className="mb-1 text-slate-500">Recent Events:</div>
        {events.length === 0 ? (
          <div className="text-slate-600">(none yet)</div>
        ) : (
          <ul className="max-h-64 space-y-1 overflow-y-auto">
            {events.map((evt, i) => (
              <li
                key={`${evt.event_type ?? "unknown"}-${evt.timestamp_ms ?? "?"}-${i}`}
                className="truncate rounded bg-slate-950/60 px-2 py-1"
                title={safeStringify(evt)}
              >
                {renderEventTypeLabel(evt)} — {safeStringify(evt)}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function renderEventTypeLabel(evt) {
  const type = evt?.event_type ?? "UnknownEvent";
  const known = KNOWN_EVENT_TYPES.includes(type);
  return known ? type : `${type} (unrecognized)`;
}

function safeStringify(value) {
  try {
    return JSON.stringify(value);
  } catch (err) {
    return "(unserializable event)";
  }
}
