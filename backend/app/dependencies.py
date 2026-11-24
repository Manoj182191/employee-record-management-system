from typing import Annotated, Any, Callable, Literal

import jwt
from fastapi import Depends, Header, HTTPException

from app.config import Settings
from app.database.db import session


Role = Literal["admin", "employee"]
CurrentUser = dict[str, Any]


def current_user_dependency(settings: Settings) -> Callable[..., CurrentUser]:
    def current_user(
        authorization: Annotated[str | None, Header()] = None,
    ) -> CurrentUser:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "Authentication required.")
        token = authorization[7:]
        try:
            claims = jwt.decode(
                token,
                settings.jwt_secret,
                algorithms=["HS256"],
                options={"require": ["exp", "sub"]},
            )
        except jwt.InvalidTokenError as exc:
            raise HTTPException(401, "Invalid or expired access token.") from exc
        username = claims.get("sub")
        if not isinstance(username, str):
            raise HTTPException(401, "Invalid access token.")
        with session(settings) as connection:
            user = connection.execute(
                """SELECT u.username, u.role, u.employee_id, u.active,
                          u.token_version, e.status AS employee_status,
                          e.active AS employee_active
                   FROM users u
                   LEFT JOIN employees e ON e.employee_id = u.employee_id
                   WHERE u.username = ?""",
                (username,),
            ).fetchone()
        if user is None or not user["active"]:
            raise HTTPException(401, "Account is inactive or no longer exists.")
        if user["role"] == "employee" and (
            user["employee_status"] != "Approved"
            or not user["employee_active"]
        ):
            raise HTTPException(401, "Employee account is not approved or is inactive.")
        token_version = claims.get("ver", 0)
        if (
            not isinstance(token_version, int)
            or isinstance(token_version, bool)
            or token_version != user["token_version"]
        ):
            raise HTTPException(401, "Invalid or expired access token.")
        return dict(user)

    return current_user


def role_dependency(current_user: Callable[..., CurrentUser]):
    def require_roles(*roles: Role):
        def dependency(
            user: Annotated[CurrentUser, Depends(current_user)],
        ) -> CurrentUser:
            if user["role"] not in roles:
                raise HTTPException(403, "This action is not permitted for your role.")
            return user

        return dependency

    return require_roles
