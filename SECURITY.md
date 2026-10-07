# Security policy

## Responsible disclosure

Plak runs in production as a beta (see `README.md`). If you have found a
security vulnerability, report it responsibly:

- Report vulnerabilities privately, not through a public GitHub issue.
- Preferably through
  [a private security advisory](https://github.com/DigiGilde/plak/security/advisories/new):
  private vulnerability reporting is on, so the report stays between you
  and the maintainers until there is a fix.
- By email it goes to `digigilde@rijksoverheid.nl` (see `publiccode.yml`,
  `maintenance.contacts`).
- Give us reasonable time to investigate and fix the problem before you
  publish details.
- Do not take any action beyond what is needed to demonstrate the
  vulnerability (no data exfiltration, no disruption of the service).

We confirm receipt within 5 working days and keep you posted on the
progress until the problem is solved. We aim to have a fix or a
mitigation within 90 days of the report, sooner for a serious problem,
and agree a date with you if it will take longer.

## security.txt

Both hosts serve a `security.txt` conforming to RFC 9116 under
`/.well-known/security.txt`, with the same document, so that the
retrieval URL is always covered by a `Canonical` line. The file is
rendered per request (`backend/src/plak/platform/security_txt.py`);
`Expires` lies 90 days ahead, counted from the start of the current UTC
day, so that it never expires without anyone doing anything.

Four things to know:

- The GitHub advisory form is the first `Contact`, and this file the
  first `Policy`. Both only became reachable when the repository went
  public on 2026-09-27 with private vulnerability reporting switched on;
  before that they would have answered a 404, which is worse than one
  route fewer.
- `digigilde@rijksoverheid.nl` is the same address as in
  `publiccode.yml`. The CVD routes of NCSC-NL follow behind it, so a
  reporter ends up somewhere either way.
- There is no `Encryption` field. RFC 9116 cannot tie a key to one
  `Contact`, and the first contacts are the Plak team, which has no key of
  its own; NCSC-NL's key there would read as ours. The advisory form needs
  no key. `sectxt` recommends the field; leaving it out is deliberate.
- We do not sign the file with PGP. `sectxt` recommends it, but signing
  asks for key management and rotation that this repo has nowhere else,
  and a signature that expires is more harmful than no signature. This is
  a deliberate choice, not an omission.

`backend/tests/test_security_txt.py` runs the rendered file through
`sectxt`, the parser behind the Digital Trust Center's check.

## Scope

This policy covers the code in this repository, the CLI (`cli/`) and the
`actions/publish` action (`actions/`) included. Vulnerabilities in underlying
infrastructure (ZAD, the OIDC provider in use, and so on) you report to
the administrator of that service.
