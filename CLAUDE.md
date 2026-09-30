# Plak: instructions for Claude

An addition to the global instructions. What is written here applies in
this repo.

## Test coverage: 100%

Every line and every branch is covered by a test, or explicitly excluded
with a reason. There is no third category. A branch you do not test is a
branch nobody knows the behaviour of.

- **New code arrives with its tests.** Not in a next round, not "not
  just yet": in the same change.
- **Excluding is allowed, silently is not.** If you genuinely cannot
  reach a path in a test, mark it (`# pragma: no cover - <why>` in
  Python, `/* v8 ignore start */ ... /* v8 ignore stop */` in
  TypeScript, with a comment saying why). `v8 ignore next` and
  `v8 ignore if` are silently ignored by this vitest setup, so a marker
  of that kind excludes nothing. A marking is a claim a reviewer can
  contradict; an uncovered line without a marking is a blind spot
  nobody sees. Reading `coverage-final.json` directly does not show the
  exclusions; the text or html report does.
- **Cover behaviour, not lines.** A test that only touches a line to
  make the counter go up is worse than no test: it suggests coverage
  that is not there. Every branch comes with the question of what goes
  wrong if that side runs and the other one does not.
- **Error paths and refusals weigh heaviest.** On an authorization
  check the refusal is the interesting case, not the pass. For the
  access gate, the sessions, the ingest and the serving: every ground
  for refusal has a test of its own.
- **Measure it, do not guess.** `just coverage` runs the three suites
  with coverage (backend and CLI `pytest --cov`, frontend
  `vitest --coverage`). In a report, name the measured number, never an
  estimate.

All three suites sit at 100% and the floors are set there
(`backend/pyproject.toml`, `cli/pyproject.toml`,
`frontend/vite.config.ts`), so anything uncovered and unmarked fails CI.

## Language

Dutch for everything a person sees: UI texts, error messages, OpenAPI
descriptions, the `just --list` recipe descriptions and the output of the
shell scripts in `dev/`.

English for everything around it: code and identifiers, code comments,
tests, commit messages, branch names, `README.md`, `SECURITY.md` and
everything under `docs/`, and the route paths of the admin SPA
(`/-/sessions`, `/-/profile`, `/cli-link`). A path is an address, not
interface text, and the SPA answers in two languages under one.

The admin SPA is the exception: it is bilingual, Dutch and English.
Every string it shows lives in `frontend/src/i18n/`, in a shared
catalogue plus one section per area, always in both languages; a key in
one and not the other is a compile error. Which language a member gets
follows their own choice on their account (`members.language`, set on
`/-/profile`), then their browser, then English. So a Dutch literal in a
component is a bug, and `frontend/tests/dutch-literals.test.ts` says so.

The `title` and `detail` of a problem+json answer have both languages
too: they live in the catalogue in `backend/src/plak/messages.py` and
the API renders the language the request asks for in `Accept-Language`.
English is what a client that asks for nothing gets; the admin SPA
sends the language it is showing, so a refusal arrives in the language
on screen. A new message is added to both catalogues at once, which
`test_messages.py` enforces.

The file names under `docs/` are English (`security.md`, `audit-log.md`,
`publishing.md`, `local-development.md`, `deploying-on-zad.md`), matching
their English contents. Where a doc quotes a Dutch label, route or
message, the Dutch is quoted literally, with a short English gloss only
where the meaning is not obvious.

`plugin/skills/plak-publiceren` is English: it is distributed to people
outside this repository, as the `plak` plugin. Two things stay Dutch in
it: the user phrasings it has to recognise or speak back ("werk mijn
site bij", "zet dit online"), and the eval prompts in `plugin/evals/`,
which are the words a Dutch user types. Where it quotes CLI output, the
quote is the English string the CLI really prints.

The CLI (`cli/`) is English all the way out: identifiers, the argparse
help and description texts, and everything it prints to stdout and
stderr. The same holds for the step names, job names and echoed
messages in `actions/publiceer/action.yml` and in the workflows under
`.github/workflows/`, except where a name is a contract with the
platform: the ZAD deployment and GitHub environment `productie` and the
component `beheer`. It is developer tooling with English
documentation in a repository that goes public, not a part of the
interface. That now holds for the text it relays too: the CLI sends no
`Accept-Language`, so the `detail` it prints comes back in English.

No em dashes or en dashes, not in code and not in docs.

## Design of the interface

The interface follows `@nldd/design-system` and the design guidelines
that package ships (`design-guidelines.md` in the skill). Compose with
components; custom CSS is the exception and then uses `--primitives-*`.
When in doubt about a pattern: look at how regelrecht
(`github.com/minbzk/regelrecht`) and waggle
(`code.overheid.nl/robbertbos/waggle`) do it, those are the house
projects that have taken the system furthest.

## Security

The access gate, the sessions, the ingest and the serving carry the
security design. A change there never goes without tests, and never
without the access matrix in `backend/tests/test_access_gate.py` moving
along with it. Refusals are byte-identical neutral 404s; that is a
requirement, not an implementation detail.

## Containers

Podman, not Docker: `Containerfile`, `podman compose`,
`dev/compose.yml`. Deleting goes with `trash`, never with `rm`.
