"""Prove bootstrap helper runs outside the repo without orng_led on PYTHONPATH."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BOOTSTRAP = REPO / "bootstrap"


@pytest.fixture()
def outside_helper(tmp_path: Path):
    base = tmp_path / "outside-install"
    boot = base / "bootstrap"
    boot.mkdir(parents=True)
    shutil.copy2(BOOTSTRAP / "updater_helper.py", boot / "updater_helper.py")
    shutil.copy2(BOOTSTRAP / "helper_lib.py", boot / "helper_lib.py")
    app = base / "app"
    releases = app / "releases"
    releases.mkdir(parents=True)
    for ver, bid in (("0.1.0", "old"), ("0.2.0", "new")):
        rel = releases / f"{ver}-{bid}"
        rel.mkdir()
        (rel / "RELEASE.json").write_text(
            json.dumps(
                {
                    "version": ver,
                    "build_id": bid,
                    "stub_command": [sys.executable, "-c", "import time; time.sleep(30)"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
    (app / "current.json").write_text(
        json.dumps(
            {
                "version": "0.1.0",
                "build_id": "old",
                "path": str(releases / "0.1.0-old"),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return base, boot, app


def test_standalone_helper_status_and_apply(outside_helper: tuple[Path, Path, Path]) -> None:
    base, boot, app = outside_helper
    env = os.environ.copy()
    env["ORNG_APP_ROOT"] = str(app)
    env["PYTHONPATH"] = ""

    status = subprocess.run(
        [sys.executable, str(boot / "updater_helper.py"), "status"],
        cwd=str(Path.home()),
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = json.loads(status.stdout)
    assert payload["standalone"] is True
    assert payload["helper_protocol"] == 2
    assert "ModuleNotFoundError" not in status.stderr

    (app / "apply-request.json").write_text(
        json.dumps(
            {
                "expected_version": "0.2.0",
                "expected_build_id": "new",
                "staged_path": str(app / "releases" / "0.2.0-new"),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    applied = subprocess.run(
        [
            sys.executable,
            str(boot / "updater_helper.py"),
            "apply-once",
            "--skip-process",
        ],
        cwd=str(base),
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    out = json.loads(applied.stdout)
    assert out["status"] == "applied"
    current = json.loads((app / "current.json").read_text(encoding="utf-8"))
    assert current["build_id"] == "new"


def test_standalone_helper_rollback(outside_helper: tuple[Path, Path, Path]) -> None:
    _base, boot, app = outside_helper
    env = os.environ.copy()
    env["ORNG_APP_ROOT"] = str(app)
    env["PYTHONPATH"] = ""
    # Seed previous + force health-fail apply so rollback path runs.
    (app / "previous.json").write_text(
        json.dumps(
            {
                "version": "0.1.0",
                "build_id": "old",
                "path": str(app / "releases" / "0.1.0-old"),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (app / "apply-request.json").write_text(
        json.dumps(
            {
                "expected_version": "0.2.0",
                "expected_build_id": "new",
                "staged_path": str(app / "releases" / "0.2.0-new"),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rolled = subprocess.run(
        [
            sys.executable,
            str(boot / "updater_helper.py"),
            "apply-once",
            "--skip-process",
            "--health-fail",
        ],
        cwd=str(_base),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    out = json.loads(rolled.stdout)
    assert out["status"] == "rolled_back"
    current = json.loads((app / "current.json").read_text(encoding="utf-8"))
    assert current["build_id"] == "old"
