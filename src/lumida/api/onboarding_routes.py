"""Onboarding and jobs (Stage 4.5), under ``/api/v1``: adding a database from the web app,
testing its connection, "run now", AI work, and following a job until it's done.

Connection URLs never pass through here: a database is added by the *name* of a setting
in the server's ``.env`` (``GET /connections`` lists the names, never the values).
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from lumida import __version__
from lumida.accounts.store import audit
from lumida.api.auth import DB, Admin, Reviewer, Viewer
from lumida.api.schemas import SourceOut
from lumida.api.views import NotFoundError, owned, source_out
from lumida.context.store import ContextError
from lumida.graph.models import DataSource, Finding, Job, JobKind, Role, SourceKind
from lumida.ingestion.connections import available_connections
from lumida.ingestion.settings import ScanSettings
from lumida.ingestion.sources import add_source, get_source
from lumida.onboarding.jobs import enqueue
from lumida.onboarding.setup_scripts import SETUP_SCRIPTS

router = APIRouter(prefix="/api/v1")
KINDS = Literal["postgres", "mysql", "sqlserver", "oracle", "snowflake"]


class About(BaseModel):
    version: str
    ai_enabled: bool  # whether "propose rules" and "explain" can be offered


class Connection(BaseModel):
    name: str
    kind: str  # read from the URL's scheme


class NewSource(BaseModel):
    name: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.-]+$")
    kind: KINDS
    connection_ref: str = Field(min_length=1, max_length=500)
    run_at: str | None = Field(default="03:00", description='local "HH:MM"; null = not scheduled')
    timezone: str = "UTC"
    include_schemas: list[str] = Field(default_factory=list)
    exclude_schemas: list[str] = Field(default_factory=list)


class ConnectionTest(BaseModel):
    kind: KINDS
    connection_ref: str = Field(min_length=1, max_length=500)
    include_schemas: list[str] = Field(default_factory=list)
    exclude_schemas: list[str] = Field(default_factory=list)


class NewJob(BaseModel):
    kind: Literal["run", "test_connection", "propose_rules", "explain"]
    finding_id: uuid.UUID | None = None  # "explain": one finding (default: up to 10 open)


class JobOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    source: str | None
    requested_by: str
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    result: dict[str, Any]
    error: str | None


def job_out(session: DB, job: Job) -> JobOut:
    name = session.get_one(DataSource, job.data_source_id).name if job.data_source_id else None
    return JobOut(
        id=job.id, kind=job.kind.value, status=job.status.value, source=name,
        requested_by=job.requested_by, requested_at=job.requested_at, started_at=job.started_at,
        finished_at=job.finished_at, result=job.result, error=job.error,
    )  # fmt: skip


def _settings(include: list[str], exclude: list[str], **more: Any) -> dict[str, Any]:
    raw = {"include_schemas": include, "exclude_schemas": exclude, **more}
    return ScanSettings.model_validate(raw).model_dump(mode="json", exclude_defaults=True)


def _offered(connection_ref: str) -> None:
    if connection_ref not in available_connections():
        raise ContextError(
            f"{connection_ref!r} isn't a database URL in the server's .env "
            "(add it there, restart Lumida, then pick it here)"
        )


# --- what the web app needs to know --------------------------------------------------------


@router.get("/about")
def about(request: Request, me: Viewer) -> About:
    return About(version=__version__, ai_enabled=request.app.state.ai_enabled)


@router.get("/connections")
def connections(me: Admin) -> list[Connection]:
    """Settings in the server's .env that hold a database URL: names and kinds only."""
    return [Connection(name=n, kind=k) for n, k in available_connections().items()]


@router.get("/setup-scripts/{kind}")
def setup_script(kind: KINDS, me: Admin) -> dict[str, str]:
    """The SQL that creates Lumida's read-only login on this kind of database."""
    return {"kind": kind, "script": SETUP_SCRIPTS[kind]}


