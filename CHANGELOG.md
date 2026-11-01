# Changelog

Every change to what Plak ships, newest first, in the format of
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). A section is
a CalVer version, the date of its release: `## [2026.10.1]` is the tag
`v2026.10.1`, and a second release on the same day adds `.N`. A pull
request describes its change in a fragment under `changelog.d/`; a
release pull request turns the fragments into the next section here,
and merging it tags the release and sends it to production. How to
write a fragment and the rest of the model are in
[docs/releasing.md](docs/releasing.md).

## [Unreleased]

Merged but not released yet: the fragments in [changelog.d/](changelog.d/README.md).

## [2026.11.1]

### Added

- A group admin can change a group's address, and a site admin a site's
  address. The old address redirects whoever may see the site through the
  30th day, can be taken back until then, and is free for others after the
  nightly cleanup.
- A group admin can give a site an address that another site in the group
  recently gave up, which ends that site's redirect at once.
- The Deploy tab of a site names the old addresses its workflows may still
  hold, after a change of the site's address or its group's.
- The API for it: `PUT /groups/{group}/slug` and `PUT /sites/{group}/{site}/slug`,
  `previousSlugs` on groups and sites, and `slugRedirectDays` on `/me`.
- "Choose a folder" on a site's Overview tab picks a whole folder to publish
  without dragging it.
- `PLAK_OPERATOR_NAME` and `PLAK_OPERATOR_EMAIL` name the organisation that
  runs an installation and how to reach it, on the privacy and accessibility
  pages and at `GET /-/api/v1/operator`.
- `PLAK_STORE_COMPRESSED=false` keeps new deploys uncompressed and puts stored files back
  uncompressed at start, so the app can be rolled back to a release from before compression.

### Changed

- Creating a group or site answers `SLUG_EXISTS` also for a slug that was
  recently the address of another group or site.
- The creation budget of 20 per hour counts changes of address as well.
- The audit log filters on group and site also find a change of address by
  its old slug, and a group's change by each of its sites.
- The API knows only the current address of a group or site: an old one
  answers 404, also to a workflow.
- A repository link that still took workflows without the site id requires
  it once its site or group gets another address.
- `plak publish` streams the bundle from a temporary file instead of
  holding it in memory three times.
- The CLI reuses one connection per run, and retries once, after a
  second, a token refresh that never reached the server, a preview
  removal, and the CI token request. A publish is never retried.
- The privacy and accessibility statements are the standard statements of
  the software, for any installation: they name its operator and address,
  the rights and where to complain, and no longer claim that an
  organisation is stored.
- Text files are stored compressed, so a version published from now on takes up less of the site's storage and quota.
- Published sites load faster: HTML, CSS, JavaScript and other text files are sent compressed with brotli or gzip.
- An empty group now shows a "Publish a site" button in its empty state, on the group page and on the overview, for members who may publish there.
- The admin pages load with fewer database round trips, and an admin request
  no longer writes to the database. A member's last login is now the moment
  they signed in, not their latest request.
- Large files on a published site and large deploys go through in fewer,
  bigger reads.
- The platform member list shows when someone was last active: the later of
  their last sign-in and their last use of a CLI session.
- Error messages and interface texts use two short sentences instead of a semicolon.
- Creating a new site takes what publishing a new version takes: a folder, the
  loose contents of a folder, an archive or a single HTML file, dragged or
  chosen with "Choose a folder", in the sheet and on the Overview and Group
  pages. A refused drop on those pages now opens the sheet and explains there.
- A bundle with `_encoded` in the root of the site is refused, like `_preview` and `_version`.
- The NCSC-NL routes in `security.txt` (preamble, `Policy` and `Contact`
  lines) now appear only with `PLAK_SECURITY_TXT_NCSC=true`, so an
  installation outside the central government no longer points reporters
  at NCSC-NL.
- `security.txt` takes its mail `Contact` from `PLAK_OPERATOR_EMAIL`, after
  the GitHub advisory form, so security reports about an installation reach
  the organisation that runs it. Without the setting the file has no mail
  line of its own.
- A page view on a published site costs one database query where it took
  up to four, and redeeming a secret link checks the key once.
