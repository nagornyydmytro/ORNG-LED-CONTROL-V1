"""Low-level Pi host operations (user/group/chown/systemctl/udev).

All mutating helpers are no-ops or skipped under temp-root overlays.
Real apply on Path('/') requires uid 0.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


class SysOpError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def is_real_root_fs(root: Path) -> bool:
    return root in {Path("/"), Path("")}


def require_root_for_apply(*, apply: bool, real_fs: bool) -> None:
    if not apply or not real_fs:
        return
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        raise SysOpError("not_root", "bootstrap --apply on the device requires root")


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=check,
        capture_output=True,
        text=True,
        timeout=120,
    )


def user_exists(name: str) -> bool:
    try:
        import pwd

        pwd.getpwnam(name)
        return True
    except Exception:  # noqa: BLE001
        return False


def group_exists(name: str) -> bool:
    try:
        import grp

        grp.getgrnam(name)
        return True
    except Exception:  # noqa: BLE001
        return False


def ensure_service_user(
    *,
    user: str,
    group: str,
    home: str,
    input_group: str,
    apply: bool,
    real_fs: bool,
) -> dict[str, Any]:
    planned: dict[str, Any] = {
        "user": user,
        "group": group,
        "home": home,
        "input_group": input_group,
    }
    if not apply or not real_fs:
        planned["executed"] = False
        return planned

    if not user_exists(user):
        _run(
            [
                "useradd",
                "--system",
                "--home",
                home,
                "--shell",
                "/usr/sbin/nologin",
                "--user-group",
                user,
            ]
        )
        planned["useradd"] = True
    else:
        planned["useradd"] = False

    if group_exists(input_group):
        _run(["usermod", "-aG", input_group, user], check=False)
        planned["usermod_input"] = True
    else:
        planned["usermod_input"] = False
        planned["input_group_missing"] = True

    planned["executed"] = True
    return planned


def chown_path(path: Path, user: str, group: str, *, recursive: bool = False) -> None:
    if not hasattr(os, "chown"):
        return
    try:
        import grp
        import pwd

        uid = pwd.getpwnam(user).pw_uid
        gid = grp.getgrnam(group).gr_gid
    except Exception:  # noqa: BLE001
        return
    if recursive and path.is_dir():
        for p in [path, *path.rglob("*")]:
            try:
                os.chown(p, uid, gid)
            except OSError:
                pass
    else:
        try:
            os.chown(path, uid, gid)
        except OSError:
            pass


def chmod_path(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def daemon_reload(*, apply: bool, real_fs: bool) -> dict[str, Any]:
    if not apply or not real_fs:
        return {"executed": False, "command": ["systemctl", "daemon-reload"]}
    if shutil.which("systemctl") is None:
        return {"executed": False, "error": "systemctl_missing"}
    _run(["systemctl", "daemon-reload"])
    return {"executed": True}


def udev_reload(*, apply: bool, real_fs: bool) -> dict[str, Any]:
    if not apply or not real_fs:
        return {"executed": False}
    if shutil.which("udevadm") is None:
        return {"executed": False, "error": "udevadm_missing"}
    _run(["udevadm", "control", "--reload"], check=False)
    _run(["udevadm", "trigger"], check=False)
    return {"executed": True}


def service_enable_start(unit: str, *, apply: bool, real_fs: bool) -> dict[str, Any]:
    if not apply or not real_fs:
        return {"executed": False, "unit": unit}
    _run(["systemctl", "enable", "--now", unit])
    return {"executed": True, "unit": unit}


def service_stop_disable(unit: str, *, apply: bool, real_fs: bool) -> dict[str, Any]:
    if not apply or not real_fs:
        return {"executed": False, "unit": unit}
    _run(["systemctl", "disable", "--now", unit], check=False)
    return {"executed": True, "unit": unit}
