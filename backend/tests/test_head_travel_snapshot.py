"""Head ranges belong to one scene revision, prepared off the frame."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from orng_led.audio.head_motion import bounds_for
from orng_led.audio.service import BpmService
from orng_led.config.io import load_show_config
from orng_led.presets.models import EpisodeCard, PresetDocument
from orng_led.presets.io import save_preset


def _spatial() -> SimpleNamespace:
    return SimpleNamespace(
        pan_min=0.0,
        pan_max=1.0,
        home_pan=0.5,
        tilt_min=0.0,
        tilt_max=1.0,
        home_tilt=0.5,
        max_pan_speed=0.35,
        max_tilt_speed=0.25,
        side="right",
        pan_offset=0.0,
        tilt_offset=0.0,
        pan_invert=False,
        tilt_invert=False,
    )


def _document(effect: str) -> PresetDocument:
    return PresetDocument(
        id="P01",
        label="same name",
        episodes=[
            EpisodeCard(
                id="ep1",
                duration_s=8.0,
                groups=["all_rear"],
                palette="warm_orange",
                effect=effect,
                speed=0.5,
                intensity=1.0,
                transition="soft",
            )
        ],
    )


def _scene(root: Path, effect: str) -> Path:
    directory = root / "presets"
    directory.mkdir(parents=True)
    save_preset(directory / "P01.yaml", _document(effect))
    os.utime(directory / "P01.yaml", (1_700_000_000, 1_700_000_000))
    return directory


def test_unknown_head_does_not_borrow_another_span() -> None:
    from orng_led.audio.head_motion import prepare_head_travel

    show = load_show_config()
    travel = prepare_head_travel(
        show, {"P01": _document("wave")}, scene_id="orng-bar", revision="wave"
    )
    box = bounds_for(
        SimpleNamespace(id="beam_not_in_this_scene", spatial=_spatial()),
        travel=travel,
    )
    assert box["pan"]["still"] is True
    assert "unknown" in str(box["pan"]["reason"])
    assert "beam_right" in travel.spans


def test_two_scenes_with_the_same_names_keep_their_own_motion(tmp_path) -> None:
    from orng_led.audio.head_motion import prepare_head_travel

    _scene(tmp_path / "wide", "wave")
    _scene(tmp_path / "narrow", "glow")
    show = load_show_config()
    wide = prepare_head_travel(
        show, {"P01": _document("wave")}, scene_id="room-wide", revision="same-name"
    )
    narrow = prepare_head_travel(
        show, {"P01": _document("glow")}, scene_id="room-narrow", revision="same-name"
    )
    fixture = SimpleNamespace(id="beam_right", spatial=_spatial())
    first = bounds_for(fixture, travel=wide)
    second = bounds_for(fixture, travel=narrow)
    assert wide.scene_id != narrow.scene_id
    assert first["pan"]["still"] is False and second["pan"]["still"] is False
    assert float(first["pan"]["hi"]) - float(first["pan"]["lo"]) > (
        float(second["pan"]["hi"]) - float(second["pan"]["lo"])
    ) + 0.2


def test_cold_look_does_not_scan_or_evaluate_presets(tmp_path, monkeypatch) -> None:
    from orng_led.presets import io as preset_io
    from orng_led.presets.program import YamlPresetProgram

    from orng_led.audio.head_motion import prepare_head_travel

    show = load_show_config()
    ready = prepare_head_travel(
        show, {"P01": _document("wave")}, scene_id="orng-bar", revision="ready"
    )
    service = BpmService(tmp_path, "orng-bar")
    service.configure(enabled=True, scope="auto", cast="heads", accent=0.0)
    service.follower.locked = True
    service.follower.bpm = 120.0

    def banned(*_args, **_kwargs):
        raise AssertionError("frame path scanned presets")

    real_glob = Path.glob
    real_stat = Path.stat

    def guarded_glob(self, pattern):
        if str(pattern).endswith(".yaml"):
            raise AssertionError("frame path globbed presets")
        return real_glob(self, pattern)

    def guarded_stat(self, *args, **kwargs):
        if self.suffix == ".yaml":
            raise AssertionError("frame path statted a preset")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "glob", guarded_glob)
    monkeypatch.setattr(Path, "stat", guarded_stat)
    monkeypatch.setattr(preset_io, "load_presets_dir", banned)
    monkeypatch.setattr(YamlPresetProgram, "evaluate", banned)
    stage = service.look(list(show.patch.fixtures), show.layout.placements)
    beam = stage.fixtures.get("beam_right")
    assert beam is None or beam.pan is None
    service.install_head_travel(ready)
    service.look(list(show.patch.fixtures), show.layout.placements)


def test_preset_update_changes_only_the_installed_scene(tmp_path) -> None:
    from orng_led.audio.head_motion import prepare_head_travel

    show = load_show_config()
    first = prepare_head_travel(
        show, {"P01": _document("glow")}, scene_id="orng-bar", revision="glow-1"
    )
    second = prepare_head_travel(
        show, {"P01": _document("wave")}, scene_id="orng-bar", revision="wave-2"
    )
    other = prepare_head_travel(
        show, {"P01": _document("glow")}, scene_id="other-room", revision="glow-1"
    )
    fixture = SimpleNamespace(id="beam_right", spatial=_spatial())
    glow = bounds_for(fixture, travel=first)
    wave = bounds_for(fixture, travel=second)
    held = bounds_for(fixture, travel=other)
    assert first.revision != second.revision
    assert float(wave["pan"]["hi"]) - float(wave["pan"]["lo"]) > (
        float(glow["pan"]["hi"]) - float(glow["pan"]["lo"])
    ) + 0.2
    assert held["pan"]["lo"] == pytest.approx(glow["pan"]["lo"])
    assert held["pan"]["hi"] == pytest.approx(glow["pan"]["hi"])
    service = BpmService(tmp_path, "orng-bar")
    service.install_head_travel(first)
    service.install_head_travel(second)
    assert service.head_travel.revision == "wave-2"
    assert service.head_travel.scene_id == "orng-bar"
