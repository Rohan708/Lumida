"""The job queue (Stage 4.5): "run now", connection tests and AI work, asked for in the web
app and done by ``lumida serve`` within seconds.

- **Queued twice, done once**: at most one queued-or-running job of a kind per source
  (a unique index); asking again returns the job already waiting.
- **One worker at a time per job**: a job is claimed with ``FOR UPDATE SKIP LOCKED``, so
  two containers never take the same one. A run also takes the source's advisory lock,
  like the nightly run; if the nightly run holds it, the job waits.
- **Never a URL in a message**: errors are stored without anything that looks like a
  connection URL (drivers sometimes include one).
- A job left "running" by a service that stopped is marked failed on the next start.
"""

import logging
import re
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import Engine, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lumida.graph.models import (
    ACTIVE_JOB_STATUSES,
    DataSource,
    Finding,
    Job,
    JobKind,
    JobStatus,
    SourceKind,
    utcnow,
)
from lumida.ingestion.scan import AdapterFactory, open_adapter
from lumida.ingestion.settings import ScanSettings
from lumida.reasoning.llm import LLMClient
from lumida.service.runner import run_source, source_lock

log = logging.getLogger("lumida.jobs")

LLMFactory = Callable[[], LLMClient]
EXPLAIN_LIMIT = 10  # findings explained per "explain" job without a specific finding
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S+")


def scrub(message: str, limit: int = 500) -> str:
    return _URL.sub("<connection URL hidden>", message)[:limit]