- The admin runs on design system 0.13.2: the status bar at 200% text and
  the form buttons (a row on wide screens, stacked on narrow ones) follow
  the new defaults.
- The admin is set in Inter instead of the system font, so text looks the
  same on every computer.
- A screen reader hears "other website" after a link to
  a published site or another domain.
- The file field is one button across its whole width, and shows a chosen
  file with the icon of its format. While something is dragged, the field
  of a site says "Drag the file or folder here".
- On a narrow screen, the tabs of a site or a group that do not fit go
  behind a "More tabs" button instead of scrolling sideways.
- The button with the actions of a row shows three dots side by side, as
  the menus of the design system do.
- "Choose a folder" and "Publish version" under the upload zone no longer
  touch each other.

### Deprecated

- `storageRef` on a version in the admin API is deprecated and goes away in API 2.

### Fixed

- After a navigation inside the admin, focus moves to the page heading (or
  the tab content) and a screen reader hears the page title.
- The browser tab title follows a language switch at once.
- The approve and deny buttons of the CLI link page and the actions in the
  platform members and versions menus are no longer disabled. A second
  click while busy is ignored, and a blocked member action explains why.
- Adding or changing a site role for a member without a verified e-mail
  address answers with that member, not with another member without one.
- Published sites keep answering while old versions are removed from disk,
  for example when a preview is redeployed or a site is deleted.
- Dragging the contents of a folder instead of the folder itself reports
  "3 files packed" instead of a folder "site" that does not exist,
  and a refusal speaks of "these files".
- On "Platform administration", a member whose account carries no name is
  listed by email address at the size of the rest of the row, instead of in
  small supporting text.
- A new group on the overview can be chosen in the publish sheet straight away, without a reload.
- "Publish version" waits while a new folder is being read or packed, instead of
  publishing the file that was chosen before it.
- A refused upload leaves nothing chosen, so "Publish version" and "Put online" can no
  longer publish the file that was chosen before it.
- One failed read of who is logged in no longer makes every later page of
  the admin fail until a reload.
- The code page for a secret link shows its introduction in the normal text
  colour instead of grey, announces a wrong code with "Error:"
  before the message, and links the accessibility statement.
- The site pages fetch the group once instead of once per tab, and a slow
  answer no longer overwrites a newer one when you switch quickly.

### Security

- The audit pepper and the IP key can be rotated without losing the ability to
  look up or decrypt older audit rows: `PLAK_AUDIT_PEPPER_PREVIOUS` and
  `PLAK_AUDIT_IP_KEY_PREVIOUS` hold comma-separated lists of earlier values,
  and `actor-pseudonym` returns the pseudonyms under earlier peppers as
  `previousPseudonyms`.
- Plak fetches the signing keys and the discovery document of the identity
  provider again every hour, so a key the provider withdraws stops being
  trusted without a restart.
- A CI ID token that carries a `jti` (GitHub) is accepted once. A second use
  within its lifetime is refused, and the audit log records it as
  `CI_TOKEN_REPLAYED` instead of `CI_TOKEN_INVALID`.
- A non-admin reaching the platform member routes is refused and audited
  before a malformed request is looked at.
- JSON and other non-HTML answers of the admin host carry `Referrer-Policy`
  and `X-Frame-Options`.
- Content of a site that is not public, and every view of an older version,
  is no longer stored in the browser. A page of another site without
  "Shield from other sites" could read it from the cache, past the access
  check, also after the visitor had lost access. Restricted sites load
  their files again on every page.
- The container image declares its user as the numeric `1000:1000`, so a
  Kubernetes `runAsNonRoot` check can verify it.
- The publish action runs the CLI with `uv run --locked`, so it fails
  instead of resolving dependencies the lockfile does not pin.

## [2026.10.31]

### Added

- `site-id` for the publish action and `--site-id` for the CLI, and the
  site id on the Deploy tab and in the API.
- A group admin can change a group's name and a site admin a site's title,
  on the group's and the site's Settings tab, saved without a button. The
  audit log keeps the previous name or title.

### Changed

- A new repository link requires the site id, and so does every existing
  link of a repository that is linked to several sites: a workflow of such
  a repository publishes again once it names the site id. Other existing
  links keep working without it until another repository is linked or a
  site admin requires it.
