# Local development

This document describes how to run and test Plak locally.

## Prerequisites

- **Podman** (not Docker; see the global instructions). Install Podman
  and make sure the Podman machine is running on macOS/Windows
  (`podman machine start`).
- Python >= 3.12 with `uv`.
- Node.js with npm, for the frontend.
- `just` (justfile runner) for the usual commands.
- Two hostnames pointing at 127.0.0.1 (spec §4a: two origins; the app
  separates them itself on the `Host` header): `beheer.plak.localhost` and
  `plak.localhost`. macOS and most Linux resolvers resolve everything under
  `.localhost` themselves, so usually you need to do nothing. If you get a
  DNS error, put them in `/etc/hosts`:

  ```
  127.0.0.1 beheer.plak.localhost
  127.0.0.1 plak.localhost
  ```
- A built SPA: `just build-spa` (npm run build) fills `frontend/dist`,
  which the compose stack mounts read-only into the app container. Without
  that directory `/admin` answers with a 503 and a clear message.

## Commit hooks

`.pre-commit-config.yaml` holds the gate that the CI job `pre-commit`
also runs. Install it once per clone:

```bash
uvx pre-commit install
```

From then on every `git commit` runs ruff, the whitespace and
end-of-file fixers, the YAML/JSON/TOML checks, a gitleaks scan of the
staged diff and the em dash/en dash check.
`uvx pre-commit run --all-files` runs the whole set over the tree, which
is what CI does. The gitleaks hook is the one that only works locally:
it reads the staged diff, so with `--all-files` there is nothing for it
to see. CI covers secrets in its own job (`secret-scan`), which scans
the working tree and the commits a pull request adds, because
`git commit --no-verify` walks past the local hook.

Both use `.gitleaks.toml`: the default rule set plus an allowlist for
`dev/.secrets/` and `frontend/dist/`, the two gitignored directories a
working-tree scan would otherwise trip over.

Building the gitleaks hook needs a Go toolchain on the first run. Every
hook revision is pinned to a commit SHA; Dependabot bumps them.

## Starting the dev stack

```bash
./dev/up.sh
```

This script:

1. Checks that Podman is present and that the Podman machine is running
   (macOS/Windows).
2. Picks the mock OIDC config: with `PLAK_MOCK_INTERACTIVE_LOGIN=1` it
   exports `PLAK_MOCK_CONFIG=./mock-oidc-config.interactive.json`, and
   otherwise nothing, so compose falls back to its default
   `dev/mock-oidc-config.json`. Both files are checked in, so a bare
   `podman compose -f dev/compose.yml up` works too.
3. Generates dev secrets once in `dev/.secrets/` (outside git):
   a PostgreSQL password, a session secret, an audit pepper, and an
   RSA key for `private_key_jwt` (the mock OIDC server does not verify
   client authentication, but the app configures it like production
   anyway). Existing secrets are reused, not overwritten.
4. Starts `podman compose -f dev/compose.yml up --build`.

On a first run building the images takes a while; later runs are
faster thanks to the Podman build cache.

## Seeding dev content

A fresh or migrated database is empty, and an empty admin SPA shows
nothing of the role model or the access settings. `just seed` puts a
complete example environment in it in a few seconds:

```bash
just seed
```

The recipe pipes `dev/seed.py` into the running app container; that one
already has the `plak` package, the `PLAK_*` environment and the content
volume. So the stack has to be up (`./dev/up.sh`).

**The seed is destructive and repeatable.** It first wipes every group and
every member (with everything hanging off them: sites, versions, previews,
invitees, secret links, linked repositories and CLI sessions) plus the
matching version directories on the content volume, and then puts the whole
set down again. The audit log is left untouched: the triggers from
`0001_base` refuse every UPDATE and every DELETE within the retention
period, and an audit trail that a script can rewrite is worth nothing.
The script refuses to run outside `PLAK_ENVIRONMENT=dev`.

What ends up there:

| Group | Default base | Sites |
|---|---|---|
| `rijksoverheid` | `public` | `jaarverslag` (base `public`, live + 2 previews), `kerncijfers` (base `nobody` plus secret links), `evaluatie` (base `nobody` plus invitees) |
| `inspectie` | `site_team` | `toezichtrapport` (base `site_team` plus secret links and invitees), `werkinstructies` (base `sso`, nothing live yet) |

  The base is the one choice ("who can view this site?"), the secret links
  and the invitees are the exceptions that can be added to it independently.
  `toezichtrapport` shows the combination that could not be expressed with
  a single level: the site team always gets in, and alongside that there is
  a link for someone who cannot log in at all and a reviewer on the invitee
  list.

