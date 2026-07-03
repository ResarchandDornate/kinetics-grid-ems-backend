"""Thin async HTTP client for the i.MX93 / NorthBound gateway REST + SSE APIs.

All field communication, decoding and command routing happens *inside* the
gateway. This client only consumes its Web APIs (never Modbus directly).

Auth: the gateway requires a Bearer token. We support either a static token
(GATEWAY_API_TOKEN) or logging in with GATEWAY_USERNAME/PASSWORD and caching the
returned JWT (auto re-login on 401). EMS command writes need the gateway account
to have the internal_admin role.
"""
import asyncio
from typing import AsyncIterator, Optional

import httpx

from .config import settings


class GatewayClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=10.0)
        self._token: Optional[str] = settings.gateway_api_token or None
        self._login_lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()

    # ---- Auth ----
    async def _login(self) -> None:
        """Obtain a JWT from the gateway using configured credentials."""
        if not (settings.gateway_username and settings.gateway_password):
            return
        r = await self._client.post(
            f"{settings.gateway_base_url}/api/auth/login",
            json={"username": settings.gateway_username,
                  "password": settings.gateway_password},
        )
        r.raise_for_status()
        self._token = r.json().get("access_token")

    async def _token_header(self) -> dict:
        if not self._token and not settings.gateway_api_token:
            async with self._login_lock:
                if not self._token:
                    await self._login()
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def _request(self, method: str, url: str, **kw) -> httpx.Response:
        """Send an authenticated request; on 401, re-login once and retry."""
        headers = {**kw.pop("headers", {}), **(await self._token_header())}
        resp = await self._client.request(method, url, headers=headers, **kw)
        if resp.status_code == 401 and not settings.gateway_api_token:
            # Token expired/invalid -> force re-login and retry once.
            self._token = None
            headers = {**(await self._token_header())}
            resp = await self._client.request(method, url, headers=headers, **kw)
        return resp

    async def _get_json(self, url: str, **kw) -> dict:
        r = await self._request("GET", url, **kw)
        r.raise_for_status()
        return r.json()

    # ---- Telemetry / assets (read) ----
    async def get_assets(self) -> dict:
        return await self._get_json(f"{settings.gateway_base_url}/api/assets")

    async def get_key_signals(self) -> dict:
        return await self._get_json(
            f"{settings.gateway_base_url}/api/telemetry/key-signals",
            timeout=settings.north_request_timeout,
        )

    async def get_alarms(self) -> dict:
        return await self._get_json(
            f"{settings.gateway_base_url}/api/alarms",
            timeout=settings.north_request_timeout,
        )

    async def get_latest(self) -> dict:
        return await self._get_json(f"{settings.gateway_base_url}/api/telemetry/latest")

    async def get_asset_keys(self, asset_id: str) -> dict:
        return await self._get_json(
            f"{settings.gateway_base_url}/api/assets/{asset_id}/telemetry/keys"
        )

    # ---- EMS command APIs (NorthBound, internal_admin) ----
    async def get_ems_registers(self) -> dict:
        return await self._get_json(
            f"{settings.gateway_base_url}/api/commands/ems/registers",
            timeout=settings.north_request_timeout,
        )

    async def ems_write(self, body: dict) -> httpx.Response:
        return await self._request(
            "POST", f"{settings.gateway_base_url}/api/commands/ems/write", json=body,
            timeout=settings.north_request_timeout,
        )

    async def ems_batch(self, body: dict) -> httpx.Response:
        return await self._request(
            "POST", f"{settings.gateway_base_url}/api/commands/ems/batch", json=body,
            timeout=settings.north_request_timeout,
        )

    # ---- Legacy EMS gateway command (asset-level) ----
    async def send_command(self, asset_id: str, body: dict) -> httpx.Response:
        return await self._request(
            "POST", f"{settings.gateway_base_url}/api/assets/{asset_id}/commands",
            json=body,
        )

    # ---- Logs (backfill / events sync) ----
    async def get_log_events(self, asset_id: str, limit: int = 100) -> dict:
        return await self._get_json(
            f"{settings.gateway_log_base_url}/api/logs/events",
            params={"asset_id": asset_id, "limit": limit},
        )

    async def get_log_telemetry(self, asset_id: str, date: str, limit: int = 1000) -> dict:
        return await self._get_json(
            f"{settings.gateway_log_base_url}/api/logs/telemetry",
            params={"asset_id": asset_id, "date": date, "limit": limit},
        )

    # ---- SSE live stream (legacy EMS gateway) ----
    async def stream_telemetry(self) -> AsyncIterator[str]:
        """Yield the `data:` payload of each SSE event as a raw string."""
        url = f"{settings.gateway_base_url}/api/stream/telemetry"
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", url, headers=await self._token_header()) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        yield line[len("data:"):].strip()
