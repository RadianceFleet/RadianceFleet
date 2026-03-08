import hmac
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext
from app.config import settings

security = HTTPBearer()
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return _pwd_context.verify(plain, hashed)


def verify_admin_password(plain: str) -> bool:
    """Constant-time compare against ADMIN_PASSWORD from settings (legacy)."""
    if not settings.ADMIN_PASSWORD:
        return False
    return hmac.compare_digest(plain.encode(), settings.ADMIN_PASSWORD.encode())


# ---------------------------------------------------------------------------
# JWT creation
# ---------------------------------------------------------------------------

def create_token(analyst_id: int, username: str, role: str) -> str:
    """Create a 30-minute JWT for an analyst session."""
    exp = datetime.now(timezone.utc) + timedelta(minutes=30)
    payload = {
        "exp": exp,
        "sub": username,
        "analyst_id": analyst_id,
        "role": role,
        "type": "access",
    }
    secret = settings.ADMIN_JWT_SECRET or "dev-insecure-secret"
    return jwt.encode(payload, secret, algorithm="HS256")


def create_admin_token() -> str:
    """Create 30-minute JWT for admin session (backward compat)."""
    return create_token(0, "admin", "admin")


# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------

def require_auth(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """FastAPI dependency: validate JWT, return analyst identity dict.

    Returns: {"analyst_id": int, "username": str, "role": str}
    Legacy tokens (sub=="admin" without analyst_id) get analyst_id=0.
    """
    if not settings.ADMIN_JWT_SECRET:
        raise HTTPException(status_code=401, detail="Auth not configured (set ADMIN_JWT_SECRET)")
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.ADMIN_JWT_SECRET,
            algorithms=["HS256"],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Legacy token compatibility: old tokens have sub="admin" but no analyst_id
    if "analyst_id" not in payload and payload.get("sub") == "admin":
        return {"analyst_id": 0, "username": "admin", "role": "admin"}

    return {
        "analyst_id": payload.get("analyst_id", 0),
        "username": payload.get("sub", "unknown"),
        "role": payload.get("role", "analyst"),
    }


def require_admin(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """FastAPI dependency: validate JWT and require admin role."""
    auth = require_auth(credentials)
    if auth["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return auth


def require_senior_or_admin(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """FastAPI dependency: require senior_analyst or admin role."""
    auth = require_auth(credentials)
    if auth["role"] not in ("senior_analyst", "admin"):
        raise HTTPException(status_code=403, detail="Senior analyst or admin role required")
    return auth
