"""Explicit cloud device mode — never infer Raspberry from sys.platform alone."""

from __future__ import annotations

import os
from enum import StrEnum


class CloudDeviceMode(StrEnum):
    DESKTOP = "desktop"
    RASPBERRY = "raspberry"
    KIOSK = "kiosk"


def cloud_device_mode() -> CloudDeviceMode:
    """Resolve mode from ORNG_CLOUD_DEVICE_MODE (desktop|raspberry|kiosk).

    Default: desktop. Raspberry/kiosk may auto-apply assigned releases.
    """
    raw = os.environ.get("ORNG_CLOUD_DEVICE_MODE", "desktop").strip().lower()
    if raw in {"raspberry", "pi", "rpi"}:
        return CloudDeviceMode.RASPBERRY
    if raw in {"kiosk"}:
        return CloudDeviceMode.KIOSK
    if raw in {"desktop", "windows", "macos", "mac", "win"}:
        return CloudDeviceMode.DESKTOP
    # Explicit auto-apply flag still opts into kiosk-like behaviour without
    # mistaking every Linux host for a venue Raspberry.
    if os.environ.get("ORNG_CLOUD_AUTO_APPLY", "").strip().lower() in {"1", "true", "yes"}:
        return CloudDeviceMode.KIOSK
    return CloudDeviceMode.DESKTOP


def should_auto_apply_assigned() -> bool:
    mode = cloud_device_mode()
    return mode in {CloudDeviceMode.RASPBERRY, CloudDeviceMode.KIOSK}
