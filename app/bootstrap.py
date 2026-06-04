"""Startup sync: register the gateway, upsert the asset list, and build the
telemetry field dictionary from the gateway's /telemetry/keys endpoints.

Per the spec's ingestion plan: call /api/assets and /telemetry/keys on startup
so the registry and field dictionary stay in step with the gateway.
"""
import logging

from .config import settings
from .db import pool
from .field_spec import iter_fields
from .gateway_client import GatewayClient

log = logging.getLogger("bootstrap")


async def seed_field_dictionary() -> None:
    """Load the canonical field dictionary from the spec. Runs BEFORE the
    gateway sync so we have full field metadata (names/units/types/groups)
    even when an asset is offline and /telemetry/keys is empty (spec §20)."""
    count = 0
    for f in iter_fields():
        await pool().execute(
            """
            INSERT INTO ems_telemetry_field_dictionary
                (asset_type, field_key, display_name, data_type, unit,
                 group_name, store_history, event_trigger, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8, now())
            ON CONFLICT (asset_type, field_key) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                data_type = EXCLUDED.data_type,
                unit = EXCLUDED.unit,
                group_name = EXCLUDED.group_name,
                store_history = EXCLUDED.store_history,
                event_trigger = EXCLUDED.event_trigger,
                updated_at = now()
            """,
            f["asset_type"], f["field_key"], f["display_name"], f["data_type"],
            f["unit"], f["group_name"], f["store_history"], f["event_trigger"],
        )
        count += 1
    log.info("Seeded field dictionary from spec: %d fields", count)


async def register_gateway() -> None:
    await pool().execute(
        """
        INSERT INTO ems_gateways (gateway_id, api_base_url, log_api_base_url, updated_at)
        VALUES ($1, $2, $3, now())
        ON CONFLICT (gateway_id) DO UPDATE SET
            api_base_url = EXCLUDED.api_base_url,
            log_api_base_url = EXCLUDED.log_api_base_url,
            updated_at = now()
        """,
        settings.gateway_id, settings.gateway_base_url, settings.gateway_log_base_url,
    )


async def sync_assets(gateway: GatewayClient) -> list[str]:
    data = await gateway.get_assets()
    assets = data.get("assets", [])
    asset_ids: list[str] = []
    for a in assets:
        asset_id = a.get("asset_id")
        if not asset_id:
            continue
        asset_ids.append(asset_id)
        await pool().execute(
            """
            INSERT INTO ems_assets
                (asset_id, gateway_id, asset_key, asset_type, protocol, vendor,
                 host, port, unit_id, enabled, running, online, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12, now())
            ON CONFLICT (asset_id) DO UPDATE SET
                asset_key = EXCLUDED.asset_key,
                asset_type = EXCLUDED.asset_type,
                protocol = EXCLUDED.protocol,
                vendor = EXCLUDED.vendor,
                enabled = EXCLUDED.enabled,
                running = EXCLUDED.running,
                online = EXCLUDED.online,
                updated_at = now()
            """,
            asset_id, settings.gateway_id, a.get("asset_key"), a.get("asset_type"),
            a.get("protocol"), a.get("vendor"), a.get("host"), a.get("port"),
            a.get("unit_id"), a.get("enabled"), a.get("running"), a.get("online"),
        )
    log.info("Synced %d assets: %s", len(asset_ids), ", ".join(asset_ids))
    return asset_ids


async def sync_field_dictionary(gateway: GatewayClient, asset_ids: list[str]) -> None:
    for asset_id in asset_ids:
        try:
            data = await gateway.get_asset_keys(asset_id)
        except Exception as exc:  # noqa: BLE001 - chiller may be offline (keys_count 0)
            log.warning("Could not fetch keys for %s: %s", asset_id, exc)
            continue
        asset_type = data.get("asset_type")
        keys = data.get("keys", [])
        groups = data.get("groups", {}) or {}
        # Reverse map field -> group for display grouping.
        field_group = {}
        for group_name, fields in groups.items():
            if group_name == "all":
                continue
            for f in fields or []:
                field_group[f] = group_name
        for key in keys:
            await pool().execute(
                """
                INSERT INTO ems_telemetry_field_dictionary
                    (asset_type, field_key, group_name, updated_at)
                VALUES ($1, $2, $3, now())
                ON CONFLICT (asset_type, field_key) DO UPDATE SET
                    group_name = COALESCE(EXCLUDED.group_name,
                                          ems_telemetry_field_dictionary.group_name),
                    updated_at = now()
                """,
                asset_type, key, field_group.get(key),
            )
        log.info("Field dictionary: %s -> %d keys", asset_id, len(keys))


async def run_bootstrap(gateway: GatewayClient) -> None:
    await register_gateway()
    # Always seed the canonical dictionary first — this works offline and is
    # the fallback when the gateway/asset is unreachable.
    await seed_field_dictionary()
    try:
        asset_ids = await sync_assets(gateway)
        # Gateway keys refine grouping / add any new keys on top of the seed.
        await sync_field_dictionary(gateway, asset_ids)
    except Exception as exc:  # noqa: BLE001 - gateway may be unreachable at boot
        log.warning("Bootstrap sync incomplete (gateway unreachable?): %s", exc)
