from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError
from pydantic import BaseModel

from backend.gateway.config import settings


class UserRole(str, Enum):
    ADMIN = "ADMIN"
    TRADER = "TRADER"
    RISK_MANAGER = "RISK_MANAGER"


class User(BaseModel):
    id: str
    username: str
    role: UserRole
    name: str


HARDCODED_USERS: dict[str, dict] = {
    "admin": {
        "id": "user_001",
        "username": "admin",
        "password": "admin",
        "role": UserRole.ADMIN,
        "name": "System Administrator",
    },
    "trader1": {
        "id": "user_002",
        "username": "trader1",
        "password": "trader1",
        "role": UserRole.TRADER,
        "name": "Trader One",
    },
    "risk_mgr": {
        "id": "user_003",
        "username": "risk_mgr",
        "password": "risk_mgr",
        "role": UserRole.RISK_MANAGER,
        "name": "Risk Manager",
    },
}

security = HTTPBearer()


def authenticate_user(username: str, password: str) -> User | None:
    user_data = HARDCODED_USERS.get(username)
    if not user_data or user_data["password"] != password:
        return None
    return User(
        id=user_data["id"],
        username=user_data["username"],
        role=user_data["role"],
        name=user_data["name"],
    )


def create_access_token(user: User) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_expiry_minutes)
    payload = {
        "sub": user.id,
        "username": user.username,
        "role": user.role.value,
        "name": user.name,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(user: User) -> str:
    expire = datetime.now(UTC) + timedelta(days=7)
    payload = {"sub": user.id, "type": "refresh", "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


class TokenError(Exception):
    """A login token that must not be accepted; the message says why (never the token)."""


def decode_access_token(token: str | None) -> tuple[User, datetime]:
    """Validate a login (access) token: signature, expiry and shape.

    The one check shared by the REST endpoints and the WebSocket.

    Returns:
        The user and the moment the token expires.

    Raises:
        TokenError: no token, a bad signature, an expired token, a refresh
            token used as a login, or a token missing required fields.
    """
    if not token:
        raise TokenError("no login token")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except ExpiredSignatureError as exc:
        raise TokenError("login expired") from exc
    except JWTError as exc:
        raise TokenError("invalid login token") from exc
    if payload.get("type") == "refresh":
        raise TokenError("a refresh token is not a login token")
    try:
        user = User(
            id=payload["sub"],
            username=payload["username"],
            role=UserRole(payload["role"]),
            name=payload["name"],
        )
        expires_at = datetime.fromtimestamp(float(payload["exp"]), UTC)
    except (KeyError, ValueError, TypeError) as exc:
        raise TokenError("invalid login token") from exc
    return user, expires_at


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> User:
    try:
        user, _ = decode_access_token(credentials.credentials)
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return user


def require_role(*roles: UserRole):
    async def role_checker(
        user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role {user.role} not authorized. Required: {[r.value for r in roles]}",
            )
        return user

    return role_checker
