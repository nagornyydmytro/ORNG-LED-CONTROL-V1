"""Accents follow the fresh analysis path and do not invent light."""

from __future__ import annotations

from orng_led.audio.auto_show import SliceHearing, consume_accent
from orng_led.audio.service import BpmService
from orng_led.config.io import load_show_config
from orng_led.config.models import FixtureKind
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.renderer import render_stage
from orng_led.simulator.decode import decode_simulator_view


def _service(tmp_path) -> BpmService:
    service = BpmService(tmp_path, "orng-bar")
    service.configure(enabled=True, scope="auto", accent=1.0, palette="blue", cast="heads")
    return service


def _hearing(**updates) -> SliceHearing:
    fields = {"rms": 0.1, "onset": 0.9}
    fields.update(updates)
    return SliceHearing(**fields)


def test_fresh_analysis_arms_the_accent_the_backlog_used_to_own(tmp_path) -> None:
    service = _service(tmp_path)
    service._accept_analysis(
        None, "few_onsets", {}, [0.0], dt_s=0.2, now=5.0, latency_s=0.0,
        hearing=_hearing(), energy=0.1,
    )
    assert service.section.accent_ready == 5.0


def test_carried_attack_keeps_its_time_and_does_not_repeat(tmp_path) -> None:
    service = _service(tmp_path)
    service._now = 5.05
    hearing = _hearing(origin_s=5.0)
    service._apply_pending_hearings((hearing,), dt_s=0.2)
    assert service.section.accent_ready == 5.0
    service._apply_pending_hearings((hearing,), dt_s=0.2)
    assert service.section.accent_ready == 5.0
    service.section.accent_ready = None
    service._now = 6.0
    service._apply_pending_hearings((_hearing(origin_s=5.0),), dt_s=0.2)
    assert service.section.accent_ready is None
    service._apply_pending_hearings((_hearing(),), dt_s=0.2)
    assert service.section.accent_ready is None


def test_old_generation_and_disabled_accent_do_not_arm(tmp_path) -> None:
    service = _service(tmp_path)
    service._generation = 4
    service._now = 3.0
    service._apply_pending_hearings((_hearing(origin_s=3.0, generation=3),), dt_s=0.1)
    assert service.section.accent_ready is None
    service.configure(accent=0.0)
    service._accept_analysis(
        None, "few_onsets", {}, [0.0], dt_s=0.2, now=3.0, latency_s=0.0,
        hearing=_hearing(), energy=0.1,
    )
    assert service.section.accent_ready is None
    service._retire_session("device")
    service.configure(accent=1.0)
    service._now = 4.0
    service._apply_pending_hearings((_hearing(origin_s=4.0, generation=4),), dt_s=0.1)
    assert service.section.accent_ready is None


def test_zero_accent_lights_nobody_and_live_path_keeps_the_palette(tmp_path) -> None:
    show = load_show_config()
    service = _service(tmp_path)
    fixtures = list(show.patch.fixtures)
    service._accept_analysis(
        None, "few_onsets", {}, [0.0], dt_s=0.2, now=2.0, latency_s=0.0,
        hearing=_hearing(), energy=0.1,
    )
    stage = service.look(fixtures, show.layout.placements)
    motion = {
        fixture.id: BeamMotionState.from_home(fixture.spatial.home_pan, fixture.spatial.home_tilt)
        for fixture in fixtures
        if fixture.kind is FixtureKind.BEAM
    }
    view = decode_simulator_view(show, render_stage(show, stage, motion))
    assert all(item.intensity <= 0.02 for item in view.faces)
    lit = [item for item in view.beams if item.dimmer > 0.02]
    heads = {item.id for item in lit if item.id in {"beam_left", "beam_right"}}
    assert len(heads) != 1
    assert all(item.b >= item.r for item in lit)
    from orng_led.audio.palettes import rgbw_of
    from orng_led.engine.intents import BeamIntent, StageIntent

    blue = rgbw_of("blue")
    dark = StageIntent(
        fixtures={
            "beam_left": BeamIntent(dimmer=0.0, color=blue),
            "beam_right": BeamIntent(dimmer=0.0, color=blue),
        }
    )
    silent, spent = consume_accent(
        dark, fixtures, ("beam",), 0.0, now=2.0, ready_at=2.0,
    )
    assert spent is True
    decoded = decode_simulator_view(show, render_stage(show, silent, motion))
    assert all(item.dimmer <= 0.02 for item in decoded.beams)
    assert all(item.r <= 0.05 for item in decoded.beams)
