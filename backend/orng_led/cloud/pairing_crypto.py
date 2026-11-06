"""Device-side ECDH P-256 credential envelope (matches Worker crypto.ts)."""

from __future__ import annotations

import base64
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class DeviceKeyPair:
    public_jwk: dict[str, Any]
    private_pem: bytes


def generate_client_challenge() -> str:
    return secrets.token_hex(16)


def generate_device_keypair() -> DeviceKeyPair:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
    )

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    numbers = public_key.public_numbers()
    public_jwk = {
        "kty": "EC",
        "crv": "P-256",
        "x": _int_to_b64url(numbers.x, 32),
        "y": _int_to_b64url(numbers.y, 32),
    }
    private_pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    return DeviceKeyPair(public_jwk=public_jwk, private_pem=private_pem)


def save_pairing_private_key(pairing_id: str, private_pem: bytes, config_root: Path) -> Path:
    path = Path(config_root) / ".pairing" / f"{pairing_id}.pem"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(private_pem)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def load_pairing_private_key(pairing_id: str, config_root: Path) -> bytes | None:
    path = Path(config_root) / ".pairing" / f"{pairing_id}.pem"
    if not path.is_file():
        return None
    return path.read_bytes()


def clear_pairing_private_key(pairing_id: str, config_root: Path) -> None:
    path = Path(config_root) / ".pairing" / f"{pairing_id}.pem"
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


def decrypt_credential_envelope(private_pem: bytes, envelope_b64: str) -> str:
    """Decrypt Worker ECDH-ES+A256GCM envelope → plaintext device token."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    raw = base64.b64decode(envelope_b64)
    envelope = json.loads(raw.decode("utf-8"))
    if envelope.get("v") != 1 or envelope.get("alg") != "ECDH-ES+A256GCM":
        raise ValueError("unsupported credential envelope")
    epk = envelope["epk"]
    private_key = load_pem_private_key(private_pem, password=None)
    if not isinstance(private_key, ec.EllipticCurvePrivateKey):
        raise ValueError("expected EC private key")

    peer_public = _jwk_to_ec_public(epk)
    shared = private_key.exchange(ec.ECDH(), peer_public)
    digest = hashes.Hash(hashes.SHA256())
    digest.update(shared)
    aes_key = digest.finalize()

    iv = base64.b64decode(envelope["iv"])
    ct = base64.b64decode(envelope["ct"])
    aesgcm = AESGCM(aes_key)
    plain = aesgcm.decrypt(iv, ct, None)
    return plain.decode("utf-8")


def _jwk_to_ec_public(jwk: dict[str, Any]) -> Any:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicNumbers

    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise ValueError("epk must be EC P-256")
    x = _b64url_to_int(str(jwk["x"]))
    y = _b64url_to_int(str(jwk["y"]))
    return EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()


def _int_to_b64url(value: int, length: int) -> str:
    raw = value.to_bytes(length, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_to_int(text: str) -> int:
    pad = "=" * (-len(text) % 4)
    raw = base64.urlsafe_b64decode(text + pad)
    return int.from_bytes(raw, "big")
