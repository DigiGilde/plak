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
  The tag gets a GitHub Release with that section as its notes, and
  goes to production.
- **Nothing under `[Unreleased]` means no release.** A release needs
  notes, and the entries are the notes; nothing is made up from commit
  subjects.
- **A dependency update reaches production with the next release.** If
  it should go sooner, a fix for an advisory for instance, write an
  entry for it (under `### Security` when it is one), and the push that
  merges it releases.

All of it lives in one script, `.github/scripts/release.py`, tested in
`backend/tests/test_release.py`. What is here now is the changelog, the
script, the pull request check and the Claude Code hook.

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

A release sets versions only where something changed since the previous
tag, and to the version without the `v` (`2026.10.1`):

- **The CLI**: `[project] version` in `cli/pyproject.toml` and the
  version of the editable package `plak` in `cli/uv.lock`, when anything
  under `cli/plak_cli/`, `cli/pyproject.toml` or `cli/uv.lock` changed,
  its own version line aside.
- **The plugin**: `version` in `plugin/.claude-plugin/plugin.json`, when
  anything under `plugin/` changed, `plugin/evals/` and its own version
  line aside. That manifest is the only place the plugin version lives;
  the marketplace entry carries none.
- **`publiccode.yml`**: `softwareVersion` and `releaseDate`, on every
  release.

The first release sets all of them. The CalVer versions are valid PEP 440
and sort above the `0.x` versions before them, so an installed CLI or
plugin sees them as an upgrade. A test-only change leaves a component's
version alone.

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
`CLI unchanged (2026.9.3)` when it did not.

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

The release workflow and the ruleset change that lets the App push to
`beta` follow in a later pull request.
