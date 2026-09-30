# Plak: the numbered design

This document holds the rules the code cites. A comment that says "spec §7" or
"behaviour requirement 6" means a section number here. It describes Plak as it
is, not as it was designed: where an older design paper and the code disagree,
the code is the subject and the older paper is gone.

Each rule names where it lives (module) and what holds it there (test). Paths
are relative to the repository root; backend modules are under
`backend/src/plak/`, backend tests under `backend/tests/`.

The numbering is fixed by the references in the code and cannot be renumbered
without breaking them. Behaviour requirement *N* under serving is section 5.*N*;
the two spellings are the same rule.

## 1. What Plak is

Plak publishes static sites for government organisations: public, behind SSO
Rijk, for invitees on an email address, or through a secret link. Every pull
request can get a preview of its own. A site is a tree of files that Plak does
not interpret; what Plak owns is who may see it, which version is live and who
may change that.

One application answers on two hosts (§4a), serves content and the beheer
interface itself, and keeps one PostgreSQL database. There is no nginx logic in
production; the dev stack has a dumb reverse proxy only.

## 2. Concepts

- **Group**: a collaboration unit (`nldd`, `fin`). Holds sites and members.
  Groups do not nest.
- **Site**: the published thing, with one live version and any number of
  previews.
- **Version**: an immutable file tree, target `live` or `preview`.
- **Preview**: a named temporary variant per ref (`pr-42`) pointing at a version
  with target `preview`, with an expiry.
- **Member**: a person with a record in the beheer environment. A viewer is not
  a member and gets no member record (§7).
- **Invitee**: an email address or SSO subject on a site's viewer list.
- **Secret link**: a URL carrying a key that grants access without logging in.

Models: `models/identity.py`, `models/publication.py`.

## 3. Roles and authorisation

### 3.1 Three roles, two levels, one enum type

`Role` is `reader`, `editor`, `admin`, one shared PostgreSQL enum type used by
both `group_members.role` and `site_members.role` (`constants.py`,
`models/base.py`, migration `0001_base`). A reader looks on, an editor changes
content, an admin changes policy: who may look, who may join in and what
disappears for good. The interface calls them lezer, redacteur and beheerder
(`frontend/src/format.ts`).

Beside that there is the platform role on a member: `admin` or `member`
(`models/identity.py`). That role is about people and groups, never about the
content of a site (§3.4).

Guarded by: `test_migrations.py` (the enum values and their order match
`constants.py`), `frontend/src/format.test.ts`.

### 3.2 The widest role wins

The effective role on a site is the widest of the group role on that site's
group and the site role on that site. No site role lowers a group role, and a
site role never grants group authority: a group-level check reads
`group_members` only. Ranking is explicit in `ROLE_RANK` because a `StrEnum`
compares as text and `'editor' < 'admin'` is false.

Code: `access/roles.py` (`widest`, `at_least`, `effective_site_role`),
`api/authorization.py`. Guarded by: `test_admin_api.py`.

### 3.3 One predicate for "belongs to this site"

Access base `site_team` means: everyone with an active role on this site, which
is every active member of the site's group whatever their group role, plus every
active member with a role on the site itself. The same predicate answers "who
may look at this site on its narrowest logged-in setting" and "who may manage
it", so the interface can promise nothing the gate contradicts. It also governs
`_version` views (§7).

Code: `access/gate.py` (`_belongs_to_site`). Guarded by: `test_access_gate.py`.

### 3.4 The platform administrator manages people and groups, not sites

There is no general platform-administrator bypass in the role checks. A platform
administrator activates and deactivates members, reads the platform member list,
grants the platform role, creates groups and deletes empty ones, reads the
overview of every group and site as metadata, and reads and changes group
membership and group roles, including their own. To do anything inside a group
they give themselves a group role, which is one visible act in the audit log
instead of a silent exception.

Two reading concessions follow from that, not a bypass: a platform administrator
sees that a site exists (they already see it in the overview), so they get a 403
rather than the 404 a stranger gets.

Code: `api/authorization.py`, `api/admin.py` (`_group_with_role` with
`platform_admin=True`, `_site_with_role`). Guarded by: `test_admin_api.py`.

### 3.5 Adding somebody is picking a role

`group_members.role` and `site_members.role` have no database default: whoever
adds a member picks the role instead of silently getting the widest one. The API
defaults the field to `reader`, so the unconsidered case is the harmless one.
Whoever creates a group becomes its `admin` in the same transaction; whoever
creates a site becomes `admin` of that site.

A group can never lose its last admin: the API refuses the removal or demotion
with a 409, and a constraint trigger on `group_members` refuses it in the
database as well. That trigger first checks that the group still exists, or it
would break the cascade when a group itself is deleted. There is deliberately no
equivalent on `site_members`: a site without a site admin is not orphaned,
because the group admin is the floor (§3.2).

Code: `models/identity.py`, `api/admin.py`, migration `0001_base`
(`guard_last_group_admin`). Guarded by: `test_admin_api.py`,
`test_migrations.py`.

### 3.6 Who may do what

Minimum effective role per action; the platform column marks the actions a
platform administrator may do without a group role (§3.4).

| Action | Minimum |
|---|---|
| Read own profile (`/me`), read the overview | active member |
| Activate, deactivate a member; read the platform member list; grant the platform role | platform administrator |
| Create a group; delete an empty group | platform administrator |
| Read a group, read its member list | group `reader`, or platform administrator |
| Add a group member, change a group role, remove a group member | group `admin`, or platform administrator |
| Change the group default access | group `admin` |
| Create a site | group `editor`; the creator becomes site `admin` |
| Read versions, previews, site members | effective site `reader` |
| Deploy live or preview, tear a preview down, set a version live | effective site `editor` |
| Read and remove invitees, read and revoke secret links | effective site `editor` |
| Add an invitee, create a secret link, change access, change a preview override | effective site `admin` |
| Link or unlink a repository, change external sources | effective site `admin` |
| Add, change, remove a site member | effective site `admin` |
| Delete a site | effective site `admin` |
| Read the audit log, resolve a pseudonym, reveal an IP | platform administrator |

A member who has no role at all on a site gets the neutral 404 of an unknown
site rather than a 403: without that, the API would list which sites exist in a
group they have nothing to do with.

Code: `api/admin.py`, `api/deploys.py`, `api/authorization.py`. Guarded by:
`test_admin_api.py`, `test_deploy_api.py`, `test_audit_api.py`.

