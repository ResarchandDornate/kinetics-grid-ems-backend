"""EMS backend API.

Consumes the i.MX93 gateway Web APIs (REST + SSE), stores telemetry / events /
command audit in PostgreSQL + TimescaleDB, and serves a fast dashboard API
(latest state, history, commands) for the web frontend.
"""
import asyncio
import contextlib
import json
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

import jwt as _jwt
from .bootstrap import run_bootstrap
from .config import settings
from .db import connect, disconnect, pool
from .gateway_client import GatewayClient
from .ingestion import Ingestor
from .north_ingestion import NorthIngestor
from .routers import assets, auth, commands, events, telemetry

logging.basicConfig(level=settings.log_level)
log = logging.getLogger("app")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    await connect()
    app.state.gateway = GatewayClient()
    try:
        await app.state.gateway.login()
    except Exception as exc:  # noqa: BLE001
        log.warning("Gateway login failed at startup: %s", exc)
    await run_bootstrap(app.state.gateway)

    ingest_task = None
    if settings.ingest_enabled:
        if settings.gateway_type == "northbound":
            app.state.ingestor = NorthIngestor(app.state.gateway)
        else:
            app.state.ingestor = Ingestor(app.state.gateway)
        ingest_task = asyncio.create_task(app.state.ingestor.run())
        log.info("Ingestion worker started (gateway_type=%s).", settings.gateway_type)
    else:
        app.state.ingestor = None
        log.info("Ingestion disabled (INGEST_ENABLED=false).")

    try:
        yield
    finally:
        if app.state.ingestor:
            await app.state.ingestor.stop()
        if ingest_task:
            ingest_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ingest_task
        await app.state.gateway.close()
        await disconnect()


app = FastAPI(title="EMS Backend", version="0.1.0", lifespan=lifespan)

# Local dev: allow the frontend to call us directly. Tighten for production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(assets.router)
app.include_router(telemetry.router)
app.include_router(commands.router)
app.include_router(events.router)


@app.get("/api/health")
async def health():
    try:
        await pool().fetchval("SELECT 1")
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False
    return {
        "status": "ok" if db_ok else "error",
        "db": db_ok,
        "ingestion": settings.ingest_enabled,
        "gateway_base_url": settings.gateway_base_url,
    }


@app.get("/api/stream/telemetry")
async def stream_latest(interval: float = 1.0):
    """SSE stream for our own frontend: pushes the latest state for all assets
    from the DB cache every `interval` seconds. The frontend never has to touch
    the gateway directly, and this keeps working through gateway hiccups.
    """
    async def gen():
        while True:
            rows = await pool().fetch(
                """
                SELECT asset_id, ts, online, communication_status,
                       telemetry_json, error_text
                FROM ems_asset_latest_state ORDER BY asset_id
                """
            )
            payload = {
                "assets": {
                    r["asset_id"]: {
                        "online": r["online"],
                        "communication_status": r["communication_status"],
                        "telemetry": r["telemetry_json"],
                        "error": r["error_text"],
                        "ts": r["ts"].isoformat() if r["ts"] else None,
                    }
                    for r in rows
                }
            }
            yield f"data: {json.dumps(payload, default=str)}\n\n"
            await asyncio.sleep(interval)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ── WebSocket live telemetry (/ws/telemetry) ────────────────────────────────

_ws_clients: set[WebSocket] = set()


@app.websocket("/ws/telemetry")
async def ws_telemetry(websocket: WebSocket, token: str = ""):
    # Validate the JWT passed as ?token= query param.
    try:
        _jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except _jwt.PyJWTError:
        await websocket.close(code=4001)
        return
    await websocket.accept()
    _ws_clients.add(websocket)
    try:
        while True:
            rows = await pool().fetch(
                """
                SELECT asset_id, ts, online, communication_status,
                       telemetry_json, error_text
                FROM ems_asset_latest_state ORDER BY asset_id
                """
            )
            payload = {
                "assets": {
                    r["asset_id"]: {
                        "online": r["online"],
                        "last_update_utc": r["ts"].isoformat() if r["ts"] else None,
                        "signals": r["telemetry_json"] or {},
                    }
                    for r in rows
                }
            }
            await websocket.send_text(json.dumps(payload, default=str))
            await asyncio.sleep(2)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _ws_clients.discard(websocket)


