import { useState } from "react";
import { buildWsUrl } from "./lib/config.js";
import { useEventSocket } from "./lib/useEventSocket.js";
import ConnectionBadge from "./components/ConnectionBadge.jsx";
import Panel from "./components/Panel.jsx";
import ConversationPanel from "./components/ConversationPanel.jsx";
import DeliberationPanel from "./components/DeliberationPanel.jsx";
import DispatchPanel from "./components/DispatchPanel.jsx";
import DebugEventStream from "./components/DebugEventStream.jsx";

/**
 * Phase 7B-2A wired the Conversation panel to the live event stream.
 * Phase 7B-2B adds Deliberation and Dispatch, both reading from the same
 * `events` stream -- no new WebSocket connections, no per-panel state
 * beyond what's folded from events (see DeliberationPanel.jsx /
 * DispatchPanel.jsx).
 */
export default function App() {
  const [channelKind, setChannelKind] = useState("replay");
  const [channelId, setChannelId] = useState("demo-1");
  const [activeWsUrl, setActiveWsUrl] = useState(null);

  const { status, latestEvent, events } = useEventSocket(activeWsUrl, { maxEvents: 500 });

  function handleConnect(e) {
    e.preventDefault();
    setActiveWsUrl(buildWsUrl(channelKind, channelId));
  }

  function handleDisconnect() {
    setActiveWsUrl(null);
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800 px-6 py-4">
        <h1 className="text-xl font-bold tracking-tight">Triage Line</h1>

        <form onSubmit={handleConnect} className="flex flex-wrap items-center gap-2 text-sm">
          <select
            value={channelKind}
            onChange={(e) => setChannelKind(e.target.value)}
            className="rounded border border-slate-700 bg-slate-900 px-2 py-1"
          >
            <option value="replay">Replay run</option>
            <option value="call">Live call</option>
          </select>
          <input
            type="text"
            value={channelId}
            onChange={(e) => setChannelId(e.target.value)}
            placeholder={channelKind === "replay" ? "run_id" : "call_id"}
            className="rounded border border-slate-700 bg-slate-900 px-2 py-1"
          />
          <button
            type="submit"
            className="rounded bg-sky-600 px-3 py-1 font-medium hover:bg-sky-500"
          >
            Connect
          </button>
          <button
            type="button"
            onClick={handleDisconnect}
            className="rounded border border-slate-700 px-3 py-1 font-medium hover:bg-slate-800"
          >
            Disconnect
          </button>
          <ConnectionBadge status={status} />
        </form>
      </header>

      <main className="mx-auto flex max-w-6xl flex-col gap-4 p-6">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Panel title="Conversation">
            <ConversationPanel events={events} />
          </Panel>
          <Panel title="Deliberation">
            <DeliberationPanel events={events} />
          </Panel>
        </div>

        <Panel title="Dispatch">
          <DispatchPanel events={events} />
        </Panel>

        <DebugEventStream status={status} latestEvent={latestEvent} events={events} />
      </main>
    </div>
  );
}
