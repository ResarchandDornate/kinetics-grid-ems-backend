"""Background ingestion: consume the gateway SSE telemetry stream, keep the
latest-state cache fresh, and batch-insert time-series samples.

Design notes
------------
* The gateway already persists every telemetry row to CSV (port 7000), so this
  worker does not need a durable queue for safety -- if it falls behind or
  restarts, history can be backfilled from the gateway logs. That is why the
  MVP has no Redis/queue: at 3 assets the only real risk is DB write
  amplification, which we solve with batched inserts.
* We flatten each asset's telemetry dict into EAV rows. Scalar values
  (number/bool/text) are stored typed; nested objects (e.g. storage_logger,
  raw_telemetry) are skipped for the samples table -- the full snapshot is kept
  in ems_asset_latest_state.telemetry_json instead.
* store_history is honoured: if a field is marked store_history=false in the
  field dictionary, it updates latest-state but is not written to history.
* Change-based storage (exception/deadband compression): a historised field is
  only written when its value changes (numeric: beyond numeric_deadband; text/
  bool: any change) OR when store_heartbeat_seconds have elapsed since its last
  stored sample. Latest-state is ALWAYS updated regardless. Chart readers should
  hold the last value between points; the heartbeat bounds any gap. This keeps
  constant fields (SOH, setpoints, statuses) from writing a row every second.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from .config import settings
from .db import pool
from .gateway_client import GatewayClient

log = logging.getLogger("ingestion")

# Envelope / structural / meta keys we never expand into the time-series table.
_SKIP_KEYS = {
    "storage_logger", "raw_telemetry", "data", "assets", "type", "mode",
    "gateway_id", "asset_id", "asset_type", "asset_key", "timestamp",
}


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _classify(value: Any) -> tuple[Optional[float], Optional[str], Optional[bool], Optional[dict]]:
    """Map a telemetry value to (numeric, text, bool, json) columns."""
    if isinstance(value, bool):
        return None, None, value, None
    if isinstance(value, (int, float)):
        return float(value), None, None, None
    if isinstance(value, str):
        return None, value, None, None
    if isinstance(value, (list, dict)):
        return None, None, None, value
    return None, None, None, None


class Ingestor:
    def __init__(self, gateway: GatewayClient) -> None:
        self.gateway = gateway
        self._buffer: list[tuple] = []
        self._buffer_lock = asyncio.Lock()
        self._store_history: dict[tuple[str, str], bool] = {}
        # (asset_id, field_key) -> (last stored scalar value, last stored ts)
        self._last_stored: dict[tuple[str, str], tuple[Any, datetime]] = {}
        self._stop = asyncio.Event()

    async def load_field_dictionary(self) -> None:
        """Cache store_history flags so we know what to historise."""
        rows = await pool().fetch(
            "SELECT asset_type, field_key, store_history FROM ems_telemetry_field_dictionary"
        )
        self._store_history = {
            (r["asset_type"], r["field_key"]): r["store_history"] for r in rows
        }

    def _should_store(self, asset_type: Optional[str], field_key: str) -> bool:
        # Default to storing history for unknown fields (safe / future-proof).
        return self._store_history.get((asset_type, field_key), True)

    def _changed_enough(self, asset_id: str, field_key: str, scalar: Any,
                        ts: datetime) -> bool:
        """Change-based storage gate. Returns True if this sample should be
        written to history (value changed, heartbeat due, or first seen)."""
        if not settings.store_on_change:
            return True
        key = (asset_id, field_key)
        last = self._last_stored.get(key)
        if last is None:
            self._last_stored[key] = (scalar, ts)
            return True
        last_val, last_ts = last
        if (ts - last_ts).total_seconds() >= settings.store_heartbeat_seconds:
            self._last_stored[key] = (scalar, ts)
            return True
        if isinstance(scalar, (int, float)) and isinstance(last_val, (int, float)):
            changed = abs(scalar - last_val) > settings.numeric_deadband
        else:
            changed = scalar != last_val
        if changed:
            self._last_stored[key] = (scalar, ts)
            return True
        return False

    async def run(self) -> None:
        """Run the SSE consumer + periodic flusher until stopped."""
        await self.load_field_dictionary()
        flusher = asyncio.create_task(self._flush_loop())
        try:
            while not self._stop.is_set():
                try:
                    await self._consume_stream()
                except Exception as exc:  # noqa: BLE001 - keep the worker alive
                    log.warning("SSE stream error: %s; reconnecting in %ss",
                                exc, settings.sse_reconnect_seconds)
                    await asyncio.sleep(settings.sse_reconnect_seconds)
        finally:
            self._stop.set()
            await flusher
            await self._flush()  # final drain

    async def stop(self) -> None:
        self._stop.set()

    async def _consume_stream(self) -> None:
        log.info("Connecting to gateway SSE stream...")
        async for payload in self.gateway.stream_telemetry():
            if self._stop.is_set():
                break
            if not payload:
                continue
            try:
                packet = json.loads(payload)
            except json.JSONDecodeError:
                log.debug("Skipping non-JSON SSE payload")
                continue
            await self._handle_packet(packet)

    async def _handle_packet(self, packet: dict) -> None:
        """Normalise the many packet shapes into (asset_id, asset_obj) pairs.

        Real i.MX93 SSE event:
            {"asset_id":"chiller_1", "data":{...}, "status":"ok",
             "assets":{"chiller":{"asset_id":"chiller_1","data":{...}}}}
          -> the nested `assets` dict is keyed by the SHORT name ("chiller"),
             but each object carries the real "asset_id" ("chiller_1"). We use
             the inner asset_id, never the dict key (that was the FK crash).
        Also accepts the mock's {"assets":{asset_id:{"telemetry":{...}}}} and a
        bare single-asset packet.
        """
        pairs: list[tuple[str, dict]] = []
        assets = packet.get("assets")
        if isinstance(assets, dict) and assets:
            for key, obj in assets.items():
                if isinstance(obj, dict):
                    pairs.append((obj.get("asset_id") or key, obj))
        elif isinstance(assets, list) and assets:
            for obj in assets:
                if isinstance(obj, dict) and obj.get("asset_id"):
                    pairs.append((obj["asset_id"], obj))
        elif packet.get("asset_id"):
            pairs.append((packet["asset_id"], packet))

        for asset_id, obj in pairs:
            if asset_id:
                await self._handle_asset(asset_id, obj)

    async def _handle_asset(self, asset_id: str, data: dict) -> None:
        asset_type = data.get("asset_type")
        # Real gateway puts telemetry under "data"; mock uses "telemetry" or
        # spreads fields at the top level.
        telemetry = data.get("data")
        if not isinstance(telemetry, dict) or not telemetry:
            telemetry = data.get("telemetry")
        if not isinstance(telemetry, dict):
            telemetry = data
        ts = _parse_ts(data.get("timestamp") or telemetry.get("timestamp"))
        comm = (
            data.get("communication_status")
            or telemetry.get("communication_status")
            or telemetry.get("comm_status")
            or data.get("status")
        )
        # Real SSE has no boolean "online"; derive it from comm/status.
        online = data.get("online")
        if online is None and comm is not None:
            online = str(comm).lower() in ("online", "ok", "connected")
        error_text = data.get("error") or telemetry.get("error") or telemetry.get("last_error")

        # 1) Always refresh the latest-state cache (single-row upsert, fast).
        await pool().execute(
            """
            INSERT INTO ems_asset_latest_state
                (asset_id, gateway_id, ts, online, communication_status,
                 telemetry_json, error_text, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, now())
            ON CONFLICT (asset_id) DO UPDATE SET
                gateway_id = EXCLUDED.gateway_id,
                ts = EXCLUDED.ts,
                online = EXCLUDED.online,
                communication_status = EXCLUDED.communication_status,
                telemetry_json = EXCLUDED.telemetry_json,
                error_text = EXCLUDED.error_text,
                updated_at = now()
            """,
            asset_id, settings.gateway_id, ts, online, comm, telemetry, error_text,
        )

        # 2) Buffer historised scalar fields for batched time-series insert.
        rows: list[tuple] = []
        for key, value in telemetry.items():
            if key in _SKIP_KEYS or value is None:
                continue
            if not self._should_store(asset_type, key):
                continue
            num, txt, boolean, js = _classify(value)
            # Skip json/structural values for change detection; store as-is.
            scalar = num if num is not None else (
                boolean if boolean is not None else txt)
            if scalar is not None and not self._changed_enough(asset_id, key, scalar, ts):
                continue
            rows.append((
                settings.gateway_id, asset_id, asset_type, ts, key,
                num, txt, boolean, js, "good", "web_api",
            ))

        if rows:
            async with self._buffer_lock:
                self._buffer.extend(rows)
                if len(self._buffer) >= settings.ingest_batch_size:
                    await self._flush_locked()

    async def _flush_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(settings.ingest_flush_seconds)
            await self._flush()

    async def _flush(self) -> None:
        async with self._buffer_lock:
            await self._flush_locked()

    async def _flush_locked(self) -> None:
        if not self._buffer:
            return
        batch = self._buffer
        self._buffer = []
        try:
            await pool().executemany(
                """
                INSERT INTO ems_telemetry_samples
                    (gateway_id, asset_id, asset_type, ts, field_key,
                     value_numeric, value_text, value_bool, value_json,
                     quality, source)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                """,
                batch,
            )
            log.debug("Flushed %d telemetry samples", len(batch))
        except Exception as exc:  # noqa: BLE001
            # Re-buffer on failure so we don't drop data between retries.
            log.error("Telemetry flush failed (%d rows re-queued): %s", len(batch), exc)
            self._buffer = batch + self._buffer
