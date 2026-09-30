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
- **deployment**: an environment (production, a PR preview) that fixes an image
  and a web address per component.

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
| `probe: scheme: tcp` | `/healthz` exists on neither public host and the probe arrives with the pod IP as Host; an HTTP probe would always fail |
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
over the component-wide value. That choice determines what a preview
inherits, because a clone does not copy the deployment block of the component
along with it (§8). For a preview these three are not set by hand at all:
`deploy.yml` writes them at the deployment layer through the same endpoint
`zadctl env add --deployment` uses (§8, "The preview's own secrets").

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
| `PLAK_TRUSTED_PROXIES` | env-vars | CIDRs of the router pods (see §10) |
| `PLAK_OIDC_ISS_REQUIRED` | env-vars | `true` |
| `PLAK_IDP_RECHECK_SECONDS` | env-vars | optional, default `900`; how often at most a session is checked again at Keycloak, `0` turns it off |
| `PLAK_OIDC_RP_LOGOUT` | env-vars | `false` until `{PLAK_CONTENT_BASE_URL}/-/logout?from=beheer` is registered as post-logout redirect at the IdP |
| `PLAK_AUDIT_LOOKUP_DAILY_LIMIT` | env-vars | optional, default 25, 1-1000; daily limit per platform administrator on resolving a pseudonym or IP address |
| `PLAK_CI_FORGEJO_HOSTS` | env-vars | optional, comma-separated list of https base URLs, default `https://code.overheid.nl`; every Forgejo instance whose OIDC ID token from Actions is accepted for CI publishing |
| `PLAK_INGEST_MAX_BODY` | env-vars | optional, bytes, default `104857600` (100 MiB); the largest upload |
| `PLAK_INGEST_MAX_FILE` | env-vars | optional, bytes, default `52428800` (50 MiB); the largest single unpacked file |
| `PLAK_INGEST_MAX_TOTAL` | env-vars | optional, bytes, default `209715200` (200 MiB); the largest unpacked site per deploy |
| `PLAK_SITE_MAX_BYTES` | env-vars | optional, bytes, default `524288000` (500 MiB), `0` turns it off; what all versions of one site together may occupy |
| `PLAK_STORAGE_MIN_FREE_BYTES` | env-vars | optional, bytes, default `104857600` (100 MiB), `0` turns it off; free space a deploy never takes the volume below |

The last five defaults fit the 1Gi content volume (§9). While a deploy runs,
its upload and what it unpacks sit on the volume side by side, so one deploy
needs at most `PLAK_INGEST_MAX_BODY` plus `PLAK_INGEST_MAX_TOTAL` (300 MiB)
on top of the stored versions. Plak measures the actual deploy, not that
maximum: it checks the declared upload size before reading the body and keeps
checking while it writes, refusing with `503` `STORAGE_UNAVAILABLE` rather than
letting the volume drop below `PLAK_STORAGE_MIN_FREE_BYTES`. Keep the floor plus
one full-size deploy (400 MiB with the defaults) well below the volume size, or
a large deploy can only ever be refused; on a bigger volume the limits can grow
with it.

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

### Re-validation at the IdP

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
minute, and for as long as it lasts `/healthz` answers with status 200 and

```json
{"status": "degraded", "idp_revalidation": "IdP re-validation is failing: invalid_client"}
```

That is the place to alarm on: `/healthz` is internal (the probe on ZAD is
`tcp`, so it does not notice), but can be queried inside the cluster. The
first successful re-validation clears the complaint by itself.

