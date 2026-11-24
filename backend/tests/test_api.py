from datetime import datetime, timedelta, timezone
from io import BytesIO
import sqlite3
from pathlib import Path
from time import sleep

from openpyxl import Workbook, load_workbook
from fastapi.testclient import TestClient

from app.config import Settings
from app.database.db import initialize, session
from app.main import create_app
from app.security import hash_password


def create_test_client(
    tmp_path: Path, company_logo_path: Path | None = None
) -> TestClient:
    settings = Settings(
        jwt_secret="test-secret-that-is-long-enough-for-unit-tests",
        database_path=tmp_path / "test.sqlite3",
        cors_origins=("http://localhost:5173",),
        token_minutes=30,
        login_max_attempts=3,
        login_window_seconds=60,
        company_logo_path=company_logo_path,
    )
    initialize(settings)
    with session(settings) as connection:
        connection.execute(
            "INSERT INTO users(username, password_hash, role) VALUES (?, ?, ?)",
            ("hr-admin", hash_password("ValidTestPassword123!"), "admin"),
        )
    return TestClient(create_app(settings))


def login(
    client: TestClient,
    username: str = "hr-admin",
    password: str = "ValidTestPassword123!",
    login_type: str = "admin",
) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"login_type": login_type, "username": username, "password": password},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def employee_payload(employee_id: str = "EMP-2001") -> dict[str, object]:
    return {
        "employee_id": employee_id,
        "name": "Taylor Example",
        "email": f"{employee_id.lower()}@example.test",
        "department": "Engineering",
        "designation": "Software Engineer",
        "phone": "555-0101",
        "joining_date": "2025-01-15",
        "salary": 85000,
        "password": "EmployeePassword123!",
    }


def registration_payload(employee_id: str = "EMP-3001") -> dict[str, str]:
    return {
        "name": "Morgan Registration",
        "employee_id": employee_id,
        "email": f"{employee_id.lower()}@example.test",
        "phone": "+1 555-0101",
        "department": "Engineering",
        "designation": "Software Engineer",
        "joining_date": "2025-02-01",
        "password": "RegistrationPassword123!",
        "confirm_password": "RegistrationPassword123!",
    }


def test_role_authorization_and_employee_lifecycle(tmp_path: Path):
    with create_test_client(tmp_path) as client:
        admin_headers = login(client)
        create = client.post(
            "/api/employees", json=employee_payload(), headers=admin_headers
        )
        assert create.status_code == 201
        assert create.json()["employee"]["status"] == "Pending"
        assert create.json()["employee"]["active"] == 0

        pending_login = client.post(
            "/api/auth/login",
            json={
                "login_type": "employee",
                "username": "EMP-2001",
                "password": "EmployeePassword123!",
            },
        )
        assert pending_login.status_code == 403
        assert "pending Admin approval" in pending_login.json()["detail"]

        approve = client.patch(
            "/api/employees/EMP-2001/status",
            json={"status": "Approved"},
            headers=admin_headers,
        )
        assert approve.status_code == 200
        employee_headers = login(
            client, "EMP-2001", "EmployeePassword123!", "employee"
        )

        self_view = client.get("/api/employees/EMP-2001", headers=employee_headers)
        assert self_view.status_code == 200
        assert "salary" not in self_view.json()
        other_view = client.get("/api/employees/EMP-2002", headers=employee_headers)
        assert other_view.status_code == 403

        assert client.get("/api/admin/audit-logs", headers=admin_headers).json()
        export = client.get("/api/reports/employees.csv", headers=admin_headers)
        assert export.status_code == 200
        assert "EMP-2001" in export.text


def test_profile_request_is_not_applied_until_admin_approval(tmp_path: Path):
    with create_test_client(tmp_path) as client:
        admin_headers = login(client)
        assert client.post(
            "/api/employees", json=employee_payload(), headers=admin_headers
        ).status_code == 201
        assert client.patch(
            "/api/employees/EMP-2001/status",
            json={"status": "Approved"},
            headers=admin_headers,
        ).status_code == 200
        employee_headers = login(
            client, "EMP-2001", "EmployeePassword123!", "employee"
        )
        request = client.post(
            "/api/me/profile-requests",
            json={"name": "Taylor Updated"},
            headers=employee_headers,
        )
        assert request.status_code == 201
        assert client.get(
            "/api/employees/EMP-2001", headers=employee_headers
        ).json()["name"] == "Taylor Example"

        review = client.patch(
            f"/api/admin/profile-requests/{request.json()['request_id']}",
            json={"status": "Approved"},
            headers=admin_headers,
        )
        assert review.status_code == 200
        assert client.get(
            "/api/employees/EMP-2001", headers=employee_headers
        ).json()["name"] == "Taylor Updated"


def test_employee_self_registration_requires_admin_approval(tmp_path: Path):
    with create_test_client(tmp_path) as client:
        registration = client.post(
            "/api/auth/register", json=registration_payload()
        )
        assert registration.status_code == 201
        assert registration.json()["status"] == "Pending"
        assert "waiting for Admin approval" in registration.json()["message"]

        pending_login = client.post(
            "/api/auth/login",
            json={
                "login_type": "employee",
                "username": "EMP-3001",
                "password": "RegistrationPassword123!",
            },
        )
        assert pending_login.status_code == 403
        assert "pending Admin approval" in pending_login.json()["detail"]
        assert client.get("/api/auth/me").status_code == 401
        assert client.get("/api/admin/employee-registrations").status_code == 401

        admin_headers = login(client)
        pending = client.get(
            "/api/admin/employee-registrations", headers=admin_headers
        )
        assert pending.status_code == 200
        assert pending.json()["total"] == 1
        submitted = pending.json()["items"][0]
        assert submitted["employee_id"] == "EMP-3001"
        assert submitted["email"] == "emp-3001@example.test"
        assert submitted["phone"] == "+1 555-0101"
        assert submitted["department"] == "Engineering"
        assert submitted["designation"] == "Software Engineer"
        assert submitted["joining_date"] == "2025-02-01"
        assert submitted["created_at"]

        approved = client.patch(
            "/api/employees/EMP-3001/status",
            json={"status": "Approved"},
            headers=admin_headers,
        )
        assert approved.status_code == 200
        employee_login = client.post(
            "/api/auth/login",
            json={
                "login_type": "employee",
                "username": "EMP-3001",
                "password": "RegistrationPassword123!",
            },
        )
        assert employee_login.status_code == 200
        employee_headers = {
            "Authorization": f"Bearer {employee_login.json()['access_token']}"
        }
        assert client.get("/api/auth/me", headers=employee_headers).json()["role"] == "employee"

        self_approval = client.patch(
            "/api/employees/EMP-3001/status",
            json={"status": "Rejected"},
            headers=employee_headers,
        )
        assert self_approval.status_code == 403


