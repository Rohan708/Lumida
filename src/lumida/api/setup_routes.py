"""First-run setup in the browser, under ``/api/setup`` (0.4.1). Served both by setup mode
(no memory yet) and by the normal app (memory, but no admin yet). Every change needs the
one-time setup code (header ``X-Lumida-Setup-Code``) printed where Lumida was started.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from lumida.service.first_run import (
    SetupCodeError,
    SetupError,
    SetupState,
    create_first_admin,
    docker_available,
    prepare_existing,
    probe,
    remember_memory,
    start_docker_postgres,
)

router = APIRouter(prefix="/api/setup")
CodeHeader = Annotated[str | None, Header(alias="X-Lumida-Setup-Code")]


class Status(BaseModel):
    ready: bool  # nothing left to set up: show the normal login
    needs_memory: bool
    needs_admin: bool
    setup_mode: bool  # served by `lumida serve` before memory existed (it restarts itself)
    docker: bool | None = None  # can Lumida start a private Postgres? (asked only when useful)


class ExistingPostgres(BaseModel):
    host: str = Field(default="localhost", min_length=1, max_length=255)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(default="lumida", min_length=1, max_length=63)
    user: str = Field(default="postgres", min_length=1, max_length=63)
    password: str = Field(default="", max_length=1024)


class MemoryChoice(BaseModel):
    mode: Literal["docker", "existing"]
    existing: ExistingPostgres | None = None


class FirstAdmin(BaseModel):
    name: str = Field(default="", max_length=200)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class ProbeOut(BaseModel):
    ok: bool
    message: str
    will_create: bool


def _state(request: Request) -> SetupState:
    state: SetupState | None = getattr(request.app.state, "setup", None)
    if state is None:
        raise HTTPException(409, "Lumida is already set up")
    return state


def _checked(request: Request, code: str | None) -> SetupState:
    state = _state(request)
    try:
        state.check(code)
    except SetupCodeError as exc:
        raise HTTPException(403, str(exc)) from None
    except SetupError as exc:
        raise HTTPException(409, str(exc)) from None
    return state


@router.get("/status")
def status(request: Request) -> Status:
    state: SetupState | None = getattr(request.app.state, "setup", None)
    setup_mode = bool(getattr(request.app.state, "setup_mode", False))
    if state is None:
        return Status(ready=True, needs_memory=False, needs_admin=False, setup_mode=False)
    needs_memory = state.engine is None
    needs_admin = state.needs_admin()
    ready = not setup_mode and not needs_admin
    docker = docker_available() if needs_memory else None
    return Status(ready=ready, needs_memory=needs_memory, needs_admin=needs_admin,
                  setup_mode=setup_mode, docker=docker)  # fmt: skip


@router.post("/memory/test")
def check_memory(body: ExistingPostgres, request: Request, code: CodeHeader = None) -> ProbeOut:
    _checked(request, code)
    found = probe(body.host, body.port, body.database, body.user, body.password)
    return ProbeOut(ok=found.ok, message=found.message, will_create=found.will_create)


@router.post("/memory")
def choose_memory(body: MemoryChoice, request: Request, code: CodeHeader = None) -> Status:
    state = _checked(request, code)
    if state.engine is not None:
        raise HTTPException(409, "Lumida's memory is already set up")
    try:
        if body.mode == "docker":
            url = start_docker_postgres()
        else:
            e = body.existing or ExistingPostgres()
            url = prepare_existing(e.host, e.port, e.database, e.user, e.password)
        remember_memory(state, url)
    except SetupError as exc:
        raise HTTPException(409, str(exc)) from None
    return status(request)


@router.post("/admin")
def first_admin(body: FirstAdmin, request: Request, code: CodeHeader = None) -> Status:
    state = _checked(request, code)
    if state.engine is None:
        raise HTTPException(409, "set up Lumida's memory first")
    try:
        email = create_first_admin(state.engine, name=body.name, email=body.email,
                                   password=body.password)  # fmt: skip
    except (SetupError, ValueError) as exc:  # weak password, bad email, admin exists
        raise HTTPException(422 if "password" in str(exc) else 409, str(exc)) from None
    state.admin_ready = True
    state.code = None  # one-time: setup is over
    state.finished.set()
    request.app.state.setup_done_for = email
    return status(request)
