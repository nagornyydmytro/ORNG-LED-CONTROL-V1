"""Safe archive export must never contain private keys or .local secrets."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from export_safe_archive import (  # noqa: E402
    build_archive,
    secret_scan_bytes,
    secret_scan_names,
)


def test_safe_export_excludes_secrets(tmp_path: Path) -> None:
    out = tmp_path / "safe.zip"
    names = build_archive(out, root=REPO)
    assert names
    assert not any(".local/" in n.replace("\\", "/") for n in names)
    assert not any(n.startswith(".git/") for n in names)
    assert not any("node_modules/" in n.replace("\\", "/") for n in names)
    assert not any("private.bin" in n.lower() for n in names)
    assert not any("dist-app-repair" in n for n in names)
    assert not any(n.lower().endswith(".zip") for n in names)
    assert secret_scan_names(names) == []
    assert secret_scan_bytes(out) == []


def test_root_dotenv_logs_and_snapshots_stay_out(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    (root / "cloud").mkdir(parents=True)
    (root / "docs" / "captures").mkdir(parents=True)
    (root / ".venue-logs").mkdir()
    (root / "readme.txt").write_text("ok\n", encoding="utf-8")
    (root / ".env").write_text("EXAMPLE=1\n", encoding="utf-8")
    (root / ".env.production").write_text("EXAMPLE=1\n", encoding="utf-8")
    (root / ".dev.vars").write_text("EXAMPLE=1\n", encoding="utf-8")
    (root / "cloud" / ".dev.vars").write_text("EXAMPLE=1\n", encoding="utf-8")
    (root / ".venue-logs" / "run.log").write_text("log\n", encoding="utf-8")
    (root / "docs" / "captures" / "frame.png").write_bytes(b"png")
    dest = tmp_path / "safe.zip"
    names = build_archive(dest, root=root)
    assert names == ["readme.txt"]


def test_failed_scan_keeps_the_previous_archive(tmp_path: Path) -> None:
    from export_safe_archive import UnsafeArchive

    root = tmp_path / "tree"
    root.mkdir()
    (root / "notes.txt").write_text("oauth_" + "token=example\n", encoding="utf-8")
    dest = tmp_path / "out" / "safe.zip"
    dest.parent.mkdir()
    dest.write_bytes(b"previous-safe")
    try:
        build_archive(dest, root=root)
        raised = False
    except UnsafeArchive:
        raised = True
    assert raised is True
    assert dest.read_bytes() == b"previous-safe"
    assert not (dest.parent / "safe.zip.partial").exists()


def test_safe_export_refuses_destination_inside_repo(tmp_path: Path) -> None:
    from export_safe_archive import assert_dest_outside_repo

    inside = REPO / "dist-safe" / "should-fail.zip"
    try:
        assert_dest_outside_repo(inside, root=REPO)
        raised = False
    except SystemExit:
        raised = True
    assert raised is True
