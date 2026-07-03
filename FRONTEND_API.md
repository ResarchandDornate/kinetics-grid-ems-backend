# EMS Backend API — Frontend Integration Guide

API reference for the EMS web dashboard. All examples below are **real responses**
captured from the running backend.

- **Base URL (deployed — use this):** `https://kinetic.unityess.cloud`
- **Base URL (local dev):** `http://localhost:8080` (only on the same PC as the backend)
- **Content type:** `application/json`
- **Auth:** JWT bearer token. Read endpoints are open; **command (control)
  endpoints require a logged-in `operator`**. See [Authentication](#authentication).
- **CORS:** open (`*`) in dev.

> Set one base URL in your app and prefix every path with it, e.g.
> `https://kinetic.unityess.cloud/api/telemetry/latest`.

> The frontend talks **only to this backend**, never to the EMS gateway directly.
> The backend caches latest values and history, so the dashboard stays fast and
> keeps working even if the gateway blips.

> **Telemetry fields are discovered dynamically — never hard-code them.** The
> gateway (v2) exposes more fields than any example below shows (PCS ~41, BMS
> ~31, Chiller ~20). Always build asset detail pages from
> `GET /api/assets/{id}/telemetry/keys` (gives every field's label, unit, and
> group), then read live values from `/telemetry/latest` + the SSE stream. New
> gateway fields then appear automatically with no frontend changes.

## Conventions

- All timestamps are ISO-8601 UTC (e.g. `2026-06-03T09:31:37.507552Z`).
- Asset IDs: `bms_1`, `pcs_1`, `chiller_1`. Asset types: `bms`, `pcs`, `chiller`.
- Numeric telemetry can be `null` when an asset is offline — **render as "—", never 0.**
- `online: true` at the API layer does **not** mean the asset is healthy; check the
  asset's `online` / `communication_status` fields.

## Recommended data flow

```
0. POST /api/auth/login                     → get JWT, store it
1. GET /api/health                          → is the backend up?
2. GET /api/assets                          → list cards (online/running)
3. GET /api/telemetry/latest                → initial dashboard values
4. open EventSource /api/stream/telemetry   → live updates (replaces polling)
5. GET /api/assets/{id}/telemetry/keys      → field labels/units/groups for detail pages
6. GET /api/assets/{id}/telemetry/timeseries→ charts
7. GET /api/assets/{id}/commands            → render command panel
   POST /api/assets/{id}/commands           → send a control command (needs operator token)
```

---

## Authentication

JWT bearer tokens. Flow: **sign up → log in → store `access_token` → send it as
`Authorization: Bearer <token>` on protected calls.**

- **Read endpoints** (assets, telemetry, charts, SSE) do **not** require a token in v1.
- **`POST /api/assets/{id}/commands` requires a token with role `operator` or `admin`.**
- Roles: `viewer` (read only) · `operator` (may send commands) · `admin`.
- Token lifetime: 12h by default. On `401`, send the user back to login.

### Sign up
`POST /api/auth/signup`
```json
// request
{ "username": "kedar", "password": "secret123", "email": "kedar@ornatesolar.com", "role": "operator" }
```
```json
// response 201
{ "id": 1, "username": "kedar", "email": "kedar@ornatesolar.com", "role": "operator" }
```
- `email` and `role` are optional (`role` defaults to `viewer`). `password` min 6 chars.
- 409 `USER_ALREADY_EXISTS` if the username/email is taken.
- Note: in production the backend can be configured to force every new signup to
  `viewer` (operators are then promoted by an admin), so don't rely on requesting
  `operator` at signup for the deployed system.

### Log in
`POST /api/auth/login`
```json
// request
{ "username": "kedar", "password": "secret123" }
```
```json
// response 200
{ "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...", "token_type": "bearer",
  "username": "kedar", "role": "operator" }
```
401 `INVALID_CREDENTIALS` on bad username/password.

### Current user
`GET /api/auth/me`  ·  header `Authorization: Bearer <token>`
```json
{ "id": 1, "username": "kedar", "email": "kedar@ornatesolar.com", "role": "operator" }
```
Use this on app load to validate a stored token and get the role (to show/hide
the command panel). 401 `UNAUTHORIZED` if the token is missing/expired/invalid.

### Attaching the token (example)
```js
const res = await fetch(`${BASE}/api/assets/chiller_1/commands`, {
  method: "POST",
  headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` },
  body: JSON.stringify({ command: "SET_TEMP", value: 18 }),
});
```

---

## Endpoints

### Health
`GET /api/health`
```json
{ "status": "ok", "db": true, "ingestion": true, "gateway_base_url": "http://mock-gateway:9000" }
```

### List assets (cards)
`GET /api/assets`
```json
[
  {
    "asset_id": "bms_1", "gateway_id": "imx93_gateway_1", "asset_key": "bms",
    "asset_type": "bms", "protocol": "modbus_tcp", "vendor": null,
    "enabled": true, "running": true, "online": true,
    "updated_at": "2026-06-03T09:31:04.102882Z"
  }
]
```
`GET /api/assets/{asset_id}` returns a single asset (same shape). 404 `INVALID_ASSET` if unknown.

### Latest telemetry (all assets)
`GET /api/telemetry/latest`
```json
[
  {
    "asset_id": "bms_1",
    "ts": "2026-06-03T09:31:37.507552Z",
    "online": true,
    "communication_status": "online",
    "telemetry": { "soc_percent": 71.77, "rack_voltage_v": 759.56, "power_kw": 88.89, "...": "..." },
    "error_text": null
  }
]
```

### Latest telemetry (one asset)
`GET /api/assets/{asset_id}/telemetry/latest`
```json
{
  "asset_id": "bms_1",
  "ts": "2026-06-03T09:31:37.507552Z",
  "online": true,
  "communication_status": "online",
  "telemetry": {
    "soc_percent": 71.77, "soh_percent": 98.5, "rack_voltage_v": 759.56,
    "rack_current_a": 118.52, "power_kw": 88.89, "max_cell_temp_c": 32.49,
    "current_state": "running", "alarm_count": 0, "active_alarms": [], "...": "..."
  },
  "error_text": null
}
```
404 `ASSET_TELEMETRY_NOT_FOUND` if no data yet.

### Field dictionary (labels / units / groups for detail pages)
`GET /api/assets/{asset_id}/telemetry/keys`
```json
{
  "asset_id": "bms_1",
  "asset_type": "bms",
  "keys_count": 29,
  "keys": [
    {
      "field_key": "soc_percent", "display_name": "Soc Percent", "unit": "%",
      "group_name": "stack_level", "data_type": "number",
      "store_history": true, "event_trigger": false
    }
  ]
}
```
Use `group_name` to lay out sections, `unit` for axis/labels, `display_name` as a
default label (override with your own copy as needed). Groups per asset:
- **bms:** `stack_level`, `voltage_current_power`, `temperature`, `alarms_status`
- **pcs:** `ac_side`, `dc_side`, `power_energy`, `status_faults`, `thermal`
- **chiller:** `temperatures`, `pressures`, `status`, `settings`

### Historical chart
`GET /api/assets/{asset_id}/telemetry/timeseries`

| Query param | Required | Default | Notes |
|---|---|---|---|
| `field_key` | yes | — | e.g. `soc_percent` |
| `start` | no | 1h ago | ISO-8601 |
| `end` | no | now | ISO-8601 |
| `resolution` | no | `auto` | `auto` \| `raw` \| `1m` |

`auto` returns raw 1 Hz points for ranges ≤ 6h, and 1-minute averages for longer
ranges (so year-long charts stay fast). `resolution` in the response tells you which.
```json
{
  "asset_id": "bms_1", "field_key": "soc_percent", "resolution": "raw",
  "points": [
    { "ts": "2026-06-03T08:43:51.528673Z", "value": 45.16 },
    { "ts": "2026-06-03T08:43:52.528966Z", "value": 45.13 }
  ]
}
```
> For step/status fields, points only appear when the value changes (plus a
> 5-min heartbeat). **Hold the last value between points** when drawing.

### Available commands (command panel)
`GET /api/assets/{asset_id}/commands`
```json
{
  "asset_id": "chiller_1", "asset_type": "chiller",
  "commands": [
    { "command": "CHILLER_ON", "label": "Turn On", "value_required": false,
      "value_type": null, "unit": null, "category": "control" },
    { "command": "SET_TEMP", "label": "Set Temperature", "value_required": true,
      "value_type": "number", "unit": "C", "category": "setpoint" }
  ]
}
```
Render `value_required` commands with an input of `value_type`; show `unit` next to it.
`category` is `read` | `control` | `setpoint` for grouping/styling.

### Send a command  🔒 operator
`POST /api/assets/{asset_id}/commands` — **requires `Authorization: Bearer <token>`
with role `operator`+.** Returns 403 `FORBIDDEN` for viewers, 401 if no/invalid token.
```json
// request body  (requested_by is ignored — the audit uses the logged-in user)
{ "command": "SET_TEMP", "value": 16.5 }
```
```json
// response
{
  "audit_id": 1, "status": "ok", "error_code": null,
  "message": "Command SET_TEMP accepted",
  "gateway_response": { "status": "ok", "asset_id": "chiller_1", "command": "SET_TEMP", "...": "..." }
}
```
- `value` is omitted for commands where `value_required` is false.
- Always **confirm in the UI** before sending (these can affect real hardware) and
  **debounce** — don't fire repeatedly.
- On `status: "error"`, show `message` to the user; do not auto-retry.

### Command audit history
`GET /api/assets/{asset_id}/commands/audit?limit=50` → list of past commands with
`request_ts`, `response_ts`, `command`, `status`, `message`, `requested_by`.

---

## EMS Command Panel (NorthBound write control)  🔒 operator

The NorthBound gateway exposes **93 writable EMS command registers** (`ems_system`
asset only). These let an operator control the plant — modes, start/stop,
setpoints, SOC limits, emergency stop, etc. Your backend proxies them so the
frontend never needs the gateway's internal token.

- **All three endpoints require an `operator` (or `admin`) token** — viewers get `403`.
- Available only on the NorthBound gateway (else `404`).
- Every write is recorded in the command audit trail (who/what/when + gateway response).
- Always **confirm in the UI** before sending, and **debounce** — these affect real hardware.

### List writable registers (build the panel)
`GET /api/commands/ems/registers`  ·  header `Authorization: Bearer <token>`
```json
{
  "asset_id": "ems_system",
  "commands_enabled": true,
  "write_access": true,
  "count": 93,
  "items": [
    {
      "id": "p0003", "address": 4, "register_qty": 2,
      "point_name": "Remote Mode", "signal_name": "remote_mode",
      "point_type": "float32", "unit": "", "category": "status",
      "description": "0, Local; 1, Remote", "rw": 1, "factor": 1.0,
      "latest": { "value": 1.0, "quality": "good", "timestamp_utc": "..." }
    }
  ]
}
```
Render each `item` as a control: use `point_name` as the label, `signal_name` as
the target, `description` to build the value dropdown (e.g. `0=Local, 1=Remote`),
and `latest.value` to show the current state.

### Write one register
`POST /api/commands/ems/write`  ·  operator token
```json
// request — identify the register by signal_name (preferred), point_id, or address
{ "signal_name": "remote_mode", "value": 1, "readback": true, "note": "reason" }
```
```json
// response
{
  "audit_id": 5, "status": "ok", "error_code": null, "message": null,
  "gateway_response": {
    "ok": true, "asset_id": "ems_system", "signal_name": "remote_mode",
    "requested_value": 1, "readback_value": 1.0, "description": "0, Local; 1, Remote"
  }
}
```
- On success: `status: "ok"` and `gateway_response.readback_value` confirms the new value.
- On failure: `status: "error"` with `error_code` (e.g. `403` role, `404` bad register,
  `502` Modbus write failed, `401`/`530` gateway auth/down). Show `message` to the user.

### Batch write (commissioning workflows)
`POST /api/commands/ems/batch`  ·  operator token
```json
// request (max 50 writes)
{
  "writes": [
    { "signal_name": "remote_mode", "value": 1, "readback": true },
    { "signal_name": "manual_auto_mode", "value": 1, "readback": true }
  ],
  "continue_on_error": false
}
```
```json
// response
{ "ok": true, "success_count": 2, "error_count": 0,
  "results": [ { "audit_id": 6, "status": "ok", "...": "..." } ] }
