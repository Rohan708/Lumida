# Install Lumida (self-hosted)

Lumida runs **entirely on your own machines**: Lumida itself plus a small Postgres for what it learns about your databases (the metadata store). It connects **read-only** to the databases you register and checks each of them every night at its own time. There's no account, no registration and no telemetry: nothing is sent to Lumida's makers.

There are two ways to install it. Neither needs the source code.

| | Best for | Commands |
|---|---|---|
| **Docker** (below) | a server that runs 24/7; brings its own Postgres | `docker compose up -d` |
| **pip** ([section 10](#10-without-docker-pip)) | trying it on a laptop, CI, teams without Docker; bring your own Postgres | `pip install lumida` → `lumida setup` → `lumida serve` |

## 1. Requirements
- Docker with Compose (Docker Desktop, or Docker Engine + the compose plugin)
- About 1 GB of memory and a few GB of disk for the metadata store
- Network access from the Lumida container to the databases you want to watch
- A **read-only login** on each of those databases: [`databases.md`](databases.md) (MySQL, SQL Server, Oracle, Snowflake) and `scripts/postgres/create_readonly_user.sql` (Postgres)

## 2. Configure
Put two files in an empty folder: [`docker-compose.yml`](https://github.com/Rohan708/Lumida/blob/main/deploy/docker-compose.yml) and [`.env.example`](https://github.com/Rohan708/Lumida/blob/main/deploy/.env.example). Then:
```bash
cp .env.example .env        # Windows: copy .env.example .env
```
Edit `.env`:
- `POSTGRES_PASSWORD`: a long random string, letters and digits only.
- **One URL per database you want to watch**, under any name, e.g.
  ```
  SHOP_DB_URL=postgresql+psycopg://lumida_readonly:<password>@db-host:5432/shop
  ```
- Optional: a Slack webhook (`SLACK_DATA_ALERTS=https://hooks.slack.com/...`).
- Optional: **AI features with your own provider and key** (section 11).
- Optional: `LUMIDA_VERSION=0.4.0` to pin a version instead of `latest`.

`.env` holds secrets. Keep it on the server, out of version control.

## 3. Start
```bash
docker compose up -d
docker compose ps
```
The first start downloads the Lumida image. Both services should show **healthy** within a minute. On start, Lumida brings its metadata store up to date by itself.

## 4. Add your databases
**In the browser** (after creating the first admin, section 6): *Databases → Add a database*. It shows the read-only login script for your kind of database, lets you pick the connection by the name you gave it in `.env`, tests it, and starts the first run. **Or in the terminal:**
```bash
docker compose exec lumida lumida source add shop --connection-ref SHOP_DB_URL --run-at 03:00 --timezone Europe/Berlin
docker compose exec lumida lumida source alerts shop --webhook-ref SLACK_DATA_ALERTS
```
- `--run-at` / `--timezone` choose when it runs each night (default 03:00 UTC). Change them later with `lumida source schedule shop --run-at 02:30`, or stop scheduling with `--off`.
- A new database waits for its first scheduled time. To see results right away, run it once by hand:
  ```bash
  docker compose exec lumida lumida run shop
  docker compose exec lumida lumida findings shop
  ```
- The first run records what's already wrong without alerting ("quiet baseline"). Alerts start with the next run.

## 5. Day to day
| | |
|---|---|
| What it found | `docker compose exec lumida lumida findings shop` · `lumida finding show <id>` |
| Help it learn | `docker compose exec -it lumida lumida ask shop` (a few questions about your data) |
| Health of the service | `docker compose exec lumida python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/healthz').read().decode())"` |
| Logs | `docker compose logs -f lumida` (JSON lines; never row values) |

**When a nightly run fails** (a database unreachable, a password changed), Lumida sends an alert about *that*, on the database's own channel, and tries again the next night.

The health endpoint answers **200** while the scheduler is alive, with each database's last and next run. A database that fails doesn't make the service unhealthy; that's alerted instead. To reach `/healthz` from a monitoring tool, set `LUMIDA_HEALTH_HOST=0.0.0.0` and publish port 8080.

## 6. People, the web app and the API
Lumida has an HTTP API (the web app will use it, and so can your scripts). People log in as themselves. Create the **first admin** in the container; the password goes in at a hidden prompt:
```bash
docker compose exec -it lumida lumida user add you@company.com --role admin
```
Then open the **web app at http://127.0.0.1:8000/** and log in. Admins add colleagues under *People* (or with `lumida user add`). The API's interactive docs are at http://127.0.0.1:8000/api/docs.

| Role | May |
|---|---|
| viewer | read everything |
| reviewer | + confirm or reject findings, approve rules, answer questions |
| admin | + manage people, read the audit log |

For scripts, make a personal API key (`POST /auth/keys`) and send it as `Authorization: Bearer <key>`:
```bash
curl -H "Authorization: Bearer <key>" http://127.0.0.1:8000/api/v1/sources
```
Logins last 12 hours. After 5 wrong passwords an account is locked for 15 minutes; an admin's password reset (`lumida user password EMAIL`) unlocks it. The API is published on this machine only; to reach it from other machines, put your own reverse proxy with TLS in front of it. Details: [`api.md`](../design/api.md), [`accounts.md`](../design/accounts.md).

## 7. Upgrade
```bash
docker compose pull           # the new version (or change LUMIDA_VERSION in .env first)
docker compose up -d
```
Migrations run automatically on start. A run in progress finishes its current database before the old container stops (the stop timeout is 5 minutes).

## 8. Back up
Everything Lumida knows (history, findings, your answers and rules) is in the `lumida-metadata` volume:
```bash
docker compose exec metadata pg_dump -U lumida lumida > lumida-backup.sql
```
Your own databases are never written to, so there's nothing to back up on their side.

## 9. Remove
```bash
docker compose down        # keeps the metadata volume
docker compose down -v     # also deletes everything Lumida learned
```

## Notes
- **SQL Server:** build the image with Microsoft's ODBC driver from a copy of the repository; this accepts Microsoft's licence (https://aka.ms/odbc18eula): `docker build -t lumida:sqlserver --build-arg MSSQL=yes --build-arg ACCEPT_MSSQL_EULA=yes .`, then set `LUMIDA_IMAGE=lumida` and `LUMIDA_VERSION=sqlserver` in `.env`.
- **No internet on the server (air-gapped):** on a connected machine run `docker save ghcr.io/rohan708/lumida:0.4.0 -o lumida.tar`; copy the file over and run `docker load -i lumida.tar`, then `docker compose up -d`.
- **Changing or removing a database:** in the web app, the database's *Settings* tab (admins).
- **Rules in git and CI:** *Rules → Download as YAML*, and `lumida check` in your pipeline ([`rules_as_code.md`](../design/rules_as_code.md)).
- **Included drivers:** Postgres, MySQL, Oracle, Snowflake (SQL Server: see above).
- **No outside connections are needed**, except to your databases, to Slack if you use it, and to your AI provider only if you turn AI features on. **`LUMIDA_OFFLINE=true`** blocks even those.

## 10. Without Docker (pip)
Needs Python 3.12+ and a Postgres for Lumida's own memory. Any Postgres works: an existing server with a spare database, or the one-line `docker run` that `lumida setup` shows.
```bash
pip install lumida                 # or "lumida[all]" for MySQL, Oracle and Snowflake too
lumida setup                       # asks for that Postgres, prepares it, creates your admin, writes .env
lumida serve                       # keep it running: nightly checks and the web app
```
Open http://127.0.0.1:8000. Database URLs go in the same `.env`, under names you choose. Run it as a service (systemd, Windows Task Scheduler, …) so it survives restarts.

## 11. AI features: your provider, your key (optional)
Off by default. The AI only proposes rules, reviews unsure links and writes explanations; people approve, and checks never use it. It sees table and column structure and statistics, never rows.

| Provider | `.env` |
|---|---|
| Any OpenAI-compatible service (OpenAI, OpenRouter, Groq, Mistral, Together, Azure OpenAI, …) | `LUMIDA_LLM_PROVIDER=openai`, `LUMIDA_LLM_MODEL=<model>`, `LUMIDA_LLM_API_KEY=<key>`, and `LUMIDA_LLM_BASE_URL=<the service's /v1 URL>` (empty = OpenAI) |
| A model on your own machines (Ollama, LM Studio, vLLM): nothing leaves your network | `LUMIDA_LLM_PROVIDER=openai`, `LUMIDA_LLM_MODEL=<model>`, `LUMIDA_LLM_BASE_URL=http://host.docker.internal:11434/v1` (Ollama, from Docker) |
| Google Gemini | `LUMIDA_LLM_PROVIDER=gemini`, `LUMIDA_LLM_API_KEY=<key>` |

Restart Lumida after changing `.env`. The *Propose rules with AI* and *Explain with AI* buttons appear in the web app.
