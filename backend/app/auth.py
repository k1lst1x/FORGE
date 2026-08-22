import base64
import binascii
import hashlib
import hmac
import secrets
import time

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

security = HTTPBearer(auto_error=False)
security_dependency = Depends(security)


def _password_digest(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)


def verify_password(password: str) -> bool:
    expected = settings.auth_password_hash
    if "$" in expected:
        salt_text, expected = expected.split("$", 1)
        try:
            salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        except (binascii.Error, ValueError):
            return False
    else:
        salt = settings.auth_password_salt.encode()
        if not expected:
            expected = base64.urlsafe_b64encode(
                _password_digest(settings.auth_password, salt)
            ).decode()
    actual = base64.urlsafe_b64encode(_password_digest(password, salt)).decode()
    return hmac.compare_digest(actual, expected)


def create_token(username: str) -> str:
    expires_at = int(time.time()) + settings.auth_token_ttl_seconds
    payload = f"{username}:{expires_at}"
    signature = hmac.new(settings.auth_secret.encode(), payload.encode(), hashlib.sha256).digest()
    encoded = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{payload}:{encoded}"


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = security_dependency,
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    try:
        username, expires_text, signature = credentials.credentials.split(":", 2)
        expires_at = int(expires_text)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from None

    payload = f"{username}:{expires_at}"
    expected = hmac.new(settings.auth_secret.encode(), payload.encode(), hashlib.sha256).digest()
    try:
        actual = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    except (binascii.Error, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from None
    if (
        username != settings.auth_username
        or expires_at <= int(time.time())
        or not hmac.compare_digest(actual, expected)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    return username


def generate_password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _password_digest(password, salt)
    return f"{base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"