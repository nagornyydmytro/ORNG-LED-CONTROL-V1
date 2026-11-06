"""Diagnostics redaction, remote restart safe-state, offline posture."""

from __future__ import annotations

from orng_led.raspberry.contract import load_contract
from orng_led.raspberry.diagnostics import build_venue_diagnostics, redact_mapping


def test_hardware_acceptance_pending() -> None:
    assert load_contract().hardware_acceptance == "pending"


def test_diagnostics_redacts_secrets() -> None:
    raw = {
        "wifi_password": "secret",
        "ORNG_DEVICE_TOKEN": "tok",
        "ok_field": "visible",
        "authorization": "Bearer abc.def",
    }
    out = redact_mapping(raw)
    assert out["wifi_password"] == "[redacted]"
    assert out["ORNG_DEVICE_TOKEN"] == "[redacted]"
    assert out["ok_field"] == "visible"
    assert out["authorization"] == "[redacted]"


def test_venue_diagnostics_shape() -> None:
    d = build_venue_diagnostics(None)
    assert d["hardware_acceptance"] == "pending"
    assert "armed" in d and d["armed"] in {False, True}
    assert "blackout" in d
    assert "keyboard_connected" in d
    blob = str(d)
    assert "Bearer " not in blob


def test_restart_runtime_in_allowlist() -> None:
    from orng_led.cloud.agent import ALLOWED

    assert "restart_runtime" in ALLOWED
    assert "request_diagnostics" in ALLOWED
    assert "shell" not in ALLOWED
    assert "reboot" not in ALLOWED
