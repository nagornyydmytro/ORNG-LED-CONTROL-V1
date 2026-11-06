"""Choose one capture input and keep it.

PortAudio's device index changes when something is plugged in. The saved id is
the host API plus the device name, never that index.

A name that contains "usb" is not a line input. Microphones, webcams, and
headsets stay excluded. The status text reports the device name the host gave
us; it does not claim the jack is a line input.
"""

from __future__ import annotations

from dataclasses import dataclass

# Specific interface names. "usb" alone is not in this list.
_PREFERRED = (
    "xlr",
    "scarlett",
    "focusrite",
    "behringer",
    "umc",
    "audient",
    "motu",
    "uac",
    "ag03",
    "ag06",
    "pxr",
    "line in",
    "line-in",
)
# These lose even when the name also says USB.
_REJECT = (
    "microphone array",
    "microphone",
    "микрофон",
    "мікрофон",
    "mic ",
    "webcam",
    "headset",
    "hands-free",
    "hands free",
    "realtek",
    "internal",
    "built-in",
    "builtin",
    "macbook",
    "hd audio",
    "primary sound",
    "первичн",
    "первинн",
    "sound mapper",
    "переназначен",
    "stereo mix",
    "стерео микшер",
    "стерео мікшер",
    "pc speaker",
)


@dataclass(frozen=True)
class InputDevice:
    id: str
    name: str
    inputs: int
    index: int = -1


def stable_identity(name: str, hostapi: str = "") -> str:
    """Identity that survives a PortAudio reindex."""
    return f"{hostapi.casefold().strip()}|{name.casefold().strip()}"


def _rejected(name: str) -> bool:
    text = f" {name.casefold()} "
    if "microphone" in text or "микрофон" in text or "мікрофон" in text:
        return True
    return any(hint in text for hint in _REJECT)


def _rank(name: str) -> int:
    """Higher is a better automatic choice. Rejected devices are not ranked."""
    if _rejected(name):
        return -1
    text = name.casefold()
    if any(hint in text for hint in _PREFERRED):
        return 2
    if "usb" in text:
        return 1
    return 0


def choose_input(
    devices: list[InputDevice],
    *,
    current_id: str | None = None,
) -> InputDevice | None:
    """Keep the current working input. Do not bounce between two that stay listed.

    A higher-ranked device (a named interface above a generic USB adapter) is
    taken once and then kept. Two devices of the same rank do not swap on the
    next poll. The PortAudio index is ignored.
    """
    usable = [device for device in devices if device.inputs > 0 and _rank(device.name) >= 0]
    if not usable:
        return None
    best = max(_rank(device.name) for device in usable)
    pool = [device for device in usable if _rank(device.name) == best]
    if current_id is not None:
        staying = [device for device in pool if device.id == current_id]
        if staying:
            return staying[0]
    return sorted(pool, key=lambda device: (device.name.casefold(), device.id))[0]
