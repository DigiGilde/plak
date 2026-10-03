# Publishing with Plak

This document describes the contract you use to publish a static site on
Plak: the base path contract for your build, publishing from CI with an
OIDC ID token, publishing from your own machine with `plak login`, the use
of the `publish` action, the curl fallback, and the difference
between a live version, a preview and a `_version` view.

The CLI and the action live in this repository: `cli/` is the CLI (a uv
project of its own, installing the `plak` command),
`actions/publish/action.yml` the composite
action, and `plugin/skills/plak-publish/` the Claude skill that
drives the CLI, shipped as the `plak` plugin (see `docs/skill.md`).
Until 2026-09 they sat in a separate `plak-actions` repository; a
`uses:` line that still names it points at code that no longer moves.

## 1. Concepts: live, preview, `_version`

- **Live**: the current published version of a site, reachable at
  `/{group}/{site}/...`. A `git push` to the main branch usually
  triggers a live deploy.
- **Preview**: a named temporary variant per ref (for example
  `pr-42`), reachable at `/{group}/{site}/_preview/{ref}/...`. Every
  new deploy for the same ref replaces the previous preview version
  (upsert); the previous files are removed. Previews expire 30 days
  after the last deploy by default and are purged automatically then;
  you can also tear a preview down explicitly as soon as the
  corresponding PR closes.
- **`_version`**: viewing an earlier live version without making it
  live, at `/{group}/{site}/_version/{version-id}/...`. This is an
  inspection feature for active group members (preparing a rollback), not
  a public publishing channel.

## 2. Base path contract

Plak does not serve your site from the root path of the host, but from
a path that contains the group, the site and (for previews) the ref:

- Live: `/{group}/{site}/`
- Preview: `/{group}/{site}/_preview/{ref}/`

So your build has to know under which path it will run, so that
generated `<link>`, `<script>` and asset URLs are correct. Most build
tools support this through a "base" setting; the path differs per
environment (main deploy versus PR preview), so set it through an
environment variable that your CI step fills in.

The `plak` CLI and the `publish` action (see §3) only upload;
they do not compute or set `PLAK_BASE_PATH`. Determining that path
and setting it as an environment variable is up to your own **build**
step, before you call the CLI or the action. See the workflow example in
§3 for a "Determine base path" step that does this.

### Astro example

`astro.config.mjs`:

```js
import { defineConfig } from "astro/config";

export default defineConfig({
  base: process.env.PLAK_BASE_PATH ?? "/",
  trailingSlash: "always",
});
```

For hand-written links (not going through Astro's built-in asset
resolution, for example in a `Layout.astro` or `404.astro`) use
`import.meta.env.BASE_URL`, never a hardcoded absolute path:

```astro
---
// Layout.astro
const basis = import.meta.env.BASE_URL;
---
<nav>
  <a href={`${basis}over/`}>Over ons</a>
  <a href={`${basis}contact/`}>Contact</a>
</nav>
```

```astro
---
// 404.astro
const basis = import.meta.env.BASE_URL;
---
<p>Pagina niet gevonden. <a href={basis}>Terug naar de homepage</a>.</p>
```

### Vite example

`vite.config.ts`:

```ts
import { defineConfig } from "vite";

export default defineConfig({
  base: process.env.PLAK_BASE_PATH ?? "/",
});
```

With this, Vite automatically rewrites the asset URLs in the built
`index.html`; for hand-written internal links in your source code use
`import.meta.env.BASE_URL` the same way as in the Astro example.

### Shielding: what a build tool has to know

Every site is shielded from the other sites by default: the switch
"Afschermen van andere sites" (shield from other sites) on the tab
"Toegang" (access) of the site is on unless its admin turns it off. The
page then runs in a CSP sandbox with an opaque origin, `null` (the why is
in `docs/security.md`, "Shielding from other sites"). For a site built with
Astro, Vite, Vue, React or Svelte that has consequences you need to know
before you publish, because the page comes online without an error and
only the browser console tells you what is missing.

Under the shielding the browser fetches some files in CORS mode, and from
an opaque origin those requests reach Plak as `Origin: null` without the
visitor's cookie. Plak sends no CORS headers, so the browser refuses them:

