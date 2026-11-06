"""Idempotent Raspberry bootstrap (default dry-run; real --apply on device)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orng_led.raspberry.contract import load_contract
from orng_led.raspberry.install import InstallError, install_signed_package
from orng_led.raspberry.paths import RaspberryPaths, resolve_paths
from orng_led.raspberry.runtime_validate import RuntimeValidationError, validate_release_runtime
from orng_led.raspberry.sysops import (
    SysOpError,
    chmod_path,
    chown_path,
    daemon_reload,
    ensure_service_user,
    is_real_root_fs,
    require_root_for_apply,
    service_enable_start,
    service_stop_disable,
    udev_reload,
)

def _resolve_template_root() -> Path:
    """Locate systemd/helper/orngctl templates for bootstrap.

    Dev checkout: ``<repo>/backend/orng_led/raspberry/bootstrap.py`` → repo root.
    Signed package extract: ``…/orng_led/raspberry/bootstrap.py`` (no ``backend/``) —
    then ``ORNG_INSTALL_KIT`` / ``ORNG_BOOTSTRAP_ROOT`` must point at the install kit
    (or a staged tree with ``scripts/`` + ``bootstrap/``).
    """
    here = Path(__file__).resolve()
    # backend/orng_led/raspberry → parents[3] == repo root
    repo_cand = here.parents[3]
    if (repo_cand / "scripts" / "systemd" / "orng-led-control.service").is_file():
        return repo_cand
    for key in ("ORNG_BOOTSTRAP_ROOT", "ORNG_INSTALL_KIT"):
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            continue
        root = Path(raw).expanduser().resolve()
        if root.is_dir():
            return root
    # Package-only extract without kit env: still return repo_cand for error detail.
    return repo_cand


def _template_paths(root: Path) -> tuple[Path, Path, Path, Path]:
    """Return (systemd unit, helper, helper_lib, orngctl) for repo or install-kit layout."""
    unit_repo = root / "scripts" / "systemd" / "orng-led-control.service"
    unit_kit = root / "orng-led-control.service"
    unit = unit_repo if unit_repo.is_file() else unit_kit

    helper = root / "bootstrap" / "updater_helper.py"
    helper_lib = root / "bootstrap" / "helper_lib.py"

    orngctl_repo = root / "scripts" / "raspberry" / "orngctl"
    orngctl_kit = root / "orngctl"
    orngctl = orngctl_repo if orngctl_repo.is_file() else orngctl_kit
    return unit, helper, helper_lib, orngctl


REPO_ROOT = _resolve_template_root()
SYSTEMD_SRC, HELPER_SRC, HELPER_LIB_SRC, ORNGCTL_SRC = _template_paths(REPO_ROOT)


@dataclass
class BootstrapReport:
    mode: str
    ok: bool = True
    steps: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add(self, name: str, *, ok: bool = True, detail: str = "", planned: Any = None) -> None:
        self.steps.append({"name": name, "ok": ok, "detail": detail, "planned": planned})
        if not ok:
            self.ok = False
            self.errors.append(f"{name}: {detail}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "ok": self.ok,
            "hardware_acceptance": load_contract().hardware_acceptance,
            "steps": self.steps,
            "errors": self.errors,
        }


def _write_text(path: Path, text: str, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    if mode is not None:
        chmod_path(path, mode)


def preflight_checks(
    paths: RaspberryPaths,
    *,
    skip_host_arch: bool = False,
) -> BootstrapReport:
    report = BootstrapReport(mode="check")
    contract = paths.contract

    if not skip_host_arch:
        import platform

        m = platform.machine().lower()
        if m not in {"aarch64", "arm64"}:
            report.add(
                "arch",
                ok=False,
                detail=f"expected arm64, host reports {m or 'unknown'}",
            )
        else:
            report.add("arch", detail=m)
    else:
        report.add("arch", detail="skipped (--root / non-Pi host)")

    py = sys.version_info
    need = contract.min_bootstrap_python
    if (py.major, py.minor) < need:
        report.add(
            "bootstrap_python",
            ok=False,
            detail=f"{py.major}.{py.minor} < {need[0]}.{need[1]}",
        )
    else:
        report.add("bootstrap_python", detail=f"{py.major}.{py.minor}")

    report.add("app_root", detail=str(paths.app_root))
    report.add("data_root", detail=str(paths.data_root))
    report.add("etc_root", detail=str(paths.etc_root))
    report.add(
        "templates",
        ok=SYSTEMD_SRC.is_file() and HELPER_SRC.is_file(),
        detail=f"unit={SYSTEMD_SRC.is_file()} helper={HELPER_SRC.is_file()}",
    )
    return report


def _device_env_body(*, trusted_keys_json: str = "") -> str:
    keys_line = (
        f"ORNG_APP_TRUSTED_KEYS={trusted_keys_json}\n"
        if trusted_keys_json.strip()
        else "# ORNG_APP_TRUSTED_KEYS=  # public JSON map only\n"
    )
    return (
        "# ORNG LED CONTROL device env — no secrets / private keys\n"
        "ORNG_PRODUCTION=1\n"
        "ORNG_APP_ROOT=/opt/orng-led-control\n"
        "ORNG_CONFIG_ROOT=/var/lib/orng-led-control\n"
        "ORNG_DATA_ROOT=/var/lib/orng-led-control\n"
        "ORNG_INSTALL_MODE=managed\n"
        "ORNG_CLOUD_DEVICE_MODE=raspberry\n"
        "ORNG_LINUX_KEYBOARD=1\n"
        "ORNG_LINUX_KEYBOARD_GRAB=1\n"
        "ORNG_CLOUD_DEVICE_URL=wss://device.light.orng.wtf/ws/device\n"
        f"{keys_line}"
        "# Never set ORNG_DEV_* flags or private signing keys here\n"
    )


def _udev_rules_body() -> str:
    return (
        '# ORNG LED CONTROL — grant orng-led access to input event nodes\n'
        'KERNEL=="event*", SUBSYSTEM=="input", MODE="0660", GROUP="input"\n'
    )


def _runtime_check_flags(*, real_fs: bool, overlay: bool) -> tuple[bool, bool]:
    """Return (require_wheels, require_aarch64_elf).

    ``allow_test_key`` must never weaken these. Overlay on non-Linux hosts
    cannot probe aarch64 ELF — ELF is required on Linux always.
    """
    require_wheels = True
    require_elf = sys.platform.startswith("linux")
    if overlay and not sys.platform.startswith("linux"):
        require_elf = False
    if real_fs:
        require_elf = True
        require_wheels = True
    return require_wheels, require_elf


def _apply_ownership(paths: RaspberryPaths, *, apply: bool, real_fs: bool) -> None:
    if not apply:
        return
    contract = paths.contract
    user = contract.service_user
    group = contract.service_group
    # App + releases owned by service user; etc stays root:group.
    for d in (paths.app_root, paths.bootstrap, paths.releases, paths.data_root):
        if d.exists():
            chmod_path(d, 0o755 if d != paths.data_root else 0o700)
            if real_fs:
                chown_path(d, user, group, recursive=True)
    if paths.device_env.is_file():
        chmod_path(paths.device_env, 0o640)
        if real_fs:
            chown_path(paths.device_env, "root", group)
    if paths.unit_path.is_file():
        chmod_path(paths.unit_path, 0o644)
        if real_fs:
            chown_path(paths.unit_path, "root", "root")
    if paths.udev_rules.is_file():
        chmod_path(paths.udev_rules, 0o644)
        if real_fs:
            chown_path(paths.udev_rules, "root", "root")
    helper = paths.bootstrap / "updater_helper.py"
    if helper.is_file():
        chmod_path(helper, 0o755)
        if real_fs:
            chown_path(helper, user, group)
    orngctl = paths.app_root / "bin" / "orngctl"
    if orngctl.is_file():
        chmod_path(orngctl, 0o755)
        if real_fs:
            chown_path(orngctl, "root", "root")


def plan_or_apply(
    paths: RaspberryPaths,
    *,
    apply: bool,
    release_dir: Path | None = None,
    package_path: Path | None = None,
    manifest_path: Path | None = None,
    expected_sha256: str | None = None,
    allow_test_key: bool = False,
    enable_service: bool = False,
) -> BootstrapReport:
    mode = "apply" if apply else "dry-run"
    report = BootstrapReport(mode=mode)
    contract = paths.contract
    real_fs = is_real_root_fs(paths.root)
    overlay = not real_fs

    try:
        require_root_for_apply(apply=apply, real_fs=real_fs)
    except SysOpError as exc:
        report.add("root", ok=False, detail=exc.message)
        return report

    # Preserve existing data/token/metadata — never wipe.
    for p in [
        paths.data_root / "scenes",
        paths.data_root / "device.token",
        paths.data_root / "cloud-metadata.json",
        paths.data_root / ".pairing",
    ]:
        report.add(
            "preserve",
            detail=str(p),
            planned={"exists": p.exists(), "action": "leave_untouched"},
        )

    dirs = [
        (paths.app_root, 0o755),
        (paths.bootstrap, 0o755),
        (paths.releases, 0o755),
        (paths.app_root / "bin", 0o755),
        (paths.data_root, 0o700),
        (paths.data_root / "scenes", 0o700),
        (paths.etc_root, 0o750),
    ]
    for d, mode_bits in dirs:
        report.add("mkdir", detail=str(d), planned={"mode": oct(mode_bits)})
        if apply:
            d.mkdir(parents=True, exist_ok=True)
            chmod_path(d, mode_bits)

    try:
        user_plan = ensure_service_user(
            user=contract.service_user,
            group=contract.service_group,
            home=contract.data_root,
            input_group=contract.input_group,
            apply=apply,
            real_fs=real_fs,
        )
        report.add("user", planned=user_plan)
    except SysOpError as exc:
        report.add("user", ok=False, detail=exc.message)
    except Exception as exc:  # noqa: BLE001
        report.add("user", ok=False, detail=str(exc))

    # Helper + stdlib helper_lib outside replaceable releases.
    helper_dst = paths.bootstrap / "updater_helper.py"
    helper_lib_dst = paths.bootstrap / "helper_lib.py"
    report.add(
        "helper",
        planned={
            "src": str(HELPER_SRC),
            "dst": str(helper_dst),
            "lib_src": str(HELPER_LIB_SRC),
            "lib_dst": str(helper_lib_dst),
        },
    )
    if apply and HELPER_SRC.is_file():
        paths.bootstrap.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HELPER_SRC, helper_dst)
        if HELPER_LIB_SRC.is_file():
            shutil.copy2(HELPER_LIB_SRC, helper_lib_dst)

    # Standalone orngctl wrapper (managed path prefers release Python).
    orngctl_dst = paths.app_root / "bin" / "orngctl"
    report.add("orngctl", planned={"dst": str(orngctl_dst), "src": str(ORNGCTL_SRC)})
    if apply and ORNGCTL_SRC.is_file():
        orngctl_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ORNGCTL_SRC, orngctl_dst)
        chmod_path(orngctl_dst, 0o755)

    env_path = paths.device_env
    if env_path.is_file():
        report.add("device_env", detail="exists — leave unchanged")
        if apply:
            chmod_path(env_path, 0o640)
    else:
        report.add("device_env", planned={"path": str(env_path), "mode": "0640"})
        if apply:
            _write_text(env_path, _device_env_body(), mode=0o640)

    unit_dst = paths.unit_path
    report.add("systemd_unit", planned={"src": str(SYSTEMD_SRC), "dst": str(unit_dst)})
    if apply and SYSTEMD_SRC.is_file():
        unit_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SYSTEMD_SRC, unit_dst)
        chmod_path(unit_dst, 0o644)

    udev_dst = paths.udev_rules
    report.add("udev", planned={"dst": str(udev_dst)})
    if apply:
        _write_text(udev_dst, _udev_rules_body(), mode=0o644)

    require_wheels, require_elf = _runtime_check_flags(real_fs=real_fs, overlay=overlay)
    previous_pointer: dict[str, Any] | None = None
    current = paths.current_pointer
    if current.is_file():
        try:
            previous_pointer = json.loads(current.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous_pointer = {}

    staged_path: str | None = None
    first_install = not current.is_file()

    if package_path is not None:
        man = manifest_path
        if man is None:
            man = Path(str(package_path) + ".manifest.json")
        report.add(
            "signed_package",
            planned={
                "package": str(package_path),
                "manifest": str(man),
                "expected_sha256": expected_sha256,
                "allow_test_key": allow_test_key,
            },
        )
        if apply and report.ok:
            try:
                result = install_signed_package(
                    Path(package_path),
                    Path(man),
                    paths.releases,
                    expected_sha256=expected_sha256,
                    allow_test_key=allow_test_key,
                    allow_dev_artifact=allow_test_key,
                )
                staged_path = str(result["path"])
                report.add("stage_release", detail=json.dumps(result, sort_keys=True, default=str))
                pointer = {
                    "version": result["version"],
                    "build_id": result["build_id"],
                    "path": staged_path,
                }
                _write_text(
                    current,
                    json.dumps(pointer, indent=2, sort_keys=True) + "\n",
                )
                report.add("current_pointer", detail=str(current))
            except InstallError as exc:
                report.add("signed_package", ok=False, detail=f"{exc.code}: {exc.message}")
                if first_install and current.is_file():
                    current.unlink(missing_ok=True)
                    report.add("pointer_cleared", detail="first install failed — pointer removed")
                elif previous_pointer is not None:
                    _write_text(
                        current,
                        json.dumps(previous_pointer, indent=2, sort_keys=True) + "\n",
                    )
                    report.add("pointer_restored", detail="restored previous current.json")
    elif release_dir is not None:
        # Lab/dir path — still full runtime validation (wheels + ELF on Linux).
        try:
            meta = validate_release_runtime(
                release_dir,
                require_wheels=require_wheels,
                require_aarch64_elf=require_elf,
                allow_dev_artifact=allow_test_key or overlay,
            )
            if not allow_test_key:
                bid = str(meta.get("build_id") or "")
                if bid.startswith("dirty-") or "test" in bid.lower():
                    report.add(
                        "signing_policy",
                        ok=False,
                        detail="refusing test/dirty build without --allow-test-key",
                    )
            report.add("runtime_validate", detail=json.dumps(meta, sort_keys=True))
            dest_name = f"{meta['version']}-{meta['build_id']}"
            dest = paths.releases / dest_name
            report.add("stage_release", planned={"from": str(release_dir), "to": str(dest)})
            if apply and report.ok:
                if dest.exists():
                    report.add("stage_release", detail="already present")
                else:
                    shutil.copytree(release_dir, dest)
                staged_path = str(dest)
                pointer = {
                    "version": meta["version"],
                    "build_id": meta["build_id"],
                    "path": staged_path,
                }
                _write_text(
                    current,
                    json.dumps(pointer, indent=2, sort_keys=True) + "\n",
                )
                report.add("current_pointer", detail=str(current))
        except RuntimeValidationError as exc:
            report.add("runtime_validate", ok=False, detail=f"{exc.code}: {exc.message}")
    elif current.is_file():
        ptr = str((previous_pointer or {}).get("path") or "")
        if ptr and (".git" in ptr or Path(ptr).joinpath(".git").exists()):
            report.add("current_pointer", ok=False, detail="points at git checkout")
        else:
            report.add("current_pointer", detail="present — preserved")
    else:
        report.add(
            "initial_release",
            ok=not apply,
            detail="no current.json; pass --package/--manifest or --release",
        )

    if apply:
        _apply_ownership(paths, apply=True, real_fs=real_fs)
        report.add("ownership", detail="applied" if real_fs else "overlay_skip_chown")
        report.add("daemon_reload", planned=daemon_reload(apply=True, real_fs=real_fs))
        report.add("udev_reload", planned=udev_reload(apply=True, real_fs=real_fs))

    # Service enable/start is a separate controlled step after health proof.
    if enable_service:
        if not report.ok:
            report.add(
                "enable_service",
                ok=False,
                detail="refusing enable/start while bootstrap has errors",
            )
        elif not current.is_file():
            report.add("enable_service", ok=False, detail="no current.json")
        else:
            report.add(
                "enable_service",
                planned=service_enable_start(
                    contract.unit_name, apply=apply, real_fs=real_fs
                ),
            )
    else:
        report.add(
            "enable_service",
            detail="deferred — pass --enable-service after health verification",
        )

    if apply and not report.ok:
        report.add(
            "abort",
            ok=False,
            detail="errors present — previous working state left unchanged where possible",
        )
        if first_install and current.is_file() and staged_path is None:
            current.unlink(missing_ok=True)
        if real_fs and apply:
            service_stop_disable(contract.unit_name, apply=True, real_fs=True)
    return report


def uninstall_plan(paths: RaspberryPaths, *, apply: bool, purge_data: bool) -> BootstrapReport:
    report = BootstrapReport(mode="uninstall-apply" if apply else "uninstall-dry-run")
    real_fs = is_real_root_fs(paths.root)
    report.add(
        "remove_unit",
        planned={
            "path": str(paths.unit_path),
            "systemctl": ["disable", "--now", "orng-led-control"],
        },
    )
    report.add("remove_app", planned={"path": str(paths.app_root)})
    if purge_data:
        report.add(
            "purge_data",
            ok=False,
            detail="refusing silent purge — needs explicit flag on device",
        )
    else:
        report.add("preserve_data", detail=str(paths.data_root))
    if apply:
        service_stop_disable(paths.contract.unit_name, apply=True, real_fs=real_fs)
        if paths.unit_path.is_file():
            paths.unit_path.unlink()
        if paths.app_root.is_dir() and not (paths.app_root / ".git").exists():
            helper = paths.bootstrap / "updater_helper.py"
            if helper.is_file():
                helper.unlink()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ORNG Raspberry bootstrap")
    parser.add_argument("--root", default="", help="Temp root overlay (no host changes)")
    parser.add_argument("--check", action="store_true", help="Preflight only")
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--apply", action="store_true", help="Explicit apply (required for writes)")
    parser.add_argument("--release", type=Path, default=None, help="Verified release directory")
    parser.add_argument("--package", type=Path, default=None, help="Signed .tar.gz package")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Sidecar .manifest.json (default: <package>.manifest.json)",
    )
    parser.add_argument("--expect-sha256", default=None, help="Required package content SHA-256")
    parser.add_argument(
        "--allow-test-key",
        action="store_true",
        help="Trust test signing public key only (does not skip ELF/wheelhouse)",
    )
    parser.add_argument(
        "--enable-service",
        action="store_true",
        help="After a successful install, enable+start systemd unit",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--purge-data", action="store_true")
    args = parser.parse_args(argv)

    root = args.root or None
    paths = resolve_paths(root)
    skip_arch = bool(root) or not sys.platform.startswith("linux")

    if args.check and not args.apply and not args.uninstall:
        report = preflight_checks(paths, skip_host_arch=skip_arch)
    elif args.uninstall:
        if args.apply and not args.dry_run:
            report = uninstall_plan(paths, apply=True, purge_data=args.purge_data)
        else:
            report = uninstall_plan(paths, apply=False, purge_data=args.purge_data)
    else:
        apply = bool(args.apply) and not args.dry_run
        if not args.apply and not args.dry_run and not args.check:
            apply = False
        pre = preflight_checks(paths, skip_host_arch=skip_arch)
        report = plan_or_apply(
            paths,
            apply=apply,
            release_dir=args.release,
            package_path=args.package,
            manifest_path=args.manifest,
            expected_sha256=args.expect_sha256,
            allow_test_key=args.allow_test_key,
            enable_service=args.enable_service,
        )
        report.steps = pre.steps + report.steps
        report.ok = pre.ok and report.ok
        report.errors = pre.errors + report.errors
        report.mode = "apply" if apply else "dry-run"

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"mode={report.mode} ok={report.ok}")
        for s in report.steps:
            mark = "OK" if s["ok"] else "FAIL"
            print(f"  [{mark}] {s['name']}: {s.get('detail') or s.get('planned')}")
        if report.errors:
            print("errors:")
            for e in report.errors:
                print(f"  - {e}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
