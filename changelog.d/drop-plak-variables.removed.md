- The server no longer reads `PLAK_<NAME>` variables and no longer warns about
  them at startup: every setting is a `TOON_<NAME>` variable.
- The CLI no longer reads `PLAK_HOST`, `PLAK_SITE_ID`, `PLAK_ACCESS_TOKEN` and
  `PLAK_CONFIG_DIR`: use `TOON_HOST`, `TOON_SITE_ID`, `TOON_ACCESS_TOKEN` and
  `TOON_CONFIG_DIR`.
