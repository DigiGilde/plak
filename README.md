# Plak

**Status: under development.** Plak is not in production yet; this
document describes the design and the state of the build, not a
finished product.

Plak is a platform that lets government organisations (among them
MinBZK and the Nederlandse Digitale Dienst) publish and share static
sites easily: publicly, behind SSO Rijk, with invitees by email
address, or through a secret link. Every pull request can get a
preview of its own.

## Disclaimer

Read this before you use anything here.

- **Experimental, not in production.** Plak runs nowhere for real
  users. There is no support, no service level and no guarantee of any
  kind. The HTTP contract, the URL design, the database schema and the
  configuration can change without notice and without a migration path.
- **Not independently reviewed.** No external security review, no
  penetration test, no audit. The security design is written down
  (`docs/security.md`) and covered by tests, which is not the same as
  being verified by somebody else. Do not put confidential data in an
  instance you run.
- **Built for one environment.** The target is ZAD (Zelfservice
  Applicatie Deployment) with the Keycloak of that platform, which
  brokers to SSO Rijk. Everything environment specific is
  configurable, but no other platform and no other OIDC provider has
  been tried. If you run it elsewhere, you are the first.
- **Reporting.** Security issues go through
  [a private security advisory](https://github.com/DigiGilde/plak/security/advisories/new)
  or to `digigilde@rijksoverheid.nl`, never through a public issue;
  `SECURITY.md` has the procedure. Everything else belongs in the issue
  tracker of this repository.
- **Licence.** EUPL-1.2 (see `LICENSE` and `publiccode.yml`). That
  licence grants the software as is, without warranty of any kind and
  without liability, to the extent the law allows.

The name comes from `plakkaat`, the Dutch word for a placard. Placards
used to be put up to share something with more people than you could
reach yourself. That is what Plak does too. And like a placard it can
hang in public space, visible to everyone, or in a closed-off place
where only a smaller group comes.

Plak is built for our own use, and therefore runs on the combination we
use ourselves: deployment on ZAD (Zelfservice Applicatie Deployment,
self-service application deployment) and login through that platform's
Keycloak, which in turn brokers to SSO Rijk. It is not a product
offered separately from that environment; everything environment
specific is configurable, though, so another OIDC provider or another
platform is not ruled out.

## Core concepts

- **Group**: a collaboration unit (for example `team-aurora`, `fin`). Holds
  sites and members. Groups do not nest.
- **Site**: the published thing, with a live version and previews
  (comparable to the GitLab/Vercel model).
- **Version**: an immutable file tree, with target `live` or `preview`.
- **Preview**: a named temporary variant per ref (for example
  `pr-42`), pointing at a version with target `preview`.

## The five access levels

Visibility is a property per site (previews inherit it, with an
optional override per preview):

1. **Public**: everyone.
2. **Secret link**: access through `?key=selector.verifier`, without
   logging in.
3. **SSO Rijk**: any valid SSO Rijk login.
4. **Group members**: logged in and an active member of the site's
   group.
5. **Invitees**: logged in and the sub or verified email address is on
   the site's invitee list.

Viewing and administering are separated: a login only to look at
something creates no member record. Administering (creating sites,
uploading, setting access) does require an activated member record; see
`docs/security.md` for the full access and security logic.

## URL design

Content owns the top level; everything belonging to the platform lives
under a single prefix `/admin`, so that the top level stays maximally
free for groups.

| Path | Meaning |
|---|---|
| `/{group}/{site}/...` | live content |
| `/{group}/{site}/_preview/{ref}/...` | preview content |
| `/{group}/{site}/_version/{version-id}/...` | view an old version (active group members only) |
| `/admin` | site overview (start page after login) |
| `/admin/{group}` | group page |
| `/admin/{group}/{site}` | site detail, tab Overzicht (overview); other tabs: `/access`, `/previews`, `/versions`, `/deploy` |
| `/admin/-/members` | platform administration: member activation |
| `/admin/-/privacy`, `/admin/-/toegankelijkheid`, `/admin/-/over` | platform pages (publicly accessible, no login) |
| `/-/login` | starts the OIDC login; `/-/oauth2/callback` is the redirect URI |
| `/-/logout` | POST, ends the session |
| `/-/api/v1/...` | JSON and deploy API |
| `/-/api/docs` | API documentation (OpenAPI, self-hosted UI, publicly readable) |
| `/admin/assets/...` | SPA assets (Vite base `/admin/`) |
| `/` (admin host) | 303 to `/admin`; the SPA chooses there between landing page and overview |
| `/` (content host) | public front page: explanation, login button to the admin host and the footer |

## Quickstart: local development

```bash
git clone <this-repo>
cd plak
./dev/up.sh
```

This starts the full stack (nginx + app + PostgreSQL + mock OIDC)
through Podman compose on port `8080`. The stack has two hosts and
separates them on the `Host` header: `beheer.plak.localhost` for
`/admin` and `plak.localhost` for content. On most systems both resolve
to 127.0.0.1 by themselves; if they do not, put them in `/etc/hosts`.

Log in through `http://beheer.plak.localhost:8080/-/login`; the mock
OIDC server logs you in automatically as a preconfigured dev
administrator. `http://localhost:8080` does not work: an unknown host
gets a 400.

Install the commit hooks once per clone with `uvx pre-commit install`;
the CI job `pre-commit` runs the same set over the whole tree.

See `docs/local-development.md` for prerequisites, the compose
topology, justfile recipes and the Testcontainers/Podman socket
pattern.

## Publishing with the `plak` CLI

The CLI lives in `cli/`, a uv project of its own. Install it as a tool
and the `plak` command is on your `PATH`:

```bash
uv tool install "git+https://github.com/DigiGilde/plak@beta#subdirectory=cli"
```

That is the latest: `beta` is the default branch, so `uv tool upgrade plak`
picks up whatever has landed on it. Once a version tag exists, pin instead:

```bash
uv tool install "git+https://github.com/DigiGilde/plak@<tag>#subdirectory=cli"
```

There is no tag yet, so `<tag>` is the one thing to fill in. What a branch
gives up is the guarantee that tomorrow installs what you tested today.

The repository is public, so either form clones without credentials.

Then log in once and publish:

```bash
plak login
plak publish ./dist --site team-aurora/website
plak publish ./dist --site team-aurora/website --preview pr-42
```

Without `--host` the CLI talks to the DigiGilde instance,
`https://beheer.plak.rijks.app`. For another instance, log in once with
`plak login --host <admin origin>`: every command then uses that host
until you log in somewhere else. `--host` or `PLAK_HOST` overrides it
for a single command.

`plak login` uses the OAuth 2.0 Device Authorization Grant: it shows a
code, you confirm it in the admin, and the session belongs to your user
account, for every directory. The tokens go into the system keyring
(macOS Keychain, Secret Service on Linux).
Where there is no usable keyring, or with `--insecure-storage`, they go
into `~/.config/plak/hosts.json` (mode 0600) and `plak login` says so.
That file also remembers the host you last logged in to; set
`PLAK_CONFIG_DIR` or `XDG_CONFIG_HOME` to move it. An old `.env.plak`
from an earlier version is no longer read: log in again and delete it.

No site yet? Create the group and the site from the terminal too; you
become admin of both. The access flags are optional (`--access`,
`--secret-links`, `--invitees`); a site you create without them follows
its group's default access:

```bash
plak group create team-aurora --name "Team Aurora"
plak site create team-aurora/docs --title "Documentation" --access sso
```

To publish from CI, link the repository to the site; as site admin. For
a GitHub repository the IDs come from your own `gh` login, so a private
repository links too:

```bash
plak site link team-aurora/docs minbzk/website --live-branch main
```

Deleting groups and sites, unlinking and managing members stays in the admin.

From a checkout of this repository the same commands run without
installing anything:

```bash
uv run --project cli plak login
uv run --project cli plak publish ./dist --site team-aurora/website
```

`docs/publishing.md` has the full story: the base path contract for
your build, publishing from CI with the `publiceer` action, the curl
fallback and the error codes.

## Publishing from CI

The composite action in `actions/publiceer/` publishes from GitHub
Actions or Forgejo Actions without a secret: the workflow proves who it
is with an OIDC ID token, and Plak checks that token against the
repository linked to the site. Link the repository first, on the
**Deploy** tab of the site in the admin; that tab also shows a
ready-made workflow for the site. The core of it, a live deploy on every
push to `main`:

```yaml
on:
  push:
    branches: [main]

permissions:
  contents: read
  id-token: write

jobs:
  publiceer:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<commit-sha>
      - run: npm ci && npm run build
        env:
          PLAK_BASE_PATH: /team-aurora/website/
      - uses: DigiGilde/plak/actions/publiceer@<commit-sha>
        with:
          site: team-aurora/website
          dist-path: ./dist
```

Pin the action to a commit SHA, not to a branch or a tag. The full
workflow in `docs/publishing.md` adds a preview per pull request and its
clean-up, and shows the Forgejo variant.

## Publishing with Claude Code

The Claude Code plugin in `plugin/` lets Claude do the publishing with
the rules of this project in hand: a preview by default, live only when
you ask for it, and a `plak login` that you approve yourself. It uses
the `plak` CLI above, so install that first. Then, in Claude Code:

```text
/plugin marketplace add DigiGilde/plak
/plugin install plak@plak
```

Ask for it in the directory of your site, for instance "zet deze map
online op Plak". Auto-update is off for this marketplace unless you turn
it on (`/plugin`, tab **Marketplaces**). To get a newer version by hand,
refresh the marketplace with `/plugin marketplace update plak`, then pick
**Update now** on the plugin in the **Installed** tab of `/plugin`, or
run `claude plugin update plak@plak` in your shell. `docs/skill.md`
covers what the skill does and how to run its evals.

## Deploying on ZAD

Plak is not running in production yet; the rollout is prepared, not
done. The target is ZAD (Zelfservice voor Applicatie Deployment): no
Kubernetes manifests, but one declarative project file that the
platform manages. Plak is **one component** there, because the app
serves content, the admin SPA and the host separation itself.

- Two web addresses on that one component, through `publish-on-web`
  with a dotted `domain-format` and `root-component`:
  `beheer.plak.<domain>` and `plak.<domain>`. Both have to be approved
  by the platform team; without approval the rollout falls back to the
  cluster address.
- PostgreSQL and the content volume come as ZAD services, with the
  volume's size declared in the project file. For a lot of content,
  MinIO is the considered route, not a bigger volume.
- Secrets go in as user env vars (`zadctl env`), OIDC runs through the
  ZAD Keycloak, and migrations run through a job in the portal or at
  container start: ZAD has no notion of a Job.

`deploy/plak-project.example.yaml` is the project file with comments,
`docs/deploying-on-zad.md` the step-by-step guide from nothing to
running plus the open questions for the platform team.
`.github/workflows/` holds the CI and deploy workflows. The checks run on
every commit; the rollout half is still preparatory, because there is no
ZAD environment to deploy to yet.

## Documentation

- `docs/design.md`: the numbered design. Every "spec §7" or
  "behaviour requirement 6" in the code points at a section there.
- `docs/publishing.md`: how to publish a site (base path contract, the
  `publiceer` action, curl fallback, previews and `_version`).
- `docs/skill.md`: the Claude Code plugin in `plugin/`: installing it,
  what the skill does, and running its evals.
- `docs/security.md`: the living security checklist.
- `docs/privacy.md`: draft starting point for the DPIA, personal data
  processed and open privacy items.
- `docs/local-development.md`: dev stack, justfile, Testcontainers.
- `docs/deploying-on-zad.md`: the step-by-step guide for the rollout on
  ZAD.

## The technology in short

- `backend/`: Python >= 3.12, FastAPI, SQLAlchemy (async, asyncpg),
  Alembic, joserfc (JWT and JWKS for OIDC and CI tokens), pydantic-settings.
- `frontend/`: Vue 3 (runtime-only build), Vite, `@nldd/design-system`,
  `@tanstack/vue-query`.
- `cli/`: the publishing CLI, a uv project of its own that installs the
  `plak` command; its only dependency is httpx and it shares no code with
  the backend. `cli/tests/` is its suite (`just test-cli`).
- `actions/publiceer/`: the composite action for GitHub Actions and
  Forgejo Actions that calls that CLI. `plugin/` is the Claude Code
  plugin for the same job, with the `plak-publiceren` skill and its
  evals; `.claude-plugin/marketplace.json` offers it for installation.
- The app serves content and the admin SPA itself (`FileResponse` with
  Range, ETag/304 and the fixed header set) and separates the admin and
  content host on the `Host` header; nginx is only a dumb reverse proxy
  in the dev stack. There is no X-Accel-Redirect.
- PostgreSQL 16 as the content model database; the content volume is a
  filesystem content store.
- Containers through Podman (`Containerfile`, `podman compose`).

## Licence

EUPL-1.2, see `LICENSE`.
