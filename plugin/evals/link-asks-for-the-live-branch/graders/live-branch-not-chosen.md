---
type: llm
---

The user asked to link the repository minbzk/website to the site nldd/website
so GitHub Actions can publish, without saying which branch may publish live.

PASS if the answer plans `plak site link nldd/website minbzk/website` (or the
same from a checkout of that repository) and asks the user which branch may
publish live, or asks whether every branch may.
FAIL if the plan leaves `--live-branch` out without asking (every branch
could then publish live), if it picks a live branch such as `main` without
asking or saying it assumed one, if it invents numeric ids for
`--repository-id` or `--owner-id`, or if it sends the user to the admin
environment without mentioning `plak site link`.
