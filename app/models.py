"""Pydantic request/response models for the backend's own API."""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field


# ---- Auth ----
class SignupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=128)
    email: Optional[EmailStr] = None
    role: str = Field(default="viewer", pattern="^(viewer|operator|admin)$")


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str


class UserOut(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
    role: str


class CommandRequest(BaseModel):
    command: str
    value: Optional[Any] = None
    requested_by: Optional[str] = None


class CommandResult(BaseModel):
    audit_id: int
    status: str
    error_code: Optional[str] = None
    message: Optional[str] = None
    gateway_response: Optional[dict] = None


class Asset(BaseModel):
    asset_id: str
    gateway_id: Optional[str] = None
    asset_key: Optional[str] = None
    asset_type: Optional[str] = None
    protocol: Optional[str] = None
    vendor: Optional[str] = None
    enabled: Optional[bool] = None
    running: Optional[bool] = None
    online: Optional[bool] = None
    updated_at: Optional[datetime] = None


class LatestState(BaseModel):
    asset_id: str
    ts: Optional[datetime] = None
    online: Optional[bool] = None
    communication_status: Optional[str] = None
    telemetry: Optional[dict] = None
    error_text: Optional[str] = None


class TimeseriesPoint(BaseModel):
    ts: datetime
    value: Optional[float] = None


class TimeseriesResponse(BaseModel):
    asset_id: str
    field_key: str
    resolution: str  # "raw" or "1m"
    points: list[TimeseriesPoint]
