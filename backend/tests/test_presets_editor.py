"""Preset editor CRUD, validation recovery and Mock preview."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.config import default_config_dir
from orng_led.config.models import ConfigError
from orng_led.main import create_app
from orng_led.presets.io import load_preset
from orng_led.presets.store import PresetStore


@pytest.fixture
def isolated_config(tmp_path: Path) -> Path:
    source = default_config_dir()
    target = tmp_path / "config"
    shutil.copytree(source, target)
    return target


@pytest.fixture
def client(isolated_config: Path):
    runtime = AppRuntime.create(autostart_loop=False, config_dir=isolated_config)
    application = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(application) as test_client:
        yield test_client, runtime, isolated_config


def test_p05_yaml_loads_into_runtime(client) -> None:
    test_client, runtime, _cfg = client
    presets = test_client.get("/api/presets").json()["presets"]
    assert any(item["id"] == "P05" for item in presets)
    doc = test_client.get("/api/presets/P05").json()
    assert doc["hardware_tuned"] is False
    assert len(doc["episodes"]) == 10
    assert abs(sum(ep["duration_s"] for ep in doc["episodes"]) - 180.0) < 1e-6
    assert "P05" in runtime.engine.presets


def test_create_custom_preset_without_yaml_editing(client) -> None:
    test_client, runtime, cfg = client
    ack = test_client.post(
        "/api/presets",
        json={"id": "C01", "label": "Мій пресет"},
    ).json()
    assert ack["ok"] is True
    assert "C01" in ack["state"]["presets"]
    assert (cfg / "presets" / "C01.yaml").exists()
    assert runtime.output.armed is False
    assert runtime.output.transport_kind.value == "mock"


def test_invalid_update_does_not_replace_last_valid(client) -> None:
    test_client, _runtime, cfg = client
    before = load_preset(cfg / "presets" / "P05.yaml")
    bad = before.model_dump(mode="json")
    bad["episodes"][0]["palette"] = "not_a_real_palette"
    response = test_client.put("/api/presets/P05", json={"preset": bad})
    assert response.status_code == 400
    after = load_preset(cfg / "presets" / "P05.yaml")
    assert after.model_dump() == before.model_dump()


def test_rename_duplicate_delete_and_reorder(client) -> None:
    test_client, runtime, cfg = client
    test_client.post("/api/presets", json={"id": "C02", "label": "Чернетка"}).json()
    renamed = test_client.post(
        "/api/presets/C02/rename",
        json={"label": "Чернетка 2"},
    ).json()
    assert renamed["ok"] is True
    assert test_client.get("/api/presets/C02").json()["label"] == "Чернетка 2"

    dup = test_client.post(
        "/api/presets/C02/duplicate",
        json={"new_id": "C03", "label": "Копія"},
    ).json()
    assert "C03" in dup["state"]["presets"]

    doc = test_client.get("/api/presets/C03").json()
    # Reorder: move last episode to front.
    doc["episodes"] = [doc["episodes"][-1], *doc["episodes"][:-1]]
    saved = test_client.put("/api/presets/C03", json={"preset": doc}).json()
    assert saved["ok"] is True
    reloaded = test_client.get("/api/presets/C03").json()
    assert reloaded["episodes"][0]["id"] == doc["episodes"][0]["id"]

    deleted = test_client.delete("/api/presets/C03").json()
    assert "C03" not in deleted["state"]["presets"]
    assert not (cfg / "presets" / "C03.yaml").exists()

    builtin = test_client.delete("/api/presets/P05")
    assert builtin.status_code == 400
    assert runtime.engine.active_preset_id in runtime.engine.presets


def test_preview_stays_on_mock(client) -> None:
    test_client, runtime, _cfg = client
    ack = test_client.post("/api/presets/P05/preview", json={"speed": 30}).json()
    assert ack["state"]["engine"]["preset_id"] == "P05"
    assert ack["state"]["preview_speed"] == 30.0
    assert ack["state"]["output"]["transport"] == "mock"
    assert ack["state"]["output"]["armed"] is False
    assert runtime.output.allow_real_network is False
    runtime.tick(dt_s=1.0)
    assert runtime.engine.preset_elapsed_s == 30.0


def test_store_rejects_invalid_yaml_file(tmp_path: Path) -> None:
    presets = tmp_path / "presets"
    presets.mkdir()
    bad = {
        "schema_version": 1,
        "id": "BAD",
        "label": "Bad",
        "hardware_tuned": False,
        "episodes": [],
    }
    (presets / "BAD.yaml").write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ConfigError):
        PresetStore.load(presets)