def test_employee_registration_rejects_duplicate_ids_emails_and_bad_input(tmp_path: Path):
    with create_test_client(tmp_path) as client:
        first = client.post("/api/auth/register", json=registration_payload())
        assert first.status_code == 201

        duplicate_id = client.post(
            "/api/auth/register",
            json={
                **registration_payload("EMP-3001"),
                "email": "different@example.test",
            },
        )
        assert duplicate_id.status_code == 409

        duplicate_email = client.post(
            "/api/auth/register",
            json={
                **registration_payload("EMP-3002"),
                "email": "EMP-3001@example.test",
            },
        )
        assert duplicate_email.status_code == 409

        mismatched_password = client.post(
            "/api/auth/register",
            json={
                **registration_payload("EMP-3003"),
                "confirm_password": "DifferentPassword123!",
            },
        )
        assert mismatched_password.status_code == 422

        invalid_date = client.post(
            "/api/auth/register",
            json={**registration_payload("EMP-3004"), "joining_date": "2025-02-30"},
        )
        assert invalid_date.status_code == 422


def test_rejected_employee_cannot_login_and_revoked_token_stays_invalid(tmp_path: Path):
    with create_test_client(tmp_path) as client:
        admin_headers = login(client)
        client.post("/api/auth/register", json=registration_payload())
        assert client.patch(
            "/api/employees/EMP-3001/status",
            json={"status": "Approved"},
            headers=admin_headers,
        ).status_code == 200
        employee_headers = login(
            client,
            "EMP-3001",
            "RegistrationPassword123!",
            "employee",
        )

        rejected = client.patch(
            "/api/employees/EMP-3001/status",
            json={"status": "Rejected"},
            headers=admin_headers,
        )
        assert rejected.status_code == 200
        assert client.get("/api/auth/me", headers=employee_headers).status_code == 401
        rejected_login = client.post(
            "/api/auth/login",
            json={
                "login_type": "employee",
                "username": "EMP-3001",
                "password": "RegistrationPassword123!",
            },
        )
        assert rejected_login.status_code == 403
        assert "rejected" in rejected_login.json()["detail"]


def test_api_requires_token_and_rejects_role_parameter_spoofing(tmp_path: Path):
    with create_test_client(tmp_path) as client:
        assert client.get("/health/ready").json() == {"status": "ready"}
        assert client.get("/api/employees").status_code == 401
        assert client.get("/api/employees?role=admin").status_code == 401
        wrong_login_flow = client.post(
            "/api/auth/login",
            json={
                "login_type": "employee",
                "username": "hr-admin",
                "password": "ValidTestPassword123!",
            },
        )
        assert wrong_login_flow.status_code == 401
        invalid = client.post(
            "/api/auth/login",
            json={
                "login_type": "admin",
                "username": "hr-admin",
                "password": "IncorrectPassword123!",
            },
        )
        assert invalid.status_code == 401
        assert invalid.json()["detail"] == "Invalid username or password."
        blank_username = client.post(
            "/api/auth/login",
            json={
                "login_type": "admin",
                "username": "   ",
                "password": "ValidTestPassword123!",
            },
        )
        assert blank_username.status_code == 422
        preflight = client.options(
            "/api/auth/login",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_login_throttle_and_password_change_revokes_old_token(tmp_path: Path):
    with create_test_client(tmp_path) as client:
        old_headers = login(client)
        changed = client.post(
            "/api/auth/change-password",
            json={
                "current_password": "ValidTestPassword123!",
                "new_password": "ChangedTestPassword456!",
            },
            headers=old_headers,
        )
        assert changed.status_code == 200
        assert changed.json()["token_type"] == "bearer"

        assert client.get(
            "/api/admin/audit-logs", headers=old_headers
        ).status_code == 401
        new_headers = {
            "Authorization": f"Bearer {changed.json()['access_token']}"
        }
        assert client.get(
            "/api/admin/audit-logs", headers=new_headers
        ).status_code == 200

        for _ in range(3):
            invalid = client.post(
                "/api/auth/login",
                json={
                    "login_type": "admin",
                    "username": "hr-admin",
                    "password": "WrongPassword123!",
                },
            )
            assert invalid.status_code == 401
        limited = client.post(
            "/api/auth/login",
            json={
                "login_type": "admin",
                "username": "hr-admin",
                "password": "ChangedTestPassword456!",
            },
        )
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) > 0


