"""Canonical Raspberry layout constants (see docs/raspberry-runtime-contract.md)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RaspberryContract:
    platform: str = "raspberry"
    arch: str = "arm64"
    os_id_hint: str = "raspbian|debian"
    min_release_python: tuple[int, int] = (3, 12)
    min_bootstrap_python: tuple[int, int] = (3, 11)
    app_root: str = "/opt/orng-led-control"
    data_root: str = "/var/lib/orng-led-control"
    etc_root: str = "/etc/orng-led-control"
    service_user: str = "orng-led"
    service_group: str = "orng-led"
    input_group: str = "input"
    unit_name: str = "orng-led-control.service"
    eth_address: str = "2.0.0.10/8"
    artnet_target: str = "2.0.0.11"
    artnet_port: int = 6454
    hardware_acceptance: str = "pending"


def load_contract() -> RaspberryContract:
    return RaspberryContract()
