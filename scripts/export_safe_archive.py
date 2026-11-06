#!/usr/bin/env python3
"""Create a shareable project archive that excludes secrets and private keys.

Never include: .git, .local, .venv, node_modules, .wrangler, private signing
keys, .env/.dev.vars, dist-app-repair*, caches, device tokens, pairing material,
nested ZIP archives, or runtime metadata.

Default output is outside the repo (TEMP or Desktop). Destination inside the
project root is refused.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

EXCLUDE_DIR_NAMES = {
    ".git",
    ".local",
    ".venv",
    "venv",
    "node_modules",
    ".wrangler",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "dist-app-repair",
    "dist-app-repair2",
    "dist-app-repair3",
    "dist-app-repair4",
    "dist-safe",
    "coverage",
    ".cache",
    "agent-transcripts",
    ".cursor",
}

EXCLUDE_GLOBS = [
    "**/.env",
    "**/.env.*",
    "**/.dev.vars",
    "**/*.pem",
    "**/*.key",
    "**/*private*.bin",
    "**/*private*.pem",
    "**/device.token",
    "**/.pairing/**",
    "**/cloud-metadata.json",
    "**/runtime.yaml",
    "dist-app-repair*/**",
    "**/*.pyc",
    "**/*.zip",
    "**/*.tar.gz",
    "**/*.tgz",
    "**/*.7z",
]

FORBIDDEN_ARC_PREFIXES = (
    ".git/",
    ".local/",
    ".venv/",
    "venv/",
    "node_modules/",
    ".wrangler/",
)

# Patterns split so this source file itself does not trip the scanner.
SECRET_PATTERNS = (
    "BEGIN " + "PRIVATE KEY",
    "BEGIN " + "OPENSSH PRIVATE KEY",
    "prod-20261025-01.ed25519." + "private",
    "oauth_" + "token",
    "ORNG_APP_SIGNING_" + "KEY=",
)


def default_out_path() -> Path:
    desktop = Path.home() / "Desktop"
    base = desktop if desktop.is_dir() else Path(tempfile.gettempdir())
    return base / "orng-led-control-safe.zip"


def assert_dest_outside_repo(dest: Path, *, root: Path = REPO) -> None:
    dest_res = dest.resolve()
    root_res = root.resolve()
    try:
        dest_res.relative_to(root_res)
    except ValueError:
        return
    raise SystemExit(
        f"Refusing archive destination inside project root: {dest_res}\n"
        f"Use a path outside {root_res} (default: TEMP/Desktop)."
    )


def _excluded(rel: str) -> bool:
    parts = Path(rel).parts
    if any(p in EXCLUDE_DIR_NAMES for p in parts):
        return True
    if any(p.startswith("dist-app-repair") for p in parts):
        return True
    normalized = rel.replace("\\", "/")
    for g in EXCLUDE_GLOBS:
        if fnmatch.fnmatch(normalized, g):
            return True
    lower = normalized.lower()
    if "private.bin" in lower or "private.pem" in lower:
        return True
    return lower.endswith((".zip", ".tar.gz", ".tgz"))


def iter_files(root: Path) -> list[Path]:
    out: list[Path] = []

    def walk(dir_path: Path) -> None:
        try:
            entries = list(dir_path.iterdir())
        except OSError:
            return
        for path in entries:
            name = path.name
            if name in EXCLUDE_DIR_NAMES or name.startswith("dist-app-repair"):
                continue
            if path.is_dir():
                walk(path)
                continue
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if _excluded(rel):
                continue
            out.append(path)

    walk(root)
    return out


def build_archive(dest: Path, *, root: Path = REPO) -> list[str]:
    assert_dest_outside_repo(dest, root=root)
    files = iter_files(root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    names: list[str] = []
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            rel = path.relative_to(root).as_posix()
            zf.write(path, arcname=rel)
            names.append(rel)
    return names


def verify_archive_exclusions(names: list[str]) -> list[str]:
    hits: list[str] = []
    for n in names:
        norm = n.replace("\\", "/")
        lower = norm.lower()
        if any(norm.startswith(p) or f"/{p}" in f"/{norm}" for p in FORBIDDEN_ARC_PREFIXES):
            hits.append(f"forbidden_path:{norm}")
        if lower.endswith((".zip", ".tar.gz", ".tgz")):
            hits.append(f"nested_archive:{norm}")
        if "private.bin" in lower or lower.endswith(".pem"):
            hits.append(f"key_like:{norm}")
        if "/.env" in f"/{lower}" or lower.endswith(".dev.vars"):
            hits.append(f"env_file:{norm}")
    return hits


def secret_scan_names(names: list[str]) -> list[str]:
    hits: list[str] = []
    for n in names:
        lower = n.lower().replace("\\", "/")
        if "private.bin" in lower or n.endswith(".pem") or "/.env" in f"/{lower}":
            hits.append(n)
        if ".local/ops/signing" in lower and "private" in lower:
            hits.append(n)
    return hits


def secret_scan_bytes(archive: Path) -> list[str]:
    hits: list[str] = []
    skip_names = {
        "scripts/export_safe_archive.py",
        "scripts/build-raspberry-install-kit.py",
        "backend/tests/test_safe_export.py",
    }
    with zipfile.ZipFile(archive, "r") as zf:
        for info in zf.infolist():
            if info.is_dir() or info.file_size > 2_000_000:
                continue
            name = info.filename.replace("\\", "/")
            if name in skip_names:
                continue
            try:
                data = zf.read(info.filename)
            except Exception:  # noqa: BLE001,S112 — best-effort ZIP member scan
                continue
            try:
                text = data.decode("utf-8", errors="ignore")
            except Exception:  # noqa: BLE001,S112 — ignore undecodable members
                continue
            for pat in SECRET_PATTERNS:
                if pat in text:
                    hits.append(f"{info.filename}:{pat}")
    return hits


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Destination ZIP (must be outside the project root). Default: Desktop/TEMP.",
    )
    args = parser.parse_args()
    dest = (args.out or default_out_path()).expanduser().resolve()
    names = build_archive(dest)
    excl_hits = verify_archive_exclusions(names)
    name_hits = secret_scan_names(names)
    byte_hits = secret_scan_bytes(dest)
    size = dest.stat().st_size
    digest = sha256_file(dest)
    print(dest)
    print("files", len(names))
    print("bytes", size)
    print("sha256", digest)
    if excl_hits or name_hits or byte_hits:
        print("SECRET_SCAN_FAIL")
        for h in excl_hits + name_hits + byte_hits:
            print(" ", h)
        return 2
    print("secret_scan_ok")
    print("forbidden_paths_absent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