def test_admin_update_deactivation_delete_and_live_benchmark(tmp_path: Path):
    with create_test_client(tmp_path) as client:
        admin_headers = login(client)
        assert client.post(
            "/api/employees", json=employee_payload(), headers=admin_headers
        ).status_code == 201
        assert client.patch(
            "/api/employees/EMP-2001/status",
            json={"status": "Approved"},
            headers=admin_headers,
        ).status_code == 200

        updated = {
            "name": "Taylor Revised",
            "email": "revised@example.test",
            "department": "Engineering",
            "designation": "Senior Engineer",
            "phone": "555-0102",
            "joining_date": "2025-01-15",
            "salary": 92000,
        }
        response = client.put(
            "/api/employees/EMP-2001", json=updated, headers=admin_headers
        )
        assert response.status_code == 200
        assert client.get(
            "/api/employees/EMP-2001", headers=admin_headers
        ).json()["name"] == "Taylor Revised"

        benchmark = client.get(
            "/api/benchmark?repetitions=3", headers=admin_headers
        ).json()
        assert benchmark["record_count"] == 1
        assert {entry["method"] for entry in benchmark["results"]} == {
            "Separate Chaining",
            "Linear Probing",
            "Linear Search",
        }
        assert all(entry["time_ms"] >= 0 for entry in benchmark["results"])
        hash_stats = client.get(
            "/api/hash-table/stats", headers=admin_headers
        ).json()
        assert hash_stats["size"] == 1
        assert hash_stats["capacity"] >= hash_stats["size"]

        employee_headers = login(
            client, "EMP-2001", "EmployeePassword123!", "employee"
        )
        assert client.patch(
            "/api/employees/EMP-2001/active?active=false",
            headers=admin_headers,
        ).status_code == 200
        assert client.get(
            "/api/auth/me", headers=employee_headers
        ).status_code == 401
        inactive_login = client.post(
            "/api/auth/login",
            json={
                "login_type": "employee",
                "username": "EMP-2001",
                "password": "EmployeePassword123!",
            },
        )
        assert inactive_login.status_code == 403
        assert client.patch(
            "/api/employees/EMP-2001/active?active=true",
            headers=admin_headers,
        ).status_code == 200
        assert client.get(
            "/api/auth/me", headers=employee_headers
        ).status_code == 401
        assert client.delete(
            "/api/employees/EMP-2001", headers=admin_headers
        ).status_code == 204
        assert client.get(
            "/api/employees/EMP-2001", headers=admin_headers
        ).status_code == 404


def test_department_cannot_be_removed_while_assigned(tmp_path: Path):
    with create_test_client(tmp_path) as client:
        admin_headers = login(client)
        client.post("/api/employees", json=employee_payload(), headers=admin_headers)
        in_use = client.delete(
            "/api/departments/Engineering", headers=admin_headers
        )
        assert in_use.status_code == 409
        unused = client.delete(
            "/api/departments/Operations", headers=admin_headers
        )
        assert unused.status_code == 204
        renamed = client.patch(
            "/api/departments/Engineering",
            json={"name": "Product Engineering"},
            headers=admin_headers,
        )
        assert renamed.status_code == 200
        employee = client.get(
            "/api/employees/EMP-2001", headers=admin_headers
        )
        assert employee.json()["department"] == "Product Engineering"
        assert client.delete(
            "/api/departments/Product%20Engineering", headers=admin_headers
        ).status_code == 409


def test_employee_input_rejects_whitespace_only_required_fields(tmp_path: Path):
    with create_test_client(tmp_path) as client:
        response = client.post(
            "/api/employees",
            json={**employee_payload(), "department": "   "},
            headers=login(client),
        )
        assert response.status_code == 422


def test_notifications_are_scoped_persistent_and_markable(tmp_path: Path):
    with create_test_client(tmp_path) as client:
        admin_headers = login(client)
        assert client.post(
            "/api/auth/register", json=registration_payload()
        ).status_code == 201

        admin_notifications = client.get(
            "/api/notifications", headers=admin_headers
        ).json()
        assert admin_notifications["unread_count"] == 1
        registration_notice = admin_notifications["items"][0]
        assert registration_notice["category"] == "registration"
        assert registration_notice["related_id"] == "EMP-3001"
        assert registration_notice["is_read"] == 0

        assert client.patch(
            f"/api/notifications/{registration_notice['notification_id']}",
            json={"read": True},
            headers=admin_headers,
        ).status_code == 200
        assert client.get(
            "/api/notifications", headers=admin_headers
        ).json()["unread_count"] == 0
        assert client.patch(
            "/api/notifications/read-all", headers=admin_headers
        ).json()["marked_read"] == 0

        assert client.patch(
            "/api/employees/EMP-3001/status",
            json={"status": "Approved"},
            headers=admin_headers,
        ).status_code == 200
        employee_headers = login(
            client,
            "EMP-3001",
            "RegistrationPassword123!",
            "employee",
        )
        employee_notifications = client.get(
            "/api/notifications", headers=employee_headers
        ).json()
        assert employee_notifications["unread_count"] >= 2
        assert all(
            item["category"] in {"onboarding", "registration"}
            for item in employee_notifications["items"]
        )
        employee_notice_id = employee_notifications["items"][0]["notification_id"]
        assert client.patch(
            f"/api/notifications/{employee_notice_id}",
            json={"read": True},
            headers=admin_headers,
        ).status_code == 404
        assert client.patch(
            "/api/notifications/read-all", headers=employee_headers
        ).json()["marked_read"] >= 2
        assert client.get(
            "/api/notifications", headers=employee_headers
        ).json()["unread_count"] == 0


def test_onboarding_progress_and_review_flow(tmp_path: Path):
    with create_test_client(tmp_path) as client:
        admin_headers = login(client)
        assert client.post(
            "/api/auth/register", json=registration_payload()
        ).status_code == 201
        assert client.patch(
            "/api/employees/EMP-3001/status",
            json={"status": "Approved"},
            headers=admin_headers,
        ).status_code == 200
        employee_headers = login(
            client,
            "EMP-3001",
            "RegistrationPassword123!",
            "employee",
        )

        onboarding = client.get(
            "/api/onboarding/me", headers=employee_headers
        ).json()
        assert onboarding["required_tasks"] == 6
        assert onboarding["completion_percentage"] == 0
        saved = client.put(
            "/api/onboarding/tasks/personal_info",
            json={"response": {"confirmed": True}, "submit": True},
            headers=employee_headers,
        )
        assert saved.status_code == 200
        assert saved.json()["status"] == "submitted"
        progress = client.get(
            "/api/admin/onboarding", headers=admin_headers
        ).json()
        assert progress["items"][0]["tasks"][0]["status"] == "submitted"
        reviewed = client.patch(
            "/api/admin/onboarding/EMP-3001/personal_info",
            json={"status": "approved", "note": "Details checked."},
            headers=admin_headers,
        )
        assert reviewed.status_code == 200
        updated = client.get(
            "/api/onboarding/me", headers=employee_headers
        ).json()
        assert updated["completed_tasks"] == 1
        assert updated["completion_percentage"] == 17
        assert client.put(
            "/api/onboarding/tasks/personal_info",
            json={"response": {"confirmed": True}, "submit": True},
            headers=employee_headers,
        ).status_code == 409