```
Prefer single-write for operator actions (clean per-action audit); use batch only
for predefined, pre-validated commissioning sequences.

> **Common register examples** (from the gateway's 93): `remote_mode` (0 Local /
> 1 Remote), `manual_auto_mode` (0 Manual / 1 Auto), `start_command` (0 Stop /
> 1 Start), `charge_value_setpoint` (kW), `charge_soc_setpoint` (%),
> `emergency_stop` (0 Normal / 1 E-Stop). Always fetch the live list from
> `/api/commands/ems/registers` — don't hard-code.

### Events / alarms
`GET /api/assets/{asset_id}/events?limit=100`
```json
[
  { "id": 12, "timestamp": "2026-06-03T09:30:00Z", "event_type": "fault_detected",
    "severity": "warning", "status": "active", "command": null,
    "message": "...", "error_text": null, "details_json": {} }
]
```
(Returns `[]` until events exist. `POST /api/assets/{id}/events/sync` pulls latest
from the gateway log server — usually a backend/cron concern, not the UI.)

---

## Live updates (SSE)

`GET /api/stream/telemetry` — Server-Sent Events; one `data:` JSON object per tick
(default ~1s), keyed by asset_id. Use the browser's `EventSource`:

```js
const es = new EventSource("https://kinetic.unityess.cloud/api/stream/telemetry");

