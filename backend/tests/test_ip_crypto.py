"""Tests for audit/ip_crypto.py: format, AAD row-binding and key rotation.

No database needed: these exercise encrypt_ip/decrypt_ip directly.
"""

from __future__ import annotations

import uuid

import pytest

from plak.audit.ip_crypto import IpDecryptError, decrypt_ip, encrypt_ip

_KEY_A = b"a" * 32
_KEY_B = b"b" * 32


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


def test_bad_version_byte_refused() -> None:
    entry_id = uuid.uuid4()
    blob = encrypt_ip(_KEY_A, entry_id, "203.0.113.42")
    corrupted = b"\x09" + blob[1:]
    with pytest.raises(IpDecryptError):
        decrypt_ip(_KEY_A, entry_id, corrupted)


def test_too_short_blob_refused() -> None:
    with pytest.raises(IpDecryptError):
        decrypt_ip(_KEY_A, uuid.uuid4(), b"\x02short")


def test_previous_key_decrypts_a_row_written_under_it() -> None:
    """The usual key-rotation shape: rows written before the rotation stay
    readable with the old key passed as `previous_key`."""
    entry_id = uuid.uuid4()
    blob = encrypt_ip(_KEY_A, entry_id, "203.0.113.42")
    assert decrypt_ip(_KEY_B, entry_id, blob, previous_key=_KEY_A) == "203.0.113.42"


def test_current_key_tried_before_previous() -> None:
    entry_id = uuid.uuid4()
    blob = encrypt_ip(_KEY_B, entry_id, "203.0.113.42")
    assert decrypt_ip(_KEY_B, entry_id, blob, previous_key=_KEY_A) == "203.0.113.42"


def test_neither_current_nor_previous_key_matches() -> None:
    entry_id = uuid.uuid4()
    blob = encrypt_ip(_KEY_A, entry_id, "203.0.113.42")
    with pytest.raises(IpDecryptError):
        decrypt_ip(_KEY_B, entry_id, blob, previous_key=b"c" * 32)
