# Voice Translator — Real-Time Voice Translation Web App

A production-quality **real-time voice translation calling application**. Two users can have a normal voice conversation while each hears the other in their own language — bidirectionally, simultaneously, and with sub-second latency targets.

```
User A (English speaker) ──┐
                            ├─→ LiveKit room ─→ Translation Agent ─→ OpenAI Realtime ─→ translated audio back
User B (Hindi speaker)   ──┘
```

The audio path is **streaming end-to-end** — no record → upload → STT → translate → TTS → download. Audio frames flow continuously: Browser → WebRTC/Opus → LiveKit → Translation Agent → Realtime AI → Translation Agent → LiveKit → Browser.

---

## Architecture

```
                         WEB BROWSER A (English speaker)
                              │ WebRTC / Opus
                              ▼
                       ┌──────────────┐
                       │   LiveKit    │  ← one room per call
                       │     Room     │
                       └──────┬───────┘
                              │
              ┌───────────────┼────────────────────────┐
              ▼                                        ▼
   Translation Agent subscribes           Translation Agent subscribes
   to A's mic track                       to B's mic track
              │                                        │
       AI Session A (EN→HI)                     AI Session B (HI→EN)
              │                                        │
       publishes track                          publishes track
       `translated_audio_to_B`                  `translated_audio_to_A`
              │                                        │
              ▼                                        ▼
         Browser B                                  Browser A
         hears Hindi                                hears English
```

**Critical invariant:** User A's raw mic track is **never** routed to user B. Browser B only subscribes to tracks whose name starts with `translated_audio_to_`. The agent is the only publisher of translated audio.

### Components

| Service | Tech | Role |
|---------|------|------|
| `apps/web` | React + TypeScript + Vite + Tailwind + LiveKit Client + Zustand | Frontend — Home screen, Call screen, diagnostics, transcripts |
| `services/api` | Python + FastAPI + Pydantic + SQLAlchemy + asyncpg + Redis + structlog | Auth, calls, LiveKit token minting, usage — **never on audio path** |
| `services/translation-agent` | Python + LiveKit Agents + websockets + numpy + structlog | Joins LiveKit room, subscribes to user mics, runs two `TranslationSession`s, publishes translated tracks |
| `services/worker` | Python + asyncpg + Redis | Drains Redis usage/metrics queues into PostgreSQL |
| `postgres` | PostgreSQL 17 | Persistent: users, calls, usage, metrics |
| `redis` | Redis 7 | Ephemeral call state, presence, rate limiting, locks |

### Translation session internals

Each direction (A→B, B→A) is an independent `TranslationSession` that owns:

1. LiveKit audio input (subscribed mic track)
2. Audio conversion (PCM16 → float32, 48 kHz → 24 kHz resample)
3. Realtime AI connection (persistent WebSocket to OpenAI Realtime API)
4. Streaming AI output (incremental audio chunks, never wait-for-sentence)
5. Adaptive playback queue (50–150 ms target jitter buffer, grows on stall, shrinks when stable)
6. Interruption / barge-in (atomic generation-ID bump + queue clear + AI cancel)
7. Server-side VAD (energy-based with adaptive noise floor, configurable prefix/silence)
8. Metrics (TTFA, P50/P95/P99, stalls, interrupts, reconnects)
9. Reconnect (LiveKit auto-reconnects WebRTC; agent creates a fresh AI session, preserves generation bookkeeping)
10. Cleanup (graceful WebSocket close, queue draining, metric snapshot flush)

---

## Repository structure

