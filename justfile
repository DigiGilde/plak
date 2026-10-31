# Plak - development commands

# Default: list the available commands
default:
    @just --list

# Run the backend locally with uvicorn (reload on, no access log: it would
# log secret links with ?key= in plain text)
dev:
    cd backend && uv run uvicorn plak.main:create_app --factory --reload --no-access-log

# Fill the running dev stack with sample content: wipes groups and members and seeds them again (dev/seed.py)
seed:
    podman compose -f dev/compose.yml exec -T app python - < dev/seed.py

# Run the backend and CLI test suites; Testcontainers needs the Podman socket
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

# Run the CLI tests (cli/tests); cli/ is its own uv project, separate from the backend
test-cli:
    cd cli && uv run pytest tests -q

# Coverage of the three suites; same socket trick as `just test`
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
    # .github/scripts/release.py and rulesets.py lie outside src/plak: measured on its own, as in CI.
    release_data="$(mktemp -t plak-release-coverage)"
    trap 'trash "$release_data" 2>/dev/null || true' EXIT
    COVERAGE_FILE="$release_data" uv run coverage run --include='*/.github/scripts/release.py,*/.github/scripts/rulesets.py' -m pytest -q tests/test_release.py tests/test_rulesets.py
    COVERAGE_FILE="$release_data" uv run coverage report --include='*/.github/scripts/release.py,*/.github/scripts/rulesets.py' --show-missing --fail-under=100
    cd "{{justfile_directory()}}/cli" && uv run pytest tests -q --cov=plak_cli --cov-report=term-missing:skip-covered
    cd "{{justfile_directory()}}/frontend" && npm run coverage

# Lint the backend and the CLI with ruff
lint:
    cd backend && uv run ruff check src tests
    # From the repo root, so ruff uses its defaults for cli/ instead of
    # backend/pyproject.toml; --project only picks the pinned ruff.
    uv run --project backend ruff check cli/plak_cli cli/tests

# Purge expired audit entries; needs PLAK_DB_URL
purge-audit-log:
    cd backend && uv run python -m plak.audit.retention

# Verify the integrity chain of the audit log; needs PLAK_DB_URL
verify-audit-log:
    cd backend && uv run python -m plak.audit.chain

# Publish the head of each audit chain as a log line; needs PLAK_DB_URL
publish-audit-head:
    cd backend && uv run python -m plak.audit.checkpoint

# Check the database against a previously published head line (- for stdin)
verify-audit-head bestand:
    cd backend && uv run python -m plak.audit.checkpoint --against {{bestand}}

# Scan the dependencies like the CI job `vulnerabilities` does
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

# E2E suite (Playwright, spec 13): bring the stack up on its own ports, test,
# tear the stack down again; see e2e/run.sh for the options
e2e *ARGS:
    ./e2e/run.sh {{ARGS}}

# Build the Vue SPA to frontend/dist (the app serves it under /admin)
build-spa:
    cd frontend && npm run build

# Fetch the Swagger UI assets for /-/api/docs again (version in api/docs.py)
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
