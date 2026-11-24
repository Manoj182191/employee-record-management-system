import sqlite3
from pathlib import Path

from app.cli import create_backup, reset_password
from app.config import Settings
from app.database.db import initialize, session
from app.security import hash_password, verify_password


def test_initialize_migrates_existing_management_account(tmp_path: Path):
    database_path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE employees (
            employee_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            department TEXT NOT NULL,
            designation TEXT NOT NULL,
            phone TEXT NOT NULL DEFAULT '',
            joining_date TEXT NOT NULL DEFAULT '',
            salary REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'Pending',
            active INTEGER NOT NULL DEFAULT 1,
            onboarding_complete INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE users (
            username TEXT PRIMARY KEY COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'employee', 'ceo')),
            employee_id TEXT UNIQUE REFERENCES employees(employee_id) ON DELETE CASCADE,
            active INTEGER NOT NULL DEFAULT 1,
            last_login TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO users(username, password_hash, role)
        VALUES ('legacy-management', 'unused-test-hash', 'ceo');
        """
    )
    connection.close()

    settings = Settings(
        jwt_secret="test-secret-that-is-long-enough-for-unit-tests",
        database_path=database_path,
        cors_origins=("http://localhost:5173",),
        token_minutes=30,
    )
    initialize(settings)
    initialize(settings)

    with session(settings) as connection:
        user = connection.execute(
            "SELECT role, token_version FROM users WHERE username = ?",
            ("legacy-management",),
        ).fetchone()
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert tuple(user) == ("admin", 0)
    assert version == 6


def test_cli_password_reset_rotates_password_and_token_version(
    tmp_path: Path, monkeypatch
):
    database_path = tmp_path / "reset.sqlite3"
    monkeypatch.setenv("ERMS_DATABASE_PATH", str(database_path))
    settings = Settings(
        jwt_secret="test-secret-that-is-long-enough-for-unit-tests",
        database_path=database_path,
        cors_origins=("http://localhost:5173",),
        token_minutes=30,
    )
    initialize(settings)
    with session(settings) as connection:
        connection.execute(
            "INSERT INTO users(username, password_hash, role) VALUES (?, ?, ?)",
            ("hr-admin", hash_password("OriginalPassword123!"), "admin"),
        )

    passwords = iter(("ReplacementPassword456!", "ReplacementPassword456!"))
    monkeypatch.setattr("app.cli.getpass.getpass", lambda _prompt: next(passwords))
    assert reset_password("hr-admin") == 0

    with session(settings) as connection:
        user = connection.execute(
            "SELECT password_hash, token_version FROM users WHERE username = ?",
            ("hr-admin",),
        ).fetchone()
    assert verify_password("ReplacementPassword456!", user["password_hash"])
    assert user["token_version"] == 1


def test_cli_creates_verified_database_backup(tmp_path: Path, monkeypatch):
    database_path = tmp_path / "source.sqlite3"
    backup_path = tmp_path / "backups" / "snapshot.sqlite3"
    backup_path.parent.mkdir()
    monkeypatch.setenv("ERMS_DATABASE_PATH", str(database_path))
    settings = Settings(
        jwt_secret="test-secret-that-is-long-enough-for-unit-tests",
        database_path=database_path,
        cors_origins=("http://localhost:5173",),
        token_minutes=30,
    )
    initialize(settings)
    with session(settings) as connection:
        connection.execute(
            "INSERT INTO users(username, password_hash, role) VALUES (?, ?, ?)",
            ("hr-admin", hash_password("ValidTestPassword123!"), "admin"),
        )

    assert create_backup(backup_path) == 0
    with sqlite3.connect(backup_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert create_backup(backup_path) == 2
