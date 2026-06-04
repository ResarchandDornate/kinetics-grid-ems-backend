"""Application settings, loaded from environment / .env file."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql://ems:ems_password@localhost:5432/ems"

    # Gateway (i.MX93). Point these at the mock for local dev, or the real
    # Wi-Fi IP (http://<IMX93_IP>:8000 / :7000) in the field.
    gateway_id: str = "imx93_gateway_1"
    gateway_base_url: str = "http://localhost:9000"
    gateway_log_base_url: str = "http://localhost:9000"

    # Ingestion
    ingest_enabled: bool = True
    ingest_batch_size: int = 200
    ingest_flush_seconds: float = 2.0
    sse_reconnect_seconds: float = 3.0

    # Change-based storage (deadband / exception compression).
    # A historised field is written only when its value changes (beyond the
    # numeric deadband) OR when this many seconds have passed since its last
    # stored sample (a "heartbeat" so gaps stay bounded and liveness is clear).
    # This is how industrial historians cut storage: constant fields (SOH,
    # setpoints, statuses) store rarely; live numerics still store every second.
    store_on_change: bool = True
    store_heartbeat_seconds: float = 300.0
    # Numeric change must exceed this absolute delta to be stored. 0 = any
    # change (lossless for step/constant signals). Raise to also thin noisy
    # analog fields, at the cost of resolution.
    numeric_deadband: float = 0.0

    # Auth (JWT)
    # CHANGE secret_key in production (e.g. `openssl rand -hex 32`).
    secret_key: str = "dev-insecure-change-me-please-0123456789abcdef"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 720  # 12h
    # When false, signup creates the requested role (handy for first setup).
    # When true (production), new users default to 'viewer' regardless of input.
    lock_signup_role: bool = False

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    log_level: str = "INFO"


settings = Settings()
