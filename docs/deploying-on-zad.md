# Deploying on ZAD

From nothing to a running Plak on ZAD (Zelfservice voor Applicatie
Deployment, self-service application deployment, the RijksICTGilde platform).
The example project file is in `deploy/plak-project.example.yaml`; the
workflows in `.github/workflows/` do the build and the rollout.

**Status: preparatory.** Plak does not run on ZAD yet. This document describes
the route as it follows from the ZAD project schema, the service catalogue and
the platform documentation, with the open questions named explicitly in §10.

## 1. The model in brief

ZAD does not accept Kubernetes manifests. A project is one declarative YAML
file with four concepts:

- **project**: the administrative unit, with members, domains and secrets.
- **dienst** (service): something the platform delivers and that gives you
  variables: `publish-on-web`, `postgresql-database`, `persistent-storage`,
  `minio-storage`, `keycloak`.
- **component**: a slot with a port, resources and services, without an image.
  Exactly one container per component; sidecars are added by the platform only.
- **deployment**: an environment (`productie`) that fixes an image and a web
  address per component.

Plak fits in there as **one component**. The app serves the content, the admin
SPA and the host separation itself, so no nginx sidecar and no shared volume
are needed. On every deploy CI sets only the image of that component.

## 2. Requesting a project

1. Request a ZAD project through the portal or `zadctl project create`. The
   platform assigns a project key (the `project-id` that CI uses).
2. Put the administrators of Plak in as project members (`users:` in the
   project file, role `admin`).
3. Create an API key for CI and set it as the repository secret
   `ZAD_API_KEY`. Set the project key as the repository variable
   `ZAD_PROJECT_ID`.

## 3. Getting the domain and subdomain approved

Plak needs two hostnames on one component:

| Host | What is there |
|---|---|
| `beheer.plak.<domein>` | the admin SPA, the API and the deploy endpoints |
| `plak.<domein>` | the published content and the content login |

These come from a dotted `domain-format` plus `root-component`, both directly
on the deployment (not under a service):

```yaml
deployments:
  - name: productie
    domain-format: component.subdomain
    domain-mode: nice-url
    subdomain: plak
    base-domain: rijks.app
    issuer: letsencrypt
    root-component: beheer
```

`component.subdomain` yields `{component}.{subdomain}.{domein}`; because the
component is called `beheer` that is `beheer.plak.rijks.app`.
`root-component` makes that same component also serve the address without that
first label: `plak.rijks.app`. Two Ingress objects, two Let's Encrypt
certificates, one pod.

Three things to know:

- **The web address fields sit directly on the deployment, not under a
  service.** `project_v2.json` knows `domain-format`, `subdomain`,
  `base-domain`, `issuer` and `root-component` as fields of the deployment
  object itself. The field `domain-mode` still exists (optional in the
  schema), but is not derived from `domain-format`: the Operations Manager
  reads `domain-mode` literally, and both the root ingress
  (`plak.rijks.app`) and the self-service registration of the subdomain
  start only if that value is exactly `nice-url`. Without
  `domain-mode: nice-url` alongside it there is no second host, even with
  `domain-format` and `root-component` set.
- **The component name is the first label of the hostname.** That is why the
  component is called `beheer` and not `app`.
- **Approval is a hard precondition.** All platform domains (`rijks.app`,
  `rijksapps.nl`, `rijksapp.nl`, `rijksapp.dev`) have restricted subdomains.
  Both the domain and the subdomain have to be `approved` in the `domains:`
  block of the project. If it says `requested`, the deployment falls back
  **silently** to `component-deployment-project` on the cluster address. There
  is no error message: the app runs, but on a different host than
  `PLAK_BASE_URL` and `PLAK_CONTENT_BASE_URL` say, and then the host
  separation refuses everything.

Request the subdomain through the portal or the API; a platform administrator
sets it to `approved`. Check with `zadctl project subdomains` and
`zadctl project check-subdomain plak rijks.app`: that second command wants
subdomain and base domain, both mandatory.

## 4. Applying the configuration

Take `deploy/plak-project.example.yaml` as a base and adjust: `name`, `users`,
`subdomain`, `base-domain` and the redirect URI in the Keycloak service
configuration. The file validates against `opi/schemas/project_v2.json` of
RIG-Cluster.