- Versions are stored under the site's id, so a site created again under a
  deleted name starts empty. The upgrade moves the existing directories and
  needs the content volume. After it, the previous image no longer starts.
- The summary under an access choice no longer repeats the option just
  chosen, on a site's Access tab, in the publish sheet and for a group's
  default access. A warning says when nobody can view the site, and the
  publish sheet only says what happens after publishing.
- On a group's Settings tab the secret links and invitees switches of the
  default access sit under their own "Uitzonderingen" heading.
- The What's new page no longer opens with the version you use; the footer
  shows it.
- The root of the content host redirects to the landing page of the admin
  environment, which now also says where the name comes from and lets a
  visitor without an account switch between Dutch and English.
- The header of the API docs uses the colours, wordmark and text sizes of the
  admin environment, in light and in dark mode.
- The admin environment uses the system font instead of RijksSans, which is
  licensed for Rijksoverheid publications only.
- On ZAD the content volume is 10Gi and a site may again hold 500 MiB
  across all its versions, the `PLAK_SITE_MAX_BYTES` default.
- The image runs Python 3.15 on Debian 13 (trixie), without pip.
- Deleting a site moved from the site's Overview tab to its new Settings tab.

### Fixed

- Production runs the image its release names. It ran a build of the same
  commit without its version, so the footer showed "Wat is er nieuw"
  instead of the version.
- Files left behind by an interrupted publish, cleanup or delete no longer
  count against a site's storage quota: the nightly cleanup sets them aside
  and removes them a week later.
- A lone surrogate in a group name, site title, identifier, secret link
  label or audit lookup reason is refused with a 422 instead of a server
  error.

### Security

- On the content host a HEAD request gets the headers of the GET instead
  of a 405, under the same gate and audit, so link checkers and monitors
  see what a browser sees. The admin host and the login paths are
  unchanged.
- The image patches OpenSSL (CVE-2026-84782) and moves certifi to
  2026.10.23 (PYSEC-2026-4320), also in the CLI; the SPA builds with
  vite 8.3.5 and undici 8.12.3.
- joserfc moves to 1.7.7, which rejects an oversized token before it
  splits it (GHSA-9pg5-8c7x-5hfx).
- pyasn1 moves to 0.6.5 (CVE-2026-119194, CVE-2026-119195, CVE-2026-119196,
  CVE-2026-119197).
- The SPA's tests run with glob 14.0.3 (GHSA-3g3m-2fjr-6pw4) and minimatch
  11.0.3 (GHSA-g3hv-75wg-m249).
- A page never sees a secret link that was not meant for it: Plak takes a
  `key` in the shape of a secret link out of the address before it answers,
  also one a mail client cut short and also on a refusal, and refuses
  service worker scripts, which could read a link before Plak does.
- A workflow that names its site id publishes only to that site: Plak
  accepts a CI token bound to it, so a renamed, deleted or mistyped
  address can no longer send its build to someone else's site.

## [2026.10.7]

### Security

- The container runs on a Python base image with patched Perl packages
  (CVE-2026-13221, CVE-2026-8376, CVE-2026-42496 and four more), and the
  admin build uses source-map-js 1.2.2 (GHSA-68fv-2mgg-jv7q).
- Published sites may run WebAssembly (`'wasm-unsafe-eval'`), so a
  Pagefind search works on a site with "Shield from other sites" off;
  JavaScript `eval()` stays blocked.
- A 304 carries the same content CSP and Referrer-Policy as the full
  answer, so turning a site switch or secret links on or off reaches pages
  a visitor already has in their cache.
- The front page, the code page for a secret link and the neutral 404 on
  the content host run no script and admit only their own stylesheet, by
  hash, instead of sharing the published sites' policy.

### Changed

- The publish action's pull request comment calls the version it shows
  "Plak version": Plak's id for that upload, next to the commit.

## [2026.10.4]

### Added

- A public What's new page at `/-/whats-new`, linked from the footer
  next to the Plak version.
- `plak --version`. The CLI stops with an upgrade hint when the server
  speaks a newer major API version; `plak logout` then still forgets the
  local session.
