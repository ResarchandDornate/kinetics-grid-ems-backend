-- EMS Backend Database Schema
-- Based on the gateway team's "EMS_Backend_PostgreSQL_Schema_v1.sql",
-- adapted to use the TimescaleDB extension for the high-frequency
-- telemetry table. Everything else is plain PostgreSQL.
--
-- This file is applied automatically the first time the `db` container
-- starts on an empty volume (see docker-compose.yml).

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ---------------------------------------------------------------------------
-- Users / authentication
-- role: 'viewer' (read dashboards) | 'operator' (may send commands) | 'admin'
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ems_users (
    id            BIGSERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    email         TEXT UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'viewer',
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Gateway identity & health
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ems_gateways (
    gateway_id       TEXT PRIMARY KEY,
    gateway_name     TEXT,
    site_id          TEXT,
    firmware_version TEXT,
    api_base_url     TEXT,
    log_api_base_url TEXT,
    created_at       TIMESTAMPTZ DEFAULT now(),
    updated_at       TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ems_gateway_status_snapshots (
    id               BIGSERIAL PRIMARY KEY,
    gateway_id       TEXT REFERENCES ems_gateways(gateway_id),
    ts               TIMESTAMPTZ NOT NULL DEFAULT now(),
    mode             TEXT,
    web_api_running  BOOLEAN,
    log_http_running BOOLEAN,
    udp_running      BOOLEAN,
    tcp_running      BOOLEAN,
    raw_json         JSONB
);
CREATE INDEX IF NOT EXISTS idx_gateway_status_ts
    ON ems_gateway_status_snapshots(gateway_id, ts DESC);

-- ---------------------------------------------------------------------------
-- Asset registry & latest state (dashboard cards)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ems_assets (
    asset_id   TEXT PRIMARY KEY,
    gateway_id TEXT REFERENCES ems_gateways(gateway_id),
    asset_key  TEXT,
    asset_type TEXT NOT NULL,
    protocol   TEXT,
    vendor     TEXT,
    host       TEXT,
    port       INTEGER,
    unit_id    INTEGER,
    enabled    BOOLEAN,
    running    BOOLEAN,
    online     BOOLEAN,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_assets_gateway ON ems_assets(gateway_id);
CREATE INDEX IF NOT EXISTS idx_assets_type    ON ems_assets(asset_type);

CREATE TABLE IF NOT EXISTS ems_asset_latest_state (
    asset_id             TEXT PRIMARY KEY REFERENCES ems_assets(asset_id),
    gateway_id           TEXT REFERENCES ems_gateways(gateway_id),
    ts                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    online               BOOLEAN,
    communication_status TEXT,
    telemetry_json       JSONB,
    error_text           TEXT,
    updated_at           TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ems_telemetry_field_dictionary (
    id            BIGSERIAL PRIMARY KEY,
    asset_type    TEXT NOT NULL,
    field_key     TEXT NOT NULL,
    display_name  TEXT,
    data_type     TEXT,
    unit          TEXT,
    group_name    TEXT,
    store_history BOOLEAN DEFAULT TRUE,
    -- Spec marks some fields as event/alarm triggers (e.g. fault_status,
    -- contactor states, comm status). A change in these should raise an
    -- ems_asset_events row. Seeded from the field spec; wired for later use.
    event_trigger BOOLEAN DEFAULT FALSE,
    description   TEXT,
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE(asset_type, field_key)
);

-- ---------------------------------------------------------------------------
-- Telemetry time-series (EAV) -> TimescaleDB hypertable
-- NOTE: Timescale requires the partitioning column (ts) to be part of every
-- UNIQUE/PRIMARY KEY index, so the PK is (id, ts) instead of (id) alone.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ems_telemetry_samples (
    id            BIGSERIAL,
    gateway_id    TEXT,
    asset_id      TEXT,
    asset_type    TEXT,
    ts            TIMESTAMPTZ NOT NULL,
    field_key     TEXT NOT NULL,
    value_numeric DOUBLE PRECISION,
    value_text    TEXT,
    value_bool    BOOLEAN,
    value_json    JSONB,
    quality       TEXT DEFAULT 'good',
    source        TEXT DEFAULT 'web_api',
    created_at    TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id, ts)
);

SELECT create_hypertable(
    'ems_telemetry_samples', 'ts',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_tel_asset_ts
    ON ems_telemetry_samples(asset_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_tel_field_ts
    ON ems_telemetry_samples(asset_id, field_key, ts DESC);

-- Optional but recommended: compress chunks older than 7 days to save disk
-- on a Mac Mini over multi-year retention. Safe to leave enabled.
ALTER TABLE ems_telemetry_samples SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'asset_id, field_key'
);
SELECT add_compression_policy('ems_telemetry_samples', INTERVAL '7 days',
                              if_not_exists => TRUE);

-- Continuous aggregate: 1-minute averages for fast multi-month charts.
-- The dashboard queries this view for long ranges and raw samples for
-- short "live" ranges.
CREATE MATERIALIZED VIEW IF NOT EXISTS ems_telemetry_1m
WITH (timescaledb.continuous) AS
SELECT
    asset_id,
    field_key,
    time_bucket(INTERVAL '1 minute', ts) AS bucket,
    avg(value_numeric) AS avg_value,
    min(value_numeric) AS min_value,
    max(value_numeric) AS max_value,
    count(*)           AS sample_count
FROM ems_telemetry_samples
WHERE value_numeric IS NOT NULL
GROUP BY asset_id, field_key, bucket
WITH NO DATA;

SELECT add_continuous_aggregate_policy('ems_telemetry_1m',
    start_offset => INTERVAL '3 hours',
    end_offset   => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute',
    if_not_exists => TRUE);

-- ---------------------------------------------------------------------------
-- Events / alarms
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ems_asset_events (
    id           BIGSERIAL PRIMARY KEY,
    timestamp    TIMESTAMPTZ NOT NULL,
    gateway_id   TEXT REFERENCES ems_gateways(gateway_id),
    asset_id     TEXT REFERENCES ems_assets(asset_id),
    event_type   TEXT NOT NULL,
    severity     TEXT,
    status       TEXT,
    command      TEXT,
    message      TEXT,
    error_text   TEXT,
    details_json JSONB,
    -- Used to deduplicate rows synced from the gateway CSV logs.
    dedupe_key   TEXT UNIQUE,
    created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_events_asset_ts ON ems_asset_events(asset_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_type     ON ems_asset_events(event_type);

-- ---------------------------------------------------------------------------
-- Command audit (every control command, before + after gateway response)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ems_command_audit (
    id            BIGSERIAL PRIMARY KEY,
    request_ts    TIMESTAMPTZ NOT NULL DEFAULT now(),
    response_ts   TIMESTAMPTZ,
    gateway_id    TEXT REFERENCES ems_gateways(gateway_id),
    asset_id      TEXT REFERENCES ems_assets(asset_id),
    asset_type    TEXT,
    command       TEXT NOT NULL,
    request_json  JSONB,
    status        TEXT,
    error_code    TEXT,
    message       TEXT,
    response_json JSONB,
    client_ip     TEXT,
    requested_by  TEXT,
    created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_command_asset_ts ON ems_command_audit(asset_id, request_ts DESC);
CREATE INDEX IF NOT EXISTS idx_command_name_ts  ON ems_command_audit(command, request_ts DESC);

-- ---------------------------------------------------------------------------
-- Raw API snapshots (debug / future-proofing)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ems_api_raw_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    endpoint        TEXT NOT NULL,
    gateway_id      TEXT,
    asset_id        TEXT,
    response_status TEXT,
    raw_json        JSONB
);
CREATE INDEX IF NOT EXISTS idx_raw_endpoint_ts ON ems_api_raw_snapshots(endpoint, ts DESC);
CREATE INDEX IF NOT EXISTS idx_raw_asset_ts    ON ems_api_raw_snapshots(asset_id, ts DESC);
