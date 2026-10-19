# Design: onboarding and the job queue (Stage 4.5)

**Status:** built 2026-10-18. Lint, types and the quick unit tests are clean. The "Add a database" form and a connection test were checked in the browser against the local test lab. The full test suite passes.
**Code:**
- `onboarding/jobs.py` (queue + worker) and `onboarding/setup_scripts.py`
- `api/onboarding_routes.py`
- `ingestion/connections.py` (`available_connections`)
- `graph/models/jobs.py`, migration `0007`
- the web app's *Add a database* page, *Run now*, and the AI buttons

## 1. Goal
An admin adds a database, tests it, and sees its first results without opening a terminal. Anyone allowed can press *Run now*, or ask the AI for rules and explanations, from the browser.

## 2. Decisions
| | Decision |
|---|---|
| **Passwords stay on the server** | a connection URL (with its password) lives only in the server's `.env`. The web app picks it by **name**: `GET /connections` lists the settings whose value is a database URL, with the kind read from the URL's scheme, never the value. Lumida's own metadata store is never offered. Adding a database with any other name is refused. |
| **Setup scripts** | a copy-paste script per kind of database (Postgres, MySQL, SQL Server, Oracle, Snowflake) creates the read-only login. Each one grants reading only and ends with an example `.env` line. |
| **Job queue** | a `job` table in Lumida's own database, so there's no Redis or extra service. Kinds: `run`, `test_connection`, `propose_rules`, `explain`. |
| **Who does the jobs** | `lumida serve` checks for jobs every `LUMIDA_JOB_POLL_SECONDS` (2) between its nightly-schedule checks. `lumida api` on its own does them on a background thread. |
| **Pressed twice, done once** | a unique index allows at most one queued-or-running job of a kind per database. Asking again returns the job already waiting. |
| **Never two workers on one job** | a job is claimed with `FOR UPDATE SKIP LOCKED`. A run also takes the database's advisory lock, like the nightly run; if the nightly run holds it, the job goes back to the queue and starts after. |
| **The same run as at night** | *Run now* runs the whole nightly job (scan → … → alert). The first run is the quiet baseline: it records what's normal and doesn't alert. |
| **Connection test** | logs in read-only and lists what it can see (metadata only): tables per schema. It works before the database is added, with the details given by name. |
| **No URLs in messages** | a job's error is stored with anything that looks like a connection URL replaced, because drivers sometimes include one. |
| **Restarts** | a job left "running" by a stopped service is marked failed ("interrupted") on the next start. |
| **Roles** | admins add databases and test connections. Reviewers can run now, propose rules and ask for explanations. Viewers see jobs and their outcome. |
| **AI** | the AI buttons appear only when AI is turned on (`LUMIDA_LLM_PROVIDER`). The AI still only proposes; people review. |
| **Audit** | adding a database and every requested job are in the audit log. |

## 3. Endpoints (under `/api/v1`)
| | |
|---|---|
| `GET /about` | version, whether AI is on |
| `GET /connections` | admins: setting names with a database URL, and their kind |
| `GET /setup-scripts/{kind}` | admins: the read-only login script |
| `POST /connection-tests` | admins: test before adding (`202` + a job) |
| `POST /sources` | admins: add a database (name, kind, connection name, schemas, schedule) |
| `POST /sources/{name}/jobs` | `{kind: run / test_connection / propose_rules / explain, finding_id?}` (`202` + a job) |
| `GET /sources/{name}/jobs` · `GET /jobs/{id}` | follow jobs: queued → running → succeeded / failed, with a result or an error |

## 4. Tests
- **Unit:**
  - the kind of database read from a URL
  - which settings are offered (never the metadata store, never a value)
  - URLs scrubbed from errors
  - the setup scripts only read
  - the endpoint list
- **Integration** (the test lab, through the API, with the worker run as `lumida serve` would):
  - connection test, adding the database, *Run now* pressed twice runs once, results show up
  - only offered connections can be added
  - roles
  - a job waits while the nightly run holds the lock
  - a broken database fails clearly, without its password
  - an AI job without AI configured fails clearly
  - interrupted jobs are marked
  - other tenants see nothing
- **By hand** (browser, local test lab): the form offers `AIDE_LAB_DATABASE_URL` by name, shows the Postgres script, and the connection test reported 14 tables in shop, legacy and reporting.

## 5. Not yet
- **Changing or removing a database from the browser** (schedule, schemas, removal): still `lumida source schedule` / `remove`.
- **Progress within a run** (which step it's on); the page shows queued / running / done.
- **Secrets-manager backends** (Vault, AWS/GCP/Azure) instead of `.env`, before the hosted version.
- **Stopping a job** once it has started.
