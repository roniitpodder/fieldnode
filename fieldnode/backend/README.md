# FieldNode Backend

FastAPI backend for the Smart Irrigation ("FieldNode") system: dashboard data,
device ingest, pump control, and an ML-powered AI advisor for watering
schedules — built to match the FieldNode dashboard UI (Mehta Farm / FN-001
reference design).

## Quick start

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env          # edit SECRET_KEY at minimum
./venv/bin/python seed.py     # creates demo user/farm/zone/device + sample data
./venv/bin/uvicorn app.main:app --reload --port 8000
```

Interactive API docs: http://localhost:8000/docs

Demo login after seeding: `arjun@mehtafarm.example` / `password123`

## Architecture

- **FastAPI + SQLAlchemy**, SQLite by default (swap `DATABASE_URL` for
  Postgres in production — no code changes needed).
- **Two auth schemes**: JWT bearer tokens for dashboard users
  (`/api/auth/login`), and a per-device `X-Device-Key` header for ESP32 field
  nodes hitting the ingest/command endpoints — devices can't do an OAuth
  login flow.
- **ESP32 integration is HTTP-only** (no MQTT broker to stand up):
  - Device pushes readings: `POST /api/ingest/reading`
  - Device polls for pump commands: `GET /api/pump/command`
  - Device reports a finished watering cycle: `POST /api/pump/ack?duration_seconds=<how long the pump ran>`

### ESP32 payload contract (matches the wiring diagram)

Sensors on the node: soil moisture (analog), rain sensor (a water-detection plate),
LDR (analog), DS3231 RTC. There is **no water-flow sensor and no water-level sensor**.

```jsonc
// POST /api/ingest/reading      header: X-Device-Key: <device api_key>
{
  "soil_moisture": 47.5,      // % — convert from the analog reading in firmware
  "light_level": 610,         // raw LDR / lux
  "rain_detected": false,     // true = rain plate is WET. Resolve the module's polarity
                              //        and threshold in firmware; send a clean boolean.
  "rain_intensity": 0,        // optional, 0-100 % wetness
  "temperature": 28.4,        // optional — see note below
  "humidity": 55,             // optional — see note below
  "sensor_fault": false
}
```

`temperature` / `humidity` are optional: the wiring diagram has no dedicated
temperature/humidity sensor (the DS3231 has an onboard temperature register, but no
humidity). When missing, the advisor assumes 28 °C / 55 % **and says so in its
`warnings`**. Adding a DHT22/DHT11 (or a weather-API lookup) would give the model real
values. Old firmware that still sends `tank_level` is fine — the field is ignored.

**Firmware should also veto locally:** in auto mode, don't start a cycle while the rain
pin reads wet — that protects the crop even if Wi-Fi/GSM is down.

### Water accounting without a flow sensor

Liters are **estimated**: `run time x pump_flow_rate_lpm` (default 1.2 L/min). To calibrate,
run the pump for exactly 60 s into a measuring jug, then:
`PATCH /api/devices/{id}  {"pump_flow_rate_lpm": <liters measured>}`.
The advisor converts its liters recommendation into `recommended_duration_seconds` for you.

There's no way to detect an empty tank without a level sensor, so a single pump run is
capped at `MAX_PUMP_RUN_SECONDS` (default 300 s). Keep an eye on the tank manually.

### Rain: two signals

1. **Rain sensor (live)** — plate is wet now -> watering is held (`raining_now`). If the
   plate reads wet continuously for `RAIN_SENSOR_STUCK_HOURS` (default 12 h) it's assumed
   stuck/corroded: the veto is lifted, a warning is raised, and Field Health drops.
   Plates also read wet from pump splash or dew, so mount it away from the sprinkler.
2. **Forecast (tomorrow)** — Open-Meteo probability >= 65 % skips small-deficit cycles.
- **Live telemetry**: `GET /ws/zones/{zone_id}?token=<jwt>` — a WebSocket
  that pushes new readings and pump state changes as they happen. Use this
  instead of polling `/api/overview` on an interval for the "Live telemetry"
  panel.
- **AI Advisor (ML)**: `app/ml/` trains a RandomForest classifier
  (should-water) and regressor (liters needed) on a physics-informed
  synthetic dataset (soil deficit, heat, humidity, rain probability →
  watering decision). Retrain any time with:
  ```bash
  ./venv/bin/python -m app.ml.train_model
  ```
  The model artifacts record the scikit-learn version and feature list they were built
  with; if either doesn't match, the API retrains automatically on startup.
  Note the labels come from the hand-written rules in `app/ml/rules.py`, so today the
  model approximates those rules rather than discovering anything new.
  Once real sensor + watering history accumulates in the DB, point
  `app/ml/train_model.py` at that instead of `generate_data.py` for a model
  trained on this farm's actual conditions rather than synthetic ones.
- **Rain forecasting**: `app/services/rain.py` calls Open-Meteo (free, no API
  key) using the farm's lat/long if set. **If the farm has no coordinates or the API
  is unreachable it falls back to a deterministic FAKE number** (so demos never look
  empty). Every advisor/overview response now carries `rain_forecast_source`
  (`open-meteo` | `mock` | `unavailable`) and the advisor adds a `warnings` entry when the
  number is simulated. Set the farm's coordinates via `PATCH /api/farms/{id}`, and set
  `USE_MOCK_RAIN_FALLBACK=false` for real use so decisions are never based on made-up rain.

## AI chat assistant (Groq)

The dashboard's **AI Advisor** is a farming chatbot. Farmers ask in their own language
("should I water?", "why didn't you water this morning?", "my tomato leaves are yellow",
"which crops suit my soil?") and it answers using the zone's **live data**.

```
farmer message ──► POST /api/ai/chat
                     │ 1. build a fresh FIELD SNAPSHOT (sensors, rain sensor, 24h/7d moisture,
                     │    watering log incl. skipped cycles, advisor verdict, crop list)
                     │ 2. system prompt (rules) + snapshot + last 10 messages + new message
                     ▼
                   Groq (llama-3.3-70b-versatile → falls back to llama-3.1-8b-instant)
                     ▼
        reply  +  optional suggested_action  (only if the ML advisor itself says "water now")
