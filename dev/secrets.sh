#!/usr/bin/env bash
# Sourced by dev/up.sh and e2e/run.sh: creates the dev secrets in
# dev/.secrets/ (outside version control) once. The caller sets REPO_ROOT
# and has `set -euo pipefail` on.

SECRETS_DIR="${REPO_ROOT}/dev/.secrets"
APP_ENV="${SECRETS_DIR}/app.env"
POSTGRES_ENV="${SECRETS_DIR}/postgres.env"

ensure_dev_secrets() {
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

        # Percent-encoded for the DSN below: a DSN's password component has to be
        # URL-encoded (RFC 3986), openssl-generated hex happens not to need it,
        # but building the DSN by plain interpolation would silently break on a
        # password with a ':', '@' or '/' in it.
        app_db_password_urlenc="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "${app_db_password}")"

        cat > "${APP_ENV}" <<EOF
PLAK_DB_URL=postgresql+asyncpg://plak:${app_db_password_urlenc}@postgres:5432/plak
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
}