| What | Under the shielding |
| --- | --- |
| Stylesheets, images, video, `<script src>` without `type="module"`, inline scripts | Load |
| A module script from your own site: `<script type="module" src>`, `<link rel="modulepreload">`, a dynamic `import()` | **Refused** |
| A web font from your own site (`@font-face` with a `url()` into your dist, including fonts bundled from `@fontsource`) | **Refused** |
| `fetch` or XHR to a file of your own site (JSON data, a search index such as Pagefind's) | **Refused** |
| `localStorage`, `sessionStorage`, `document.cookie` | Throw a `SecurityError` |
| Scripts and fonts from cdnjs, jsDelivr, unpkg and Google Fonts (with "Externe bronnen toestaan" on) | Load: those hosts send `Access-Control-Allow-Origin: *` |

In the console a refused module script looks like this:

```text
Access to script at 'https://plak.example.nl/team-aurora/docs/_astro/Menu.astro_astro_type_script_index_0_lang.D4k2x9Qa.js'
from origin 'null' has been blocked by CORS policy: No 'Access-Control-Allow-Origin'
header is present on the requested resource.
```

`from origin 'null'` is the sign: that is the shielding, not a CORS setting
you can fix in your build.

**Astro.** A `<script>` in a component is processed by default: Astro
bundles it and emits it as `type="module"`. A small one it inlines into
the HTML, and that runs; a larger one lands in `_astro/*.js`, and that is
refused. So on one page one script can work and the next not. Islands
(`client:load`, `client:visible` and the like) always load their component
as a module and stay dead. The HTML and styling are complete, so the page
looks right; menus, toggles and islands do nothing.

**Vite, and so Vue, React and Svelte.** A Vite build loads its whole
application through `<script type="module" crossorigin src="…/assets/index-….js">`.
Under the shielding that one file is refused, and a single-page app shows
a blank page. A Vue app that keeps state in `localStorage` (a theme choice,
a persisted Pinia store) stops at the first read even when it loads.

What you can do:

- **Keep the shielding and stay within it.** For a few small scripts in
  Astro, `<script is:inline>` leaves the script as written: a classic inline
  script that runs, but without TypeScript, imports or deduplication. Load
  a font from Google Fonts or a library from one of the CDNs above instead
  of from your dist. This does not stretch to a Vite application.
- **Turn the shielding off** for the site on the tab "Toegang". Module
  scripts, own fonts, `fetch` and storage then work. The price: your site
  shares its origin again with every other site whose shielding is off,
  and the code on your pages can reach what the visitor may see there. Do
  that only for a site whose content you trust. The CLI does not offer this
  switch; it is a decision for the site's admin in the admin environment.

## 3. Publishing from CI with an OIDC ID token

CI publishes without a secret ("trusted publishing"): a GitHub or
Forgejo workflow proves who it is with an OIDC ID token that the platform
itself issues (`ACTIONS_ID_TOKEN_REQUEST_URL` on GitHub,
`enable-openid-connect` on Forgejo), and Plak checks that token against the
repository linked to the site. So there is no secret to create, store or
rotate.

### Linking a repository

First link the repository to the site, from the terminal or in the
admin. From the terminal, with a CLI token from `plak login`:

```bash
plak site link team-aurora/docs minbzk/website --live-branch main
```

Name the repository as `owner/repo` (GitHub) or by its URL (GitHub or
Forgejo). Left out, the CLI takes the remote `origin` of the checkout you
run it in, which is only right in the right checkout: a fork or another
project links that repository instead. Without `--live-branch` every
branch may publish live. For a GitHub repository the CLI asks your own `gh` login for the
ids and sends them along, so a private repository links without further
ado; `--no-gh` leaves the lookup to Plak, and `--repository-id` with
`--owner-id` gives the ids by hand. Exit codes as for every command: `0`
linked, `1` refused or unreachable, `2` wrong usage.

In the admin: **site detail, tab
Deploy** ("Publiceren vanuit GitHub of Forgejo"), button "Repository
koppelen". Fill in the provider (GitHub or Forgejo, with for Forgejo the host
from `PLAK_CI_FORGEJO_HOSTS`), `eigenaar/repo` (owner/repo) and optionally
a live branch. Plak looks the repository up at the provider and stores its
numeric ids;
those stay the same across a rename, and the name follows it: the next
deploy stores the name its CI token gives, with a `site_repository_rename`
row in the audit log. A transfer to another owner changes the owner id, so
the site has to be linked again. Plak looks it up without credentials,
so it does not find a private repository; the form then asks for the two ids
("Repository-id", "Eigenaar-id"). Get them with
`gh api repos/<owner>/<repo> --jq '.id, .owner.id'` (on Forgejo:
`/api/v1/repos/<owner>/<repo>`, fields `id` and `owner.id`), or send them as
`repositoryId` and `ownerId` with the `PUT`. When the lookup fails, Plak
stores the entered ids as they are; when it does find the repository, they
must match. A wrong id links nothing else, it only refuses every deploy.
Entered ids that the provider could not confirm show as "Nog niet bevestigd"
(not confirmed yet) on the Deploy tab, until the first deploy whose token
carries both ids (or, on Forgejo 15, passes Plak's API recheck) confirms them
and corrects the name. A
private repository on a Forgejo whose tokens carry no ids (Forgejo 15) still
cannot publish: Plak confirms such a token against the Forgejo API, also
without credentials. Publishing live is allowed
only from the events `push`, `workflow_dispatch` and `schedule`; a token
without an `event_name` or with another event (`pull_request`,
`pull_request_target`, `issue_comment`, `workflow_run`, ...) never goes live,
not even without a live branch. Without a live branch any branch may publish
live; with a live branch only that one. Previews (and purging them) are
always allowed, from any event and from any branch. A site role of `admin`
can link (in the admin or with the CLI) and unlink (in the admin only),
`editor` can view the link (to set up the workflow).

### GitHub Actions

```yaml
# .github/workflows/publish.yml
name: Publish

on:
  push:
    branches: [main]
  pull_request:
    types: [opened, synchronize, reopened, closed]

permissions:
  contents: read
  id-token: write

jobs:
  publish:
    if: github.event.action != 'closed'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<commit-sha>

      - name: Determine base path
        run: |
          if [ "${{ github.event_name }}" = "pull_request" ]; then
            echo "PLAK_BASE_PATH=/team-aurora/website/_preview/pr-${{ github.event.pull_request.number }}/" >> "$GITHUB_ENV"
          else
            echo "PLAK_BASE_PATH=/team-aurora/website/" >> "$GITHUB_ENV"
          fi

      - name: Build site
        run: |
          npm ci
          npm run build

      - name: Publish live
        if: github.event_name == 'push'
        uses: DigiGilde/plak/actions/publish@<commit-sha>
        with:
          host: https://beheer.plak.example.org
          site: team-aurora/website
          dist-path: ./dist

      - name: Publish preview
        if: github.event_name == 'pull_request'
        uses: DigiGilde/plak/actions/publish@<commit-sha>
        with:
          host: https://beheer.plak.example.org
          site: team-aurora/website
          dist-path: ./dist
          preview-ref: pr-${{ github.event.pull_request.number }}

  teardown:
    if: github.event_name == 'pull_request' && github.event.action == 'closed'
    runs-on: ubuntu-latest
    steps:
      - name: Remove preview
        uses: DigiGilde/plak/actions/publish@<commit-sha>
        with:
          host: https://beheer.plak.example.org
          site: team-aurora/website
          preview-ref: pr-${{ github.event.pull_request.number }}
          teardown: "true"
```

The step "Determine base path" sets `PLAK_BASE_PATH` before the build, so
that the build configuration (see the Astro and Vite examples above) can
read it. This is the only place where that path is determined: neither the
CLI nor the action does this for you.

`permissions: id-token: write` is mandatory: without that flag the
workflow gets no ID token and GitHub silently refuses
`ACTIONS_ID_TOKEN_REQUEST_URL`. A pull request from a fork gets no ID token
on GitHub by default; do not use `pull_request_target` there to work around
that, because then the workflow runs with the rights of the target
repository on code from the fork.

A pull request opened by a bot (dependabot, renovate, pre-commit-ci,
github-actions, or any author of type `Bot`) needs no preview, and a
Dependabot one runs like a fork, without an ID token. The action leaves
those alone, teardown included, and sets its output `skipped` to `true`;
`skip-bot-prs: "false"` turns that off. The check is the one
`RijksICTGilde/zad-actions` does, so a workflow that uses both skips the
same pull requests.

The input `host` is the admin origin of the instance (`PLAK_BASE_URL`, e.g.
`https://beheer.plak.example.org`), without an `/admin` suffix, and at the
same time the expected `aud` claim of the ID token: the action sets that
audience itself. The deploy API exists only on that host; the content host
(`https://plak.example.org`) answers `/admin/...` with a neutral 404. Leave
`host` out to publish to the DigiGilde instance,
`https://beheer.plak.rijks.app`, or to whatever `PLAK_HOST` in the job's
environment names.

The action's output `url` is where the deploy can be seen, for a later step
of your own. The log of the publish step ends with
`Published: <url> (version <id>)`, and where the runner offers a step
summary (`GITHUB_STEP_SUMMARY`; on Forgejo only when both runner and server
support it) the action writes the same there, or the removal on teardown.

#### The preview in the pull request

Two optional inputs report a deploy back to GitHub (not to Forgejo, where
the action refuses them before it publishes):

- `environment: preview` records the deploy as a deployment in the GitHub
  environment `preview`, with its URL, so the pull request shows a "View
  deployment" button. Each pull request keeps its own deployment; teardown
  marks it inactive. Needs `deployments: write`.
- `comment-on-pr: "true"` puts the link in a comment on the pull request, updated
  on every push and marked removed on teardown. The comment says who can see
  the deploy and whether opening it needs a sign-in. Needs
  `pull-requests: write`.

```yaml
permissions:
  contents: read
  id-token: write
  deployments: write
  pull-requests: write

# ...
      - name: Publish preview
        if: github.event_name == 'pull_request'
        uses: DigiGilde/plak/actions/publish@<commit-sha>
        with:
          site: team-aurora/website
          dist-path: ./dist
          preview-ref: pr-${{ github.event.pull_request.number }}
          environment: preview
          comment-on-pr: "true"
```

Give the teardown step the same `environment` and `comment-on-pr`. Both use the
workflow's own `GITHUB_TOKEN`; pass another one through `github-token` if
needed. A pull request from a fork gets a read-only token, so there these
two cannot write, just as the deploy itself gets no ID token.

### Forgejo Actions

```yaml
# .forgejo/workflows/publish.yml
name: Publish

on:
  push:
    branches: [main]
  pull_request:
    types: [opened, synchronize, reopened, closed]

permissions:
  contents: read
  id-token: write

jobs:
  publish:
    if: github.event.action != 'closed'
    runs-on: docker # the runner label of this Forgejo instance
    enable-openid-connect: true
    steps:
      - uses: actions/checkout@<commit-sha>

      - name: Determine base path
        run: |
          if [ "${{ github.event_name }}" = "pull_request" ]; then
            echo "PLAK_BASE_PATH=/team-aurora/website/_preview/pr-${{ github.event.pull_request.number }}/" >> "$GITHUB_ENV"
          else
            echo "PLAK_BASE_PATH=/team-aurora/website/" >> "$GITHUB_ENV"
          fi

      - name: Build site
        run: |
          npm ci
          npm run build

      - name: Publish live
        if: github.event_name == 'push'
        uses: https://github.com/DigiGilde/plak/actions/publish@<commit-sha>
        with:
          host: https://beheer.plak.example.org
          site: team-aurora/website
          dist-path: ./dist

      - name: Publish preview
        if: github.event_name == 'pull_request'
        uses: https://github.com/DigiGilde/plak/actions/publish@<commit-sha>
        with:
          host: https://beheer.plak.example.org
          site: team-aurora/website
          dist-path: ./dist
          preview-ref: pr-${{ github.event.pull_request.number }}

  teardown:
    if: github.event_name == 'pull_request' && github.event.action == 'closed'
    runs-on: docker # the runner label of this Forgejo instance
    enable-openid-connect: true
    steps:
      - name: Remove preview
        uses: https://github.com/DigiGilde/plak/actions/publish@<commit-sha>
        with:
          host: https://beheer.plak.example.org
          site: team-aurora/website
          preview-ref: pr-${{ github.event.pull_request.number }}
          teardown: "true"
```

`enable-openid-connect: true` on the job is the Forgejo equivalent of
`permissions: id-token: write`. Always pin the action to a commit SHA, not
to a tag or branch (supply chain requirement, see `docs/security.md`); on
Forgejo Actions you use the full URL,
`https://github.com/DigiGilde/plak/actions/publish@<commit-sha>`, because
Forgejo resolves a short `uses:` against its own `DEFAULT_ACTIONS_URL`, which
is `https://data.forgejo.org`, not GitHub.

#### What is proven, and what is not

The GitHub form is documented and supported. GitHub's workflow syntax
documents an action in a subdirectory of a repository as
`{owner}/{repo}/{path}@{ref}`, so `DigiGilde/plak/actions/publish@<commit-sha>`
is the ordinary spelling and needs nothing special.

The Forgejo form is **not proven yet**. Forgejo's Actions documentation shows
a `uses:` with a full URL, and it shows a path inside a repository for
*reusable workflows*
(`some-org/some-repo/.forgejo/workflows/reusable.yml@main`), but it does not
document a subdirectory path for an *action*. Whether
`https://github.com/DigiGilde/plak/actions/publish@<commit-sha>` resolves is
therefore a question for a real Forgejo runner, not for this document.

One smoke test settles it. On a Forgejo instance with a runner and OIDC
enabled, in a repository linked to a site, add this workflow and start it by
hand:

```yaml
# .forgejo/workflows/plak-smoke-test.yml
name: Plak smoke test

on: [workflow_dispatch]

jobs:
  smoke-test:
    runs-on: docker # the runner label of this Forgejo instance
    enable-openid-connect: true
    steps:
      - name: Remove a preview that does not exist
        uses: https://github.com/DigiGilde/plak/actions/publish@<commit-sha>
        with:
          host: https://beheer.plak.example.org
          site: team-aurora/website
          preview-ref: smoke-test
          teardown: "true"
```

Teardown of an unknown ref is a `204` (§4), so this touches nothing. A green
run proves the whole chain: Forgejo resolved the action from a subdirectory,
the composite steps ran, and the ID token was accepted. A run that fails while
fetching the action, before any step output appears, is the answer that the
subdirectory path does not work; a run that fails inside a step is a different
problem (OIDC, linking) and says nothing about the path.

If it does not work, the fallback is to publish the action as its own small
repository again, with `action.yml` in its root and a copy of `cli/`, generated from
this repository so the two cannot drift, and to point Forgejo users at
`https://github.com/DigiGilde/plak-actions@<commit-sha>`. The GitHub form in this
document stays as it is either way.

Some Forgejo instances (for example code.overheid.nl, version 15) send
no `repository_id` claim in the ID token; Plak then matches on
`eigenaar/repo` and checks again on every deploy through the Forgejo API that
that name still belongs to the linked ids (see `docs/security.md`).

### Maintaining the action

`actions/publish/action.yml` installs uv with `astral-sh/setup-uv`, on the
same commit SHA as `.github/workflows/ci.yml` uses, so the repository has one
uv installer to bump instead of two. It replaces a hand-rolled step that
fetched `https://astral.sh/uv/<version>/install.sh` on every run and checked a
pinned `UV_INSTALLER_SHA256` against it; the action resolves the version,
verifies the download itself and reuses the runner's tool cache. Bump the SHA
here and in `ci.yml` together; dependabot proposes both (`github-actions`,
directories `/` and `/actions/publish`).

Caching stays on `enable-cache: 'auto'`, the action's default: the uv cache is
uploaded on GitHub-hosted runners and left alone on self-hosted ones, where uv
keeps its cache on the runner's own filesystem and an Actions cache service is
often not there at all.

This is a nested `uses:` inside a composite action, so the commit SHA is the
pin that matters (`docs/security.md`); `cli/tests/test_cli.py` fails on a
`uses:` in this file that is not a 40-character SHA.

**On Forgejo** the runner has to be able to fetch `astral-sh/setup-uv` from
GitHub. Forgejo resolves a short `uses:` against its own
`DEFAULT_ACTIONS_URL` (`https://data.forgejo.org`), and a nested `uses:`
cannot be written as a full URL without breaking the action on GitHub, so the
instance has to point `DEFAULT_ACTIONS_URL` at GitHub or mirror
`astral-sh/setup-uv` under that name. A runner image that already carries
`uv` on `PATH` does not help: setup-uv looks in the Actions tool cache, not on
`PATH`, and downloads uv from github.com when it does not find it there.
Where that reachability is missing, leave the action alone on Forgejo and call
the CLI straight from the workflow (§6): that needs nothing but `uv` on the
runner.

### Outside the action: the curl fallback or the CLI with a CLI token

The action is a thin layer around the HTTP contract in §4. Outside CI, or
without the action, you publish with a CLI token from `plak login` (§6)
instead of an ID token: for the deploy API those two kinds of token are
interchangeable, `Authorization: Bearer <token>`.

If you publish an archive instead of a dist directory, and the site sits
inside it in a subdirectory next to other files, you point at that directory
with `--base-path dist` (action input `base-path`). Without that flag Plak
refuses the deploy and the CLI prints the `index.html` paths it found, with
the flag that resolves it; see §4 under "Payload".

**Do not capture the CLI's stdout in CI.** In CI the CLI writes an
`::add-mask::` workflow command there, which is how the runner learns to hide
the ID token in the rest of the log. A runner only reads that command from
its own stdout, so `token=$(plak publish ...)`, `| tee` or a wrapper script
takes the line away: the token is then not masked, and it sits in whatever
captured it. The CLI skips the line when stdout is a plain file (`> log.txt`),
because the runner would not have read it there anyway, but a pipe is
indistinguishable from the runner's own. Read the version id with
`--output-file` instead, which is what the action does.

## 4. Curl fallback (bare HTTP contract)

The action and the CLI are a convenience, not a requirement. The underlying
HTTP contract is and remains directly usable, and is at the same time the
interface description for your own tooling.

Addressing is on slugs (group, site, ref), never on UUIDs. The
endpoints live on the admin origin (`PLAK_BASE_URL`); on the content host
`/admin` does not exist. `${PLAK_TOKEN}` below is one of two things: a
CI ID token (§3), or a CLI token from `plak login` (§6). Outside these two
endpoints and the CLI session endpoints, Plak accepts a Bearer token
nowhere.

A GitHub workflow that requests the ID token itself, outside the action:

```bash
id_token=$(curl -sS -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
    "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=https://beheer.plak.example.org" \
    | jq -r .value)

curl -sS -X POST \
    -H "Authorization: Bearer ${id_token}" \
    -F "file=@dist.zip" \
    "https://beheer.plak.example.org/-/api/v1/sites/${GROUP}/${SITE}/deploys"
```

That does require `permissions: id-token: write` on the job (§3); without
those rights `ACTIONS_ID_TOKEN_REQUEST_URL` and
`ACTIONS_ID_TOKEN_REQUEST_TOKEN` do not exist in the step's environment.

### Live deploy

```bash
curl -sS -X POST \
    -H "Authorization: Bearer ${PLAK_TOKEN}" \
    -F "file=@dist.zip" \
    "https://beheer.plak.example.org/-/api/v1/sites/${GROUP}/${SITE}/deploys"
```

Success: `201` with JSON body
`{"versionId": "<uuid>", "url": "<url>", "access": {"base": "<base>", "keys": <bool>, "invitees": <bool>}}`;
`url` is where the deploy can be seen, the live site here and
`.../_preview/<ref>/` for a preview. `access` is who may see it: the
preview's own access when it has one, else the site's.

### Preview deploy

Add the form field `preview` with the ref:

```bash
curl -sS -X POST \
    -H "Authorization: Bearer ${PLAK_TOKEN}" \
    -F "file=@dist.zip" \
    -F "preview=pr-42" \
    "https://beheer.plak.example.org/-/api/v1/sites/${GROUP}/${SITE}/deploys"
```

A second deploy for the same ref replaces the previous preview version
(upsert); this is idempotent per ref, not per call.

### Purging a preview (teardown)

```bash
curl -sS -X DELETE \
    -H "Authorization: Bearer ${PLAK_TOKEN}" \
    "https://beheer.plak.example.org/-/api/v1/sites/${GROUP}/${SITE}/previews/pr-42"
```

Success: `204`, also if the preview no longer exists (idempotent).

### Payload

One multipart file field `file`. Plak accepts two forms:

- **a single `.html` file**: that becomes the `index.html` of your site,
  whatever the file itself is called;
- **an archive**: `.zip`, `.tar.gz` or `.tgz`, with your built site inside.

The root of the archive is the root of your site: the `index.html` that
sits in the root of the archive ends up at `/{group}/{site}/`. That
`index.html` is mandatory, and the name is case sensitive: `Index.html`
does not count. That last one is the pitfall that goes unnoticed on a Mac
and produces a 404 on the server, so a bundle with only `Index.html` is
refused with a message that says so.

#### Enclosing directories are stripped

If in Finder or Explorer you compress the `dist` directory itself, instead
of its contents, then everything in the archive sits under `dist/`. If the
root contains exactly that one directory and nothing else, Plak strips it:
`dist/index.html` simply ends up at `/{group}/{site}/`. That repeats
as long as the root contains exactly one directory, so an archive with only
`mijnproject/build/dist/index.html` in it lands correctly too. Stripping
never loses a file: by definition there is nothing next to it.

#### Metadata from your operating system does not count

A zip you make with a right-click in Finder contains, next to your
directory, also `__MACOSX/`, with per file an AppleDouble copy starting
with `._`, and often a `.DS_Store`. Plak ignores those three entirely: they
do not stop the stripping, they are not proposed as an index and they do
not end up on your site. A Finder zip of your `dist` directory therefore
works without you having to set anything.

If after that ignoring nothing publishable is left in the archive, then
`422` follows with code `EMPTY_ARCHIVE`, the text saying that it contained
only metadata.

#### Secrets are refused, not silently published

A `.git` directory or a `.env` file in the bundle produces `422` with code
`SECRET_FILE`. Nothing is published. That is not a general ban on
dot files: `.well-known/` and `.gitignore` are fine.

The reason is that both come along by themselves as soon as you zip the
whole project directory instead of the built site. `.env` often contains a
secret, sometimes a CLI token with which someone else can publish to this
or another site on your behalf. And the serving side does not stop dot
files: what is in the bundle can be requested.

What you do: publish the directory with the built site, usually `dist` or
`build`. If that sits in the zip next to your project directory, send it
along as `basePath`; everything outside that path then does not count, so
a `.git` next to it does not stop the publication.

#### If something sits next to it, Plak does not choose

If something sits next to that directory in the root (a second directory,
or a loose file such as `LEESMIJ.md`), the stripping stops there. If there
is then no `index.html` in the root, the deploy is refused with `422`
and code `NO_INDEX`. Nothing is published: no version, no files.

Plak deliberately does not pick the shortest or deepest `index.html`
itself: that would silently leave out files you thought you were
publishing. Instead it makes a proposal. Next to the message, the error
response carries the field `indexCandidates`, with the `index.html` paths
found, shortest first and at most five:

```json
{
  "type": "about:blank",
  "title": "Onverwerkbare invoer",
  "status": 422,
  "detail": "geen index.html in de wortel van de bundel; de dichtstbijzijnde staat op 'dist/index.html'. Publiceer de map 'dist' zelf, of stuur het veld basispad mee met de waarde 'dist'",
  "code": "NO_INDEX",
  "indexCandidates": ["dist/index.html"]
}
```

#### The field `basePath`

With the optional form field `basePath` you confirm that proposal: that
directory inside the archive becomes the root of the site, and everything
next to it is not published.

```bash
curl -sS -X POST \
    -H "Authorization: Bearer ${PLAK_TOKEN}" \
    -F "file=@mijnproject.zip" \
    -F "basePath=dist" \
    "https://beheer.plak.example.org/-/api/v1/sites/${GROUP}/${SITE}/deploys"
```

With the CLI that is `--base-path dist`, with the action the input
`base-path`.

The path is relative to the root **after** the stripping, exactly like the
paths in `indexCandidates`: if everything sat under `mijnproject/`, then the
base path is `dist`. The spelling you see in your own zip
(`mijnproject/dist`) works just as well, and so does a `basePath` that the
stripping has already removed: a bundle with only `dist/index.html` in it
goes through fine with `basePath=dist`. That way a fixed
`base-path: dist` in a workflow keeps working, even if there is a
`LEESMIJ.md` next to your `dist` one time and not the next.

Beyond that it has to be an existing directory in the archive with an
`index.html` in it, and it has to pass the same path validation as any other
path (relative, no `..`, no reserved segments, no null bytes).
If it does not check out, a `422` follows; there is no silent fallback to
another root.

Limits (defaults, configurable per environment): request body at most
100 MiB, 50 MiB per unpacked file, 200 MiB unpacked cumulative, 1000
files, directory depth 10. Those limits apply to the paths as they are in
the archive, so an enclosing directory counts towards the directory depth.
What falls outside the `basePath` and is therefore not published does not
count towards the directory depth, the size and those 1000 files: a deep
`node_modules` next to your `dist` does not stop the deploy.

Two limits do apply to the whole archive, including what you do not
publish. The number of entries: fifty times the file limit, so
50,000 by default, because every entry Plak walks past costs memory. And
with a `.tar.gz` the unpacked size of all members together: a tar has
no index, so to get to the next header everything in between has to be
decompressed. A `.zip` does have that index and there counts only
what is published. In both cases the code is `TOO_MANY_FILES`
and `TOTAL_TOO_LARGE` respectively, and `detail` says whether it is about the
site or about the archive. If you run into the 1000 files of the site, then
that response carries `indexCandidates` too: a zipped project directory with
a `node_modules` in it comes out here first, and then `basePath` is the
solution.

The check on unsafe paths applies to every entry in the archive,
published or not.

Beyond the one bundle, the site itself has a ceiling: every version of it
together may occupy at most 500 MiB by default. A deploy that would cross it is
refused with a `413` and the code `SITE_QUOTA_EXCEEDED`, naming what the site
uses now and what this version would add. Every night Plak removes the live
versions older than the current one and the ones before it that the site
keeps: five by default (the platform administrator sets that default with
`PLAK_LIVE_VERSIONS_KEPT`), or the site's own number, which a site admin sets
on the site's Versions tab.
So the room usually comes back on its own the next day. Removing previews you no longer
need frees it at once; your platform administrator can also raise the
ceiling.

If the volume itself has no room for your deploy, it answers `503` with
`STORAGE_UNAVAILABLE`: before the body is read when the declared size does
not fit, or midway through the upload or the unpacking, in which case nothing
of it is kept. Nothing is wrong with your request then: try again later.

### Error contract

Errors come back as `application/problem+json`
(`{type, title, status, detail}`), supplemented with a `code` field with a
stable reason code (e.g. `TOKEN_INVALID`, `CI_REPOSITORY_NOT_TRUSTED`,
`PREVIEW_REF_INVALID`, `BODY_TOO_LARGE`):

| Status | Meaning |
|---|---|
| 401 | No CLI token, or an invalid, expired or revoked one (`TOKEN_INVALID`), or a CI token that does not check out: unknown issuer (`CI_ISSUER_UNKNOWN`), not a valid JWT, wrong algorithm or invalid claims (`CI_TOKEN_INVALID`), or an `aud` that is not exactly `PLAK_BASE_URL` (`CI_AUDIENCE_MISMATCH`) |
| 403 | The repository of the CI token is not (or no longer) linked to this site (`CI_REPOSITORY_NOT_TRUSTED`), or a live deploy from an event other than `push`, `workflow_dispatch` or `schedule`, or outside the configured live branch (`CI_BRANCH_NOT_ALLOWED`) |
| 404 | Unknown group or site (teardown of an unknown preview ref simply gives `204`) |
| 409 | Conflict; does not occur on the deploy endpoints, but does elsewhere in the admin API |
| 413 | Limit exceeded |
| 422 | Invalid archive, no `index.html` in the root, invalid `basePath`, invalid preview ref or invalid input |
| 429 | Rate limit; the response contains `Retry-After` |
| 503 | The CI provider is unreachable for fetching keys, or for confirming the repository again (with Forgejo without repository ids) (`CI_PROVIDER_UNREACHABLE`) |

The reason codes that are about the root of your bundle:

| Code | Status | Meaning |
|---|---|---|
| `NO_INDEX` | 422 | After the stripping there is no `index.html` in the root. `indexCandidates` carries the index paths found, shortest first; `detail` names the shortest path and what you should do |
| `BASE_PATH_INVALID` | 422 | The `basePath` sent along does not pass the path validation (absolute, `..`, reserved segment, null byte, empty or too deep) |
| `BASE_PATH_UNKNOWN` | 422 | The `basePath` is not a directory in the archive, or points at a file. `detail` names what you do not see: a directory that differs only in capitalisation, or the enclosing directory that was already stripped |
| `BASE_PATH_WITHOUT_INDEX` | 422 | The directory exists, but contains no `index.html` (or no files at all) |

A candidate from `indexCandidates` without a `/` in it is already in the
root of the bundle. There is then no directory to send back as `basePath`:
the recovery action is to leave that field out, and `detail` says so too.

A refused deploy leaves nothing behind: no version, no files and
no half upload. The live site stays as it was.

## 5. Limitations

- **Reserved segments in your dist**: a directory or file `_preview`,
  `_version` or `_versie` (the old spelling) in the root of your site
  is refused at ingest with a clear error message. These names
  belong to the platform (see §4 of the design spec) and can therefore not be
  part of your published file tree. This is about the root
  after the stripping and after your `basePath`: an archive `site/_preview/...`
  with `site/index.html` next to it is refused, because `_preview` then
  ends up in the root. If your enclosing directory is itself called `_preview`
  and gets stripped, there is no problem: what lands in the root
  is the contents, not the name.
- **External sources are on unless you turn them off**: scripts and styles
  from cdnjs, jsDelivr and unpkg, scripts from the Tailwind CDN
  (`https://cdn.tailwindcss.com`) and fonts from Google Fonts are allowed,
  so that a page which pulls Chart.js, Tailwind or the web components of a
  design system from a CDN simply works. On the tab "Toegang" (access)
  of the site you turn "Externe bronnen toestaan" (allow external sources)
  off; that is the safer choice for a confidential page, because then the
  page fetches nothing from outside and nobody outside sees who is viewing
  it. Blocked in both positions: fetching data from or sending it to other
  hosts (`connect-src` stays `'self'`), images from elsewhere, an iframe
  around the page, and a form that posts somewhere else. What you know for
  sure you bundle into your dist; that needs no external source and lets
  nobody watch along (with the shielding on, mind what §2 says about
  module scripts and fonts from your dist).
- **Shielding from other sites is on unless you turn it off**: module
  scripts, web fonts and `fetch` from your own site are refused and
  browser storage throws, which a build from Astro or Vite (Vue, React,
  Svelte) runs into straight away. What breaks, how it shows and what to do
  is in §2, "Shielding: what a build tool has to know".
- **Absolute base under `_version`**: if your site was built with an
  absolute base (for example hardcoded `https://voorbeeld.nl/pad/`), then
  that absolute base inside a `_version/{version-id}/` view still
  points at the live assets, not at the old version being viewed. A site
  that uses relative or environment-dependent base URLs (as in
  the examples above) does show exactly the version being viewed under
  `_version`. `_version` is an inspection feature for group members, not a
  guarantee of a pixel-perfect rendering of every build configuration.

## 6. Publishing from your own machine with `plak login`

Outside CI (or while trying out a workflow) you publish with a
CLI token instead of a CI ID token. `plak login` fetches such a token through
the OAuth 2.0 Device Authorization Grant (RFC 8628), without you ever
entering a password or client secret yourself:

```bash
uv tool install "git+https://github.com/DigiGilde/plak@beta#subdirectory=cli"
plak login
```

Every command finds its host in this order: `--host`, `PLAK_HOST`, the
host you last logged in to, and otherwise the DigiGilde instance,
`https://beheer.plak.rijks.app`. For another instance, log in once with
`plak login --host <admin origin>`; the commands below then need no
`--host` either.

`beta` is the default branch, so this installs the latest and
`uv tool upgrade plak` follows it. Pin to a version tag instead once one
exists; `README.md` carries both forms. The repository is public, so the
install needs no credentials. From a
checkout of this repository the same command runs without installing
anything:
`uv run --project cli plak login`, and so for every command below.

The CLI shows a code (`ABCD-EFGH`) and an address in the admin
(`/cli-link`); open that address (the CLI tries this automatically), check
that the code matches what your terminal shows and that the account at the top
is yours, and click "Koppelen" (link). If the request comes from a network
other than the one your browser is on now, the page warns about that
separately. Only continue if you started `plak login` yourself just now: if
you were sent this link or code by somebody else, click "Weigeren" (refuse).
Linking requires an admin session no older than fifteen minutes; if your
session is older, the admin sends you past the login again first.

After linking, the CLI stores the access token (`plakcli_...`, valid for an
hour) and the refresh token (`plakclr_...`) itself; they refresh each other
without you having to link again. A linked session works at most
30 days after the last refresh and in any case no longer than 90 days after
the linking itself, and acts with exactly the roles of the member who linked.

```bash
plak publish ./dist --site team-aurora/website --preview pr-42
```

Leave `--preview` out for a live deploy. You purge a preview with the
subcommand `preview-remove`:

```bash
plak preview-remove pr-42 --site team-aurora/website
```

### Creating a group or a site

A site has to exist before you can publish to it. With the CLI token you can
create both a group and a site from the terminal; you become admin of what
you create, exactly as in the admin. Deleting and managing members stay in
the admin: the CLI token is refused there.

```bash
plak group create team-aurora --name "Team Aurora"
plak site create team-aurora/docs --title "Documentation" --access sso
```

Any active member may create a group; a site needs group role `editor`
or `admin` in that group. The access flags are all optional:

| Flag | Meaning |
|---|---|
| `--access {public,sso,site_team,nobody}` | the base: anyone, anyone signed in with SSO Rijk, the members of the site and its group, or nobody except through the two exceptions below |
| `--secret-links` / `--no-secret-links` | whether a valid secret link lets anyone in, without signing in |
| `--invitees` / `--no-invitees` | whether invitees get in after signing in |

On a group they set the default access that new sites in that group start
with; left out, a group starts on `site_team` without exceptions. On a site
every flag you leave out follows the group's default access, so
`--secret-links` alone keeps the group's base and invitee setting and turns
secret links on. The CLI sends only the flags you gave; the server fills in
the rest.

The CLI prints what it created and the access it ended up with. When that is
not public, it says who can see it and where to change it:

```text
$ plak site create team/docs --title Docs --secret-links
Created site 'team/docs' (Docs). You are its admin.
Access: nobody (nobody by default), secret links on, invitees off.
Who can see it: anyone with a secret link.
Change it at: https://beheer.plak.example.org/team/docs/access
Publish to it with: plak publish <dist> --host https://beheer.plak.example.org --site team/docs
```

```text
$ plak group create team --name Team
Created group 'team' (Team). You are its admin.
Default access for new sites: site_team (members of the site and its group), secret links off, invitees off.
Who can see it: members of the site and its group.
Change it at: https://beheer.plak.example.org/team/-/settings
```

Per member at most 20 groups and sites together can be created per hour,
through the admin and the CLI together; over that the server answers 429 and
the CLI prints the reason. Exit codes are those of every other command: `0`
created, `1` refused or unreachable (the server's reason on stderr, for
instance an existing slug or a role that is too narrow), `2` wrong usage or
no session.

The CI ID token from a workflow cannot create anything: it is bound to the
one site whose repository is linked.

Your linked sessions are in the admin under the account menu,
"Gekoppelde sessies" (linked sessions, `/-/sessions`): name, when linked,
last used and when the session expires, with a button to revoke one. One
machine can hold several, and the name in a row is the one the client
program gave itself. Revoking takes effect immediately and retroactively:
whoever used that session has to do `plak login` again afterwards. You reach
the same from the machine itself with `plak logout`. That also works when the
access token has already expired: the CLI then sends the (expired) access
token or the refresh token
along (`DELETE /-/api/v1/cli/session`, with
`Authorization: Bearer plakcli_...` or a body
`{"refreshToken": "plakclr_..."}`). The response is always `204`, also for
an unknown token.

Refreshing happens automatically; if you start two CLI commands at the same
time, the second one may offer a refresh token that was just replaced. Within
ten seconds that only gives `INVALID_GRANT` and the session stays; if an old
refresh token turns up again later, Plak revokes the whole session,
because then somebody else has it. If a platform administrator deactivates
you, all your linked sessions disappear; after reactivation you link again.

## 7. CLI and server compatibility

The CLI and the server are released independently. They are coupled through
the HTTP API, whose contract version the server sends as the `API-Version`
response header (for example `1.0.0`).

- Within a major version the server keeps the API backward compatible, so an
  older CLI keeps working against a newer server.
- A breaking change bumps the major, and ships together with a new CLI. A
  pull request check refuses one without it; minor and patch follow from
  what a release changed in the schema (`docs/releasing.md`).
- The CLI reads `API-Version` on every response, before it looks at the body.
  If the server's major is higher than the one the CLI supports, it stops with
  exit code 1 and `Error: this server speaks API 2.x, this plak CLI (<version>)
  supports API 1.x. Install a newer CLI: ...`. A missing or malformed header
  is ignored.
- `plak --version` prints the installed version (`plak 2026.10.1`). The CLI also
  identifies itself on every request as `User-Agent: plak-cli/<version>`.

### A new major

There is only API 1 so far. When a second major comes, the
[NL API Design Rules 2.2.2](https://gitdocumentatie.logius.nl/publicatie/api/adr/2.2.2/)
set what happens to the first (`/core/transition-period`,
`/core/deprecation-schedule`):

- **The old major stays.** `/-/api/v1` keeps running next to `/-/api/v2`
  for a limited and fixed transition period, and no more than two majors
  run at once.
- **The schedule is public before the transition starts:** when v1 was
  deprecated and the date it goes away, under `### Deprecated` in
  `CHANGELOG.md` and in these docs. Members who use the API through a
  linked repository or the CLI hear about it directly as well.
- **Every v1 response says so** during the transition: `Deprecation`
  ([RFC 9745](https://www.rfc-editor.org/rfc/rfc9745), the date v1 was
  deprecated, as `@<unix time>`) and `Sunset`
  ([RFC 8594](https://www.rfc-editor.org/rfc/rfc8594), the date it goes
  away, as an HTTP date).
- **The CLI that speaks v2 comes out before v1 goes away**, so an upgrade
  is possible during the whole transition.

How long the transition lasts is decided when v2 is planned.
