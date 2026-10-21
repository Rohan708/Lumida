"""The Lumida API: what the web app (4.4) and a customer's own scripts use.

- **Personal logins** (Stage 4.3): ``POST /api/v1/auth/login`` with email + password
  returns a token; every other request sends it as ``Authorization: Bearer <token>``.
  Scripts use personal API keys (``/api/v1/auth/keys``). Roles: viewer, reviewer, admin.
- The web app (Stage 4.4) is served from the same address: ``/`` (files in ``lumida/web``).
- Runs inside ``lumida serve`` (one container), or alone with ``lumida api``.
- Interactive docs at ``/api/docs``, the machine-readable description at ``/api/openapi.json``.
"""

import logging
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy import Engine

from lumida import __version__
from lumida.accounts.passwords import WeakPasswordError
from lumida.accounts.store import AccountError, AccountNotFoundError, AuthPolicy
from lumida.api import account_routes, onboarding_routes, routes, setup_routes
from lumida.api.views import NotFoundError
from lumida.context.questions import AnswerError
from lumida.context.store import ContextError
from lumida.detection.recording import InvalidTransitionError
from lumida.ingestion.sources import SourceExistsError, SourceNotFoundError
from lumida.rules.store import InvalidRuleError
from lumida.service.first_run import SetupState

log = logging.getLogger("lumida.api")

# A request that can't be done as asked (the CLI prints these as one line): the API returns
# {"detail": "..."} with 404, 409 or 422. Anything else is a bug and stays a 500.
NOT_FOUND = (SourceNotFoundError, NotFoundError, AccountNotFoundError)
CONFLICT = (
    InvalidTransitionError,
    InvalidRuleError,
    AnswerError,
    ContextError,
    AccountError,
    SourceExistsError,
)
INVALID = (WeakPasswordError,)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
WEB_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)


def create_app(
    engine: Engine,
    policy: AuthPolicy | None = None,
    *,
    ai_enabled: bool = False,
    setup: SetupState | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Lumida API",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.engine = engine
    app.state.policy = policy or AuthPolicy()
    app.state.ai_enabled = ai_enabled  # whether to offer "propose rules" / "explain"
    # First admin from the browser, while nobody can log in yet (0.4.1).
    app.state.setup = setup or SetupState(code=None, engine=engine)
    app.include_router(setup_routes.router)
    app.include_router(account_routes.router)
    app.include_router(routes.router)
    app.include_router(onboarding_routes.router)
    app.middleware("http")(_security_headers)
    # Last: everything that isn't the API is the web app's own files.
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    for errors, code in ((NOT_FOUND, 404), (CONFLICT, 409), (INVALID, 422)):
        for error in errors:
            app.add_exception_handler(error, _reply(code))

    @app.exception_handler(ValidationError)
    def invalid(_request: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse({"detail": _validation(exc)}, status_code=422)  # e.g. a rule spec

    return app


async def _security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if not request.url.path.startswith("/api/"):
        # The web app runs only its own files: no inline scripts, nothing from elsewhere,
        # never inside another site's frame.
        response.headers.setdefault("Content-Security-Policy", WEB_CSP)
        response.headers.setdefault("X-Frame-Options", "DENY")
    return response


def _reply(code: int) -> Callable[[Request, Exception], JSONResponse]:
    def handle(_request: Request, exc: Exception) -> JSONResponse:
        detail = str(exc.args[0]) if exc.args else type(exc).__name__
        return JSONResponse({"detail": detail}, status_code=code)

    return handle


def _validation(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" if e["loc"] else e["msg"]
        for e in exc.errors(include_url=False, include_input=False)
    )


def create_setup_app(state: SetupState) -> FastAPI:
    """Setup mode: ``lumida serve`` before Lumida's memory exists. Only the setup steps and
    the web app's files; it's replaced by the normal app once setup is done."""
    app = FastAPI(title="Lumida setup", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url=None)  # fmt: skip
    app.state.setup = state
    app.state.setup_mode = True
    app.include_router(setup_routes.router)
    app.middleware("http")(_security_headers)
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


def api_server(app: FastAPI, host: str, port: int) -> uvicorn.Server:
    """A uvicorn server that logs through our own logging setup (JSON in the image)."""
    config = uvicorn.Config(app, host=host, port=port, log_config=None, access_log=True)
    return uvicorn.Server(config)


def start_api_thread(server: uvicorn.Server) -> threading.Thread:
    """Run the API next to the scheduler. Uvicorn leaves signal handling to the main
    thread when it isn't in it, so ``docker stop`` still reaches the scheduler."""
    thread = threading.Thread(target=server.run, name="lumida-api", daemon=True)
    thread.start()
    return thread
