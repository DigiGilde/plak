#!/usr/bin/env bash
# Runs the E2E suite (spec 13): brings the compose stack up on its own
# ports as a separate compose project (plak-e2e), waits for health,
# runs Playwright and tears the stack down again.
#
# Environment variables:
#   PLAK_E2E_PORT        host port for nginx (default 18888)
#   PLAK_E2E_OIDC_PORT   host port for the mock OIDC (default 18889)
#   PLAK_E2E_KEEP_UP=1   keep the stack running afterwards (debugging)
#   PLAK_E2E_ENGINE      container engine (default podman; the GitHub
#                        runner has only docker, so the CI job sets it)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="plak-e2e"
export PLAK_E2E_PORT="${PLAK_E2E_PORT:-18888}"
export PLAK_E2E_OIDC_PORT="${PLAK_E2E_OIDC_PORT:-18889}"
ENGINE="${PLAK_E2E_ENGINE:-podman}"

COMPOSE=("${ENGINE}" compose -p "${PROJECT}" -f dev/compose.yml -f e2e/compose.e2e.yml)

if ! command -v "${ENGINE}" >/dev/null 2>&1; then
    echo "${ENGINE} niet gevonden op PATH." >&2
    exit 1
fi
if [[ "${ENGINE}" == "podman" ]] && podman machine list --format '{{.Name}}' >/dev/null 2>&1; then
    running="$(podman machine list --format '{{.Running}}' 2>/dev/null | head -n1)"
    if [[ "${running}" != "true" ]]; then
        echo "Podman-machine draait niet; start 'm met 'podman machine start'." >&2
        exit 1
    fi
fi

# Dev secrets: reuse dev/.secrets/ when present. dev/up.sh cannot create
# them here (that script keeps hanging in the foreground), so the function
# it uses lives in dev/secrets.sh.
# shellcheck source=dev/secrets.sh
source "${REPO_ROOT}/dev/secrets.sh"
ensure_dev_secrets

cd "${REPO_ROOT}"

cleanup() {
    status=$?
    # The Playwright step leaves the CWD in e2e/; the COMPOSE paths are
    # relative to the repo root, so back to the root for logs and teardown.
    cd "${REPO_ROOT}"
    if [[ "${PLAK_E2E_KEEP_UP:-0}" == "1" ]]; then
        echo "PLAK_E2E_KEEP_UP=1: stack blijft draaien (project ${PROJECT})."
        return "${status}"
    fi
    if [[ ${status} -ne 0 ]]; then
        echo "== laatste app/nginx-logs (run faalde) =="
        "${COMPOSE[@]}" logs --tail 50 app nginx || true
    fi
    "${COMPOSE[@]}" down -v --remove-orphans || true
    return "${status}"
}
trap cleanup EXIT

echo "== stack starten (project ${PROJECT}, poort ${PLAK_E2E_PORT}) =="
"${COMPOSE[@]}" up -d --build

echo "== wachten op gezondheid =="
ready=""
for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null -H "Host: beheer.plak.localhost" \
            "http://127.0.0.1:${PLAK_E2E_PORT}/healthz-proxy" \
        && curl -fsS -o /dev/null -H "Host: plak.localhost" \
            "http://127.0.0.1:${PLAK_E2E_PORT}/healthz-proxy" \
        && curl -fsS -o /dev/null -H "Host: beheer.plak.localhost" \
            "http://127.0.0.1:${PLAK_E2E_PORT}/"; then
        ready="ja"
        break
    fi
    sleep 2
done
if [[ -z "${ready}" ]]; then
    echo "Stack werd niet gezond binnen 120 seconden." >&2
    exit 1
fi

echo "== Playwright draaien =="
cd "${REPO_ROOT}/e2e"
if [[ ! -d node_modules ]]; then
    if [[ -f package-lock.json ]]; then
        npm ci
    else
        npm install
    fi
fi
npx playwright install chromium
npx playwright test "$@"
