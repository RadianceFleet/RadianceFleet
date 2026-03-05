import hmac
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.config import settings

security = HTTPBearer()


def create_admin_token() -> str:
    """Create 30-minute JWT for admin session."""
    exp = datetime.now(timezone.utc) + timedelta(minutes=30)
    payload = {"exp": exp, "sub": "admin", "type": "access"}
    secret = settings.ADMIN_JWT_SECRET or "dev-insecure-secret"
    return jwt.encode(payload, secret, algorithm="HS256")


def require_admin(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """FastAPI dependency: validate JWT, raise 401 if invalid/expired."""
    if not settings.ADMIN_JWT_SECRET:
        raise HTTPException(status_code=401, detail="Admin auth not configured (set ADMIN_JWT_SECRET)")
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.ADMIN_JWT_SECRET,
            algorithms=["HS256"],
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def verify_admin_password(plain: str) -> bool:
    """Constant-time compare against ADMIN_PASSWORD from settings."""
    if not settings.ADMIN_PASSWORD:
        return False
    return hmac.compare_digest(plain.encode(), settings.ADMIN_PASSWORD.encode())
