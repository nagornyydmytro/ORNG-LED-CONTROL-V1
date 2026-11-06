"""Wi‑Fi default + Ethernet Art-Net NetworkManager helper.

Dry-run by default. Real ``--apply`` only on Linux with root.
Never touches Wi‑Fi. Art-Net Ethernet is never-default (no gateway/DNS).
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orng_led.raspberry.contract import load_contract

_IFACE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,15}$")
CONNECTION_NAME = "orng-artnet"
BACKUP_DIR = Path("/var/lib/orng-led-control/network-backup")


class NetworkPlanError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_iface(name: str) -> str:
    if not _IFACE_RE.match(name):
        raise NetworkPlanError("bad_iface", f"Invalid interface name: {name!r}")
    return name


def validate_cidr(cidr: str) -> str:
    try:
        ipaddress.ip_interface(cidr)
    except ValueError as exc:
        raise NetworkPlanError("bad_cidr", str(exc)) from exc
    return cidr


def validate_ip(ip: str) -> str:
    try:
        ipaddress.ip_address(ip)
    except ValueError as exc:
        raise NetworkPlanError("bad_ip", str(exc)) from exc
    return ip


@dataclass
class NetworkPlan:
    iface: str
    cidr: str
    target: str
    port: int
    connection_name: str = CONNECTION_NAME
    commands: list[list[str]] = field(default_factory=list)
    nm_profile: dict[str, Any] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)
    restore: list[list[str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "iface": self.iface,
            "cidr": self.cidr,
            "target": self.target,
            "port": self.port,
            "connection_name": self.connection_name,
            "commands": self.commands,
            "nm_profile": self.nm_profile,
            "checks": self.checks,
            "restore": self.restore,
            "note": "dry-run only unless --apply on Raspberry (root)",
        }


def detect_ethernet_iface() -> str | None:
    """Pick a non-wireless ethernet device that is present (not assumed eth0)."""
    if shutil.which("nmcli") is None:
        return None
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except Exception:  # noqa: BLE001
        return None
    candidates: list[str] = []
    for line in out.splitlines():
        parts = line.strip().split(":")
        if len(parts) < 2:
            continue
        dev, typ = parts[0], parts[1]
        if typ != "ethernet":
            continue
        if not _IFACE_RE.match(dev):
            continue
        candidates.append(dev)
    # Prefer a connected/disconnected ethernet that looks like wired NIC.
    for pref in ("eth0", "enp", "eno", "ens"):
        for c in candidates:
            if c == pref or c.startswith(pref):
                return c
    return candidates[0] if candidates else None


def build_artnet_ethernet_plan(
    *,
    iface: str | None = None,
    cidr: str | None = None,
    target: str | None = None,
    port: int | None = None,
) -> NetworkPlan:
    contract = load_contract()
    if iface is None or iface == "auto":
        detected = detect_ethernet_iface()
        iface = detected or "eth0"
    iface = validate_iface(iface)
    cidr = validate_cidr(cidr or contract.eth_address)
    target = validate_ip(target or contract.artnet_target)
    port = int(port or contract.artnet_port)
    addr = str(ipaddress.ip_interface(cidr).ip)

    plan = NetworkPlan(iface=iface, cidr=cidr, target=target, port=port)
    plan.nm_profile = {
        "connection.id": plan.connection_name,
        "connection.interface-name": iface,
        "connection.type": "802-3-ethernet",
        "ipv4.method": "manual",
        "ipv4.addresses": cidr,
        "ipv4.gateway": "",
        "ipv4.dns": "",
        "ipv4.never-default": "yes",
        "ipv6.method": "disabled",
    }
    plan.commands = [
        ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"],
        ["nmcli", "-t", "-f", "NAME,DEVICE,TYPE", "connection", "show"],
        [
            "nmcli",
            "connection",
            "add",
            "type",
            "ethernet",
            "ifname",
            iface,
            "con-name",
            plan.connection_name,
            "ipv4.method",
            "manual",
            "ipv4.addresses",
            cidr,
            "ipv4.never-default",
            "yes",
            "ipv4.gateway",
            "",
            "ipv4.dns",
            "",
            "ipv6.method",
            "disabled",
        ],
        ["nmcli", "connection", "up", plan.connection_name],
        ["ip", "route", "get", target],
        ["ip", "-4", "addr", "show", "dev", iface],
    ]
    plan.checks = [
        {
            "name": "route_get_target",
            "command": ["ip", "route", "get", target],
            "expect_contains": [iface, addr],
        },
        {
            "name": "wifi_default_preserved",
            "command": ["ip", "route", "show", "default"],
            "expect_not_iface": iface,
        },
        {
            "name": "link_carrier",
            "command": ["ip", "link", "show", "dev", iface],
        },
        {
            "name": "ping_optional",
            "command": ["ping", "-c", "1", "-W", "1", "-I", iface, target],
            "optional": True,
        },
    ]
    plan.restore = [
        ["nmcli", "connection", "down", plan.connection_name],
        ["nmcli", "connection", "delete", plan.connection_name],
    ]
    return plan


def parse_route_get(output: str) -> dict[str, str]:
    parts = output.split()
    out: dict[str, str] = {}
    for i, tok in enumerate(parts):
        if tok == "dev" and i + 1 < len(parts):
            out["dev"] = parts[i + 1]
        elif tok == "src" and i + 1 < len(parts):
            out["src"] = parts[i + 1]
        elif tok == "via" and i + 1 < len(parts):
            out["via"] = parts[i + 1]
    if parts:
        out["destination"] = parts[0]
    return out


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    # Never pass through shell — argv list only.
    return subprocess.run(cmd, check=check, capture_output=True, text=True, timeout=60)


def _backup_ethernet_profiles(backup_dir: Path) -> list[str]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(backup_dir, 0o700)
    except OSError:
        pass
    saved: list[str] = []
    try:
        out = _run(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show"]).stdout
    except Exception:  # noqa: BLE001
        return saved
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) < 2:
            continue
        name, typ = parts[0], parts[1]
        if typ != "802-3-ethernet" and typ != "ethernet":
            continue
        if name == CONNECTION_NAME:
            continue
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:64]
        dest = backup_dir / f"{safe}.nmconnection.bak"
        # Export via nmcli connection show id → text dump (no secrets required for restore of name).
        try:
            dump = _run(["nmcli", "connection", "show", "id", name], check=False)
            dest.write_text(dump.stdout or "", encoding="utf-8")
            os.chmod(dest, 0o600)
            saved.append(name)
        except Exception:  # noqa: BLE001
            continue
    meta = backup_dir / "backup-meta.json"
    meta.write_text(
        json.dumps({"saved": saved, "ts": int(time.time())}, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(meta, 0o600)
    except OSError:
        pass
    return saved


def _connection_exists(name: str) -> bool:
    try:
        out = _run(["nmcli", "-t", "-f", "NAME", "connection", "show"]).stdout
    except Exception:  # noqa: BLE001
        return False
    return any(line.strip() == name for line in out.splitlines())


def apply_artnet_ethernet(plan: NetworkPlan, *, allow_udp_diag: bool = False) -> dict[str, Any]:
    if not sys_is_linux_root():
        raise NetworkPlanError(
            "apply_refused",
            "network --apply requires Linux root on the Raspberry",
        )
    if shutil.which("nmcli") is None:
        raise NetworkPlanError("nmcli_missing", "NetworkManager nmcli not found")

    backup = _backup_ethernet_profiles(BACKUP_DIR)
    result: dict[str, Any] = {
        "ok": False,
        "iface": plan.iface,
        "connection": plan.connection_name,
        "backup_profiles": backup,
        "checks": [],
    }

    try:
        # Prevent stock DHCP ethernet profiles from racing for the Art-Net NIC.
        try:
            devices = _run(
                ["nmcli", "-t", "-f", "DEVICE,TYPE,CONNECTION", "device"],
                check=False,
            ).stdout
            for line in devices.splitlines():
                parts = line.split(":")
                if len(parts) < 3:
                    continue
                dev, typ, conn = parts[0], parts[1], parts[2]
                if typ != "ethernet" or dev != plan.iface:
                    continue
                if conn and conn != plan.connection_name:
                    _run(["nmcli", "device", "disconnect", plan.iface], check=False)
                    _run(
                        ["nmcli", "connection", "modify", conn, "connection.autoconnect", "no"],
                        check=False,
                    )
                    result["checks"].append(
                        {
                            "name": "disable_stock_ethernet_autoconnect",
                            "ok": True,
                            "connection": conn,
                        }
                    )
        except Exception:  # noqa: BLE001
            pass

        if _connection_exists(plan.connection_name):
            _run(["nmcli", "connection", "delete", plan.connection_name], check=False)
        add_cmd = [
            "nmcli",
            "connection",
            "add",
            "type",
            "ethernet",
            "ifname",
            plan.iface,
            "con-name",
            plan.connection_name,
            "ipv4.method",
            "manual",
            "ipv4.addresses",
            plan.cidr,
            "ipv4.never-default",
            "yes",
            "ipv4.gateway",
            "",
            "ipv4.dns",
            "",
            "ipv6.method",
            "disabled",
            "connection.autoconnect",
            "yes",
        ]
        _run(add_cmd)
        _run(["nmcli", "connection", "up", plan.connection_name])

        # Verify routes.
        route = _run(["ip", "route", "get", plan.target])
        parsed = parse_route_get(route.stdout)
        addr = str(ipaddress.ip_interface(plan.cidr).ip)
        route_ok = parsed.get("dev") == plan.iface and parsed.get("src") == addr
        result["checks"].append(
            {
                "name": "route_get_target",
                "ok": route_ok,
                "parsed": parsed,
                "raw": route.stdout.strip(),
            }
        )
        if not route_ok:
            raise NetworkPlanError(
                "route_check_failed",
                f"expected dev={plan.iface} src={addr}, got {parsed}",
            )

        default = _run(["ip", "route", "show", "default"], check=False)
        default_text = default.stdout or ""
        wifi_default_ok = f"dev {plan.iface}" not in default_text
        result["checks"].append(
            {
                "name": "wifi_default_preserved",
                "ok": wifi_default_ok,
                "raw": default_text.strip(),
            }
        )
        if not wifi_default_ok:
            raise NetworkPlanError(
                "default_route_stolen",
                "Ethernet became default route — rolling back",
            )

        # DNS still via Wi‑Fi (best-effort). resolvectl is optional on Pi images.
        dns_ok = True
        dns_detail = "skipped"
        if shutil.which("resolvectl"):
            dns = _run(["resolvectl", "status"], check=False)
            dns_ok = True  # informational only — never fail apply on DNS tooling
            dns_detail = "checked (output omitted)" if dns.returncode == 0 else "resolvectl_nonzero"
        result["checks"].append(
            {
                "name": "dns_status",
                "ok": dns_ok,
                "detail": dns_detail,
            }
        )

        if allow_udp_diag:
            # Explicit opt-in only — never during initial apply before controller confirm.
            diag = _run(
                [
                    "python3",
                    "-c",
                    (
                        "import socket;"
                        f"s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);"
                        f"s.bind(('{addr}',0));s.settimeout(0.5);"
                        f"s.sendto(b'ORNG-DIAG',('{plan.target}',{plan.port}));"
                        "s.close();print('udp_diag_sent')"
                    ),
                ],
                check=False,
            )
            result["checks"].append(
                {"name": "udp_diag", "ok": diag.returncode == 0, "note": "opt-in"}
            )

        result["ok"] = True
        return result
    except Exception as exc:
        # Automatic rollback.
        try:
            restore_artnet_ethernet(plan.connection_name)
        except Exception:  # noqa: BLE001
            pass
        result["ok"] = False
        result["error"] = str(exc)
        if isinstance(exc, NetworkPlanError):
            result["code"] = exc.code
        raise


def restore_artnet_ethernet(connection_name: str = CONNECTION_NAME) -> dict[str, Any]:
    if not sys_is_linux_root():
        raise NetworkPlanError("restore_refused", "restore requires Linux root")
    if not re.match(r"^[A-Za-z0-9._-]{1,64}$", connection_name):
        raise NetworkPlanError("bad_connection", connection_name)
    actions: list[str] = []
    if _connection_exists(connection_name):
        _run(["nmcli", "connection", "down", connection_name], check=False)
        _run(["nmcli", "connection", "delete", connection_name], check=False)
        actions.append("deleted")
    else:
        actions.append("absent")
    return {"ok": True, "connection": connection_name, "actions": actions}


def sys_is_linux_root() -> bool:
    if not sys_platform_linux():
        return False
    return hasattr(os, "geteuid") and os.geteuid() == 0


def sys_platform_linux() -> bool:
    return os.name == "posix" and hasattr(os, "uname") and os.uname().sysname == "Linux"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ORNG Art-Net Ethernet planner/applier")
    parser.add_argument("--iface", default="auto", help="Ethernet iface or 'auto'")
    parser.add_argument("--cidr", default=None)
    parser.add_argument("--target", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--restore", action="store_true")
    parser.add_argument(
        "--allow-udp-diag",
        action="store_true",
        help="Opt-in UDP probe (never during first controller connect without confirm)",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--parse-route-get", default=None)
    args = parser.parse_args(argv)

    if args.parse_route_get is not None:
        print(json.dumps(parse_route_get(args.parse_route_get), indent=2, sort_keys=True))
        return 0

    if args.restore:
        try:
            out = restore_artnet_ethernet(CONNECTION_NAME)
        except NetworkPlanError as exc:
            print(json.dumps({"ok": False, "code": exc.code, "message": exc.message}))
            return 1
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0

    try:
        plan = build_artnet_ethernet_plan(
            iface=None if args.iface == "auto" else args.iface,
            cidr=args.cidr,
            target=args.target,
            port=args.port,
        )
    except NetworkPlanError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "message": exc.message}))
        return 1

    if args.apply:
        try:
            result = apply_artnet_ethernet(plan, allow_udp_diag=bool(args.allow_udp_diag))
        except NetworkPlanError as exc:
            print(
                json.dumps(
                    {"ok": False, "code": exc.code, "message": exc.message},
                    indent=2,
                    sort_keys=True,
                )
            )
            return 2
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("ok") else 2

    if args.json:
        print(json.dumps(plan.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"plan iface={plan.iface} {plan.cidr} -> {plan.target}:{plan.port}")
        for cmd in plan.commands:
            print(" ", " ".join(cmd))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
