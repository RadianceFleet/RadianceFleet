import hmac
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import settings

security = HTTPBearer(auto_error=False)
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
    exp = datetime.now(UTC) + timedelta(minutes=30)
    payload = {
        "exp": exp,
        "sub": username,
        "analyst_id": analyst_id,
        "role": role,
        "type": "access",
    }
    if not settings.ADMIN_JWT_SECRET:
        raise ValueError("ADMIN_JWT_SECRET must be set to create tokens")
    return jwt.encode(payload, settings.ADMIN_JWT_SECRET, algorithm="HS256")


def create_admin_token() -> str:
    """Create 30-minute JWT for admin session (backward compat)."""
    return create_token(0, "admin", "admin")


# ---------------------------------------------------------------------------
# API Key verification
# ---------------------------------------------------------------------------


def verify_api_key(key: str, db: Session) -> dict | None:
    """Check a raw API key against all active ApiKey records.

    Returns {"key_id": ..., "scope": "read_only"} on match, else None.
    """
    from app.models.api_key import ApiKey

    active_keys = db.query(ApiKey).filter(ApiKey.is_active == True).all()  # noqa: E712
    for record in active_keys:
        if verify_password(key, record.key_hash):
            return {"key_id": record.key_id, "scope": record.scope}
    return None


# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------


def require_auth(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    request: Request = None,
) -> dict:
    """FastAPI dependency: validate JWT or X-API-Key header, return identity dict.

    Returns: {"analyst_id": int, "username": str, "role": str}
    Legacy tokens (sub=="admin" without analyst_id) get analyst_id=0.
    API key auth returns analyst_id=0, role="viewer".
    """
    # --- Try X-API-Key header first ---
    api_key_header = request.headers.get("X-API-Key") if request else None
    if api_key_header:
        from app.database import get_db

        db: Session = next(get_db())
        try:
            result = verify_api_key(api_key_header, db)
        finally:
            db.close()
        if result:
            return {
                "analyst_id": 0,
                "username": "api_key",
                "role": "viewer",
                "key_id": result["key_id"],
            }
        raise HTTPException(status_code=401, detail="Invalid API key")

    # --- Fall back to Bearer JWT ---
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if not settings.ADMIN_JWT_SECRET:
        raise HTTPException(status_code=401, detail="Auth not configured (set ADMIN_JWT_SECRET)")
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.ADMIN_JWT_SECRET,
            algorithms=["HS256"],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token") from None

    # Legacy token compatibility: old tokens have sub="admin" but no analyst_id
    if "analyst_id" not in payload and payload.get("sub") == "admin":
        return {"analyst_id": 0, "username": "admin", "role": "admin"}

    return {
        "analyst_id": payload.get("analyst_id", 0),
        "username": payload.get("sub", "unknown"),
        "role": payload.get("role", "analyst"),
    }


def require_admin(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    request: Request = None,
) -> dict:
    """FastAPI dependency: validate JWT and require admin role."""
    auth = require_auth(credentials, request)
    if auth["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return auth


def require_senior_or_admin(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    request: Request = None,
) -> dict:
    """FastAPI dependency: require senior_analyst or admin role."""
    auth = require_auth(credentials, request)
    if auth["role"] not in ("senior_analyst", "admin"):
        raise HTTPException(status_code=403, detail="Senior analyst or admin role required")
    return auth
