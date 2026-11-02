- Plak is now called Toon, at `toon.rijks.app` and `beheer.toon.rijks.app`. The
  old addresses redirect, so links keep working. A visitor who came in through a
  secret link opens the full link once more.
- A site built with absolute URLs on `https://plak.rijks.app` has to be built and
  published again, because files it loads from the old host are blocked.
- Everyone signs in again once, and each machine runs `toon login`, because a CLI
  session from before the rename no longer works. "Linked CLI sessions" lists it
  until it expires, at most 30 days later.
- The CLI is the `toon` command instead of `plak`, and `toon --version` prints
  `toon-cli` and the version. An old CLI stops with `Error: HTTP 302`.
- The action is `DigiGilde/toon/actions/publish`, and a workflow changes its
  `uses:` line and its `host`.
- The action and the CLI translate the two old DigiGilde hosts to the new ones
  and warn on stderr, so a workflow that only changed its `uses:` line keeps
  publishing.
- The CLI reads `TOON_HOST`, `TOON_SITE_ID`, `TOON_ACCESS_TOKEN` and
  `TOON_CONFIG_DIR`, and falls back to the `PLAK_` name of each.
- The Claude Code plugin and its marketplace are `toon`, installed with
  `/plugin install toon@toon`.
- The skill checks that `toon` is this CLI and not the CLI of the TOON data
  format.
- Pull request comments and GitHub deployments that an earlier version made are
  still found and updated.
