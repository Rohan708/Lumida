"""Stage 4.5 against real Postgres: an admin adds the test lab from the API by the name of
its setting, tests the connection, presses "run now" (twice: it runs once), and the job
queue does the work like `lumida serve` would. Roles, locks, failures and tenants too."""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from lumida.accounts.store import create_user
from lumida.api.app import create_app
from lumida.db import create_db_engine
from lumida.graph.models import DataSource, IngestionRun, Job, JobKind, JobStatus, Role, Tenant
from lumida.ingestion.sources import default_tenant, get_source
from lumida.lab.runner import build_lab
from lumida.onboarding.jobs import enqueue, recover_interrupted, work_once
from lumida.service.migrate import upgrade_to_head
from lumida.service.runner import source_lock

PASSWORD = "a long test password"
ADMIN, REVIEWER, VIEWER, OUTSIDER = (
    "admin@shop.example", "rev@shop.example", "view@shop.example", "boss@other.example",
)  # fmt: skip
LAB_REF = "ONBOARDING_LAB_URL"  # set to the lab's URL for this module


@pytest.fixture(scope="module")
def store(make_database: Callable[[], str]) -> Iterator[tuple[Engine, str]]:
    url, lab_url = make_database(), make_database()
    upgrade_to_head(url)
    lab = create_db_engine(lab_url)
    build_lab(lab, seed=11, size="tiny")
    lab.dispose()
    engine = create_db_engine(url)
    with Session(engine) as session:
        tenant = default_tenant(session)
        for email, role in ((ADMIN, Role.ADMIN), (REVIEWER, Role.REVIEWER), (VIEWER, Role.VIEWER)):
            create_user(session, tenant_id=tenant.id, email=email, name="", role=role,
                        password=PASSWORD, by="test")  # fmt: skip
        other = Tenant(name="other")
        session.add(other)
        session.flush()
        create_user(session, tenant_id=other.id, email=OUTSIDER, name="", role=Role.ADMIN,
                    password=PASSWORD, by="test")  # fmt: skip
        session.commit()
    yield engine, lab_url
    engine.dispose()


