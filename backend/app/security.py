from datetime import datetime, timedelta, timezone
import bcrypt
import jwt

from app.config import Settings


def hash_password(password: str) -> str:
    encoded = password.encode("utf-8")
    if len(encoded) > 72:
        raise ValueError("Password must be no more than 72 UTF-8 bytes.")
    return bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        if len(password.encode("utf-8")) > 72:
            return False
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return False


def create_access_token(
    username: str, settings: Settings, token_version: int = 0
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "iat": now,
        "exp": now + timedelta(minutes=settings.token_minutes),
        "ver": token_version,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
