# Privacy (AVG)

**Status: draft starting point, not a legal determination.** This document
gives the privacy officer (FG, "Functionaris Gegevensbescherming") and the
controller ("verwerkingsverantwoordelijke") a factual starting point for a
full DPIA (Model DPIA Rijksdienst), not a substitute for one. Nothing here
constitutes legal advice or a completed assessment; `docs/security.md` lists
the DPIA as "planned, prerequisite for production go-live" and this document
is the input to that work, not its conclusion.

Facts below are derived from the code and configuration as they stand today
and cite their source. Anything not verified against the code is marked as
such rather than assumed.

## Data subjects

- **Members**: people with a `members` row, created on first visit to the
  admin host (`backend/src/plak/models/identity.py`, `Member`). Logging in
  alone does not create one.
- **Content viewers**: people who log in with SSO Rijk on the content host
  without being a member, for example to view an SSO-gated or invitee-gated
  site (`backend/src/plak/models/audit.py`, `ContentViewer`).
- **Invitees**: people named on a site's invitee list by sub or email
  address, whether or not they ever log in (`backend/src/plak/models/publication.py`,
  `Invitee`).
- **Anonymous visitors**: people who view public or secret-link content
  without logging in; the audit log still records their (truncated/encrypted)
  IP address.
- **CLI users**: members who authorize a `plak login` device (CLI publishing
  tool).
- **People named in published content**: anyone whose personal data appears
  inside a published site, without ever interacting with Plak's own
  authentication.

## Personal data processed, per store

| Store | Personal data | Purpose | Source | Retention |
|---|---|---|---|---|
| `members` (`models/identity.py`) | `sso_subject`, `email`, `name`, `platform_role`, `status`, `language`, `created_at`, `last_login_at` | administering group/site membership and roles | SSO Rijk OIDC claims, on first admin-host visit | not time-bounded; tied to account lifecycle (deactivation), see "Retention and deletion" |
| `group_members` / `site_members` (`models/identity.py`) | `member_id`, `role`, `added_by`, `added_at` | who has what role where | set by an admin | tied to membership |
| `content_viewers` (`models/audit.py`) | `sso_subject`, `email` (nullable, may be unverified), `email_verified`, `last_seen_at` | makes a content-host SSO login traceable to a person for the audit log; a content-only viewer gets no `members` row | SSO Rijk OIDC claims, on every successful content-host login | 90 days from `last_seen_at`, purged by `audit/retention.py` (`docs/audit-log.md`, "Retention") |
| `invitees` (`models/publication.py`) | `identifier` (email address or `sub`, lowercased), `added_by`, `added_at` | per-site invitee access list | admin input | until removed by an admin; no automatic expiry |
| `access_keys` (`models/publication.py`) | `label`, `selector`, `verifier_hash` (SHA-256 of a random secret) | secret-link credential, not itself tied to a person | generated on creation | mandatory `expires_at`, 90 days default, 365 days maximum (BIO2 5.18.02, `docs/security.md`) |
| `audit_log_entries` (`models/audit.py`) | `actor_pseudonym` (HMAC-SHA256 of the actor identifier under `PLAK_AUDIT_PEPPER`), `ip_truncated` (/24 IPv4, /48 IPv6), `ip_encrypted` (AES-256-GCM, reversible, under the separate `PLAK_AUDIT_IP_KEY`), `action`, `result`, `reason_code`, `refs` | security and access audit trail | every login, access decision, admin action and CLI event | 90 days for pure viewing/login-success actions, 3 years for everything else, including every refusal, admin operation and publication (`docs/audit-log.md`, "Retention"; enforced in the database by `audit_log_retention()`, `backend/alembic/versions/0001_base.py`) |
| Admin/content sessions (`auth/sessions.py`) | `sub`, `email`, refresh token (kept in process memory only, `repr=False`, never logged) | keeps someone logged in | OIDC login | in-memory only, not persisted to the database; expires after 12 hours (`MAX_SESSION_AGE`) or on logout; lost entirely on a restart (`docs/security.md`, "Sessions do not survive a restart") |
| `cli_device_authorizations` (`models/cli.py`) | `device_hash`, `user_code_hash`, `client_name`, `ip_truncated`, `member_id` | `plak login` device pairing | CLI device flow (RFC 8628) | short-lived: expires after 10 minutes, deleted once exchanged, swept by the cleanup job |
| `cli_sessions` / `cli_refresh_tokens` (`models/cli.py`) | `member_id`, `client_name`, token selector/hash pairs | CLI publishing sessions | approved by a member in an admin session | access token 1 hour; session up to 30 days after last refresh, 90 days absolute (`docs/security.md`, "CI id token"/"CLI device flow") |
| Published content (files on the content volume) | whatever the publisher includes: potentially names, contact details or special categories of data (AVG art. 9) | the published site itself | uploaded by a publisher (member or CI) | until the site or version is removed; Plak does not parse, classify or scan content for personal data |

