# Lumida: light on your data

Point Lumida at your databases, read-only. It finds the relationships nobody documented, learns what normal looks like for every table and column, and tells you every morning what's wrong, what it breaks and why, with the evidence.

**It runs entirely on your own machines.** There's no account, no sign-up and no telemetry. Your data, and even your table names, never reach Lumida's makers.

## Install
```bash
pip install lumida                 # Postgres support included
pip install "lumida[all]"          # + MySQL, Oracle, Snowflake drivers
```
Requires Python 3.12+.

## Start
```bash
lumida serve
```
Open **http://127.0.0.1:8000**. The first time, the browser walks you through setup, using the one-time code printed in the terminal:
1. **Where Lumida keeps what it learns:** "Start one for me" (a private Postgres via Docker) or a Postgres you already have.
2. **Your admin account.**
3. **Databases → Add a database:** a script for the read-only login, a connection test, then *Run now*.

Connection URLs stay in `.env` on your machine, under a name you choose:
```
SHOP_DB_URL=postgresql+psycopg://readonly_user:<password>@db-host:5432/shop
```

**Prefer Docker?** One `docker compose up -d` brings Lumida and its own Postgres. See the [install guide](https://github.com/Rohan708/Lumida/blob/main/docs/setup/install.md).

## What it finds
- **Structure:** missing keys, dropped columns, changed types, unindexed links.
- **Relationships:** links nobody declared, orphan rows, duplicates.
- **Values:** sudden empties, out-of-range numbers, "USA" next to "US", near-duplicate emails.
- **Business rules:** "shipped after ordered", "total = sum of lines". AI proposes, you approve.
- **Loads:** volume drops, tables that stopped updating, missed deadlines; it knows weekdays and closed days.
- **Unusual rows**, **copies that disagree** (app database vs warehouse) and **personal data in unexpected places**.

Every problem comes with real numbers and the IDs of the rows involved. Confirm or dismiss it, and Lumida remembers.

## How it stays safe
- **Read-only:** a read-only login, read-only transactions, timeouts on every query. It never writes to your databases.
- **Counted in place:** the counting happens inside your database. Only counts, statistics and row IDs come back.
- **AI is optional and yours:** bring any OpenAI-compatible service, a local model (Ollama, LM Studio, vLLM) or Gemini, with your own key. AI only proposes; people approve. `LUMIDA_OFFLINE=true` blocks every outside connection.

## In CI
Keep approved rules in git and fail the build when data breaks them:
```bash
lumida rules export shop -o lumida-rules.yaml
lumida check --rules lumida-rules.yaml --connection-ref STAGING_DB_URL
```

Supports PostgreSQL, MySQL/MariaDB, SQL Server, Oracle and Snowflake.

Open source under the Apache 2.0 licence: https://github.com/Rohan708/Lumida
