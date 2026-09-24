import { useEffect, useState } from "react";
import { buildWsUrl } from "./lib/config.js";
import { useEventSocket } from "./lib/useEventSocket.js";
import ConnectionBadge from "./components/ConnectionBadge.jsx";
import Panel from "./components/Panel.jsx";
import ConversationPanel from "./components/ConversationPanel.jsx";
import DeliberationPanel from "./components/DeliberationPanel.jsx";
import DispatchPanel from "./components/DispatchPanel.jsx";
import MetricsPanel from "./components/MetricsPanel.jsx";
import DebugEventStream from "./components/DebugEventStream.jsx";

export default function App() {
  const [channelKind, setChannelKind] = useState("replay");
  const [channelId, setChannelId] = useState("demo-1");
  const [activeChannel, setActiveChannel] = useState(null);
  const [scenarios, setScenarios] = useState([]);
  const [scenarioId, setScenarioId] = useState("normal_tow");
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState("");
  const { status, latestEvent, events } = useEventSocket(activeChannel?.url, { maxEvents: 500 });

  useEffect(() => {
    fetch("/demo/scenarios")
      .then((response) => {
        if (!response.ok) throw new Error("Unable to load replay scenarios");
        return response.json();
      })
      .then((data) => setScenarios(data.scenarios ?? []))
      .catch(() => setScenarios([]));
  }, []);

  function handleConnect(e) {
    e.preventDefault();
    const url = buildWsUrl(channelKind, channelId);
    if (url) {
      setRunError("");
      setActiveChannel({ url, kind: channelKind, id: channelId.trim() });
    }
  }

  async function handleRun() {
    if (status !== "connected" || activeChannel?.kind !== "replay" || running) return;
    setRunError("");
    setRunning(true);
    try {
      const response = await fetch(
        `/demo/run/${encodeURIComponent(activeChannel.id)}/${encodeURIComponent(scenarioId)}`,
        { method: "POST" },
      );
      if (!response.ok) {
        const body = await response.json();
        throw new Error(body.error || `Replay failed (${response.status})`);
      }
    } catch (error) {
      setRunError(error.message || "Replay request failed");
    } finally {
      setRunning(false);
    }
  }

  const interrupted = events.some((event) => event.event_type === "BargeIn");
  const callActivity = activeChannel?.kind === "call" && events.some((event) =>
    ["TurnStarted", "PartialTranscript", "FinalTranscript", "BackchannelSent", "BargeIn"].includes(event.event_type),
  );

  return (
    <div className="min-h-screen bg-stone-950 text-stone-100">
      <header className="border-b border-stone-800 bg-stone-900/70 px-4 py-5 sm:px-7">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.24em] text-amber-500">Operations / Dispatch</p>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight">Triage Line</h1>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <ConnectionBadge status={status} />
            <span className="rounded-full border border-stone-700 px-3 py-1.5 text-stone-300">
              {callActivity ? "CALL ACTIVITY" : activeChannel?.kind === "call" ? "AWAITING CALL" : events.length ? "REPLAY EVENTS" : "NO ACTIVE CALL"}
            </span>
            {interrupted && <span role="status" className="rounded-full border border-amber-700 bg-amber-950/50 px-3 py-1.5 text-amber-300">INTERRUPTION RECORDED</span>}
          </div>
        </div>
      </header>

      <main className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-5 sm:px-7 sm:py-7">
        <div className="rounded-lg border border-stone-800 bg-stone-900/60 p-4">
          <form onSubmit={handleConnect} className="flex flex-wrap items-end gap-3 text-sm">
            <label className="flex flex-col gap-1 text-stone-400">Channel
              <select value={channelKind} onChange={(e) => setChannelKind(e.target.value)} className="rounded border border-stone-700 bg-stone-950 px-3 py-2 text-stone-100">
                <option value="replay">Replay run</option>
                <option value="call">Call monitor</option>
              </select>
            </label>
            <label className="flex min-w-[150px] flex-col gap-1 text-stone-400">{channelKind === "replay" ? "Run ID" : "Call ID"}
              <input type="text" value={channelId} onChange={(e) => setChannelId(e.target.value)} required className="rounded border border-stone-700 bg-stone-950 px-3 py-2 text-stone-100" />
            </label>
            <button type="submit" className="rounded bg-amber-600 px-4 py-2 font-semibold text-stone-950 hover:bg-amber-500">Connect</button>
            <button type="button" onClick={() => setActiveChannel(null)} className="rounded border border-stone-700 px-4 py-2 hover:bg-stone-800">Disconnect</button>
            {activeChannel?.kind === "replay" && (
              <>
                <label className="flex flex-col gap-1 text-stone-400">Scenario
                  <select value={scenarioId} onChange={(e) => setScenarioId(e.target.value)} className="max-w-[220px] rounded border border-stone-700 bg-stone-950 px-3 py-2 text-stone-100">
                    {scenarios.map((id) => <option key={id} value={id}>{id.replaceAll("_", " ")}</option>)}
                  </select>
                </label>
                <button type="button" onClick={handleRun} disabled={status !== "connected" || running || !scenarios.includes(scenarioId)} className="rounded border border-amber-700 px-4 py-2 font-medium text-amber-300 hover:bg-amber-950 disabled:cursor-not-allowed disabled:opacity-40">
                  {running ? "Running…" : "Run replay"}
                </button>
              </>
            )}
          </form>
          {runError && <p role="alert" className="mt-3 text-sm text-red-300">{runError}</p>}
          {activeChannel?.kind === "call" && <p className="mt-3 text-xs text-stone-400">Call monitor is read-only. A live call producer is not connected in this build.</p>}
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Panel title="Conversation"><ConversationPanel events={events} /></Panel>
          <Panel title="Deliberation"><DeliberationPanel events={events} /></Panel>
          <Panel title="Dispatch state"><DispatchPanel events={events} /></Panel>
        </div>
        <Panel title="Measurements"><MetricsPanel events={events} /></Panel>
        <DebugEventStream status={status} latestEvent={latestEvent} events={events} />
      </main>
    </div>
  );
}
