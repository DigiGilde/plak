# Audit log: vocabulary, retention and access

`audit_log_entries` is append-only: a row is never modified, and only
deleted by the purge once its retention period has passed. What is written
here is therefore a contract. Renaming an action, result or reason cannot be
undone afterwards; an environment with rows then carries two vocabularies.
A new term goes here first, only then into the code.

The code reads these names from one place,
`backend/src/plak/audit/vocabulary.py`;
`backend/tests/test_audit_vocabulary.py` keeps that file and this document
in step.

## Fields

| Field | Contents |
|---|---|
| `actor_kind` | `member`, `ci`, `system` or `anonymous` |
| `actor_pseudonym` | HMAC-SHA256 with `PLAK_AUDIT_PEPPER` of the SSO subject (member or content viewer), or for a CI actor of `provider:host:repository-id` (for a refused token without ids: `provider:host:eigenaar/repo` in lower case); never stored in readable form |
| `action` | see below |
| `result` | `allowed`, `refused` or `login_redirect` |
| `reason_code` | why, as a machine-readable code; empty on an ordinary allow |
| `refs` | what it is about: group, site, path, route template, and for a secret link the selector. Never an email address, the secret part of a link, a token or a query string |
| `ip_truncated` | IPv4 truncated to /24, IPv6 to /48 |
| `ip_encrypted` | the full IP address, AES-256-GCM-encrypted under `PLAK_AUDIT_IP_KEY` (a different key from `PLAK_AUDIT_PEPPER`); `null` if there was no IP. Lives as long as the row itself, and never travels in `GET /platform/audit`; only `POST /platform/audit/entries/{id}/ip` decrypts it |
| `occurred_at` | time, set by the database |

Alongside `audit_log_entries`, Plak keeps `content_viewers`: sso_subject,
email address (can be `null`, if the identity provider sends no claim),
whether that email address was a verified claim, and the time of the last
login on the content host, of everyone who viewed protected content there
via SSO. Without this table such a person would not be traceable to anything
in the audit log: a content-only visitor never gets a `members` row (that
comes into being only on the first admin visit, `auth/members.py`). Updated
on every successful content-host login (so `last_seen_at` is the last
*login*, not every individual page view); rows older than 90 days (by
`last_seen_at`) go along in the same purge as the audit log
(`audit_purge`), see "Retention" below. Only a verified email address counts
in the forward lookup (`actor-pseudonym` below); the SSO subject itself
always stays usable to find someone again, verified email address or not.

## Actions

### Viewing content

| Action | Result | When |
|---|---|---|
| `content_access` | `allowed` | an HTML page on non-public content (key, SSO, site team, invitees) has been served, and every `_version` view. Individual files (css, images, scripts) not: a page with fifty parts is one view, not fifty |
| `content_access` | `refused` | a request was refused; the visitor got the neutral 404 |
| `content_access` | `login_redirect` | an anonymous visitor was sent to log in |

Public content is never logged.

A secret link can also be shared without the code: `?key=` then carries only
the selector and the visitor gets a page that asks for the code. That page
is itself a `refused` with reason `KEY_CODE_REQUIRED`; a wrong code is
`KEY_CODE_INVALID` and an attempt over the limit per selector
`KEY_CODE_THROTTLED`. All three carry `refs.selector`, and `refs.kind` is
`code` when a code is submitted. The code itself appears nowhere in the log,
not even derived. A correct code writes no row of its own: the visitor gets
the same cookie as with a full link, and the request after that is the row
that says content was viewed.

Whoever views through a secret link is not logged in and stays `anonymous`
without a pseudonym. Which link it was is in `refs.selector`: the public
part of the link, the same one that appears with `key_create` and
`key_revoke` and that admin sees in the list of links. The secret part never
appears in the log. Whoever gave a link to one person can trace who viewed;
whoever shares a link with several people sees only that it was that link.

### Logging in and out

| Action | Result | When |
|---|---|---|
| `login` | `allowed` | a session was created. `refs.kind` is `admin` or `content` |
| `login` | `refused` | a callback was rejected; `reason_code` says why. The actor is then always `anonymous`, because there is no verified subject: a pseudonym from a token that failed validation would let an attacker plant pseudonyms of their choosing |
| `logout` | `allowed` | a session was ended. `refs.kind` is `admin` or `content` |
| `idp_session_ended` | `allowed` | a session was not ended by the person themselves but by the IdP: `reason_code` `IDP_SESSION_ENDED` (the periodic re-validation got `invalid_grant` back), `IDP_SUB_MISMATCH` (the id token from that re-validation was about someone else), `IDP_TOKEN_INVALID` (that id token failed validation) or `IDP_BACKCHANNEL_LOGOUT` (the IdP called the back-channel logout endpoint). `refs.kind` is `admin` or `content`. Never token material, never the `sid` |

