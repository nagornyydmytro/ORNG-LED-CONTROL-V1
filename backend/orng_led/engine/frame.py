"""512-channel DMX frame helpers."""

from __future__ import annotations

from orng_led.config.schema import DMX_UNIVERSE_SIZE


def empty_frame() -> list[int]:
    return [0] * DMX_UNIVERSE_SIZE


def clamp_dmx(value: float | int) -> int:
    if value <= 0:
        return 0
    if value >= 255:
        return 255
    return int(round(value))


def write_channel(frame: list[int], global_channel: int, value: float | int) -> None:
    """Write a 1-based DMX channel into a 0-based frame list."""
    if global_channel < 1 or global_channel > DMX_UNIVERSE_SIZE:
        raise ValueError(f"Channel {global_channel} outside 1..{DMX_UNIVERSE_SIZE}.")
    frame[global_channel - 1] = clamp_dmx(value)


def assert_frame_bounds(frame: list[int]) -> None:
    if len(frame) != DMX_UNIVERSE_SIZE:
        raise AssertionError(f"Frame length {len(frame)} != {DMX_UNIVERSE_SIZE}")
    for index, value in enumerate(frame):
        if not isinstance(value, int) or value < 0 or value > 255:
            raise AssertionError(f"Illegal frame[{index}]={value!r}")