def enqueue(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    kind: JobKind,
    by: str,
    source: DataSource | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[Job, bool]:
    """Queue a job, or return the one of this kind already waiting for this source.
    Returns (job, created)."""
    if source is not None:
        waiting = _active(session, source.id, kind)
        if waiting is not None:
            return waiting, False
    job = Job(
        tenant_id=tenant_id, data_source_id=source.id if source else None, kind=kind,
        payload=payload or {}, requested_by=by,
    )  # fmt: skip
    try:
        with session.begin_nested():
            session.add(job)
    except IntegrityError:  # someone queued the same job a moment ago
        assert source is not None  # noqa: S101 - only per-source jobs are unique
        waiting = _active(session, source.id, kind)
        if waiting is None:
            raise
        return waiting, False
    return job, True


def _active(session: Session, source_id: uuid.UUID, kind: JobKind) -> Job | None:
    return session.scalar(
        select(Job).where(
            Job.data_source_id == source_id, Job.kind == kind, Job.status.in_(ACTIVE_JOB_STATUSES)
        )
    )


def recover_interrupted(engine: Engine) -> int:
    """Jobs a stopped service left running can't finish: say so."""
    with Session(engine) as session:
        done = session.execute(
            update(Job)
            .where(Job.status == JobStatus.RUNNING)
            .values(
                status=JobStatus.FAILED,
                finished_at=utcnow(),
                error="interrupted: the service stopped while it was running",
            )
        )
        session.commit()
        return int(getattr(done, "rowcount", 0) or 0)


def claim_next(engine: Engine) -> uuid.UUID | None:
    with Session(engine) as session:
        job = session.scalar(
            select(Job)
            .where(Job.status == JobStatus.QUEUED)
            .order_by(Job.requested_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        job.status, job.started_at = JobStatus.RUNNING, utcnow()
        session.commit()
        return job.id


def work_once(
    engine: Engine,
    *,
    adapter_factory: AdapterFactory = open_adapter,
    llm_factory: LLMFactory | None = None,
) -> uuid.UUID | None:
    """Do the oldest queued job, if any. Returns its id."""
    job_id = claim_next(engine)
    if job_id is not None:
        run_job(engine, job_id, adapter_factory=adapter_factory, llm_factory=llm_factory)
    return job_id


def run_job(
    engine: Engine,
    job_id: uuid.UUID,
    *,
    adapter_factory: AdapterFactory = open_adapter,
    llm_factory: LLMFactory | None = None,
) -> None:
    with Session(engine) as session:
        job = session.get_one(Job, job_id)
        kind, source_id, payload = job.kind, job.data_source_id, dict(job.payload)
    log.info("job %s (%s): starting", job_id, kind.value)
    try:
        if kind is JobKind.RUN:
            assert source_id is not None  # noqa: S101 - runs always have a source
            outcome = _run(engine, source_id, adapter_factory)
            if outcome is None:  # the nightly run has it: try again shortly
                _requeue(engine, job_id)
                return
            ok, result = outcome
        elif kind is JobKind.TEST_CONNECTION:
            ok, result = True, _test(engine, source_id, payload, adapter_factory)
        else:
            if llm_factory is None:
                raise RuntimeError(
                    "AI features are off: set LUMIDA_LLM_PROVIDER and its API key in .env"
                )
            ok, result = True, _ai(engine, kind, source_id, payload, llm_factory())
        _finish(engine, job_id, ok, result, None if ok else result.get("error"))
    except Exception as exc:  # a job must never take the service down
        log.exception("job %s failed", job_id)
        _finish(engine, job_id, False, {}, scrub(f"{type(exc).__name__}: {exc}"))


def _finish(
    engine: Engine, job_id: uuid.UUID, ok: bool, result: dict[str, Any], error: str | None
) -> None:
    status = JobStatus.SUCCEEDED if ok else JobStatus.FAILED
    with Session(engine) as session:
        job = session.get_one(Job, job_id)
        job.status, job.finished_at, job.result, job.error = status, utcnow(), result, error
        session.commit()
    log.info("job %s: %s", job_id, status.value)


def _requeue(engine: Engine, job_id: uuid.UUID) -> None:
    with Session(engine) as session:
        job = session.get_one(Job, job_id)
        job.status, job.started_at = JobStatus.QUEUED, None
        session.commit()


def _run(
    engine: Engine, source_id: uuid.UUID, adapter_factory: AdapterFactory
) -> tuple[bool, dict[str, Any]] | None:
    with source_lock(engine, source_id) as locked:
        if not locked:
            return None
        with Session(engine) as session:
            source = session.get_one(DataSource, source_id)
            session.expunge(source)
        ok, summary = run_source(engine, source, adapter_factory)
    lines = summary.splitlines()
    failed = next((line.strip() for line in lines[1:] if line.strip().startswith("ERR")), None)
    result: dict[str, Any] = {"summary": lines}
    if not ok:
        result["error"] = scrub(failed or lines[0])
    return ok, result


def _test(
    engine: Engine,
    source_id: uuid.UUID | None,
    payload: dict[str, Any],
    adapter_factory: AdapterFactory,
) -> dict[str, Any]:
    """Log in read-only and list what can be seen. Names and counts only."""
    if source_id is not None:
        with Session(engine) as session:
            source = session.get_one(DataSource, source_id)
            session.expunge(source)
    else:  # a database not added yet
        settings = ScanSettings.model_validate(payload.get("settings", {}))
        source = DataSource(
            name=payload.get("name", "new"), kind=SourceKind(payload["kind"]),
            connection_ref=payload["connection_ref"],
            settings=settings.model_dump(mode="json", exclude_defaults=True),
        )  # fmt: skip
    with adapter_factory(source) as adapter:
        tables = adapter.introspect()
    schemas: dict[str, int] = {}
    for t in tables:
        schemas[t.observation.schema_name] = schemas.get(t.observation.schema_name, 0) + 1
    return {
        "tables": sum(1 for t in tables if t.profilable),
        "objects": len(tables),
        "schemas": dict(sorted(schemas.items())),
    }


def _ai(
    engine: Engine,
    kind: JobKind,
    source_id: uuid.UUID | None,
    payload: dict[str, Any],
    llm: LLMClient,
) -> dict[str, Any]:
    from lumida.reasoning.explain import explain_findings, unexplained
    from lumida.reasoning.propose import propose_rules

    assert source_id is not None  # noqa: S101 - AI jobs always have a source
    with Session(engine) as session:
        source = session.get_one(DataSource, source_id)
        if kind is JobKind.PROPOSE_RULES:
            proposed = propose_rules(session, source, llm)
            session.commit()
            return {"summary": [proposed.summary()], "proposed": len(proposed.proposed)}
        if payload.get("finding_id"):
            finding = session.get_one(Finding, uuid.UUID(payload["finding_id"]))
            findings = [finding] if finding.data_source_id == source.id else []
        else:
            findings = unexplained(session, source, EXPLAIN_LIMIT)
        explained = explain_findings(session, source, llm, findings)
        session.commit()
        return {"summary": [explained.summary()], "rejected": explained.rejected}
