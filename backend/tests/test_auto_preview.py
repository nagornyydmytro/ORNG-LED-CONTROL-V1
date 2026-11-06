"""Isolated Auto preview: runtime catalog, canonical frames, live executor untouched."""

from __future__ import annotations

from fastapi.testclient import TestClient

from orng_led.audio.gallery import FEATURED_LOOKS
from orng_led.audio.library import BY_ID, COMPOSITIONS, LOOKS
from orng_led.audio.preview import (
    DEFAULT_LOOK,
    _wanted_preview_combos,
    catalog_payload,
    compatibility,
    pack_render_identity,
    phase_frame_count,
    preview_clip,
    preview_scene_hash,
    render_preview_view,
)
from orng_led.audio.settings import intensity_of
from orng_led.config.io import load_show_config
from orng_led.config.models import FixtureKind
from orng_led.engine.intents import StageIntent
from orng_led.engine.renderer import render_stage
from orng_led.main import create_app
from orng_led.output.mock import MockTransport
from orng_led.simulator.decode import decode_simulator_view
from orng_led.api.runtime import AppRuntime


def test_cloud_catalog_dump_matches_runtime_library() -> None:
    from pathlib import Path
    import json

    dumped = json.loads(
        (Path(__file__).resolve().parents[2] / "cloud" / "src" / "scenes" / "autoCatalog.json").read_text(
            encoding="utf-8"
        )
    )
    live = catalog_payload()
    assert dumped["count"] == live["count"] == len(COMPOSITIONS)
    assert [row["id"] for row in dumped["compositions"]] == [row["id"] for row in live["compositions"]]
    assert dumped["default_look"] == "vertical_wave"


def test_pack_identity_changes_when_render_changes() -> None:
    first = pack_render_identity(
        {"vertical_wave|4|orange|full": {"frames": [{"n": 1, "pars": [{"i": 0.2}]}]}}
    )
    renamed = pack_render_identity(
        {"hit_bars|4|orange|full": {"frames": [{"n": 1, "pars": [{"i": 0.2}]}]}}
    )
    redrawn = pack_render_identity(
        {"vertical_wave|4|orange|full": {"frames": [{"n": 1, "pars": [{"i": 0.9}]}]}}
    )
    assert first != renamed
    assert first != redrawn


def test_preview_dump_marks_available_combos_instead_of_every_mix() -> None:
    wanted = _wanted_preview_combos()
    non_featured = next(look for look in LOOKS if look not in FEATURED_LOOKS)
    intensities = {item[3] for item in wanted if item[0] == non_featured}
    assert intensities == {"full"}
    featured_palettes = {item[2] for item in wanted if item[0] == "vertical_wave"}
    assert len(featured_palettes) > 1
    assert any(item[0] == "hit_bars" for item in wanted)


def test_catalog_is_the_runtime_library() -> None:
    payload = catalog_payload()
    rows = payload["compositions"]
    assert payload["count"] == len(COMPOSITIONS) == len(rows)
    assert payload["count"] != 101 or len(COMPOSITIONS) == 101
    assert [row["id"] for row in rows] == list(LOOKS)
    assert payload["default_look"] == "vertical_wave"
    assert any(row["id"] == "vertical_wave" for row in rows)
    assert BY_ID["vertical_wave"].family == "vertical_wave"


def test_preview_frames_match_canonical_renderer() -> None:
    show = load_show_config()
    clip = preview_clip(
        show,
        "vertical_wave",
        cycle_beats=4.0,
        palette="orange",
        intensity="full",
        bpm=120.0,
        frames=8,
    )
    assert clip["compatible"] is True
    assert clip["transport"] == "none"
    assert clip["playback"] == "renderer"
    assert clip["source"] == "script"
    assert len(clip["frames"]) == 8
    direct = render_preview_view(
        show,
        "vertical_wave",
        beats=0.0,
        cycle_beats=4.0,
        palette="orange",
        intensity="full",
    )
    assert clip["frames"][0] == direct
    from orng_led.audio.library import BY_ID as LIB
    from orng_led.audio.auto_show import build_auto_stage
    from orng_led.audio.preview import _pose

    composition = LIB["vertical_wave"]
    stage = build_auto_stage(
        list(show.patch.fixtures),
        "orange",
        "groove",
        0.0,
        intensity_of("full"),
        look="vertical_wave",
        beats=0.0,
        cycle_beats=4.0,
        placements=show.layout.placements,
        groups=composition.groups,
    )
    frame = render_stage(show, stage, _pose(show))
    view = decode_simulator_view(show, frame).model_dump(mode="json")
    view["blackout_visual"] = False
    assert direct == view


def test_period_and_bpm_change_cycle_duration() -> None:
    show = load_show_config()
    four = preview_clip(show, "vertical_wave", cycle_beats=4, bpm=120, frames=8)
    eight = preview_clip(show, "vertical_wave", cycle_beats=8, bpm=120, frames=8)
    slow = preview_clip(show, "vertical_wave", cycle_beats=4, bpm=60, frames=8)
    assert four["seconds"] == 2.0
    assert eight["seconds"] == 4.0
    assert slow["seconds"] == 4.0
    same_beat = render_preview_view(
        show, "vertical_wave", beats=1.0, cycle_beats=2.0, palette="orange", intensity="full"
    )
    longer = render_preview_view(
        show, "vertical_wave", beats=1.0, cycle_beats=8.0, palette="orange", intensity="full"
    )
    assert same_beat != longer


