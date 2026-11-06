"""Isolated desk: button command → state → changed frame. Mock only."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.config.io import load_show_config
from orng_led.engine.clock import FRAME_DT
from orng_led.main import create_app
from orng_led.output.mock import MockTransport
from orng_led.simulator.decode import decode_simulator_view


def _client(tmp_path: Path):
    runtime = AppRuntime.create(
        autostart_loop=False, config_dir=tmp_path, show=load_show_config()
    )
    assert isinstance(runtime.output.transport, MockTransport)
    runtime.engine.set_blackout(False)
    runtime.engine.set_master_brightness(1.0)
    app = create_app(runtime=runtime, autostart_loop=False)
    return runtime, TestClient(app)


def _command(client: TestClient, runtime: AppRuntime, patch: dict) -> dict:
    body = {
        "enabled": True,
        "scope": "auto",
        "mode": "smooth",
        "palette": runtime.bpm.palette,
        "cast": runtime.bpm.cast,
        "character": runtime.bpm.character,
        "intensity": runtime.bpm.intensity,
        "period": runtime.bpm.period if runtime.bpm.period is not None else "auto",
        "source_id": "web:desk",
        "client_command_id": f"desk-{patch}",
        **patch,
    }
    response = client.post("/api/commands/bpm-sync", json=body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload.get("idempotent_replay") is not True
    return payload


def _frame(runtime: AppRuntime) -> tuple[list[int], object, dict]:
    snap = runtime.tick(FRAME_DT)
    view = decode_simulator_view(runtime.show, snap.frame)
    state = runtime.bpm.status()
    return list(snap.frame), view, state


def test_desk_auto_buttons_change_state_and_frame_without_lock(tmp_path: Path) -> None:
    runtime, raw = _client(tmp_path)
    with raw as client:
        start = _command(client, runtime, {"palette": "orange", "cast": "all"})
        assert start["state"]["bpm"]["enabled"] is True
        assert start["state"]["bpm"]["scope"] == "auto"
        follower = runtime.bpm.follower
        follower.bpm = 120.0
        follower._started = True
        follower._beats = 1.0
        follower._at = runtime.bpm.clock_s
        follower.locked = False
        follower._release_until = None
        assert runtime.bpm.follower.locked is False

        before, before_view, before_status = _frame(runtime)
        assert before_status["locked"] is False
        assert any(value > 0 for value in before)

        character = _command(client, runtime, {"character": "smooth", "period": "auto"})
        assert character["state"]["bpm"]["character"] == "smooth"
        after_character, _, status = _frame(runtime)
        assert status["character"] == "smooth"
        assert status["locked"] is False
        assert after_character != before
        current = runtime.bpm.planner.current
        assert current is not None
        assert current.picture_cycle == pytest.approx(8.0)

        period = _command(client, runtime, {"period": 2})
        assert period["state"]["bpm"]["period"] in {2, 2.0, "2"}
        after_period, _, status = _frame(runtime)
        assert status["locked"] is False
        assert after_period != after_character
        assert runtime.bpm.planner.current is not None
        assert runtime.bpm.planner.current.picture_cycle == pytest.approx(2.0)

        intensity = _command(client, runtime, {"intensity": "soft"})
        assert intensity["state"]["bpm"]["intensity"] == "soft"
        after_intensity, view_soft, status = _frame(runtime)
        assert status["intensity"] == "soft"
        assert after_intensity != after_period
        pars = [item.intensity for item in view_soft.pars if not item.face]
        if pars:
            assert max(pars) <= 0.5 + 1e-6

        bars = _command(client, runtime, {"cast": "bars"})
        assert bars["state"]["bpm"]["cast"] == "bars"
        after_bars, view_bars, status = _frame(runtime)
        assert status["cast"] == "bars"
        assert after_bars != after_intensity
        assert all(item.intensity <= 0.02 for item in view_bars.pars if not item.face)
        assert any(item.dimmer > 0.02 for item in view_bars.bars)

        palette = _command(client, runtime, {"palette": "blue", "cast": "all"})
        assert palette["state"]["bpm"]["palette"] == "blue"
        after_palette, view_blue, status = _frame(runtime)
        assert status["palette"] == "blue"
        assert after_palette != after_bars
        blues = [item for item in view_blue.pars if not item.face and item.intensity > 0.02]
        if blues:
            assert blues[0].b >= blues[0].r

        selected = client.get("/api/state").json()["bpm"]
        assert selected["character"] == "smooth"
        assert selected["intensity"] == "soft"
        assert selected["cast"] == "all"
        assert selected["palette"] == "blue"
        assert selected["locked"] is False
        assert after_palette != before
