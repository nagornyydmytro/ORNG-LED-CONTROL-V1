"""Preview age is the device's own snapshot age. A repeat does not mint a new one."""

from __future__ import annotations

import time

from orng_led.api.runtime import AppRuntime
from orng_led.output.mock import MockTransport


def test_a_stopped_renderer_keeps_one_sequence_while_age_grows() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    assert isinstance(runtime.output.transport, MockTransport)
    runtime.engine.set_blackout(True)
    runtime.tick(dt_s=1.0 / 30.0)
    first = runtime.desk_preview()
    assert first is not None
    assert "stage" not in first
    assert "look" in first
    time.sleep(0.05)
    second = runtime.desk_preview()
    assert second is not None
    assert second["session"] == first["session"]
    assert second["sequence"] == first["sequence"]
    assert float(second["age_ms"]) >= float(first["age_ms"])
    runtime.tick(dt_s=1.0 / 30.0)
    third = runtime.desk_preview()
    assert third is not None
    assert third["session"] == first["session"]
    assert third["sequence"] == int(first["sequence"]) + 1
    assert float(third["age_ms"]) < 50
