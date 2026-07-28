"""DROP / COLOR HIT / SWEEP HIT: layer interaction, failsafes, Mock safety."""

from __future__ import annotations

from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.config import default_config_dir, load_show_config
from orng_led.engine.engine import Engine
from orng_led.engine.layers import DROP_MAX_DURATION_S
from orng_led.main import create_app
from orng_led.presets.store import PresetStore
from orng_led.simulator.decode import decode_simulator_view


def _engine(preset_id: str = "P05") -> Engine:
    show = load_show_config()
    programs = PresetStore.load(default_config_dir() / "presets").programs()
    engine = Engine(show=show, presets=programs, active_preset_id=preset_id)
    engine.set_blackout(False)
    engine.set_face(True)
    return engine


def _lit(engine: Engine, frame: list[int]) -> tuple[float, float]:
    view = decode_simulator_view(engine.show, frame)
    rear = sum(p.intensity for p in view.pars)
    rear += sum(b.dimmer for b in view.bars) + sum(b.dimmer for b in view.beams)
    face = sum(f.intensity for f in view.faces)
    return rear, face


def test_drop_kills_the_stage_and_keeps_the_show_clock() -> None:
    engine = _engine("P08")
    for _ in range(30):
        engine.tick(dt_s=1 / 30, wall_dt_s=1 / 30)
    before_clock = engine.preset_elapsed_s

    engine.drop_press()
    snap = engine.tick(dt_s=1 / 30, wall_dt_s=1 / 30)
    rear, face = _lit(engine, snap.frame)
    assert rear == 0.0
    assert face > 0.0  # DJ face light is not part of the stage look
    assert snap.drop_active is True
    assert engine.preset_elapsed_s > before_clock  # clock keeps running

    engine.drop_release()
    snap = engine.tick(dt_s=1 / 30, wall_dt_s=1 / 30)
    rear, _ = _lit(engine, snap.frame)
    assert rear > 0.0
    assert snap.drop_active is False


def test_drop_has_a_real_time_max_duration_failsafe() -> None:
    engine = _engine("P05")
    engine.drop_press()
    steps = int((DROP_MAX_DURATION_S + 0.3) * 30)
    for _ in range(steps):
        # 60× preview speed must not shorten or lengthen the wall-clock failsafe.
        engine.tick(dt_s=(1 / 30) * 60, wall_dt_s=1 / 30)
    assert engine.overlays.drop_held is False
    rear, _ = _lit(engine, engine.tick(dt_s=1 / 30, wall_dt_s=1 / 30).frame)
    assert rear > 0.0


def test_color_hit_is_a_contrasting_colour_and_returns() -> None:
    engine = _engine("P01")  # warm look
    engine.tick(dt_s=1.0, wall_dt_s=1.0)
    chosen = engine.trigger_color_hit()
    assert chosen.b > chosen.r  # complement of warm, never brand orange

    snap = engine.tick(dt_s=1 / 30, wall_dt_s=1 / 30)
    view = decode_simulator_view(engine.show, snap.frame)
    assert any(p.b > p.r for p in view.pars)
    assert snap.color_hit_active is True

    for _ in range(20):
        snap = engine.tick(dt_s=1 / 30, wall_dt_s=1 / 30)
    assert snap.color_hit_active is False


def test_explicit_color_hit_colour_is_respected() -> None:
    from orng_led.engine.intents import Rgbw

    engine = _engine("P05")
    engine.trigger_color_hit(Rgbw(r=0.0, g=1.0, b=0.0))
    snap = engine.tick(dt_s=1 / 30, wall_dt_s=1 / 30)
    view = decode_simulator_view(engine.show, snap.frame)
    assert all(p.g > p.r for p in view.pars if p.intensity > 0.1)


def test_sweep_hit_travels_left_to_right_over_the_layout() -> None:
    """Compare against an identical engine without the sweep, so only the
    positional delta of the effect is measured."""
    swept = _engine("P03")
    plain = _engine("P03")
    swept.trigger_sweep_hit()
    placements = {p.fixture_id: p for p in swept.show.layout.placements}
    centroids: list[float] = []

    for _ in range(24):
        snap = swept.tick(dt_s=1 / 30, wall_dt_s=1 / 30)
        base = plain.tick(dt_s=1 / 30, wall_dt_s=1 / 30)
        if not snap.sweep_active:
            continue
        view = decode_simulator_view(swept.show, snap.frame)
        reference = {p.id: p.intensity for p in decode_simulator_view(plain.show, base.frame).pars}
        weights = [
            (placements[p.id].x, max(0.0, p.intensity - reference.get(p.id, 0.0)))
            for p in view.pars
            if p.id in placements
        ]
        total = sum(w for _x, w in weights)
        if total > 0.1:
            centroids.append(sum(x * w for x, w in weights) / total)

    assert len(centroids) >= 3
    assert centroids[-1] > centroids[0]
    assert min(centroids) < 0.45 and max(centroids) > 0.55


