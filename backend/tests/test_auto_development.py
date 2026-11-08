"""Motif development, one allowed-composition filter, no catalog tour."""

from __future__ import annotations

from orng_led.audio.auto_show import StructuralEvent, build_auto_stage
from orng_led.audio.library import BY_ID, allowed_compositions, musical_anchor
from orng_led.audio.planner import MOTIF_HOLD, EpisodePlanner
from orng_led.audio.service import BpmService
from orng_led.config.io import load_show_config
from orng_led.engine.intents import BarIntent, ParIntent


def _show():
    return load_show_config()


def _fixtures():
    return list(_show().patch.fixtures)


def test_long_groove_develops_once_and_a_calm_stretch_does_not_chop() -> None:
    fixtures = _fixtures()
    planner = EpisodePlanner(seed=3)
    opening = planner.advance(0.0, section="groove", level=0.7, theme="orange", locked=True, fixtures=fixtures)
    low, high = MOTIF_HOLD["rhythmic"]
    held = planner.advance(float(low), section="groove", level=0.7, theme="orange", locked=True, fixtures=fixtures)
    assert held.look == opening.look
    planner.advance(float(high - low), section="groove", level=0.7, theme="orange", locked=True, fixtures=fixtures)
    released = planner.advance(float(high), section="groove", level=0.7, theme="orange", locked=True, fixtures=fixtures)
    assert released.look != opening.look
    assert BY_ID[released.look].program == BY_ID[opening.look].program
    assert released.picture_cycle in {0.5, 1.0, 2.0, 4.0, 8.0}

    calm = EpisodePlanner(seed=3)
    calm.advance(0.0, section="groove", level=0.5, theme="orange", character="smooth", locked=True, fixtures=fixtures)
    looks = []
    for beat in range(0, 80, 4):
        episode = calm.advance(
            float(beat), section="break", level=0.3, theme="orange", character="smooth", locked=True, fixtures=fixtures
        )
        if episode.section == "break":
            looks.append(episode.look)
    assert looks
    assert len(set(looks)) == 1


def test_section_chatter_does_not_refire_and_a_drop_is_once() -> None:
    planner = EpisodePlanner(seed=1)
    planner.advance(0.0, section="groove", level=0.7, theme="orange", locked=True)
    for beat in range(1, 12):
        section = "break" if beat % 2 else "groove"
        planner.advance(float(beat), section=section, level=0.7, theme="orange", locked=True)
    assert not any(episode.reason == "flow" for episode in planner.played)
    planner.advance(12.0, section="break", level=0.4, theme="orange", locked=True)
    planner.advance(16.0, section="break", level=0.4, theme="orange", locked=True)
    flows = [episode for episode in planner.played if episode.reason == "flow"]
    assert len(flows) == 1

    drop = StructuralEvent("drop", 7, 4.0)
    again = EpisodePlanner(seed=1)
    again.advance(0.0, section="groove", level=0.7, theme="orange", locked=True)
    again.advance(4.0, section="groove", level=0.9, theme="orange", locked=True, events=(drop,))
    again.advance(6.0, section="drop", level=0.9, theme="orange", locked=True, events=(drop,))
    assert sum(1 for row in again.journal() if row["kind"] == "drop_next") == 1
    assert any(episode.reason == "drop_next" for episode in again.played)
    assert 6.0 < MOTIF_HOLD["rhythmic"][1]


def test_palette_keeps_the_motif_and_intensity_is_not_tempo() -> None:
    planner = EpisodePlanner(seed=2)
    opening = planner.advance(0.0, section="groove", level=0.4, theme="orange", palette="orange", locked=True)
    coloured = planner.advance(4.0, section="groove", level=0.4, theme="orange", palette="blue", locked=True)
    assert coloured.look == opening.look
    assert coloured.palette == "blue"
    assert coloured.picture_cycle == opening.picture_cycle
    louder = planner.advance(8.0, section="groove", level=1.0, theme="orange", palette="blue", locked=True)
    assert louder.look == opening.look
    assert louder.picture_cycle == opening.picture_cycle
    assert louder.intensity == 1.0


