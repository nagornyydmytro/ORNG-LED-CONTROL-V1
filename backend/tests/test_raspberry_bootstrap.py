"""Raspberry bootstrap dry-run / temp-root (no host OS mutation)."""

from __future__ import annotations

import json
import os
import struct
import sys
from pathlib import Path

import pytest

from orng_led.raspberry.bootstrap import main as bootstrap_main
from orng_led.raspberry.network import (
    NetworkPlanError,
    build_artnet_ethernet_plan,
    parse_route_get,
    validate_iface,
)
from orng_led.raspberry.runtime_validate import (
    EM_AARCH64,
    ELF_MAGIC,
    RuntimeValidationError,
    assert_linux_aarch64_elf,
    validate_release_runtime,
)


def fake_aarch64_elf() -> bytes:
    """Minimal ELF64 little-endian AArch64 header. Not an executable."""
    header = bytearray(64)
    header[0:4] = ELF_MAGIC
    header[4] = 2  # ELFCLASS64
    header[5] = 1  # ELFDATA2LSB
    header[6] = 1  # EV_CURRENT
    struct.pack_into("<H", header, 18, EM_AARCH64)
    return bytes(header)


def _fake_release(root: Path, *, with_python: bool = True, with_wheel: bool = True) -> Path:
    rel = root / "release"
    rel.mkdir()
    (rel / "wheelhouse").mkdir()
    (rel / "wheelhouse" / "README.txt").write_text("fixture\n", encoding="utf-8")
    if with_wheel:
        # Non-empty wheelhouse marker (not README-only).
        wheel = rel / "wheelhouse" / "orng_fixture-0.0.0-py3-none-any.whl"
        wheel.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    (rel / "RELEASE.json").write_text(
        json.dumps(
            {
                "version": "0.2.0",
                "build_id": "lab-1",
                "platform": "raspberry",
                "arch": "arm64",
                "dev_artifact": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if with_python:
        bin_dir = rel / "runtime" / "bin"
        bin_dir.mkdir(parents=True)
        # Linux layout: a real AArch64 ELF header. Version probe is monkeypatched
        # because this is not an executable. Production ELF checks still run.
        target = bin_dir / "python"
        target.write_bytes(fake_aarch64_elf())
        if sys.platform != "win32":
            os.chmod(target, 0o755)
    return rel


def test_bootstrap_dry_run_default(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = bootstrap_main(["--root", str(tmp_path / "root"), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "dry-run"
    assert out["hardware_acceptance"] == "pending"
    assert code in {0, 1}  # may fail arch skip ok


def test_bootstrap_check_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = bootstrap_main(["--root", str(tmp_path / "root"), "--check", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "check"
    assert any(s["name"] == "bootstrap_python" for s in out["steps"])
    assert code == 0


def test_template_paths_support_install_kit_layout(tmp_path: Path) -> None:
    from orng_led.raspberry.bootstrap import _template_paths

    kit = tmp_path / "kit"
    (kit / "bootstrap").mkdir(parents=True)
    (kit / "orng-led-control.service").write_text("[Unit]\n", encoding="utf-8")
    (kit / "bootstrap" / "updater_helper.py").write_text("# helper\n", encoding="utf-8")
    (kit / "bootstrap" / "helper_lib.py").write_text("# lib\n", encoding="utf-8")
    (kit / "orngctl").write_text("#!/bin/sh\n", encoding="utf-8")
    unit, helper, helper_lib, orngctl = _template_paths(kit)
    assert unit.is_file()
    assert helper.is_file()
    assert helper_lib.is_file()
    assert orngctl.is_file()


def test_temp_root_apply_idempotent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "orng_led.raspberry.runtime_validate.probe_python_version",
        lambda _py: (3, 12),
    )
    overlay = tmp_path / "root"
    release = _fake_release(tmp_path)
    args = [
        "--root",
        str(overlay),
        "--apply",
        "--release",
        str(release),
        "--allow-test-key",
        "--json",
    ]
    code1 = bootstrap_main(args)
    out1 = json.loads(capsys.readouterr().out)
    assert code1 == 0, out1
    assert out1["ok"] is True
    app = overlay / "opt" / "orng-led-control"
    data = overlay / "var" / "lib" / "orng-led-control"
    assert (app / "bootstrap" / "updater_helper.py").is_file()
    assert (data / "scenes").is_dir()
    token = data / "device.token"
    token.write_text("secret-token-do-not-delete\n", encoding="utf-8")
    meta = data / "cloud-metadata.json"
    meta.write_text("{}\n", encoding="utf-8")
    current = app / "current.json"
    assert current.is_file()
    ptr = json.loads(current.read_text(encoding="utf-8"))
    assert ".git" not in ptr.get("path", "")
    assert "GitHub" not in ptr.get("path", "") or "releases" in ptr.get("path", "")

    code2 = bootstrap_main(args)
    out2 = json.loads(capsys.readouterr().out)
    assert code2 == 0 and out2["ok"]
    assert token.read_text(encoding="utf-8").startswith("secret-token")
    assert meta.is_file()


def test_no_repo_pointer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "orng_led.raspberry.runtime_validate.probe_python_version",
        lambda _py: (3, 12),
    )
    overlay = tmp_path / "root"
    release = _fake_release(tmp_path)
    bootstrap_main(
        [
            "--root",
            str(overlay),
            "--apply",
            "--release",
            str(release),
            "--allow-test-key",
            "--json",
        ]
    )
    capsys.readouterr()
    ptr = json.loads(
        (overlay / "opt" / "orng-led-control" / "current.json").read_text(encoding="utf-8")
    )
    assert "releases" in ptr["path"].replace("\\", "/")


def test_text_script_is_rejected_as_not_elf(tmp_path: Path) -> None:
    rel = _fake_release(tmp_path)
    python = rel / "runtime" / "bin" / "python"
    python.write_text("#!/usr/bin/env python3\nprint('Python 3.12.0')\n", encoding="utf-8")
    with pytest.raises(RuntimeValidationError) as exc:
        assert_linux_aarch64_elf(python)
    assert exc.value.code == "not_elf"
    with pytest.raises(RuntimeValidationError) as runtime:
        validate_release_runtime(
            rel,
            require_wheels=True,
            require_aarch64_elf=True,
            allow_dev_artifact=True,
        )
    assert runtime.value.code == "not_elf"


def test_fake_elf_header_satisfies_production_elf_check(tmp_path: Path) -> None:
    rel = _fake_release(tmp_path)
    assert_linux_aarch64_elf(rel / "runtime" / "bin" / "python")


def test_runtime_rejects_missing_python(tmp_path: Path) -> None:
    rel = _fake_release(tmp_path, with_python=False)
    with pytest.raises(RuntimeValidationError) as exc:
        validate_release_runtime(
            rel,
            require_wheels=False,
            require_aarch64_elf=False,
            allow_dev_artifact=True,
        )
    assert exc.value.code in {"missing_runtime_python", "missing_embedded_runtime"}


def test_runtime_rejects_empty_wheelhouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "orng_led.raspberry.runtime_validate.probe_python_version",
        lambda _py: (3, 12),
    )
    rel = _fake_release(tmp_path, with_python=True, with_wheel=False)
    with pytest.raises(RuntimeValidationError) as exc:
        validate_release_runtime(
            rel,
            require_wheels=True,
            require_aarch64_elf=True,
            allow_dev_artifact=True,
        )
    assert exc.value.code == "empty_wheelhouse"


def test_allow_test_key_does_not_skip_empty_wheelhouse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "orng_led.raspberry.runtime_validate.probe_python_version",
        lambda _py: (3, 12),
    )
    overlay = tmp_path / "root"
    release = _fake_release(tmp_path, with_wheel=False)
    code = bootstrap_main(
        [
            "--root",
            str(overlay),
            "--apply",
            "--release",
            str(release),
            "--allow-test-key",
            "--json",
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert code != 0
    assert out["ok"] is False
    assert any("empty_wheelhouse" in e for e in out["errors"])


def test_network_apply_refused_without_linux_root(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from orng_led.raspberry.network import main as network_main

    code = network_main(["--apply", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 2
    assert out.get("code") == "apply_refused" or out.get("ok") is False


def test_network_plan_and_validation() -> None:
    plan = build_artnet_ethernet_plan()
    assert plan.iface == "eth0"
    assert "never-default" in json.dumps(plan.nm_profile)
    assert any(c[0] == "nmcli" for c in plan.commands)
    parsed = parse_route_get("2.0.0.11 dev eth0 src 2.0.0.10 uid 1000")
    assert parsed["dev"] == "eth0"
    assert parsed["src"] == "2.0.0.10"
    with pytest.raises(NetworkPlanError):
        validate_iface("eth0; rm -rf /")
    with pytest.raises(NetworkPlanError):
        build_artnet_ethernet_plan(cidr="not-an-ip")


def test_systemd_unit_static() -> None:
    unit = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "systemd"
        / "orng-led-control.service"
    )
    text = unit.read_text(encoding="utf-8")
    assert "updater_helper.py" in text
    assert "EnvironmentFile=-/etc/orng-led-control/device.env" in text
    assert "ORNG_CLOUD_DEVICE_MODE=raspberry" in text
    assert "ORNG_LINUX_KEYBOARD=1" in text
    assert "ORNG_CLOUD_WS_URL" not in text
    assert "StartLimitBurst" in text
    assert "User=orng-led" in text


def test_orngctl_doctor_redaction(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORNG_DEVICE_TOKEN", "super-secret-token-value")
    from orng_led.raspberry.orngctl import main as orngctl_main

    code = orngctl_main(["doctor", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out.get("hardware_acceptance") == "pending"
    blob = json.dumps(out)
    assert "super-secret-token-value" not in blob
