"""Picture speed, character, intensity and pairs on decoded frames. No Art-Net."""

from __future__ import annotations

import pytest

from orng_led.audio.auto_show import build_auto_stage
from orng_led.audio.library import BY_ID, LOOKS, director_looks
from orng_led.audio.planner import EpisodePlanner, _cycle_floor, _step_cycle, _variation_cycles
from orng_led.audio.settings import intensity_of
from orng_led.config.io import load_show_config
from orng_led.engine.intents import BarIntent, BeamIntent, ParIntent
from orng_led.engine.renderer import render_stage
from orng_led.simulator.decode import decode_simulator_view

PAIRS = (("par_1", "par_4"), ("par_2", "par_3"), ("beam_left", "beam_right"), ("bar_1", "bar_4"), ("bar_2", "bar_3"))


def _show():
    return load_show_config()


def _pose(show):
    return {
        fixture.id: type("P", (), {"pan": float(fixture.spatial.home_pan), "tilt": float(fixture.spatial.home_tilt)})()
        for fixture in show.patch.fixtures
        if fixture.kind.value == "beam"
    }


def _advance(planner: EpisodePlanner, beats: float, **fields: object):
    return planner.advance(
        beats,
        section=str(fields.get("section") or "groove"),
        level=float(fields.get("level") or 0.7),
        theme=str(fields.get("theme") or "orange"),
        locked=bool(fields.get("locked", True)),
        character=str(fields.get("character") or planner.character),
        period=fields.get("period", planner.period),
        drive=bool(fields.get("drive") or False),
        drive_id=int(fields.get("drive_id") or 0),
        drive_density=float(fields.get("drive_density") or 0.0),
        beat_period_s=0.5,
    )


def test_variation_steps_one_subdivision_and_keeps_operator_period() -> None:
    rng = EpisodePlanner(seed=1)._rng
    jumped = _step_cycle(8.0, (0.5, 1.0, 2.0, 4.0, 8.0), rng, 1.0)
    assert jumped == 4.0
    planner = EpisodePlanner(seed=3)
    planner.character = "impact"
    planner.period = None
    first = _advance(planner, 0.0, character="impact")
    start = float(first.picture_cycle)
    later = first
    for beat in (4.0, 8.0, 12.0, 16.0, 20.0, 24.0, 28.0):
        later = _advance(planner, beat, character="impact")
        picture = [action.cycle_beats for action in later.actions if action.role == "picture"]
        extra = [action.cycle_beats for action in later.actions if action.role == "response"]
        assert all(cycle + 1e-9 >= 1.0 for cycle in picture)
        assert not extra
        if picture:
            ratio = max(start, picture[0]) / min(start, picture[0])
            assert ratio <= 2.0 + 1e-9
            start = picture[0]
    forced = EpisodePlanner(seed=9)
    forced.character = "rhythmic"
    opening = _advance(forced, 0.0, character="rhythmic", period=4.0)
    assert opening.picture_cycle == 4.0
    held = _advance(forced, 16.0, character="rhythmic", period=4.0)
    assert held.picture_cycle == 4.0


def test_groove_decoded_channels_do_not_become_a_blink() -> None:
    show = _show()
    fixtures = list(show.patch.fixtures)
    placements = show.layout.placements
    planner = EpisodePlanner(seed=7)
    planner.character = "impact"
    planner.bind_scene(fixtures)
    episode = _advance(planner, 0.0, character="impact")
    flashes = 0
    previous = None
    for step in range(64):
        beat = step * 0.25
        episode = _advance(planner, beat, character="impact")
        assert planner.flash_gate(beat, wall_s=beat * 0.5) is None
        assert all(action.cycle_beats + 1e-9 >= 1.0 for action in episode.actions if action.role == "picture")
        assert not any(action.role == "response" for action in episode.actions)
        stage = build_auto_stage(
            fixtures,
            "orange",
            "groove",
            0.0,
            0.7,
            beats=beat,
            cycle_beats=episode.picture_cycle,
            placements=placements,
            look=episode.look,
            actions=episode.actions,
            flash_hold=None,
            colour="orange",
        )
        pars = [intent.intensity for intent in stage.fixtures.values() if isinstance(intent, ParIntent)]
        now = max(pars) if pars else 0.0
        if previous is not None and previous >= 0.35 and now < 0.05:
            flashes += 1
        previous = now
    assert flashes <= 8


def test_character_changes_the_decoded_motion_not_just_the_name() -> None:
    show = _show()
    fixtures = list(show.patch.fixtures)
    placements = show.layout.placements
    traces: dict[str, list[float]] = {}
    looks: dict[str, set[str]] = {}
    for character in ("smooth", "rhythmic", "impact"):
        planner = EpisodePlanner(seed=11)
        planner.character = character
        planner.bind_scene(fixtures)
        values: list[float] = []
        used: set[str] = set()
        for step in range(48):
            beat = step * 0.5
            episode = _advance(planner, beat, character=character)
            used.add(episode.look)
            family = getattr(BY_ID.get(episode.look), "family", "")
            assert family in {
                "breathe",
                "spatial_sine",
                "vertical_wave",
                "fill_drain",
                "open_close",
                "compound",
                "layer",
                "phase_pulse",
                "cascade",
                "call_response",
                "band_tail",
                "meet",
                "multi_band",
                "diagonal",
                "hit_decay",
                "double_hit",
                "sparse_hits",
                "density_build",
                "dark_window",
                "negative",
            }
            stage = build_auto_stage(
                fixtures,
                "orange",
                "groove",
                0.0,
                0.7,
                beats=beat,
                cycle_beats=episode.picture_cycle,
                placements=placements,
                look=episode.look,
                actions=episode.actions,
                colour="orange",
            )
            on = 0
            for intent in stage.fixtures.values():
                if isinstance(intent, BarIntent):
                    on += sum(1 for bit in intent.segments if bit > 0.5)
                elif isinstance(intent, ParIntent):
                    on += 2 if intent.intensity > 0.2 else 0
            values.append(float(on))
        traces[character] = values
        looks[character] = used
    from orng_led.audio.settings import CHARACTER_FAMILIES

    smooth_families = {getattr(BY_ID.get(name), "family", "") for name in looks["smooth"]}
    impact_families = {getattr(BY_ID.get(name), "family", "") for name in looks["impact"]}
    assert smooth_families & CHARACTER_FAMILIES["smooth"]
    assert impact_families & CHARACTER_FAMILIES["impact"]
    assert traces["smooth"] != traces["impact"] or looks["smooth"] != looks["impact"]
    smooth_delta = sum(abs(b - a) for a, b in zip(traces["smooth"], traces["smooth"][1:]))
    impact_delta = sum(abs(b - a) for a, b in zip(traces["impact"], traces["impact"][1:]))
    assert impact_delta >= smooth_delta