## 4. URL design

Slugs (group, site, preview ref) match
`^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$`, so a slug can never start with `_` and
can never be exactly `-` (`constants.py`, `frontend/src/composables/slug.ts`).
The slug is the stable identifier, in the API too; renaming is out of scope.

| Path | Meaning |
|---|---|
| `/{group}/{site}/...` | live content (content host) |
| `/{group}/{site}/_preview/{ref}/...` | preview content |
| `/{group}/{site}/_version/{version-id}/...` | an older version, for the site team only |
| `/-/login`, `/-/oauth2/callback`, `/-/logout` | OIDC login, callback and logout, spelled the same on both hosts |
| `/-/code` | hands in the code of a secret link (content host only) |
| `/-/api/v1/...` | JSON and deploy API (beheer host only) |
| `/-/api/docs` | API documentation, self-hosted UI, publicly readable |
| `/-/oidc/backchannel-logout` | back-channel logout from the OP (beheer host only) |
| `/-/groups`, `/-/members`, `/-/privacy`, `/-/accessibility`, `/-/about`, `/-/sessions` | SPA pages inside the platform namespace |
| `/cli-link` | SPA page the CLI device flow opens by URL |
| `/` on the beheer host | the SPA |
| `/` on the content host | public front page |
| `/robots.txt`, `/favicon.ico`, `/.well-known/...` | the locations the web pins down, on both hosts |
| `/healthz` | internal only, on neither public host |

The platform namespace is the single segment `-`. Because `-` is not a valid
slug it can never collide with content, and every application endpoint lives
under it on both hosts, which is what keeps the root of the beheer host free for
the SPA. Inside a dist the reserved top-level segments are `_preview` and
`_version` (§6). Reserved group slugs are the web's own locations plus the
root-level SPA pages, one constant shared by routing, slug validation and a
CHECK on `groups.slug`.

The beheer SPA has no `/beheer` or `/admin` path prefix: it sits on the root of
the beheer host and the application's own paths are carved out of it (§9).

Code: `constants.py`, `serving/router.py`, `platform/spa.py`,
`frontend/src/router.ts`. Guarded by: `test_constants.py`,
`test_host_separation.py`, `test_spa.py`, `frontend/tests/router-guard.test.ts`.

### 4a. Two origins, one application

Published content may contain arbitrary JavaScript (§5.7 allows
`script-src 'self' 'unsafe-inline'`). On one shared origin that content could
call the beheer API with the session cookie the browser sends along, so beheer
and content stand on separate origins:

- **beheer host** (`beheer.<domain>`, `PLAK_BASE_URL`): the SPA, `/-/api/v1`,
  login, callback, logout, back-channel logout. Carries the beheer session
  cookie (`__Host-plak-session`, SameSite=Strict) and the CSRF cookie.
- **content host** (`<domain>`, `PLAK_CONTENT_BASE_URL`): published sites,
  previews and version views, the public front page, and of the platform
  namespace exactly four paths: content login, its callback, content logout and
  `/-/code`. Restricted content gets a content session (SameSite=Lax) without
  any beheer authority, in a cookie scoped to the site it is for (§5.10).

The separation is enforced by the application itself, as middleware that reads
the `Host` header: on the content host only the paths above exist, on the beheer
host everything but `/healthz`. Every refusal is byte-identical to any other
neutral 404, headers included, because the security headers sit outside that
middleware and follow the host rather than the path (§5.6, §9).

The defence on the beheer API is layered, and the Origin check is the load
bearing one: a present `Origin` must be exactly the beheer origin, and in its
absence `Sec-Fetch-Site` may be at most `same-origin` or `none`. `same-site` is
refused on purpose, because the content host is a sibling of the beheer host and
a browser would count it as same-site; for the same reason SameSite=Strict is a
second layer and not the defence. CORS is never opened. A non-browser client
without either header (CI, curl) passes, and session-borne mutations still need
the CSRF double submit.

Code: `host_separation.py`, `api/origin_guard.py`, `config.py`, `main.py`.
Guarded by: `test_host_separation.py`, `test_origin_guard.py`,
`test_api_integration.py`, `test_app_integration.py`.

## 5. Serving

The application serves content itself with Starlette's `FileResponse` (sendfile
where the server offers it, Range requests) and answers conditional requests
without touching the store. There is no X-Accel-Redirect and no nginx datapath;
nginx exists only in the dev compose stack, passing the `Host` through.

Order per request: path validation, access decision, audit, key redeem,
`If-None-Match`, file resolution, response. The lexical 301 sits before all of
it (§5.2).

Code: `serving/router.py`, `serving/response.py`, `serving/resolution.py`,
`serving/mime.py`. Guarded by: `test_serving.py`, `test_resolution.py`,
`test_mime.py`.

The nine behaviour requirements below are the same numbers as §5.1 to §5.9.

### 5.1 Path validation before anything else

The request path is validated serving-side, independent of what ingest already
refused: null bytes, backslashes and `..` segments yield the neutral 404. There
is deliberately no second URL decode, or `%252e%252e` would still collapse into
`..`. Containment inside the version root is enforced once more by
`ContentStore.file_path`. Code: `serving/resolution.py` (`normalise_rest`),
`ingest/store.py`. Guarded by: `test_resolution.py`, `test_store.py`.

### 5.2 Trailing slash and the index rewrite

`/{group}/{site}` without a slash gets a purely lexical 301 to
`/{group}/{site}/`, before any existence or access check, so the redirect leaks
nothing; the same holds for a preview root and a version root. A directory
*inside* a site gets its 301 only after an allow decision. A path ending in `/`
resolves to `index.html`, case-sensitively: `Index.html` is a different file.
Code: `serving/router.py` (`_lexical_slash_redirect`), `serving/resolution.py`.
Guarded by: `test_serving.py`, `test_app_integration.py`.

### 5.3 MIME table

Python's `mimetypes` plus an explicit table pinned independently of the platform
(`.mjs`, `.map`, `.wasm`, `.webmanifest`, `.avif`, `.woff2`, `.ttf`, `.otf` and
more). Unknown is `application/octet-stream`. Every content response carries
`X-Content-Type-Options: nosniff`. Code: `serving/mime.py`. Guarded by:
`test_mime.py`.

### 5.4 ETag and 304

