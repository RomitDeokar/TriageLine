# Free API keys: how to run TriageLine at no cost

TriageLine has **no LLM in the agent**. Understanding and tool calls are handled by the rule-based
`ParticipantAgent`. Only three things need hosted services:

| Need | Free option (checked 2026-09) | Env var(s) |
|---|---|---|
| Realtime transport (rooms, agent dispatch) | **LiveKit Cloud "Build" plan**: no card, 1,000 agent-session minutes + 5,000 WebRTC minutes per month, up to 5 concurrent agent sessions | `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` |
| Speech-to-text | **Groq** free plan: `whisper-large-v3-turbo`, OpenAI-compatible, about 20 req/min and 2,000 req/day<br>or **Deepgram**: $200 free credit at signup (`nova-3` streaming) | `GROQ_API_KEY` or `DEEPGRAM_API_KEY` |
| Text-to-speech | **Deepgram** Aura-2 (same $200 credit, about 6M characters) | `DEEPGRAM_API_KEY` |
| *Optional:* official LLM judge | The official evaluators hard-code **`gpt-4o`** through the OpenAI SDK. There is **no free gpt-4o**: GitHub Models was retired in July 2026. Use a paid OpenAI key (a full 100-example judge pass costs well under $1), or run without the judge (`--no-llm-judge`, exact match = lower bound). | `OPENAI_API_KEY` (optional `OPENAI_BASE_URL`) |

> Groq TTS (Orpheus) is **not** supported here. It returns WAV only and caps input at 200 characters,
> which breaks streamed agent speech. Use Deepgram for TTS.

## 1. LiveKit Cloud (required for the live benchmark and the demo)
1. Sign up at <https://cloud.livekit.io>. GitHub or Google login works, and no card is needed.
2. Create a project. Open **Settings → API Keys → Create key**.
3. Copy the **WebSocket URL** (`wss://<project>.livekit.cloud`), the **API Key** and the **API Secret**.
4. You don't need dispatch rules: the agent registers without `agent_name`, so it is auto-dispatched to
   every new room.

## 2. Groq (free STT)
1. Sign in at <https://console.groq.com>, then **API Keys → Create API Key**.
2. Copy the key (it starts with `gsk_`). Set `TRIAGELINE_STT_PROVIDER=groq`.
3. Free-plan limits apply per organisation. A 100-example FDB run makes about 100–300 STT requests, so it
   fits in one day's quota.

## 3. Deepgram (free TTS, or STT)
1. Sign up at <https://console.deepgram.com>. The $200 credit is added automatically.
2. Open **API Keys → Create a New API Key** with the Member role, and copy it.
3. Set `TRIAGELINE_TTS_PROVIDER=deepgram` (and optionally `TRIAGELINE_STT_PROVIDER=deepgram`).

## 4. Put the keys in place
```bash
cp livekit_agent/.env.example livekit_agent/.env.local     # gitignored, never committed
# edit livekit_agent/.env.local:
LIVEKIT_URL=wss://<project>.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
TRIAGELINE_STT_PROVIDER=groq
GROQ_API_KEY=gsk_...
TRIAGELINE_TTS_PROVIDER=deepgram
DEEPGRAM_API_KEY=...
# OPENAI_API_KEY=sk-...        # only if you want the official gpt-4o judge (paid)
```

## 5. Run
```bash
# no keys at all: official data + official evaluators, text replay (diagnostic)
./run_fdb_v3.sh --offline-text

# fully free live run (LiveKit + Groq + Deepgram), judge off
./run_fdb_v3.sh --no-llm-judge --limit 5      # smoke run first
./run_fdb_v3.sh --no-llm-judge                # all 100 examples

# the scored configuration (adds the official gpt-4o judge)
./run_fdb_v3.sh

# talk to the agents yourself
python livekit_agent/cascaded_agent.py console          # FDB tool agent, local mic/speaker
python livekit_agent/triage_livekit_agent.py console    # Triage Line extension
```
To join from a browser, open <https://agents-playground.livekit.io>, connect it to your project, and the
running agent (`... dev`) joins the room.

## Notes
- The official runner transcribes the agent's audio with NVIDIA NeMo `parakeet-tdt-0.6b-v2`, running
  locally. It is free but large (it pulls in torch). A GPU is recommended; CPU works, just slowly.
- Keys are only read from the environment or from `livekit_agent/.env.local`. `run_fdb_v3.sh` writes them
  to `v3/.env.local` (mode 600, inside the gitignored clone) because the official scripts read that file.
  Keys are never written to `results/`.
- Free tiers change. If a limit is hit, the run log shows the provider's error; re-run with `--limit`, or
  switch providers through `TRIAGELINE_STT_PROVIDER` / `TRIAGELINE_TTS_PROVIDER`.
