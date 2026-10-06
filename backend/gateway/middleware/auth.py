import os
import secrets
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError
from pydantic import BaseModel

from backend.gateway.config import MIN_JWT_SECRET_LENGTH, Settings, settings

# The key that signs and checks every login token. Set once at start-up by
# load_jwt_secret() (the gateway's lifespan); never a value from the code.
_signing_key: str | None = None


def load_or_create_secret(path: Path) -> tuple[str, bool]:
    """Read this machine's signing key, or create a random one the first time.

    Written to a temporary file and renamed, so a crash never leaves half a
    key; a missing, empty or too-short file is replaced (that only logs
    everyone out once). The file sits in data/ (gitignored, per machine).

    Returns:
        The key and whether it was just created.
    """
    if path.exists():
        existing = path.read_text(encoding="utf-8").strip()
        if len(existing) >= MIN_JWT_SECRET_LENGTH:
            return existing, False
    key = secrets.token_hex(32)  # 256 random bits
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # owner-only where the OS supports it
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key + "\n")
    os.replace(tmp, path)
    return key, True


def load_jwt_secret(s: Settings = settings) -> dict[str, object]:
    """Choose the login-signing key, once, at start-up.

    A real JWT_SECRET in .env wins; otherwise this machine's own random key in
    data/jwt-secret-<mode>.key, created the first time.

    Returns:
        Where the key came from, for the start-up log (never the key itself).

    Raises:
        RuntimeError: JWT_SECRET is set in .env but too short to be safe.
    """
    global _signing_key
    if problem := s.jwt_secret_problem():
        raise RuntimeError(problem)
    if from_env := s.jwt_secret_from_env():
        _signing_key = from_env
        return {"source": ".env"}
    path = Path(s.resolved_jwt_secret_path)
    _signing_key, created = load_or_create_secret(path)
    return {"source": "this machine's key file", "path": str(path), "created": created,
            "ignored_public_placeholder": s.jwt_secret_is_placeholder()}


def _key() -> str:
    if _signing_key is None:
        raise RuntimeError("login-signing key not loaded: call load_jwt_secret() at start-up")
    return _signing_key


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
    return jwt.encode(payload, _key(), algorithm=settings.jwt_algorithm)


def create_refresh_token(user: User) -> str:
    expire = datetime.now(UTC) + timedelta(days=7)
    payload = {"sub": user.id, "type": "refresh", "exp": expire}
    return jwt.encode(payload, _key(), algorithm=settings.jwt_algorithm)


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
        payload = jwt.decode(token, _key(), algorithms=[settings.jwt_algorithm])
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


def decode_refresh_token(token: str) -> str:
    """Validate a refresh token (signature, expiry, type).

    Returns:
        The user id it was issued to.

    Raises:
        TokenError: a bad signature, an expired token, or not a refresh token.
    """
    try:
        payload = jwt.decode(token, _key(), algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise TokenError("invalid or expired refresh token") from exc
    if payload.get("type") != "refresh" or not isinstance(payload.get("sub"), str):
        raise TokenError("invalid refresh token")
    return payload["sub"]


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