- The Plak CLI runs on Windows, with the session in Windows Credential
  Manager or else in `%APPDATA%\plak\hosts.json`.
- A deploy answers with the `url` where it can be seen and who may see
  it; `plak publish` ends with that URL and the version, and the publish
  action passes the URL on as an output and in the job summary.
- The publish action can show a preview on its pull request, as a GitHub
  deployment, as a comment with the link and whether it needs a sign-in,
  or both; it skips pull requests opened by bots by default.
- Every night Plak removes the live versions a site no longer keeps: the
  current one and five before it by default (`PLAK_LIVE_VERSIONS_KEPT`),
  another number per site on the Versions tab, which also shows the
  site's usage. A site no longer gets stuck on its storage quota.
- `GET /-/healthz` on the admin host replaces the unreachable `/healthz`:
  `ok`, `degraded` or `fail` (503, no database), naming the failing
  checks, among them a nearly full content volume and, in production, an
  unmounted content root. Low space also logs an ERROR at most once an
  hour, and "Platformbeheer" shows how full the volume is.
- The Deploy tab marks a repository whose IDs were entered by hand "Nog
  niet bevestigd" (not confirmed yet) until its first deploy confirms
  them; the API returns `idsConfirmed`.

### Changed

- The publish action lives at `actions/publish` and needs no `host` for
  the DigiGilde instance; the Claude Code skill is `plak-publish`. A
  workflow changes the path when it moves its pin.
- On ZAD a site may hold 200 MiB across all its versions
  (`PLAK_SITE_MAX_BYTES`), down from the 500 MiB default, because a ZAD
  volume is capped at 1Gi.
- `plak preview-remove` reports the removal on stderr, like the CLI's
  other messages for people.
- The API documentation and the OpenAPI schema are in English, and in
  Dutch through a switch on the page, the language on your profile or
  `Accept-Language`.
- The `API-Version` header follows the API from release to release: the
  minor goes up when a release adds to it, the patch when it changes it
  otherwise.

### Fixed

- The repository form on a site's Deploy tab closes when you switch to
  another site.
- When `plak site link` finds no repository, or GitHub's anonymous lookup
  limit is used up, the CLI says why `gh` gave no IDs and what to do.

### Security

- A version published from CI shows as its origin the repository its
  signed token names, not the name typed on the site's link. A renamed
  repository's new name replaces the old one on the Deploy tab, logged as
  `site_repository_rename`.

## [2026.9.30]

The first tagged version: what ran in production on 30 September 2026.

### Added

- Publish a static site under `/{group}/{site}/`, with a live version,
  previews and earlier versions to look back at.
- Five access levels per site: public, secret link, SSO Rijk, group
  members and invitees by email address, with an override per preview.
- The Plak CLI (`plak`): log in with a device code, create groups and
  sites, link a repository and publish, all from the terminal.
- The `publiceer` action for GitHub Actions and Forgejo Actions, which
  publishes without a secret on an OIDC token from the linked repository.
- The Plak plugin for Claude Code, which publishes a preview by default
  and goes live only when you ask for it.
- The admin at `/admin`, in Dutch and English, for groups, sites,
  members, access, previews, versions and deploys.
- Login through SSO Rijk, with sessions that are rechecked at the
  identity provider and end on a back-channel logout.
- An append-only audit log with a hash chain, pseudonymised actors and
  encrypted IP addresses.
- Deployment on ZAD as one component, from an image that is scanned by
  Trivy and carries SBOM and provenance attestations.

[Unreleased]: https://github.com/DigiGilde/plak/compare/v2026.11.1...HEAD
[2026.11.1]: https://github.com/DigiGilde/plak/compare/v2026.10.31...v2026.11.1
[2026.10.31]: https://github.com/DigiGilde/plak/compare/v2026.10.7...v2026.10.31
[2026.10.7]: https://github.com/DigiGilde/plak/compare/v2026.10.4...v2026.10.7
[2026.10.4]: https://github.com/DigiGilde/plak/compare/v2026.9.30...v2026.10.4
[2026.9.30]: https://github.com/DigiGilde/plak/releases/tag/v2026.9.30