es.onmessage = (e) => {
  const { assets } = JSON.parse(e.data);
  // assets = { bms_1: { online, communication_status, telemetry, error, ts }, ... }
  updateDashboard(assets);
};

es.onerror = () => {
  // EventSource auto-reconnects; optionally show a "reconnecting" badge.
};
```
Payload shape:
```json
{
  "assets": {
    "bms_1": {
      "online": true, "communication_status": "online",
      "telemetry": { "soc_percent": 71.8, "...": "..." },
      "error": null, "ts": "2026-06-03T09:31:37Z"
    },
    "pcs_1":     { "...": "..." },
    "chiller_1": { "...": "..." }
  }
}
```
Pattern: load initial state with `GET /api/telemetry/latest`, then keep it fresh via SSE.

---

## Error format

Errors use standard HTTP status codes. FastAPI wraps the spec error codes in `detail`:
```json
{ "detail": "INVALID_ASSET" }
```
Common codes: `INVALID_ASSET` (404), `ASSET_TELEMETRY_NOT_FOUND` (404),
`MISSING_COMMAND` (422 from validation). For command forwarding failures, the 200
response carries `status: "error"` with `error_code: "GATEWAY_UNREACHABLE"`.

---

## TypeScript types (copy into the frontend)

```ts
export type AssetType = "bms" | "pcs" | "chiller";
export type Role = "viewer" | "operator" | "admin";

