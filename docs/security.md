# Security checklist

The numbered rules themselves live in `docs/design.md`; a section number
here (§13, §5.7, §4a) is a section there.

This is the checklist from the design (§13). Every item gets a status:
**implemented** (built and tested, with the source alongside), **planned**
(still to be built) or **open** (falls outside the app, or was deliberately
postponed). Update this table as an item gets built; an item only moves to
"implemented" once the accompanying tests are green and the review panel has
approved it.

This table lists the security requirements from the design spec and their
current state. Whatever is still open below is open because it falls outside
the app or was deliberately postponed (base image digests, DPIA, pentest).

## Authentication and session

| Item | Status |
|---|---|
| Alg allowlist (RS256/PS256/ES256, no `none`/HS*) as our own code on top of authlib | implemented: `auth/oidc.py` (`ALG_ALLOWLIST`), `backend/tests/test_oidc.py` |
| RFC 9207 `iss` check on the callback query parameters, our own code on top of authlib | implemented: `auth/oidc.py` (`check_callback_iss`), `backend/tests/test_oidc.py` |
| `acr` from a configured list (`PLAK_OIDC_REQUIRED_ACR`); empty = no acr check and no `acr_values`, with a startup warning in production. Adjusted because the ZAD Keycloak only supplies `acr` `0`/`1` | implemented: `auth/oidc.py`, `backend/tests/test_oidc.py`; see `docs/local-development.md`, OIDC configuration |
| Client authentication `private_key_jwt` (default) or `client_secret_post`/`client_secret_basic` via `PLAK_OIDC_CLIENT_AUTH`; exactly the matching secrets required, error messages without secret values. `client_secret_*` deviates from the NL GOV OIDC profile (private_key_jwt or mTLS) and exists for the ZAD Keycloak, which only creates client-secret clients; the ZAD variables `OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` are the source for the `PLAK_OIDC_*` settings | implemented: `config.py`, `auth/oidc.py`, `backend/tests/test_config.py`, `backend/tests/test_oidc.py` |
| `at_hash` validation when present in the id token | implemented: `auth/oidc.py`, `backend/tests/test_oidc.py` |
| `__Host-` session cookie: HttpOnly, Secure, `Path=/`, SameSite=Strict (spec §7; only the content session is Lax) | implemented: `auth/sessions.py`, `backend/tests/test_sessions.py` |
| Content session (`__Host-plak-content`, SameSite=Lax) separate from the admin session; admin routes refuse content sessions, content serving refuses admin sessions | implemented: `auth/sessions.py` (`SessionKind`), `platform/pages.py`, `backend/tests/test_sessions.py` |
| Session id rotation on login | implemented: `platform/pages.py` (an existing session of the same kind expires at the callback), `backend/tests/test_sessions.py` |
| Logging out on the admin host exclusively via POST, and only from the admin origin itself (`admin_origin_ok`). POST alone is no guard there: the content host is same-site with the admin host, so `SameSite=Strict` still sends the session cookie along with a form on a published site. The content leg keeps accepting GET, for the reason in the row below | implemented: `platform/pages.py`, `api/origin_guard.py`, `backend/tests/test_sessions.py` |
| Logging out ends both sessions: the admin logout redirects to `/-/logout` on the content host, which clears the content session and sends the visitor back. That step accepts GET, because it arrives via a 303; making someone log out is the most harmless forgery there is. `?from=` is a fixed word, not an address | implemented: `platform/pages.py`, `host_separation.py`, `backend/tests/test_sessions.py`. That is why the admin CSP allows the content origin in `form-action` |
| RP-initiated logout at the IdP (OIDC RP-Initiated Logout 1.0), with `id_token_hint` | implemented behind `PLAK_OIDC_RP_LOGOUT`, off by default: the `post_logout_redirect_uri` is `{PLAK_CONTENT_BASE_URL}/-/logout?from=beheer` and must be registered at the IdP, otherwise the visitor lands on an error page of the IdP. With the flag on, `form-action` also allows the issuer origin |
| Periodic re-validation of a session at the IdP: on the first request after `PLAK_IDP_RECHECK_SECONDS` (default 900, `0` turns it off) the session's refresh token goes to the token endpoint (`grant_type=refresh_token`, same client authentication as the login). If that succeeds the session stays, the rotated refresh token is stored and the clock restarts; the `sub` of a new id token must equal that of the session. If the IdP refuses with `invalid_grant` (blocked, deleted, session ended there), the Plak session expires and the request continues as not logged in. An outage (network, timeout, 5xx) leaves the session in place, does not block the request and is retried after a one-minute backoff; that goes to the application log, not the audit log. If the token endpoint refuses our **client** instead of the session (`invalid_client`, `unauthorized_client`, `unsupported_grant_type`, `invalid_request`, `invalid_scope`), everyone stays logged in - a broken integration must not log the whole platform out - but it gets loud: an ERROR line with the error code and the issuer, at most one per backoff window, and `/healthz` answers `{"status": "degraded", "idp_revalidation": "IdP re-validation is failing: <code>"}` until a successful re-validation clears the complaint. `/healthz` still returns 200 while it does: the probe keeps the pod alive and a broken IdP integration is no reason to restart | implemented: `auth/revalidation.py`, `auth/oidc.py` (`refresh_tokens`), `main.py` (`SessionRecheckMiddleware`), `backend/tests/test_session_revalidation.py`. The window in which someone blocked at the IdP can still get in is thereby at most the interval instead of the full `MAX_SESSION_AGE` of 12 hours |
| The refresh token sits with the session in process memory: never in a log, never in an audit ref, never to the browser. The field has `repr=False`, so it does not end up in a dataclass dump in a log line or traceback either | implemented: `auth/sessions.py`, `backend/tests/test_session_revalidation.py` |
| Back-channel logout (OIDC Back-Channel Logout 1.0) on `POST /-/oidc/backchannel-logout`, only on the admin host. Checked are: the signature via the existing JWKS handling, `iss`, `aud`/`azp`, a recent `iat`, the claim `events` with `http://schemas.openid.net/event/backchannel-logout`, the presence of `sid` or `sub`, the absence of a `nonce`, and a `jti` that did not already pass by shortly before (replay cache in process memory). After that the sessions the token is about expire (on `sid`, otherwise all sessions of that `sub`; both session kinds). The answer is always a bare status with `Cache-Control: no-store` and does not betray whether there was a session | implemented: `platform/backchannel.py`, `auth/oidc.py` (`validate_logout_token`), `backend/tests/test_backchannel_logout.py`. The Keycloak client on ZAD still has to be configured for it (`docs/deploying-on-zad.md` §6a); until then the endpoint exists but nobody calls it |
| CLI sessions (`plak login`) are **not** re-validated at the IdP | deliberate, open point: a CLI session arises from a device code that a member approved in an admin session, and gets no IdP token of its own. The refresh token of that admin session sits in process memory and is long gone by the next CLI refresh (up to 30 days later, 90 absolute), so there is nothing to check with. Someone blocked at the IdP therefore keeps their CLI session until it expires or is revoked; deactivating in Plak does work immediately, because member status is checked per refresh and per deploy. Closing this requires storing the IdP refresh token of the approving admin session with the CLI session in the database, and thereby token material at rest that currently exists nowhere: that requires encryption and a decision of its own |
| Sessions do not survive a restart and cannot be revoked centrally | deliberate: the store sits in process memory and ZAD runs one replica. Deactivating works immediately anyway, because member status is checked per request (`require_active_member`, `_belongs_to_site`); revoking sessions would replace the message "je toegang is ingetrokken" with "je bent niet meer ingelogd" |
| `returnTo` strictly validated (own origin only, paths only) | implemented: `auth/sessions.py` (`valid_return_to`), `backend/tests/test_sessions.py` |

