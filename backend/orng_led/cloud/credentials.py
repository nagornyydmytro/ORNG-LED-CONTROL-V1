"""Device token storage — never in runtime.yaml / never in status URLs.

Windows/macOS: prefer OS keyring; plaintext file only if keyring unavailable
and ORNG_CLOUD_ALLOW_FILE_TOKEN=1 (or tests). Raspberry/kiosk file storage
uses ORNG_CLOUD_DEVICE_MODE, not bare sys.platform.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from orng_led.cloud.device_mode import CloudDeviceMode, cloud_device_mode

_KEYRING_SERVICE = "orng-led-control"
_KEYRING_USER = "device-token"


class CredentialStorageError(RuntimeError):
    """Secure credential storage could not be created."""


def device_token_path() -> Path:
    override = os.environ.get("ORNG_CLOUD_TOKEN_FILE")
    if override:
        return Path(override)

    if cloud_device_mode() in {CloudDeviceMode.RASPBERRY, CloudDeviceMode.KIOSK}:
        return Path("/var/lib/orng-led-control/device.token")

    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "ORNG-LED-CONTROL" / "device.token"

    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "ORNG-LED-CONTROL"
            / "device.token"
        )

    # Linux desktop (not raspberry/kiosk mode)
    xdg = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return xdg / "orng-led-control" / "device.token"


def credential_status() -> dict[str, object]:
    """Status without revealing the token."""
    path = device_token_path()
    return {
        "has_token": bool(load_device_token()),
        "storage": _storage_kind(),
        "device_mode": cloud_device_mode().value,
        "token_path_configured": bool(os.environ.get("ORNG_CLOUD_TOKEN_FILE")),
        "token_file_exists": path.is_file(),
        "token_file_basename": path.name if path.is_file() else None,
    }


def _storage_kind() -> str:
    if _try_keyring_get():
        return "keyring"
    if device_token_path().is_file():
        return "file"
    return "none"


def _try_keyring_get() -> str | None:
    try:
        import keyring  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        return None
    try:
        value = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
    except Exception:  # noqa: BLE001
        return None
    return str(value) if value else None


def _try_keyring_set(token: str) -> bool:
    try:
        import keyring  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        return False
    try:
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER, token)
        got = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
        return bool(got) and str(got) == token
    except Exception:  # noqa: BLE001
        return False


def _try_keyring_delete() -> None:
    try:
        import keyring  # type: ignore[import-untyped]

        keyring.delete_password(_KEYRING_SERVICE, _KEYRING_USER)
    except Exception:  # noqa: BLE001
        pass


def _prefer_keyring() -> bool:
    if cloud_device_mode() != CloudDeviceMode.DESKTOP:
        return False
    return os.environ.get("ORNG_CLOUD_PREFER_KEYRING", "1").strip() != "0"


def _allow_file_fallback() -> bool:
    if os.environ.get("ORNG_CLOUD_ALLOW_FILE_TOKEN", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return True
    if cloud_device_mode() in {CloudDeviceMode.RASPBERRY, CloudDeviceMode.KIOSK}:
        return True
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return False


def load_device_token() -> str | None:
    from_keyring = _try_keyring_get()
    if from_keyring:
        return from_keyring.strip() or None

    path = device_token_path()
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _atomic_write_token_file(path: Path, token: str) -> None:
    """Write token atomically with mode 0600 from the start (no insecure window)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if cloud_device_mode() in {CloudDeviceMode.RASPBERRY, CloudDeviceMode.KIOSK}:
        try:
            os.chmod(path.parent, 0o700)
        except OSError as exc:
            raise CredentialStorageError(
                f"Cannot secure credential directory {path.parent}: {exc}"
            ) from exc

    tmp = path.with_name(path.name + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    # Create with 0600 immediately (Unix); Windows ignores mode bits.
    fd = os.open(str(tmp), flags, 0o600)
    try:
        os.write(fd, (token + "\n").encode("utf-8"))
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            pass
    finally:
        os.close(fd)
    try:
        os.chmod(tmp, 0o600)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise CredentialStorageError(f"Cannot chmod 0600 token file: {exc}") from exc

    os.replace(str(tmp), str(path))
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        try:
            path.unlink()
        except OSError:
            pass
        raise CredentialStorageError(
            f"Cannot secure token file after replace: {exc}"
        ) from exc


def save_device_token(token: str) -> Path | None:
    """Persist token. Returns file path if written to disk, else None (keyring-only)."""
    cleaned = str(token).strip()
    if not cleaned:
        raise ValueError("empty device token")

    if _prefer_keyring():
        if _try_keyring_set(cleaned):
            path = device_token_path()
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass
            return None

    if not _allow_file_fallback() and cloud_device_mode() == CloudDeviceMode.DESKTOP:
        raise CredentialStorageError(
            "Secure keyring unavailable; set ORNG_CLOUD_ALLOW_FILE_TOKEN=1 for file fallback "
            "or install the keyring package / OS credential store."
        )

    path = device_token_path()
    try:
        _atomic_write_token_file(path, cleaned)
    except CredentialStorageError:
        raise
    except OSError as exc:
        raise CredentialStorageError(f"Cannot write device token: {exc}") from exc
    return path


def clear_device_token() -> None:
    _try_keyring_delete()
    path = device_token_path()
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass
    tmp = path.with_name(path.name + ".tmp")
    if tmp.is_file():
        try:
            tmp.unlink()
        except OSError:
            pass
