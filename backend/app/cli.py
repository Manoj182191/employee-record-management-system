import argparse
import getpass
import re
import sqlite3
import sys
from pathlib import Path

from app.config import Settings
from app.database.db import connect, initialize, session
from app.security import hash_password


def create_user(role: str) -> int:
    settings = Settings.from_env()
    username = input("Username: ").strip()
    password = getpass.getpass("Password (minimum 12 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if not re.fullmatch(r"[A-Za-z0-9._-]{2,80}", username):
        print("Username must contain 2-80 letters, numbers, dots, underscores or hyphens.")
        return 2
    if len(password) < 12 or password != confirmation:
        print("Passwords must match and contain at least 12 characters.")
        return 2
    try:
        password_hash = hash_password(password)
    except ValueError as exc:
        print(str(exc))
        return 2
    initialize(settings)
    with session(settings) as connection:
        try:
            connection.execute(
                "INSERT INTO users(username, password_hash, role) VALUES (?, ?, ?)",
                (username, password_hash, role),
            )
        except sqlite3.IntegrityError:
            print("That username already exists.")
            return 2
    print(f"Created {role} account '{username}'.")
    return 0


def reset_password(username: str) -> int:
    settings = Settings.from_env()
    password = getpass.getpass("New password (minimum 12 characters): ")
    confirmation = getpass.getpass("Confirm new password: ")
    if len(password) < 12 or password != confirmation:
        print("Passwords must match and contain at least 12 characters.")
        return 2
    try:
        password_hash = hash_password(password)
    except ValueError as exc:
        print(str(exc))
        return 2
    initialize(settings)
    with session(settings) as connection:
        cursor = connection.execute(
            """UPDATE users SET password_hash = ?, token_version = token_version + 1
               WHERE username = ? COLLATE NOCASE""",
            (password_hash, username),
        )
        if cursor.rowcount == 0:
            print(f"No account exists with username '{username}'.")
            return 2
        connection.execute(
            """INSERT INTO audit_logs(actor_username, action, target_id)
               VALUES (?, ?, ?)""",
            ("system:cli", "account.password_reset", username),
        )
    print(f"Password reset for '{username}'. Existing access tokens were revoked.")
    return 0


def create_backup(destination: Path) -> int:
    settings = Settings.from_env()
    source_path = settings.database_path.resolve()
    destination_path = destination.resolve()
    if not source_path.is_file():
        print(f"Database does not exist: {source_path}")
        return 2
    if destination_path == source_path:
        print("Backup destination must differ from the live database.")
        return 2
    if destination_path.exists():
        print(f"Backup destination already exists: {destination_path}")
        return 2
    if not destination_path.parent.is_dir():
        print(f"Backup directory does not exist: {destination_path.parent}")
        return 2
    source = destination_connection = None
    destination_created = False
    failure_message = None
    try:
        source = sqlite3.connect(source_path)
        destination_connection = connect(
            Settings(
                jwt_secret=settings.jwt_secret,
                database_path=destination_path,
                cors_origins=settings.cors_origins,
                token_minutes=settings.token_minutes,
            )
        )
        destination_created = True
        source.backup(destination_connection)
        integrity = destination_connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise sqlite3.DatabaseError(
                f"Backup integrity check failed: {integrity}"
            )
    except (OSError, sqlite3.Error) as exc:
        failure_message = str(exc)
    finally:
        if destination_connection is not None:
            destination_connection.close()
        if source is not None:
            source.close()
    if failure_message is not None:
        if destination_created:
            try:
                destination_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                print(f"Could not remove incomplete backup: {exc}")
        print(f"Could not create database backup: {failure_message}")
        return 2
    print(f"Database backup created: {destination_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Provision ERMS administrator accounts and maintain the database."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("create-admin", help="Create an administrator account")
    reset_parser = subparsers.add_parser(
        "reset-password", help="Reset an account password and revoke its tokens"
    )
    reset_parser.add_argument("username")
    backup_parser = subparsers.add_parser(
        "backup", help="Create a verified SQLite backup"
    )
    backup_parser.add_argument("destination", type=Path)
    arguments = parser.parse_args()
    if arguments.command == "create-admin":
        return create_user("admin")
    if arguments.command == "reset-password":
        return reset_password(arguments.username)
    return create_backup(arguments.destination)


if __name__ == "__main__":
    sys.exit(main())
