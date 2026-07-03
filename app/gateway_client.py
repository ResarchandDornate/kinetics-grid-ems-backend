"""Thin async HTTP client for the i.MX93 gateway REST + SSE endpoints.

All field communication, decoding and command routing happens *inside* the
gateway. This client only consumes its Web APIs (never Modbus directly).
"""
import logging
from typing import AsyncIterator

import httpx

from .config import settings

log = logging.getLogger("gateway_client")


class GatewayClient:
    def __init__(self) -> None:
        token = settings.gateway_api_token
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.AsyncClient(timeout=10.0, headers=headers)

    async def login(self) -> None:
        """Log in with GATEWAY_USERNAME/PASSWORD and store the resulting token."""
        if not (settings.gateway_username and settings.gateway_password):
            return
        resp = await self._client.post(
            f"{settings.gateway_base_url}/api/auth/login",
            json={"username": settings.gateway_username, "password": settings.gateway_password},
        )
        resp.raise_for_status()
        token = resp.json().get("access_token", "")
        self._client.headers["Authorization"] = f"Bearer {token}"
        log.info("Gateway login successful (username=%s).", settings.gateway_username)

    async def _get(self, url: str, **kwargs) -> httpx.Response:
        """GET with one automatic re-login retry on 401."""
        r = await self._client.get(url, **kwargs)
        if r.status_code == 401 and settings.gateway_username:
            log.info("Gateway 401 — re-logging in.")
            await self.login()
            r = await self._client.get(url, **kwargs)
        return r

    async def _post(self, url: str, **kwargs) -> httpx.Response:
        """POST with one automatic re-login retry on 401."""
        r = await self._client.post(url, **kwargs)
        if r.status_code == 401 and settings.gateway_username:
            log.info("Gateway 401 — re-logging in.")
            await self.login()
            r = await self._client.post(url, **kwargs)
        return r

    async def close(self) -> None:
        await self._client.aclose()

    # ---- REST (port 8000) ----
    async def get_assets(self) -> dict:
        r = await self._get(f"{settings.gateway_base_url}/api/assets")
        r.raise_for_status()
        return r.json()

    # ---- NorthBound gateway (read-only REST) ----
    async def get_key_signals(self) -> dict:
        """All-assets compact telemetry. The NorthBound gateway is slow, so this
        call uses a generous timeout."""
        r = await self._get(
            f"{settings.gateway_base_url}/api/telemetry/key-signals",
            timeout=settings.north_request_timeout,
        )
        r.raise_for_status()
        return r.json()

    async def get_alarms(self) -> dict:
        r = await self._get(
            f"{settings.gateway_base_url}/api/alarms",
            timeout=settings.north_request_timeout,
        )
        r.raise_for_status()
        return r.json()

    async def get_latest(self) -> dict:
        r = await self._get(f"{settings.gateway_base_url}/api/telemetry/latest")
        r.raise_for_status()
        return r.json()

    async def get_asset_keys(self, asset_id: str) -> dict:
        r = await self._get(
            f"{settings.gateway_base_url}/api/assets/{asset_id}/telemetry/keys"
        )
        r.raise_for_status()
        return r.json()

    # ---- EMS command APIs (NorthBound, internal_admin) ----
    async def get_ems_registers(self) -> dict:
        r = await self._get(
            f"{settings.gateway_base_url}/api/commands/ems/registers",
            timeout=settings.north_request_timeout,
        )
        r.raise_for_status()
        return r.json()

    async def ems_write(self, body: dict) -> httpx.Response:
        return await self._post(
            f"{settings.gateway_base_url}/api/commands/ems/write",
            json=body, timeout=settings.north_request_timeout,
        )

    async def ems_batch(self, body: dict) -> httpx.Response:
        return await self._post(
            f"{settings.gateway_base_url}/api/commands/ems/batch",
            json=body, timeout=settings.north_request_timeout,
        )

    async def send_command(self, asset_id: str, body: dict) -> httpx.Response:
        return await self._client.post(
            f"{settings.gateway_base_url}/api/assets/{asset_id}/commands",
            json=body,
        )

    # ---- Logs (port 7000) for backfill / events sync ----
    async def get_log_events(self, asset_id: str, limit: int = 100) -> dict:
        r = await self._get(
            f"{settings.gateway_log_base_url}/api/logs/events",
            params={"asset_id": asset_id, "limit": limit},
        )
        r.raise_for_status()
        return r.json()

    async def get_log_telemetry(self, asset_id: str, date: str, limit: int = 1000) -> dict:
        r = await self._get(
            f"{settings.gateway_log_base_url}/api/logs/telemetry",
            params={"asset_id": asset_id, "date": date, "limit": limit},
        )
        r.raise_for_status()
        return r.json()

    # ---- SSE live stream (port 8000) ----
    async def stream_telemetry(self) -> AsyncIterator[str]:
        """Yield the `data:` payload of each SSE event as a raw string."""
        url = f"{settings.gateway_base_url}/api/stream/telemetry"
        auth_header = dict(self._client.headers).get("Authorization", "")
        headers = {"Authorization": auth_header} if auth_header else {}
        async with httpx.AsyncClient(timeout=None, headers=headers) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        yield line[len("data:"):].strip()
