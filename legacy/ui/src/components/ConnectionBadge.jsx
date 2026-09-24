const STATUS_STYLES = {
  idle: { dot: "bg-slate-500", label: "NOT CONNECTED" },
  connecting: { dot: "bg-amber-400 animate-pulse", label: "CONNECTING" },
  connected: { dot: "bg-emerald-400", label: "CONNECTED" },
  disconnected: { dot: "bg-red-500", label: "DISCONNECTED" },
  error: { dot: "bg-red-500", label: "ERROR" },
};

export default function ConnectionBadge({ status }) {
  const style = STATUS_STYLES[status] ?? STATUS_STYLES.idle;
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-slate-700 bg-slate-900 px-3 py-1 text-sm font-medium">
      <span className={`h-2.5 w-2.5 rounded-full ${style.dot}`} />
      {style.label}
    </span>
  );
}
