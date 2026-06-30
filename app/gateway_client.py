"""Thin async HTTP client for the i.MX93 gateway REST + SSE endpoints.

All field communication, decoding and command routing happens *inside* the
gateway. This client only consumes its Web APIs (never Modbus directly).
"""
from typing import AsyncIterator

import httpx

from .config import settings


class GatewayClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=10.0)

    async def close(self) -> None:
        await self._client.aclose()

    # ---- REST (port 8000) ----
    async def get_assets(self) -> dict:
        r = await self._client.get(f"{settings.gateway_base_url}/api/assets")
        r.raise_for_status()
        return r.json()

    # ---- NorthBound gateway (read-only REST) ----
    async def get_key_signals(self) -> dict:
        """All-assets compact telemetry. The NorthBound gateway is slow, so this
        call uses a generous timeout."""
        r = await self._client.get(
            f"{settings.gateway_base_url}/api/telemetry/key-signals",
            timeout=settings.north_request_timeout,
        )
        r.raise_for_status()
        return r.json()

    async def get_alarms(self) -> dict:
        r = await self._client.get(
            f"{settings.gateway_base_url}/api/alarms",
            timeout=settings.north_request_timeout,
        )
        r.raise_for_status()
        return r.json()

    async def get_latest(self) -> dict:
        r = await self._client.get(f"{settings.gateway_base_url}/api/telemetry/latest")
        r.raise_for_status()
        return r.json()

    async def get_asset_keys(self, asset_id: str) -> dict:
        r = await self._client.get(
            f"{settings.gateway_base_url}/api/assets/{asset_id}/telemetry/keys"
        )
        r.raise_for_status()
        return r.json()

    async def send_command(self, asset_id: str, body: dict) -> httpx.Response:
        return await self._client.post(
            f"{settings.gateway_base_url}/api/assets/{asset_id}/commands",
            json=body,
        )

    # ---- Logs (port 7000) for backfill / events sync ----
    async def get_log_events(self, asset_id: str, limit: int = 100) -> dict:
        r = await self._client.get(
            f"{settings.gateway_log_base_url}/api/logs/events",
            params={"asset_id": asset_id, "limit": limit},
        )
        r.raise_for_status()
        return r.json()

    async def get_log_telemetry(self, asset_id: str, date: str, limit: int = 1000) -> dict:
        r = await self._client.get(
            f"{settings.gateway_log_base_url}/api/logs/telemetry",
            params={"asset_id": asset_id, "date": date, "limit": limit},
        )
        r.raise_for_status()
        return r.json()

    # ---- SSE live stream (port 8000) ----
    async def stream_telemetry(self) -> AsyncIterator[str]:
        """Yield the `data:` payload of each SSE event as a raw string."""
        url = f"{settings.gateway_base_url}/api/stream/telemetry"
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        yield line[len("data:"):].strip()
