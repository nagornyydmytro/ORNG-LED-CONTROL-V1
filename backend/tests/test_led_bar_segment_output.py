"""LED Bar segment/whole exclusivity using the saved operator mapping."""

from __future__ import annotations

from orng_led.config import default_config_dir, load_show_config
from orng_led.config.models import ChannelRole, FixtureKind
from orng_led.config.validation import global_channel
from orng_led.engine.engine import Engine, FakeClock
from orng_led.engine.intents import BarIntent, Rgbw, StageIntent
from orng_led.engine.renderer import render_stage
from orng_led.engine.show_whitelist import assert_show_frame_clean, scrub_show_frame
from orng_led.presets.store import PresetStore
from orng_led.simulator.decode import decode_simulator_view

PRESET_IDS = tuple(f"P{i:02d}" for i in range(1, 11))
FORBIDDEN_BAR_ROLES = (
    ChannelRole.PROGRAM,
    ChannelRole.EFFECT_SPEED,
    ChannelRole.MACRO,
    ChannelRole.DIRECTION_MODE,
)


def _programs() -> dict:
    return PresetStore.load(default_config_dir() / "presets").programs()


def _engine(preset_id: str = "P05") -> Engine:
    show = load_show_config()
    engine = Engine(show=show, presets=_programs(), active_preset_id=preset_id, clock=FakeClock())
    engine.set_blackout(False)
    return engine


def _bar_channels(show, fixture_id: str):
    fixture = next(fx for fx in show.patch.fixtures if fx.id == fixture_id)
    profile = show.profile_for(fixture)
    return fixture, profile


def _read_role_values(
    show, frame, fixture_id: str, role: ChannelRole
) -> list[tuple[int, int, int]]:
    """Return (local, global, value) for every channel with ``role``."""
    fixture, profile = _bar_channels(show, fixture_id)
    out: list[tuple[int, int, int]] = []
    for channel in profile.channels:
        if channel.role is not role:
            continue
        global_ch = global_channel(fixture.start_address, channel.local)
        out.append((channel.local, global_ch, int(frame[global_ch - 1])))
    return out


def _segment_values(show, frame, fixture_id: str) -> list[int]:
    return [
        value
        for _, _, value in _read_role_values(show, frame, fixture_id, ChannelRole.SEGMENT_COLOR)
    ]


def _palette_values(show, fixture_id: str) -> set[int]:
    _, profile = _bar_channels(show, fixture_id)
    values = {0}
    for channel in profile.channels:
        if channel.role is ChannelRole.SEGMENT_COLOR and channel.palette:
            values.update(int(v) for v in channel.palette.values())
    return values


def _former_strip_locals(show, fixture_id: str) -> list[int]:
    """Locals that used to be strip/mode select (now unused, must stay 0)."""
    _, profile = _bar_channels(show, fixture_id)
    # Local 3 was the former Direction/Mode fixed=236 channel on bar_15ch.
    return [
        channel.local
        for channel in profile.channels
        if channel.local == 3
        or (channel.role is ChannelRole.UNUSED and "strip" in (channel.notes or "").lower())
    ]


def _read_local(show, frame, fixture_id: str, local: int) -> int:
    fixture, _ = _bar_channels(show, fixture_id)
    global_ch = global_channel(fixture.start_address, local)
    return int(frame[global_ch - 1])


def _find_chase_episode(programs) -> tuple[str, int]:
    """Prefer all_rear chase so all four bars are driven."""
    fallback: tuple[str, int] | None = None
    for preset_id, program in programs.items():
        doc = getattr(program, "document", None)
        if doc is None:
            continue
        for index, episode in enumerate(doc.episodes):
            if episode.effect != "chase":
                continue
            groups = set(episode.groups)
            if groups == {"all_rear"} or "all_rear" in groups:
                return preset_id, index
            if fallback is None and ("bar" in groups):
                fallback = (preset_id, index)
    if fallback is not None:
        return fallback
    raise AssertionError("No chase+bar episode found in staff presets")


def _seek_into_episode(engine: Engine, episode_index: int, *, settle_s: float = 4.0) -> None:
    """Land past the soft transition using the episode's real timeline."""
    engine.seek_episode(episode_index)
    remaining = settle_s
    while remaining > 0:
        step = min(0.25, remaining)
        engine.tick(dt_s=step, wall_dt_s=step)
        remaining -= step


