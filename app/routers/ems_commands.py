"""NorthBound EMS command APIs (write control registers).

Exposes the gateway's EMS Command Panel (93 writable `ems_system` registers) via
our backend, so the frontend never needs the gateway's internal token. Flow:

  frontend --(our JWT, operator role)--> backend --(gateway internal token)--> gateway

Only available when GATEWAY_TYPE=northbound. Every write is recorded in
ems_command_audit before and after the gateway call.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import require_role
from ..config import settings
from ..db import pool
from ..gateway_client import GatewayClient
from ..models import EmsBatchRequest, EmsWriteRequest

log = logging.getLogger("ems_commands")
router = APIRouter(prefix="/api/commands/ems", tags=["ems-commands"])

_EMS_ASSET = "ems_system"


def _require_northbound() -> None:
    if settings.gateway_type != "northbound":
        raise HTTPException(
            status_code=404,
            detail="EMS command APIs are only available on the NorthBound gateway",
        )


def _target(body: EmsWriteRequest) -> str:
    return body.signal_name or body.point_id or (
        f"address:{body.address}" if body.address is not None else "unknown")


@router.get("/registers")
async def list_registers(request: Request, user: dict = Depends(require_role("operator"))):
    """Catalog of the 93 writable EMS registers (for the Command Panel UI)."""
    _require_northbound()
    gateway: GatewayClient = request.app.state.gateway
    try:
        return await gateway.get_ems_registers()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Gateway error: {exc}")


async def _ensure_ems_asset() -> None:
    """The command audit FK-references ems_assets. Make sure ems_system exists
    even if telemetry ingestion hasn't registered it yet (e.g. gateway down)."""
    await pool().execute(
        """
        INSERT INTO ems_assets (asset_id, gateway_id, asset_key, asset_type, updated_at)
        VALUES ($1, $2, $1, 'ems', now())
        ON CONFLICT (asset_id) DO NOTHING
        """,
        _EMS_ASSET, settings.gateway_id,
    )


async def _write_one(gateway: GatewayClient, body: EmsWriteRequest, user: dict,
                     client_ip: str | None) -> dict:
    payload = body.model_dump(exclude_none=True)
    await _ensure_ems_asset()
    # Audit the request first.
    audit_id = await pool().fetchval(
        """
        INSERT INTO ems_command_audit
            (gateway_id, asset_id, asset_type, command, request_json,
             status, client_ip, requested_by)
        VALUES ($1,$2,$3,$4,$5,'pending',$6,$7)
        RETURNING id
        """,
        settings.gateway_id, _EMS_ASSET, "ems", _target(body), payload,
        client_ip, user["username"],
    )
    status, error_code, message, response_json = "error", None, None, None
    try:
        resp = await gateway.ems_write(payload)
        try:
            response_json = resp.json()
        except Exception:  # noqa: BLE001
            response_json = {"raw": resp.text}
        if resp.is_success and response_json.get("ok"):
            status = "ok"
        else:
            error_code = str(resp.status_code)
            message = response_json.get("detail") or response_json.get("message")
    except Exception as exc:  # noqa: BLE001
        error_code = "GATEWAY_UNREACHABLE"
        message = str(exc)
        log.error("EMS write failed for %s: %s", _target(body), exc)

    await pool().execute(
        """
        UPDATE ems_command_audit
        SET response_ts = now(), status = $2, error_code = $3,
            message = $4, response_json = $5
        WHERE id = $1
        """,
        audit_id, status, error_code, message, response_json,
    )
    return {"audit_id": audit_id, "status": status, "error_code": error_code,
            "message": message, "gateway_response": response_json}


@router.post("/write")
async def write_register(body: EmsWriteRequest, request: Request,
                         user: dict = Depends(require_role("operator"))):
    """Write one EMS command register (audited)."""
    _require_northbound()
    if not (body.signal_name or body.point_id or body.address is not None):
        raise HTTPException(status_code=422,
                            detail="Provide signal_name, point_id, or address")
    gateway: GatewayClient = request.app.state.gateway
    client_ip = request.client.host if request.client else None
    return await _write_one(gateway, body, user, client_ip)


@router.post("/batch")
async def batch_write(body: EmsBatchRequest, request: Request,
                      user: dict = Depends(require_role("operator"))):
    """Write multiple EMS registers. Each write is audited individually."""
    _require_northbound()
    gateway: GatewayClient = request.app.state.gateway
    client_ip = request.client.host if request.client else None
    results = []
    for w in body.writes:
        res = await _write_one(gateway, w, user, client_ip)
        results.append(res)
        if res["status"] != "ok" and not body.continue_on_error:
            break
    ok = all(r["status"] == "ok" for r in results) and len(results) == len(body.writes)
    return {
        "ok": ok,
        "success_count": sum(1 for r in results if r["status"] == "ok"),
        "error_count": sum(1 for r in results if r["status"] != "ok"),
        "results": results,
    }
