# Releasing

Plak releases itself, from `CHANGELOG.md`. Nobody picks a version
number and nobody tags by hand. A pull request adds a fragment file for
its change, and a release pull request turns the fragments into the
changelog.

## The model

- **A push to `beta` deploys nowhere.** It builds, scans and attests an
  image. Nothing on ZAD runs it. Only a release tag reaches `productie`.
- **A change adds a fragment.** One file per change under
  `changelog.d/`, in the pull request that makes the change. Fragments
  never conflict, and nobody edits `CHANGELOG.md` for a change.
- **A release is a pull request.** "Release vYYYY.M.D" turns every
  fragment into a section of `CHANGELOG.md`, sets the component
  versions, writes the What's new notes and deletes the fragments. It
  comes from a button or from `just release` (see Making a release).
- **Merging that pull request is the release.** On the push to `beta`,
  `release.yml` sees that the newest section has no tag yet and tags the
  commit that added it, `vYYYY.M.D[.N]`. The tag gets a GitHub Release
  with that section as its notes, and then goes to production.
- **No fragments means no release.** A release needs notes, and the
  fragments are the notes. Nothing is made up from commit subjects.
- **A dependency update reaches production with the next release.** If
  it should go sooner, a fix for an advisory for instance, give it a
  `security` fragment and prepare a release.

All of it lives in `scripts/release.py`, a thin entry over the package
`scripts/toon_release/`, tested in `scripts/tests/` with one test file
per module. `prepare-release.yml` makes the
release pull request, `release.yml` tags it once merged, and
`deploy.yml` publishes the GitHub Release for the tag and then takes it
to production.

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
30 September 2026, before releases were automatic. Tags from here on
come from a release pull request.

## When a push to beta releases

`release.py decide` answers with a route:

| Route | When |
|---|---|
| `release` | the newest section of `CHANGELOG.md` has no tag yet |
| `none` | anything else: no release |

On `release` it also names the commit to tag: the one that added that
section. It refuses, and the job turns red, when that commit is not a
clean release: a fragment left in `changelog.d/`, a tag that is not
newer than the newest one, or a component version that is not the
release's. A rerun of a release whose tag already exists answers `none`.

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
- `containers/toon/`
- `cli/plak_cli/`, `cli/pyproject.toml`, `cli/uv.lock`
- `plugin/`, except `plugin/evals/`
- `actions/publish/`

Tests never count, wherever they sit: a `tests/` or `__tests__/`
directory, a `*.test.*` or `*.spec.*` file, `test_*.py`, `conftest.py`.
Neither do docs, workflows or the dev stack.

## Writing a fragment

One file per change, in `changelog.d/`, in the same pull request:

```text
changelog.d/<slug>.<kind>.md
```

The slug is lowercase words joined by hyphens (`site-id-required`).
The kind is the heading the line lands under:

| Kind | Heading | For |
|---|---|---|
| `added` | `### Added` | something that was not there before |
| `changed` | `### Changed` | something that works differently now |
| `deprecated` | `### Deprecated` | something that will go away |
| `removed` | `### Removed` | something that went away |
| `fixed` | `### Fixed` | a bug that is gone |
| `security` | `### Security` | anything that touches security, whatever else it is |

The file holds list items exactly as they appear in `CHANGELOG.md`, one
per change, with no heading:

```markdown
- A preview stays reachable after the site is published live again.
```

Write the effect, not the implementation: what a member, a publisher or
an operator notices. "Previews stay reachable after a redeploy", not
"Move the preview lookup into the resolver". A line may wrap onto an
indented next line. Prose outside a list item, a heading or an empty
file fails the check, and so does any file in `changelog.d/` that is
not a fragment or the `README.md`.

A pull request that changes a shipped path but deserves no entry (a
refactor without effect, a lockfile refresh) says so in its description,
on a line of its own: `No changelog entry: <reason>`. Its change then
waits for the next release.

## Making a release

Two ways, same result: a pull request "Release vYYYY.M.D" against
`beta`.

- **The button.** *Actions*, *Prepare release*, *Run workflow* on
  `beta`. The workflow `prepare-release.yml` runs `release.py prepare`
  and opens the pull request as the release App.
