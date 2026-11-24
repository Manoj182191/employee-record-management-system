import csv
import hashlib
import io
import json
import math
import re
import secrets
import sqlite3
import time
import uuid
from zipfile import BadZipFile
from calendar import monthrange
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import qrcode
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import Response, StreamingResponse
from openpyxl import Workbook, load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from app.config import BACKEND_ROOT, Settings
from app.database.db import session
from app.dependencies import current_user_dependency, role_dependency
from app.hashmap import EmployeeHashMap, LinearProbingMap
from app.security import hash_password


PRIVATE_STORAGE = BACKEND_ROOT / "private_storage"
DOCUMENT_CATEGORIES = {
    "resume", "identity", "education", "offer_letter", "photo", "other",
}
ALLOWED_FILES = {
    ".pdf": ("application/pdf", b"%PDF-"),
    ".jpg": ("image/jpeg", b"\xff\xd8\xff"),
    ".jpeg": ("image/jpeg", b"\xff\xd8\xff"),
    ".png": ("image/png", b"\x89PNG\r\n\x1a\n"),
}
IMPORT_COLUMNS = {
    "employee_id", "name", "email", "department", "designation",
    "phone", "joining_date", "salary", "password",
}
TaskKey = Literal[
    "personal_info", "contact_info", "documents", "policy_acknowledgement",
    "emergency_contact", "role_confirmation",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NotificationReadInput(StrictModel):
    read: bool = True


class OnboardingSaveInput(StrictModel):
    response: dict[str, Any]
    submit: bool = False


class OnboardingReviewInput(StrictModel):
    status: Literal["approved", "rejected"]
    note: str = Field(default="", max_length=500)


class DocumentReviewInput(StrictModel):
    status: Literal["Verified", "Rejected"]
    note: str = Field(default="", max_length=500)


class LeavePolicyInput(StrictModel):
    annual_allowance: float = Field(ge=0, le=366)
    count_weekends: bool = False
    active: bool = True


class LeaveRequestInput(StrictModel):
    leave_type: str = Field(min_length=1, max_length=60)
    start_date: date
    end_date: date
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("leave_type", "reason")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field must not be blank.")
        return value

    @model_validator(mode="after")
    def validate_dates(self) -> "LeaveRequestInput":
        if self.end_date < self.start_date:
            raise ValueError("End date must be on or after start date.")
        if (self.end_date - self.start_date).days > 365:
            raise ValueError("A leave request cannot exceed one year.")
        return self


class LeaveDecisionInput(StrictModel):
    status: Literal["Approved", "Rejected"]
    rejection_reason: str = Field(default="", max_length=1000)

    @field_validator("rejection_reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        return value.strip()


class AttendanceCorrectionInput(StrictModel):
    requested_check_out: datetime
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        return value.strip()


class CorrectionReviewInput(StrictModel):
    status: Literal["Approved", "Rejected"]
    note: str = Field(default="", max_length=500)


class PerformancePeriodInput(StrictModel):
    name: str = Field(min_length=2, max_length=100)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_dates(self) -> "PerformancePeriodInput":
        if self.end_date < self.start_date:
            raise ValueError("Period end date must be on or after start date.")
        return self


class PerformanceGoalInput(StrictModel):
    employee_id: str = Field(min_length=2, max_length=30)
    period_id: int = Field(gt=0)
    title: str = Field(min_length=2, max_length=160)
    measurable_target: str = Field(min_length=2, max_length=1000)
    due_date: date


class GoalProgressInput(StrictModel):
    status: Literal["not_started", "in_progress", "completed"]
    progress_note: str = Field(default="", max_length=2000)


class SelfAssessmentInput(StrictModel):
    self_assessment: str = Field(min_length=5, max_length=5000)
    self_rating: int = Field(ge=1, le=5)


class ManagerFeedbackInput(StrictModel):
    manager_feedback: str = Field(min_length=5, max_length=5000)
    manager_rating: int = Field(ge=1, le=5)


class AdminReviewInput(StrictModel):
    admin_note: str = Field(min_length=2, max_length=2000)


class ManagerAssignmentInput(StrictModel):
    manager_id: str | None = Field(default=None, min_length=2, max_length=30)


class DepartmentHeadInput(StrictModel):
    employee_id: str | None = Field(default=None, min_length=2, max_length=30)


class LabKeyInput(StrictModel):
    employee_id: str = Field(min_length=1, max_length=40)


class HashMapLabInput(LabKeyInput):
    action: Literal["insert", "search", "delete"]


class ImportDecisionInput(StrictModel):
    confirm: bool


def _notify(
    connection: sqlite3.Connection,
    recipient: str,
    category: str,
    priority: str,
    title: str,
    message: str,
    related_type: str | None = None,
    related_id: str | None = None,
) -> None:
    connection.execute(
        """INSERT INTO notifications(
               recipient_username, category, priority, title, message,
               related_type, related_id
           ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (recipient, category, priority, title, message, related_type, related_id),
    )


def _notify_admins(
    connection: sqlite3.Connection,
    category: str,
    title: str,
    message: str,
    related_type: str,
    related_id: str,
) -> None:
    for row in connection.execute(
        "SELECT username FROM users WHERE role = 'admin' AND active = 1"
    ):
        _notify(
            connection, row["username"], category, "normal",
            title, message, related_type, related_id,
        )


def _timeline(
    connection: sqlite3.Connection,
    employee_id: str,
    actor: str,
    event_type: str,
    summary: str,
    previous: dict[str, Any] | None = None,
    new: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        """INSERT INTO employee_timeline(
               employee_id, actor_username, event_type, summary,
               previous_values_json, new_values_json
           ) VALUES (?, ?, ?, ?, ?, ?)""",
        (
            employee_id, actor, event_type, summary,
            json.dumps(previous or {}, ensure_ascii=True),
            json.dumps(new or {}, ensure_ascii=True),
        ),
    )


def _audit(
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


def _employee_for_user(user: dict[str, Any]) -> str:
    employee_id = user.get("employee_id")
    if not employee_id:
        raise HTTPException(403, "This account is not linked to an employee record.")
    return str(employee_id)


def _manager_or_admin(
    connection: sqlite3.Connection,
    user: dict[str, Any],
    employee_id: str,
) -> bool:
    if user["role"] == "admin":
        return True
    return bool(
        user.get("employee_id")
        and connection.execute(
            """SELECT 1 FROM employee_reporting
               WHERE employee_id = ? AND manager_id = ?""",
            (employee_id, user["employee_id"]),
        ).fetchone()
    )


def _require_manager_or_admin(
    connection: sqlite3.Connection,
    user: dict[str, Any],
    employee_id: str,
) -> None:
    owns_record = user.get("employee_id") == employee_id
    if not (owns_record or _manager_or_admin(connection, user, employee_id)):
        raise HTTPException(403, "This action is limited to HR or the assigned manager.")


def _notify_employee(
    connection: sqlite3.Connection,
    employee_id: str,
    category: str,
    priority: str,
    title: str,
    message: str,
    related_type: str,
    related_id: str,
) -> None:
    row = connection.execute(
        "SELECT username FROM users WHERE employee_id = ? AND role = 'employee'",
        (employee_id,),
    ).fetchone()
    if row:
        _notify(
            connection, row["username"], category, priority,
            title, message, related_type, related_id,
        )


def _task_payload(task_key: str, response: dict[str, Any]) -> None:
    allowed = {
        "personal_info": {"confirmed"},
        "contact_info": {"confirmed"},
        "documents": {"confirmed"},
        "policy_acknowledgement": {"acknowledged", "policy_version"},
        "emergency_contact": {"name", "phone", "relationship"},
        "role_confirmation": {"confirmed"},
    }
    if task_key not in allowed or set(response) - allowed[task_key]:
        raise HTTPException(422, "Invalid onboarding task response.")
    if task_key in {"personal_info", "contact_info", "documents", "role_confirmation"}:
        if response.get("confirmed") is not True:
            raise HTTPException(422, "Confirm the requested information to submit this task.")
    elif task_key == "policy_acknowledgement":
        if response.get("acknowledged") is not True or not str(response.get("policy_version", "")).strip():
            raise HTTPException(422, "Policy acknowledgement and version are required.")
    elif task_key == "emergency_contact":
        if any(not str(response.get(field, "")).strip() for field in ("name", "phone", "relationship")):
            raise HTTPException(422, "Emergency contact name, phone and relationship are required.")
        if not any(char.isdigit() for char in str(response["phone"])):
            raise HTTPException(422, "Enter a valid emergency contact phone number.")


def _workdays(start: date, end: date, count_weekends: bool) -> float:
    if count_weekends:
        return float((end - start).days + 1)
    days = 0
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days += 1
        cursor += timedelta(days=1)
    return float(days)


def _iso_utc(value: datetime, organization_timezone: str = "UTC") -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo(organization_timezone))
    return value.astimezone(timezone.utc).isoformat()


def _attendance_worked_hours(row: sqlite3.Row | dict[str, Any]) -> float | None:
    if not row["check_out_utc"]:
        return None
    start = datetime.fromisoformat(row["check_in_utc"])
    end = datetime.fromisoformat(row["check_out_utc"])
    return round(max(0.0, (end - start).total_seconds() / 3600), 2)


def _file_response(path: Path, filename: str, content_type: str) -> StreamingResponse:
    if not path.is_file():
        raise HTTPException(404, "Document file is unavailable.")

    def chunks():
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 64):
                yield chunk

    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name)[:160]
    return StreamingResponse(
        chunks(),
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


def _employee_public_name(connection: sqlite3.Connection, employee_id: str) -> str:
    row = connection.execute(
        "SELECT name FROM employees WHERE employee_id = ?", (employee_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Employee not found.")
    return row["name"]


def _initialize_onboarding_tasks(
    connection: sqlite3.Connection, employee_id: str
) -> None:
    connection.execute(
        """INSERT OR IGNORE INTO employee_onboarding_tasks(employee_id, task_key)
           SELECT ?, task_key FROM onboarding_task_templates""",
        (employee_id,),
    )


def create_advanced_api(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api")
    current_user = current_user_dependency(settings)
    require_roles = role_dependency(current_user)

    @router.get("/notifications")
    def list_notifications(
        user: Annotated[dict[str, Any], Depends(current_user)],
        unread_only: bool = False,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        query = """SELECT notification_id, category, priority, title, message,
                          related_type, related_id, is_read, created_at, read_at
                   FROM notifications WHERE recipient_username = ?"""
        parameters: list[Any] = [user["username"]]
        if unread_only:
            query += " AND is_read = 0"
        with session(settings) as connection:
            unread = connection.execute(
                """SELECT COUNT(*) FROM notifications
                   WHERE recipient_username = ? AND is_read = 0""",
                (user["username"],),
            ).fetchone()[0]
            total = connection.execute(
                "SELECT COUNT(*) FROM notifications WHERE recipient_username = ?"
                + (" AND is_read = 0" if unread_only else ""),
                (user["username"],),
            ).fetchone()[0]
            rows = connection.execute(
                query + " ORDER BY notification_id DESC LIMIT ? OFFSET ?",
                [*parameters, limit, offset],
            ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "unread_count": unread,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @router.patch("/notifications/read-all")
    def mark_all_notifications_read(
        user: Annotated[dict[str, Any], Depends(current_user)],
    ) -> dict[str, int]:
        with session(settings) as connection:
            cursor = connection.execute(
                """UPDATE notifications SET is_read = 1, read_at = CURRENT_TIMESTAMP
                   WHERE recipient_username = ? AND is_read = 0""",
                (user["username"],),
            )
        return {"marked_read": cursor.rowcount}

    @router.patch("/notifications/{notification_id}")
    def mark_notification_read(
        notification_id: int,
        body: NotificationReadInput,
        user: Annotated[dict[str, Any], Depends(current_user)],
    ) -> dict[str, str]:
        with session(settings) as connection:
            cursor = connection.execute(
                """UPDATE notifications SET is_read = ?, read_at = ?
                   WHERE notification_id = ? AND recipient_username = ?""",
                (
                    int(body.read),
                    "CURRENT_TIMESTAMP" if body.read else None,
                    notification_id,
                    user["username"],
                ),
            )
            if cursor.rowcount == 0:
                exists = connection.execute(
                    """SELECT 1 FROM notifications WHERE notification_id = ?
                       AND recipient_username = ?""",
                    (notification_id, user["username"]),
                ).fetchone()
                if exists is None:
                    raise HTTPException(404, "Notification not found.")
        if body.read:
            with session(settings) as connection:
                connection.execute(
                    """UPDATE notifications SET read_at = CURRENT_TIMESTAMP
                       WHERE notification_id = ? AND recipient_username = ?""",
                    (notification_id, user["username"]),
                )
        return {"message": "Notification updated."}

    @router.get("/onboarding/me")
    def my_onboarding(
        user: Annotated[dict[str, Any], Depends(require_roles("employee"))],
    ) -> dict[str, Any]:
        employee_id = _employee_for_user(user)
        with session(settings) as connection:
            rows = connection.execute(
                """SELECT t.task_key, t.title, t.description, t.required,
                          t.display_order, et.status, et.response_json,
                          et.submitted_at, et.reviewed_at, et.review_note
                   FROM onboarding_task_templates t
                   JOIN employee_onboarding_tasks et ON et.task_key = t.task_key
                   WHERE et.employee_id = ? ORDER BY t.display_order""",
                (employee_id,),
            ).fetchall()
        tasks = [
            {
                **{key: row[key] for key in (
                    "task_key", "title", "description", "required", "display_order",
                    "status", "submitted_at", "reviewed_at", "review_note",
                )},
                "response": json.loads(row["response_json"]),
            }
            for row in rows
        ]
        required = [task for task in tasks if task["required"]]
        completed = sum(task["status"] == "approved" for task in required)
        return {
            "employee_id": employee_id,
            "tasks": tasks,
            "required_tasks": len(required),
            "completed_tasks": completed,
            "completion_percentage": round(100 * completed / len(required)) if required else 100,
        }

    @router.put("/onboarding/tasks/{task_key}")
    def save_onboarding_task(
        task_key: TaskKey,
        body: OnboardingSaveInput,
        user: Annotated[dict[str, Any], Depends(require_roles("employee"))],
    ) -> dict[str, str]:
        employee_id = _employee_for_user(user)
        allowed = {
            "personal_info": {"confirmed"},
            "contact_info": {"confirmed"},
            "documents": {"confirmed"},
            "policy_acknowledgement": {"acknowledged", "policy_version"},
            "emergency_contact": {"name", "phone", "relationship"},
            "role_confirmation": {"confirmed"},
        }
        if set(body.response) - allowed[task_key]:
            raise HTTPException(422, "Unexpected fields for this onboarding task.")
        if body.submit:
            _task_payload(task_key, body.response)
        if task_key == "documents" and body.submit:
            with session(settings) as connection:
                verified = connection.execute(
                    """SELECT 1 FROM employee_documents WHERE employee_id = ?
                       AND is_current = 1 AND status = 'Verified'
                       AND category IN ('identity', 'resume', 'education', 'offer_letter')
                       LIMIT 1""",
                    (employee_id,),
                ).fetchone()
            if verified is None:
                raise HTTPException(
                    409, "At least one required document must be verified before completing this task."
                )
        with session(settings) as connection:
            row = connection.execute(
                """SELECT status FROM employee_onboarding_tasks
                   WHERE employee_id = ? AND task_key = ?""",
                (employee_id, task_key),
            ).fetchone()
            if row is None:
                raise HTTPException(404, "Onboarding task not found.")
            if row["status"] == "approved":
                raise HTTPException(409, "This onboarding task is already approved.")
            status = "submitted" if body.submit else "pending"
            connection.execute(
                """UPDATE employee_onboarding_tasks SET response_json = ?, status = ?,
                   submitted_at = CASE WHEN ? = 1 THEN CURRENT_TIMESTAMP ELSE submitted_at END,
                   review_note = '' WHERE employee_id = ? AND task_key = ?""",
                (
                    json.dumps(body.response, ensure_ascii=True),
                    status,
                    int(body.submit),
                    employee_id,
                    task_key,
                ),
            )
            if body.submit:
                _notify_admins(
                    connection, "onboarding", "Onboarding task submitted",
                    f"{_employee_public_name(connection, employee_id)} submitted an onboarding task.",
                    "onboarding", employee_id,
                )
                _audit(
                    connection, user["username"], "onboarding.task_submitted",
                    employee_id, f"task={task_key}",
                )
        return {"status": status, "message": "Progress saved." if not body.submit else "Task submitted for HR review."}

    @router.get("/admin/onboarding")
    def all_onboarding_progress(
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
        search: str = Query(default="", max_length=100),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        with session(settings) as connection:
            employees = connection.execute(
                """SELECT employee_id, name, department, designation, status
                   FROM employees WHERE (? = '' OR employee_id LIKE ? OR name LIKE ?)
                   ORDER BY name LIMIT ? OFFSET ?""",
                (
                    search.strip(), f"%{search.strip()}%", f"%{search.strip()}%",
                    limit, offset,
                ),
            ).fetchall()
            total = connection.execute(
                """SELECT COUNT(*) FROM employees
                   WHERE (? = '' OR employee_id LIKE ? OR name LIKE ?)""",
                (search.strip(), f"%{search.strip()}%", f"%{search.strip()}%"),
            ).fetchone()[0]
            progress = []
            for employee in employees:
                rows = connection.execute(
                    """SELECT t.task_key, t.title, t.required, et.status,
                              et.submitted_at, et.review_note
                       FROM onboarding_task_templates t
                       JOIN employee_onboarding_tasks et ON et.task_key = t.task_key
                       WHERE et.employee_id = ? ORDER BY t.display_order""",
                    (employee["employee_id"],),
                ).fetchall()
                tasks = [dict(row) for row in rows]
                required = [task for task in tasks if task["required"]]
                complete = sum(task["status"] == "approved" for task in required)
                progress.append(
                    {
                        **dict(employee),
                        "tasks": tasks,
                        "completed_tasks": complete,
                        "required_tasks": len(required),
                        "completion_percentage": round(100 * complete / len(required)) if required else 100,
                    }
                )
        return {"items": progress, "total": total, "limit": limit, "offset": offset}

    @router.patch("/admin/onboarding/{employee_id}/{task_key}")
    def review_onboarding_task(
        employee_id: str,
        task_key: TaskKey,
        body: OnboardingReviewInput,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> dict[str, str]:
        with session(settings) as connection:
            row = connection.execute(
                """SELECT status FROM employee_onboarding_tasks
                   WHERE employee_id = ? AND task_key = ?""",
                (employee_id, task_key),
            ).fetchone()
            if row is None:
                raise HTTPException(404, "Onboarding task not found.")
            if row["status"] != "submitted":
                raise HTTPException(409, "Only submitted tasks can be reviewed.")
            connection.execute(
                """UPDATE employee_onboarding_tasks SET status = ?, reviewed_by = ?,
                   reviewed_at = CURRENT_TIMESTAMP, review_note = ?
                   WHERE employee_id = ? AND task_key = ?""",
                (body.status, user["username"], body.note, employee_id, task_key),
            )
            _timeline(
                connection, employee_id, user["username"], "onboarding.review",
                f"Onboarding task {task_key} {body.status}.",
                {"status": "submitted"}, {"status": body.status, "note": body.note},
            )
            _audit(
                connection, user["username"], f"onboarding.{body.status}",
                employee_id, f"task={task_key}",
            )
            _notify_employee(
                connection, employee_id, "onboarding", "normal",
                "Onboarding task reviewed",
                f"Your {task_key.replace('_', ' ')} task was {body.status}.",
                "onboarding", task_key,
            )
            tasks = connection.execute(
                """SELECT t.required, et.status FROM onboarding_task_templates t
                   JOIN employee_onboarding_tasks et ON et.task_key = t.task_key
                   WHERE et.employee_id = ?""",
                (employee_id,),
            ).fetchall()
            all_complete = all(
                not task["required"] or task["status"] == "approved" for task in tasks
            )
            connection.execute(
                "UPDATE employees SET onboarding_complete = ? WHERE employee_id = ?",
                (int(all_complete), employee_id),
            )
        return {"status": body.status, "message": "Onboarding task reviewed."}

    @router.get("/documents")
    def list_documents(
        user: Annotated[dict[str, Any], Depends(current_user)],
        employee_id: str | None = None,
        include_history: bool = False,
    ) -> list[dict[str, Any]]:
        if user["role"] == "employee":
            target_id = _employee_for_user(user)
            if employee_id and employee_id != target_id:
                raise HTTPException(403, "Employees can view only their own documents.")
        else:
            target_id = employee_id
        query = """SELECT document_id, document_group_id, employee_id, category,
                          original_name, content_type, file_size, version, status,
                          review_note, created_at, reviewed_at, is_current
                   FROM employee_documents WHERE 1 = 1"""
        parameters: list[Any] = []
        if target_id:
            query += " AND employee_id = ?"
            parameters.append(target_id)
        if not include_history:
            query += " AND is_current = 1"
        query += " ORDER BY created_at DESC, document_id DESC"
        with session(settings) as connection:
            return [dict(row) for row in connection.execute(query, parameters)]

    @router.post("/documents", status_code=201)
    async def upload_document(
        request: Request,
        user: Annotated[dict[str, Any], Depends(current_user)],
        category: str = Form(...),
        file: UploadFile = File(...),
        employee_id: str | None = Form(default=None),
        replaces_document_id: int | None = Form(default=None),
    ) -> dict[str, Any]:
        if category not in DOCUMENT_CATEGORIES:
            raise HTTPException(422, "Unsupported document category.")
        if user["role"] == "employee":
            target_id = _employee_for_user(user)
            if employee_id and employee_id != target_id:
                raise HTTPException(403, "Employees can upload documents only to their own record.")
        else:
            if not employee_id:
                raise HTTPException(422, "An employee ID is required for HR uploads.")
            target_id = employee_id

        filename = Path((file.filename or "document").replace("\\", "/")).name
        extension = Path(filename).suffix.lower()
        expected = ALLOWED_FILES.get(extension)
        if expected is None:
            raise HTTPException(415, "Only PDF, JPEG and PNG documents are accepted.")
        content = await file.read(settings.max_upload_bytes + 1)
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(413, "Document exceeds the configured upload size limit.")
        content_type, signature = expected
        if file.content_type != content_type or not content.startswith(signature):
            raise HTTPException(415, "File content does not match its allowed file type.")
        if len(content) == 0:
            raise HTTPException(422, "The uploaded file is empty.")

        group_id = str(uuid.uuid4())
        version = 1
        previous_id: int | None = None
        with session(settings) as connection:
            if connection.execute(
                "SELECT 1 FROM employees WHERE employee_id = ?", (target_id,)
            ).fetchone() is None:
                raise HTTPException(404, "Employee not found.")
            if replaces_document_id is not None:
                previous = connection.execute(
                    """SELECT document_id, document_group_id, employee_id, category,
                              version, is_current
                       FROM employee_documents WHERE document_id = ?""",
                    (replaces_document_id,),
                ).fetchone()
                if (
                    previous is None
                    or previous["employee_id"] != target_id
                    or not previous["is_current"]
                ):
                    raise HTTPException(404, "Current document to replace was not found.")
                if previous["category"] != category:
                    raise HTTPException(422, "A replacement must use the same document category.")
                group_id = previous["document_group_id"]
                version = previous["version"] + 1
                previous_id = previous["document_id"]

        storage_key = f"{uuid.uuid4().hex}{extension}"
        PRIVATE_STORAGE.mkdir(parents=True, exist_ok=True)
        path = PRIVATE_STORAGE / storage_key
        path.write_bytes(content)
        try:
            with session(settings) as connection:
                if previous_id is not None:
                    connection.execute(
                        "UPDATE employee_documents SET is_current = 0 WHERE document_id = ?",
                        (previous_id,),
                    )
                cursor = connection.execute(
                    """INSERT INTO employee_documents(
                           document_group_id, employee_id, category, original_name,
                           storage_key, content_type, file_size, version,
                           uploaded_by, replaces_document_id
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        group_id, target_id, category, filename, storage_key,
                        content_type, len(content), version, user["username"], previous_id,
                    ),
                )
                document_id = cursor.lastrowid
                _notify_admins(
                    connection, "document", "Document submitted for verification",
                    f"An employee document was uploaded for {target_id}.",
                    "document", str(document_id),
                )
                _audit(
                    connection, user["username"], "document.uploaded",
                    target_id, f"document_id={document_id};category={category};version={version}",
                )
            return {
                "document_id": document_id,
                "category": category,
                "version": version,
                "status": "Pending",
                "message": "Document uploaded for verification.",
            }
        except Exception:
            path.unlink(missing_ok=True)
            raise

    @router.get("/documents/{document_id}/download")
    def download_document(
        document_id: int,
        user: Annotated[dict[str, Any], Depends(current_user)],
    ) -> StreamingResponse:
        with session(settings) as connection:
            document = connection.execute(
                """SELECT employee_id, original_name, storage_key, content_type
                   FROM employee_documents WHERE document_id = ?""",
                (document_id,),
            ).fetchone()
            if document is None:
                raise HTTPException(404, "Document not found.")
            if user["role"] != "admin" and user.get("employee_id") != document["employee_id"]:
                raise HTTPException(403, "You are not authorized to download this document.")
        return _file_response(
            PRIVATE_STORAGE / document["storage_key"],
            document["original_name"],
            document["content_type"],
        )

    @router.patch("/admin/documents/{document_id}")
    def review_document(
        document_id: int,
        body: DocumentReviewInput,
        request: Request,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> dict[str, str]:
        del request
        with session(settings) as connection:
            document = connection.execute(
                """SELECT employee_id, status FROM employee_documents
                   WHERE document_id = ? AND is_current = 1""",
                (document_id,),
            ).fetchone()
            if document is None:
                raise HTTPException(404, "Current document not found.")
            if document["status"] != "Pending":
                raise HTTPException(409, "Only pending documents can be reviewed.")
            connection.execute(
                """UPDATE employee_documents SET status = ?, review_note = ?,
                   reviewed_by = ?, reviewed_at = CURRENT_TIMESTAMP
                   WHERE document_id = ?""",
                (body.status, body.note, user["username"], document_id),
            )
            _audit(
                connection, user["username"], f"document.{body.status.lower()}",
                document["employee_id"], f"document_id={document_id}",
            )
            _timeline(
                connection, document["employee_id"], user["username"],
                "document.review", f"Document {body.status.lower()}.",
                {"status": "Pending"},
                {"status": body.status, "document_id": document_id},
            )
            _notify_employee(
                connection, document["employee_id"], "document",
                "normal" if body.status == "Verified" else "high",
                "Document verification update",
                f"Your document was {body.status.lower()}. {body.note}".strip(),
                "document", str(document_id),
            )
        return {"message": f"Document {body.status.lower()}."}

    @router.get("/employees/{employee_id}/timeline")
    def employee_timeline(
        employee_id: str,
        user: Annotated[dict[str, Any], Depends(current_user)],
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        with session(settings) as connection:
            _require_manager_or_admin(connection, user, employee_id)
            if user["role"] == "employee" and user["employee_id"] != employee_id:
                raise HTTPException(403, "Employees can view only their own timeline.")
            rows = connection.execute(
                """SELECT event_id, employee_id, actor_username, event_type, summary,
                          previous_values_json, new_values_json, created_at
                   FROM employee_timeline WHERE employee_id = ?
                   ORDER BY event_id DESC LIMIT ? OFFSET ?""",
                (employee_id, limit, offset),
            ).fetchall()
            total = connection.execute(
                "SELECT COUNT(*) FROM employee_timeline WHERE employee_id = ?",
                (employee_id,),
            ).fetchone()[0]
        items = []
        for row in rows:
            item = dict(row)
            item["previous_values"] = json.loads(item.pop("previous_values_json"))
            item["new_values"] = json.loads(item.pop("new_values_json"))
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @router.get("/leave/policies")
    def list_leave_policies(
        user: Annotated[dict[str, Any], Depends(current_user)],
    ) -> list[dict[str, Any]]:
        with session(settings) as connection:
            return [
                dict(row)
                for row in connection.execute(
                    """SELECT leave_type, annual_allowance, count_weekends, active,
                              updated_at FROM leave_policies
                       ORDER BY leave_type COLLATE NOCASE"""
                )
            ]

    @router.put("/admin/leave/policies/{leave_type}")
    def save_leave_policy(
        leave_type: str,
        body: LeavePolicyInput,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> dict[str, Any]:
        leave_type = leave_type.strip()
        if not leave_type or len(leave_type) > 60:
            raise HTTPException(422, "Leave type must contain 1-60 characters.")
        with session(settings) as connection:
            existing = connection.execute(
                "SELECT leave_type FROM leave_policies WHERE leave_type = ? COLLATE NOCASE",
                (leave_type,),
            ).fetchone()
            canonical = existing["leave_type"] if existing else leave_type
            connection.execute(
                """INSERT INTO leave_policies(
                       leave_type, annual_allowance, count_weekends, active, updated_by,
                       updated_at
                   ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(leave_type) DO UPDATE SET
                       annual_allowance = excluded.annual_allowance,
                       count_weekends = excluded.count_weekends,
                       active = excluded.active, updated_by = excluded.updated_by,
                       updated_at = CURRENT_TIMESTAMP""",
                (
                    canonical, body.annual_allowance, int(body.count_weekends),
                    int(body.active), user["username"],
                ),
            )
            _audit(
                connection, user["username"], "leave.policy_updated", canonical,
                json.dumps(body.model_dump(), sort_keys=True),
            )
            row = connection.execute(
                "SELECT * FROM leave_policies WHERE leave_type = ?", (canonical,)
            ).fetchone()
        return dict(row)

    @router.get("/leave/balances")
    def leave_balances(
        user: Annotated[dict[str, Any], Depends(current_user)],
        employee_id: str | None = None,
        year: int | None = Query(default=None, ge=2000, le=2200),
    ) -> dict[str, Any]:
        selected_year = year or datetime.now(ZoneInfo(settings.organization_timezone)).year
        single_target = employee_id is not None
        if user["role"] == "employee":
            own_id = _employee_for_user(user)
            if employee_id and employee_id != own_id:
                raise HTTPException(403, "Employees can view only their own leave balance.")
            if employee_id:
                targets = [employee_id]
            else:
                with session(settings) as connection:
                    reports = [
                        row["employee_id"] for row in connection.execute(
                            """SELECT r.employee_id FROM employee_reporting r
                               JOIN employees e ON e.employee_id = r.employee_id
                               WHERE r.manager_id = ? AND e.status = 'Approved'
                                 AND e.active = 1 ORDER BY r.employee_id""",
                            (own_id,),
                        )
                    ]
                    is_manager = bool(reports)
                targets = [own_id, *reports]
                single_target = not is_manager
        elif employee_id:
            targets = [employee_id]
        else:
            with session(settings) as connection:
                targets = [
                    row["employee_id"] for row in connection.execute(
                        "SELECT employee_id FROM employees ORDER BY employee_id"
                    )
                ]
        with session(settings) as connection:
            result = []
            for target in targets:
                _require_manager_or_admin(connection, user, target)
                balances = []
                for policy in connection.execute(
                    """SELECT leave_type, annual_allowance, count_weekends
                       FROM leave_policies WHERE active = 1 ORDER BY leave_type"""
                ):
                    used = connection.execute(
                        """SELECT COALESCE(SUM(requested_days), 0)
                           FROM leave_requests WHERE employee_id = ? AND leave_type = ?
                           AND status = 'Approved' AND substr(start_date, 1, 4) = ?""",
                        (target, policy["leave_type"], str(selected_year)),
                    ).fetchone()[0]
                    pending = connection.execute(
                        """SELECT COALESCE(SUM(requested_days), 0)
                           FROM leave_requests WHERE employee_id = ? AND leave_type = ?
                           AND status = 'Pending' AND substr(start_date, 1, 4) = ?""",
                        (target, policy["leave_type"], str(selected_year)),
                    ).fetchone()[0]
                    balances.append(
                        {
                            "leave_type": policy["leave_type"],
                            "annual_allowance": policy["annual_allowance"],
                            "approved_days": used,
                            "pending_days": pending,
                            "available_days": max(0, policy["annual_allowance"] - used - pending),
                            "count_weekends": bool(policy["count_weekends"]),
                        }
                    )
                result.append({"employee_id": target, "year": selected_year, "balances": balances})
        return result[0] if single_target and result else {"year": selected_year, "employees": result}

    @router.get("/leave/requests")
    def list_leave_requests(
        user: Annotated[dict[str, Any], Depends(current_user)],
        status: Literal["Pending", "Approved", "Rejected", "Cancelled"] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        query = """SELECT l.leave_request_id, l.employee_id, e.name, e.department,
                          l.leave_type, l.start_date, l.end_date, l.requested_days,
                          l.reason, l.status, l.rejection_reason, l.reviewed_by,
                          l.created_at, l.reviewed_at
                   FROM leave_requests l JOIN employees e ON e.employee_id = l.employee_id
                   WHERE 1 = 1"""
        parameters: list[Any] = []
        if user["role"] == "employee":
            employee_id = _employee_for_user(user)
            is_manager = False
            with session(settings) as connection:
                is_manager = bool(connection.execute(
                    "SELECT 1 FROM employee_reporting WHERE manager_id = ? LIMIT 1",
                    (employee_id,),
                ).fetchone())
            if not is_manager:
                query += " AND l.employee_id = ?"
                parameters.append(employee_id)
            else:
                query += """ AND (l.employee_id = ? OR l.employee_id IN
                    (SELECT employee_id FROM employee_reporting WHERE manager_id = ?))"""
                parameters.extend([employee_id, employee_id])
        if status:
            query += " AND l.status = ?"
            parameters.append(status)
        if start_date:
            query += " AND l.end_date >= ?"
            parameters.append(start_date.isoformat())
        if end_date:
            query += " AND l.start_date <= ?"
            parameters.append(end_date.isoformat())
        with session(settings) as connection:
            total_query = "SELECT COUNT(*) FROM (" + query + ")"
            total = connection.execute(total_query, parameters).fetchone()[0]
            rows = connection.execute(
                query + " ORDER BY l.start_date DESC, l.leave_request_id DESC LIMIT ? OFFSET ?",
                [*parameters, limit, offset],
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    @router.post("/leave/requests", status_code=201)
    def create_leave_request(
        body: LeaveRequestInput,
        user: Annotated[dict[str, Any], Depends(require_roles("employee"))],
    ) -> dict[str, Any]:
        employee_id = _employee_for_user(user)
        if body.start_date.year != body.end_date.year:
            raise HTTPException(422, "Split a leave request across calendar years.")
        with session(settings) as connection:
            policy = connection.execute(
                """SELECT annual_allowance, count_weekends FROM leave_policies
                   WHERE leave_type = ? COLLATE NOCASE AND active = 1""",
                (body.leave_type,),
            ).fetchone()
            if policy is None:
                raise HTTPException(422, "This leave type is not configured or is inactive.")
            actual_type = connection.execute(
                "SELECT leave_type FROM leave_policies WHERE leave_type = ? COLLATE NOCASE",
                (body.leave_type,),
            ).fetchone()["leave_type"]
            overlapping = connection.execute(
                """SELECT 1 FROM leave_requests WHERE employee_id = ?
                   AND status IN ('Pending', 'Approved')
                   AND start_date <= ? AND end_date >= ? LIMIT 1""",
                (employee_id, body.end_date.isoformat(), body.start_date.isoformat()),
            ).fetchone()
            if overlapping:
                raise HTTPException(409, "This request overlaps another pending or approved leave.")
            days = _workdays(body.start_date, body.end_date, bool(policy["count_weekends"]))
            if days <= 0:
                raise HTTPException(422, "The selected dates contain no leave days under this policy.")
            used = connection.execute(
                """SELECT COALESCE(SUM(requested_days), 0) FROM leave_requests
                   WHERE employee_id = ? AND leave_type = ? AND status IN ('Pending', 'Approved')
                   AND substr(start_date, 1, 4) = ?""",
                (employee_id, actual_type, str(body.start_date.year)),
            ).fetchone()[0]
            if used + days > policy["annual_allowance"]:
                raise HTTPException(
                    409,
                    f"Insufficient {actual_type} balance. Available: {max(0, policy['annual_allowance'] - used):g} days.",
                )
            cursor = connection.execute(
                """INSERT INTO leave_requests(
                       employee_id, leave_type, start_date, end_date, requested_days, reason
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    employee_id, actual_type, body.start_date.isoformat(),
                    body.end_date.isoformat(), days, body.reason.strip(),
                ),
            )
            request_id = cursor.lastrowid
            _notify_admins(
                connection, "leave", "Leave request submitted",
                f"{_employee_public_name(connection, employee_id)} submitted a leave request.",
                "leave", str(request_id),
            )
            manager = connection.execute(
                "SELECT manager_id FROM employee_reporting WHERE employee_id = ?",
                (employee_id,),
            ).fetchone()
            if manager and manager["manager_id"]:
                _notify_employee(
                    connection, manager["manager_id"], "leave", "normal",
                    "Team leave request",
                    f"{_employee_public_name(connection, employee_id)} submitted a leave request.",
                    "leave", str(request_id),
                )
            _audit(connection, user["username"], "leave.requested", employee_id, f"request_id={request_id}")
        return {"leave_request_id": request_id, "requested_days": days, "status": "Pending"}

    @router.patch("/leave/requests/{leave_request_id}")
    def decide_leave_request(
        leave_request_id: int,
        body: LeaveDecisionInput,
        user: Annotated[dict[str, Any], Depends(current_user)],
    ) -> dict[str, str]:
        if user["role"] not in {"admin", "employee"}:
            raise HTTPException(403, "This action is not permitted.")
        with session(settings) as connection:
            row = connection.execute(
                """SELECT employee_id, status FROM leave_requests
                   WHERE leave_request_id = ?""",
                (leave_request_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(404, "Leave request not found.")
            _require_manager_or_admin(connection, user, row["employee_id"])
            if user["role"] != "admin" and user.get("employee_id") == row["employee_id"]:
                raise HTTPException(403, "Employees cannot review their own leave request.")
            if row["status"] != "Pending":
                raise HTTPException(409, "Only pending leave requests can be reviewed.")
            if body.status == "Rejected" and not body.rejection_reason.strip():
                raise HTTPException(422, "A rejection reason is required.")
            connection.execute(
                """UPDATE leave_requests SET status = ?, rejection_reason = ?,
                   reviewed_by = ?, reviewed_at = CURRENT_TIMESTAMP
                   WHERE leave_request_id = ?""",
                (body.status, body.rejection_reason, user["username"], leave_request_id),
            )
            _audit(
                connection, user["username"], f"leave.{body.status.lower()}",
                row["employee_id"], f"leave_request_id={leave_request_id}",
            )
            _notify_employee(
                connection, row["employee_id"], "leave",
                "normal" if body.status == "Approved" else "high",
                "Leave request decision",
                (
                    "Your leave request was approved."
                    if body.status == "Approved"
                    else f"Your leave request was rejected. {body.rejection_reason}".strip()
                ),
                "leave", str(leave_request_id),
            )
        return {"status": body.status, "message": f"Leave request {body.status.lower()}."}

    @router.get("/leave/calendar")
    def leave_calendar(
        user: Annotated[dict[str, Any], Depends(current_user)],
        start_date: date = Query(...),
        end_date: date = Query(...),
    ) -> list[dict[str, Any]]:
        if end_date < start_date or (end_date - start_date).days > 93:
            raise HTTPException(422, "Choose a calendar range no longer than 94 days.")
        query = """SELECT l.leave_request_id, l.employee_id, e.name, e.department,
                          l.leave_type, l.start_date, l.end_date, l.requested_days,
                          l.status
                   FROM leave_requests l JOIN employees e ON e.employee_id = l.employee_id
                   WHERE l.status = 'Approved' AND l.start_date <= ? AND l.end_date >= ?"""
        parameters: list[Any] = [end_date.isoformat(), start_date.isoformat()]
        if user["role"] == "employee":
            own_id = _employee_for_user(user)
            with session(settings) as connection:
                is_manager = connection.execute(
                    "SELECT 1 FROM employee_reporting WHERE manager_id = ? LIMIT 1",
                    (own_id,),
                ).fetchone()
            if not is_manager:
                query += " AND l.employee_id = ?"
                parameters.append(own_id)
            else:
                query += """ AND (l.employee_id = ? OR l.employee_id IN
                    (SELECT employee_id FROM employee_reporting WHERE manager_id = ?))"""
                parameters.extend([own_id, own_id])
        with session(settings) as connection:
            return [dict(row) for row in connection.execute(query + " ORDER BY l.start_date", parameters)]

    @router.post("/attendance/check-in", status_code=201)
    def attendance_check_in(
        user: Annotated[dict[str, Any], Depends(require_roles("employee"))],
    ) -> dict[str, Any]:
        employee_id = _employee_for_user(user)
        now = datetime.now(timezone.utc)
        local_now = now.astimezone(ZoneInfo(settings.organization_timezone))
        is_late = local_now.time().replace(tzinfo=None) > datetime_time.fromisoformat(settings.workday_start)
        with session(settings) as connection:
            try:
                cursor = connection.execute(
                    """INSERT INTO attendance_records(
                           employee_id, local_work_date, check_in_utc, is_late
                       ) VALUES (?, ?, ?, ?)""",
                    (employee_id, local_now.date().isoformat(), now.isoformat(), int(is_late)),
                )
            except sqlite3.IntegrityError as exc:
                raise HTTPException(409, "Attendance is already checked in for today.") from exc
            attendance_id = cursor.lastrowid
            _audit(connection, user["username"], "attendance.check_in", employee_id)
        return {
            "attendance_id": attendance_id,
            "local_work_date": local_now.date().isoformat(),
            "check_in": now.isoformat(),
            "is_late": is_late,
        }

    @router.patch("/attendance/check-out")
    def attendance_check_out(
        user: Annotated[dict[str, Any], Depends(require_roles("employee"))],
    ) -> dict[str, Any]:
        employee_id = _employee_for_user(user)
        now = datetime.now(timezone.utc)
        today = now.astimezone(ZoneInfo(settings.organization_timezone)).date().isoformat()
        with session(settings) as connection:
            row = connection.execute(
                """SELECT attendance_id, check_in_utc, check_out_utc
                   FROM attendance_records WHERE employee_id = ? AND local_work_date = ?""",
                (employee_id, today),
            ).fetchone()
            if row is None:
                raise HTTPException(409, "Check in before checking out.")
            if row["check_out_utc"]:
                raise HTTPException(409, "Attendance has already been checked out.")
            if now <= datetime.fromisoformat(row["check_in_utc"]):
                raise HTTPException(409, "Check-out must be later than check-in.")
            connection.execute(
                "UPDATE attendance_records SET check_out_utc = ? WHERE attendance_id = ?",
                (now.isoformat(), row["attendance_id"]),
            )
            _audit(connection, user["username"], "attendance.check_out", employee_id)
        return {"attendance_id": row["attendance_id"], "check_out": now.isoformat()}

    @router.get("/attendance/history")
    def attendance_history(
        user: Annotated[dict[str, Any], Depends(current_user)],
        employee_id: str | None = None,
        month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        query = """SELECT a.attendance_id, a.employee_id, e.name, e.department,
                          a.local_work_date, a.check_in_utc, a.check_out_utc, a.is_late
                   FROM attendance_records a JOIN employees e ON e.employee_id = a.employee_id
                   WHERE 1 = 1"""
        parameters: list[Any] = []
        if user["role"] == "employee":
            target = _employee_for_user(user)
            if employee_id and employee_id != target:
                with session(settings) as connection:
                    _require_manager_or_admin(connection, user, employee_id)
                target = employee_id
            query += " AND a.employee_id = ?"
            parameters.append(target)
        elif employee_id:
            query += " AND a.employee_id = ?"
            parameters.append(employee_id)
        if month:
            try:
                datetime.strptime(month, "%Y-%m")
            except ValueError as exc:
                raise HTTPException(422, "Month must use YYYY-MM format.") from exc
            query += " AND substr(a.local_work_date, 1, 7) = ?"
            parameters.append(month)
        with session(settings) as connection:
            total = connection.execute(
                "SELECT COUNT(*) FROM (" + query + ")", parameters
            ).fetchone()[0]
            rows = connection.execute(
                query + " ORDER BY a.local_work_date DESC LIMIT ? OFFSET ?",
                [*parameters, limit, offset],
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["worked_hours"] = _attendance_worked_hours(item)
            item["is_late"] = bool(item["is_late"])
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @router.post("/attendance/corrections", status_code=201)
    def request_attendance_correction(
        body: AttendanceCorrectionInput,
        user: Annotated[dict[str, Any], Depends(require_roles("employee"))],
    ) -> dict[str, Any]:
        employee_id = _employee_for_user(user)
        requested = _iso_utc(body.requested_check_out, settings.organization_timezone)
        now = datetime.now(timezone.utc)
        if datetime.fromisoformat(requested) > now:
            raise HTTPException(422, "A correction cannot request a future check-out.")
        with session(settings) as connection:
            record = connection.execute(
                """SELECT attendance_id, check_in_utc, check_out_utc
                   FROM attendance_records WHERE employee_id = ?
                   AND local_work_date = ?""",
                (
                    employee_id,
                    datetime.fromisoformat(requested).astimezone(
                        ZoneInfo(settings.organization_timezone)
                    ).date().isoformat(),
                ),
            ).fetchone()
            if record is None:
                raise HTTPException(404, "Attendance record not found.")
            if record["check_out_utc"]:
                raise HTTPException(409, "This attendance record already has a check-out.")
            if datetime.fromisoformat(requested) <= datetime.fromisoformat(record["check_in_utc"]):
                raise HTTPException(422, "Requested check-out must be later than check-in.")
            pending = connection.execute(
                """SELECT 1 FROM attendance_corrections WHERE attendance_id = ?
                   AND status = 'Pending'""",
                (record["attendance_id"],),
            ).fetchone()
            if pending:
                raise HTTPException(409, "A correction request is already pending.")
            cursor = connection.execute(
                """INSERT INTO attendance_corrections(
                       attendance_id, employee_id, requested_check_out_utc, reason
                   ) VALUES (?, ?, ?, ?)""",
                (record["attendance_id"], employee_id, requested, body.reason),
            )
            correction_id = cursor.lastrowid
            _notify_admins(
                connection, "attendance", "Attendance correction requested",
                f"{_employee_public_name(connection, employee_id)} requested a missed check-out correction.",
                "attendance_correction", str(correction_id),
            )
            _audit(connection, user["username"], "attendance.correction_requested", employee_id, f"correction_id={correction_id}")
        return {"correction_id": correction_id, "status": "Pending"}

    @router.get("/admin/attendance/corrections")
    def list_attendance_corrections(
        user: Annotated[dict[str, Any], Depends(current_user)],
        status: Literal["Pending", "Approved", "Rejected"] = "Pending",
    ) -> list[dict[str, Any]]:
        query = """SELECT c.correction_id, c.employee_id, e.name, c.attendance_id,
                          a.local_work_date, a.check_in_utc,
                          c.requested_check_out_utc, c.reason, c.status,
                          c.created_at, c.review_note
                   FROM attendance_corrections c
                   JOIN employees e ON e.employee_id = c.employee_id
                   JOIN attendance_records a ON a.attendance_id = c.attendance_id
                   WHERE c.status = ?"""
        parameters: list[Any] = [status]
        if user["role"] == "employee":
            employee_id = _employee_for_user(user)
            with session(settings) as connection:
                is_manager = connection.execute(
                    "SELECT 1 FROM employee_reporting WHERE manager_id = ? LIMIT 1",
                    (employee_id,),
                ).fetchone()
                if not is_manager:
                    query += " AND c.employee_id = ?"
                    parameters.append(employee_id)
                else:
                    query += """ AND (c.employee_id = ? OR c.employee_id IN
                        (SELECT employee_id FROM employee_reporting WHERE manager_id = ?))"""
                    parameters.extend([employee_id, employee_id])
        with session(settings) as connection:
            return [dict(row) for row in connection.execute(query + " ORDER BY c.created_at DESC", parameters)]

    @router.patch("/admin/attendance/corrections/{correction_id}")
    def review_attendance_correction(
        correction_id: int,
        body: CorrectionReviewInput,
        user: Annotated[dict[str, Any], Depends(current_user)],
    ) -> dict[str, str]:
        with session(settings) as connection:
            correction = connection.execute(
                """SELECT attendance_id, employee_id, requested_check_out_utc, status
                   FROM attendance_corrections WHERE correction_id = ?""",
                (correction_id,),
            ).fetchone()
            if correction is None:
                raise HTTPException(404, "Attendance correction not found.")
            _require_manager_or_admin(connection, user, correction["employee_id"])
            if user["role"] == "employee" and user.get("employee_id") == correction["employee_id"]:
                raise HTTPException(403, "Employees cannot review their own correction.")
            if correction["status"] != "Pending":
                raise HTTPException(409, "Only pending corrections can be reviewed.")
            connection.execute(
                """UPDATE attendance_corrections SET status = ?, reviewed_by = ?,
                   review_note = ?, reviewed_at = CURRENT_TIMESTAMP
                   WHERE correction_id = ?""",
                (body.status, user["username"], body.note, correction_id),
            )
            if body.status == "Approved":
                record = connection.execute(
                    "SELECT check_out_utc FROM attendance_records WHERE attendance_id = ?",
                    (correction["attendance_id"],),
                ).fetchone()
                if record["check_out_utc"] is not None:
                    raise HTTPException(409, "Attendance already has a check-out.")
                connection.execute(
                    "UPDATE attendance_records SET check_out_utc = ? WHERE attendance_id = ?",
                    (correction["requested_check_out_utc"], correction["attendance_id"]),
                )
            _audit(connection, user["username"], f"attendance.correction_{body.status.lower()}", correction["employee_id"])
            _notify_employee(
                connection, correction["employee_id"], "attendance", "normal",
                "Attendance correction reviewed",
                f"Your attendance correction was {body.status.lower()}.",
                "attendance_correction", str(correction_id),
            )
        return {"status": body.status, "message": "Attendance correction reviewed."}

    @router.get("/admin/attendance/monthly")
    def monthly_attendance_report(
        user: Annotated[dict[str, Any], Depends(current_user)],
        month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
        department: str = Query(default="", max_length=80),
    ) -> list[dict[str, Any]]:
        try:
            datetime.strptime(month, "%Y-%m")
        except ValueError as exc:
            raise HTTPException(422, "Month must use YYYY-MM format.") from exc
        query = """SELECT e.employee_id, e.name, e.department,
                          COUNT(a.attendance_id) AS days_present,
                          SUM(CASE WHEN a.is_late = 1 THEN 1 ELSE 0 END) AS late_days,
                          SUM(CASE WHEN a.check_out_utc IS NULL THEN 1 ELSE 0 END) AS missed_checkouts
                   FROM employees e LEFT JOIN attendance_records a
                     ON a.employee_id = e.employee_id
                    AND substr(a.local_work_date, 1, 7) = ?
                   WHERE e.status = 'Approved' AND e.active = 1"""
        parameters: list[Any] = [month]
        if user["role"] == "employee":
            own = _employee_for_user(user)
            query += """ AND (e.employee_id = ? OR e.employee_id IN
                (SELECT employee_id FROM employee_reporting WHERE manager_id = ?))"""
            parameters.extend([own, own])
        if department:
            query += " AND e.department = ? COLLATE NOCASE"
            parameters.append(department)
        query += " GROUP BY e.employee_id ORDER BY e.name"
        with session(settings) as connection:
            return [dict(row) for row in connection.execute(query, parameters)]

    @router.get("/performance/periods")
    def list_performance_periods(
        user: Annotated[dict[str, Any], Depends(current_user)],
    ) -> list[dict[str, Any]]:
        with session(settings) as connection:
            return [
                dict(row) for row in connection.execute(
                    """SELECT period_id, name, start_date, end_date, status, created_at
                       FROM performance_periods ORDER BY start_date DESC"""
                )
            ]

    @router.post("/admin/performance/periods", status_code=201)
    def create_performance_period(
        body: PerformancePeriodInput,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> dict[str, Any]:
        with session(settings) as connection:
            try:
                cursor = connection.execute(
                    """INSERT INTO performance_periods(
                           name, start_date, end_date, created_by
                       ) VALUES (?, ?, ?, ?)""",
                    (
                        body.name.strip(), body.start_date.isoformat(),
                        body.end_date.isoformat(), user["username"],
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise HTTPException(409, "A review period with that name already exists.") from exc
            period_id = cursor.lastrowid
            _audit(connection, user["username"], "performance.period_created", str(period_id))
            for employee in connection.execute(
                "SELECT employee_id FROM employees WHERE status = 'Approved' AND active = 1"
            ):
                connection.execute(
                    """INSERT OR IGNORE INTO performance_reviews(employee_id, period_id)
                       VALUES (?, ?)""",
                    (employee["employee_id"], period_id),
                )
        return {"period_id": period_id, "name": body.name, "status": "Open"}

    @router.patch("/admin/performance/periods/{period_id}/close")
    def close_performance_period(
        period_id: int,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> dict[str, str]:
        with session(settings) as connection:
            cursor = connection.execute(
                "UPDATE performance_periods SET status = 'Closed' WHERE period_id = ? AND status = 'Open'",
                (period_id,),
            )
            if cursor.rowcount == 0:
                exists = connection.execute(
                    "SELECT 1 FROM performance_periods WHERE period_id = ?", (period_id,)
                ).fetchone()
                if exists is None:
                    raise HTTPException(404, "Performance period not found.")
                raise HTTPException(409, "Performance period is already closed.")
            _audit(connection, user["username"], "performance.period_closed", str(period_id))
        return {"status": "Closed"}

    @router.post("/performance/goals", status_code=201)
    def create_performance_goal(
        body: PerformanceGoalInput,
        user: Annotated[dict[str, Any], Depends(current_user)],
    ) -> dict[str, Any]:
        if user["role"] != "admin":
            if user.get("employee_id") == body.employee_id:
                raise HTTPException(403, "Employees cannot assign goals to themselves.")
            with session(settings) as connection:
                _require_manager_or_admin(connection, user, body.employee_id)
        with session(settings) as connection:
            period = connection.execute(
                """SELECT start_date, end_date, status FROM performance_periods
                   WHERE period_id = ?""",
                (body.period_id,),
            ).fetchone()
            if period is None:
                raise HTTPException(404, "Performance period not found.")
            if period["status"] != "Open":
                raise HTTPException(409, "Goals cannot be assigned to a closed period.")
            if not (period["start_date"] <= body.due_date.isoformat() <= period["end_date"]):
                raise HTTPException(422, "Goal due date must fall within the review period.")
            employee = connection.execute(
                "SELECT status, active FROM employees WHERE employee_id = ?",
                (body.employee_id,),
            ).fetchone()
            if employee is None or employee["status"] != "Approved" or not employee["active"]:
                raise HTTPException(422, "Goals can be assigned only to active approved employees.")
            cursor = connection.execute(
                """INSERT INTO employee_goals(
                       employee_id, period_id, title, measurable_target, due_date, assigned_by
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    body.employee_id, body.period_id, body.title.strip(),
                    body.measurable_target.strip(), body.due_date.isoformat(),
                    user["username"],
                ),
            )
            goal_id = cursor.lastrowid
            connection.execute(
                """INSERT OR IGNORE INTO performance_reviews(employee_id, period_id)
                   VALUES (?, ?)""",
                (body.employee_id, body.period_id),
            )
            _notify_employee(
                connection, body.employee_id, "performance", "normal",
                "A performance goal was assigned",
                f"{body.title.strip()} · due {body.due_date.isoformat()}",
                "goal", str(goal_id),
            )
            _audit(connection, user["username"], "performance.goal_assigned", body.employee_id, f"goal_id={goal_id}")
        return {"goal_id": goal_id, "status": "not_started"}

    @router.get("/performance/goals")
    def list_performance_goals(
        user: Annotated[dict[str, Any], Depends(current_user)],
        employee_id: str | None = None,
        period_id: int | None = None,
    ) -> list[dict[str, Any]]:
        query = """SELECT g.goal_id, g.employee_id, e.name, g.period_id, p.name AS period_name,
                          g.title, g.measurable_target, g.due_date, g.status,
                          g.progress_note, g.updated_at
                   FROM employee_goals g JOIN employees e ON e.employee_id = g.employee_id
                   JOIN performance_periods p ON p.period_id = g.period_id
                   WHERE 1 = 1"""
        parameters: list[Any] = []
        if user["role"] == "employee":
            own = _employee_for_user(user)
            if employee_id:
                if employee_id != own:
                    with session(settings) as connection:
                        _require_manager_or_admin(connection, user, employee_id)
                query += " AND g.employee_id = ?"
                parameters.append(employee_id)
            else:
                query += """ AND (g.employee_id = ? OR g.employee_id IN
                    (SELECT employee_id FROM employee_reporting WHERE manager_id = ?))"""
                parameters.extend([own, own])
        elif employee_id:
            query += " AND g.employee_id = ?"
            parameters.append(employee_id)
        if period_id is not None:
            query += " AND g.period_id = ?"
            parameters.append(period_id)
        with session(settings) as connection:
            return [dict(row) for row in connection.execute(query + " ORDER BY g.due_date", parameters)]

    @router.patch("/performance/goals/{goal_id}")
    def update_goal_progress(
        goal_id: int,
        body: GoalProgressInput,
        user: Annotated[dict[str, Any], Depends(require_roles("employee"))],
    ) -> dict[str, str]:
        employee_id = _employee_for_user(user)
        with session(settings) as connection:
            goal = connection.execute(
                """SELECT g.employee_id, g.period_id, p.status
                   FROM employee_goals g JOIN performance_periods p ON p.period_id = g.period_id
                   WHERE g.goal_id = ?""",
                (goal_id,),
            ).fetchone()
            if goal is None:
                raise HTTPException(404, "Goal not found.")
            if goal["employee_id"] != employee_id:
                raise HTTPException(403, "Employees can update only their own goals.")
            if goal["status"] != "Open":
                raise HTTPException(409, "Goals in a closed period cannot be changed.")
            connection.execute(
                """UPDATE employee_goals SET status = ?, progress_note = ?,
                   updated_at = CURRENT_TIMESTAMP WHERE goal_id = ?""",
                (body.status, body.progress_note.strip(), goal_id),
            )
            _audit(connection, user["username"], "performance.goal_progress_updated", employee_id, f"goal_id={goal_id}")
        return {"status": body.status, "message": "Goal progress updated."}

    @router.get("/performance/reviews")
    def list_performance_reviews(
        user: Annotated[dict[str, Any], Depends(current_user)],
        period_id: int | None = None,
        employee_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = """SELECT r.review_id, r.employee_id, e.name, e.department,
                          r.period_id, p.name AS period_name, p.status AS period_status,
                          r.self_assessment, r.self_rating, r.self_submitted_at,
                          r.manager_feedback, r.manager_rating, r.manager_username,
                          r.manager_submitted_at, r.admin_note, r.admin_reviewed_at
                   FROM performance_reviews r
                   JOIN employees e ON e.employee_id = r.employee_id
                   JOIN performance_periods p ON p.period_id = r.period_id
                   WHERE 1 = 1"""
        parameters: list[Any] = []
        if user["role"] == "employee":
            own = _employee_for_user(user)
            if employee_id:
                if employee_id != own:
                    with session(settings) as connection:
                        _require_manager_or_admin(connection, user, employee_id)
                query += " AND r.employee_id = ?"
                parameters.append(employee_id)
            else:
                query += """ AND (r.employee_id = ? OR r.employee_id IN
                    (SELECT employee_id FROM employee_reporting WHERE manager_id = ?))"""
                parameters.extend([own, own])
        elif employee_id:
            query += " AND r.employee_id = ?"
            parameters.append(employee_id)
        if period_id is not None:
            query += " AND r.period_id = ?"
            parameters.append(period_id)
        with session(settings) as connection:
            return [
                dict(row) for row in connection.execute(
                    query + " ORDER BY p.start_date DESC, e.name", parameters
                )
            ]

    @router.put("/performance/reviews/{period_id}/self-assessment")
    def submit_self_assessment(
        period_id: int,
        body: SelfAssessmentInput,
        user: Annotated[dict[str, Any], Depends(require_roles("employee"))],
    ) -> dict[str, str]:
        employee_id = _employee_for_user(user)
        with session(settings) as connection:
            period = connection.execute(
                "SELECT status FROM performance_periods WHERE period_id = ?",
                (period_id,),
            ).fetchone()
            if period is None:
                raise HTTPException(404, "Performance period not found.")
            if period["status"] != "Open":
                raise HTTPException(409, "This review period is closed.")
            review = connection.execute(
                """SELECT self_submitted_at FROM performance_reviews
                   WHERE employee_id = ? AND period_id = ?""",
                (employee_id, period_id),
            ).fetchone()
            if review and review["self_submitted_at"]:
                raise HTTPException(409, "This self-assessment has already been submitted.")
            connection.execute(
                """INSERT INTO performance_reviews(
                       employee_id, period_id, self_assessment, self_rating, self_submitted_at
                   ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(employee_id, period_id) DO UPDATE SET
                       self_assessment = excluded.self_assessment,
                       self_rating = excluded.self_rating,
                       self_submitted_at = CURRENT_TIMESTAMP""",
                (
                    employee_id, period_id, body.self_assessment.strip(),
                    body.self_rating,
                ),
            )
            manager = connection.execute(
                "SELECT manager_id FROM employee_reporting WHERE employee_id = ?",
                (employee_id,),
            ).fetchone()
            if manager and manager["manager_id"]:
                _notify_employee(
                    connection, manager["manager_id"], "performance", "normal",
                    "A team self-assessment is ready",
                    f"{_employee_public_name(connection, employee_id)} submitted a self-assessment.",
                    "performance", f"{employee_id}:{period_id}",
                )
            _audit(connection, user["username"], "performance.self_assessment_submitted", employee_id, f"period_id={period_id}")
        return {"message": "Self-assessment submitted and locked for editing."}

    @router.put("/performance/reviews/{review_id}/manager-feedback")
    def submit_manager_feedback(
        review_id: int,
        body: ManagerFeedbackInput,
        user: Annotated[dict[str, Any], Depends(require_roles("employee"))],
    ) -> dict[str, str]:
        with session(settings) as connection:
            review = connection.execute(
                """SELECT r.employee_id, r.self_submitted_at, r.manager_submitted_at,
                          p.status
                   FROM performance_reviews r JOIN performance_periods p ON p.period_id = r.period_id
                   WHERE r.review_id = ?""",
                (review_id,),
            ).fetchone()
            if review is None:
                raise HTTPException(404, "Performance review not found.")
            _require_manager_or_admin(connection, user, review["employee_id"])
            if user["role"] != "admin" and user.get("employee_id") == review["employee_id"]:
                raise HTTPException(403, "Managers cannot review their own performance.")
            if review["self_submitted_at"] is None:
                raise HTTPException(409, "Employee must submit a self-assessment first.")
            if review["manager_submitted_at"] is not None:
                raise HTTPException(409, "Manager feedback is already submitted.")
            if review["status"] != "Open":
                raise HTTPException(409, "This review period is closed.")
            connection.execute(
                """UPDATE performance_reviews SET manager_feedback = ?, manager_rating = ?,
                   manager_username = ?, manager_submitted_at = CURRENT_TIMESTAMP
                   WHERE review_id = ?""",
                (
                    body.manager_feedback.strip(), body.manager_rating,
                    user["username"], review_id,
                ),
            )
            _notify_employee(
                connection, review["employee_id"], "performance", "normal",
                "Manager feedback submitted",
                "Your manager submitted feedback for your performance review.",
                "performance", str(review_id),
            )
            _audit(connection, user["username"], "performance.manager_feedback_submitted", review["employee_id"], f"review_id={review_id}")
        return {"message": "Manager feedback submitted and locked."}

    @router.patch("/admin/performance/reviews/{review_id}")
    def review_performance_review(
        review_id: int,
        body: AdminReviewInput,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> dict[str, str]:
        with session(settings) as connection:
            review = connection.execute(
                """SELECT employee_id, period_id, manager_submitted_at
                   FROM performance_reviews WHERE review_id = ?""",
                (review_id,),
            ).fetchone()
            if review is None:
                raise HTTPException(404, "Performance review not found.")
            connection.execute(
                """UPDATE performance_reviews SET admin_note = ?, admin_username = ?,
                   admin_reviewed_at = CURRENT_TIMESTAMP WHERE review_id = ?""",
                (body.admin_note.strip(), user["username"], review_id),
            )
            _audit(connection, user["username"], "performance.admin_reviewed", review["employee_id"], f"review_id={review_id}")
        return {"message": "Performance review noted."}

    @router.post("/performance/reviews/{period_id}/ensure")
    def ensure_performance_review(
        period_id: int,
        employee_id: str,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> dict[str, str]:
        with session(settings) as connection:
            period = connection.execute(
                "SELECT 1 FROM performance_periods WHERE period_id = ?", (period_id,)
            ).fetchone()
            if period is None:
                raise HTTPException(404, "Performance period not found.")
            employee = connection.execute(
                "SELECT 1 FROM employees WHERE employee_id = ?", (employee_id,)
            ).fetchone()
            if employee is None:
                raise HTTPException(404, "Employee not found.")
            connection.execute(
                "INSERT OR IGNORE INTO performance_reviews(employee_id, period_id) VALUES (?, ?)",
                (employee_id, period_id),
            )
        return {"message": "Review record is available."}

    @router.get("/organization/chart")
    def organization_chart(
        user: Annotated[dict[str, Any], Depends(current_user)],
        search: str = Query(default="", max_length=100),
    ) -> list[dict[str, Any]]:
        query = """SELECT e.employee_id, e.name, e.department, e.designation,
                          r.manager_id, h.employee_id AS department_head_id,
                          head.name AS department_head_name,
                          head.designation AS department_head_designation
                   FROM employees e LEFT JOIN employee_reporting r
                     ON r.employee_id = e.employee_id
                   LEFT JOIN department_heads h
                     ON h.department_name = e.department COLLATE NOCASE
                   LEFT JOIN employees head ON head.employee_id = h.employee_id
                   WHERE e.status = 'Approved' AND e.active = 1"""
        parameters: list[Any] = []
        if user["role"] == "employee":
            own = _employee_for_user(user)
            query += " AND (e.employee_id = ? OR r.manager_id = ?)"
            parameters.extend([own, own])
        if search.strip():
            like = f"%{search.strip()}%"
            query += """ AND (e.employee_id LIKE ? OR e.name LIKE ?
                         OR e.department LIKE ? OR e.designation LIKE ?)"""
            parameters.extend([like, like, like, like])
        with session(settings) as connection:
            items = [
                dict(row) for row in connection.execute(
                    query + " ORDER BY e.department, e.name", parameters
                )
            ]
            visible_employee_ids = {item["employee_id"] for item in items}
            for item in items:
                item["department_head_assigned"] = item["department_head_id"] is not None
                if (
                    user["role"] == "employee"
                    and item["department_head_id"] not in visible_employee_ids
                ):
                    item["department_head_id"] = None
                    item["department_head_name"] = None
                    item["department_head_designation"] = None
        return items

    @router.get("/admin/departments/heads")
    def list_department_heads(
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> list[dict[str, Any]]:
        with session(settings) as connection:
            return [
                dict(row) for row in connection.execute(
                    """SELECT d.name AS department, h.employee_id AS head_employee_id,
                              e.name AS head_name, e.designation AS head_designation,
                              h.assigned_by, h.assigned_at
                       FROM departments d
                       LEFT JOIN department_heads h ON h.department_name = d.name
                       LEFT JOIN employees e ON e.employee_id = h.employee_id
                       ORDER BY d.name"""
                )
            ]

    @router.put("/admin/departments/{department}/head")
    def assign_department_head(
        department: str,
        body: DepartmentHeadInput,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> dict[str, Any]:
        department = department.strip()
        if not department:
            raise HTTPException(422, "Department name must not be blank.")
        head_id = body.employee_id.strip() if body.employee_id else None
        with session(settings) as connection:
            department_row = connection.execute(
                "SELECT name FROM departments WHERE name = ? COLLATE NOCASE",
                (department,),
            ).fetchone()
            if department_row is None:
                raise HTTPException(404, "Department not found.")
            department = department_row["name"]
            previous = connection.execute(
                "SELECT employee_id FROM department_heads WHERE department_name = ?",
                (department,),
            ).fetchone()
            previous_id = previous["employee_id"] if previous else None
            if previous_id == head_id:
                return {"department": department, "head_employee_id": head_id}
            if head_id:
                employee = connection.execute(
                    """SELECT department, status, active FROM employees
                       WHERE employee_id = ?""",
                    (head_id,),
                ).fetchone()
                if employee is None or employee["status"] != "Approved" or not employee["active"]:
                    raise HTTPException(422, "Department head must be an active, approved employee.")
                if employee["department"].casefold() != department.casefold():
                    raise HTTPException(422, "Department head must belong to the selected department.")
                assigned_elsewhere = connection.execute(
                    """SELECT department_name FROM department_heads
                       WHERE employee_id = ? AND department_name <> ? COLLATE NOCASE""",
                    (head_id, department),
                ).fetchone()
                if assigned_elsewhere:
                    raise HTTPException(409, "This employee already heads another department.")
                connection.execute(
                    """INSERT INTO department_heads(
                           department_name, employee_id, assigned_by
                       ) VALUES (?, ?, ?)
                       ON CONFLICT(department_name) DO UPDATE SET
                           employee_id = excluded.employee_id,
                           assigned_by = excluded.assigned_by,
                           assigned_at = CURRENT_TIMESTAMP""",
                    (department, head_id, user["username"]),
                )
            else:
                connection.execute(
                    "DELETE FROM department_heads WHERE department_name = ?",
                    (department,),
                )
            connection.execute(
                """INSERT INTO department_head_history(
                       department_name, previous_head_id, new_head_id, assigned_by
                   ) VALUES (?, ?, ?, ?)""",
                (department, previous_id, head_id, user["username"]),
            )
            _audit(
                connection, user["username"], "department.head_assigned",
                department, f"previous={previous_id or ''};new={head_id or ''}",
            )
        return {"department": department, "head_employee_id": head_id}

    @router.get("/admin/departments/heads/history")
    def department_head_history(
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
        department: str | None = Query(default=None, max_length=80),
    ) -> list[dict[str, Any]]:
        query = """SELECT assignment_id, department_name, previous_head_id,
                          new_head_id, assigned_by, assigned_at
                   FROM department_head_history"""
        parameters: tuple[Any, ...] = ()
        if department:
            query += " WHERE department_name = ? COLLATE NOCASE"
            parameters = (department.strip(),)
        with session(settings) as connection:
            return [
                dict(row) for row in connection.execute(
                    query + " ORDER BY assignment_id DESC LIMIT 200", parameters
                )
            ]

    @router.put("/admin/employees/{employee_id}/manager")
    def assign_manager(
        employee_id: str,
        body: ManagerAssignmentInput,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> dict[str, Any]:
        manager_id = body.manager_id.strip() if body.manager_id else None
        if manager_id == employee_id:
            raise HTTPException(422, "An employee cannot be their own manager.")
        with session(settings) as connection:
            employee = connection.execute(
                """SELECT status, active FROM employees WHERE employee_id = ?""",
                (employee_id,),
            ).fetchone()
            if employee is None:
                raise HTTPException(404, "Employee not found.")
            if employee["status"] != "Approved" or not employee["active"]:
                raise HTTPException(409, "ID cards can be issued only to active approved employees.")
            if manager_id is not None:
                manager = connection.execute(
                    """SELECT status, active FROM employees WHERE employee_id = ?""",
                    (manager_id,),
                ).fetchone()
                if manager is None or manager["status"] != "Approved" or not manager["active"]:
                    raise HTTPException(422, "Manager must be an active approved employee.")
                current = manager_id
                visited: set[str] = set()
                while current:
                    if current == employee_id:
                        raise HTTPException(409, "This assignment would create a reporting cycle.")
                    if current in visited:
                        raise HTTPException(409, "Existing reporting hierarchy contains a cycle.")
                    visited.add(current)
                    ancestor = connection.execute(
                        "SELECT manager_id FROM employee_reporting WHERE employee_id = ?",
                        (current,),
                    ).fetchone()
                    current = ancestor["manager_id"] if ancestor else None
            previous = connection.execute(
                "SELECT manager_id FROM employee_reporting WHERE employee_id = ?",
                (employee_id,),
            ).fetchone()
            previous_id = previous["manager_id"] if previous else None
            connection.execute(
                """INSERT INTO employee_reporting(employee_id, manager_id, assigned_by)
                   VALUES (?, ?, ?)
                   ON CONFLICT(employee_id) DO UPDATE SET
                       manager_id = excluded.manager_id,
                       assigned_by = excluded.assigned_by,
                       assigned_at = CURRENT_TIMESTAMP""",
                (employee_id, manager_id, user["username"]),
            )
            connection.execute(
                """INSERT INTO manager_assignment_history(
                       employee_id, previous_manager_id, new_manager_id, assigned_by
                   ) VALUES (?, ?, ?, ?)""",
                (employee_id, previous_id, manager_id, user["username"]),
            )
            _timeline(
                connection, employee_id, user["username"], "manager.assignment",
                "Reporting manager updated.",
                {"manager_id": previous_id}, {"manager_id": manager_id},
            )
            _audit(connection, user["username"], "manager.assigned", employee_id, f"manager_id={manager_id or ''}")
            if manager_id:
                _notify_employee(
                    connection, employee_id, "organization", "normal",
                    "Reporting manager updated",
                    f"Your reporting manager is now {_employee_public_name(connection, manager_id)}.",
                    "employee", employee_id,
                )
        return {"employee_id": employee_id, "manager_id": manager_id}

    @router.get("/employees/{employee_id}/manager-history")
    def manager_history(
        employee_id: str,
        user: Annotated[dict[str, Any], Depends(current_user)],
    ) -> list[dict[str, Any]]:
        with session(settings) as connection:
            _require_manager_or_admin(connection, user, employee_id)
            if user["role"] == "employee" and user["employee_id"] != employee_id:
                raise HTTPException(403, "Employees can view only their own manager history.")
            return [
                dict(row) for row in connection.execute(
                    """SELECT assignment_id, previous_manager_id, new_manager_id,
                              assigned_by, assigned_at FROM manager_assignment_history
                       WHERE employee_id = ? ORDER BY assignment_id DESC""",
                    (employee_id,),
                )
            ]

    def build_id_card(employee_id: str, verification_token: str, card_id: int) -> bytes:
        with session(settings) as connection:
            employee = connection.execute(
                """SELECT employee_id, name, email, department, designation,
                          joining_date FROM employees WHERE employee_id = ?""",
                (employee_id,),
            ).fetchone()
            photo = connection.execute(
                """SELECT storage_key FROM employee_documents WHERE employee_id = ?
                   AND category = 'photo' AND status = 'Verified' AND is_current = 1
                   ORDER BY document_id DESC LIMIT 1""",
                (employee_id,),
            ).fetchone()
        if employee is None:
            raise HTTPException(404, "Employee not found.")
        width, height = landscape((3.375 * 72, 2.125 * 72))
        output = io.BytesIO()
        pdf = canvas.Canvas(output, pagesize=(width, height))
        pdf.setFillColor(colors.HexColor("#142b49"))
        pdf.rect(0, height - 42, width, 42, fill=1, stroke=0)
        pdf.setFillColor(colors.white)
        pdf.setFont("Helvetica-Bold", 14)
        company_text_x = 16
        if settings.company_logo_path is not None:
            logo_path = settings.company_logo_path
            if not logo_path.is_file():
                raise HTTPException(500, "Configured company logo file is unavailable.")
            try:
                logo = ImageReader(str(logo_path))
                pdf.drawImage(
                    logo, 12, height - 37, width=28, height=28,
                    preserveAspectRatio=True, anchor="c", mask="auto",
                )
            except (OSError, ValueError) as exc:
                raise HTTPException(500, "Configured company logo is not a readable image.") from exc
            company_text_x = 47
        pdf.drawString(company_text_x, height - 27, settings.company_name[:40])
        pdf.setFont("Helvetica", 7)
        pdf.drawRightString(width - 14, height - 26, "EMPLOYEE IDENTIFICATION")

        photo_path = PRIVATE_STORAGE / photo["storage_key"] if photo else None
        if photo_path and photo_path.is_file():
            try:
                pdf.drawImage(
                    ImageReader(str(photo_path)), width - 84, height - 112,
                    width=62, height=70, preserveAspectRatio=True, anchor="c",
                    mask="auto",
                )
            except (OSError, ValueError):
                pass

        pdf.setFillColor(colors.HexColor("#142b49"))
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(16, height - 64, employee["name"][:42])
        pdf.setFont("Helvetica", 9)
        pdf.drawString(16, height - 81, f"Employee ID: {employee['employee_id']}")
        pdf.drawString(16, height - 96, f"{employee['designation'][:28]} · {employee['department'][:24]}")
        pdf.drawString(16, height - 111, f"Joined: {employee['joining_date'] or '—'}")
        qr_url = f"{settings.public_api_url}/api/id-cards/verify/{verification_token}"
        qr = qrcode.make(qr_url)
        qr_buffer = io.BytesIO()
        qr.save(qr_buffer, format="PNG")
        qr_buffer.seek(0)
        pdf.drawImage(ImageReader(qr_buffer), width - 62, 8, width=48, height=48)
        pdf.setFillColor(colors.HexColor("#68788c"))
        pdf.setFont("Helvetica", 6)
        pdf.drawString(16, 13, f"Card #{card_id} · Verify via QR code")
        pdf.showPage()
        pdf.save()
        return output.getvalue()

    @router.post("/admin/id-cards/{employee_id}/issue", status_code=201)
    def issue_id_card(
        employee_id: str,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> dict[str, Any]:
        verification_token = secrets.token_urlsafe(32)
        verification_hash = hashlib.sha256(verification_token.encode("ascii")).hexdigest()
        with session(settings) as connection:
            employee = connection.execute(
                "SELECT 1 FROM employees WHERE employee_id = ?", (employee_id,)
            ).fetchone()
            if employee is None:
                raise HTTPException(404, "Employee not found.")
            connection.execute(
                "UPDATE id_cards SET revoked_at = CURRENT_TIMESTAMP WHERE employee_id = ? AND revoked_at IS NULL",
                (employee_id,),
            )
            cursor = connection.execute(
                """INSERT INTO id_cards(employee_id, verification_hash, issued_by)
                   VALUES (?, ?, ?)""",
                (employee_id, verification_hash, user["username"]),
            )
            card_id = cursor.lastrowid
            _audit(connection, user["username"], "id_card.issued", employee_id, f"card_id={card_id}")
        pdf_key = f"{uuid.uuid4().hex}.pdf"
        PRIVATE_STORAGE.mkdir(parents=True, exist_ok=True)
        pdf_path = PRIVATE_STORAGE / pdf_key
        try:
            pdf_path.write_bytes(build_id_card(employee_id, verification_token, card_id))
            with session(settings) as connection:
                connection.execute(
                    "UPDATE id_cards SET pdf_storage_key = ? WHERE card_id = ?",
                    (pdf_key, card_id),
                )
                _timeline(connection, employee_id, user["username"], "id_card.issued", "Employee ID card issued.", new={"card_id": card_id})
                _notify_employee(
                    connection, employee_id, "identity", "normal",
                    "Employee ID card issued",
                    "Your employee ID card is ready to download.",
                    "id_card", str(card_id),
                )
        except Exception:
            pdf_path.unlink(missing_ok=True)
            with session(settings) as connection:
                connection.execute(
                    "UPDATE id_cards SET revoked_at = CURRENT_TIMESTAMP WHERE card_id = ?",
                    (card_id,),
                )
            raise
        return {"card_id": card_id, "message": "ID card issued. The employee can now download it."}

    @router.get("/id-cards/me")
    def my_id_card(
        user: Annotated[dict[str, Any], Depends(require_roles("employee"))],
    ) -> dict[str, Any]:
        employee_id = _employee_for_user(user)
        with session(settings) as connection:
            card = connection.execute(
                """SELECT card_id, issued_at, pdf_storage_key FROM id_cards
                   WHERE employee_id = ? AND revoked_at IS NULL
                   ORDER BY card_id DESC LIMIT 1""",
                (employee_id,),
            ).fetchone()
        if card is None:
            raise HTTPException(404, "No active ID card has been issued yet.")
        return {
            "card_id": card["card_id"],
            "issued_at": card["issued_at"],
            "download_url": "/api/id-cards/me/pdf",
        }

    @router.get("/id-cards/me/pdf")
    def download_my_id_card(
        user: Annotated[dict[str, Any], Depends(require_roles("employee"))],
    ) -> Response:
        employee_id = _employee_for_user(user)
        with session(settings) as connection:
            card = connection.execute(
                """SELECT card_id, pdf_storage_key FROM id_cards
                   WHERE employee_id = ? AND revoked_at IS NULL
                   ORDER BY card_id DESC LIMIT 1""",
                (employee_id,),
            ).fetchone()
            if card is None:
                raise HTTPException(404, "No active ID card has been issued yet.")
        return _file_response(
            PRIVATE_STORAGE / card["pdf_storage_key"],
            f"employee-card-{card['card_id']}.pdf",
            "application/pdf",
        )

    @router.get("/admin/id-cards/{employee_id}/pdf")
    def download_employee_id_card(
        employee_id: str,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> Response:
        with session(settings) as connection:
            card = connection.execute(
                """SELECT card_id, pdf_storage_key FROM id_cards
                   WHERE employee_id = ? AND revoked_at IS NULL
                   ORDER BY card_id DESC LIMIT 1""",
                (employee_id,),
            ).fetchone()
        if card is None:
            raise HTTPException(404, "No active ID card has been issued.")
        return _file_response(
            PRIVATE_STORAGE / card["pdf_storage_key"],
            f"employee-card-{card['card_id']}.pdf",
            "application/pdf",
        )

    @router.get("/id-cards/verify/{verification_token}")
    def verify_id_card(verification_token: str) -> dict[str, Any]:
        if len(verification_token) < 32 or len(verification_token) > 100:
            raise HTTPException(404, "Card verification failed.")
        token_hash = hashlib.sha256(verification_token.encode("ascii", errors="ignore")).hexdigest()
        with session(settings) as connection:
            row = connection.execute(
                """SELECT e.employee_id, e.name, e.department, e.designation,
                          e.joining_date, e.status, e.active
                   FROM id_cards c JOIN employees e ON e.employee_id = c.employee_id
                   WHERE c.verification_hash = ? AND c.revoked_at IS NULL""",
                (token_hash,),
            ).fetchone()
        if row is None or row["status"] != "Approved" or not row["active"]:
            raise HTTPException(404, "Card verification failed.")
        return {
            "verified": True,
            "company": settings.company_name,
            "employee_id": row["employee_id"],
            "name": row["name"],
            "department": row["department"],
            "designation": row["designation"],
            "joining_date": row["joining_date"],
        }

    @router.get("/reports/employees.xlsx")
    def export_employees_xlsx(
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> StreamingResponse:
        columns = [
            "employee_id", "name", "email", "department", "designation",
            "phone", "joining_date", "salary", "status", "active",
            "onboarding_complete",
        ]
        with session(settings) as connection:
            rows = connection.execute(
                f"SELECT {', '.join(columns)} FROM employees ORDER BY employee_id"
            ).fetchall()
        workbook = Workbook(write_only=True)
        worksheet = workbook.create_sheet("Employees")
        worksheet.append(columns)
        for row in rows:
            worksheet.append([
                "'" + row[key]
                if isinstance(row[key], str)
                and row[key].lstrip(" \t\r\n").startswith(("=", "+", "-", "@"))
                else row[key]
                for key in columns
            ])
        output = io.BytesIO()
        workbook.save(output)
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": 'attachment; filename="employee_records.xlsx"',
                "Cache-Control": "private, no-store",
            },
        )

    @router.get("/admin/reports/{report_name}.{extension}")
    def export_workforce_report(
        report_name: str,
        extension: Literal["csv", "xlsx"],
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> StreamingResponse:
        if start_date and end_date and end_date < start_date:
            raise HTTPException(422, "End date must be on or after start date.")
        filters: list[str] = []
        parameters: list[Any] = []
        date_column = {
            "joining-dates": "joining_date",
            "employee-growth": "joining_date",
        }.get(report_name)
        if date_column and start_date:
            filters.append(f"{date_column} >= ?")
            parameters.append(start_date.isoformat())
        if date_column and end_date:
            filters.append(f"{date_column} <= ?")
            parameters.append(end_date.isoformat())
        if report_name == "leave":
            if start_date:
                filters.append("l.end_date >= ?")
                parameters.append(start_date.isoformat())
            if end_date:
                filters.append("l.start_date <= ?")
                parameters.append(end_date.isoformat())
        if report_name == "attendance":
            if start_date:
                filters.append("a.local_work_date >= ?")
                parameters.append(start_date.isoformat())
            if end_date:
                filters.append("a.local_work_date <= ?")
                parameters.append(end_date.isoformat())
        if report_name == "employee-growth":
            filters.append("joining_date <> ''")
        date_filter = (" WHERE " + " AND ".join(filters)) if filters else ""
        report_queries = {
            "department-employees": (
                """SELECT employee_id, name, department, designation, status,
                          CASE WHEN active = 1 THEN 'Active' ELSE 'Inactive' END AS account_status,
                          joining_date
                   FROM employees ORDER BY department, name""",
                ["employee_id", "name", "department", "designation", "status", "account_status", "joining_date"],
            ),
            "employee-status": (
                """SELECT employee_id, name, department, designation, status,
                          CASE WHEN active = 1 THEN 'Active' ELSE 'Inactive' END AS account_status,
                          joining_date
                   FROM employees ORDER BY active DESC, status, name""",
                ["employee_id", "name", "department", "designation", "status", "account_status", "joining_date"],
            ),
            "joining-dates": (
                """SELECT employee_id, name, department, designation, joining_date,
                          status, CASE WHEN active = 1 THEN 'Active' ELSE 'Inactive' END AS account_status
                   FROM employees""" + date_filter + " ORDER BY joining_date, employee_id",
                ["employee_id", "name", "department", "designation", "joining_date", "status", "account_status"],
            ),
            "approval-status": (
                """SELECT status, COUNT(*) AS employee_count,
                          SUM(CASE WHEN active = 1 THEN 1 ELSE 0 END) AS active_count,
                          SUM(CASE WHEN active = 0 THEN 1 ELSE 0 END) AS inactive_count
                   FROM employees GROUP BY status ORDER BY status""",
                ["status", "employee_count", "active_count", "inactive_count"],
            ),
            "leave": (
                """SELECT l.leave_request_id, l.employee_id, e.name, e.department,
                          l.leave_type, l.start_date, l.end_date, l.requested_days,
                          l.reason, l.status, l.rejection_reason, l.created_at
                   FROM leave_requests l JOIN employees e ON e.employee_id = l.employee_id
                   """ + date_filter + """
                   ORDER BY l.start_date DESC, l.leave_request_id DESC""",
                ["leave_request_id", "employee_id", "name", "department", "leave_type", "start_date", "end_date", "requested_days", "reason", "status", "rejection_reason", "created_at"],
            ),
            "attendance": (
                """SELECT a.attendance_id, a.employee_id, e.name, e.department,
                          a.local_work_date, a.check_in_utc, a.check_out_utc,
                          a.is_late
                   FROM attendance_records a JOIN employees e ON e.employee_id = a.employee_id
                   """ + date_filter + """
                   ORDER BY a.local_work_date DESC, e.name""",
                ["attendance_id", "employee_id", "name", "department", "local_work_date", "check_in_utc", "check_out_utc", "worked_hours", "is_late"],
            ),
            "employee-growth": (
                """SELECT substr(joining_date, 1, 7) AS month,
                          COUNT(*) AS employees_joined
                   FROM employees""" + date_filter + """
                   GROUP BY substr(joining_date, 1, 7)
                   ORDER BY month""",
                ["month", "employees_joined"],
            ),
            "workforce": (
                """SELECT department, COUNT(*) AS total,
                          SUM(CASE WHEN status = 'Approved' AND active = 1 THEN 1 ELSE 0 END) AS active_approved,
                          SUM(CASE WHEN status = 'Pending' THEN 1 ELSE 0 END) AS pending,
                          SUM(CASE WHEN active = 0 THEN 1 ELSE 0 END) AS inactive
                   FROM employees GROUP BY department ORDER BY department""",
                ["department", "total", "active_approved", "pending", "inactive"],
            ),
        }
        selected = report_queries.get(report_name)
        if selected is None:
            raise HTTPException(404, "Report not found.")
        query, columns = selected
        with session(settings) as connection:
            rows = [
                dict(row) for row in connection.execute(query, parameters)
            ]
        if report_name == "attendance":
            for row in rows:
                row["worked_hours"] = _attendance_worked_hours(row)
                row["is_late"] = "Yes" if row["is_late"] else "No"

        if extension == "csv":
            output = io.StringIO(newline="")
            writer = csv.DictWriter(output, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    key: (
                        "'" + str(row[key])
                        if isinstance(row.get(key), str)
                        and row[key].lstrip(" \t\r\n").startswith(("=", "+", "-", "@"))
                        else row.get(key, "")
                    )
                    for key in columns
                })
            output.seek(0)
            return StreamingResponse(
                iter([output.getvalue()]),
                media_type="text/csv; charset=utf-8",
                headers={
                    "Content-Disposition": f'attachment; filename="erms-{report_name}.csv"',
                    "Cache-Control": "private, no-store",
                },
            )

        workbook = Workbook(write_only=True)
        worksheet = workbook.create_sheet(report_name[:31])
        worksheet.append(columns)
        for row in rows:
            worksheet.append([
                "'" + row[key]
                if isinstance(row.get(key), str)
                and row[key].lstrip(" \t\r\n").startswith(("=", "+", "-", "@"))
                else row.get(key)
                for key in columns
            ])
        output = io.BytesIO()
        workbook.save(output)
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="erms-{report_name}.xlsx"',
                "Cache-Control": "private, no-store",
            },
        )

    @router.post("/admin/import/employees/preview")
    async def preview_employee_import(
        file: UploadFile = File(...),
        user: dict[str, Any] = Depends(require_roles("admin")),
    ) -> dict[str, Any]:
        filename = Path(file.filename or "").name
        suffix = Path(filename).suffix.lower()
        if suffix not in {".csv", ".xlsx"}:
            raise HTTPException(422, "Upload a CSV or XLSX file.")
        content = await file.read(settings.max_upload_bytes + 1)
        await file.close()
        if not content:
            raise HTTPException(422, "The import file is empty.")
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(413, "The import file exceeds the configured size limit.")

        columns = [
            "employee_id", "name", "email", "department", "designation",
            "phone", "joining_date", "salary", "password",
        ]
        records: list[tuple[int, dict[str, Any]]] = []
        if suffix == ".csv":
            try:
                reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""))
                headers = reader.fieldnames or []
                normalized = [header.strip().lower() for header in headers if header]
                if len(normalized) != len(headers) or len(normalized) != len(set(normalized)):
                    raise HTTPException(422, "The CSV has blank or duplicate column headers.")
                if set(normalized) != IMPORT_COLUMNS:
                    missing = sorted(IMPORT_COLUMNS - set(normalized))
                    extra = sorted(set(normalized) - IMPORT_COLUMNS)
                    raise HTTPException(
                        422,
                        f"Import columns must match the template. Missing: {missing or 'none'}; unexpected: {extra or 'none'}.",
                    )
                reader.fieldnames = normalized
                for row_number, row in enumerate(reader, start=2):
                    if row and any(str(value or "").strip() for value in row.values()):
                        parsed_row = dict(row)
                        if None in row:
                            parsed_row["__extra_columns"] = True
                        records.append((row_number, parsed_row))
                    if len(records) > 500:
                        raise HTTPException(413, "A single import is limited to 500 non-empty rows.")
            except UnicodeDecodeError as exc:
                raise HTTPException(422, "CSV files must use UTF-8 encoding.") from exc
            except csv.Error as exc:
                raise HTTPException(422, "The CSV file could not be parsed.") from exc
        else:
            try:
                workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            except (BadZipFile, InvalidFileException, ValueError) as exc:
                raise HTTPException(422, "The XLSX file is invalid or unreadable.") from exc
            iterator = workbook.active.iter_rows(values_only=True)
            first = next(iterator, None)
            headers = [
                str(value).strip().lower() if value is not None else ""
                for value in (first or ())
            ]
            while headers and not headers[-1]:
                headers.pop()
            if not headers or any(not header for header in headers) or len(headers) != len(set(headers)):
                workbook.close()
                raise HTTPException(422, "The XLSX has blank or duplicate column headers.")
            if set(headers) != IMPORT_COLUMNS:
                workbook.close()
                missing = sorted(IMPORT_COLUMNS - set(headers))
                extra = sorted(set(headers) - IMPORT_COLUMNS)
                raise HTTPException(
                    422,
                    f"Import columns must match the template. Missing: {missing or 'none'}; unexpected: {extra or 'none'}.",
                )
            for row_number, values in enumerate(iterator, start=2):
                if values and any(value is not None and str(value).strip() for value in values):
                    parsed_row = dict(zip(headers, values))
                    if len(values) > len(headers) and any(
                        value is not None and str(value).strip()
                        for value in values[len(headers):]
                    ):
                        parsed_row["__extra_columns"] = True
                    records.append((row_number, parsed_row))
                if len(records) > 500:
                    workbook.close()
                    raise HTTPException(413, "A single import is limited to 500 non-empty rows.")
            workbook.close()

        with session(settings) as connection:
            existing_ids = {
                row["employee_id"].casefold()
                for row in connection.execute("SELECT employee_id FROM employees")
            }
            existing_emails = {
                row["email"].casefold()
                for row in connection.execute("SELECT email FROM employees")
            }

        preview_rows: list[dict[str, Any]] = []
        saved_rows: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        seen_emails: set[str] = set()
        for row_number, source in records:
            def value(name: str) -> str:
                item = source.get(name, "")
                if isinstance(item, (datetime, date)):
                    return item.isoformat()
                return str(item or "").strip()

            employee_id = value("employee_id")
            name = value("name")
            email = value("email").lower()
            department = value("department")
            designation = value("designation")
            phone = value("phone")
            joining_date = value("joining_date")
            password = value("password")
            row_errors: list[str] = []
            if source.get("__extra_columns"):
                row_errors.append("The row contains values beyond the expected columns.")
            if not re.fullmatch(r"[A-Za-z0-9._-]{2,30}", employee_id):
                row_errors.append("Employee ID must use 2-30 letters, numbers, dots, underscores or hyphens.")
            if not name or len(name) > 100:
                row_errors.append("Name is required and must be at most 100 characters.")
            if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                row_errors.append("Enter a valid email address.")
            if not department or len(department) > 80:
                row_errors.append("Department is required and must be at most 80 characters.")
            if not designation or len(designation) > 80:
                row_errors.append("Designation is required and must be at most 80 characters.")
            if not phone or len(phone) > 30 or not any(character.isdigit() for character in phone):
                row_errors.append("Enter a phone number containing a digit (maximum 30 characters).")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", joining_date):
                row_errors.append("Joining date must use YYYY-MM-DD format.")
            try:
                date.fromisoformat(joining_date)
            except ValueError:
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", joining_date):
                    row_errors.append("Joining date must be a valid calendar date.")
            try:
                salary = float(value("salary").replace(",", ""))
                if not math.isfinite(salary) or salary < 0 or salary > 1_000_000_000:
                    raise ValueError
            except ValueError:
                salary = 0.0
                row_errors.append("Salary must be a non-negative number no greater than 1,000,000,000.")
            if len(password) < 12 or len(password.encode("utf-8")) > 72:
                row_errors.append("Password must contain 12-72 UTF-8 bytes.")
            if employee_id.casefold() in existing_ids or employee_id.casefold() in seen_ids:
                row_errors.append("Employee ID is already in use or duplicated in this file.")
            if email.casefold() in existing_emails or email.casefold() in seen_emails:
                row_errors.append("Email address is already in use or duplicated in this file.")
            seen_ids.add(employee_id.casefold())
            seen_emails.add(email.casefold())
            if row_errors:
                errors.append({
                    "row": row_number,
                    "employee_id": employee_id,
                    "error": " ".join(row_errors),
                })
                continue
            candidate = {
                "row": row_number,
                "employee_id": employee_id,
                "name": name,
                "email": email,
                "department": department,
                "designation": designation,
                "phone": phone,
                "joining_date": joining_date,
                "salary": salary,
                "password_hash": hash_password(password),
            }
            saved_rows.append(candidate)
            preview_rows.append({key: item for key, item in candidate.items() if key != "password_hash"})

        batch_id = uuid.uuid4().hex
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        with session(settings) as connection:
            connection.execute(
                """INSERT INTO employee_import_batches(
                       batch_id, uploaded_by, rows_json, errors_json, expires_at
                   ) VALUES (?, ?, ?, ?, ?)""",
                (
                    batch_id, user["username"],
                    json.dumps(saved_rows, ensure_ascii=True),
                    json.dumps(errors, ensure_ascii=True),
                    expires_at,
                ),
            )
        return {
            "batch_id": batch_id,
            "total_rows": len(records),
            "valid_count": len(preview_rows),
            "error_count": len(errors),
            "rows": preview_rows,
            "errors": errors,
            "expires_at": expires_at,
        }

    @router.post("/admin/import/employees/{batch_id}/commit")
    def commit_employee_import(
        batch_id: str,
        body: ImportDecisionInput,
        request: Request,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> dict[str, Any]:
        if not body.confirm:
            raise HTTPException(422, "Confirm the import before committing it.")
        imported_ids: list[str] = []
        with session(settings) as connection:
            connection.execute("BEGIN IMMEDIATE")
            batch = connection.execute(
                """SELECT rows_json, errors_json, status, expires_at
                   FROM employee_import_batches
                   WHERE batch_id = ? AND uploaded_by = ?""",
                (batch_id, user["username"]),
            ).fetchone()
            if batch is None:
                raise HTTPException(404, "Import preview not found.")
            if batch["status"] != "preview":
                raise HTTPException(409, "This import preview has already been processed.")
            expires_at = datetime.fromisoformat(batch["expires_at"])
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= datetime.now(timezone.utc):
                raise HTTPException(410, "This import preview has expired. Upload the file again.")
            rows = json.loads(batch["rows_json"])
            if json.loads(batch["errors_json"]):
                raise HTTPException(422, "Resolve all row errors and preview a corrected file before importing.")
            if not rows:
                raise HTTPException(422, "The import contains no valid employee records.")
            try:
                for row in rows:
                    connection.execute(
                        """INSERT INTO employees(
                               employee_id, name, email, department, designation,
                               phone, joining_date, salary, status, active,
                               onboarding_complete
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending', 0, 0)""",
                        (
                            row["employee_id"], row["name"], row["email"],
                            row["department"], row["designation"], row["phone"],
                            row["joining_date"], row["salary"],
                        ),
                    )
                    connection.execute(
                        """INSERT INTO users(
                               username, password_hash, role, employee_id, active
                           ) VALUES (?, ?, 'employee', ?, 0)""",
                        (row["employee_id"], row["password_hash"], row["employee_id"]),
                    )
                    _initialize_onboarding_tasks(connection, row["employee_id"])
                    _timeline(
                        connection, row["employee_id"], user["username"],
                        "employee.imported", "Employee record imported and awaiting approval.",
                        new={
                            "name": row["name"],
                            "department": row["department"],
                            "designation": row["designation"],
                            "status": "Pending",
                        },
                    )
                    _notify_admins(
                        connection, "registration", "Employee import awaiting review",
                        f"{row['name']} was imported and is awaiting approval.",
                        "employee", row["employee_id"],
                    )
                    _notify_employee(
                        connection, row["employee_id"], "onboarding", "normal",
                        "Onboarding tasks are ready",
                        "Your onboarding checklist is available after your account is approved.",
                        "onboarding", row["employee_id"],
                    )
                    _audit(connection, user["username"], "employee.imported", row["employee_id"])
                    imported_ids.append(row["employee_id"])
            except sqlite3.IntegrityError as exc:
                raise HTTPException(
                    409,
                    "An Employee ID or email became unavailable after preview. No employees were imported.",
                ) from exc
            summary_rows = [
                {"row": row["row"], "employee_id": row["employee_id"], "status": "Imported"}
                for row in rows
            ]
            connection.execute(
                """UPDATE employee_import_batches SET status = 'committed', rows_json = ?
                   WHERE batch_id = ?""",
                (json.dumps(summary_rows), batch_id),
            )
            _audit(
                connection, user["username"], "employee.import_batch_committed",
                batch_id, f"imported_count={len(imported_ids)}",
            )

        employee_map: EmployeeHashMap[str, dict[str, Any]] = request.app.state.employee_map
        with session(settings) as connection:
            for employee_id in imported_ids:
                row = connection.execute(
                    "SELECT * FROM employees WHERE employee_id = ?", (employee_id,)
                ).fetchone()
                if row is not None:
                    employee_map.put(employee_id, dict(row))
        return {
            "batch_id": batch_id,
            "imported_count": len(imported_ids),
            "employee_ids": imported_ids,
            "status": "committed",
        }

    @router.get("/admin/import/employees/{batch_id}/summary")
    def employee_import_summary(
        batch_id: str,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> StreamingResponse:
        with session(settings) as connection:
            batch = connection.execute(
                """SELECT rows_json, errors_json, status FROM employee_import_batches
                   WHERE batch_id = ? AND uploaded_by = ?""",
                (batch_id, user["username"]),
            ).fetchone()
        if batch is None:
            raise HTTPException(404, "Import summary not found.")
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=["row", "employee_id", "status", "error"])
        writer.writeheader()
        for row in json.loads(batch["rows_json"]):
            writer.writerow({
                "row": row["row"],
                "employee_id": row["employee_id"],
                "status": row.get("status", batch["status"]),
                "error": "",
            })
        for row in json.loads(batch["errors_json"]):
            writer.writerow({
                "row": row["row"],
                "employee_id": row.get("employee_id", ""),
                "status": "Error",
                "error": row["error"],
            })
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="employee-import-{batch_id}.csv"',
                "Cache-Control": "private, no-store",
            },
        )

    @router.post("/hash-map-lab/benchmark")
    def hash_map_lab_benchmark(
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
        size: int = Query(...),
        repetitions: int = Query(default=3, ge=1, le=10),
    ) -> dict[str, Any]:
        if size not in {100, 1000, 10000}:
            raise HTTPException(422, "Dataset size must be 100, 1,000 or 10,000.")
        keys = [f"LAB-{index:05d}" for index in range(size)]
        search_count = min(size, 100)
        search_keys = [
            keys[index * (size - 1) // (search_count - 1)]
            for index in range(search_count)
        ] if search_count > 1 else keys
        names = (
            "Custom Separate Chaining",
            "Custom Linear Probing",
            "Linear Search baseline",
        )
        results: dict[str, list[dict[str, float]]] = {name: [] for name in names}
        results_match = True
        for _ in range(repetitions):
            records = [
                {"employee_id": key, "name": f"Lab Employee {index:05d}"}
                for index, key in enumerate(keys)
            ]
            chain: EmployeeHashMap[str, dict[str, str]] = EmployeeHashMap()
            started = time.perf_counter_ns()
            for record in records:
                chain.put(record["employee_id"], record)
            chain_insert_ms = (time.perf_counter_ns() - started) / 1_000_000
            chain_insert_probes = chain.probes
            chain.probes = 0
            started = time.perf_counter_ns()
            chain_found = [chain.get(key) for key in search_keys]
            chain_search_ms = (time.perf_counter_ns() - started) / 1_000_000
            chain_search_probes = chain.probes

            probing: LinearProbingMap[str, dict[str, str]] = LinearProbingMap()
            started = time.perf_counter_ns()
            for record in records:
                probing.put(record["employee_id"], record)
            probing_insert_ms = (time.perf_counter_ns() - started) / 1_000_000
            probing_insert_probes = probing.probes
            probing.probes = 0
            started = time.perf_counter_ns()
            probing_found = [probing.get(key) for key in search_keys]
            probing_search_ms = (time.perf_counter_ns() - started) / 1_000_000
            probing_search_probes = probing.probes

            baseline: list[dict[str, str]] = []
            started = time.perf_counter_ns()
            for record in records:
                baseline.append(record)
            baseline_insert_ms = (time.perf_counter_ns() - started) / 1_000_000
            comparisons = 0
            baseline_found: list[dict[str, str] | None] = []
            started = time.perf_counter_ns()
            for key in search_keys:
                found = None
                for record in baseline:
                    comparisons += 1
                    if record["employee_id"] == key:
                        found = record
                        break
                baseline_found.append(found)
            baseline_search_ms = (time.perf_counter_ns() - started) / 1_000_000
            results_match = results_match and chain_found == probing_found == baseline_found

            for name, measured in (
                (
                    names[0],
                    {
                        "insert_ms": chain_insert_ms,
                        "search_ms": chain_search_ms,
                        "collisions": chain.collisions,
                        "insert_probes": chain_insert_probes,
                        "search_probes": chain_search_probes,
                        "load_factor": chain.size / chain.capacity,
                        "capacity": chain.capacity,
                    },
                ),
                (
                    names[1],
                    {
                        "insert_ms": probing_insert_ms,
                        "search_ms": probing_search_ms,
                        "collisions": probing.collisions,
                        "insert_probes": probing_insert_probes,
                        "search_probes": probing_search_probes,
                        "load_factor": probing.size / probing.capacity,
                        "capacity": probing.capacity,
                    },
                ),
                (
                    names[2],
                    {
                        "insert_ms": baseline_insert_ms,
                        "search_ms": baseline_search_ms,
                        "collisions": 0,
                        "insert_probes": size,
                        "search_probes": comparisons,
                        "load_factor": 0,
                        "capacity": size,
                    },
                ),
            ):
                results[name].append(measured)

        methods = []
        for name in names:
            measured = results[name]
            mean = lambda field: sum(item[field] for item in measured) / repetitions
            methods.append({
                "name": name,
                "insert_ms_average": round(mean("insert_ms"), 6),
                "search_ms_average": round(mean("search_ms"), 6),
                "collisions": round(mean("collisions"), 2),
                "insertion_probes": round(mean("insert_probes"), 2),
                "search_probes": round(mean("search_probes"), 2),
                "probes": round(mean("insert_probes") + mean("search_probes"), 2),
                "load_factor": round(mean("load_factor"), 4),
                "capacity": round(mean("capacity")),
            })
        return {
            "dataset_size": size,
            "repetitions": repetitions,
            "search_count_per_repetition": len(search_keys),
            "methods": methods,
            "results_match": results_match,
            "timings_are_measured": True,
            "note": "Times are measured in this process; complexity descriptions are theoretical.",
        }

    @router.post("/hash-map-lab/demo")
    def hash_map_lab_demo(
        body: HashMapLabInput,
        request: Request,
        user: Annotated[dict[str, Any], Depends(require_roles("admin"))],
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9._-]{2,30}", body.employee_id):
            raise HTTPException(422, "Use a 2-30 character demonstration key.")
        maps = getattr(request.app.state, "hash_map_lab", None)
        if maps is None:
            chain: EmployeeHashMap[str, dict[str, str]] = EmployeeHashMap()
            probing: LinearProbingMap[str, dict[str, str]] = LinearProbingMap()
            for index in range(1, 6):
                key = f"LAB-{index:03d}"
                value = {"employee_id": key, "name": f"Lab Employee {index}"}
                chain.put(key, value)
                probing.put(key, value)
            maps = {"chain": chain, "probing": probing}
            request.app.state.hash_map_lab = maps
        chain = maps["chain"]
        probing = maps["probing"]
        previous = chain.get(body.employee_id)
        if body.action == "insert":
            value = {"employee_id": body.employee_id, "name": "Demonstration record"}
            chain.put(body.employee_id, value)
            probing.put(body.employee_id, value)
            operation_result: dict[str, Any] = {
                "inserted": True,
                "updated": previous is not None,
            }
        elif body.action == "delete":
            chain_deleted = chain.delete(body.employee_id)
            probing_deleted = probing.delete(body.employee_id)
            if chain_deleted != probing_deleted:
                raise HTTPException(500, "Demonstration maps became inconsistent.")
            operation_result = {"deleted": chain_deleted, "existed": previous is not None}
        else:
            operation_result = {"found": previous is not None, "record": previous}

        chain_keys = {key for bucket in chain.bucket_snapshot() for key, _ in bucket}
        probing_keys = {
            slot["key"] for slot in probing.slot_snapshot() if slot["state"] == "occupied"
        }
        if chain_keys != probing_keys:
            raise HTTPException(500, "Demonstration maps returned different key sets.")
        return {
            "action": body.action,
            "employee_id": body.employee_id,
            "result": operation_result,
            "results_match": True,
            "persistent_employee_data_changed": False,
            "separate_chaining": {
                "stats": chain.stats(),
                "buckets": [
                    {"index": index, "keys": [key for key, _ in bucket]}
                    for index, bucket in enumerate(chain.bucket_snapshot())
                ],
            },
            "linear_probing": {
                "stats": {
                    "size": probing.size,
                    "capacity": probing.capacity,
                    "load_factor": round(probing.size / probing.capacity, 3),
                    "collisions": probing.collisions,
                    "probes": probing.probes,
                    "resizes": probing.resizes,
                },
                "slots": probing.slot_snapshot(),
            },
        }

    return router