## Access to content

| Item | Status |
|---|---|
| Neutral 404s, byte-identical on refusal and on non-existence | implemented: `serving/response.py` (`neutral_404_response`, the single construction point), `backend/tests/test_access_gate.py`, `backend/tests/test_serving.py` |
| Constant-time comparison of secret link verifiers | implemented: `access/keys.py`, `backend/tests/test_keys.py` |
| Constant-time comparison of CLI tokens (device code, access and refresh token): selector plus SHA-256 hash, with a dummy hash for an unknown selector so that an unknown selector takes as long as a wrong secret | implemented: `cli/service.py` (`matches`, `_DUMMY_HASH`), `backend/tests/test_cli_service.py` |
| `__Secure-` key cookie, HttpOnly, SameSite=Lax, path exactly on site/preview | implemented: `auth/sessions.py` (`KEY_COOKIE`), `serving/router.py`, `backend/tests/test_serving.py` |
| Secret link without a code: `?key=selector` only shows a code page for a usable key of that site, everything beyond that stays the neutral 404; the code goes in the body of a POST, never in the URL | implemented: `serving/code_page.py`, `access/gate.py` (`code_page_needed`), `access/keys.py` (`verify_parts`, `selector_usable`, `compare_dummy`), `backend/tests/test_code_page.py` |
| Secret link: `?key=` is redeemed (cookie plus 302 to the URL without `key`); key never in audit, returnTo or access log | implemented: `serving/router.py`, `containers/plak/Containerfile` and `justfile` (`--no-access-log`), `backend/tests/test_serving.py` |
| Expired preview gets the neutral 404 straight away at the access decision itself (not only via the purge job) | implemented: `access/gate.py` (`REASON_PREVIEW_EXPIRED`), `backend/tests/test_access_gate.py` |
| `_version` views exclusively for active group members, every access audited | implemented: `access/gate.py`, `serving/router.py`, `backend/tests/test_access_gate.py`, `backend/tests/test_serving.py` |
| Path validation before every access check (traversal, null bytes, backslashes, percent-encoded) | implemented: `serving/resolution.py` (`normalise_rest`), `backend/tests/test_resolution.py` |
| Non-public content is not served to a subresource request from another site's page on the same origin (one hostname for every site, so the cookies ride along); navigation and clients without `Sec-Fetch-*` stay through. A stopgap while every site shares one origin, see design.md §5.10 | implemented: `serving/router.py` (`_foreign_subresource`), `backend/tests/test_serving.py` |

Serving (§5) is done entirely by the app itself:
`FileResponse` with Range, ETag/304 and the fixed header set, and the
host separation of §4a as app middleware (`host_separation.py`). There is no
X-Accel path and no nginx logic.

## API security

