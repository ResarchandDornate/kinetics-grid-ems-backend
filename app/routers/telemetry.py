"""Historical time-series endpoint backed by the TimescaleDB hypertable.

For short ranges it returns raw 1 Hz samples; for long ranges it auto-switches
to the 1-minute continuous aggregate so multi-month charts stay fast.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query

from ..db import pool
from ..models import TimeseriesResponse

router = APIRouter(prefix="/api", tags=["telemetry"])

# Above this span we serve the pre-aggregated 1-minute view instead of raw rows.
_RAW_MAX_SPAN = timedelta(hours=6)


@router.get("/assets/{asset_id}/telemetry/timeseries", response_model=TimeseriesResponse)
async def timeseries(
    asset_id: str,
    field_key: str = Query(..., description="Telemetry field, e.g. soc_percent"),
    start: datetime | None = Query(None, description="ISO start time (default: 1h ago)"),
    end: datetime | None = Query(None, description="ISO end time (default: now)"),
    resolution: str = Query("auto", pattern="^(auto|raw|1m)$"),
):
    end = end or datetime.now(timezone.utc)
    start = start or (end - timedelta(hours=1))

    use_agg = resolution == "1m" or (
        resolution == "auto" and (end - start) > _RAW_MAX_SPAN
    )

    if use_agg:
        rows = await pool().fetch(
            """
            SELECT bucket AS ts, avg_value AS value
            FROM ems_telemetry_1m
            WHERE asset_id = $1 AND field_key = $2 AND bucket BETWEEN $3 AND $4
            ORDER BY bucket
            """,
            asset_id, field_key, start, end,
        )
        res = "1m"
    else:
        rows = await pool().fetch(
            """
            SELECT ts, value_numeric AS value
            FROM ems_telemetry_samples
            WHERE asset_id = $1 AND field_key = $2 AND ts BETWEEN $3 AND $4
            ORDER BY ts
            """,
            asset_id, field_key, start, end,
        )
        res = "raw"

    return {
        "asset_id": asset_id,
        "field_key": field_key,
        "resolution": res,
        "points": [dict(r) for r in rows],
    }