`ETag` is the version id. `If-None-Match` is answered by the application itself
with a 304, after resolution has found a file: a 304 for a path without a file
would let an intermediary treat a non-existent resource as fresh. The 304
carries `ETag`, `Cache-Control`, `nosniff` and, where it applies,
`X-Robots-Tag`. Code: `serving/response.py` (`etag_for`,
`if_none_match_matches`, `make_304`). Guarded by: `test_serving.py`.

### 5.5 Cache-Control

HTML gets `no-cache, must-revalidate`, every other asset
`max-age=31536000`, with `immutable` on top for public content only: an asset
URL is not content-addressed and survives a redeploy, and private content has
no shared cache to win the extra reach back from. The prefix `private, ` is
added whenever the effective access is not public, and always for a `_version`
view. The login redirect and the directory 301 carry `no-store`, because a 301
is heuristically cacheable. Code: `serving/response.py` (`cache_control`),
`serving/router.py`. Guarded by: `test_serving.py`.

### 5.6 The neutral 404

Refusal and non-existence are one byte-identical answer, headers included:
status 404, body `Niet gevonden\n`, `Cache-Control: no-store`, `nosniff`, the
strict content CSP and `Referrer-Policy: no-referrer`. It has a single
construction point, which is what keeps it identical; it keeps the strict CSP
whatever a site allows, because a policy that followed the site would say which
site the refusal was about.

A site without a live version is a neutral 404 for everyone, public base
included. A version's own root `404.html` is shown to authorised visitors only,
with status 404.

Code: `serving/response.py` (`neutral_404_response`), `access/gate.py`,
`access/decision.py`. Guarded by: `test_access_gate.py`, `test_serving.py`,
`test_front_page.py`.

### 5.7 The content CSP

The publication contract, on every content response:

```
default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self'
'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:;
connect-src 'self'; media-src 'self'; frame-ancestors 'none'; base-uri 'self';
form-action 'self'; object-src 'none'
```

A site may allow **external sources**, which is on by default for a new site
(`sites.external_sources`, server default true). That widens `script-src` and
`style-src` with the three CDN hosts, `style-src` also with Google Fonts, and
`font-src` with the Google Fonts file host. `connect-src` stays `'self'`: a page
may load a library from those hosts, never send data to them.

A site is also **shielded from the other sites**, which is on by default for a
new site (`sites.sandbox`, server default true). That appends
`sandbox allow-scripts allow-forms allow-popups`. `allow-same-origin` is
deliberately absent: without it the document gets an opaque origin, so it is
same-origin with nothing, can read no other site on the shared content
hostname and stores nothing. Browser storage is what the switch costs, and why
it is a switch at all.

An opaque origin is cross-site with everything, its own site included, and
that has two consequences for the page's own subresources. Its no-cors loads
(stylesheets, classic scripts, images, media) do work, but only because the
content session cookie and the secret-link cookie are `SameSite=None` (§7.6);
under `SameSite=Lax` a non-public site serves its pages and none of its
assets. What cannot work is what the browser fetches in CORS mode from such a
document: web fonts, module scripts and `fetch`/XHR go out with `Origin: null`
and no cookie, and Plak sets no CORS headers anywhere. A site that needs a web
font of its own, ES modules or calls back to itself has to have this switch
off.

The boundary this draws is per site and one-directional: it keeps the content
of *this* site away from what a visitor may see on the others. A site with the
shielding off is back on the shared origin, so the platform is not isolated by
this, only defaulted safe.

Every policy comes out of one directive table with one addition per switch, so
the four combinations cannot drift apart. The neutral 404 always carries the
plain policy, without either addition (§5.6).

Code: `serving/response.py` (`_CONTENT_DIRECTIVES`, `EXTERNAL_SOURCES`,
`SANDBOX`, `content_csp`), `api/admin.py` (`PUT .../external-sources`,
`PUT .../sandbox`). Guarded by: `test_serving.py`, `test_security_headers.py`,
`test_admin_api.py`.

### 5.8 The base path contract

A dist is built with base `/{group}/{site}/` for live and
`/{group}/{site}/_preview/{ref}/` for a preview. Ingest refuses `_preview` and
`_version` as top-level entries of the published tree. A dist built with an
absolute base refers, under `_version/{id}/`, to the live assets; relative dists
show exactly there. The contract with its Astro and Vite examples and the CI
snippets lives in `docs/publishing.md` §2, which is the reference for it; this
section only pins the rule down.

### 5.9 Indexability

Previews and `_version` views carry `X-Robots-Tag: noindex, nofollow`, and so
does every SPA response (§9). Live content with a public base is deliberately
indexable; every other base yields a crawler no content at all. `robots.txt`
differs per host: the beheer host disallows everything, the content host
disallows nothing.

Code: `serving/router.py`, `platform/spa.py` (`spa_headers`),
`platform/pages.py` (`ROBOTS_TXT_ADMIN`, `ROBOTS_TXT_CONTENT`). Guarded by:
`test_serving.py`, `test_spa.py`, `test_front_page.py`.

### 5.10 Subresources of another site

Every site of every group is served from one hostname under a path prefix and
uploaded content may run its own JavaScript. So a page of site A can `fetch()`
site B, and the gate then decides on whatever credentials the browser attached.
Three things stand in the way of that, and it is worth being exact about what
each one does and does not do.

**The content session cookie is scoped to one site**, `Path=/{group}/{site}/`
(§7.6), the same shape the secret-link cookie has. A cookie path is matched
against the **requested** URL, not against the page that asked for it, so this
does not keep a session out of a fetch aimed at a site the visitor has already
opened: their cookie for site B goes along whoever asks for site B. What it does
take away is the ambient reach of one session over every site at once. A site
the visitor has not opened in this browser has no cookie, and a request aimed at
it is anonymous.

**Published content has an origin of its own** (§5.7), which is the boundary a
browser does enforce: an opaque-origin document can read no document and no
storage of another site, whatever it asks for and whatever comes back. What it
does not take away is the credential: a sandboxed page is cross-site with its
own site as well, so the cookies it needs for its own assets are
`SameSite=None` (§7.6) and therefore ride along on any page's request for that
site. Reading the answer is what the origin stops.

**Non-public content is served to a subresource request only when the request
says it comes from the same site**: `Sec-Fetch-Site: same-origin` plus a
`Sec-Fetch-Dest` other than `document`, `iframe` or `frame` needs a `Referer`
whose path lies inside `/{group}/{site}/`. This reasons about headers, one of
which an attacking page can partly shape, and it is the layer that catches a
request that does carry a credential of its own. Live, preview and `_version` of
one site are one publishing team and therefore one boundary.

