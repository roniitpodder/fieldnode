# FieldNode — Smart Irrigation (backend + frontend, stitched)

The FastAPI backend and the React dashboard, wired together. The dashboard no
longer ships hardcoded demo values: every number on screen comes from the API.

```
fieldnode/
├── backend/     FastAPI + SQLAlchemy + the ML advisor and Groq chat assistant
└── frontend/    Vite + React dashboard, now talking to the backend
```

## Run it

Two terminals. **Backend first.**

### 1. Backend (port 8000)

```bash
cd backend
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env            # set SECRET_KEY, and GROQ_API_KEY for the chat
./venv/bin/python seed.py       # demo user, farm, zones, device, sample readings
./venv/bin/uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

### 2. Frontend (port 3000)

```bash
cd frontend
pnpm install      # or: npm install
pnpm dev
```

Open http://localhost:3000 and sign in with the seeded account:

```
arjun@mehtafarm.example  /  password123
```

Vite proxies `/api` and `/ws` to `http://127.0.0.1:8000`, so both sides are
same-origin and there is no CORS or base-URL setup to do. Point somewhere else
with `VITE_BACKEND_URL` (dev proxy) or `VITE_API_BASE` (baked into the build).

### Single-process deployment

```bash
cd frontend && pnpm build          # writes frontend/dist/public
cd ../backend && ./venv/bin/uvicorn app.main:app --port 8000
```

`app/main.py` mounts `frontend/dist/public` when it exists and falls through to
`index.html` for client-side routes, so the whole app is served from :8000.

## The AI advisory chat

It lives under **Zones & crops**, below the zone cards, and is the main thing
this merge adds to the UI.

- Sends to `POST /api/ai/chat` with the currently selected `zone_id`, so every
  answer is grounded in that zone's live sensor snapshot server-side.
- Loads and clears the stored conversation via `GET`/`DELETE /api/ai/chat/history`.
- When the backend returns a `suggested_action`, the chat renders a **Confirm**
  button that calls `POST /api/pump/control`. The assistant never runs the pump
  itself — the farmer's tap is what does it.
- If the language model is unreachable, the backend answers from the rule-based
  advisor and sets `degraded: true`; the bubble shows an "offline answer" note.
- Without `GROQ_API_KEY` in `backend/.env` the endpoint returns 503 with a clear
  message, which the chat surfaces as-is. Everything else in the app still works.

Switching zones in the chat's dropdown also switches the dashboard's active
zone, and vice versa — there is one selected zone across the whole page.

## What changed in the frontend

| Area | Before | After |
|---|---|---|
| Data | Hardcoded arrays in `Home.tsx` | `lib/api.ts` typed client + `hooks/useFieldData.ts` |
| Auth | None | JWT login/register (`pages/Login.tsx`, `contexts/AuthContext.tsx`) gating the app |
| Live updates | `setInterval` faking "12 sec ago" | Zone WebSocket `/ws/zones/{id}` with polling fallback |
| Charts | Fixed SVG path strings | Built from real readings (`lib/chart.ts`) |
| Pump | Local `useState` toggle | `POST /api/pump/control`, auto/manual from `zone.auto_mode` |
| Schedule | Static table | `POST /api/ai/generate-schedule` + real schedule rows, cancellable |
| Crop presets | Three hardcoded names | `GET /api/crops`, applying one PATCHes the zone's thresholds |
| Zones | Three fake zones | `GET /api/zones`, with create |
| Activity | Fake list | `GET /api/activity` with filters |
| Analytics | Decorative paths | `GET /api/analytics/zones/{id}/trends` |
| **Tank level / dry-run guard** | Fake 62% tank gauge | **Removed** — the hardware has no water-level sensor. Replaced with the real rain-sensor status and the server-side 300 s run-time cap |
| AI chat | — | New, under Zones & crops |

The "tank level" removal matters: the old UI promised a sensor the wiring
diagram doesn't have. The backend estimates liters as *run time × calibrated
pump flow rate*, and caps each run instead. The Settings page now exposes that
calibration field.

## Endpoints the dashboard uses

```
POST   /api/auth/login | /api/auth/register      GET /api/auth/me
GET    /api/farms                                POST /api/farms
GET    /api/zones                                POST /api/zones   PATCH /api/zones/{id}
GET    /api/crops
GET    /api/devices?zone_id=                     PATCH /api/devices/{id}
GET    /api/overview/zones/{id}
GET    /api/zones/{id}/readings?hours=
GET    /api/analytics/zones/{id}/trends?days=
POST   /api/pump/control                         GET /api/pump/events?zone_id=
GET    /api/schedules?zone_id=                   POST /api/schedules/{id}/cancel
POST   /api/ai/advise                            POST /api/ai/generate-schedule
POST   /api/ai/chat                              GET|DELETE /api/ai/chat/history?zone_id=
GET    /api/activity                             GET /api/notifications
WS     /ws/zones/{id}?token=<jwt>
```

Device-facing endpoints (`/api/ingest/reading`, `/api/pump/command`,
`/api/pump/ack`) are unchanged and still authenticate with `X-Device-Key`. Your
ESP32 firmware needs no changes.

## Notes

- The JWT is kept in `localStorage` under `fieldnode_token`. A stale token is
  validated against `/api/auth/me` on boot and cleared if rejected.
- Backend timestamps are naive UTC; the frontend appends `Z` before parsing so
  "3 min ago" is correct rather than shifted by your timezone.
- If a new account has no farm yet, the dashboard shows an empty state with
  "Create farm" / "Add zone" rather than erroring.
