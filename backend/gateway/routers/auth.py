from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from backend.gateway.config import settings
from backend.gateway.middleware.auth import (
    USERS,
    TokenError,
    check_login,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    demo_logins,
    user_by_id,
)
from backend.shared.events.event_bus import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict


class DemoLoginsResponse(BaseModel):
    demo_logins: list[str]
    note: str


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request):
    user = await check_login(body.username, body.password)
    client = request.client.host if request.client else None
    if not user:
        # Typed text goes into the audit trail: keep it short and printable.
        # Actor unknown: a failed login proves nothing about who typed it.
        name = "".join(ch for ch in body.username[:40] if ch.isprintable())
        await audit("login_failed",
                    f"Failed login for {name!r}" + ("" if body.username in USERS else " (no such user)"),
                    actor="unknown", payload={"username": name, "client": client})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    demo = user.username in demo_logins()
    await audit("login", f"{user.username} logged in" + (" (sandbox login)" if demo else ""),
                actor=user.username, payload={"client": client, "demo_login": demo})
    return TokenResponse(
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(user),
        expires_in=settings.jwt_expiry_minutes * 60,
        user=user.model_dump(),
    )


@router.get("/demo-logins", response_model=DemoLoginsResponse)
async def get_demo_logins():
    """Which users still accept the demo login (password = user name) here - no login needed.

    The login page shows demo buttons only for these; empty on a machine where
    every user has a real password, and always empty on mainnet.
    """
    users = demo_logins()
    return DemoLoginsResponse(
        demo_logins=users,
        note=("Sandbox logins: password = user name, sandbox only. Real passwords: scripts/hash_password.py."
              if users else "Every user needs a real password on this gateway."),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest):
    try:
        user = user_by_id(decode_refresh_token(body.refresh_token))
    except TokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return TokenResponse(
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(user),
        expires_in=settings.jwt_expiry_minutes * 60,
        user=user.model_dump(),
    )
