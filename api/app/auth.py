"""Verify a Supabase access token, then read the role from staff_profile (never from the token)."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

import anyio
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings
from .db import get_conn

bearer = HTTPBearer(auto_error=False)


@dataclass
class Staff:
    user_id: UUID
    clinic_id: UUID
    role: str
    email: str | None


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{url.rstrip('/')}/auth/v1/.well-known/jwks.json", cache_keys=True)


def _decode_sync(token: str) -> dict:
    s = get_settings()
    opts = {"audience": "authenticated"}
    if s.supabase_jwt_secret:
        return jwt.decode(token, s.supabase_jwt_secret, algorithms=["HS256"], **opts)
    if not s.supabase_url:
        raise jwt.InvalidTokenError("auth not configured")
    key = _jwks_client(s.supabase_url).get_signing_key_from_jwt(token).key
    return jwt.decode(token, key, algorithms=["RS256", "ES256"], **opts)


async def current_staff(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer), conn=Depends(get_conn)
) -> Staff:
    unauth = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    if creds is None:
        raise unauth
    try:
        claims = await anyio.to_thread.run_sync(_decode_sync, creds.credentials)
        user_id = UUID(claims["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauth
    cur = await conn.execute("select clinic_id, role, email from staff_profile where user_id = %s", [user_id])
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No staff profile for this user")
    return Staff(user_id, row["clinic_id"], row["role"], row["email"] or claims.get("email"))


def require_role(*roles: str):
    async def dep(staff: Staff = Depends(current_staff)) -> Staff:
        if staff.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")
        return staff
    return dep