```
voice-translator/
├── apps/
│   └── web/                       # React + Vite + TypeScript frontend
│       ├── src/
│       │   ├── app/
│       │   ├── components/         # CallButton, Modal, DiagnosticsPanel, LanguageSelect, icons
│       │   ├── screens/           # HomeScreen, CallScreen
│       │   ├── livekit/            # VoiceTranslatorRoom wrapper
│       │   ├── audio/              # (placeholder for Web Audio helpers)
│       │   ├── services/           # api.ts — FastAPI client
│       │   ├── store/              # callStore.ts — Zustand central store
│       │   ├── hooks/
│       │   ├── types/              # re-exports shared-types
│       │   └── utils/              # validation.ts
│       ├── index.html
│       ├── vite.config.ts
│       ├── tsconfig.json
│       ├── tailwind.config.js
│       └── package.json
│
├── services/
│   ├── api/                        # FastAPI backend
│   │   ├── app/
│   │   │   ├── routes/             # misc, calls, usage
│   │   │   ├── models/             # Pydantic schemas
│   │   │   ├── db/                 # SQLAlchemy models + session
│   │   │   ├── livekit/            # token minting
│   │   │   ├── redis/              # client + state helpers
│   │   │   ├── auth/               # Supabase / dev JWT
│   │   │   ├── services/           # calls.py — business logic
│   │   │   ├── config.py
│   │   │   ├── languages.py
│   │   │   ├── logging_setup.py
│   │   │   └── main.py
│   │   ├── tests/                  # pytest tests
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   │
│   ├── translation-agent/          # Python LiveKit translation agent
│   │   ├── app/
│   │   │   ├── main.py             # entrypoint (LiveKit agents CLI)
│   │   │   ├── agent.py            # TranslationAgent — joins room, manages sessions
│   │   │   ├── session.py         # TranslationSession — one direction
│   │   │   ├── translator.py      # RealtimeTranslator — OpenAI Realtime API abstraction
│   │   │   ├── audio.py            # PCM conversion, resampling, chunking
│   │   │   ├── vad.py              # StreamingVAD
│   │   │   ├── interruption.py     # GenerationId + InterruptController
│   │   │   ├── playback.py         # Adaptive PlaybackQueue
│   │   │   ├── metrics.py          # SessionMetrics (TTFA, percentiles)
│   │   │   ├── prompts.py          # Translation system prompt
│   │   │   ├── config.py
│   │   │   └── logging_setup.py
│   │   ├── tests/
│   │   │   ├── test_agent.py       # VAD, generation IDs, playback queue, audio
│   │   │   └── stress_test.py      # Concurrent-call stress harness
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   │
│   └── worker/                     # Background usage/metrics flusher
│       └── app/main.py
│
├── packages/
│   └── shared-types/               # Shared TS types between frontend & backend
│
├── infra/                          # (reserved for prod infra-as-code)
│
├── migrations/
│   └── 001_initial_schema.sql      # PostgreSQL schema
│
├── tests/                          # Cross-service integration tests
│
├── docker-compose.yml
├── .env.example
├── .gitignore
├── pyproject.toml                  # ruff + pytest config
└── README.md                       # ← you are here
```

---

## Quick start (local development)

### Prerequisites

- Docker + Docker Compose
- Node 18+ and `pnpm` (or npm/yarn)
- A **LiveKit Cloud** project (free tier works) — get URL + API key/secret
- An OpenAI API key with access to the Realtime API (`gpt-realtime-translate`)

### 1. Configure environment

```bash
cd voice-translator
cp .env.example .env
# Edit .env and fill in:
#   LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
#   OPENAI_API_KEY
#   VITE_LIVEKIT_URL (must match LIVEKIT_URL)
```

### 2. Start backend services (postgres, redis, api, agent, worker)

```bash
docker compose up -d postgres redis
# Wait for healthchecks:
docker compose ps

# Run database migrations (already mounted as init scripts, but for manual):
docker compose exec postgres psql -U vtranslate -d voice_translator -f /docker-entrypoint-initdb.d/001_initial_schema.sql

# Start API + worker + translation-agent:
docker compose up -d api worker translation-agent
docker compose logs -f api
```

API docs: http://localhost:8000/docs

### 3. Start the frontend

```bash
cd apps/web
npm install        # or pnpm install
npm run dev
```

Open http://localhost:5173 — you should see the Home screen.

### 4. Test call flow

Open **two browser tabs** (or two devices) at `http://localhost:5173`.

**Tab A:**
- "I speak" → English
- "I want to hear" → Hindi
- Click **Start Call**