The precondition is that the client issues a refresh token; with the default
`sso-only` template of the ZAD Keycloak it does. If the client issues none,
the session stays and the app logs a warning per session: the re-validation is
then no extra risk, but also no extra certainty.

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
and is never called, and the periodic re-validation stays the boundary (at
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

A PR gets a preview deployment `pr<nummer>` that is cloned from `productie`,
and when the PR is closed the cleanup action removes the deployment, the
GitHub deployments and the image. Both jobs only run once the repository
variable `ZAD_PROJECT_ID` is set; until then a PR builds and scans its image
and stops there.

Watch out with previews: **a clone does not carry over the settings of the
source.** Besides the web address (the preview lands on the cluster address
with one host, not two), `components` is on the exclusion list of the clone as
well, together with `name`, `subdomain`, `base-domain`, `domain-format`,
`issuer` and `backup`. The new deployment gets only the
`{reference, image}` pairs from the API call. So everything Plak has under
`deployments[].components[]` falls away: the `env-vars` from the example file
and every user-env-var that was set with `--deployment`.

That is not a cosmetic difference. `PLAK_CONTENT_ROOT`,
`PLAK_SESSION_SECRET` and `PLAK_AUDIT_PEPPER` have no default value, so a
preview cloned that way does not start. What does come along sits in the root
of the project or on the deployment itself: the component definition (port,
resources, probe, storage), the `aliases` for `PLAK_DB_URL`, the services
with their `OIDC_*` variables, and the component-wide user-env-vars.

So set component-wide what may be the same in every environment
(`zadctl env add -c beheer`, without `--deployment`), so that a preview
inherits it. What differs per environment, `PLAK_BASE_URL` and
`PLAK_CONTENT_BASE_URL`, stays manual work per preview. See §10, question 7.

### The preview's own secrets

Component-wide is the wrong place for `PLAK_SESSION_SECRET`,
`PLAK_AUDIT_PEPPER` and `PLAK_AUDIT_IP_KEY`. A preview that inherits those
shares the signing secret, the audit pseudonymisation pepper and the audit
IP-address key with production, and then anyone with write access to the
repository can open a pull request, get it deployed as a preview (gated on
CI passing, not on review), and from that code mint session cookies
production accepts and decrypt production's audited IP addresses.

`deploy.yml` therefore gives every preview its own three, in the step
`Give the preview its own secrets`, before anything reaches the cluster:

1. `POST /api/v2/projects/{project}/:upsert-deployment?rollout=false` with
   `cloneFrom: productie` and the component plus its image. `rollout=false`
   writes the deployment to the project file and puts nothing on the
   cluster: no manifests, no provisioning, no pod.
2. `DELETE` and then `POST` on
   `/api/v2/projects/{project}/services/user-env-vars/values/deployment/{deployment}/component/beheer?rollout=false`,
   with three freshly generated values. That is the deployment-component
   layer from §5, which wins over the component-wide value.
3. The `zad-actions/deploy` step, unchanged, which updates the same
   deployment with `rollout` at its default and is therefore the call that
   rolls out. It processes only this deployment, never `productie`.

The order is not a preference. The values endpoint answers 404 until the
component is attached to the deployment, so the deployment has to exist in
the project file first; and both writes are asynchronous, so each is polled
through `/api/tasks/{task_id}` before the next step assumes its result.
Because nothing is rolled out until step 3, a preview pod never runs with
production's secrets, not even for the seconds a
set-afterwards-and-restart would cost.

Both endpoints take the project API key in `X-API-Key`, the same
`ZAD_API_KEY` secret the deploy action already uses, so no second
credential is needed. The values are generated with
`openssl rand -base64 32`, masked with `::add-mask::`, never echoed and
never passed as a command-line argument. They are written once per preview:
a later push to the same pull request finds all three names already set and
leaves them alone.

This is the route the action itself does not offer. Checked against
`RijksICTGilde/zad-actions` `deploy/action.yml` at both the pinned `v2`
(commit `5ad04045d781ed153ad75625305bf14d57496128`) and `v4.2.0`: neither
has an input for env-vars, user-env-vars or secrets, and `deploy`,
`cleanup` and `scheduled-cleanup` are the only three actions in that
repository. What the action cannot do, the API can, and the workflow calls
it directly.

Verified against the Operations Manager OpenAPI document (vendored in
`RijksICTGilde/zad-cli` as `api/upstream-openapi.json`) and the handlers in
`RijksICTGilde/RIG-Cluster`: `rollout=false` is honoured for
`upsert_deployment` and `configure_service_values` (both are in
`DEFERRABLE_TASK_TYPES` in `opi/core/task_rollout.py`), the 404 comes from
`_enqueue_values_write` in `opi/api/v2/router.py`, and the rollout in
`handle_upsert_deployment` is scoped with `deployment_name=`. Not verified
against a running ZAD: Plak has no project on the platform yet, so the first
real preview is also the first test of this step.

`PLAK_CONTENT_BASE_URL` is a different matter and still unsolved; see §10
question 7.

The database is not shared. `generate_database_name` and
`generate_database_username` in `opi/utils/naming.py` compose
`{project}_{deployment}`, and `DatabaseManager` provisions a database, a
user and a password per deployment, so `pr123` gets `plak_pr123` with its
own credentials, not production's. `PLAK_DB_URL` is an alias over the
per-deployment `DATABASE_*` variables (§4), so it resolves to that database.
The one-account limitation of §7 is about role separation inside one
deployment's database, not about previews reaching production's.

## 9. Storage sizing, and MinIO as the next step

`persistent-storage` takes a `size` declared in the project file, free-form
(the ZAD project schema sets no maximum we could find). What the platform
actually allows per volume, and whether self-service offers a fixed catalogue
of sizes, is a question for the platform team (§10, question 1). The volume is
ReadWriteOnce on `ocs-storagecluster-ceph-rbd`, the number of replicas is
fixed hard at 1, and as soon as a component has a persistent volume the
platform switches the rollout strategy to `Recreate` itself (so a short
interruption on every deploy).

For Plak 1Gi is a start, not a terminus: the candidate sites are 1 to 13 MB
now, but every kept version and every preview counts towards it. As soon as it
pinches, **MinIO is the route**, not a bigger volume: `minio-storage` gives a
bucket per deployment, with versioning and backup, and with the
`ContentStore` abstraction the app already has a place for it.

MinIO is the way to more storage, but not to a second replica. The number of
replicas is not a setting of the project on ZAD: the schema has no `replicas`
field and the Operations Manager renders 1 (0 if the component is turned off),
whatever the storage is. So ReadWriteOnce is not the binding restriction here,
and a second pod is a question for the platform team.

So keep an eye on how full the volume is from day one, and treat the move to
MinIO as planned work instead of as an emergency measure.

## 10. What is still open

Questions for the platform team, with what we do for now as long as the answer
is not there:

1. **What is the actual maximum persistent volume size, does self-service
   offer only a fixed catalogue of sizes, is there a ReadWriteMany option, and
   can a component run more than one replica?** We could not confirm a
   maximum or a size catalogue from the ZAD platform repo; the project schema
   places no upper bound on `size`. The replica question is separate, because
   the project schema has no `replicas` field and the platform renders 1 hard
   (§9). For now: 1Gi as our own starting size, one pod, and MinIO as the
   route to more storage. The sizes offered by the ZAD self-service wizard
   could not be verified from the platform repo either; treat any number
   quoted for it as unconfirmed until the platform team answers.
2. **Can the Keycloak client of the project be set to `client-jwt`
   (private_key_jwt with JWKS) and to exact-match redirect URIs, and can
   `directAccessGrants` and service accounts be turned off?** Self-service
   does not offer that. For now: `client_secret_post`, with wildcard redirect
   URIs, as a documented deviation from the OAuth NL profile.
3. **Can the realm pass on an `acr` value that belongs to SSO Rijk?** SSO Rijk
   is SAML and delivers no acr; the ZAD Keycloak advertises `0` and `1`. For
   now: no acr check, logged explicitly at startup.
4. **What is the CIDR of the router pods, and does the HAProxy router set
   X-Forwarded-For in append or replace mode?** Plak takes the last untrusted
   hop from the right. For now: the RFC1918 ranges in
   `PLAK_TRUSTED_PROXIES`, which works as long as visitors have a public IP,
   but is too coarse to lean on.
5. **What does the router log at the edge, and can a tenant request that?**
   `zadctl logs` delivers container logs only. For now: the app itself logs no
   query strings (`--no-access-log`, because a secret link is in `?key=`), so
   there is no second source.
6. **Does a preview get two hostnames?** The deploy action on `v2` has no
   `domain-format`, `subdomain` or `base-domain`; from `v4.0.6` it does.
   Without those inputs a preview runs on one host and the origin separation
   is not right there. Two possible outcomes: move the pin to `v4` and claim
   two hosts plus two Let's Encrypt certificates per PR (then also ask about
   the certificate budget per platform domain), or deliberately accept
   previews on one host. For now: not solved, previews stay on the cluster
   address.
