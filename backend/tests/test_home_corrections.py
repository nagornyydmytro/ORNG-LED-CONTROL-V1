"""HOME acceptance corrections: safety, blackout, groups, transitions, timers."""

from __future__ import annotations

from orng_led.api.runtime import AppRuntime
from orng_led.config import load_show_config
from orng_led.config.schema import DMX_UNIVERSE_SIZE
from orng_led.engine.layers import STROBE_HOLD_TIMEOUT_S
from orng_led.presets.models import EpisodeCard, PresetDocument
from orng_led.presets.program import YamlPresetProgram, _matches_groups


def test_startup_is_mock_disarmed_blackout_zero() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    assert runtime.output.transport_kind.value == "mock"
    assert runtime.output.armed is False
    assert runtime.output.allow_real_network is False
    assert runtime.engine.overlays.blackout is True
    state = runtime.build_state()
    assert state.engine.blackout is True
    assert state.frame == [0] * DMX_UNIVERSE_SIZE
    assert state.output.transport == "mock"
    assert state.output.armed is False


def test_blackout_overrides_raw_tester() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    runtime.engine.set_blackout(False)
    runtime.enter_raw_tester()
    runtime.raw_tester_set(channel=1, value=255)
    assert runtime.build_state().frame[0] == 255
    runtime.apply_blackout(True)
    state = runtime.build_state()
    assert state.engine.blackout is True
    assert state.frame == [0] * DMX_UNIVERSE_SIZE
    assert runtime.raw_tester.active is True
    assert runtime.raw_tester.frame[0] == 255


def test_identify_fixture_is_mock_visible_and_blackout_wins() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    runtime.engine.set_blackout(False)
    runtime.identify_fixture("par_1", level=200)
    state = runtime.build_state()
    assert state.raw_tester["active"] is True
    assert state.simulator.nonzero_channels > 1
    par = next(p for p in state.simulator.pars if p.id == "par_1")
    assert par.intensity > 0.5
    assert par.r > 0.5

    runtime.identify_fixture("bar_1", level=180)
    bar = next(b for b in runtime.build_state().simulator.bars if b.id == "bar_1")
    assert bar.dimmer > 0.5
    assert sum(bar.segments) > 0

    runtime.identify_fixture("beam_left", level=200)
    beam = next(b for b in runtime.build_state().simulator.beams if b.id == "beam_left")
    assert beam.dimmer > 0.5
    assert beam.shutter_open is True

    runtime.apply_blackout(True)
    assert runtime.build_state().frame == [0] * DMX_UNIVERSE_SIZE


def test_identify_group_outer_only() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    runtime.engine.set_blackout(False)
    runtime.identify_group("outer", level=180)
    state = runtime.build_state()
    outer_ids = {fx.id for fx in runtime.show.patch.fixtures if "outer" in fx.groups}
    lit = {p.id for p in state.simulator.pars if p.intensity > 0.1}
    lit |= {b.id for b in state.simulator.bars if b.dimmer > 0.1}
    assert lit <= outer_ids
    assert "par_1" in lit or "bar_1" in lit


def test_outer_par_group_excludes_inner_par() -> None:
    assert _matches_groups(["outer", "par", "left"], ["outer", "par"]) is True
    assert _matches_groups(["inner", "par", "left"], ["outer", "par"]) is False
    assert _matches_groups(["outer", "bar", "left"], ["outer", "par"]) is False
    assert _matches_groups(["par", "left"], ["par", "beam"]) is True
    assert _matches_groups(["beam", "left"], ["par", "beam"]) is True
    assert _matches_groups(["bar", "left"], ["par", "beam"]) is False
    assert _matches_groups(["left", "par"], ["left", "right"]) is True


def test_episode_transitions_cut_fade_soft() -> None:
    show = load_show_config()
    doc = PresetDocument(
        id="T01",
        label="Transitions",
        builtin=False,
        hardware_tuned=False,
        episodes=[
            EpisodeCard(
                id="ep1",
                duration_s=10.0,
                groups=["par"],
                palette="deep_red",
                effect="static",
                speed=0.0,
                intensity=1.0,
                transition="cut",
            ),
            EpisodeCard(
                id="ep2",
                duration_s=10.0,
                groups=["par"],
                palette="cool_blue",
                effect="static",
                speed=0.0,
                intensity=1.0,
                transition="fade",
            ),
            EpisodeCard(
                id="ep3",
                duration_s=10.0,
                groups=["par"],
                palette="mint",
                effect="static",
                speed=0.0,
                intensity=1.0,
                transition="soft",
            ),
        ],
    )
    program = YamlPresetProgram(document=doc)

    # Mid-episode cut: stable look, no accidental 255 jump from blend.
    cut_a = program.evaluate(1.0, show)
    cut_b = program.evaluate(2.0, show)
    assert cut_a.fixtures["par_1"].intensity == cut_b.fixtures["par_1"].intensity

    # Fade into ep2: early frame blends red→blue; late frame is fully blue.
    early = program.evaluate(10.2, show)
    late = program.evaluate(12.5, show)
    assert early.fixtures["par_1"].color.r > late.fixtures["par_1"].color.r
    assert early.fixtures["par_1"].color.b < late.fixtures["par_1"].color.b

    # Soft into ep3 after fade window.
    soft_early = program.evaluate(20.3, show)
    soft_late = program.evaluate(23.5, show)
    assert soft_early.fixtures["par_1"].color.g < soft_late.fixtures["par_1"].color.g

    # Loop boundary last→first uses ep1 transition (cut): no blend residue.
    loop = program.evaluate(0.05, show)
    assert "par_1" in loop.fixtures


def test_preview_speed_does_not_accelerate_strobe_timeout() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    runtime.engine.set_blackout(False)
    runtime.apply_preview_speed(120.0)
    runtime.engine.strobe_press()
    # 7 wall seconds at ×120 must NOT trip the 8s strobe failsafe.
    for _ in range(7):
        runtime.tick(dt_s=1.0)
    assert runtime.engine.overlays.strobe_held is True
    # One more wall second reaches the timeout.
    runtime.tick(dt_s=1.0)
    assert runtime.engine.overlays.strobe_held is False
    assert STROBE_HOLD_TIMEOUT_S == 8.0


def test_preview_speed_sixty_advances_show_not_wall() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    runtime.engine.set_blackout(False)
    runtime.apply_preview_speed(60.0)
    runtime.engine.select_preset("P05", reset_clock=True)
    runtime.tick(dt_s=1.0)
    assert abs(runtime.engine.preset_elapsed_s - 60.0) < 1e-9
    assert abs(runtime.engine.clock.time() - 1.0) < 1e-9