def test_intensity_scales_brightness_not_the_mask_or_tempo() -> None:
    show = _show()
    fixtures = list(show.patch.fixtures)
    placements = show.layout.placements
    masks = []
    peaks = []
    for name in ("soft", "normal", "full"):
        amount = intensity_of(name)
        stage = build_auto_stage(
            fixtures,
            "orange",
            "groove",
            0.0,
            amount,
            look="wash",
            beats=2.0,
            cycle_beats=4.0,
            placements=placements,
            colour="orange",
        )
        bars = [stage.fixtures[ident] for ident in ("bar_1", "bar_2", "bar_3", "bar_4") if ident in stage.fixtures]
        assert bars and all(isinstance(bar, BarIntent) for bar in bars)
        mask = tuple(tuple(1 if bit > 0.5 else 0 for bit in bar.segments) for bar in bars)
        masks.append(mask)
        pars = [intent.intensity for intent in stage.fixtures.values() if isinstance(intent, ParIntent)]
        dimmers = [bar.dimmer for bar in bars]
        peaks.append((max(pars) if pars else 0.0, max(dimmers)))
        for intent in stage.fixtures.values():
            if isinstance(intent, BeamIntent):
                assert intent.dimmer == pytest.approx(0.0) or intent.dimmer == pytest.approx(amount)
                assert intent.strobe == 0.0
    assert masks[0] == masks[1] == masks[2]
    assert peaks[0][0] < peaks[1][0] <= peaks[2][0] + 1e-9
    assert peaks[0][1] < peaks[1][1] <= peaks[2][1] + 1e-9


def test_mirror_pairs_stay_together_except_named_left_right() -> None:
    show = _show()
    fixtures = list(show.patch.fixtures)
    placements = show.layout.placements
    for look in (
        "sine_horizontal",
        "sine_heads",
        "cascade_heads",
        "pulse_by_x",
        "bloom",
        "vertical_wave_stagger_x",
    ):
        for beats in (0.0, 0.35, 0.7):
            stage = build_auto_stage(
                fixtures,
                "orange",
                "groove",
                0.0,
                0.85,
                look=look,
                beats=beats,
                cycle_beats=2.0,
                placements=placements,
            )
            for left_id, right_id in PAIRS:
                if left_id.startswith("bar"):
                    continue
                if left_id not in stage.fixtures or right_id not in stage.fixtures:
                    continue
                assert stage.fixtures[left_id] == stage.fixtures[right_id], (look, beats, left_id, right_id)
    crossed = build_auto_stage(
        fixtures,
        "orange",
        "groove",
        0.0,
        0.85,
        look="fill_left",
        beats=1.0,
        cycle_beats=2.0,
        placements=placements,
    )
    assert crossed.fixtures["bar_1"].segments != crossed.fixtures["bar_4"].segments


def test_head_and_bar_pairs_fire_together_on_a_shared_hit() -> None:
    show = _show()
    stage = build_auto_stage(
        list(show.patch.fixtures),
        "orange",
        "groove",
        0.0,
        0.85,
        look="hit_heads",
        beats=0.0,
        cycle_beats=1.0,
        placements=show.layout.placements,
        colour="orange",
        cast="heads",
        groups=("par", "beam"),
    )
    left = stage.fixtures["beam_left"]
    right = stage.fixtures["beam_right"]
    assert isinstance(left, BeamIntent) and isinstance(right, BeamIntent)
    assert left.dimmer == right.dimmer
    assert stage.fixtures["par_1"].intensity == stage.fixtures["par_4"].intensity
    frame = render_stage(show, stage, {})
    view = decode_simulator_view(show, frame)
    beams = {item.id: item for item in view.beams}
    assert beams["beam_left"].dimmer == beams["beam_right"].dimmer


def test_director_phrase_catalog_keeps_the_library_reachable() -> None:
    names = director_looks("phrase", "orange", character="rhythmic", current="vertical_wave")
    assert "vertical_wave" in names
    assert len(names) >= 40
    assert len(names) <= len(LOOKS)
    assert "breathe_pars" not in names


def test_cycle_floor_blocks_wave_blinks() -> None:
    assert _cycle_floor("sine_vertical", "smooth") == 2.0
    assert _cycle_floor("cascade_up", "impact") == 1.0
    assert _cycle_floor("band_left", "rhythmic") == 1.0
    assert 0.5 not in _variation_cycles("smooth")
