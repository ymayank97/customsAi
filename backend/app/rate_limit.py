"""Shared SlowAPI limiter for FastAPI routes."""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def rate_key_user_or_ip(request: Request) -> str:
    auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer ") and len(auth) > 24:
        return "u:" + auth[7:40]
    return "ip:" + get_remote_address(request)


limiter = Limiter(key_func=rate_key_user_or_ip)
