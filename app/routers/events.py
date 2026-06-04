"""Events / alarms endpoint, plus an on-demand sync from the gateway log API.

The gateway exposes event history on port 7000. We pull it and upsert into
ems_asset_events, deduplicating on (asset_id + timestamp + event_type + command)
so repeated syncs don't create duplicates.
"""
import hashlib
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from ..config import settings
from ..db import pool
from ..gateway_client import GatewayClient

log = logging.getLogger("events")
router = APIRouter(prefix="/api", tags=["events"])


def _parse_ts(value) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _dedupe_key(asset_id: str, row: dict) -> str:
    raw = "|".join(str(row.get(k, "")) for k in
                   ("timestamp", "event_type", "command", "message"))
    return hashlib.sha1(f"{asset_id}|{raw}".encode()).hexdigest()


@router.get("/assets/{asset_id}/events")
async def list_events(asset_id: str, limit: int = 100):
    rows = await pool().fetch(
        """
        SELECT id, timestamp, event_type, severity, status, command,
               message, error_text, details_json
        FROM ems_asset_events
        WHERE asset_id = $1 ORDER BY timestamp DESC LIMIT $2
        """,
        asset_id, limit,
    )
    return [dict(r) for r in rows]


@router.post("/assets/{asset_id}/events/sync")
async def sync_events(asset_id: str, request: Request, limit: int = 100):
    """Pull recent events from the gateway log server and upsert them."""
    gateway: GatewayClient = request.app.state.gateway
    data = await gateway.get_log_events(asset_id, limit=limit)
    rows = data.get("rows", [])
    inserted = 0
    for row in rows:
        key = _dedupe_key(asset_id, row)
        result = await pool().execute(
            """
            INSERT INTO ems_asset_events
                (timestamp, gateway_id, asset_id, event_type, status,
                 command, message, error_text, details_json, dedupe_key)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (dedupe_key) DO NOTHING
            """,
            _parse_ts(row.get("timestamp")), settings.gateway_id, asset_id,
            row.get("event_type", "unknown"), row.get("status"),
            row.get("command"), row.get("message"), row.get("error"),
            row, key,
        )
        if result.endswith("1"):
            inserted += 1
    return {"status": "ok", "fetched": len(rows), "inserted": inserted}
