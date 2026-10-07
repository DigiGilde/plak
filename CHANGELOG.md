# Changelog

Every change to what Plak ships, newest first, in the format of
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). A section is
a CalVer version, the date of its release: `## [2026.10.1]` is the tag
`v2026.10.1`, and a second release on the same day adds `.N`. A push
to `beta` with entries under `[Unreleased]` and no hold marker becomes
a release, with those entries as its notes, and goes to production. How to write an entry, the hold marker and the
rest of the model are in [docs/releasing.md](docs/releasing.md).

## [Unreleased]

<!-- release: hold -->

### Added

- `site-id` for the publish action and `--site-id` for the CLI; the site
  id on the Deploy tab and in the API.
- A group admin can change a group's name and a site admin a site's title,
  on the group's and the site's Settings tab; the audit log keeps the
  previous name or title.

### Changed

- The What's new page no longer opens with the version you use; the footer
  shows it.
- The root of the content host redirects to the landing page of the admin
  environment, which now also says where the name comes from and lets a
  visitor without an account switch between Dutch and English.
- The header of the API docs uses the colours, wordmark and text sizes of the
  admin environment, in light and in dark mode.
- The admin environment uses the system font instead of RijksSans, which is
  licensed for Rijksoverheid publications only.
- A new repository link requires the site id, and so does every
  existing link of a repository that is linked to several sites; other
  existing links keep working without it until another repository is
  linked or a site admin requires it.
- Deleting a site moved from the site's Overview tab to its new Settings tab.

### Fixed

- Production runs the image its release names, so the footer shows the
  version instead of "Wat is er nieuw"; it ran a build of the same commit
  without its version.
- Files left behind by an interrupted publish, cleanup or delete no
  longer count against a site's storage quota: the nightly cleanup sets
  them aside and removes them a week later, and a site created again
  under a deleted name starts empty.
- A lone surrogate in a group name, site title, identifier, secret link
  label or audit lookup reason is refused with a 422 instead of a server
  error.

### Security

- On the content host a HEAD request gets the headers of the GET instead
  of a 405, under the same gate and audit, so link checkers and monitors
  see what a browser sees. The admin host and the login paths are
  unchanged.
- A workflow that names its site id publishes only to that site: Plak
  accepts a CI token bound to it, so a renamed, deleted or mistyped
  address can no longer send its build to someone else's site.
- A page never sees a secret link that was not meant for it: Plak takes a
  `key` in the shape of a secret link out of the address before it answers,
  also on a refusal, and refuses service worker scripts, which could read a
  link before Plak does.

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

[Unreleased]: https://github.com/DigiGilde/plak/compare/v2026.10.7...HEAD
[2026.10.7]: https://github.com/DigiGilde/plak/compare/v2026.10.4...v2026.10.7
[2026.10.4]: https://github.com/DigiGilde/plak/compare/v2026.9.30...v2026.10.4
[2026.9.30]: https://github.com/DigiGilde/plak/releases/tag/v2026.9.30
