export default function Panel({ title, children }) {
  return (
    <section className="flex min-h-[220px] flex-col rounded-lg border border-slate-800 bg-slate-900/60 p-4">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
        {title}
      </h2>
      <div className="flex flex-1 items-center justify-center text-sm text-slate-600">
        {children ?? "Nothing yet — wired up in Phase 7B-2."}
      </div>
    </section>
  );
}
