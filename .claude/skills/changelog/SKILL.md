---
name: changelog
description: Use when writing a changelog fragment in the Plak repository (changelog.d/<slug>.<kind>.md or a What's new note), before opening a pull request that changes backend, frontend, CLI, plugin, container or action code, when the changelog check warns or fails on a pull request, or when the PreToolUse hook denies `gh pr create` for a missing fragment. Covers the fragment file, which kind to pick, the "No changelog entry:" opt-out and the bilingual What's new note fragments.
---

# Writing a changelog fragment

Plak releases itself from `CHANGELOG.md` (the full model is in
`docs/releasing.md`). Every pull request that changes what ships adds a
fragment file under `changelog.d/` in the same pull request. Nobody
edits `CHANGELOG.md` for a change, and nobody writes version numbers or
version sections: a release pull request does that.

A release is made from a button or `just release`, and merging it tags
the release. Its notes are the fragments, so a change without one waits
for a release that has an entry.

## The file

`changelog.d/<slug>.<kind>.md`, for example
`changelog.d/preview-after-live.fixed.md`:

```markdown
- A preview stays reachable after the site is published live again.
```

- **Slug:** lowercase words joined by hyphens, naming the change.
- **Kind:** `added`, `changed`, `deprecated`, `removed`, `fixed` or
  `security`.
- **Content:** list items as they appear in `CHANGELOG.md`, no heading.
  One line per change. A long line may wrap onto an indented next line.
- **Describe the effect, not the implementation.** What does a member,
  a publisher or an operator notice? "Invitees get the login page
  instead of a 404", not "Check invitees before the group lookup in the
  gate".
- **One sharp sentence.** No preamble, no rationale (that belongs in the
  commit), no pull request number.
- **English**, like everything around the interface. Quote a Dutch
  interface label literally where you name one.
- No em dash or en dash.

## The kind

| Kind | For |
|---|---|
| `added` | something new |
| `changed` | something that works differently |
| `deprecated` | something that will go away |
| `removed` | something that went away |
| `fixed` | a bug that is gone |
| `security` | anything security relevant: the access gate, sessions, the ingest, the serving, headers, a dependency with an advisory. Security wins over the other kinds. |

One change with two effects can be two files with different slugs.

## No entry needed

A change to a shipped path that has no effect worth a line (a refactor,
a lockfile refresh without a fix in it, a test helper under `src/`) says
so in the pull request description, on a line of its own:

```text
No changelog entry: refactor without a change in behaviour
```

That line clears the CI warning and lets the `gh pr create` hook
through. The change then reaches production with the next release. A
dependency update that should go out sooner (a fix for an advisory)
gets a `security` fragment instead. Changes that only touch tests, docs,
workflows or the dev stack need neither a fragment nor that line.

## What's new notes

Some changes also need a What's new note, in both languages, as two more
fragments in the same pull request:

- `changelog.d/<slug>.note.nl.md` (Dutch)
- `changelog.d/<slug>.note.en.md` (English)

When:

- **Added or Changed** that a member notices, in the admin, the CLI, the
  plugin or a published site: a note.
- **Fixed**: a note only when members saw the bug themselves.
- **Security, dependency or process changes** that nobody notices: never.

Not every fragment gets a note, on purpose: the page is for members and
stays short.

Rules:

- Markdown, usually a `## ` heading and one short paragraph or a few
  bullets.
- In the words of the interface: use the labels the admin shows, in each
  language its own ("Toegang" and "Access").
- Both files, always, and neither empty. One language only fails the
  check.
- A note does not need an entry of the same slug, and an entry does not
  need a note. Note fragments alone never make a release.

The CI check prints a hint when `frontend/src/` or `cli/plak_cli/`
changed without a note fragment. It is a question, not an error: add a
note if the answer is yes.

## Never

- Never edit `CHANGELOG.md` for a change, and never bring back a
  `## [Unreleased]` section: the check fails on it.
- Never edit a released section (`## [2026.9.30]`: the version of the
  tag `v2026.9.30`, without the v and without a date). A correction is a
  new fragment.
- Never add a version section or change a version number by hand.
- Never edit the link references at the bottom (`[2026.10.4]: https://…`).
  A release writes them, and `release.py links` restores them.
- Never put another file in `changelog.d/` than fragments and its
  `README.md`.