def test_document_upload_review_versioning_and_download_authorization(
    tmp_path: Path, monkeypatch
):
    from app import advanced_api

    monkeypatch.setattr(advanced_api, "PRIVATE_STORAGE", tmp_path / "private")
    with create_test_client(tmp_path) as client:
        admin_headers = login(client)
        for employee_id in ("EMP-3001", "EMP-3002"):
            assert client.post(
                "/api/auth/register",
                json={
                    **registration_payload(employee_id),
                    "email": f"{employee_id.lower()}@example.test",
                },
            ).status_code == 201
            assert client.patch(
                f"/api/employees/{employee_id}/status",
                json={"status": "Approved"},
                headers=admin_headers,
            ).status_code == 200
        employee_headers = login(
            client,
            "EMP-3001",
            "RegistrationPassword123!",
            "employee",
        )
        other_headers = login(
            client,
            "EMP-3002",
            "RegistrationPassword123!",
            "employee",
        )
        uploaded = client.post(
            "/api/documents",
            data={"category": "resume"},
            files={"file": ("resume.pdf", b"%PDF-1.7\nresume", "application/pdf")},
            headers=employee_headers,
        )
        assert uploaded.status_code == 201
        document_id = uploaded.json()["document_id"]
        assert uploaded.json()["status"] == "Pending"
        assert client.get(
            f"/api/documents/{document_id}/download", headers=other_headers
        ).status_code == 403
        assert client.get(
            f"/api/documents/{document_id}/download", headers=employee_headers
        ).content.startswith(b"%PDF-")
        verified = client.patch(
            f"/api/admin/documents/{document_id}",
            json={"status": "Verified", "note": "Reviewed."},
            headers=admin_headers,
        )
        assert verified.status_code == 200

        replacement = client.post(
            "/api/documents",
            data={
                "category": "resume",
                "replaces_document_id": str(document_id),
            },
            files={"file": ("resume-v2.pdf", b"%PDF-1.7\nupdated", "application/pdf")},
            headers=employee_headers,
        )
        assert replacement.status_code == 201
        assert replacement.json()["version"] == 2
        mismatched_replacement = client.post(
            "/api/documents",
            data={
                "category": "identity",
                "replaces_document_id": str(replacement.json()["document_id"]),
            },
            files={"file": ("identity.pdf", b"%PDF-1.7\nidentity", "application/pdf")},
            headers=employee_headers,
        )
        assert mismatched_replacement.status_code == 422
        history = client.get(
            "/api/documents?include_history=true", headers=employee_headers
        ).json()
        assert len(history) == 2
        assert sum(item["is_current"] for item in history) == 1


def test_leave_policy_overlap_and_attendance_validation(tmp_path: Path):
    from datetime import date

    with create_test_client(tmp_path) as client:
        admin_headers = login(client)
        assert client.post(
            "/api/auth/register", json=registration_payload()
        ).status_code == 201
        assert client.patch(
            "/api/employees/EMP-3001/status",
            json={"status": "Approved"},
            headers=admin_headers,
        ).status_code == 200
        employee_headers = login(
            client,
            "EMP-3001",
            "RegistrationPassword123!",
            "employee",
        )
        policy = client.put(
            "/api/admin/leave/policies/Casual",
            json={"annual_allowance": 10, "count_weekends": False, "active": True},
            headers=admin_headers,
        )
        assert policy.status_code == 200
        leave = client.post(
            "/api/leave/requests",
            json={
                "leave_type": "Casual",
                "start_date": "2030-01-07",
                "end_date": "2030-01-07",
                "reason": "Personal appointment",
            },
            headers=employee_headers,
        )
        assert leave.status_code == 201
        assert leave.json()["requested_days"] == 1
        overlap = client.post(
            "/api/leave/requests",
            json={
                "leave_type": "Casual",
                "start_date": "2030-01-07",
                "end_date": "2030-01-08",
                "reason": "Second request",
            },
            headers=employee_headers,
        )
        assert overlap.status_code == 409
        decision = client.patch(
            f"/api/leave/requests/{leave.json()['leave_request_id']}",
            json={"status": "Approved"},
            headers=admin_headers,
        )
        assert decision.status_code == 200
        balance = client.get(
            "/api/leave/balances?year=2030", headers=employee_headers
        ).json()
        assert balance["balances"][0]["approved_days"] == 1
        calendar = client.get(
            "/api/leave/calendar?start_date=2030-01-01&end_date=2030-01-31",
            headers=employee_headers,
        )
        assert calendar.status_code == 200
        assert calendar.json()[0]["requested_days"] == 1

        check_in = client.post(
            "/api/attendance/check-in", headers=employee_headers
        )
        assert check_in.status_code == 201
        assert client.post(
            "/api/attendance/check-in", headers=employee_headers
        ).status_code == 409
        check_out = client.patch(
            "/api/attendance/check-out", headers=employee_headers
        )
        assert check_out.status_code == 200
        assert client.patch(
            "/api/attendance/check-out", headers=employee_headers
        ).status_code == 409
        history = client.get(
            f"/api/attendance/history?month={date.today().strftime('%Y-%m')}",
            headers=employee_headers,
        ).json()
        assert history["total"] == 1
        assert history["items"][0]["worked_hours"] is not None