### Admin

An allowed admin operation has an action of its own; a refused one always
arrives as `admin_access`, with the route template in `refs.route` and the
method in `refs.method`. A refused attempt deliberately does not record
*who* the target was: the route template says what was attempted, the actor
who attempted it, and more personal data adds little for investigation.

| Action | Result |
|---|---|
| `group_create`, `group_delete`, `group_default_visibility` | `allowed` |
| `group_member_add`, `group_member_remove`, `group_member_role` | `allowed` |
| `site_create`, `site_delete`, `site_visibility`, `version_set_live` | `allowed` |
| `site_external_sources` | `allowed` |
| `site_member_add`, `site_member_remove`, `site_member_role` | `allowed` |
| `preview_visibility` | `allowed` |
| `invitee_add`, `invitee_remove` | `allowed` |
| `key_create`, `key_revoke` | `allowed` |
| `site_repository_set`, `site_repository_remove` | `allowed` |
| `member_activate`, `member_deactivate`, `member_platform_role` | `allowed` |
| `admin_access` | `refused` |

`site_repository_set` and `site_repository_remove` are about the repository
linked to a site so that it may publish from there with a CI ID token
(`api/admin.py`, "Linked repository"); `refs` carries group, site, provider,
host, `repository` (`eigenaar/repo`), `repository_id` and `live_branch`, and
on unlinking only group and site.

`site_external_sources` is about the site toggle "Externe bronnen
toestaan": `refs.external_sources` is the new state (`true` or `false`),
alongside group and site. The toggle is on by default; a row with `false`
therefore means someone deliberately turned it off.

`group_default_visibility`, `site_visibility` and `preview_visibility` carry
no single level, but the three fields access is made of: `refs.base`
(`public`, `sso`, `site_team` or `nobody`), `refs.keys` and `refs.invitees`
(both `true` or `false`). It stays
one action per operation: base and exceptions are set in one PUT and
therefore belong in one row, otherwise the audit log would show a half state
that never existed. With `preview_visibility` all three are `null` if the
exception has been removed and the preview follows the site again. The
action names themselves stay: `audit_log_entries` is append-only, and a
rename would leave an environment with two vocabularies behind.

`member_deactivate` carries, besides `refs.member_id`, also
`refs.cli_sessions_revoked`: the number of CLI sessions (`plak login`) of
that member revoked on deactivation. Reactivating does not bring them back.

`admin_access` records authorization refusals only: every 403, and a 401 or
404 on a modifying request (that 404 is a disguised 403 there). On a read
request a 401 is "not logged in", because the interface requests `/me` on
every load to choose between landing page and overview, and a 404 is an
ordinary miss. 409, 413 and 422 are collisions, not refusals.

### Publishing

| Action | Result |
|---|---|
| `deploy` | `allowed`, `refused` |
| `preview_teardown` | `allowed`, `refused` |

The actor is `ci` for a CI ID token, `member` for a CLI token from `plak
login` or for an ordinary admin session. For a CI actor `refs` carries the
claims from the token (`ci/trust.py`, `AUDIT_CLAIMS`, each truncated at 200
characters): `provider`, and where present `repository`, `ref`, `sha`,
`run_id`, `workflow`, `event_name`. On a successful deploy or preview
teardown the actor is pseudonymised on the *stored* repository id
(`ci/trust.py:TrustedRepository.actor_identifier`); on a refused token on
what the token itself claimed (`repository_id`, or otherwise `repository` in
lower case, `ci/trust.py:refused_actor_identifier`) - this way an attacker
cannot plant a pseudonym of a genuinely linked repository. For a CLI token
`refs.via` carries the value `cli` and `refs.cli_session` the id of the CLI
session (never token material), and the actor is the same member and
pseudonym as an ordinary login.

### CLI pairing (`plak login`)

