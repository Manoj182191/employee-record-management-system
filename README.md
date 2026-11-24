# Employee Record Management System (ERMS)

ERMS is an employee record management application and M.Tech academic project. It combines everyday HR workflows with a practical demonstration of custom hash maps, collision handling, and measured lookup performance.

> **Project status:** The instructions below describe the current source code. No cloud deployment is configured or claimed.

## Table of contents

- [Project overview](#project-overview)
- [Roles and application modules](#roles-and-application-modules)
- [Hash Map Performance Lab](#hash-map-performance-lab)
- [Prerequisites](#prerequisites)
- [Install and configure](#install-and-configure)
- [Run the complete application](#run-the-complete-application)
- [Testing and validation](#testing-and-validation)
- [Project structure](#project-structure)
- [Database and file storage](#database-and-file-storage)
- [Deployment options](#deployment-options)
- [Troubleshooting](#troubleshooting)
- [Suggested GitHub description](#suggested-github-description)

## Project overview

ERMS provides a central place for HR to manage employee records and approvals, while employees can access their own permitted workflows. The application is split into two independently running parts:

| Part | Technologies | Responsibility |
|---|---|---|
| Frontend | React 18, JavaScript, Vite 6 | Existing browser interface; calls the API over HTTP |
| Backend | Python, FastAPI, Uvicorn | Authentication, role checks, validation, business rules, reports, and API responses |
| Database | SQLite via Python's standard-library `sqlite3` | Persistent source of truth for accounts, employees, and module records |
| Hash maps | Custom Python implementations | In-memory employee lookup and the Hash Map Performance Lab |

The frontend does not contain or serve the backend. The browser sends API requests to the configured API base URL and uses bearer tokens for authenticated requests. SQLite persists application data; the employee hash map is a process-local index rebuilt from SQLite when the API starts.

### Roles

| Role | Access |
|---|---|
| **Admin / HR** | Manage employees and approvals, organization data, documents, reports, imports, leave, attendance, performance, notifications, and ID cards. |
| **Employee** | Access their own profile and authorized employee workflows, such as onboarding, documents, leave, attendance, performance tasks, notifications, and an issued ID card. |
| **Manager** | A manager is an employee account with assigned-team permissions, not a separate login role. Access is limited to authorized assigned-team workflows. |

### Application modules

| Module | What is implemented |
|---|---|
| Notifications | Role-relevant notifications, read controls, history, and unread counts |
| Onboarding | Employee checklist with saved progress and Admin review |
| Documents | Categorized private uploads, review, replacement/version history, and authorized downloads |
| Employee history | Employment timeline and recorded employee changes |
| Leave | Configurable leave policies, applications, decisions, and history |
| Attendance | Check-in/out, attendance history, reports, and authorized missed-checkout corrections |
| Performance | Review periods, goals, employee self-assessments, and manager/Admin workflows |
| Organization chart | Department structure, department heads, and reporting relationships |
| Manager assignment | Admin-managed reporting assignments and restricted team access |
| Employee ID cards | Generated PDF cards, private downloads, and QR verification |
| Reports and imports/exports | CSV/XLSX reports and validated CSV/XLSX employee import workflows |
| Hash Map Performance Lab | Separate Chaining, Linear Probing, and linear-search benchmark/demo |

Employee registration creates a Pending account. Admin approval is required before employee login is enabled. Employee IDs are used as employee login names. Admin accounts are provisioned through the backend CLI; the project does not define a default Admin password.

## Hash Map Performance Lab

The application includes two custom implementations in `backend/app/hashmap/`:

| Implementation | Collision resolution | Resize trigger in the current code |
|---|---|---|
| **Separate Chaining** (`EmployeeHashMap`) | Each bucket holds a list of key/value entries that share the same bucket index. | Resizes when load factor exceeds `0.75`. |
| **Linear Probing** (`LinearProbingMap`) | Open addressing: after a collision, probe subsequent slots; deleted slots are marked for later reuse. | Resizes before insertion would exceed `0.65` load factor. |

Separate Chaining is the employee lookup map. At API startup, it is populated from SQLite; employee API writes keep the in-process map synchronized. SQLite remains the persistent source of truth. Linear Probing is available for comparison and demonstration.

The lab compares both custom maps with a linear-search baseline on generated datasets of 100, 1,000, and 10,000 records. It reports measured timings, collisions/probes, and checks that search results agree. Timings are measurements from the current run, not guarantees. For both hash maps, expected lookup is generally O(1) at controlled load, while a worst-case lookup can be O(n); linear search is O(n).

## Prerequisites

- **Python 3.10 or later**. Python 3.10 is the minimum used by the project.
- **Node.js 18.x, 20.x, or 22 or later** and npm. These ranges match the locked Vite package's declared engine support; npm is installed with Node.js.
- **Git for Windows** to clone the repository. The project does not pin a minimum Git version.
- **Visual Studio Code** is optional; any editor and PowerShell terminal can be used.

Check installed versions:

```powershell
python --version
node --version
npm --version
git --version
```

## Install and configure

The following commands are for Windows PowerShell and clone the named GitHub repository:

```powershell
Set-Location $HOME\Desktop
git clone https://github.com/Manoj182191/employee-record-management-system.git
Set-Location .\employee-record-management-system
```

Create and activate a project virtual environment, then install the backend and test dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements-dev.txt
```

`backend/requirements-dev.txt` includes the backend requirements and the packages used by the test suite. For a runtime-only installation, use `backend\requirements.txt` instead.

### Backend environment

`backend/.env.example` is the configuration template. The backend reads **process environment variables**; it does not automatically load a `.env` file. In the activated PowerShell terminal, the following snippet loads the example key/value settings for that terminal session:

```powershell
Get-Content .\backend\.env.example | ForEach-Object {
    if ($_ -match '^\s*([^#=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim(), 'Process')
    }
}
$env:ERMS_JWT_SECRET = (python -c "import secrets; print(secrets.token_urlsafe(48))")
```

The template's JWT secret is blank, so the second command supplies a random local-development secret. It is not stored in the repository. This value lasts only for the current PowerShell process; set the same value again after restarting if existing local tokens should remain valid. Production deployments should inject a stable random secret through the hosting provider's secret manager.

The backend validates that `ERMS_JWT_SECRET` has at least 32 characters. Other settings and development defaults are listed in `backend/.env.example`; for example, the default database path is `database/erms.sqlite3` relative to the backend directory, and the default CORS origins are the local Vite URLs.

### Initialize the database and create the first Admin

There is no separate migration command. Database initialization and pending migrations run automatically when the API starts. The CLI's `create-admin` command also initializes the database before creating an account. Run it once for the first Admin:

```powershell
Set-Location .\backend
python -m app.cli create-admin
```

Enter an Admin username and password when prompted. The CLI requires a password of at least 12 characters. Do not repeat this command on every application start; it creates another Admin account.

## Run the complete application

Keep the backend and frontend running in **separate PowerShell terminals**.

### Terminal 1: FastAPI backend

From the project root, activate the virtual environment and load the backend environment as described above. Then:

```powershell
Set-Location .\backend
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The API listens at `http://127.0.0.1:8000`. FastAPI Swagger documentation is at `http://127.0.0.1:8000/docs`; readiness can be checked at `http://127.0.0.1:8000/health/ready`.

### Terminal 2: React/Vite frontend

```powershell
Set-Location $HOME\Desktop\employee-record-management-system\frontend
if (-not (Test-Path .\.env.local)) {
    Copy-Item .\.env.example .\.env.local
}
npm ci
npm run dev
```

The frontend is available at `http://localhost:5173`. Its `.env.local` sets `VITE_API_BASE_URL`, which defaults to `http://127.0.0.1:8000`. The browser makes HTTP requests to that API URL; backend CORS must allow the page's origin.

After changing `VITE_API_BASE_URL`, restart Vite. Vite reads its `VITE_*` settings when the development server starts and embeds them in production builds. Never put backend secrets in a `VITE_*` variable.

## Testing and validation

From the project root, with the virtual environment activated:

```powershell
python -m pytest backend\tests -q
```

The backend tests cover authentication and permissions, registration/approval, employee workflows, hash maps, imports/exports, migrations, and backup behavior.

Build the production frontend:

```powershell
Set-Location .\frontend
npm run build
```

The generated site is written to `frontend/dist/`. The package also defines `npm run dev` and `npm run preview`; it does not define an `npm test` or lint script. The project has no separate frontend unit-test command.

The README commands were checked against the current files. At the time this README was updated, the backend suite reported **27 passed** and `npm run build` completed successfully. These results describe that run, not a guarantee about future environments.

## Project structure

```text
employee-record-management-system/
├── backend/
│   ├── app/
│   │   ├── api.py                 # Authentication and core employee API
│   │   ├── advanced_api.py        # Additional HR modules and API routes
│   │   ├── cli.py                 # Admin creation, password reset, DB backup
│   │   ├── config.py              # Environment-based backend settings
│   │   ├── database/
│   │   │   ├── db.py              # SQLite connections and migrations
│   │   │   ├── schema.sql         # Core schema
│   │   │   ├── migration_005.sql
│   │   │   └── migration_006.sql
│   │   └── hashmap/
│   │       ├── separate_chaining.py
│   │       └── linear_probing.py
│   ├── tests/                     # Backend pytest suite
│   ├── database/                  # Runtime SQLite database and backups
│   ├── private_storage/            # Runtime private uploads and generated PDFs
│   ├── .env.example
│   ├── requirements.txt
│   └── requirements-dev.txt
├── frontend/
│   ├── src/
│   │   ├── App.jsx                # Main application UI
│   │   ├── AdvancedModules.jsx    # Additional module UI
│   │   ├── api.js                 # API requests and authenticated downloads
│   │   ├── main.jsx
│   │   └── style.css
│   ├── public/
│   ├── .env.example
│   ├── package.json
│   └── package-lock.json
├── requirements.txt              # Includes backend runtime requirements
├── DEPLOYMENT.md
└── README.md
```

## Database and file storage

- SQLite is the persistent source of truth and uses Python's built-in `sqlite3`; the project does not use an ORM.
- By default, the database file is `backend/database/erms.sqlite3`. Set `ERMS_DATABASE_PATH` to use another absolute path; relative paths resolve from `backend/`.
- The database connection enables foreign keys and WAL mode. `PRAGMA user_version` tracks the schema; the current source contains six migration steps, including `migration_005.sql` and `migration_006.sql`. Migrations run automatically at backend startup.
- The in-memory `EmployeeHashMap` is rebuilt from the employee table at startup and updated by employee write operations. It is not a replacement for SQLite and does not persist across backend process restarts.
- Uploaded employee documents and generated ID-card PDFs are stored under `backend/private_storage/`; document metadata and storage keys are in SQLite. This directory is private application data, not a public static directory.
- Create a verified database backup with the CLI from the `backend` directory. The destination directory must already exist and the destination file must not already exist:

  ```powershell
  python -m app.cli backup C:\secure-backups\erms-YYYYMMDD-HHMMSS.sqlite3
  ```

- The backup command backs up SQLite only. It does not include `private_storage/`. There is **no restore CLI command**; coordinate a maintenance-window restore of both the database backup and its matching private files. See [DEPLOYMENT.md](./DEPLOYMENT.md) for the documented restore limitations and procedure.

## Deployment options

**No deployment is included or claimed.** The following are architecture recommendations based on the current code, not verified provider-specific deployment instructions.

| Approach | Fit with the current project |
|---|---|
| **Vercel frontend + Render backend** | A reasonable option to evaluate: Vercel can serve the Vite build, while an ASGI-capable backend service can run FastAPI. The backend host must provide durable storage for both SQLite and `backend/private_storage/`, a stable environment for secrets, and a single backend instance. This repository has no provider deployment configuration, and this setup has not been deployed or verified. |
| **Full Vercel deployment** | Not suitable for durable employee data as-is. The app writes to local SQLite and private local files; serverless filesystem and process lifetimes do not provide the persistence and shared process state this architecture expects. A full serverless approach would require redesigning storage (for example, managed PostgreSQL and private object storage) and validating the backend runtime. |
| **Single persistent host/container** | Best match for the current architecture: one backend process on a host with a persistent local volume for SQLite and private files, plus a static frontend and HTTPS reverse proxy. |

### Deployment configuration to plan

Set backend variables in the hosting provider's environment/secret manager:

| Variable | Purpose |
|---|---|
| `ERMS_JWT_SECRET` | Required signing secret; generate a random value of at least 32 characters and keep it stable between restarts. |
| `ERMS_DATABASE_PATH` | Absolute database path on persistent storage. |
| `ERMS_CORS_ORIGINS` | Exact frontend origin(s), including scheme and hostname; use HTTPS in production. |
| `ERMS_PUBLIC_API_URL` | Public API base URL used in employee ID-card QR links. |
| `ERMS_TOKEN_MINUTES` | JWT lifetime; default `30`. |
| `ERMS_LOGIN_MAX_ATTEMPTS` | Failed-login threshold; default `5`. |
| `ERMS_LOGIN_WINDOW_SECONDS` | Login-throttle window/block duration; default `900`. |
| `ERMS_TIMEZONE` | Organization IANA timezone; default `UTC`. |
| `ERMS_WORKDAY_START` | Local workday start; default `09:00`. |
| `ERMS_COMPANY_NAME` | ID-card company name; default `ERMS`. |
| `ERMS_COMPANY_LOGO` | Optional path to an image accessible by the backend. |
| `ERMS_MAX_UPLOAD_BYTES` | Upload limit; default `10485760` bytes. |

Set `VITE_API_BASE_URL` in the frontend build environment to the public API URL. It is a public build-time setting, not a secret. The backend defaults to local development CORS origins, so configure `ERMS_CORS_ORIGINS` to the exact deployed frontend origin. CORS is not an authorization mechanism.

SQLite and private file persistence are deployment blockers on hosts with ephemeral storage. Free hosting tiers may sleep, restart, impose limits, or discard local files; verify the provider's current storage and service policies before use. Back up the database and private files together, store backups off-host, and test restoration. For horizontal scaling, migrate from local SQLite/files and the process-local employee map to shared persistence and storage first.

## Troubleshooting

### PowerShell blocks virtual-environment activation

Allow scripts for the current terminal only, then activate:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Alternatively, run the virtual environment's Python directly, for example `.\.venv\Scripts\python.exe -m pytest backend\tests -q`.

### Python module or dependency is missing

Check that the virtual environment is active, then from the repository root install the development requirements:

```powershell
python -m pip install -r backend\requirements-dev.txt
```

### npm install fails

Check `node --version` and `npm --version`, then verify npm's cache and use the lockfile:

```powershell
npm cache verify
Set-Location .\frontend
npm ci
```

### Backend port 8000 is already in use

From the project root, start the backend on another port:

```powershell
Set-Location .\backend
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
```

Update `VITE_API_BASE_URL` in `frontend/.env.local` to `http://127.0.0.1:8001`, set `ERMS_PUBLIC_API_URL` to that API origin if generating ID cards, and restart both processes. If port 5173 is occupied, start Vite from `frontend/` with `npm run dev -- --port 5174`, set `ERMS_CORS_ORIGINS` to include `http://localhost:5174` (and `http://127.0.0.1:5174` if used), then restart the backend.

### Frontend cannot reach the API

Confirm the backend is running and `http://127.0.0.1:8000/health/ready` returns ready. Check `VITE_API_BASE_URL` in `frontend/.env.local`, restart Vite after edits, and inspect the backend terminal for errors.

### CORS error in the browser

Set `ERMS_CORS_ORIGINS` in the backend process environment to the page's exact origin, such as `http://localhost:5173` or `http://127.0.0.1:5173`. These are distinct origins. Do not include an API path; restart the backend after changing the value.

### Database migration or startup error

The API initializes/migrates the database on startup. Confirm `ERMS_DATABASE_PATH` points to the intended writable file and that the backend process has permission to create its parent directory. Back up existing data before any manual repair; do not delete the database to clear an error. A migration error about duplicate employee emails differing only by case requires resolving those duplicates before startup can complete.

## Suggested GitHub description

> Employee Record Management System built with FastAPI, React, and SQLite, combining HR workflows with a custom hash-map lab for separate chaining, linear probing, and linear-search comparisons.
