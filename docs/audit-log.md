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
| `refs` | what it is about: group, site, path, route template, the fetch metadata of a content request (`fetch_dest`, `fetch_site`, `referer_present`), for a secret link the selector, and `ip_unvouched` when the address next to it could not be vouched for (see "When the IP address is a claim"). Never an email address, the secret part of a link, a token or a query string |
| `ip_truncated` | IPv4 truncated to /24, IPv6 to /48; an IPv4-mapped IPv6 address (`::ffff:a.b.c.d`) counts as IPv4 |
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
| `content_access` | `login_redirect` | an anonymous visitor was sent to log in. On live content the reason is `LOGIN_REQUIRED`; on a preview or `_version` view, where every anonymous refusal becomes this redirect, it is the reason that refused (`NO_ACCESS`, `UNKNOWN_PREVIEW` and so on) |

Public content is never logged.

Every `content_access` row carries `refs.fetch_dest` and `refs.fetch_site`: the
`Sec-Fetch-Dest` and `Sec-Fetch-Site` the browser sent, or `null` when it sent
neither. They are fixed tokens from the browser, never a URL and never
anything about the person, and they are what makes a request made by another
site's page recognisable afterwards.

Beside them stands `refs.referer_present`: whether a `Referer` came along at
all, never which one. It is there to tell two refusals apart that otherwise
read identically. A `FOREIGN_SUBRESOURCE` refusal with a `Referer` is what the
guard is for, another site's page reaching for this one. The same refusal on a
`style`, `script`, `image` or `font` destination *without* a `Referer` is
almost always a site breaking its own assets: its pages suppress the referrer
themselves (`<meta name="referrer" content="no-referrer">` or a
`referrerpolicy` attribute), so the guard can no longer see that the request
comes from the site itself. That is a bug report about a published site, not an
attack, and the two need to be distinguishable without ever writing down a
visitor's URL.

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
| `site_external_sources`, `site_sandbox` | `allowed` |
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
host, `repository` (`eigenaar/repo`), `repository_id`, `ids_confirmed` and
`live_branch`, and on unlinking only group and site. `ids_confirmed` is
`false` when the admin entered the ids and the provider could not confirm
them (a private repository, or the provider was unavailable). Linked with the
CLI token (`plak site link`), the row carries `refs.via` and
`refs.cli_session` as a creation does.

`site_external_sources` is about the site toggle "Externe bronnen
toestaan": `refs.external_sources` is the new state (`true` or `false`),
alongside group and site. The toggle is on by default; a row with `false`
therefore means someone deliberately turned it off.

`site_sandbox` is about the site toggle "Afschermen van andere sites"
(shield from other sites): `refs.sandbox` is the new state (`true` or
`false`), alongside group and site. The toggle is on by default, so a row with
`false` means someone deliberately gave up the shielding, normally because
their site needs browser storage.