- **Roles side by side in one group.** In `rijksoverheid` Dev Beheerder is
  `admin`, Sanne Jansen `editor` and Tim de Vries `reader`.
- **A site role only.** Noor Bakker is not a group member anywhere, but is
  `editor` on `evaluatie`. A site role adds to the group role and never
  subtracts from it, so she only gets in at that one site.
- **A deactivated member.** Kim Visser is on `deactivated`, so that
  `/admin/-/platform` has something to do.
- **The person logging in.** The user from `PLAK_BOOTSTRAP_ADMIN_SUB`
  (default `dev-beheerder`) is platform administrator and group
  administrator of `rijksoverheid`, and deliberately not a member of
  `inspectie`. That way you see the difference between "platform
  administrator sees everything" and real group membership right away.
  He is also on the invitee list of `evaluatie`, so that route can be
  tested with the mock login.
- **Real bundles.** The versions go through the normal ingest path: the
  seed builds the `.tar.gz` in memory, there is no bundle in the repo.
- **Previews.** `pr-42` inherits the access of the site, `pr-77` has its
  own override that is stricter: base `site_team` without exceptions. An
  override is always the whole access, never a base from the preview with
  exceptions from the site.

The secret link of `kerncijfers` is shown once, at the bottom of the
output, and cannot be reconstructed afterwards. Instead of a deploy
token the seed prints how to log in with `plak login` to publish from
your own machine (device flow, linking on `/cli-link`).

Checking that it is there, without a browser:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://plak.localhost:8080/rijksoverheid/jaarverslag/
curl -s -o /dev/null -w '%{http_code}\n' http://plak.localhost:8080/rijksoverheid/jaarverslag/_preview/pr-42/
```

`backend/tests/test_dev_seed.py` runs the same seed against the
testcontainer database, so that a schema change which breaks the script
fails in `just test` and not only when someone wants their dev environment
back.

## Compose topology

The app serves everything itself: content via `FileResponse` (Range,
ETag/304, the fixed header set from spec §5), the built admin SPA under
`/admin` (spec §9 headers, `PLAK_SPA_PATH`) and the host separation of
spec §4a (content paths only on the content host, `/admin` and the
platform routes only on the admin host, otherwise the neutral 404). That
is necessary because ZAD knows one container per component, without
sidecar, shared volume or
ConfigMap; there is no X-Accel-Redirect.

One path on the content host is neither content nor a 404: the root `/`
carries the public front page (`platform/pages.py`),
with the login button and the footer to the admin host from `PLAK_BASE_URL`.
The root of the admin host stays a 303 to `/admin/`, where the SPA shows the
landing itself. To check without a browser:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://plak.localhost:8080/          # 200, front page
curl -s -o /dev/null -w '%{http_code}\n' http://beheer.plak.localhost:8080/   # 303 to /admin/
```

nginx stays in the dev stack only as a dumb reverse proxy: it passes the
`Host` header and the `X-Forwarded-*` headers on to the app and provides
the `oidc.plak.localhost` alias for the mock OIDC. The admin host
(`beheer.plak.localhost`) and the content host (`plak.localhost`) both
point at the same nginx on port `8080`; the app makes the distinction.

```
┌───────────────────────────┐      ┌─────────────┐      ┌──────────────┐
│    nginx (dumb proxy)     │─────▶│     app     │─────▶│  postgres    │
│  beheer.plak.localhost    │◀─────│  (FastAPI)  │      │  (16)        │
│  plak.localhost     :8080 │      │ content+SPA │      └──────────────┘
└───────────────────────────┘      └──┬───────┬──┘
                                      │       │ OIDC (via alias oidc.plak.localhost)
                          content-    │       ▼
                          volume ◀────┘  ┌──────────────┐
                          + frontend/dist│  mock-oidc   │
                          (read-only)    │  :8081       │
                                         └──────────────┘
```

