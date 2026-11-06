"""Path resolution under real FS or ``--root`` temp overlay."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orng_led.raspberry.contract import RaspberryContract, load_contract


@dataclass(frozen=True)
class RaspberryPaths:
    root: Path  # "" or temp root prefix
    contract: RaspberryContract

    def _p(self, absolute: str) -> Path:
        # absolute like /opt/... → <root>/opt/...
        rel = absolute.lstrip("/")
        return self.root / rel if str(self.root) not in {"", "."} else Path(absolute)

    @property
    def app_root(self) -> Path:
        return self._p(self.contract.app_root)

    @property
    def data_root(self) -> Path:
        return self._p(self.contract.data_root)

    @property
    def etc_root(self) -> Path:
        return self._p(self.contract.etc_root)

    @property
    def bootstrap(self) -> Path:
        return self.app_root / "bootstrap"

    @property
    def releases(self) -> Path:
        return self.app_root / "releases"

    @property
    def current_pointer(self) -> Path:
        return self.app_root / "current.json"

    @property
    def device_env(self) -> Path:
        return self.etc_root / "device.env"

    @property
    def unit_path(self) -> Path:
        return self._p(f"/etc/systemd/system/{self.contract.unit_name}")

    @property
    def udev_rules(self) -> Path:
        return self._p("/etc/udev/rules.d/99-orng-led-input.rules")


def resolve_paths(root: str | Path | None = None) -> RaspberryPaths:
    if root in {None, "", "/"}:
        # Real FS: use absolute contract paths via empty overlay marker.
        return RaspberryPaths(root=Path("/"), contract=load_contract())
    return RaspberryPaths(root=Path(root), contract=load_contract())
