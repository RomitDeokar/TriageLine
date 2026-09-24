export default function Panel({ title, children }) {
  return (
    <section className="flex min-w-0 min-h-[220px] flex-col rounded-lg border border-stone-800 bg-stone-900/60 p-4 sm:p-5">
      <h2 className="mb-4 border-b border-stone-800 pb-3 text-xs font-semibold uppercase tracking-[0.16em] text-stone-400">
        {title}
      </h2>
      <div className="flex min-w-0 flex-1 items-start text-sm text-stone-400">
        {children}
      </div>
    </section>
  );
}
