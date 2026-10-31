# Changelog fragments

One file per change, added in the pull request that makes it. A release
turns them into a section of `CHANGELOG.md` and deletes them.

- `<slug>.<kind>.md`: list items for the changelog, where kind is
  `added`, `changed`, `deprecated`, `removed`, `fixed` or `security`.
- `<slug>.note.nl.md` and `<slug>.note.en.md`: a What's new note, in
  both languages.

The slug is lowercase words joined by hyphens. Nothing else belongs
here. See `docs/releasing.md` and the `changelog` skill.
