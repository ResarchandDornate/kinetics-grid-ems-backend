# NorthBound EMS Gateway — Frontend Integration Guide

API reference for the **North gateway dashboard** (a separate frontend that shows
**only** NorthBound EMS Gateway data). All shapes below were captured from the
**live running gateway**, which differs from the v0.1 draft doc in a few places
(noted inline) — trust this guide.

- **Telemetry base URL:** `https://ems-api.unityess.cloud` (read-only, direct)
- **Live updates:** `wss://ems-api.unityess.cloud/ws/telemetry` (WebSocket)
- **Command base URL:** `https://kinetic.unityess.cloud` (your backend — for writes)
- **Content type:** `application/json`

> **Reads** (telemetry/alarms/history) come **directly from the gateway** — no auth,
> plain `GET`. **Writes** (EMS commands) go **through your backend** with an
> `operator` login, because they need the gateway's secret internal token. See
> [EMS Command Panel](#ems-command-panel-write-control-operator).

## ⚠️ Read this first — gateway behavior

1. **It's slow.** The gateway exposes **1421 signals across 9 assets** over a
   Cloudflare tunnel. Full payloads can take several seconds. Occasionally you'll
   get **`502`** (tunnel up, board busy) or a timeout — **retry with backoff**,
   don't treat a single failure as "offline".
2. **Use the light endpoints for the dashboard.** Load
   `/api/telemetry/key-signals` for cards, not the full `/api/telemetry`.
3. **Signals are self-describing.** Every signal carries its own `display_name`,
   `unit`, `category`, and `quality` — render dynamically, never hard-code.

## The 9 assets (live)

| asset_id | Display name | ~signals |
|---|---|---|
| `bms_1` | BMS 1 | 761 |
| `pcs_1` | PCS 1 | 269 |
| `liquid_cooling` | Liquid Cooling | 113 |
| `ems_system` | EMS System | 93 |
| `utility_meter` | Utility Meter | — |
| `fire_protection` | Fire Protection | 23 |
| `io_module` | I/O Module | 21 |
| `dehumidifier` | Dehumidifier | 16 |
| `remote_control` | Remote Control | — |

> Note: real IDs are `ems_system` and `remote_control` (the v0.1 doc's
> `existing_ems` / `remote_status` are **wrong**). Always build your asset list
> from `GET /api/assets` rather than hard-coding these.

## Recommended startup flow

```
1. GET /api/health                    → gateway up? read-only? signal quality
2. GET /api/assets                    → build asset cards/nav (note: items[])
3. GET /api/telemetry/key-signals     → fill all dashboard cards in one call
4. GET /api/alarms                    → alarm banner / panel
5. open WebSocket /ws/telemetry       → live updates
```

---

## Endpoints

### Health
`GET /api/health` — real response:
```json
{
  "status": "ok",
  "timestamp_utc": "2026-06-30T06:56:00.134266+00:00",
  "gateway_mode": "read_only",
  "asset_count": 9,
  "online_asset_count": 9,
  "total_signal_count": 1421,
  "bad_signal_count": 0,
  "commands_enabled": false,
  "poll_errors": [],
  "storage_can_write": true,
  "storage": { "enabled": true, "type": "sqlite", "free_space_mb": 113342, "used_percent": 0.0 }
}
```
Use `online_asset_count` / `asset_count` and `bad_signal_count` for a top-level
health badge.

### List assets (cards)
`GET /api/assets` — **returns `items` (not `assets`)**:
```json
{
  "items": [
    {
      "asset_id": "bms_1", "display_name": "BMS 1", "online": true,
      "signal_count": 761, "bad_signal_count": 0,
      "last_update_utc": "2026-06-30T07:35:45.010749+00:00"
    }
  ]
}
```

### Key signals — all assets (MAIN dashboard call)
`GET /api/telemetry/key-signals`
```json
{
  "assets": {
    "bms_1": {
      "asset_id": "bms_1", "display_name": "BMS 1", "online": true,
      "last_update_utc": "2026-06-30T07:35:50.017291+00:00",
      "signal_count": 761, "bad_signal_count": 0,
      "signals": {
        "insulation_too_low": {
          "name": "insulation_too_low",
          "display_name": "Insulation Too Low",
          "value": 90.0087,
          "unit": "",
          "category": "insulation",
          "quality": "good",
          "description": "0, Normal; 1, Fault",
          "updated_utc": "2026-06-30T07:35:49.652454+00:00"
        }
      }
    }
  }
}
```
Each signal object: `value`, `unit`, `category`, `quality` (`good`/`bad`),
`display_name`, `description`. Render the value with its `unit`; show a warning
dot when `quality !== "good"`. (There's also `address` / `raw_registers` — ignore
those in the UI, they're for debugging.)

### One asset's telemetry (detail page)
`GET /api/assets/{asset_id}/telemetry` — optional `?category=...`
```
GET /api/assets/bms_1/telemetry
GET /api/assets/bms_1/telemetry?category=soc_soh
GET /api/assets/pcs_1/telemetry?category=power_energy
```
Returns the asset's signals (same signal-object shape as above), filtered to the
category when given. Use categories to lay out detail-page sections.

> ⚠️ `GET /api/assets/{id}/key-signals` from the v0.1 doc **returns 404** on the
> live gateway — do **not** use it. For per-asset cards, read the asset's entry
> out of the all-assets `/api/telemetry/key-signals` response instead.

### Alarms
`GET /api/alarms`
```json
{
  "timestamp_utc": "...",
  "alarms": [
    { "asset_id": "pcs_1", "severity": "warning", "code": "...", "message": "...", "timestamp_utc": "..." }
  ]
}
```
`severity` is `info | warning | critical`. Drive the alarm banner color from it.

### History (charts)
`GET /api/storage/points?asset_id=...&signal_name=...&limit=100` — trend for one
signal from the gateway's local SQLite historian.
```
GET /api/storage/points?asset_id=bms_1&signal_name=soc.display_percent&limit=100
GET /api/storage/points?asset_id=pcs_1&signal_name=ac.total_active_power_kw&limit=100
```
`GET /api/storage/snapshots?asset_id=bms_1&limit=10` — recent full snapshots.

> History depth is limited to whatever the gateway's local SQLite keeps. For
> long-term history you'd need a backend storing this data (see note at bottom).

### Category filters (for detail-page sections)

| asset | categories |
|---|---|
| `bms_1` | soc_soh, voltage, current, thermal, insulation, contactors_precharge, limits, faults, alarms, status |
| `pcs_1` | status, power_energy, ac_measurements, dc_measurements, grid_mode, insulation, faults, alarms |
| `utility_meter` | ac_measurements, power_energy, insulation, alarms, status |
| `liquid_cooling` | thermal, pressure, alarms, mode, status |
| `fire_protection` | status, alarms |
| `dehumidifier` | status, thermal, humidity, alarms |
| `io_module` | digital_inputs, digital_outputs, analog_inputs, analog_outputs, status |
| `remote_control` | status, schedule, remote_readback |

---

## Live updates (WebSocket)

This gateway uses **WebSocket**, not SSE. Load once with REST, then subscribe:
```js
const ws = new WebSocket("wss://ems-api.unityess.cloud/ws/telemetry");

ws.onmessage = (event) => {
  const update = JSON.parse(event.data);
  // contains latest telemetry/signals — merge into your dashboard state
  applyTelemetry(update);
};

ws.onclose = () => {
  // reconnect with backoff (the tunnel can drop)
  setTimeout(connect, 2000);
};
```
> Do **NOT** use `/api/stream/telemetry` (the old EMS SSE endpoint) — it's gone
> on this gateway and will 404.

---

## Error / status handling

| Code | Meaning | Frontend action |
|---|---|---|
| `200` | OK | — |
| `404` | Endpoint/asset not found (or a legacy path) | Don't retry; check the path |
| `502` | Tunnel up, board busy/slow | **Retry with backoff** — not "offline" |
| `530` / `1033` | Cloudflare tunnel down | Show "gateway offline", keep retrying |
| timeout | Gateway slow under load | Retry; prefer key-signals over full telemetry |

Treat `502`/timeout as *transient* — only show "offline" after several
consecutive failures or a `530`.

---

## TypeScript types

```ts
export interface Signal {
  name: string;
  display_name: string;
  value: number | string | boolean | null;
  unit: string;
  category: string;
  quality: "good" | "bad" | string;
  description?: string;
  updated_utc: string;
}

export interface AssetSignals {
  asset_id: string;
  display_name: string;
  online: boolean;
  last_update_utc: string | null;
  signal_count: number;
  bad_signal_count: number;
  signals: Record<string, Signal>;
}

export interface KeySignalsResponse {
  assets: Record<string, AssetSignals>;
}

export interface AssetListItem {
  asset_id: string;
  display_name: string;
  online: boolean;
  signal_count: number;
  bad_signal_count: number;
  last_update_utc: string | null;
}

export interface AssetsResponse { items: AssetListItem[]; }

export interface Alarm {
  asset_id: string;
  severity: "info" | "warning" | "critical";
  code: string;
  message: string;
  timestamp_utc: string;
}
export interface AlarmsResponse { timestamp_utc: string; alarms: Alarm[]; }

export interface Health {
  status: string;
  gateway_mode: "read_only";
  asset_count: number;
  online_asset_count: number;
  total_signal_count: number;
  bad_signal_count: number;
  commands_enabled: boolean;
}
```

---

## EMS Command Panel (write control)  🔒 operator

Everything above is **read-only** telemetry, straight from the gateway. **Commands
are different** — writing to the plant needs the gateway's secret internal token,
so they go **through your backend**, never the gateway directly:

```
Frontend ──(backend operator JWT)──► https://kinetic.unityess.cloud ──(gateway internal token)──► gateway
```

- **Command base URL: `https://kinetic.unityess.cloud`** (your backend) — NOT `ems-api.unityess.cloud`.
- **Requires a backend `operator` login:** `POST https://kinetic.unityess.cloud/api/auth/login`,
  then send `Authorization: Bearer <token>`. Viewers get `403`.
- Only the **`ems_system`** asset is writable — **93 registers** (modes, start/stop,
  setpoints, SOC limits, emergency stop, …).
- Every write is **audited** by the backend. Always **confirm in the UI** and
  **debounce** — these affect real hardware.

### List writable registers (build the panel)
`GET /api/commands/ems/registers`  ·  operator token
```json
{
  "asset_id": "ems_system", "commands_enabled": true, "write_access": true, "count": 93,
  "items": [
    {
      "id": "p0003", "address": 4, "signal_name": "remote_mode", "point_name": "Remote Mode",
      "unit": "", "category": "status", "description": "0, Local; 1, Remote", "rw": 1, "factor": 1.0,
      "latest": { "value": 1.0, "quality": "good", "timestamp_utc": "..." }
    }
  ]
}
```
Render each item as a control: `point_name` = label, `signal_name` = target,
`description` = value options (e.g. `0=Local, 1=Remote`), `latest.value` = current state.

### Write one register
`POST /api/commands/ems/write`  ·  operator token
```json
// request — target by signal_name (preferred), point_id, or address
{ "signal_name": "remote_mode", "value": 1, "readback": true, "note": "reason" }
```
```json
// response
{ "audit_id": 5, "status": "ok", "error_code": null,
  "gateway_response": { "ok": true, "signal_name": "remote_mode",
                        "requested_value": 1, "readback_value": 1.0 } }
```
Success → `status: "ok"` and `gateway_response.readback_value` confirms the new value.
Failure → `status: "error"` + `error_code` (`403` role, `404` bad register, `502` Modbus
write failed, `401`/`530` gateway auth/down); show `message` to the user.

### Batch write (commissioning only)
`POST /api/commands/ems/batch`  ·  operator token
```json
{ "writes": [ { "signal_name": "remote_mode", "value": 1 },
              { "signal_name": "manual_auto_mode", "value": 1 } ],
  "continue_on_error": false }
```
Prefer single-write for operator actions (clean per-action audit).

> **Register examples** (of 93): `remote_mode` (0 Local/1 Remote), `manual_auto_mode`
> (0 Manual/1 Auto), `start_command` (0 Stop/1 Start), `charge_soc_setpoint` (%),
> `emergency_stop` (0 Normal/1 E-Stop). Always fetch the live list — don't hard-code.

### Command TypeScript types
```ts
export interface EmsRegister {
  id: string; address: number; signal_name: string; point_name: string;
  unit: string; category: string; description: string; rw: number; factor: number;
  latest: { value: number; quality: string; timestamp_utc: string } | null;
}
export interface EmsRegistersResponse {
  asset_id: "ems_system"; commands_enabled: boolean; write_access: boolean;
  count: number; items: EmsRegister[];
}
export interface EmsWriteRequest {
  signal_name?: string; point_id?: string; address?: number;   // one required
  value: number; readback?: boolean; note?: string;
}
export interface EmsWriteResult {
  audit_id: number; status: string; error_code: string | null;
  message: string | null; gateway_response: Record<string, unknown> | null;
}
```

---

## Quick reference

Reads are on the gateway (`ems-api.unityess.cloud`); **commands are on your backend
(`kinetic.unityess.cloud`)**.

| Method | Path | Base | Use |
|---|---|---|---|
| GET | `/api/health` | gateway | Gateway status badge |
| GET | `/api/assets` | gateway | Asset cards (`items[]`) |
| GET | `/api/telemetry/key-signals` | gateway | **Main dashboard** (all assets) |
| GET | `/api/assets/{id}/telemetry` | gateway | Detail page (+ `?category=`) |
| GET | `/api/alarms` | gateway | Alarm banner |
| GET | `/api/storage/points` | gateway | Signal history chart |
| GET | `/api/storage/snapshots` | gateway | Recent snapshots |
| WS | `/ws/telemetry` | gateway | Live updates |
| GET | `/api/commands/ems/registers` | **backend** | List 93 write registers (🔒 operator) |
| POST | `/api/commands/ems/write` | **backend** | Write one register (🔒 operator) |
| POST | `/api/commands/ems/batch` | **backend** | Batch write (🔒 operator) |
| — | ~~`/api/stream/telemetry`~~ | — | ❌ gone (404) — do not use |
| — | ~~`/api/assets/{id}/key-signals`~~ | — | ❌ 404 on live gateway |

Don't use in the dashboard: `/api/registers/raw`, `/api/registers/map`
(debug-only, huge).

---

## Architecture note (important)

This North dashboard reads **directly from the gateway** (simplest path for
telemetry) but sends **commands through your backend** (`kinetic.unityess.cloud`)
so the gateway's internal write-token stays secret and every command is audited.
Two more things to know:

1. **This is a different gateway than the original EMS one.** The URL
   `https://ems-api.unityess.cloud` now runs the **NorthBound** gateway (9 assets,
   WebSocket, read-only) — the old EMS gateway (3 assets, SSE, commands) is no
   longer at this URL. So the EMS backend's SSE ingestion will not work against it.
2. **No long-term history / no auth here.** If later you want unified login,
   long-term cloud history, or both EMS + North under one backend URL, that's a
   backend ingestion job (pull `/api/telemetry/key-signals` on a timer, store it).
   Ask and I'll build it.