What is in it and why:

| Part | Why |
|---|---|
| `services: publish-on-web, postgresql-database, persistent-storage, keycloak` | the four services Plak needs |
| component `beheer`, inbound 8080 | the app; one container, port 8080 as in the Containerfile |
| `persistent-storage` `content` on `/content` | the content volume, `PLAK_CONTENT_ROOT` |
| `probe: scheme: tcp` | `/-/healthz` exists on the admin host only and the probe arrives with the pod IP as Host; an HTTP probe would always fail |
| `aliases` for `PLAK_DB_URL` | the DB service delivers separate variables, Plak wants a DSN |
| `backup: enabled: true` | content sits on a PVC, not in the database |

Changes go in through the portal, `zadctl` or the API; the project file itself
is managed by the platform. Useful commands:

```bash
zadctl project describe
zadctl project pending        # saved but not deployed yet
zadctl project status         # deployments, components and URLs
```

## 5. Setting secrets and settings

Two layers. Non-secret settings sit as `env-vars` in the project file; secrets
go in as **user-env-vars**, stored AGE-encrypted in the project file and
delivered as a Secret through `envFrom` in the pod:

```bash
zadctl env add -c beheer --deployment productie \
  PLAK_SESSION_SECRET=... \
  PLAK_AUDIT_PEPPER=... \
  PLAK_AUDIT_IP_KEY=...
```

`zadctl env` writes user-env-vars, and those have two levels of their own.
Without `--deployment` they land component-wide
(`components[].user-env-vars`, in the root of the project) and apply to every
deployment; with `--deployment` they sit under that one deployment and win
over the component-wide value. That choice determines what a second
deployment inherits, because a clone does not copy the deployment block of
the component along with it (§8, "A second deployment").

What Plak needs. `PLAK_DB_URL`, `PLAK_CONTENT_ROOT`, `PLAK_SESSION_SECRET`,
`PLAK_AUDIT_PEPPER` and `PLAK_AUDIT_IP_KEY` have no default value and are
always there; the bottom four become mandatory as soon as
`PLAK_ENVIRONMENT=productie`. If one is missing the app does not start and the
error message names the variable. `PLAK_BOOTSTRAP_ADMIN_SUB` may technically
stay empty, but then nobody is a platform administrator: every first visit
immediately becomes an active but ordinary member, and there is nobody to make
anybody an administrator.

