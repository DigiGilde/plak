#!/bin/sh
# Migrates, then becomes the server. ZAD has no Job object and no `command`
# field on a component, so the image itself is the only place where
# "before the server starts" can be expressed (docs/deploying-on-zad.md §7).
set -eu

# `alembic current` connects and reads alembic_version; on a database that
# has never been migrated it succeeds and prints nothing, so a failure here
# means the server is not reachable yet rather than the schema being absent.
# The platform provisions the database alongside the pod, so the first
# attempts losing is normal.
attempts=0
until alembic current >/dev/null 2>&1; do
    attempts=$((attempts + 1))
    if [ "${attempts}" -ge 30 ]; then
        echo "entrypoint: database unreachable after ${attempts} attempts" >&2
        exit 1
    fi
    sleep 1
done

alembic upgrade head

# exec: uvicorn replaces this shell as PID 1, so the SIGTERM that ends a
# rollout reaches the server instead of a shell that ignores it.
exec uvicorn plak.main:create_app \
    --factory \
    --host 0.0.0.0 \
    --port 8080 \
    --no-access-log
