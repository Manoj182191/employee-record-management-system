import csv
import hashlib
import hmac
import io
import json
import re
import sqlite3
import time
from datetime import date
from typing import Annotated, Any, Literal

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import Settings
from app.database.db import session
from app.dependencies import current_user_dependency, role_dependency
from app.hashmap import EmployeeHashMap, LinearProbingMap
from app.security import create_access_token, hash_password, verify_password
from app.advanced_api import (
    _initialize_onboarding_tasks,
    _notify_admins,
    _notify_employee,
    _timeline,
)


Role = Literal["admin", "employee"]
EmployeeStatus = Literal["Pending", "Approved", "Rejected"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(StrictModel):
    login_type: Literal["admin", "employee"]
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=1, max_length=72)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Username must not be blank.")
        return value

    @field_validator("password")
    @classmethod
    def validate_password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be no more than 72 UTF-8 bytes.")
        return value


class EmployeeRegistrationInput(StrictModel):
    employee_id: str = Field(min_length=2, max_length=30)
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=5, max_length=254)
    phone: str = Field(min_length=1, max_length=30)
    department: str = Field(min_length=1, max_length=80)
    designation: str = Field(min_length=1, max_length=80)
    joining_date: str = Field(min_length=10, max_length=10)
    password: str = Field(min_length=12, max_length=72)
    confirm_password: str = Field(min_length=12, max_length=72)

    @field_validator("employee_id")
    @classmethod
    def validate_employee_id(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{2,30}", value):
            raise ValueError("Use 2-30 letters, numbers, dots, underscores or hyphens.")
        return value

    @field_validator("name", "department", "designation")
    @classmethod
    def require_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field must not be blank.")
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return EmployeeInput.normalize_email(value)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Phone number must not be blank.")
        if not any(character.isdigit() for character in value):
            raise ValueError("Enter a phone number containing at least one digit.")
        return value

    @field_validator("joining_date")
    @classmethod
    def validate_joining_date(cls, value: str) -> str:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Use an ISO date in YYYY-MM-DD format.")
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Use a valid date in YYYY-MM-DD format.") from exc
        return value

    @field_validator("password")
    @classmethod
    def validate_password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be no more than 72 UTF-8 bytes.")
        return value

    @model_validator(mode="after")
    def validate_password_confirmation(self) -> "EmployeeRegistrationInput":
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self


class EmployeeInput(StrictModel):
    employee_id: str = Field(min_length=2, max_length=30)
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=5, max_length=254)
    department: str = Field(min_length=1, max_length=80)
    designation: str = Field(min_length=1, max_length=80)
    phone: str = Field(default="", max_length=30)
    joining_date: str = Field(default="", max_length=10)
    salary: float = Field(ge=0, le=1_000_000_000)
    password: str = Field(min_length=12, max_length=72)

    @field_validator("employee_id")
    @classmethod
    def validate_employee_id(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{2,30}", value):
            raise ValueError("Use 2-30 letters, numbers, dots, underscores or hyphens.")
        return value

    @field_validator("name", "department", "designation")
    @classmethod
    def require_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field must not be blank.")
        return value

    @field_validator("phone")
    @classmethod
    def strip_phone(cls, value: str) -> str:
        return value.strip()

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Enter a valid email address.")
        return value

    @field_validator("joining_date")
    @classmethod
    def validate_joining_date(cls, value: str) -> str:
        value = value.strip()
        if value:
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("Use an ISO date in YYYY-MM-DD format.") from exc
        return value

    @field_validator("password")
    @classmethod
    def validate_password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be no more than 72 UTF-8 bytes.")
        return value


class EmployeeUpdate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=5, max_length=254)
    department: str = Field(min_length=1, max_length=80)
    designation: str = Field(min_length=1, max_length=80)
    phone: str = Field(default="", max_length=30)
    joining_date: str = Field(default="", max_length=10)
    salary: float = Field(ge=0, le=1_000_000_000)

    @field_validator("name", "department", "designation")
    @classmethod
    def require_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field must not be blank.")
        return value

    @field_validator("phone")
    @classmethod
    def strip_phone(cls, value: str) -> str:
        return value.strip()

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return EmployeeInput.normalize_email(value)

    @field_validator("joining_date")
    @classmethod
    def validate_joining_date(cls, value: str) -> str:
        return EmployeeInput.validate_joining_date(value)


class ProfileRequestInput(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=30)

    @field_validator("name", "phone")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def require_change(self) -> "ProfileRequestInput":
        if not self.model_fields_set or all(
            getattr(self, field) is None for field in ("name", "phone")
        ):
            raise ValueError("Submit at least one profile change.")
        return self


class ReviewInput(StrictModel):
    status: Literal["Approved", "Rejected"]


class DepartmentInput(StrictModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field must not be blank.")
        return value


class PasswordInput(StrictModel):
    current_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=12, max_length=72)

    @field_validator("new_password")
    @classmethod
    def validate_password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be no more than 72 UTF-8 bytes.")
        return value


