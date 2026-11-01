- The audit pepper and the IP key can be rotated without losing the ability to
  look up or decrypt older audit rows: `PLAK_AUDIT_PEPPER_PREVIOUS` and
  `PLAK_AUDIT_IP_KEY_PREVIOUS` hold comma-separated lists of earlier values,
  and `actor-pseudonym` returns the pseudonyms under earlier peppers as
  `previousPseudonyms`.