def test_palette_and_intensity_change_the_frame() -> None:
    show = load_show_config()
    orange = render_preview_view(
        show, "vertical_wave", beats=1.0, cycle_beats=4.0, palette="orange", intensity="full"
    )
    blue = render_preview_view(
        show, "vertical_wave", beats=1.0, cycle_beats=4.0, palette="blue", intensity="full"
    )
    soft = render_preview_view(
        show, "vertical_wave", beats=1.0, cycle_beats=4.0, palette="orange", intensity="soft"
    )
    assert orange != blue
    assert orange != soft
    orange_sum = sum(orange["bars"][0]["segment_r"]) if orange["bars"] else 0
    soft_sum = sum(soft["bars"][0]["segment_r"]) if soft["bars"] else 0
    assert orange["bars"]
    assert any(bar["dimmer"] != soft["bars"][i]["dimmer"] for i, bar in enumerate(orange["bars"])) or orange_sum != soft_sum


def test_incompatible_look_is_explained_not_replaced() -> None:
    show = load_show_config().model_copy(deep=True)
    keep = {fixture.id for fixture in show.patch.fixtures if fixture.kind is FixtureKind.PAR}
    show.patch.fixtures = [fixture for fixture in show.patch.fixtures if fixture.id in keep]
    show.layout.placements = [
        place for place in show.layout.placements if place.fixture_id in keep
    ]
    check = compatibility(show, "vertical_wave")
    assert check["ok"] is False
    assert "wash" in str(check["reason"]).lower() or "не замінено" in str(check["reason"])
    clip = preview_clip(show, "vertical_wave", frames=8)
    assert clip["compatible"] is False
    assert clip["canonical"] is False
    assert clip["frames"] == []
    assert "wash" not in (clip.get("look") or "")


def test_http_preview_does_not_touch_live_executor() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    assert isinstance(runtime.output.transport, MockTransport)
    runtime.engine.set_blackout(True)
    runtime.engine.set_face(False)
    preset = runtime.engine.active_preset_id
    bpm_before = dict(runtime.bpm.status())
    sent = runtime.output.frames_sent
    app = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(app) as client:
        catalog = client.get("/api/auto/catalog")
        assert catalog.status_code == 200
        body = catalog.json()
        assert body["count"] == len(COMPOSITIONS)
        assert body["default_look"] == DEFAULT_LOOK
        assert any(row["id"] == "vertical_wave" for row in body["compositions"])
        before = client.get("/api/state").json()
        response = client.get(
            "/api/auto/preview-clip",
            params={
                "look": "vertical_wave",
                "cycle_beats": 4,
                "palette": "orange",
                "intensity": "full",
                "bpm": 120,
                "frames": 8,
            },
        )
        assert response.status_code == 200
        clip = response.json()
        assert clip["compatible"] is True
        assert clip["transport"] == "none"
        assert any(frame["nonzero_channels"] > 0 for frame in clip["frames"])
        after = client.get("/api/state").json()
        assert after["engine"]["blackout"] is True
        assert after["engine"]["preset_id"] == before["engine"]["preset_id"] == preset
        assert after["engine"]["face_on"] == before["engine"]["face_on"]
        assert after["output"]["armed"] == before["output"]["armed"]
        assert after["output"]["transport"] == "mock"
        assert after["output"]["frames_sent"] == sent
        assert after["bpm"]["enabled"] == bpm_before.get("enabled")
        assert after["bpm"]["locked"] == bpm_before.get("locked")
        blue = client.get(
            "/api/auto/preview-clip",
            params={"look": "vertical_wave", "palette": "blue", "frames": 8, "cycle_beats": 4},
        ).json()
        assert blue["frames"][2] != clip["frames"][2]
        half = client.get(
            "/api/auto/preview-clip",
            params={"look": "vertical_wave", "cycle_beats": 0.5, "bpm": 120, "frames": 8},
        ).json()
        assert half["seconds"] == 0.25
    assert runtime.engine.active_preset_id == preset
    assert runtime.engine.overlays.blackout is True
    assert not isinstance(runtime.output.transport, type(None))
    parked = render_stage(runtime.show, StageIntent(), {})
    assert parked is not None


def test_phase_samples_follow_cycle_not_a_flat_24() -> None:
    assert phase_frame_count(0.5) == 16
    assert phase_frame_count(4) == 48
    assert phase_frame_count(8) == 72
    show = load_show_config()
    clip = preview_clip(show, "vertical_wave", cycle_beats=8, bpm=40, frames=0)
    assert clip["canonical"] is True
    assert len(clip["frames"]) == 72
    assert clip["seconds"] == 12.0
    assert clip["frames"][0] != clip["frames"][36]


def test_empty_frames_are_not_a_successful_renderer_preview() -> None:
    show = load_show_config().model_copy(deep=True)
    keep = {fixture.id for fixture in show.patch.fixtures if fixture.kind is FixtureKind.PAR}
    show.patch.fixtures = [fixture for fixture in show.patch.fixtures if fixture.id in keep]
    show.layout.placements = [place for place in show.layout.placements if place.fixture_id in keep]
    clip = preview_clip(show, "vertical_wave")
    assert clip["frames"] == []
    assert clip["canonical"] is False
    assert clip["compatible"] is False


def test_preview_hash_changes_with_layout_not_presets(tmp_path) -> None:
    import shutil

    from orng_led.config.io import default_scene_dir

    dest = tmp_path / "scene"
    shutil.copytree(default_scene_dir(), dest)
    first = preview_scene_hash(dest)
    (dest / "presets" / "hash_probe.yaml").write_text("id: HASH\nlabel: probe\n", encoding="utf-8")
    assert preview_scene_hash(dest) == first
    layout = dest / "layout.yaml"
    layout.write_text(layout.read_text(encoding="utf-8") + "\n# preview-revision\n", encoding="utf-8")
    assert preview_scene_hash(dest) != first