```

**Setup:** put your key in `.env` (`GROQ_API_KEY=gsk_...`, free at console.groq.com), then verify:
`python scripts/check_groq.py` (checks the key, that the model names still exist, and prints
real sample answers). Model names on Groq change over time — if it warns, update `GROQ_MODEL`.

**Design decisions**
- *Grounded, not free-styling:* the assistant only sees the snapshot for farm facts and is told
  never to invent readings. Simulated/assumed values are labelled; a simulated rain forecast is
  hidden entirely so it can't be quoted as real.
- *It cannot touch the pump.* `suggested_action` is computed from the ML advisor (rules included),
  never from the LLM's text. The UI shows a confirm button that calls `POST /api/pump/control`.
- *Snapshot instead of LLM tool-calling:* one API call, works on any model, and avoids the
  malformed tool-call failures Llama models on Groq sometimes produce.
- *Graceful degradation:* if Groq is unreachable/rate-limited (common on a weak farm connection)
  the reply is the rule-based advisor's answer with `degraded: true`. A missing/invalid key or a
  retired model name is a loud `503` with the fix, not a silent fallback.
- *Safety:* no pesticide doses (refers to the local agriculture extension office/KVK), no
  diagnosis claims, per-user rate limit, ownership checks, messages capped at 1000 chars.

| Endpoint | Purpose |
|---|---|
| `POST /api/ai/chat` `{zone_id, message}` | Send a question → `{reply, suggested_action, degraded, model}` |
| `GET /api/ai/chat/history?zone_id=` | Past messages, oldest → newest (to restore the chat panel) |
| `DELETE /api/ai/chat/history?zone_id=` | "Clear chat" |

Frontend sketch:
```js
const r = await fetch(`${API}/api/ai/chat`, {
  method: "POST",
  headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
  body: JSON.stringify({ zone_id, message }),
});
const { reply, suggested_action, degraded } = await r.json();
// show `reply`; if suggested_action, render a button labelled suggested_action.label that POSTs
// { zone_id, action: "start", duration_seconds } to /api/pump/control when tapped.
// if degraded, optionally show a small "offline mode" badge.
```
Replies are plain text (no markdown) with short "-" bullets. Answer language follows the
farmer's message, with `users.language` as the tie-breaker.

## Key endpoints for the dashboard

| Screenshot element | Endpoint |
|---|---|
| Live field status cards (moisture, temp, water used, field health) | `GET /api/overview/zones/{zone_id}` |
| Rain-aware protection banner | overview response: `rain_protection_on`, `raining_now`, `rain_sensor_status`, `rain_probability`, `rain_forecast_source` |
| Live telemetry panel | `GET /ws/zones/{zone_id}?token=...` (WebSocket) |
| Pump status + Auto mode toggle | `POST /api/pump/control`, `PATCH /api/zones/{zone_id}` (`auto_mode`) |
| Activity log | `GET /api/activity` |
| Analytics charts | `GET /api/analytics/zones/{zone_id}/trends` |
| AI Advisor chat panel | `POST /api/ai/chat`, `GET/DELETE /api/ai/chat/history` |
| "Plan watering" button (AI advisor) | `POST /api/ai/advise` (returns `warnings`, `raining_now`, `recommended_duration_seconds`) or `POST /api/ai/generate-schedule` |
| Zones & crops page | `GET/POST/PATCH /api/zones`, `GET/POST /api/crops` |
| Notifications badge | `GET /api/notifications?unread_only=true` |

## Connecting your frontend

1. Point your frontend's API base URL at wherever this is deployed.
2. Login flow: `POST /api/auth/login` (form-encoded `username`/`password`,
   `username` = email) → store `access_token`, send as
   `Authorization: Bearer <token>` on every subsequent request.
3. For the WebSocket, pass the same token as a query param since browsers
   can't set custom headers on the WS handshake.
4. CORS is wide open (`*`) for now — tighten `CORS_ORIGINS` in
   `app/config.py` to your actual frontend domain before going to production.

## Upgrading an existing database

Startup runs `app/migrate.py`, which adds the new columns (`rain_detected`,
`rain_intensity`, `pump_flow_rate_lpm`) to an older `fieldnode.db` and converts old
`low_tank` notifications to `info`. Existing data is kept. (Or just delete
`fieldnode.db` and re-run `seed.py` for a clean start.)

## Tests

```bash
./venv/bin/pip install pytest httpx
./venv/bin/python -m pytest tests -q
```

The chat tests fake Groq's HTTP API, so they run offline and need no key; use
`scripts/check_groq.py` to test against the real service.

Covers the advisor's behaviour (rain-sensor veto, forecast skip, liters scaling,
model/rule agreement, sklearn version match) and the API (rain ingest, stuck-sensor
guard, pump ack/calibration, warnings).

## Known sandbox limitation

The real Open-Meteo weather call was tested and its fallback path confirmed
in this dev sandbox, but the sandbox's network allowlist blocks
`api.open-meteo.com` outright — so only the fallback path actually ran here.
The real-fetch code is fully implemented and will activate automatically
once this is deployed somewhere with normal outbound internet access and a
farm's coordinates are set.