| Variable | Source | Notes |
|---|---|---|
| `PLAK_DB_URL` | alias on the DB service | `postgresql+asyncpg://...`; the app, alembic and the purge job all three use this one account |
| `PLAK_CONTENT_ROOT` | env-vars | equal to the `mount-path` of the volume: `/content` |
| `PLAK_SESSION_SECRET` | user-env-var | at least 32 bytes |
| `PLAK_AUDIT_PEPPER` | user-env-var | at least 32 bytes, different from `PLAK_SESSION_SECRET`; rotating makes old audit pseudonyms incomparable |
| `PLAK_AUDIT_IP_KEY` | user-env-var | base64 of 32 random bytes (`openssl rand -base64 32`), different from `PLAK_SESSION_SECRET`, `PLAK_AUDIT_PEPPER` and (if set) `PLAK_AUDIT_IP_KEY_PREVIOUS`; see below for rotating |
| `PLAK_AUDIT_IP_KEY_PREVIOUS` | user-env-var | optional; only during a key rotation, see below |
| `PLAK_BOOTSTRAP_ADMIN_SUB` | env-vars | the `sub` of the first platform administrator, otherwise nobody can get in |
| `PLAK_ENVIRONMENT` | env-vars | `productie` |
| `PLAK_BASE_URL` | env-vars | `https://beheer.plak.<domein>` |
| `PLAK_CONTENT_BASE_URL` | env-vars | `https://plak.<domein>` |
| `PLAK_BEHIND_PROXY` | env-vars | `true`: the HAProxy router stands in front of the pod |
| `PLAK_OIDC_ISS_REQUIRED` | env-vars | `true` |
| `PLAK_IDP_RECHECK_SECONDS` | env-vars | optional, default `900`; how often at most a session is checked again at Keycloak, `0` turns it off |
| `PLAK_OIDC_RP_LOGOUT` | env-vars | `false` until `{PLAK_CONTENT_BASE_URL}/-/logout?from=beheer` is registered as post-logout redirect at the IdP |
| `PLAK_AUDIT_LOOKUP_DAILY_LIMIT` | env-vars | optional, default 25, 1-1000; daily limit per platform administrator on resolving a pseudonym or IP address |
| `PLAK_CI_FORGEJO_HOSTS` | env-vars | optional, comma-separated list of https base URLs, default `https://code.overheid.nl`; every Forgejo instance whose OIDC ID token from Actions is accepted for CI publishing |
| `PLAK_INGEST_MAX_BODY` | env-vars | optional, bytes, default `104857600` (100 MiB); the largest upload |
| `PLAK_INGEST_MAX_FILE` | env-vars | optional, bytes, default `52428800` (50 MiB); the largest single unpacked file |
| `PLAK_INGEST_MAX_TOTAL` | env-vars | optional, bytes, default `209715200` (200 MiB); the largest unpacked site per deploy |
| `PLAK_SITE_MAX_BYTES` | env-vars | optional, bytes, default `524288000` (500 MiB), `0` turns it off; what all versions of one site together may occupy. ZAD runs with `209715200` (200 MiB), set in the project file: the volume is capped at 1Gi (about 700 MiB usable), which leaves room for several sites at their maximum while a site can still publish several times a day before the nightly cleanup |
| `PLAK_LIVE_VERSIONS_KEPT` | env-vars | optional, default `5`, `0` keeps everything; previous live versions the nightly cleanup keeps besides the current live one, for every site without a number of its own |
| `PLAK_STORAGE_MIN_FREE_BYTES` | env-vars | optional, bytes, default `104857600` (100 MiB), `0` turns it off; free space a deploy never takes the volume below |

The last six defaults fit the 1Gi content volume (§9). While a deploy runs,
its upload and what it unpacks sit on the volume side by side, so one deploy
needs at most `PLAK_INGEST_MAX_BODY` plus `PLAK_INGEST_MAX_TOTAL` (300 MiB)
on top of the stored versions. Plak measures the actual deploy, not that
maximum: it checks the declared upload size before reading the body and keeps
checking while it writes, refusing with `503` `STORAGE_UNAVAILABLE` rather than
letting the volume drop below `PLAK_STORAGE_MIN_FREE_BYTES`. Keep the floor plus
one full-size deploy (400 MiB with the defaults) well below the volume size, or
a large deploy can only ever be refused; a bigger volume is not available on ZAD (§9).

Besides the host separation, `PLAK_BASE_URL` is also the exact CI audience: a
GitHub or Forgejo workflow requests its ID token with this value as
`audience`, and Plak refuses a token whose `aud` is not literally equal to it
(`ci/tokens.py`, no normalisation of a trailing `/`). Without
`PLAK_BASE_URL` CI tokens are refused anyway. The pod needs outbound https to
`token.actions.githubusercontent.com` (JWKS for GitHub tokens),
`api.github.com` (looking up repositories when linking) and every host in
`PLAK_CI_FORGEJO_HOSTS` (JWKS and repository lookup for Forgejo); without
those outbound connections linking does not work and CI deploys are refused
with `CI_PROVIDER_UNREACHABLE`.

Rotating `PLAK_AUDIT_IP_KEY`: set the new value as `PLAK_AUDIT_IP_KEY` and the
old one as `PLAK_AUDIT_IP_KEY_PREVIOUS`, and deploy. New audit records are
encrypted under the new key; older ones stay decryptable as long as the
previous key is in `PLAK_AUDIT_IP_KEY_PREVIOUS`. Only remove that variable
once nothing has to be revealed any more from the period before the rotation;
after that those records become unreadable (`docs/security.md`), although
they do stay in place until their own retention period is up.

`PLAK_BASE_URL` and `PLAK_CONTENT_BASE_URL` are not cosmetic. The app derives
the host separation from them (which path exists on which host), the HSTS
decision, and every shareable URL the SPA shows. If they differ from what the
ingress serves, everybody gets a neutral 404.

