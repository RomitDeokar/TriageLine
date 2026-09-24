import { useMemo } from "react";

/**
 * Phase 7B-2A: renders the live conversation from the same event stream
 * `useEventSocket` already exposes (see App.jsx) -- no second event
 * source, no new WebSocket connection. Builds a display list purely by
 * folding over `events` (oldest -> newest); does not track any parallel
 * state of its own.
 *
 * Events handled (per docs/UI_SPEC.md section 3 + core/events.py):
 *   - PartialTranscript  -> in-progress caller/agent line, replaced in
 *                           place (not appended) as more partials or the
 *                           matching FinalTranscript arrive.
 *   - FinalTranscript    -> finalized line; replaces any pending partial
 *                           for that speaker rather than duplicating it.
 *   - BackchannelSent    -> short inline agent acknowledgment. The event
 *                           has no `speaker` field (see core/events.py),
 *                           so it is rendered as agent-sourced per
 *                           core/dialogue/engine.py, which is the only
 *                           place this event is emitted.
 *   - BargeIn            -> inline "── barge-in ──" divider at the point
 *                           it occurred, per UI_SPEC.md 1.1.
 *
 * All other event types are ignored here -- they belong to the
 * Deliberation/Dispatch panels being built in later phases.
 */
export default function ConversationPanel({ events }) {
  const timeline = useMemo(() => buildTimeline(events), [events]);

  if (timeline.length === 0) {
    return (
      <div className="text-sm text-slate-600">
        No conversation yet — connect and run a call or replay.
      </div>
    );
  }

  return (
    <div className="flex w-full flex-col gap-3 overflow-y-auto text-sm">
      {timeline.map((item) => {
        if (item.kind === "divider") {
          return (
            <div
              key={item.key}
              className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-amber-400"
            >
              <span className="h-px flex-1 bg-amber-400/40" />
              barge-in
              <span className="h-px flex-1 bg-amber-400/40" />
            </div>
          );
        }

        if (item.kind === "backchannel") {
          return (
            <div key={item.key} className="ml-4 flex items-center gap-2 text-xs text-slate-500">
              <span className="italic">agent (backchannel):</span>
              <span className="italic">"{item.text}"</span>
            </div>
          );
        }

        const isCaller = item.speaker === "caller";
        return (
          <div key={item.key} className={isCaller ? "text-left" : "text-right"}>
            <div
              className={`text-[10px] font-semibold uppercase tracking-wider ${
                isCaller ? "text-amber-400" : "text-emerald-400"
              }`}
            >
              {item.speaker ?? "unknown"}
            </div>
            <p
              className={`mt-0.5 inline-block max-w-[85%] rounded-lg px-3 py-1.5 ${
                item.partial
                  ? "italic text-slate-400 opacity-70"
                  : "text-slate-100 bg-slate-800/70"
              }`}
            >
              "{item.text}"
            </p>
          </div>
        );
      })}
    </div>
  );
}

/**
 * `events` (from useEventSocket) is most-recent-first and capped; iterate
 * oldest -> newest to reconstruct conversation order. A partial line for
 * a given speaker is tracked separately and replaced in place -- by its
 * FinalTranscript, or by a newer PartialTranscript for the same speaker --
 * rather than appended as a new row, matching UI_SPEC.md 1.1's "replaced
 * in place (no duplicate lines) when finalized."
 */
function buildTimeline(events) {
  const chronological = [...events].reverse();
  const timeline = [];
  // One pending partial slot per speaker, referenced by index into `timeline`.
  const pendingPartialIndex = { caller: null, agent: null };

  chronological.forEach((evt, i) => {
    const key = `${evt.event_type ?? "unknown"}-${evt.timestamp_ms ?? "t"}-${i}`;

    switch (evt.event_type) {
      case "PartialTranscript": {
        const speaker = evt.speaker;
        const existingIndex = pendingPartialIndex[speaker];
        if (existingIndex != null && timeline[existingIndex]?.partial) {
          timeline[existingIndex] = { ...timeline[existingIndex], text: evt.text, key };
        } else {
          timeline.push({ kind: "line", speaker, text: evt.text, partial: true, key });
          pendingPartialIndex[speaker] = timeline.length - 1;
        }
        break;
      }

      case "FinalTranscript": {
        const speaker = evt.speaker;
        const existingIndex = pendingPartialIndex[speaker];
        if (existingIndex != null && timeline[existingIndex]?.partial) {
          timeline[existingIndex] = { kind: "line", speaker, text: evt.text, partial: false, key };
        } else {
          timeline.push({ kind: "line", speaker, text: evt.text, partial: false, key });
        }
        pendingPartialIndex[speaker] = null;
        break;
      }

      case "BackchannelSent": {
        timeline.push({ kind: "backchannel", text: evt.text, key });
        break;
      }

      case "BargeIn": {
        timeline.push({ kind: "divider", key });
        break;
      }

      default:
        // Deliberation/dispatch/metrics events -- not this panel's concern.
        break;
    }
  });

  return timeline;
}
