# ERMS — Employee Records Management System

ERMS is split into two independently running applications:

- **Backend:** FastAPI REST API, SQLite persistence, JWT authentication, and a custom in-memory employee hash map.
- **Frontend:** React and Vite application. It communicates with the API over HTTP and does not serve or bundle the Python backend.

The supplied SQLite file had an empty legacy employee table. ERMS does not add invented employee records. Startup initializes an empty database plus starter department/designation reference values. Employee records are entered by HR.

## Requirements

- Python 3.10+
- Node.js 18+ and npm

## Start the backend

In PowerShell:

```powershell
cd C:\path\to\erms_hashmap_project\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:ERMS_JWT_SECRET = (python -c "import secrets; print(secrets.token_urlsafe(48))")
python -m app.cli create-admin
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The CLI prompts for the initial HR administrator username and password. Passwords must be at least 12 characters. Create additional administrator accounts with `python -m app.cli create-admin`. If an account loses access, an operator can reset its password with `python -m app.cli reset-password USERNAME`; this revokes its active access tokens. Create a verified SQLite backup with `python -m app.cli backup C:\secure-backups\erms.sqlite3` (the destination must not already exist). Protect backups as sensitive employee data. Keep the JWT secret in the backend process environment; use the same secret across backend restarts if existing tokens need to remain valid.

The API is available at [http://127.0.0.1:8000](http://127.0.0.1:8000); interactive API documentation is at `/docs`. The database is created at `backend/database/erms.sqlite3` by default.
`/health` reports that the API process is running; `/health/ready` checks database connectivity. Database schema upgrades are applied automatically on startup and tracked with SQLite's `user_version`.

For a separate backend process, set `ERMS_JWT_SECRET` in that process before starting it. Optional configuration:

| Variable | Default | Purpose |
|---|---|---|
| `ERMS_JWT_SECRET` | Required | Signing key, minimum 32 characters |
| `ERMS_DATABASE_PATH` | `database/erms.sqlite3` | SQLite path; relative paths resolve from `backend/` |
| `ERMS_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated allowed browser origins |
| `ERMS_TOKEN_MINUTES` | `30` | JWT access-token lifetime |
| `ERMS_LOGIN_MAX_ATTEMPTS` | `5` | Failed attempts per username and client address before temporary throttling |
| `ERMS_LOGIN_WINDOW_SECONDS` | `900` | Login-throttle window and block duration |
| `ERMS_TIMEZONE` | `UTC` | IANA timezone used for attendance dates, late-arrival checks, and missed-checkout corrections |
| `ERMS_WORKDAY_START` | `09:00` | Local workday start used for late-arrival indicators |
| `ERMS_COMPANY_NAME` | `ERMS` | Company name printed on employee ID cards |
| `ERMS_COMPANY_LOGO` | Unset | Optional local image file embedded in generated employee ID-card PDFs; relative paths resolve from `backend/` |
| `ERMS_PUBLIC_API_URL` | `http://127.0.0.1:8000` | Public API base URL embedded in QR verification links |
| `ERMS_MAX_UPLOAD_BYTES` | `10485760` | Maximum size of each uploaded employee document or import file |

`backend/.env.example` documents the variables; the backend reads process environment variables directly.

## Start the frontend

In another PowerShell terminal:

```powershell
cd C:\path\to\erms_hashmap_project\frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). To use a non-default API URL, copy `frontend/.env.example` to `frontend/.env.local`, set `VITE_API_BASE_URL`, and add the frontend origin to `ERMS_CORS_ORIGINS` on the backend.

## Included workflows

- Administrator accounts are created through the backend CLI. Employees can register with their employee ID, contact details, department, designation, joining date, and password. New registrations remain Pending and cannot sign in until HR approves them from **Pending requests**.
- HR can also create employee records and their login in one operation; these accounts also require approval before employee access is enabled. Employee IDs are employee login names.
- HR can search/filter, add/edit/delete, approve/reject, activate/deactivate, view audit activity, manage department/designation reference values, review profile change requests, and export a CSV.
- Admins can assign a department head to an eligible employee, review assignment history, and view department-grouped hierarchy. Admin report exports are available as CSV or XLSX for department employees, employee status, joining dates, approval status, leave, attendance, employee growth, and workforce summaries. Date filters are supported where applicable.
- Admin and employees have notification history, unread counts, individual/all read controls, and notification links to the related service.
- Employees can complete an automatically saved onboarding checklist, upload and replace private documents, view their own employment timeline, apply for configurable leave, check in/out, request missed-checkout corrections, complete performance self-assessments, and download an issued PDF ID card.
- Admins can review onboarding and documents, manage leave policies and approvals, view attendance reports, assign reporting managers, access team/employee performance records, issue ID cards, and view the organization chart. Managers remain employee accounts and are limited to directly assigned team records.
- Documents are kept in private storage and downloaded through authenticated routes. Employee information, notifications, document metadata, timelines, leave, attendance, performance, manager assignments, ID cards, and import batches are persisted in SQLite. Database migrations run automatically on startup.
- Admins can preview and transactionally import CSV/XLSX employee records. The import template has the columns `employee_id,name,email,department,designation,phone,joining_date,salary,password`; imports are limited to 500 rows per file, validate duplicates and row errors, hash passwords before persisting a preview, and create Pending accounts only. No rows are committed if validation errors remain.
- The Hash Map Lab compares the custom Separate Chaining map, custom Linear Probing map, and a linear-search baseline on generated datasets of 100, 1,000, or 10,000 rows. It measures insertion/search timings and collision/probe counts over repeated runs, and verifies matching results. Its interactive map demo is isolated from employee records. Times are observations for the current process, not complexity guarantees.
- Separate Chaining remains the primary employee lookup index. SQLite is the source of truth; the in-memory employee map is rebuilt at startup and synchronized by employee writes.

## Tests

From the project root:

```powershell
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
backend\.venv\Scripts\python.exe -m pytest backend\tests -q
```

The tests cover authentication and role boundaries, registration approval, notifications, onboarding review, private document access/versioning, leave and attendance validation/correction, manager hierarchy, ID-card issuance and verification, CSV/XLSX import/export, hash-map benchmarks and demonstrations, collision handling, login throttling, database migrations, and verified backups.

## Operational notes

- The API validates input, hashes passwords with bcrypt, issues signed JWT bearer tokens, and derives roles from the authenticated database account—not request parameters.
- Failed sign-ins are throttled per username and direct client address. If running behind a reverse proxy, configure the proxy and deployment network deliberately; the API does not trust forwarded client-IP headers.
- Password changes issue a replacement access token and revoke earlier tokens for that account. The CLI can reset an account password when the user cannot sign in.
- SQLite schema changes are versioned and applied at startup. Use the CLI backup operation and store backup files securely; the application does not schedule off-host backups.
- The import preview expires after 24 hours and is scoped to the Admin who uploaded it. Its temporary SQLite rows store password hashes, never the submitted plaintext passwords.
- The Hash Map Lab demo state is in-memory and isolated per API process; it is intended for demonstration and does not change employees in SQLite. Benchmark timings are process-local.
- The frontend stores the short-lived access token in `sessionStorage` and sends it in the `Authorization` header.
- Deactivating an employee also disables that employee's login.
- Department/designation values can be added or renamed. Deletion is blocked while any employee record references the value.
- Run the API as a single process when relying on its in-memory map. SQLite remains authoritative; horizontally scaled deployments would need a shared cache/index invalidation strategy and production identity/security operations.

## Production-readiness boundaries

This is a working application baseline, not a compliance-certified deployment. Email-based self-service password recovery, MFA/SSO, scheduled off-host backups, and centralized secret management are not included. Configure operational controls and validate them against organizational policies before handling real employee data.

See [DEPLOYMENT.md](./DEPLOYMENT.md) for production environment, hosting, backup, and restore guidance.

## Legacy files

`employees.db` is the original empty database and is retained unchanged. The application uses `backend/database/erms.sqlite3`; the former root `main.py` / `static/` implementation is no longer the application entry point.