**Tab B:**
- "I speak" → Hindi
- "I want to hear" → English
- Click **Start Call** (Tab B's call hits the same callee logic — for V1 the partner ID is generated; for a true 2-party call, supply a known callee_id)

Both tabs should connect to the same LiveKit room (the agent joins automatically when the room is created via LiveKit's agent dispatcher), translate bidirectionally, and you should hear translated audio within ~300–800 ms of starting to speak.

---

## Configuration reference

All config is via environment variables (see `.env.example` for the full list).

### Critical realtime tuning

| Variable | Default | What it does |
|----------|---------|--------------|
| `REALTIME_TRANSLATION_MODEL` | `gpt-realtime-translate` | OpenAI Realtime model — swap providers by replacing `translator.py` |
| `REALTIME_TRANSLATION_VOICE` | `alloy` | Voice for translated audio |
| `VAD_PREFIX_PADDING_MS` | `250` | How long speech must persist before VAD fires SPEECH_STARTED |
| `VAD_SILENCE_DURATION_MS` | `350` | How long silence must persist before VAD fires SPEECH_ENDED |
| `PLAYBACK_BUFFER_MS` | `100` | Initial jitter buffer target (ms) |
| `PLAYBACK_BUFFER_MAX_MS` | `400` | Cap on adaptive buffer growth |
| `AUDIO_SAMPLE_RATE` | `24000` | Internal pipeline rate (Realtime API native) |

### Never expose to the browser

- `OPENAI_API_KEY`
- `LIVEKIT_API_SECRET`
- `SUPABASE_SERVICE_ROLE_KEY`

Only short-lived LiveKit participant tokens are sent to the browser.

---

## Performance targets

| Metric | Target | How measured |
|--------|--------|--------------|
| Call setup | < 2 s | `POST /api/v1/calls` → LiveKit room connected |
| First translated audio (TTFA) | 300–800 ms | `metrics.ttfa_ms` — first AI audio frame after speech start |
| Interruption | < 200 ms | `InterruptController.barge_in()` — atomic gen-bump + queue clear |
| Playback buffer | 50–150 ms adaptive | `PlaybackQueue.target_buffer_ms` |
| Codec | Opus | LiveKit default for audio tracks |
| Audio stored during call | None | Frames are streamed; never persisted |
| Persistent AI session | Yes | One WebSocket per direction per call |
| Translation sessions per call | 2 | One per direction |

### Where the metrics live

- **Live** — `DiagnosticsPanel` (toggle via "Stats" button during a call) shows RTT, packet loss, jitter, TTFA, stalls, interrupts, reconnects.
- **Structured logs** — every `translation_first_audio` event emits JSON like:
  ```json
  {"event": "translation_first_audio", "call_id": "call_123", "participant_id": "user_1", "source_language": "en", "target_language": "hi", "latency_ms": 472}
  ```
- **PostgreSQL** — `usage` and `call_quality_metrics` tables store per-call rollups (flushed by the worker).

---

## Tests

### Backend (FastAPI)

```bash
cd services/api
pip install -r requirements.txt
pytest -v
```

### Translation Agent

```bash
cd services/translation-agent
pip install -r requirements.txt
pytest -v
```

Tests cover: VAD speech-start/end, generation-ID bumping, playback queue stale-chunk dropping, queue clear on interrupt, prompt rendering, percentile calculation, audio PCM round-trip + resampling.

### Frontend

```bash
cd apps/web
npm install
npm run test
```

Tests cover: store state transitions, language selection, mute toggle, transcript truncation, diagnostics updates.

### Stress test (developer mode)

```bash
cd services/translation-agent
python -m tests.stress_test --concurrency 10 --duration 120 --api-url http://localhost:8000
```

Reports P50/P95/P99 TTFA, stalls, reconnects, success rate.

---

## Production deployment

### Required external services

- LiveKit Cloud (or self-hosted LiveKit cluster)
- Supabase project (for Auth + Postgres) or standalone PostgreSQL 17
- Redis 7+
- OpenAI API key with Realtime access

### Production checklist

1. **Auth** — set `AUTH_PROVIDER=supabase`, fill in `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`. Replace the dev JWT path in `app/auth/auth.py` with `supabase.auth.get_user(token)`.
2. **Database** — run `migrations/001_initial_schema.sql` against your Postgres. Set `DATABASE_URL` accordingly.
3. **Redis** — set `REDIS_URL` to your managed Redis. Note: Redis holds **no audio** — only ephemeral call state + presence + locks.
4. **LiveKit** — `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`. LiveKit Cloud is recommended for V1.
5. **Translation agent** — set `OPENAI_API_KEY` and `REALTIME_TRANSLATION_MODEL`. Scale horizontally by running more agent replicas — each agent process handles one room at a time and LiveKit's dispatcher balances rooms across workers.
6. **Frontend** — `npm run build`, deploy the static `dist/` to your CDN. Set `VITE_API_URL` and `VITE_LIVEKIT_URL` to production URLs.
7. **CORS** — set `CORS_ORIGINS` to your frontend's production origin(s).

### Scaling

```
Load Balancer
      │
 ┌────┴────┐
 ▼         ▼
API 1     API 2          ← stateless FastAPI, scale horizontally
            │
   Translation Agent Worker Pool
   ┌────┬────┬────┐
   ▼    ▼    ▼
   A1   A2   A3       ← each handles N concurrent rooms (configurable)
            │
         LiveKit Cloud
            │
       Redis (ephemeral state)
       PostgreSQL (persistent state)
```

No Kubernetes in V1 — Docker Compose + a managed LiveKit + a managed Postgres/Redis is sufficient. The agent pool can be scaled by `docker compose up -d --scale translation-agent=N`.

---

## V1 acceptance test

Run this manually:

1. **User A** — Browser A, English → Hindi, Start Call
2. **User B** — Browser B, Hindi → English, Start Call
3. A says: *"Hello, how are you?"*
4. B should hear Hindi audio within ~500–800 ms
5. B replies in Hindi
6. A should hear English audio
7. A interrupts B mid-translation — the previous translated audio must stop quickly and the new speech must be processed
8. Repeat for ≥ 5 minutes
9. Simulate a network interruption (toggle Wi-Fi off/on) — the call must recover without manual restart

---

## Adding a new language

1. Add the language to `services/api/app/languages.py`:
   ```python
   Language(code="ta", name="Tamil", native_name="தமிழ்"),
   ```
2. Add it to `services/translation-agent/app/languages.py` for prompt rendering.
3. The frontend automatically picks up new languages from the `/api/v1/languages` endpoint.

No core logic changes needed.

---

## Adding a new realtime AI provider

Implement the `RealtimeTranslator` interface in a new file (e.g. `app/translators/azure.py`):

```python
class AzureRealtimeTranslator:
    async def connect(self) -> None: ...
    async def send_audio(self, samples: np.ndarray) -> None: ...
    async def receive_audio(self) -> AsyncIterator[TranslatedAudioChunk]: ...
    async def receive_transcript(self) -> AsyncIterator[TranscriptEvent]: ...
    async def interrupt(self) -> None: ...
    async def commit_input(self) -> None: ...
    async def close(self) -> None: ...
```

Then in `TranslationSession.__init__`, swap the translator instance. The rest of the agent (VAD, interruption, playback, metrics) is provider-agnostic.

---

## Troubleshooting

### No translated audio
- Verify `OPENAI_API_KEY` is valid and has Realtime API access.
- Check agent logs: `docker compose logs translation-agent | grep translator_connect`
- Verify the agent joined the room: `docker compose logs translation-agent | grep agent_entered_room`
- Verify the frontend is subscribing to the right track name (`translated_audio_to_<your_user_id>`).

### High latency
- Increase `PLAYBACK_BUFFER_MS` if you hear stuttering.
- Decrease it if you want lower latency at the cost of occasional drops.
- Check the Diagnostics panel — if `RTT > 300 ms` you have a network issue, not a code issue.
- Check `ai_latency_p95_ms` — if very high, the AI provider is slow (try a different `REALTIME_TRANSLATION_MODEL`).

### Stale audio after interruption
- Should not happen — generation-ID protection is in `PlaybackQueue.put()`.
- If it does, check that `InterruptController.barge_in()` is being called from `_vad_loop` on `SPEECH_STARTED`.

---

## License

Proprietary — internal use only.
#   9 _ 2 6 _ v o i c e _ c a l l _ t r a n s l a t e  
 