| Item | Status |
|---|---|
| CSRF token in a header (double submit against `__Host-plak-csrf`) on session-borne mutations | implemented: `auth/sessions.py` (`csrf_valid`), `api/admin.py` (`require_csrf`), `backend/tests/test_admin_api.py` |
| Bearer tokens accepted exclusively on the two deploy/preview endpoints and the CLI session endpoints | implemented: `api/deploys.py` (`BearerOutsideDeploysMiddleware`), `api/cli.py`, `backend/tests/test_deploy_api.py`, `backend/tests/test_cli_api.py` |
| Secret links: always an expiry date, 90 days by default, 365 at most (BIO2 5.18.02); `expires_at` is NOT NULL | implemented, expiry date mandatory: `expiry.py`, `backend/tests/test_expiry.py`, `test_keys.py` |
| CI id token (OIDC "trusted publishing"): issuer allowlist (GitHub fixed, Forgejo per host in `PLAK_CI_FORGEJO_HOSTS`), keys via discovery/JWKS of the issuer origin itself (https, 5s timeout, size limit, no redirects; cached for an hour, one fetch at a time per issuer, no retry for 30 seconds after a failed fetch), RS256 only, `exp`/`iat` mandatory with 60s leeway, `aud` must be exactly `PLAK_BASE_URL` | implemented: `ci/providers.py`, `ci/tokens.py`, `config.py`, `backend/tests/test_ci_providers.py`, `test_ci_tokens.py` |
| CI repository trust: a site links exactly one repository (`PUT/DELETE /sites/{group}/{site}/repository`, site role admin, without a secret); a token matches on `repository_id` (and `repository_owner_id` if present) or, without ids (Forgejo 15), on `eigenaar/repo` with a re-confirmation against the Forgejo API (cached for 5 minutes); live publishing only from `push`, `workflow_dispatch` or `schedule` (fail closed without `event_name`) and, if it is set, only from `liveBranch`; previews and cleanup from any event and from any branch | implemented: `ci/trust.py`, `api/admin.py` ("Linked repository"), `api/deploys.py`, `backend/tests/test_ci_trust.py`, `test_admin_api.py`, `test_deploy_api.py` |
| CLI device flow (`plak login`, RFC 8628): secrets as selector + SHA-256 hash, compared constant-time; the refresh token rotates on every use, reuse of an already used refresh token revokes the whole session (`cli_refresh_reuse`), except for the just-replaced token within ten seconds (concurrent refresh: only `INVALID_GRANT`); logging out also works with an expired access token or with the refresh token and always answers 204 (no oracle); deactivating a member revokes all their CLI sessions; the rate limit counts Bearer requests per IP, never per (unproven) token; access token 1 hour, session 30 days after the last refresh and at most 90 days after linking; approving requires an admin session no older than fifteen minutes that was itself started from the admin origin (`self_initiated`; a login another site navigated the browser into never counts as fresh, because the IdP returns without a prompt on an existing SSO session), a valid CSRF header and only goes via the admin origin; the approval screen shows the account being linked to, the program, the time and the truncated network, and warns separately if the request comes from a different truncated network than the approver (`sameNetwork`, without full IP addresses) and warns explicitly against a shared link or code (anti-phishing) | implemented: `cli/service.py`, `api/cli.py`, `api/admin.py` ("CLI login: the member's side"), `frontend/src/pages/CliKoppelen.vue`, `backend/tests/test_cli_service.py`, `test_cli_api.py`, `test_admin_api.py` |
| Authorization server-side per endpoint, never only in the SPA | implemented: `api/admin.py`, `backend/tests/test_admin_api.py` |
| `application/problem+json` error contract with fixed status codes | implemented: `api/errors.py`, `backend/tests/test_api_integration.py` |

## Headers and CSP

| Item | Status |
|---|---|
| Content CSP (§5.7 regime) on all content routes | implemented: `serving/response.py` (`CONTENT_CSP`, on the neutral 404 too), `backend/tests/test_serving.py` |
| External sources per site (on by default) | implemented: `sites.external_sources`, `serving/response.py` (`CONTENT_CSP_EXTERNAL`), `api/admin.py` (`PUT /sites/{group}/{site}/external-sources`, site role admin, CSRF, audit action `site_external_sources`), `frontend/src/components/site/TabAccess.vue`, `backend/tests/test_security_headers.py`, `test_serving.py`, `test_admin_api.py`. See "External sources" below |
| Admin CSP (§9 regime, stricter) on everything on the admin host | implemented: the app serves the SPA itself with this CSP (`platform/spa.py`, `backend/tests/test_spa.py`), and HTML on the admin host that carries no CSP of its own gets the same regime from `security_headers.py`. The regime follows the host, not the path: a path rule would make a refusal on the content host distinguishable from an ordinary neutral 404. `/-/api/docs` does carry a CSP of its own (`DOCS_CSP` in `api/docs.py`): identical, apart from `style-src`, which allows `'unsafe-inline'` because Swagger UI puts style attributes on its elements. `script-src` stays `'self'`; the page has no inline script, the Swagger bootstrap sits in `docs-init.js`; JSON answers only get `frame-ancestors 'none'` (`backend/tests/test_security_headers.py`) |
| HSTS (includeSubDomains) | implemented: app middleware `security_headers.py`, only with an https `PLAK_BASE_URL`, `max-age=31536000; includeSubDomains`; preload deliberately not |
| `Permissions-Policy` | implemented: `camera=(), microphone=(), geolocation=()` on every answer |
| `Cross-Origin-Opener-Policy: same-origin` on everything on the admin host | implemented: `security_headers.py` |
| `frame-ancestors 'none'` on everything on the admin host | implemented: in the admin CSP of the SPA; API answers without a CSP get `frame-ancestors 'none'` from `security_headers.py` |
| `X-Robots-Tag: noindex, nofollow` on HTML on the admin host, previews and `_version` | implemented: `serving/response.py` (previews and `_version`), `platform/spa.py` and `security_headers.py` (admin host), `backend/tests/test_serving.py`, `backend/tests/test_security_headers.py`. Still open: key content (see below) |
| No inline scripts in the built SPA | implemented: `frontend/tests/build-output.test.ts` guards the built `index.html` (vitest, not in CI yet because that workflow is missing). Open: a CSP smoke test in the browser; the header side is covered by `backend/tests/test_security_headers.py` and `backend/tests/test_spa.py` |

