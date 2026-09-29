# Plak - development commands

# Standaard: toon beschikbare commands
default:
    @just --list

# Start de backend lokaal met uvicorn (reload aan; geen accesslog, want die
# zou geheime links met ?key= in klare tekst loggen)
dev:
    cd backend && uv run uvicorn plak.main:create_app --factory --reload --no-access-log

# Vul de draaiende dev-stack met voorbeeldinhoud: wist groepen en leden en zaait ze opnieuw (dev/seed.py)
seed:
    podman compose -f dev/compose.yml exec -T app python - < dev/seed.py

# Draai de backend- en CLI-testsuites; testcontainers heeft de Podman-socket nodig
test:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ "$(uname -s)" == "Darwin" ]]; then
        # macOS: no native Podman socket, so via the podman machine
        socket="$(podman machine inspect --format '{{'{{'}}.ConnectionInfo.PodmanSocket.Path{{'}}'}}' 2>/dev/null || true)"
        if [[ -z "${socket}" ]]; then
            # fallback: default location of the podman machine socket
            socket="${HOME}/.local/share/containers/podman/machine/podman.sock"
        fi
        export DOCKER_HOST="unix://${socket}"
    else
        export DOCKER_HOST="unix://${XDG_RUNTIME_DIR}/podman/podman.sock"
    fi
    cd backend && uv run python -m pytest -n 4
    cd "{{justfile_directory()}}" && just test-cli

# Draai de CLI-tests (cli/tests); cli/ is een eigen uv-project, los van de backend
test-cli:
    cd cli && uv run pytest tests -q

# Dekking van de drie suites; dezelfde socket-truc als `just test`
coverage:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ "$(uname -s)" == "Darwin" ]]; then
        socket="$(podman machine inspect --format '{{'{{'}}.ConnectionInfo.PodmanSocket.Path{{'}}'}}' 2>/dev/null || true)"
        if [[ -z "${socket}" ]]; then
            socket="${HOME}/.local/share/containers/podman/machine/podman.sock"
        fi
        export DOCKER_HOST="unix://${socket}"
    else
        export DOCKER_HOST="unix://${XDG_RUNTIME_DIR}/podman/podman.sock"
    fi
    cd backend && uv run python -m pytest -n 4 --cov=src/plak --cov-report=term-missing:skip-covered
    cd "{{justfile_directory()}}/cli" && uv run pytest tests -q --cov=plak_cli --cov-report=term-missing:skip-covered
    cd "{{justfile_directory()}}/frontend" && npm run coverage

# Lint de backend en de CLI met ruff
lint:
    cd backend && uv run ruff check src tests
    # From the repo root, so ruff uses its defaults for cli/ instead of
    # backend/pyproject.toml; --project only picks the pinned ruff.
    uv run --project backend ruff check cli/plak_cli cli/tests

# Ruim verlopen auditregels op; vraagt PLAK_DB_URL
purge-audit-log:
    cd backend && uv run python -m plak.audit.retention

# Controleer de integriteitsketen van het auditlog; vraagt PLAK_DB_URL
verify-audit-log:
    cd backend && uv run python -m plak.audit.chain

# Publiceer de kop van elke auditketen als logregel; vraagt PLAK_DB_URL
publish-audit-head:
    cd backend && uv run python -m plak.audit.checkpoint

# Controleer de database tegen een eerder gepubliceerde kopregel (- voor stdin)
verify-audit-head bestand:
    cd backend && uv run python -m plak.audit.checkpoint --against {{bestand}}

# Scan de afhankelijkheden zoals de CI-job `vulnerabilities` dat doet
scan:
    #!/usr/bin/env bash
    # Same ignore list as the CI (.trivyignore.yaml). The image scan is not
    # part of this: that runs with trivy on the built image, in deploy.yml.
    set -euo pipefail
    requirements="$(mktemp -t plak-requirements)"
    report="$(mktemp -t plak-npm-audit)"
    trap 'trash "$requirements" "$report" 2>/dev/null || true' EXIT
    uv export --directory backend --frozen --no-emit-project --quiet \
        --format requirements-txt -o "$requirements"
    flags=()
    while IFS= read -r id; do
        if [ -n "$id" ]; then
            flags+=(--ignore-vuln "$id")
        fi
    done < <(uv run --script .github/scripts/vulnerabilities.py ids)
    uvx pip-audit@2.10.1 --requirement "$requirements" --disable-pip \
        --progress-spinner off ${flags[@]+"${flags[@]}"}
    npm audit --json --prefix frontend > "$report" || true
    uv run --script .github/scripts/vulnerabilities.py npm-audit "$report"

# E2E-suite (Playwright, spec 13): stack op eigen poorten omhoog, testen,
# stack weer afbreken; zie e2e/run.sh voor de knoppen
e2e *ARGS:
    ./e2e/run.sh {{ARGS}}

# Bouw de Vue-SPA naar frontend/dist (de app serveert die onder /admin)
build-spa:
    cd frontend && npm run build

# Haal de Swagger UI-assets van /-/api/docs opnieuw op (versie in api/docs.py)
refresh-swagger-ui:
    #!/usr/bin/env bash
    set -euo pipefail
    version=$(grep -o 'SWAGGER_UI_VERSION = "[^"]*"' backend/src/plak/api/docs.py | cut -d'"' -f2)
    target=backend/src/plak/static/docs
    for file in swagger-ui-bundle.js swagger-ui.css; do
      curl -sfL --max-time 60 -o "$target/$file" \
        "https://cdn.jsdelivr.net/npm/swagger-ui-dist@$version/$file"
    done
    # The download is trusted by nobody: it has to match SHA256SUMS, which is
    # in git and reviewed. After a version bump those sums are stale on
    # purpose; regenerate them only once you have checked the new bytes
    # against the npm registry, never just because this line went red.
    cd "$target" && shasum -a 256 -c SHA256SUMS
