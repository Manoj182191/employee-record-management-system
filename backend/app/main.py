import logging
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api import create_api
from app.advanced_api import create_advanced_api
from app.config import Settings
from app.database.db import initialize, session
from app.hashmap import EmployeeHashMap


logger = logging.getLogger("erms")


def create_app(settings_override: Settings | None = None) -> FastAPI:
    settings = settings_override or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        settings.validate()
        initialize(settings)
        employee_map: EmployeeHashMap[str, dict] = EmployeeHashMap()
        with session(settings) as connection:
            for row in connection.execute("SELECT * FROM employees ORDER BY employee_id"):
                item = dict(row)
                employee_map.put(item["employee_id"], item)
        application.state.employee_map = employee_map
        logger.info("ERMS API initialized with %d employee records", employee_map.size)
        yield

    application = FastAPI(
        title="Employee Record Management System API",
        version="2.0.0",
        description="Role-based employee records and workforce reporting API.",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    application.include_router(create_api(settings))
    application.include_router(create_advanced_api(settings))

    @application.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/health/ready", tags=["health"])
    def readiness() -> dict[str, str]:
        try:
            with session(settings) as connection:
                connection.execute("SELECT 1").fetchone()
        except sqlite3.Error as exc:
            logger.exception("ERMS readiness check failed")
            raise HTTPException(503, "Database is not ready.") from exc
        return {"status": "ready"}

    return application


app = create_app()
