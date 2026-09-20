#!/usr/bin/env bash
# Runs once, on an empty data directory, from the official postgres image's
# /docker-entrypoint-initdb.d/.
#
# The container's own POSTGRES_USER is a superuser; on ZAD the single account
# the app, the migrations and the retention job share is an ordinary owner of
# the database. This creates that account here as well, so dev and e2e run
# into the same permission wall as production instead of finding out later.
# The superuser stays behind as a bootstrap account only: nothing connects on
# it after this script.
set -euo pipefail

app_user="${PLAK_DB_USER:-plak}"
: "${PLAK_DB_PASSWORD:?PLAK_DB_PASSWORD ontbreekt in de postgres-omgeving}"

psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" <<EOSQL
CREATE ROLE "${app_user}" LOGIN PASSWORD '${PLAK_DB_PASSWORD}'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
-- Owner of the database and of schema public, which is what alembic needs to
-- create tables; nothing wider than that.
ALTER DATABASE "${POSTGRES_DB}" OWNER TO "${app_user}";
ALTER SCHEMA public OWNER TO "${app_user}";
EOSQL
