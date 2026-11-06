"""First-install pointer cleared / previous pointer restored on failure."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orng_led.raspberry.bootstrap import main as bootstrap_main


def test_first_install_clears_pointer_on_runtime_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "orng_led.raspberry.runtime_validate.probe_python_version",
        lambda _py: (3, 11),  # too old → fail after would-be stage
    )
    overlay = tmp_path / "root"
    rel = tmp_path / "release"
    rel.mkdir()
    (rel / "wheelhouse").mkdir()
    (rel / "wheelhouse" / "x.whl").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    (rel / "RELEASE.json").write_text(
        json.dumps(
            {
                "version": "0.2.0",
                "build_id": "lab-fail",
                "platform": "raspberry",
                "arch": "arm64",
                "dev_artifact": True,
            }
        ),
        encoding="utf-8",
    )
    bin_dir = rel / "runtime" / "bin"
    bin_dir.mkdir(parents=True)
    from test_raspberry_bootstrap import fake_aarch64_elf

    (bin_dir / "python").write_bytes(fake_aarch64_elf())

    code = bootstrap_main(
        [
            "--root",
            str(overlay),
            "--apply",
            "--release",
            str(rel),
            "--allow-test-key",
            "--json",
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert code != 0
    assert out["ok"] is False
    current = overlay / "opt" / "orng-led-control" / "current.json"
    assert not current.is_file()


def test_temp_root_apply_twice_preserves_data(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "orng_led.raspberry.runtime_validate.probe_python_version",
        lambda _py: (3, 12),
    )
    from test_raspberry_bootstrap import _fake_release

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
    assert bootstrap_main(args) == 0
    capsys.readouterr()
    data = overlay / "var" / "lib" / "orng-led-control"
    (data / "device.token").write_text("keep-me\n", encoding="utf-8")
    assert bootstrap_main(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert (data / "device.token").read_text(encoding="utf-8") == "keep-me\n"
