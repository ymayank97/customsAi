from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.auth_token import mint_access_token, verify_google_token
from backend.app.config import settings
from backend.app.db import User, get_engine
from backend.app.deps import get_current_user
from backend.app.rate_limit import limiter
from backend.app.membership import user_payload

router = APIRouter(tags=["auth"])


class GoogleLoginBody(BaseModel):
    credential: str = Field(description="Google Sign-In JWT (credential)")


@router.get("/api/auth/config")
def auth_public_config():
    """Frontend loads this without auth to bootstrap Google GIS."""
    return {
        "auth_required": settings.auth_enforced,
        "google_client_id": settings.google_oauth_client_id or "",
    }


@router.post("/api/auth/google")
@limiter.limit("10/minute", key_func=get_remote_address)
def google_exchange(request: Request, body: GoogleLoginBody):
    if not settings.auth_enforced:
        raise HTTPException(
            400,
            "OAuth disabled: set GOOGLE_OAUTH_CLIENT_ID and JWT_SECRET to enable.",
        )
    try:
        claims = verify_google_token(body.credential, settings.google_oauth_client_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid Google credential")
    gsub = (claims.get("sub") or "").strip()
    email = (claims.get("email") or "").strip().lower()
    name = (claims.get("name") or "").strip()
    picture = (claims.get("picture") or "").strip()
    if not gsub or not email:
        raise HTTPException(status_code=401, detail="Incomplete Google identity")

    engine = get_engine()
    with Session(engine) as session:
        user = session.execute(select(User).where(User.google_sub == gsub)).scalar_one_or_none()
        if user:
            user.email = email
            user.name = name or user.name
            user.picture = picture or user.picture
        else:
            user = User(
                google_sub=gsub,
                email=email,
                name=name,
                picture=picture or "",
            )
            session.add(user)
        session.commit()
        session.refresh(user)

    token = mint_access_token(
        user_id=user.id,
        email=user.email,
        secret=settings.jwt_secret,
        expire_days=settings.jwt_exp_days,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in_days": settings.jwt_exp_days,
        "user": user_payload(user),
    }


@router.get("/api/auth/me")
def auth_me(who: User | None = Depends(get_current_user)):
    if not settings.auth_enforced:
        return {"authenticated": False, "auth_disabled": True, "user": None}
    assert who is not None
    return {
        "authenticated": True,
        "auth_disabled": False,
        "user": user_payload(who),
    }