### OIDC

The `keycloak` service injects `OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID` and
`OIDC_CLIENT_SECRET` (without the `PLAK_` prefix). Plak reads those as the
source for `PLAK_OIDC_ISSUER`, `PLAK_OIDC_CLIENT_ID` and
`PLAK_OIDC_CLIENT_SECRET`, so you do not have to set those three yourself.
What you do have to set:

- `PLAK_OIDC_CLIENT_AUTH=client_secret_post`. The ZAD Keycloak creates a
  client-secret client; `private_key_jwt` cannot be set through self-service
  (see §10).
- `PLAK_OIDC_REQUIRED_ACR=` (empty). The ZAD Keycloak advertises only the acr
  values `0` and `1`, so an eIDAS LoA requirement shuts everybody out. At
  startup the app logs explicitly that there is no acr check.

### Revalidation at the IdP

With `PLAK_IDP_RECHECK_SECONDS` (default 900) Plak redeems the refresh token
of the session at the token endpoint of Keycloak on the first request after
that interval, with the same client authentication as the login
(`PLAK_OIDC_CLIENT_AUTH`). If Keycloak refuses with `invalid_grant`, the
session has been ended there (blocked, deleted, logged out) and the Plak
session lapses immediately. For that the pod needs outbound https to the
Keycloak service, which already applied for the login anyway.

If something goes wrong with the link itself instead of with a session
(`invalid_client` because the client secret no longer matches,
`unauthorized_client` or `unsupported_grant_type` because the client is not
allowed the refresh grant), then nobody is logged out, but Plak complains
loudly: an ERROR line with the error code and the issuer, at most one per
minute, and for as long as it lasts `GET /-/healthz` on the admin host
answers with status 200 and

```json
{"status": "degraded", "checks": ["idp_revalidation"]}
```

The endpoint is public, so it names only the check, never the error code; the
ERROR line in the log is where that is. The probe on ZAD is `tcp`, so it does
not notice either: alarm on the log or on `/-/healthz`. The first successful
revalidation clears the complaint by itself.

The precondition is that the client issues a refresh token; with the default
`sso-only` template of the ZAD Keycloak it does. If the client issues none,
the session stays and the app logs a warning per session: the revalidation is
then no extra risk, but also no extra certainty.

### Content volume

Nobody watches the ZAD probe (`tcp`), so trouble with the content volume (1Gi,
§9) is made visible in three places.

`GET https://beheer.plak.<domain>/-/healthz` is public and needs no login. It
is on the admin host only; the content host answers it with the neutral 404.
It is never cached, and it counts against the normal rate limit. The answer
names only the checks that complain, never a message or a number:

| Answer | Status | Meaning |
|---|---|---|
| `{"status": "ok"}` | 200 | nothing complains |
| `{"status": "degraded", "checks": ["storage"]}` | 200 | one or more of `storage`, `content_root`, `idp_revalidation` complain |
| `{"status": "fail", "checks": ["database"]}` | 503 | the database does not answer within two seconds (`SELECT 1`); degraded checks may be listed next to `database` |

`storage` appears as soon as the free space drops below
`PLAK_STORAGE_MIN_FREE_BYTES` plus `PLAK_INGEST_MAX_TOTAL` (300 MiB with the
defaults): the point where a deploy of the maximum size would be refused. With
`PLAK_STORAGE_MIN_FREE_BYTES=0` the check is off and the check never appears.

`content_root` is decided once, at startup, and only with
`PLAK_ENVIRONMENT=productie`: the content root is on the same device as its
parent, so no volume is mounted there. Plak creates the directory itself, so a
wrong `PLAK_CONTENT_ROOT` would otherwise write into the container's own layer
and lose everything at the next restart. This is a warning, the pod starts
anyway, and only a restart with the right mount clears it.

Several complaints sit in one answer, in the order `database`, `storage`,
`content_root`, `idp_revalidation`. The detailed messages are in the log and on
"Platformbeheer".

The log is the place to alarm on, because it is there without anyone calling
anything. The low-space condition is measured every five minutes by a small
task inside the app, and writes an ERROR line (`Content volume has 240 MiB
free; a deploy of the maximum size (200 MiB) would take it below the 100 MiB
reserve. Deploys will be refused soon; free up space or enlarge the volume.`)
at most once per hour for as long as it lasts. A content root that is not a
mount point writes an ERROR line once, at startup.

