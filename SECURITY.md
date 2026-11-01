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
rendered per request (`backend/src/toon/platform/security_txt.py`);
`Expires` lies 90 days ahead, counted from the start of the current UTC
day, so that it never expires without anyone doing anything.

Five things to know:

- The GitHub advisory form is the first `Contact`, and this file the
  first `Policy`. Both only became reachable when the repository went
  public on 2026-09-27 with private vulnerability reporting switched on;
  before that they would have answered a 404, which is worse than one
  route fewer.
- The second `Contact` is `mailto:` the address in `TOON_OPERATOR_EMAIL`,
  the organisation that runs the installation. On the Digi Gilde's own
  installation that is `digigilde@rijksoverheid.nl`, the same address as
  in `publiccode.yml`. Without the setting there is no mail line of the
  operator, and the advisory form keeps the required `Contact` present.
- The CVD routes of NCSC-NL, the central reporting point for the Dutch
  central government, are listed only when the installation enables
  them with `TOON_SECURITY_TXT_NCSC=true`. They then add two `Policy`
  lines after this file and three `Contact` lines after the operator's
  address, under a preamble that names NCSC-NL. The Digi Gilde's own
  installation enables them.
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
