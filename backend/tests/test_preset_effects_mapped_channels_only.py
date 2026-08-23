"""Preset episode effects/transitions may only drive mapped show channels."""

from __future__ import annotations

import pytest

from orng_led.config import load_show_config
from orng_led.config.models import ChannelRole, FixtureKind
from orng_led.config.validation import global_channel
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.renderer import render_stage
from orng_led.engine.show_whitelist import (
    assert_only_mapped_show_channels,
    forbidden_nonzero_channels,
)
from orng_led.presets.models import EFFECTS, TRANSITIONS, EpisodeCard, PresetDocument
from orng_led.presets.program import YamlPresetProgram


def _sample_times() -> tuple[float, ...]:
    # Inside episode + soft/fade blend into the next card.
    return (0.0, 0.5, 3.0, 9.0, 17.0, 17.7, 18.1, 18.5)


@pytest.mark.parametrize("effect", EFFECTS)
@pytest.mark.parametrize("transition", TRANSITIONS)
def test_every_effect_transition_only_uses_mapped_channels(effect: str, transition: str) -> None:
    show = load_show_config()
    for fixture in show.patch.fixtures:
        if fixture.kind is FixtureKind.BEAM:
            fixture.spatial.beam_calibration_confirmed = True

    first = EpisodeCard(
        id="e1",
        duration_s=18,
        groups=["all_rear"],
        palette="deep_red",
        intensity=1.0,
        speed=0.85,
        effect=effect,
        transition=transition,
    )
    second = EpisodeCard(
        id="e2",
        duration_s=18,
        groups=["all_rear"],
        palette="cool_blue",
        intensity=1.0,
        speed=0.6,
        effect="static",
        transition="cut",
    )
    program = YamlPresetProgram(
        PresetDocument(id="MAPTEST", label="map-test", builtin=False, episodes=[first, second])
    )

    for time_s in _sample_times():
        stage = program.evaluate(time_s, show)
        motion = {
            fx.id: BeamMotionState.from_home(fx.spatial.home_pan, fx.spatial.home_tilt)
            for fx in show.patch.fixtures
            if fx.kind is FixtureKind.BEAM
        }
        # Scrub on (show path) and scrub off (renderer itself must not write junk).
        for scrub in (True, False):
            frame = render_stage(show, stage, motion, master=1.0, scrub=scrub)
            assert_only_mapped_show_channels(show, frame)
            assert forbidden_nonzero_channels(show, frame) == []
            for fixture in show.patch.fixtures:
                profile = show.profile_for(fixture)
                for channel in profile.channels:
                    if channel.role is not ChannelRole.UNUSED:
                        continue
                    index = global_channel(fixture.start_address, channel.local) - 1
                    assert frame[index] == 0, (
                        f"{effect}/{transition} wrote unused "
                        f"{fixture.id} ch{channel.local} at t={time_s} scrub={scrub}"
                    )