### External sources

The content CSP allows scripts and styles from a fixed list of hosts by
default. That is the setting that fits what people deliver: HTML from an AI
assistant that loads Chart.js or Tailwind from a CDN, or a site that draws the
web components of a design system from a CDN. Without that setting such
content breaks without anyone seeing why.

Per site there is therefore a switch, "Externe bronnen toestaan" (allow
external sources) (`sites.external_sources`, `true` by default). Turning it off
is an extra restriction, and the safer choice for a confidential page: the page
then fetches nothing from outside, lets nobody from outside watch along either,
and runs no code that we did not deploy. With it on, the content gets the same
CSP with exactly these additions, and no others:

| Directive | Added |
|---|---|
| `script-src` | `https://cdnjs.cloudflare.com https://cdn.jsdelivr.net https://unpkg.com https://cdn.tailwindcss.com` |
| `style-src` | the first three, plus `https://fonts.googleapis.com` |
| `font-src` | `https://fonts.gstatic.com` |

Tailwind's CDN is in `script-src` only: it is a script that injects its own
`<style>` element, which `'unsafe-inline'` in `style-src` already allows.

This is a fixed list, not a free-text field: supplying a host of your own would
turn the switch into a way to run arbitrary code on the content host.

What does not move with it: `connect-src` stays `'self'`, so even with external
sources on the page cannot send data to those parties (or to anyone else).
`img-src` stays `'self' data: blob:`, and `frame-ancestors 'none'`,
`form-action 'self'`, `object-src 'none'` and `base-uri 'self'` stay in place.
Both policies come from one table in `serving/response.py`, so the strict
variant and the variant with external sources cannot drift apart;
`backend/tests/test_security_headers.py` pins that relationship down.

The switch applies to every answer that carries the content CSP for that site:
the live pages, previews, `_version` views and assets. Two places deliberately
do not follow it. The neutral 404 always keeps the strict `CONTENT_CSP`,
because it has to stay byte-identical: a deviating CSP would betray which site
a refused path belonged to. The code page for a secret link
(`serving/code_page.py`) keeps the strict CSP as well, because it is a page of
the platform itself, with its own markup and no need for anything from outside.

What the administrator needs to know is in the SPA in those words too: the
parties behind cdnjs, jsDelivr, unpkg, the Tailwind CDN and Google Fonts see
the IP address of
every visitor of the page, and their code runs in the page. For confidential
pages the switch therefore stays off; anyone who wants certainty bundles the
library into the dist.

#### Accepted risk: the switch stays on by default

This is a decision, not an oversight. The publisher decides what their own page
loads, and a default of off would break the content people actually deliver
without them being able to see why. So a published site may load scripts and
styles from the allowed CDNs until its publisher turns that off.

What is accepted with it: every site runs on one origin. A compromise of one of
those CDNs therefore does not stay with one site. The code runs in the page of
every site whose visitor loads it, on the content origin, with whatever that
page can reach.

The per-site sandbox reduces that substantially: a sandboxed document has no
cookies and cannot reach another site's content, so a compromised script is
limited to the page it ran in. It is not the whole answer. It does not stop the
CDN seeing every visitor of every site that loads from it, it does not stop the
script doing whatever that one page does, and it holds only for the documents
that are sandboxed.

Whoever cannot accept that turns the switch off per site and bundles the
library into the dist; for confidential pages that is the advice, in those
words, in the interface.

## Ingest and storage

| Item | Status |
|---|---|
| Fail-closed ingest: absolute paths, `..` segments, symlinks/hardlinks, null bytes refused | implemented: `ingest/unpacker.py`, `backend/tests/test_unpacker.py` |
| Bomb guard: decompression incrementally against limits before materialisation | implemented: `ingest/unpacker.py` (archive headers are not trusted), `backend/tests/test_unpacker.py` |
| Top-level `_preview`/`_version` in a dist refused | implemented: `ingest/unpacker.py` (`RESERVED_SEGMENTS`), `backend/tests/test_unpacker.py` |
| Atomic rename on the same volume (no EXDEV/copy fallback) | implemented: `ingest/store.py`, `backend/tests/test_store.py` |
| Atomic live swap and preview upsert | implemented: `ingest/service.py` (pointer swap in the same transaction, upsert with `ON CONFLICT`), `backend/tests/test_ingest_service.py` |

## Rate limiting