def test_saved_bar_mapping_has_segments_and_whole_without_required_fixed() -> None:
    show = load_show_config()
    bars = [fx for fx in show.patch.fixtures if fx.kind is FixtureKind.BAR]
    assert len(bars) == 4
    profile_ids = {fx.profile_id for fx in bars}
    assert len(profile_ids) == 1  # shared profile applies to all four
    for fixture in bars:
        profile = show.profile_for(fixture)
        roles = {ch.role for ch in profile.channels}
        assert ChannelRole.DIMMER in roles
        assert ChannelRole.SEGMENT_COLOR in roles
        assert ChannelRole.WHOLE_COLOR in roles
        # Former strip-select must not keep a fixed service value.
        ch3 = next(ch for ch in profile.channels if ch.local == 3)
        assert ch3.role is ChannelRole.UNUSED
        assert ch3.fixed_value is None
        segs = [ch for ch in profile.channels if ch.role is ChannelRole.SEGMENT_COLOR]
        assert len(segs) == 8
        assert all(ch.palette for ch in segs)


def test_chase_moves_segment_palette_values_on_all_four_bars() -> None:
    show = load_show_config()
    programs = _programs()
    preset_id, episode_index = _find_chase_episode(programs)
    engine = _engine(preset_id)
    _seek_into_episode(engine, episode_index, settle_s=4.0)

    snapshots: list[dict[str, list[int]]] = []
    for _ in range(8):
        frame = engine.tick(dt_s=0.35, wall_dt_s=0.35).frame
        per_bar: dict[str, list[int]] = {}
        for fixture in show.patch.fixtures:
            if fixture.kind is not FixtureKind.BAR:
                continue
            segs = _segment_values(show, frame, fixture.id)
            whole = _read_role_values(show, frame, fixture.id, ChannelRole.WHOLE_COLOR)
            forbidden = []
            for role in FORBIDDEN_BAR_ROLES:
                forbidden.extend(_read_role_values(show, frame, fixture.id, role))
            assert whole and whole[0][2] == 0, fixture.id
            for local in _former_strip_locals(show, fixture.id):
                assert _read_local(show, frame, fixture.id, local) == 0
            assert all(v == 0 for _, _, v in forbidden)
            palette = _palette_values(show, fixture.id)
            assert all(v in palette for v in segs), (fixture.id, segs, palette)
            assert any(v > 0 for v in segs), fixture.id
            assert len(set(segs)) >= 2, fixture.id
            per_bar[fixture.id] = segs
        snapshots.append(per_bar)

    for fixture_id in snapshots[0]:
        patterns = [tuple(snap[fixture_id]) for snap in snapshots]
        assert len(set(patterns)) >= 3, fixture_id


def test_pulse_to_chase_transition_never_leaves_whole_color_on() -> None:
    """Regression: soft blend used to keep whole=True for ~1.5s into chase."""
    show = load_show_config()
    engine = _engine("P05")
    engine.seek_episode(4)
    for _ in range(20):
        frame = engine.tick(dt_s=0.15, wall_dt_s=0.15).frame
        for fixture in show.patch.fixtures:
            if fixture.kind is not FixtureKind.BAR:
                continue
            whole = _read_role_values(show, frame, fixture.id, ChannelRole.WHOLE_COLOR)
            assert whole[0][2] == 0, (fixture.id, engine.preset_elapsed_s)
            segs = _segment_values(show, frame, fixture.id)
            dimmer = _read_role_values(show, frame, fixture.id, ChannelRole.DIMMER)[0][2]
            if dimmer > 20:
                assert any(v > 0 for v in segs), (fixture.id, dimmer, segs)
            assert _read_local(show, frame, fixture.id, 3) == 0


def test_whole_mode_zeros_segments_segment_mode_zeros_whole() -> None:
    show = load_show_config()
    bar = next(fx for fx in show.patch.fixtures if fx.kind is FixtureKind.BAR)
    color = Rgbw(r=1, g=0.2, b=0)
    seg_stage = StageIntent(
        fixtures={
            bar.id: BarIntent(
                segments=(1, 0, 1, 0, 1, 0, 1, 0),
                dimmer=1.0,
                color=color,
                whole=False,
            )
        }
    )
    whole_stage = StageIntent(
        fixtures={bar.id: BarIntent(segments=(0,) * 8, dimmer=1.0, color=color, whole=True)}
    )
    seg_frame = render_stage(show, seg_stage, {}, master=1.0)
    whole_frame = render_stage(show, whole_stage, {}, master=1.0)
    assert _read_role_values(show, seg_frame, bar.id, ChannelRole.WHOLE_COLOR)[0][2] == 0
    assert any(v > 0 for v in _segment_values(show, seg_frame, bar.id))
    assert all(v == 0 for v in _segment_values(show, whole_frame, bar.id))
    assert _read_role_values(show, whole_frame, bar.id, ChannelRole.WHOLE_COLOR)[0][2] > 0
    assert _read_local(show, seg_frame, bar.id, 3) == 0
    assert _read_local(show, whole_frame, bar.id, 3) == 0


