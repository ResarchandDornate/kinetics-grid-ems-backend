"""Command proxy with full audit trail.

Every command is written to ems_command_audit *before* it is forwarded to the
gateway, then the same row is updated with the gateway response. This satisfies
the spec's requirement that command audit be append-only and capture both
request and response.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import require_role
from ..command_spec import commands_for
from ..config import settings
from ..db import pool
from ..gateway_client import GatewayClient
from ..models import CommandRequest, CommandResult

log = logging.getLogger("commands")
router = APIRouter(prefix="/api", tags=["commands"])


@router.get("/assets/{asset_id}/commands")
async def available_commands(asset_id: str):
    """Catalog of commands the frontend may send to this asset, so the
    Command Panel can render buttons + value inputs dynamically."""
    asset = await pool().fetchrow(
        "SELECT asset_type FROM ems_assets WHERE asset_id = $1", asset_id
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="INVALID_ASSET")
    return {
        "asset_id": asset_id,
        "asset_type": asset["asset_type"],
        "commands": commands_for(asset["asset_type"]),
    }


@router.post("/assets/{asset_id}/commands", response_model=CommandResult)
async def send_command(
    asset_id: str,
    cmd: CommandRequest,
    request: Request,
    user: dict = Depends(require_role("operator")),
):
    asset = await pool().fetchrow(
        "SELECT asset_type FROM ems_assets WHERE asset_id = $1", asset_id
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="INVALID_ASSET")

    # The authenticated operator is the source of truth for the audit trail.
    cmd.requested_by = user["username"]

    # Body forwarded to the gateway (omit our internal requested_by field).
    body = {"command": cmd.command}
    if cmd.value is not None:
        body["value"] = cmd.value

    client_ip = request.client.host if request.client else None

    # 1) Audit the request first.
    audit_id = await pool().fetchval(
        """
        INSERT INTO ems_command_audit
            (gateway_id, asset_id, asset_type, command, request_json,
             status, client_ip, requested_by)
        VALUES ($1,$2,$3,$4,$5,'pending',$6,$7)
        RETURNING id
        """,
        settings.gateway_id, asset_id, asset["asset_type"], cmd.command,
        body, client_ip, cmd.requested_by,
    )

    # 2) Forward to the gateway.
    gateway: GatewayClient = request.app.state.gateway
    status = "error"
    error_code = None
    message = None
    response_json = None
    try:
        resp = await gateway.send_command(asset_id, body)
        try:
            response_json = resp.json()
        except Exception:  # noqa: BLE001
            response_json = {"raw": resp.text}
        status = response_json.get("status", "ok" if resp.is_success else "error")
        error_code = response_json.get("error_code")
        message = response_json.get("message")
    except Exception as exc:  # noqa: BLE001
        error_code = "GATEWAY_UNREACHABLE"
        message = str(exc)
        log.error("Command forward failed for %s/%s: %s", asset_id, cmd.command, exc)

    # 3) Update the audit row with the response.
    await pool().execute(
        """
        UPDATE ems_command_audit
        SET response_ts = now(), status = $2, error_code = $3,
            message = $4, response_json = $5
        WHERE id = $1
        """,
        audit_id, status, error_code, message, response_json,
    )

    return CommandResult(
        audit_id=audit_id,
        status=status,
        error_code=error_code,
        message=message,
        gateway_response=response_json,
    )


@router.get("/assets/{asset_id}/commands/audit")
async def command_audit(asset_id: str, limit: int = 50):
    rows = await pool().fetch(
        """
        SELECT id, request_ts, response_ts, command, request_json, status,
               error_code, message, requested_by
        FROM ems_command_audit
        WHERE asset_id = $1 ORDER BY request_ts DESC LIMIT $2
        """,
        asset_id, limit,
    )
    return [dict(r) for r in rows]