| Item | Status |
|---|---|
| Limits per endpoint class (login strict, API mutations moderate, content generous) | implemented: `ratelimit.py`, `backend/tests/test_ratelimit.py` |
| Global backstop limit per class | implemented: `ratelimit.py`, `backend/tests/test_ratelimit.py` |
| Fail-closed on a broken counter | implemented: `ratelimit.py`, `backend/tests/test_ratelimit.py` |
| Trusted proxy configuration mandatory in production mode (startup refused without it) | implemented: `config.py` (`PLAK_TRUSTED_PROXIES` mandatory with `PLAK_ENVIRONMENT=productie`), `net.py`, `backend/tests/test_config.py` |
| Code of a secret link: its own class `code`, ten attempts per quarter of an hour per client IP and on top of that ten per quarter of an hour per selector | implemented: `ratelimit.py` (`RateLimitClass.CODE`), `serving/code_page.py`, `config.py` (`PLAK_RATELIMIT_CODE_*`), `backend/tests/test_code_page.py`, `backend/tests/test_ratelimit.py` |
| 429 with `Retry-After`, no escalating penalties | implemented: `ratelimit.py`, `backend/tests/test_ratelimit.py` |

## Audit

| Item | Status |
|---|---|
| Append-only audit log (DB triggers refuse UPDATE/DELETE) | implemented |
| Pseudonymised actor (HMAC pepper), truncated IP | implemented: `audit/pseudonymisation.py` (IPv4 /24, IPv6 /48), `backend/tests/test_audit.py` |
| Every refusal and login redirect audited; viewing of non-public content one row per page, kept 90 days | implemented: `serving/router.py`, `backend/tests/test_serving.py`; vocabulary and retention periods in `docs/audit-log.md` |
| Audit failures do not block the action (fail-open on the log, fail-closed on the decision) | implemented: `audit/log.py`, `backend/tests/test_audit.py` |
| Logging in, failed callbacks and logging out audited (BIO2 5.17.01) | implemented: `platform/pages.py`, reasons from a closed set in `audit/vocabulary.py`; a failed attempt is always anonymous |
| Refused admin actions audited (BIO2 5.18.01) | implemented: one place, the `ApiError` handler in `api/errors.py`, so also for endpoints still to come. Route template in `refs`, never the path |
| Retention period and purging | implemented: 90 days for viewing, 3 years for the rest, enforced by a trigger; purging with `just purge-audit-log`, on the same `PLAK_DB_URL` as the app |
| Content-only SSO viewers traceable (`content_viewers`) | implemented: sso_subject, email address (may be `null`) with whether it was a verified claim, and last login updated on every content host login, kept 90 days by the same purge, with its own BEFORE DELETE trigger that enforces it; `auth/content_viewers.py`, `api/admin.py` (`actor-pseudonym`/`actor-identity`), `backend/tests/test_content_viewers.py`, `backend/tests/test_migrations.py` |
| Only a verified email address matches in the forward lookup | implemented: an unverified email claim of a content viewer does not count for `actor-pseudonym`; the SSO subject always matches, verified or not; `api/admin.py`, `backend/tests/test_audit_api.py` |
| Ambiguous identifier (email address without a unique constraint) recognised | implemented: an exact SSO subject always wins; if an email address matches more than one subject (members among themselves, or a member and a content viewer), the answer is 409 `IDENTIFIER_AMBIGUOUS` instead of an arbitrary first hit - this holds for the group and site member endpoints too, not just the audit lookup. The group and site member endpoints also resolve an identifier on an exact, exactly-one-active-member name (`_find_member_by_identifier`); the audit lookup (`_resolve_lookup_identifier`) deliberately does not, because a name is not something there that should let you guess someone's pseudonym; `api/admin.py`, `backend/tests/test_audit_api.py`, `backend/tests/test_admin_api.py` |
| Mandatory motivation and daily limit on tracing, counted atomically | implemented: `reason` (10-500 characters, no control or formatting characters, no `@`) mandatory and itself audited for `actor-pseudonym`, `actor-identity` and the IP disclosure, also on a 404 or 409; limit per platform administrator counted from the audit log itself (`PLAK_AUDIT_LOOKUP_DAILY_LIMIT`, default 25, 1-1000), so it holds up across a restart or multiple replicas. The counting and the writing of its own row happen in one transaction under a Postgres advisory lock on the administrator, so two concurrent requests cannot both see themselves as "still under the limit" (TOCTOU); `audit/log.py` (`write_strict_limited`), `backend/tests/test_audit.py` (concurrency test), `backend/tests/test_audit_api.py` |
| Platform administrator check as a FastAPI dependency on the audit log endpoints | implemented: runs before the body is validated, so a non-administrator always gets a 403 (and is audited), never a 422 that leaves the refusal unaudited; `api/admin.py` (`require_platform_admin`), `backend/tests/test_audit_api.py` |
| Full IP address encrypted separately (`ip_encrypted`), own key, bound to the row | implemented: AES-256-GCM under `PLAK_AUDIT_IP_KEY` (32 bytes, separate from `PLAK_AUDIT_PEPPER` and `PLAK_SESSION_SECRET`); the AAD contains the row id, so a ciphertext copied to another row does not decrypt there; decryptable only via `POST /platform/audit/entries/{id}/ip`, never via `GET /platform/audit`, and that disclosure itself also counts towards the daily limit; `audit/ip_crypto.py`, `backend/tests/test_ip_crypto.py`, `backend/tests/test_audit.py`, `backend/tests/test_audit_ip.py` |
| Key rotation for `PLAK_AUDIT_IP_KEY` | implemented: every encrypted value carries a key id (derived from the key, not a secret); `PLAK_AUDIT_IP_KEY_PREVIOUS` keeps the previous key around as long as older rows still have to be disclosable. Without that variable, rows from before the rotation become unreadable, while `ip_truncated` and the row itself are preserved; `config.py`, `audit/ip_crypto.py`, `backend/tests/test_audit_ip.py` |
| Chain head published outside the database | implemented: `just publish-audit-head` (`audit/checkpoint.py`) writes the last position and hash of every chain to the application log, `just verify-audit-head <bestand>` holds a line published earlier against the database. A shipped line cannot be retracted, so what the walk itself cannot catch - a rewrite with the chain recomputed, rows removed from the newest end, an oldest end that moved further than the retention period allows - shows up against it. Which check catches what is tabled in the audit log doc; the walk alone is not enough. It prevents nothing and proves nothing if nobody kept a line; `backend/tests/test_audit_checkpoint.py`, `docs/audit-log.md` |
| An IP address we cannot vouch for is marked as such | implemented: `refs.ip_unvouched` is `true` on a row whose address was derived over a skipped `X-Forwarded-For` entry, which with `PLAK_TRUSTED_PROXIES` as wide as all of RFC1918 is a value a privately addressed client can write itself. The address is stored unchanged and nothing is refused over it; `net.py` (`ClientAddress`), `audit/log.py`, `backend/tests/test_net.py`, `backend/tests/test_audit.py`. Disappears as a concern once the setting names the routers' own range |
| Database errors do not log bind parameters | implemented: `hide_parameters=True` on the SQLAlchemy engine of the app and of the purge job, so that a failed statement never puts a sub, email address or `reason` in the log; `db.py`, `audit/retention.py` |