- **nginx**: `containers/nginx-dev/Containerfile` (nginx-unprivileged),
  listens on `127.0.0.1:8080`. Two server blocks (admin host and
  default/content host, `containers/nginx-dev/nginx.conf`) that proxy
  everything 1-to-1 to the app, plus the oidc alias block. No SPA root, no
  `/admin` logic, no internal location. Only `/healthz-proxy` is nginx's
  own (the e2e runner waits on it).
- **app**: `containers/plak/Containerfile` (multi-stage: a node stage builds the
  SPA and puts it in the image at `/app/spa`; `uv` for the backend; the
  build context is the repo root). Does route resolution, the access
  decision, audit, file resolution and the streaming itself. In compose the
  app gets `./frontend/dist` mounted read-only at `/spa`
  (`PLAK_SPA_PATH=/spa`), so that `just build-spa` is visible without a
  rebuild. The same holds for the backend: `./backend/src` is read-only at
  `/app/src` and uvicorn runs with `--reload`, so a change in Python code
  is there within seconds. That polls (`WATCHFILES_FORCE_POLLING`), because
  over the mount from macOS inotify events do not reach the container. A
  new migration or a changed dependency still needs `podman compose -f
  dev/compose.yml build`, because the migration service runs from the
  image. Gets `PLAK_BASE_URL` (the admin host) and
  `PLAK_CONTENT_BASE_URL` (the content host) for `TrustedHostMiddleware`,
  the host separation and the Origin check. Security headers (HSTS only on
  https, `Permissions-Policy`, COOP and `frame-ancestors` under `/admin`)
  the app sets itself.
- **postgres**: PostgreSQL 16, with a healthcheck (`pg_isready`) that
  the app waits for.
- **mock-oidc**: `ghcr.io/navikt/mock-oauth2-server`, configured via
  the file `PLAK_MOCK_CONFIG` points at (default
  `dev/mock-oidc-config.json`), reachable on `127.0.0.1:8081`.

Both ports are bound explicitly to `127.0.0.1`, not to all interfaces:
the dev stack is not meant to be reachable from outside your own
machine.

| Port | Service | Purpose |
|---|---|---|
| `127.0.0.1:8080` | nginx | `beheer.plak.localhost`: SPA and `/admin`; `plak.localhost`: content |
| `127.0.0.1:8081` | mock-oidc | OIDC discovery and tokens (reachable separately for debugging) |

## Mock login as dev administrator

`dev/mock-oidc-config.json` configures the mock OIDC server with a
fixed `tokenCallback` that gives every login the same claims:

```json
{
  "sub": "dev-beheerder",
  "acr": "http://eidas.europa.eu/LoA/substantial",
  "email": "beheerder@plak.local",
  "email_verified": true
}
```

The app container gets `PLAK_BOOTSTRAP_ADMIN_SUB=dev-beheerder`
(see `dev/compose.yml`), so this sub record automatically gets the
platform role `admin` and status `active` on the first `/admin` login
(spec §7, bootstrap). Log in via
`http://beheer.plak.localhost:8080/-/login`; there is no
choice screen, every login comes in as `dev-beheerder`.

Alongside the `authorization_code` rule, the mock server has the same rule for
`refresh_token`, because the periodic revalidation with the IdP
(`PLAK_IDP_RECHECK_SECONDS`, `auth/revalidation.py`) refreshes with it.
Without that rule the mock would answer a refresh with different claims and
the revalidation would drop the session on a sub mismatch.

To test other member profiles (a deactivated member or an ordinary
group member, for instance), adjust `tokenCallbacks` with an extra
`requestMappings` rule on a different `scope` value, or deactivate a
second sub via `/admin/-/platform` after a first visit with that sub.
The login screen below is the quicker route.

### The login screen of the mock, for manual testing

`interactiveLogin` is `false` by default and stays that way: the e2e suite
logs in without a screen and would hang on one. For testing by hand you turn
it on for one run:

```bash
PLAK_MOCK_INTERACTIVE_LOGIN=1 ./dev/up.sh
```

`dev/up.sh` then exports `PLAK_MOCK_CONFIG=./mock-oidc-config.interactive.json`
instead of leaving compose on its default `dev/mock-oidc-config.json`, and says
which of the two it took. The variable is read once, at startup; without it the
next `./dev/up.sh` is silent again.

