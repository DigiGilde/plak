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
- **Reporting.** Security issues go to `digigilde@rijksoverheid.nl`,
  privately, never through a public issue; `SECURITY.md` has the
  procedure. Everything else belongs in the issue tracker of this
  repository, as soon as it is public.
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

Plak is the successor to `moza-publiceer-rapportages` (Java/Spring
Boot, never in production). That project proved the domain model and
the security design; Plak is a rebuild, greenfield, in the house
standard stack, without any migration or compatibility burden.

## Core concepts

- **Group**: a collaboration unit (for example `nldd`, `fin`). Holds
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

See `docs/local-development.md` for prerequisites, the compose
topology, justfile recipes and the Testcontainers/Podman socket
pattern.

## Publishing with the `plak` CLI

The CLI lives in `cli/`, a uv project of its own. Install it as a tool
and the `plak` command is on your `PATH`:

```bash
uv tool install "git+https://github.com/minbzk/plak@<commit-sha>#subdirectory=cli"
```

Pin `<commit-sha>` to a real commit; the bare `#subdirectory=cli` form without a
ref tracks `main`, so a single commit landing there would run on every machine
that installs or upgrades afterwards. `<commit-sha>` is a placeholder: this
repository has no remote and no tagged release yet, so there is no fixed ref to
put here today. Replace it with a version tag once one exists (see
`cli/uv.lock` for the exact dependencies a given ref installs). To move to a
newer commit once pinned, reinstall with that commit's SHA; `uv tool upgrade
plak` re-resolves against the same pinned ref and finds nothing newer.

Then log in once per instance and publish:

```bash
plak login --host https://beheer.plak.example.org
plak publish ./dist --site nldd/website
plak publish ./dist --site nldd/website --preview pr-42
```

`plak login` uses the OAuth 2.0 Device Authorization Grant: it shows a
code, you confirm it in the admin, and the session lands in `.env.plak`
in the current directory (mode 0600, so put it in your `.gitignore`).

From a checkout of this repository the same commands run without
installing anything:

```bash
uv run --project cli plak login --host https://beheer.plak.example.org
uv run --project cli plak publish ./dist --site nldd/website
```

`docs/publishing.md` has the full story: the base path contract for
your build, publishing from CI with the `publiceer` action, the curl
fallback and the error codes.

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
`.github/workflows/` holds the CI and deploy workflows; the repo has no
remote yet, so those are preparatory.

## Documentation

- `docs/design.md`: the numbered design. Every "spec §7" or
  "behaviour requirement 6" in the code points at a section there.
- `docs/publishing.md`: how to publish a site (base path contract, the
  `publiceer` action, curl fallback, previews and `_version`).
- `docs/skill.md`: the Claude Code plugin in `plugin/`: installing it,
  what the skill does, and running its evals.
- `docs/security.md`: the living security checklist.
- `docs/local-development.md`: dev stack, justfile, Testcontainers.
- `docs/deploying-on-zad.md`: the step-by-step guide for the rollout on
  ZAD.

## The technology in short

- `backend/`: Python >= 3.12, FastAPI, SQLAlchemy (async, asyncpg),
  Alembic, authlib (OIDC), pydantic-settings.
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
