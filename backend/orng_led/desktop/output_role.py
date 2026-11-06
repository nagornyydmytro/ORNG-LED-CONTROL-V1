"""Who sends Art-Net for this desk.

``raspberry`` (default on a desktop) means the location Raspberry is the only
sender. ``local`` is an explicit choice to use this computer's Art-Net.
A dropped cloud link must not flip the role.
"""

from __future__ import annotations

import os

from orng_led.cloud.device_mode import CloudDeviceMode, cloud_device_mode


def desktop_output_role() -> str:
    """``local``, ``raspberry``, or ``unset``.

    Unset keeps the historical desk behaviour so tests and a manual server
    are unchanged. The product launcher sets ``raspberry`` and does not flip
    it when the cloud link drops.
    """
    raw = os.environ.get("ORNG_DESKTOP_OUTPUT", "").strip().lower()
    if raw in {"local", "artnet", "this-computer"}:
        return "local"
    if raw in {"raspberry", "remote", "pi"}:
        return "raspberry"
    if cloud_device_mode() == CloudDeviceMode.RASPBERRY:
        return "local"
    return "unset"


def local_artnet_allowed() -> bool:
    return desktop_output_role() != "raspberry"