Two things deliberately fall outside the last one. A top-level navigation is
something the visitor does and sees, also from one site to another; a
`window.open()` to another site is therefore served, and the opened document is
same-origin with its opener unless the sandbox of §5.7 separates them. And a
request without `Sec-Fetch-Site` at all (curl, a link checker, an older browser)
is served: it carries no ambient credentials, the same reasoning as the origin
check on `/-/code` (§7.4).

This leans on the site's own pages supplying a `Referer`, which is why
`Referrer-Policy` on a site with secret links is `same-origin` and no longer
`no-referrer` (§7.2). A refusal is the neutral 404 (§5.6), audited with reason
`FOREIGN_SUBRESOURCE`.

The check sits before the login redirect, so it decides the same way with a
session and without one: nobody logs in because of a stylesheet fetch, and a
302 would tell an anonymous caller that this site exists. It does not sit
before the neutral 404, which is this answer already; a refusal the gate made
itself keeps the reason that really refused (`UNKNOWN_SITE` and the rest).

Code: `serving/router.py` (`_foreign_subresource`). Guarded by:
`test_serving.py`.

## 6. Ingest

Transport is multipart with one file field. The payload is a single `.html` file
or a `.zip`/`.tar.gz`/`.tgz` archive. The upload is never read into memory: the
body streams chunk by chunk into a spool file in `{content_root}/_tmp/`, outside
every servable path, with the body limit checked as it goes.

Validation is fail-closed: absolute paths, `..` segments, symlinks and
hardlinks, null bytes and empty paths are refused, backslashes are normalised,
and `_preview` and `_version` are refused as top-level entries of the published
tree. Decompression is checked incrementally against the limits, so an archive
bomb never gets past them; header sizes are not trusted, though a header already
claiming more than the limit is an early refusal.

Limits (defaults, env-overridable, sized for a 1 GiB volume): request body
100 MiB, 50 MiB per unpacked file, 200 MiB unpacked in total, 1000 files, depth
10 (`config.py`, `ingest_max_*`).

Those bound one bundle. Two more bound what the bundles leave behind, because a
live version is never cleaned up: `site_max_bytes` (500 MiB, 0 turns it off)
caps what every version of one site together occupies, measured on the volume
just before the new version is renamed into place, so a refusal leaves nothing
behind and answers 413. `storage_min_free_bytes` (100 MiB, 0 turns it off) is
the free space a deploy never takes the volume below, because a volume run dry
takes the serving of every other site down with it. It is held against the
actual deploy, not the largest one allowed: before the body is read, the
declared `Content-Length` (capped at the body limit) must fit above it; while
the upload is spooled and unpacked, every chunk is checked against it, with the
volume measured again at least every 4 MiB. Crossing it at any of those
moments answers the same 503 `STORAGE_UNAVAILABLE`, and the spool and work
directory are cleaned up as on any other refusal.

The root of the site is settled in three steps: a chain of enclosing directories
holding exactly one entry is peeled off; a `basispad` from the caller then wins;
and if that yields no `index.html` in the root the bundle is refused, naming the
`index.html` paths found as a proposal. The machine proposes, the human
confirms. Capitalisation counts, because both the filesystem and §5.2 look for
exactly `index.html`.

Versions are immutable: everything is written to `{root}/_tmp/{uuid}` first and
moved with one atomic `os.rename` to `{group}/{site}/{version_id}` on the same
filesystem. Going live is a pointer swap on `sites.live_version_id` in the same
transaction as the version insert. Rollback is a pointer swap to an earlier
version with target `live`; a version with target `preview` is never a rollback
target. Every live version is kept.

Code: `ingest/unpacker.py`, `ingest/store.py`, `ingest/service.py`. Guarded by:
`test_unpacker.py`, `test_store.py`, `test_ingest_service.py`,
`test_deploy_api.py`.

## 7. Access, sessions and audit

Looking and administering are separate. The browser only ever gets an HttpOnly
session cookie; no token ever lives in the web app (BFF). The reason is that
Plak's core resource is static content the browser navigates and loads itself,
where nothing can carry an Authorization header.

### 7.1 Access is a base plus two extras

Access is not one choice out of five but one base plus two independent switches
(`AccessPolicy` in `constants.py`). A site, a group default and a preview
override all carry the same three fields.

The base (`AccessBase`), on `sites.access_base`:

1. `public`: everyone, without a session.
2. `sso`: anyone with a valid login at the configured OIDC provider. The gate
   checks exactly one thing there: that the session carries a `sub`. No member
   lookup, no claim about provenance. The interface therefore calls it
   "Iedereen die inlogt met SSO Rijk" and says so in its explanation rather than
   promising a provenance the code does not enforce.
3. `site_team`: everyone with an active role on the site or its group (§3.3).
4. `nobody`: the base grants nothing.

The extras, each a boolean beside the base:

- `access_keys`: a valid secret link grants access, without a session.
- `access_invitees`: someone on the invitee list gets in after logging in, on
  their `sub` or on an email address the IdP marked verified. An unverified
  email address never matches.

`nobody` with both extras off means nobody can view the site. That is allowed
and useful: it closes a site without deleting or rolling it back, and it gives
the same 404 as a site that does not exist. The site team can still inspect the
content through `_version` (§7.3). Public plus an extra is allowed and adds
nobody; the interface says so instead of hiding the switch.

### 7.2 The decision order

Every way in is tried before any refusal is chosen, because the base and the
extras are an OR and stopping at the first closed door would make it an AND:

1. base `public`: allow. No database hit.
2. `access_keys` on and a key presented that verifies (query or cookie): allow,
   recording the selector that granted it.
3. with a session: base `sso` allows; base `site_team` allows if the visitor
   belongs to the site; otherwise `access_invitees` on and the visitor is an
   invitee allows; otherwise the neutral 404.
4. without a session: if logging in could help (`login_can_help`: base `sso`,
   base `site_team`, or invitees on), a login redirect for live content and a
   neutral 404 for a preview, because the existence of a preview does not leak.
   The serving layer then turns every anonymous preview refusal into the same
   login redirect on a top-level navigation, whatever its reason (§7.6).
5. otherwise the neutral 404, with reason `KEY_INVALID` when a key was presented
   and refused and nothing else could have helped, and `NO_ACCESS` otherwise.
   Outward they are the same answer.

