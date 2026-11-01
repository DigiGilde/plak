"""Tests for audit/ip_crypto.py: format, AAD row-binding and key rotation.

No database needed: these exercise encrypt_ip/decrypt_ip directly.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from toon.audit import ip_crypto
from toon.audit.ip_crypto import IpDecryptError, decrypt_ip, encrypt_ip

_KEY_A = b"a" * 32
_KEY_B = b"b" * 32
_KEY_C = b"c" * 32
_KEY_D = b"d" * 32


def test_roundtrip() -> None:
    entry_id = uuid.uuid4()
    blob = encrypt_ip(_KEY_A, entry_id, "203.0.113.42")
    assert decrypt_ip(_KEY_A, entry_id, blob) == "203.0.113.42"


def test_ciphertext_copied_onto_another_row_does_not_decrypt() -> None:
    """The AAD binds the ciphertext to its own row: pasting it onto another
    audit_log_entries row (a forged row, say) must not decrypt there."""
    entry_id = uuid.uuid4()
    other_id = uuid.uuid4()
    blob = encrypt_ip(_KEY_A, entry_id, "203.0.113.42")
    with pytest.raises(IpDecryptError):
        decrypt_ip(_KEY_A, other_id, blob)


def test_wrong_key_refused() -> None:
    entry_id = uuid.uuid4()
    blob = encrypt_ip(_KEY_A, entry_id, "203.0.113.42")
    with pytest.raises(IpDecryptError):
        decrypt_ip(_KEY_B, entry_id, blob)


def test_tampered_ciphertext_refused() -> None:
    entry_id = uuid.uuid4()
    blob = bytearray(encrypt_ip(_KEY_A, entry_id, "203.0.113.42"))
    blob[-1] ^= 0xFF
    with pytest.raises(IpDecryptError):
        decrypt_ip(_KEY_A, entry_id, bytes(blob))


@pytest.mark.parametrize("version", [b"\x00", b"\x01", b"\x04", b"\x09", b"\xff"])
def test_bad_version_byte_refused(version: bytes) -> None:
    entry_id = uuid.uuid4()
    blob = encrypt_ip(_KEY_A, entry_id, "203.0.113.42")
    corrupted = version + blob[1:]
    with pytest.raises(IpDecryptError, match="unknown version"):
        decrypt_ip(_KEY_A, entry_id, corrupted)


@pytest.mark.parametrize("version", [b"\x02", b"\x03"])
def test_too_short_blob_refused(version: bytes) -> None:
    with pytest.raises(IpDecryptError, match="unknown version"):
        decrypt_ip(_KEY_A, uuid.uuid4(), version + b"short")


def _hand_built_blob(version: bytes, aad_prefix: bytes, key: bytes, entry_id: uuid.UUID, ip: str) -> bytes:
    """The format spelled out, without the module's own constants: what a row
    holds does not change when those do."""
    nonce = b"n" * 12
    ciphertext = AESGCM(key).encrypt(nonce, ip.encode(), aad_prefix + entry_id.bytes)
    return version + hashlib.sha256(key).digest()[:8] + nonce + ciphertext


def test_a_new_blob_carries_the_new_version_and_the_new_aad() -> None:
    entry_id = uuid.uuid4()
    blob = encrypt_ip(_KEY_A, entry_id, "203.0.113.42")
    assert blob[:1] == b"\x03"
    plaintext = AESGCM(_KEY_A).decrypt(blob[9:21], blob[21:], b"toon-audit-ip-v1" + entry_id.bytes)
    assert plaintext == b"203.0.113.42"


def test_a_hand_built_new_blob_decrypts() -> None:
    entry_id = uuid.uuid4()
    blob = _hand_built_blob(b"\x03", b"toon-audit-ip-v1", _KEY_A, entry_id, "203.0.113.42")
    assert decrypt_ip(_KEY_A, entry_id, blob) == "203.0.113.42"


def test_a_row_written_under_the_old_version_stays_readable() -> None:
    """Rows are immutable, so what was written before the rename is read as it
    was written, for as long as the row lives."""
    entry_id = uuid.uuid4()
    blob = _hand_built_blob(b"\x02", b"plak-audit-ip-v1", _KEY_A, entry_id, "2001:db8::1")
    assert decrypt_ip(_KEY_A, entry_id, blob) == "2001:db8::1"


def test_a_row_written_under_the_old_version_and_a_previous_key_stays_readable() -> None:
    entry_id = uuid.uuid4()
    blob = _hand_built_blob(b"\x02", b"plak-audit-ip-v1", _KEY_A, entry_id, "203.0.113.42")
    assert decrypt_ip(_KEY_C, entry_id, blob, previous_keys=(_KEY_B, _KEY_A)) == "203.0.113.42"


@pytest.mark.parametrize(
    ("version", "aad_prefix"),
    [(b"\x02", b"toon-audit-ip-v1"), (b"\x03", b"plak-audit-ip-v1")],
    ids=["old-version-new-aad", "new-version-old-aad"],
)
def test_the_version_byte_decides_the_aad(version: bytes, aad_prefix: bytes) -> None:
    """No trying one context string after the other: a blob whose version does
    not match its AAD is not a row either version ever wrote."""
    entry_id = uuid.uuid4()
    blob = _hand_built_blob(version, aad_prefix, _KEY_A, entry_id, "203.0.113.42")
    with pytest.raises(IpDecryptError, match="cannot be decrypted with this key"):
        decrypt_ip(_KEY_A, entry_id, blob)


def test_an_old_blob_copied_onto_another_row_does_not_decrypt() -> None:
    entry_id = uuid.uuid4()
    blob = _hand_built_blob(b"\x02", b"plak-audit-ip-v1", _KEY_A, entry_id, "203.0.113.42")
    with pytest.raises(IpDecryptError, match="cannot be decrypted with this key"):
        decrypt_ip(_KEY_A, uuid.uuid4(), blob)


def test_previous_key_decrypts_a_row_written_under_it() -> None:
    """The usual key-rotation shape: rows written before the rotation stay
    readable with the old key passed in `previous_keys`."""
    entry_id = uuid.uuid4()
    blob = encrypt_ip(_KEY_A, entry_id, "203.0.113.42")
    assert decrypt_ip(_KEY_B, entry_id, blob, previous_keys=[_KEY_A]) == "203.0.113.42"


def test_current_key_tried_before_previous() -> None:
    entry_id = uuid.uuid4()
    blob = encrypt_ip(_KEY_B, entry_id, "203.0.113.42")
    assert decrypt_ip(_KEY_B, entry_id, blob, previous_keys=[_KEY_A]) == "203.0.113.42"


def test_neither_current_nor_previous_key_matches() -> None:
    entry_id = uuid.uuid4()
    blob = encrypt_ip(_KEY_A, entry_id, "203.0.113.42")
    with pytest.raises(IpDecryptError):
        decrypt_ip(_KEY_B, entry_id, blob, previous_keys=[_KEY_C])


def test_the_second_of_two_previous_keys_decrypts_a_row_written_under_it() -> None:
    """Two rotations on: the oldest key still configured is the last one."""
    entry_id = uuid.uuid4()
    blob = encrypt_ip(_KEY_A, entry_id, "2001:db8::1")
    assert decrypt_ip(_KEY_C, entry_id, blob, previous_keys=(_KEY_B, _KEY_A)) == "2001:db8::1"


def test_a_key_id_that_names_no_configured_key_is_refused() -> None:
    entry_id = uuid.uuid4()
    blob = encrypt_ip(_KEY_A, entry_id, "203.0.113.42")
    with pytest.raises(IpDecryptError, match="none of the configured keys"):
        decrypt_ip(_KEY_C, entry_id, blob, previous_keys=(_KEY_B, _KEY_D))


def test_only_the_key_the_blob_names_is_tried(monkeypatch) -> None:
    """The key id picks the key: no trial decryption against every configured
    key, and a key that fails to decrypt is not followed by the next one."""
    entry_id = uuid.uuid4()
    blob = encrypt_ip(_KEY_A, entry_id, "203.0.113.42")
    tried: list[bytes] = []
    real_aesgcm = ip_crypto.AESGCM

    def recording_aesgcm(key: bytes):
        tried.append(key)
        return real_aesgcm(key)

    monkeypatch.setattr(ip_crypto, "AESGCM", recording_aesgcm)
    assert decrypt_ip(_KEY_C, entry_id, blob, previous_keys=(_KEY_B, _KEY_A, _KEY_D)) == "203.0.113.42"
    assert tried == [_KEY_A]

    tried.clear()
    with pytest.raises(IpDecryptError, match="cannot be decrypted with this key"):
        decrypt_ip(_KEY_C, uuid.uuid4(), blob, previous_keys=(_KEY_B, _KEY_A, _KEY_D))
    assert tried == [_KEY_A]
