import { useMemo } from "react";

const METRICS = [
  { key: "barge_in_latency_ms", label: "Barge-in latency", unit: "ms" },
  { key: "time_to_decision_ms", label: "Time to decision", unit: "ms" },
];

/** MetricsTick is the only source of these values; no event-count estimates. */
export default function MetricsPanel({ events }) {
  const latest = useMemo(
    () => events.find((event) => event.event_type === "MetricsTick"),
    [events],
  );
  const available = METRICS.filter(({ key }) =>
    latest && typeof latest[key] === "number" && Number.isFinite(latest[key]),
  );

  if (!available.length) {
    return (
      <p className="text-sm text-slate-400">
        No measurements received yet. Metrics appear when a MetricsTick is emitted; replay runs may not emit one.
      </p>
    );
  }

  return (
    <div className="grid w-full gap-3 sm:grid-cols-2" aria-live="polite">
      {available.map(({ key, label, unit }) => (
        <div key={key} className="rounded-md border border-stone-700 bg-stone-900/70 px-4 py-3">
          <div className="text-xs uppercase tracking-wider text-stone-400">{label}</div>
          <div className="mt-1 font-mono text-2xl font-semibold tabular-nums text-stone-100">
            {latest[key]} <span className="text-sm font-normal text-stone-400">{unit}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