An invalid, expired or revoked key therefore grants nothing and refuses nothing:
a site team member with a broken link falls through to the team route.

A preview override is one whole policy, base and both extras together or none of
them (a CHECK on `previews`), and it replaces the site's policy rather than
merging with it. An expired preview is a neutral 404 at the decision itself; the
cleanup job is only the safety net.

`Referrer-Policy: same-origin` hangs on the configuration (`access_keys` is on
for this site), not on the way this visitor got in, so the header does not vary
per visitor; everything else gets `strict-origin-when-cross-origin`. Both send
nothing to another origin, and the page URL never carries the verifier: a
`?key=` is redeemed with a 302 that strips it. Not `no-referrer`, because the
guard of §5.10 needs a site's own pages to identify themselves.

Code: `access/gate.py` (`decide`, `decide_preview`, `_assess`),
`access/decision.py`, `serving/response.py`. Guarded by: `test_access_gate.py`
(the full matrix), `test_serving.py`.

### 7.3 `_version` views

`/{group}/{site}/_version/{id}/` is for the site team only (§3.3), whatever the
site's access setting; for anybody else the neutral 404. The one exception is an
anonymous top-level navigation, which goes to the login whatever lies behind the
path (§7.6).
Membership is checked before the version lookup, so a non-member cannot probe
for the existence of version ids. Every `_version` access is audited.

Code: `access/gate.py` (`decide_version`), `serving/router.py`. Guarded by:
`test_access_gate.py`, `test_serving.py`.

### 7.4 Secret links

A secret link is `?key=selector.verifier`. The database keeps the selector and
SHA-256 of the verifier, compared in constant time; the plaintext exists only at
the moment of creation. A valid key is redeemed: the response is a 302 to the
same URL without `key` with `Cache-Control: no-store`, setting a
`__Secure-plak-key` cookie (HttpOnly, SameSite=None) on the path of exactly that
site or preview, so the key does not stay behind in the address bar, the history
or a log. The cookie is a reference to the key record, not proof in itself, and
is validated server-side on every request, so revoking or expiry breaks
outstanding cookies at once. The uvicorn access log is off for the same reason.

A link can also be shared in two parts: `?key=selector` alone shows the code
page, where the verifier goes in the body of a POST to `/-/code`. That page
appears only for a selector belonging to a usable key of a live site that has
secret links on; everything else stays the neutral 404, or the page would say
which selectors exist. It has no session and no CSRF token, so what guards it is
same-origin only, a hard limit per selector on top of the per-IP `code` rate
limit class, and the fact that a successful POST only sets a cookie for a key
whose code the caller just proved to know.

Code: `access/keys.py`, `serving/code_page.py`, `serving/router.py`
(`_redeem_key`). Guarded by: `test_keys.py`, `test_code_page.py`,
`test_serving.py`.

### 7.5 Looking creates no member record

A login only to look creates no member record: the decision uses session claims
only. A record comes into being on a first beheer visit, and it is created
**active** straight away, because anyone who can complete the SSO login may use
Plak. `PLAK_BOOTSTRAP_ADMIN_SUB` additionally makes that member a platform
administrator, idempotently. A member set to `deactivated` loses beheer access
and the `site_team` route; the other ways in do not depend on member status.

Because a viewer has no member row, an SSO viewer of protected content is
recorded in `content_viewers` instead, upserted on every successful content-host
login, purged after 90 days without one. Without it the audit log would have
nothing to resolve such an actor to.

Code: `auth/members.py`, `auth/content_viewers.py`. Guarded by:
`test_members.py`, `test_content_viewers.py`, `test_sessions.py`.

### 7.6 Sessions

Server-side sessions with a signed `__Host` cookie; the browser gets a signed
reference, claims never stand on their own in the cookie. The store is in
process memory, because Plak runs one replica (§11), and sessions deliberately
do not survive a restart. Maximum session age is 12 hours, with session id
rotation at login. Two kinds (§4a) live in one store, kept apart by the kind
field plus a separate cookie name: beheer routes accept the beheer session only,
content serving the content session only. `returnTo` is validated strictly: own
origin, paths only, and never carrying a `key` parameter.

The admin session cookie is SameSite=Strict. On the content host the two
cookies differ: the site cookie is SameSite=None, the anchor stays Lax. None is
what a shared link to restricted content needs (it has to open after a
cross-site navigation, which Lax already covers) plus what a
sandboxed page needs to reach its own assets, since its opaque origin is
cross-site with its own site (§5.7, §5.10). The anchor is the cookie that can
mint a site cookie for the next site and is only ever read on a top-level
navigation, so it keeps the narrower Lax. What None costs, and why the
alternatives are worse, is in `docs/security.md`. Logging out on the beheer
host is POST only and ends the content session too, through a redirect to
`/-/logout` on the content host.

The content session rides in two cookies, because all sites share one
hostname (§5.10):

- `__Secure-plak-content`, `Path=/{group}/{site}/`: the session id for content
  requests. `__Secure-` rather than `__Host-`, because the `__Host-` prefix
  requires `Path=/`. What that prefix also forbids is a `Domain` attribute, and
  giving that up is what makes this cookie shadowable from a sibling host on the
  same registrable domain; the value is signed and the session has to exist in
  the store, so the worst a shadow achieves is pushing a visitor into a session
  of the shadower's own, on an origin that has no session-borne mutations. The
  beheer cookie keeps its `__Host-` prefix and with it the guarantee that
  nothing on the content host can write it.
- `__Secure-plak-content-anchor`, `Path=/-/`: the same session id where the
  login, the callback and the logout can read it, and nowhere else.

One server-side session behind both: one 12-hour lifetime, one `kind`, one
revocation. Opening a site the visitor has not opened before arrives without a
site cookie, so the gate sees an anonymous visitor and answers with the login
redirect; `/-/login` then hands out the cookie for that site off the anchor
session and sends the visitor on, without a round trip to the IdP. It does that
only for a top-level navigation (`Sec-Fetch-Dest: document`, a header page
script cannot set), because otherwise a page could walk another site's request
through the login and collect a session for it on the way back. A preview or a
`_version` view never gets a login redirect from the gate, and a site without a
live version has no live route that could hand one out, so the serving layer
turns every anonymous refusal on those two routes into the login redirect,
whatever the reason. The answer is then the same for a path with nothing behind
it, so guessing paths teaches nothing. It does that only for a top-level
navigation, not when a `?key=` or key cookie came along (its holder may have no
SSO account), and not for a path that is no `/{group}/{site}/` of slugs, where
the login could set no site cookie and the visitor would loop. Everything else
keeps the byte-identical neutral 404.

