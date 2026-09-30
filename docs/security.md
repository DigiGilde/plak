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
| Alg allowlist (RS256/PS256/ES256, no `none`/HS*) as our own code on top of joserfc | implemented: `auth/oidc.py` (`ALG_ALLOWLIST`), `backend/tests/test_oidc.py` |
| RFC 9207 `iss` check on the callback query parameters, our own code on top of joserfc | implemented: `auth/oidc.py` (`check_callback_iss`), `backend/tests/test_oidc.py` |
| `acr` from a configured list (`PLAK_OIDC_REQUIRED_ACR`); empty = no acr check and no `acr_values`, with a startup warning in production. Adjusted because the ZAD Keycloak only supplies `acr` `0`/`1` | implemented: `auth/oidc.py`, `backend/tests/test_oidc.py`; see `docs/local-development.md`, OIDC configuration |
| Client authentication `private_key_jwt` (default) or `client_secret_post`/`client_secret_basic` via `PLAK_OIDC_CLIENT_AUTH`; exactly the matching secrets required, error messages without secret values. `client_secret_*` deviates from the NL GOV OIDC profile (private_key_jwt or mTLS) and exists for the ZAD Keycloak, which only creates client-secret clients; the ZAD variables `OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` are the source for the `PLAK_OIDC_*` settings | implemented: `config.py`, `auth/oidc.py`, `backend/tests/test_config.py`, `backend/tests/test_oidc.py` |
| `at_hash` validation when present in the id token | implemented: `auth/oidc.py`, `backend/tests/test_oidc.py` |
| `__Host-` session cookie: HttpOnly, Secure, `Path=/`, SameSite=Strict (spec §7; only the content session differs, and it is `__Secure-` with a path per site and SameSite=None, see the row below and "Why the content cookies are SameSite=None") | implemented: `auth/sessions.py`, `backend/tests/test_sessions.py` |
| Content session (SameSite=None, see below) separate from the admin session; admin routes refuse content sessions, content serving refuses admin sessions | implemented: `auth/sessions.py` (`SessionKind`), `platform/pages.py`, `backend/tests/test_sessions.py` |
| Content session cookie scoped per site (`__Secure-plak-content`, `Path=/{group}/{site}/`), with an anchor under `/-/`. One session behind both, so revocation, the 12-hour age and the kind check are unchanged. It narrows what one session reaches: a site this browser has not opened carries no cookie at all. It does not make the site boundary browser-enforced, because a cookie path is matched against the requested URL and not against the page that asks: a fetch aimed at a site the visitor has already opened still carries that site's cookie. What enforces the boundary is the origin per site; what catches the rest is the subresource check. `__Host-` had to go because that prefix requires `Path=/`; what it also forbade (a `Domain` attribute) is given up only for this cookie, never for the admin session | implemented: `auth/sessions.py`, `platform/pages.py`, `serving/router.py`, `backend/tests/test_sessions.py`, `backend/tests/test_serving.py` |
| Session id rotation on login | implemented: `platform/pages.py` (an existing session of the same kind expires at the callback), `backend/tests/test_sessions.py` |
| Logging out on the admin host exclusively via POST, and only from the admin origin itself (`admin_origin_ok`). POST alone is no guard there: the content host is same-site with the admin host, so `SameSite=Strict` still sends the session cookie along with a form on a published site. The content leg keeps accepting GET, for the reason in the row below | implemented: `platform/pages.py`, `api/origin_guard.py`, `backend/tests/test_sessions.py` |
| Logging out ends both sessions: the admin logout redirects to `/-/logout` on the content host, which clears the content session and sends the visitor back. That step accepts GET, because it arrives via a 303; making someone log out is the most harmless forgery there is. `?from=` is a fixed word, not an address | implemented: `platform/pages.py`, `host_separation.py`, `backend/tests/test_sessions.py`. That is why the admin CSP allows the content origin in `form-action` |
| RP-initiated logout at the IdP (OIDC RP-Initiated Logout 1.0), with `id_token_hint` | implemented behind `PLAK_OIDC_RP_LOGOUT`, off by default: the `post_logout_redirect_uri` is `{PLAK_CONTENT_BASE_URL}/-/logout?from=beheer` and must be registered at the IdP, otherwise the visitor lands on an error page of the IdP. With the flag on, `form-action` also allows the issuer origin |
| Periodic re-validation of a session at the IdP: on the first request after `PLAK_IDP_RECHECK_SECONDS` (default 900, `0` turns it off) the session's refresh token goes to the token endpoint (`grant_type=refresh_token`, same client authentication as the login). If that succeeds the session stays, the rotated refresh token is stored and the clock restarts; the `sub` of a new id token must equal that of the session. If the IdP refuses with `invalid_grant` (blocked, deleted, session ended there), the Plak session expires and the request continues as not logged in. An outage (network, timeout, 5xx) leaves the session in place, does not block the request and is retried after a one-minute backoff; that goes to the application log, not the audit log. If the token endpoint refuses our **client** instead of the session (`invalid_client`, `unauthorized_client`, `unsupported_grant_type`, `invalid_request`, `invalid_scope`), everyone stays logged in - a broken integration must not log the whole platform out - but it gets loud: an ERROR line with the error code and the issuer, at most one per backoff window, and `/healthz` answers `{"status": "degraded", "idp_revalidation": "IdP re-validation is failing: <code>"}` until a successful re-validation clears the complaint. `/healthz` still returns 200 while it does: the probe keeps the pod alive and a broken IdP integration is no reason to restart | implemented: `auth/revalidation.py`, `auth/oidc.py` (`refresh_tokens`), `main.py` (`SessionRecheckMiddleware`), `backend/tests/test_session_revalidation.py`. The window in which someone blocked at the IdP can still get in is thereby at most the interval instead of the full `MAX_SESSION_AGE` of 12 hours |
| The refresh token sits with the session in process memory: never in a log, never in an audit ref, never to the browser. The field has `repr=False`, so it does not end up in a dataclass dump in a log line or traceback either | implemented: `auth/sessions.py`, `backend/tests/test_session_revalidation.py` |
| Back-channel logout (OIDC Back-Channel Logout 1.0) on `POST /-/oidc/backchannel-logout`, only on the admin host. The endpoint is unauthenticated and its rate limit falls into the `content` class (keyed on client IP, see the row below), so a cheap, unverified pre-check runs first: token size, JWT shape, `typ` (untyped or `JWT`/`logout+jwt`, since Keycloak still mints plain `JWT`; see [keycloak/keycloak#28939](https://github.com/keycloak/keycloak/issues/28939)) and `alg` from the allowlist, `iss`, a recent `iat`, a non-empty `jti`, no `nonce`, the `events` claim, and a `jti` already in the replay cache - all read straight off the unverified JWT, so this can only narrow what reaches a JWKS fetch and a signature verification, never stand in for it. Only after that does the full check run: the signature via the existing JWKS handling, `iss`, `aud`/`azp`, a recent `iat`, an `exp` that has not passed, the claim `events` with `http://schemas.openid.net/event/backchannel-logout`, the presence of `sid` or `sub`, the absence of a `nonce`, and a `jti` (required, like `exp`, by section 2.4; a token without either is refused) that did not already pass by shortly before (replay cache in process memory). After that the sessions the token is about expire (on `sid`, otherwise all sessions of that `sub`; both session kinds). The answer is always a bare status with `Cache-Control: no-store`, byte-identical whether the pre-check or the full check refused, and does not betray whether there was a session | implemented: `platform/backchannel.py`, `auth/oidc.py` (`validate_logout_token`, `logout_token_prefilter`), `backend/tests/test_backchannel_logout.py`, `backend/tests/test_oidc.py`. The Keycloak client on ZAD still has to be configured for it (`docs/deploying-on-zad.md` §6a); until then the endpoint exists but nobody calls it |
| CLI sessions (`plak login`) are **not** re-validated at the IdP | deliberate, open point: a CLI session arises from a device code that a member approved in an admin session, and gets no IdP token of its own. The refresh token of that admin session sits in process memory and is long gone by the next CLI refresh (up to 30 days later, 90 absolute), so there is nothing to check with. Someone blocked at the IdP therefore keeps their CLI session until it expires or is revoked; deactivating in Plak does work immediately, because member status is checked per refresh and per deploy. Closing this requires storing the IdP refresh token of the approving admin session with the CLI session in the database, and thereby token material at rest that currently exists nowhere: that requires encryption and a decision of its own |
| Sessions do not survive a restart and cannot be revoked centrally | deliberate: the store sits in process memory and ZAD runs one replica. Deactivating works immediately anyway, because member status is checked per request (`require_active_member`, `_belongs_to_site`); revoking sessions would replace the message "je toegang is ingetrokken" with "je bent niet meer ingelogd" |
| `returnTo` strictly validated (own origin only, paths only) | implemented: `auth/sessions.py` (`valid_return_to`), `backend/tests/test_sessions.py` |

## Session lifetime and logout

A summary of the session and logout rows above, in one place, with the
platform's own timeouts alongside.

- **Plak admin and content sessions** last at most `MAX_SESSION_AGE`, 12 hours
  after login, with no idle timeout of its own (`auth/sessions.py`). They live
  in the process memory of a single replica, so a restart or a redeploy ends
  every session at once.
- **Re-validation at Keycloak** runs on the first request after
  `PLAK_IDP_RECHECK_SECONDS` (default 900 seconds) since the session was last
  checked; an `invalid_grant` from the token endpoint ends the Plak session
  (`auth/revalidation.py`).
- **The ZAD Keycloak's own SSO session limits**, as set by the platform
  (RijksICTGilde/RIG-Cluster, `operations-manager/python/opi/configs/keycloak/bootstrap.yaml`):
  `ssoSessionIdleTimeout` 28800 seconds (8 hours), `ssoSessionMaxLifespan` 43200
  seconds (12 hours), explicitly to keep "a working day in one session" instead
  of Keycloak's own defaults (30 minutes idle, 10 hours max). These values come
  from the platform's own source, not from a measurement against our realm.
  `connectors/keycloak.py` applies `create_realm`/`update_realm` with the same
  session settings to every realm it manages, platform and per-project alike,
  so the project realm should carry the same values, but this was not
  independently confirmed against the live realm.

What ends a session, and when:

| Event | Effect |
|---|---|
| Logging out in Plak | Ends the admin and the content session at once (`platform/pages.py`, see the logout rows above). The Keycloak session itself stays unless `PLAK_OIDC_RP_LOGOUT` is on, so the next login may be silent (an existing SSO session). CLI sessions (`plak login`) are untouched and are revoked separately, on the linked-CLI-sessions page |
| Logging out at Keycloak, or out of another app in the same realm | At once, if back-channel logout is configured on the client (§6a of `docs/deploying-on-zad.md`); otherwise at the next re-validation, at most `PLAK_IDP_RECHECK_SECONDS` later |
| Account disabled or deleted in the ZAD realm | At the next re-validation (`invalid_grant`) |
| Account closed at SSO Rijk | Does **not** reach the realm: the ZAD realm brokers SSO Rijk over SAML and imports the user locally, and the refresh grant reads only that local `enabled` flag, never SSO Rijk itself. A new login fails, but an existing session lasts until the 12-hour cap. Same realm setup as Waggle, which raised the same gap with the platform team (`docs/security.md` commit `87fd3750` there; see also [RijksICTGilde/RIG-Cluster#173](https://github.com/RijksICTGilde/RIG-Cluster/issues/173)) |
| Member deactivated in Plak (`_deactivate`) | Immediate: `require_active_member` checks `member.status` on every request. Also revokes every CLI session of that member at once (`auth/members.py`, `api/admin.py`) |

**CLI sessions** (`plak login`) follow a schedule of their own, unrelated to
the admin/content sessions above: 30 days after the last refresh, and 90 days
after linking at the latest, whichever comes first (`REFRESH_IDLE_TTL`,
`SESSION_MAX_TTL`, `cli/service.py`). They are not re-validated at the IdP;
see the CLI row above.

## Access to content

| Item | Status |
|---|---|
| Neutral 404s, byte-identical on refusal and on non-existence | implemented: `serving/response.py` (`neutral_404_response`, the single construction point), `backend/tests/test_access_gate.py`, `backend/tests/test_serving.py` |
| Constant-time comparison of secret link verifiers | implemented: `access/keys.py`, `backend/tests/test_keys.py` |
| Constant-time comparison of CLI tokens (device code, access and refresh token): selector plus SHA-256 hash, with a dummy hash for an unknown selector so that an unknown selector takes as long as a wrong secret | implemented: `cli/service.py` (`matches`, `_DUMMY_HASH`), `backend/tests/test_cli_service.py` |
| `__Secure-` key cookie, HttpOnly, SameSite=None (see "Why the content cookies are SameSite=None"), path exactly on site/preview | implemented: `auth/sessions.py` (`KEY_COOKIE`), `serving/router.py`, `backend/tests/test_serving.py` |
| Secret link without a code: `?key=selector` only shows a code page for a usable key of that site, everything beyond that stays the neutral 404; the code goes in the body of a POST, never in the URL | implemented: `serving/code_page.py`, `access/gate.py` (`code_page_needed`), `access/keys.py` (`verify_parts`, `selector_usable`, `compare_dummy`), `backend/tests/test_code_page.py` |
| Secret link: `?key=` is redeemed (cookie plus 302 to the URL without `key`); key never in audit, returnTo or access log | implemented: `serving/router.py`, `containers/plak/Containerfile` and `justfile` (`--no-access-log`), `backend/tests/test_serving.py` |
| Expired preview gets the neutral 404 straight away at the access decision itself (not only via the purge job) | implemented: `access/gate.py` (`REASON_PREVIEW_EXPIRED`), `backend/tests/test_access_gate.py` |
| Anonymous top-level navigation to a preview or `_version` view: the same login redirect whatever lies behind the path, so it betrays no existence of non-public content (a public preview is served, as it always was); not for a subresource, not with a key in play, not for a path of non-slugs (the login could scope no site cookie there, so it would loop) | implemented: `serving/router.py`, `backend/tests/test_serving.py` (`TestFirstVisitToPreviewOrVersion`) |
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
| Bearer tokens accepted exclusively on the two deploy/preview endpoints, the CLI session endpoints and three endpoints of the admin API that take the CLI token only: the two creation endpoints (`POST /groups`, `POST /groups/{group}/sites`) and linking a repository (`PUT /sites/{group}/{site}/repository`, so `plak site link`; unlinking stays session only); the allowlist is method plus exact path shape, and every other admin route answers a bearer with 401 `BEARER_NOT_ACCEPTED` before it is routed, also when a valid session rides along | implemented: `api/deploys.py` (`BearerOutsideDeploysMiddleware`, `accepts_bearer`), `api/cli.py`, `backend/tests/test_deploy_api.py`, `backend/tests/test_cli_api.py`, `backend/tests/test_cli_creation.py` (every admin route with a valid CLI token), `backend/tests/test_api_documentation.py` (OpenAPI and middleware agree per operation) |
| Creating a group or a site from the CLI (`plak group create`, `plak site create`): only a member's CLI token from `plak login`, never a CI ID token (401 `TOKEN_INVALID`), with the same role checks as the session (any active member for a group, group role `editor` for a site). The request may carry an initial access; that is no way around the access policy, because the creator becomes group admin or site admin and may set that access on the next request anyway. The CLI may not delete anything or manage members: those routes stay session only. A bearer request carries no ambient credentials, so it skips the CSRF double submit; the origin guard stays on it, as on the deploy router, so script on the content host holding a token is still refused. Group and site creation are audited with `refs.via` `cli` and the CLI session id, and with the access the new group or site starts with | implemented: `api/admin.py` (`require_creator`), `api/errors.py` (refusals audited as the CLI member), `backend/tests/test_cli_creation.py`, `backend/tests/test_api_integration.py` |
| Secret links: always an expiry date, 90 days by default, 365 at most (BIO2 5.18.02); `expires_at` is NOT NULL | implemented, expiry date mandatory: `expiry.py`, `backend/tests/test_expiry.py`, `test_keys.py` |
| CI id token (OIDC "trusted publishing"): issuer allowlist (GitHub fixed, Forgejo per host in `PLAK_CI_FORGEJO_HOSTS`), keys via discovery/JWKS of the issuer origin itself (https, 5s timeout, size limit, no redirects; cached for an hour, one fetch at a time per issuer, no retry for 30 seconds after a failed fetch), RS256 only, `exp`/`iat` mandatory with 60s leeway, `aud` must be exactly `PLAK_BASE_URL` | implemented: `ci/providers.py`, `ci/tokens.py`, `config.py`, `backend/tests/test_ci_providers.py`, `test_ci_tokens.py` |
| CI repository trust: a site links exactly one repository (`PUT/DELETE /sites/{group}/{site}/repository`, site role admin, without a secret; the ids come from an anonymous lookup, or, for a private repository, from the admin, and a found repository must match entered ids); a token matches on `repository_id` (and `repository_owner_id` if present) or, without ids (Forgejo 15), on `eigenaar/repo` with a re-confirmation against the Forgejo API (cached for 5 minutes); live publishing only from `push`, `workflow_dispatch` or `schedule` (fail closed without `event_name`) and, if it is set, only from `liveBranch`; previews and cleanup from any event and from any branch | implemented: `ci/trust.py`, `api/admin.py` ("Linked repository"), `api/deploys.py`, `backend/tests/test_ci_trust.py`, `test_admin_api.py`, `test_deploy_api.py` |
| CLI device flow (`plak login`, RFC 8628): secrets as selector + SHA-256 hash, compared constant-time; the refresh token rotates on every use, reuse of an already used refresh token revokes the whole session (`cli_refresh_reuse`), except for the just-replaced token within ten seconds (concurrent refresh: only `INVALID_GRANT`); logging out also works with an expired access token or with the refresh token and always answers 204 (no oracle); deactivating a member revokes all their CLI sessions; the rate limit counts Bearer requests per IP, never per (unproven) token; access token 1 hour, session 30 days after the last refresh and at most 90 days after linking; approving requires an admin session no older than fifteen minutes that was itself started from the admin origin (`self_initiated`, recorded on `/-/login` as `admin_origin_ok`; a login another site navigated the browser into never counts as fresh, because the IdP returns without a prompt on an existing SSO session). The backend cannot tell a script on an admin page that sends the browser to `/-/login` from a member's own click (both arrive as `Sec-Fetch-Site: same-origin`), so the SPA never navigates to the login by script: `/cli-link` without a (fresh) session shows a sign-in link the member follows themselves, back to the bare `/cli-link` without the `?code` from the link, and the member then types the code their own terminal shows; `frontend/tests/login-navigation.test.ts` fails on any script navigation to the login route. Requiring `Sec-Fetch-User: ?1` on that login was evaluated and rejected: WebKit never sends the header, not even on a real link click (WebKit bug 247697; MDN browser-compat-data lists Safari as unsupported), so no Safari member could ever approve. A stronger option for later is a real re-authentication at the IdP for the approval (`prompt=login` or `max_age`, plus an `auth_time` check on the ID token), still to be verified against the ZAD Keycloak. Approving further requires a valid CSRF header and only goes via the admin origin; the approval screen shows the account being linked to, the program, the time and the truncated network, and warns separately if the request comes from a different truncated network than the approver (`sameNetwork`, without full IP addresses) and warns explicitly against a shared link or code (anti-phishing) | implemented: `cli/service.py`, `api/cli.py`, `api/admin.py` ("CLI login: the member's side"), `frontend/src/pages/CliKoppelen.vue`, `frontend/tests/login-navigation.test.ts`, `backend/tests/test_cli_service.py`, `test_cli_api.py`, `test_admin_api.py`, `test_sessions.py` (`TestLoginOrigin`) |
| CLI session at rest (`plak login`): per user account, not per directory. The tokens go into the system keyring through the `keyring` library (macOS Keychain, Secret Service on Linux), one entry per host under the service `plak:<host>`; `hosts.json` in the config directory (`$PLAK_CONFIG_DIR`, else `$XDG_CONFIG_HOME/plak`, else `~/.config/plak`) holds the default host, the expiry and where the tokens live. A backend the library does not recommend (the null backend `PYTHON_KEYRING_BACKEND` can select, which drops a secret silently, or a plain-text one) counts as no keyring. Without a usable keyring, or with `--insecure-storage`, the tokens go into that file in plain text and `plak login` says so; a session that moves out of the keyring on a refresh says so too, and one that fell back returns to the keyring on its next refresh. The file is written 0600 through a temp file and an atomic replace, and is only read when it is a regular file of the user's own with mode 0600, not behind a symlink, checked on the opened descriptor, in a directory of the user's own that nobody else can write to: its default host decides where a `PLAK_ACCESS_TOKEN` or CI ID token goes, and a looser file could hold a planted session. A session is only ever sent to the host it was issued for. It is stored as JSON, so a token the server chose cannot add a key of its own. A `.env.plak` from an earlier version is never read, only named | implemented: `cli/plak_cli/__init__.py`, `cli/tests/test_cli.py` |
| Authorization server-side per endpoint, never only in the SPA | implemented: `api/admin.py`, `backend/tests/test_admin_api.py` |
| `application/problem+json` error contract with fixed status codes | implemented: `api/errors.py`, `backend/tests/test_api_integration.py` |

## Headers and CSP

| Item | Status |
|---|---|
| Content CSP (§5.7 regime) on all content routes | implemented: `serving/response.py` (`CONTENT_CSP`, on the neutral 404 too), `backend/tests/test_serving.py` |
| External sources per site (on by default) | implemented: `sites.external_sources`, `serving/response.py` (`CONTENT_CSP_EXTERNAL`), `api/admin.py` (`PUT /sites/{group}/{site}/external-sources`, site role admin, CSRF, audit action `site_external_sources`), `frontend/src/components/site/TabAccess.vue`, `backend/tests/test_security_headers.py`, `test_serving.py`, `test_admin_api.py`. See "External sources" below |
| Shielding from other sites per site (on by default) | implemented: `sites.sandbox`, `serving/response.py` (`SANDBOX`, `CONTENT_CSP_SANDBOX`), `api/admin.py` (`PUT /sites/{group}/{site}/sandbox`, site role admin, CSRF, audit action `site_sandbox`), `frontend/src/components/site/TabAccess.vue`, `backend/tests/test_security_headers.py`, `test_serving.py`, `test_admin_api.py`. See "Shielding from other sites" below |
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

The per-site sandbox reduces that substantially: a sandboxed document cannot
read another site's content or storage, so a compromised script is limited to
the page it ran in. It is not the whole answer. It does not stop the
CDN seeing every visitor of every site that loads from it, it does not stop the
script doing whatever that one page does, and it holds only for the documents
that are sandboxed.

Whoever cannot accept that turns the switch off per site and bundles the
library into the dist; for confidential pages that is the advice, in those
words, in the interface.

### Shielding from other sites

Every site of every group is served from one hostname, and published content
may run its own JavaScript. Without a countermeasure the pages of site A are
same-origin with the pages of site B: a script on A can fetch B's paths on the
visitor's authority and read the answer, open B in a window and read the
document, or overwrite the cookies of the shared origin. That was reproduced
end to end, not derived on paper. Giving every site its own origin is not
available to this project, so the equivalent has to come out of the response.

The content CSP therefore carries, per site and on by default
(`sites.sandbox`, server default true):

```
sandbox allow-scripts allow-forms allow-popups
```

The load-bearing part is what is *not* in that list: without `allow-same-origin`
the browser gives the document an opaque origin. It is then same-origin with
nothing at all, so it can read no other document on this hostname and can
neither read nor write storage. Scripts, forms and popups stay allowed, so an
ordinary page keeps working.

Verified in a browser against the dev stack, with and without the directive.
With the sandbox the page's own stylesheet, script and image load normally,
`window.origin` reads `null`, `localStorage`, `sessionStorage` and
`document.cookie` throw a `SecurityError`, a `fetch` of another site's path
fails instead of returning that site's HTML, and a window opened on another
site's path cannot be read by the opener. Without it, the same page reads the
other site's page in full.

The price is real and falls on the publisher: no `localStorage`, no
`sessionStorage`, no `document.cookie`, and nothing the browser fetches in
CORS mode. That last one is measured, not derived: from an opaque origin a web
font, a module script and a `fetch` of the site's own files go out with
`Origin: null` and no cookie, and Plak sets no CORS headers, so the browser
refuses them. A site that stores anything in the browser, or serves its own
web font, or loads ES modules, stops working until its owner turns the
shielding off, which is why the switch and its explanation sit next to the
external-sources switch on the site's access screen, in the words a publisher
uses ("een onthouden voorkeur, een half ingevuld formulier").

What this does and does not buy, stated plainly. It protects the visitors of
*other* sites against this one: content published here cannot reach across to
what that visitor may see elsewhere on the hostname. It is per site, so a site
whose owner turns the shielding off is back in the shared-origin situation, for
its own visitors and towards every other site, and the platform as a whole is
not isolated by this. It is a default that most sites can keep, not a boundary
the platform can enforce, and it does not replace the access gate: a page that
is refused is still refused.

The same two exceptions as for external sources apply. The neutral 404 keeps
the plain `CONTENT_CSP` whatever a site sets, because it has to stay
byte-identical across every cause of refusal. The code page for a secret link
keeps it too: it is a page of the platform itself.

### Why the content cookies are SameSite=None

The shielding above has a consequence the first version of it missed. An
opaque origin is cross-site with *everything*, the document's own site
included. Measured in a browser: the subresource requests a sandboxed page
makes for its own stylesheet, script and image arrive with
`Sec-Fetch-Site: cross-site`, without an `Origin` and without a `Referer`,
whatever `Referrer-Policy` the response carries. Under `SameSite=Lax` the
browser therefore withholds the content session cookie there, and every
non-public site was served as a page without a single one of its own assets,
while public sites were unaffected because they need no cookie.

There is no request signal that separates such a load from a third party's,
so the site cookie (`__Secure-plak-content`) and the secret-link cookie
(`__Secure-plak-key`) are `SameSite=None; Secure`. The anchor stays `Lax`: it
is only ever read on a top-level navigation, which Lax covers, and it is the
cookie that can mint a site cookie for the next site.

What that opens, shape by shape, for a site this browser has a cookie for:

- **Top-level navigation** from another site: unchanged. Lax already sent the
  cookie on a top-level GET navigation, and the visitor sees where they land.
- **`fetch`/XHR**: the request goes out with the cookie, the answer is
  unreadable. Reading a cross-origin response needs CORS, and Plak sets no
  CORS headers anywhere; `no-cors` yields an opaque response. So the pages
  themselves, which is where the content is, stay out of reach.
- **`<iframe>`**: `frame-ancestors 'none'` on every content response, the
  neutral 404 included, so the browser refuses to render either and the two
  fail identically.
- **`<img>`**: a real image of the site loads. The including page cannot read
  its pixels (the canvas is tainted) but does learn that it exists and what
  its intrinsic dimensions are, for a path it guessed.
- **`<link rel=stylesheet>`**: the site's stylesheet loads and applies.
  `cssRules` throws a `SecurityError`, but the effect is observable through
  `getComputedStyle`, so a private site's CSS has to be treated as readable by
  a page that knows its URL.
- **`<script src>`**: a real script asset of the site executes in the
  including page. `X-Content-Type-Options: nosniff` keeps HTML pages and the
  neutral 404 from executing, but whatever a non-public site puts in a `.js`
  file is then readable through its own globals. Data does not belong in an
  asset of a non-public site.
- **Mutations**: none. The content origin has no session-borne mutation and
  therefore no CSRF cookie. Its only POST is `/-/code`, which is anonymous and
  guarded by its own origin check (`Origin`, falling back to
  `Sec-Fetch-Site`), so a form on another site fails there. The content logout
  is a GET and deliberately forgeable.

Accepted, in short: a third-party page can have a visited site's assets loaded
with the visitor's credentials and observe their effect, but not read its
pages. The alternatives are worse. Not sandboxing non-public sites inverts the
model, since those are the sites with something to protect and any published
page can attack them. Serving assets of a non-public site without credentials
is the access gate with a hole in it. A separate cookie for the sandboxed case
only would be attachable by a third party in exactly the same way, so it buys
nothing but a second thing to revoke. `Cross-Origin-Resource-Policy:
same-site` was tried and rejected on measurement: an opaque origin is not
same-site, so it blocks the page's own assets as thoroughly as a stranger's.
`_foreign_subresource` cannot help here either; it only looks at same-origin
requests, and a cross-site rule would have to allow requests without a
`Referer`, which is exactly what a third party can arrange.

Browsers that block or partition third-party cookies are the residual risk in
the other direction: where a browser treats the opaque origin as a third party
to the site, it withholds the cookie again and the assets of a non-public
sandboxed site do not load. Verified working in Chromium; the durable fix for
both sides is an origin per site, which makes the sandbox and this exception
unnecessary at once.

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
| Trusted proxy configuration mandatory in production mode (startup refused without it) | implemented: `config.py` (`PLAK_BEHIND_PROXY` mandatory with `PLAK_ENVIRONMENT=productie`; both answers are valid, so unset cannot be one), `net.py`, `backend/tests/test_config.py` |
| Code of a secret link: its own class `code`, ten attempts per quarter of an hour per client IP and on top of that ten per quarter of an hour per selector | implemented: `ratelimit.py` (`RateLimitClass.CODE`), `serving/code_page.py`, `config.py` (`PLAK_RATELIMIT_CODE_*`), `backend/tests/test_code_page.py`, `backend/tests/test_ratelimit.py` |
| 429 with `Retry-After`, no escalating penalties | implemented: `ratelimit.py`, `backend/tests/test_ratelimit.py` |
| Creation budget: per member at most 20 new groups and sites together per hour, through the admin and the CLI together; every attempt that passes the role check and the input validation counts, a slug collision included. Over budget: 429 `TOO_MANY_CREATIONS` with `Retry-After`, audited as `admin_access`/`refused`. It bounds what a stolen CLI token can litter the platform with; the counter lives in process memory, like the other per-member limits | implemented: `api/admin.py` (`_require_creation_budget`), `backend/tests/test_cli_creation.py` (`TestCreationBudget`) |

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
| Chain head published outside the database | implemented: `just publish-audit-head` (`audit/checkpoint.py`) writes both ends of every chain, with their hashes and retention deadlines, to the application log, `just verify-audit-head <bestand>` holds a line published earlier against the database. A shipped line cannot be retracted, so what the walk itself cannot catch - a rewrite with the chain and its head recomputed, rows removed from the newest end with the head moved back, a row gone before its published retention deadline - shows up against it. Which check catches what is tabled in the audit log doc; the walk alone is not enough. It prevents nothing and proves nothing if nobody kept a line; `backend/tests/test_audit_checkpoint.py`, `docs/audit-log.md` |
| An IP address we cannot vouch for is marked as such | implemented: `refs.ip_unvouched` is `true` on a row that arrived with `PLAK_BEHIND_PROXY` set and no usable `X-Forwarded-For` entry behind it; the row then carries the direct peer, which is a proxy rather than a visitor. The address is stored unchanged and nothing is refused over it; `net.py` (`ClientAddress`), `audit/log.py`, `backend/tests/test_net.py`, `backend/tests/test_audit.py` |
| Database errors do not log bind parameters | implemented: `hide_parameters=True` on the SQLAlchemy engine of the app and of the purge job, so that a failed statement never puts a sub, email address or `reason` in the log; `db.py`, `audit/retention.py` |

## Database

| Item | Status |
|---|---|
| One shared PostgreSQL enum type `access_base`, a test guards the sync with the application constant | implemented |
| Access is a base plus two exceptions, not a single level | implemented: `sites.access_base` / `access_keys` / `access_invitees` (and the same three as group default and as preview override), `constants.py` (`AccessBase`, `AccessPolicy`), `access/gate.py`, `backend/tests/test_access_gate.py` (every base times every combination of the exceptions) |
| DB account separation (migration, runtime and purge account separate) | not done: the shared PostgreSQL service on ZAD hands out exactly one user, so the separation cannot exist in production. Local mirrors production: one account for app, alembic and purge job |
| Append-only audit log without account separation | the guarantee is the triggers from `0001_base`: `audit_log_no_update`, `audit_log_delete_after_retention`, `content_viewer_delete_after_retention`, `audit_log_chain_head_guard` and a `TRUNCATE` guard on each of those tables, proven by `backend/tests/test_migrations.py`. They apply to every session on that account, so to the app itself too. What is lost: the account is also schema owner and can disable the triggers, rewrite the functions, and then `TRUNCATE` or drop the table; see `docs/audit-log.md` |
| Audit rows authentic, not only immutable | implemented: `audit_log_chain` (a BEFORE INSERT trigger in `0001_base`) stamps `occurred_at` itself, so a caller cannot pick the moment its row claims, and hashes every row over its predecessor's hash plus its own length-prefixed content. Removing, rewriting or back-dating a row breaks the chain from there on. Sixteen chains per retention period instead of one, keyed on the period and the row id, so the per-insert lock does not serialise every audit write in the application and the purge only ever takes the oldest end of a chain. `just verify-audit-log` (`plak/audit/chain.py`) walks the chains and names the first break in each; `backend/tests/test_audit_chain.py`. Every row carries its predecessor's hash, so the oldest surviving row is checked too, and each chain's newest row is held against the head registered in `audit_log_chain_heads`, so rows removed from the newest end are caught as well. What is not covered: the schema owner can disable the triggers and recompute the whole chain and its head, and rows dropped off the front of a chain leave nothing pointing at them. Only an off-host append-only sink (WORM or SIEM) closes that, and that decision is open; see `docs/audit-log.md` |

## Supply chain

| Item | Status |
|---|---|
| Base images pinned on digest | open: `containers/plak/Containerfile` (node 22, uv 0.5/python 3.12, python 3.12-slim-bookworm) pins on fixed tags; digests are a production prerequisite. `containers/nginx-dev/Containerfile` is only the dev proxy and falls outside this |
| CI actions pinned on commit SHA | done: every `uses:` in `ci.yml` and `deploy.yml` sits on a commit SHA, fixed by `test_workflows.py`. Handled on the user side: `docs/publishing.md` pins every `uses:` on a commit SHA, and the one nested `uses:` in `actions/publiceer/action.yml` (`astral-sh/setup-uv`) sits on a commit SHA too, fixed by `cli/tests/test_cli.py` |
| Lockfiles (uv.lock, package-lock.json) | implemented |
| Vendored files pinned on hash | done since 2026-09-28: `backend/src/plak/static/docs/SHA256SUMS` records the sha256 of the two Swagger UI files, `just refresh-swagger-ui` checks its download against it, and `test_api_documentation.py` checks the shipped bytes without a network call. Before this the recipe printed a truncated hash and compared it to nothing, which read as verification. It hid a real change: a repo-wide rename of `project` to `site` had edited a URI-scheme list inside the minified bundle. Both files were restored from the npm registry tarball of the pinned version. Weight: 1.5 MB of minified JavaScript, served from the admin origin under `script-src 'self'`, so fully trusted script beside the session cookie, arriving in git as a diff nobody reads |
| Vulnerability scan on dependencies | done: the CI job `vulnerabilities` runs pip-audit on the exported lockfile and npm audit on the frontend, locally via `just scan`. Accepted findings sit in `.trivyignore.yaml` with a date and a motivation and come back by themselves on that date |
| SBOM per image | done: `deploy.yml` generates a CycloneDX SBOM with trivy and keeps it 90 days as an artefact |
| Build provenance and SBOM attestation | done: `deploy.yml` attests the pushed image digest with `actions/attest`, once as SLSA build provenance and once with the CycloneDX SBOM, and pushes both to the registry. That happens in the job `provenance`, not in `build`: see "Where `id-token: write` may sit" below. `test_workflows.py` fixes the wiring. Verify with `gh attestation verify oci://<image> -R DigiGilde/plak`. On the Free plan this only works while the repository is public, which it is since 2026-09-27; going private again would take it away |
| Updating dependencies | done: `.github/dependabot.yml` follows github-actions, uv, npm (frontend and e2e) and docker (both Containerfiles), weekly and grouped. A dependabot PR gets no preview environment but does go through the test gate |
| Production deploy behind the tests | done: `ci.yml` has become `workflow_call` and `deploy.yml` calls it; the job `production` hangs on `needs: [ci, build]`. Open: branch protection on `beta` with the eight `ci /` checks and the three `CodeQL /` checks as required, see the checklist below |
| Static analysis in CI | done: `codeql.yml` runs CodeQL over `actions`, `javascript-typescript` and `python`, each with `build-mode: none`, on every pull request, on a push to `beta` and weekly. Its own workflow and not a job in `ci.yml`, because a called workflow carries no schedule; `security-events: write` sits on the analyse job alone. Findings land in the security tab; the analyse job itself only goes red on an analysis that breaks. What can go red on a pull request is the separate `Code scanning results / CodeQL` check GitHub adds, on newly introduced alerts above the threshold in Settings |
| Image scan in CI | done: `deploy.yml` runs trivy twice on the built image, first a full report in the log and then the gate on CRITICAL and HIGH with `ignore-unfixed`. A red scan fails `build`, so nothing gets deployed |
| `security.txt` (RFC 9116) under `/.well-known/` | done: both hosts serve the same document from `platform/security_txt.py`, with a `Canonical` per https origin and an `Expires` that is set 90 days ahead per request. `test_security_txt.py` runs it through `sectxt`. The GitHub advisory form is the first `Contact` and `SECURITY.md` the first `Policy`, both reachable since the repository went public with private vulnerability reporting on. No `Encryption` field: it cannot be tied to one `Contact`, and NCSC-NL's key would read as the Plak team's (`SECURITY.md`) |

## Production prerequisites (organisational)

| Item | Status |
|---|---|
| DPIA | planned, prerequisite for production go-live; `docs/privacy.md` is the draft starting point |
| Pentest | planned, prerequisite for production go-live |

### Account separation can come back on a database of our own

Plak runs on the *shared* PostgreSQL service of ZAD, which gives a project
exactly one user; there is no way to ask for a second one. The separate service
`namespace-postgresql-database` does offer `CREATEROLE` and `postInitSQL`. If
Plak moves to that, the separation (migration, runtime and purge account) can
come back, with `postInitSQL` as the place for the roles and their rights. Open
point, not a prerequisite.

## Pre-production / open points

### The platform's shared domains are not on the Public Suffix List

Checked on 2026-09-29: none of the four ZAD domain families (`rijks.app`,
`rijksapps.nl`, `rijksapp.nl`, `rijksapp.dev`, see `docs/deploying-on-zad.md`
§3) appear in the Public Suffix List. Plak's own base domain today is
`rijks.app` (`beheer.plak.rijks.app`, `plak.rijks.app`).

Because the base domain is not on the list, a browser treats it as an
ordinary registrable domain rather than as a suffix under which unrelated
sites are isolated from each other. JavaScript running on the content host
could therefore set cookies scoped to `Domain=plak.rijks.app` (reaching the
admin host, `beheer.plak.rijks.app`) and to `Domain=rijks.app` (reaching
every other application on `*.rijks.app`, ZAD-hosted or not).

That exposure is not the default, though. Every site has its own shielding
switch, "Afschermen van andere sites" (`Site.sandbox`, `serving/response.py`,
`serving/router.py` ~274-285), **on by default** for a site regardless of
whether it is public or restricted (`Site.sandbox` is read on its own; access
level plays no part in it). With it on, the page is served under
`sandbox="allow-scripts allow-forms allow-popups"` and deliberately without
`allow-same-origin`, which gives the page an opaque origin; a sandboxed
document with an opaque origin cannot read or write `document.cookie` or use
`cookieStore` at all (throws in Chromium, Firefox and WebKit). So a script on
a shielded site cannot reach cookies in the first place, whether its own,
the admin host's or another `*.rijks.app` application's. The exposure above
is real only for a site whose own administrator switched shielding off (or
any third-party or DOM-XSS script that runs inside such a site).

The admin session cookie is `__Host-` prefixed (see the cookie row above), so
even on an unshielded site this cannot fix or overwrite that cookie itself:
the `__Host-` prefix forbids a `Domain` attribute and requires an exact host
match, which is exactly why the admin session uses it. What a cookie bomb can
still do is push the total cookie volume sent to `*.rijks.app` past the
browser's per-domain limits, which makes the admin host unusable for that
browser until its cookies for the domain are cleared - a denial of service,
not a session takeover, and one the admin host cannot defend itself against:
an oversized `Cookie` header is refused by the router or by uvicorn before
Plak's own code ever runs, and `Clear-Site-Data: "cookies"` would clear the
whole registrable domain, every `*.rijks.app` application along with it, not
only Plak's own cookies. Other applications on `*.rijks.app` that use an
ordinary (non-`__Host-`) session cookie do not have the `__Host-` protection
either and are exposed to cookie tossing: a `Domain=rijks.app` cookie that a
more specific path match lets substitute for theirs.

Status: open. Options under evaluation: making the per-site shielding
mandatory rather than a switch a site administrator can turn off, a separate
registrable domain for content that no other application shares (ideally one
that is itself on the Public Suffix List), and getting `rijks.app` onto the
Public Suffix List. No decision made yet.

### Proving trusted publishing end to end, deliberately postponed

Everything about CI trusted publishing is verified by reading the code and by
tests with tokens we mint ourselves. A workflow could prove the real chain:
bring up the e2e stack, seed a `site_repositories` row for this repository,
request a genuine GitHub OIDC token with that stack's base URL as the audience,
run `actions/publiceer`, and fetch the published page back.

Decided on 2026-09-27 not to build it yet, and the reason is the permission
rather than the work. A job with `id-token: write` can request a token for any
audience it likes, not only the throwaway stack. That token carries this
repository's real numeric id, so any Plak installation holding a
`site_repositories` row for this repository would accept it. Today that is no
installation, so the blast radius is empty. It stops being empty the day Plak
publishes its own site or docs through Plak, which is the obvious first use.

So: build it when something trusts this repository, not before. Until then the
permission would exist for a proof about a chain nobody uses. When it is built,
it belongs in a workflow of its own, manual and scheduled rather than a gate
(a fork or a Dependabot run gets no OIDC token, so it could only ever go red
after a merge), in a job of its own that runs no project code. Not the `e2e`
job: that one runs `npm ci` twice and a `uv sync`, which would put the token
within reach of the whole dependency tree. The rule that follows from this is
in the next section.

The negative cases stay where they are, in `backend/tests/test_deploy_api.py`:
GitHub will not issue a token for a repository you do not own, so a wrong
repository, a wrong audience or a wrong event cannot be produced for real.

### Where `id-token: write` may sit

The rule: a job that holds `id-token: write` runs no code of this project and
no code of its dependencies. The permission is not scoped to one audience, so
a job that has it can mint a token for any audience it names, and that token
carries this repository's numeric id. Whoever gets to run in such a job can
therefore speak as this repository to anything that trusts it, this project's
own CI trust rules included.

Today `deploy.yml` is the only file with the permission, in the job `provenance`,
which does four things: log in to ghcr, download the SBOM artefact, and run
`actions/attest` twice. `build` builds the image, so it executes the
Containerfile and with it `npm ci` and `uv sync`; it keeps `contents: read` and
`packages: write` and nothing more. The digest passes from `build` to `provenance`
as a job output, so the split cannot make the two disagree about which image
was attested. `backend/tests/test_workflows.py` holds both halves of that in
place.

What this is worth today is a fair question: nothing deploys to production and
no Plak installation trusts this repository, so the blast radius is empty. It
is separated now because it is cheap now (one job, one artefact that was
already being uploaded) and because the alternative is remembering to do it on
the day the radius stops being empty.

### Now the repo is on GitHub

The remote is `https://github.com/DigiGilde/plak`, first pushed on 2026-09-27
and public since the same day, default branch `beta`. Work through what is
left. Everything still unticked below is a repository setting, so it is a click
in Settings and not a change in this repository.

- [x] **The first run of `ci.yml` and `deploy.yml`.** `ci.yml` is green on
  `beta` with all eight jobs (`backend`, `cli`, `frontend`, `e2e`,
  `pre-commit`, `secret-scan`, `containers`, `vulnerabilities`). `deploy.yml`
  builds and pushes the image, but has never completed a deploy: `productie`
  hangs on a push to `main` and there is no environment to deploy to yet.
- [x] **Dependabot has delivered its first round** for every ecosystem
  (github-actions, uv for `backend` and `cli`, npm for `frontend` and `e2e`,
  pre-commit, docker), and a dependabot pull request gets the test gate but no
  preview environment, as intended.
- [x] **Secret scanning and push protection.** GitHub switched both on itself
  when the repository became public.
- [x] **Private vulnerability reporting** is on, so
  `/security/advisories/new` no longer answers a 404.
- [x] **Go through the first CodeQL run.** Fifteen alerts on 2026-09-27:
  eleven Python and four TypeScript, none in `actions`. One was real
  (`js/bad-tag-filter`: the build-output test matched `</script>` without
  allowing a space or attributes, so the CSP guard failed open) and is fixed;
  fourteen were dismissed, each with the mechanism and a test named in the
  dismissal comment. The query set stays the default one; a finding is
  answered or dismissed with a reason, never made quiet by narrowing the
  queries.

  Re-audited adversarially on 2026-09-28, with the brief of proving the
  dismissals wrong rather than confirming them: payloads were run through the
  real validators, not reasoned about. All fourteen hold. Three of them rest
  on the same blind spot, worth naming because it will recur: `filename in
  _ASSETS`, `lang in i18n.SUPPORTED` and `base in _CATALOGUES` are
  allowlists-by-membership, the strongest validation there is (nothing is left
  to escape), and CodeQL counts a comparison as a barrier only against a
  literal (`x == "nl"`, `x in ("nl", "en")`), not against a named table. A
  codebase that escapes gets green; one that uses allowlists gets red. Do not
  let the alert count steer the design. What does both is handing on the
  table's own value instead of the checked input, which is what the docs
  assets and `?lang=` now do (see below).

  That re-audit found four issues CodeQL never raised, all of them outside its
  language model (a `justfile`, shell, file writing, a URL parse): the
  unverified Swagger UI download (see "Supply chain" above), and three
  credential paths in the CLI, closed in `b6edbac` -- userinfo accepted in
  `--host` and then printed by every message naming the host, a token with a
  newline able to inject `PLAK_HOST` into `.env.plak`, and the `::add-mask::`
  line being written when stdout is a plain file the runner never reads.
  The CLI's stdout must not be captured in CI; see `docs/publishing.md` §3.

  Re-reviewed on 2026-09-30 with the brief of fixing rather than dismissing,
  with CodeQL 2.27.1 run locally on the default suites (it reproduces the
  security tab alert for alert). Twenty alerts stood dismissed by then: the
  fourteen, #16 (the `::add-mask::` line, first flagged once `b6edbac` moved
  it into a function whose parameter is called `secret`), #17 and #18 (#2 and
  #3 again, under a new fingerprint after the regex fix in `d6733b6`), and
  #24 to #26 from `plak group create` and `plak site create`. Thirteen no
  longer fire because the code changed:

  - `py/clear-text-logging-sensitive-data` on the CLI host, #9 to #14 and #24
    to #26. The dismissals blamed the `.env.plak` dict, but the source was the
    function name `_trusted_stored_host`: CodeQL's sensitive-data heuristic
    reads any name containing "trusted" as a secret. Renamed to
    `_stored_host_if_safe`, with a test that a host from the stored session is
    printed while its tokens never reach stdout or stderr.
  - `py/path-injection` #6 and #7: `_ASSETS` holds each asset's path, so the
    route's `filename` only selects an entry and is never joined onto a path.
  - `py/reflective-xss` #5: `?lang=` picks one of `i18n.SUPPORTED` and the
    page renders that constant, never the query value.
  - `js/incomplete-url-substring-sanitization` #4: the mock compares the
    Forgejo host with an explicit `===`; `Array.includes` on a value that may
    hold `https://github.com` read as a substring check.

  Five stay dismissed, where the query is right about the pattern, wrong about
  the risk, and the only change that would silence it makes the code worse:

  - #16, `::add-mask::` in `_mask_in_ci_log`: writing the token to stdout is
    how the runner learns to mask it; there is no other channel.
  - #15, `dev/seed.py` prints the secret-link key it just created: a dev
    fixture on the developer's own terminal, kept out of the image by
    `.dockerignore`.
  - #8, `py/url-redirection` in `_cookie_for_next_site`: `target` has passed
    `valid_return_to`. CodeQL accepts only a literal comparison or the value on
    the right of a `+` here, and `"/" + value[1:]` would silence it without
    checking anything.
  - #17 and #18, `strippable()` in `dutch-literals.test.ts`: a `<!--` or
    `<style` left after one pass makes the scanner read more and fail loudly;
    stripping to a fixpoint, which is what the query wants, makes it read less.

  #2 and #3 stay dismissed as the old fingerprints of #17 and #18; they match
  no code any more.
- [ ] **Turn on Dependabot security updates**
  (`dependabot_security_updates`, Settings, Code security). Dependabot opens
  version updates today; without this it does not open a pull request for an
  advisory out of the weekly rhythm.
- [ ] **Turn on the two extra secret-scanning options**
  (`secret_scanning_non_provider_patterns` and
  `secret_scanning_validity_checks`, Settings, Code security). The first
  catches keys of providers GitHub has no partner pattern for, the second says
  whether a leaked token is still live.
- [ ] **Branch protection on `beta`**, which is the default branch, with the
  required checks `ci / backend-coverage`, `ci / cli`, `ci / frontend`, `ci / e2e`,
  `ci / pre-commit`, `ci / secret-scan`, `ci / containers`,
  `ci / vulnerabilities` and `CodeQL / Analyse (actions)`,
  `CodeQL / Analyse (javascript-typescript)`, `CodeQL / Analyse (python)`.
  Only then is the test gate in `deploy.yml` also closed for a direct push.
  The rule moves along to `main` on the day production exists. Merging goes
  through a merge queue (rebase), which runs the same required checks on the
  queued commit; that is why `deploy.yml` and `codeql.yml` also trigger on
  `merge_group`, and why `build` skips it.
- [x] **`security.txt` is complete.**
  `Contact: https://github.com/DigiGilde/plak/security/advisories/new` leads the
  `Contact` lines and `Policy: https://github.com/DigiGilde/plak/blob/beta/SECURITY.md`
  the `Policy` lines, in `backend/src/plak/platform/security_txt.py`;
  `SECURITY.md` names the same two routes. Going private again would make both
  answer a 404, so they move along with the visibility.

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
(`__Secure-plak-content`, HttpOnly, Secure, SameSite=None,
`Path=/{group}/{site}/`, 12 hours, no CSRF cookie, with an anchor under `/-/`,
see the cookie row above) without admin
authority: `session_from_request` (admin and API) accepts kind `admin` only, content serving kind `content` only,
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

- `PLAK_BEHIND_PROXY` says whether a proxy stands in front of the pod; on ZAD
  the HAProxy router does, so `true`. The app then reads the last
  `X-Forwarded-For` entry, which is the one that proxy wrote about the peer it
  accepted. `false` where there is none, and the socket decides: reading the
  header there would let a client name itself. The predecessor named the
  routers' own range instead, which on ZAD cannot be had -- the router pods sit
  on the cluster pod network -- and named all of RFC1918 in its place. A second
  proxy (a CDN, an nginx in the pod) would move the entry and this setting
  would have to become a count.
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
