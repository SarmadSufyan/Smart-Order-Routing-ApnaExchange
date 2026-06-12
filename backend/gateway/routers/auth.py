from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from backend.gateway.config import settings
from backend.gateway.middleware.auth import (
    authenticate_user,
    create_access_token,
    create_refresh_token,
)

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


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    user = authenticate_user(body.username, body.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    return TokenResponse(
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(user),
        expires_in=settings.jwt_expiry_minutes * 60,
        user=user.model_dump(),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest):
    from jose import JWTError, jwt

    try:
        payload = jwt.decode(
            body.refresh_token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid refresh token")

        from backend.gateway.middleware.auth import HARDCODED_USERS, User, UserRole

        user_id = payload["sub"]
        user_data = next(
            (u for u in HARDCODED_USERS.values() if u["id"] == user_id), None
        )
        if not user_data:
            raise HTTPException(status_code=401, detail="User not found")

        user = User(
            id=user_data["id"],
            username=user_data["username"],
            role=user_data["role"],
            name=user_data["name"],
        )
        return TokenResponse(
            access_token=create_access_token(user),
            refresh_token=create_refresh_token(user),
            expires_in=settings.jwt_expiry_minutes * 60,
            user=user.model_dump(),
        )
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
