#!/usr/bin/env python3
"""Assemble a Pi install kit outside the git worktree (no secrets / no .git)."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXPECTED_CANDIDATE_SHA = "8945aa31dd9392fc343ad3da37339491f7d55a6743ac91233e2773e13c15b45f"

FORBIDDEN_NAME_PARTS = (
    ".git",
    ".local",
    ".venv",
    "node_modules",
    "private.bin",
    ".env",
    "device.token",
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def secret_scan(root: Path) -> list[str]:
    hits: list[str] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix().lower()
        if any(x in rel for x in FORBIDDEN_NAME_PARTS):
            hits.append(rel)
            continue
        if p.suffix.lower() in {".pem", ".key"} or "private" in p.name.lower():
            hits.append(rel)
            continue
        if p.stat().st_size > 2_000_000:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # Split literals so this source file is not flagged by archive scanners.
        if ("BEGIN " + "PRIVATE KEY") in text or ("BEGIN " + "OPENSSH PRIVATE KEY") in text:
            hits.append(f"{rel}:private_key_pem")
    return hits


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--expect-sha256", default=EXPECTED_CANDIDATE_SHA)
    args = parser.parse_args()

    pkg = args.package.resolve()
    man = (args.manifest or Path(str(pkg) + ".manifest.json")).resolve()
    out = args.out.resolve()
    # Refuse writing inside repo.
    try:
        out.relative_to(REPO)
        raise SystemExit(f"Refusing install kit inside repo: {out}")
    except ValueError:
        pass

    digest = sha256_file(pkg)
    if args.expect_sha256 and digest.lower() != args.expect_sha256.lower():
        raise SystemExit(f"SHA-256 mismatch: expected {args.expect_sha256}, got {digest}")
    if not man.is_file():
        raise SystemExit(f"Missing manifest: {man}")

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    shutil.copy2(pkg, out / pkg.name)
    shutil.copy2(man, out / man.name)

    # Public test key only (raw b64) — never private.
    from orng_led.updater.trusted_keys import TEST_APP_KEY_ID, _BUILTIN

    pub = _BUILTIN[TEST_APP_KEY_ID].public_key_raw_b64
    (out / "test-ed25519-1.public.b64.txt").write_text(pub + "\n", encoding="utf-8")

    # Standalone helpers / units
    (out / "bootstrap").mkdir()
    shutil.copy2(REPO / "bootstrap" / "updater_helper.py", out / "bootstrap" / "updater_helper.py")
    shutil.copy2(REPO / "bootstrap" / "helper_lib.py", out / "bootstrap" / "helper_lib.py")
    shutil.copy2(REPO / "scripts" / "raspberry" / "orngctl", out / "orngctl")
    shutil.copy2(
        REPO / "scripts" / "systemd" / "orng-led-control.service",
        out / "orng-led-control.service",
    )
    shutil.copy2(
        REPO / "scripts" / "raspberry" / "templates" / "99-orng-led-input.rules",
        out / "99-orng-led-input.rules",
    )
    # Minimal install driver (stdlib + copied helpers; uses system python3 for bootstrap).
    install_sh = out / "install-on-pi.sh"
    install_sh.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
KIT="$(cd "$(dirname "$0")" && pwd)"
# Windows scp may leave CRLF; normalize text helpers before awk/bash.
find "$KIT" -maxdepth 2 -type f \\( -name '*.sh' -o -name 'orngctl' -o -name 'SHA256SUMS' -o -name '*.txt' -o -name '*.rules' -o -name '*.service' -o -name '*.json' -o -name '*.py' \\) \\
  -exec sed -i 's/\\r$//' {} +
PKG="$(ls "$KIT"/orng-app-*-raspberry-arm64.tar.gz | head -1)"
MAN="${PKG}.manifest.json"
EXPECT_SHA="$(awk '/orng-app-.*\\.tar\\.gz$/ && $2 !~ /manifest/ {print $1; exit}' "$KIT/SHA256SUMS")"
if [[ -z "${EXPECT_SHA}" && -f "$KIT/kit-meta.json" ]]; then
  EXPECT_SHA="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha256"])' "$KIT/kit-meta.json")"
fi
echo "package=$PKG"
echo "expect_sha=$EXPECT_SHA"
echo "$EXPECT_SHA  $PKG" | sha256sum -c -
# Bootstrap CLI from package; templates (unit/helper/orngctl) from this kit.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
tar -xzf "$PKG" -C "$TMP" orng_led 2>/dev/null || tar -xzf "$PKG" -C "$TMP"
export PYTHONPATH="$TMP${PYTHONPATH:+:$PYTHONPATH}"
export ORNG_INSTALL_KIT="$KIT"
export ORNG_BOOTSTRAP_ROOT="$KIT"
export ORNG_DEV_ALLOW_TEST_APP_KEYS=1
python3 -m orng_led.raspberry.bootstrap --check --json
python3 -m orng_led.raspberry.bootstrap --dry-run --package "$PKG" --manifest "$MAN" \\
  --expect-sha256 "$EXPECT_SHA" --allow-test-key --json
echo "Dry-run OK. Re-run with --apply as root when ready:"
echo "  sudo env ORNG_DEV_ALLOW_TEST_APP_KEYS=1 ORNG_INSTALL_KIT=$KIT ORNG_BOOTSTRAP_ROOT=$KIT PYTHONPATH=$TMP \\\\
    python3 -m orng_led.raspberry.bootstrap --apply --package $PKG --manifest $MAN \\\\
    --expect-sha256 $EXPECT_SHA --allow-test-key"
""",
        encoding="utf-8",
        newline="\n",
    )
    install_sh.chmod(0o755)

    readme = out / "README-INSTALL.txt"
    readme.write_text(
        """ORNG LED CONTROL — Raspberry install kit (hardware-candidate / test key)

Contents: signed package + manifest, public test key, helper, unit, udev, SHA256SUMS.
No .git, private keys, tokens, or full repo.

1) Copy kit to Pi (scp).
2) Verify: sha256sum -c SHA256SUMS
3) Run install-on-pi.sh dry-run, then sudo --apply (lab/test key only).
4) Health must show blackout=true output_armed=false before any Art-Net.
5) Enable service only after health OK:
   sudo python3 -m orng_led.raspberry.bootstrap --apply --enable-service ...

This kit is NOT a production stable release.
""",
        encoding="utf-8",
        newline="\n",
    )

    sums = []
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.name != "SHA256SUMS":
            sums.append(f"{sha256_file(p)}  {p.relative_to(out).as_posix()}")
    (out / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")

    hits = secret_scan(out)
    if hits:
        print("SECRET_SCAN_FAIL")
        for h in hits:
            print(" ", h)
        return 2

    meta = {
        "package": pkg.name,
        "sha256": digest,
        "bytes": pkg.stat().st_size,
        "key_id": "test-ed25519-1",
        "channel": "hardware-candidate",
        "kit": str(out),
        "secret_scan_ok": True,
    }
    (out / "kit-meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
