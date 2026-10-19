# Lumida: the self-hosted image. Runs `lumida serve` (the nightly scheduler, the API and
# the web app) by default.
#
#   docker build -t lumida:local .
#   docker build -t lumida:local --build-arg EXTRAS=mysql,oracle,snowflake .   (the default)
#
# SQL Server needs Microsoft's ODBC driver, under Microsoft's licence
# (https://aka.ms/odbc18eula). Whoever builds the image accepts it, explicitly:
#
#   docker build -t lumida:sqlserver --build-arg MSSQL=yes --build-arg ACCEPT_MSSQL_EULA=yes .

ARG PYTHON_VERSION=3.12

# --- build the wheel ------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS build
WORKDIR /src
RUN pip install --no-cache-dir build==1.2.2
COPY pyproject.toml README.md PACKAGE.md LICENSE ./
COPY src ./src
RUN python -m build --wheel --outdir /wheels

# --- runtime --------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim
ARG EXTRAS="mysql,oracle,snowflake"
ARG MSSQL=no
ARG ACCEPT_MSSQL_EULA=no
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LUMIDA_LOG_FORMAT=json \
    LUMIDA_HEALTH_HOST=127.0.0.1 \
    LUMIDA_HEALTH_PORT=8080 \
    LUMIDA_API_HOST=0.0.0.0 \
    LUMIDA_API_PORT=8000

# SQL Server variant only: Microsoft's ODBC Driver 18, from Microsoft's own package source.
RUN if [ "$MSSQL" = "yes" ]; then \
      if [ "$ACCEPT_MSSQL_EULA" != "yes" ]; then \
        echo "The SQL Server driver is under Microsoft's licence (https://aka.ms/odbc18eula)."; \
        echo "To accept it, build with --build-arg ACCEPT_MSSQL_EULA=yes"; \
        exit 1; \
      fi; \
      apt-get update \
      && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
      && . /etc/os-release \
      && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
         | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
      && curl -fsSL "https://packages.microsoft.com/config/debian/${VERSION_ID}/prod.list" \
         -o /etc/apt/sources.list.d/mssql-release.list \
      && apt-get update \
      && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc \
      && apt-get purge -y curl gnupg \
      && apt-get autoremove -y \
      && rm -rf /var/lib/apt/lists/*; \
    fi

RUN useradd --create-home --uid 10001 lumida
COPY --from=build /wheels /wheels
RUN wheel="$(ls /wheels/*.whl)" \
    && extras="$EXTRAS" \
    && if [ "$MSSQL" = "yes" ]; then extras="${extras:+$extras,}sqlserver"; fi \
    && if [ -n "$extras" ]; then target="${wheel}[${extras}]"; else target="$wheel"; fi \
    && pip install --no-cache-dir "$target" \
    && rm -rf /wheels

USER lumida
WORKDIR /home/lumida

# Healthy while the scheduler loop is alive (failing customer databases are alerted, not
# treated as the container being broken).
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"

# The web app and the API (log in with accounts made by `lumida user add`). Publish it on
# the host's localhost, or behind your own reverse proxy with TLS; see docs/setup/install.md.
EXPOSE 8000

ENTRYPOINT ["lumida"]
CMD ["serve"]