`refs` on audit rows generally carries structural identifiers (group/site
slugs, ids), not raw personal data; this was spot-checked for `invitee_add`/
`invitee_remove` (`backend/src/plak/api/admin.py`) but was not audited
exhaustively across every action, so this is a strong indication, not a
guarantee.

## Proposed legal basis (AVG art. 6)

A proposal, to be confirmed by the controller:

- **Primary: art. 6(1)(e)**, a task carried out in the public interest / in
  the exercise of official authority.
- **Additional: art. 6(1)(c)**, a legal obligation for security and audit
  logging (duty of care, BIO, art. 32).
- **Not** consent (6(1)(a): an authority relationship makes consent
  problematic as a basis) and **not** legitimate interest (6(1)(f): generally
  unavailable to a government body acting in its public task).

The controller ("verwerkingsverantwoordelijke") and, where ZAD or another
hosting party is a processor, the processor relationship (art. 28) still need
to be recorded; see "Processors" below.

## Published content: publisher responsibility and EEA/GHCR boundary

Published content can contain personal data, including special categories
under art. 9, and Plak does not inspect, classify or filter it. The publisher
who uploads or deploys a site is responsible for what it contains; Plak's
role is limited to storing and serving what it is given, subject to the
access controls the publisher configures.

Published content should stay on infrastructure within the EEA: never in
GitHub.com repositories, and never baked into container images pushed to
GHCR. Verified against Plak's own build and deploy design:

- The container image (`containers/plak/Containerfile`) copies in only the
  backend source and the built admin SPA (`frontend/dist`); it contains no
  application content. Published content is never part of the image, so it
  never reaches GHCR.
- On ZAD, published content lives on the `content` persistent volume
  (`PLAK_CONTENT_ROOT`, `docs/deploying-on-zad.md` §4, "the content volume"),
  separate from the image and from the database.
- Source code (this repository) is public on GitHub.com; that is
  intentional (open source, EUPL-1.2) and does not carry published content
  or personal data from any deployment.

**Unverified**: whether the ZAD platform itself, or the specific ZAD
tenant/region Plak runs in, guarantees storage within the EEA is a platform
question, not something this repository's code can establish. That
confirmation belongs with whoever operates the ZAD deployment.

## Retention and deletion

What Plak does today:

- Audit log rows are deleted by `audit/retention.py` once their retention
  term (90 days or 3 years, see the table above) has passed, enforced not
  only by that job but by a database trigger
  (`audit_log_delete_after_retention`, `backend/alembic/versions/0001_base.py`)
  that refuses to delete a row before its term is up, even from the
  application's own database account.
- `content_viewers` rows are purged the same way at a fixed 90 days from
  `last_seen_at`.
- Secret links (`access_keys`) always carry a mandatory expiry date (90 days
  default, 365 maximum); nothing revives one after `expires_at`.
- CLI device authorizations and short-lived tokens expire and are swept
  automatically.
- Sessions live in process memory only and do not survive a restart.

**Open gap: database backups.** Backups carry personal data (members,
invitees, content viewers, audit rows including encrypted IPs) and need an
explicit, bounded backup retention term, encryption at rest, and a deletion
process that actually covers backups, not only the live tables. This gap is
**not resolved**: Plak
runs on ZAD's shared PostgreSQL service, and this repository's documentation
(`docs/deploying-on-zad.md`) does not describe a backup retention or deletion
policy for that service. Whether backups are made, for how long, and whether
a deletion request against the live tables is honoured in the backups too is
unverified and belongs with the platform team.

This is also where the tension between the AVG's deletion obligation and the
Archiefwet's retention/preservation duty applies: a purged audit row or a
removed member is not necessarily allowed to disappear from an archival
perspective, and vice versa. The outcome of that trade-off should be
documented explicitly rather than defaulting either way; that has not been
done yet.

## Rights of data subjects

Access (art. 15), rectification (art. 16) and erasure (art. 17, limited by
the legal basis and the Archiefwet trade-off above) are not implemented as
self-service features today. An operator fulfilling a request would, as far
as the code supports it:

- **Access/rectification for a member**: a member can see and edit their own
  profile (`/-/profile`, `language` and, presumably, other self-service
  fields; not independently verified beyond `language`). An operator can
  look up and correct `members`/`invitees` rows directly.
- **Erasure of a member**: deactivating a member (`MemberStatus.DEACTIVATED`)
  revokes access and CLI sessions immediately (`docs/security.md`); this is
  not the same as deleting the row, and whether/how a `members` row is
  actually deleted rather than only deactivated is unverified.
- **Access to the audit trail about a person**: audit rows store a
  pseudonym, not a raw identifier, so answering "what is logged about me"
  requires the reverse lookup an operator performs through
  `POST /platform/audit/actor-identity` and the IP reveal endpoint
  (`audit_ip_reveal`), both themselves audited acts subject to a daily rate
  limit (`backend/src/plak/api/admin.py`, `docs/audit-log.md`). There is no
  self-service report a data subject can request through the product itself.