## Database

| Item | Status |
|---|---|
| One shared PostgreSQL enum type `access_base`, a test guards the sync with the application constant | implemented |
| Access is a base plus two exceptions, not a single level | implemented: `sites.access_base` / `access_keys` / `access_invitees` (and the same three as group default and as preview override), `constants.py` (`AccessBase`, `AccessPolicy`), `access/gate.py`, `backend/tests/test_access_gate.py` (every base times every combination of the exceptions) |
| DB account separation (migration, runtime and purge account separate) | not done: the shared PostgreSQL service on ZAD hands out exactly one user, so the separation cannot exist in production. Local mirrors production: one account for app, alembic and purge job |
| Append-only audit log without account separation | the guarantee is the triggers from `0001_base`: `audit_log_no_update`, `audit_log_delete_after_retention` and `content_viewer_delete_after_retention`, proven by `backend/tests/test_migrations.py`. They apply to every session on that account, so to the app itself too. What is lost: the account is also schema owner and can disable the triggers, rewrite the functions, `TRUNCATE` or drop the table; see `docs/audit-log.md` |
| Audit rows authentic, not only immutable | implemented: `audit_log_chain` (a BEFORE INSERT trigger in `0001_base`) stamps `occurred_at` itself, so a caller cannot pick the moment its row claims, and hashes every row over its predecessor's hash plus its own length-prefixed content. Removing, rewriting or back-dating a row breaks the chain from there on. Sixteen chains instead of one, keyed on the row id, so the per-insert lock does not serialise every audit write in the application. `just verify-audit-log` (`plak/audit/chain.py`) walks the chains and names the first break in each; `backend/tests/test_audit_chain.py`. What is not covered: the schema owner can disable the trigger and recompute the whole chain, and rows dropped off the end of a chain leave nothing pointing at them. Only an off-host append-only sink (WORM or SIEM) closes that, and that decision is open; see `docs/audit-log.md` |

## Supply chain

| Item | Status |
|---|---|
| Base images pinned on digest | open: `containers/plak/Containerfile` (node 22, uv 0.5/python 3.12, python 3.12-slim-bookworm) pins on fixed tags; digests are a production prerequisite. `containers/nginx-dev/Containerfile` is only the dev proxy and falls outside this |
| CI actions pinned on commit SHA | done: every `uses:` in `ci.yml` and `deploy.yml` sits on a commit SHA, fixed by `test_workflows.py`. Handled on the user side: `docs/publishing.md` pins every `uses:` on a commit SHA, and the one nested `uses:` in `actions/publiceer/action.yml` (`astral-sh/setup-uv`) sits on a commit SHA too, fixed by `cli/tests/test_cli.py` |
| Lockfiles (uv.lock, package-lock.json) | implemented |
| Vulnerability scan on dependencies | done: the CI job `vulnerabilities` runs pip-audit on the exported lockfile and npm audit on the frontend, locally via `just scan`. Accepted findings sit in `.trivyignore.yaml` with a date and a motivation and come back by themselves on that date |
| SBOM per image | done: `deploy.yml` generates a CycloneDX SBOM with trivy and keeps it 90 days as an artefact |
| Updating dependencies | done: `.github/dependabot.yml` follows github-actions, uv, npm (frontend and e2e) and docker (both Containerfiles), weekly and grouped. A dependabot PR gets no preview environment but does go through the test gate |
| Production deploy behind the tests | done: `ci.yml` has become `workflow_call` and `deploy.yml` calls it; the job `productie` hangs on `needs: [ci, bouw]`. Open: branch protection with the checks `ci / backend`, `ci / frontend` and `ci / vulnerabilities` as soon as the repo has a remote |
| Image scan in CI | done: `deploy.yml` runs trivy twice on the built image, first a full report in the log and then the gate on CRITICAL and HIGH with `ignore-unfixed`. A red scan fails `bouw`, so nothing gets deployed |
| `security.txt` (RFC 9116) under `/.well-known/` | done: both hosts serve the same document from `platform/security_txt.py`, with a `Canonical` per https origin and an `Expires` that is set 90 days ahead per request. `test_security_txt.py` runs it through `sectxt`. Open: the GitHub advisory as the first `Contact` as soon as the repo has a remote |

