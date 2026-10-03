# Releasing

Plak releases itself, from `CHANGELOG.md`. Nobody picks a version
number and nobody cuts a release by hand; what a person does is write
one line under `## [Unreleased]` per change, in the same pull request.

## The model

- **A push to `beta` deploys nowhere.** It builds, scans and attests an
  image; nothing on ZAD runs it. Only a release tag reaches `productie`.
- **An entry under `[Unreleased]`, without the hold marker, makes that
  push a release.** The release step turns `[Unreleased]` into a
  versioned section, sets the component versions, commits that as
  `Release vYYYY.M.D` and gives it an annotated tag `vYYYY.M.D[.N]`.
  The tag goes to production, and once it runs there it gets a GitHub
  Release with that section as its notes.
- **Nothing under `[Unreleased]` means no release.** A release needs
  notes, and the entries are the notes; nothing is made up from commit
  subjects.
- **A dependency update reaches production with the next release.** If
  it should go sooner, a fix for an advisory for instance, write an
  entry for it (under `### Security` when it is one), and the push that
  merges it releases.

All of it lives in one script, `.github/scripts/release.py`, tested in
`backend/tests/test_release.py`. `.github/workflows/release.yml` runs it
on every push to `beta`; `deploy.yml` takes the tag to production and
publishes the GitHub Release.

## Tags

Versions are CalVer tags, the date of the release in Europe/Amsterdam:

- `v2026.10.1` for the first release of a day,
- `v2026.10.1.1`, `v2026.10.1.2`, ... for a second and third that day.

No zero padding (`v2026.1.5`, not `v2026.01.05`), and a date that exists.
The pattern is `^v[0-9]{4}\.[0-9]{1,2}\.[0-9]{1,2}(\.[0-9]+)?$`;
`release.py validate-tag <tag>` checks the rest. A new tag is always newer
than the newest one, so a runner with a wrong clock refuses rather than
cutting a release that sorts before the last.

In `CHANGELOG.md` a released section is headed by the version alone,
without the `v` and without a date, because the version already is the
date: the tag `v2026.10.1` has the section `## [2026.10.1]`. The older
form `## [v2026.10.1] - 2026-10-01` fails the check.

`v2026.9.30` is the baseline, tagged by hand: what ran in production on
30 September 2026, before releases were automatic. Every tag after it
comes from the release step.

## When a push to beta releases

`release.py decide` answers with a route:

| Route | When |
|---|---|
| `hold` | `[Unreleased]` holds the line `<!-- release: hold -->` |
| `release` | `[Unreleased]` has entries |
| `none` | anything else: no release |

### Shipped paths

A change counts as shipped when it touches what ends up in the image, the
CLI, the plugin or the action. Shipped paths do not decide whether a push
releases; they drive the changelog warning and the hook (below) and
which component gets a new version. The list is `SHIPPED_PATHS` in
`release.py`, and that constant is the only place it lives:

- `backend/src/`, `backend/pyproject.toml`, `backend/uv.lock`,
  `backend/alembic/`, `backend/alembic.ini`
- `frontend/src/`, `frontend/package.json`, `frontend/package-lock.json`,
  `frontend/index.html`, `frontend/vite.config.ts`, `frontend/public/`
- `containers/plak/`
- `cli/plak_cli/`, `cli/pyproject.toml`, `cli/uv.lock`
- `plugin/`, except `plugin/evals/`
- `actions/publish/`

Tests never count, wherever they sit: a `tests/` or `__tests__/`
directory, a `*.test.*` or `*.spec.*` file, `test_*.py`, `conftest.py`.
Neither do docs, workflows or the dev stack.

## Writing an entry

One line per change under `## [Unreleased]`, below the heading that fits:

| Heading | For |
|---|---|
| `### Added` | something that was not there before |
| `### Changed` | something that works differently now |
| `### Deprecated` | something that will go away |
| `### Removed` | something that went away |
| `### Fixed` | a bug that is gone |
| `### Security` | anything that touches security, whatever else it is |

Write the effect, not the implementation: what a member, a publisher or
an operator notices. "Previews stay reachable after a redeploy", not
"Move the preview lookup into the resolver". A line may wrap onto an
indented next line; prose outside a list item, a heading that is not in
the table, a heading twice or a heading without entries fails the check.

A pull request that changes a shipped path but deserves no entry (a
refactor without effect, a lockfile refresh) says so in its description,
on a line of its own: `No changelog entry: <reason>`. Its change then
waits for the next release.

### Holding a release

Put `<!-- release: hold -->` on a line of its own anywhere in
`[Unreleased]` to stop pushes to `beta` from releasing, for instance
while a feature lands over several pull requests. Entries keep
collecting under it. Taking the line out, in a pull request, releases
everything that collected.