def test_blackout_outranks_every_quick_effect() -> None:
    engine = _engine("P10")
    engine.drop_release()
    engine.trigger_color_hit()
    engine.trigger_sweep_hit()
    engine.strobe_press()
    engine.set_blackout(True)
    snap = engine.tick(dt_s=1 / 30, wall_dt_s=1 / 30)
    assert snap.frame == [0] * 512


def test_failsafes_release_every_momentary_control() -> None:
    engine = _engine("P07")
    engine.strobe_press()
    engine.drop_press()
    engine.on_visibility_hidden()
    assert engine.overlays.strobe_held is False
    assert engine.overlays.drop_held is False

    engine.strobe_press()
    engine.drop_press()
    engine.on_control_disconnect()
    assert engine.overlays.strobe_held is False
    assert engine.overlays.drop_held is False


def test_quick_effect_endpoints_stay_on_mock_and_are_idempotent() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    application = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(application) as client:
        client.post("/api/commands/blackout", json={"enabled": False})

        first = client.post(
            "/api/commands/drop",
            json={"action": "press", "client_command_id": "drop-1"},
        )
        assert first.status_code == 200
        assert first.json()["state"]["engine"]["drop_active"] is True
        replay = client.post(
            "/api/commands/drop",
            json={"action": "press", "client_command_id": "drop-1"},
        )
        assert replay.json()["idempotent_replay"] is True
        client.post("/api/commands/drop", json={"action": "release"})

        color = client.post("/api/commands/color-hit", json={"r": 0.0, "g": 0.2, "b": 1.0})
        assert color.status_code == 200
        assert color.json()["state"]["engine"]["color_hit_active"] is True

        sweep = client.post("/api/commands/sweep-hit", json={})
        assert sweep.status_code == 200
        assert sweep.json()["state"]["engine"]["sweep_active"] is True

        state = client.get("/api/state").json()
        assert state["output"]["transport"] == "mock"
        assert state["output"]["armed"] is False
        assert state["output"]["network_allowed"] is False


def test_preview_clip_is_off_transport_and_uses_the_real_renderer() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    application = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(application) as client:
        before = client.get("/api/state").json()
        response = client.get("/api/presets/P06/preview-clip?seconds=2&fps=10")
        assert response.status_code == 200
        clip = response.json()
        assert clip["safe_mock_preview"] is True
        assert clip["transport"] == "none"
        assert len(clip["frames"]) == 20
        assert any(frame["nonzero_channels"] > 0 for frame in clip["frames"])

        after = client.get("/api/state").json()
        # Live output untouched: still blackout, still zero frame, still Mock.
        assert after["engine"]["blackout"] == before["engine"]["blackout"]
        assert after["frame"] == [0] * 512
        assert after["output"]["frames_sent"] >= before["output"]["frames_sent"]
        assert after["output"]["transport"] == "mock"


def test_preview_clip_can_start_at_a_chosen_episode() -> None:
    """The presets page previews one episode at a time, still off-transport."""
    runtime = AppRuntime.create(autostart_loop=False)
    application = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(application) as client:
        head = client.get("/api/presets/P06/preview-clip?seconds=1&fps=10").json()
        later = client.get("/api/presets/P06/preview-clip?seconds=1&fps=10&start_s=54").json()
        assert later["start_s"] == 54.0
        assert later["transport"] == "none"
        # Episode 4 must not render identically to episode 1.
        assert later["frames"][0] != head["frames"][0]
        # Deterministic: the same offset renders the same frames.
        again = client.get("/api/presets/P06/preview-clip?seconds=1&fps=10&start_s=54").json()
        assert again["frames"][0] == later["frames"][0]
        assert client.get("/api/state").json()["frame"] == [0] * 512


def test_stage_layout_endpoint_exposes_the_reference_plan() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    application = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(application) as client:
        layout = client.get("/api/stage/layout").json()
        assert layout["hardware_verified"] is False
        assert len(layout["placements"]) == 12
        assert layout["cable_chain"][0] == "par_1"
        assert layout["cable_chain"][-1] == "face_par_1"
        assert layout["artnet_node"]["fixture_id"] == "artnet_node"
        by_id = {p["fixture_id"]: p for p in layout["placements"]}
        assert by_id["bar_2"]["orientation"] == "vertical"
        assert by_id["beam_left"]["x"] < by_id["beam_right"]["x"]
