# Design: first-run setup in the browser (0.4.1)

**Status:** built 2026-10-21. Tested in a browser from a clean folder: the pip route with an existing Postgres, the "Start one for me" Docker route, the wrong-code and wrong-password cases, and the hand-over to the normal app. Unit and integration tests pass.
**Code:**
- `service/first_run.py`: setup state and code, Postgres checks, Docker Postgres, setup mode
- `api/setup_routes.py` (`/api/setup/...`) and `create_setup_app` in `api/app.py`
- the setup steps in `web/app.js`

## Goal
The only command anyone types is **`lumida serve`**. Everything else happens in the browser: where Lumida keeps what it learns, the first admin, and then the databases to watch.

## Flow
```
pip install lumida → lumida serve
   "Open http://127.0.0.1:8000 · code 1234-5678"
Browser:
  1. Lumida's memory:  ( ) Start one for me (Docker)   ( ) A Postgres I already have   ( ) Without one (later)
  2. Your admin account
  3. Lumida switches to normal mode by itself → log in → Databases → Add a database
```
- **Docker install:** memory comes with compose, so the browser opens at step 2. The code is in `docker compose logs lumida`.
- **Scripted installs:** `lumida setup --database-url … --admin-email … --password-stdin` still works.

## Decisions
| | Decision |
|---|---|
| **Setup mode** | `lumida serve` with no memory configured serves only the setup steps and the web app's files. After the first admin is created, it starts the normal service in the same process (migrations, scheduler, API). |
| **Who may set up** | a **one-time code** (8 digits) printed in the terminal or log must accompany every setup step. After 20 wrong codes setup locks until a restart. Once the admin exists the code stops working. |
| **Where** | setup mode listens on **127.0.0.1 only**. On a server, use an SSH tunnel, or the Docker route. |
| **Memory must be Postgres** | the store relies on Postgres features (JSONB, partial unique indexes, advisory locks, `SKIP LOCKED`). The page offers only choices that work. "Without one" shows as *coming later*. |
| **Existing Postgres** | a form with host, port, database, user and password (defaults filled in), plus a **Test** button with plain answers: wrong password, can't reach, will create. A missing database is created; then migrations run. |
| **Start one for me** | runs `postgres:16-alpine` in Docker. The container and volume are named `lumida-metadata-<random>`; the port is free, chosen by Docker, and reachable **from this machine only**; the password is random and saved only in `.env`. It restarts with Docker (`--restart unless-stopped`). If Docker isn't running, the option is greyed out with an explanation. |
| **Saved where** | `LUMIDA_DATABASE_URL` in `.env` in the folder Lumida runs from. Other lines are kept. Nothing leaves the machine. |
| **Admin** | name, email and password (12+ characters, typed twice). Weak passwords and bad emails are refused with a clear message. |

## Tests
- **Unit:**
  - the setup code: wrong codes and the lockout
  - recognising "memory not configured"
  - escaping passwords in the connection URL
  - plain-language errors
- **Integration:**
  - setup mode with a real Postgres: no code, wrong password, database to be created, Docker unavailable, choosing memory writes `.env`, a weak password is refused, the admin is created, and the code is one-time
  - the normal app's first-admin step, then logging in with it
  - an installed app has nothing to set up
- **By hand:** both routes in a browser and through the endpoints, from a clean folder with the built package. The test Postgres and Docker containers were removed afterwards.

## Not yet
- **Memory without a separate Postgres** (a bundled database or a file-based mode).
- **Setup over the network** with an extra confirmation (today: SSH tunnel, or Docker).
- **Changing the memory** later from the browser (today: edit `.env`).
