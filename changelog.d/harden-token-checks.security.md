- Plak fetches the signing keys and the discovery document of the identity
  provider again every hour, so a key the provider withdraws stops being
  trusted without a restart.
- A CI ID token that carries a `jti` (GitHub) is accepted once. A second use
  within its lifetime is refused, and the audit log records it as
  `CI_TOKEN_REPLAYED` instead of `CI_TOKEN_INVALID`.
- A non-admin reaching the platform member routes is refused and audited
  before a malformed request is looked at.
- JSON and other non-HTML answers of the admin host carry `Referrer-Policy`
  and `X-Frame-Options`.