def test_whitelist_zeros_unused_and_blocks_program_roles() -> None:
    show = load_show_config()
    engine = _engine("P05")
    _seek_into_episode(engine, 4, settle_s=5.0)
    frame = engine.tick(dt_s=0.2, wall_dt_s=0.2).frame

    assert_show_frame_clean(show, frame)
    for fixture in show.patch.fixtures:
        if fixture.kind is not FixtureKind.BAR:
            continue
        assert _read_local(show, frame, fixture.id, 3) == 0
        clone = list(frame)
        scrub_show_frame(show, clone)
        assert _read_local(show, clone, fixture.id, 3) == 0
        for role in FORBIDDEN_BAR_ROLES:
            assert all(v == 0 for _, _, v in _read_role_values(show, frame, fixture.id, role))


def test_preview_decode_matches_post_whitelist_segment_pattern() -> None:
    show = load_show_config()
    engine = _engine("P05")
    _seek_into_episode(engine, 4, settle_s=6.0)
    frame = engine.tick(dt_s=0.3, wall_dt_s=0.3).frame
    view = decode_simulator_view(show, frame)
    for bar in view.bars:
        segs = _segment_values(show, frame, bar.id)
        decoded_on = [1 if level > 0.02 else 0 for level in bar.segments]
        wire_on = [1 if value > 0 else 0 for value in segs]
        assert decoded_on == wire_on, bar.id
        whole = _read_role_values(show, frame, bar.id, ChannelRole.WHOLE_COLOR)[0][2]
        assert whole == 0


def test_p01_p10_and_safe_states_keep_former_strip_channel_zero() -> None:
    show = load_show_config()
    for preset_id in (*PRESET_IDS, "NONE"):
        engine = _engine(preset_id if preset_id != "NONE" else "P01")
        if preset_id == "NONE":
            engine.select_preset("NONE")
        for _ in range(40):
            frame = engine.tick(dt_s=4.0, wall_dt_s=4.0).frame
            for fixture in show.patch.fixtures:
                if fixture.kind is not FixtureKind.BAR:
                    continue
                assert _read_local(show, frame, fixture.id, 3) == 0
                segs = _segment_values(show, frame, fixture.id)
                whole = _read_role_values(show, frame, fixture.id, ChannelRole.WHOLE_COLOR)[0][2]
                if whole > 0:
                    assert all(v == 0 for v in segs), (preset_id, fixture.id)
                if any(v > 0 for v in segs):
                    assert whole == 0, (preset_id, fixture.id)

    # Blackout + Live FX must also keep former strip channel at 0.
    engine = _engine("P05")
    _seek_into_episode(engine, 4, settle_s=3.0)
    engine.trigger_white_hit()
    frame = engine.tick(dt_s=0.05, wall_dt_s=0.05).frame
    for fixture in show.patch.fixtures:
        if fixture.kind is FixtureKind.BAR:
            assert _read_local(show, frame, fixture.id, 3) == 0
    engine.set_blackout(True)
    frame = engine.tick(dt_s=0.05, wall_dt_s=0.05).frame
    for fixture in show.patch.fixtures:
        if fixture.kind is FixtureKind.BAR:
            assert _read_local(show, frame, fixture.id, 3) == 0


def test_live_fx_does_not_enable_program_or_break_segment_exclusivity() -> None:
    show = load_show_config()
    engine = _engine("P05")
    _seek_into_episode(engine, 4, settle_s=5.0)
    engine.tick(dt_s=0.2, wall_dt_s=0.2)
    engine.trigger_white_hit()
    frame = engine.tick(dt_s=0.05, wall_dt_s=0.05).frame

    assert_show_frame_clean(show, frame)
    for fixture in show.patch.fixtures:
        if fixture.kind is not FixtureKind.BAR:
            continue
        whole = _read_role_values(show, frame, fixture.id, ChannelRole.WHOLE_COLOR)[0][2]
        segs = _segment_values(show, frame, fixture.id)
        assert whole == 0
        assert any(v > 0 for v in segs)
        assert _read_local(show, frame, fixture.id, 3) == 0
        for role in (ChannelRole.PROGRAM, ChannelRole.EFFECT_SPEED):
            vals = _read_role_values(show, frame, fixture.id, role)
            assert all(v == 0 for _, _, v in vals)
