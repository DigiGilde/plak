#!/usr/bin/env bash
# Starts the Toon dev stack (nginx + app + postgres + mock OIDC) via
# podman compose. Generates dev secrets in dev/.secrets/ (outside git)
# once, picks the mock OIDC config and starts the stack with --build.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=dev/secrets.sh
source "${REPO_ROOT}/dev/secrets.sh"

if ! command -v podman >/dev/null 2>&1; then
    echo "podman niet gevonden op PATH; installeer podman om de dev-stack te starten." >&2
    exit 1
fi

if podman machine list --format '{{.Name}}' >/dev/null 2>&1; then
    # macOS/Windows: podman runs via a VM that must be active.
    running="$(podman machine list --format '{{.Running}}' 2>/dev/null | head -n1)"
    if [[ "${running}" != "true" ]]; then
        echo "Podman-machine draait niet; start 'm met 'podman machine start'." >&2
        exit 1
    fi
fi

ensure_dev_secrets

# Mock IdP config: by default everyone is logged in silently as
# dev-beheerder (dev/mock-oidc-config.json, the default of TOON_MOCK_CONFIG
# in dev/compose.yml; the e2e suite relies on it).
# TOON_MOCK_INTERACTIVE_LOGIN=1 picks dev/mock-oidc-config.interactive.json:
# the mock then shows a login screen where you type a subject yourself. See
# docs/local-development.md.
mock_config="./mock-oidc-config.json"
mode="stil (elke aanmelding wordt dev-beheerder)"
case "${TOON_MOCK_INTERACTIVE_LOGIN:-}" in
    1 | true | TRUE | yes | ja)
        mock_config="./mock-oidc-config.interactive.json"
        mode="inlogscherm (je typt zelf een subject)"
        export TOON_MOCK_CONFIG="${mock_config}"
        ;;
esac

echo "Mock-IdP: ${mode}; config uit $(basename "${mock_config}")."

cd "${REPO_ROOT}"
exec podman compose -f dev/compose.yml up --build