| Action | Result | When |
|---|---|---|
| `cli_login` | `allowed` | a member approved a pending device code on `/cli-koppelen`. `refs.via` is `cli`, `refs.device_authorization` the id of the request |
| `cli_login_denied` | `allowed` | a member refused a pending device code. Same `refs` as above |
| `cli_token_issued` | `allowed` | the CLI exchanged its device code after approval: the tokens have left Plak and the CLI session exists. Actor is the member who approved, `refs.via` is `cli`, `refs.cli_session` the id of the new session. Refreshing writes no row |
| `cli_logout` | `allowed` | a CLI session was revoked by `plak logout`, with the access token (an expired one too) or a refresh token. `refs.via` is `cli`, `refs.cli_session` the session id |
| `cli_logout` | `refused` | a logout attempt with an unknown or already revoked token; the answer is 204 anyway, so that the endpoint does not give away which tokens exist. Actor is `anonymous`, `reason_code` is `TOKEN_INVALID` |
| `cli_session_revoke` | `allowed` | a member unlinked a linked device via "Gekoppelde apparaten". Same `refs` as with `cli_logout` |
| `cli_refresh_reuse` | `refused` | an already used refresh token was presented again, later than ten seconds after the replacement or not the most recently replaced token; the whole CLI session has been revoked. The just-replaced token within those ten seconds (two refreshes at once) gets only `INVALID_GRANT` and writes no row. Actor is the member behind that session, or `anonymous` if the token could no longer be traced. `refs.via` is `cli`, `reason_code` is `INVALID_GRANT` |

### My own account

| Action | Result | When |
|---|---|---|
| `member_language` | `allowed` | a member set the interface language of the beheer on their own account, from the profile page. `refs.language` is the new value: `nl`, `en`, or `null` when the member handed the choice back to their browser |

The value is in the row because it is a setting somebody changed about
themselves, and a row saying only that something changed answers nothing. It
is a language, not personal data beyond the account it already hangs on.

### The audit log itself

| Action | Result | When |
|---|---|---|
| `audit_read` | `allowed` | a page from the audit log was requested. `refs` carries the filters |
| `audit_actor_lookup` | `allowed` | someone looked up the pseudonym of a person or a repository. `refs.pseudonym` carries the resulting pseudonym (absent on a 404 or a 409), `refs.resolved_as` `member`, `content_viewer`, `ci`, `unknown` or `ambiguous` (and then `refs.matches` carries the number of hits), `refs.reason` the justification, never what was looked up |
| `audit_actor_identity` | `allowed` | someone looked up the actor behind a pseudonym. `refs.pseudonym` carries the pseudonym looked up, `refs.resolved_as` `member`, `content_viewer`, `ci` or `unknown`, `refs.reason` the justification, never the identifier found |
| `audit_ip_reveal` | `allowed` | someone tried to decrypt the full IP address on an audit row. `refs.entry` carries the id of that row, `refs.reason` the justification, `refs.revealed` whether it succeeded, never the IP address itself |
| `audit_purge` | `allowed` | the purge deleted rows, from `audit_log_entries` and from `content_viewers`. `refs` carries the count per table |

## Reasons

**Content** (`content_access`): `OK`, `NO_ACCESS`, `LOGIN_REQUIRED`,
`KEY_INVALID`, `KEY_CODE_REQUIRED` (a link without a code: the code page was
shown), `KEY_CODE_INVALID` (a wrong code submitted),
`KEY_CODE_THROTTLED` (more attempts than the limit per selector allows),
`PATH_INVALID`, `NO_LIVE_VERSION`, `PREVIEW_EXPIRED`,
`UNKNOWN_GROUP`, `UNKNOWN_SITE`, `UNKNOWN_PREVIEW`, `UNKNOWN_VERSION`,
`UNKNOWN_STORAGE`.

**Login** (`login`, `refused`): `IDP_ERROR` (the IdP reported an error
itself), `IDP_UNREACHABLE` (the link with the IdP is not working: metadata,
JWKS or token endpoint unreachable, or the configuration is wrong;
deliberately separate, so that an outage does not read as a spike of failed
attempts), `ATTEMPT_MISSING` (no valid login attempt for this callback),
`STATE_MISMATCH`, `CODE_MISSING`, `ISS_MISMATCH` (missing or differing),
`ACR_INSUFFICIENT` (the required authentication level, such as a second
factor, was not reached) and `TOKEN_INVALID` (signature, claims, nonce or
`at_hash` is wrong, or the token endpoint refused the code). Never the value
that made validation stumble.

**Admin** (`admin_access`): the `code` from the problem+json response, such
as `NO_SESSION`, `CSRF_INVALID`, `ORIGIN_REFUSED`, `MEMBER_DEACTIVATED`,
`MEMBER_AWAITING_ACTIVATION`, `INSUFFICIENT_ROLE`, `NOT_ADMIN`, `UNKNOWN_SITE`.

