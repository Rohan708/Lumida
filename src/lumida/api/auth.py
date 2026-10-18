"""Who is calling, and may they? Every endpoint except login declares the role it needs:

- ``Viewer``: read anything in their own tenant
- ``Reviewer``: + review findings, rules and relationships, add rules, answer questions
- ``Admin``: + manage people, read the audit log

A request without a valid token gets 401; a valid one with too small a role gets 403.
Scripts send a token; the web app (Stage 4.4) is logged in by an HttpOnly cookie.
"""

from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from lumida.accounts.store import Principal, resolve
from lumida.graph.models import Role


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:  # closing rolls back uncommitted work
        yield session


DB = Annotated[Session, Depends(get_session)]
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))]
SESSION_COOKIE = "lumida_session"
CSRF_HEADER = "X-Lumida-CSRF"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
UNAUTHORIZED = HTTPException(401, "log in first (missing, expired or unknown token)",
                             headers={"WWW-Authenticate": "Bearer"})  # fmt: skip


def current_principal(request: Request, credentials: Credentials, session: DB) -> Principal:
    """From ``Authorization: Bearer`` (scripts, API keys) or the session cookie (the web
    app). A cookie is sent by the browser on its own, so a change made with one must also
    carry the ``X-Lumida-CSRF`` header: another website can't add that header to a request
    without our permission (no CORS is configured), so it can't act as the person."""
    if credentials is not None:
        raw = credentials.credentials
    else:
        raw = request.cookies.get(SESSION_COOKIE, "")
        if raw and request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != "1":
            raise HTTPException(
                403, f"a change made from the browser needs the {CSRF_HEADER} header"
            )
    principal = resolve(session, raw) if raw else None
    if principal is None:
        raise UNAUTHORIZED
    return principal


def set_session_cookie(response: Response, request: Request, token: str, expires: datetime) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        expires=expires,
        path="/",
        httponly=True,  # page scripts can't read it
        samesite="strict",  # not sent with requests started from other sites
        secure=request.url.scheme == "https",  # behind a TLS proxy (localhost works without)
    )


def need(role: Role) -> Callable[[Principal], Principal]:
    def check(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
        if not principal.can(role):
            raise HTTPException(403, f"this needs the {role.value} role "
                                     f"(you are {principal.role.value})")  # fmt: skip
        return principal

    return check


Viewer = Annotated[Principal, Depends(need(Role.VIEWER))]
Reviewer = Annotated[Principal, Depends(need(Role.REVIEWER))]
Admin = Annotated[Principal, Depends(need(Role.ADMIN))]