export interface SignupRequest {
  username: string; password: string; email?: string; role?: Role;
}
export interface LoginRequest { username: string; password: string; }
export interface TokenResponse {
  access_token: string; token_type: "bearer"; username: string; role: Role;
}
export interface UserOut { id: number; username: string; email: string | null; role: Role; }

export interface Asset {
  asset_id: string;
  gateway_id: string | null;
  asset_key: string | null;
  asset_type: AssetType | null;
  protocol: string | null;
  vendor: string | null;
  enabled: boolean | null;
  running: boolean | null;
  online: boolean | null;
  updated_at: string | null;
}

export interface LatestState {
  asset_id: string;
  ts: string | null;
  online: boolean | null;
  communication_status: string | null;
  telemetry: Record<string, number | string | boolean | null | unknown[]> | null;
  error_text: string | null;
}

export interface FieldKey {
  field_key: string;
  display_name: string | null;
  unit: string | null;
  group_name: string | null;
  data_type: "number" | "integer" | "boolean" | "string" | "datetime" | "array";
  store_history: boolean;
  event_trigger: boolean;
}

export interface TimeseriesResponse {
  asset_id: string;
  field_key: string;
  resolution: "raw" | "1m";
  points: { ts: string; value: number | null }[];
}

export interface CommandDef {
  command: string;
  label: string;
  value_required: boolean;
  value_type: "number" | "integer" | null;
  unit: string | null;
  category: "read" | "control" | "setpoint";
}