def test_operator_next_and_exclusion_share_the_filter(tmp_path) -> None:
    fixtures = _fixtures()
    plain = EpisodePlanner(seed=5)
    beat = 0.0
    seen: list[str] = []
    while beat <= MOTIF_HOLD["rhythmic"][1] + 1e-9:
        episode = plain.advance(beat, section="groove", level=0.7, theme="orange", locked=True, fixtures=fixtures)
        if not seen or seen[-1] != episode.look:
            seen.append(episode.look)
        beat += 16.0
    assert len(seen) >= 2
    banned = frozenset(seen[1:])
    planner = EpisodePlanner(seed=5)
    planner.set_excluded(banned)
    beat = 0.0
    played: list[str] = []
    while beat <= MOTIF_HOLD["rhythmic"][1] + 32.0:
        episode = planner.advance(beat, section="groove", level=0.7, theme="orange", locked=True, fixtures=fixtures)
        played.append(episode.look)
        beat += 16.0
    chosen = planner.request_next_look(beat, locked=True)
    played.append(chosen)
    drop = StructuralEvent("drop", 3, beat + 2.0)
    planner.advance(beat + 2.0, section="groove", level=0.9, theme="orange", locked=True, fixtures=fixtures, events=(drop,))
    if planner.upcoming is not None:
        played.append(planner.upcoming.look)
    lost = planner.advance(
        beat + 4.0, section="break", level=0.0, theme="orange", locked=False, signal_lost=True, fixtures=fixtures
    )
    played.append(lost.look)
    back = planner.advance(
        beat + 6.0, section="groove", level=0.7, theme="orange", locked=True, fixtures=fixtures
    )
    played.append(back.look)
    if planner.upcoming is not None:
        played.append(planner.upcoming.look)
    assert banned.isdisjoint(played)

    service = BpmService(tmp_path, "room")
    service.configure(enabled=True, scope="auto", excluded=tuple(banned))
    status = service.status()
    assert status["excluded"] == sorted(banned)
    assert "journal" not in status
    assert "reasons" not in status


def test_seeds_reach_suitable_looks_without_touring_the_catalogue() -> None:
    fixtures = _fixtures()
    allowed = {item.id for item in allowed_compositions("orange", fixtures=fixtures)}
    pools = set()
    for pool in ("phrase", "calm", "rise", "accent"):
        pools.update(item.id for item in allowed_compositions("orange", pool=pool, fixtures=fixtures))
    played: set[str] = set()
    for seed in (1, 7, 11, 99):
        planner = EpisodePlanner(seed=seed)
        beat = 0.0
        while beat < 140.0:
            episode = planner.advance(
                beat, section="groove", level=0.7, theme="orange", locked=True, fixtures=fixtures
            )
            played.add(episode.look)
            beat += 16.0
    assert len(played) >= 2
    assert played <= allowed
    assert len(played) < len(pools)
    for name in played:
        assert BY_ID[name].pool != "accent"


def test_smooth_and_beat_move_differently() -> None:
    show = _show()
    fixtures = _fixtures()
    placed = show.layout.placements
    wave = BY_ID["vertical_wave"]
    pulse = BY_ID["pulse"]
    assert musical_anchor(wave) is None
    assert musical_anchor(pulse) == "brightness"

    def _bits(look: str, beat: float) -> tuple:
        stage = build_auto_stage(
            fixtures, "orange", "groove", 0.0, 0.8, beats=beat, cycle_beats=2.0,
            placements=placed, look=look,
        )
        bars = []
        pars = []
        for intent in stage.fixtures.values():
            if isinstance(intent, BarIntent):
                bars.append(tuple(round(float(bit), 2) for bit in intent.segments))
            elif isinstance(intent, ParIntent):
                pars.append(round(float(intent.intensity), 3))
        return (tuple(bars), tuple(pars))

    assert _bits("vertical_wave", 0.0) != _bits("vertical_wave", 1.0)
    assert _bits("pulse", 0.0) != _bits("pulse", 1.0)
    assert _bits("vertical_wave", 0.0) != _bits("pulse", 0.0)
