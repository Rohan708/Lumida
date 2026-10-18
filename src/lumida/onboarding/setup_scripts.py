"""Copy-paste scripts that create the read-only login Lumida connects as, one per kind of
database. Shown in the web app's "Add a database" form; the same as docs/setup/databases.md.

Placeholders in <angle brackets> are for the person to fill in. Every script grants reading
only; Lumida also opens read-only transactions with timeouts on its side.
"""

POSTGRES = """\
-- Run as a superuser or the database owner, in the database to watch.
CREATE ROLE lumida_readonly LOGIN PASSWORD '<strong password>'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
ALTER ROLE lumida_readonly SET default_transaction_read_only = on;
ALTER ROLE lumida_readonly SET statement_timeout = '30s';
ALTER ROLE lumida_readonly SET lock_timeout = '2s';
GRANT CONNECT ON DATABASE <database> TO lumida_readonly;

-- Repeat for every schema to watch:
GRANT USAGE ON SCHEMA <schema> TO lumida_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA <schema> TO lumida_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA <schema> GRANT SELECT ON TABLES TO lumida_readonly;

-- Optional: query statistics, which help find undocumented links between tables.
GRANT pg_read_all_stats TO lumida_readonly;

-- .env on the Lumida server:
-- SHOP_DB_URL=postgresql+psycopg://lumida_readonly:<password>@<host>:5432/<database>
"""

MYSQL = """\
CREATE USER 'lumida'@'%' IDENTIFIED BY '<strong password>';
GRANT SELECT, SHOW VIEW ON <database>.* TO 'lumida'@'%';
-- Optional: query statistics, which help find undocumented links between tables.
GRANT SELECT ON performance_schema.events_statements_summary_by_digest TO 'lumida'@'%';

-- .env on the Lumida server:
-- SHOP_DB_URL=mysql+pymysql://lumida:<password>@<host>:3306/<database>
"""

SQLSERVER = """\
CREATE LOGIN lumida WITH PASSWORD = '<strong password>';
USE <database>;
CREATE USER lumida FOR LOGIN lumida;
ALTER ROLE db_datareader ADD MEMBER lumida;
GRANT VIEW DATABASE STATE TO lumida;  -- optional: query statistics

-- .env on the Lumida server (needs the SQL Server image variant / ODBC Driver 18):
-- SHOP_DB_URL=mssql+pyodbc://lumida:<password>@<host>:1433/<database>?driver=ODBC+Driver+18+for+SQL+Server&ApplicationIntent=ReadOnly&Encrypt=yes
"""

ORACLE = """\
CREATE USER lumida IDENTIFIED BY "<strong password>";
GRANT CREATE SESSION TO lumida;
GRANT SELECT ANY TABLE TO lumida;  -- or SELECT on each table of the application's schema
GRANT SELECT ON v_$sql TO lumida;  -- optional: query statistics

-- .env on the Lumida server:
-- SHOP_DB_URL=oracle+oracledb://lumida:<password>@<host>:1521/?service_name=<service>
"""

SNOWFLAKE = """\
-- As ACCOUNTADMIN (or SECURITYADMIN + SYSADMIN):
CREATE ROLE IF NOT EXISTS LUMIDA_READONLY;
GRANT USAGE ON WAREHOUSE <warehouse> TO ROLE LUMIDA_READONLY;
GRANT USAGE ON DATABASE <database> TO ROLE LUMIDA_READONLY;
GRANT USAGE ON ALL SCHEMAS IN DATABASE <database> TO ROLE LUMIDA_READONLY;
GRANT SELECT ON ALL TABLES IN DATABASE <database> TO ROLE LUMIDA_READONLY;
GRANT SELECT ON FUTURE TABLES IN DATABASE <database> TO ROLE LUMIDA_READONLY;
CREATE USER IF NOT EXISTS LUMIDA_SVC DEFAULT_ROLE = LUMIDA_READONLY
  RSA_PUBLIC_KEY = '<public key, without the BEGIN/END lines>';
GRANT ROLE LUMIDA_READONLY TO USER LUMIDA_SVC;

-- .env on the Lumida server (the private key file stays on the server):
-- SHOP_DB_URL=snowflake://LUMIDA_SVC@<account>/<database>?warehouse=<warehouse>&role=LUMIDA_READONLY&private_key_file=/keys/lumida_svc.p8
"""

SETUP_SCRIPTS = {
    "postgres": POSTGRES,
    "mysql": MYSQL,
    "sqlserver": SQLSERVER,
    "oracle": ORACLE,
    "snowflake": SNOWFLAKE,
}
