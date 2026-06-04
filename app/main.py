"""EMS backend API.

Consumes the i.MX93 gateway Web APIs (REST + SSE), stores telemetry / events /
command audit in PostgreSQL + TimescaleDB, and serves a fast dashboard API
(latest state, history, commands) for the web frontend.
"""
import asyncio
import contextlib
import json
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .bootstrap import run_bootstrap
from .config import settings
from .db import connect, disconnect, pool
from .gateway_client import GatewayClient
from .ingestion import Ingestor
from .routers import assets, auth, commands, events, telemetry

logging.basicConfig(level=settings.log_level)
log = logging.getLogger("app")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    await connect()
    app.state.gateway = GatewayClient()
    await run_bootstrap(app.state.gateway)

    ingest_task = None
    if settings.ingest_enabled:
        app.state.ingestor = Ingestor(app.state.gateway)
        ingest_task = asyncio.create_task(app.state.ingestor.run())
        log.info("Ingestion worker started.")
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