## Production prerequisites (organisational)

| Item | Status |
|---|---|
| DPIA | planned, prerequisite for production go-live |
| Pentest | planned, prerequisite for production go-live |

### Account separation can come back on a database of our own

Plak runs on the *shared* PostgreSQL service of ZAD, which gives a project
exactly one user; there is no way to ask for a second one. The separate service
`namespace-postgresql-database` does offer `CREATEROLE` and `postInitSQL`. If
Plak moves to that, the separation (migration, runtime and purge account) can
come back, with `postInitSQL` as the place for the roles and their rights. Open
point, not a prerequisite.

## Pre-production / open points

### As soon as the repo has a GitHub remote

The repo has no remote yet. This is ready but waits on that; work through it on
the day it is there.

- [ ] **Turn on private vulnerability reporting** (Settings, Security). Without
  that, `/security/advisories/new` gives a 404.
- [ ] **Complete `security.txt`** in `backend/src/plak/platform/security_txt.py`:
  `Contact: https://github.com/minbzk/plak/security/advisories/new` as the
  *first* `Contact`, plus `Policy: https://github.com/minbzk/plak/blob/main/SECURITY.md`.
  After that, remove the block about the missing advisory line in `SECURITY.md`.
- [ ] **Branch protection on `main`** with the required checks `ci / backend`,
  `ci / frontend` and `ci / vulnerabilities`. Only then is the test gate in
  `deploy.yml` also closed for a direct push.
- [ ] **Go through the first run of `ci.yml` and `deploy.yml`.** They have never
  run; `actionlint` and `test_workflows.py` check the wiring, not the
  execution.
- [ ] **Check dependabot**: the first round of PRs has to come in for all four
  ecosystems (actions, uv, npm, docker), and a dependabot PR should not get a
  preview environment.

What still has to be filled in or built cluster-specifically before this can go
to production. The deploy itself is a declarative ZAD project file plus
zad-actions, no Kubernetes manifests.

**Content origin login (spec §4a) - implemented.** Protected content on
the content host redirects anonymously
to `/-/login?returnTo=<path>` on the same host; the segment `-` can
never be a slug, so `/-/` is the platform namespace there. The flow is
the same OIDC client as the admin login (PKCE, state, nonce, iss check,
`__Host-` login cookie), with redirect URI `{PLAK_CONTENT_BASE_URL}/-/oauth2/callback`;
that second redirect URI has to be registered at the IdP alongside
`{PLAK_BASE_URL}/-/oauth2/callback`. The outcome is a content session
(`__Host-plak-content`, HttpOnly, Secure, SameSite=Lax, `Path=/`, 12 hours,
no CSRF cookie) without admin authority: `session_from_request` (admin and
API) accepts kind `admin` only, content serving kind `content` only,
and a login attempt carries its kind so that a callback never redeems an
attempt of the other flow. A content login creates no member record (spec §7
"Kijken"). On the content host, host separation allows only `/-/login` and
`/-/oauth2/callback` from the `/-/` namespace; everything else there, the API
first of all, is the neutral 404. On the admin host the same spelling exists
for the admin flow; the host decides which of the two you get. The two E2E
scenarios that were waiting on this (group member via `_version`, invitee with
a deep link) now run without a skip.

**Public front page on the root of the content host.**
Without it, `/` would be the neutral 404 there and anyone who heard the name of
the service and typed the address would hit a dead end. That root carries a
server-rendered front
page (`platform/pages.py`, `front_page_html`): what Plak is, where the name
comes from, a button to the admin host and the footer with Over Plak,
Toegankelijkheid, Privacy and the API documentation. The page carries **no
authority whatsoever**, because this is the origin on which uploaded sites run
their own JavaScript (see `api/origin_guard.py`): no script, no
session reading, no API call, no CSRF token, no SPA. Its own CSS sits
inline (the content CSP allows `style-src 'unsafe-inline'`; a separate file
would require a route under `/-/` and host separation keeps exactly that away
here), and every link is absolute to the admin origin from
`PLAK_BASE_URL`, never from the `Host` header. Without that setting the root
stays the neutral 404, because then there is no address to point to. The rest
of the content host does not change: a refusal stays the byte-identical
neutral 404, and the front page betrays nothing, because it always exists and
sits at a fixed address. A side benefit: the accessibility statement has to be
legally reachable and only sat on the admin host, behind an address that nobody
guesses; this footer is the first place where it is publicly findable.
Tests: `backend/tests/test_front_page.py`.

