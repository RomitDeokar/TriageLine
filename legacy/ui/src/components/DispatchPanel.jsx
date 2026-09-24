import { useMemo } from "react";

/**
 * Phase 7B-2B: renders the dispatch/action state machine from the same
 * event stream `useEventSocket` already exposes (see App.jsx) -- no second
 * event source, no new WebSocket connection. Builds one record per
 * action_id by folding over `events` (oldest -> newest), matching the
 * pattern used by ConversationPanel.jsx / DeliberationPanel.jsx.
 *
 * Events handled (per docs/UI_SPEC.md section 3 + core/events.py):
 *   - ActionProposed  -> state = PROPOSED
 *   - ActionPending   -> state = PENDING_CONFIRMATION
 *   - ActionFinalized -> state = FINALIZED
 *   - ActionAborted   -> state = ABORTED (with reason)
 *
 * State is derived strictly from the most recent matching event for that
 * action_id -- never inferred or defaulted to FINALIZED.
 */
const STEPS = ["PROPOSED", "PENDING_CONFIRMATION", "FINALIZED"];

export default function DispatchPanel({ events }) {
  const actions = useMemo(() => buildActions(events), [events]);

  if (actions.length === 0) {
    return (
      <div className="text-sm text-slate-600">
        No dispatch action yet — connect and run a call or replay.
      </div>
    );
  }

  // Most recent action first.
  const ordered = [...actions].reverse();

  return (
    <div className="flex w-full flex-col gap-4 overflow-y-auto text-sm">
      {ordered.map((a) => (
        <ActionCard key={a.actionId} action={a} />
      ))}
    </div>
  );
}

function ActionCard({ action }) {
  const { actionId, actionType, state, reason, log } = action;
  const aborted = state === "ABORTED";

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-800/40 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          {actionType ?? "action"} · {actionId}
        </span>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-1">
        {STEPS.map((step, i) => (
          <StepChip
            key={step}
            label={step.replace("_", " ")}
            active={!aborted && state === step}
            done={!aborted && STEPS.indexOf(state) > i}
            aborted={aborted}
            isLast={i === STEPS.length - 1}
          />
        ))}
        {aborted && <StepChip label="ABORTED" active aborted isLast />}
      </div>

      {aborted && reason && (
        <div className="mt-2 rounded bg-red-500/10 px-2 py-1 text-xs text-red-300">
          aborted: {reason}
        </div>
      )}

      {log.length > 0 && (
        <div className="mt-3">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
            transition log
          </div>
          <ul className="mt-1 space-y-0.5 text-xs text-slate-400">
            {log.map((entry, i) => (
              <li key={i}>
                <span className="text-slate-600">{entry.ts ?? ""}</span>{" "}
                {entry.label}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function StepChip({ label, active, done, aborted, isLast }) {
  const color = aborted
    ? "bg-red-500/20 text-red-300 border-red-500/40"
    : active
    ? "bg-amber-400/20 text-amber-300 border-amber-400/50"
    : done
    ? "bg-emerald-400/20 text-emerald-300 border-emerald-400/40"
    : "bg-slate-800 text-slate-500 border-slate-700";

  return (
    <div className="flex items-center gap-1 whitespace-nowrap">
      <span className={`rounded border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${color}`}>
        {label}
      </span>
      {!isLast && <span className="text-slate-700">→</span>}
    </div>
  );
}

/**
 * `events` is most-recent-first and capped; iterate oldest -> newest so
 * state transitions apply in order. One record per action_id, state is
 * simply "whatever the last matching event said" -- e.g. a stale
 * ActionPending can never appear after a real ActionFinalized because we
 * process strictly in chronological order and always overwrite `state`.
 */
function buildActions(events) {
  const chronological = [...events].reverse();
  const byId = new Map();
  const order = [];

  function getOrCreate(actionId) {
    if (!byId.has(actionId)) {
      const record = {
        actionId,
        actionType: null,
        state: "PROPOSED",
        reason: null,
        log: [],
      };
      byId.set(actionId, record);
      order.push(actionId);
    }
    return byId.get(actionId);
  }

  function fmtTs(ts) {
    return ts != null ? String(ts) : null;
  }

  chronological.forEach((evt) => {
    switch (evt.event_type) {
      case "ActionProposed": {
        const rec = getOrCreate(evt.action_id);
        rec.actionType = evt.action_type ?? rec.actionType;
        rec.state = "PROPOSED";
        rec.log.push({ ts: fmtTs(evt.timestamp_ms), label: "PROPOSED" });
        break;
      }

      case "ActionPending": {
        const rec = getOrCreate(evt.action_id);
        rec.state = "PENDING_CONFIRMATION";
        rec.log.push({ ts: fmtTs(evt.timestamp_ms), label: "PENDING CONFIRMATION" });
        break;
      }

      case "ActionFinalized": {
        const rec = getOrCreate(evt.action_id);
        rec.state = "FINALIZED";
        rec.log.push({ ts: fmtTs(evt.timestamp_ms), label: "FINALIZED" });
        break;
      }

      case "ActionAborted": {
        const rec = getOrCreate(evt.action_id);
        rec.state = "ABORTED";
        rec.reason = evt.reason ?? null;
        rec.log.push({
          ts: fmtTs(evt.timestamp_ms),
          label: `ABORTED${evt.reason ? ` (${evt.reason})` : ""}`,
        });
        break;
      }

      default:
        // Conversation/deliberation/metrics events -- not this panel's concern.
        break;
    }
  });

  return order.map((id) => byId.get(id));
}
