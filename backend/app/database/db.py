import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import Settings


def _migrate_legacy_roles(connection: sqlite3.Connection) -> None:
    connection.execute("UPDATE users SET role = 'admin' WHERE role = 'ceo'")


def _migrate_token_versions(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(users)")
    }
    if "token_version" not in columns:
        connection.execute(
            "ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0"
        )


def _migrate_login_attempts(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS login_attempts (
            identifier_hash TEXT PRIMARY KEY,
            attempts INTEGER NOT NULL CHECK (attempts > 0),
            window_started_at INTEGER NOT NULL,
            blocked_until INTEGER
        )"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_login_attempts_window
           ON login_attempts(window_started_at)"""
    )


def _migrate_case_insensitive_emails(connection: sqlite3.Connection) -> None:
    try:
        connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_employees_email_nocase
               ON employees(email COLLATE NOCASE)"""
        )
    except sqlite3.IntegrityError as exc:
        raise RuntimeError(
            "Database contains duplicate employee emails differing only by case. "
            "Resolve the duplicates before restarting ERMS."
        ) from exc


def _migrate_advanced_hrms(connection: sqlite3.Connection) -> None:
    migration_path = Path(__file__).with_name("migration_005.sql")
    connection.executescript(migration_path.read_text(encoding="utf-8"))


def _migrate_department_heads(connection: sqlite3.Connection) -> None:
    migration_path = Path(__file__).with_name("migration_006.sql")
    connection.executescript(migration_path.read_text(encoding="utf-8"))


MIGRATIONS = (
    _migrate_legacy_roles,
    _migrate_token_versions,
    _migrate_login_attempts,
    _migrate_case_insensitive_emails,
    _migrate_advanced_hrms,
    _migrate_department_heads,
)


def connect(settings: Settings) -> sqlite3.Connection:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.database_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def session(settings: Settings) -> Iterator[sqlite3.Connection]:
    connection = connect(settings)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize(settings: Settings) -> None:
    schema_path = Path(__file__).with_name("schema.sql")
    with session(settings) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        current_version = connection.execute("PRAGMA user_version").fetchone()[0]
        if current_version > len(MIGRATIONS):
            raise RuntimeError(
                f"Database schema version {current_version} is newer than this application supports."
            )
        connection.executescript(schema_path.read_text(encoding="utf-8"))
        for version, migration in enumerate(MIGRATIONS, start=1):
            if version <= current_version:
                continue
            migration(connection)
            connection.execute(f"PRAGMA user_version = {version}")
        connection.executemany(
            "INSERT OR IGNORE INTO departments(name) VALUES (?)",
            [("Engineering",), ("Human Resources",), ("Finance",), ("Design",), ("Operations",)],
        )
        connection.executemany(
            "INSERT OR IGNORE INTO designations(name) VALUES (?)",
            [
                ("Software Engineer",),
                ("HR Executive",),
                ("Financial Analyst",),
                ("UI/UX Designer",),
                ("Operations Manager",),
            ],
        )