@pytest.fixture
def client(store: tuple[Engine, str], monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv(LAB_REF, store[1])
    with TestClient(create_app(store[0])) as c:
        yield c


def _as(client: TestClient, email: str) -> dict[str, str]:
    reply = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert reply.status_code == 200, reply.text
    return {"Authorization": f"Bearer {reply.json()['token']}"}


def _ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.text
    return response.json()


def _job(engine: Engine, job_id: str) -> Job:
    with Session(engine) as session:
        job = session.get_one(Job, job_id)
        session.expunge(job)
        return job


def test_add_test_and_run_a_database(client: TestClient, store: tuple[Engine, str]) -> None:
    engine, lab_url = store
    admin = _as(client, ADMIN)
    offered = _ok(client.get("/api/v1/connections", headers=admin))
    assert {"name": LAB_REF, "kind": "postgres"} in offered
    assert lab_url not in client.get("/api/v1/connections", headers=admin).text  # names only
    script = _ok(client.get("/api/v1/setup-scripts/postgres", headers=admin))["script"]
    assert "lumida_readonly" in script

    scope = {"exclude_schemas": ["aide_lab"]}
    body = {"kind": "postgres", "connection_ref": LAB_REF, **scope}
    test = _ok(client.post("/api/v1/connection-tests", headers=admin, json=body), 202)
    assert test["status"] == "queued"
    assert work_once(engine) is not None  # what `lumida serve` does every 2 seconds
    tested = _ok(client.get(f"/api/v1/jobs/{test['id']}", headers=admin))
    assert tested["status"] == "succeeded", tested
    assert tested["result"]["tables"] > 5
    assert set(tested["result"]["schemas"]) == {"shop", "legacy", "reporting"}

    new = {"name": "shop", "kind": "postgres", "connection_ref": LAB_REF, "run_at": "02:30",
           "timezone": "Asia/Kolkata", **scope}  # fmt: skip
    added = _ok(client.post("/api/v1/sources", headers=admin, json=new), 201)
    assert (added["name"], added["run_at"], added["timezone"]) == ("shop", "02:30", "Asia/Kolkata")
    assert client.post("/api/v1/sources", headers=admin, json=new).status_code == 409

    reviewer = _as(client, REVIEWER)
    first = _ok(
        client.post("/api/v1/sources/shop/jobs", headers=reviewer, json={"kind": "run"}), 202
    )
    again = _ok(
        client.post("/api/v1/sources/shop/jobs", headers=reviewer, json={"kind": "run"}), 202
    )
    assert first["id"] == again["id"]  # pressed twice, queued once
    assert work_once(engine) is not None
    assert work_once(engine) is None  # nothing else waiting
    done = _ok(client.get(f"/api/v1/jobs/{first['id']}", headers=reviewer))
    assert done["status"] == "succeeded", done
    assert done["result"]["summary"][0] == "lumida run shop: ok"
    shop = _ok(client.get("/api/v1/sources/shop", headers=reviewer))
    assert shop["last_run"]["status"] == "succeeded"
    assert shop["baseline_done"]
    jobs = _ok(client.get("/api/v1/sources/shop/jobs", headers=reviewer))
    assert [j["id"] for j in jobs] == [first["id"]]
    log = _ok(client.get("/api/v1/audit", headers=admin, params={"action": "source.add"}))
    assert log[0]["actor"] == ADMIN


def test_only_offered_connections_can_be_added(client: TestClient) -> None:
    admin = _as(client, ADMIN)
    body = {"name": "metadata", "kind": "postgres", "connection_ref": "LUMIDA_DATABASE_URL"}
    refused = client.post("/api/v1/sources", headers=admin, json=body)
    assert refused.status_code == 409
    assert "isn't a database URL in the server's .env" in refused.json()["detail"]


def test_who_may_do_what(client: TestClient, store: tuple[Engine, str]) -> None:
    viewer, reviewer = _as(client, VIEWER), _as(client, REVIEWER)
    assert client.get("/api/v1/connections", headers=reviewer).status_code == 403
    body = {"name": "x", "kind": "postgres", "connection_ref": LAB_REF}
    assert client.post("/api/v1/sources", headers=reviewer, json=body).status_code == 403
    assert client.post("/api/v1/sources/shop/jobs", headers=viewer,
                       json={"kind": "run"}).status_code == 403  # fmt: skip
    test = client.post("/api/v1/sources/shop/jobs", headers=reviewer,
                       json={"kind": "test_connection"})  # fmt: skip
    assert test.status_code == 403
    ai = client.post("/api/v1/sources/shop/jobs", headers=reviewer, json={"kind": "propose_rules"})
    assert ai.status_code == 409  # AI features are off in this app
    assert "AI features are off" in ai.json()["detail"]
    assert client.get("/api/v1/sources/shop/jobs", headers=viewer).status_code == 200


def test_jobs_wait_for_a_run_in_progress(client: TestClient, store: tuple[Engine, str]) -> None:
    engine, _ = store
    reviewer = _as(client, REVIEWER)
    job = _ok(client.post("/api/v1/sources/shop/jobs", headers=reviewer, json={"kind": "run"}), 202)
    with Session(engine) as session:
        source_id = get_source(session, "shop").id
    with source_lock(engine, source_id) as locked:  # the nightly run has it
        assert locked
        work_once(engine)
        assert _job(engine, job["id"]).status is JobStatus.QUEUED  # put back, not lost
    work_once(engine)
    assert _job(engine, job["id"]).status is JobStatus.SUCCEEDED


def test_failures_are_reported_without_urls(
    client: TestClient, store: tuple[Engine, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _ = store
    monkeypatch.setenv("BROKEN_DB_URL", "postgresql+psycopg://lumida:hunter2@127.0.0.1:1/none")
    admin = _as(client, ADMIN)
    _ok(client.post("/api/v1/sources", headers=admin,
                    json={"name": "broken", "kind": "postgres", "connection_ref": "BROKEN_DB_URL"}),
        201)  # fmt: skip
    test = _ok(client.post("/api/v1/sources/broken/jobs", headers=admin,
                           json={"kind": "test_connection"}), 202)  # fmt: skip
    run = _ok(client.post("/api/v1/sources/broken/jobs", headers=admin, json={"kind": "run"}), 202)
    work_once(engine)
    work_once(engine)
    for job in (test, run):
        failed = _ok(client.get(f"/api/v1/jobs/{job['id']}", headers=admin))
        assert failed["status"] == "failed", failed
        assert failed["error"]
        assert "hunter2" not in client.get(f"/api/v1/jobs/{job['id']}", headers=admin).text
    with Session(engine) as session:  # the failed run is recorded like a nightly one
        source_id = get_source(session, "broken").id
        runs = session.scalars(select(IngestionRun).where(IngestionRun.data_source_id == source_id))
        assert [r.status.value for r in runs] == ["failed"]


def test_ai_jobs_without_ai_and_interrupted_jobs(store: tuple[Engine, str]) -> None:
    engine, _ = store
    with Session(engine) as session:
        tenant_id, shop_id = default_tenant(session).id, get_source(session, "shop").id
        shop = session.get_one(DataSource, shop_id)
        ai, _ = enqueue(
            session, tenant_id=tenant_id, kind=JobKind.PROPOSE_RULES, by="t", source=shop
        )
        session.commit()
        ai_id = ai.id
    work_once(engine)  # no AI configured in the worker
    failed = _job(engine, str(ai_id))
    assert failed.status is JobStatus.FAILED
    assert "AI features are off" in (failed.error or "")

    with Session(engine) as session:  # a fresh copy of the source: the last session is closed
        shop = session.get_one(DataSource, shop_id)
        stuck, _ = enqueue(session, tenant_id=tenant_id, kind=JobKind.RUN, by="t", source=shop)
        stuck.status = JobStatus.RUNNING  # the service stopped while it ran
        session.commit()
        stuck_id = stuck.id
    assert recover_interrupted(engine) == 1
    assert "interrupted" in (_job(engine, str(stuck_id)).error or "")


def test_other_tenants_see_nothing(client: TestClient) -> None:
    ours, theirs = _as(client, ADMIN), _as(client, OUTSIDER)
    (job, *_) = _ok(client.get("/api/v1/sources/shop/jobs", headers=ours))
    assert client.get(f"/api/v1/jobs/{job['id']}", headers=theirs).status_code == 404
    assert client.get("/api/v1/sources/shop/jobs", headers=theirs).status_code == 404
    refused = client.post("/api/v1/sources/shop/jobs", headers=theirs, json={"kind": "run"})
    assert refused.status_code == 404


# --- changing and removing a database, rules download (Stage 4.6) -------------------------


def test_change_pause_and_download_rules(client: TestClient) -> None:
    admin, reviewer = _as(client, ADMIN), _as(client, REVIEWER)
    change = {"run_at": "01:15", "timezone": "Europe/Berlin", "exclude_schemas": ["aide_lab"]}
    changed = _ok(client.patch("/api/v1/sources/shop", headers=admin, json=change))
    assert (changed["run_at"], changed["timezone"]) == ("01:15", "Europe/Berlin")
    assert changed["exclude_schemas"] == ["aide_lab"]
    assert client.patch("/api/v1/sources/shop", headers=reviewer, json=change).status_code == 403
    bad = client.patch("/api/v1/sources/shop", headers=admin, json={"timezone": "Mars/Base"})
    assert bad.status_code == 422

    paused = _ok(client.patch("/api/v1/sources/shop", headers=admin, json={"paused": True}))
    assert paused["disabled"] is True
    assert paused["run_at"] == "01:15"  # pausing keeps the schedule
    resumed = _ok(client.patch("/api/v1/sources/shop", headers=admin, json={"paused": False}))
    assert resumed["disabled"] is False

    download = client.get("/api/v1/sources/shop/rules/export", headers=reviewer)
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    assert download.text.startswith("# Lumida rules for shop.")


def test_removing_a_database_needs_its_name(client: TestClient, store: tuple[Engine, str]) -> None:
    engine, _ = store
    admin = _as(client, ADMIN)
    with Session(engine) as session:
        broken_id = get_source(session, "broken").id
        assert session.scalars(select(Job).where(Job.data_source_id == broken_id)).all()
    refused = client.delete("/api/v1/sources/broken", headers=admin, params={"confirm": "brokn"})
    assert refused.status_code == 409
    assert "repeat its name" in refused.json()["detail"]
    gone = client.delete("/api/v1/sources/broken", headers=admin, params={"confirm": "broken"})
    assert gone.status_code == 204
    assert client.get("/api/v1/sources/broken", headers=admin).status_code == 404
    with Session(engine) as session:  # its jobs and runs went with it
        assert session.scalars(select(Job).where(Job.data_source_id == broken_id)).all() == []
    log = _ok(client.get("/api/v1/audit", headers=admin, params={"action": "source.remove"}))
    assert log[0]["details"]["name"] == "broken"