export interface CommandResult {
  audit_id: number;
  status: string;
  error_code: string | null;
  message: string | null;
  gateway_response: Record<string, unknown> | null;
}

// --- EMS command registers (NorthBound write control) ---
export interface EmsRegister {
  id: string;
  address: number;
  register_qty: number;
  point_name: string;
  signal_name: string;
  point_type: string;
  unit: string;
  category: string;
  description: string;
  rw: number;
  factor: number;
  latest: { value: number; quality: string; timestamp_utc: string } | null;
}
export interface EmsRegistersResponse {
  asset_id: "ems_system";
  commands_enabled: boolean;
  write_access: boolean;
  count: number;
  items: EmsRegister[];
}
export interface EmsWriteRequest {
  signal_name?: string;   // one of signal_name / point_id / address required
  point_id?: string;
  address?: number;
  value: number;
  readback?: boolean;     // default true
  note?: string;
}
export interface EmsBatchRequest {
  writes: EmsWriteRequest[];      // 1..50
  continue_on_error?: boolean;
}

export interface StreamPayload {
  assets: Record<string, {
    online: boolean | null;
    communication_status: string | null;
    telemetry: Record<string, unknown> | null;
    error: string | null;
    ts: string | null;
  }>;
}
```

---

## Quick reference

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/signup` | Create a user |
| POST | `/api/auth/login` | Get JWT token |
| GET | `/api/auth/me` | Current user (🔒 token) |
| GET | `/api/health` | Backend liveness |
| GET | `/api/assets` | Asset cards |
| GET | `/api/assets/{id}` | One asset |
| GET | `/api/telemetry/latest` | Latest for all assets |
| GET | `/api/assets/{id}/telemetry/latest` | Latest for one asset |
| GET | `/api/assets/{id}/telemetry/keys` | Field labels/units/groups |
| GET | `/api/assets/{id}/telemetry/timeseries` | Historical chart data |
| GET | `/api/stream/telemetry` | **SSE** live updates |
| GET | `/api/assets/{id}/commands` | Available commands |
| POST | `/api/assets/{id}/commands` | Send a command (🔒 operator) |
| GET | `/api/assets/{id}/commands/audit` | Command history |
| GET | `/api/commands/ems/registers` | List 93 EMS write registers (🔒 operator) |
| POST | `/api/commands/ems/write` | Write one EMS register (🔒 operator) |
| POST | `/api/commands/ems/batch` | Batch write EMS registers (🔒 operator) |
| GET | `/api/assets/{id}/events` | Events / alarms |

Interactive docs (try every endpoint live): **`https://kinetic.unityess.cloud/docs`**
(or `http://localhost:8080/docs` when running the backend locally).
