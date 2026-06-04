"""Authentication endpoints: signup, login, me."""
import logging

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import create_access_token, get_current_user, hash_password, verify_password
from ..config import settings
from ..db import pool
from ..models import LoginRequest, SignupRequest, TokenResponse, UserOut

log = logging.getLogger("auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/signup", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest):
    # In production (lock_signup_role=true) force new users to 'viewer';
    # promotion to operator/admin is then an admin-only DB action.
    role = "viewer" if settings.lock_signup_role else body.role
    try:
        row = await pool().fetchrow(
            """
            INSERT INTO ems_users (username, email, password_hash, role)
            VALUES ($1, $2, $3, $4)
            RETURNING id, username, email, role
            """,
            body.username, body.email, hash_password(body.password), role,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="USER_ALREADY_EXISTS")
    return dict(row)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    row = await pool().fetchrow(
        "SELECT id, username, role, password_hash, is_active FROM ems_users WHERE username = $1",
        body.username,
    )
    if row is None or not row["is_active"] or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="INVALID_CREDENTIALS")
    token = create_access_token(dict(row))
    return TokenResponse(access_token=token, username=row["username"], role=row["role"])


@router.get("/me", response_model=UserOut)
async def me(user: dict = Depends(get_current_user)):
    return UserOut(id=user["id"], username=user["username"],
                   email=user.get("email"), role=user["role"])