### Released sections are frozen

Once a section is tagged, its text is what the GitHub Release says. A
section is frozen when the base branch has it and its tag exists; the
check fails a pull request that edits, renames or removes one. A
correction goes under `[Unreleased]` as a new line. A section that only
the pull request adds is new, not edited, even for a tag that exists
already: that is how the section for the hand-made `v2026.9.30` came in.

## Component versions

A release writes the version without the `v` (`2026.10.1`). Nobody
edits these lines by hand. The image and `publiccode.yml` get it on every
release; the CLI and the plugin only when they changed since the
previous tag:

- **The image**: `[project] version` in `backend/pyproject.toml` and the
  version of the editable package `plak-api` in `backend/uv.lock`;
  `version` in `frontend/package.json` and both places it appears in
  `frontend/package-lock.json`. Every tag builds a new image, so these
  follow every release. What the running server and the footer show
  still comes from the tag itself, through `PLAK_VERSION` at build time.
- **The CLI**: `[project] version` in `cli/pyproject.toml` and the
  version of the editable package `plak` in `cli/uv.lock`, when anything
  under `cli/plak_cli/`, `cli/pyproject.toml` or `cli/uv.lock` changed,
  its own version line aside.
- **The plugin**: `version` in `plugin/.claude-plugin/plugin.json`, when
  anything under `plugin/` changed, `plugin/evals/` and its own version
  line aside. That manifest is the only place the plugin version lives;
  the marketplace entry carries none, and a pull request leaves it alone.
- **`publiccode.yml`**: `softwareVersion` and `releaseDate`, on every
  release.
- **The API**: `backend/openapi.json` and `API_VERSION` in
  `backend/src/plak/main.py`, on every release; see below.

The first release sets all of them. The CalVer versions are valid PEP 440
and sort above the `0.x` versions before them, so an installed CLI or
plugin sees them as an upgrade. A test-only change leaves a component's
version alone.

## The API contract

The API has a SemVer version of its own, in the `API-Version` header on
every response and in `info.version` of its schema. The CalVer of a
release says when; the API version says whether a client still fits.
The CLI reads only its major (`docs/publishing.md`).

- **`backend/openapi.json` is the contract a release shipped.** Only the
  release step writes it, into the release commit, so the file at a tag
  is what clients of that release see. A pull request leaves it alone;
  there is nothing to keep up to date by hand.
- **The major moves by hand, together with the path.** A breaking change
  means `/-/api/v2` and `API_VERSION = "2.0.0"` in
  `backend/src/plak/main.py`, in the same pull request.
  `test_openapi_file.py` holds the two to each other.
