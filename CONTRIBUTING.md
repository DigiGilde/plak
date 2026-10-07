# Contributing to Plak

Plak is built by the Digi Gilde and licensed under the EUPL-1.2. Issues
and pull requests are welcome. By contributing you agree that your work
is released under that licence.

## Proposing a change

- **Start with an issue** for anything bigger than a small fix, so we can
  agree on the direction before you spend time on it.
- **Pull requests go against `beta`.** Releases are cut from `beta`; do
  not target `main`.
- **Security problems do not go in an issue or a pull request.** Report
  them as described in [SECURITY.md](SECURITY.md).

## What a pull request needs

- **A changelog line.** A change to shipped behaviour adds one line under
  `## [Unreleased]` in `CHANGELOG.md`, in English, describing the effect
  rather than the implementation. Docs, tests and workflow changes need
  none. See [docs/releasing.md](docs/releasing.md).
- **Tests, at 100% coverage.** Every line and branch is covered by a
  test, or excluded with a marker and a reason. Backend and CLI use
  `pytest --cov`, the frontend `vitest --coverage`; `just coverage` runs
  all three. CI fails below 100%.
- **A clean `pre-commit` run.** Install it once with
  `uvx pre-commit install`; it runs ruff, the file checks, a secret scan
  and the check that bans em dashes and en dashes. CI runs the same set.
- **A change to the access gate, sessions, ingest or serving** comes with
  tests, and with the access matrix in `backend/tests/test_access_gate.py`
  updated. Refusals are neutral 404s, byte for byte identical.

## Language

Code, identifiers, comments, tests, commit messages, branch names and
documentation are English. The admin interface is bilingual (Dutch and
English): every string lives in `frontend/src/i18n/`, in both languages;
a Dutch literal in a component is a bug. Use a plain hyphen, never an em
dash or an en dash.

## Running it locally

[docs/local-development.md](docs/local-development.md) explains the dev
stack. It needs Podman (not Docker), `uv`, Node.js and `just`:

```bash
./dev/up.sh
```

`just --list` shows the other recipes. The design is in
[docs/design.md](docs/design.md).

## Conduct

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).