def test_employee_import_preview_is_transactional_and_hashes_passwords(tmp_path: Path):
    with create_test_client(tmp_path) as client:
        admin_headers = login(client)
        csv_content = (
            "employee_id,name,email,department,designation,phone,joining_date,salary,password\n"
            "EMP-4101,Jordan Example,jordan@example.test,Engineering,Developer,5550101,2025-03-01,75000,NewPassword123!\n"
            "EMP-4101,Duplicate Example,duplicate@example.test,Engineering,Developer,5550102,2025-03-02,75000,NewPassword123!\n"
        )
        preview = client.post(
            "/api/admin/import/employees/preview",
            files={"file": ("employees.csv", csv_content.encode(), "text/csv")},
            headers=admin_headers,
        )
        assert preview.status_code == 200
        data = preview.json()
        assert data["valid_count"] == 1
        assert data["error_count"] == 1
        assert "password" not in str(data).lower()
        rejected_commit = client.post(
            f"/api/admin/import/employees/{data['batch_id']}/commit",
            json={"confirm": True},
            headers=admin_headers,
        )
        assert rejected_commit.status_code == 422
        assert client.get(
            "/api/employees/EMP-4101", headers=admin_headers
        ).status_code == 404

        valid_content = (
            "employee_id,name,email,department,designation,phone,joining_date,salary,password\n"
            "EMP-4102,Casey Example,casey@example.test,Engineering,Developer,5550103,2025-03-03,76000,NewPassword123!\n"
        )
        valid_preview = client.post(
            "/api/admin/import/employees/preview",
            files={"file": ("employees.csv", valid_content.encode(), "text/csv")},
            headers=admin_headers,
        ).json()
        committed = client.post(
            f"/api/admin/import/employees/{valid_preview['batch_id']}/commit",
            json={"confirm": True},
            headers=admin_headers,
        )
        assert committed.status_code == 200
        assert committed.json()["imported_count"] == 1
        imported = client.get(
            "/api/employees/EMP-4102", headers=admin_headers
        )
        assert imported.status_code == 200
        assert imported.json()["status"] == "Pending"
        with sqlite3.connect(tmp_path / "test.sqlite3") as connection:
            password_hash = connection.execute(
                "SELECT password_hash FROM users WHERE username = 'EMP-4102'"
            ).fetchone()[0]
        assert password_hash != "NewPassword123!"
        assert client.post(
            "/api/auth/login",
            json={
                "login_type": "employee",
                "username": "EMP-4102",
                "password": "NewPassword123!",
            },
        ).status_code == 403
        xlsx = Workbook()
        worksheet = xlsx.active
        worksheet.append([
            "employee_id", "name", "email", "department", "designation",
            "phone", "joining_date", "salary", "password",
        ])
        worksheet.append([
            "EMP-4103", "Riley Example", "riley@example.test", "Finance",
            "Analyst", "5550104", "2025-03-04", 72000, "AnotherPassword123!",
        ])
        xlsx_bytes = BytesIO()
        xlsx.save(xlsx_bytes)
        xlsx_preview = client.post(
            "/api/admin/import/employees/preview",
            files={"file": ("employees.xlsx", xlsx_bytes.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            headers=admin_headers,
        )
        assert xlsx_preview.status_code == 200
        assert xlsx_preview.json()["valid_count"] == 1
        xlsx_batch_id = xlsx_preview.json()["batch_id"]
        assert client.post(
            f"/api/admin/import/employees/{xlsx_batch_id}/commit",
            json={"confirm": True},
            headers=admin_headers,
        ).json()["imported_count"] == 1
        summary = client.get(
            f"/api/admin/import/employees/{valid_preview['batch_id']}/summary",
            headers=admin_headers,
        )
        assert summary.status_code == 200
        assert "Imported" in summary.text
        workbook = client.get("/api/reports/employees.xlsx", headers=admin_headers)
        assert workbook.status_code == 200
        assert workbook.content.startswith(b"PK")


def test_hash_map_lab_benchmarks_and_demo_are_consistent(tmp_path: Path):
    with create_test_client(tmp_path) as client:
        admin_headers = login(client)
        benchmark = client.post(
            "/api/hash-map-lab/benchmark?size=100&repetitions=2",
            headers=admin_headers,
        )
        assert benchmark.status_code == 200
        data = benchmark.json()
        assert data["dataset_size"] == 100
        assert data["repetitions"] == 2
        assert data["results_match"] is True
        assert len(data["methods"]) == 3
        assert all(item["insert_ms_average"] >= 0 for item in data["methods"])
        assert all(item["search_ms_average"] >= 0 for item in data["methods"])
        assert all(item["probes"] > 0 for item in data["methods"])
        larger_benchmark = client.post(
            "/api/hash-map-lab/benchmark?size=10000&repetitions=1",
            headers=admin_headers,
        )
        assert larger_benchmark.status_code == 200
        assert larger_benchmark.json()["results_match"] is True
        assert larger_benchmark.json()["dataset_size"] == 10000
        assert client.post(
            "/api/hash-map-lab/benchmark?size=101",
            headers=admin_headers,
        ).status_code == 422

        inserted = client.post(
            "/api/hash-map-lab/demo",
            json={"action": "insert", "employee_id": "DEMO-777"},
            headers=admin_headers,
        )
        assert inserted.status_code == 200
        assert inserted.json()["results_match"] is True
        assert inserted.json()["persistent_employee_data_changed"] is False
        assert len(inserted.json()["separate_chaining"]["buckets"]) > 0
        assert len(inserted.json()["linear_probing"]["slots"]) > 0
        found = client.post(
            "/api/hash-map-lab/demo",
            json={"action": "search", "employee_id": "DEMO-777"},
            headers=admin_headers,
        )
        assert found.json()["result"]["found"] is True
        deleted = client.post(
            "/api/hash-map-lab/demo",
            json={"action": "delete", "employee_id": "DEMO-777"},
            headers=admin_headers,
        )
        assert deleted.json()["result"]["deleted"] is True


def test_manager_permissions_hierarchy_and_chart(tmp_path: Path):
    with create_test_client(tmp_path) as client:
        admin_headers = login(client)
        for employee_id in ("EMP-4001", "EMP-4002"):
            assert client.post(
                "/api/auth/register",
                json={
                    **registration_payload(employee_id),
                    "email": f"{employee_id.lower()}@example.test",
                },
            ).status_code == 201
            assert client.patch(
                f"/api/employees/{employee_id}/status",
                json={"status": "Approved"},
                headers=admin_headers,
            ).status_code == 200
        assigned = client.put(
            "/api/admin/employees/EMP-4002/manager",
            json={"manager_id": "EMP-4001"},
            headers=admin_headers,
        )
        assert assigned.status_code == 200
        manager_headers = login(
            client,
            "EMP-4001",
            "RegistrationPassword123!",
            "employee",
        )
        chart = client.get(
            "/api/organization/chart", headers=manager_headers
        )
        assert chart.status_code == 200
        assert {item["employee_id"] for item in chart.json()} == {
            "EMP-4001", "EMP-4002"
        }
        team_balances = client.get(
            "/api/leave/balances", headers=manager_headers
        ).json()
        assert {item["employee_id"] for item in team_balances["employees"]} == {
            "EMP-4001", "EMP-4002"
        }
        cycle = client.put(
            "/api/admin/employees/EMP-4001/manager",
            json={"manager_id": "EMP-4002"},
            headers=admin_headers,
        )
        assert cycle.status_code == 409
        employee_headers = login(
            client,
            "EMP-4002",
            "RegistrationPassword123!",
            "employee",
        )
        assert client.get(
            "/api/employees?search=EMP-4001", headers=employee_headers
        ).json()["total"] == 0
        own_chart = client.get(
            "/api/organization/chart", headers=employee_headers
        ).json()
        assert [item["employee_id"] for item in own_chart] == ["EMP-4002"]


def test_missed_checkout_correction_uses_server_review_and_org_timezone(tmp_path: Path):
    with create_test_client(tmp_path) as client:
        admin_headers = login(client)
        assert client.post(
            "/api/auth/register", json=registration_payload("EMP-5001")
        ).status_code == 201
        assert client.patch(
            "/api/employees/EMP-5001/status",
            json={"status": "Approved"},
            headers=admin_headers,
        ).status_code == 200
        employee_headers = login(
            client, "EMP-5001", "RegistrationPassword123!", "employee"
        )
        now = datetime.now(timezone.utc)
        check_in = now - timedelta(hours=2)
        requested = now - timedelta(minutes=30)
        with sqlite3.connect(tmp_path / "test.sqlite3") as connection:
            connection.execute(
                """INSERT INTO attendance_records(
                       employee_id, local_work_date, check_in_utc
                   ) VALUES (?, ?, ?)""",
                ("EMP-5001", now.date().isoformat(), check_in.isoformat()),
            )
        correction = client.post(
            "/api/attendance/corrections",
            json={
                "requested_check_out": requested.replace(tzinfo=None).isoformat(timespec="seconds"),
                "reason": "I forgot to check out.",
            },
            headers=employee_headers,
        )
        assert correction.status_code == 201
        correction_id = correction.json()["correction_id"]
        assert client.post(
            "/api/attendance/corrections",
            json={
                "requested_check_out": requested.replace(tzinfo=None).isoformat(timespec="seconds"),
                "reason": "Duplicate request.",
            },
            headers=employee_headers,
        ).status_code == 409
        review = client.patch(
            f"/api/admin/attendance/corrections/{correction_id}",
            json={"status": "Approved", "note": ""},
            headers=admin_headers,
        )
        assert review.status_code == 200
        with sqlite3.connect(tmp_path / "test.sqlite3") as connection:
            check_out = connection.execute(
                """SELECT a.check_out_utc FROM attendance_records a
                   JOIN attendance_corrections c ON c.attendance_id = a.attendance_id
                   WHERE c.correction_id = ?""",
                (correction_id,),
            ).fetchone()[0]
        assert check_out == requested.replace(tzinfo=timezone.utc).isoformat(timespec="seconds")


def test_employee_id_card_logo_pdf_and_secure_verification(tmp_path: Path, monkeypatch):
    from app import advanced_api
    from PIL import Image

    verification_token = "safe-verification-identifier-123456789"
    logo_path = tmp_path / "company-logo.png"
    Image.new("RGB", (48, 48), color=(30, 80, 120)).save(logo_path)
    monkeypatch.setattr(advanced_api, "PRIVATE_STORAGE", tmp_path / "private-storage")
    monkeypatch.setattr(
        advanced_api.secrets, "token_urlsafe", lambda _: verification_token
    )
    with create_test_client(tmp_path, company_logo_path=logo_path) as client:
        admin_headers = login(client)
        assert client.post(
            "/api/auth/register", json=registration_payload("EMP-5002")
        ).status_code == 201
        assert client.patch(
            "/api/employees/EMP-5002/status",
            json={"status": "Approved"},
            headers=admin_headers,
        ).status_code == 200
        employee_headers = login(
            client, "EMP-5002", "RegistrationPassword123!", "employee"
        )
        issue = client.post(
            "/api/admin/id-cards/EMP-5002/issue", headers=admin_headers
        )
        assert issue.status_code == 201
        assert "verification_token" not in issue.text
        card_id = issue.json()["card_id"]
        employee_pdf = client.get("/api/id-cards/me/pdf", headers=employee_headers)
        admin_pdf = client.get(
            f"/api/admin/id-cards/EMP-5002/pdf", headers=admin_headers
        )
        assert employee_pdf.status_code == 200
        assert employee_pdf.content.startswith(b"%PDF")
        assert employee_pdf.content.count(b"/Subtype /Image") >= 2
        assert admin_pdf.status_code == 200
        verified = client.get(
            f"/api/id-cards/verify/{verification_token}"
        )
        assert verified.status_code == 200
        assert verified.json()["verified"] is True
        assert verified.json()["employee_id"] == "EMP-5002"
        assert verification_token not in verified.text
        with sqlite3.connect(tmp_path / "test.sqlite3") as connection:
            storage_key = connection.execute(
                "SELECT pdf_storage_key FROM id_cards WHERE card_id = ?",
                (card_id,),
            ).fetchone()[0]
        assert (tmp_path / "private-storage" / storage_key).is_file()


def test_department_heads_and_department_scoped_hierarchy_permissions(tmp_path: Path):
    with create_test_client(tmp_path) as client:
        admin_headers = login(client)
        department_people = ("EMP-DH-1", "EMP-DH-2", "EMP-DH-3")
        for employee_id in department_people:
            response = client.post(
                "/api/auth/register",
                json={
                    **registration_payload(employee_id),
                    "email": f"{employee_id.lower()}@example.test",
                },
            )
            assert response.status_code == 201
            assert client.patch(
                f"/api/employees/{employee_id}/status",
                json={"status": "Approved"},
                headers=admin_headers,
            ).status_code == 200

        assert client.put(
            "/api/admin/departments/Engineering/head",
            json={"employee_id": "EMP-DH-1"},
            headers=admin_headers,
        ).status_code == 200
        assert client.put(
            "/api/admin/employees/EMP-DH-2/manager",
            json={"manager_id": "EMP-DH-1"},
            headers=admin_headers,
        ).status_code == 200
        wrong_department = client.put(
            "/api/admin/departments/Finance/head",
            json={"employee_id": "EMP-DH-1"},
            headers=admin_headers,
        )
        assert wrong_department.status_code == 422
        heads = client.get("/api/admin/departments/heads", headers=admin_headers)
        assert heads.status_code == 200
        engineering = next(item for item in heads.json() if item["department"] == "Engineering")
        assert engineering["head_employee_id"] == "EMP-DH-1"
        assert client.get(
            "/api/admin/departments/heads", headers=login(
                client, "EMP-DH-2", "RegistrationPassword123!", "employee"
            )
        ).status_code == 403

        admin_chart = client.get("/api/organization/chart", headers=admin_headers).json()
        head_record = next(item for item in admin_chart if item["employee_id"] == "EMP-DH-2")
        assert head_record["department_head_id"] == "EMP-DH-1"
        assert head_record["department_head_name"] == "Morgan Registration"
        manager_headers = login(
            client, "EMP-DH-1", "RegistrationPassword123!", "employee"
        )
        manager_chart = client.get(
            "/api/organization/chart", headers=manager_headers
        ).json()
        assert {item["employee_id"] for item in manager_chart} == {"EMP-DH-1", "EMP-DH-2"}
        history = client.get(
            "/api/admin/departments/heads/history", headers=admin_headers
        )
        assert history.status_code == 200
        assert history.json()[0]["new_head_id"] == "EMP-DH-1"

        with sqlite3.connect(tmp_path / "test.sqlite3") as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                "UPDATE employees SET active = 0 WHERE employee_id = 'EMP-DH-1'"
            )
            head = connection.execute(
                "SELECT employee_id FROM department_heads WHERE department_name = 'Engineering'"
            ).fetchone()
            history = connection.execute(
                """SELECT assigned_by, previous_head_id, new_head_id
                   FROM department_head_history
                   WHERE assigned_by = 'system:employee_update'"""
            ).fetchone()
        assert head is None
        assert tuple(history) == ("system:employee_update", "EMP-DH-1", None)


def test_performance_workflow_admin_manager_employee_and_scope(tmp_path: Path):
    with create_test_client(tmp_path) as client:
        admin_headers = login(client)
        ids = ("EMP-PM", "EMP-PT", "EMP-OUT")
        for employee_id in ids:
            assert client.post(
                "/api/auth/register",
                json={
                    **registration_payload(employee_id),
                    "email": f"{employee_id.lower()}@example.test",
                },
            ).status_code == 201
            assert client.patch(
                f"/api/employees/{employee_id}/status",
                json={"status": "Approved"},
                headers=admin_headers,
            ).status_code == 200
        assert client.put(
            "/api/admin/employees/EMP-PT/manager",
            json={"manager_id": "EMP-PM"},
            headers=admin_headers,
        ).status_code == 200
        period_response = client.post(
            "/api/admin/performance/periods",
            json={
                "name": "FY 2026 Review",
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
            },
            headers=admin_headers,
        )
        assert period_response.status_code == 201
        period_id = period_response.json()["period_id"]
        manager_headers = login(
            client, "EMP-PM", "RegistrationPassword123!", "employee"
        )
        employee_headers = login(
            client, "EMP-PT", "RegistrationPassword123!", "employee"
        )
        outsider_headers = login(
            client, "EMP-OUT", "RegistrationPassword123!", "employee"
        )

        goal = client.post(
            "/api/performance/goals",
            json={
                "employee_id": "EMP-PT",
                "period_id": period_id,
                "title": "Deliver project",
                "measurable_target": "Release 3 milestones",
                "due_date": "2026-10-01",
            },
            headers=manager_headers,
        )
        assert goal.status_code == 201
        assert client.post(
            "/api/performance/goals",
            json={
                "employee_id": "EMP-PM",
                "period_id": period_id,
                "title": "Self-assigned",
                "measurable_target": "Complete own goal",
                "due_date": "2026-10-01",
            },
            headers=manager_headers,
        ).status_code == 403
        assert client.post(
            "/api/performance/goals",
            json={
                "employee_id": "EMP-OUT",
                "period_id": period_id,
                "title": "Unassigned",
                "measurable_target": "Must be blocked",
                "due_date": "2026-10-01",
            },
            headers=manager_headers,
        ).status_code == 403
        assert client.post(
            "/api/performance/goals",
            json={
                "employee_id": "EMP-OUT",
                "period_id": period_id,
                "title": "Admin assigned",
                "measurable_target": "Complete target",
                "due_date": "2026-10-01",
            },
            headers=admin_headers,
        ).status_code == 201

        assert client.patch(
            f"/api/performance/goals/{goal.json()['goal_id']}",
            json={"status": "in_progress", "progress_note": "First milestone started"},
            headers=employee_headers,
        ).status_code == 200
        self_assessment = client.put(
            f"/api/performance/reviews/{period_id}/self-assessment",
            json={"self_assessment": "Delivered the first milestone.", "self_rating": 4},
            headers=employee_headers,
        )
        assert self_assessment.status_code == 200
        assert client.put(
            f"/api/performance/reviews/{period_id}/self-assessment",
            json={"self_assessment": "Attempt to edit a submitted review.", "self_rating": 5},
            headers=employee_headers,
        ).status_code == 409
        team_reviews = client.get(
            "/api/performance/reviews", headers=manager_headers
        ).json()
        assert {item["employee_id"] for item in team_reviews} == {"EMP-PM", "EMP-PT"}
        target_review = next(item for item in team_reviews if item["employee_id"] == "EMP-PT")
        own_review = next(item for item in team_reviews if item["employee_id"] == "EMP-PM")
        assert client.get(
            "/api/performance/reviews?employee_id=EMP-OUT",
            headers=manager_headers,
        ).status_code == 403
        assert client.put(
            f"/api/performance/reviews/{own_review['review_id']}/manager-feedback",
            json={"manager_feedback": "Review own performance.", "manager_rating": 3},
            headers=manager_headers,
        ).status_code == 403
        feedback = client.put(
            f"/api/performance/reviews/{target_review['review_id']}/manager-feedback",
            json={"manager_feedback": "Strong delivery and clear communication.", "manager_rating": 4},
            headers=manager_headers,
        )
        assert feedback.status_code == 200
        assert client.put(
            f"/api/performance/reviews/{target_review['review_id']}/manager-feedback",
            json={"manager_feedback": "Second attempt.", "manager_rating": 2},
            headers=manager_headers,
        ).status_code == 409
        employee_reviews = client.get(
            "/api/performance/reviews", headers=employee_headers
        ).json()
        visible_review = next(item for item in employee_reviews if item["employee_id"] == "EMP-PT")
        assert visible_review["manager_rating"] == 4
        assert client.patch(
            f"/api/admin/performance/reviews/{visible_review['review_id']}",
            json={"admin_note": "Reviewed by HR."},
            headers=admin_headers,
        ).status_code == 200
        assert client.get(
            "/api/performance/reviews?employee_id=EMP-PT",
            headers=outsider_headers,
        ).status_code == 403
        closed = client.patch(
            f"/api/admin/performance/periods/{period_id}/close",
            headers=admin_headers,
        )
        assert closed.status_code == 200
        assert client.patch(
            f"/api/performance/goals/{goal.json()['goal_id']}",
            json={"status": "completed", "progress_note": "Closed period change"},
            headers=employee_headers,
        ).status_code == 409


def test_admin_workforce_reports_csv_xlsx_and_date_filters(tmp_path: Path):
    with create_test_client(tmp_path) as client:
        admin_headers = login(client)
        assert client.post(
            "/api/auth/register", json=registration_payload("EMP-REPORT")
        ).status_code == 201
        assert client.patch(
            "/api/employees/EMP-REPORT/status",
            json={"status": "Approved"},
            headers=admin_headers,
        ).status_code == 200
        employee_headers = login(
            client, "EMP-REPORT", "RegistrationPassword123!", "employee"
        )
        leave_policy = client.put(
            "/api/admin/leave/policies/Annual",
            json={"annual_allowance": 10, "count_weekends": True, "active": True},
            headers=admin_headers,
        )
        assert leave_policy.status_code == 200
        assert client.post(
            "/api/leave/requests",
            json={
                "leave_type": "Annual",
                "start_date": "2030-07-05",
                "end_date": "2030-07-06",
                "reason": "Family event",
            },
            headers=employee_headers,
        ).status_code == 201
        assert client.post(
            "/api/attendance/check-in", headers=employee_headers
        ).status_code == 201
        sleep(0.02)
        assert client.patch(
            "/api/attendance/check-out", headers=employee_headers
        ).status_code == 200

        reports = (
            "department-employees",
            "employee-status",
            "joining-dates",
            "approval-status",
            "leave",
            "attendance",
            "employee-growth",
            "workforce",
        )
        for report_name in reports:
            csv_response = client.get(
                f"/api/admin/reports/{report_name}.csv",
                headers=admin_headers,
            )
            xlsx_response = client.get(
                f"/api/admin/reports/{report_name}.xlsx",
                headers=admin_headers,
            )
            assert csv_response.status_code == 200, (report_name, csv_response.text)
            assert "text/csv" in csv_response.headers["content-type"]
            assert xlsx_response.status_code == 200, report_name
            assert xlsx_response.content.startswith(b"PK")
            workbook = load_workbook(BytesIO(xlsx_response.content), read_only=True)
            sheet = workbook.active
            headers = next(sheet.iter_rows(values_only=True))
            assert headers
            workbook.close()
        leave_report = client.get(
            "/api/admin/reports/leave.csv?start_date=2030-07-01&end_date=2030-07-31",
            headers=admin_headers,
        )
        assert "Family event" in leave_report.text
        no_leave = client.get(
            "/api/admin/reports/leave.csv?start_date=2030-08-01&end_date=2030-08-31",
            headers=admin_headers,
        )
        assert "Family event" not in no_leave.text
        assert client.get(
            "/api/admin/reports/attendance.csv",
            headers=employee_headers,
        ).status_code == 403
        assert client.get(
            "/api/admin/reports/not-a-report.csv",
            headers=admin_headers,
        ).status_code == 404
        assert client.get(
            "/api/admin/reports/leave.csv?start_date=2030-08-01&end_date=2030-07-01",
            headers=admin_headers,
        ).status_code == 422