# --- adding a database ---------------------------------------------------------------------


@router.post("/connection-tests", status_code=202)
def test_connection(body: ConnectionTest, session: DB, me: Admin) -> JobOut:
    """Check a connection before adding it: logs in read-only and counts what it can see."""
    _offered(body.connection_ref)
    payload = {
        "kind": body.kind,
        "connection_ref": body.connection_ref,
        "settings": _settings(body.include_schemas, body.exclude_schemas),
    }
    job, _ = enqueue(session, tenant_id=me.tenant_id, kind=JobKind.TEST_CONNECTION, by=me.email,
                     payload=payload)  # fmt: skip
    audit(session, me.tenant_id, me.email, "job.request", job, {"kind": "test_connection",
          "connection_ref": body.connection_ref})  # fmt: skip
    session.commit()
    return job_out(session, job)


@router.post("/sources", status_code=201)
def add_database(body: NewSource, session: DB, me: Admin) -> SourceOut:
    _offered(body.connection_ref)
    settings = _settings(body.include_schemas, body.exclude_schemas, run_at=body.run_at,
                         timezone=body.timezone)  # fmt: skip
    source = add_source(session, name=body.name, kind=SourceKind(body.kind),
                        connection_ref=body.connection_ref, settings=settings,
                        tenant_id=me.tenant_id)  # fmt: skip
    details = {"name": source.name, "kind": body.kind, "connection_ref": body.connection_ref}
    audit(session, me.tenant_id, me.email, "source.add", source, details)
    session.commit()
    return source_out(session, source)


# --- jobs ----------------------------------------------------------------------------------

JOB_ROLES = {"run": Role.REVIEWER, "test_connection": Role.ADMIN,
             "propose_rules": Role.REVIEWER, "explain": Role.REVIEWER}  # fmt: skip


@router.post("/sources/{name}/jobs", status_code=202)
def start_job(name: str, body: NewJob, request: Request, session: DB, me: Reviewer) -> JobOut:
    """Run now, test the connection, or ask the AI. Asking again while one is waiting
    returns the waiting job."""
    needed = JOB_ROLES[body.kind]
    if not me.can(needed):
        raise HTTPException(403, f"this needs the {needed.value} role (you are {me.role.value})")
    kind = JobKind(body.kind)
    if kind in (JobKind.PROPOSE_RULES, JobKind.EXPLAIN) and not request.app.state.ai_enabled:
        raise ContextError("AI features are off (LUMIDA_LLM_PROVIDER in the server's .env)")
    source = get_source(session, name, me.tenant_id)
    payload: dict[str, Any] = {}
    if body.finding_id is not None:
        finding = owned(session, Finding, body.finding_id, me.tenant_id)
        if finding.data_source_id != source.id:
            raise NotFoundError(f"no finding {body.finding_id} in {name}")
        payload["finding_id"] = str(finding.id)
    job, created = enqueue(session, tenant_id=me.tenant_id, kind=kind, by=me.email,
                           source=source, payload=payload)  # fmt: skip
    if created:
        audit(session, me.tenant_id, me.email, "job.request", job, {"kind": kind.value,
              "source": name})  # fmt: skip
    session.commit()
    return job_out(session, job)


@router.get("/sources/{name}/jobs")
def source_jobs(
    name: str, session: DB, me: Viewer, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[JobOut]:
    source = get_source(session, name, me.tenant_id)
    jobs = session.scalars(
        select(Job)
        .where(Job.data_source_id == source.id)
        .order_by(Job.requested_at.desc())
        .limit(limit)
    )
    return [job_out(session, j) for j in jobs]


@router.get("/jobs/{job_id}")
def job(job_id: uuid.UUID, session: DB, me: Viewer) -> JobOut:
    found = session.get(Job, job_id)
    if found is None or found.tenant_id != me.tenant_id:
        raise NotFoundError(f"no job with id {job_id}")
    return job_out(session, found)
