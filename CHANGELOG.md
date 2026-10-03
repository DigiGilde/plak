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

- `plak --version`, and a Plak CLI that stops with an upgrade hint when
  the server speaks a newer major API version than it supports.
- A public What's new page at `/-/whats-new`, linked from the footer
  next to the Plak version.
- The publish action can show a deploy on the pull request, as a
  GitHub deployment and as a comment with the link, and by default
  skips pull requests opened by bots.
- A deploy answers with the `url` where it can be seen, and the publish
  action passes it on as an output.
- A deploy also answers with who may see it, and the publish action's
  pull request comment says whether opening the link needs a sign-in.
- `plak publish` ends with the URL and version it published, and the
  publish action writes the same to the job summary.
- Every night Plak removes a site's live versions beyond the current one
  and the ones before it that the site keeps (five by default, set with
  `PLAK_LIVE_VERSIONS_KEPT`; a site admin can set another number per site),
  so a site no longer gets stuck on its storage quota; the Versions tab
  shows the site's usage and this rule, and is where a site admin sets it.
- `GET /-/healthz` on the admin host replaces the unreachable `/healthz`:
  `ok`, `degraded` or, with an unreachable database, `fail` (503), naming
  the failing checks, among them a nearly full content volume (`storage`)
  and a content root that is not mounted in production (`content_root`).
  Low space also writes an ERROR line at most once per hour, and platform
  administrators see how full the volume is on "Platformbeheer".

### Changed

- On ZAD a site may hold 200 MiB across all its versions (`PLAK_SITE_MAX_BYTES`),
  down from the 500 MiB default, because a ZAD volume is capped at 1Gi.
- The publish action needs no `host` for the DigiGilde instance.
- The publish action lives at `actions/publish` and the Claude Code skill
  is `plak-publish`; a workflow changes the path when it moves its pin.
- `plak preview-remove` reports the removal on stderr instead of stdout,
  like the other messages the CLI prints for people.
- The `API-Version` header follows the API from release to release: the
  minor goes up when a release adds to it, the patch when it changes it
  otherwise.

### Fixed

- The repository form on a site's Deploy tab closes when you switch to
  another site, instead of staying open for the previous one.
- The Plak CLI runs on Windows: it keeps the session in Windows
  Credential Manager, or else in `%APPDATA%\plak\hosts.json`.
- When `plak site link` cannot find a repository, or GitHub's anonymous
  lookup limit is used up, the CLI says why `gh` gave no IDs (not
  installed, not logged in, no access, `--no-gh`) and what to do.

### Security

- A version published from CI shows the repository its signed token
  names, so a renamed repository or a mistyped name on the site's link
  no longer shows up as its origin on the Versions tab; the Deploy tab
  takes over that name, with a `site_repository_rename` row in the
  audit log.

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