The logout clears the anchor and the site cookie at every path the
session handed one out for (the session remembers up to 32 of them). Past that
ceiling a site cookie survives in the browser until it closes, and opens
nothing: the session behind the id is gone.

Code: `auth/sessions.py`, `platform/pages.py`. Guarded by: `test_sessions.py`.

### 7.7 OIDC

Authorization code plus PKCE. Client authentication is configurable
(`private_key_jwt`, `client_secret_post`, `client_secret_basic`), because the
ZAD Keycloak hands out client-secret clients. Id token validation: an
asymmetric alg allowlist (RS256/PS256/ES256, never `none` or HS\*) as our own
code on top of authlib, exact issuer, `aud`, `azp`, `nonce`, `exp`, `iat`,
`at_hash` when present, and the RFC 9207 `iss` response parameter, checked on
the callback query before the token exchange and enforceable through a config
flag. The `acr` requirement is a configured list and may be empty, which is
logged explicitly at startup.

Back-channel logout is accepted on `/-/oidc/backchannel-logout`, beheer host
only, with signature, `iss`, `aud`/`azp`, a recent `iat`, the logout event
claim, `sid` or `sub`, the absence of a `nonce` and a `jti` replay cache.

Because a Plak session outlives the IdP's own, a session is re-validated against
the IdP on the first request after `PLAK_IDP_RECHECK_SECONDS` (default 900, `0`
turns it off) with `grant_type=refresh_token`. Success keeps the session and
rotates the stored refresh token; the `sub` of a new id token must match. A hard
refusal (`invalid_grant`) drops the session and the request continues as not
logged in. A network failure leaves the session alone and is retried after a
backoff. A refusal of our client itself keeps everyone logged in, because a
broken integration must not log the platform out, but raises an ERROR line and a
standing complaint that `/healthz` reports as `degraded` while still answering
200.

Code: `auth/oidc.py`, `auth/revalidation.py`, `platform/backchannel.py`,
`config.py`. Guarded by: `test_oidc.py`, `test_session_revalidation.py`,
`test_backchannel_logout.py`, `test_config.py`.

### 7.8 API security

The beheer API refuses everything that does not demonstrably come from the
beheer origin (§4a) and never sets CORS headers. Session-borne mutations need
the CSRF double submit against `__Host-plak-csrf`. A `Bearer` Authorization
header is accepted on the two deploy endpoints and the two CLI session endpoints
and nowhere else; a middleware answers 401 to any other request carrying one.
Deploy tokens no longer exist: a Bearer is either a CI ID token or a CLI access
token (§8).

Code: `api/origin_guard.py`, `api/admin.py` (`require_csrf`), `api/deploys.py`
(`BearerOutsideDeploysMiddleware`). Guarded by: `test_origin_guard.py`,
`test_admin_api.py`, `test_deploy_api.py`, `test_cli_api.py`.

### 7.9 Audit

The audit log is append-only, with a pseudonymised actor (HMAC-SHA256 with a
pepper) and a truncated IP (IPv4 /24, IPv6 /48) stored as its network notation.
The full address sits beside it encrypted under a separate key
(AES-256-GCM, with the row id as additional authenticated data so a ciphertext
copied onto another row does not decrypt), so revealing it is a deliberate,
itself-audited act.

Logged: every refusal and login redirect, every `_version` access, every view of
non-public content (one row per page, not per asset), every admin action, every
deploy and preview teardown, the CLI pairing actions, and every access to the
audit log itself. Viewing public content is deliberately not logged.

Logging is fail-open: a failed audit write never blocks the action it describes,
and the caller writes it as a separate step after the decision. The exception is
`write_strict`, used where the audit row is the only record that a
de-anonymisation happened at all; those callers fail closed, and a daily cap per
actor is serialised with a transaction-scoped advisory lock so two concurrent
requests cannot both pass it.

The vocabulary of actions, results and reasons, the retention tiers and the
lookup procedure live in `docs/audit-log.md`, which is the reference for them;
`audit/vocabulary.py` mirrors it and a test keeps the two in step.

Code: `audit/log.py`, `audit/pseudonymisation.py`, `audit/ip_crypto.py`,
`audit/vocabulary.py`, `audit/retention.py`. Guarded by: `test_audit.py`,
`test_audit_ip.py`, `test_ip_crypto.py`, `test_audit_api.py`,
`test_audit_vocabulary.py`.

## 8. Publishing: API, CI and CLI

The API base is `/-/api/v1` and addressing is on slugs, never UUIDs.

| Endpoint | Auth |
|---|---|
| `POST /-/api/v1/sites/{group}/{site}/deploys` | CI ID token, CLI token, or beheer session with CSRF |
| `DELETE /-/api/v1/sites/{group}/{site}/previews/{ref}` | the same three |

A live deploy is the default; the form field `preview=<ref>` makes it a preview
deploy. Teardown is idempotent: already gone is a 204, not a 404. Both endpoints
are fixed verbatim by this section, because CI workflows and the action depend
on their spelling.

Beside them the session API for the SPA: groups, sites, access, invitees, keys,
repository link, members at both levels, versions, rollback, previews, platform
members and the audit log. The same role rules (§3.6) apply.

**CI trust through OIDC.** There are no deploy tokens. CI authenticates with the
ID token its forge mints (`Authorization: Bearer <JWT>` from GitHub or Forgejo
Actions). The token is accepted when its `iss` is one of the configured issuers,
the signing key comes from that issuer's own discovery document and JWKS over
https, the algorithm is RS256, `exp` and `iat` are present within 60 seconds of
leeway, and `aud` is exactly `PLAK_BASE_URL`. Every URL fetched on a provider's
behalf is built from configuration, never from a token or a request, which is
the SSRF defence, together with https only, no redirects, a timeout and a size
cap.

