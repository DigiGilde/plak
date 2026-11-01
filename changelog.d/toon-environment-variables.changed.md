- The server reads its settings from `TOON_<NAME>` variables.
- The `PLAK_<NAME>` names keep working for now, and at startup the app logs a
  warning that names every `PLAK_` variable still in use.
- A second warning names a `PLAK_` audit secret whose value the `TOON_` names
  neither hold nor list as previous, so a wrong value shows before the old one
  is removed.
- New audit chain heads say `toon-audit-chain-checkpoint/3` as their format, and
  lines in the older `plak-audit-chain-checkpoint/3` format still verify.
- The image build argument is `TOON_VERSION` instead of `PLAK_VERSION`.
