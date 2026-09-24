import { useMemo } from "react";

/**
 * Phase 7B-2B: renders the live deliberation record from the same event
 * stream `useEventSocket` already exposes (see App.jsx) -- no second event
 * source, no new WebSocket connection. Builds a per-decision record purely
 * by folding over `events` (oldest -> newest), matching the pattern used
 * by ConversationPanel.jsx.
 *
 * Events handled (per docs/UI_SPEC.md section 3 + core/events.py):
 *   - DeliberationStarted     -> a decision has begun; shows "deliberating"
 *                                 until it resolves.
 *   - DeliberationResolved    -> populates known/uncertain/options/chosen/
 *                                 confidence for that decision_id. Only
 *                                 fields actually present on the event are
 *                                 shown -- nothing is invented.
 *   - SelfCritiqueFailed      -> attached to the decision it applies to;
 *                                 shown as a constraint-check warning.
 *   - ReDeliberationTriggered -> starts a *new* decision record that
 *                                 supersedes an earlier one. The earlier
 *                                 record is kept (not overwritten) and
 *                                 flagged as superseded, per UI_SPEC.md's
 *                                 "preserve the event history" note. The
 *                                 record is visually marked with a banner
 *                                 so the re-deliberation is obvious.
 *
 * All other event types are ignored here -- they belong to the
 * Conversation/Dispatch panels.
 */
export default function DeliberationPanel({ events }) {
  const decisions = useMemo(() => buildDecisions(events), [events]);

  if (decisions.length === 0) {
    return (
      <div className="text-sm text-slate-600">
        No deliberation yet — connect and run a call or replay.
      </div>
    );
  }

  // Most recent decision first, so the active/latest reasoning is what a
  // judge sees without scrolling; older (superseded) records stay below.
  const ordered = [...decisions].reverse();

  return (
    <div className="flex w-full flex-col gap-3 overflow-y-auto text-sm">
      {ordered.map((d) => (
        <DecisionCard key={d.decisionId} decision={d} />
      ))}
    </div>
  );
}

function DecisionCard({ decision }) {
  const {
    decisionId,
    intent,
    status,
    known,
    uncertain,
    options,
    chosen,
    confidence,
    critiqueFailures,
    isReDeliberation,
    reDeliberationReason,
    supersededBy,
  } = decision;

  return (
    <div
      className={`rounded-lg border p-3 ${
        isReDeliberation
          ? "border-amber-500/70 bg-amber-500/5"
          : "border-slate-800 bg-slate-800/40"
      } ${supersededBy ? "opacity-50" : ""}`}
    >
      {isReDeliberation && (
        <div className="mb-2 rounded bg-amber-500/20 px-2 py-1 text-xs font-semibold uppercase tracking-wider text-amber-300">
          re-deliberating: {reDeliberationReason || "new info"}
        </div>
      )}

      {supersededBy && (
        <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
          superseded by a later decision
        </div>
      )}

      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          {intent ?? "decision"}
        </span>
        <StatusBadge status={status} />
      </div>

      {known && Object.keys(known).length > 0 && (
        <Field label="Known">
          <ul className="list-inside list-disc">
            {Object.entries(known).map(([k, v]) => (
              <li key={k}>
                <span className="text-slate-400">{k}:</span> {String(v)}
              </li>
            ))}
          </ul>
        </Field>
      )}

      {uncertain && uncertain.length > 0 && (
        <Field label="Uncertain">
          <ul className="list-inside list-disc">
            {uncertain.map((u, i) => (
              <li key={i}>{String(u)}</li>
            ))}
          </ul>
        </Field>
      )}

      {options && options.length > 0 && (
        <Field label="Options considered">
          <ul className="list-inside list-disc">
            {options.map((o, i) => (
              <li key={i} className={o === chosen ? "font-semibold text-emerald-400" : ""}>
                {String(o)}
                {o === chosen ? " (chosen)" : ""}
              </li>
            ))}
          </ul>
        </Field>
      )}

      {chosen && (!options || !options.includes(chosen)) && (
        <Field label="Chosen">{chosen}</Field>
      )}

      {confidence != null && (
        <Field label="Confidence">{Number(confidence).toFixed(2)}</Field>
      )}

      {critiqueFailures.length > 0 && (
        <div className="mt-2 flex flex-col gap-1">
          {critiqueFailures.map((c, i) => (
            <div
              key={i}
              className="rounded bg-red-500/10 px-2 py-1 text-xs text-red-300"
            >
              ⚠ self-critique failed: {c.failedConstraint} → fallback: {c.fallbackAction}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Field({ label, children }) {
  return (
    <div className="mt-2">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
        {label}
      </div>
      <div className="text-slate-200">{children}</div>
    </div>
  );
}

function StatusBadge({ status }) {
  const styles = {
    deliberating: "bg-amber-400/20 text-amber-300",
    resolved: "bg-emerald-400/20 text-emerald-300",
  };
  return (
    <span
      className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${
        styles[status] ?? "bg-slate-700 text-slate-300"
      }`}
    >
      {status}
    </span>
  );
}

/**
 * `events` (from useEventSocket) is most-recent-first and capped; iterate
 * oldest -> newest to reconstruct decision order. One record per
 * decision_id, keyed in a map for O(1) updates but returned as an array in
 * insertion order so re-deliberations (new decision_ids) render as
 * separate, additional cards rather than replacing the old one.
 */
function buildDecisions(events) {
  const chronological = [...events].reverse();
  const byId = new Map();
  const order = [];

  function getOrCreate(decisionId) {
    if (!byId.has(decisionId)) {
      const record = {
        decisionId,
        intent: null,
        status: "deliberating",
        known: null,
        uncertain: null,
        options: null,
        chosen: null,
        confidence: null,
        critiqueFailures: [],
        isReDeliberation: false,
        reDeliberationReason: null,
        supersededBy: null,
      };
      byId.set(decisionId, record);
      order.push(decisionId);
    }
    return byId.get(decisionId);
  }

  chronological.forEach((evt) => {
    switch (evt.event_type) {
      case "DeliberationStarted": {
        const rec = getOrCreate(evt.decision_id);
        rec.intent = evt.intent ?? rec.intent;
        rec.status = "deliberating";
        break;
      }

      case "DeliberationResolved": {
        const rec = getOrCreate(evt.decision_id);
        rec.status = "resolved";
        rec.known = evt.known ?? rec.known;
        rec.uncertain = evt.uncertain ?? rec.uncertain;
        rec.options = evt.options ?? rec.options;
        rec.chosen = evt.chosen ?? rec.chosen;
        rec.confidence = evt.confidence ?? rec.confidence;
        break;
      }

      case "SelfCritiqueFailed": {
        const rec = getOrCreate(evt.decision_id);
        rec.critiqueFailures.push({
          failedConstraint: evt.failed_constraint,
          fallbackAction: evt.fallback_action,
        });
        break;
      }

      case "ReDeliberationTriggered": {
        // The new decision (decision_id) is a fresh record flagged as a
        // re-deliberation; the old one (supersedes_id) is marked
        // superseded but kept in the list, not overwritten.
        const rec = getOrCreate(evt.decision_id);
        rec.isReDeliberation = true;
        rec.reDeliberationReason = evt.reason ?? null;
        rec.status = "deliberating";

        if (evt.supersedes_id && byId.has(evt.supersedes_id)) {
          byId.get(evt.supersedes_id).supersededBy = evt.decision_id;
        }
        break;
      }

      default:
        // Conversation/dispatch/metrics events -- not this panel's concern.
        break;
    }
  });

  return order.map((id) => byId.get(id));
}