def _employee_map_item(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return dict(row)


def _public_employee(
    row: sqlite3.Row | dict[str, Any], role: Role
) -> dict[str, Any]:
    item = dict(row)
    if role != "admin":
        item.pop("salary", None)
    return item


def _record_audit(
    connection: sqlite3.Connection,
    actor: str,
    action: str,
    target: str,
    details: str = "",
) -> None:
    connection.execute(
        "INSERT INTO audit_logs(actor_username, action, target_id, details) VALUES (?, ?, ?, ?)",
        (actor, action, target, details),
    )


def _login_identifier(secret: str, remote_host: str, username: str) -> str:
    value = f"{remote_host}\0{username.casefold()}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), value, hashlib.sha256).hexdigest()


def _record_failed_login(
    connection: sqlite3.Connection,
    identifier: str,
    now: int,
    settings: Settings,
) -> None:
    connection.execute(
        """INSERT INTO login_attempts(
               identifier_hash, attempts, window_started_at, blocked_until
           ) VALUES (?, 1, ?, NULL)
           ON CONFLICT(identifier_hash) DO UPDATE SET
               attempts = CASE
                   WHEN excluded.window_started_at - login_attempts.window_started_at >= ?
                   THEN 1 ELSE login_attempts.attempts + 1 END,
               window_started_at = CASE
                   WHEN excluded.window_started_at - login_attempts.window_started_at >= ?
                   THEN excluded.window_started_at
                   ELSE login_attempts.window_started_at END,
               blocked_until = CASE
                   WHEN excluded.window_started_at - login_attempts.window_started_at >= ?
                   THEN NULL ELSE login_attempts.blocked_until END""",
        (
            identifier,
            now,
            settings.login_window_seconds,
            settings.login_window_seconds,
            settings.login_window_seconds,
        ),
    )
    attempts = connection.execute(
        "SELECT attempts FROM login_attempts WHERE identifier_hash = ?",
        (identifier,),
    ).fetchone()["attempts"]
    if attempts >= settings.login_max_attempts:
        connection.execute(
            """UPDATE login_attempts SET blocked_until = ?
               WHERE identifier_hash = ?""",
            (now + settings.login_window_seconds, identifier),
        )
    connection.execute(
        "DELETE FROM login_attempts WHERE window_started_at < ?",
        (now - settings.login_window_seconds * 2,),
    )


def create_api(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api")
    current_user = current_user_dependency(settings)
    require_roles = role_dependency(current_user)

    @router.post("/auth/login")
    def login(body: LoginInput, request: Request) -> dict[str, Any]:
        now = int(time.time())
        remote_host = request.client.host if request.client else "unknown"
        identifier = _login_identifier(
            settings.jwt_secret, remote_host, body.username
        )
        failure: tuple[int, str, dict[str, str] | None] | None = None
        public_user: dict[str, Any] | None = None
        token: str | None = None
        with session(settings) as connection:
            attempt = connection.execute(
                """SELECT attempts, window_started_at, blocked_until
                   FROM login_attempts WHERE identifier_hash = ?""",
                (identifier,),
            ).fetchone()
            if attempt and attempt["blocked_until"] and attempt["blocked_until"] > now:
                retry_after = attempt["blocked_until"] - now
                failure = (
                    429,
                    "Too many sign-in attempts. Try again later.",
                    {"Retry-After": str(retry_after)},
                )
            user = connection.execute(
                """SELECT u.username, u.password_hash, u.role, u.employee_id,
                          u.active, u.token_version, e.status AS employee_status,
                          e.active AS employee_active
                   FROM users u
                   LEFT JOIN employees e ON e.employee_id = u.employee_id
                   WHERE u.username = ? COLLATE NOCASE""",
                (body.username,),
            ).fetchone() if failure is None else None
            password_matches = bool(
                user is not None
                and verify_password(body.password, user["password_hash"])
            )
            if failure is None:
                if (
                    not password_matches
                    or user["role"] != body.login_type
                ):
                    _record_failed_login(connection, identifier, now, settings)
                    failure = (401, "Invalid username or password.", None)
                elif user["role"] == "employee" and user["employee_status"] == "Pending":
                    failure = (
                        403,
                        "Your employee account is pending Admin approval.",
                        None,
                    )
                elif user["role"] == "employee" and user["employee_status"] == "Rejected":
                    failure = (
                        403,
                        "Your employee registration was rejected. Contact Admin.",
                        None,
                    )
                elif (
                    not user["active"]
                    or (
                        user["role"] == "employee"
                        and (
                            user["employee_status"] != "Approved"
                            or not user["employee_active"]
                        )
                    )
                ):
                    failure = (403, "This account is inactive.", None)
                else:
                    connection.execute(
                        "DELETE FROM login_attempts WHERE identifier_hash = ?",
                        (identifier,),
                    )
                    connection.execute(
                        "UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE username = ?",
                        (user["username"],),
                    )
                    public_user = {
                        "username": user["username"],
                        "role": user["role"],
                        "employee_id": user["employee_id"],
                    }
                    token = create_access_token(
                        user["username"], settings, user["token_version"]
                    )
        if failure is not None:
            status, detail, headers = failure
            raise HTTPException(status, detail, headers=headers)
        if public_user is None or token is None:
            raise RuntimeError("Login completed without a user or access token.")
        return {
            "access_token": token,
            "token_type": "bearer",
            "user": public_user,
        }

    @router.post("/auth/register", status_code=201)
    def register_employee(
        body: EmployeeRegistrationInput, request: Request
    ) -> dict[str, str]:
        password_hash = hash_password(body.password)
        with session(settings) as connection:
            try:
                connection.execute(
                    """INSERT INTO employees(
                           employee_id, name, email, department, designation, phone,
                           joining_date, salary, status, active, onboarding_complete
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'Pending', 0, 0)""",
                    (
                        body.employee_id,
                        body.name,
                        body.email,
                        body.department,
                        body.designation,
                        body.phone,
                        body.joining_date,
                    ),
                )
                connection.execute(
                    """INSERT INTO users(
                           username, password_hash, role, employee_id, active
                       ) VALUES (?, ?, 'employee', ?, 0)""",
                    (body.employee_id, password_hash, body.employee_id),
                )
                _initialize_onboarding_tasks(connection, body.employee_id)
                _record_audit(
                    connection,
                    body.employee_id,
                    "employee.registration_submitted",
                    body.employee_id,
                )
                _timeline(
                    connection, body.employee_id, body.employee_id,
                    "employee.registration", "Employee registration submitted.",
                    new={
                        "name": body.name,
                        "department": body.department,
                        "designation": body.designation,
                        "status": "Pending",
                    },
                )
                _notify_admins(
                    connection, "registration", "Employee registration submitted",
                    f"{body.name} submitted an employee registration for review.",
                    "employee", body.employee_id,
                )
                _notify_employee(
                    connection, body.employee_id, "onboarding", "normal",
                    "Onboarding tasks are ready",
                    "Your onboarding checklist is available while your account awaits approval.",
                    "onboarding", body.employee_id,
                )
            except sqlite3.IntegrityError as exc:
                raise HTTPException(
                    409, "Employee ID, email, or account already exists."
                ) from exc
            row = connection.execute(
                "SELECT * FROM employees WHERE employee_id = ?", (body.employee_id,)
            ).fetchone()
        request.app.state.employee_map.put(body.employee_id, dict(row))
        return {
            "employee_id": body.employee_id,
            "status": "Pending",
            "message": "Registration submitted. Your account is waiting for Admin approval.",
        }

    @router.get("/auth/me")
    def me(user: Annotated[dict[str, Any], Depends(current_user)]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "username": user["username"],
            "role": user["role"],
            "employee_id": user["employee_id"],
        }
        if user["role"] == "employee":
            with session(settings) as connection:
                row = connection.execute(
                    """SELECT employee_id, name, email, department, designation, phone,
                              joining_date, status, active, onboarding_complete
                       FROM employees WHERE employee_id = ?""",
                    (user["employee_id"],),
                ).fetchone()
                reporting = connection.execute(
                    "SELECT manager_id FROM employee_reporting WHERE employee_id = ?",
                    (user["employee_id"],),
                ).fetchone()
                is_manager = connection.execute(
                    "SELECT 1 FROM employee_reporting WHERE manager_id = ? LIMIT 1",
                    (user["employee_id"],),
                ).fetchone()
            if row is None:
                raise HTTPException(404, "Employee record not found.")
            result["employee"] = dict(row)
            result["manager_id"] = reporting["manager_id"] if reporting else None
            result["is_manager"] = bool(is_manager)
        return result

    @router.get("/employees")
    def list_employees(
        request: Request,
        user: Annotated[dict[str, Any], Depends(require_roles("admin", "employee"))],
        search: str = Query(default="", max_length=120),
        department: str = Query(default="", max_length=80),
        status: EmployeeStatus | None = None,
        active: bool | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        query = """SELECT employee_id, name, email, department, designation, phone,
                          joining_date, salary, status, active, onboarding_complete,
                          created_at, updated_at
                   FROM employees WHERE 1 = 1"""
        parameters: list[Any] = []
        if user["role"] == "employee":
            query += " AND employee_id = ?"
            parameters.append(user["employee_id"])
        if search.strip():
            like = f"%{search.strip()}%"
            query += " AND (employee_id LIKE ? OR name LIKE ? OR email LIKE ?)"
            parameters.extend([like, like, like])
        if department.strip():
            query += " AND department = ? COLLATE NOCASE"
            parameters.append(department.strip())
        if status:
            query += " AND status = ?"
            parameters.append(status)
        if active is not None:
            query += " AND active = ?"
            parameters.append(int(active))
        count_query = query.replace("SELECT employee_id", "SELECT COUNT(*)", 1)
        with session(settings) as connection:
            total = connection.execute(count_query, parameters).fetchone()[0]
            rows = connection.execute(
                query + " ORDER BY employee_id LIMIT ? OFFSET ?",
                [*parameters, limit, offset],
            ).fetchall()
        return {
            "items": [_public_employee(row, user["role"]) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @router.get("/admin/employee-registrations")
    def pending_employee_registrations(
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        with session(settings) as connection:
            total = connection.execute(
                """SELECT COUNT(*)
                   FROM employees e JOIN users u ON u.employee_id = e.employee_id
                   WHERE e.status = 'Pending' AND u.role = 'employee'"""
            ).fetchone()[0]
            rows = connection.execute(
                """SELECT e.employee_id, e.name, e.email, e.phone, e.department,
                          e.designation, e.joining_date, e.status, e.created_at
                   FROM employees e JOIN users u ON u.employee_id = e.employee_id
                   WHERE e.status = 'Pending' AND u.role = 'employee'
                   ORDER BY e.created_at DESC, e.employee_id
                   LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @router.post("/employees", status_code=201)
    def add_employee(
        body: EmployeeInput,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
        request: Request,
    ) -> dict[str, Any]:
        if len(body.password.encode("utf-8")) > 72:
            raise HTTPException(422, "Password must be no more than 72 UTF-8 bytes.")
        values = body.model_dump()
        values["password_hash"] = hash_password(values.pop("password"))
        with session(settings) as connection:
            try:
                connection.execute(
                    """INSERT INTO employees(
                       employee_id, name, email, department, designation, phone,
                       joining_date, salary, status, active, onboarding_complete
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending', 0, 0)""",
                    (
                        body.employee_id, body.name, body.email, body.department,
                        body.designation, body.phone, body.joining_date, body.salary,
                    ),
                )
                connection.execute(
                    """INSERT INTO users(
                           username, password_hash, role, employee_id, active
                       ) VALUES (?, ?, 'employee', ?, 0)""",
                    (body.employee_id, values["password_hash"], body.employee_id),
                )
                _initialize_onboarding_tasks(connection, body.employee_id)
                _record_audit(connection, user["username"], "employee.created", body.employee_id)
                _timeline(
                    connection, body.employee_id, user["username"],
                    "employee.created", "Employee record created by HR.",
                    new={
                        "name": body.name,
                        "department": body.department,
                        "designation": body.designation,
                        "salary": body.salary,
                        "status": "Pending",
                    },
                )
                _notify_admins(
                    connection, "registration", "Employee record created",
                    f"{body.name} was added and is awaiting approval.",
                    "employee", body.employee_id,
                )
                _notify_employee(
                    connection, body.employee_id, "onboarding", "normal",
                    "Onboarding tasks are ready",
                    "Your onboarding checklist is available after your account is approved.",
                    "onboarding", body.employee_id,
                )
            except sqlite3.IntegrityError as exc:
                message = "Employee ID, email, or employee login already exists."
                raise HTTPException(409, message) from exc
            row = connection.execute(
                "SELECT * FROM employees WHERE employee_id = ?", (body.employee_id,)
            ).fetchone()
        item = _employee_map_item(row)
        request.app.state.employee_map.put(body.employee_id, item)
        return {"employee": item}

    @router.get("/employees/{employee_id}")
    def get_employee(
        employee_id: str,
        request: Request,
        user: Annotated[dict[str, Any], Depends(require_roles("admin", "employee"))],
    ) -> dict[str, Any]:
        if user["role"] == "employee" and user["employee_id"] != employee_id:
            raise HTTPException(403, "Employees can view only their own record.")
        employee_map: EmployeeHashMap[str, dict[str, Any]] = request.app.state.employee_map
        item = employee_map.get(employee_id)
        if item is None:
            raise HTTPException(404, "Employee not found.")
        return _public_employee(item, user["role"])

    @router.put("/employees/{employee_id}")
    def update_employee(
        employee_id: str,
        body: EmployeeUpdate,
        request: Request,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> dict[str, Any]:
        with session(settings) as connection:
            previous = connection.execute(
                """SELECT name, email, department, designation, phone,
                          joining_date, salary FROM employees WHERE employee_id = ?""",
                (employee_id,),
            ).fetchone()
            if previous is None:
                raise HTTPException(404, "Employee not found.")
            try:
                cursor = connection.execute(
                    """UPDATE employees SET name = ?, email = ?, department = ?, designation = ?,
                       phone = ?, joining_date = ?, salary = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE employee_id = ?""",
                    (
                        body.name, body.email, body.department, body.designation,
                        body.phone, body.joining_date, body.salary, employee_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise HTTPException(409, "That work email is already assigned.") from exc
            if cursor.rowcount == 0:
                raise HTTPException(404, "Employee not found.")
            _record_audit(connection, user["username"], "employee.updated", employee_id)
            updated_values = body.model_dump()
            previous_values = dict(previous)
            changed_before = {
                key: value for key, value in previous_values.items()
                if updated_values.get(key) != value
            }
            if changed_before:
                _timeline(
                    connection, employee_id, user["username"], "employee.updated",
                    "Employee profile updated by HR.", changed_before,
                    {key: updated_values[key] for key in changed_before},
                )
            row = connection.execute(
                "SELECT * FROM employees WHERE employee_id = ?", (employee_id,)
            ).fetchone()
        item = _employee_map_item(row)
        request.app.state.employee_map.put(employee_id, item)
        return {"employee": item}

    @router.delete("/employees/{employee_id}", status_code=204)
    def delete_employee(
        employee_id: str,
        request: Request,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> Response:
        with session(settings) as connection:
            previous = connection.execute(
                "SELECT name, department, designation, status FROM employees WHERE employee_id = ?",
                (employee_id,),
            ).fetchone()
            if previous is None:
                raise HTTPException(404, "Employee not found.")
            _timeline(
                connection, employee_id, user["username"], "employee.deleted",
                "Employee record deleted.", dict(previous),
            )
            cursor = connection.execute(
                "DELETE FROM employees WHERE employee_id = ?", (employee_id,)
            )
            _record_audit(connection, user["username"], "employee.deleted", employee_id)
        request.app.state.employee_map.delete(employee_id)
        return Response(status_code=204)

    @router.patch("/employees/{employee_id}/status")
    def update_employee_status(
        employee_id: str,
        body: ReviewInput,
        request: Request,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> dict[str, str]:
        with session(settings) as connection:
            old = connection.execute(
                "SELECT status FROM employees WHERE employee_id = ?",
                (employee_id,),
            ).fetchone()
            if old is None:
                raise HTTPException(404, "Employee not found.")
            cursor = connection.execute(
                """UPDATE employees SET status = ?, active = ?,
                   updated_at = CURRENT_TIMESTAMP WHERE employee_id = ?""",
                (body.status, int(body.status == "Approved"), employee_id),
            )
            if cursor.rowcount == 0:
                raise HTTPException(404, "Employee not found.")
            connection.execute(
                """UPDATE users SET active = ?, token_version = token_version + 1
                   WHERE employee_id = ?""",
                (int(body.status == "Approved"), employee_id),
            )
            _record_audit(
                connection, user["username"], f"employee.{body.status.lower()}",
                employee_id,
            )
            _timeline(
                connection, employee_id, user["username"], "employee.status",
                f"Employee status changed to {body.status}.",
                {"status": old["status"]}, {"status": body.status},
            )
            _notify_employee(
                connection, employee_id, "registration",
                "normal" if body.status == "Approved" else "high",
                "Employee registration decision",
                f"Your employee registration was {body.status.lower()}.",
                "employee", employee_id,
            )
            if body.status == "Approved":
                _notify_employee(
                    connection, employee_id, "onboarding", "normal",
                    "Complete your onboarding",
                    "Your account is approved. Complete the tasks in your onboarding checklist.",
                    "onboarding", employee_id,
                )
            row = connection.execute(
                "SELECT * FROM employees WHERE employee_id = ?", (employee_id,)
            ).fetchone()
        request.app.state.employee_map.put(employee_id, _employee_map_item(row))
        return {"message": f"Employee record {body.status.lower()}."}

    @router.patch("/employees/{employee_id}/active")
    def update_employee_active(
        employee_id: str,
        active: bool,
        request: Request,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> dict[str, str]:
        with session(settings) as connection:
            old = connection.execute(
                "SELECT active FROM employees WHERE employee_id = ?",
                (employee_id,),
            ).fetchone()
            if old is None:
                raise HTTPException(404, "Employee not found.")
            cursor = connection.execute(
                "UPDATE employees SET active = ?, updated_at = CURRENT_TIMESTAMP WHERE employee_id = ?",
                (int(active), employee_id),
            )
            if cursor.rowcount == 0:
                raise HTTPException(404, "Employee not found.")
            connection.execute(
                """UPDATE users SET active = ?, token_version = token_version + 1
                   WHERE employee_id = ?""",
                (int(active), employee_id),
            )
            _record_audit(
                connection, user["username"],
                "employee.activated" if active else "employee.deactivated",
                employee_id,
            )
            _timeline(
                connection, employee_id, user["username"], "employee.active",
                f"Employee account {'activated' if active else 'deactivated'}.",
                {"active": old["active"]}, {"active": int(active)},
            )
            row = connection.execute(
                "SELECT * FROM employees WHERE employee_id = ?", (employee_id,)
            ).fetchone()
        request.app.state.employee_map.put(employee_id, _employee_map_item(row))
        return {"message": f"Employee {'activated' if active else 'deactivated'}."}

    @router.get("/me/profile-requests")
    def my_profile_requests(
        user: Annotated[dict[str, Any], Depends(require_roles("employee"))],
    ) -> list[dict[str, Any]]:
        with session(settings) as connection:
            rows = connection.execute(
                """SELECT request_id, changes_json, status, created_at, reviewed_at
                   FROM profile_update_requests WHERE employee_id = ?
                   ORDER BY request_id DESC""",
                (user["employee_id"],),
            ).fetchall()
        return [
            {**dict(row), "changes": json.loads(row["changes_json"])}
            for row in rows
        ]

    @router.post("/me/profile-requests", status_code=201)
    def create_profile_request(
        body: ProfileRequestInput,
        user: Annotated[dict[str, Any], Depends(require_roles("employee"))],
    ) -> dict[str, Any]:
        changes = body.model_dump(exclude_unset=True, exclude_none=True)
        with session(settings) as connection:
            employee = connection.execute(
                "SELECT name, phone FROM employees WHERE employee_id = ?",
                (user["employee_id"],),
            ).fetchone()
            if employee is None:
                raise HTTPException(404, "Employee profile not found.")
            cursor = connection.execute(
                """INSERT INTO profile_update_requests(employee_id, changes_json)
                   VALUES (?, ?)""",
                (user["employee_id"], json.dumps(changes, ensure_ascii=True)),
            )
            _record_audit(
                connection, user["username"], "profile_request.submitted",
                user["employee_id"],
            )
            request_id = cursor.lastrowid
            _notify_admins(
                connection, "profile", "Profile update submitted",
                f"{employee['name']} submitted a profile update request.",
                "profile_request", str(request_id),
            )
        return {"request_id": request_id, "status": "Pending", "changes": changes}

    @router.get("/admin/profile-requests")
    def list_profile_requests(
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
        status: Literal["Pending", "Approved", "Rejected"] | None = None,
    ) -> list[dict[str, Any]]:
        query = """SELECT r.request_id, r.employee_id, e.name, r.changes_json,
                          r.status, r.created_at, r.reviewed_by, r.reviewed_at
                   FROM profile_update_requests r
                   JOIN employees e ON e.employee_id = r.employee_id"""
        parameters: list[Any] = []
        if status:
            query += " WHERE r.status = ?"
            parameters.append(status)
        query += " ORDER BY r.request_id DESC"
        with session(settings) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            {**dict(row), "changes": json.loads(row["changes_json"])}
            for row in rows
        ]

    @router.patch("/admin/profile-requests/{request_id}")
    def review_profile_request(
        request_id: int,
        body: ReviewInput,
        request: Request,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> dict[str, str]:
        with session(settings) as connection:
            row = connection.execute(
                """SELECT employee_id, changes_json, status FROM profile_update_requests
                   WHERE request_id = ?""",
                (request_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(404, "Profile update request not found.")
            if row["status"] != "Pending":
                raise HTTPException(409, "This profile update request has already been reviewed.")
            connection.execute(
                """UPDATE profile_update_requests SET status = ?, reviewed_by = ?,
                   reviewed_at = CURRENT_TIMESTAMP WHERE request_id = ?""",
                (body.status, user["username"], request_id),
            )
            if body.status == "Approved":
                changes = json.loads(row["changes_json"])
                previous = connection.execute(
                    "SELECT name, phone FROM employees WHERE employee_id = ?",
                    (row["employee_id"],),
                ).fetchone()
                updates = ", ".join(f"{field} = ?" for field in changes)
                connection.execute(
                    f"""UPDATE employees SET {updates}, updated_at = CURRENT_TIMESTAMP
                        WHERE employee_id = ?""",
                    [*changes.values(), row["employee_id"]],
                )
                _timeline(
                    connection, row["employee_id"], user["username"],
                    "profile.update_approved", "Employee profile changes approved.",
                    dict(previous), changes,
                )
            else:
                changes = json.loads(row["changes_json"])
                _timeline(
                    connection, row["employee_id"], user["username"],
                    "profile.update_rejected", "Employee profile changes rejected.",
                    new={"requested_changes": changes},
                )
            _record_audit(
                connection, user["username"],
                f"profile_request.{body.status.lower()}",
                row["employee_id"], f"request_id={request_id}",
            )
            _notify_employee(
                connection, row["employee_id"], "profile", "normal",
                "Profile update decision",
                f"Your profile update request was {body.status.lower()}.",
                "profile_request", str(request_id),
            )
            employee = connection.execute(
                "SELECT * FROM employees WHERE employee_id = ?", (row["employee_id"],)
            ).fetchone()
        request.app.state.employee_map.put(
            row["employee_id"], _employee_map_item(employee)
        )
        return {"message": f"Profile update request {body.status.lower()}."}

    @router.patch("/me/onboarding")
    def complete_onboarding(
        request: Request,
        user: Annotated[dict[str, Any], Depends(require_roles("employee"))],
    ) -> dict[str, str]:
        with session(settings) as connection:
            cursor = connection.execute(
                """UPDATE employees SET onboarding_complete = 1,
                   updated_at = CURRENT_TIMESTAMP WHERE employee_id = ?""",
                (user["employee_id"],),
            )
            if cursor.rowcount == 0:
                raise HTTPException(404, "Employee record not found.")
            _record_audit(
                connection, user["username"], "employee.onboarding_completed",
                user["employee_id"],
            )
            row = connection.execute(
                "SELECT * FROM employees WHERE employee_id = ?",
                (user["employee_id"],),
            ).fetchone()
        request.app.state.employee_map.put(
            user["employee_id"], _employee_map_item(row)
        )
        return {"message": "Onboarding marked complete."}

    @router.post("/auth/change-password")
    def change_password(
        body: PasswordInput,
        user: Annotated[dict[str, Any], Depends(current_user)],
    ) -> dict[str, str]:
        with session(settings) as connection:
            row = connection.execute(
                "SELECT password_hash FROM users WHERE username = ?",
                (user["username"],),
            ).fetchone()
            if row is None or not verify_password(body.current_password, row["password_hash"]):
                raise HTTPException(400, "Current password is incorrect.")
            connection.execute(
                """UPDATE users SET password_hash = ?, token_version = token_version + 1
                   WHERE username = ?""",
                (hash_password(body.new_password), user["username"]),
            )
            _record_audit(
                connection, user["username"], "account.password_changed",
                user["username"],
            )
            version = connection.execute(
                "SELECT token_version FROM users WHERE username = ?",
                (user["username"],),
            ).fetchone()["token_version"]
        return {
            "access_token": create_access_token(
                user["username"], settings, version
            ),
            "token_type": "bearer",
        }

    @router.get("/departments")
    def list_departments(
        user: Annotated[dict[str, Any], Depends(current_user)],
    ) -> list[str]:
        with session(settings) as connection:
            return [
                row["name"] for row in connection.execute(
                    "SELECT name FROM departments ORDER BY name COLLATE NOCASE"
                )
            ]

    @router.post("/departments", status_code=201)
    def add_department(
        body: DepartmentInput,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> dict[str, str]:
        with session(settings) as connection:
            try:
                connection.execute("INSERT INTO departments(name) VALUES (?)", (body.name,))
            except sqlite3.IntegrityError as exc:
                raise HTTPException(409, "Department already exists.") from exc
            _record_audit(
                connection, user["username"], "department.created", body.name
            )
        return {"name": body.name}

    @router.delete("/departments/{name}", status_code=204)
    def delete_department(
        name: str,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> Response:
        with session(settings) as connection:
            used = connection.execute(
                "SELECT 1 FROM employees WHERE department = ? COLLATE NOCASE LIMIT 1",
                (name,),
            ).fetchone()
            if used:
                raise HTTPException(409, "Department is assigned to employee records.")
            cursor = connection.execute(
                "DELETE FROM departments WHERE name = ? COLLATE NOCASE", (name,)
            )
            if cursor.rowcount == 0:
                raise HTTPException(404, "Department not found.")
            _record_audit(connection, user["username"], "department.deleted", name)
        return Response(status_code=204)

    @router.patch("/departments/{name}")
    def rename_department(
        name: str,
        body: DepartmentInput,
        request: Request,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> dict[str, str]:
        with session(settings) as connection:
            try:
                cursor = connection.execute(
                    "UPDATE departments SET name = ? WHERE name = ? COLLATE NOCASE",
                    (body.name, name),
                )
            except sqlite3.IntegrityError as exc:
                raise HTTPException(409, "Department already exists.") from exc
            if cursor.rowcount == 0:
                raise HTTPException(404, "Department not found.")
            connection.execute(
                "UPDATE employees SET department = ?, updated_at = CURRENT_TIMESTAMP WHERE department = ? COLLATE NOCASE",
                (body.name, name),
            )
            _record_audit(
                connection, user["username"], "department.renamed",
                name, f"new_name={body.name}",
            )
            updated = connection.execute(
                "SELECT * FROM employees WHERE department = ? COLLATE NOCASE",
                (body.name,),
            ).fetchall()
        for row in updated:
            request.app.state.employee_map.put(
                row["employee_id"], _employee_map_item(row)
            )
        return {"name": body.name}

    @router.get("/designations")
    def list_designations(
        user: Annotated[dict[str, Any], Depends(current_user)],
    ) -> list[str]:
        with session(settings) as connection:
            return [
                row["name"] for row in connection.execute(
                    "SELECT name FROM designations ORDER BY name COLLATE NOCASE"
                )
            ]

    @router.post("/designations", status_code=201)
    def add_designation(
        body: DepartmentInput,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> dict[str, str]:
        with session(settings) as connection:
            try:
                connection.execute("INSERT INTO designations(name) VALUES (?)", (body.name,))
            except sqlite3.IntegrityError as exc:
                raise HTTPException(409, "Designation already exists.") from exc
            _record_audit(
                connection, user["username"], "designation.created", body.name
            )
        return {"name": body.name}

    @router.delete("/designations/{name}", status_code=204)
    def delete_designation(
        name: str,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> Response:
        with session(settings) as connection:
            used = connection.execute(
                "SELECT 1 FROM employees WHERE designation = ? COLLATE NOCASE LIMIT 1",
                (name,),
            ).fetchone()
            if used:
                raise HTTPException(409, "Designation is assigned to employee records.")
            cursor = connection.execute(
                "DELETE FROM designations WHERE name = ? COLLATE NOCASE", (name,)
            )
            if cursor.rowcount == 0:
                raise HTTPException(404, "Designation not found.")
            _record_audit(connection, user["username"], "designation.deleted", name)
        return Response(status_code=204)

    @router.patch("/designations/{name}")
    def rename_designation(
        name: str,
        body: DepartmentInput,
        request: Request,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> dict[str, str]:
        with session(settings) as connection:
            try:
                cursor = connection.execute(
                    "UPDATE designations SET name = ? WHERE name = ? COLLATE NOCASE",
                    (body.name, name),
                )
            except sqlite3.IntegrityError as exc:
                raise HTTPException(409, "Designation already exists.") from exc
            if cursor.rowcount == 0:
                raise HTTPException(404, "Designation not found.")
            connection.execute(
                "UPDATE employees SET designation = ?, updated_at = CURRENT_TIMESTAMP WHERE designation = ? COLLATE NOCASE",
                (body.name, name),
            )
            _record_audit(
                connection, user["username"], "designation.renamed",
                name, f"new_name={body.name}",
            )
            updated = connection.execute(
                "SELECT * FROM employees WHERE designation = ? COLLATE NOCASE",
                (body.name,),
            ).fetchall()
        for row in updated:
            request.app.state.employee_map.put(
                row["employee_id"], _employee_map_item(row)
            )
        return {"name": body.name}

    @router.get("/stats")
    def workforce_stats(
        user: Annotated[dict[str, Any], Depends(require_roles("admin", "employee"))],
    ) -> dict[str, Any]:
        with session(settings) as connection:
            if user["role"] == "employee":
                row = connection.execute(
                    """SELECT employee_id, name, department, designation, status,
                              active, onboarding_complete
                       FROM employees WHERE employee_id = ?""",
                    (user["employee_id"],),
                ).fetchone()
                if row is None:
                    raise HTTPException(404, "Employee record not found.")
                return {"employee": dict(row)}
            total = connection.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
            approved = connection.execute(
                "SELECT COUNT(*) FROM employees WHERE status = 'Approved' AND active = 1"
            ).fetchone()[0]
            pending = connection.execute(
                "SELECT COUNT(*) FROM employees WHERE status = 'Pending'"
            ).fetchone()[0]
            inactive = connection.execute(
                "SELECT COUNT(*) FROM employees WHERE active = 0"
            ).fetchone()[0]
            rows = connection.execute(
                """SELECT department, COUNT(*) AS count FROM employees
                   WHERE active = 1 GROUP BY department ORDER BY department"""
            ).fetchall()
        return {
            "total": total,
            "approved": approved,
            "pending": pending,
            "inactive": inactive,
            "departments": {row["department"]: row["count"] for row in rows},
        }

    @router.get("/reports/workforce")
    def workforce_report(
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> dict[str, Any]:
        with session(settings) as connection:
            departments = connection.execute(
                """SELECT department, COUNT(*) AS total,
                          SUM(CASE WHEN status = 'Approved' AND active = 1 THEN 1 ELSE 0 END) AS active_approved,
                          SUM(CASE WHEN status = 'Pending' THEN 1 ELSE 0 END) AS pending
                   FROM employees GROUP BY department ORDER BY department"""
            ).fetchall()
            statuses = connection.execute(
                "SELECT status, COUNT(*) AS count FROM employees GROUP BY status ORDER BY status"
            ).fetchall()
        return {
            "departments": [dict(row) for row in departments],
            "statuses": {row["status"]: row["count"] for row in statuses},
        }

    @router.get("/reports/employees.csv")
    def export_employees_csv(
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> StreamingResponse:
        with session(settings) as connection:
            rows = connection.execute(
                """SELECT employee_id, name, email, department, designation, phone,
                          joining_date, salary, status, active, onboarding_complete
                   FROM employees ORDER BY employee_id"""
            ).fetchall()
        output = io.StringIO(newline="")
        columns = [
            "employee_id", "name", "email", "department", "designation",
            "phone", "joining_date", "salary", "status", "active",
            "onboarding_complete",
        ]
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: (
                        "'" + value
                        if isinstance(value, str)
                        and value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@"))
                        else value
                    )
                    for key, value in dict(row).items()
                }
            )
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="employee_records.csv"'},
        )

    @router.get("/admin/audit-logs")
    def audit_logs(
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> list[dict[str, Any]]:
        with session(settings) as connection:
            rows = connection.execute(
                """SELECT audit_id, actor_username, action, target_id, details, created_at
                   FROM audit_logs ORDER BY audit_id DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    @router.get("/hash-table")
    def hash_table(
        request: Request,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> list[dict[str, Any]]:
        employee_map: EmployeeHashMap[str, dict[str, Any]] = request.app.state.employee_map
        return [
            {
                "index": index,
                "employee_ids": [key for key, _ in bucket],
                "count": len(bucket),
            }
            for index, bucket in enumerate(employee_map.bucket_snapshot())
        ]

    @router.get("/hash-table/stats")
    def hash_table_stats(
        request: Request,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> dict[str, int | float]:
        employee_map: EmployeeHashMap[str, dict[str, Any]] = request.app.state.employee_map
        return employee_map.stats()

    @router.get("/benchmark")
    def benchmark(
        request: Request,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
        repetitions: int = Query(default=100, ge=1, le=10_000),
    ) -> dict[str, Any]:
        with session(settings) as connection:
            records = [
                dict(row) for row in connection.execute(
                    "SELECT * FROM employees ORDER BY employee_id"
                )
            ]
        if not records:
            return {
                "record_count": 0,
                "repetitions": repetitions,
                "results": [],
                "note": "No employee records are available to measure.",
            }
        keys = [record["employee_id"] for record in records]
        targets = [keys[index % len(keys)] for index in range(repetitions)]
        chain_map: EmployeeHashMap[str, dict[str, Any]] = request.app.state.employee_map
        chain_stats = chain_map.stats()
        probing_map: LinearProbingMap[str, dict[str, Any]] = LinearProbingMap()
        for record in records:
            probing_map.put(record["employee_id"], record)

        start = time.perf_counter_ns()
        for key in targets:
            chain_map.get(key)
        chain_ns = time.perf_counter_ns() - start

        start = time.perf_counter_ns()
        for key in targets:
            probing_map.get(key)
        probing_ns = time.perf_counter_ns() - start

        start = time.perf_counter_ns()
        for key in targets:
            next((record for record in records if record["employee_id"] == key), None)
        scan_ns = time.perf_counter_ns() - start

        to_ms = lambda elapsed: round(elapsed / 1_000_000, 4)
        return {
            "record_count": len(records),
            "repetitions": repetitions,
            "results": [
                {
                    "method": "Separate Chaining",
                    "time_ms": to_ms(chain_ns),
                    "collisions": chain_stats["collisions"],
                    "load_factor": chain_stats["load_factor"],
                },
                {
                    "method": "Linear Probing",
                    "time_ms": to_ms(probing_ns),
                    "collisions": probing_map.collisions,
                    "load_factor": round(probing_map.size / probing_map.capacity, 3),
                },
                {
                    "method": "Linear Search",
                    "time_ms": to_ms(scan_ns),
                    "collisions": 0,
                    "load_factor": None,
                },
            ],
            "note": "Measured in this process against current SQLite records; timings vary by machine and dataset.",
        }

    return router