What changes: `/-/login` now lands on a login page of the mock with a subject
field. Whatever you type there becomes the `sub` of the session, so you can
walk into the admin as any account: `dev-beheerder` for the bootstrap
administrator, anything else for a member you created or deactivated
yourself through `/admin/-/platform`. The config fills the rest of the claims
around it (`acr` at the required level, `email` as `<subject>@plak.local`),
so the app sees a complete id token whichever subject you pick.

This is a manual testing aid, nothing more. It is not a login screen of Plak
itself, it authenticates nobody, and it exists only in the dev stack.
Two things to know while using it:

- Do not run the e2e suite against a stack started this way. That suite has
  its own stack and its own config (`e2e/mock-oidc-config.e2e.json`), so
  `just e2e` is unaffected either way.
- The periodic revalidation against the IdP (`PLAK_IDP_RECHECK_SECONDS`,
  default 900) refreshes with a `refresh_token` grant, which carries no
  subject field for the mock to match on. A session may therefore be dropped
  after a quarter of an hour in this mode; log in again.

## OIDC configuration

The app is an OIDC client (authorization code + PKCE). The settings
live as `PLAK_OIDC_*` in the environment; `dev/compose.yml` and
`dev/.secrets/app.env` fill them for the dev stack.

| Variable | Meaning |
|---|---|
| `PLAK_OIDC_ISSUER` | Issuer URL of the OP, exactly equal to `issuer` in the discovery metadata |
| `PLAK_OIDC_CLIENT_ID` | Client id at the OP |
| `PLAK_OIDC_CLIENT_AUTH` | Client authentication at the token endpoint: `private_key_jwt` (default), `client_secret_post` or `client_secret_basic` |
| `PLAK_OIDC_CLIENT_PRIVATE_JWK` | Private RSA or EC key (JSON) for `private_key_jwt`; required with that method, not allowed with the others |
| `PLAK_OIDC_CLIENT_SECRET` | Client secret for `client_secret_post` and `client_secret_basic`; required with those methods, not allowed with `private_key_jwt` |
| `PLAK_OIDC_REQUIRED_ACR` | Comma-separated list of allowed `acr` values in the id token; empty or absent = no acr check and no `acr_values` in the authorization request |
| `PLAK_OIDC_ISS_REQUIRED` | RFC 9207: `iss` required in the callback (always `true` in production) |
| `PLAK_IDP_RECHECK_SECONDS` | How often at most a session is checked again at the IdP (`grant_type=refresh_token`); default 900, `0` turns it off |

Exactly the secrets belonging to the chosen method have to be set.
The app refuses to start on a missing or superfluous secret; the
message names only the variable name, never the value.

An empty `PLAK_OIDC_REQUIRED_ACR` logs a message at startup, in
`PLAK_ENVIRONMENT=productie` as a warning: the authentication level then
depends entirely on the OP. The other production requirements
(`PLAK_BEHIND_PROXY`, `PLAK_OIDC_ISS_REQUIRED`, `PLAK_BASE_URL`,
`PLAK_CONTENT_BASE_URL`) stay fail-fast.

In `PLAK_ENVIRONMENT=productie` the issuer is checked as well:
`PLAK_OIDC_ISSUER` has to be `https` and has to name a real host. `localhost`,
anything under `.localhost`, `127.0.0.0/8` and `[::1]` are refused at startup,
because an issuer there is the dev mock, not an IdP. On ZAD the check applies
to the issuer derived from `OIDC_DISCOVERY_URL` just the same. In dev nothing
changes: the mock runs on `http://oidc.plak.localhost:8080/default`.

Whatever the environment, the app logs one INFO line at startup naming the
issuer and the client id (`OIDC configuration: issuer ..., client_id ...`), so a
wrong configuration is visible in the first lines of the log instead of only in a
failing login. No secret is ever in that line.

### On ZAD (Keycloak)

The Keycloak service of ZAD creates a confidential client with a
client secret per deployment (`private_key_jwt` cannot be set there via
self-service) and injects `OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID` and
`OIDC_CLIENT_SECRET` (without the `PLAK_` prefix) into the container. The
app takes those over as long as the `PLAK_OIDC_*` variable itself is not
set:

- `PLAK_OIDC_ISSUER` from `OIDC_DISCOVERY_URL` without the suffix
  `/.well-known/openid-configuration` (OIDC Discovery 1.0 §4);
