"""Load/save preset YAML documents atomically."""

from __future__ import annotations

from pathlib import Path

from orng_led.config.io import atomic_write_yaml, parse_model
from orng_led.config.models import ConfigError
from orng_led.presets.models import PresetDocument


def default_presets_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "presets"


def load_preset(path: Path) -> PresetDocument:
    from orng_led.config.io import _read_yaml
    from orng_led.presets.show_contract import validate_preset_show_contract

    doc = parse_model(PresetDocument, _read_yaml(path), source=path)
    validate_preset_show_contract(doc)
    return doc


def save_preset(path: Path, document: PresetDocument) -> None:
    # Always persist hardware_tuned=false on HOME.
    doc = document.model_copy(update={"hardware_tuned": False})
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_yaml(path, doc.model_dump(mode="json"))


def load_presets_dir(directory: Path) -> dict[str, PresetDocument]:
    if not directory.exists():
        return {}
    if not directory.is_dir():
        raise ConfigError(f"Presets path is not a directory: {directory}")
    result: dict[str, PresetDocument] = {}
    for path in sorted(directory.glob("*.yaml")):
        doc = load_preset(path)
        if doc.id in result:
            raise ConfigError(f"Duplicate preset id {doc.id!r} from {path}")
        if path.stem != doc.id:
            raise ConfigError(f"Preset file {path.name} must be named {doc.id}.yaml")
        result[doc.id] = doc
    return result


def preset_path(directory: Path, preset_id: str) -> Path:
    return directory / f"{preset_id}.yaml"
