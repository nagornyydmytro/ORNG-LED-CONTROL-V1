"""Ed25519 signing / verification for orng-app-release-v1 manifests."""

from __future__ import annotations

import base64
import json
import re
from typing import Any

from orng_led.updater.trusted_keys import get_trusted_app_key

APP_MANIFEST_SCHEMA = 1
UPDATER_PROTOCOL_VERSION = 1
FORMAT = "orng-app-release-v1"

_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)
_SAFE_BUILD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_PLATFORM = frozenset(
    {"desktop_windows", "desktop_macos", "raspberry", "linux"}
)
_SAFE_ARCH = frozenset({"amd64", "arm64", "armv7"})
_SAFE_CHANNEL = frozenset({"stable", "beta", "dev", "test", "hardware-candidate"})


class AppSignError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def stable_stringify(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return stable_stringify(manifest).encode("utf-8")


def validate_identity_fields(
    *,
    version: str,
    build_id: str,
    platform: str,
    arch: str,
    channel: str,
) -> None:
    if not _SEMVER_RE.match(version):
        raise AppSignError("bad_version", f"Invalid SemVer: {version}")
    if not _SAFE_BUILD_RE.match(build_id):
        raise AppSignError("bad_build_id", f"Unsafe build_id: {build_id}")
    if platform not in _SAFE_PLATFORM:
        raise AppSignError("bad_platform", f"Unsupported platform: {platform}")
    if arch not in _SAFE_ARCH:
        raise AppSignError("bad_arch", f"Unsupported arch: {arch}")
    if channel not in _SAFE_CHANNEL:
        raise AppSignError("bad_channel", f"Unsupported channel: {channel}")


def build_signing_manifest(
    *,
    version: str,
    build_id: str,
    platform: str,
    arch: str,
    channel: str,
    content_sha256: str,
    package_size: int,
    files: dict[str, str],
    notes: str | None = None,
    min_updater_version: str | None = None,
    updater_protocol_version: int = UPDATER_PROTOCOL_VERSION,
    entrypoint: str = "orng_led.main:app",
    launcher: str = "bootstrap/run_release.py",
    health_endpoint: str = "/api/health",
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_identity_fields(
        version=version,
        build_id=build_id,
        platform=platform,
        arch=arch,
        channel=channel,
    )
    if not files:
        raise AppSignError("empty_files", "Release must contain at least one file")
    # Deterministic file map order in canonical JSON via sort_keys.
    return {
        "schema": APP_MANIFEST_SCHEMA,
        "format": FORMAT,
        "version": version,
        "build_id": build_id,
        "platform": platform,
        "arch": arch,
        "channel": channel,
        "content_sha256": content_sha256,
        "package_size": package_size,
        "files": dict(sorted(files.items())),
        "entrypoint": entrypoint,
        "launcher": launcher,
        "health_endpoint": health_endpoint,
        "runtime": runtime
        or {
            "python": "3.11+",
            "requires_pypi": False,
            "requires_nodejs": False,
            "wheelhouse": "wheelhouse",
            "venv": ".venv",
        },
        "min_updater_version": min_updater_version,
        "updater_protocol_version": updater_protocol_version,
        "notes": notes,
    }


def verify_app_signature(
    *,
    manifest: dict[str, Any],
    signature_b64: str,
    key_id: str,
) -> None:
    trusted = get_trusted_app_key(key_id)
    if trusted is None:
        raise AppSignError("unknown_key", f"Untrusted or inactive key_id: {key_id}")
    if not signature_b64 or not isinstance(signature_b64, str):
        raise AppSignError("unsigned", "App release signature required")

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        pub_raw = base64.b64decode(trusted.public_key_raw_b64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise AppSignError("invalid_key", "Public key is not valid base64") from exc
    if len(pub_raw) != 32:
        raise AppSignError("invalid_key", "Ed25519 public key must be 32 bytes")

    try:
        sig = base64.b64decode(signature_b64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise AppSignError("bad_signature", "Signature is not valid base64") from exc
    if len(sig) != 64:
        raise AppSignError("bad_signature", "Ed25519 signature must be 64 bytes")

    public = Ed25519PublicKey.from_public_bytes(pub_raw)
    data = canonical_manifest_bytes(manifest)
    try:
        public.verify(sig, data)
    except InvalidSignature as exc:
        raise AppSignError("bad_signature", "Ed25519 signature verification failed") from exc


def sign_app_manifest_with_private_key(
    manifest: dict[str, Any],
    private_key_pem_or_pkcs8: bytes,
) -> str:
    """Sign for release builder / tests. Private key never shipped in app package.

    Accepts PEM, PKCS8 DER, or raw 32-byte Ed25519 seed (``.private.bin``).
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        load_der_private_key,
        load_pem_private_key,
    )

    raw = private_key_pem_or_pkcs8.strip()
    private: Ed25519PrivateKey
    if raw.startswith(b"-----"):
        loaded = load_pem_private_key(raw, password=None)
        if not isinstance(loaded, Ed25519PrivateKey):
            raise AppSignError("invalid_key", "Expected Ed25519 private key")
        private = loaded
    elif len(raw) == 32:
        private = Ed25519PrivateKey.from_private_bytes(raw)
    else:
        loaded = load_der_private_key(raw, password=None)
        if not isinstance(loaded, Ed25519PrivateKey):
            raise AppSignError("invalid_key", "Expected Ed25519 private key")
        private = loaded
    sig = private.sign(canonical_manifest_bytes(manifest))
    return base64.b64encode(sig).decode("ascii")