- `PLAK_OIDC_CLIENT_ID` from `OIDC_CLIENT_ID`;
- `PLAK_OIDC_CLIENT_SECRET` from `OIDC_CLIENT_SECRET`, only when
  `PLAK_OIDC_CLIENT_AUTH` is a `client_secret_*` method.

In the `user-env-vars` of the ZAD project this then suffices:

```
PLAK_OIDC_CLIENT_AUTH=client_secret_post
PLAK_OIDC_REQUIRED_ACR=
PLAK_OIDC_ISS_REQUIRED=true
```

The ZAD Keycloak (`keycloak.rijksapp.nl`, discovery verified on
2026-09-12) advertises as `token_endpoint_auth_methods_supported`
`private_key_jwt`, `client_secret_basic`, `client_secret_post`,
`tls_client_auth` and `client_secret_jwt`, and as `acr_values_supported`
`["0","1"]`. Keycloak sets `acr` to `1` after an authentication at the
first level of the flow and to `0` when an existing session is reused
without re-authentication. An eIDAS LoA such as the dev value
`http://eidas.europa.eu/LoA/substantial` never comes out of there; with
that requirement nobody can log in on ZAD. So leave
`PLAK_OIDC_REQUIRED_ACR` empty on ZAD. `PLAK_OIDC_REQUIRED_ACR=1` would
refuse reuse of the Keycloak SSO session and is only an option after
verification with a real id token from the project realm.

## Justfile recipes

```bash
just             # toont beschikbare commands
just dev         # start de backend lokaal met uvicorn (reload aan)
just seed        # vult de draaiende dev-stack met voorbeeldinhoud
just test        # draait de backend- en CLI-testsuites (testcontainers, zie hieronder)
just test-cli    # draait alleen de CLI-tests (cli/tests)
just lint        # ruff check op backend/src, backend/tests en cli/
just coverage    # de drie suites met dekking
```

`just coverage` measures branch coverage and fails below 100%, the floor
in `backend/pyproject.toml`, `cli/pyproject.toml` and, per metric,
`frontend/vite.config.ts`. A path no test can reach carries an explicit
exclusion with its reason (`# pragma: no cover - ...` in Python,
`/* v8 ignore start */ ... /* v8 ignore stop */` in TypeScript; `next` is
silently ignored there). CI enforces the floors without running the
suites a second time: the CLI and frontend inside their test jobs, the
backend in `backend-coverage` over the data of its test parts, and
`.github/scripts/release.py` in `release-script`.

On the backend the measurement needs `concurrency = ["thread",
"greenlet"]`: SQLAlchemy's async adapter runs most of an async route body
inside a greenlet, and without that setting coverage.py does not see it
(`api/admin.py` measured 58% against 82% on the same tests).

`just dev` runs the app without nginx in front of it on `localhost:8000`.
Without `PLAK_CONTENT_BASE_URL` there is one host with both worlds (the app
logs a warning about that at startup); the SPA comes from
`PLAK_SPA_PATH`, default `../frontend/dist` relative to `backend/`.
Use the full compose stack (`./dev/up.sh`) to test the two hosts and the
host separation.

## Testcontainers via the Podman socket

The backend tests run against a real PostgreSQL container
(testcontainers), not against an in-memory database. Testcontainers
expects a Docker daemon by default; point it at the Podman socket via
`DOCKER_HOST`. `just test` already does this for you, platform-dependent:

- **Linux**: `unix://${XDG_RUNTIME_DIR}/podman/podman.sock` (the native
  rootless Podman socket; make sure it is active, for example via
  `systemctl --user start podman.socket`).
- **macOS**: Podman runs via a light Linux VM (`podman machine`);
  there is no native Podman socket on the host. `just test` gets the path
  with `podman machine inspect` and falls back to
  `~/.local/share/containers/podman/machine/podman.sock` if that fails for
  whatever reason.

To run this separately from `just`, set `DOCKER_HOST` yourself following
the same pattern before you call `uv run pytest`:

```bash
export DOCKER_HOST="unix://$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}')"
cd backend && uv run pytest
```

A common pitfall: if the Podman machine is not running
(`podman machine start` not executed), testcontainers fails with a
connection error that gives little hint that the socket is to blame;
check `podman machine list` first on an unexplained testcontainers
failure.