**Publishing** (`deploy`, `preview_teardown`, `refused`): for a CLI token
`TOKEN_INVALID` (unknown, revoked or expired token, or the member no longer
active); for a CI ID token `CI_ISSUER_UNKNOWN` (the `iss` belongs to no
configured provider), `CI_TOKEN_INVALID` (not a valid JWT, wrong algorithm,
signature or claims are wrong), `CI_AUDIENCE_MISMATCH` (the `aud` is not
exactly `PLAK_BASE_URL`), `CI_REPOSITORY_NOT_TRUSTED` (no repository, or a
different one, linked to the site), `CI_BRANCH_NOT_ALLOWED` (a live deploy
from an event other than `push`, `workflow_dispatch` or `schedule`, without
`event_name`, or outside the configured `liveBranch`) and `CI_PROVIDER_UNREACHABLE` (the keys or, on Forgejo 15 without
repository ids, the reconfirmation of the repository cannot be fetched).
Furthermore `TOKEN_INVALID` for every unrecognised or missing Bearer scheme.
See `api/deploys.py`, `ci/tokens.py` and `ci/trust.py`; the full list is
`CI_REASONS` in `audit/vocabulary.py`.

## Retention

As short as it can be, as long as BIO2 asks.

| Period | What |
|---|---|
| **90 days** | viewing and presence: `content_access` with `allowed`, `login` with `allowed`, `logout`, `cli_logout` with `allowed` |
| **3 years** | everything else: every refusal, login redirect, admin operation, publication, and every access to the audit log itself. Also `cli_login` and `cli_token_issued`: those hand out a credential that lives up to 90 days, so whoever approved it must be traceable for longer than the credential itself |

Three years is the floor BIO2 5.28.01 sets for information about security
incidents; every refusal and every admin operation can become part of one.
Viewing behaviour is not, and therefore does not stay any longer than needed
to trace a leak in the weeks after.

The periods are a SQL function in the database and are enforced by a
trigger: only what is older than its period may be deleted. A shorter period
takes a schema change, not a setting.

Purging is done with `python -m plak.audit.retention` (`just
purge-audit-log`), on `PLAK_DB_URL`. `content_viewers` follows the same
fixed period of 90 days (by `last_seen_at`), without the per-(action,
result) function that `audit_log_entries` has.

## One database account

Plak talks to the database under one account. The shared PostgreSQL service
on ZAD hands out exactly one user, so a separation between a migration, a
runtime and a purge account cannot exist there. Locally we therefore run it
the same way: one account for the app, for alembic and for the purge job.

The guarantee therefore rests entirely on the triggers from migration
`0001_base`:

- `audit_log_no_update` refuses every `UPDATE` on `audit_log_entries`,
  always.
- `audit_log_delete_after_retention` refuses every `DELETE` of a row that
  has not yet reached its retention period.
- `content_viewer_delete_after_retention` does the same for
  `content_viewers` at 90 days.

These triggers apply to everyone who logs in with this account, so to the
app itself as well. That the purge job may delete an expired row is exactly
the intended path: the trigger, not the account, decides what may go.

What we lose by it: the account is also the owner of the schema. An owner
can disable the trigger, rewrite the function, `TRUNCATE` the table or drop
it. An attacker who gets hold of the runtime credentials can therefore do so
too. The triggers stop mistakes and off-hand commands, not an owner who
deliberately wants to wipe the log.

## Access

Platform administrators only, via `GET /-/api/v1/platform/audit`. There is
no per-group window: the group is only a free-form key in `refs`, and
authorization on a field without `NOT NULL` and without a foreign key is no
authorization.

Actors stay pseudonymous in the response. Whoever looks for someone first
computes their pseudonym with `POST /-/api/v1/platform/audit/actor-pseudonym`
and filters on it; so you have to know already who you are looking for. The
other way round is possible too: given a pseudonym from a log row, `POST
/-/api/v1/platform/audit/actor-identity` returns the member, the content
viewer or the linked repository behind it, by pseudonymising every member,
every content viewer and every linked repository again and comparing; there
is no reversible table standing by. Both are a POST with a body, so that an
email address does not end up in a query string and thereby not in a proxy
log.

`actor-pseudonym` searches first on the exact SSO subject (unique per member
and per content viewer, so never ambiguous); only if that gives no hit does
an email address count, and then only a verified email address of a
content viewer (an unverified claim is no proof that someone controls that
address). If an email address belongs to more than one subject, the answer
is 409 `IDENTIFIER_AMBIGUOUS`: search on the SSO subject in that case. An
identifier that is an email address (contains an `@`) and belongs to no one
gives 404 `IDENTIFIER_UNKNOWN` instead of a pseudonym, because that
pseudonym can never occur in the log anyway. A non-email address that
belongs to nothing (a loose SSO subject, say) is still pseudonymised
literally, as `unknown`.

