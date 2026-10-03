---
name: changelog
description: Use when writing or editing a CHANGELOG.md entry in the Plak repository, before opening a pull request that changes backend, frontend, CLI, plugin, container or action code, when the changelog check warns or fails on a pull request, or when the PreToolUse hook denies `gh pr create` for a missing entry under [Unreleased]. Covers the entry line, which heading to pick, the hold marker, the "No changelog entry:" opt-out and the bilingual What's new notes.
---

# Writing a changelog entry

Plak releases itself from `CHANGELOG.md` (the full model is in
`docs/releasing.md`). Every pull request that changes what ships adds
its line under `## [Unreleased]` in the same pull request. Nobody writes
version numbers or version sections: the release step does that.

Only a push to `beta` with entries under `[Unreleased]` (and no hold
marker) becomes a release and goes to production, with those entries
as its release notes. So an entry is also what ships a change: without
one it waits for the next release.

## The line

One line per change, a list item under the right heading:

```markdown
## [Unreleased]

### Fixed

- A preview stays reachable after the site is published live again.
```

- **Describe the effect, not the implementation.** What does a member,
  a publisher or an operator notice? "Invitees get the login page
  instead of a 404", not "Check invitees before the group lookup in the
  gate".
- **One sharp sentence.** No preamble, no rationale (that belongs in the
  commit), no pull request number. A long line may wrap onto an indented
  next line.
- **English**, like everything around the interface. Quote a Dutch
  interface label literally where you name one.
- No em dash or en dash.

## The heading

| Heading | For |
|---|---|
| `### Added` | something new |
| `### Changed` | something that works differently |
| `### Deprecated` | something that will go away |
| `### Removed` | something that went away |
| `### Fixed` | a bug that is gone |
| `### Security` | anything security relevant: the access gate, sessions, the ingest, the serving, headers, a dependency with an advisory. Security wins over the other headings. |

Add a heading only if it is not there yet; each heading appears once and
never stays empty.

## No entry needed

A change to a shipped path that has no effect worth a line (a refactor,
a lockfile refresh without a fix in it, a test helper under `src/`) says
so in the pull request description, on a line of its own:

```text
No changelog entry: refactor without a change in behaviour
```

That line clears the CI warning and lets the `gh pr create` hook
through; the change then reaches production with the next release. A
dependency update that should go out sooner (a fix for an advisory)
gets an entry instead, under `### Security` when it is one. Changes that only touch tests, docs, workflows or the dev stack
need neither an entry nor that line.

## Holding releases

`<!-- release: hold -->` on its own line anywhere under `[Unreleased]`
stops pushes to `beta` from releasing, for work that lands over several
pull requests. Only add or remove it when the user asks for it; removing
it releases everything that collected.

## What's new notes

Some entries also need a What's new note, in both languages, in the
same pull request:

- `frontend/src/content/releases/unreleased.nl.md` (Dutch)
- `frontend/src/content/releases/unreleased.en.md` (English)

When:

- **Added or Changed** that a member notices, in the admin, the CLI, the
  plugin or a published site: a note.
- **Fixed**: a note only when members saw the bug themselves.
- **Security, dependency or process changes** that nobody notices: never.

Not every changelog line gets a note, on purpose: the page is for
members and stays short.

Rules:

- In the words of the interface: use the labels the admin shows, in each
  language its own ("Toegang" and "Access").
- Both files, always. One language only fails the check; so does one
  empty file next to one with content.
- Append to the file if it already exists; keep it short, one short
  paragraph or a few bullets per change.

The CI check prints a hint when `frontend/src/` or `cli/plak_cli/`
changed without a note. It is a question, not an error: add a note if
the answer is yes.

## Never

- Never edit a released section (`## [2026.9.30]`: the version of the
  tag `v2026.9.30`, without the v and without a date); the check fails
  on it. A correction is a new line under `[Unreleased]`.
- Never add a version section or change a version number by hand.
