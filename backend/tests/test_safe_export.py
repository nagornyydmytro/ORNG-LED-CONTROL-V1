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


def test_safe_export_refuses_destination_inside_repo(tmp_path: Path) -> None:
    from export_safe_archive import assert_dest_outside_repo

    inside = REPO / "dist-safe" / "should-fail.zip"
    try:
        assert_dest_outside_repo(inside, root=REPO)
        raised = False
    except SystemExit:
        raised = True
    assert raised is True
