from dataclasses import dataclass
import os
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    jwt_secret: str
    database_path: Path
    cors_origins: tuple[str, ...]
    token_minutes: int
    login_max_attempts: int = 5
    login_window_seconds: int = 900
    organization_timezone: str = "UTC"
    workday_start: str = "09:00"
    company_name: str = "ERMS"
    public_api_url: str = "http://127.0.0.1:8000"
    max_upload_bytes: int = 10 * 1024 * 1024
    company_logo_path: Path | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        raw_path = Path(os.getenv("ERMS_DATABASE_PATH", "database/erms.sqlite3"))
        database_path = raw_path if raw_path.is_absolute() else BACKEND_ROOT / raw_path
        origins = tuple(
            origin.strip()
            for origin in os.getenv(
                "ERMS_CORS_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173",
            ).split(",")
            if origin.strip()
        )
        try:
            token_minutes = int(os.getenv("ERMS_TOKEN_MINUTES", "30"))
        except ValueError as exc:
            raise ValueError("ERMS_TOKEN_MINUTES must be a positive integer.") from exc
        try:
            login_max_attempts = int(os.getenv("ERMS_LOGIN_MAX_ATTEMPTS", "5"))
            login_window_seconds = int(os.getenv("ERMS_LOGIN_WINDOW_SECONDS", "900"))
            max_upload_bytes = int(os.getenv("ERMS_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
        except ValueError as exc:
            raise ValueError(
                "Login attempt limits, upload size and windows must be positive integers."
            ) from exc
        raw_logo_path = os.getenv("ERMS_COMPANY_LOGO", "").strip()
        logo_path = Path(raw_logo_path) if raw_logo_path else None
        if logo_path is not None and not logo_path.is_absolute():
            logo_path = BACKEND_ROOT / logo_path
        return cls(
            jwt_secret=os.getenv("ERMS_JWT_SECRET", ""),
            database_path=database_path,
            cors_origins=origins,
            token_minutes=token_minutes,
            login_max_attempts=login_max_attempts,
            login_window_seconds=login_window_seconds,
            organization_timezone=os.getenv("ERMS_TIMEZONE", "UTC").strip(),
            workday_start=os.getenv("ERMS_WORKDAY_START", "09:00").strip(),
            company_name=os.getenv("ERMS_COMPANY_NAME", "ERMS").strip(),
            public_api_url=os.getenv(
                "ERMS_PUBLIC_API_URL", "http://127.0.0.1:8000"
            ).rstrip("/"),
            max_upload_bytes=max_upload_bytes,
            company_logo_path=logo_path,
        )

    def validate(self) -> None:
        if len(self.jwt_secret) < 32:
            raise ValueError("ERMS_JWT_SECRET must contain at least 32 characters.")
        if self.token_minutes < 1:
            raise ValueError("ERMS_TOKEN_MINUTES must be a positive integer.")
        if self.login_max_attempts < 1 or self.login_window_seconds < 1:
            raise ValueError(
                "Login attempt limits and windows must be positive integers."
            )
        if self.max_upload_bytes < 1:
            raise ValueError("ERMS_MAX_UPLOAD_BYTES must be positive.")
        if not self.organization_timezone or not self.company_name:
            raise ValueError("ERMS_TIMEZONE and ERMS_COMPANY_NAME must not be blank.")
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(self.organization_timezone)
        except (KeyError, ValueError) as exc:
            raise ValueError("ERMS_TIMEZONE must be a valid IANA timezone.") from exc
        try:
            from datetime import time

            time.fromisoformat(self.workday_start)
        except ValueError as exc:
            raise ValueError("ERMS_WORKDAY_START must use HH:MM format.") from exc
        if not self.cors_origins:
            raise ValueError("Configure at least one ERMS_CORS_ORIGINS value.")