A platform administrator sees the whole volume in the admin SPA, on
"Platformbeheer" (`/-/platform`), below the member list: size, used, free and
the reserve, without any group or site. It is backed by
`GET /-/api/v1/platform/storage` (platform administrators only) and turns red
under the same threshold as `storage`.

## 6. Registering the second OIDC redirect URI

Plak logs in on two origins: `/-/oauth2/callback` on the admin host and
`/-/oauth2/callback` on the content host. The ZAD Keycloak automatically sets
`https://{host}/*` for every **ingress host of the deployment**, but as soon
as `domain-format` is set explicitly the root host is not counted in there.
So the content callback has to be added by hand, in the Keycloak service
configuration of the project:

```yaml
services:
  - keycloak:
      config:
        template: sso-only
        additional_redirect_uris:
          - "https://plak.rijks.app/-/oauth2/callback"
```

One line of its own per environment. After the first rollout, check that both
URIs are on the client; without the second one every viewer login breaks on
`invalid_redirect_uri`.

## 6a. Keycloak client: logout settings

Plak offers the endpoint from OIDC Back-Channel Logout 1.0 on the admin host:

```
POST https://beheer.plak.<domein>/-/oidc/backchannel-logout
```

The endpoint expects a `logout_token` as a form field. A cheap, unverified
pre-check runs first (token size, JWT shape, `typ`/`alg`, `iss`, a recent
`iat`, `jti`, no `nonce`, the `events` claim, and a jti already in the replay
cache), then the full check: the signature (through the JWKS of the issuer),
`iss`, `aud`, a recent `iat`, `exp`, the claim `events` with
`http://schemas.openid.net/event/backchannel-logout`, the presence of `sid` or
`sub`, the absence of a `nonce`, and a `jti` that did not come by shortly
before. After that the corresponding Plak sessions (admin and content) lapse
and a 200 follows with `Cache-Control: no-store`. See `docs/security.md`
("Session lifetime and logout") for the full session picture and the
pre-check.

Self-service has no field for this, so this is set per client in the Keycloak
admin console (Clients -> `<client>` -> Settings), where the project has
access; otherwise ask the platform team.

Per environment, on the client in the ZAD Keycloak (Clients -> `<client>` ->
Settings):

| Field | Value | Why |
|---|---|---|
| Front channel logout | Off | While it is on, Keycloak skips back-channel logout for the client entirely, whatever the URL says |
| Backchannel logout URL | `https://beheer.plak.<domein>/-/oidc/backchannel-logout` | Empty means Keycloak never calls us and `POST /-/oidc/backchannel-logout` is dead code. Per environment its own admin host |
| Backchannel logout session required | On | Puts `sid` in the logout token, which is what matches the exact session; only the session it is about lapses. Off means the token carries only a `sub`, and every session of that person lapses |
| Backchannel logout revoke offline sessions | Off | Only adds a `revoke_offline_access` event to the token; Plak keeps no offline sessions and does not act on it |
| Admin URL | empty | With no back-channel URL, Keycloak would fall back to this one in its own pre-OIDC format, which Plak does not speak |

Without this configuration the rest works fine: the endpoint then only exists
and is never called, and the periodic revalidation stays the boundary (at
most `PLAK_IDP_RECHECK_SECONDS`, see `docs/security.md`).

Test it: log in, sign the session out from Users -> Sessions in the Keycloak
console, and refresh. You should land on the login page at once rather than
after the session's 12-hour cap; the attempt shows up as an `idp_session_ended`
audit event with reason `IDP_BACKCHANNEL_LOGOUT` (`audit/vocabulary.py`).

## 7. Running migrations

**ZAD has no notion of a Job.** The project schema has no place for a one-off
task, and `zadctl` has no `job` command. There are three real routes:

1. **Ad-hoc job in the portal** (the designated route for a one-off
   migration). Under Acties of a deployment there is "job run": image plus
   command, executed as one pod with the database variables of that
   deployment. Run `alembic upgrade head` there with the same image as the
   app. Only in the web UI: not in `zadctl`, not in the v2 API, so not from
   CI.