- **Minor and patch are set by the release.** It prints the schema with
  `python -m plak.api.openapi_file` and compares it, with
  [oasdiff](https://github.com/oasdiff/oasdiff), with
  `backend/openapi.json` at the previous tag:

  | What changed | Version |
  |---|---|
  | nothing in the schema | stays |
  | only what oasdiff does not list, such as a description | patch + 1 |
  | something oasdiff lists as compatible, such as a new endpoint | minor + 1 |
  | a breaking change, with the major raised | the new major, `.0.0` |
  | a breaking change, without | the release refuses |

  A minor or patch edited by hand in `API_VERSION` counts for nothing;
  the previous release's version is the starting point.

The release workflow passes the schema and oasdiff to
`release.py promote --spec <file> --oasdiff <binary>`.

A tag without `backend/openapi.json`, such as the hand-made `v2026.9.30`,
counts as no contract yet: the release after it writes the file at the
version the code has.

### The API check

`.github/workflows/api.yml` runs `release.py api-check` on every pull
request and in the merge queue. It compares the schema of the pull
request with `backend/openapi.json` at the newest tag and fails on a
breaking change unless the major went up. On a pull request, every
change oasdiff lists goes into one sticky comment, with the version the
next release gives the API; the comment goes away when there is none.
Until a release has written the file, the check passes with a notice.

oasdiff comes from the image in `.github/oasdiff/Containerfile`, pinned
by tag and digest, so Dependabot bumps it with the other base images. The
workflow copies the static binary out of it and never runs the image. It
runs with external references off.

## The release workflow

`.github/workflows/release.yml` runs on every push to `beta`, one run at
a time and in push order.

1. **`decide`** runs `release.py decide`, without any token. On `hold`
   or `none` that is the end of it.
2. **`prepare`** prints the API schema, gets oasdiff and writes the
   release with `release.py promote`. It hands the staged change on as a
   patch. This job runs `uv sync` and the backend, and it never sees the
   environment, the App's key or a token.
3. **`publish`**, in the environment `release`, applies that patch to
   the index of a clean checkout of the same commit, never to its
   working tree, so the `release.py` it then runs is the commit's own.
   `release.py verify-staged` recomputes every staged file from the
   commit and the tag and wants exactly that, as a plain file: the
   promoted changelog, each version line at the release's version,
   `API_VERSION` at the version the staged schema declares, the What's
   new notes renamed. `backend/openapi.json` is the one file it cannot
   recompute, since that needs the backend; it is data the image does not
   ship. A workflow, code, another version or a dependency slipped into a
   lockfile stops the release here.
   Only then does it get a token of the `digigilde-plak-release` App,
   commit as the App (`Release vX`), tag (`Plak vX`) and push the commit
   and the tag in one atomic push. If another merge reached `beta`
   meanwhile, the push is refused as a whole: no commit, no tag. The
   next push to `beta` releases what collected.

The release commit is a push to `beta` too. Its `[Unreleased]` is empty,
so it releases nothing. The tag starts `deploy.yml`, because the App,
unlike `GITHUB_TOKEN`, starts workflows with its pushes.

### Production and the GitHub Release

`deploy.yml` builds the image for the tag and rolls it out to
`productie` after four guards, each of which fails the job:

- the tag has the CalVer form,
- the tagged commit is on `beta`,
- the tag is the newest release, so an old tag pushed again cannot roll
  production back,
- the tagged commit has its section in `CHANGELOG.md`, so a tag set by
  hand on any other commit goes nowhere.

Once production runs it and the attestations are on the image, the job
`github-release` publishes the GitHub Release: the notes of
`release.py notes`, with the image and how to verify it.

## What's new notes

Some changes are worth telling a member about inside the admin, not
only in the changelog. For those, write a note in both languages:

- `frontend/src/content/releases/unreleased.nl.md`
- `frontend/src/content/releases/unreleased.en.md`

When a change needs one:

- **Added or Changed** that a member notices, in the admin, the CLI, the
  plugin or a published site: a note.
- **Fixed**: a note only when members saw the bug themselves.
- **Security, dependency or process changes** that nobody notices: never.

Not every changelog line gets a note, on purpose: the page is for
members and stays short. A note uses the words the interface uses. At a
release, notes with content are renamed to `<version>.nl.md` and
`<version>.en.md`; empty ones are deleted. A note in one language only
fails the check, for `unreleased` as for a versioned note, and so does a
pair where one is empty and the other is not. The admin page that shows
them comes in its own pull request; until the folder exists, there is
nothing to rename.

## The changelog check

`.github/workflows/changelog.yml` runs `release.py check` on every pull
request (also when its description is edited) and in the merge queue.

It fails for what is unambiguous:

- a malformed `CHANGELOG.md` or `[Unreleased]`,
- a frozen section (in the base branch and tagged) that changed,
- a What's new note in one language only.

It warns, without failing, when a shipped path changed and
`[Unreleased]` did not, unless the description has a
`No changelog entry: <reason>` line. On a pull request the warning is
one sticky comment that goes away once the entry or the reason is
there. A separate hint appears when `frontend/src/` or `cli/plak_cli/`
changed without a What's new note. In the merge queue only the failing
half runs.

`release.py check --base origin/beta` runs the same check locally.

## The Claude Code hook

`.claude/settings.json` runs `release.py hook` before every
`gh pr create` that Claude Code makes in this repository. It computes the
same warning against `origin/beta`, and when an entry is missing and
neither the command nor its `--body-file` has a `No changelog entry:`
line, it denies the call with the reason. The changelog skill in
`.claude/skills/changelog/` says how to write the entry. Anything
unexpected (no `origin/beta`, no `uv`) lets the call through: the check
in CI still runs.

## Release notes

`release.py notes --tag <tag>` prints the body of the GitHub Release: the
section for that tag as it stands at the tag, then one line for the CLI
and one for the plugin, `CLI 2026.10.1` when that release set it and
`CLI unchanged (2026.9.3)` when it did not, and the API version at that
tag (`API 1.4.0`). With `--image <name> --digest <sha256:...>` it ends
with a *Container image* section: the image, its digest and the
`gh attestation verify` commands. Anything that is not an image name
and a sha256 digest is refused rather than published.

The tag itself is annotated with `Plak v2026.10.1` as its only text
(`git tag -a v2026.10.1 -m "Plak v2026.10.1"`). The notes live in
`CHANGELOG.md` at the tagged commit (`git show v2026.10.1:CHANGELOG.md`)
and in the GitHub Release. They stay out of the tag because a tag
message cannot be edited once pushed, the changelog at that commit
already carries the same text, and git strips lines starting with `#`
(the `###` headings) from a tag message by default.

## One-time setup

The release workflow pushes the release commit and the tag with a GitHub
App token, not with `GITHUB_TOKEN`: a tag pushed with that token starts
no workflow, and the ruleset on `beta` can name an App as the one actor
allowed to push. Create it once, as an owner of the DigiGilde
organisation.

1. **Create the App** under *Settings, Developer settings, GitHub Apps,
   New GitHub App* of the organisation:
   - name `digigilde-plak-release`, homepage
     `https://github.com/DigiGilde/plak`;
   - *Webhook*: untick *Active*;
   - *Repository permissions*: *Contents: Read and write*, nothing else
     (*Metadata: Read-only* comes with it);
   - *Where can this GitHub App be installed?*: *Only on this account*.
2. **Note the Client ID** on the App's page and **generate a private
   key**; the browser downloads a `.pem` file.
3. **Install the App** on the organisation, with *Only select
   repositories* and only `plak`.
4. **Create the environment `release`**, deployable from `beta` only:

   ```bash
   gh api --method PUT repos/DigiGilde/plak/environments/release \
     -F 'deployment_branch_policy[protected_branches]=false' \
     -F 'deployment_branch_policy[custom_branch_policies]=true'
   gh api --method POST repos/DigiGilde/plak/environments/release/deployment-branch-policies \
     -f name=beta -f type=branch
   ```

5. **Put the Client ID and the key in that environment**, not in the
   repository, so only a job on `beta` that names the environment can
   read them:

   ```bash
   gh variable set RELEASE_APP_CLIENT_ID --env release --repo DigiGilde/plak --body '<client id>'
   gh secret set RELEASE_APP_PRIVATE_KEY --env release --repo DigiGilde/plak < digigilde-plak-release.private-key.pem
   ```

   Then move the `.pem` to the Trash; GitHub keeps no copy, and a new key
   can always be generated.

6. **Move the protection of `beta` into rulesets.** Classic branch
   protection has no way to let an App past its required checks; a
   ruleset does. A bypass covers a whole ruleset, so there are two:
   - `.github/rulesets/beta.json`: the pull request rule, the required
     checks and the merge queue, with the App as the only bypass;
   - `.github/rulesets/beta-history.json`: no deleting `beta` and no
     force push, without any bypass, the App included.

   The `actor_id` is `0` in the files; fill in the **App ID** from the
   App's page (not the Client ID):

   ```bash
   APP_ID='<app id>'
   jq --argjson app "$APP_ID" '.bypass_actors[0].actor_id = $app' .github/rulesets/beta.json \
     | gh api --method PUT repos/DigiGilde/plak/rulesets/24241440 --input -
   gh api --method POST repos/DigiGilde/plak/rulesets --input .github/rulesets/beta-history.json
   gh api --method DELETE repos/DigiGilde/plak/branches/beta/protection
   ```

   `24241440` is the existing *Merge queue on beta* ruleset, which
   `beta.json` replaces. Delete the classic protection only after both
   rulesets are in place.
7. **Reserve the release tags for the App**, again in two:
   `release-tags.json` lets only the App create a `v*` tag,
   `release-tags-fixed.json` lets nobody move or delete one.

   ```bash
   jq --argjson app "$APP_ID" '.bypass_actors[0].actor_id = $app' .github/rulesets/release-tags.json \
     | gh api --method POST repos/DigiGilde/plak/rulesets --input -
   gh api --method POST repos/DigiGilde/plak/rulesets --input .github/rulesets/release-tags-fixed.json
   ```

8. **Check them under *Settings, Rules, Rulesets*:** the App shows up by
   name in the bypass list of `beta` and `release tags`, and nowhere
   else.

A required check in `beta.json` that no job reports would block every
merge; `test_workflows.py` fails when one names a job that does not
exist.

A file in `.github/rulesets/` only takes effect once it is applied. After
changing one, apply it with the `gh api --method PUT` command above (with
the ruleset's id, `gh api repos/DigiGilde/plak/rulesets` lists them) before
the pull request merges. The `ci / rulesets` check
(`.github/scripts/rulesets.py`) is what catches a forgotten apply: it runs
on every pull request and in the merge queue, and daily as the workflow
*Ruleset drift*, and fails when a live ruleset differs from its file, has
no file, or a file has no live ruleset. It matches by `name`, ignores
`bypass_actors` (GitHub only shows them to those who can write the
ruleset) and the fields GitHub adds itself, and compares the required
checks as a set. Because it is itself a required check in `beta.json`, a
pull request that changes `beta.json` shows red until the file is applied.
