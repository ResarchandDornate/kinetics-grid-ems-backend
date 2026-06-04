"""Authentication core: password hashing, JWT issue/verify, and FastAPI
dependencies for current-user and role checks.

Roles: 'viewer' < 'operator' < 'admin'. Command endpoints require operator+.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext

from .config import settings
from .db import pool

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer = HTTPBearer(auto_error=True)

ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}


def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _pwd.verify(password, password_hash)


def create_access_token(user: dict) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload = {
        "sub": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
        "exp": expire,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="UNAUTHORIZED",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            creds.credentials, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise cred_exc

    row = await pool().fetchrow(
        "SELECT id, username, email, role, is_active FROM ems_users WHERE id = $1",
        user_id,
    )
    if row is None or not row["is_active"]:
        raise cred_exc
    return dict(row)


def require_role(min_role: str):
    """Dependency factory: ensure the current user is at least `min_role`."""
    needed = ROLE_RANK[min_role]

    async def _checker(user: dict = Depends(get_current_user)) -> dict:
        if ROLE_RANK.get(user["role"], -1) < needed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"FORBIDDEN: requires role '{min_role}' or higher",
            )
        return user

    return _checker