2. **Migrating at container start.** Have the start command do
   `alembic upgrade head` first and then uvicorn. That can be done with
   `command:` on the component, or by putting it in the image. Safe because
   ZAD fixes the number of replicas hard at 1 and switches automatically to
   `Recreate` with a persistent volume: two migrations never run at the same
   time. Price: a failed migration is a pod that does not start, and the
   rollout only notices that through the probe.
3. **Bootstrap actions**: HTTP calls that the platform fires at the app after
   the rollout. That is suitable for initialisation through an endpoint, not
   for a schema migration, and the field is not in the schema version that
   `deploy/plak-project.example.yaml` is validated against.

Route 1 is the most honest for the first rollout (you see the output), route 2
is the route as soon as there are regular migrations. Pick one and write it
down.

Plak runs on one database account. The shared PostgreSQL service delivers one
user plus an `_ro` variant, and no `CREATE ROLE`. The app, alembic and the
purge job therefore all three use `PLAK_DB_URL`. Local runs the same way, so
that dev and production give the same picture.

The retention period of the audit log stays in place, because the triggers
from `0001_base` enforce it (`docs/audit-log.md`). What is missing is the layer
underneath: that one account is also the owner of the table and can turn off
the triggers, rewrite the functions or `TRUNCATE` the table. Schedule the
purging as a scheduled task that runs `python -m plak.audit.retention`, daily
for instance.

Whoever wants the separation back needs the separate service
`namespace-postgresql-database`: that one does offer `CREATEROLE` and
`postInitSQL`, the place to put the roles and their rights. That is heavier
and worth a conversation with the platform team; the audit log is the
strongest argument for having that conversation.

## 8. Linking CI

`.github/workflows/ci.yml` runs lint, backend tests, typecheck, build,
frontend tests and the CSP check on the built SPA.
`.github/workflows/deploy.yml` builds the image, pushes it to GHCR and deploys
with `RijksICTGilde/zad-actions`. Both are pinned to commit SHAs.

Production follows release tags only. A push to `beta` builds, scans and
attests its image and deploys nowhere. A tag
`vYYYY.M.D` (or `vYYYY.M.D.N` for a second release that day) rolls out to
`productie` as the image tagged `YYYY.M.D`, its only tag, with that version
baked in as `PLAK_VERSION`. The release workflow sets the tag
(`docs/releasing.md`); the tag rulesets let nobody else.

Before rolling out, the `production` job refuses the tag, and goes red, when it
does not match `vYYYY.M.D[.N]` (no leading zeros), when the tagged commit is not
on `beta` or has no section in `CHANGELOG.md`, or when it is not the newest
release tag, so pushing an old tag again never rolls production back.

There are no preview deployments. A pull request builds, scans and attests
its image and stops there; what runs on ZAD is production, from a release tag.
Reviewing happens on the dev stack (`docs/local-development.md`), which serves
both origins on `localhost:8080`.

Previews were tried and taken out again on 3 October 2026. Not because another
deployment cannot have two origins -- it can, exactly as `productie` does --
but because each one needs a subdomain of its own, and every new subdomain
costs an approval:

- Two hostnames need a dotted domain (`rijks.app`, `rijksapp.nl`,
  `rijksapp.dev`). The cluster's own domain needs no claim at all, but is
  `supports-dots: false`, and that flag is about the certificate: the cluster
  serves `DNS:*.rig.prd1.gn2.quattro.rijksapps.nl`, a wildcard that covers one
  label. A dotted format there is accepted, rolls out, reports `Healthy` and
  then serves a certificate for a name it does not carry:
  `beheer.proef.plak-jr3.rig.prd1...` gives `SSL: no alternative certificate
  subject name matches target host name`. On the hyphenated formats, which do
  fit the wildcard, `root-component` makes no second ingress: measured with
  `component-deployment-project` plus `root-component: beheer`, the API
  returns one URL.
- A dotted domain needs a subdomain claim, and a subdomain belongs to exactly
  one deployment. `productie` holds `plak | rijks.app`, and a second
  deployment asking for it fails the rollout with "Subdomein 'plak.rijks.app'
  is niet beschikbaar".