# ── Dashboard stub endpoints expected by northbound-ems-dashboard ────────────

@app.get("/api/alarms")
async def get_alarms():
    rows = await pool().fetch(
        "SELECT asset_id, event_type, severity, message, timestamp FROM ems_asset_events "
        "WHERE severity IN ('warning','error','critical') ORDER BY timestamp DESC LIMIT 200"
    )
    return {
        "alarms": [
            {
                "id": i,
                "asset_id": r["asset_id"],
                "type": r["event_type"],
                "severity": r["severity"],
                "message": r["message"],
                "ts": r["timestamp"].isoformat() if r["timestamp"] else None,
            }
            for i, r in enumerate(rows)
        ],
        "total": len(rows),
    }


@app.get("/api/telemetry/key-signals")
async def get_key_signals():
    rows = await pool().fetch(
        "SELECT asset_id, telemetry_json FROM ems_asset_latest_state ORDER BY asset_id"
    )
    return {
        "assets": {
            r["asset_id"]: {"signals": r["telemetry_json"] or {}}
            for r in rows
        }
    }


@app.get("/api/telemetry")
async def get_full_telemetry():
    rows = await pool().fetch(
        "SELECT asset_id, ts, online, communication_status, telemetry_json, error_text "
        "FROM ems_asset_latest_state ORDER BY asset_id"
    )
    online_count = sum(1 for r in rows if r["online"])
    return {
        "assets": {
            r["asset_id"]: {
                "asset_id": r["asset_id"],
                "online": r["online"],
                "last_update_utc": r["ts"].isoformat() if r["ts"] else None,
                "signals": r["telemetry_json"] or {},
            }
            for r in rows
        },
        "total_asset_count": len(rows),
        "online_count": online_count,
    }


@app.get("/api/assets/{asset_id}/telemetry")
async def get_asset_telemetry(asset_id: str, compact: bool = True, category: str = "", page: int = 1, page_size: int = 500):
    row = await pool().fetchrow(
        "SELECT asset_id, ts, online, telemetry_json FROM ems_asset_latest_state WHERE asset_id = $1",
        asset_id,
    )
    if row is None:
        return {"asset_id": asset_id, "online": None, "signals": {}, "signal_count": 0}
    signals = row["telemetry_json"] or {}
    return {
        "asset_id": row["asset_id"],
        "online": row["online"],
        "last_update_utc": row["ts"].isoformat() if row["ts"] else None,
        "signals": signals,
        "signal_count": len(signals),
        "pagination": {"page": 1, "has_more": False, "total": len(signals)},
    }


@app.get("/api/logs")
async def get_logs(severity: str = "", asset_id: str = "", source: str = "", search: str = "", limit: int = 100, order: str = "desc"):
    order_clause = "DESC" if order != "asc" else "ASC"
    rows = await pool().fetch(
        f"SELECT asset_id, event_type, severity, message, timestamp FROM ems_asset_events "
        f"ORDER BY timestamp {order_clause} LIMIT $1",
        min(limit, 500),
    )
    items = [
        {
            "id": i,
            "asset_id": r["asset_id"],
            "event_type": r["event_type"],
            "severity": r["severity"],
            "message": r["message"],
            "timestamp_utc": r["timestamp"].isoformat() if r["timestamp"] else None,
            "source": "gateway",
        }
        for i, r in enumerate(rows)
    ]
    return {"items": items, "total": len(items)}


