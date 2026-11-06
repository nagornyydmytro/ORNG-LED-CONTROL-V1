"""One integration pass of the musical chain. Isolated MockTransport, disarmed."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from orng_led.api.runtime import AppRuntime
from orng_led.api.schemas import PreviewSpeedCommand
from orng_led.audio.auto_show import build_auto_stage
from orng_led.audio.bpm import TempoReading, tempo_ratio
from orng_led.audio.gallery import render_look
from orng_led.config.io import load_show_config
from orng_led.config.models import ChannelRole, FixtureKind
from orng_led.config.validation import global_channel
from orng_led.engine.clock import FRAME_DT
from orng_led.engine.intents import BarIntent
from orng_led.input.contract import PROGRAM_SPEED_MAX, PROGRAM_SPEED_MIN
from orng_led.output.mock import MockTransport
from orng_led.simulator.decode import decode_simulator_view

RATE = 44100


def _runtime(tmp_path: Path) -> AppRuntime:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path, show=load_show_config())
    assert isinstance(runtime.output.transport, MockTransport)
    assert runtime.output.armed is False
    runtime.engine.set_blackout(False)
    runtime.engine.set_master_brightness(1.0)
    return runtime


def _lock(runtime: AppRuntime, bpm: float = 120.0, now: float = 0.0) -> None:
    runtime.bpm.follower.push(TempoReading(bpm, 0.0, 1.0), now=now)
    runtime.bpm.follower.push(TempoReading(bpm, 0.0, 1.0), now=now)
    runtime.bpm.section.section = "groove"
    runtime.bpm.section.level = 0.85
    runtime.bpm.section.shown = 0.85


def _bar_bits(stage) -> tuple[tuple[int, ...], ...]:
    rows = []
    for ident in ("bar_1", "bar_2", "bar_3", "bar_4"):
        intent = stage.fixtures.get(ident)
        if not isinstance(intent, BarIntent):
            rows.append(())
            continue
        rows.append(tuple(1 if bit > 0.5 else 0 for bit in intent.segments))
    return tuple(rows)


def test_hits_follow_phase_when_signal_starts_late(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange", character="rhythmic")
    offset_s = 0.21
    delay_s = 0.04
    runtime.bpm._now = offset_s
    period = 60.0 / 120.0
    heard = ((0.0 - delay_s - offset_s) % period) / period
    runtime.bpm.follower.push(TempoReading(120.0, heard, 1.0), now=offset_s, latency_s=delay_s)
    runtime.bpm.follower.push(TempoReading(120.0, heard, 1.0), now=offset_s, latency_s=delay_s)
    runtime.bpm.section.section = "groove"
    runtime.bpm.section.level = 0.85
    runtime.bpm.section.shown = 0.85
    assert runtime.bpm.follower.locked
    crossings = []
    prev = runtime.bpm.follower.action_beats_at(runtime.bpm.clock_s)
    for _ in range(int(2.5 / FRAME_DT)):
        runtime.tick(FRAME_DT)
        now = runtime.bpm.follower.action_beats_at(runtime.bpm.clock_s)
        if int(now) > int(prev):
            crossings.append(runtime.bpm.follower.phase_at(runtime.bpm.clock_s))
        prev = now
    assert crossings
    assert max(min(phase, 1.0 - phase) for phase in crossings) < 0.12


def test_tempo_scales_action_period_once(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.preview_speed = 2.0
    runtime.apply_bpm_sync(enabled=True, scope="preset", mode="smooth")
    runtime.bpm._now = 0.0
    runtime.bpm.follower.push(TempoReading(174.0, 0.0, 1.0), now=0.0)
    runtime.bpm.follower.push(TempoReading(174.0, 0.0, 1.0), now=0.0)
    assert runtime.bpm.follower.locked
    factor = runtime.bpm.show_factor(2.0)
    assert factor == pytest.approx(tempo_ratio(174.0))
    assert factor != pytest.approx(tempo_ratio(174.0) * 2.0)
    start = runtime.engine.preset_elapsed_s
    runtime.tick(1.0)
    assert runtime.engine.preset_elapsed_s - start == pytest.approx(factor, abs=0.08)
    assert runtime.engine.effect_rate_scale == pytest.approx(1.0)


def test_long_cycle_does_not_restart_every_beat() -> None:
    show = load_show_config()
    first = build_auto_stage(
        list(show.patch.fixtures),
        "orange",
        "groove",
        0.0,
        0.85,
        look="vertical_wave",
        beats=0.0,
        cycle_beats=8.0,
        placements=show.layout.placements,
        groups=("bar",),
    )
    next_beat = build_auto_stage(
        list(show.patch.fixtures),
        "orange",
        "groove",
        0.0,
        0.85,
        look="vertical_wave",
        beats=1.0,
        cycle_beats=8.0,
        placements=show.layout.placements,
        groups=("bar",),
    )
    later = build_auto_stage(
        list(show.patch.fixtures),
        "orange",
        "groove",
        0.0,
        0.85,
        look="vertical_wave",
        beats=4.0,
        cycle_beats=8.0,
        placements=show.layout.placements,
        groups=("bar",),
    )
    assert _bar_bits(first) != _bar_bits(later)
    delta = sum(
        abs(a - b)
        for left, right in zip(_bar_bits(first), _bar_bits(next_beat), strict=True)
        for a, b in zip(left, right, strict=True)
    )
    assert delta <= 8


def test_cast_heads_does_not_expand_to_all_fixtures(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.apply_bpm_sync(
        enabled=True, scope="auto", palette="orange", cast="heads", character="rhythmic"
    )
    _lock(runtime)
    snap = runtime.tick(FRAME_DT)
    view = decode_simulator_view(runtime.engine.show, snap.frame)
    lit_bars = [
        item for item in view.bars if item.dimmer > 0.05 or any(bit > 0.05 for bit in item.segments)
    ]
    assert lit_bars == []
    assert any(item.intensity > 0.05 for item in view.pars if not item.face)


def test_auto_settings_change_the_decoded_frame(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.apply_bpm_sync(
        enabled=True, scope="auto", palette="orange", character="smooth", intensity="soft"
    )
    _lock(runtime)
    runtime.tick(FRAME_DT)
    planner = runtime.bpm.planner
    assert planner.current is not None
    planner.current = planner.current.__class__(
        **{
            **{
                field: getattr(planner.current, field)
                for field in planner.current.__dataclass_fields__
            },
            "look": "wash",
            "groups": ("par", "bar", "beam"),
        }
    )
    soft = runtime.tick(FRAME_DT)
    runtime.apply_bpm_sync(character="impact", intensity="full", palette="contrast", period=1.0)
    loud = runtime.tick(FRAME_DT)
    assert planner.character == "impact"
    assert planner.palette == "contrast"
    assert planner.period == pytest.approx(1.0)
    assert runtime.bpm.status()["palette"] == "contrast"
    assert soft.frame != loud.frame


def test_palette_keeps_look_and_looks_differ_beyond_colour() -> None:
    show = load_show_config()
    wave_orange = render_look(
        "vertical_wave", beats=0.35, cycle_beats=2.0, palette="orange", show=show
    )
    wave_ice = render_look("vertical_wave", beats=0.35, cycle_beats=2.0, palette="ice", show=show)
    pulse = render_look("pulse", beats=0.35, cycle_beats=2.0, palette="orange", show=show)
    orange_bits = [item["segments"] for item in wave_orange["bars"]]
    ice_bits = [item["segments"] for item in wave_ice["bars"]]
    pulse_bits = [item["segments"] for item in pulse["bars"]]
    assert orange_bits == ice_bits
    assert orange_bits != pulse_bits
    orange_color = (
        wave_orange["bars"][1]["r"],
        wave_orange["bars"][1]["g"],
        wave_orange["bars"][1]["b"],
    )
    ice_color = (wave_ice["bars"][1]["r"], wave_ice["bars"][1]["g"], wave_ice["bars"][1]["b"])
    assert orange_color != ice_color


def test_blackout_kills_auto_layers_and_keeps_service_channels(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="carnival", character="impact")
    _lock(runtime)
    runtime.bpm.section.section = "drop"
    runtime.bpm.section.level = 1.0
    runtime.bpm.section.shown = 1.0
    live = runtime.tick(FRAME_DT)
    assert sum(live.frame) > 0
    runtime.engine.set_blackout(True)
    dark = runtime.tick(FRAME_DT)
    view = decode_simulator_view(runtime.engine.show, dark.frame)
    assert all(item.intensity <= 0.02 for item in view.pars)
    assert all(
        item.dimmer <= 0.02 and all(bit <= 0.02 for bit in item.segments) for item in view.bars
    )
    show = runtime.engine.show
    for fixture in show.patch.fixtures:
        if fixture.kind is not FixtureKind.BEAM:
            continue
        profile = show.profile_for(fixture)
        for channel in profile.channels:
            if channel.role not in {ChannelRole.PAN_COARSE, ChannelRole.TILT_COARSE}:
                continue
            index = global_channel(fixture.start_address, channel.local) - 1
            assert dark.frame[index] == live.frame[index]


def test_leaving_auto_clears_plan_and_keeps_holds(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange")
    _lock(runtime)
    runtime.tick(FRAME_DT)
    assert runtime.bpm.planner.current is not None
    runtime.apply_bpm_sync(scope="preset")
    assert runtime.bpm.planner.current is None
    assert runtime.engine.auto_base is None
    runtime.engine.strobe_press()
    runtime.tick(FRAME_DT)
    assert runtime.engine.overlays.strobe_held is True
    runtime.engine.strobe_release()
    assert runtime.engine.overlays.strobe_held is False


def test_page_refresh_does_not_write_speed(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.apply_preview_speed(2.5)
    assert PROGRAM_SPEED_MIN <= 2.5 <= PROGRAM_SPEED_MAX
    runtime.apply_bpm_sync(enabled=True, scope="preset", mode="smooth")
    kept = runtime.preview_speed
    state = runtime.build_state()
    assert state.preview_speed == pytest.approx(kept)
    assert PreviewSpeedCommand(value=0.5).value == pytest.approx(0.5)
    with pytest.raises(ValidationError):
        PreviewSpeedCommand(value=0.25)


def test_analysis_crash_does_not_block_tick_or_blackout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange")

    def boom(*_args: object, **_kwargs: object):
        raise RuntimeError("analysis down")

    monkeypatch.setattr("orng_led.audio.service.inspect_tempo", boom)
    started = time.perf_counter()
    assert runtime.bpm.feed([0.2] * RATE, RATE, now=1.0) is None
    snap = runtime.tick(FRAME_DT)
    runtime.engine.set_blackout(True)
    runtime.tick(FRAME_DT)
    runtime.engine.strobe_press()
    runtime.engine.strobe_release()
    assert time.perf_counter() - started < 0.2
    assert isinstance(snap.frame, list)
    assert runtime.output.transport is not None
    assert runtime.engine.overlays.strobe_held is False


def _tick_stats(samples: list[float]) -> dict[str, float | int]:
    ordered = sorted(samples)

    def at(q: float) -> float:
        index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
        return round(ordered[index] * 1000, 2)

    return {
        "n": len(ordered),
        "p50_ms": at(0.5),
        "p95_ms": at(0.95),
        "max_ms": round(max(ordered) * 1000, 2),
    }


def test_home_timing_report_is_calc_interval_not_sendto(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.apply_bpm_sync(enabled=False)
    command_started = time.perf_counter()
    runtime.apply_bpm_sync(enabled=False, palette="orange")
    command_ms = (time.perf_counter() - command_started) * 1000
    off_ticks: list[float] = []
    for _ in range(90):
        started = time.perf_counter()
        runtime.tick(FRAME_DT)
        off_ticks.append(time.perf_counter() - started)
    off = runtime.frame_timing()
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange")
    _lock(runtime, now=runtime.bpm.clock_s)
    on_ticks: list[float] = []
    for _ in range(90):
        started = time.perf_counter()
        runtime.tick(FRAME_DT)
        on_ticks.append(time.perf_counter() - started)
    on = runtime.frame_timing()
    diag = runtime.bpm.diagnostics()
    off_tick = _tick_stats(off_ticks)
    on_tick = _tick_stats(on_ticks)
    rss_kb = None
    try:
        import psutil

        rss_kb = round(psutil.Process(os.getpid()).memory_info().rss / 1024)
    except Exception:  # noqa: BLE001
        rss_kb = None
    assert off["engine"]["n"] >= 90
    assert on["send"]["n"] >= 90
    assert "send_blocked" in on
    assert "calc_interval" in on
    assert on_tick["p95_ms"] < 80
    print(
        "home_timing",
        {
            "off_tick": off_tick,
            "on_tick": on_tick,
            "on_engine_p95_ms": on["engine"]["p95_ms"],
            "on_send_p95_ms": on["send"]["p95_ms"],
            "command_ms": round(command_ms, 2),
            "analysis_age_s": diag.get("analysis_age_s"),
            "rss_kb": rss_kb,
            "calc_gaps_over_100ms": on["calc_gaps_over_100ms"],
            "send_blocked": on["send_blocked"],
            "note": "calc_interval is the live loop, not sendto",
        },
    )
