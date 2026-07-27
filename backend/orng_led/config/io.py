"""YAML load/save with schema validation and atomic writes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from orng_led.config.models import (
    AppConfig,
    ConfigError,
    FixtureProfile,
    PatchDocument,
    ShowConfig,
    SpatialLayout,
    require_supported_schema_version,
)
from orng_led.config.schema import SCHEMA_VERSION
from orng_led.config.validation import validate_patch


def default_config_dir() -> Path:
    """Repository `config/` directory (provisional YAML lives here)."""
    return Path(__file__).resolve().parents[3] / "config"


def _read_yaml(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if data is None:
        raise ConfigError(f"YAML document is empty: {path}")
    return data


def _validation_message(exc: ValidationError, path: Path) -> str:
    parts = [f"{err['loc']}: {err['msg']}" for err in exc.errors()]
    return f"Validation failed for {path}: " + "; ".join(parts)


def parse_model[T](model_type: type[T], data: Any, *, source: Path | str) -> T:
    if not isinstance(data, dict):
        raise ConfigError(f"{source}: expected a YAML mapping at document root.")
    if "schema_version" in data:
        require_supported_schema_version(int(data["schema_version"]))
    try:
        return model_type.model_validate(data)  # type: ignore[attr-defined, return-value]
    except ValidationError as exc:
        raise ConfigError(_validation_message(exc, Path(str(source)))) from exc
    except ConfigError:
        raise


def atomic_write_yaml(path: Path, data: dict[str, Any]) -> None:
    """Write YAML atomically so a crash cannot leave a truncated file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(payload, encoding="utf-8", newline="\n")
        tmp_path.replace(path)
    except OSError as exc:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise ConfigError(f"Atomic write failed for {path}: {exc}") from exc


def save_model(
    path: Path, model: AppConfig | FixtureProfile | PatchDocument | SpatialLayout
) -> None:
    atomic_write_yaml(path, model.model_dump(mode="json"))


def load_app_config(path: Path) -> AppConfig:
    return parse_model(AppConfig, _read_yaml(path), source=path)


def load_profile(path: Path) -> FixtureProfile:
    profile = parse_model(FixtureProfile, _read_yaml(path), source=path)
    if profile.hardware_verified:
        raise ConfigError(
            f"{path}: provisional HOME profiles must set hardware_verified: false "
            "until physical verification."
        )
    return profile


def load_profiles(directory: Path) -> dict[str, FixtureProfile]:
    if not directory.is_dir():
        raise ConfigError(f"Profiles directory not found: {directory}")
    profiles: dict[str, FixtureProfile] = {}
    for path in sorted(directory.glob("*.yaml")):
        profile = load_profile(path)
        if profile.id in profiles:
            raise ConfigError(f"Duplicate profile id {profile.id!r} from {path}.")
        profiles[profile.id] = profile
    if not profiles:
        raise ConfigError(f"No fixture profiles found in {directory}.")
    return profiles


def load_patch(path: Path) -> PatchDocument:
    return parse_model(PatchDocument, _read_yaml(path), source=path)


def load_layout(path: Path) -> SpatialLayout:
    return parse_model(SpatialLayout, _read_yaml(path), source=path)


def load_show_config(config_dir: Path | None = None) -> ShowConfig:
    root = config_dir or default_config_dir()
    app = load_app_config(root / "app.yaml")
    profiles = load_profiles(root / "profiles")
    patch = load_patch(root / "patch.yaml")
    layout = load_layout(root / "layout.yaml")
    validate_patch(patch, profiles)

    layout_ids = set(layout.fixtures)
    patch_ids = {fixture.id for fixture in patch.fixtures}
    if layout_ids != patch_ids:
        missing = sorted(patch_ids - layout_ids)
        extra = sorted(layout_ids - patch_ids)
        raise ConfigError(
            "layout.fixtures must list exactly the patch fixture ids. "
            f"missing={missing}, extra={extra}."
        )

    for fixture in patch.fixtures:
        if fixture.profile_id not in profiles:
            raise ConfigError(
                f"Fixture {fixture.id!r} references missing profile {fixture.profile_id!r}."
            )

    return ShowConfig(app=app, profiles=profiles, patch=patch, layout=layout)


def dump_default_schema_version() -> int:
    return SCHEMA_VERSION
