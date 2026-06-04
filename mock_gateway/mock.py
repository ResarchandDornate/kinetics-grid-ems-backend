"""Mock i.MX93 EMS gateway.

Reproduces the gateway's Web API (port 8000) that the backend consumes, driven
by the SAME canonical field spec the backend seeds its dictionary from
(app/field_spec.py). That guarantees the mock emits EVERY documented field with
the EXACT production name/type/unit -- zero drift between mock and real gateway.

Swap GATEWAY_BASE_URL back to the real i.MX93 IP later; no backend changes.
"""
import asyncio
import json
import math
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.field_spec import FIELD_SPEC, display_name

app = FastAPI(title="Mock i.MX93 Gateway")

GATEWAY_ID = "imx93_gateway_1"

ASSETS = [
    {"asset_id": "bms_1", "asset_key": "bms", "asset_type": "bms",
     "protocol": "modbus_tcp", "vendor": None, "enabled": True, "running": True, "online": True},
    {"asset_id": "pcs_1", "asset_key": "pcs", "asset_type": "pcs",
     "protocol": "modbus_tcp", "vendor": "njoy", "enabled": True, "running": True, "online": True},
    {"asset_id": "chiller_1", "asset_key": "chiller", "asset_type": "chiller",
     "protocol": "modbus_rtu", "vendor": None, "enabled": True, "running": True, "online": True},
]
_ID_TO_TYPE = {"bms_1": "bms", "pcs_1": "pcs", "chiller_1": "chiller"}

# Mutable setpoints so SET_TEMP / PCS_SET_ACTIVE_POWER have a visible effect.
_state = {"chiller_set_temp": 18.0, "pcs_active_power": 0.0}

# (base, amplitude, period_seconds) for believable analog waves.
RANGES = {
    # BMS
    "soc_percent": (65, 20, 600), "soh_percent": (98.5, 0, 1),
    "rack_inner_soc_percent": (64, 20, 600),
    "rack_voltage_v": (750, 15, 300), "rack_current_a": (0, 120, 120),
    "power_kw": (0, 90, 120),
    "max_allowed_charge_current_a": (200, 0, 1),
    "max_allowed_discharge_current_a": (200, 0, 1),
    "max_cell_voltage_mv": (3320, 25, 90), "min_cell_voltage_mv": (3290, 25, 110),
    "avg_cell_voltage_mv": (3305, 5, 100), "cell_voltage_diff_mv": (30, 8, 70),
    "max_cell_temp_c": (31, 3, 400), "min_cell_temp_c": (28, 3, 420),
    "avg_temp_c": (29.5, 2, 400),
    "insulation_resistance_kohm": (5000, 200, 600),
    "positive_insulation_resistance_kohm": (5200, 200, 600),
    "negative_insulation_resistance_kohm": (5100, 200, 600),
    # PCS
    "ab_voltage_v": (400, 3, 90), "bc_voltage_v": (400, 3, 95), "ca_voltage_v": (400, 3, 100),
    "phase_a_voltage_v": (230, 2, 90), "phase_b_voltage_v": (230, 2, 95),
    "phase_c_voltage_v": (230, 2, 100),
    "phase_a_current_a": (100, 30, 120), "phase_b_current_a": (100, 30, 125),
    "phase_c_current_a": (100, 30, 130), "frequency_hz": (50, 0.05, 60),
    "active_power_kw": (0, 80, 180), "reactive_power_kvar": (0, 10, 200),
    "apparent_power_kva": (80, 10, 180), "power_factor": (0.98, 0.02, 240),
    "bus_voltage_v": (760, 10, 300), "battery_voltage_v": (750, 12, 300),
    "battery_current_a": (0, 110, 120), "dc_power_kw": (0, 78, 180),
    "dc_total_current_a": (0, 110, 120), "igbt_temperature_c": (45, 6, 300),
    "ambient_temperature_c": (30, 4, 600), "inductance_temperature_c": (40, 5, 300),
    # Chiller (temps centred on the setpoint, see value_for)
    "outlet_water_pressure": (3.2, 0.2, 200), "return_water_pressure": (2.8, 0.2, 210),
    "ambient_temp": (33, 3, 600),
}

# String/status fields -> believable steady value.
STRINGS = {
    "communication_status": "online", "comm_status": "online", "status": "ok",
    "current_state": "running", "bcu_state": "idle", "precharge_stage": "complete",
    "operating_status": "running", "grid_offgrid_status": "on_grid",
    "water_pump": "on", "compressor1": "on", "compressor2": "off",
    "electric_heater": "off", "condensate_fan": "on", "makeup_pump": "off",
    "last_error": "", "error": "",
}
INTS = {"alarm_count": 0, "fault_code": 0, "control_mode": 2,
        "operating_status_raw": 3, "grid_offgrid_status_raw": 1}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _wave(t: float, base: float, amp: float, period: float) -> float:
    if amp == 0:
        return base
    return round(base + amp * math.sin(2 * math.pi * t / period), 2)


