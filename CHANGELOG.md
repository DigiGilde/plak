# Changelog

Every change to what Plak ships, newest first, in the format of
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). A section is
a CalVer version, the date of its release: `## [2026.10.1]` is the tag
`v2026.10.1`, and a second release on the same day adds `.N`. Every
push to `beta` goes to staging; one with entries under `[Unreleased]`
and no hold marker becomes a release, with those entries as its notes,
and goes to production. How to write an entry, the hold marker and the
rest of the model are in [docs/releasing.md](docs/releasing.md).

## [Unreleased]

<!-- release: hold -->

### Added

- `plak --version`, and a Plak CLI that stops with an upgrade hint when
  the server speaks a newer major API version than it supports.
- A public What's new page at `/-/whats-new`, linked from the footer
  next to the Plak version.

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
