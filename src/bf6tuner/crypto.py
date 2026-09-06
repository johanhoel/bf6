"""Encrypted database bundle format.

The shipped executable carries its settings database as a single AES-256-GCM
blob rather than as editable JSON files sitting next to the .exe. That stops
casual copying and casual tampering, and the GCM tag means a modified bundle
fails to load instead of silently feeding bad values into the recommendation
engine.

It is *not* DRM. The key travels inside the binary, because the binary has to
decrypt without a server. Anyone determined enough to run a debugger will get
the plaintext. What this buys is a real integrity check plus a meaningful step
up from "double-click the JSON and edit it".

Bundle layout::

    b"BF6TDB\x01"    magic + format version
    <uint32 BE>      length of the header JSON
    <header JSON>    {"created": ..., "version": ...} - authenticated, not secret
    <12 bytes>       AES-GCM nonce
    <ciphertext>     AES-256-GCM(payload) with the tag appended
"""

from __future__ import annotations

import gzip
import json
import os
import struct
from typing import Any

MAGIC = b"BF6TDB\x01"
AAD = b"bf6tuner/db/1"
NONCE_BYTES = 12
KEY_BYTES = 32


class BundleError(RuntimeError):
    """Raised when a bundle is missing, malformed, or fails its integrity check."""


def _aesgcm(key: bytes):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise BundleError(
            "The 'cryptography' package is required to read the encrypted database. "
            "Install it with: pip install cryptography"
        ) from exc
    if len(key) != KEY_BYTES:
        raise BundleError(f"Bundle key must be {KEY_BYTES} bytes, got {len(key)}")
    return AESGCM(key)


def new_key() -> bytes:
    """Generate a fresh random bundle key. Called once per build."""
    return os.urandom(KEY_BYTES)


def pack(payload: dict[str, Any], key: bytes, header: dict[str, Any] | None = None) -> bytes:
    """Compress, encrypt and frame ``payload`` into a bundle."""
    header = dict(header or {})
    header_bytes = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    plaintext = gzip.compress(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"), mtime=0
    )
    nonce = os.urandom(NONCE_BYTES)
    # The header is authenticated alongside the payload so it cannot be swapped
    # for one claiming a different database version.
    ciphertext = _aesgcm(key).encrypt(nonce, plaintext, AAD + header_bytes)
    return MAGIC + struct.pack(">I", len(header_bytes)) + header_bytes + nonce + ciphertext


def unpack(blob: bytes, key: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify and decrypt a bundle. Returns ``(payload, header)``."""
    if len(blob) < len(MAGIC) + 4 + NONCE_BYTES + 16:
        raise BundleError("Database bundle is truncated.")
    if not blob.startswith(MAGIC):
        raise BundleError("Not a BF6 Tuner database bundle (bad magic).")

    offset = len(MAGIC)
    (header_len,) = struct.unpack(">I", blob[offset : offset + 4])
    offset += 4
    if header_len > len(blob):
        raise BundleError("Database bundle header length is out of range.")

    header_bytes = blob[offset : offset + header_len]
    offset += header_len
    nonce = blob[offset : offset + NONCE_BYTES]
    offset += NONCE_BYTES
    ciphertext = blob[offset:]

    try:
        plaintext = _aesgcm(key).decrypt(nonce, ciphertext, AAD + header_bytes)
    except BundleError:
        raise
    except Exception as exc:
        raise BundleError(
            "Database bundle failed its integrity check. It has been modified or "
            "does not match this build of the application."
        ) from exc

    try:
        payload = json.loads(gzip.decompress(plaintext).decode("utf-8"))
        header = json.loads(header_bytes.decode("utf-8"))
    except Exception as exc:
        raise BundleError("Database bundle decrypted but its contents are unreadable.") from exc

    return payload, header
