from __future__ import annotations

from typing import Annotated, Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.app.auth_token import decode_access_token
from backend.app.config import settings
from backend.app.db import User, get_engine

_bearer = HTTPBearer(auto_error=False)


def owner_scope_user_id(current: User | None) -> int | None:
    """When auth is off, rows are globally visible / unscoped."""
    return current.id if (settings.auth_enforced and current is not None) else None


def get_current_user(
    creds: Annotated[Optional[HTTPAuthorizationCredentials], Depends(_bearer)],
) -> User | None:
    if not settings.auth_enforced:
        return None
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_access_token(creds.credentials, settings.jwt_secret)
        uid = int(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    engine = get_engine()
    with Session(engine) as s:
        u = s.get(User, uid)
    if u is None:
        raise HTTPException(status_code=401, detail="Unknown user")
    return u
