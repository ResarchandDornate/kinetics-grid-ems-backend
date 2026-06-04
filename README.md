# EMS Backend

Backend for the FRDM **i.MX93 EMS Gateway** web dashboard. It consumes the
gateway's REST + SSE Web APIs, stores telemetry / events / command audit in
**PostgreSQL + TimescaleDB**, and serves a fast dashboard API (latest state,
history, commands) to the frontend.

The gateway itself is the source of truth for all field communication (Modbus),
decoding, scaling and command routing. This backend never talks Modbus directly.

## Architecture

```
i.MX93 Gateway ──SSE :8000──►  ingestion worker ─┐
      │                                          ├─► Postgres + TimescaleDB
      └──REST :7000 (CSV logs, backfill) ────────┘        │
                                                          ▼
   Frontend ◄── FastAPI (latest / history / commands / SSE) ◄┘
```

- **No Redis / no separate worker process in the MVP.** The gateway already
  logs every telemetry row to CSV (port 7000), so a durable queue isn't needed
  for safety — if ingestion restarts, history can be backfilled from the
  gateway logs. Batched inserts handle the write load (3 assets @ 1 Hz).
- **TimescaleDB from day one** (it's just a Postgres extension). The telemetry
  table is a hypertable with compression after 7 days and a 1-minute continuous
  aggregate for fast long-range charts. No "migrate later" step.
- Add Redis + a dedicated worker only when you scale to many sites / users.

## Quick start (everything in Docker, including a mock gateway)

You don't need the real i.MX93 to start. A **mock gateway** emits realistic
telemetry once per second using the exact field names from the spec.

```powershell
copy .env.example .env
docker compose up --build
```

Services:
| Service       | URL                        | What |
|---------------|----------------------------|------|
| api           | http://localhost:8080      | This backend |
| mock-gateway  | http://localhost:9000      | Fake i.MX93 (REST + SSE) |
| db            | localhost:5432             | Postgres + TimescaleDB |

Open the API docs at **http://localhost:8080/docs**.

### Try it
```powershell
# Health
curl.exe http://localhost:8080/api/health

# Assets (synced from the gateway on startup)
curl.exe http://localhost:8080/api/assets

# Latest state (served from our DB cache, sub-ms)
curl.exe http://localhost:8080/api/telemetry/latest

# Live stream for the frontend (Ctrl+C to stop)
curl.exe -N http://localhost:8080/api/stream/telemetry

# History (TimescaleDB) — give it ~30s to collect samples first
curl.exe "http://localhost:8080/api/assets/bms_1/telemetry/timeseries?field_key=soc_percent"

# Send a command (audited + forwarded to gateway, effect visible in telemetry)
curl.exe -X POST "http://localhost:8080/api/assets/chiller_1/commands" -H "Content-Type: application/json" -d "{\"command\":\"SET_TEMP\",\"value\":18.0}"
curl.exe http://localhost:8080/api/assets/chiller_1/commands/audit
```

## Switching to the real gateway

Edit `.env` (or the `api` env in `docker-compose.yml`) and point at the current
Wi-Fi IP from `ip -4 addr show mlan0`:

```
GATEWAY_BASE_URL=http://10.55.41.131:8000
GATEWAY_LOG_BASE_URL=http://10.55.41.131:7000
```

Then drop the `mock-gateway` service (or just ignore it). **No code changes** —
the mock speaks the same API.

## Running the API outside Docker (against the dockerized DB)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
docker compose up -d db mock-gateway
uvicorn app.main:app --reload --port 8080
```

## Project layout

```
app/
  config.py          settings (env / .env)
  db.py              asyncpg pool (JSONB codec)
  gateway_client.py  REST + SSE client for the i.MX93
  bootstrap.py       startup sync: gateway, assets, field dictionary
  ingestion.py       SSE consumer + batched time-series writer
  models.py          pydantic API models
  main.py            FastAPI app, lifespan, frontend SSE
  routers/
    assets.py        registry + latest state
    telemetry.py     history (raw + 1-minute aggregate)
    commands.py      command proxy + audit
    events.py        events + gateway log sync
db/schema.sql        DB schema (Timescale hypertable, aggregate, compression)
mock_gateway/mock.py fake i.MX93 for local development
```

## Data model

Uses the gateway team's official schema (`ems_gateways`, `ems_assets`,
`ems_asset_latest_state`, `ems_telemetry_samples`, `ems_telemetry_field_dictionary`,
`ems_asset_events`, `ems_command_audit`, `ems_api_raw_snapshots`).

Telemetry is stored EAV-style (one row per field per timestamp). The
`store_history` flag in `ems_telemetry_field_dictionary` controls which fields
are historised — set it `false` for noisy fields you only need live, to keep
row growth in check over multi-year retention.

## Production hardening (later, per the spec)

- Add auth (API key / JWT) and HTTPS; protect command endpoints behind an
  authenticated operator role.
- Enable the gateway's API-key support and put this backend behind it.
- Restrict CORS `allow_origins` to your dashboard domain.
- Add a nightly `pg_dump` backup of the Postgres volume.
- Schedule periodic backfill from the gateway log API to fill any ingestion gaps.
```
