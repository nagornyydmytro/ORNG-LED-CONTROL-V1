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
    """Config/data root: ``ORNG_CONFIG_ROOT`` (managed data) or repo ``config/``."""
    import os

    override = os.environ.get("ORNG_CONFIG_ROOT", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "config"


def scenes_dir(config_dir: Path | None = None) -> Path:
    """Directory that holds per-scene folders."""
    return (config_dir or default_config_dir()) / "scenes"


def runtime_state_path(config_dir: Path | None = None) -> Path:
    """Device-local runtime state (active scene), not part of a scene."""
    return (config_dir or default_config_dir()) / "runtime.yaml"


def default_scene_dir(config_dir: Path | None = None) -> Path:
    """Directory of the active scene (defaults to orng-bar when unset/missing)."""
    root = config_dir or default_config_dir()
    try:
        state = load_runtime_state(root)
        scene_id = state.active_scene_id
    except ConfigError:
        scene_id = "orng-bar"
    candidate = scenes_dir(root) / scene_id
    if candidate.is_dir() and (candidate / "app.yaml").is_file():
        return candidate
    fallback = scenes_dir(root) / "orng-bar"
    if fallback.is_dir():
        return fallback
    raise ConfigError(f"No usable scene directory under {scenes_dir(root)}")


def is_scene_directory(path: Path) -> bool:
    return (path / "app.yaml").is_file() and (path / "profiles").is_dir()


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


def read_yaml(path: Path) -> Any:
    """Public YAML reader used by scene loaders."""
    return _read_yaml(path)


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


def load_controls(path: Path):
    """Load scene controls.yaml when present; return None if missing.

    Legacy ``pad_presets`` keys in controls.yaml are ignored — pad slots are
    owned exclusively by ``app.yaml``.
    """
    from orng_led.scenes.models import SceneControls

    if not path.is_file():
        return None
    data = _read_yaml(path)
    if isinstance(data, dict):
        data.pop("pad_presets", None)
    return parse_model(SceneControls, data, source=path)


def load_runtime_state(config_dir: Path | None = None):
    from orng_led.scenes.models import RuntimeState

    root = config_dir or default_config_dir()
    path = runtime_state_path(root)
    if not path.is_file():
        return RuntimeState()
    return parse_model(RuntimeState, _read_yaml(path), source=path)


def save_runtime_state(config_dir: Path | None, state) -> None:
    from orng_led.scenes.models import RuntimeState

    if not isinstance(state, RuntimeState):
        state = RuntimeState.model_validate(state)
    root = config_dir or default_config_dir()
    atomic_write_yaml(runtime_state_path(root), state.model_dump(mode="json"))


def load_show_config_from_scene_dir(scene_dir: Path) -> ShowConfig:
    """Load app/patch/layout/profiles from a scene directory."""
    root = Path(scene_dir)
    if not (root / "app.yaml").is_file():
        raise ConfigError(f"Scene directory missing app.yaml: {root}")
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


def load_show_config(config_dir: Path | None = None) -> ShowConfig:
    """Load show config.

    - ``None`` → active scene under the repository ``config/`` (via runtime.yaml).
    - Path to a scene directory (has ``app.yaml``) → load that scene directly.
    - Path to a config root (has ``scenes/`` or ``runtime.yaml``) → active scene
      under that root.
    """
    if config_dir is None:
        return load_show_config_from_scene_dir(default_scene_dir())

    root = Path(config_dir)
    if is_scene_directory(root):
        return load_show_config_from_scene_dir(root)

    # Config root: resolve active scene.
    if (root / "scenes").is_dir() or (root / "runtime.yaml").is_file():
        return load_show_config_from_scene_dir(default_scene_dir(root))

    # Last resort: treat as scene dir and let loaders raise clear errors.
    return load_show_config_from_scene_dir(root)


def dump_default_schema_version() -> int:
    return SCHEMA_VERSION
