"""Token authentication for the job endpoints.

A single static API key, sent as ``Authorization: Bearer <key>``. There is no user
model in this system — nothing to put in a JWT's claims and nothing to revoke
individually — so a signed token would add key rotation, expiry and clock handling
without buying anything a shared secret does not already give.

The key is optional locally so ``docker compose up`` works with no setup. Settings
refuses to start a staging or production environment without one, so "we forgot to set
it" fails loudly instead of silently shipping an open API.
"""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

SettingsDep = Annotated[Settings, Depends(get_settings)]

UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Missing or invalid API key. Send 'Authorization: Bearer <key>'.",
    headers={"WWW-Authenticate": "Bearer"},
)


def extract_bearer_token(header: str | None) -> str | None:
    """Pull the token out of an ``Authorization`` header, or None if malformed."""
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def require_api_key(request: Request, settings: SettingsDep) -> None:
    """Reject the request unless it carries the configured API key.

    A no-op when no key is configured, which is only allowed locally.
    """
    if not settings.auth_enabled:
        return

    token = extract_bearer_token(request.headers.get("Authorization"))
    if token is None:
        logger.warning("auth.rejected", reason="missing_token", path=request.url.path)
        raise UNAUTHENTICATED

    # Constant-time: a plain == leaks how much of the key matched via timing.
    if not secrets.compare_digest(token, settings.api_key or ""):
        logger.warning("auth.rejected", reason="bad_token", path=request.url.path)
        raise UNAUTHENTICATED
