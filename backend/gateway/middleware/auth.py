from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
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


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> User:
    token = credentials.credentials
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
            )
        return User(
            id=payload["sub"],
            username=payload["username"],
            role=UserRole(payload["role"]),
            name=payload["name"],
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        )


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
