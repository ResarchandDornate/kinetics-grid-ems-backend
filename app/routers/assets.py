"""Asset registry + latest-state endpoints (the fast dashboard path).

These read from our own DB (ems_assets / ems_asset_latest_state), never from
the gateway directly, so the dashboard stays fast and works even if the
gateway is briefly unreachable.
"""
from fastapi import APIRouter, HTTPException

from ..db import pool
from ..models import Asset, LatestState

router = APIRouter(prefix="/api", tags=["assets"])


@router.get("/assets", response_model=list[Asset])
async def list_assets():
    rows = await pool().fetch(
        """
        SELECT asset_id, gateway_id, asset_key, asset_type, protocol, vendor,
               enabled, running, online, updated_at
        FROM ems_assets ORDER BY asset_id
        """
    )
    return [dict(r) for r in rows]


@router.get("/assets/{asset_id}", response_model=Asset)
async def get_asset(asset_id: str):
    row = await pool().fetchrow(
        """
        SELECT asset_id, gateway_id, asset_key, asset_type, protocol, vendor,
               enabled, running, online, updated_at
        FROM ems_assets WHERE asset_id = $1
        """,
        asset_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="INVALID_ASSET")
    return dict(row)


@router.get("/telemetry/latest", response_model=list[LatestState])
async def latest_all():
    rows = await pool().fetch(
        """
        SELECT asset_id, ts, online, communication_status,
               telemetry_json AS telemetry, error_text
        FROM ems_asset_latest_state ORDER BY asset_id
        """
    )
    return [dict(r) for r in rows]


@router.get("/assets/{asset_id}/telemetry/latest", response_model=LatestState)
async def latest_one(asset_id: str):
    row = await pool().fetchrow(
        """
        SELECT asset_id, ts, online, communication_status,
               telemetry_json AS telemetry, error_text
        FROM ems_asset_latest_state WHERE asset_id = $1
        """,
        asset_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="ASSET_TELEMETRY_NOT_FOUND")
    return dict(row)


@router.get("/assets/{asset_id}/telemetry/keys")
async def asset_keys(asset_id: str):
    asset = await pool().fetchrow(
        "SELECT asset_type FROM ems_assets WHERE asset_id = $1", asset_id
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="INVALID_ASSET")
    rows = await pool().fetch(
        """
        SELECT field_key, display_name, unit, group_name, data_type,
               store_history, event_trigger
        FROM ems_telemetry_field_dictionary
        WHERE asset_type = $1 ORDER BY group_name, field_key
        """,
        asset["asset_type"],
    )
    return {
        "asset_id": asset_id,
        "asset_type": asset["asset_type"],
        "keys_count": len(rows),
        "keys": [dict(r) for r in rows],
    }