`group_create` and `site_create` record the access the new group or site
starts with, in the same three fields as below: `refs.base`, `refs.keys` and
`refs.invitees`, alongside group (and site). That is the effective access,
whether it was chosen or taken from the default (for a group: `site_team`
without extras; for a site: the group's default access). Created with the
CLI token from `plak login` (`plak group create`, `plak site create`), the
row also carries `refs.via` with the value `cli` and `refs.cli_session` with
the id of the CLI session, as on a deploy. A refusal on those two routes
through the CLI token arrives as `admin_access` like any other, with the
member as actor and the same `refs.via` and `refs.cli_session`; that
includes `TOO_MANY_CREATIONS`, the 429 of the creation budget.

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

`site_member_remove` also comes from the group screen: removing someone from
a group can take the roles they held on sites in that group along, and each
of those gets a row of its own with the same action and the same `refs`
(group, site, `member_id`) as a removal from the site screen. The removal is
one transaction, so either the row for the group and the rows for the sites
all describe a change that happened, or none of them is written.

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
| `cli_login` | `allowed` | a member approved a pending device code on `/cli-link`. `refs.via` is `cli`, `refs.device_authorization` the id of the request |
| `cli_login_denied` | `allowed` | a member refused a pending device code. Same `refs` as above |
| `cli_token_issued` | `allowed` | the CLI exchanged its device code after approval: the tokens have left Plak and the CLI session exists. Actor is the member who approved, `refs.via` is `cli`, `refs.cli_session` the id of the new session. Refreshing writes no row |
| `cli_logout` | `allowed` | a CLI session was revoked by `plak logout`, with the access token (an expired one too) or a refresh token. `refs.via` is `cli`, `refs.cli_session` the session id |
| `cli_logout` | `refused` | a logout attempt with an unknown or already revoked token; the answer is 204 anyway, so that the endpoint does not give away which tokens exist. Actor is `anonymous`, `reason_code` is `TOKEN_INVALID` |
| `cli_session_revoke` | `allowed` | a member revoked a linked session via "Gekoppelde sessies". Same `refs` as with `cli_logout` |
| `cli_refresh_reuse` | `refused` | an already used refresh token was presented again, later than ten seconds after the replacement or not the most recently replaced token; the whole CLI session has been revoked. The just-replaced token within those ten seconds (two refreshes at once) gets only `INVALID_GRANT` and writes no row. Actor is the member behind that session, or `anonymous` if the token could no longer be traced. `refs.via` is `cli`, `reason_code` is `INVALID_GRANT` |

### My own account

| Action | Result | When |
|---|---|---|
| `member_language` | `allowed` | a member set the interface language of the admin on their own account, from the profile page. `refs.language` is the new value: `nl`, `en`, or `null` when the member handed the choice back to their browser |

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
`UNKNOWN_STORAGE`, `FOREIGN_SUBRESOURCE` (non-public content asked for as a
subresource of another site's page, see design.md §5.10).

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
Furthermore `TOKEN_INVALID` for every unrecognised or missing Bearer scheme,
and `INTERNAL_ERROR` for a deploy that failed on the machine rather than on a
refusal, a full disk for instance: the client gets the generic 500 and the
exception itself goes to the application log.
See `api/deploys.py`, `ci/tokens.py` and `ci/trust.py`; the full list is
`CI_REASONS` in `audit/vocabulary.py`.

## Retention

As short as it can be, as long as BIO2 asks.

| Period | What |
|---|---|
| **90 days** | viewing and presence: `content_access` with `allowed`, `login` with `allowed`, `logout`, `cli_logout` with `allowed` |
| **3 years** (1096 days) | everything else: every refusal, login redirect, admin operation, publication, and every access to the audit log itself. Also `cli_login` and `cli_token_issued`: those hand out a credential that lives up to 90 days, so whoever approved it must be traceable for longer than the credential itself |

Three years is the floor BIO2 5.28.01 sets for information about security
incidents; every refusal and every admin operation can become part of one.
Viewing behaviour is not, and therefore does not stay any longer than needed
to trace a leak in the weeks after.

The periods are a SQL function in the database and are enforced by a
trigger: only what is older than its period may be deleted. A shorter period
takes a schema change, not a setting. The period a row gets is fixed when it
is written, by the chain it lands in (see "The integrity chain" below): the
trigger and the purge ask the chain, not the row, so moving an action to the
other period later applies to new rows only.

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

- `audit_log_chain` stamps `occurred_at` on every `INSERT` and hashes the row
  into its chain; see below.
- `audit_log_chain_head_guard` refuses every write to
  `audit_log_chain_heads` that does not come from `audit_log_chain`, and every
  move of a head other than one position forward.
- `audit_log_no_update` refuses every `UPDATE` on `audit_log_entries`,
  always.
- `audit_log_delete_after_retention` refuses every `DELETE` of a row that
  has not yet reached its retention period.
- `content_viewer_delete_after_retention` does the same for
  `content_viewers` at 90 days.
- `audit_log_entries_no_truncate`, `audit_log_chain_heads_no_truncate` and
  `content_viewers_no_truncate` refuse `TRUNCATE` on those tables. It fires
  no row trigger, so without these it would get past every guard above in
  one command.

These triggers apply to everyone who logs in with this account, so to the
app itself as well. That the purge job may delete an expired row is exactly
the intended path: the trigger, not the account, decides what may go.

What we lose by it: the account is also the owner of the schema. An owner
can disable the triggers, rewrite the functions, and then `TRUNCATE` the table,
or drop it. An attacker who gets hold of the runtime credentials can therefore do so
too. The triggers stop mistakes and off-hand commands, not an owner who
deliberately wants to wipe the log.

## The integrity chain

Append-only says a row cannot be changed. It says nothing about whether a row
is true. `INSERT` is the one thing the app account is supposed to do, so
inventing history needs no trigger switched off at all: before the chain, a
row could carry someone else's `actor_pseudonym`, made-up `refs` and an
`occurred_at` two years back, and it would read back exactly as written.

Every row is therefore hashed on insert, by `audit_log_chain` in `0001_base`:

- `occurred_at` is set by the trigger to `clock_timestamp()`, whatever the
  statement carried. The moment a row claims is the database's, not the
  caller's.
- The row gets a position in a chain, `chain_shard` plus `chain_seq`, and a
  `chain_hash` over the previous row's hash and its own content. The content
  is fed in length-prefixed, so no two different rows can produce the same
  hash input. The previous row's hash is kept on the row too, as
  `chain_prev_hash`, so every row's own hash can be recomputed from the row
  alone, also after its predecessor has been purged.
- Removing a row, rewriting one or back-dating one leaves every row behind it
  in that chain with a hash that no longer follows, and the break says where.

**Sixteen chains per retention period, not one.** A chain is a serialisation
point: an insert has to read the tail of its chain under a lock, and audit rows
are written on essentially every request. One global chain would put every
audit write in the application behind a single lock. Which chain a row lands
in follows from its retention period and its id: chains 0 to 15 hold the
90-day rows, chains 16 to 31 the three-year rows, and the id picks one of the
sixteen. That spreads the contention sixteen ways while the shard stays part of
the hash (a row cannot be moved to another chain unnoticed). The lock is a
transaction-scoped advisory lock, so it is released on commit or rollback and
cannot outlive its writer; the app writes each audit row in a transaction of
its own.

Keeping the two periods apart is what makes the purge and the chain fit
together. Within a chain `occurred_at` rises with `chain_seq`, and every row
has the same period, so what has expired is always the oldest stretch of the
chain. Were the periods mixed in one chain, the purge would cut the 90-day
rows out from between the three-year rows, and from day ninety on every chain
would show gaps that look exactly like a deletion.

**The head lives apart from the rows.** `audit_log_chain_heads` keeps, per
chain, the last position, its hash and when that row was written; the trigger
chains a new row onto that, not onto the newest row still in the table. A
quiet 90-day chain can age out completely, and a chain that then restarted at
position 1 would hand out positions a published checkpoint already holds a
different hash for. The head table holds no personal data, only positions and
hashes, so it outlives the rows without stretching any retention period.

**Checking it.** `python -m plak.audit.chain` (`just verify-audit-log`) walks
every chain and reports the first break in each, on the same `PLAK_DB_URL`.
It recomputes the hashes with the very same SQL function the trigger uses, so
the check cannot drift away from the write.

**A chain does not have to start at position 1.** The retention purge removes
the oldest rows of a chain, so on any environment older than ninety days the
chains begin somewhere above 1, and the row they begin at was hashed over a
predecessor that is gone. That row is the anchor. Its own hash is still
checked, recomputed over the `chain_prev_hash` it carries, so rewriting it is a
break like anywhere else; only its link backwards cannot be checked, because
there is nothing left to link to. The walk says in the log which position each
chain begins at, because that is a fact about the log a reader has to know.

Reporting a purged front as a break instead would have made `verify-audit-log`
report a broken chain as a matter of routine, and the first real break after
that would be read as "the purge again". The asymmetry that makes this safe:
the purge only ever removes from the oldest end of a chain (one period per
chain, times rising along it), so a gap *between* two surviving rows is never
something it left, and that stays a break.

**The newest end is held against the head.** Once the rows are walked, each
chain's newest surviving row is compared with its entry in
`audit_log_chain_heads`. The purge takes a chain from the front and the head
row is always the last of a chain to expire, so while any row is left, the
newest one is the head. A chain with no rows left is in order only when its
head row has passed its retention deadline (the purge took all of it), or when
the head never moved from position 0. Rows and heads are read in one snapshot,
so an insert during the walk cannot look like a head without its row.

The walk reports the first break per chain as one of:

| Report | What it means |
|---|---|
| `sequence_gap` | a position is missing between two rows |
| `hash_mismatch` | the row's content no longer matches its hash |
| `link_mismatch` | the row's `chain_prev_hash` is not the hash of the row before it, or position 1 claims a predecessor |
| `time_went_back` | the row is older than the one before it |
| `tail_missing` | the chain's registered head stands past its newest surviving row, or the chain has no rows left while its head row is still within its retention period: rows were removed from the newest end. Reported at the head's position, with no row to name |
| `head_mismatch` | the newest row and the registered head disagree otherwise (the head stands behind the row, holds another hash, or is missing while the chain has rows): the head was rewritten, and the next row would chain onto something else |

**Which check catches what.** The two checks do not overlap, and neither is
enough on its own:

| | `verify-audit-log` (the walk) | `verify-audit-head` (a published line) |
|---|---|---|
| A row rewritten, its hash left alone | caught (`hash_mismatch`), the oldest surviving row included | caught if it is a published position |
| A row rewritten and its own hash recomputed | caught at the next row (`link_mismatch`) | caught if it is a published position |
| A row rewritten, chain recomputed | not caught | caught |
| A row removed from the middle | caught (`sequence_gap`) | caught if it is a published position |
| Rows removed from the newest end | caught (`tail_missing`), unless the head was moved back to match | caught (`row_missing` before its deadline, `chain_shortened` if the head was moved back as well) |
| Rows removed from the oldest end | not caught | caught (`front_purged` before its published deadline); after it, that is the purge |
| The whole table emptied | caught (`tail_missing` per chain) while the heads remain; not caught if they were emptied too | caught |

So running only the walk is not enough, and a nightly publication with a
retained log is what makes the second column exist at all.

**What it does not prove.** The account is still the owner of the schema. An
attacker holding those credentials can disable the trigger, rewrite rows and
recompute every hash afterwards, and `verify-audit-log` will say the chain is
whole. The same goes for rows dropped off the front of a chain, where nothing
left points at them and the purge takes from anyway, and for rows dropped off
the newest end by someone who also moves the head back. What the chain buys is that tampering now has to be
complete and deliberate to go unnoticed, and that a verifier outside this
database, holding a chain hash from an earlier moment, can tell that the
history was rewritten. Only shipping the log off-host, to an append-only WORM
sink or a SIEM the runtime account cannot reach, makes the log authoritative
against its own owner; that is an infrastructure decision, and it has not
been taken. Getting a chain hash outside this database is cheaper than that,
and is what the next section does.

## Publishing the chain head

`python -m plak.audit.checkpoint` (`just publish-audit-head`) writes one line
to the application log with, per chain, the last position and its hash, plus
the oldest position still present and its hash, and for both the moment their
retention period runs out. The hash at the last position covers every row
before it in that chain, so that one value is a commitment to the whole history
up to that moment; the oldest position is there because the purge moves that
end, and a line from before is the only thing that can say where it stood. The
deadlines are what tell the purge from a deletion later: a published row that
is gone before its deadline did not go by the purge.

Why the application log: a line that has been shipped cannot be retracted
afterwards. Whoever holds the database can rewrite every row and recompute
every hash, but not the copy that already left the machine. From that moment
on the two disagree, and anyone who kept the older line can say so.

The line looks like this, on one line:

```
INFO audit-chain-checkpoint {"format":"plak-audit-chain-checkpoint/3","taken_at":"2026-09-24T03:00:01.284915+00:00","entries_total":41027,"digest":"9f2c...","shards":[{"shard":0,"first":1904,"first_hash":"c70d...","first_expires":"2026-09-24T02:58:40.102311+00:00","seq":2571,"hash":"4ab1...","expires":"2026-12-23T02:59:57.881020+00:00"}, ...]}
```

`audit-chain-checkpoint` is the grep handle and everything after it is one JSON
object, so the same line serves a person scrolling through the log and a script
that reads it. What is in it and why:

| Field | Why it is there |
|---|---|
| `format` | the line is meant to be compared with one from a year ago, so it says which version wrote it. `/3` is the layout with sixteen chains per retention period; a `/1` or `/2` line numbered its chains differently and is refused |
| `taken_at` | the database's own clock, the same one that stamps `occurred_at`; without it two lines cannot be put in order |
| `shards` | per chain the last position (`seq`) and its hash, and the oldest position still present (`first`) and its hash, each with the moment its retention period runs out (`expires`, `first_expires`). This is the evidence: a hash is what a rewrite changes, the last position what a truncation lowers, the first position where the purge has got to, and a deadline whether a row that is gone was allowed to go. A chain whose rows have all aged out has a last position and no first one |
| `entries_total` | the sum of the positions, so the number of rows ever written. It may never go down, and it is the one number a reader can compare at a glance |
| `digest` | one SHA-256 over all the heads. Two lines with the same digest are the same history; a different one says to look at the shards |

The hashes are not secret. They are a commitment, not a credential: they
reveal nothing about the rows they cover, and they are worth exactly as much as
the number of places that have seen them. A published head that gets redacted
out of the log, or kept in a place nobody else can read, protects nothing and
makes the check below impossible.

**How often.** Nightly is enough, next to the purge; more often makes the
window between two published heads smaller, which is the only thing frequency
buys. The line is a few hundred bytes.

**Checking a line against the database.** `just verify-audit-head <bestand>`
(`python -m plak.audit.checkpoint --against -` reads standard input) takes a
line as it stands in the log, timestamp and marker and all, and holds every
published head against the database:

| Report | What it means |
|---|---|
| `chain_shortened` | that chain's head stands at a lower position than when the line was published. Appending cannot do that |
| `hash_mismatch` | the row at that position is not the row that was published: its stored hash differs, or its content no longer produces that hash. The history was rewritten |
| `row_missing` | the published last row is gone. After its published deadline that is the whole quiet chain aging out, which is reported and does not make the check fail; before it, somebody removed the row |
| `front_purged` | the published oldest row is gone. After its published deadline that is what the purge does every night, reported without failing the check; before it, the front of the chain was cut off, and the check fails |

Rows written after the publication change nothing: a head that is no longer the
head still has to be where it was, with the hash it had.

**What this proves.** That an outside observer holding an older line can tell
that history was rewritten, including in the cases `verify-audit-log` cannot
see: the schema owner who switched the trigger off and recomputed every hash
(the head included), rows removed from the newest end with the head moved back
to match, and the oldest end having moved further than the retention period
allows.
**What it does not.** It prevents nothing, it notices nothing by itself, and it
proves nothing at all if nobody kept an older line. Its whole value sits in the
log retention and in the fact that the log leaves the machine. Shipping every
row to a sink the runtime account cannot reach remains the heavier answer.

## When the IP address is a claim

`PLAK_BEHIND_PROXY` says whether a proxy stands in front of the pod. With one,
the derivation (`net.py`) reads the last `X-Forwarded-For` entry: the one that
proxy wrote about the connection it accepted. Everything to the left of it is
the client's to invent, and is never read. Without one, the socket decides and
the header is not read at all.

The row says when that did not hold. `refs.ip_unvouched` is `true` where the
proxy the deployment promises wrote nothing usable: no `X-Forwarded-For`
arrived, or its last entry is not an address; `ip_truncated` and `ip_encrypted` then carry the direct peer
instead, which is a proxy's address rather than a visitor's. The flag is absent
on every other row, and that absence is the ordinary case.

A `true` here is a sign that the deployment and the chain in front of it
disagree, which is a configuration question rather than a visitor's doing. The
address is stored unchanged either way and nothing is refused over it: this is
a note about the derivation, not a decision.

Reading a fixed entry replaced a walk that skipped entries falling inside
`PLAK_TRUSTED_PROXIES`. That needed the setting to name the routers, and on ZAD
their range cannot be had, so it named all of RFC1918 -- wide enough that a
visitor on a private address could write an entry the walk would skip. Worse,
the walk read `X-Forwarded-For` with `headers.get`, which returns the first
header line only; the OpenShift router adds a line of its own rather than
extending the client's, so the entry it wrote was never read at all. Measured
on 2026-09-30: a request carrying `X-Forwarded-For: 203.0.113.99` was recorded
under that address.

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

- Plak runs with one account, and that is the owner of the tables: `ALTER
  TABLE ... DISABLE TRIGGER` is open to it, and with the `TRUNCATE` guard off
  the log can be emptied in one command; `DROP TABLE` is open too. There is
  no second layer that stops that (see "One database account"); a published
  chain head makes it visible afterwards, it does not prevent it.
- No alerting. Reading is possible, signalling not yet.
- The 429 from the rate limiter and the 401 on a Bearer header outside the
  endpoints that accept one do not reach the log; they bypass `ApiError`.
  The 429 from `LOOKUP_LIMIT_REACHED` and from `TOO_MANY_CREATIONS` does go
  through `ApiError` and therefore does reach the log, as
  `admin_access`/`refused`.
- If `PLAK_AUDIT_IP_KEY` is lost or rotated without setting
  `PLAK_AUDIT_IP_KEY_PREVIOUS`, older `ip_encrypted` values become
  unreadable (docs/security.md); they do stay in place until their row
  itself is purged.
- The session store is in process memory: a restart throws all sessions
  away, and revocation works only as long as one replica is running.