def value_for(field_key: str, dtype: str, t: float):
    """Produce one believable value for a field, honouring command setpoints."""
    if dtype == "number":
        # Chiller temperatures track the current setpoint so SET_TEMP shows.
        if field_key == "set_temperature":
            return _state["chiller_set_temp"]
        if field_key == "outlet_water_temp":
            return _wave(t, _state["chiller_set_temp"], 0.8, 120)
        if field_key == "return_water_temp":
            return _wave(t, _state["chiller_set_temp"] + 4, 0.8, 130)
        if field_key == "active_power_kw" and _state["pcs_active_power"]:
            return round(_state["pcs_active_power"], 2)
        base, amp, period = RANGES.get(field_key, (50, 10, 120))
        return _wave(t, base, amp, period)
    if dtype == "integer":
        return INTS.get(field_key, 0)
    if dtype == "boolean":
        return False if field_key == "fault_status" else True
    if dtype == "datetime":
        return _now()
    if dtype == "array":
        return []
    # string
    return STRINGS.get(field_key, "ok")


def build_telemetry(asset_type: str, asset_id: str, t: float) -> dict:
    tel = {"gateway_id": GATEWAY_ID, "asset_id": asset_id,
           "asset_type": asset_type, "timestamp": _now()}
    for key, dtype, _unit, _group, _hist, _evt in FIELD_SPEC[asset_type]:
        tel[key] = value_for(key, dtype, t)
    return tel


def all_telemetry(t: float) -> dict:
    return {
        "status": "ok", "gateway_id": GATEWAY_ID, "timestamp": _now(),
        "assets": {
            a["asset_id"]: {
                "asset_id": a["asset_id"], "asset_type": a["asset_type"],
                "online": True,
                "telemetry": build_telemetry(a["asset_type"], a["asset_id"], t),
            }
            for a in ASSETS
        },
    }


def keys_response(asset_id: str) -> dict:
    asset_type = _ID_TO_TYPE[asset_id]
    fields = FIELD_SPEC[asset_type]
    keys = [f[0] for f in fields]
    groups: dict[str, list[str]] = {}
    for key, _dt, _u, group, _h, _e in fields:
        groups.setdefault(group, []).append(key)
    groups["all"] = keys
    return {"status": "ok", "asset_id": asset_id, "asset_type": asset_type,
            "keys_count": len(keys), "keys": keys, "groups": groups}


# Monotonic counter for non-streaming endpoints (avoids importing time;
# resets on restart, which is fine for a mock).
_counter = {"n": 0}


def _tick() -> int:
    _counter["n"] += 1
    return _counter["n"]


@app.get("/api/gateway/health")
async def health():
    return {"status": "ok", "gateway_id": GATEWAY_ID, "timestamp": _now()}


@app.get("/api/assets")
async def assets():
    return {"status": "ok", "gateway_id": GATEWAY_ID, "timestamp": _now(),
            "assets_count": len(ASSETS), "assets": ASSETS}


@app.get("/api/telemetry/latest")
async def telemetry_latest():
    return all_telemetry(_tick())


@app.get("/api/assets/{asset_id}/telemetry/latest")
async def asset_latest(asset_id: str):
    if asset_id not in _ID_TO_TYPE:
        return JSONResponse(status_code=404,
                            content={"status": "error", "error_code": "INVALID_ASSET"})
    return {"status": "ok", "asset_id": asset_id,
            "telemetry": build_telemetry(_ID_TO_TYPE[asset_id], asset_id, _tick())}


@app.get("/api/assets/{asset_id}/telemetry/keys")
async def asset_keys(asset_id: str):
    if asset_id not in _ID_TO_TYPE:
        return JSONResponse(status_code=404,
                            content={"status": "error", "error_code": "INVALID_ASSET"})
    return keys_response(asset_id)


@app.post("/api/assets/{asset_id}/commands")
async def command(asset_id: str, request: Request):
    body = await request.json()
    cmd = body.get("command")
    if not cmd:
        return JSONResponse(status_code=400, content={
            "status": "error", "error_code": "MISSING_COMMAND",
            "message": "Missing command", "timestamp": _now()})
    if asset_id == "chiller_1" and cmd == "SET_TEMP" and body.get("value") is not None:
        _state["chiller_set_temp"] = float(body["value"])
    if asset_id == "pcs_1" and cmd == "PCS_SET_ACTIVE_POWER" and body.get("value") is not None:
        _state["pcs_active_power"] = float(body["value"])
    return {"status": "ok", "asset_id": asset_id, "command": cmd,
            "message": f"Command {cmd} accepted", "timestamp": _now()}


@app.get("/api/logs/events")
async def log_events(asset_id: str, limit: int = 100):
    rows = [{"timestamp": _now(), "asset_id": asset_id,
             "event_type": "communication_restored", "status": "ok",
             "message": "Asset communication online"}]
    return {"status": "ok", "rows_count": len(rows), "rows": rows}


@app.get("/api/stream/telemetry")
async def stream():
    async def gen():
        t = 0
        while True:
            yield f"data: {json.dumps(all_telemetry(t))}\n\n"
            t += 1
            await asyncio.sleep(1.0)
    return StreamingResponse(gen(), media_type="text/event-stream")
