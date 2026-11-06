"""Sanitized venue diagnostics for heartbeat / doctor / orngctl."""

from __future__ import annotations

import os
import platform
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orng_led.raspberry.contract import load_contract

if TYPE_CHECKING:
    from orng_led.api.runtime import AppRuntime

# Match secret-like names. Do not use a bare "KEY" substring: that redacts
# keyboard_connected / keyboard_count and hides the macropad from heartbeat.
_SENSITIVE_PARTS = ("TOKEN", "PASSWORD", "SECRET", "PRIVATE", "BEARER")


def redact_value(key: str, value: Any) -> Any:
    upper = key.upper()
    if upper == "KEY" or upper.endswith("_KEY") or any(part in upper for part in _SENSITIVE_PARTS):
        return "[redacted]"
    if isinstance(value, str) and (
        value.startswith("Bearer ") or len(value) > 80 and "token" in upper.lower()
    ):
        return "[redacted]"
    return value


def redact_mapping(data: dict[str, Any]) -> dict[str, Any]:
    return {k: redact_value(k, v) for k, v in data.items()}


def _cpu_temp_c() -> float | None:
    thermal = Path("/sys/class/thermal/thermal_zone0/temp")
    if not thermal.is_file():
        return None
    try:
        return int(thermal.read_text(encoding="utf-8").strip()) / 1000.0
    except Exception:  # noqa: BLE001
        return None


def _disk_free_bytes(path: str | Path) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except Exception:  # noqa: BLE001
        return None


def build_venue_diagnostics(runtime: AppRuntime | None = None) -> dict[str, Any]:
    """Structured safe fields for heartbeat / doctor --json."""
    contract = load_contract()
    from orng_led.cloud.agent import CloudDeviceAgent
    from orng_led.updater.mode import detect_arch, detect_install_mode, detect_platform
    from orng_led.updater.service import AppUpdateService
    from orng_led.version import app_version, build_id

    upd = AppUpdateService(runtime).status() if runtime else AppUpdateService(None).status()
    kb: dict[str, Any] = {"connected": False, "count": 0}
    if runtime is not None:
        try:
            kb = runtime.linux_keyboard_status()
        except Exception:  # noqa: BLE001
            kb = {"connected": False, "count": 0, "error": "unavailable"}

    armed = False
    blackout = True
    transport = None
    scene_id = None
    last_error = None
    cloud_connected = False
    if runtime is not None:
        try:
            st = runtime.output.status()
            armed = bool(st.armed)
            transport = st.transport.value if hasattr(st.transport, "value") else str(st.transport)
            last_error = st.last_error
        except Exception:  # noqa: BLE001
            pass
        try:
            blackout = bool(runtime.engine.overlays.blackout)
        except Exception:  # noqa: BLE001
            blackout = True
        try:
            scene_id = runtime.scene_id
        except Exception:  # noqa: BLE001
            pass
        agent = getattr(runtime, "_cloud_agent", None) or getattr(runtime, "cloud_agent", None)
        if isinstance(agent, CloudDeviceAgent):
            cloud_connected = bool(agent.connected)

    ethernet_link: bool | None = None
    artnet_route_valid: bool | None = None
    if runtime is not None:
        try:
            from orng_led.raspberry.venue_ready import cached_probe_network

            target = None
            try:
                target = runtime.show.app.artnet.target_ip
            except Exception:  # noqa: BLE001
                target = None
            ethernet_link, artnet_route_valid, _reason = cached_probe_network(target=target)
        except Exception:  # noqa: BLE001
            ethernet_link, artnet_route_valid = None, None
        venue = getattr(runtime, "venue_ready_status", None)
        if callable(venue):
            try:
                vs = venue()
                if ethernet_link is None and vs.get("ethernet_link") is not None:
                    ethernet_link = vs.get("ethernet_link")
                if artnet_route_valid is None and vs.get("artnet_route_valid") is not None:
                    artnet_route_valid = vs.get("artnet_route_valid")
            except Exception:  # noqa: BLE001
                pass

    disk = _disk_free_bytes(upd.get("current_release", {}).get("path") or "/")
    payload = {
        "hardware_acceptance": contract.hardware_acceptance,
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "arch": detect_arch(),
        "platform": detect_platform(),
        "install_mode": detect_install_mode().value,
        "app_version": app_version(),
        "build_id": build_id(),
        "service_uptime_s": None,  # filled by helper env if present
        "boot_monotonic_s": time.monotonic(),
        "active_scene_id": scene_id,
        "update_state": upd.get("update_state"),
        "wifi_connected": None,  # boolean only when known — never SSID/password
        "cloud_connected": cloud_connected,
        "ethernet_link": ethernet_link,
        "artnet_route_valid": artnet_route_valid,
        "keyboard_connected": bool(kb.get("connected")),
        "keyboard_count": int(kb.get("count") or (1 if kb.get("connected") else 0)),
        "disk_free_bytes": disk,
        "cpu_temp_c": _cpu_temp_c(),
        "blackout": blackout,
        "armed": armed,
        "transport": transport,
        "last_error": last_error,
        "restart_reason": os.environ.get("ORNG_RESTART_REASON", "") or None,
    }
    return redact_mapping(payload)


def heartbeat_venue_fields(runtime: AppRuntime | None) -> dict[str, Any]:
    """Subset merged into cloud heartbeat payload."""
    d = build_venue_diagnostics(runtime)
    return {
        "hardware_acceptance": d.get("hardware_acceptance"),
        "os_release": (d.get("os") or {}).get("release"),
        "runtime_python": (d.get("os") or {}).get("python"),
        "service_uptime_s": d.get("service_uptime_s"),
        "wifi_connected": d.get("wifi_connected"),
        "cloud_connected": d.get("cloud_connected"),
        "ethernet_link": d.get("ethernet_link"),
        "artnet_route_valid": d.get("artnet_route_valid"),
        "keyboard_connected": d.get("keyboard_connected"),
        "keyboard_count": d.get("keyboard_count"),
        "disk_free_bytes": d.get("disk_free_bytes"),
        "cpu_temp_c": d.get("cpu_temp_c"),
        "restart_reason": d.get("restart_reason"),
    }
