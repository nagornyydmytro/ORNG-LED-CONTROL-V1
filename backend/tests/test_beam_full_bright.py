"""Beams never use Master Dimmer — full bright when lit, strobe allowed."""

from __future__ import annotations

from orng_led.config import load_show_config
from orng_led.config.models import ChannelRole, FixtureKind
from orng_led.config.validation import global_channel
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.intents import BeamIntent, Rgbw, StageIntent
from orng_led.engine.renderer import render_stage
from orng_led.presets.program import YamlPresetProgram
from orng_led.presets.staff import build_staff_preset


def test_beam_profile_has_no_dimmer_role() -> None:
    show = load_show_config()
    profile = show.profiles["beam_13ch"]
    roles = {ch.role for ch in profile.channels}
    assert ChannelRole.DIMMER not in roles
    fixed = [ch for ch in profile.channels if ch.role is ChannelRole.FIXED]
    assert fixed and all(int(ch.fixed_value or 0) == 255 for ch in fixed)


def test_render_beam_ignores_master_and_intent_dimmer_curve() -> None:
    show = load_show_config()
    beam = next(fx for fx in show.patch.fixtures if fx.kind is FixtureKind.BEAM)
    beam = beam.model_copy(
        update={"spatial": beam.spatial.model_copy(update={"beam_calibration_confirmed": True})}
    )
    show.patch.fixtures = [beam if fx.id == beam.id else fx for fx in show.patch.fixtures]
    stage = StageIntent(
        fixtures={
            beam.id: BeamIntent(
                pan=0.5,
                tilt=0.5,
                dimmer=0.2,
                color=Rgbw(r=1, g=0, b=0),
                shutter_open=True,
            )
        }
    )
    frame = render_stage(
        show,
        stage,
        {beam.id: BeamMotionState(pan=0.5, tilt=0.5)},
        master=0.1,
    )
    profile = show.profile_for(beam)
    for ch in profile.channels:
        idx = global_channel(beam.start_address, ch.local) - 1
        if ch.role is ChannelRole.FIXED:
            assert frame[idx] == 255
        if ch.role is ChannelRole.RED:
            assert frame[idx] == 255  # full, not scaled by 0.2 * 0.1
        if ch.role is ChannelRole.GREEN:
            assert frame[idx] == 0


def test_staff_presets_set_beam_dimmer_full_when_present() -> None:
    show = load_show_config()
    for preset_id in ("P01", "P05", "P09", "P10"):
        program = YamlPresetProgram(build_staff_preset(preset_id))
        for t in (1.0, 30.0, 90.0, 150.0):
            stage = program.evaluate(t, show)
            for fixture in show.patch.fixtures:
                if fixture.kind is not FixtureKind.BEAM:
                    continue
                intent = stage.fixtures.get(fixture.id)
                if intent is None:
                    continue
                assert isinstance(intent, BeamIntent)
                assert intent.dimmer == 1.0


def test_pulse_never_drives_beam_fixture_strobe() -> None:
    """Episode pulse must not write fixture strobe_speed — preview matches hardware."""
    show = load_show_config()
    program = YamlPresetProgram(build_staff_preset("P05"))
    saw_beam = False
    for step in range(0, 18, 1):
        stage = program.evaluate(float(step), show)
        for fixture in show.patch.fixtures:
            if fixture.kind is not FixtureKind.BEAM:
                continue
            intent = stage.fixtures.get(fixture.id)
            if isinstance(intent, BeamIntent):
                saw_beam = True
                assert intent.strobe == 0.0
                assert intent.dimmer == 1.0
    assert saw_beam