- **From a machine.** `just release` does the same from a clean
  checkout. It needs Podman, because oasdiff runs through
  `scripts/oasdiff.sh`, and `gh`. It switches to `origin/beta`, prepares,
  commits on `release/<tag>`, pushes and opens the pull request.

`release.py prepare` refuses when `changelog.d/` has no entry fragment
(note fragments alone do not make a release), when a fragment is
malformed, when the newest section still waits for its tag, or when the
tag exists or is not newer than the newest one. It writes the new
section at the top of `CHANGELOG.md`, the component versions, the API
schema and version, the What's new notes, and removes the fragments.

Then:

- **The pull request keeps itself current.** Every push to `beta`
  refreshes the open release pull request (`prepare-release.yml` on
  `push`): it prepares again from the new `beta` and force-pushes the
  branch, with a lease on the commit it read, so a push in between
  refuses. The tag stays while `verify-staged` would still take it
  (dated today or yesterday, newer than the newest tag); a stale tag
  gets a new branch and a new pull request, and the old one is closed
  with the reason and its branch deleted (a pull request cannot change
  its head branch). Nothing happens without an open release pull
  request, when the open one already holds what `beta` would release,
  or while a just merged release still waits for its tag.
- **Edit the pull request right before merging, or edit the
  fragments.** A refresh overwrites hand edits in the pull request. To
  reword a line for good, reword its fragment on `beta`; a refresh
  carries it in. Edits in the pull request itself survive only when
  nothing lands on `beta` before the merge. The check looks at
  structure, not exact text.
