# ERMS production deployment guide

This guide covers the current architecture: a React/Vite frontend, a FastAPI
backend, SQLite as the source of truth, and employee documents/ID-card PDFs in
backend-local private storage. No deployment or production secret is configured
by this guide.

## Production environment

Set these in the hosting platform's secret manager or service environment. Do
not commit a production `.env` file, put a secret in frontend variables, or
regenerate the JWT key at each process start.

| Variable | Required | Production setting |
|---|---|---|
| `ERMS_JWT_SECRET` | Yes | Generate once with `python -c "import secrets; print(secrets.token_urlsafe(48))"` and store the same value in the host's secret manager. The application rejects values shorter than 32 characters, but does not measure entropy; use a randomly generated value. Keep it stable across restarts; rotate it deliberately because rotation invalidates all existing access tokens. |
| `ERMS_DATABASE_PATH` | Yes for durable hosting | Absolute path on a persistent local block volume, e.g. `/srv/erms/data/erms.sqlite3`. Relative paths resolve from `backend/`. |
| `ERMS_CORS_ORIGINS` | Yes | Exact HTTPS frontend origin(s), comma-separated, e.g. `https://hr.example.com`; do not leave the localhost defaults or use `*`. |
| `ERMS_PUBLIC_API_URL` | Yes when issuing ID cards | Public HTTPS API origin, e.g. `https://api.example.com`; this is embedded in QR verification links. |
| `VITE_API_BASE_URL` | Yes for a non-local API | Public HTTPS API base URL, set at frontend build time. It is not secret. |
| `ERMS_TOKEN_MINUTES` | Recommended | Short access-token lifetime; default is 30 minutes. |
| `ERMS_LOGIN_MAX_ATTEMPTS` | Recommended | Failed attempts before temporary throttling; default is 5. |
| `ERMS_LOGIN_WINDOW_SECONDS` | Recommended | Throttle window/block duration; default is 900 seconds. |
| `ERMS_MAX_UPLOAD_BYTES` | Recommended | Enforce a limit consistent with proxy limits and available storage; default is 10 MiB. |
| `ERMS_TIMEZONE` | As required | Valid IANA timezone for attendance calculations; default is `UTC`. |
| `ERMS_WORKDAY_START` | As required | Local workday start in `HH:MM`; default is `09:00`. |
| `ERMS_COMPANY_NAME` | Optional | Company name printed on ID cards; default is `ERMS`. |
| `ERMS_COMPANY_LOGO` | Optional | Path to a logo available to the backend process. |

`backend/.env.example` and `frontend/.env.example` are development templates;
the backend reads process environment variables directly. The frontend embeds
`VITE_API_BASE_URL` into its built assets, so rebuild after changing it. Never
put `ERMS_JWT_SECRET` in a `VITE_*` variable.

## Recommended hosting topology

For the current SQLite and local-file design, use one Linux VM or one
single-instance container on a host with a persistent local block volume:

1. Mount durable storage for both the SQLite database and
   `backend/private_storage/`.
2. Run exactly one backend process/worker. Keep SQLite on a local filesystem;
   do not put its live database or WAL files on NFS or another shared network
   filesystem.
3. Terminate TLS at a maintained reverse proxy or managed HTTPS ingress, and
   forward requests to Uvicorn on a private interface. Do not expose the
   development server or `/docs` publicly unless access is restricted.
4. Serve the built frontend over HTTPS and set `ERMS_CORS_ORIGINS` to its exact
   origin. CORS is a browser policy, not an authorization boundary.
5. Restrict access to the host, persistent volume, logs, backups, and private
   storage. Encrypt disks/backups where supported and monitor disk capacity.
6. Configure appropriate edge security headers (including HSTS, frame
   protection, and a restrictive Content Security Policy) at the HTTPS
   frontend/reverse proxy. The application does not currently set a complete
   global security-header policy.

The employee lookup hash map and Hash Map Lab demo state are process-local.
SQLite is authoritative, but multiple backend replicas are not a supported
deployment topology. If horizontal scaling or higher write concurrency is
required, first migrate persistence to a server database such as PostgreSQL,
move uploaded files to private object storage, and replace process-local
coordination with shared services.

## Build and start

Install backend dependencies from `backend/requirements.txt` using a supported
Python version, then start the API as a single worker. Example service command:

```text
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Run it with `backend/` as the working directory and with the production
environment variables above injected by the service manager. Provision the
first Admin interactively with `python -m app.cli create-admin`; do not seed
credentials in source code.

Build the frontend in `frontend/`:

```text
npm ci
npm run build
```

Set `VITE_API_BASE_URL` in the build environment before building. Publish only
the resulting `frontend/dist/` through the HTTPS web server. The API uses
short-lived bearer tokens stored in browser `sessionStorage`; TLS is required
end to end between the browser, proxy, and backend.

## Backups and restore

The supported CLI command creates and integrity-checks a SQLite backup:

```text
python -m app.cli backup /secure-backups/erms-YYYYMMDD-HHMMSS.sqlite3
```

The destination directory must already exist and the destination file must not
exist. This command backs up SQLite only; it does not back up
`backend/private_storage/`. Establish a coordinated backup job that preserves
both the database and private files, stores copies off-host, restricts access,
and regularly tests restoration. Pause writes or otherwise coordinate the
snapshot so the database metadata and uploaded files represent the same
application state.

There is currently **no ERMS restore CLI command**. Until one is implemented
and tested, restore operationally during a maintenance window:

1. Stop the backend and preserve the current database and private-storage
   directory as a separate recovery point.
2. Restore the selected SQLite backup to the configured database path and
   restore its matching private-storage snapshot.
3. Before restarting, verify the restored database with
   `python -c 'import sqlite3; c=sqlite3.connect("/srv/erms/data/erms.sqlite3"); print(c.execute("PRAGMA integrity_check").fetchone()[0]); c.close()'`
   after replacing the example path with the configured database path.
   The result must be `ok`.
4. Start the backend and verify `/health/ready`, employee/document access,
   expected schema version, and application logs.

Do not delete the previous recovery point until the restored service and files
have been checked. The application does not currently schedule backups, copy
them off-host, or include private files in its SQLite backup command.

## Deployment blockers and security boundaries

- Production secrets, allowed origins, public URLs, persistent volume paths,
  TLS, and backup destinations must be supplied by the operator; none are
  configured in this source tree.
- SQLite backups do not include private uploads, and restore is a documented
  manual procedure rather than an application command.
- `ERMS_LOGIN_*` throttling uses the direct peer address. The API does not
  trust forwarded client-IP headers; account for this when configuring a proxy
  and do not expose the backend outside the trusted network.
- The backend dependency manifest uses version ranges rather than a fully
  pinned Python lockfile. Review and pin a tested dependency set in the release
  process; keep the frontend lockfile and use `npm ci`.
- The JWT configuration enforces a minimum string length but not secret
  randomness. Generate the production value cryptographically and inject it
  through the host environment/secret manager.
- MFA/SSO, centralized audit/alerting, and scheduled off-host backups are not
  included. Assess whether organizational policy requires them before using
  real employee data.