Whether a verified token may deploy is a separate question: a site names one
repository (`site_repositories`). A token matches when its issuer is that
repository's provider and host and it carries the stored `repository_id` (and
`repository_owner_id` where present), because ids survive renames and transfers
while names do not. A Forgejo version that sends no ids matches on
`owner/repo` case-insensitively, and Plak then asks the Forgejo REST API whether
that name still has the stored ids, so a repository deleted and recreated under
the same name does not inherit the trust. A GitHub token without ids is refused.
A live deploy additionally requires an event out of `push`, `workflow_dispatch`
or `schedule` and, when one is configured, the live branch; previews and
teardown accept any ref and any event. Claims copied into the audit record
(`repository`, `ref`, `sha`, `run_id`, `workflow`, `event_name`) are each length
capped.

**CLI login.** `plak login` is an OAuth 2.0 device authorization grant (RFC
8628) Plak runs itself on top of the beheer SSO login: the CLI asks for a device
code and a user code, the member approves that code in the SPA with a fresh
session, and the CLI trades its device code for a one-hour access token plus a
rotating refresh token (idle 30 days, absolute 90). A refresh token presented
after it was rotated means two parties hold the chain, so the whole session is
revoked; a token presented inside a ten-second grace is a retry, not theft.
Every secret is `<prefix>_<selector>_<secret>`, stored as selector plus SHA-256
and compared in constant time against a dummy hash for unknown selectors. A CLI
token acts as its member, with exactly that member's roles.

**Previews.** Upsert per (site, ref) in one atomic
`INSERT ... ON CONFLICT DO UPDATE`; the expiry is the last deploy plus 30 days.
A successful upsert removes the replaced preview version, row and file tree.
Teardown and the daily cleanup job (03:00) remove the preview row, its version
and its files, and the job also sweeps orphaned preview versions, stale `_tmp`
directories and expired CLI device authorizations and sessions.

**Deleting.** Deleting a site cascades over versions, previews, invitees, keys,
site members and the repository link, and removes the file trees. A group can
only be deleted when it is empty, by a platform administrator.

**Error contract.** Every API error is `application/problem+json` (RFC 9457, NL
API Design Rules) with `code` as the machine-readable extension: 401, 403, 404,
409, 413, 422, 429 with `Retry-After`. The OpenAPI schema and a self-hosted docs
UI sit on `/-/api/docs`; the assets are local files because the CSP of §9 allows
no CDN.

The workflow examples, the curl fallback and the `publiceer` action
(`actions/publiceer/`) are in `docs/publishing.md`.

Code: `api/deploys.py`, `api/cli.py`, `api/admin.py`, `api/errors.py`,
`api/docs.py`, `ci/tokens.py`, `ci/trust.py`, `ci/providers.py`,
`cli/service.py`, `ingest/service.py`, `previews/cleanup_job.py`. Guarded by:
`test_deploy_api.py`, `test_cli_api.py`, `test_cli_service.py`,
`test_ci_tokens.py`, `test_ci_trust.py`, `test_ci_providers.py`,
`test_cleanup_job.py`, `test_api_documentation.py`, `test_error_handlers.py`.

## 9. The beheer interface

A Vue 3 SPA (runtime-only build, Vite base `/`), served by the application
itself from `PLAK_SPA_PATH` on the root of the beheer host, as pure ASGI
middleware outside the rate limit and inside the host separation. On the content
host it steps aside entirely.

The boundary is stated negatively, because the SPA is the fallback for the whole
host: the application claims `/-/...` (with only the SPA pages named in
`SPA_PAGE_PATHS` carved back out, so a typo in an API path stays a 404 instead
of coming back as the whole interface with status 200), the locations the web
pins down, and `/healthz`. Everything else is the SPA: an existing file is
served statically, every other path gets `index.html`.

Every SPA response carries a fixed header set: `Cache-Control`
(`immutable` for `assets/`, `no-cache` otherwise), the beheer CSP,
`nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`,
`X-Robots-Tag: noindex, nofollow` (§5.9) and
`Cross-Origin-Opener-Policy: same-origin`.

The beheer CSP is the strict regime beside the content CSP of §5.7:

```
default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:;
object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'
```

`form-action` has no fallback to `default-src`, so it is spelled out; it is
widened for the logout form only, with the content origin and, when RP-initiated
logout is on, the issuer origin, because a browser checks every redirect of a
form navigation against it. Preconditions that follow from `script-src 'self'`:
no inline scripts in the built `index.html`, no legacy plugin, a runtime-only
Vue build, and a design system that needs no runtime `<style>` injection.

The regime follows the host, not the path: a path rule used to make a refusal on
the content host distinguishable from an ordinary neutral 404. Responses that
carry no CSP of their own get it from the security-headers middleware: HTML on
the beheer host gets the full beheer CSP plus `noindex`, JSON gets
`frame-ancestors 'none'`. `/-/api/docs` carries its own, identical but for
`style-src`, which allows `'unsafe-inline'` because Swagger UI puts style
attributes on its elements; `script-src` stays `'self'`.

The SPA reads the content origin from `/me` and builds every shareable URL
(public URL, secret link, preview link, CI snippet) on it, never on
`window.location.origin`. Route guard: `/-/members` is platform administration,
and denial is the default when the session cannot be resolved. Interface
language is Dutch; the wire carries the English enum values.

Code: `platform/spa.py`, `security_headers.py`, `api/docs.py`,
`frontend/src/router.ts`, `frontend/src/format.ts`, `frontend/src/api/plak.ts`.
Guarded by: `test_spa.py`, `test_security_headers.py`,
`frontend/tests/build-output.test.ts`, `frontend/tests/router-guard.test.ts`,
`frontend/src/format.test.ts`.

## 10. Rate limiting

Fixed window with `Retry-After` and HTTP 429, no escalating penalties, plus a
global backstop limit per class as a DoS net. Classes and defaults
(`config.py`): login 10 per 60 s (backstop 1000), API 60 per 60 s (backstop
5000), content 600 per 60 s (backstop 20000), and `code` 10 per 900 s (backstop
500) for the secret-link code page. The key is the member or token when
authenticated and the IP for anonymous traffic. `/healthz` is exempt and SPA
assets sit outside the middleware entirely.

The login class covers the login paths of both flows, from the same constants
the routes are registered with, so moving a route cannot silently change its
class. Behind the ZAD router, trusted-proxy configuration
(`PLAK_TRUSTED_PROXIES`, X-Forwarded-For) is a required deployment setting.

Code: `ratelimit.py`, `net.py`, `constants.py`. Guarded by: `test_ratelimit.py`,
`test_app_integration.py`.

## 11. Deployment

