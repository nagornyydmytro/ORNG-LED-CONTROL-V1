"""Device-side safe credential rotation (matches Worker /device/rotation*).

Flow:
  1. GET /device/rotation (old Bearer)
  2. if pending → ECDH keypair → POST /device/rotation/prepare
  3. decrypt envelope
  4. atomic save_device_token(new)
  5. POST /device/rotation/ack with old Bearer + new token
  6. caller reconnects with new credential

Never logs tokens. Crash after save before ACK: retry prepare/ack safely.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from orng_led.cloud.credentials import device_token_path, load_device_token, save_device_token
from orng_led.cloud.pairing_crypto import (
    decrypt_credential_envelope,
    generate_device_keypair,
)
from orng_led.cloud.sync_service import SceneSyncError, cloud_http_base

logger = logging.getLogger(__name__)


class RotationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _rotation_key_path() -> Path:
    override = os.environ.get("ORNG_CLOUD_ROTATION_KEY_FILE")
    if override:
        return Path(override)
    return device_token_path().with_name("rotation-pending.pem")


def _save_rotation_private(pem: bytes) -> None:
    path = _rotation_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, pem)
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            pass
    finally:
        os.close(fd)


def _load_rotation_private() -> bytes | None:
    path = _rotation_key_path()
    if not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _clear_rotation_private() -> None:
    path = _rotation_key_path()
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


async def complete_pending_rotation(*, http_client: Any | None = None) -> bool:
    """Finish pending rotation. Returns True if credential changed (reconnect)."""
    old_token = load_device_token()
    if not old_token:
        return False

    import httpx

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        try:
            http_base = cloud_http_base()
        except SceneSyncError as exc:
            raise RotationError(exc.code, exc.message) from exc

        headers = {"authorization": f"Bearer {old_token}"}
        status_res = await client.get(f"{http_base}/device/rotation", headers=headers)
        if status_res.status_code >= 400:
            raise RotationError("rotation_status_failed", f"HTTP {status_res.status_code}")
        body = status_res.json() or {}
        if not body.get("rotation_pending"):
            _clear_rotation_private()
            return False

        keypair = generate_device_keypair()
        _save_rotation_private(keypair.private_pem)
        prep = await client.post(
            f"{http_base}/device/rotation/prepare",
            headers={**headers, "content-type": "application/json"},
            json={"public_key_jwk": keypair.public_jwk},
        )
        if prep.status_code >= 400:
            raise RotationError("rotation_prepare_failed", f"HTTP {prep.status_code}")
        prep_body = prep.json() or {}
        envelope_b64 = str(prep_body.get("credential_envelope_b64") or "")
        if not envelope_b64:
            # Some Workers return envelope on GET after prepare — try status again.
            envelope_b64 = str(body.get("credential_envelope_b64") or "")
        if not envelope_b64:
            raise RotationError("rotation_no_envelope", "prepare returned no envelope")

        try:
            new_token = decrypt_credential_envelope(keypair.private_pem, envelope_b64).strip()
        except Exception as exc:  # noqa: BLE001
            raise RotationError("rotation_decrypt_failed", "envelope decrypt failed") from exc

        if not new_token or new_token == old_token:
            raise RotationError("rotation_bad_token", "decrypted credential invalid")

        save_device_token(new_token)

        ack = await client.post(
            f"{http_base}/device/rotation/ack",
            headers={**headers, "content-type": "application/json"},
            json={"token": new_token},
        )
        if ack.status_code >= 400:
            # Credential already saved — next reconnect uses new token; ACK may be
            # retried if server still accepts old during overlap window.
            logger.warning("rotation ack HTTP %s (new credential already saved)", ack.status_code)
            raise RotationError("rotation_ack_failed", f"HTTP {ack.status_code}")

        _clear_rotation_private()
        logger.info("device credential rotation completed")
        return True
    finally:
        if owns_client:
            await client.aclose()
