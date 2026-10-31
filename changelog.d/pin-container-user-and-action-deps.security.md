- The container image declares its user as the numeric `1000:1000`, so a
  Kubernetes `runAsNonRoot` check can verify it.
- The publish action runs the CLI with `uv run --locked`, so it fails
  instead of resolving dependencies the lockfile does not pin.