- **Merge it through the queue.** The push to `beta` is the release.
  The tag lands on the last commit of the release: the commit that added
  the section plus any following commits that only touch release files
  (`CHANGELOG.md`, the version files, `backend/openapi.json`,
  `API_VERSION`, the What's new notes and the fragment removals). So
  edits made in the release pull request, also through the web UI, are
  part of the release. That tag goes to the GitHub Release and to
  production.
- **If a refresh failed, prepare again.** An API change or a new
  fragment that lands on `beta` is normally carried in by the refresh.
  When that run failed (a malformed fragment, say), the pull request is
  behind `beta`, and the failure shows in the merge queue. Close the
  pull request, delete its branch (`release/<tag>`) and prepare again,
  or fix `beta` and let the next push refresh it. The button stops with
  that advice
  when the branch already exists.

### One-time switch

Open pull requests that still write their line under `## [Unreleased]`
or edit the `unreleased.*` notes move their line into a fragment, and
their note into `changelog.d/<slug>.note.nl.md` and `.note.en.md`.
`## [Unreleased]` stays, as Keep a Changelog has it, with one line under
it that points at `changelog.d/`.

### Released sections are frozen

Once a section is tagged, its text is what the GitHub Release says. A
section is frozen when the base branch has it and its tag exists; the
check fails a pull request that edits, renames or removes one. A
correction is a new fragment. A section that only
the pull request adds is new, not edited, even for a tag that exists
already: that is how the section for the hand-made `v2026.9.30` came in.

### Links

The headings are links, through the link references at the bottom of
`CHANGELOG.md`: each version to the compare view with the version before
it, the first version to its tag. A release writes them anew and nobody
edits them by hand. The check fails when they do not match the
headings, and `release.py links` writes them.

## Component versions

A release writes the version without the `v` (`2026.10.1`). Nobody
edits these lines by hand. The image and `publiccode.yml` get it on every
release; the CLI and the plugin only when they changed since the
previous tag:

- **The image**: `[project] version` in `backend/pyproject.toml` and the
  version of the editable package `toon-api` in `backend/uv.lock`;
  `version` in `frontend/package.json` and both places it appears in
  `frontend/package-lock.json`. Every tag builds a new image, so these
  follow every release. What the footer shows still comes from the tag
  itself, through `TOON_VERSION` at build time.
- **The CLI**: `[project] version` in `cli/pyproject.toml` and the
  version of the editable package `plak` in `cli/uv.lock`, when anything
  under `cli/plak_cli/`, `cli/pyproject.toml` or `cli/uv.lock` changed,
  its own version line aside.
- **The plugin**: `version` in `plugin/.claude-plugin/plugin.json`, when
  anything under `plugin/` changed, `plugin/evals/` and its own version
  line aside. That manifest is the only place the plugin version lives;
  the marketplace entry carries none. The release pull request writes
  it, and a feature pull request leaves it alone.
- **`publiccode.yml`**: `softwareVersion` and `releaseDate`, on every
  release.
- **The API**: `backend/openapi.json` and `API_VERSION` in
  `backend/src/toon/main.py`, on every release; see below.

The first release sets all of them. The CalVer versions are valid PEP 440
and sort above the `0.x` versions before them, so an installed CLI or
plugin sees them as an upgrade. A test-only change leaves a component's
version alone.

## The API contract

The API has a SemVer version of its own, in the `API-Version` header on
every response and in `info.version` of its schema. The CalVer of a
release says when; the API version says whether a client still fits.
The CLI reads only its major (`docs/publishing.md`).

- **`backend/openapi.json` is the contract a release shipped.** Only a
  release writes it, in the release pull request, so the file at a tag
  is what clients of that release see. A feature pull request leaves
  it alone, so there is nothing to keep up to date by hand.
- **The major moves by hand, together with the path.** A breaking change
  means `/-/api/v2` and `API_VERSION = "2.0.0"` in
  `backend/src/toon/main.py`, in the same pull request.
  `test_openapi_file.py` holds the two to each other.
- **Minor and patch are set by the release.** It prints the schema with
  `python -m toon.api.openapi_file` and compares it, with
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

`prepare-release.yml` and `just release` pass the schema and oasdiff
to `release.py prepare --spec <file> --oasdiff <binary>`.

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

On a release pull request, and on the release commit in the merge
queue, it also wants `backend/openapi.json` and `API_VERSION` to be what
`prepare` would write now. When an API change merged after the release
was prepared, the check fails and the release is prepared again. The
rule applies to the release itself, not to an API pull request queued
behind it.

oasdiff comes from the image in `.github/oasdiff/Containerfile`, pinned
by tag and digest, so Dependabot bumps it with the other base images. The
workflow copies the static binary out of it and never runs the image. It
runs with external references off.

## The release workflows

`.github/workflows/prepare-release.yml` makes the pull request and keeps
it current. It runs on `workflow_dispatch` (the button) and on every
push to `beta`, and only for `beta`.

1. **`prepare`** first asks `release.py pending` whether a merged
   release still waits for its tag, and on a push looks for the open
   pull request from a `release/v*` branch of this repository (with the
   read-only workflow token). The button always opens; a push refreshes
   only an open pull request, and does nothing otherwise. It then prints
   the API schema, gets oasdiff and runs `release.py prepare`, for a
   refresh under the tag `release.py refresh-tag` picks (the pull
   request's own while it holds, else the next). A refresh whose tree
   equals the pull request's head stops here. Otherwise it hands the
   staged change on as a patch. This job runs `uv sync` and the
   backend, and it never sees the environment, the App's key or a token.
2. **`open`**, in the environment `release`, applies that patch to the
   index of a clean checkout of the same commit, never to its working
   tree, so the `release.py` it then runs is the commit's own.
   `release.py verify-staged` recomputes every staged file from the
   commit and the tag and wants exactly that, as a plain file, with
   every file a release always writes present. The tag must be dated
   today or yesterday and newer than the newest tag. It checks the
   assembled changelog, each version line at the release's version,
   `API_VERSION` at the version the staged schema declares, the What's
   new notes from the note fragments and the fragments removed.
   `backend/openapi.json` is the one file it cannot recompute, since
   that needs the backend. A workflow, code, another version or a
   dependency slipped into a lockfile stops the release here. Only then
   does it get a token of the `digigilde-toon-release` App and commit as
   the App (`Release vX`). It takes nothing but the tag and "changed"
   from `prepare`: which pull request a refresh touches it looks up
   itself (`release.py release-pr` on `gh pr list --json`, with the
   read-only workflow token, before the App token exists), so a
   dependency that ran in `prepare` cannot point the App at another pull
   request. Opening (the button): it pushes `release/vX` and opens the
   pull request with that token; an existing branch of that name fails
   the job. A push never opens one: without an open release pull request
   it fails. Refreshing under the same tag: it force-pushes the branch
   with `--force-with-lease` on the commit it found and comments on the
   pull request. Refreshing under a new tag: it pushes the new branch,
   opens its pull request and closes the one it found with a comment,
   deleting that branch.

`.github/workflows/release.yml` runs on every push to `beta`, one run at
a time and in push order.

1. **`decide`** runs `release.py decide`, without any token. On `none`
   that is the end of it.
2. **`tag`**, in the environment `release`, gets a token of the App with
   Contents: Read and write, tags the commit `decide` named (`Plak vX`)
   and pushes the tag, and only the tag. No job pushes to `beta`.

The pull request is checked by `changelog.yml` and `api.yml`, as any
other, and again in the merge queue. The tag starts `deploy.yml`,
because the App, unlike `GITHUB_TOKEN`, starts workflows with its
pushes.

### Production and the GitHub Release

`deploy.yml` tests, builds, scans and attests the image for the tag.
Then the job `github-release` publishes the GitHub Release, after four
guards, each of which fails the job:

- the tag has the CalVer form,
- the tagged commit is on `beta`,
- the tag is the newest release, so an old tag pushed again cannot roll
  production back,
- the tagged commit has its section in `CHANGELOG.md`, so a tag set by
  hand on any other commit goes nowhere.

The release holds the notes of `release.py notes`, with the image and
how to verify it. Only then does `production` roll the image out to
`productie`: what runs there has been released. It runs the same four
guards again, right before the rollout and within its one-at-a-time
queue, since a newer tag may have arrived while it waited. A rerun finds
the release already there and goes on to production.

## What's new notes

Some changes are worth telling a member about inside the admin, not
only in the changelog. For those, add a note fragment in both languages,
in the same pull request:

- `changelog.d/<slug>.note.nl.md` (Dutch)
- `changelog.d/<slug>.note.en.md` (English)

A note is Markdown, usually a `## ` heading and a short paragraph. When
a change needs one:

- **Added or Changed** that a member notices, in the admin, the CLI, the
  plugin or a published site: a note.
- **Fixed**: a note only when members saw the bug themselves.
- **Security, dependency or process changes** that nobody notices: never.

Not every fragment gets a note, on purpose: the page is for members and
stays short. A note uses the words the interface uses. A note in one
language only fails the check, and so does an empty one. At a release,
`prepare` sorts the note fragments by file name, joins them with a
blank line and writes `frontend/src/content/releases/<version>.nl.md`
and `<version>.en.md`. Without note fragments there are no files, and a
release refuses to overwrite an existing one. Nothing else in that
folder may be named differently from `<version>.<nl|en>.md`.

## The changelog check

`.github/workflows/changelog.yml` runs `release.py check` on every pull
request (also when its description is edited) and in the merge queue.

It fails for what is unambiguous:

- a malformed `CHANGELOG.md`: `## [Unreleased]` missing, not the first
  section, or holding anything but its one pointer line (an entry under
  it belongs in a fragment),
- a malformed fragment, or a file in `changelog.d/` that is none.
  Fragments are read from the commit, not the working tree, and a
  fragment must be a plain file (no symlink),
- a frozen section (in the base branch and tagged) that changed,
- a What's new note in one language only or a misnamed note file,
- on a pull request that adds a section (a release): more than one new
  section, one that is not on top, a fragment left behind, or a
  component version that is not the release's.

It warns, without failing, when a shipped path changed and no entry
fragment was added, unless the description has a
`No changelog entry: <reason>` line. A release pull request never
warns. On a pull request the warning is one sticky comment that goes
away once the fragment or the reason is there. A separate hint appears
when `frontend/src/` or `cli/plak_cli/` changed without a note
fragment. In the merge queue only the failing half runs.

`release.py check --base origin/beta` runs the same check locally.

## The Claude Code hook

`.claude/settings.json` runs `release.py hook` before every
`gh pr create` that Claude Code makes in this repository. It computes the
same warning against `origin/beta`, and when no entry fragment was added
and neither the command nor its `--body-file` has a
`No changelog entry:` line, it denies the call with the reason, which
names `changelog.d/<slug>.<kind>.md`. The changelog skill in
`.claude/skills/changelog/` says how to write the fragment. A release
pull request goes through. Anything unexpected (no `origin/beta`, no
`uv`) lets the call through: the check
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

The release workflows open the release pull request and push the tag
with a GitHub App token, not with `GITHUB_TOKEN`. A tag pushed with that
token starts no workflow, a pull request opened with it starts no
checks, and the ruleset on `v*` tags can name an App as the one actor
allowed to create them. Create it once, as an owner of the DigiGilde
organisation.

1. **Create the App** under *Settings, Developer settings, GitHub Apps,
   New GitHub App* of the organisation:
   - name `digigilde-toon-release`, homepage
     `https://github.com/DigiGilde/plak`;
   - *Webhook*: untick *Active*;
   - *Repository permissions*: *Contents: Read and write* and
     *Pull requests: Read and write*, nothing else (*Metadata:
     Read-only* comes with it);
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
   gh secret set RELEASE_APP_PRIVATE_KEY --env release --repo DigiGilde/plak < digigilde-toon-release.private-key.pem
   ```

   Then move the `.pem` to the Trash; GitHub keeps no copy, and a new key
   can always be generated.

6. **Move the protection of `beta` into rulesets.** Classic branch
   protection is replaced by two rulesets, neither with a bypass,
   because the App never pushes to `beta`:
   - `.github/rulesets/beta.json`: the pull request rule, the required
     checks and the merge queue;
   - `.github/rulesets/beta-history.json`: no deleting `beta` and no
     force push.

   ```bash
   gh api --method PUT repos/DigiGilde/plak/rulesets/24241440 --input .github/rulesets/beta.json
   gh api --method POST repos/DigiGilde/plak/rulesets --input .github/rulesets/beta-history.json
   gh api --method DELETE repos/DigiGilde/plak/branches/beta/protection
   ```

   `24241440` is the existing *Merge queue on beta* ruleset, which
   `beta.json` replaces. Delete the classic protection only after both
   rulesets are in place.
7. **Reserve the release tags for the App**, in two rulesets:
   `release-tags.json` lets only the App create a `v*` tag,
   `release-tags-fixed.json` lets nobody move or delete one.

   The `actor_id` is `0` in `release-tags.json`. Fill in the **App ID**
   from the App's page (not the Client ID):

   ```bash
   APP_ID='<app id>'
   jq --argjson app "$APP_ID" '.bypass_actors[0].actor_id = $app' .github/rulesets/release-tags.json \
     | gh api --method POST repos/DigiGilde/plak/rulesets --input -
   gh api --method POST repos/DigiGilde/plak/rulesets --input .github/rulesets/release-tags-fixed.json
   ```

8. **Check them under *Settings, Rules, Rulesets*:** the App shows up by
   name in the bypass list of `release tags`, and nowhere else. `beta`
   has no bypass list.

A required check in `beta.json` that no job reports would block every
merge; `test_workflows.py` fails when one names a job that does not
exist.

A file in `.github/rulesets/` only takes effect once it is applied. After
changing one, apply it with the `gh api --method PUT` command above (with
the ruleset's id, `gh api repos/DigiGilde/plak/rulesets` lists them) before
the pull request merges. The `rulesets` check
(`scripts/rulesets.py`) is what catches a forgotten apply: the
workflow *Rulesets* (`rulesets.yml`) runs it on every pull request, in the
merge queue and daily, and it fails when a live ruleset differs from its
file, has no file, or a file has no live ruleset. It matches by `name`, ignores
`bypass_actors` (GitHub only shows them to those who can write the
ruleset) and the fields GitHub adds itself, and compares the required
checks as a set. Because it is itself a required check in `beta.json`, a
pull request that changes `beta.json` shows red until the file is applied.
