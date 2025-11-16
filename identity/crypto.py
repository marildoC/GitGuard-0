# identity/crypto.py - AES-GCM encryption helpers for face gallery and other sensitive data.

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except Exception as exc:  # pragma: no cover
    AESGCM = None  # type: ignore[assignment]
    _import_error = exc
else:
    _import_error = None

logger = logging.getLogger(__name__)


_MAGIC = b"GGFG"  # GaitGuard Face Gallery
_VERSION = 1
_NONCE_LEN = 12
_KEY_LEN = 32  # AES-256


@dataclass(frozen=True)
class CryptoConfig:
    env_var: str = "GAITGUARD_FACE_KEY"


_key_cache: Dict[str, bytes] = {}


def _ensure_backend_available() -> None:
    if AESGCM is None:
        raise RuntimeError(
            "cryptography AESGCM backend not available. "
            "Install the 'cryptography' package. "
            f"Original error: {_import_error}"
        )


def _decode_key(raw: str) -> bytes:
    s = raw.strip()

    # Hex (64 hex chars for 32 bytes)
    if all(c in "0123456789abcdefABCDEF" for c in s) and len(s) in (32, 48, 64):
        key = bytes.fromhex(s)
    else:
        # Try base64; if it fails, treat as UTF-8 bytes directly.
        try:
            key = base64.b64decode(s, validate=True)
        except Exception:
            key = s.encode("utf-8")

    if len(key) not in (16, 24, 32):
        raise ValueError(
            f"Invalid AES key length {len(key)} bytes; expected 16/24/32."
        )

    # We standardise on 32 bytes; shorter keys are padded via HKDF-style expansion
    # here we just rehash via SHA256 on the raw key if needed to keep logic simple.
    if len(key) != _KEY_LEN:
        import hashlib

        key = hashlib.sha256(key).digest()

    return key


def load_key_from_env(env_var: str = CryptoConfig.env_var) -> bytes:
    if env_var in _key_cache:
        return _key_cache[env_var]

    raw = os.getenv(env_var)
    if not raw:
        raise RuntimeError(
            f"Encryption key environment variable '{env_var}' is not set. "
            "Generate a strong random key and set it, e.g.:\n"
            "  export GAITGUARD_FACE_KEY=\"$(openssl rand -hex 32)\""
        )

    try:
        key = _decode_key(raw)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to decode AES key from env var '{env_var}': {exc}"
        ) from exc

    _key_cache[env_var] = key
    logger.info("Loaded AES key from env var '%s' (length=%d bytes).", env_var, len(key))
    return key


def encrypt_bytes(
    plaintext: bytes,
    *,
    key: Optional[bytes] = None,
    env_var: str = CryptoConfig.env_var,
    aad: Optional[bytes] = None,
) -> bytes:
    _ensure_backend_available()

    if not isinstance(plaintext, (bytes, bytearray)):
        raise TypeError("encrypt_bytes expects 'bytes' plaintext.")

    if key is None:
        key = load_key_from_env(env_var)

    if aad is None:
        aad = b""

    nonce = os.urandom(_NONCE_LEN)
    aesgcm = AESGCM(key)
    ct = aesgcm.encrypt(nonce, plaintext, aad)

    header = _MAGIC + bytes([_VERSION]) + nonce
    return header + ct


def decrypt_bytes(
    blob: bytes,
    *,
    key: Optional[bytes] = None,
    env_var: str = CryptoConfig.env_var,
    aad: Optional[bytes] = None,
) -> bytes:
    _ensure_backend_available()

    if not isinstance(blob, (bytes, bytearray)):
        raise TypeError("decrypt_bytes expects 'bytes' blob.")

    if len(blob) < len(_MAGIC) + 1 + _NONCE_LEN + 16:
        raise ValueError("Ciphertext too short to be valid.")

    if key is None:
        key = load_key_from_env(env_var)

    if aad is None:
        aad = b""

    if blob[: len(_MAGIC)] != _MAGIC:
        raise ValueError("Invalid ciphertext magic header.")

    version = blob[len(_MAGIC)]
    if version != _VERSION:
        raise ValueError(f"Unsupported ciphertext version {version}.")

    offset = len(_MAGIC) + 1
    nonce = blob[offset : offset + _NONCE_LEN]
    ct = blob[offset + _NONCE_LEN :]

    aesgcm = AESGCM(key)
    try:
        return aesgcm.decrypt(nonce, ct, aad)
    except Exception as exc:
        raise RuntimeError(f"Decryption failed or authentication tag invalid: {exc}") from exc


def encrypt_json(
    obj: Any,
    *,
    key: Optional[bytes] = None,
    env_var: str = CryptoConfig.env_var,
    aad: Optional[bytes] = None,
    ensure_ascii: bool = False,
) -> bytes:
    data = json.dumps(obj, ensure_ascii=ensure_ascii, separators=(",", ":")).encode("utf-8")
    return encrypt_bytes(data, key=key, env_var=env_var, aad=aad)


def decrypt_json(
    blob: bytes,
    *,
    key: Optional[bytes] = None,
    env_var: str = CryptoConfig.env_var,
    aad: Optional[bytes] = None,
) -> Any:
    data = decrypt_bytes(blob, key=key, env_var=env_var, aad=aad)
    return json.loads(data.decode("utf-8"))