@app.get("/api/logs/summary")
async def get_logs_summary():
    try:
        row = await pool().fetchrow(
            "SELECT COUNT(*) FILTER (WHERE severity='critical') AS critical, "
            "COUNT(*) FILTER (WHERE severity='error') AS error, "
            "COUNT(*) FILTER (WHERE severity='warning') AS warning, "
            "COUNT(*) AS total FROM ems_asset_events"
        )
        return {k: (row[k] or 0) for k in ["critical", "error", "warning", "total"]}
    except Exception:  # noqa: BLE001
        return {"total": 0}


@app.get("/api/logs/filters")
async def get_logs_filters():
    rows = await pool().fetch("SELECT DISTINCT asset_id FROM ems_asset_events ORDER BY asset_id")
    return {"asset_ids": [r["asset_id"] for r in rows], "severities": ["info", "warning", "error", "critical"]}


@app.get("/api/config/runtime")
async def get_runtime_config():
    return {
        "gateway_type": settings.gateway_type,
        "gateway_id": settings.gateway_id,
        "gateway_base_url": settings.gateway_base_url,
        "ingest_enabled": settings.ingest_enabled,
        "ingest_batch_size": settings.ingest_batch_size,
        "ingest_flush_seconds": settings.ingest_flush_seconds,
        "store_on_change": settings.store_on_change,
        "numeric_deadband": settings.numeric_deadband,
    }


@app.get("/api/storage/health")
async def get_storage_health():
    try:
        await pool().fetchval("SELECT 1")
        return {"status": "ok", "backend": "timescaledb"}
    except Exception:  # noqa: BLE001
        return {"status": "error"}


@app.get("/api/storage/status")
async def get_storage_status():
    count = await pool().fetchval("SELECT COUNT(*) FROM ems_telemetry_samples") or 0
    return {"total_samples": count, "backend": "timescaledb"}


@app.get("/api/storage/snapshots")
async def get_storage_snapshots(asset_id: str = "", limit: int = 10):
    q = "SELECT asset_id, ts, online, telemetry_json FROM ems_asset_latest_state"
    args: list = []
    if asset_id:
        q += " WHERE asset_id = $1"
        args.append(asset_id)
    rows = await pool().fetch(q, *args)
    return {
        "snapshots": [
            {
                "asset_id": r["asset_id"],
                "timestamp_utc": r["ts"].isoformat() if r["ts"] else None,
                "online": r["online"],
                "signals": r["telemetry_json"] or {},
            }
            for r in rows[:limit]
        ]
    }


@app.get("/api/storage/points")
async def get_storage_points(asset_id: str, signal_name: str, limit: int = 100):
    rows = await pool().fetch(
        "SELECT ts, value_numeric AS value FROM ems_telemetry_samples "
        "WHERE asset_id = $1 AND field_key = $2 ORDER BY ts DESC LIMIT $3",
        asset_id, signal_name, min(limit, 2000),
    )
    return {"points": [{"ts": r["ts"].isoformat(), "value": r["value"]} for r in rows]}


@app.get("/api/registers/map")
async def get_registers_map():
    return {"registers": {}, "note": "Register map not available for NorthBound gateway"}


@app.get("/api/registers/raw")
async def get_registers_raw(asset_id: str = "", category: str = "", key_only: bool = False):
    q = "SELECT asset_id, telemetry_json FROM ems_asset_latest_state"
    args: list = []
    if asset_id:
        q += " WHERE asset_id = $1"
        args.append(asset_id)
    rows = await pool().fetch(q, *args)
    result = []
    for r in rows:
        for k, v in (r["telemetry_json"] or {}).items():
            if not isinstance(v, (dict, list)):
                result.append({"asset_id": r["asset_id"], "key": k, "value": v})
    return {"registers": result, "total": len(result)}


@app.get("/api/server-upload/status")
async def get_server_upload_status():
    return {"supported": False, "status": "not_applicable"}