- **Erasure from published content**: entirely the publisher's
  responsibility; Plak has no mechanism to locate or redact personal data
  inside a published site.

A documented, FG-approved process for handling an access/rectification/
erasure request (who receives it, identity verification, response term) is
an open item.

## Processors and registration (art. 28, art. 13)

Open items, to be completed by the controller before go-live:

- Whether ZAD (and any managed service it depends on, such as its shared
  PostgreSQL) qualifies as a processor, and if so, a processor agreement
  (verwerkersovereenkomst, art. 28).
- Registration of this processing in the controller's processing register
  ("verwerkingsregister").
- A privacy statement (privacyverklaring, art. 13) reachable from the login
  and access screens, covering the controller, FG contact, purposes and
  legal basis, categories of personal data, recipients/processors, retention
  terms, data subject rights and the right to complain to the Autoriteit
  Persoonsgegevens. Plak does not currently serve one; `/admin/-/privacy` is
  listed as a platform page in `README.md` but its content was not verified
  as part of this document.

## Risks

Following the risk section (Deel C) of the Model DPIA Rijksdienst, applied
to Plak's actual design.

| # | Risk | Mitigation in Plak today | Open |
|---|---|---|---|
| R.1 | Published content (script or otherwise) reads session cookies, keys or other sites' data | per-site content session cookie path scoping, origin-per-site enforcement, subresource checks, sandbox CSP mode (`docs/security.md`, "Access to content", "Headers and CSP") | shared content hostname across sites remains a stopgap per `docs/security.md` (§5.10); sandbox/external-sources are per-site toggles an admin can turn off |
| R.2 | Access level set too broadly, spreading sensitive content further than intended | access is deny-by-default per site (`NOBODY` unless a base or extra is chosen), admin-only to change (`access/gate.py`, `constants.py`) | no automatic warning or review workflow when a site is set to `public` |
| R.3 | Leaked secret link | constant-time comparison, mandatory expiry (90/365 days), single code-page flow, key excluded from audit/access logs (`docs/security.md`, "Access to content") | none identified |
| R.4 | Unauthorized access via a broken OIDC/session/authorization check | alg allowlist, `iss`/`at_hash` checks, CSRF, per-endpoint server-side authorization, neutral 404s (`docs/security.md`) | account separation on the shared database is not possible on ZAD today (`docs/security.md`, "DB account separation") |
| R.5 | Personal data kept longer than necessary, in logs or in backups | audit log and `content_viewers` retention enforced by database trigger, not only application code (`docs/audit-log.md`) | database backup retention/deletion is undocumented and unverified, see "Retention and deletion" above |
| R.6 | Re-identification of a person from a logged identifier or IP address | actor pseudonymisation (HMAC-SHA256, keyed pepper), IP truncation by default with reversible encryption gated behind an explicitly audited reveal action (`audit/pseudonymisation.py`, `audit/ip_crypto.py`) | the reveal key (`PLAK_AUDIT_IP_KEY`) and the pepper are operational secrets whose handling (rotation, storage) falls outside this repository |
| R.7 | Personal data in published content transferred outside the EEA | content never enters the container image or GHCR; content lives on the ZAD content volume (verified, see "Published content" above) | whether the ZAD platform/region itself guarantees EEA storage is unverified |
| R.8 | Audit log tampering hides unlawful access or a data subject's history | append-only triggers, hash chain per shard, published chain head for external verification (`docs/audit-log.md`) | the schema owner (the one database account Plak has on ZAD) can disable the triggers, the `TRUNCATE` guard among them, and then empty the table; this is a documented, not a closed, gap (`docs/audit-log.md`, "Known gaps") |

## Open actions

1. Commission the full DPIA (Model DPIA Rijksdienst), with FG consultation
   (art. 35(2)), before production go-live; this document is the input, not
   the assessment.
2. Confirm the legal basis (art. 6) and, if special categories of data are
   expected to appear structurally in published content, the art. 9
   exception that applies.
3. Establish and document database backup retention, encryption at rest, and
   a deletion process that covers backups.
4. Document the Archiefwet/AVG retention trade-off explicitly, for both the
   audit log and member/invitee data.
5. Determine whether ZAD (or a service it depends on) is a processor and, if
   so, put a processor agreement (art. 28) in place.
6. Register the processing in the controller's verwerkingsregister.
7. Publish a privacy statement (art. 13) reachable from the login and access
   screens.
8. Define and document a self-service or FG-mediated process for access,
   rectification and erasure requests, including how a member row is
   actually deleted (not only deactivated).
9. Confirm, with the ZAD platform team, that the region/tenant Plak runs in
   guarantees storage within the EEA.
10. Decide whether publishers need a content-classification step (personal
    data / special categories) before publishing.
