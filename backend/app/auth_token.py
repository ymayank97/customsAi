from __future__ import annotations

import time
from typing import Any

import jwt
from google.auth.transport import requests as grequests
from google.oauth2 import id_token


_GOOGLE_ISS = ("https://accounts.google.com", "accounts.google.com")


def verify_google_token(credential: str, google_client_id: str) -> dict[str, Any]:
    claims = id_token.verify_oauth2_token(
        credential,
        grequests.Request(),
        audience=google_client_id,
    )
    iss = claims.get("iss") or ""
    if iss not in _GOOGLE_ISS:
        raise ValueError("Invalid token issuer")
    if not claims.get("sub"):
        raise ValueError("Missing subject")
    return claims


def mint_access_token(*, user_id: int, email: str, secret: str, expire_days: int) -> str:
    now = int(time.time())
    payload = {"sub": str(user_id), "email": email, "iat": now, "exp": now + expire_days * 86400}
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str, secret: str) -> dict[str, Any]:
    return jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        options={"require": ["exp", "iat", "sub"]},
    )