- A subdomain of its own per pull request does work, and was measured to
  compose the right addresses, but every claim arrives as `requested` and
  waits for a platform administrator. One approval for one long-lived
  deployment is nothing; one per pull request is not a thing to build on.

And a clone carries no deployment-level variables at all, only the
component-wide ones, so `PLAK_CONTENT_BASE_URL` and `PLAK_BASE_URL` were
missing and the pod refused to start:
`ConfigurationError: PLAK_CONTENT_BASE_URL: Field required`.

One thing to know when judging this yourself: a sleeping preview reports
`Healthy` and answers 200. That is `zad-waker`, the sleep-mode page the
platform puts in front of a deployment scaled to zero, not the application.

So what is blocked is the environment *per pull request*, not the environment.
A deployment that lives on, with one subdomain approved once, works exactly as
`productie` does -- whether that is called staging or a shared preview slot is
a question for whoever needs one. The approval is a single click, once.

### A second deployment

Nothing runs beside `productie`. Whoever adds a deployment next, staging or
otherwise, starts from three things the previews taught; the route they
used is in the history of `deploy.yml`, before `aecdb30`.

- **A clone does not carry over the settings of the source.** `components`
  is on the exclusion list of the clone, together with `name`, `subdomain`,
  `base-domain`, `domain-format`, `issuer` and `backup`; the new deployment
  gets only the `{reference, image}` pairs from the API call. Everything
  under `deployments[].components[]` falls away, and without
  `PLAK_CONTENT_ROOT`, `PLAK_SESSION_SECRET` and `PLAK_AUDIT_PEPPER` the pod
  does not start. What may be the same everywhere goes component-wide
  (`zadctl env add -c beheer`, without `--deployment`); `PLAK_BASE_URL` and
  `PLAK_CONTENT_BASE_URL` differ per deployment.
- **The three secrets are never shared.** A deployment that inherits
  `PLAK_SESSION_SECRET`, `PLAK_AUDIT_PEPPER` and `PLAK_AUDIT_IP_KEY` from
  production lets whatever code runs there mint session cookies production
  accepts and decrypt production's audited IP addresses. Write its own at
  the deployment layer (§5) before the first rollout:
  `:upsert-deployment?rollout=false` puts the deployment in the project file
  without a pod, and the values endpoint answers 404 until it is there.
- **The database is per deployment.** The platform composes
  `{project}_{deployment}` and provisions a user and password for each
  (`opi/utils/naming.py` in `RijksICTGilde/RIG-Cluster`), and `PLAK_DB_URL`
  is an alias over those per-deployment variables (§4). Still a question for
  the platform team: whether the shared database server keeps one
  deployment's account out of another's database at the PostgreSQL level.

## 9. Storage sizing

`persistent-storage` offers a fixed catalogue of sizes: 50Mi, 100Mi, 250Mi,
500Mi and 1Gi. **1Gi is the maximum per volume**, and a volume can grow but not
shrink (source: RIG-Cluster, `operations-manager/python/opi/services/catalog/persistent_storage/help.md`
and `persistent-storage.v1.0.json`). Plak already runs on 1Gi, so there is no
bigger volume to move to. The volume is ReadWriteOnce on `ocs-storagecluster-ceph-rbd`, the number of replicas is
fixed hard at 1, and as soon as a component has a persistent volume the
platform switches the rollout strategy to `Recreate` itself (so a short
interruption on every deploy).

Of those 1024 MiB, the reserve (`PLAK_STORAGE_MIN_FREE_BYTES`, 100 MiB) and one
deploy of the maximum size in flight (200 MiB unpacked) are never available to
stored versions, so roughly 700 MiB is left for all sites together, previews
included (about 600 MiB while the upload of such a deploy sits next to what it
unpacks).

