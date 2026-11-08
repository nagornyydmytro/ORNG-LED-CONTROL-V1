"""Head-range work must stay off the live frame, and Auto must not rehome.

The tick measurements use the real ``AppRuntime._tick_loop``. Output stays on
Mock, disarmed, and the desk starts in blackout. Motion checks clear blackout
only inside this process.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import threading
import time
from pathlib import Path

import pytest

from orng_led.api.runtime import AppRuntime
from orng_led.audio.head_motion import (
    HeadRig,
    _period,
    motion_loop_beats,
    prepare_head_travel,
    preview_heads,
    target,
)
from orng_led.audio.planner import AutoAction, AutoEpisode
from orng_led.config.models import FixtureKind
from orng_led.engine.clock import FRAME_DT
from orng_led.output.mock import MockTransport
from orng_led.simulator.decode import decode_simulator_view

_GAP_S = 0.10


def _private_runtime(tmp_path: Path) -> AppRuntime:
    root = tmp_path / "config"
    shutil.copytree(Path(os.environ["ORNG_CONFIG_ROOT"]), root)
    runtime = AppRuntime.create(autostart_loop=False, config_dir=root / "scenes" / "orng-bar")
    assert isinstance(runtime.output.transport, MockTransport)
    assert runtime.output.armed is False
    assert runtime.engine.overlays.blackout is True
    return runtime


def _alt_scene(runtime: AppRuntime) -> None:
    source = runtime.config_dir
    dest = source.parent / "orng-alt"
    shutil.copytree(source, dest)
    text = (dest / "scene.yaml").read_text(encoding="utf-8")
    (dest / "scene.yaml").write_text(
        text.replace("scene_id: orng-bar", "scene_id: orng-alt").replace(
            "name: ORNG BAR", "name: ALT"
        ),
        encoding="utf-8",
    )


async def _run_loop(runtime: AppRuntime):
    runtime._running = True
    runtime._shutting_down = False
    runtime._last_wall = None
    task = asyncio.create_task(runtime._tick_loop())
    runtime._loop_task = task
    try:
        yield task
    finally:
        runtime._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def _gaps_since(runtime: AppRuntime, start: int) -> list[float]:
    return [lag + FRAME_DT for lag in list(runtime._lag_samples)[start:]]


def test_same_label_rename_does_not_stall_the_tick_loop(tmp_path: Path) -> None:
    runtime = _private_runtime(tmp_path)
    preset = next(iter(runtime.preset_store.documents.values()))
    calls = {"n": 0}
    real = prepare_head_travel

    def _counted(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    async def body() -> tuple[float, float]:
        import orng_led.api.runtime as runtime_mod

        runtime_mod.prepare_head_travel = _counted  # type: ignore[attr-defined]
        # The live installer imports inside the method. Patch that name too.
        import orng_led.audio.head_motion as motion_mod

        original = motion_mod.prepare_head_travel
        motion_mod.prepare_head_travel = _counted
        try:
            task_cm = _run_loop(runtime)
            task = await task_cm.__anext__()
            try:
                await asyncio.sleep(0.25)
                mark = len(runtime._lag_samples)
                calls["n"] = 0
                started = time.perf_counter()
                runtime.rename_preset(preset.id, preset.label)
                blocked = time.perf_counter() - started
                await asyncio.sleep(0.20)
                gaps = _gaps_since(runtime, mark)
            finally:
                await task_cm.aclose()
        finally:
            motion_mod.prepare_head_travel = original
        return blocked, max(gaps) if gaps else 0.0

    try:
        blocked, gap = asyncio.run(body())
    finally:
        asyncio.run(runtime.shutdown())
    print(f"RENAME_BLOCK {blocked:.4f} RENAME_GAP {gap:.4f} PREPARE_CALLS {calls['n']}")
    assert calls["n"] == 0 and blocked < _GAP_S and gap < _GAP_S, (
        f"calls={calls['n']} blocked={blocked:.4f} gap={gap:.4f}"
    )


def test_motion_edit_and_scene_change_keep_the_frame_and_the_generation(
    tmp_path: Path,
) -> None:
    runtime = _private_runtime(tmp_path)
    _alt_scene(runtime)
    preset = next(iter(runtime.preset_store.documents.values()))
    payload = preset.model_dump(mode="json")
    payload["episodes"][0]["effect"] = (
        "wave" if payload["episodes"][0]["effect"] != "wave" else "pulse"
    )
    hold = threading.Event()
    release = threading.Event()
    prepared = {"n": 0}
    import orng_led.audio.head_motion as motion_mod

    real = motion_mod.prepare_head_travel

    def _slow(show, documents, **kwargs):
        import inspect

        prepared["n"] += 1
        scene_id = kwargs.get("scene_id")
        if (
            scene_id == "orng-bar"
            and not hold.is_set()
            and not release.is_set()
            and getattr(_slow, "armed", False)
        ):
            hold.set()
            release.wait(5)
        accepted = {
            name: kwargs[name]
            for name in inspect.signature(real).parameters
            if name in kwargs
        }
        return real(show, documents, **accepted)

    _slow.armed = False  # type: ignore[attr-defined]

    async def body() -> tuple[float, float, int]:
        motion_mod.prepare_head_travel = _slow
        try:
            task_cm = _run_loop(runtime)
            await task_cm.__anext__()
            try:
                await asyncio.sleep(0.15)
                _slow.armed = True  # type: ignore[attr-defined]
                worker = threading.Thread(
                    target=lambda: runtime.update_preset(preset.id, payload),
                    name="preset-edit",
                    daemon=True,
                )
                worker.start()
                for _ in range(160):
                    if hold.is_set():
                        break
                    await asyncio.sleep(0.05)
                assert hold.is_set(), "motion edit did not reach prepare"
                worker.join(2)
                mark = len(runtime._lag_samples)
                sampled = prepared["n"]
                runtime.activate_scene("orng-alt")
                sampled = prepared["n"] - sampled
                await asyncio.sleep(0.08)
                activate_gap = max(_gaps_since(runtime, mark) or [0.0])
                mark = len(runtime._lag_samples)
                release.set()
                deadline = time.perf_counter() + 4.0
                while time.perf_counter() < deadline:
                    travel = runtime.bpm.head_travel
                    if travel is not None and str(getattr(travel, "scene_id", "")) == "orng-alt":
                        break
                    await asyncio.sleep(0.05)
                return activate_gap, max(_gaps_since(runtime, mark) or [0.0]), sampled
            finally:
                release.set()
                await task_cm.aclose()
        finally:
            motion_mod.prepare_head_travel = real

    try:
        activate_gap, compute_gap, sampled_during_activate = asyncio.run(body())
    finally:
        release.set()
        asyncio.run(runtime.shutdown())
    travel = runtime.bpm.head_travel
    print(
        f"ACTIVATE_GAP {activate_gap:.4f} COMPUTE_GAP {compute_gap:.4f} "
        f"SAMPLED_DURING_ACTIVATE {sampled_during_activate} "
        f"TRAVEL {None if travel is None else travel.scene_id}"
    )
    assert runtime.scene_id == "orng-alt"
    assert runtime.output.armed is False
    assert isinstance(runtime.output.transport, MockTransport)
    assert travel is not None and travel.scene_id == "orng-alt"
    assert sampled_during_activate == 0
    assert compute_gap < _GAP_S


def test_stale_same_scene_calculation_does_not_replace_a_newer_one(tmp_path: Path) -> None:
    runtime = _private_runtime(tmp_path)
    preset = next(iter(runtime.preset_store.documents.values()))
    older = preset.model_dump(mode="json")
    newer = preset.model_dump(mode="json")
    older["episodes"][0]["effect"] = (
        "wave" if older["episodes"][0]["effect"] != "wave" else "pulse"
    )
    newer["episodes"][0]["effect"] = "chase" if older["episodes"][0]["effect"] != "chase" else "static"
    newer["episodes"][0]["speed"] = 0.15
    hold = threading.Event()
    release = threading.Event()
    phase = {"hold_next": True}
    import orng_led.audio.head_motion as motion_mod

    real = motion_mod.prepare_head_travel

    def _slow(show, documents, **kwargs):
        import inspect

        if phase["hold_next"]:
            phase["hold_next"] = False
            hold.set()
            release.wait(5)
        accepted = {
            name: kwargs[name]
            for name in inspect.signature(real).parameters
            if name in kwargs
        }
        return real(show, documents, **accepted)

    motion_mod.prepare_head_travel = _slow
    try:
        worker = threading.Thread(
            target=lambda: runtime.update_preset(preset.id, older),
            name="stale-preset",
            daemon=True,
        )
        worker.start()
        assert hold.wait(8), "older calculation did not start"
        runtime.update_preset(preset.id, newer)
        fresh = runtime.bpm.head_travel
        assert fresh is not None
        fresh_revision = str(fresh.revision)
        release.set()
        worker.join(8)
        late = runtime.bpm.head_travel
        late_revision = None if late is None else str(late.revision)
        print(f"FRESH {fresh_revision} LATE {late_revision}")
        assert late is not None
        assert late_revision == fresh_revision
    finally:
        release.set()
        motion_mod.prepare_head_travel = real
        asyncio.run(runtime.shutdown())


def test_patch_profile_and_reload_replace_the_snapshot(tmp_path: Path) -> None:
    runtime = _private_runtime(tmp_path)
    try:
        first = runtime.bpm.head_travel
        assert first is not None
        patch = runtime.show.patch.model_dump(mode="json")
        beam = next(item for item in patch["fixtures"] if item["kind"] == "beam")
        beam["spatial"]["pan_min"] = min(0.2, float(beam["spatial"]["pan_max"]) - 0.05)
        runtime.save_patch(patch)
        after_patch = runtime.bpm.head_travel
        assert after_patch is not None
        assert after_patch.revision != first.revision

        profile_id = beam["profile_id"]
        profile = runtime.show.profiles[profile_id].model_dump(mode="json")
        profile["channels"][0]["fixed_value"] = 0
        runtime.save_profile(profile)
        after_profile = runtime.bpm.head_travel
        assert after_profile is not None
        assert after_profile.revision != after_patch.revision

        stale = after_profile
        runtime.bpm.head_travel = first
        runtime.reload_from_disk()
        restored = runtime.bpm.head_travel
        assert restored is not None
        assert restored.revision != first.revision
        assert restored.revision == stale.revision or restored.scene_id == runtime.scene_id
    finally:
        asyncio.run(runtime.shutdown())


def _confirm_beams(runtime: AppRuntime) -> None:
    fixtures = []
    for fixture in runtime.show.patch.fixtures:
        if fixture.kind is FixtureKind.BEAM:
            fixture = fixture.model_copy(
                update={
                    "spatial": fixture.spatial.model_copy(
                        update={"beam_calibration_confirmed": True}
                    )
                }
            )
        fixtures.append(fixture)
    show = runtime.show.model_copy(
        update={"patch": runtime.show.patch.model_copy(update={"fixtures": fixtures})}
    )
    runtime.show = show
    runtime.engine.show = show


def _pin_oval(runtime: AppRuntime) -> None:
    episode = AutoEpisode(
        ident=1,
        look="heads_only_oval",
        groups=("beam",),
        theme="heads",
        intensity=0.85,
        start_beat=0.0,
        duration_beats=64,
        actions=(
            AutoAction(
                role="picture",
                look="heads_only_oval",
                cycle_beats=8.0,
                groups=("beam",),
            ),
        ),
        transition="soft",
        effects=(),
        end_early=(),
        reason="phrase",
        section="groove",
        character="smooth",
        cast="heads",
    )
    runtime.bpm.planner.advance = lambda *args, **kwargs: episode  # type: ignore[method-assign]
    follower = runtime.bpm.follower
    follower.bpm = 120.0
    follower.locked = True
    follower._started = True
    follower._beats = 0.0
    follower._at = runtime.bpm._now
    follower._fresh_until = runtime.bpm._now + 120.0
    runtime.bpm.enabled = True
    runtime.bpm.scope = "auto"
    runtime.bpm.cast = "heads"


def _decoded(runtime: AppRuntime, fixture_id: str, frame: list[int]) -> float:
    view = decode_simulator_view(runtime.show, frame)
    beam = next(item for item in view.beams if item.id == fixture_id)
    return float(beam.pan)


def _run_interruption(runtime: AppRuntime, *, sweep: bool) -> dict[str, float]:
    _confirm_beams(runtime)
    _pin_oval(runtime)
    runtime.engine.set_blackout(False)
    fixture = next(item for item in runtime.show.patch.fixtures if item.id == "beam_left")
    home = float(fixture.spatial.home_pan)
    limit = float(fixture.spatial.max_pan_speed)
    pans: list[float] = []
    for _ in range(45):
        snap = runtime.tick(FRAME_DT)
        pans.append(_decoded(runtime, fixture.id, snap.frame))
    before = pans[-1]
    pose_before = runtime.bpm.heads.pose(fixture.id)
    other = HeadRig()
    preview_heads(
        runtime.show,
        runtime.engine.auto_base,
        beats=4.0,
        section="groove",
        look="heads_only_oval",
        rig=other,
        travel=runtime.bpm.head_travel,
    )
    assert runtime.bpm.heads.pose(fixture.id) == pose_before
    if sweep:
        runtime.engine.overlays.sweep_held = True
    else:
        runtime.engine.set_blackout(True)
    held: list[float] = []
    hold_step = 0.0
    previous = before
    for _ in range(12):
        snap = runtime.tick(FRAME_DT)
        pan = _decoded(runtime, fixture.id, snap.frame)
        hold_step = max(hold_step, abs(pan - previous))
        previous = pan
        held.append(pan)
    runtime.engine.overlays.sweep_held = False
    runtime.engine.set_blackout(False)
    after: list[float] = []
    for _ in range(20):
        snap = runtime.tick(FRAME_DT)
        after.append(_decoded(runtime, fixture.id, snap.frame))
    runtime.engine.set_blackout(True)
    steps = [abs(after[0] - held[-1])]
    steps.extend(abs(b - a) for a, b in zip(after, after[1:]))
    return {
        "home": home,
        "before": before,
        "held": held[-1],
        "hold_step": hold_step,
        "after": after[-1],
        "max_step": max(steps),
        "limit": limit * FRAME_DT + 0.012,
        "pose": -1.0 if runtime.bpm.heads.pose(fixture.id) is None else 1.0,
    }


def test_blackout_and_sweep_do_not_restart_from_home(tmp_path: Path) -> None:
    runtime = _private_runtime(tmp_path)
    try:
        blackout = _run_interruption(runtime, sweep=False)
    finally:
        asyncio.run(runtime.shutdown())
    sweep_root = tmp_path / "sweep"
    sweep_root.mkdir()
    sweep_runtime = _private_runtime(sweep_root)
    try:
        sweep = _run_interruption(sweep_runtime, sweep=True)
    finally:
        asyncio.run(sweep_runtime.shutdown())
    print(f"BLACKOUT {blackout}")
    print(f"SWEEP {sweep}")
    for name, row in (("blackout", blackout), ("sweep", sweep)):
        assert abs(row["before"] - row["home"]) > 0.05, name
        assert row["hold_step"] <= row["limit"], name
        if name == "blackout":
            assert abs(row["held"] - row["before"]) <= row["limit"], name
        assert row["max_step"] <= row["limit"], name
        assert abs(row["after"] - row["home"]) > abs(row["before"] - row["home"]) - 0.02, name
        assert row["pose"] > 0, name
        assert row["after"] != pytest.approx(row["home"], abs=0.02)


def test_head_periods_stay_on_two_four_eight_and_changes_do_not_jerk() -> None:
    assert _period("heads_beat_pass", "groove") == 2.0
    assert _period("heads_rise_pulse", "groove") == 4.0
    assert _period("heads_only_oval", "groove") >= 8.0
    assert motion_loop_beats("heads_beat_pass", 0.5) >= 2.0
    assert motion_loop_beats("heads_beat_pass", 0.25) >= 2.0
    span = (0.08, 0.92)
    start = target(0.0, -1.0, "groove", look="heads_beat_pass", pan_span=span, tilt_span=span)
    half = target(0.5, -1.0, "groove", look="heads_beat_pass", pan_span=span, tilt_span=span)
    assert start[0] is not None and half[0] is not None
    assert abs(float(start[0]) - float(half[0])) > 1e-4
    left = target(1.0, -1.0, "groove", look="heads_only_oval", pan_span=span, tilt_span=span)
    right = target(1.0, 1.0, "groove", look="heads_only_oval", pan_span=span, tilt_span=span)
    assert left[0] is not None and right[0] is not None
    assert float(left[0]) + float(right[0]) == pytest.approx(span[0] + span[1])

    from orng_led.audio.auto_show import build_auto_stage
    from orng_led.config.io import load_show_config

    show = load_show_config()
    rig = HeadRig()
    stage = build_auto_stage(
        list(show.patch.fixtures),
        "orange",
        "groove",
        0.0,
        0.8,
        look="vertical_wave",
        beats=1.0,
        cycle_beats=4.0,
        placements=show.layout.placements,
        groups=("bar",),
    )
    bars = {
        fixture.id: stage.fixtures.get(fixture.id)
        for fixture in show.patch.fixtures
        if fixture.kind is FixtureKind.BAR
    }
    painted = preview_heads(
        show,
        stage,
        beats=1.0,
        section="groove",
        look="vertical_wave",
        rig=rig,
    )
    for fixture_id, intent in bars.items():
        assert painted.fixtures.get(fixture_id) == intent

    from orng_led.audio.head_motion import HeadTravel

    beam_id = next(fixture.id for fixture in show.patch.fixtures if fixture.kind is FixtureKind.BEAM)
    travel = HeadTravel(
        scene_id="period",
        revision="period",
        spans={beam_id: (0.08, 0.92, 0.24, 0.76)},
    )
    rig = HeadRig()
    beats = 0.0
    previous = None
    look = "heads_only_oval"
    bpm = 120.0
    for index in range(40):
        if index == 15:
            look = "heads_only_fan"
        elif index == 28:
            bpm = 90.0
            look = "heads_beat_pass"
        beats += (bpm / 60.0) * FRAME_DT
        picture = build_auto_stage(
            list(show.patch.fixtures),
            "orange",
            "groove",
            0.0,
            0.8,
            look=look,
            beats=beats,
            cycle_beats=8.0,
            placements=show.layout.placements,
            groups=("beam",),
        )
        preview_heads(
            show,
            picture,
            beats=beats,
            section="groove",
            look=look,
            beat_period_s=60.0 / bpm,
            rig=rig,
            travel=travel,
        )
        pose = rig.pose(beam_id)
        assert pose is not None
        if previous is not None:
            assert abs(pose[0] - previous[0]) <= 0.04 * ((bpm / 60.0) * FRAME_DT) + 1e-6
            assert abs(pose[1] - previous[1]) <= 0.025 * ((bpm / 60.0) * FRAME_DT) + 1e-6
        previous = pose