Plak is **one component** on ZAD, because the application serves content, the
SPA and the host separation itself. Two web addresses on that one component
through `publish-on-web` with a dotted `domain-format` and `root-component`:
`beheer.plak.<domain>` and `plak.<domain>`. PostgreSQL and the content volume
are ZAD services, with the volume's size declared in the project file; for a
lot of content, MinIO through the existing `ContentStore` abstraction is the
considered route, not a bigger volume.

The deployment runs `replicas: 1`: sessions and rate-limit counters live in
process memory and the content volume is read-write-once. `/healthz` exists
internally only (§4a): the probe reaches the pod directly, so the path exists on
neither public host. Secrets (OIDC key or client secret, database credentials,
audit pepper, audit IP key, session secret) come in as user env vars; nothing
sits in the image or the repository. Migrations run as a job in the portal or at
container start, because ZAD has no notion of a Job.

`deploy/plak-project.example.yaml` is the annotated project file,
`docs/deploying-on-zad.md` the step-by-step guide; `dev/compose.yml` mirrors the
topology locally (nginx as a dumb proxy, app, PostgreSQL, mock OIDC), see
`docs/local-development.md`.

Guarded by: `test_zad_project_file.py`, `test_workflows.py`,
`test_app_integration.py`.

## 12. The data model

PostgreSQL with Alembic migrations; the whole schema is migration `0001_base`.
Tables: `members`, `groups`, `group_members`, `sites`, `site_members`,
`site_repositories`, `versions`, `previews`, `invitees`, `access_keys`,
`audit_log_entries`, `content_viewers`, `cli_device_authorizations`,
`cli_sessions`, `cli_refresh_tokens`.

Rules that live in the database rather than only in the application:

- The access base is one shared PostgreSQL enum type used by
  `groups.default_access_base`, `sites.access_base` and
  `previews.access_base_override`; `role` likewise for `group_members.role` and
  `site_members.role`. The migration hardcodes the value lists on purpose, as a
  frozen snapshot, and a test holds them against `constants.py`, order included,
  because in SQL that order is the sort order of the type.
- A preview override is all three fields or none (`ck_previews_access_override`).
- A version comes from a member or from CI, never both and never neither
  (`ck_versions_origin`). A CI deploy records its repository as text, not as a
  reference, so unlinking a repository does not rewrite deploy history.
- `uq_previews_site_ref`, `uq_sites_group_slug`, `uq_members_sso_subject`,
  `uq_invitees_site_identifier`, `uq_access_keys_selector`,
  `uq_site_repositories_site`.
- `groups.slug` refuses the reserved slugs by CHECK; emails and invitee
  identifiers are lowercase by CHECK.
- Access lives on the site; the group carries a default for new sites and a
  preview may override. A deploy never changes access policy.
- Deleting a site or a group cascades in the database and removes the file trees
  on the volume (§8).

Append-only on the audit log rests on triggers: `audit_log_no_update` refuses
every UPDATE always, `audit_log_delete_after_retention` refuses the DELETE of a
row that has not reached its retention, and `content_viewer_delete_after_retention`
does the same at 90 days. The retention itself is a SQL function
(`audit_log_retention`), so shortening a term costs a schema change rather than
a setting, and the purge job asks the database what has expired instead of
carrying a term of its own. `guard_last_group_admin` keeps a group from losing
its last admin (§3.5).

**One database account.** Plak talks to the database under one account, for the
app, for Alembic and for the purge job, because the shared PostgreSQL service on
ZAD hands out exactly one user; locally it is run the same way. The separation
between a migration, a runtime and a purge account that an earlier design
assumed does not exist. The triggers therefore apply to the application itself
as well, and what they cost is stated plainly in `docs/audit-log.md`: the
account owns the schema, so an owner can disable a trigger. The triggers stop
mistakes and off-hand commands, not a deliberate owner.

Code: `alembic/versions/0001_base.py`, `models/`. Guarded by:
`test_migrations.py`, `test_audit.py`, `test_constants.py`.

## 13. Quality and evidence

- **Backend**: pytest against a real PostgreSQL in a container
  (Testcontainers over the Podman socket, `just test`). Required suites: the
  access matrix (base times extras times visitor class times
  live/preview/`_version`/no-live-version/expired-preview/revoked-key-with-cookie,
  including byte-level equality of the neutral 404 and the slash redirect
  behaviour), serving path validation including percent-encoded traversal,
  fail-closed ingest, serving (redirects, MIME, 304, header set), rate limiting,
  the deploy API (CI trust, idempotent teardown, concurrent preview upsert,
  problem+json), storage lifecycle, host separation, origin guarding, OIDC, the
  audit log and the migrations.
- **Frontend**: vitest (`npm test` in `frontend/`), including a check on the
  built `index.html` for inline scripts against the CSP of §9.
- **E2E**: Playwright against the compose stack with mock OIDC on
  `http://localhost`, across both origins: logging in, creating a group and a
  site, uploading a dist fixture, deploying a preview through the API, viewing
  an older version through `_version`, creating a secret link and using it
  anonymously, the invitee flow including the return to the requested page after
  login, and deleting a site (`e2e/`).
- **Lint**: `just lint` runs `ruff check` over `src` and `tests`.
- **Security**: the living checklist in `docs/security.md` carries the items of
  this document with their status. DPIA and a pentest are conditions for
  production.

## 14. Out of scope, deliberately

Subdomain or wildcard per site; magic-link login for externals (secret links
cover externals, invitees need SSO); custom domains per group; object storage
(the recorded scaling route, not part of the first delivery); renaming group or
site slugs; email notification (inviting sends no mail, the administrator shares
the URL themselves); central session revocation (deactivating works immediately
because member status is checked per request).

## 15. References that no longer have a subject

Nothing cited by the code has lost its subject. Three rules from the earlier
design did disappear and are named here so an older reference is not left
guessing:

- **Deploy tokens** (`plak_<prefix8>_<secret32>` bearer tokens with a group or
  site scope, under the old §7 and §8) no longer exist. CI authenticates with an
  ID token from its forge and a person with a CLI token from `plak login` (§8).
- **The X-Accel-Redirect datapath and the nginx header discipline** under the
  old §5, together with the parity test between the dev path and the nginx path,
  are gone: the application serves content itself and there is only one path
  (§5).
- **Separate database accounts** for migrations, runtime and purging under the
  old §12 are gone: there is one account, and the triggers carry the guarantee
  (§12).