What one site can hold is bounded twice. `PLAK_SITE_MAX_BYTES` (500 MiB by
default, 200 MiB on ZAD) caps all its versions together, and every night at 03:00 UTC the
cleanup job removes the live versions beyond the current one and the ones
before it that the site keeps, row and files, with a `version_cleanup` row in
the audit log. That number is `PLAK_LIVE_VERSIONS_KEPT` (5) for every site,
unless a site admin set one of its own on the site's Versions tab (`0` keeps
everything for that site). The current live version always stays,
also after a rollback to an older one. Previews are not counted in that
number; they expire after 30 days on their own. A site publishing 13 MB at a
time therefore settles at about six live versions (some 80 MB) plus its
previews, where without the cleanup it would reach the quota after some 38
deploys and then be refused until someone intervened. Size the volume for the
number of sites times that settled size, plus the floor and one full deploy
(§5), and keep an eye on sites with a larger number of their own. Setting
`PLAK_LIVE_VERSIONS_KEPT=0` keeps every live version of the sites without
their own number, and then the quota is their only bound.

The candidate sites are 1 to 13 MB now, but every kept version and every
preview counts towards the volume. Plak writes to that one volume and nowhere
else (`ContentStore` is a plain filesystem class). More room than 1Gi would need
storage other than a volume, which is not planned; the quota and the cleanup
above are what keep the volume from filling.

The number of replicas is not a setting of the project on ZAD: the schema has
no `replicas` field and the Operations Manager renders 1 (0 if the component is
turned off), whatever the storage is. So ReadWriteOnce is not the binding
restriction here, and a second pod is a question for the platform team.

So keep an eye on how full the volume is from day one: `/-/healthz`, the log and
the platform administrator's view on "Platformbeheer" (`/-/platform`) all report it (§5, "Content
volume").

## 10. What is still open

Questions for the platform team, with what we do for now as long as the answer
is not there:

1. **Answered, from the platform repo.** The `persistent-storage` service
   offers 50Mi, 100Mi, 250Mi, 500Mi and 1Gi; 1Gi is the maximum per volume and
   a volume can grow but not shrink (RIG-Cluster,
   `operations-manager/python/opi/services/catalog/persistent_storage/help.md`
   and `persistent-storage.v1.0.json`). Plak runs on 1Gi, so more room than that
   would need storage other than a volume, which is not planned. What stays
   open is whether there is a ReadWriteMany option and whether a component can
   run more than one replica: the project schema has no `replicas` field and
   the platform renders 1 hard (§9). For now: 1Gi, one pod, a site quota of
   200 MiB, and the signals of §5 ("Content volume") to see it fill up.
2. **Can the Keycloak client of the project be set to `client-jwt`
   (private_key_jwt with JWKS) and to exact-match redirect URIs, and can
   `directAccessGrants` and service accounts be turned off?** Self-service
   does not offer that. For now: `client_secret_post`, with wildcard redirect
   URIs, as a documented deviation from the OAuth NL profile.
3. **Can the realm pass on an `acr` value that belongs to SSO Rijk?** SSO Rijk
   is SAML and delivers no acr; the ZAD Keycloak advertises `0` and `1`. For
   now: no acr check, logged explicitly at startup.
4. **Answered, by measuring.** The HAProxy router runs `option forwardfor`
   under its Append policy, and adds an `X-Forwarded-For` line of its own
   behind whatever the client sent rather than extending it. A client-supplied
   header therefore arrives untouched, one line earlier. Measured on
   2026-09-30 against `plak.rijks.app`: a plain request was recorded under the
   real address, one carrying `X-Forwarded-For: 203.0.113.99` under that
   invented one, because the derivation read the header with `headers.get`,
   which returns the first line only. `PLAK_BEHIND_PROXY` replaced the
   CIDR list: which entry to read is known, the router's range is not and
   cannot be had, because the router pods sit on the cluster pod network.
5. **What does the router log at the edge, and can a tenant request that?**
   `zadctl logs` delivers container logs only. For now: the app itself logs no
   query strings (`--no-access-log`, because a secret link is in `?key=`), so
   there is no second source.
6. **Is there an acceptance environment of SSO Rijk or of the `rig-platform`
   realm?** None was found; the sandbox authenticates against the production
   realm. For now: the mock OIDC in the dev stack, plus the production realm
   for the real check.

## See also

- `deploy/plak-project.example.yaml`: the project file with comments.
- `docs/security.md`: the security checklist, including the headers the app
  sets itself because there is no nginx in front of it any more.
- `docs/local-development.md`: the dev stack with two hosts on `:8080`.
