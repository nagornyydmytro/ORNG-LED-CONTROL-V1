"""Multi-scene Stage 1: discovery, switch safety, holds, linux keyboard stubs."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.config import default_config_dir, default_scene_dir, load_show_config
from orng_led.config.io import load_show_config_from_scene_dir
from orng_led.config.models import ConfigError
from orng_led.input.adapters import KeyboardInputAdapter
from orng_led.input.contract import InputAction, InputEvent, InputSource
from orng_led.input.linux_keyboard import normalize_linux_key_code
from orng_led.main import create_app
from orng_led.scenes.store import SceneStore, is_safe_scene_id

FIXTURE_PRE = Path(__file__).resolve().parent / "fixtures" / "pre_scene_orng_bar"


def _copy_orng_bar(target: Path) -> Path:
    shutil.copytree(default_scene_dir(), target)
    return target


def _write_min_scene(root: Path, scene_id: str, *, name: str | None = None) -> Path:
    """Clone ORNG BAR into a sibling scene folder under a temp config root."""
    scenes = root / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    dest = scenes / scene_id
    shutil.copytree(default_scene_dir(), dest)
    (dest / "scene.yaml").write_text(
        yaml.safe_dump(
            {"schema_version": 1, "scene_id": scene_id, "name": name or scene_id.upper()},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return dest


def test_discover_and_load_orng_bar() -> None:
    store = SceneStore(default_config_dir())
    scenes = store.list_scenes()
    ok = [s for s in scenes if s.error is None]
    assert any(s.scene_id == "orng-bar" for s in ok)
    bundle = store.load_scene("orng-bar")
    assert bundle.manifest.scene_id == "orng-bar"
    assert bundle.manifest.name == "ORNG BAR"
    assert len(bundle.show.patch.fixtures) == 12
    assert list(bundle.show.app.pad_presets) == [
        "A01",
        "A02",
        "Mix",
        "P01",
        "P02",
        "P03",
        "P04",
        "P05",
        "P06",
    ]
    from orng_led.scenes.models import SceneControls

    assert "pad_presets" not in SceneControls.model_fields
    assert any(b.code == "KeyQ" for b in bundle.controls.keyboard)


def test_equivalence_vs_pre_scene_fixture() -> None:
    live = load_show_config_from_scene_dir(default_scene_dir())
    baseline = load_show_config_from_scene_dir(FIXTURE_PRE)
    assert live.app.model_dump() == baseline.app.model_dump()
    assert live.patch.model_dump() == baseline.patch.model_dump()
    assert live.layout.model_dump() == baseline.layout.model_dump()
    assert {k: v.model_dump() for k, v in live.profiles.items()} == {
        k: v.model_dump() for k, v in baseline.profiles.items()
    }
    live_presets = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted((default_scene_dir() / "presets").glob("*.yaml"))
    }
    baseline_presets = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted((FIXTURE_PRE / "presets").glob("*.yaml"))
    }
    assert live_presets == baseline_presets


def test_load_show_config_default_resolves_active_scene() -> None:
    show = load_show_config()
    assert len(show.patch.fixtures) == 12
    assert (default_scene_dir() / "app.yaml").is_file()


def test_path_traversal_scene_id_rejected() -> None:
    assert is_safe_scene_id("orng-bar") is True
    assert is_safe_scene_id("../etc") is False
    assert is_safe_scene_id("a/b") is False
    assert is_safe_scene_id("a\\b") is False
    store = SceneStore(default_config_dir())
    with pytest.raises(ConfigError):
        store.load_scene("../orng-bar")


def test_two_temp_scenes_isolated(tmp_path: Path) -> None:
    root = tmp_path / "config"
    a = _write_min_scene(root, "scene-a", name="Scene A")
    b = _write_min_scene(root, "scene-b", name="Scene B")
    # Distinct pad presets so switch is observable.
    app_a = yaml.safe_load((a / "app.yaml").read_text(encoding="utf-8"))
    app_b = yaml.safe_load((b / "app.yaml").read_text(encoding="utf-8"))
    app_a["pad_presets"] = ["A01", "A02", "A03", "P01", "P02", "P03", "P04", "P05", "P06"]
    app_b["pad_presets"] = ["P01", "P02", "P03", "P04", "P05", "P06", "P07", "P08", "P09"]
    (a / "app.yaml").write_text(yaml.safe_dump(app_a, sort_keys=False), encoding="utf-8")
    (b / "app.yaml").write_text(yaml.safe_dump(app_b, sort_keys=False), encoding="utf-8")
    controls_b = yaml.safe_load((b / "controls.yaml").read_text(encoding="utf-8"))
    controls_b.pop("pad_presets", None)
    # Remap KeyQ to a different action flavour for keyboard mapping check.
    for binding in controls_b["keyboard"]:
        if binding.get("code") == "KeyQ":
            binding["pad_slot"] = 1
    (b / "controls.yaml").write_text(yaml.safe_dump(controls_b, sort_keys=False), encoding="utf-8")
    (root / "runtime.yaml").write_text(
        "schema_version: 1\nactive_scene_id: scene-a\n",
        encoding="utf-8",
    )

    runtime = AppRuntime.create(autostart_loop=False, config_dir=root)
    assert runtime.scene_id == "scene-a"
    assert runtime.show.app.pad_presets[0] == "A01"

    listed = runtime.list_scenes()
    ids = {item["scene_id"] for item in listed if not item["error"]}
    assert ids == {"scene-a", "scene-b"}

    state, replay = runtime.activate_scene("scene-b")
    assert replay is False
    assert state.scene_id == "scene-b"
    assert runtime.show.app.pad_presets[0] == "P01"
    assert runtime.engine.overlays.blackout is True
    assert runtime.output.status().armed is False
    # Keyboard mapping follows active scene controls.
    adapter = KeyboardInputAdapter.from_controls(runtime.scene_controls)
    events = adapter.handle_raw({"code": "KeyQ", "type": "keydown", "repeat": False})
    assert events and events[0].pad_slot == 1


def test_switch_blackout_and_disarm(tmp_path: Path) -> None:
    root = tmp_path / "config"
    _write_min_scene(root, "scene-a")
    _write_min_scene(root, "scene-b")
    (root / "runtime.yaml").write_text(
        "schema_version: 1\nactive_scene_id: scene-a\n",
        encoding="utf-8",
    )
    runtime = AppRuntime.create(autostart_loop=False, config_dir=root)
    runtime.engine.set_blackout(False)
    runtime.output.armed = True
    runtime.activate_scene("scene-b")
    assert runtime.engine.overlays.blackout is True
    assert runtime.output.status().armed is False


def test_activate_scene_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "config"
    _write_min_scene(root, "scene-a")
    _write_min_scene(root, "scene-b")
    (root / "runtime.yaml").write_text(
        "schema_version: 1\nactive_scene_id: scene-a\n",
        encoding="utf-8",
    )
    runtime = AppRuntime.create(autostart_loop=False, config_dir=root)
    state1, replay1 = runtime.activate_scene("scene-b", client_command_id="switch-1")
    assert replay1 is False
    seq1 = state1.sequence
    output1 = runtime.output
    state2, replay2 = runtime.activate_scene("scene-b", client_command_id="switch-1")
    assert replay2 is True
    assert state2.sequence == seq1
    assert runtime.output is output1


def test_invalid_scene_does_not_replace(tmp_path: Path) -> None:
    root = tmp_path / "config"
    _write_min_scene(root, "scene-a")
    bad = _write_min_scene(root, "scene-bad")
    (bad / "patch.yaml").write_text("schema_version: 1\nfixtures: []\n", encoding="utf-8")
    (root / "runtime.yaml").write_text(
        "schema_version: 1\nactive_scene_id: scene-a\n",
        encoding="utf-8",
    )
    runtime = AppRuntime.create(autostart_loop=False, config_dir=root)
    before_patch = runtime.show.patch.model_dump()
    with pytest.raises(ConfigError):
        runtime.activate_scene("scene-bad")
    assert runtime.scene_id == "scene-a"
    assert runtime.show.patch.model_dump() == before_patch
    assert runtime.scene_error


def test_no_old_frame_through_new_patch(tmp_path: Path) -> None:
    root = tmp_path / "config"
    a = _write_min_scene(root, "scene-a")
    b = _write_min_scene(root, "scene-b")
    (root / "runtime.yaml").write_text(
        "schema_version: 1\nactive_scene_id: scene-a\n",
        encoding="utf-8",
    )
    runtime = AppRuntime.create(autostart_loop=False, config_dir=root)
    runtime.engine.set_blackout(False)
    runtime.apply_select_preset("A01")
    old_output = runtime.output
    old_engine = runtime.engine
    runtime.activate_scene("scene-b")
    assert runtime.output is not old_output
    assert runtime.engine is not old_engine
    assert runtime.config_dir == b
    assert runtime.config_dir != a
    # New controller starts safe; blackout on.
    assert runtime.engine.overlays.blackout is True
    assert sum(runtime.build_state().frame) == 0 or runtime.build_state().output.armed is False


def test_source_scoped_holds_two_sessions(tmp_path: Path) -> None:
    scene = _copy_orng_bar(tmp_path / "scene")
    runtime = AppRuntime.create(autostart_loop=False, config_dir=scene)
    dispatcher = runtime.input_dispatcher
    assert dispatcher is not None

    press_a = InputEvent(
        action=InputAction.STROBE_PRESS,
        source=InputSource.UI,
        edge="press",
        source_id="web:a",
    )
    press_b = InputEvent(
        action=InputAction.STROBE_PRESS,
        source=InputSource.UI,
        edge="press",
        source_id="web:b",
    )
    assert dispatcher.dispatch(press_a).accepted is True
    assert runtime.engine.overlays.strobe_held is True
    assert dispatcher.dispatch(press_b).accepted is True
    assert runtime.engine.overlays.strobe_held is True

    release_a = InputEvent(
        action=InputAction.STROBE_RELEASE,
        source=InputSource.UI,
        edge="release",
        source_id="web:a",
    )
    assert dispatcher.dispatch(release_a).accepted is True
    assert runtime.engine.overlays.strobe_held is True  # b still holding

    runtime.on_ws_disconnect(source_id="web:a")
    assert runtime.engine.overlays.strobe_held is True

    runtime.on_ws_disconnect(source_id="web:b")
    assert runtime.engine.overlays.strobe_held is False


def test_ws_disconnect_does_not_clear_linux_hold(tmp_path: Path) -> None:
    scene = _copy_orng_bar(tmp_path / "scene")
    runtime = AppRuntime.create(autostart_loop=False, config_dir=scene)
    dispatcher = runtime.input_dispatcher
    assert dispatcher is not None
    linux = InputEvent(
        action=InputAction.STROBE_PRESS,
        source=InputSource.KEYBOARD,
        edge="press",
        source_id="linux-keyboard:event0",
    )
    assert dispatcher.dispatch(linux).accepted is True
    runtime.on_ws_disconnect(source_id="web:browser")
    assert runtime.engine.overlays.strobe_held is True
    dispatcher.force_release_source("linux-keyboard:event0")
    assert runtime.engine.overlays.strobe_held is False


def test_keyboard_unplug_frees_only_its_holds(tmp_path: Path) -> None:
    scene = _copy_orng_bar(tmp_path / "scene")
    runtime = AppRuntime.create(autostart_loop=False, config_dir=scene)
    dispatcher = runtime.input_dispatcher
    assert dispatcher is not None
    dispatcher.dispatch(
        InputEvent(
            action=InputAction.STROBE_PRESS,
            source=InputSource.KEYBOARD,
            edge="press",
            source_id="linux-keyboard:a",
        )
    )
    dispatcher.dispatch(
        InputEvent(
            action=InputAction.SWEEP_PRESS,
            source=InputSource.UI,
            edge="press",
            source_id="web:ui",
        )
    )
    assert runtime.engine.overlays.strobe_held is True
    assert runtime.engine.overlays.sweep_held is True
    dispatcher.force_release_source("linux-keyboard:a")
    assert runtime.engine.overlays.strobe_held is False
    assert runtime.engine.overlays.sweep_held is True


def test_shutdown_frees_all_holds(tmp_path: Path) -> None:
    import asyncio

    scene = _copy_orng_bar(tmp_path / "scene")
    runtime = AppRuntime.create(autostart_loop=False, config_dir=scene)
    dispatcher = runtime.input_dispatcher
    assert dispatcher is not None
    dispatcher.dispatch(
        InputEvent(
            action=InputAction.STROBE_PRESS,
            source=InputSource.UI,
            edge="press",
            source_id="web:1",
        )
    )
    asyncio.run(runtime.shutdown())
    assert runtime.engine.overlays.strobe_held is False
    assert dispatcher.holds.any_held("strobe") is False


def test_linux_key_normalization_without_device() -> None:
    from orng_led.input.linux_keyboard import pick_linux_key_name

    assert normalize_linux_key_code("KEY_Q") == "KeyQ"
    assert normalize_linux_key_code("KEY_SPACE") == "Space"
    assert normalize_linux_key_code("KEY_EQUAL") == "Equal"
    assert normalize_linux_key_code("KEY_MINUS") == "Minus"
    assert normalize_linux_key_code("KEY_KPPLUS") == "NumpadAdd"
    assert normalize_linux_key_code("KEY_KPMINUS") == "NumpadSubtract"
    assert normalize_linux_key_code("KEY_MUTE") == "AudioVolumeMute"
    assert normalize_linux_key_code("KEY_UNKNOWN") is None
    # CH57x Mute arrives as an alias tuple — prefer KEY_MUTE over KEY_MIN_INTERESTING.
    assert pick_linux_key_name(("KEY_MIN_INTERESTING", "KEY_MUTE")) == "KEY_MUTE"
    assert normalize_linux_key_code(pick_linux_key_name(("KEY_MIN_INTERESTING", "KEY_MUTE")) or "") == (
        "AudioVolumeMute"
    )


def test_equal_key_maps_to_zoom_up(tmp_path: Path) -> None:
    scene = _copy_orng_bar(tmp_path / "scene")
    runtime = AppRuntime.create(autostart_loop=False, config_dir=scene)
    adapter = KeyboardInputAdapter.from_controls(runtime.scene_controls)
    events = adapter.handle_raw({"code": "Equal", "type": "keydown", "repeat": False})
    assert events and events[0].action is InputAction.ZOOM_UP
    for code in ("Minus", "NumpadAdd", "NumpadSubtract"):
        mapped = adapter.handle_raw({"code": code, "type": "keydown", "repeat": False})
        assert mapped, code


def test_import_backend_without_linux_deps() -> None:
    # Package import must succeed on Win/mac without evdev installed.
    import sys

    import orng_led.input.linux_keyboard as lk

    assert hasattr(lk, "normalize_linux_key_code")
    if not sys.platform.startswith("linux"):
        assert lk.linux_keyboard_enabled() is False
        assert lk.evdev_available() is False


def test_api_scenes_and_input_status(tmp_path: Path) -> None:
    root = tmp_path / "config"
    _write_min_scene(root, "orng-bar", name="ORNG BAR")
    (root / "runtime.yaml").write_text(
        "schema_version: 1\nactive_scene_id: orng-bar\n",
        encoding="utf-8",
    )
    runtime = AppRuntime.create(autostart_loop=False, config_dir=root)
    app = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(app) as client:
        scenes = client.get("/api/scenes").json()
        assert any(s["scene_id"] == "orng-bar" and not s["error"] for s in scenes["scenes"])
        active = client.get("/api/scenes/active").json()
        assert active["scene_id"] == "orng-bar"
        health = client.get("/api/health").json()
        assert health["scene_id"] == "orng-bar"
        status = client.get("/api/input/status").json()
        assert "linux_keyboard" in status
        assert "enabled" in status["linux_keyboard"]


def test_corrupt_scene_listed_with_error(tmp_path: Path) -> None:
    root = tmp_path / "config"
    _write_min_scene(root, "good", name="Good")
    bad = root / "scenes" / "broken"
    bad.mkdir(parents=True)
    (bad / "scene.yaml").write_text(
        "schema_version: 1\nscene_id: broken\nname: Broken\n",
        encoding="utf-8",
    )
    # Missing app.yaml → corrupt.
    store = SceneStore(root)
    listed = {s.scene_id: s for s in store.list_scenes()}
    assert listed["good"].error is None
    assert listed["broken"].error


def test_ws_and_http_share_source_id_releases_strobe_on_disconnect(tmp_path: Path) -> None:
    root = tmp_path / "config"
    _write_min_scene(root, "orng-bar", name="ORNG BAR")
    (root / "runtime.yaml").write_text(
        "schema_version: 1\nactive_scene_id: orng-bar\n",
        encoding="utf-8",
    )
    runtime = AppRuntime.create(autostart_loop=False, config_dir=root)
    app = create_app(runtime=runtime, autostart_loop=False)
    tab = "web:test-tab"
    other = "web:other-tab"
    linux = "linux-keyboard:event0"
    with TestClient(app) as client:
        with client.websocket_connect(f"/api/ws?source_id={tab}") as ws:
            hello = ws.receive_json()
            assert hello["source_id"] == tab
            ack = client.post(
                "/api/input/button",
                json={
                    "button_id": 13,
                    "edge": "press",
                    "source": "ui",
                    "source_id": tab,
                },
            ).json()
            assert ack["accepted"] is True
            ws.receive_json()  # broadcast
            assert runtime.engine.overlays.strobe_held is True
            # Second tab + linux holds must survive this tab's disconnect.
            assert (
                client.post(
                    "/api/input/button",
                    json={
                        "button_id": 13,
                        "edge": "press",
                        "source": "ui",
                        "source_id": other,
                    },
                ).json()["accepted"]
                is True
            )
            assert runtime.input_dispatcher is not None
            runtime.input_dispatcher.dispatch(
                InputEvent(
                    action=InputAction.STROBE_PRESS,
                    source=InputSource.KEYBOARD,
                    edge="press",
                    source_id=linux,
                )
            )
        # Tab WS closed → only that source released; others remain.
        assert runtime.engine.overlays.strobe_held is True
        assert runtime.input_dispatcher.holds.is_held_by("strobe", other)
        assert runtime.input_dispatcher.holds.is_held_by("strobe", linux)
        assert not runtime.input_dispatcher.holds.is_held_by("strobe", tab)


def test_sweep_source_scoped_via_input_action(tmp_path: Path) -> None:
    root = tmp_path / "config"
    _write_min_scene(root, "orng-bar", name="ORNG BAR")
    (root / "runtime.yaml").write_text(
        "schema_version: 1\nactive_scene_id: orng-bar\n",
        encoding="utf-8",
    )
    runtime = AppRuntime.create(autostart_loop=False, config_dir=root)
    app = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(app) as client:
        a = "web:sweep-a"
        b = "web:sweep-b"
        assert (
            client.post(
                "/api/input/action",
                json={"action": "sweep_press", "edge": "press", "source_id": a},
            ).json()["accepted"]
            is True
        )
        assert (
            client.post(
                "/api/input/action",
                json={"action": "sweep_press", "edge": "press", "source_id": b},
            ).json()["accepted"]
            is True
        )
        assert runtime.engine.overlays.sweep_held is True
        assert (
            client.post(
                "/api/input/action",
                json={"action": "sweep_release", "edge": "release", "source_id": a},
            ).json()["accepted"]
            is True
        )
        assert runtime.engine.overlays.sweep_held is True
        with client.websocket_connect(f"/api/ws?source_id={b}") as ws:
            ws.receive_json()
        assert runtime.engine.overlays.sweep_held is False

        # Vertical + horizontal handover across sources.
        assert (
            client.post(
                "/api/input/action",
                json={
                    "action": "sweep_press",
                    "edge": "press",
                    "source_id": a,
                },
            ).json()["accepted"]
            is True
        )
        assert (
            client.post(
                "/api/input/action",
                json={
                    "action": "vertical_sweep_press",
                    "edge": "press",
                    "source_id": b,
                },
            ).json()["accepted"]
            is True
        )
        assert runtime.engine.overlays.sweep_held is True
        assert (
            client.post(
                "/api/input/action",
                json={"action": "sweep_release", "edge": "release", "source_id": a},
            ).json()["accepted"]
            is True
        )
        assert runtime.engine.overlays.sweep_held is True
        # Linux hold survives browser disconnect.
        linux = "linux-keyboard:evt1"
        runtime.input_dispatcher.dispatch(
            InputEvent(
                action=InputAction.SWEEP_PRESS,
                source=InputSource.KEYBOARD,
                edge="press",
                source_id=linux,
            )
        )
        with client.websocket_connect(f"/api/ws?source_id={b}") as ws:
            ws.receive_json()
        assert runtime.input_dispatcher.holds.is_held_by("sweep", linux)


def test_controls_ignores_legacy_pad_presets(tmp_path: Path) -> None:
    scene = _copy_orng_bar(tmp_path / "scene")
    controls_path = scene / "controls.yaml"
    data = yaml.safe_load(controls_path.read_text(encoding="utf-8"))
    data["pad_presets"] = ["P01"] * 9
    controls_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    from orng_led.config.io import load_controls

    controls = load_controls(controls_path)
    assert controls is not None
    assert "pad_presets" not in controls.model_dump()


def test_missing_runtime_yaml_defaults_orng_bar(tmp_path: Path) -> None:
    root = tmp_path / "config"
    _write_min_scene(root, "orng-bar", name="ORNG BAR")
    # No runtime.yaml → default active scene is orng-bar.
    runtime = AppRuntime.create(autostart_loop=False, config_dir=root)
    assert runtime.scene_id == "orng-bar"
    _write_min_scene(root, "alt", name="Alt")
    runtime.activate_scene("alt")
    assert (root / "runtime.yaml").is_file()
    saved = yaml.safe_load((root / "runtime.yaml").read_text(encoding="utf-8"))
    assert saved["active_scene_id"] == "alt"


def test_same_source_holds_horizontal_and_vertical_any_release_order(tmp_path: Path) -> None:
    """One tab may hold H+V together; each release must target its own effect."""
    scene = _copy_orng_bar(tmp_path / "scene")
    runtime = AppRuntime.create(autostart_loop=False, config_dir=scene)
    dispatcher = runtime.input_dispatcher
    assert dispatcher is not None
    source = "web:overlap-tab"

    def press(action: InputAction) -> None:
        assert dispatcher.dispatch(
            InputEvent(action=action, source=InputSource.UI, edge="press", source_id=source)
        ).accepted

    def release(action: InputAction) -> None:
        assert dispatcher.dispatch(
            InputEvent(action=action, source=InputSource.UI, edge="release", source_id=source)
        ).accepted

    # Order A: H then V, release H then V.
    press(InputAction.SWEEP_PRESS)
    press(InputAction.VERTICAL_SWEEP_PRESS)
    assert dispatcher.holds.is_held_by("sweep", source)
    assert dispatcher.holds.is_held_by("vertical_sweep", source)
    assert runtime.engine.overlays.sweep_held is True
    release(InputAction.SWEEP_RELEASE)
    assert not dispatcher.holds.is_held_by("sweep", source)
    assert dispatcher.holds.is_held_by("vertical_sweep", source)
    assert runtime.engine.overlays.sweep_held is True
    release(InputAction.VERTICAL_SWEEP_RELEASE)
    assert not dispatcher.holds.any_held("sweep")
    assert not dispatcher.holds.any_held("vertical_sweep")
    assert runtime.engine.overlays.sweep_held is False

    # Order B: H then V, release V then H.
    press(InputAction.SWEEP_PRESS)
    press(InputAction.VERTICAL_SWEEP_PRESS)
    release(InputAction.VERTICAL_SWEEP_RELEASE)
    assert dispatcher.holds.is_held_by("sweep", source)
    assert not dispatcher.holds.is_held_by("vertical_sweep", source)
    assert runtime.engine.overlays.sweep_held is True
    release(InputAction.SWEEP_RELEASE)
    assert runtime.engine.overlays.sweep_held is False

    # Disconnect of this tab clears both; linux hold survives.
    press(InputAction.SWEEP_PRESS)
    press(InputAction.VERTICAL_SWEEP_PRESS)
    linux = "linux-keyboard:keep"
    assert dispatcher.dispatch(
        InputEvent(
            action=InputAction.STROBE_PRESS,
            source=InputSource.KEYBOARD,
            edge="press",
            source_id=linux,
        )
    ).accepted
    runtime.on_ws_disconnect(source_id=source)
    assert not dispatcher.holds.is_held_by("sweep", source)
    assert not dispatcher.holds.is_held_by("vertical_sweep", source)
    assert dispatcher.holds.is_held_by("strobe", linux)
