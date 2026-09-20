#!/usr/bin/env bash
# Starts the Plak dev stack (nginx + app + postgres + mock OIDC) via
# podman compose. Generates dev secrets in dev/.secrets/ (outside git)
# once, picks the mock OIDC config and starts the stack with --build.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS_DIR="${REPO_ROOT}/dev/.secrets"
APP_ENV="${SECRETS_DIR}/app.env"
POSTGRES_ENV="${SECRETS_DIR}/postgres.env"

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

mkdir -p "${SECRETS_DIR}"
chmod 700 "${SECRETS_DIR}"

if [[ ! -f "${APP_ENV}" || ! -f "${POSTGRES_ENV}" ]]; then
    echo "Genereer dev-secrets in ${SECRETS_DIR} ..."

    postgres_password="$(openssl rand -hex 24)"
    # Separate account from the superuser the container bootstraps with; the
    # init script (dev/postgres-init) creates it.
    app_db_password="$(openssl rand -hex 24)"
    session_secret="$(openssl rand -hex 32)"
    audit_pepper="$(openssl rand -hex 32)"
    # AES-256-GCM key for the full IP address in the audit log
    # (audit/ip_crypto.py): base64 of 32 random bytes, a different secret
    # than audit_pepper and session_secret.
    audit_ip_key="$(openssl rand -base64 32)"

    # private_key_jwt key: mock-oauth2-server does not verify client
    # authentication, so a freshly generated dev RSA key suffices (see
    # spec §7 for the real-IdP integration test).
    oidc_jwk="$(
        cd "${REPO_ROOT}/backend" && uv run python - <<'PY'
import base64
import json

from cryptography.hazmat.primitives.asymmetric import rsa


def b64url(n: int) -> str:
    length = (n.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()


key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
priv = key.private_numbers()
pub = priv.public_numbers

jwk = {
    "kty": "RSA",
    "use": "sig",
    "alg": "RS256",
    "kid": "plak-dev",
    "n": b64url(pub.n),
    "e": b64url(pub.e),
    "d": b64url(priv.d),
    "p": b64url(priv.p),
    "q": b64url(priv.q),
    "dp": b64url(priv.dmp1),
    "dq": b64url(priv.dmq1),
    "qi": b64url(priv.iqmp),
}
print(json.dumps(jwk))
PY
    )"

    cat > "${POSTGRES_ENV}" <<EOF
POSTGRES_PASSWORD=${postgres_password}
PLAK_DB_PASSWORD=${app_db_password}
EOF

    cat > "${APP_ENV}" <<EOF
PLAK_DB_URL=postgresql+asyncpg://plak:${app_db_password}@postgres:5432/plak
PLAK_SESSION_SECRET=${session_secret}
PLAK_AUDIT_PEPPER=${audit_pepper}
PLAK_AUDIT_IP_KEY=${audit_ip_key}
PLAK_OIDC_CLIENT_PRIVATE_JWK=${oidc_jwk}
EOF

    chmod 600 "${APP_ENV}" "${POSTGRES_ENV}"
    echo "Dev-secrets aangemaakt."
else
    echo "Dev-secrets bestaan al in ${SECRETS_DIR}, hergebruik ze."
fi

# Mock-IdP-config: standaard logt iedereen stilzwijgend in als
# dev-beheerder (dev/mock-oidc-config.json, de default van PLAK_MOCK_CONFIG
# in dev/compose.yml; de e2e-suite rekent daarop).
# PLAK_MOCK_INTERACTIVE_LOGIN=1 kiest dev/mock-oidc-config.interactive.json:
# dan toont de mock een inlogscherm waarin je zelf een subject typt. Zie
# docs/local-development.md.
mock_config="./mock-oidc-config.json"
mode="stil (elke aanmelding wordt dev-beheerder)"
case "${PLAK_MOCK_INTERACTIVE_LOGIN:-}" in
    1 | true | TRUE | yes | ja)
        mock_config="./mock-oidc-config.interactive.json"
        mode="inlogscherm (je typt zelf een subject)"
        export PLAK_MOCK_CONFIG="${mock_config}"
        ;;
esac

echo "Mock-IdP: ${mode}; config uit $(basename "${mock_config}")."

cd "${REPO_ROOT}"
exec podman compose -f dev/compose.yml up --build