**Secret link: redeem and redirect.** A valid `?key=`
is redeemed: the app sets the `__Secure-plak-key` cookie and answers with
a 302 (`Cache-Control: no-store`) to the same URL without the
`key` parameter (other query parameters stay); the cookie route serves
after that. The cookie carries the id of the link, signed with
`PLAK_SESSION_SECRET`: a bare id is not proof of access, because it appears
nowhere as a secret. An invalid key or cookie stays the neutral 404.
The audit record carries the path and the selector of the link, never the
query string, and a stray `key` does not travel along
in `returnTo` either. The access log of uvicorn logs path plus query string and
is therefore off (`--no-access-log` in `containers/plak/Containerfile` and in
`just dev`); the audit log and the app logging stay. A reverse proxy or
ingress router in front of the app that logs request lines (the dev nginx does)
does still see the key; on ZAD that is a question for the platform
administrators (router logging and Loki retention). Keys
always expire, by default after 90 days and after 365 at most (`expiry.py`). A
rotation of `PLAK_SESSION_SECRET` invalidates every existing key cookie:
visitors with such a cookie have to open their link again. Still open:
`X-Robots-Tag: noindex` on key content.

**Secret link without a code.** The same link can be shared in two
ways. In full (`?key=selector.verifier`): whoever has the link can view.
Without the code (`?key=selector`): the link goes via one channel, the code via
the other. A selector alone yields a small page on the content host
that asks for the code; after the right code the visitor gets exactly the same
`__Secure-plak-key` cookie as with a full link.

The code page appears only if the selector belongs to an existing,
usable key of that site. The neutral 404 remains the answer for: an
unknown or wrong selector, a revoked or expired key, a
key of another site, a site that is not set to "secret link", a
site without a live version, an unknown site or group, and a preview. Otherwise
the page itself would betray which selectors exist. The page names no
site title and no group name: only that a code is needed, an input field,
a button and an error line. It carries the same headers as protected content
(`no-store`, `noindex`, the `CONTENT_CSP`, `Referrer-Policy: same-origin`).
That policy is not `no-referrer`, because under `no-referrer` Chrome anonymises
the Origin of the form navigation to `Origin: null`, which would cost the origin
guard its teeth. The page URL carries only the selector, never the verifier, so
a same-origin `Referer` to our own POST target leaks nothing.

The code goes with a POST to `/-/code` (the only POST path on the
content host), with the selector and the intended path in the body; the path
is validated as a returnTo (own origin, no scheme, host or
backslash). An anonymous visitor has no session and therefore no CSRF token;
instead: the same origin only (`Origin`, otherwise, and when it is the literal
`null` of an anonymised form navigation, `Sec-Fetch-Site`),
only `application/x-www-form-urlencoded` and at most 4 KiB, and the fact that
a successful POST only sets a cookie for a key whose code the caller
already knew. Everything that falls outside that is the neutral 404.

Limits: ten attempts per quarter of an hour per client IP (rate limit class
`code`, `PLAK_RATELIMIT_CODE_*`) and ten per quarter of an hour per selector,
counted in the route itself. Above the limit the same answer comes as with a
wrong code, with an extra line that it has to be tried again later. That
betrays nothing: a selector that does not exist is counted too and therefore
runs into the limit just as well, so "blocked" says nothing about existence.
The comparison runs via the same constant-time path as `keys.verify`, with a
dummy hash for an unknown selector, an unknown site and a blocked attempt, so
that a wrong selector costs as much as a wrong code.

**Audit scope.** Viewing of non-public content goes into the log, one row per
page, kept 90 days, viewable only by a platform administrator, and every
access is itself recorded. See
`docs/audit-log.md` for the vocabulary, the retention periods and the access.

**The audit vocabulary is English.** The actions, results and reference keys
in `audit_log_entries` (`content_access`, `allowed`, `refused`, `group`,
`member_id`) are English. The table is append-only, which constrains any
future rename: existing rows cannot be rewritten, so a rename after rows
exist leaves two vocabularies in the table, and for `actor_kind`, a
PostgreSQL enum, a value rename rewrites the type but not the rows already
written with it. The schema starts from one base migration (`0001_base`), so
an environment that migrates from zero knows only the vocabulary as it
stands today.

Other points:

- `PLAK_TRUSTED_PROXIES` has to contain the CIDR of the HAProxy router pods on
  ZAD (the router adds `X-Forwarded-For` in append mode; the app
  takes the last untrusted hop), otherwise the
  client IP derivation ends up on the router IP. The range can be read off in
  production from the direct peer address the app sees. As long as the setting
  is wider than that (today: all of RFC1918), a privately addressed client can
  write its own address into the header and have it stick; such a row is marked
  with `refs.ip_unvouched` (`docs/audit-log.md`), and naming the real range
  makes that concern go away.
- `/healthz` exists only internally (spec §4a/§11): on both public hosts
  `host_separation.py` gives the path a neutral 404, and a probe with a
  different `Host` already strands on `TrustedHostMiddleware` before that. The
  ZAD project file (round 3) therefore has to let the probe in past both
  (a separate port or an exception of its own); that is still to be
  built.
- The deploy pipeline is still missing: ZAD project file plus zad-actions, with
  `alembic upgrade head` at container start or via a bootstrap action.
- No metrics endpoint: Haven expects a scrapable metrics endpoint;
  still to be built (and then, like `/healthz`, not routed publicly).
