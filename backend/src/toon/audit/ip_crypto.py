"""Reversible encryption of the full audit IP address.

`ip_truncated` on audit_log_entries is what the log shows by default; this is
the encrypted full address next to it, under its own key
(`TOON_AUDIT_IP_KEY`, separate from the audit pepper) so revealing it is a
deliberate, audited act (`audit_ip_reveal`) and not a byproduct of reading
the pseudonym or the truncated network.

Format: 1 version byte, 8-byte key id (first 8 bytes of SHA-256 of the raw
key, so a rotated key can be identified without trial decryption), 12-byte
random nonce, then the AES-256-GCM ciphertext (tag included). The AAD binds
the ciphertext to the row it lives on (the fixed context string plus the
row's id, 16 raw bytes): without that, a ciphertext copied onto another row
would decrypt there too, since AES-GCM only proves the ciphertext was not
tampered with, not which row it belongs to.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Sequence

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = b"\x03"
_KEY_ID_LEN = 8
_NONCE_LEN = 12
_HEADER_LEN = len(_VERSION) + _KEY_ID_LEN + _NONCE_LEN
# Rows are immutable, so \x02 must stay readable until the last such row is purged.
_AAD_PREFIXES = {_VERSION: b"toon-audit-ip-v1", b"\x02": b"plak-audit-ip-v1"}


class IpDecryptError(ValueError):
    """Raised when a blob cannot be decrypted: wrong key, wrong row, wrong version, or corrupt data."""


def _key_id(key: bytes) -> bytes:
    """Public, non-secret fingerprint of a key: identifies which of the
    configured keys a row was encrypted under, without needing to try each
    of them against every row."""
    return hashlib.sha256(key).digest()[:_KEY_ID_LEN]


def _aad(version: bytes, entry_id: uuid.UUID) -> bytes:
    return _AAD_PREFIXES[version] + entry_id.bytes


def encrypt_ip(key: bytes, entry_id: uuid.UUID, ip: str) -> bytes:
    nonce = os.urandom(_NONCE_LEN)
    ciphertext = AESGCM(key).encrypt(nonce, ip.encode("utf-8"), _aad(_VERSION, entry_id))
    return _VERSION + _key_id(key) + nonce + ciphertext


def decrypt_ip(key: bytes, entry_id: uuid.UUID, blob: bytes, *, previous_keys: Sequence[bytes] = ()) -> str:
    """Decrypts `blob`, the `ip_encrypted` value of the row identified by
    `entry_id` (the AAD binds the two together), with the one key among `key`
    and `previous_keys` whose key id the blob carries. A row encrypted before
    a key rotation names one of the previous keys."""
    version = blob[:1]
    if len(blob) < _HEADER_LEN or version not in _AAD_PREFIXES:
        raise IpDecryptError("unknown version of the encrypted IP address")
    blob_key_id = blob[len(_VERSION) : len(_VERSION) + _KEY_ID_LEN]
    nonce = blob[len(_VERSION) + _KEY_ID_LEN : _HEADER_LEN]
    ciphertext = blob[_HEADER_LEN:]
    aad = _aad(version, entry_id)

    for candidate in (key, *previous_keys):
        if _key_id(candidate) != blob_key_id:
            continue
        try:
            return AESGCM(candidate).decrypt(nonce, ciphertext, aad).decode("utf-8")
        except InvalidTag as error:
            raise IpDecryptError("IP address cannot be decrypted with this key") from error
    raise IpDecryptError("none of the configured keys belongs to this encrypted IP address")