7. **How does a preview get its settings?** A clone does not copy the
   `components` block of the source, so a preview starts without env-vars
   and without deployment user-env-vars (§8); without `PLAK_CONTENT_ROOT`,
   `PLAK_SESSION_SECRET` and `PLAK_AUDIT_PEPPER` the pod does not even
   start. What is the same in every environment can go component-wide, and
   a component-wide user-env-var with `${PUBLIC_HOST}` is filled in by the
   platform per deployment with its own web address (including scheme).
   The three secrets are no longer part of this question: `deploy.yml`
   writes them per preview through the values API before the rollout (§8,
   "The preview's own secrets"). What remains is
   `PLAK_CONTENT_BASE_URL`, which on one host coincides with
   `PLAK_BASE_URL` (question 6). It could be written the same way, but
   deciding what it should say depends on the answer to question 6, so it
   is left alone until that is settled. For now: unresolved for the content
   origin, solved for the secrets.
8. **Is there an acceptance environment of SSO Rijk or of the `rig-platform`
   realm?** None was found; the sandbox authenticates against the production
   realm. For now: the mock OIDC in the dev stack, plus the production realm
   for the real check.
9. **Does a preview share production's PostgreSQL database?** No, as far as
   the platform source says. `generate_database_name` and
   `generate_database_username` in `opi/utils/naming.py` of
   `RijksICTGilde/RIG-Cluster` compose `{project}_{deployment}`, and
   `DatabaseManager` creates the database, the user and the password per
   deployment, so `pr123` lands on `plak_pr123` with credentials of its
   own. Because `PLAK_DB_URL` is an alias over the per-deployment
   `DATABASE_*` variables, that is what a preview receives. What we could
   not check from here is whether the shared database server keeps one
   project's deployments out of each other's databases at the PostgreSQL
   level, or whether a preview account can simply connect to
   `plak_productie`. That is the remaining question for the platform team.
   Also unanswered: how many databases a project may have before this
   pinches, given one per open pull request.

## See also

- `deploy/plak-project.example.yaml`: the project file with comments.
- `docs/security.md`: the security checklist, including the headers the app
  sets itself because there is no nginx in front of it any more.
- `docs/local-development.md`: the dev stack with two hosts on `:8080`.
