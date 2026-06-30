"""NorthBound EMS Gateway ingestion (read-only, REST polling).

The NorthBound gateway replaced the old EMS gateway: 9 assets, ~1421
self-describing signals, no SSE/commands. It's slow over the Cloudflare tunnel,
so we POLL `GET /api/telemetry/key-signals` on a timer instead of streaming.

Each signal is self-describing:
    "signals": { "<name>": { "value", "unit", "category", "quality",
                             "display_name", "description", "updated_utc" } }

We reuse the base Ingestor's batched writer + change-based storage; only the
acquisition loop and per-asset mapping differ.
"""
import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from .config import settings
from .db import pool
from .ingestion import Ingestor, _classify, _parse_ts

log = logging.getLogger("north_ingestion")


def derive_asset_type(asset_id: str) -> str:
    if asset_id.startswith("bms"):
        return "bms"
    if asset_id.startswith("pcs"):
        return "pcs"
    return asset_id  # ems_system, utility_meter, liquid_cooling, io_module, ...


class NorthIngestor(Ingestor):
    def __init__(self, gateway) -> None:
        super().__init__(gateway)
        self._dict_seen: set[tuple[str, str]] = set()
        self._assets_seen: set[str] = set()
        self._alarm_dedupe: set[str] = set()

    async def run(self) -> None:
        await self.load_field_dictionary()
        flusher = asyncio.create_task(self._flush_loop())
        alarms = asyncio.create_task(self._alarms_loop())
        log.info("NorthBound poller started (every %ss).", settings.north_poll_seconds)
        try:
            while not self._stop.is_set():
                try:
                    data = await self.gateway.get_key_signals()
                    await self._handle_key_signals(data)
                except Exception as exc:  # noqa: BLE001 - keep polling through outages
                    log.warning("key-signals poll failed: %s", exc)
                await asyncio.sleep(settings.north_poll_seconds)
        finally:
            self._stop.set()
            for t in (flusher, alarms):
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
            await self._flush()

    async def _handle_key_signals(self, data: dict) -> None:
        assets = data.get("assets")
        if not isinstance(assets, dict):
            return
        for asset_id, aobj in assets.items():
            if isinstance(aobj, dict):
                await self._ingest_asset(asset_id, aobj)

    async def _ensure_asset(self, asset_id: str, online: Optional[bool]) -> None:
        await pool().execute(
            """
            INSERT INTO ems_assets
                (asset_id, gateway_id, asset_key, asset_type, online, updated_at)
            VALUES ($1,$2,$3,$4,$5, now())
            ON CONFLICT (asset_id) DO UPDATE SET online = EXCLUDED.online, updated_at = now()
            """,
            asset_id, settings.gateway_id, asset_id, derive_asset_type(asset_id), online,
        )
        self._assets_seen.add(asset_id)

    async def _ingest_asset(self, asset_id: str, aobj: dict) -> None:
        online = aobj.get("online")
        comm = "online" if online else "offline"
        ts = _parse_ts(aobj.get("last_update_utc"))
        signals = aobj.get("signals") or {}

        await self._ensure_asset(asset_id, online)

        # Flat {name: value} snapshot for the latest-state cache / dashboard.
        flat = {n: s.get("value") for n, s in signals.items() if isinstance(s, dict)}
        await pool().execute(
            """
            INSERT INTO ems_asset_latest_state
                (asset_id, gateway_id, ts, online, communication_status,
                 telemetry_json, error_text, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7, now())
            ON CONFLICT (asset_id) DO UPDATE SET
                gateway_id = EXCLUDED.gateway_id, ts = EXCLUDED.ts,
                online = EXCLUDED.online,
                communication_status = EXCLUDED.communication_status,
                telemetry_json = EXCLUDED.telemetry_json, updated_at = now()
            """,
            asset_id, settings.gateway_id, ts, online, comm, flat, None,
        )

        # Historise scalar signals (change-detected + heartbeat from base class).
        asset_type = asset_id  # per-asset field dictionary, no cross-asset mixing
        rows: list[tuple] = []
        for name, s in signals.items():
            if not isinstance(s, dict):
                continue
            value = s.get("value")
            if value is None:
                continue
            await self._maybe_upsert_dict(asset_type, name, s)
            num, txt, boolean, js = _classify(value)
            scalar = num if num is not None else (boolean if boolean is not None else txt)
            if scalar is None:
                continue
            if not self._changed_enough(asset_id, name, scalar, ts):
                continue
            rows.append((
                settings.gateway_id, asset_id, asset_type, ts, name,
                num, txt, boolean, js, s.get("quality", "good"), "north_api",
            ))

        if rows:
            async with self._buffer_lock:
                self._buffer.extend(rows)
                if len(self._buffer) >= settings.ingest_batch_size:
                    await self._flush_locked()

    async def _maybe_upsert_dict(self, asset_type: str, name: str, s: dict) -> None:
        key = (asset_type, name)
        if key in self._dict_seen:
            return
        self._dict_seen.add(key)
        value = s.get("value")
        data_type = (
            "boolean" if isinstance(value, bool)
            else "number" if isinstance(value, (int, float))
            else "string"
        )
        await pool().execute(
            """
            INSERT INTO ems_telemetry_field_dictionary
                (asset_type, field_key, display_name, data_type, unit,
                 group_name, description, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7, now())
            ON CONFLICT (asset_type, field_key) DO UPDATE SET
                display_name = EXCLUDED.display_name, data_type = EXCLUDED.data_type,
                unit = EXCLUDED.unit, group_name = EXCLUDED.group_name,
                description = EXCLUDED.description, updated_at = now()
            """,
            asset_type, name, s.get("display_name") or name, data_type,
            s.get("unit") or None, s.get("category"), s.get("description"),
        )

    # ---- Alarms ----
    async def _alarms_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data = await self.gateway.get_alarms()
                await self._sync_alarms(data)
            except Exception as exc:  # noqa: BLE001
                log.debug("alarms poll failed: %s", exc)
            await asyncio.sleep(settings.north_alarms_poll_seconds)

    async def _sync_alarms(self, data: dict) -> None:
        for al in data.get("alarms", []) or []:
            if not isinstance(al, dict):
                continue
            asset_id = al.get("asset_id")
            if not asset_id or asset_id not in self._assets_seen:
                continue
            raw = "|".join(str(al.get(k, "")) for k in ("timestamp_utc", "code", "message"))
            dk = hashlib.sha1(f"{asset_id}|{raw}".encode()).hexdigest()
            if dk in self._alarm_dedupe:
                continue
            self._alarm_dedupe.add(dk)
            await pool().execute(
                """
                INSERT INTO ems_asset_events
                    (timestamp, gateway_id, asset_id, event_type, severity,
                     message, details_json, dedupe_key)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                ON CONFLICT (dedupe_key) DO NOTHING
                """,
                _parse_ts(al.get("timestamp_utc")), settings.gateway_id, asset_id,
                al.get("code") or "alarm", al.get("severity"), al.get("message"), al, dk,
            )
