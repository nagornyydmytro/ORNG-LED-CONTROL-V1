"""Device-local venue_ready policy: Art-Net + arm under blackout with retry.

Raspberry always uses ``output_policy=venue_ready``. Failures are logged and
surfaced via health — never swallowed silently. Blackout is never cleared here.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from orng_led.cloud.device_mode import CloudDeviceMode, cloud_device_mode
from orng_led.raspberry.contract import load_contract

logger = logging.getLogger(__name__)

OUTPUT_POLICY_VENUE_READY = "venue_ready"
VENUE_READY_BACKOFF_S = (1.0, 2.0, 5.0, 10.0, 20.0, 30.0)


@dataclass
class VenueReadyStatus:
    """In-memory readiness supervisor state (mirrored into /api/health)."""

    policy: str = "none"
    state: str = "idle"  # idle|waiting_network|activating|ready|degraded
    attempts: int = 0
    last_error: str | None = None
    ethernet_link: bool | None = None
    artnet_route_valid: bool | None = None
    startup_preset: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_policy": self.policy,
            "venue_ready_state": self.state,
            "venue_ready_attempts": self.attempts,
            "venue_last_error": self.last_error,
            "ethernet_link": self.ethernet_link,
            "artnet_route_valid": self.artnet_route_valid,
            "startup_preset": self.startup_preset,
            "venue_notes": list(self.notes)[-8:],
        }


def output_policy() -> str:
    """Resolve device-local output policy.

    Raspberry (ORNG_CLOUD_DEVICE_MODE=raspberry) is always venue_ready.
    Explicit ORNG_OUTPUT_POLICY / ORNG_KEEP_OUTPUT_ARMED also opt in.
    """
    raw = (os.environ.get("ORNG_OUTPUT_POLICY") or "").strip().lower()
    if raw in {OUTPUT_POLICY_VENUE_READY, "venue", "always_arm"}:
        return OUTPUT_POLICY_VENUE_READY
    if cloud_device_mode() == CloudDeviceMode.RASPBERRY:
        return OUTPUT_POLICY_VENUE_READY
    flag = (os.environ.get("ORNG_KEEP_OUTPUT_ARMED") or "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return OUTPUT_POLICY_VENUE_READY
    return "manual"


def venue_ready_enabled() -> bool:
    return output_policy() == OUTPUT_POLICY_VENUE_READY


def backoff_seconds(attempt: int) -> float:
    if attempt <= 0:
        return VENUE_READY_BACKOFF_S[0]
    idx = min(attempt - 1, len(VENUE_READY_BACKOFF_S) - 1)
    return float(VENUE_READY_BACKOFF_S[idx])


def _run_ip(*args: str, timeout: float = 5.0) -> str | None:
    if shutil.which("ip") is None:
        return None
    try:
        proc = subprocess.run(
            ["ip", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("venue_ready: ip %s failed: %s", " ".join(args), exc)
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout or ""


def ethernet_link_up(iface: str | None = None) -> bool | None:
    """Return True/False when probe works; None when unavailable (non-Linux)."""
    contract = load_contract()
    name = iface or "eth0"
    out = _run_ip("link", "show", "dev", name)
    if out is None:
        # Fallback: any ethernet-looking carrier via `ip -br link`
        br = _run_ip("-br", "link")
        if br is None:
            return None
        for line in br.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            dev, state = parts[0], parts[1].upper()
            if not (dev == "eth0" or dev.startswith(("enp", "eno", "ens"))):
                continue
            return state in {"UP", "UNKNOWN"}
        return False
    lower = out.lower()
    if "state up" in lower or "state unknown" in lower:
        return True
    if "state down" in lower:
        return False
    # Carrier bit when present
    if "lower_up" in lower:
        return True
    if "no-carrier" in lower or "lower_down" in lower:
        return False
    _ = contract
    return None


def artnet_route_valid(
    target: str | None = None,
    *,
    expected_src: str | None = None,
) -> bool | None:
    """True when ``ip route get <target>`` uses Art-Net ethernet source."""
    contract = load_contract()
    dest = target or contract.artnet_target
    want_src = expected_src or contract.eth_address.split("/", 1)[0]
    out = _run_ip("route", "get", dest)
    if out is None:
        return None
    text = " ".join(out.split())
    if "unreachable" in text.lower() or "network is unreachable" in text.lower():
        return False
    # Prefer explicit src match; also accept via eth0/en*
    if f"src {want_src}" in text:
        return True
    if " dev eth0 " in f" {text} " or " dev en" in f" {text} ":
        return True
    # Route exists but wrong interface — still not venue-ready for Art-Net.
    if "via" in text or "dev" in text:
        return False
    return False


def probe_network(*, target: str | None = None) -> tuple[bool | None, bool | None, str | None]:
    """Return (ethernet_link, artnet_route_valid, error_reason)."""
    link = ethernet_link_up()
    route = artnet_route_valid(target)
    if link is False:
        return link, route, "ethernet_link_down"
    if route is False:
        return link, route, "artnet_route_invalid"
    if link is None and route is None:
        # Non-Linux / no ip tool — supervisor may still try activate.
        return None, None, None
    return link, route, None


_probe_guard = threading.Lock()
_probe_cache: dict[str, object] = {
    "mono": 0.0,
    "target": None,
    "link": None,
    "route": None,
    "reason": None,
    "running": False,
}
_PROBE_CACHE_S = 5.0


def cached_probe_network(
    *, target: str | None = None
) -> tuple[bool | None, bool | None, str | None]:
    """Last network probe. Never blocks, and never starts a second probe."""
    now = time.monotonic()
    with _probe_guard:
        fresh = (
            _probe_cache["target"] == target
            and float(_probe_cache["mono"] or 0.0) > 0.0
            and now - float(_probe_cache["mono"] or 0.0) < _PROBE_CACHE_S
        )
        if fresh or bool(_probe_cache["running"]):
            return (
                _probe_cache["link"],  # type: ignore[return-value]
                _probe_cache["route"],  # type: ignore[return-value]
                _probe_cache["reason"],  # type: ignore[return-value]
            )
        _probe_cache["running"] = True
        _probe_cache["target"] = target

    def work() -> None:
        try:
            link, route, reason = probe_network(target=target)
        except Exception:  # noqa: BLE001
            link, route, reason = None, None, "probe_failed"
        with _probe_guard:
            _probe_cache["link"] = link
            _probe_cache["route"] = route
            _probe_cache["reason"] = reason
            _probe_cache["mono"] = time.monotonic()
            _probe_cache["running"] = False

    threading.Thread(target=work, name="orng-net-probe", daemon=True).start()
    with _probe_guard:
        return (
            _probe_cache["link"],  # type: ignore[return-value]
            _probe_cache["route"],  # type: ignore[return-value]
            _probe_cache["reason"],  # type: ignore[return-value]
        )


def resolve_startup_preset(show_app: Any) -> str:
    """Canonical startup preset under blackout: app.startup_preset or first pad / A01."""
    explicit = getattr(show_app, "startup_preset", None)
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    pads = getattr(show_app, "pad_presets", None) or []
    if pads:
        first = str(pads[0]).strip()
        if first:
            return first
    return "A01"


def log_venue_event(level: int, message: str, *args: Any) -> None:
    logger.log(level, "venue_ready: " + message, *args)
