- `plak publish` streams the bundle from a temporary file instead of
  holding it in memory three times.
- The CLI reuses one connection per run, and retries once, after a
  second, a token refresh that never reached the server, a preview
  removal, and the CI token request. A publish is never retried.