If `identifier` matches no member and no content viewer, and contains no
`@`, then `actor-pseudonym` tries it as a linked repository: `eigenaar/repo`,
`host/eigenaar/repo` or `https://host/eigenaar/repo`. Only a repository that
is linked to a site at this moment yields `resolved_as` `ci`; if the same
name is linked at two providers without the host being supplied, the answer
here too is 409 `IDENTIFIER_AMBIGUOUS`, with the message to name the host as
well. A repository that has never been linked, or that has since been
unlinked, therefore matches nothing at that step: without an `@` that falls
back on the same literal pseudonymisation as any other unknown, non-email
identifier (`unknown`), not on a 404 - that pseudonym then simply never
occurs in the log.

If `actor-identity` yields nothing (404 `PSEUDONYM_UNKNOWN`), then there is
no member, content viewer or linked repository that pseudonymises to that
pseudonym. Several causes: the pepper has been rotated since that row was
written; the actor only ever visited content via the content-host SSO and
that visit is more than 90 days ago, so its `content_viewers` row has been
purged; or the repository has since been unlinked, or deleted along with its
site or group (`ON DELETE CASCADE`). A repository that has never been linked
cannot be traced by `actor-identity` by definition: only linked repositories
are pseudonymised again and compared.

Both endpoints, and the IP reveal below, require a `reason`: 10 to 500
characters, mandatory in the body and to be found unchanged in `refs.reason`
of the corresponding audit row, so readable for every platform administrator
who reads the audit log. Name a case or ticket number therefore, not an
email address or other personal data: a `reason` with an `@` in it, or with
a control or formatting character (such as a tab, a line break or an rtl
override), gets 422. Per platform administrator there is moreover a
daily limit (`PLAK_AUDIT_LOOKUP_DAILY_LIMIT`, 25 by default) on the total
of `audit_actor_lookup`, `audit_actor_identity` and `audit_ip_reveal`
together, counted from the audit log itself over the last 24 hours on that
administrator's pseudonym: not from a counter in memory, which would not
survive a restart or a second replica. The counting and the writing of the
row itself happen in one database transaction, with an advisory lock on the
administrator, so that two concurrent requests cannot both see themselves as
"still under the limit" before either has been written. Whoever hits the
limit gets 429 `LOOKUP_LIMIT_REACHED`; that refusal is itself audited too,
as an ordinary `admin_access`/`refused` row. A result that yields nothing
(404) counts just as much: that too is an attempt at tracing.

Every access writes a row of its own, and it stays for three years. For
these endpoints (`GET /platform/audit`, `actor-pseudonym`, `actor-identity`,
the IP reveal) that writing is even strict: if the audit row fails, no
answer comes back (503 `AUDIT_UNAVAILABLE`) instead of the fail-open
behaviour that the rest of the audit log has.

The full IP address on a single row comes separately: `POST
/-/api/v1/platform/audit/entries/{id}/ip`, with the same `reason`
requirement and the same daily limit, decrypts `ip_encrypted` under
`PLAK_AUDIT_IP_KEY`. `GET /platform/audit` itself shows only `ip_truncated`;
the full address is therefore always a separate, audited step. The
encryption is bound to the row itself (the row id is in the AAD), so pasting
a copied ciphertext onto another row does not decrypt there. On a key
rotation (`docs/security.md`) older rows stay readable as long as
`PLAK_AUDIT_IP_KEY_PREVIOUS` carries the previous key.

## Known gaps

- `TRUNCATE` fires no row triggers. Plak runs with one account, and that is
  the owner of the table: the log can therefore be emptied in one command.
  `ALTER TABLE ... DISABLE TRIGGER` and `DROP TABLE` are open too. There is
  no second layer that stops that (see "One database account").
- No alerting. Reading is possible, signalling not yet.
- The 429 from the rate limiter and the 401 on a Bearer header outside the
  deploy endpoints do not reach the log; they bypass `ApiError`. The 429
  from `LOOKUP_LIMIT_REACHED` does go through `ApiError` and therefore does
  reach the log, as `admin_access`/`refused`.
- If `PLAK_AUDIT_IP_KEY` is lost or rotated without setting
  `PLAK_AUDIT_IP_KEY_PREVIOUS`, older `ip_encrypted` values become
  unreadable (docs/security.md); they do stay in place until their row
  itself is purged.
- The session store is in process memory: a restart throws all sessions
  away, and revocation works only as long as one replica is running.
