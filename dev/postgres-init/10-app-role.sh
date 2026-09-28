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

# The values go in as psql variables and come out quoted by psql itself
# (:"name" as an identifier, :'name' as a literal), so a quote in the
# password cannot break out of the statement. The heredoc is quoted for the
# same reason: the shell expands nothing in it.
psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
  -v app_user="${app_user}" -v app_password="${PLAK_DB_PASSWORD}" -v db="${POSTGRES_DB}" <<'EOSQL'
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
-- Owner of the database and of schema public, which is what alembic needs to
-- create tables; nothing wider than that.
ALTER DATABASE :"db" OWNER TO :"app_user";
ALTER SCHEMA public OWNER TO :"app_user";
EOSQL
