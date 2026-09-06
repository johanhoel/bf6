from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bf6tuner import crypto  # noqa: E402

pytest.importorskip("cryptography")


def test_roundtrip():
    key = crypto.new_key()
    payload = {"gpu_db": {"gpus": [{"id": "rtx 4090"}]}}
    blob = crypto.pack(payload, key, {"version": "test"})
    restored, header = crypto.unpack(blob, key)
    assert restored == payload
    assert header["version"] == "test"


def test_wrong_key_is_rejected():
    blob = crypto.pack({"a": 1}, crypto.new_key())
    with pytest.raises(crypto.BundleError):
        crypto.unpack(blob, crypto.new_key())


def test_tampered_payload_is_rejected():
    key = crypto.new_key()
    blob = bytearray(crypto.pack({"a": 1}, key))
    blob[-5] ^= 0xFF
    with pytest.raises(crypto.BundleError, match="integrity"):
        crypto.unpack(bytes(blob), key)


def test_tampered_header_is_rejected():
    key = crypto.new_key()
    blob = crypto.pack({"a": 1}, key, {"version": "1.0"})
    tampered = blob.replace(b'"1.0"', b'"9.9"')
    assert tampered != blob
    with pytest.raises(crypto.BundleError):
        crypto.unpack(tampered, key)


def test_garbage_is_rejected():
    with pytest.raises(crypto.BundleError):
        crypto.unpack(b"not a bundle at all, really not", crypto.new_key())
