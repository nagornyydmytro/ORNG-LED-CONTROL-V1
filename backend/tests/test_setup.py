"""Setup wizard: persistence, validation and raw-tester safety."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.config import default_config_dir, load_show_config
from orng_led.main import create_app


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


def test_raw_tester_starts_and_exits_at_zero(client) -> None:
    test_client, runtime, _cfg = client
    entered = test_client.post("/api/setup/raw-tester/enter", json={}).json()
    assert entered["state"]["raw_tester"]["active"] is True
    assert entered["state"]["raw_tester"]["nonzero_channels"] == 0
    assert all(v == 0 for v in entered["state"]["frame"])

    set_ack = test_client.post(
        "/api/setup/raw-tester/set",
        json={"channel": 1, "value": 200},
    ).json()
    assert set_ack["state"]["frame"][0] == 200
    assert set_ack["state"]["raw_tester"]["nonzero_channels"] == 1
    assert runtime.output.transport_kind.value == "mock"
    assert runtime.output.armed is False

    exited = test_client.post("/api/setup/raw-tester/exit", json={}).json()
    assert exited["state"]["raw_tester"]["active"] is False
    assert exited["state"]["raw_tester"]["frame"] is None
    # Exit clears tester values and publishes a zero frame before returning to engine.
    assert runtime.raw_tester.nonzero_channels == 0
    last = runtime.output.transport.last_frame  # type: ignore[attr-defined]
    assert last is not None and all(v == 0 for v in last)


def test_patch_errors_visible_before_save(client) -> None:
    test_client, runtime, _cfg = client
    patch = runtime.show.patch.model_dump(mode="json")
    patch["fixtures"][1]["start_address"] = patch["fixtures"][0]["start_address"]
    result = test_client.post(
        "/api/setup/validate-patch",
        json={"patch": patch},
    ).json()
    assert result["ok"] is False
    assert any("overlap" in err.lower() or "Channel overlap" in err for err in result["errors"])

    bad = test_client.put("/api/config/patch", json={"patch": patch})
    assert bad.status_code == 400
    # Disk unchanged relative to loaded show
    reloaded = load_show_config(runtime.config_dir)
    assert reloaded.patch.fixtures[1].start_address != patch["fixtures"][1]["start_address"]


def test_app_config_persists_without_arming(client) -> None:
    test_client, runtime, cfg = client
    app = runtime.show.app.model_dump(mode="json")
    app["artnet"]["target_ip"] = "10.0.0.50"
    app["artnet"]["universe"] = 2
    app["artnet"]["fps"] = 30
    app["artnet"]["hardware_verified"] = True  # must be forced false on save
    app["output_armed"] = True
    app["transport"] = "artnet"

    ack = test_client.put("/api/config/app", json={"config": app}).json()
    assert ack["ok"] is True
    assert ack["state"]["output"]["armed"] is False
    assert ack["state"]["output"]["transport"] == "mock"
    assert ack["state"]["output"]["network_allowed"] is False

    saved = load_show_config(cfg).app
    assert saved.artnet.target_ip == "10.0.0.50"
    assert saved.artnet.universe == 2
    assert saved.artnet.hardware_verified is False
    assert saved.output_armed is False


def test_profile_save_keeps_hardware_unverified(client) -> None:
    test_client, runtime, cfg = client
    profile = runtime.show.profiles["par_7ch_provisional"].model_dump(mode="json")
    profile["hardware_verified"] = True
    profile["channels"][0]["label"] = "Dimmer (edited)"
    ack = test_client.put(
        "/api/config/profiles/par_7ch_provisional",
        json={"profile": profile},
    ).json()
    assert ack["ok"] is True
    reloaded = load_show_config(cfg).profiles["par_7ch_provisional"]
    assert reloaded.hardware_verified is False
    assert reloaded.channels[0].label == "Dimmer (edited)"


def test_identify_fixture_and_readiness(client) -> None:
    test_client, runtime, _cfg = client
    ack = test_client.post(
        "/api/setup/identify-fixture",
        json={"fixture_id": "par_1", "level": 210},
    ).json()
    assert ack["state"]["raw_tester"]["active"] is True
    assert ack["state"]["frame"][0] == 210

    ready = test_client.get("/api/setup/readiness").json()
    assert ready["artnet_badge"] == "Не перевірено на обладнанні"
    assert ready["output_armed"] is False
    assert ready["patch_ok"] is True
    assert all(p["badge"] == "Не перевірено на обладнанні" for p in ready["profiles"])

    exit_ack = test_client.post("/api/setup/raw-tester/exit", json={}).json()
    assert exit_ack["state"]["raw_tester"]["active"] is False
    assert runtime.raw_tester.nonzero_channels == 0
    last = runtime.output.transport.last_frame  # type: ignore[attr-defined]
    assert last is not None and all(v == 0 for v in last)


def test_layout_save_roundtrip(client) -> None:
    test_client, runtime, cfg = client
    layout = runtime.show.layout.model_dump(mode="json")
    layout["description"] = "Updated layout note (still PENDING HARDWARE)."
    ack = test_client.put("/api/config/layout", json={"layout": layout}).json()
    assert ack["ok"] is True
    assert load_show_config(cfg).layout.description.startswith("Updated layout")
